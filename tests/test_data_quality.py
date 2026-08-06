"""Nuclens 공통 데이터 품질 계약 회귀 테스트. 외부 호출 0."""

import unittest

import data_quality

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
        # 한도는 60 → 90 (2026-08-05). 원인·다음 절차·수치를 담으라고 프롬프트를
        # 바꿨는데 60자로는 그게 안 들어간다. 잘림 검사 자체는 그대로다.
        self.assertIn(
            "implication:incomplete_or_over_90",
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


class FeaturesGateTests(unittest.TestCase):
    """features 결손을 '게시 자격'과 '큐레이션 완결성'으로 분리해 다룬다.

    분리가 없던 동안 결손 항목이 재큐레이션 대상에서 영구히 빠졌다.
    근거: docs/score_distribution.md §4.
    """

    BASE = {
        "summary": "한빛 1·2호기 계속운전 심사가 재개됐다.",
        "importance": "must_read",
        "section": "khnp",
        "category": "정책",
    }

    def test_publishing_gate_ignores_features_by_default(self):
        # 아카이브 적재·배포 게이트는 features 가 없어도 통과해야 한다.
        # 제목·요약·링크는 멀쩡하므로 내보낼 수 있고, 여기서 막으면
        # 트렌드·prior_coverage 재료가 통째로 사라진다.
        self.assertEqual(curation_errors(dict(self.BASE)), [])
        self.assertEqual(curation_errors({**self.BASE, "features": None}), [])

    def test_curation_completeness_flags_missing_features(self):
        self.assertEqual(
            curation_errors(dict(self.BASE), require_features=True),
            ["features:missing"],
        )

    def test_non_dict_features_are_treated_as_missing(self):
        for bad in (None, [], "", 0, "policy_decision"):
            with self.subTest(features=bad):
                self.assertIn(
                    "features:missing",
                    curation_errors({**self.BASE, "features": bad},
                                    require_features=True),
                )

    def test_present_features_pass(self):
        payload = {**self.BASE, "features": {"event_type": "policy_decision",
                                             "korea_relevance": 3}}
        self.assertEqual(curation_errors(payload, require_features=True), [])

    def test_features_error_does_not_mask_other_errors(self):
        payload = {"summary": "", "features": None}
        errors = curation_errors(payload, require_features=True)
        self.assertIn("summary:missing", errors)
        self.assertIn("features:missing", errors)


class TestHollowImplication(unittest.TestCase):
    """정보량 0인 AI 해석을 걸러낸다.

    사용자 지적(2026-08-05): "AI 헝가리 정부의 원전 운영에 대한 긍정적 입장을
    시사한다 >> 이거 보면 내용이 너무 없어." 카드 두 번째 줄이 제목을 바꿔 말하기만
    하면 읽는 사람이 얻는 게 없다.

    실측 라이브 issues.json: implication 이 있는 64건 중 40건(62%)이 상투적
    종결부로 끝났다. **재생성 사유로 쓰지 않는다** — curation_errors 에 넣으면
    재생성 1회 뒤에도 남을 때 기사가 격리돼 영문 제목 폴백으로 떨어진다.
    """

    def test_the_reported_sentence_is_caught(self):
        self.assertTrue(data_quality.implication_is_hollow(
            "헝가리 정부의 원자력 발전 운영에 대한 긍정적 입장을 시사한다."))

    def test_common_hollow_shapes(self):
        for text in (
            "미국 SMR 상용화 및 기술 실증 가속화에 기여할 것입니다.",
            "선진원자로 기술의 상업화 및 운영 표준화에 중요한 이정표가 될 것으로 예상됩니다.",
            "정부의 탈원전 정책 전환 기조가 언론에서도 주요하게 다뤄지고 있음을 보여줍니다.",
            "향후 원자력 안전 관련 법규 및 행정 절차 변경 가능성을 주시할 필요가 있습니다.",
        ):
            self.assertTrue(data_quality.implication_is_hollow(text), text)

    def test_concrete_causes_survive(self):
        """원인·다음 절차가 들어 있으면 살린다 — 이것이 우리가 원하는 문장이다."""
        for text in (
            "다뉴브강 수위가 회복되며 냉각수 취수 제한이 풀린 결과로, 앞서 예고된 전면 정지는 피했다.",
            "미국 내 신규 원전 건설 가능성을 열어두는 전략적 움직임입니다.",
            "EIB의 대출 승인은 루마니아 원전 개보수 사업의 재정적 안정성을 확보한다.",
        ):
            self.assertFalse(data_quality.implication_is_hollow(text), text)

    def test_quantities_and_dates_rescue_a_cliche_ending(self):
        """'언제·얼마'가 실려 있으면 어미가 상투적이어도 정보는 있다."""
        self.assertFalse(data_quality.implication_is_hollow(
            "2028년 착공을 목표로 인허가 절차가 이어질 전망이다."))
        self.assertFalse(data_quality.implication_is_hollow(
            "설비용량 1,400MW 증설분이 계통에 들어올 것으로 예상됩니다."))

    def test_model_numbers_do_not_rescue(self):
        """'AP1000'·'3·4호기'의 숫자를 수량으로 세면 게이트가 사문화된다(실측 오탐)."""
        self.assertTrue(data_quality.implication_is_hollow(
            "주요 원전 공급사 간 협력으로 AP1000 및 AP300 SMR의 시장 확산 가속화를 시사합니다."))

    def test_empty_is_not_hollow(self):
        """빈 값은 '빈껍데기'가 아니다 — 폐기 집계를 부풀리면 안 된다."""
        self.assertFalse(data_quality.implication_is_hollow(""))
        self.assertFalse(data_quality.implication_is_hollow(None))

    def test_hollow_is_not_a_regeneration_error(self):
        """curation_errors 에 들어가면 문체 위반으로 기사가 격리된다."""
        payload = {"summary": "헝가리 총리가 팍스 원전 가동 상황을 발표했다.",
                   "implication": "헝가리 정부의 긍정적 입장을 시사한다."}
        self.assertEqual(data_quality.curation_errors(payload), [])

    def test_implication_limit_allows_a_cause_clause(self):
        """60자로는 원인·다음 절차를 못 담는다 — 한도를 90자로 올렸다."""
        self.assertEqual(data_quality.IMPLICATION_LIMIT, 90)
        payload = {"summary": "요약 문장이다.",
                   "implication": "다뉴브강 수위가 회복되며 냉각수 취수 제한이 풀린 결과로, "
                                  "앞서 예고된 전면 정지는 피했다."}
        self.assertEqual(data_quality.curation_errors(payload), [])


if __name__ == "__main__":
    unittest.main()


class TestPortalRelaysAreNotIndependent(unittest.TestCase):
    """포털 중계는 독립 출처가 아니다.

    원 매체 기사를 그대로 실어 나르므로, 원문과 포털 사본이 같은 이슈 클러스터에
    들어가면 build_data._is_independent_source 가 둘 다 세어 '복수 출처 확인'
    배지를 위조한다. 실측 2026-08-06: 아카이브 970건 중 23건이 포털 중계였고
    **전부 independent 로 잡혀 있었다**(네이트 12 · v.daum.net 6 · MSN 5).

    대부분 Google News 피드의 <source> 가 포털로 찍혀 들어오므로 domain 은
    news.google.co.kr 이다 — 별칭(publisher) 매칭이 살아 있어야 잡힌다.
    """

    PORTALS = ("네이트", "v.daum.net", "MSN", "다음", "네이버")

    def test_portal_publisher_is_a_relay(self):
        for publisher in self.PORTALS:
            with self.subTest(publisher=publisher):
                profile = source_profile("news.google.co.kr", publisher)
                self.assertEqual("distributed_claim", profile["evidence_role"])

    def test_portal_domain_is_a_relay(self):
        for domain in ("daum.net", "v.daum.net", "n.news.naver.com",
                       "news.nate.com", "msn.com"):
            with self.subTest(domain=domain):
                self.assertEqual("distributed_claim",
                                 source_profile(domain, "")["evidence_role"])

    def test_real_outlets_stay_independent(self):
        # 포털을 거른다고 진짜 매체까지 걸리면 검증이 통째로 죽는다.
        for domain, publisher in (("yna.co.kr", "연합뉴스"),
                                  ("news.google.co.kr", "전기신문"),
                                  ("chosun.com", "조선일보"),
                                  ("kbs.co.kr", "KBS 뉴스")):
            with self.subTest(publisher=publisher):
                self.assertEqual("independent",
                                 source_profile(domain, publisher)["evidence_role"])

    def test_official_sources_are_unaffected(self):
        self.assertEqual("primary", source_profile("nssc.go.kr", "")["evidence_role"])

    def test_ranking_tier_is_unchanged(self):
        # 랭킹 점수는 건드리지 않는다 — 등록 전에도 tier3 기본값이었다.
        self.assertEqual(3, source_profile("news.google.co.kr", "네이트")["source_tier"])
