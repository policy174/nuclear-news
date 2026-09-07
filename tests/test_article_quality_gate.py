"""Deterministic article/card quality gate tests (no network or LLM)."""

import unittest

import article_quality_gate as gate


class ArticleIntegrityTests(unittest.TestCase):
    def test_normal_translation_with_same_entities_and_quantity_passes(self):
        article = {
            "title": (
                "Doosan Enerbility signs TerraPower equipment contract for "
                "345 MW US Natrium SMR project"
            ),
            "title_kr": "두산에너빌리티, 미국 테라파워 345MW SMR 기자재 계약",
            "summary": "두산에너빌리티가 테라파워의 345MW급 SMR 기자재를 공급한다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.findings, ())

    def test_gross_entity_and_topic_switch_is_quarantined(self):
        article = {
            "title": "Cameco starts construction at a new Canadian uranium mine",
            "title_kr": "스페인 알마라즈 원전 수명 연장 결정",
            "summary": "스페인 정부가 알마라즈 원전의 가동 시한을 연장했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        self.assertIn("title_source_mismatch", [f.code for f in result.findings])

    def test_observed_canada_mine_to_spain_plant_corruption_is_caught(self):
        """Regression for archive/2026-08.jsonl hash b2f90d7ef7549859."""
        article = {
            "title": "캐나다, 우라늄 광산 착공…세계 공급 20% 생산",
            "title_kr": "스페인 알마라즈 원전 수명 2030년까지 연장",
            "summary": "스페인 정부가 알마라즈 원전 가동 시한을 연장하기로 결정했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-16")
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertTrue(finding.details["country_conflict"])
        self.assertTrue(finding.details["topic_conflict"])

    def test_country_difference_alone_never_hard_blocks_translation(self):
        article = {
            "title": "Korean consortium prepares bid for Czech nuclear project",
            "title_kr": "한국 컨소시엄, 원전 사업 입찰 준비",
            "summary": "한국 컨소시엄이 해외 원전 사업 입찰을 준비한다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertNotEqual(result.action, "quarantine")

    def test_changed_key_quantity_is_quarantined(self):
        article = {
            "title": "Government approves eight new reactors",
            "title_kr": "정부, 신규 원자로 10기 건설 승인",
            "summary": "정부가 신규 원자로 10기 건설을 승인했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertIn("기", finding.details["quantity_conflicts"])

    def test_changed_quantity_with_standard_unit_spacing_is_quarantined(self):
        article = {
            "title": "Utility plans a 2400 MW nuclear project",
            "title_kr": "전력사, 1200MW 원전 사업 추진",
            "summary": "전력사가 1200MW 원전 사업을 추진한다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertIn("mw", finding.details["quantity_conflicts"])

    def test_equivalent_power_units_match_but_changed_value_is_quarantined(self):
        equivalent = {
            "title": "Developer plans a 0.345 GW reactor",
            "title_kr": "사업자, 345MW 원자로 건설 추진",
            "summary": "사업자가 345MW 원자로 건설을 추진한다.",
            "features": {},
        }
        self.assertNotEqual(
            gate.audit_article_integrity(equivalent, reference_date="2026-08-17").action,
            "quarantine",
        )

        changed = {**equivalent, "title_kr": "사업자, 500MW 원자로 건설 추진"}
        result = gate.audit_article_integrity(changed, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        finding = next(f for f in result.findings if f.code == "title_source_mismatch")
        self.assertIn("mw", finding.details["quantity_conflicts"])

    def test_european_decimal_comma_matches_decimal_point_translation(self):
        article = {
            "title": "Le taux du Livret A remonte à 1,7 %",
            "title_kr": "리브레 A 금리 1.7%로 인상",
            "summary": "리브레 A 금리가 1.7%로 인상된다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertNotEqual(result.action, "quarantine")

    def test_partial_overlap_cannot_hide_added_entity_country_or_quantity(self):
        article = {
            "title": "Doosan and TerraPower sign a 345 MW US supply contract",
            "description": "Doosan will supply TerraPower equipment for the US project.",
            "title_kr": "두산·테라파워·EDF, 미국·프랑스 345MW·900MW 공급 계약",
            "summary": "두산과 테라파워, EDF가 미국과 프랑스 사업에 참여한다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        finding = next(row for row in result.findings if row.code == "title_source_mismatch")
        self.assertIn("edf", finding.details["introduced_entities"])
        self.assertIn("FR", finding.details["introduced_countries"])
        self.assertEqual(
            finding.details["quantity_conflicts"]["mw"]["unsupported_output"],
            ["900"],
        )

    def test_new_critical_quantity_without_any_source_quantity_is_blocked(self):
        article = {
            "title": "Doosan and TerraPower sign an equipment supply contract",
            "description": "The companies announced the equipment agreement today.",
            "title_kr": "두산·테라파워, 900MW 기자재 공급 계약",
            "summary": "두산이 테라파워에 기자재를 공급한다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        finding = next(row for row in result.findings if row.code == "title_source_mismatch")
        self.assertEqual(finding.details["quantity_conflicts"]["mw"]["source"], [])

    def test_source_subsets_and_description_supported_additions_are_not_overblocked(self):
        article = {
            "title": "Korean Doosan signs US contracts for 345 MW and 500 MW projects",
            "description": "TerraPower is the US counterparty.",
            "title_kr": "두산, 미국 테라파워 345MW 사업 계약",
            "summary": "두산이 미국 테라파워의 345MW 사업 계약을 체결했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertNotEqual(result.action, "quarantine", result.as_dict())

    def test_sparse_legacy_title_does_not_block_a_body_only_secondary_metric(self):
        article = {
            "title": "상반기 원전 발전량 17% 감소",
            "title_kr": "상반기 원전 발전량 17% 감소, 이용률 80.5% 하락",
            "summary": "상반기 원전 발전량과 이용률이 함께 하락했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertNotEqual(result.action, "quarantine", result.as_dict())

    def test_real_world_unit_list_punctuation_and_english_units_normalize(self):
        cases = (
            ("고리 3ㆍ4호기 계속운전 심사", "고리 3·4호기 계속운전 심사"),
            ("China approves eight new nuclear power units", "중국, 신규 원전 8기 승인"),
            ("Wendelstein designs 2-MW gyrotrons", "벤델슈타인, 2MW 자이로트론 개발"),
        )
        for title, title_kr in cases:
            with self.subTest(title=title):
                article = {"title": title, "title_kr": title_kr,
                           "summary": title_kr + "했다.", "features": {}}
                result = gate.audit_article_integrity(article, reference_date="2026-08-17")
                self.assertNotEqual(result.action, "quarantine", result.as_dict())

    def test_summary_entity_switch_is_removed_and_article_quarantined(self):
        article = {
            "title": "TerraPower signs Natrium supplier agreement",
            "title_kr": "테라파워, 나트륨 원자로 공급 협약 체결",
            "summary": "프랑스 EDF가 플라망빌 원전 재가동을 승인했다.",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "quarantine")
        self.assertEqual(result.value["summary"], "")
        self.assertIn("summary", result.removed_fields)

    def test_implausible_far_future_date_is_cleared_not_quarantined(self):
        article = {
            "title": "원전 정책 발표",
            "title_kr": "원전 정책 발표",
            "summary": "정부가 원전 정책을 발표했다.",
            "event_date": "2206-08-14",
            "event_date_type": "scheduled",
            "event_date_precision": "day",
            "event_date_source": "article_text",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.action, "sanitize")
        self.assertIsNone(result.value["event_date"])
        self.assertEqual(result.value["event_date_type"], "unknown")

    def test_historical_occurrence_with_exact_body_date_is_not_assumed_wrong(self):
        article = {
            "title": "Chernobyl 1986 accident lessons",
            "title_kr": "1986년 체르노빌 사고의 교훈",
            "summary": "보고서가 1986년 체르노빌 사고의 교훈을 분석했다.",
            "event_date": "1986-04-26",
            "event_date_type": "occurrence",
            "event_date_precision": "day",
            "event_date_source": "article_text",
            "features": {},
        }
        result = gate.audit_article_integrity(
            article,
            source={"title": article["title"],
                    "article_text": "The Chernobyl accident occurred on April 26, 1986."},
            reference_date="2026-08-17",
        )
        self.assertEqual(result.value["event_date"], "1986-04-26")

    def test_reasonable_scheduled_project_date_is_retained(self):
        article = {
            "title": "SMR operation planned for 2035",
            "title_kr": "SMR, 2035년 가동 예정",
            "summary": "사업자가 2035년 SMR 가동을 계획하고 있다.",
            "event_date": "2035-01-01",
            "event_date_type": "scheduled",
            "event_date_precision": "year",
            "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.value["event_date"], "2035-01-01")

    def test_date_is_cleared_when_declared_title_contains_no_date_marker(self):
        article = {
            "title": "정부, 신규 원전 정책 발표",
            "title_kr": "정부, 신규 원전 정책 발표",
            "summary": "정부가 신규 원전 정책을 발표했다.",
            "event_date": "2024-08-14",
            "event_date_type": "announcement",
            "event_date_precision": "day",
            "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_source_unsubstantiated",
                      [finding.code for finding in result.findings])

    def test_date_with_unknown_source_is_cleared(self):
        article = {
            "title": "정부, 원전 정책 발표", "title_kr": "정부, 원전 정책 발표",
            "summary": "정부가 원전 정책을 발표했다.",
            "event_date": "2024-08-14", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "unknown",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_source_unknown", [row.code for row in result.findings])

    def test_article_text_date_is_cleared_when_body_is_not_available(self):
        article = {
            "title": "정부, 원전 계획 발표",
            "title_kr": "정부, 원전 계획 발표",
            "summary": "정부가 원전 계획을 발표했다.",
            "event_date": "2026-08-14",
            "event_date_type": "announcement",
            "event_date_precision": "day",
            "event_date_source": "article_text",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_source_unavailable", [row.code for row in result.findings])

    def test_compact_and_short_title_dates_count_as_date_evidence(self):
        for title, event_date in (
            ("아시아라운드업 20260807", "2026-08-07"),
            ("2036년 전망", "2036-01-01"),
            ("8/13 주목할 종목", "2026-08-13"),
        ):
            with self.subTest(title=title):
                article = {
                    "title": title, "title_kr": title,
                    "summary": "시장 주요 동향을 정리해 발표했다.",
                    "event_date": event_date,
                    "event_date_type": "scheduled" if event_date.startswith("2036") else "announcement",
                    "event_date_precision": "year" if title.startswith("2036") else "day",
                    "event_date_source": "title",
                    "features": {},
                }
                result = gate.audit_article_integrity(article, reference_date="2026-08-17")
                self.assertEqual(result.value["event_date"], event_date)

    def test_provided_body_without_any_date_marker_disproves_date_source(self):
        article = {
            "title": "정부, 원전 계획 발표",
            "title_kr": "정부, 원전 계획 발표",
            "summary": "정부가 원전 계획을 발표했다.",
            "event_date": "2026-08-14",
            "event_date_type": "announcement",
            "event_date_precision": "day",
            "event_date_source": "article_text",
            "features": {},
        }
        result = gate.audit_article_integrity(
            article,
            source={"title": article["title"],
                    "article_text": "정부는 원전 계획의 주요 내용을 공개했다."},
            reference_date="2026-08-17",
        )
        self.assertIsNone(result.value["event_date"])

    def test_different_explicit_source_date_is_cleared(self):
        article = {
            "title": "정부, 원전 계획 발표", "title_kr": "정부, 원전 계획 발표",
            "summary": "정부가 원전 계획을 발표했다.",
            "event_date": "2026-08-17", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "description",
            "features": {},
        }
        result = gate.audit_article_integrity(
            article,
            source={"title": article["title"],
                    "description": "정부는 2026-08-16 계획을 발표했다."},
            reference_date="2026-08-17",
        )
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_source_conflict", [row.code for row in result.findings])

    def test_matching_korean_source_date_is_kept(self):
        article = {
            "title": "정부, 원전 계획 발표", "title_kr": "정부, 원전 계획 발표",
            "summary": "정부가 원전 계획을 발표했다.",
            "event_date": "2026-08-16", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "description",
            "features": {},
        }
        result = gate.audit_article_integrity(
            article,
            source={"title": article["title"],
                    "description": "정부는 8월 16일 계획을 발표했다."},
            reference_date="2026-08-17",
        )
        self.assertEqual(result.value["event_date"], "2026-08-16")

    def test_relative_source_date_uses_publication_reference(self):
        article = {
            "title": "정부, 원전 계획 발표", "title_kr": "정부, 원전 계획 발표",
            "summary": "정부가 원전 계획을 발표했다.",
            "event_date": "2026-08-16", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "description",
            "features": {},
        }
        result = gate.audit_article_integrity(
            article,
            source={"title": article["title"], "description": "정부는 어제 계획을 발표했다."},
            reference_date="2026-08-17",
        )
        self.assertEqual(result.value["event_date"], "2026-08-16")

    def test_year_only_evidence_rejects_a_different_event_year(self):
        article = {
            "title": "2036년 SMR 가동 예정", "title_kr": "2036년 SMR 가동 예정",
            "summary": "2036년 SMR 가동이 예정돼 있다.",
            "event_date": "2024-08-14", "event_date_type": "scheduled",
            "event_date_precision": "day", "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])

    def test_year_precision_survives_an_unrelated_full_date(self):
        article = {
            "title": "2026-08-17 정부, 2035년 SMR 가동 계획 발표",
            "title_kr": "정부, 2035년 SMR 가동 계획 발표",
            "summary": "정부가 2035년 SMR 가동 계획을 발표했다.",
            "event_date": "2035-01-01", "event_date_type": "scheduled",
            "event_date_precision": "year", "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.value["event_date"], "2035-01-01")

    def test_decimal_is_not_misread_as_month_and_day(self):
        article = {
            "title": "2026년 세제개편안 지역계수 최대 1.5 적용",
            "title_kr": "2026년 세제개편안 지역계수 최대 1.5 적용",
            "summary": "정부가 지역계수 최대 1.5 적용안을 발표했다.",
            "event_date": "2026-01-01", "event_date_type": "scheduled",
            "event_date_precision": "year", "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertEqual(result.value["event_date"], "2026-01-01")

    def test_month_only_deadline_cannot_invent_a_day(self):
        article = {
            "title": "2027년도 지원사업 공모, 오는 9월 말까지 접수",
            "title_kr": "2027년도 지원사업 공모, 9월 말 접수 마감",
            "summary": "2027년도 지원사업 신청을 9월 말까지 받는다.",
            "event_date": "2026-09-30", "event_date_type": "deadline",
            "event_date_precision": "day", "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_source_unsubstantiated",
                      [row.code for row in result.findings])

    def test_completed_event_more_than_timezone_skew_in_future_is_cleared(self):
        article = {
            "title": "정부, 9월 20일 원전 정책 발표",
            "title_kr": "정부, 9월 20일 원전 정책 발표",
            "summary": "정부가 원전 정책을 발표했다.",
            "event_date": "2026-09-20", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "title",
            "features": {},
        }
        result = gate.audit_article_integrity(article, reference_date="2026-08-17")
        self.assertIsNone(result.value["event_date"])
        self.assertIn("event_date_future_completed_event",
                      [row.code for row in result.findings])


class CurrencyQuantityTests(unittest.TestCase):
    """달러 금액도 수치다 — 예전에는 이 축이 통째로 안 보였다.

    `_NUMBER_UNIT_RE` 의 단위 목록에 `억 달러` 가 없어서 숫자와 `달러` 사이의 '억'
    이 매칭을 끊었다. 그래서 달러 금액은 문자열이 글자 그대로 같을 때만 검증됐고,
    대본이 표현만 바꿔 금액을 틀려도 수치 규칙은 아무 말을 하지 않았다.
    """

    def test_korean_multiplier_units_are_read_as_money(self):
        self.assertEqual(gate._quantity_map("1050억 달러"), {"달러": {"105000000000"}})
        self.assertEqual(gate._quantity_map("3조 달러"), {"달러": {"3000000000000"}})
        self.assertEqual(gate._quantity_map("500만 달러"), {"달러": {"5000000"}})

    def test_same_amount_in_either_language_is_one_value(self):
        """`1050억 달러` 와 `$105 billion` 은 같은 금액이다."""
        for text in ("1050억 달러", "1,050억달러", "$105 billion", "105 billion dollars"):
            self.assertEqual(gate._quantity_map(text), {"달러": {"105000000000"}}, text)

    def test_non_currency_scale_words_are_not_money(self):
        """`5 million tonnes` 가 달러가 되면 없는 충돌이 생긴다."""
        self.assertEqual(gate._quantity_map("5 million tonnes of uranium"), {})

    def test_wrong_dollar_amount_now_conflicts(self):
        source = "엔비디아가 데이터센터에 최대 1050억 달러를 보증한다."
        self.assertEqual(gate._critical_quantity_conflicts(source, "최대 1050억 달러를 보증합니다."), {})
        conflict = gate._critical_quantity_conflicts(source, "최대 10500억 달러를 보증합니다.")
        self.assertEqual(conflict["달러"]["unsupported_output"], ["1050000000000"])

    def test_dollars_and_won_stay_separate_units(self):
        """환율 환산은 하지 않는다 — 그래야 억 달러를 조 원으로 옮긴 실수가 걸린다.

        실제 사고(2026-08-18): 기사의 `1050억 달러(약 149조 원)` 가 전문가 대본에서
        `1050조 원` 이 되어 방송됐다. 숫자만 옮기고 단위를 갈아 끼운 형태다.
        """
        source = "최대 1050억 달러(약 149조 원)의 금융 보증을 제공한다."
        self.assertEqual(gate._quantity_map(source),
                         {"달러": {"105000000000"}, "조원": {"149"}})
        conflict = gate._critical_quantity_conflicts(source, "최대 1050조 원을 보증합니다.")
        self.assertEqual(conflict["조원"]["unsupported_output"], ["1050"])


class DeliveryManifestEvidenceTests(unittest.TestCase):
    """발송 직전 게이트도 큐레이션이 봉인한 근거를 읽는다.

    큐레이션 호출은 받아 온 **본문**을 보고 `verified_evidence` 를 만든다. 발송
    직전에는 본문이 없고(저작권상 보존하지 않는다) 큐에 남은 것은 제목과 짧은
    `source_excerpt` 뿐이다. 그 excerpt 는 대개 제목을 다시 쓴 한 줄이라 본문에만
    있던 사실을 담지 못한다.

    그런데 excerpt 가 **비어 있지 않다**는 사실 자체가 `quantity_is_hard` ·
    `directional_is_hard` 를 켠다. 그 두 스위치는 "진짜 본문 근거를 들고 있다"는
    뜻으로 설계된 것인데, 실제로 들고 있는 것은 제목 한 줄이다. 그래서 본문에서
    비롯한 정상 요약이 '지어낸 사실'로 분류됐다.

    실측 2026-08-18~22 발송 직전 격리 82건(보존된 판정 신호 전수)에서 74건
    (90.2%)이 **이미 manifest 안에 있던 사실** 때문에 차단됐다.
    """

    BOUND = {
        "hash": "delivery-manifest-1",
        "title": "김성환 장관 “전력, 산업 보조수단 아닌 국가 안보자산”",
        # 실제 큐 값의 모양 — 제목을 다시 쓰고 매체명을 붙인 한 줄이다.
        "source_excerpt": "김성환 장관 “전력, 산업 보조수단 아닌 국가 안보자산” 인사이트N파워",
        "published_at": "2026-08-22T09:00:00+09:00",
        "features": {},
    }

    def manifest_for(self, body):
        return gate.build_evidence_manifest(
            {"title": self.BOUND["title"],
             "description": self.BOUND["source_excerpt"],
             "article_text": body,
             "published_at": self.BOUND["published_at"]},
            article=self.BOUND,
        )

    def source(self, article):
        """daily_brief._quality_source 와 같은 모양 — 본문은 없다."""
        return {"title": article.get("title", ""),
                "description": article.get("source_excerpt", ""),
                "published_at": article.get("published_at")}

    def test_body_only_fact_missing_from_the_excerpt_is_not_a_mismatch(self):
        """실측 d60c75d7877511ce — 제목에 없는 '전기본'이 본문에는 있었다."""
        body = ("김성환 기후에너지환경부 장관은 제12차 전력수급기본계획에서 "
                "수요 전망을 재산정하겠다고 밝혔다.")
        article = {
            **self.BOUND,
            "verified_evidence": self.manifest_for(body),
            "title_kr": "김성환 기후에너지환경부 장관, 전력을 국가 안보자산으로 규정",
            "summary": ("김성환 장관은 전력을 국가 안보자산으로 격상하고, 제12차 "
                        "전력수급기본계획에서 수요 전망을 재산정하겠다고 밝혔다."),
        }
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertNotEqual(result.action, "quarantine", result.findings)

    def test_the_same_article_without_a_manifest_still_blocks(self):
        """근거가 없으면 통과가 아니다 — 판단 불가와 사실 일치는 다르다."""
        article = {
            **self.BOUND,
            "title_kr": "김성환 기후에너지환경부 장관, 전력을 국가 안보자산으로 규정",
            "summary": ("김성환 장관은 제12차 전력수급기본계획에서 수요 전망을 "
                        "재산정하겠다고 밝혔다."),
        }
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")

    def test_a_manifest_bound_to_another_article_grants_nothing(self):
        """결속이 어긋난 manifest 는 무효다 — 남의 근거로 통과시키지 않는다."""
        stolen = self.manifest_for(
            "김성환 장관이 제12차 전력수급기본계획을 언급했다.")
        article = {
            **self.BOUND,
            "hash": "a-different-article",
            "verified_evidence": stolen,
            "title_kr": "김성환 기후에너지환경부 장관, 전력을 국가 안보자산으로 규정",
            "summary": ("김성환 장관은 제12차 전력수급기본계획에서 수요 전망을 "
                        "재산정하겠다고 밝혔다."),
        }
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")

    def test_manifest_never_licenses_a_fact_the_body_did_not_have(self):
        """본문에 없던 수치는 manifest 가 있어도 그대로 막힌다."""
        article = {
            **self.BOUND,
            "verified_evidence": self.manifest_for(
                "김성환 장관이 전력 수요 대응을 강조했다."),
            "title_kr": "김성환 장관, 전력 국가 안보자산 규정",
            "summary": "김성환 장관은 원전 비중을 47.2%로 높이겠다고 밝혔다.",
        }
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")

    def test_entity_replacement_is_not_excused_by_a_manifest(self):
        """A사 → B사 교체는 계속 차단된다 — manifest 에 B 가 없기 때문이다."""
        article = {
            "hash": "swap-1",
            "title": "Doosan Enerbility signs a TerraPower equipment contract",
            "source_excerpt": "Doosan Enerbility signs a TerraPower contract Reuters",
            "published_at": "2026-08-22T09:00:00+09:00",
            "features": {},
            "title_kr": "웨스팅하우스, 홀텍과 기자재 계약 체결",
            "summary": "웨스팅하우스가 홀텍과 SMR 기자재 공급 계약을 체결했다.",
        }
        article["verified_evidence"] = gate.build_evidence_manifest(
            {"title": article["title"], "description": article["source_excerpt"],
             "article_text": "Doosan Enerbility signed the TerraPower contract.",
             "published_at": article["published_at"]},
            article=article)
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")

    def test_country_switch_is_not_excused_by_a_manifest(self):
        """미국 사건 → 캐나다 사건도 그대로 차단된다."""
        article = {
            "hash": "swap-2",
            "title": "Cameco starts construction at a new Canadian uranium mine",
            "source_excerpt": "Cameco starts a Canadian uranium mine World Nuclear News",
            "published_at": "2026-08-22T09:00:00+09:00",
            "features": {},
            "title_kr": "스페인 알마라즈 원전 수명 연장 결정",
            "summary": "스페인 정부가 알마라즈 원전의 가동 시한을 연장했다.",
        }
        article["verified_evidence"] = gate.build_evidence_manifest(
            {"title": article["title"], "description": article["source_excerpt"],
             "article_text": "Cameco began construction in Saskatchewan, Canada.",
             "published_at": article["published_at"]},
            article=article)
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")

    def test_real_quantity_conflict_still_blocks(self):
        """원문이 **다른 값**을 말하면 계속 차단이다 ($50M → 3억 5천만 달러).

        한국어 복합 수사(`3억 5,000만`)는 뒤 자리만 읽히는 별개의 파서 한계가
        있어서, 이 테스트는 그 한계에 기대지 않는 표기를 쓴다.
        """
        article = {
            "hash": "money-1",
            "title": "Groq raises $50 million in a new funding round",
            "source_excerpt": "Groq raises $50 million Bloomberg",
            "published_at": "2026-08-22T09:00:00+09:00",
            "features": {},
            "title_kr": "그록, 3억 5000만 달러 투자 유치",
            "summary": "AI 추론 기업 그록이 350000000달러 규모의 투자를 유치했다.",
        }
        article["verified_evidence"] = gate.build_evidence_manifest(
            {"title": article["title"], "description": article["source_excerpt"],
             "article_text": "Groq raised $50 million.",
             "published_at": article["published_at"]},
            article=article)
        result = gate.audit_article_integrity(
            article, source=self.source(article),
            reference_date=article["published_at"])
        self.assertEqual(result.action, "quarantine")


class AbbreviatedMoneyScaleTests(unittest.TestCase):
    """`$20bn` 을 못 읽으면 매치가 실패하는 것이 아니라 **금액이 틀리게 읽힌다**.

    자릿수 그룹이 선택적이라 `bn` 을 모르면 `$20` 만 잡히고 값은 20 이 된다.
    한국어 `200억 달러` 는 20,000,000,000 이므로 같은 금액이 충돌로 잡힌다.
    실측 2026-08-20 캐나다 관세 기사가 이 경로로 차단됐다.
    """

    def test_abbreviated_scales_match_the_full_words(self):
        for text in ("$20bn", "$20 billion", "20 billion dollars"):
            self.assertEqual(gate._quantity_map(text), {"달러": {"20000000000"}}, text)
        self.assertEqual(gate._quantity_map("$800m"), {"달러": {"800000000"}})
        self.assertEqual(gate._quantity_map("$1.2bn"), {"달러": {"1200000000"}})
        self.assertEqual(gate._quantity_map("$600B"), {"달러": {"600000000000"}})
        self.assertEqual(gate._quantity_map("250mn dollars"), {"달러": {"250000000"}})

    def test_observed_canada_tariff_false_positive_is_gone(self):
        source = "Canada makes final attempt to avoid US tariffs on $20bn of goods"
        summary = "캐나다 정부가 200억 달러 규모의 자국 상품에 부과될 관세를 피하려 한다."
        self.assertEqual(gate._critical_quantity_conflicts(source, summary), {})

    def test_french_milliard_is_a_billion(self):
        """실측 2026-08-20 — 원문이 불어라 금액 축이 통째로 안 보였다."""
        self.assertEqual(gate._quantity_map("105 milliards de dollars"),
                         {"달러": {"105000000000"}})
        self.assertEqual(
            gate._critical_quantity_conflicts(
                "Nvidia garantit 105 milliards de dollars",
                "엔비디아가 1,050억 달러 규모의 자금 조달을 보증했다."),
            {})

    def test_hanja_dollar_sign_is_money(self):
        """`弗` 은 달러의 한자 표기다 — 실측 '3500억弗 대미투자'."""
        self.assertEqual(gate._quantity_map("3500억弗"), {"달러": {"350000000000"}})
        self.assertEqual(
            gate._critical_quantity_conflicts(
                '구윤철 "3500억弗 대미투자, 에너지 관심 높아"',
                "구윤철 부총리는 3500억 달러 투자를 언급했다."),
            {})

    def test_a_wrong_abbreviated_amount_still_conflicts(self):
        """축약 표기를 읽게 됐다고 금액 검증이 느슨해지지는 않는다."""
        conflict = gate._critical_quantity_conflicts(
            "Canada faces US tariffs on $20bn of goods",
            "캐나다가 2000억 달러 규모 관세에 직면했다.")
        self.assertEqual(conflict["달러"]["unsupported_output"], ["200000000000"])

    def test_non_currency_scale_words_are_still_not_money(self):
        for text in ("5 million tonnes of uranium", "20 million people",
                     "300MW SMR", "the 20m reactor vessel"):
            self.assertEqual(gate._quantity_map(text).get("달러"), None, text)


class DirectionalAdditionTests(unittest.TestCase):
    """요약에만 있는 이름과 요약에만 있는 국가는 같은 무게가 아니다.

    본문이 '오하이오주'·'NRC' 라고만 써도 올바른 한국어 요약은 '미국'이라고 쓴다.
    그 승격을 환각으로 세면 정상 기사가 격리된다 — 실측 2026-08-18 의 발송 직전
    격리 20건 중 9건(45%)이 이 사유 하나뿐이었다.
    """

    def signals(self, **overrides):
        base = {
            "entity_conflict": False, "country_conflict": False,
            "topic_conflict": False, "stage_conflict": False,
            "entity_replacement": False, "country_replacement": False,
            "topic_replacement": False, "quantity_conflicts": {},
            "introduced_entities": [], "introduced_countries": [],
        }
        return {**base, **overrides}

    def test_lone_introduced_country_is_not_a_hard_block(self):
        self.assertFalse(gate._gross_mismatch(
            self.signals(introduced_countries=["US"]), directional_is_hard=True))

    def test_lone_introduced_entity_still_blocks(self):
        self.assertTrue(gate._gross_mismatch(
            self.signals(introduced_entities=["holtec"]), directional_is_hard=True))

    def test_country_replacement_still_blocks(self):
        """국가가 **바뀐** 것은 여전히 차단이다 — 추가와 교체를 가른다."""
        self.assertTrue(gate._gross_mismatch(self.signals(
            country_replacement=True, introduced_countries=["ES"],
            topic_conflict=True), directional_is_hard=True))


class EligibilityTests(unittest.TestCase):
    BASE = {
        "title": "한수원, 원전 계약 체결",
        "title_kr": "한수원, 원전 계약 체결",
        "summary": "한수원이 원전 기자재 계약을 체결했다.",
    }

    def test_reviewed_record_is_eligible(self):
        decision = gate.assess_delivery_eligibility({**self.BASE, "features": {}})
        self.assertEqual((decision.status, decision.eligible, decision.action),
                         ("reviewed", True, "auto_send"))

    def test_inferred_fallback_is_held(self):
        decision = gate.assess_delivery_eligibility(dict(self.BASE))
        self.assertEqual(decision.status, "fallback")
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.action, "hold")

    def test_old_non_fallback_schema_remains_compatible_by_default(self):
        legacy = {**self.BASE, "title": "KHNP signs a nuclear contract",
                  "implication": "국내 공급망 참여 기회가 확대될 수 있다."}
        decision = gate.assess_delivery_eligibility(legacy)
        self.assertEqual(decision.status, "unreviewed")
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.action, "legacy_allow")

    def test_old_schema_with_terse_summary_remains_compatible(self):
        legacy = {**self.BASE, "title": "Old nuclear article", "summary": "요약",
                  "implication": "기존 큐에 저장된 시사점"}
        decision = gate.assess_delivery_eligibility(legacy)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.action, "legacy_allow")

    def test_strict_mode_holds_old_unreviewed_schema(self):
        legacy = {**self.BASE, "title": "KHNP signs a nuclear contract",
                  "implication": "국내 공급망 참여 기회가 확대될 수 있다."}
        decision = gate.assess_delivery_eligibility(legacy, legacy_compat=False)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.action, "hold")

    def test_primary_fallback_requires_explicit_opt_in_and_drops_analysis(self):
        fallback = {**self.BASE, "curation_status": "fallback",
                    "source_type": "official", "evidence_role": "primary"}
        self.assertFalse(gate.assess_delivery_eligibility(fallback).eligible)
        allowed = gate.assess_delivery_eligibility(
            fallback, allow_primary_fallback=True
        )
        self.assertTrue(allowed.eligible)
        self.assertIn("investment", allowed.limitations)

    def test_integrity_quarantine_overrides_reviewed_status(self):
        article = {**self.BASE, "features": {}}
        integrity = gate.GateResult(dict(article), "quarantine")
        decision = gate.assess_delivery_eligibility(article, integrity=integrity)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.action, "quarantine")


class FinalCardTests(unittest.TestCase):
    ARTICLE = {
        "hash": "terrapower-article-hash",
        "title": "Doosan Enerbility signs TerraPower 345 MW equipment contract",
        "title_kr": "두산에너빌리티, 테라파워 345MW 기자재 계약",
        "summary": "두산에너빌리티가 테라파워의 345MW급 기자재를 공급한다.",
        "source_excerpt": "Doosan Enerbility signed the 345 MW equipment contract.",
        "detail": "계약은 미국 원자로 프로젝트에 적용된다.",
        "published_at": "2026-08-16T00:00:00+00:00",
        "features": {},
    }

    def _card(self, **overrides):
        card = {
            "headline": "두산에너빌리티, 테라파워 345MW 기자재 계약",
            "what": "두산에너빌리티가 테라파워의 345MW급 기자재를 공급한다.",
            "why": "국내 제조사의 글로벌 공급망 참여가 확대됐다.",
            "investment": "SMR 기자재 공급망 수혜 가능성이 있다.",
            "kr_takeaway": "한수원의 해외 SMR 공급망 전략에 참고할 수 있다.",
        }
        card.update(overrides)
        return card

    def test_supported_card_passes(self):
        result = gate.validate_final_card(self._card(), self.ARTICLE)
        self.assertEqual(result.action, "allow")

    def test_unsupported_optional_quantity_drops_only_that_field(self):
        card = self._card(why="계약 규모는 900MW로 확정됐다.")
        result = gate.validate_final_card(card, self.ARTICLE)
        self.assertEqual(result.action, "sanitize")
        self.assertIsNone(result.value["why"])
        self.assertEqual(result.removed_fields, ("why",))
        self.assertIsNotNone(result.value["what"])

    def test_core_summary_cannot_validate_itself(self):
        article = {
            **self.ARTICLE,
            "summary": "두산에너빌리티가 테라파워의 900MW급 기자재를 공급한다.",
        }
        card = self._card(what=article["summary"])
        result = gate.validate_final_card(card, article)
        self.assertEqual(result.action, "quarantine")
        self.assertIn("card_what_unsupported", [row.code for row in result.findings])

    def test_hallucinated_detail_cannot_launder_an_optional_claim(self):
        article = {
            **self.ARTICLE,
            "detail": "EDF가 900MW 계약을 체결했다.",
        }
        card = self._card(why="EDF가 900MW 계약을 체결했다.")
        result = gate.validate_final_card(card, article)
        self.assertEqual(result.action, "sanitize")
        self.assertIsNone(result.value["why"])

    def test_verified_body_manifest_supports_later_optional_claims(self):
        bound_article = {**self.ARTICLE, "source_excerpt": ""}
        manifest = gate.build_evidence_manifest({
            "title": bound_article["title"],
            "article_text": (
                "The NRC issued the construction permit for Kemmerer unit 1 "
                "on March 9, 2024. TerraPower is developing the project in the US."
            ),
        }, article=bound_article)
        article = {**bound_article, "verified_evidence": manifest}
        card = self._card(
            kr_takeaway=(
                "미국 NRC가 2024년 3월 9일 테라파워 케머러 1호기 "
                "건설허가를 발급했다."
            )
        )
        result = gate.validate_final_card(card, article)
        self.assertNotIn("kr_takeaway", result.removed_fields, result.as_dict())

    def test_manifest_date_requires_matching_month_as_well_as_year_and_day(self):
        bound_article = {**self.ARTICLE, "source_excerpt": ""}
        manifest = gate.build_evidence_manifest(
            {"title": bound_article["title"],
             "article_text": "The NRC issued the permit on March 9, 2024."},
            article=bound_article,
        )
        article = {**bound_article, "verified_evidence": manifest}
        result = gate.validate_final_card(
            self._card(why="NRC가 2024년 4월 9일 허가를 발급했다."), article
        )
        self.assertIn("why", result.removed_fields)

    def test_manifest_is_bound_to_article_and_retained_source_fingerprint(self):
        manifest = gate.build_evidence_manifest(
            {"title": self.ARTICLE["title"],
             "article_text": "EDF signed a 900 MW contract."},
            article=self.ARTICLE,
        )
        other = {
            **self.ARTICLE,
            "hash": "different-article-hash",
            "verified_evidence": manifest,
        }
        reused = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."), other
        )
        self.assertIn("why", reused.removed_fields)

        stale = {
            **self.ARTICLE,
            "source_excerpt": "This source text changed after the cache hit.",
            "verified_evidence": manifest,
            # Queue validation must prefer the actual retained excerpt over a
            # copied archive component digest.
            "verified_source_components": gate.evidence_manifest_source_components(manifest),
        }
        stale_result = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."), stale
        )
        self.assertIn("why", stale_result.removed_fields)

    def test_archive_can_validate_manifest_with_component_digests_only(self):
        manifest = gate.build_evidence_manifest(
            {"title": self.ARTICLE["title"],
             "description": self.ARTICLE["source_excerpt"],
             "published_at": self.ARTICLE["published_at"],
             "article_text": "EDF signed a 900 MW contract."},
            article=self.ARTICLE,
        )
        archive_article = {
            key: value for key, value in self.ARTICLE.items()
            if key not in {"source_excerpt", "published_at"}
        }
        archive_article.update({
            "verified_evidence": manifest,
            "verified_source_components": gate.evidence_manifest_source_components(manifest),
        })
        allowed = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."), archive_article
        )
        self.assertNotIn("why", allowed.removed_fields, allowed.as_dict())

        tampered_components = dict(archive_article["verified_source_components"])
        tampered_components["source_excerpt"] = "0" * 64
        rejected = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."),
            {**archive_article, "verified_source_components": tampered_components},
        )
        self.assertIn("why", rejected.removed_fields)

    def test_manifest_content_tampering_and_version_one_are_not_trusted(self):
        manifest = gate.build_evidence_manifest(
            {"title": self.ARTICLE["title"], "article_text": "TerraPower 345 MW."},
            article=self.ARTICLE,
        )
        tampered = dict(manifest)
        tampered["entities"] = ["edf"]
        tampered["quantities"] = {"mw": ["900"]}
        result = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."),
            {**self.ARTICLE, "verified_evidence": tampered},
        )
        self.assertIn("why", result.removed_fields)

        forged_v1 = {
            "version": 1, "entities": ["edf"], "claims": ["900mw"],
            "quantities": {"mw": ["900"]},
        }
        old = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."),
            {**self.ARTICLE, "verified_evidence": forged_v1},
        )
        self.assertIn("why", old.removed_fields)

    def test_unversioned_manifest_cannot_launder_a_claim(self):
        article = {
            **self.ARTICLE,
            "verified_evidence": {"entities": ["edf"], "claims": ["900mw"]},
        }
        result = gate.validate_final_card(
            self._card(why="EDF가 900MW 계약을 체결했다."), article
        )
        self.assertIn("why", result.removed_fields)

    def test_discrete_year_and_reactor_counts_do_not_use_rounding_tolerance(self):
        article = {
            **self.ARTICLE,
            "source_excerpt": "사업은 2024년 원자로 100기 계획을 담고 있다.",
        }
        result = gate.validate_final_card(
            self._card(why="사업은 2025년 원자로 101기 계획으로 확정됐다."), article
        )
        self.assertIn("why", result.removed_fields)

    def test_english_calendar_dates_are_not_model_identifiers(self):
        claims = gate.concrete_claims("Meeting on March 10 and report in August 2026")
        self.assertNotIn("march10", claims)
        self.assertNotIn("august2026", claims)
        self.assertIn("apr1400", gate.concrete_claims("APR1400 reactor design"))

    def test_unsupported_year_with_korean_suffix_is_a_concrete_claim(self):
        result = gate.validate_final_card(
            self._card(why="2030년까지 공급망 참여가 확정됐다."), self.ARTICLE
        )
        self.assertIn("why", result.removed_fields)

    def test_model_identifier_with_korean_suffix_is_checked(self):
        result = gate.validate_final_card(
            self._card(why="AP1000급 기자재 공급이 확정됐다."), self.ARTICLE
        )
        self.assertIn("why", result.removed_fields)

    def test_unsupported_what_quarantines_entire_card(self):
        card = self._card(what="두산에너빌리티가 900MW 공급계약을 체결했다.")
        result = gate.validate_final_card(card, self.ARTICLE)
        self.assertEqual(result.action, "quarantine")
        self.assertIsNone(result.value["what"])
        self.assertIn("what", result.removed_fields)

    def test_rounding_difference_is_supported(self):
        article = {**self.ARTICLE, "source_excerpt": "사업 지분은 47.2%로 집계됐다."}
        result = gate.validate_final_card(
            self._card(why="사업 지분 약 47%가 확보됐다."), article
        )
        self.assertNotIn("why", result.removed_fields)

    def test_quantity_spacing_and_commas_normalize(self):
        article = {**self.ARTICLE,
                   "source_excerpt": "계약 금액은 1,200억 원이며 설비 용량은 345 MW다."}
        card = self._card(why="1,200억원 계약으로 345MW 공급이 확정됐다.")
        result = gate.validate_final_card(card, article)
        self.assertNotIn("why", result.removed_fields)

    def test_unsupported_headline_quarantines_card(self):
        card = self._card(headline="EDF, 플라망빌 900MW 재가동 승인")
        result = gate.validate_final_card(card, self.ARTICLE)
        self.assertEqual(result.action, "quarantine")
        self.assertEqual(result.value["headline"], card["headline"])

    def test_analytic_khnp_perspective_is_allowed(self):
        result = gate.validate_final_card(self._card(), self.ARTICLE)
        self.assertNotIn("kr_takeaway", result.removed_fields)

    def test_unsupported_factual_khnp_participation_is_removed(self):
        card = self._card(kr_takeaway="한수원이 이번 계약을 직접 체결했다.")
        result = gate.validate_final_card(card, self.ARTICLE)
        self.assertIn("kr_takeaway", result.removed_fields)

    def test_analysis_marker_cannot_launder_earlier_factual_country_claim(self):
        result = gate.validate_final_card(
            self._card(why="두산이 스페인에서 계약을 체결해 수혜 가능성이 있다."),
            self.ARTICLE,
        )
        self.assertIn("why", result.removed_fields)

    def test_investment_verb_makes_new_country_a_factual_claim(self):
        result = gate.validate_final_card(
            self._card(investment="두산이 스페인에 투자했다."), self.ARTICLE
        )
        self.assertIn("investment", result.removed_fields)

    def test_possibility_directly_scoping_contract_is_still_analysis(self):
        article = {**self.ARTICLE, "source_excerpt": "Spain is a potential market."}
        result = gate.validate_final_card(
            self._card(why="스페인에서 계약을 체결할 가능성이 있다."), article
        )
        self.assertNotIn("why", result.removed_fields, result.as_dict())

    def test_collection_sanitizer_persists_optional_field_removal(self):
        curation = {
            **self.ARTICLE,
            "why_important": "EDF가 900MW 계약을 체결했다.",
            "implication": "SMR 기자재 공급망 수혜 가능성이 있다.",
            "watch_next": "향후 공급 일정 확인이 필요하다.",
        }
        result = gate.sanitize_curation_optional_fields(
            curation,
            article=self.ARTICLE,
            source={"article_hash": self.ARTICLE["hash"],
                    "title": self.ARTICLE["title"],
                    "description": self.ARTICLE["source_excerpt"],
                    "published_at": self.ARTICLE["published_at"]},
        )
        self.assertEqual(result.value["why_important"], "")
        self.assertIn("why_important", result.removed_fields)
        self.assertEqual(result.value["implication"], curation["implication"])

    def test_collection_sanitizer_matches_actual_implication_consumption(self):
        curation = {
            **self.ARTICLE,
            "implication": "한수원의 해외 SMR 공급망 전략에 참고할 수 있다.",
            "watch_next": "한수원이 이번 계약을 직접 체결했다.",
        }
        result = gate.sanitize_curation_optional_fields(
            curation,
            article=self.ARTICLE,
            source={"article_hash": self.ARTICLE["hash"],
                    "title": self.ARTICLE["title"],
                    "description": self.ARTICLE["source_excerpt"],
                    "published_at": self.ARTICLE["published_at"]},
        )
        self.assertEqual(result.value["implication"], curation["implication"])
        self.assertNotIn("implication", result.removed_fields)
        self.assertEqual(result.value["watch_next"], "")
        self.assertIn("watch_next", result.removed_fields)

    def test_enrichment_cannot_invent_a_named_investment_beneficiary(self):
        card = self._card(investment="EDF의 중장기 수혜 가능성이 있다.")
        result = gate.validate_final_card(card, self.ARTICLE)
        self.assertIn("investment", result.removed_fields)

    def test_no_evidence_does_not_create_false_deletions(self):
        card = self._card()
        result = gate.validate_final_card(card, {})
        self.assertEqual(result.action, "allow")
        self.assertEqual(result.removed_fields, ())
        self.assertEqual(result.findings[0].code, "card_evidence_insufficient")

    def test_diagnostics_are_json_shaped(self):
        result = gate.validate_final_card(
            self._card(investment="2030년까지 900MW를 공급했다."), self.ARTICLE
        )
        payload = result.as_dict()
        self.assertEqual(payload["action"], "sanitize")
        self.assertIn("investment", payload["removed_fields"])


DOOSAN = {
    "hash": "h-doosan",
    "title": "Doosan Enerbility signs TerraPower contract for 345 MW Natrium SMR",
    "title_kr": "두산에너빌리티, 테라파워 345MW 나트륨 SMR 기자재 계약 체결",
    "summary": "두산에너빌리티가 미국 테라파워의 345MW급 SMR 기자재를 공급한다.",
    "article_date": "2026-08-14",
}
KHNP = {
    "hash": "h-khnp",
    "title": "KHNP wins Czech Dukovany reactor construction contract",
    "title_kr": "한국수력원자력, 체코 두코바니 원전 건설 계약 수주",
    "summary": "한국수력원자력이 체코 두코바니 신규 원전 2기 건설 계약을 따냈다.",
    "article_date": "2026-08-14",
}


def contracts_of(*articles, ranks=None):
    ranks = ranks or list(range(1, len(articles) + 1))
    return gate.build_evidence_contracts(
        [{"key": article["hash"], "rank": rank, "articles": [article]}
         for article, rank in zip(articles, ranks)],
        reference_date="2026-08-14",
    )


class EvidenceContractTests(unittest.TestCase):
    """근거 계약은 검증된 기사에서만 나온다 — LLM 출력은 근거가 아니다."""

    def test_contract_collects_only_article_side_fields(self):
        contract, = gate.build_evidence_contracts([{
            "key": "i1", "rank": 3,
            "articles": [{**DOOSAN, "why_important": "웨스팅하우스 견제 목적이다.",
                          "implication": "카자흐스탄 진출 발판이 된다."}],
        }], reference_date="2026-08-14")
        self.assertIn("doosan", contract.entities)
        self.assertEqual(contract.rank, 3)
        self.assertEqual(contract.article_hashes, ("h-doosan",))
        # 해석 필드가 근거로 들어오면 그 안의 이름이 사실이 되어 버린다.
        self.assertNotIn("westinghouse", contract.entities)
        self.assertNotIn("KZ", contract.countries)

    def test_tampered_manifest_contributes_nothing(self):
        manifest = gate.build_evidence_manifest(
            {"title": DOOSAN["title"], "article_hash": "h-doosan",
             "description": "Westinghouse also joined the 500 MW project."},
            article={"hash": "h-doosan", "title": DOOSAN["title"]})
        self.assertIn("westinghouse", manifest["entities"])
        forged = {**manifest, "entities": [*manifest["entities"], "rosatom"]}
        contract, = gate.build_evidence_contracts(
            [{"key": "i1", "articles": [DOOSAN], "manifests": [forged]}])
        self.assertNotIn("rosatom", contract.entities)
        self.assertNotIn("westinghouse", contract.entities)

    def test_valid_manifest_supplies_body_only_facts(self):
        source = {"title": DOOSAN["title"], "article_hash": "h-doosan",
                  "description": "Westinghouse also joined the project."}
        article = {"hash": "h-doosan", "title": DOOSAN["title"]}
        manifest = gate.build_evidence_manifest(source, article=article)
        contract, = gate.build_evidence_contracts(
            [{"key": "i1", "articles": [DOOSAN], "manifests": [manifest]}])
        self.assertIn("westinghouse", contract.entities)
        self.assertEqual(contract.manifest_count, 1)


class SpokenScriptAuditTests(unittest.TestCase):
    """오디오 대본은 마지막 변환까지 끝난 뒤 기사와 대조한다."""

    def setUp(self):
        self.contracts = contracts_of(DOOSAN, KHNP)

    def audit(self, line, **kwargs):
        return gate.audit_spoken_script(
            f"HOST: {line}", self.contracts, reference_date="2026-08-14", **kwargs)

    def problems(self, line):
        audit = self.audit(line)
        return {key: value for finding in audit.findings
                for key, value in finding.details.items() if key != "line"}

    def test_faithful_paragraph_passes(self):
        audit = self.audit("두산에너빌리티가 테라파워의 345MW급 SMR 기자재를 공급하기로 했습니다.")
        self.assertEqual(audit.action, "allow")
        self.assertEqual(audit.removed, ())

    def test_invented_institution_is_removed(self):
        self.assertIn("westinghouse",
                      self.problems("이번 계약에는 웨스팅하우스도 함께 참여해 기자재를 공급했습니다.")
                      ["entities"])

    def test_invented_country_is_removed(self):
        self.assertIn("KZ", self.problems("이번 수주로 카자흐스탄 시장에도 진출했습니다.")["countries"])

    def test_invented_quantity_is_removed(self):
        self.assertIn("500mw", self.problems("설비 용량은 500MW로 확정됐습니다.")["claims"])

    def test_spoken_unit_form_is_checked_too(self):
        """낭독 대본은 '500메가와트입니다'로 읽는다 — 기호로만 보면 못 잡는다."""
        self.assertIn("500mw", self.problems("설비 용량은 500메가와트입니다.")["claims"])

    def test_invented_date_is_removed(self):
        self.assertIn("2026-11-03",
                      self.problems("11월 3일에 최종 승인이 발표됐습니다.")["dates"])

    def test_invented_stage_is_removed(self):
        self.assertIn("construction",
                      self.problems("해당 부지는 이미 착공에 들어갔습니다.")["stages"])

    def test_cross_attributed_quantity_is_removed(self):
        """한 기사 이야기에 다른 기사의 수치를 끼워 넣는 경우."""
        audit = self.audit(
            "한국수력원자력이 수주한 체코 두코바니 건설 계약은 345MW 규모입니다.")
        self.assertEqual(audit.action, "sanitize")
        finding, = audit.findings
        self.assertEqual(finding.code, "script_claim_cross_attributed")
        self.assertEqual(finding.details["attributed_to"], "h-khnp")
        self.assertEqual(finding.details["claims"], ["345mw"])

    def test_system_frame_line_is_exempt(self):
        """오프닝의 날짜는 시스템이 붙인 것이지 기사 주장이 아니다."""
        frame = "8월 14일 금요일 Nuclens 전문가 브리핑입니다."
        self.assertEqual(self.audit(frame).action, "sanitize")
        self.assertEqual(self.audit(frame, exempt=[f"HOST: {frame}"]).action, "allow")

    def test_reorder_that_adds_a_claim_is_caught_on_the_final_text(self):
        """순서 재배치는 검증 뒤에 대본을 다시 쓴다 — 최종본을 봐야 잡는다."""
        verified = ("HOST: 두산에너빌리티가 테라파워에 345MW급 기자재를 공급합니다.\n"
                    "HOST: 한국수력원자력은 체코 두코바니 건설 계약을 수주했습니다.")
        self.assertTrue(gate.audit_spoken_script(
            verified, self.contracts, reference_date="2026-08-14").ok)
        reordered = ("HOST: 한국수력원자력은 체코 두코바니 건설 계약을 수주했습니다.\n"
                     "HOST: 두산에너빌리티는 로사톰과도 공급 계약을 체결했습니다.")
        audit = gate.audit_spoken_script(
            reordered, self.contracts, reference_date="2026-08-14")
        self.assertEqual(audit.action, "sanitize")
        self.assertIn("rosatom", audit.findings[0].details["entities"])

    def test_below_minimum_lines_rejects_instead_of_shipping_a_stub(self):
        audit = gate.audit_spoken_script(
            "HOST: 로사톰이 카자흐스탄에서 500MW 설비를 수주했습니다.",
            self.contracts, reference_date="2026-08-14", min_lines=3)
        self.assertEqual(audit.action, "reject")

    def test_no_contracts_leaves_the_script_untouched(self):
        """근거가 없다는 것은 거짓이라는 증거가 아니다 — 브리핑을 비우지 않는다."""
        audit = gate.audit_spoken_script("HOST: 아무 말.", [])
        self.assertEqual(audit.action, "allow")
        self.assertEqual(audit.findings[0].code, "script_evidence_missing")


class NarrativeDigestTests(unittest.TestCase):
    def test_same_inputs_give_the_same_digest(self):
        self.assertEqual(gate.evidence_digest(contracts_of(DOOSAN, KHNP)),
                         gate.evidence_digest(contracts_of(DOOSAN, KHNP)))

    def test_card_order_changes_the_digest(self):
        self.assertNotEqual(
            gate.evidence_digest(contracts_of(DOOSAN, KHNP, ranks=[1, 2])),
            gate.evidence_digest(contracts_of(DOOSAN, KHNP, ranks=[2, 1])))

    def test_article_set_changes_the_digest(self):
        self.assertNotEqual(gate.evidence_digest(contracts_of(DOOSAN, KHNP)),
                            gate.evidence_digest(contracts_of(DOOSAN)))

    def test_resanitized_article_text_changes_the_digest(self):
        """hash 는 그대로인데 검증으로 문장이 빠진 경우도 다른 재료다."""
        trimmed = {**DOOSAN, "summary": ""}
        self.assertNotEqual(gate.evidence_digest(contracts_of(DOOSAN)),
                            gate.evidence_digest(contracts_of(trimmed)))

    def test_gate_version_is_part_of_the_digest(self):
        contracts = contracts_of(DOOSAN)
        before = gate.evidence_digest(contracts)
        original = gate.NARRATIVE_GATE_VERSION
        gate.NARRATIVE_GATE_VERSION = original + 1
        try:
            self.assertNotEqual(gate.evidence_digest(contracts), before)
        finally:
            gate.NARRATIVE_GATE_VERSION = original

    def test_script_digest_ignores_formatting_only_changes(self):
        self.assertEqual(gate.script_digest("HOST: 가.\nHOST: 나."),
                         gate.script_digest("  HOST: 가.  \n\nHOST: 나.\n"))
        self.assertNotEqual(gate.script_digest("HOST: 가."),
                            gate.script_digest("HOST: 나."))


class SummaryTests(unittest.TestCase):
    def test_aggregate_counts_actions_fields_and_codes(self):
        results = [
            gate.GateResult({}, "allow"),
            gate.GateResult({}, "sanitize", ("why",),
                            (gate.Finding("unsupported", "sanitize", "why"),)),
            gate.GateResult({}, "quarantine", (),
                            (gate.Finding("mismatch", "quarantine", "headline"),)),
        ]
        summary = gate.summarize_findings(results)
        self.assertEqual(summary["checked"], 3)
        self.assertEqual(summary["allowed"], 1)
        self.assertEqual(summary["sanitized"], 1)
        self.assertEqual(summary["quarantined"], 1)
        self.assertEqual(summary["removed_fields"]["why"], 1)


if __name__ == "__main__":
    unittest.main()
