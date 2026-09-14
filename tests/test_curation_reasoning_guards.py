import unittest

import news_bot


class CurationReasoningGuardTests(unittest.TestCase):
    def test_real_saeul_title_merge_is_separated(self):
        merged = "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장"
        self.assertEqual(news_bot.separate_curation_headline_events(merged),
                         "상업운전 앞둔 새울 3호기 시운전 중 자동정지")

    def test_single_incident_or_single_extension_is_untouched(self):
        for title in ("새울 3호기 시운전 중 자동정지",
                      "새울 3·4호기 건설사업 사업기간 10개월 연장"):
            with self.subTest(title=title):
                self.assertEqual(news_bot.separate_curation_headline_events(title), title)

    def test_source_supported_causality_is_retained(self):
        value = "폭염 때문에 전력수요가 늘었다."
        self.assertEqual(news_bot.drop_unsupported_causal_interpretation(
            value, "폭염 때문에 전력수요가 늘었다."), value)

    def test_generated_only_causality_is_removed_from_optional_analysis(self):
        self.assertEqual(news_bot.drop_unsupported_causal_interpretation(
            "자동정지 때문에 사업기간이 연장됐다.",
            "자동정지했다. 사업기간도 연장됐다.", "새울"), "")

    def test_normalizer_repairs_saeul_title_but_keeps_two_source_facts(self):
        article = {
            "title": "상업운전 앞둔 새울 3호기 자동정지…사업기간도 10개월 연장",
            "description": "새울 3호기가 자동정지했다. 사업기간도 연장됐다.",
        }
        item = {
            "title_kr": "상업운전 앞둔 새울 3호기 시운전 중 자동정지 및 사업기간 연장",
            "summary": "새울 3호기가 자동정지했으며 사업기간도 연장됐다.",
            "detail": "자동정지와 사업기간 변경은 별도 사안이다.",
            "implication": "자동정지 때문에 사업기간이 연장됐다.",
        }
        result = news_bot.normalize_curation_item(item, article, body=article["description"])
        self.assertEqual(result["title_kr"],
                         "상업운전 앞둔 새울 3호기 시운전 중 자동정지")
        self.assertIn("사업기간", result["summary"])
        self.assertEqual(result["implication"], "")


if __name__ == "__main__":
    unittest.main()


class HeadlineConcretenessPromptGuard(unittest.TestCase):
    """헤드라인 구체성 조항이 프롬프트에서 사라지지 않게 잠근다.

    실사례(2026-09-08 해외 브리핑): 같은 POWER Magazine 기사를 놓고 v2 는
    "행정명령 발동"을, 우리는 "인프라 투자 재편 가능성 시사"를 제목으로 냈다 —
    detail 엔 행정명령이 적혀 있었는데 제목이 주제 서술을 택해 사건을 묻었다.
    구체 사건 존재 판정 자체는 게이트로 못 하므로(원문 이해 필요) 조항 존재만
    잠근다 — 약한 가드로 충분하다는 판단, 계획 P4b.
    """

    def test_the_prompt_bans_thematic_headlines(self):
        for marker in ("가능성 시사", "주제 서술형 제목", "새로 전하는 구체 사건"):
            self.assertIn(marker, news_bot.CURATION_SYSTEM_PROMPT)

    def test_the_prompt_bans_background_facts_as_headline(self):
        """실사례(2026-09-14 히어로): 영남일보 칼럼 `[취재수첩] 영덕의 꿈`이
        배경으로 적은 6월 부지 선정을 제목으로 끌어올려 must_read·1위가 됐다.
        같은 기사를 v2 는 '지역 기대감 고조'(nice_to_know)로 냈다 — v2 프롬프트엔
        '본문 속 사건이 곧 헤드라인' 조항이 없고 '사건 단계 유지'만 있다.
        구체성 조항(09-08 자체 추가)은 남기되 '새로 전하는' 사건으로 좁히고,
        배경 사건 금지 + v2 의 단계 유지 조항을 함께 잠근다."""
        for marker in ("배경으로 인용된 과거 사건", "사건 단계를 정확히 유지",
                       "그 글의 논지"):
            self.assertIn(marker, news_bot.CURATION_SYSTEM_PROMPT)
        self.assertNotIn("반드시 그것이 헤드라인", news_bot.CURATION_SYSTEM_PROMPT)
