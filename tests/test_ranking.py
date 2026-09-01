"""ranking.py 단위 테스트 — 점수식·감쇠·중복·다양성·피드백 사전확률."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ranking

NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)
CFG = ranking.load_config()


def item(h="h1", importance="nice_to_know", section="international",
         domain="example.com", title="Some title", queued_hours_ago=1,
         features=None, related_reports=None, **kw):
    d = {
        "hash": h, "importance": importance, "section": section,
        "domain": domain, "title": title, "title_kr": title,
        "link": f"https://{domain}/a/{h}",
        "queued_at": (NOW - timedelta(hours=queued_hours_ago)).isoformat(),
        "related_reports": related_reports or [],
    }
    if features is not None:
        d["features"] = features
    d.update(kw)
    return d


def feat(**kw):
    base = {"event_type": "other", "korea_relevance": 0, "market_materiality": 0,
            "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
            "report_worthiness": 0}
    base.update(kw)
    return base


class TestSanitize(unittest.TestCase):
    def test_none_and_non_dict(self):
        self.assertIsNone(ranking.sanitize_features(None))
        self.assertIsNone(ranking.sanitize_features("policy"))
        self.assertIsNone(ranking.sanitize_features([1, 2]))

    def test_missing_fields_default_zero(self):
        f = ranking.sanitize_features({"event_type": "contract_award"})
        self.assertEqual(f["event_type"], "contract_award")
        self.assertEqual(f["korea_relevance"], 0)
        self.assertEqual(f["report_worthiness"], 0)

    def test_out_of_range_clamped(self):
        f = ranking.sanitize_features({"korea_relevance": 99, "novelty": -5,
                                       "market_materiality": "3"})
        self.assertEqual(f["korea_relevance"], 3)
        self.assertEqual(f["novelty"], 0)
        self.assertEqual(f["market_materiality"], 3)

    def test_bad_event_type(self):
        f = ranking.sanitize_features({"event_type": "invented_type"})
        self.assertEqual(f["event_type"], "other")

    def test_non_int_scores(self):
        f = ranking.sanitize_features({"evidence_strength": "strong"})
        self.assertEqual(f["evidence_strength"], 0)


class TestLegacyScore(unittest.TestCase):
    """features 없는 옛 큐 항목 — 기존 rank_item 공식 그대로여야 함."""

    def test_must_read_khnp_primary_reports(self):
        a = item(importance="must_read", section="khnp", domain="khnp.co.kr",
                 related_reports=["r1"], queued_hours_ago=0)
        s, b = ranking.score_item(a, CFG, now=NOW)
        # 10(must) + 2(khnp) + 2(primary) + 1(reports) = 15
        self.assertEqual(s, 15.0)
        self.assertTrue(b.get("legacy"))

    def test_nice_plain(self):
        a = item(importance="nice_to_know", queued_hours_ago=0)
        s, _ = ranking.score_item(a, CFG, now=NOW)
        self.assertEqual(s, 5.0)


class TestScrapSeedBonus(unittest.TestCase):
    """사내스크랩 시드 가점 — features 유무(legacy 포함) 양쪽에 적용."""

    def test_bonus_applies_to_both_paths(self):
        plain = item(importance="nice_to_know", queued_hours_ago=0)
        seeded = {**plain, "matched": "사내스크랩"}
        s_plain, _ = ranking.score_item(plain, CFG, now=NOW)
        s_seed, b = ranking.score_item(seeded, CFG, now=NOW)
        self.assertEqual(s_seed - s_plain, CFG.get("scrap_seed_bonus", 2.5))
        self.assertIn("scrap_seed", b)
        # features 있는 경로에도 동일
        withf = item(features=feat(event_type="other"), importance="nice_to_know",
                     queued_hours_ago=0)
        s1, _ = ranking.score_item(withf, CFG, now=NOW)
        s2, _ = ranking.score_item({**withf, "matched": "사내스크랩"}, CFG, now=NOW)
        self.assertEqual(s2 - s1, CFG.get("scrap_seed_bonus", 2.5))

    def test_other_matched_values_get_no_bonus(self):
        a = item(importance="nice_to_know", queued_hours_ago=0)
        s1, _ = ranking.score_item(a, CFG, now=NOW)
        s2, b = ranking.score_item({**a, "matched": "계속운전"}, CFG, now=NOW)
        self.assertEqual(s1, s2)
        self.assertNotIn("scrap_seed", b)


class TestNewScore(unittest.TestCase):
    def test_breakdown_explainable(self):
        a = item(features=feat(event_type="contract_award", korea_relevance=3,
                               market_materiality=2, evidence_strength=3),
                 importance="must_read", queued_hours_ago=0)
        s, b = ranking.score_item(a, CFG, now=NOW)
        self.assertIn("importance", b)
        self.assertIn("event:contract_award", b)
        self.assertIn("korea_relevance", b)
        self.assertAlmostEqual(s, sum(v for k, v in b.items() if k != "legacy"),
                               places=2)

    def test_tier1_source_bonus(self):
        a = item(domain="iaea.org", features=feat(), queued_hours_ago=0)
        a["link"] = "https://www.iaea.org/newscenter/x"
        _, b = ranking.score_item(a, CFG, now=NOW)
        self.assertIn("source_tier1", b)

    def test_time_decay_old_article_lower(self):
        fresh = item(h="a", features=feat(novelty=2), queued_hours_ago=0)
        old = item(h="b", features=feat(novelty=2), queued_hours_ago=36)
        s1, _ = ranking.score_item(fresh, CFG, now=NOW)
        s2, b2 = ranking.score_item(old, CFG, now=NOW)
        self.assertLess(s2, s1)
        self.assertIn("time_decay", b2)

    def test_decay_capped(self):
        ancient = item(features=feat(), queued_hours_ago=1000)
        _, b = ranking.score_item(ancient, CFG, now=NOW)
        self.assertGreaterEqual(b["time_decay"], -CFG["time_decay"]["max"])


class TestCodeDerivedFeatures(unittest.TestCase):
    """novelty·evidence_strength 는 LLM 이 아니라 코드가 판정한다."""

    def test_confirmed_fact_with_numbers_scores_highest(self):
        a = {"title_kr": "한수원, 체코 두코바니 원전 2기 본계약 체결",
             "summary": "한수원이 24조 원 규모의 두코바니 원전 2기 건설 본계약을 체결했다."}
        self.assertEqual(ranking.derive_evidence_strength(a), 3)

    def test_speculation_scores_low(self):
        a = {"title_kr": "정부, 신규 원전 추가 검토 전망",
             "summary": "정부가 신규 원전 건설을 추가로 검토할 것으로 예상된다."}
        self.assertLessEqual(ranking.derive_evidence_strength(a), 1)

    def test_confirmed_without_numbers_drops_one_step(self):
        withnum = {"title_kr": "원안위, 한울 4호기 임계 허용",
                   "summary": "원안위가 한울 4호기의 임계를 허용했다."}
        without = {"title_kr": "원안위, 임계 허용",
                   "summary": "원안위가 임계를 허용했다."}
        self.assertGreater(ranking.derive_evidence_strength(withnum),
                           ranking.derive_evidence_strength(without))

    def test_novelty_follows_prior_coverage(self):
        self.assertEqual(ranking.derive_novelty({"prior_coverage": 0}), 3)
        self.assertEqual(ranking.derive_novelty({"prior_coverage": 2}), 2)
        self.assertEqual(ranking.derive_novelty({"prior_coverage": 5}), 1)
        # 구 큐 항목은 값이 없다 — 지어내지 않고 중립값
        self.assertEqual(ranking.derive_novelty({}), 2)

    def test_llm_values_are_overridden(self):
        a = item(features=feat(novelty=3, evidence_strength=3), queued_hours_ago=0)
        a.update({"title_kr": "정부, 원전 확대 검토 전망", "summary": "검토할 것으로 예상된다.",
                  "prior_coverage": 4})
        _, b = ranking.score_item(a, CFG, now=NOW)
        # novelty 가중치는 0 이므로 breakdown 에 남지 않는다
        self.assertNotIn("novelty", b)
        # LLM 이 3점을 줬어도 전망 표현이라 코드는 0점 → 기여 0 이라 항목이 사라진다
        self.assertEqual(ranking.derive_evidence_strength(a), 0)
        self.assertNotIn("evidence_strength", b)

    def test_prior_coverage_counts_same_event_only(self):
        prior = ["한수원, 체코 두코바니 원전 본계약 체결", "미국 NRC, SMR 인허가 절차 개편"]
        self.assertEqual(
            ranking.prior_coverage_count("한수원 체코 두코바니 원전 본계약 체결", prior), 1)
        self.assertEqual(
            ranking.prior_coverage_count("프랑스 EDF, 플라망빌 3호기 출력 상승", prior), 0)


class TestTrackingBonus(unittest.TestCase):
    def test_follow_up_outranks_brand_new(self):
        base = dict(features=feat(korea_relevance=1), queued_hours_ago=0)
        new = item(h="a", **base)
        new.update({"title_kr": "원안위, 한울 4호기 임계 허용", "summary": "허용했다.",
                    "prior_coverage": 0})
        follow = item(h="b", **base)
        follow.update({"title_kr": "원안위, 한울 4호기 임계 허용", "summary": "허용했다.",
                       "prior_coverage": 1})
        s_new, _ = ranking.score_item(new, CFG, now=NOW)
        s_follow, b = ranking.score_item(follow, CFG, now=NOW)
        self.assertGreater(s_follow, s_new)
        self.assertIn("tracking:follow_up", b)

    def test_repeated_issue_gets_almost_nothing(self):
        repeat = item(features=feat(), queued_hours_ago=0)
        repeat.update({"title_kr": "원안위, 한울 4호기 임계 허용", "summary": "허용했다.",
                       "prior_coverage": 6})
        _, b = ranking.score_item(repeat, CFG, now=NOW)
        self.assertIn("tracking:repeat", b)
        self.assertLess(b["tracking:repeat"], CFG["tracking"]["follow_up"])


class TestDuplicates(unittest.TestCase):
    def test_same_and_followup_titles_clustered(self):
        a = item(h="a", title="한수원, 체코 두코바니 원전 본계약 체결")
        b = item(h="b", title="한수원 체코 두코바니 원전 본계약을 체결했다")  # 후속·우라까이
        c = item(h="c", title="미국 NRC, NuScale SMR 설계 인증")
        scores = {"a": 10.0, "b": 5.0, "c": 7.0}
        kept, dropped = ranking.cluster_duplicates([a, b, c], scores)
        self.assertEqual({x["hash"] for x in kept}, {"a", "c"})
        self.assertEqual(dropped[0]["hash"], "b")
        self.assertEqual(dropped[0]["dup_of"], "a")  # 점수 높은 쪽이 대표

    def test_distinct_titles_kept(self):
        a = item(h="a", title="폴란드 신규 원전 부지 확정")
        b = item(h="b", title="우라늄 현물가 급등, 카자흐 감산")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 5, "b": 5})
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_paraphrased_same_event_clustered(self):
        """2026-07-13 실전 사례: 같은 사건의 패러프레이즈 (문자열 ratio 0.52)."""
        a = item(h="a", title="반도체 특구 전력 수요, 원전 18기 필요성 제기…전력 수급 불확실성 증대")
        b = item(h="b", title="'반도체 특구'에 원전 18기 필요하다는데…커지는 전력 물음표")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 6, "b": 5})
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["dup_of"], "a")

    def test_same_entity_different_events_not_clustered(self):
        a = item(h="a", title="원안위, 고리2호기 계속운전 심사 착수")
        b = item(h="b", title="원안위, 한빛1호기 계속운전 심사 결과 발표")
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 5})
        self.assertEqual(len(kept), 2)


class TestContainmentDuplicates(unittest.TestCase):
    """포함 비율 판정 — 한쪽이 금액·부제를 덧붙여 자카드가 무너지는 경우."""

    def test_long_headline_absorbs_reworded_twin(self):
        """실전 사례(2026-07-17): 같은 EIB 대출을 두 매체가 다른 어휘로 썼다.

        공유 6 토큰인데 자카드 0.43 으로 0.45 를 놓친다. 짧은 쪽을 분모로 두면 0.60.
        """
        a = item(h="a", title="유럽투자은행(EIB), 체르나보다 원전 1호기 개보수 사업에 8억 유로 대출 승인")
        b = item(h="b", title="유럽투자은행, 루마니아 체르나보다 원전 1호기 설비개선에 8억 유로 지원 확정")
        ta, tb = ranking._title_tokens(a), ranking._title_tokens(b)
        self.assertLess(len(ta & tb) / len(ta | tb), ranking._TOKEN_JACCARD_THRESHOLD)
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 6, "b": 5})
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["dup_of"], "a")

    def test_boilerplate_only_overlap_kept(self):
        """상투어만 겹치면 붙이지 않는다 — 0.57 로 내렸을 때 실제로 붙던 쌍.

        공유 토큰이 미국·원자·협력·체결 뿐인데 포함비율은 0.571 이다. 같은 값에
        실제 중복(포천양수발전소 2건)도 있어 기준값으로는 못 가른다 → 0.60 을 지킨다.
        """
        a = item(h="a", title="미국 해양광물관리국, NRC와 해상 원자력 프로젝트 협력 MOU 체결")
        b = item(h="b", title="미국-사우디아라비아, 민간 원자력 협력 협정 체결")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_pocheon_pair_is_a_known_miss(self):
        """0.60 을 지키는 대가 — 이 실제 중복은 놓친다(0.571).

        놓치는 걸 알고 남긴다. 잡으려면 공유 토큰의 고유명사 여부를 봐야 한다.
        """
        a = item(h="a", title="한수원, 포천양수발전소 본공사 착수…2033년 준공 목표")
        b = item(h="b", title="한수원, 1조 7,508억 원 규모 포천양수발전소 본공사 착수")
        ta, tb = ranking._title_tokens(a), ranking._title_tokens(b)
        cov = len(ta & tb) / min(len(ta), len(tb))
        self.assertLess(cov, ranking._TOKEN_CONTAIN_THRESHOLD)
        self.assertEqual(len(ranking.cluster_duplicates([a, b], {"a": 6, "b": 5})[0]), 2)

    def test_shared_actor_different_events_kept(self):
        """0.55 이하로 내리면 붙던 쌍 — 0.60 에서는 갈라져 있어야 한다."""
        a = item(h="a", title="러시아, 우크라이나 드론 공격으로 민간인 7명 사망 주장")
        b = item(h="b", title="우크라이나 자포리자 지역 러시아 공격으로 1명 사망, 31명 부상")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_three_shared_tokens_left_uncaught_on_purpose(self):
        """공유 3 토큰 구간은 일부러 안 잡는다.

        2026-07-20 김현주 한빛본부장 취임을 네 매체가 쓴 건 실제 중복이라 여기서 놓친다.
        그래도 3 토큰까지 내리면 '원안·계속·심사' 같은 상투어만으로 별개 사건이 붙는다
        (고리2호기 ↔ 한빛1호기가 정확히 0.600). 재현율보다 정확도를 택한 자리다.
        """
        a = item(h="a", title="김현주 한빛원자력본부장 취임, 안전 최우선 및 지역 상생 강조")
        b = item(h="b", title="김현주 신임 한빛원자력본부장 취임")
        self.assertEqual(len(ranking._title_tokens(a) & ranking._title_tokens(b)), 3)
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 6, "b": 5})
        self.assertEqual(len(kept), 2)

    def test_facility_conflict_vetoes_similar_titles(self):
        """서식만 같고 호기가 다르면 유사도가 높아도 붙이지 않는다."""
        a = item(h="a", title="원안위, 고리2호기 계속운전 심사 착수")
        b = item(h="b", title="원안위, 한빛1호기 계속운전 심사 착수")
        self.assertTrue(ranking._facility_conflict(
            ranking._title_facilities(a), ranking._title_facilities(b)))
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)

    def test_containment_needs_minimum_shared_tokens(self):
        """짧은 제목 둘이 우연히 두 어절 겹친 것만으로 붙지 않는다."""
        a = item(h="a", title="원전 수출 확대")
        b = item(h="b", title="원전 수출 전략 재검토와 신규 부지 선정 절차 착수")
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)


class TestSemanticDedupHook(unittest.TestCase):
    """의미 dedup 은 주입식이다 — ranking 은 LLM 을 모른 채 남는다.

    실측으로 확정된 벽: 2026-08-06 웨스팅하우스 한 파트너십이 두 칸을 썼는데 공유
    토큰이 '웨스' 하나였다('어멘텀' vs '아멘텀'). 2026-08-07 에는 다뉴브강 가뭄 한
    사건이 네 칸('팍스' vs '팍시'). 표기가 갈리면 어떤 문자열 기준도 못 넘는다.
    """

    # 제목이 서로 닮으면 기존 문자열 dedup 이 먼저 먹어 후크가 볼 게 남지 않는다.
    DISTINCT = [
        "체코 두코바니 본계약 체결", "폴란드 신규 부지 확정", "우라늄 현물가 급등",
        "프랑스 플라망빌 출력 상승", "일본 가시와자키 재가동 승인",
        "영국 사이즈웰 최종투자결정", "캐나다 다링턴 SMR 착공",
        "인도 쿠단쿨람 6호기 임계", "브라질 앙그라 3호기 공사 재개",
        "스웨덴 신규 원전 금융 지원", "핀란드 올킬루오토 정기 정비",
        "네덜란드 보르셀러 수명 연장", "벨기에 티앙주 영구 정지",
        "스위스 베즈나우 냉각계통 점검", "이집트 엘다바 격납건물 타설",
        "튀르키예 아쿠유 시운전 착수", "아르헨티나 아투차 국산화 확대",
        "멕시코 라구나베르데 출력 증강", "남아공 쿠버그 계속운전 허가",
        "베트남 닌투언 사업 재개 검토",
    ]

    def test_hook_absent_keeps_old_behaviour(self):
        rows = [item(h=f"h{i}", title=t) for i, t in enumerate(self.DISTINCT[:5])]
        sel, diag = ranking.rank_and_select(rows, 3, CFG, now=NOW)
        self.assertEqual(len(sel), 3)
        self.assertEqual(diag["dropped_duplicates"], [])

    def test_hook_drops_are_recorded_with_dup_of(self):
        """큐 정리(prune_hashes)가 dup_of 로 돌아간다 — 안 붙으면 중복이 내일 재등장."""
        rows = [item(h="a", title="웨스팅하우스, 어멘텀과 원전 공통 플랫폼 협력"),
                item(h="b", title="웨스팅하우스, AP1000 잠재력·아멘텀 파트너십 발표"),
                item(h="c", title="폴란드 신규 원전 부지 확정")]
        # 문자열 기준으로는 안 잡히는 것이 전제다
        self.assertEqual(len(ranking.cluster_duplicates(rows, {"a": 9, "b": 8, "c": 7})[1]), 0)

        def fake(articles, scores):
            keep = [a for a in articles if a["hash"] != "b"]
            gone = [dict(a, dup_of="a") for a in articles if a["hash"] == "b"]
            return keep, gone

        sel, diag = ranking.rank_and_select(rows, 3, CFG, now=NOW, semantic_dedup=fake)
        self.assertEqual({a["hash"] for a in sel}, {"a", "c"})
        self.assertEqual([d["hash"] for d in diag["dropped_duplicates"]], ["b"])
        self.assertEqual(diag["dropped_duplicates"][0]["dup_of"], "a")

    def test_hook_only_sees_the_head(self):
        """풀 전체를 LLM 에 보내면 프롬프트가 커지고 인덱스 분할이 깨진다."""
        rows = [item(h=f"h{i}", title=t) for i, t in enumerate(self.DISTINCT)]
        limit = max(3 * ranking.SEMANTIC_HEAD_MULTIPLIER, ranking.SEMANTIC_HEAD_MIN)
        self.assertGreater(len(rows), limit, "상한이 실제로 걸리는 표본이어야 한다")
        seen: list[int] = []

        def fake(articles, scores):
            seen.append(len(articles))
            return list(articles), []

        ranking.rank_and_select(rows, 3, CFG, now=NOW, semantic_dedup=fake)
        self.assertEqual(seen, [limit])

    def test_hook_failure_keeps_everything(self):
        """Gemini 가 죽어도 브리핑은 나가야 한다 — 중복이 남는 게 빈 브리핑보다 낫다."""
        rows = [item(h=f"h{i}", title=t) for i, t in enumerate(self.DISTINCT[:5])]
        sel, diag = ranking.rank_and_select(
            rows, 3, CFG, now=NOW, semantic_dedup=lambda arts, sc: (list(arts), []))
        self.assertEqual(len(sel), 3)
        self.assertEqual(diag["dropped_duplicates"], [])


class TestFacilityDuplicates(unittest.TestCase):
    """호기 지목 기반 판정 — 매체별 곁가지가 달라 제목 유사도가 무너지는 경우."""

    def test_facility_parsing(self):
        f = ranking._title_facilities
        self.assertEqual(f({"title_kr": "고리 3·4호기 계속운전"}), {"고리3", "고리4"})
        self.assertEqual(f({"title_kr": "고리2호기 영구정지"}), {"고리2"})
        self.assertEqual(f({"title_kr": "한빛 1, 2호기 심사"}), {"한빛1", "한빛2"})
        # '신고리 5호기' 가 '고리5' 로 잡히면 다른 호기와 붙는다
        self.assertEqual(f({"title_kr": "신고리 5호기 준공"}), {"신고리5"})
        self.assertEqual(f({"title_kr": "체코 두코바니 본계약"}), frozenset())

    def test_2026_08_06_same_announcement_three_outlets(self):
        """실전 사례: 원안위 한 발표를 세 매체가 각기 다른 곁가지와 함께 실었다.

        상호 포함계수 0.33~0.44 로 제목 유사도 기준은 전부 미달한다.
        """
        common = {"tags": ["#계속운전"]}
        a = item(h="a", title="원안위, 고리 3·4호기 계속운전 올해 하반기 결정 및 처벌법 개정 추진",
                 **common)
        b = item(h="b", title="원안위, 고리 3·4호기 계속운전 연내 결론 및 SMR 규제 가속화",
                 **common)
        c = item(h="c", title="고리 3·4호기 올해, 한빛 1·2호기 내년 계속운전 심사 상정 예정",
                 **common)
        # 전제: 기존 제목 유사도로는 셋 다 서로 남남이다
        for x, y in ((a, b), (a, c), (b, c)):
            self.assertFalse(ranking._same_event(
                ranking._norm_title(x), ranking._title_tokens(x),
                ranking._norm_title(y), ranking._title_tokens(y), 0.82))
        kept, dropped = ranking.cluster_duplicates([a, b, c], {"a": 9, "b": 8, "c": 7})
        self.assertEqual([x["hash"] for x in kept], ["a"])
        self.assertEqual({x["dup_of"] for x in dropped}, {"a"})

    def test_same_facility_without_shared_tag_kept(self):
        """같은 호기라도 맥락이 다르면(공통 태그 없음) 붙이지 않는다."""
        a = item(h="a", title="원안위, 고리 3·4호기 계속운전 심사 상정",
                 tags=["#계속운전"])
        b = item(h="b", title="고리 3·4호기 앞 해상 어업권 보상 협상 결렬",
                 tags=["#지역수용성"])
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_different_facility_same_tag_kept(self):
        a = item(h="a", title="원안위, 고리 3·4호기 계속운전 심사 상정", tags=["#계속운전"])
        b = item(h="b", title="한빛 1·2호기 계속운전 신청서 제출", tags=["#계속운전"])
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)

    def test_missing_tags_falls_back_to_title_similarity(self):
        """태그가 없으면 호기 판정은 쉬고 기존 동작 그대로."""
        a = item(h="a", title="원안위, 고리 3·4호기 계속운전 올해 하반기 결정 및 처벌법 개정 추진")
        b = item(h="b", title="원안위, 고리 3·4호기 계속운전 연내 결론 및 SMR 규제 가속화")
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 4})
        self.assertEqual(len(kept), 2)


class TestDiversity(unittest.TestCase):
    def test_topic_overexposure_penalized(self):
        # smr 이 이미 2건(cap) 선정된 뒤엔 3번째 smr 에 penalty(2.5) →
        # 점수차가 penalty 안이면 다른 주제가 비집고 들어온다 (소프트 페널티 설계)
        items = [item(h=f"s{i}", section="smr", title=f"SMR {i}") for i in range(3)]
        items.append(item(h="x", section="international", title="기타"))
        scores = {"s0": 10, "s1": 9, "s2": 8, "x": 6.5}
        sel = ranking.select_diverse(items, scores, 3, CFG)
        self.assertEqual([a["hash"] for a in sel], ["s0", "s1", "x"])

    def test_topic_penalty_not_absolute(self):
        # 점수차가 penalty 보다 크면 같은 주제라도 그대로 선정 (강한 뉴스 존중)
        items = [item(h=f"s{i}", section="smr", title=f"SMR {i}") for i in range(3)]
        items.append(item(h="x", section="international", title="기타"))
        scores = {"s0": 10, "s1": 9, "s2": 8, "x": 3}
        sel = ranking.select_diverse(items, scores, 3, CFG)
        self.assertEqual([a["hash"] for a in sel], ["s0", "s1", "s2"])

    def test_tie_deterministic(self):
        a = item(h="aaa", title="t1", queued_hours_ago=1)
        b = item(h="bbb", title="t2", queued_hours_ago=1)
        scores = {"aaa": 5.0, "bbb": 5.0}
        sel1 = ranking.select_diverse([a, b], scores, 1, CFG)
        sel2 = ranking.select_diverse([b, a], scores, 1, CFG)
        self.assertEqual(sel1[0]["hash"], sel2[0]["hash"])  # 입력 순서 무관


class TestSelectionFloor(unittest.TestCase):
    """캡은 상한이지 하한이 아니다 — 기준 미달이면 자리를 비운다.

    다만 절대 점수 하한은 쓸 수 없다. must_read 의 37%가 features 결손으로
    _legacy_score 경로를 타 등급 기본값에 고정되기 때문(실측, docs 참조).
    """

    def setUp(self):
        # 하한 14 를 확실히 넘는/못 넘는 항목
        self.high = item(h="high", features=feat(event_type="policy_decision",
                                                 policy_materiality=3,
                                                 korea_relevance=3),
                         title="High scoring item")
        self.low = item(h="low", features=feat(event_type="opinion"),
                        title="Low scoring item")
        self.floor = {"nice_to_know": 14.0}

    def test_floor_none_is_backward_compatible(self):
        items = [self.high, self.low]
        base, _ = ranking.rank_and_select(items, 5, CFG, NOW)
        with_none, _ = ranking.rank_and_select(items, 5, CFG, NOW, None)
        self.assertEqual([a["hash"] for a in base], [a["hash"] for a in with_none])
        self.assertEqual(len(base), 2)

    def test_below_floor_dropped(self):
        sel, diag = ranking.rank_and_select([self.high, self.low], 5, CFG, NOW,
                                            self.floor)
        self.assertEqual([a["hash"] for a in sel], ["high"])
        self.assertEqual([d["hash"] for d in diag["dropped_below_floor"]], ["low"])
        self.assertEqual(diag["candidate_count"], 2)

    def test_score_equal_to_floor_is_kept(self):
        """경계는 포함. >= 로 고정한다."""
        scores = {"x": 14.0}
        ok, _ = ranking.floor_verdict(item(h="x", features=feat()), scores,
                                      {"nice_to_know": 14.0})
        self.assertTrue(ok)
        ok2, _ = ranking.floor_verdict(item(h="x", features=feat()), {"x": 13.99},
                                       {"nice_to_know": 14.0})
        self.assertFalse(ok2)

    def test_must_read_is_not_exempt_by_grade(self):
        """등급 면제는 제거했다 — 등급이 점수를 무조건 이기면 하한이 무의미해진다.

        must_read 의 상당수가 LLM 판정이 아니라 큐레이션 실패 폴백이 붙인 값이었다.
        등급을 점수 위에 두려면 등급을 믿을 수 있어야 한다.
        근거: docs/AS_IS.md C1′.
        """
        weak = item(h="mr", importance="must_read", features=feat(event_type="opinion"))
        ok, reason = ranking.floor_verdict(weak, {"mr": 1.0}, {"must_read": 99.0})
        self.assertFalse(ok)
        self.assertEqual(reason, "below_floor")

    def test_must_read_passes_when_no_limit_configured(self):
        """운영 설정에는 must_read 하한이 없다 — 등급별 하한을 안 걸면 통과한다."""
        weak = item(h="mr", importance="must_read", features=feat(event_type="opinion"))
        ok, reason = ranking.floor_verdict(weak, {"mr": 1.0}, {"nice_to_know": 14.0})
        self.assertTrue(ok)
        self.assertEqual(reason, "no_limit_for_grade")

    def test_missing_features_exempt_even_for_graded_floor(self):
        """결손 면제는 등급 하한보다 먼저 걸린다 — 데이터 결손을 중요도로 읽지 않는다."""
        legacy = item(h="mr", importance="must_read")  # features 키 자체가 없음
        ok, reason = ranking.floor_verdict(legacy, {"mr": 1.0}, {"must_read": 99.0})
        self.assertTrue(ok)
        self.assertEqual(reason, "exempt_no_features")

    def test_missing_features_exempt(self):
        """features 결손은 데이터 문제이지 중요도 문제가 아니다."""
        legacy = item(h="lg")  # features 키 자체가 없음
        self.assertIsNone(ranking.sanitize_features(legacy.get("features")))
        ok, reason = ranking.floor_verdict(legacy, {"lg": 5.0}, {"nice_to_know": 14.0})
        self.assertTrue(ok)
        self.assertEqual(reason, "exempt_no_features")

    def test_floor_applied_before_diversity_penalty(self):
        """다양성 페널티가 하한 판정에 섞이면 '주제가 겹쳐서' 잘리게 된다."""
        strong = feat(event_type="policy_decision", policy_materiality=3,
                      korea_relevance=3)
        # 제목이 서로 안 닮아야 중복 클러스터에 안 걸린다(임계 0.82)
        titles = ["체코 두코바니 본계약 체결",
                  "미국 NRC 인허가 규정 개정 의결",
                  "프랑스 EDF 연료 재처리 계약 갱신"]
        trio = [item(h=f"t{i}", section="smr", features=strong, title=t)
                for i, t in enumerate(titles)]
        sel, diag = ranking.rank_and_select(trio, 3, CFG, NOW, self.floor)
        # 셋 다 하한을 넘으므로 페널티를 받아도 하한에서 탈락하지 않는다
        self.assertEqual(diag["dropped_below_floor"], [])
        self.assertEqual(len(sel), 3)

    def test_resolve_floor_reads_region(self):
        cfg = {"selection_floor": {"_comment": "무시",
                                   "nice_to_know": {"domestic": 12.0, "overseas": 15.0}}}
        self.assertEqual(ranking.resolve_floor(cfg, "domestic"), {"nice_to_know": 12.0})
        self.assertEqual(ranking.resolve_floor(cfg, "overseas"), {"nice_to_know": 15.0})
        self.assertIsNone(ranking.resolve_floor({}, "domestic"))

    def test_resolve_floor_accepts_flat_number(self):
        cfg = {"selection_floor": {"nice_to_know": 13.0}}
        self.assertEqual(ranking.resolve_floor(cfg, "domestic"), {"nice_to_know": 13.0})

    def test_repo_config_floor_is_region_symmetric(self):
        """국내가 불리하다는 1차 가설은 실측 분포로 기각됐다 — 같은 값을 유지한다."""
        cfg = ranking.load_config()
        self.assertEqual(ranking.resolve_floor(cfg, "domestic"),
                         ranking.resolve_floor(cfg, "overseas"))


class TestConfig(unittest.TestCase):
    def test_missing_config_falls_back(self):
        cfg = ranking.load_config(Path("no_such_file.json"))
        self.assertIn("importance_base", cfg)

    def test_repo_config_loads(self):
        cfg = ranking.load_config()
        self.assertEqual(cfg["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()


class TestAdaptiveCap(unittest.TestCase):
    """캡이 상한이 아니라 하한처럼 작동하던 것을 고친 뒤의 계약.

    실측: 2026-07-25~08-06 12일 연속 노출이 7~9건으로 고정됐다. 08-06 큐(82건)에서
    하한을 넘긴 적격이 국내 16 · 해외 13 인데 나간 것은 3 과 6 이었다.
    """

    SPEC = {"base": 3, "max": 8, "must_read_bonus_per": 1, "must_read_bonus_max": 2,
            "eligible_bonus_step": 5, "eligible_bonus_max": 3}

    def test_quiet_day_never_exceeds_what_exists(self):
        cap, detail = ranking.decide_cap(self.SPEC, eligible=2, must_read=0)
        self.assertEqual(2, cap)
        self.assertEqual(2, detail["eligible"])

    def test_zero_eligible_is_zero(self):
        self.assertEqual(0, ranking.decide_cap(self.SPEC, eligible=0, must_read=0)[0])

    def test_busy_day_never_exceeds_max(self):
        cap, _ = ranking.decide_cap(self.SPEC, eligible=200, must_read=50)
        self.assertEqual(self.SPEC["max"], cap)

    def test_must_read_bonus_is_capped(self):
        _, detail = ranking.decide_cap(self.SPEC, eligible=100, must_read=9)
        self.assertEqual(self.SPEC["must_read_bonus_max"], detail["must_read_bonus"])

    def test_volume_alone_can_widen_the_cap(self):
        """must_read 는 19일 중 11일이 0건이다 — 그것만 보면 캡이 영영 안 움직인다."""
        cap, detail = ranking.decide_cap(self.SPEC, eligible=16, must_read=0)
        self.assertGreater(cap, self.SPEC["base"])
        self.assertEqual(0, detail["must_read_bonus"])
        self.assertGreater(detail["eligible_bonus"], 0)

    def test_volume_bonus_is_capped(self):
        _, detail = ranking.decide_cap(self.SPEC, eligible=500, must_read=0)
        self.assertEqual(self.SPEC["eligible_bonus_max"], detail["eligible_bonus"])

    def test_surge_bonus_is_off_until_r7(self):
        # 확대 원인이 둘이면 어느 쪽인지 못 가른다 — surge 는 별도 릴리스에서 켠다.
        _, detail = ranking.decide_cap(self.SPEC, eligible=16, must_read=1)
        self.assertEqual(0, detail["surge_bonus"])

    def test_detail_is_structured_not_a_sentence(self):
        """'캡에 걸림' 한 줄로는 base 를 올릴지 max 를 올릴지 수집을 늘릴지 못 가른다."""
        _, detail = ranking.decide_cap(self.SPEC, eligible=16, must_read=1)
        for key in ("base", "max", "eligible", "must_read", "must_read_bonus",
                    "eligible_bonus", "surge_bonus", "cap_before_limits", "cap_applied"):
            self.assertIn(key, detail)

    def test_missing_config_falls_back_to_the_old_constants(self):
        self.assertIsNone(ranking.resolve_caps({}, "domestic"))
        self.assertIsNone(ranking.resolve_caps({"selection_caps": "쓰레기"}, "domestic"))
        self.assertIsNone(ranking.resolve_caps({"selection_caps": {}}, "domestic"))

    def test_shipped_config_keeps_both_regions(self):
        for region, base in (("domestic", 3), ("overseas", 6)):
            spec = ranking.resolve_caps(CFG, region)
            self.assertIsNotNone(spec, f"{region} 캡 설정이 없다")
            self.assertEqual(base, spec["base"])
            self.assertGreaterEqual(spec["max"], spec["base"])

    def test_cap_is_decided_after_the_floor(self):
        """앞에서 정하면 하한에 걸려 사라질 후보까지 세어 캡이 부푼다.

        features 를 채워야 하한이 실제로 걸린다 — 결손 레코드는 floor_verdict 가
        면제한다(큐레이션 실패를 중요도로 오독하지 않으려고).
        """
        feats = {"event_type": "other", "korea_relevance": 0,
                 "market_materiality": 0, "policy_materiality": 0,
                 "report_worthiness": 0}
        low = [item(h=f"low{i}", importance="nice_to_know",
                    title=f"서로 다른 제목 {i}", features=dict(feats)) for i in range(20)]
        selected, diag = ranking.rank_and_select(
            low, 3, CFG, NOW, {"nice_to_know": 999.0}, cap_spec=self.SPEC)
        self.assertEqual([], selected)
        self.assertEqual(0, diag["cap"]["eligible"])
        self.assertEqual(0, diag["cap"]["cap_applied"])

    def test_eligible_counts_survivors_not_raw_candidates(self):
        feats = {"event_type": "other", "korea_relevance": 0,
                 "market_materiality": 0, "policy_materiality": 0,
                 "report_worthiness": 0}
        pool = [item(h=f"x{i}", importance="nice_to_know",
                     title=f"서로 다른 제목 {i}", features=dict(feats)) for i in range(20)]
        _, diag = ranking.rank_and_select(pool, 3, CFG, NOW, None, cap_spec=self.SPEC)
        self.assertLessEqual(diag["cap"]["eligible"], len(pool))
