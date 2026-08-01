"""Nuclens 공통 데이터 품질 계약 회귀 테스트. 외부 호출 0."""

import unittest

from data_quality import (
    curation_errors,
    invalid_url_reason,
    is_complete_sentence,
    legacy_url_hash,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    split_title_publisher,
    title_key,
    url_hash,
)


class UrlQualityTests(unittest.TestCase):
    def test_double_slash_and_tracking_params_are_normalized(self):
        left = "https://www.example.com//articles/story/?utm_source=x&id=7#top"
        right = "https://www.example.com/articles/story?id=7"
        self.assertEqual(normalize_url(left), right)
        self.assertEqual(url_hash(left), url_hash(right))

    def test_article_identity_query_is_preserved(self):
        self.assertNotEqual(
            normalize_url("https://example.com/read?id=7"),
            normalize_url("https://example.com/read?id=8"),
        )

    def test_legacy_hash_remains_available_for_state_transition(self):
        raw = "https://example.com/story?utm_source=old"
        self.assertNotEqual(legacy_url_hash(raw), url_hash(raw))

    def test_error_path_is_rejected_case_insensitively(self):
        self.assertEqual(invalid_url_reason("https://example.com/Error/please-try-again"), "error_path")
        self.assertEqual(invalid_url_reason("javascript:alert(1)"), "invalid_url")
        self.assertEqual(invalid_url_reason("https://example.com/news/error-budget"), "")

    def test_exact_title_key_only_normalizes_spacing_and_case(self):
        self.assertEqual(title_key("  NRC   Approves Licence  "), title_key("nrc approves licence"))
        self.assertNotEqual(title_key("NRC approves licence"), title_key("NRC licence approved today"))


class PublisherTests(unittest.TestCase):
    def test_explicit_rss_publisher_wins_and_repeated_suffix_is_removed(self):
        title, publisher = split_title_publisher(
            "원전 계속운전 심사 - 전기신문 - 전기신문", "전기신문"
        )
        self.assertEqual(title, "원전 계속운전 심사")
        self.assertEqual(publisher, "전기신문")

    def test_title_suffix_is_fallback_when_source_element_is_missing(self):
        self.assertEqual(
            split_title_publisher("원안위가 심사 결과를 발표했다 - KBS 뉴스"),
            ("원안위가 심사 결과를 발표했다", "KBS 뉴스"),
        )


class SourceModelTests(unittest.TestCase):
    def test_official_and_specialist_sources_are_not_conflated(self):
        official = source_profile("energy.gov", "US DOE")
        specialist = source_profile("world-nuclear-news.org", "World Nuclear News")
        self.assertEqual((official["source_type"], official["evidence_role"]), ("official", "primary"))
        self.assertEqual(
            (specialist["source_type"], specialist["evidence_role"]),
            ("specialist_media", "independent"),
        )

    def test_press_release_distribution_is_explicit(self):
        profile = source_profile("globenewswire.com", "GlobeNewswire")
        self.assertEqual(profile["source_type"], "press_release")
        self.assertEqual(profile["evidence_role"], "distributed_claim")

    def test_unknown_domain_still_has_non_null_rank_tier(self):
        profile = source_profile("regional-news.example", "지역매체")
        self.assertEqual(profile["source_tier"], 3)
        self.assertEqual(profile["publisher"], "지역매체")


class TextAndEventDateTests(unittest.TestCase):
    def test_complete_sentence_gate(self):
        self.assertTrue(is_complete_sentence("원안위가 운영 변경을 승인했다."))
        self.assertTrue(is_complete_sentence("상업운전은 2027년 시작될 예정이다"))
        self.assertFalse(is_complete_sentence("운영 변경 승인"))
        self.assertFalse(is_complete_sentence("규제 심사가 강화될 것으로 예상되"))

    def test_curation_limits_do_not_allow_mid_sentence_slicing(self):
        self.assertEqual(curation_errors({"summary": "정부가 계획을 발표했다."}), [])
        self.assertIn(
            "summary:incomplete_or_over_80",
            curation_errors({"summary": "정부가 계획을 발표"}),
        )
        self.assertIn(
            "implication:incomplete_or_over_60",
            curation_errors({"summary": "정부가 계획을 발표했다.", "implication": "시장 영향 확대 가능"}),
        )

    def test_event_date_requires_explicit_iso_date_and_metadata(self):
        valid = normalize_event_date_fields({
            "event_date": "2026-08-01",
            "event_date_type": "announcement",
            "event_date_precision": "day",
            "event_date_source": "description",
        })
        self.assertEqual(valid["event_date"], "2026-08-01")
        invalid = normalize_event_date_fields({"event_date": "2026년 8월"})
        self.assertEqual(invalid, {
            "event_date": None,
            "event_date_type": "unknown",
            "event_date_precision": "unknown",
            "event_date_source": "unknown",
        })


if __name__ == "__main__":
    unittest.main()
