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

    def test_must_read_always_exempt(self):
        weak = item(h="mr", importance="must_read", features=feat(event_type="opinion"))
        ok, reason = ranking.floor_verdict(weak, {"mr": 1.0}, {"nice_to_know": 14.0,
                                                               "must_read": 99.0})
        self.assertTrue(ok)
        self.assertEqual(reason, "exempt_grade")

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
