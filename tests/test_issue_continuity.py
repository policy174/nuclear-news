"""issue_continuity.py — 연속일 반복 게이트 회귀 테스트.

사용자가 지목한 사례를 그대로 재현한다 (2026-08-17):
  · 동일 이슈 + 내용 변화 없음      → 다음날 반복 선정 확률이 낮아진다
  · 동일 이슈 + MOU→본계약          → 중요한 후속으로 유지된다
  · 전혀 다른 신규 중요 기사        → 기존과 똑같이 경쟁한다
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import issue_continuity as continuity
import ranking

NOW = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
TODAY = "2026-08-17"
YESTERDAY = "2026-08-16"
CFG = ranking.load_config()

# V1 적응(5차 이식): 판정 모듈은 들여왔지만 ranking 이 continuity 를 읽는 배선은
# 백테스트 게이트(docs/ 채점표) 통과 후에 이식한다. 배선 통합 테스트는 그때까지
# 잠근다 — V2 ranking 은 `_continuity_of` 를 노출하므로 배선이 이식되면 자동으로
# 다시 돈다.
RANKING_WIRED = hasattr(ranking, "_continuity_of")
_WIRING_SKIP = unittest.skipUnless(
    RANKING_WIRED, "ranking 의 continuity 배선 미이식 (백테스트 게이트 후)")


def article(title, *, h="c1", summary="", fingerprint=None, tags=None,
            importance="nice_to_know", features=None, prior_coverage=None,
            section="domestic", domain="example.co.kr"):
    row = {
        "hash": h, "title_kr": title, "title": title, "summary": summary,
        "importance": importance, "section": section, "domain": domain,
        "link": f"https://{domain}/{h}",
        "queued_at": (NOW - timedelta(hours=2)).isoformat(),
        "tags": tags or [],
        "story_fingerprint": fingerprint or {},
    }
    if features is not None:
        row["features"] = features
    if prior_coverage is not None:
        row["prior_coverage"] = prior_coverage
    return row


def sent(title, *, h="p1", date=YESTERDAY, summary="", fingerprint=None,
         tags=None, region="국내", members=None):
    row = {"date": date, "hash": h, "title_kr": title, "title": title,
           "summary": summary, "region": region, "tags": tags or [],
           "story_fingerprint": fingerprint or {}}
    if members is not None:
        row["story_members"] = _members(h, members)
    return row


def _members(own, hashes):
    """story_members 모양의 근거 목록. hash 만 중요하다."""
    return [{"hash": h, "title": f"근거 {h}", "publisher": "x", "fold_stage": "collect_fold"}
            for h in [own, *hashes]]


def with_members(row, hashes):
    """후보 기사에 근거 목록을 달아 준다 (story 가 접힌 뒤의 모습)."""
    row["story_members"] = _members(row["hash"], hashes)
    return row


def feat(**kw):
    base = {"event_type": "contract_award", "korea_relevance": 3,
            "market_materiality": 2, "policy_materiality": 2,
            "novelty": 0, "evidence_strength": 0, "report_worthiness": 0}
    base.update(kw)
    return base


# ---- ① 같은 이슈인가 -----------------------------------------------------------

class SameIssueTests(unittest.TestCase):
    def test_legacy_fingerprint_bridges_the_same_terrapower_event_after_seven_days(self):
        prior = sent(
            "현대건설·테라파워, SMR 사업 협력 계약 체결", h="old",
            date="2026-08-10",
            fingerprint={"countries": ["South Korea", "USA"],
                         "actors": ["Hyundai E&C", "TerraPower", "Export-Import Bank"],
                         "assets": ["SMR"], "event_family": "contract_award"})
        cand = article(
            "테라파워와 현대건설의 차세대 원자로 협력 본격화", h="new",
            fingerprint={"countries": ["South Korea", "USA"],
                         "actors": ["Hyundai E&C", "TerraPower"],
                         "assets": ["SMR"], "event_family": "contract_award"},
            features=feat())

        verdict = continuity.verdict_for(
            cand, [prior], continuity.DEFAULT_CONFIG, TODAY)

        self.assertTrue(verdict["identity_confirmed"])
        self.assertEqual(verdict["identity_method"], "fingerprint_anchors")
        self.assertEqual(verdict["progression"], "none")
        self.assertTrue(verdict["drop"])

    def test_country_and_event_family_alone_never_bridge_unrelated_events(self):
        prior = sent(
            "프랑스 정부 신규 원전 금융 계획 발표",
            fingerprint={"countries": ["France"], "event_family": "policy_decision"})
        cand = article(
            "EDF 원전 정비 일정 변경",
            fingerprint={"countries": ["France"], "event_family": "policy_decision"})
        self.assertIsNone(
            continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_title_variation_matches(self):
        """실측 2026-08-15/16 — 표기가 갈려도(알마라즈/알마라스) 같은 이슈다."""
        prior = sent("스페인, 알마라즈 원전 운영 기한 2030년까지 연장")
        cand = article("스페인 정부, 알마라스(Almaraz) 원전 운영 연장 승인")
        match = continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(match["similarity"], 0.62)

    def test_low_title_similarity_matches_by_anchor(self):
        """실측 2026-08-16 테라파워 두 건 — 제목 유사도 0.36 은 어떤 문턱에도 못 미친다.

        같은 당사자(TerraPower·두산에너빌리티)가 같은 사안을 이어 간다는 사실은
        제목이 아니라 앵커·fingerprint 에서 나온다.
        """
        prior = sent(
            "테라파워-한국 기업 SMR 협력, 한국 원전기업 수출 공급망 확대",
            fingerprint={"countries": ["South Korea", "USA"],
                         "actors": ["TerraPower", "Doosan Enerbility"],
                         "assets": ["Natrium SMR"]})
        cand = article(
            "두산에너빌리티, 美 테라파워 차세대 SMR 핵심 기자재 공급 계약 체결",
            fingerprint={"countries": ["USA"],
                         "actors": ["TerraPower", "Doosan Enerbility", "HD Hyundai"],
                         "assets": ["Kemmerer SMR"]})
        self.assertLess(continuity.title_similarity(cand, prior), 0.62)
        match = continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG)
        self.assertIsNotNone(match)
        self.assertTrue(any(r.startswith("anchors:") for r in match["reasons"]))

    def test_one_word_title_does_not_contain_everything(self):
        """어절 한둘짜리 제목이 포함비율 1.0 으로 아무 데나 붙으면 안 된다.

        실측 2026-08-17: `옛 기사`(토큰 {기사}) 가 `미국 에너지부, … 관련 기사
        발표` 와 유사도 1.0 으로 붙었다 — 포함비율의 분모가 1 이었기 때문이다.
        창이 하루였을 때는 거의 안 드러났지만, 14일 창에서는 조용히 기사를
        후보에서 지운다.
        """
        prior = sent("미국 에너지부, 80년 수명 연장된 원전 '80 클럽' 관련 기사 발표")
        cand = article("옛 기사")
        self.assertLess(continuity.title_similarity(cand, prior), 0.62)
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_contained_title_still_matches_when_long_enough(self):
        """짧은 쪽이 제 길이를 갖췄으면 포함비율은 그대로 근거다 (실측 IAEA 성명)."""
        prior = sent("IAEA 사무총장, 우크라이나 상황 관련 성명 발표")
        cand = article("IAEA 사무총장, 우크라이나 상황 관련 362차 성명 발표")
        self.assertGreaterEqual(continuity.title_similarity(cand, prior), 0.85)

    def test_unrelated_articles_do_not_match(self):
        prior = sent("한전·가스공사 상반기 이자비용 2.7조원")
        cand = article("정부, 영호남 산업용 전기요금 최대 10% 인하 방침")
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_facility_conflict_vetoes_match(self):
        """호기가 어긋나면 서식이 같아도 같은 사건일 수 없다 (기존 거부권과 같은 규칙)."""
        prior = sent("원안위, 고리 2호기 계속운전 심사 착수")
        cand = article("원안위, 한빛 1호기 계속운전 심사 결과 발표")
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_shared_generic_anchor_alone_does_not_match(self):
        """'SMR' 만 공유하는 다른 사업자 기사는 붙지 않는다."""
        prior = sent("테라파워 나트륨 SMR, 美 와이오밍주 건설 현황")
        cand = article("X-에너지, 텍사스 SMR 프로젝트에 10억 달러 공적 자금 확보")
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_generic_anchors_measured_not_listed(self):
        """그날의 유행어는 목록이 아니라 풀에서 세어 정한다."""
        pool = [article(f"SMR 관련 기사 {i} 공급망 확대", h=f"h{i}") for i in range(12)]
        generic = continuity.generic_anchors(pool)
        self.assertIn("smr", generic)
        self.assertNotIn("terrapower", generic)

    def test_common_noun_alone_does_not_match(self):
        """실측 2026-08-17 오탐 — '차세대' 하나로 무관한 두 기사가 붙었다."""
        prior = sent("정부, SMR·차세대 태양광을 미래 성장동력으로 육성 발표")
        cand = article("빌 게이츠 방한, AI 전력전쟁 속 빅테크의 차세대 원전 선점 동향")
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_word_tail_alone_does_not_match(self):
        """'시장서'·'본격화' 같은 어절 꼬리는 이름이 아니다."""
        prior = sent("LS일렉트릭, 북미 데이터센터 배전 시장서 1.2조 원 수주")
        cand = article("엔비디아, AI 인프라 시장서 GPU 공급 넘어 운영 역할 확대")
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_named_anchor_pair_still_matches(self):
        prior = sent("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결")
        cand = article("두산에너빌리티, 美 테라파워 345MW급 나트륨 원자로 기자재 제작")
        self.assertIsNotNone(
            continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_named_anchors_exclude_domain_acronyms(self):
        named = continuity.named_anchors(
            article("테라파워, SMR 및 ESS 연계 사업 추진"))
        self.assertIn("테라파워", named)
        self.assertNotIn("smr", named)
        self.assertNotIn("ess", named)


# ---- ② 단계가 움직였는가 --------------------------------------------------------

class ProgressionTests(unittest.TestCase):
    def verdict(self, before, after, **kw):
        return continuity.progression(sent(before), article(after, **kw))["verdict"]

    def test_mou_to_contract_is_material(self):
        self.assertEqual(
            self.verdict("한수원, 필리핀 바탄 원전 협력 양해각서(MOU) 체결",
                         "한수원, 필리핀 바탄 원전 복구 본계약 체결"),
            "material")

    def test_cooperation_to_supply_contract_is_material(self):
        """사용자 지목: '협력 논의 → 공급 계약 체결'."""
        self.assertEqual(
            self.verdict("테라파워-한국 기업 SMR 협력, 한국 원전기업 수출 공급망 확대",
                         "두산에너빌리티, 美 테라파워 차세대 SMR 핵심 기자재 공급 계약 체결"),
            "material")

    def test_review_to_approval_is_material(self):
        self.assertEqual(
            self.verdict("원안위, 고리 2호기 계속운전 심사 착수",
                         "원안위, 고리 2호기 계속운전 최종 승인"),
            "material")

    def test_plan_to_construction_is_material(self):
        self.assertEqual(
            self.verdict("한수원, 신규 원전 2기 건설 계획 발표",
                         "한수원, 신규 원전 2기 착공"),
            "material")

    def test_bid_to_preferred_bidder_is_material(self):
        self.assertEqual(
            self.verdict("체코 신규 원전 입찰 마감, 3사 경쟁",
                         "체코 신규 원전, 한수원 우선협상대상자 선정"),
            "material")

    def test_forecast_to_announcement_is_material(self):
        self.assertEqual(
            self.verdict("정부, 신규 원전 부지 이달 중 선정 전망",
                         "정부, 신규 원전 부지 경북 영덕 공식 발표"),
            "material")

    def test_scope_expansion_is_material(self):
        """사용자 지목: '기존 계약 → 계약 규모 확대'. 수치가 함께 새로 붙어야 한다."""
        self.assertEqual(
            self.verdict("두산에너빌리티, 테라파워에 원자로 기자재 공급계약 체결",
                         "두산에너빌리티, 테라파워 공급계약 규모 확대…2조원 추가 수주"),
            "material")

    def test_expansion_word_without_numbers_is_not_material(self):
        self.assertNotEqual(
            self.verdict("두산에너빌리티, 테라파워에 원자로 기자재 공급계약 체결",
                         "두산에너빌리티, 테라파워 공급계약 범위 확대"),
            "material")

    def test_one_extra_word_is_not_progression(self):
        """실측 2026-08-17 — 한 낱말만 늘어난 재탕이 요약의 숫자 하나로 감점 절반을 받았다."""
        verdict = continuity.progression(
            sent("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결"),
            article("두산에너빌리티, 美 테라파워 차세대 SMR 핵심 기자재 공급 계약 체결",
                    summary="계약 규모는 345MW급 원자로 기자재다."))
        self.assertEqual(verdict["verdict"], "none")

    def test_restatement_guard_does_not_block_real_advance(self):
        """제목이 닮았어도 척도가 올라갔으면 material 이다."""
        verdict = continuity.progression(
            sent("한수원, 필리핀 원전 협력 양해각서 체결"),
            article("한수원, 필리핀 원전 협력 본계약 체결"))
        self.assertEqual(verdict["verdict"], "material")

    def test_retitled_restatement_is_none(self):
        """사실관계가 같고 제목만 바뀐 재인용."""
        self.assertEqual(
            self.verdict("한수원, 체코 두코바니 원전 본계약 체결",
                         "체코 두코바니 원전, 한수원과 본계약 체결"),
            "none")

    def test_unmarked_prior_is_only_minor(self):
        """어제가 척도에 아무 말도 안 했으면 '넘어갔다'고 단정하지 않는다.

        실측 알마라즈: 어제 '운영 기한 2030년까지 연장' 은 이미 승인 사실인데
        승인이라는 낱말이 없다. material 로 두면 표현만 바꾼 재탕이 면제된다.
        """
        self.assertEqual(
            self.verdict("스페인, 알마라즈 원전 운영 기한 2030년까지 연장",
                         "스페인 정부, 알마라스(Almaraz) 원전 운영 연장 승인"),
            "minor")

    def test_state_flip_is_material(self):
        verdict = continuity.progression(sent("한빛 3호기 가동중단"),
                                         article("한빛 3호기 재가동"))
        self.assertEqual(verdict["verdict"], "material")
        self.assertEqual(verdict["kind"], "stage_flip")

    def test_stage_flip_needs_prior_stage(self):
        """어제가 침묵했으면 상태 전환으로 세지 않는다 (stage_conflict 와 같은 보수성)."""
        self.assertNotEqual(
            continuity.progression(sent("한빛 3호기 관련 지역 설명회"),
                                   article("한빛 3호기, 업계 관심 집중"))["verdict"],
            "material")

    def test_roundup_original_title_does_not_leak_stages(self):
        """묶음 기사의 원문 제목이 카드에 없는 단계를 들여오면 안 된다.

        실측 2026-08-17 (g-enews, 원문 확인): 기사 본문이 캐나다 우라늄 광산
        착공으로 시작해 중간에 스페인 알마라즈 연장을 다룬다. 큐레이션이 원자력
        꼭지를 카드 제목으로 뽑는 것 자체는 옳지만, 두 제목을 합쳐 읽으면
        '착공'(캐나다)이 딸려 와 스페인 반복 보도가 '상태 전환'으로 면제됐다.
        """
        cand = {
            "title_kr": "스페인 알마라즈 원전 수명 2030년까지 연장",
            "title": "캐나다, 우라늄 광산 착공…세계 공급 20% 생산",
            "summary": "스페인 정부가 알마라즈 원전 가동 시한을 2030년까지 연장하기로 결정했다.",
        }
        self.assertEqual(continuity.subject_titles(cand), (cand["title_kr"],))
        self.assertEqual(continuity._stages(cand), frozenset())
        self.assertNotEqual(
            continuity.progression(sent("스페인 정부, 알마라스 원전 운영 연장 승인"),
                                   cand)["verdict"],
            "material")

    def test_translated_title_still_contributes_stages(self):
        """원문이 외국어면 번역 관계다 — 번역이 떨어뜨린 표현을 원문에서 줍는다."""
        row = {"title_kr": "스페인, 알마라즈 원전 운영 연장", "title": "Spain restarts Almaraz"}
        self.assertEqual(len(continuity.subject_titles(row)), 2)
        self.assertIn("restart", continuity._stages(row))

    def test_restart_is_not_read_as_permit_approval(self):
        """'재가동'의 앞 두 글자가 '재가'(裁可)로 잡히면 재가동이 인허가 승인이 된다."""
        self.assertEqual(continuity.scale_tier(article("한빛 3호기 재가동"), "permit"), -1)


# ---- ③ 점수에 실제로 반영되는가 -------------------------------------------------

@_WIRING_SKIP
class ScoreEffectTests(unittest.TestCase):
    def score(self, item):
        return ranking.score_item(item, CFG, NOW)

    def test_repeat_without_progression_loses_points(self):
        """동일 이슈 + 내용 변화 없음 → 다음날 반복 선정 확률 감소."""
        base = article("체코 두코바니 원전, 한수원과 본계약 체결",
                       features=feat(), prior_coverage=1)
        clean_score, _ = self.score(base)

        repeated = dict(base)
        continuity.annotate([repeated],
                            [sent("한수원, 체코 두코바니 원전 본계약 체결")],
                            CFG, TODAY)
        self.assertEqual(repeated["continuity"]["progression"], "none")
        repeat_score, breakdown = self.score(repeated)

        self.assertLess(repeat_score, clean_score)
        # 감점과 tracking 가점 취소가 함께 걸린다 — 하나만으로는 상쇄된다.
        self.assertIn("continuity:none", breakdown)
        self.assertIn("tracking:follow_up:cancelled", breakdown)
        self.assertGreaterEqual(clean_score - repeat_score, 5.0)

    def test_material_progression_keeps_score(self):
        """동일 이슈 + MOU→본계약 → 중요한 후속 기사로 유지."""
        base = article("한수원, 필리핀 바탄 원전 복구 본계약 체결",
                       features=feat(), prior_coverage=1)
        clean_score, _ = self.score(base)

        follow_up = dict(base)
        continuity.annotate([follow_up],
                            [sent("한수원, 필리핀 바탄 원전 협력 양해각서(MOU) 체결")],
                            CFG, TODAY)
        self.assertEqual(follow_up["continuity"]["progression"], "material")
        follow_score, breakdown = self.score(follow_up)

        self.assertLessEqual(clean_score - follow_score, 1.0)
        self.assertNotIn("tracking:follow_up:cancelled", breakdown)

    def test_unrelated_new_article_is_untouched(self):
        """전혀 다른 신규 중요 기사 → 기존과 동일하게 정상 경쟁."""
        fresh = article("정부, 영호남 산업용 전기요금 최대 10% 인하 방침",
                        features=feat(event_type="policy_decision"), prior_coverage=0)
        before, _ = self.score(fresh)
        continuity.annotate([fresh], [sent("한전·가스공사 상반기 이자비용 2.7조원")],
                            CFG, TODAY)
        self.assertNotIn("continuity", fresh)
        after, breakdown = self.score(fresh)
        self.assertEqual(before, after)
        self.assertFalse([k for k in breakdown if k.startswith("continuity")])

    def penalty_at(self, before, after, date, **kw):
        item = article(after, features=feat(), **kw)
        continuity.annotate([item], [sent(before, date=date)], CFG, TODAY)
        return (item.get("continuity") or {}).get("penalty", 0.0)

    def test_penalty_holds_through_the_window(self):
        """사용자 지목 ① — 사흘 나흘 전 반복이 그새 식어 있으면 안 된다.

        예전에는 발송 당일부터 하루 20% 씩 깎여 3일 전 반복이 이미 40% 였다.
        지금은 창(repeat_window_days) 안에서는 만액이다. 실측 사례를 그대로 쓴다
        (2026-08-02 → 08-05 헝가리 팍스 원전, 같은 이슈에 새 단계 없음).
        """
        before = "헝가리 총리, 팍스 원전 일요일 가동 중단 발표"
        after = "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표"
        full = self.penalty_at(before, after, "2026-08-16")     # 하루 전
        self.assertEqual(full, 5.0)
        for day in ("2026-08-14", "2026-08-11", "2026-08-10"):  # 3·6·7일 전
            self.assertEqual(self.penalty_at(before, after, day), full)

    def test_penalty_decays_outside_the_window(self):
        """창을 넘어서면 식는다 — 한 달째 이어지는 이슈까지 잡지는 않는다."""
        before = "헝가리 총리, 팍스 원전 일요일 가동 중단 발표"
        after = "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표"
        self.assertLess(self.penalty_at(before, after, "2026-08-08"),   # 9일 전
                        self.penalty_at(before, after, "2026-08-10"))   # 7일 전

    def test_anchor_only_match_uses_the_narrow_window(self):
        """이름만 공유해 붙은 매칭은 예전 폭 그대로다.

        실측 2026-08-17: 3~14일 간격 앵커 단독 매칭 5쌍 중 3쌍이 오탐이었다.
        창을 넓히는 것은 제목·fingerprint 로 붙은 매칭에만 해당한다.
        """
        fp = {"actors": ["TerraPower", "Doosan Enerbility"]}
        prior = sent("테라파워-한국 기업 SMR 협력, 수출 공급망 확대",
                     date="2026-08-14", fingerprint=fp)
        item = article("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 제작 착수",
                       features=feat(), fingerprint=fp)
        continuity.annotate([item], [prior], CFG, TODAY)
        cont = item["continuity"]
        self.assertTrue(all(r.startswith("anchors:") for r in cont["match_reasons"]))
        self.assertEqual(cont["window_days"], 1)
        self.assertLess(cont["penalty"], 5.0)

    def test_third_send_is_penalised_harder(self):
        """창 안에서 세 번째로 오는 이슈. 실측: 중국 8기 승인이 나흘에 세 번 나갔다."""
        before = "헝가리 총리, 팍스 원전 일요일 가동 중단 발표"
        after = "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표"
        twice = article(after, features=feat())
        continuity.annotate([twice], [sent(before, h="s1", date="2026-08-15"),
                                      sent(before, h="s2", date="2026-08-13")],
                           CFG, TODAY)
        self.assertEqual(twice["continuity"]["repeat_streak"], 2)
        self.assertGreater(twice["continuity"]["penalty"],
                           self.penalty_at(before, after, "2026-08-15"))

    def test_fully_decayed_match_leaves_no_verdict(self):
        """감쇠가 0 이면 판정을 남기지 않는다.

        남기면 감점 0 에 tracking 취소만 살아남아, '닷새 전에 비슷한 게 있었다'는
        이유로 오늘 기사가 조용히 1.5점을 잃는다 (실측 2026-08-17 큐에서 8건).
        """
        item = article("헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표",
                       features=feat())
        continuity.annotate(
            [item],
            [sent("헝가리 총리, 팍스 원전 일요일 가동 중단 발표", date="2026-08-06")],
            CFG, TODAY)
        self.assertNotIn("continuity", item)

    def test_empty_history_disables_the_gate(self):
        item = article("체코 두코바니 원전, 한수원과 본계약 체결", features=feat())
        diag = continuity.annotate([item], [], CFG, TODAY)
        self.assertEqual(diag["matched"], 0)
        self.assertNotIn("continuity", item)


# ---- ④ 선정에서 빠지는가 --------------------------------------------------------

@_WIRING_SKIP
class SelectionTests(unittest.TestCase):
    def test_near_identical_repeat_is_dropped_from_candidates(self):
        repeat = article("한수원, 체코 두코바니 원전 본계약 체결",
                         h="r1", features=feat(), importance="must_read")
        fresh = article("원안위, 새울 3호기 운영허가 심사 결과 의결",
                        h="f1", features=feat(event_type="regulatory_action"))
        continuity.annotate([repeat, fresh],
                            [sent("한수원, 체코 두코바니 원전 본계약 체결", h="old")],
                            CFG, TODAY)
        self.assertTrue(repeat["continuity"]["drop"])

        selected, diag = ranking.rank_and_select([repeat, fresh], 5, CFG, NOW)
        self.assertEqual([a["hash"] for a in selected], ["f1"])
        self.assertEqual(len(diag["dropped_repeat"]), 1)
        self.assertEqual(diag["dropped_repeat"][0]["hash"], "r1")

    def test_material_progression_survives_selection(self):
        follow_up = article("한수원, 필리핀 바탄 원전 복구 본계약 체결",
                            h="f2", features=feat(), importance="must_read")
        continuity.annotate(
            [follow_up],
            [sent("한수원, 필리핀 바탄 원전 협력 양해각서(MOU) 체결", h="old")],
            CFG, TODAY)
        selected, diag = ranking.rank_and_select([follow_up], 5, CFG, NOW)
        self.assertEqual([a["hash"] for a in selected], ["f2"])
        self.assertEqual(diag["dropped_repeat"], [])

    def test_restatement_is_excluded_for_two_weeks(self):
        """사용자 지목 ② — 제목·내용이 사실상 같은 재전송은 한참 뒤에 와도 뺀다.

        실측 2026-08-17: `IAEA 사무총장, 우크라이나 상황 관련 성명 발표` 가
        7/24 · 8/02 · 8/15 세 번 나갔다(제목 동일). 예전 게이트는 어제분(max_days
        =1)만 봤으므로 두 번째·세 번째를 통과시켰다.
        """
        for day, expected in (("2026-08-16", True),   # 하루 전
                              ("2026-08-08", True),   # 9일 전 — 실측 IAEA 간격
                              ("2026-08-03", True),   # 14일 전 — 창의 끝
                              ("2026-08-01", False)): # 16일 전 — 창 밖
            item = article("체코 두코바니 원전, 한수원과 본계약 체결",
                           h="r3", features=feat())
            continuity.annotate(
                [item],
                [sent("한수원, 체코 두코바니 원전 본계약 체결", h="old", date=day)],
                CFG, TODAY)
            self.assertEqual(bool((item.get("continuity") or {}).get("drop")),
                             expected, f"prior={day}")

    def test_old_repeat_needs_a_verbatim_title_to_be_dropped(self):
        """8~14일 구간은 낱말 하나만 달라도 지우지 않는다 — 감점까지다.

        실측 2026-08-17 큐: `웨스팅하우스-아멘텀, AP1000/AP300 협력 계약 체결`
        (8/05) → 12일 뒤 `美 웨스팅하우스·아멘텀, AP1000·AP300 협력 확대 계약`.
        유사도 0.906. 요약에 새 체결일이 있어 '확대 계약'일 수 있는데 그것을
        확인할 재료가 없다(event_date 는 발송 로그에 비어 있다). 같은 구간의
        확실한 재전송(IAEA 성명)은 제목이 글자까지 같아 0.95 를 넘는다.
        """
        item = article("美 웨스팅하우스·아멘텀, AP1000·AP300 협력 확대 계약",
                       h="w1", features=feat())
        continuity.annotate(
            [item],
            [sent("웨스팅하우스-아멘텀, AP1000/AP300 협력 계약 체결",
                  h="old", date="2026-08-05")],
            CFG, TODAY)
        cont = item["continuity"]
        self.assertGreaterEqual(cont["similarity"], 0.85)   # 최근이었다면 삭제 대상
        self.assertFalse(cont["drop"])
        self.assertGreater(cont["penalty"], 0)

    def test_material_progression_is_allowed_regardless_of_gap(self):
        """사용자 지목 ③ — 협의→계약·심사→승인은 기간과 관계없이 다시 나간다."""
        follow_up = article("한수원, 필리핀 바탄 원전 복구 본계약 체결",
                            h="f3", features=feat(), importance="must_read")
        continuity.annotate(
            [follow_up],
            [sent("한수원, 필리핀 바탄 원전 협력 양해각서(MOU) 체결", h="old",
                  date="2026-08-05")],   # 12일 전
            CFG, TODAY)
        # 만료돼 판정이 없거나, 있어도 삭제가 아니고 감점이 미미해야 한다.
        cont = follow_up.get("continuity") or {}
        self.assertFalse(cont.get("drop"))
        self.assertLessEqual(cont.get("penalty", 0.0), 0.5)
        selected, diag = ranking.rank_and_select([follow_up], 5, CFG, NOW)
        self.assertEqual([a["hash"] for a in selected], ["f3"])
        self.assertEqual(diag["dropped_repeat"], [])

    def test_alias_pair_is_one_name_not_two(self):
        """`풀네임(약어)` 두 표기는 이름 하나다.

        실측 2026-08-17: 3~14일 간격 앵커 매칭 오탐 3쌍이 전부 이 형태였다 —
        `anchors:nssc,원자력안전위원회` 하나로 정기검사 기사와 입법예고 기사가
        붙었다. 창이 넓어질수록 이 오탐의 값이 비싸진다.
        """
        prior = sent("한울 4호기, 원자력안전위원회(NSSC) 정기검사 중 임계 허용",
                     date="2026-08-11")
        cand = article("원자력안전위원회(NSSC), 입법 및 행정예고 진행")
        self.assertEqual(
            continuity.distinct_names(
                continuity.named_anchors(cand) & continuity.named_anchors(prior),
                continuity.alias_groups(cand) + continuity.alias_groups(prior)),
            1)
        self.assertIsNone(continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_two_real_names_still_match(self):
        """별칭 병합이 서로 다른 두 당사자까지 접으면 안 된다."""
        fp = {"actors": ["TerraPower", "Doosan Enerbility"]}
        prior = sent("테라파워-한국 기업 SMR 협력, 수출 공급망 확대", fingerprint=fp)
        cand = article("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결",
                       fingerprint=fp)
        self.assertIsNotNone(
            continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_hard_drop_can_be_switched_off(self):
        cfg = {**CFG, "continuity": {**(CFG.get("continuity") or {}),
                                     "hard_drop": {"enabled": False}}}
        repeat = article("한수원, 체코 두코바니 원전 본계약 체결", h="r2", features=feat())
        continuity.annotate([repeat],
                            [sent("한수원, 체코 두코바니 원전 본계약 체결", h="old")],
                            cfg, TODAY)
        self.assertFalse(repeat["continuity"]["drop"])
        self.assertLess(repeat["continuity"]["score_delta"], 0)


# ---- ⑤ 발송 이력 읽기 -----------------------------------------------------------

class RecentSentTests(unittest.TestCase):
    def test_reads_only_article_rows_inside_window(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_log.jsonl"
            path.write_text("\n".join([
                json.dumps({"date": "2026-08-16", "hash": "a", "title_kr": "어제"},
                           ensure_ascii=False),
                json.dumps({"date": "2026-08-01", "hash": "b", "title_kr": "옛날"},
                           ensure_ascii=False),
                json.dumps({"record_type": "selection_stats", "date": "2026-08-16"}),
                "{ not json",
            ]) + "\n", encoding="utf-8")
            rows = continuity.load_recent_sent(
                5, path=path, today=datetime(2026, 8, 17).date())
        self.assertEqual([r["hash"] for r in rows], ["a"])

    def test_missing_file_is_not_fatal(self):
        rows = continuity.load_recent_sent(5, path=Path("/nonexistent/x.jsonl"))
        self.assertEqual(rows, [])

    def test_same_day_other_region_counts_as_sent(self):
        """국내에서 이미 나간 이슈는 해외 풀에서 다시 겨루지 않는다.

        실측 2026-08-16: 같은 테라파워 이슈가 국내 1번과 해외 3번을 동시에 먹었다.
        두 지역이 각자 풀에서 따로 랭킹되기 때문이다.
        """
        domestic = article("테라파워-한국 기업 SMR 협력, 원전기업 수출 공급망 확대",
                           h="d1", fingerprint={"actors": ["TerraPower", "Doosan Enerbility"]})
        overseas = article("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결",
                           h="o1", fingerprint={"actors": ["TerraPower", "Doosan Enerbility"]})
        record = continuity.as_sent_record(domestic, TODAY)
        self.assertEqual(record["date"], TODAY)
        continuity.annotate([overseas], [record], CFG, TODAY)
        self.assertIn("continuity", overseas)
        self.assertEqual(overseas["continuity"]["days_ago"], 0)

    def test_sent_record_carries_the_evidence_list(self):
        """같은 날 다른 지역 경로에도 근거 교집합이 서야 한다."""
        domestic = with_members(article("한수원, 체코 두코바니 원전 본계약 체결", h="d1"),
                                ["e1", "e2"])
        record = continuity.as_sent_record(domestic, TODAY)
        self.assertEqual({m["hash"] for m in record["story_members"]},
                         {"d1", "e1", "e2"})


# ---- ⑤ 근거 교집합 --------------------------------------------------------------
#
# 실측 2026-08-22. 8/20 국회 본회의 한 건이 이틀 연속 나갔다. 제목·척도 어휘가
# 전부 흔들렸지만(제목 0.776 < hard_drop 0.85, 요약의 '통과' ↔ '의결' 때문에
# minor), 두 카드의 근거 목록은 14건 중 12건이 같았다.

class EvidenceOverlapTests(unittest.TestCase):
    EVIDENCE = [f"e{i}" for i in range(1, 13)]

    def prior(self, title, **kw):
        return sent(title, h="p1",
                    members=["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8",
                             "e9", "e10", "e11", "y1", "y2", "y3", "y4"], **kw)

    def repeat(self, title, **kw):
        """오늘 근거 14건 중 12건이 어제와 같다 (실측 비율 0.857)."""
        return with_members(article(title, h="c1", **kw),
                            ["p1"] + [f"e{i}" for i in range(1, 13)])

    def test_reworded_repeat_is_dropped_even_below_the_title_threshold(self):
        """사용자 지목 사례. 제목이 0.85 에 못 미쳐도 근거가 같으면 재전송이다."""
        prior = self.prior("국회, 국가기간 전력망 확충 특별법 등 민생법안 70건 처리",
                           summary="국회 본회의에서 특별법 등 70건의 민생법안이 통과됨.")
        cand = self.repeat("국회 본회의, 「국가기간 전력망 확충 특별법」 등 73건 의안 처리",
                           summary="국회는 20일 본회의를 열고 특별법 개정안 등 73건의 안건을 의결했다.")
        match = continuity.same_issue(cand, prior, CFG)
        self.assertTrue(match["evidence_confirmed"])
        self.assertLess(match["similarity"], 0.85)
        self.assertIn("evidence:12/14", match["reasons"])

        continuity.annotate([cand], [prior], CFG, TODAY)
        verdict = cand["continuity"]
        # 요약의 '통과' ↔ '의결' 만으로 서던 minor 가 사라진다.
        self.assertEqual(verdict["progression"], "none")
        self.assertTrue(verdict["drop"])
        self.assertEqual(verdict["evidence_shared"], 12)
        # 진단이 '겹쳤다'와 '문턱을 넘었다'를 가를 수 있어야 한다.
        self.assertTrue(verdict["evidence_confirmed"])

    def test_shared_evidence_never_beats_a_real_advance(self):
        """근거가 겹쳐도 단계가 넘어갔으면 진전이다 — ①~③ 이 위에 남는다."""
        prior = self.prior("한수원, 필리핀 원전 협력 양해각서 체결")
        cand = self.repeat("한수원, 필리핀 원전 본계약 체결")
        continuity.annotate([cand], [prior], CFG, TODAY)
        self.assertEqual(cand["continuity"]["progression"], "material")
        self.assertFalse(cand["continuity"]["drop"])

    def test_thin_overlap_leaves_a_follow_up_untouched(self):
        """실측 테라파워 8/17→8/18 — 3건을 공유하지만 진짜 후속이다.

        두 카드가 서로의 근거 목록에 상대를 들고 있으므로(cross_cited) 그것만
        보고 접었으면 이 후속이 죽었다. 비율이 가른다.
        """
        prior = sent("두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결",
                     h="p1", members=["e1", "e2"] + [f"w{i}" for i in range(1, 13)],
                     fingerprint={"actors": ["TerraPower"]})
        cand = with_members(
            article("SK이노베이션-테라파워, 나트륨 SMR 사업 공조 및 글로벌 진출 합의",
                    h="c1", fingerprint={"actors": ["TerraPower"]}),
            ["p1", "e1", "e2"] + [f"z{i}" for i in range(1, 13)])
        overlap = continuity.story_cluster.evidence_overlap(cand, prior)
        self.assertTrue(overlap.cross_cited)
        match = continuity.same_issue(cand, prior, CFG)
        self.assertFalse(match["evidence_confirmed"])
        # 근거가 매칭 사유에 끼지 않으므로 앵커 경로의 좁은 창이 그대로 유지된다.
        self.assertNotIn("evidence:3/16", match["reasons"])

    def test_evidence_path_needs_count_and_ratio_together(self):
        """근거가 한둘뿐인 작은 story 는 비율이 1.0 이어도 확정이 아니다."""
        prior = sent("한수원, 체코 두코바니 원전 본계약 체결", h="p1", members=["c1"])
        cand = with_members(article("체코 두코바니 원전, 한수원과 본계약 체결", h="c1"),
                            ["p1"])
        overlap = continuity.story_cluster.evidence_overlap(cand, prior)
        self.assertEqual(overlap.ratio, 1.0)
        self.assertFalse(continuity.same_issue(cand, prior, CFG)["evidence_confirmed"])

    def test_incidental_overlap_is_reported_but_not_confirmed(self):
        """근거 한 건이 겹치는 일은 흔하다 — 판정에는 닿지 않아야 한다.

        실측 2026-08-22 국내 후보 풀에서 겹침 5건 중 넷이 1~2건짜리였고 전부
        판정에 영향이 없었다. 진단은 그 넷을 확정과 같은 칸에 세면 안 된다.
        """
        prior = sent("미국 내 AI 데이터센터 건설 반대 여론 확산 및 선거 쟁점화",
                     h="p1", members=["e1"] + [f"w{i}" for i in range(1, 10)])
        cand = with_members(
            article("AI 데이터센터 건립 수용성 조사, 국가 필요성 대비 지역 반대 뚜렷",
                    h="c1"),
            ["e1"] + [f"z{i}" for i in range(1, 10)])
        self.assertEqual(
            continuity.story_cluster.evidence_overlap(cand, prior).shared, 1)
        continuity.annotate([cand], [prior], CFG, TODAY)
        verdict = cand.get("continuity")
        if verdict:
            self.assertEqual(verdict["evidence_shared"], 1)
            self.assertFalse(verdict["evidence_confirmed"])

    def test_stories_without_evidence_behave_as_before(self):
        """근거 목록이 없으면(옛 레코드·수집 직후) 판정이 예전 그대로다."""
        prior = sent("스페인, 알마라즈 원전 운영 기한 2030년까지 연장", h="p1")
        cand = article("스페인 정부, 알마라스(Almaraz) 원전 운영 연장 승인", h="c1")
        continuity.annotate([cand], [prior], CFG, TODAY)
        self.assertEqual(cand["continuity"]["progression"], "minor")

    def test_evidence_confirmed_still_inherits_story_id(self):
        """근거를 압도적으로 공유하는 재전송은 여전히 story_id 를 물려받는다.

        어휘와 무관한 hash 비교라 anchor_only 와 달리 identity 확정과 동급이다
        (`same_issue` 의 story_id_inheritable 정의 참조).
        """
        prior = self.prior("국회, 국가기간 전력망 확충 특별법 등 민생법안 70건 처리")
        cand = self.repeat("국회 본회의, 「국가기간 전력망 확충 특별법」 등 73건 의안 처리")
        continuity.annotate([cand], [prior], CFG, TODAY)
        self.assertEqual(cand["story_id"], continuity.story_cluster.fallback_story_id(prior))
        self.assertEqual(cand["story_id_source"], "history")


# ---- ⑥ story_id 상속 — 실사례 회귀 (2026-08-31) ----------------------------------
#
# 2026-08-31 라이브: 『프랑스 볼탈리아, 브라질 350억 달러 규모 데이터센터 프로젝트
# 승인』(8/28 발송)과 전혀 무관한 『미국 AI 데이터센터 프로젝트, 주민 반발로 1300억
# 달러 지연·취소』(8/31 신규)가 `#데이터센터`·`#전력수요` 라는 업계 공통 태그
# 둘만으로 anchor 경로에 걸렸다. `annotate()` 가 그 매칭이 얼마나 강한지 보지
# 않고 story_id 를 그대로 물려줘 두 기사가 같은 story_id 를 갖게 됐고, 그 값이
# web/build_data.py 의 issue_id 로 그대로 이어져 `build_issue_pages()` 가
# `FileExistsError: web/public/issue/story-69f963bc223b6b84` 로 죽었다.
#
# 실제 사고의 두 기사는 지문(story_fingerprint)이 아직 없는 큐레이션 단계였다
# (국내 피드는 LLM story dedup 을 거치지 않는 경우가 흔하다) — 아래 재현도 같은
# 조건(지문 없음)에서 시작한다.

class StoryIdInheritanceTests(unittest.TestCase):
    def test_generic_industry_tags_do_not_inherit_story_id(self):
        """앵커 매칭 자체는 여전히 걸리지만(감점 대상), story_id 는 안 물려준다."""
        prior = sent(
            "프랑스 볼탈리아, 브라질 350억 달러 규모 데이터센터 프로젝트 승인",
            h="voltalia", date="2026-08-28",
            tags=["#데이터센터", "#전력수요", "#브라질"])
        cand = article(
            "미국 AI 데이터센터 프로젝트, 주민 반발로 1300억 달러 지연·취소",
            h="us-delay", tags=["#데이터센터", "#전력수요", "#미국"])

        match = continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG)
        self.assertIsNotNone(match, "앵커 매칭 자체는 여전히 걸려야 한다(감점 대상)")
        self.assertTrue(match["anchor_only"])
        self.assertFalse(match["identity_confirmed"])
        self.assertFalse(match["story_id_inheritable"])

        continuity.annotate([cand], [prior], continuity.DEFAULT_CONFIG, "2026-08-31")
        self.assertNotEqual(cand.get("story_id"), "story-voltalia")
        self.assertNotEqual(cand.get("story_id_source"), "history")

    def test_disjoint_fingerprint_countries_veto_the_match(self):
        """지문에 나라가 있으면 앵커·story_id 동일성보다 위에서 막는다."""
        prior = sent(
            "프랑스 볼탈리아, 브라질 350억 달러 규모 데이터센터 프로젝트 승인",
            h="voltalia", date="2026-08-28",
            fingerprint={"countries": ["France", "Brazil"], "actors": ["Voltalia"],
                        "assets": ["Data center"]},
            tags=["#데이터센터", "#전력수요"])
        cand = article(
            "미국 AI 데이터센터 프로젝트, 주민 반발로 1300억 달러 지연·취소",
            h="us-delay",
            fingerprint={"countries": ["USA"], "actors": ["Meta"],
                        "assets": ["Data center"]},
            tags=["#데이터센터", "#전력수요"])
        self.assertIsNone(
            continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG))

    def test_shared_story_id_with_contested_fingerprint_is_not_confirmed(self):
        """자기강화 차단: 오염된 상태를 그대로 줘도(같은 story_id) 새로 확인된
        지문이 신원 축에서 어긋나면 identity_confirmed 로 세지 않는다.

        1차 continuity 가 약한 매칭으로 story_id 를 잘못 물려준 뒤, 후속 단계
        (semantic dedup 등)에서 진짜 지문이 붙는 경우를 흉내 낸다 — "이미 같은
        id" 라는 사실 하나만으로 2차 재판정이 그 id 를 재확인해 버리면 안 된다.
        """
        prior = sent(
            "프랑스 볼탈리아, 브라질 350억 달러 규모 데이터센터 프로젝트 승인",
            h="voltalia", date="2026-08-28",
            fingerprint={"actors": ["Voltalia"], "assets": ["Data center"],
                        "drivers": ["power demand"]})
        cand = article(
            "미국 AI 데이터센터 프로젝트, 주민 반발로 1300억 달러 지연·취소",
            h="us-delay",
            fingerprint={"actors": ["OpenAI"], "assets": ["Data center campus"],
                        "drivers": ["local opposition"]})
        cand["story_id"] = "story-voltalia"          # 오염된 상태를 그대로 재현
        cand["story_id_source"] = "history"
        prior["story_id"] = "story-voltalia"

        match = continuity.same_issue(cand, prior, continuity.DEFAULT_CONFIG)
        self.assertIsNotNone(match)
        self.assertIn("story_id:story-voltalia", match["reasons"])
        self.assertFalse(
            match["identity_confirmed"],
            "이미 같은 id 라는 사실만으로 신원을 확정하면 자기강화 고리가 생긴다")
        self.assertFalse(match["story_id_inheritable"])

    def test_two_pass_annotate_does_not_self_reinforce(self):
        """daily_brief.py 의 1차 판정 → story consolidation → 2차 재판정을 흉내 낸다.

        1차에서 story_id 를 못 물려받으면 candidate 는 여전히 제 hash 기반
        id 를 들고 있으므로, 2차 재판정에서도 `direct_story_id` 가 서지 않는다
        — 자기강화가 시작조차 되지 않는다.
        """
        prior = sent(
            "프랑스 볼탈리아, 브라질 350억 달러 규모 데이터센터 프로젝트 승인",
            h="voltalia", date="2026-08-28",
            tags=["#데이터센터", "#전력수요", "#브라질"])
        cand = article(
            "미국 AI 데이터센터 프로젝트, 주민 반발로 1300억 달러 지연·취소",
            h="us-delay", tags=["#데이터센터", "#전력수요", "#미국"])

        continuity.annotate([cand], [prior], continuity.DEFAULT_CONFIG, "2026-08-31")
        first_pass_story_id = cand.get("story_id")
        self.assertNotEqual(cand.get("story_id_source"), "history")

        # story 조립 뒤 재판정 (rank_and_select 의 continuity_recheck 흉내).
        continuity.annotate([cand], [prior], continuity.DEFAULT_CONFIG, "2026-08-31")
        self.assertEqual(cand.get("story_id"), first_pass_story_id)
        self.assertNotEqual(cand.get("story_id_source"), "history")
        self.assertFalse(cand["continuity"]["identity_confirmed"])

    def test_strong_fingerprint_identity_still_inherits_story_id(self):
        """정상적인 연속성은 보존된다 — 신원 축이 확정되면 story_id 를 물려받는다."""
        prior = sent(
            "현대건설·테라파워, SMR 사업 협력 계약 체결", h="old", date="2026-08-10",
            fingerprint={"countries": ["South Korea", "USA"],
                        "actors": ["Hyundai E&C", "TerraPower", "Export-Import Bank"],
                        "assets": ["SMR"], "event_family": "contract_award"})
        cand = article(
            "테라파워와 현대건설의 차세대 원자로 협력 본격화", h="new",
            fingerprint={"countries": ["South Korea", "USA"],
                        "actors": ["Hyundai E&C", "TerraPower"],
                        "assets": ["SMR"], "event_family": "contract_award"},
            features=feat())

        continuity.annotate([cand], [prior], continuity.DEFAULT_CONFIG, TODAY)
        self.assertEqual(cand["story_id"], continuity.story_cluster.fallback_story_id(prior))
        self.assertEqual(cand["story_id_source"], "history")


# ---- ⑦ story 가 접힌 뒤 다시 판정하는가 -------------------------------------------

@_WIRING_SKIP
class ContinuityRecheckTests(unittest.TestCase):
    """판정 재료 하나(근거 교집합)가 rank_and_select **안에서** 만들어진다."""

    def test_recheck_runs_after_folding_and_its_verdict_decides(self):
        late = article("국회 본회의, 전력망 특별법 등 73건 의안 처리",
                       h="late", features=feat(), importance="must_read")
        fresh = article("원안위, 새울 3호기 운영허가 심사 결과 의결",
                        h="fresh", features=feat(event_type="regulatory_action"))
        seen = {}

        def recheck(rows):
            # ranking 이 story 를 다 접은 뒤에 부른다 — 그 자리에서야 근거가 생긴다.
            seen["hashes"] = [r["hash"] for r in rows]
            for row in rows:
                if row["hash"] == "late":
                    row["continuity"] = {"drop": True, "penalty": 5.0,
                                         "score_delta": -5.0, "progression": "none",
                                         "similarity": 0.776, "evidence_shared": 12,
                                         "prior_title": "국회, 전력망 특별법 등 70건 처리",
                                         "prior_date": YESTERDAY, "days_ago": 1,
                                         "match_reasons": ["evidence:12/14"]}

        selected, diag = ranking.rank_and_select(
            [late, fresh], 5, CFG, NOW, continuity_recheck=recheck)
        self.assertEqual(sorted(seen["hashes"]), ["fresh", "late"])
        self.assertEqual([a["hash"] for a in selected], ["fresh"])
        self.assertEqual(len(diag["dropped_repeat"]), 1)
        self.assertEqual(diag["dropped_repeat"][0]["evidence_shared"], 12)

    def test_without_the_hook_nothing_changes(self):
        late = article("국회 본회의, 전력망 특별법 등 73건 의안 처리",
                       h="late", features=feat(), importance="must_read")
        selected, diag = ranking.rank_and_select([late], 5, CFG, NOW)
        self.assertEqual([a["hash"] for a in selected], ["late"])
        self.assertEqual(diag["dropped_repeat"], [])

    def test_generic_anchors_can_be_pinned_across_both_passes(self):
        """두 번째 판정에서 '흔한 말'의 정의가 바뀌면 같은 하루가 두 기준을 쓴다."""
        pool = [article(f"데이터센터 전력 수요 관련 기사 {i}", h=f"a{i}")
                for i in range(40)]
        pinned = continuity.generic_anchors(pool)
        diag = continuity.annotate(pool[:2], [sent("데이터센터 전력 수요 급증")],
                                   CFG, TODAY, generic=pinned)
        self.assertEqual(diag["checked"], 2)


if __name__ == "__main__":
    unittest.main()
