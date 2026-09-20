# -*- coding: utf-8 -*-
"""카드 카피의 편집 품질 검사 — `card_qa`.

이 파일의 절반은 **오탐 검사**다. 중복 판정은 켜기는 쉽고 믿기는 어렵다 —
임계값이 조금만 낮으면 기관명이 같다는 이유로 멀쩡한 카드가 걸리고, 그러면
repair 가 일상이 되어 호출량이 배로 늘면서 아무도 경고를 안 본다. 그래서
"걸려야 하는 것" 과 "걸리면 안 되는 것" 을 같은 무게로 검사한다.

임계값의 근거는 픽스처가 아니라 **실제로 발간된 카피**다. 그 실측치는 아래
CalibrationTests 에 박제한다 — 값을 바꾸려면 다시 재야 한다는 뜻이다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import card_qa  # noqa: E402

# SAR 실데이터에서 뽑은 입력. 숫자 접지 검사가 이 문자열을 근거로 본다.
SOURCE = ("기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행 "
          "기후에너지환경부가 19일부터 1년간 전북 진안과 충남 금산에서 기온에 따라 "
          "송전용량을 탄력적으로 조정하는 SAR 시범사업을 시행한다. "
          "기존 정적등급(SLR) 방식은 최악의 환경을 전제로 송전용량을 고정했다. "
          "2026-09-19 시행.")


def step(headline="SAR, 계획 넘어 현장 적용", facts=None, why=None) -> dict:
    return {"headline": headline,
            "facts": facts if facts is not None else ["진안·금산에서 시범사업 착수"],
            "why": why if why is not None else ["계절별 여유용량을 실제 송전에 활용 가능"]}


def codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


class RedundancyTests(unittest.TestCase):
    def test_a_why_that_restates_a_fact_is_a_failure(self):
        """이 카드에서 가장 중요한 검사다.

        `확인된 사실` 과 `왜 중요한가` 가 같은 말이면 카드가 한 칸을 낭비한 것이
        아니라, 독자에게 "그래서 무엇이 달라지는가" 를 끝내 말하지 않은 것이다.
        """
        report = card_qa.review_step(step(
            facts=["SAR 진안·금산 시범사업 착수"],
            why=["진안·금산에서 실제 시범사업 착수"]), SOURCE)
        self.assertIn("fact_why_overlap", codes(report))
        self.assertTrue(report.failures)

    def test_bullets_that_differ_only_by_particles_are_a_failure(self):
        """조사·어미만 바꿔 같은 문장을 두 번 쓰는 우회를 막는다."""
        report = card_qa.review_step(step(
            facts=["진안과 금산에서 시범사업을 착수", "진안·금산에서 시범사업 착수"]), SOURCE)
        self.assertIn("dup_fact", codes(report))

    def test_different_facts_pass(self):
        report = card_qa.review_step(step(
            facts=["진안·금산에서 시범사업 착수", "실증기간 1년", "계절별 기온 기준 적용"]), SOURCE)
        self.assertEqual(report.findings, [])

    def test_sharing_an_institution_name_is_not_redundancy(self):
        """**오탐 검사.** 짧은 고유명사·기관명이 겹친다고 실패시키지 않는다.

        기관명은 같은 날 카드의 두 사실에 같이 나오는 게 정상이다 — 술어가
        다르면 다른 사실이다.
        """
        report = card_qa.review_step(step(
            facts=["기후에너지환경부가 시범사업을 시행한다",
                   "기후에너지환경부가 고정 기준을 폐지한다"]), SOURCE)
        self.assertNotIn("dup_fact", codes(report))

    def test_a_headline_that_copies_the_first_fact_is_a_warning_not_a_failure(self):
        report = card_qa.review_step(step(
            headline="진안·금산 시범사업 착수",
            facts=["진안·금산에서 시범사업 착수"]), SOURCE)
        self.assertIn("headline_copies_fact", codes(report))
        self.assertEqual([f.code for f in report.warnings], ["headline_copies_fact"])


class VagueTests(unittest.TestCase):
    VAGUE = ("정책적 의미가 큽니다", "향후 귀추가 주목됩니다", "관심이 필요합니다",
             "시장 영향이 예상됩니다", "관련 논의가 지속될 전망입니다",
             "지켜볼 필요가 있습니다", "전력망 운영 효율 개선 기대")
    CONCRETE = ("계절별 여유용량을 실제 송전에 활용 가능",
                "송전용량 산정이 고정 기준에서 계절 기준으로 전환",
                "실증 결과에 따라 확대 여부를 판단",
                "1년 실증 뒤 적용지역 확대 여부가 갈립니다",
                "원안위 심의 결과를 주목해야 하며 12월 의결이 예정돼 있습니다")

    def test_abstract_lines_are_caught(self):
        for text in self.VAGUE:
            with self.subTest(text=text):
                self.assertTrue(card_qa.is_vague(text))

    def test_concrete_lines_pass(self):
        """**오탐 검사.** 구체적인 결과가 뒤따르면 관용구가 있어도 통과한다."""
        for text in self.CONCRETE:
            with self.subTest(text=text):
                self.assertFalse(card_qa.is_vague(text))

    def test_a_long_line_that_names_what_changes_passes(self):
        """실제 발간된 카피에서 나온 모양 — 끝맺음만 관용구이고 알맹이는 있다."""
        self.assertFalse(card_qa.is_vague(
            "미국의 전력망 공급망 규제 강화는 수출 통제 및 인증 절차에 "
            "직접적인 영향을 미칠 것"))


class GroundingTests(unittest.TestCase):
    def test_a_number_that_is_not_in_the_input_fails(self):
        report = card_qa.review_step(step(
            facts=["실증기간 1년", "송전용량 17.5GW 확대"]), SOURCE)
        self.assertIn("ungrounded", codes(report))

    def test_a_date_that_is_not_in_the_input_fails(self):
        report = card_qa.review_step(step(facts=["9월 25일 시범사업 착수"]), SOURCE)
        self.assertIn("ungrounded", codes(report))

    def test_the_same_day_in_another_notation_is_the_same_day(self):
        """`2026-09-19` · `9월 19일` · `9.19` 는 같은 날이다."""
        for form in ("2026-09-19", "9월 19일", "9.19", "2026년 9월 19일"):
            with self.subTest(form=form):
                self.assertNotIn("ungrounded", codes(
                    card_qa.review_step(step(facts=[f"{form} 시범사업 착수"]), SOURCE)))

    def test_a_substring_is_not_grounding(self):
        """**예전 검사가 여기서 틀렸다.** `"17" in "170"` 이 참이라 통과했다."""
        self.assertTrue(card_qa.ungrounded("17건 의결", "170건 접수"))
        self.assertFalse(card_qa.ungrounded("170건 접수", "170건 접수"))

    def test_numbers_the_card_counted_itself_are_allowed(self):
        """표지의 "현안 3건" 은 입력이 아니라 카드가 센 수다 — 걸리면 안 된다."""
        item = {"title": "t", "summary": SOURCE, "detail": "", "why_important": "",
                "implication": "", "open_question": "", "body": "",
                "event_date": "", "source": ""}
        raw = {"hook": {"headline": "오늘 먼저 볼 원자력 현안 3건"},
               "steps": [{"headline": "제목", "facts": ["진안·금산에서 시범사업 착수"],
                          "why": ["계절별 여유용량을 실제 송전에 활용 가능"]}] * 3}
        self.assertNotIn("ungrounded", codes(card_qa.review_daily(raw, [item] * 3)))

    def test_the_cover_cannot_invent_a_number_either(self):
        item = {"title": "t", "summary": SOURCE, "detail": "", "why_important": "",
                "implication": "", "open_question": "", "body": "",
                "event_date": "", "source": ""}
        raw = {"hook": {"headline": "원전 12기 계속운전 확정"}, "steps": []}
        report = card_qa.review_daily(raw, [item] * 3)
        self.assertEqual([f.where for f in report.failures], ["hook.headline"])

    def test_a_card_cannot_borrow_another_cards_number(self):
        """카드 사이의 근거 누수. 카드마다 **자기 이슈의 입력만** 근거로 본다."""
        items = [
            {"title": "A", "summary": "실증기간 1년", "detail": "", "why_important": "",
             "implication": "", "open_question": "", "body": "", "event_date": "", "source": ""},
            {"title": "B", "summary": "설비용량 17.5GW", "detail": "", "why_important": "",
             "implication": "", "open_question": "", "body": "", "event_date": "", "source": ""},
        ]
        raw = {"steps": [
            # 1번 카드가 2번 기사의 숫자를 썼다.
            {"headline": "제목", "facts": ["17.5GW 확대"], "why": ["운영 기준이 전환"]},
            {"headline": "제목", "facts": ["설비용량 17.5GW"], "why": ["운영 기준이 전환"]},
        ]}
        report = card_qa.review_daily(raw, items)
        leaks = [f for f in report.findings if f.code == "ungrounded"]
        self.assertEqual(len(leaks), 1)
        self.assertTrue(leaks[0].where.startswith("step1"))


class NormalizationTests(unittest.TestCase):
    def test_accents_and_punctuation_do_not_change_the_text(self):
        self.assertEqual(card_qa.normalize("[[진안]]·금산에서, 착수!"),
                         card_qa.normalize("진안 금산에서 착수"))

    def test_a_stem_is_never_cut_below_two_characters(self):
        """"에서" 를 떼어 "진안" 이 "진" 이 되면 안 된다."""
        self.assertIn("진안", card_qa.tokens("진안에서"))
        self.assertIn("금산", card_qa.tokens("금산은"))

    def test_short_lines_are_not_compared(self):
        """짧으면 공통 낱말 하나가 점수를 끌어올린다 — 아예 재지 않는다."""
        self.assertEqual(card_qa.similarity("착수", "착수"), 0.0)


class CalibrationTests(unittest.TestCase):
    """**임계값의 근거를 코드에 박제한다.**

    라이브 실측 2026-09-20, 지난 30 브리핑일의 상위 3건. 숫자를 바꾸고 싶으면
    다시 재라는 뜻이고, 아래 순서 관계가 깨지면 임계값이 실측 분포 안으로
    들어왔다는 뜻이다 — 그때부터 오탐이 난다.
    """
    # 실제 발간된 카피에서 관측된 최댓값.
    OBSERVED_FACT_WHY_MAX = 0.526      # n=85
    OBSERVED_HEADLINE_FACT_MAX = 0.708  # n=90
    OBSERVED_SIBLING_TITLE_MAX = 0.745  # n=123 (중복 판정의 최악 조건)

    def test_every_threshold_sits_outside_the_observed_distribution(self):
        self.assertGreater(card_qa.FACT_WHY, self.OBSERVED_FACT_WHY_MAX)
        self.assertGreater(card_qa.HEADLINE_FACT, self.OBSERVED_HEADLINE_FACT_MAX)
        # 중복만 관측 최댓값보다 낮다. 그 최댓값은 **진짜 중복**이었기 때문이다
        # (두 매체가 같은 제목을 썼다) — 잡아야 하는 쪽이라 일부러 아래에 둔다.
        self.assertLess(card_qa.DUPLICATE, self.OBSERVED_SIBLING_TITLE_MAX)

    def test_the_headline_versus_source_title_check_is_deliberately_absent(self):
        """재 보고 뺀 검사다. 같은 표본에서 **90건 중 44건(49%)** 이 걸렸다.

        절반이 걸리는 검사는 경고로 둬도 repair 를 일상으로 만들고, 경고가
        흔해지면 아무도 안 본다. 원제 축약은 유사도로 사후에 때릴 문제가 아니라
        Narrator 가 `headline_angle` 을 먼저 정해서 푸는 문제다.
        """
        self.assertNotIn("headline_copies_source",
                         {name for name in dir(card_qa) if not name.startswith("_")})


if __name__ == "__main__":
    unittest.main()
