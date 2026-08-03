import json
import re
import sys
import tempfile
import unittest
from html import escape as html_escape
from itertools import combinations
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT = ROOT / "public" / "data"
try:
    _manifest = json.loads((DATA_ROOT / "manifest.json").read_text(encoding="utf-8"))
    DATA_DIR = DATA_ROOT / _manifest["base_path"]
except (OSError, KeyError, json.JSONDecodeError):
    DATA_DIR = DATA_ROOT

import build_data  # noqa: E402


class BrandAccessibilityTests(unittest.TestCase):
    def test_pretendard_variable_is_self_hosted(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        font = (
            ROOT
            / "public"
            / "fonts"
            / "pretendard"
            / "v1.3.9"
            / "PretendardVariable.woff2"
        )
        license_file = font.with_name("OFL.txt")

        self.assertIn('@font-face', css)
        self.assertIn('font-family: "Pretendard Variable";', css)
        self.assertIn('font-weight: 45 920;', css)
        self.assertIn('font-display: swap;', css)
        self.assertIn(
            'url("fonts/pretendard/v1.3.9/PretendardVariable.woff2")',
            css,
        )
        self.assertEqual(font.read_bytes()[:4], b"wOF2")
        self.assertIn(
            "SIL OPEN FONT LICENSE Version 1.1",
            license_file.read_text(encoding="utf-8"),
        )

    def test_muted_text_meets_wcag_aa_on_paper(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css))

        def luminance(hex_color: str) -> float:
            channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                for value in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        lighter, darker = sorted(
            (luminance(tokens["c-text-muted"]), luminance(tokens["c-bg"])),
            reverse=True,
        )
        contrast = (lighter + 0.05) / (darker + 0.05)
        self.assertGreaterEqual(contrast, 4.5)

    def test_rendered_text_has_12_5px_minimum(self):
        """12.5px 하한. 예외는 두지 않는다.

        ``font-size:`` 만 보면 ``font: 11px var(--ff-base)`` 축약형이 통째로
        빠져나간다 — 통합 시안이 정체성을 9~11px 모노 오버라인에 걸어둔 탓에
        이 구멍으로 40건 중 17건이 검사를 우회할 수 있었다. 오버라인은 크기가
        아니라 굵기·자간·색으로 구분한다.
        """
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css_sizes = [
            float(value)
            for value in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", css)
        ]
        # font 축약형의 크기 자리 — `font: [style] [weight] <size>[/line-height] <family>`
        shorthand_sizes = [
            float(value)
            for value in re.findall(r"\bfont:\s*[^;}]*?(\d+(?:\.\d+)?)px", css)
        ]
        inline_svg_sizes = [
            float(value)
            for value in re.findall(r'font-size="(\d+(?:\.\d+)?)"', app)
        ]
        too_small = [
            size for size in css_sizes + shorthand_sizes + inline_svg_sizes
            if size < 12.5
        ]
        self.assertEqual(too_small, [], f"12.5px 미만 글자 크기: {too_small}")
        self.assertRegex(css, r"small\s*{\s*font-size:\s*inherit;\s*}")

    def test_issue_cards_do_not_use_dashed_verification_border(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn(".issue-card.state-unverified { border-left-style: dashed; }", css)
        for status in build_data.VERIFICATION_LABELS:
            self.assertIn(f".verification-badge.v-{status}", css)

    def test_p1_design_tokens_replace_legacy_palette(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for token in (
            "c-primary", "c-secondary", "c-accent", "c-bg", "c-surface",
            "c-surface-sunken", "c-border", "c-text", "c-text-secondary",
            "c-text-muted", "c-positive", "c-warning", "c-critical",
            "c-verified", "c-unverified", "c-focus",
        ):
            self.assertRegex(css, rf"--{token}:\s*#[0-9a-f]{{6}}")
        for legacy in (
            "ink", "ink-soft", "muted", "paper", "panel", "line",
            "line-strong", "navy", "blue", "blue-soft", "sand", "orange",
            "green", "shadow",
        ):
            self.assertNotRegex(css, rf"--{legacy}\s*:")

    def test_focus_ring_covers_all_interactive_controls(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        selector = ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible"
        self.assertIn(selector, css)
        self.assertIn("outline: 2px solid var(--c-focus);", css)
        self.assertIn("box-shadow: var(--fo-ring);", css)

    def test_n_lettermark_is_restored_without_lens_geometry(self):
        favicon = (ROOT / "public" / "favicon.svg").read_text(encoding="utf-8")
        logo_mark = (ROOT / "public" / "logo-mark.svg").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="brand-mark" aria-hidden="true">N</span>', html)
        self.assertIn('aria-label="Nuclens"', favicon)
        self.assertIn("<path", favicon)
        self.assertNotIn('id="favicon-lens"', favicon)
        self.assertNotIn("<clipPath", favicon)
        self.assertIn('aria-label="Nuclens N"', logo_mark)
        self.assertIn("<path", logo_mark)
        self.assertNotIn("<clipPath", logo_mark)
        self.assertNotIn("nuclens-lens", logo_mark)

    def test_link_preview_image_exists_and_matches_the_deployed_mark(self):
        """공유 카드 이미지는 화면과 같은 심벌이어야 한다.

        브랜드 개편안의 Overlap Lens 는 7bc99b2 에서 N 마크로 되돌렸다.
        og:image 만 렌즈로 두면 공유 카드와 사이트가 다른 브랜드가 된다.
        """
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        image = ROOT / "public" / "og-image.png"
        self.assertTrue(image.exists(), "og-image.png 가 없다")
        self.assertGreater(image.stat().st_size, 1000)
        self.assertTrue(image.read_bytes().startswith(b"\x89PNG"), "PNG 헤더가 아니다")
        self.assertIn('property="og:image"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertIn('property="og:image:width" content="1200"', html)
        # 손으로 만든 바이너리가 아니라 재현 가능한 산출물이어야 한다
        self.assertTrue((ROOT / "tools" / "make_og_image.py").exists())
        generator = (ROOT / "tools" / "make_og_image.py").read_text(encoding="utf-8")
        self.assertNotIn("LENS_R", generator, "og 이미지가 되돌린 렌즈 심벌을 쓰고 있다")


class SelectionReasonTests(unittest.TestCase):
    def test_breakdown_becomes_two_human_reasons(self):
        delivery = {
            "score": 22.5,
            "breakdown": {
                "importance": 10,
                "event:policy_decision": 6,
                "korea_relevance": 3.6,
                "policy_materiality": 3,
            },
        }
        self.assertEqual(
            build_data.selection_reasons(delivery),
            ["정책 결정", "국내 관련성 높음"],
        )

    def test_time_decay_is_never_exposed(self):
        reasons = build_data.selection_reasons({"score": 5, "breakdown": {"time_decay": -1}})
        self.assertEqual(reasons, ["브리핑 우선순위"])

    def test_official_and_specialist_labels_are_distinct(self):
        delivery = {"score": 5, "breakdown": {"source_tier1": 4}}
        self.assertEqual(
            build_data.selection_reasons(delivery, {"evidence_role": "primary"}),
            ["공식 원문"],
        )
        self.assertEqual(
            build_data.selection_reasons(
                delivery, {"evidence_role": "independent", "source_type": "specialist_media"}
            ),
            ["전문 매체"],
        )


class DataQualityGateTests(unittest.TestCase):
    @staticmethod
    def _record(hash_value="h1", url="https://example.com/a", title="원전 계획 발표"):
        return {
            "hash": hash_value,
            "url": url,
            "title": title,
            "publisher": "테스트 매체",
            "source_tier": 3,
            "importance": "nice_to_know",
            "summary": "정부가 신규 원전 계획을 발표했다.",
            "implication": "",
            "why_important": "",
        }

    def test_duplicate_url_fails_build_gate(self):
        with self.assertRaisesRegex(ValueError, "duplicate_url"):
            build_data.validate_archive_records([
                self._record(), self._record("h2", title="다른 제목")
            ])

    def test_incomplete_summary_fails_build_gate(self):
        record = self._record()
        record["summary"] = "정부가 신규 원전 계획을 발표"
        with self.assertRaisesRegex(ValueError, "summary:incomplete"):
            build_data.validate_archive_records([record])


class ChangeLineTests(unittest.TestCase):
    """변화 문장이 같은 사실을 두 번 말하거나 문단으로 번지지 않는지."""

    @staticmethod
    def _member(summary, briefing_date="2026-07-30", article_date="2026-07-30", hash_value="h1"):
        return {
            "hash": hash_value,
            "briefing_date": briefing_date,
            "article_date": article_date,
            "title_kr": summary,
            "summary": summary,
        }

    def test_restated_fact_does_not_become_a_change_arrow(self):
        previous = self._member(
            "미국 에너지부(DOE)가 원자력 라이프사이클 혁신 캠퍼스 유치를 위한 잠재적 후보지로"
            " 유타, 테네시, 오클라호마, 루이지애나, 아이다호 5개 주를 선정했습니다.",
            briefing_date="2026-07-29",
            article_date="2026-07-29",
            hash_value="h0",
        )
        current = self._member(
            "미국 에너지부(DOE)가 원자력 수명 주기 혁신 캠퍼스 유치 최종 후보지로"
            " 아이다호, 루이지애나, 오클라호마, 테네시, 유타 5개 주를 선정했다.",
        )
        change = build_data.latest_change_line([current], [previous])
        self.assertNotIn("→", change)
        self.assertLessEqual(len(change), build_data.CHANGE_LINE_LIMIT)

    def test_card_change_block_is_empty_when_it_repeats_the_summary(self):
        summary = "독일이 2040년대 유럽 최초의 상업용 핵융합 발전소 운영을 목표로 3개의 국가 허브 계획을 발표했다."
        current = self._member(summary)
        self.assertEqual(build_data.change_line_for_card([current], [], summary), "")

    def test_card_change_block_survives_when_the_state_actually_moved(self):
        previous = self._member(
            "다뉴브강 수위가 역대 최저치를 기록했습니다.",
            briefing_date="2026-07-29",
            article_date="2026-07-29",
            hash_value="h0",
        )
        summary = "헝가리 총리가 다뉴브강의 낮은 수위로 원자력 발전소 가동이 중단될 수 있다고 경고했다."
        current = self._member(summary)
        self.assertIn("→", build_data.change_line_for_card([current], [previous], summary))

    def test_genuinely_new_fact_keeps_the_change_arrow(self):
        previous = self._member(
            "원안위가 신한울 3호기 건설 허가 심사를 시작했다.",
            briefing_date="2026-07-29",
            article_date="2026-07-29",
            hash_value="h0",
        )
        current = self._member("한수원이 체코 두코바니 신규 원전 본계약에 서명했다.")
        self.assertIn("→", build_data.latest_change_line([current], [previous]))


class DailyHeadlineTests(unittest.TestCase):
    def test_headline_never_exceeds_the_hero_limit(self):
        row = {
            "status": "ongoing",
            "latest_change": (
                "미국 에너지부가 후보지 5곳을 선정했다 → 미국 에너지부가 원자력 라이프사이클 혁신"
                " 캠퍼스 유치를 위한 잠재적 후보지로 유타, 테네시, 오클라호마, 루이지애나,"
                " 아이다호 5개 주를 선정했으며 후속 절차를 예고했습니다"
            ),
            "title": "미국 에너지부, 혁신 캠퍼스 후보지 5개 주 선정",
            "summary": "",
        }
        headline = build_data.daily_headline([row])
        self.assertLessEqual(len(headline), build_data.HEADLINE_LIMIT)
        self.assertNotIn("→", headline)

    def test_headline_follows_the_ranking_not_the_first_tracked_issue(self):
        """추적 이슈가 있다고 순위를 건너뛰면 안 된다.

        실측(2026-08-02 라이브): 옛 코드가 '화살표 있는 첫 이슈'를 집는 바람에
        하위권 헝가리 갈수기 뉴스가 1위였던 한국 우라늄 농축 이슈를 밀어냈다.
        """
        rows = [
            {"status": "new", "latest_change": "", "previous_article_count": 0,
             "title": "사우디에 이어 한국도 미국에 우라늄 농축권한 요청", "summary": ""},
            {"status": "ongoing", "previous_article_count": 2,
             "latest_change": "가동 우려 → 헝가리 총리가 원전 가동을 중단할 것이라고 발표했습니다.",
             "title": "헝가리 총리, 팍스 원전 가동 중단 발표", "summary": ""},
        ]
        lead = build_data.daily_lead(rows)
        self.assertIn("우라늄", lead["headline"])
        self.assertEqual(lead["kind"], "issue")

    def test_headline_uses_the_title_not_the_generated_change_sentence(self):
        """제목은 개조식인데 변화 문장은 기사체다 — h1 에는 제목을 쓴다."""
        rows = [{
            "status": "ongoing", "previous_article_count": 3,
            "latest_change": "심사 착수 → 원안위가 신한울 3호기 건설 허가를 의결했습니다.",
            "title": "원안위, 신한울 3호기 건설 허가 의결", "summary": "",
        }]
        lead = build_data.daily_lead(rows)
        self.assertEqual(lead["headline"], "원안위, 신한울 3호기 건설 허가 의결")
        self.assertEqual(lead["kind"], "change")  # 이어지는 이슈라 '달라졌는가'
        self.assertNotIn("의결했습니다", lead["headline"])

    def test_headline_skips_an_issue_the_previous_day_already_led_with(self):
        """이틀 연속 같은 사건을 '무엇이 달라졌는가'로 내걸면 거짓말이 된다."""
        rows = [
            {"status": "ongoing", "previous_article_count": 2,
             "title": "헝가리 총리, 팍스 원전 일요일 가동 중단 발표", "summary": ""},
            {"status": "new", "previous_article_count": 0,
             "title": "한수원, 영덕군과 신규 원전 건설 협력 합의", "summary": ""},
        ]
        yesterday = "헝가리 총리, 다뉴브강 수위 저하로 팍스 원전 가동 중단 경고"
        self.assertIn("영덕", build_data.daily_lead(rows, yesterday)["headline"])
        # 전날 정보가 없으면 순위를 그대로 따른다
        self.assertIn("헝가리", build_data.daily_lead(rows)["headline"])

    def test_all_issues_repeating_still_produces_a_headline(self):
        rows = [{"status": "ongoing", "previous_article_count": 1,
                 "title": "헝가리 총리, 팍스 원전 가동 중단 발표", "summary": ""}]
        lead = build_data.daily_lead(rows, "헝가리 총리, 팍스 원전 가동 중단 경고")
        self.assertIn("헝가리", lead["headline"])  # 억지로 비우지 않는다

    def test_headline_without_a_change_is_not_labelled_as_one(self):
        rows = [{"status": "new", "latest_change": "", "title": "한수원, 체코 본계약 서명", "summary": ""}]
        lead = build_data.daily_lead(rows)
        self.assertEqual(lead["kind"], "issue")
        self.assertEqual(lead["headline"], "한수원, 체코 본계약 서명")

    def test_empty_briefing_has_a_stable_headline(self):
        self.assertEqual(
            build_data.daily_headline([]), "오늘 새로 연결된 원자력 이슈가 없습니다"
        )


class VerificationStateTests(unittest.TestCase):
    """P3 검증 모델 — 재인용은 독립 출처로 세지 않는다."""

    @staticmethod
    def _article(hash_value, publisher, evidence_role="independent", source_type="general_media"):
        return {
            "hash": hash_value,
            "publisher": publisher,
            "domain": "news.google.co.kr",
            "evidence_role": evidence_role,
            "source_type": source_type,
        }

    def test_official_document_wins(self):
        state = build_data.verification_state([
            self._article("h1", "IAEA", evidence_role="primary", source_type="official"),
            self._article("h2", "로이터"),
        ], checked_at="2026-08-01T09:00:00+09:00")
        self.assertEqual(state["status"], "official")
        self.assertEqual(state["label"], "공식 확인")
        self.assertEqual(state["official_source_count"], 1)
        self.assertEqual(state["checked_at"], "2026-08-01T09:00:00+09:00")

    def test_two_independent_publishers_are_corroborated(self):
        state = build_data.verification_state([
            self._article("h1", "로이터"), self._article("h2", "연합뉴스"),
        ])
        self.assertEqual(state["status"], "corroborated")
        self.assertEqual(state["independent_source_count"], 2)

    def test_same_publisher_twice_is_still_one_source(self):
        state = build_data.verification_state([
            self._article("h1", "로이터"), self._article("h2", " 로이터 "),
        ])
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["independent_source_count"], 1)

    def test_distributed_claims_only_stay_unverified(self):
        state = build_data.verification_state([
            self._article("h1", "PR뉴스와이어", evidence_role="distributed_claim", source_type="press_release"),
            self._article("h2", "글로브뉴스와이어", evidence_role="distributed_claim", source_type="press_release"),
        ])
        self.assertEqual(state["status"], "unverified")
        self.assertEqual(state["independent_source_count"], 0)
        self.assertEqual(state["source_count"], 2)

    def test_no_evidence_does_not_invent_a_status(self):
        state = build_data.verification_state([])
        self.assertEqual(state["status"], "unverified")
        self.assertEqual(state["source_count"], 0)


class RegionClassificationTests(unittest.TestCase):
    def test_google_korea_domain_does_not_turn_us_story_domestic(self):
        record = {
            "title_kr": "미국 원전의 80년 장기운전 및 민간 금융 동향",
            "summary": "미국 원전 정책을 분석한다.",
            "domain": "news.google.co.kr",
            "section": "international",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertIn("US", countries)
        self.assertEqual(build_data.region_of(record, countries), "해외")

    def test_korean_project_with_foreign_counterpart_stays_domestic(self):
        record = {
            "title_kr": "한국 SMR 선박, 미국선급협회 기본승인 획득",
            "domain": "world-nuclear-news.org",
            "section": "smr",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertIn("KR", countries)
        self.assertEqual(build_data.region_of(record, countries), "국내")

    def test_legacy_eu_bucket_is_refined_to_actual_country(self):
        record = {
            "title_kr": "독일, 국가 핵융합 허브와 연구개발 지원 계획 발표",
            "summary": "독일 정부가 핵융합 연구개발 지원 계획을 발표했다.",
            "countries": ["EU_ETC"],
            "section": "international",
        }
        countries, source = build_data.infer_countries(record)
        self.assertEqual(countries, ["DE"])
        self.assertEqual(source, "legacy-refined-v2")

    def test_serbia_is_a_country_not_eu(self):
        record = {
            "title_kr": "세르비아 정부, 신규 원자력 프로그램 검토",
            "section": "international",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertEqual(countries, ["RS"])

    def test_us_agency_token_uses_word_boundary(self):
        countries, _ = build_data.infer_countries({
            "title_kr": "NRC, 오이스터크릭 인허가 종료계획 승인",
            "section": "international",
        })
        self.assertEqual(countries, ["US"])

    def test_eu_and_geographic_europe_are_distinct(self):
        eu, _ = build_data.infer_countries({
            "title_kr": "EU 집행위원회, 원자력 공동투자 기준 발표",
            "countries": ["EU"],
        })
        europe, source = build_data.infer_countries({
            "title_kr": "유럽 강 수위 저하로 전력 생산 차질",
            "countries": ["EU"],
        })
        self.assertEqual(eu, ["EU"])
        self.assertEqual(europe, ["EUROPE"])
        self.assertEqual(source, "eu-refined-v2")

    def test_country_trend_counts_distinct_issues_not_articles(self):
        issues = [
            {"members": [
                {"article_date": "2026-07-20", "countries": ["DE"]},
                {"article_date": "2026-07-21", "countries": ["DE"]},
            ]},
            {"members": [
                {"article_date": "2026-07-22", "countries": ["DE", "FR"]},
            ]},
            {"members": [
                {"article_date": "2026-06-01", "countries": ["FR"]},
            ]},
        ]
        counts = build_data.count_country_issues(issues, "2026-07-01")
        self.assertEqual(counts, {"DE": 2, "FR": 1})


class IssueSimilarityTests(unittest.TestCase):
    def test_paraphrased_12th_plan_articles_are_one_issue(self):
        # 생성 데이터의 특정 해시에 결합하지 않고, 같은 사건의 두 표현을 고정
        # 회귀 표본으로 둔다. 품질 마이그레이션으로 중복 기사가 삭제돼도 유효하다.
        left = {
            "title_kr": "12차 전기본, 원전 반영 여부 두고 정부 부처 간 정책 혼선",
            "summary": "12차 전력수급기본계획의 원전 반영 여부를 두고 정부 내 입장이 엇갈렸습니다.",
            "tags": ["#12차전기본", "#원전정책", "#정부정책"],
            "countries": ["KR"],
        }
        right = {
            "title_kr": "12차 전력수급기본계획, 원전 반영 여부 두고 정부 부처 간 혼선",
            "summary": "12차 전력수급기본계획 수립 과정에서 원전 반영을 두고 부처 간 이견이 보도됐습니다.",
            "tags": ["#12차전기본", "#에너지정책", "#정부이견"],
            "countries": ["KR"],
        }
        matched, _, diagnostics = build_data.issue_similarity(left, right)
        self.assertTrue(matched)
        self.assertEqual(diagnostics["tag_shared"], 1)

    def test_prepare_insights_drops_evidence_missing_from_public_news(self):
        insights = {
            "items": [{
                "keyword": "원전 정책",
                "direction": "원전 정책 논의가 이어졌습니다.",
                "evidence": [{"hash": "kept"}, {"hash": "removed"}],
            }],
        }
        news = [{
            "hash": "kept", "region": "국내", "countries": ["KR"],
            "topics": ["정책"], "publisher": "산업통상자원부", "domain": "motie.go.kr",
        }]
        prepared = build_data.prepare_insights(insights, news)
        self.assertEqual([row["hash"] for row in prepared["items"][0]["evidence"]], ["kept"])
        self.assertEqual(prepared["items"][0]["region_scope"], "국내")

    def test_unrelated_safety_events_stay_separate(self):
        left = {
            "title_kr": "다뉴브강 저수위로 헝가리 원전 가동 중단",
            "summary": "강 수위 저하로 냉각수 확보에 차질이 발생했다.",
            "tags": ["#원전안전", "#기후변화"],
        }
        right = {
            "title_kr": "미국 핵연구시설 글러브박스 화재 발생",
            "summary": "플루토늄 취급 시설에서 화재가 발생했다.",
            "tags": ["#원전안전", "#화재"],
        }
        matched, _, _ = build_data.issue_similarity(left, right)
        self.assertFalse(matched)

    def test_same_regulator_does_not_mean_same_issue(self):
        left = {
            "title_kr": "원자력안전위원회, 입법 및 행정예고 진행",
            "summary": "원안위가 입법예고 사항을 공지했다.",
            "tags": ["#원안위", "#입법예고"],
        }
        right = {
            "title_kr": "한울 4호기, 원자력안전위원회 정기검사 중 임계 허용",
            "summary": "원안위가 한울 4호기의 임계를 허용했다.",
            "tags": ["#원안위", "#한울4호기"],
        }
        matched, _, _ = build_data.issue_similarity(left, right)
        self.assertFalse(matched)

    def test_cached_embeddings_connect_supported_followup(self):
        left = {
            "hash": "left",
            "title_kr": "월성 계속운전 지원 체계 검토 착수",
            "summary": "지역 지원 제도를 검토한다.",
            "tags": ["#계속운전"],
            "topics": ["restart_lto"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "지역 상생 재원 논의 본격화",
            "summary": "장기운전과 연계한 재원을 논의한다.",
            "tags": ["#계속운전"],
            "topics": ["restart_lto"],
            "countries": ["KR"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [0.99, 0.01]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertTrue(matched)
        self.assertEqual(diagnostics["method"], "embedding")

    def test_country_conflict_blocks_embedding_merge(self):
        left = {
            "hash": "left",
            "title_kr": "한국 신규 원전 정책 발표",
            "tags": ["#원전정책"],
            "topics": ["policy_general"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "미국 신규 원전 지원책 공개",
            "tags": ["#원전정책"],
            "topics": ["policy_general"],
            "countries": ["US"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [1.0, 0.0]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertFalse(matched)
        self.assertIn("country_conflict", diagnostics["blocked_by"])

    def test_facility_conflict_blocks_embedding_merge(self):
        left = {
            "hash": "left",
            "title_kr": "월성 2호기 정기검사 진행",
            "tags": ["#정기검사"],
            "topics": ["regulation"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "한울 4호기 정기검사 진행",
            "tags": ["#정기검사"],
            "topics": ["regulation"],
            "countries": ["KR"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [1.0, 0.0]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertFalse(matched)
        self.assertIn("facility_conflict", diagnostics["blocked_by"])

    def test_recent_member_bridge_keeps_evolving_issue_together(self):
        articles = [
            {
                "hash": "a", "briefing_date": "2026-07-01", "article_date": "2026-07-01",
                "title_kr": "월성2호기 계속운전 지역지원 체계 검토",
                "tags": ["#월성2호기", "#지역지원"], "countries": ["KR"],
            },
            {
                "hash": "b", "briefing_date": "2026-07-02", "article_date": "2026-07-02",
                "title_kr": "월성2호기 지역지원 제도와 주민수용성 논의",
                "tags": ["#월성2호기", "#지역지원", "#주민수용성"], "countries": ["KR"],
            },
            {
                "hash": "c", "briefing_date": "2026-07-03", "article_date": "2026-07-03",
                "title_kr": "월성2호기 주민수용성 확보 위한 상생기금 협의",
                "tags": ["#월성2호기", "#주민수용성"], "countries": ["KR"],
            },
        ]
        matched_ac, _, _ = build_data.issue_similarity(articles[0], articles[2])
        self.assertFalse(matched_ac)
        issues = build_data.cluster_selected_articles(articles)
        self.assertEqual(len(issues), 1)
        self.assertEqual([member["hash"] for member in issues[0]["members"]], ["a", "b", "c"])

    def _nrc_rulemaking_articles(self):
        """일반 제목 1건 + 서로 다른 규정 2건. 라이브 사고의 최소 재현."""
        return [
            {
                "hash": "a", "briefing_date": "2026-08-01", "article_date": "2026-08-01",
                "title_kr": "미국 원자력규제위원회, 공청회서 신규 규정 제안 내용 공개",
                "tags": ["#NRC", "#규정제안"], "countries": ["US"],
            },
            {
                "hash": "b", "briefing_date": "2026-08-02", "article_date": "2026-08-02",
                "title_kr": "미국 NRC, 환경영향평가 규정 개정 제안 규칙 공청회 개최",
                "tags": ["#NRC", "#환경영향평가"], "countries": ["US"],
            },
            {
                "hash": "c", "briefing_date": "2026-08-03", "article_date": "2026-08-03",
                "title_kr": "미국 NRC, 방사성 물질 운송 규정 현대화 제안 및 의견 수렴",
                "tags": ["#NRC", "#방사성물질운송"], "countries": ["US"],
            },
        ]

    def test_rejected_pair_vetoes_the_whole_cluster_not_just_one_reference(self):
        """쌍 판정은 전이적이지 않다 — A=B, A=C 를 승인해도 B≠C 면 갈라야 한다.

        2026-08-03 라이브(issue-6b93ed7e22e9bb4b)에서 서로 다른 NRC 규정 제정
        2건이 일반적 제목을 경유해 한 이슈로 합쳐졌다. LLM 은 그 둘을 "서로 다른
        규정 제안"으로 이미 기각한 상태였는데도, 멤버 하나만 맞으면 합류시키는
        탐욕적 매칭이 기각을 우회했다.
        """
        articles = self._nrc_rulemaking_articles()
        pair = build_data._pair_id
        overrides = {
            "approved": set(), "rejected": set(),
            "llm_approved": {pair("a", "b"), pair("a", "c")},
            "llm_rejected": {pair("b", "c")},
        }
        issues = build_data.cluster_selected_articles(articles, None, None, overrides, [])
        members = {issue["issue_id"]: [m["hash"] for m in issue["members"]] for issue in issues}
        same_cluster = [hashes for hashes in members.values() if "b" in hashes and "c" in hashes]
        self.assertEqual(
            same_cluster, [],
            f"'다른 사건'으로 기각된 b·c 가 한 묶음에 있다: {members}")

    def test_veto_does_not_fire_without_a_rejection(self):
        """거부권이 없을 땐 기존 병합 동작이 그대로여야 한다(과교정 방지)."""
        articles = self._nrc_rulemaking_articles()
        pair = build_data._pair_id
        overrides = {
            "approved": set(), "rejected": set(),
            "llm_approved": {pair("a", "b"), pair("a", "c")},
            "llm_rejected": set(),
        }
        issues = build_data.cluster_selected_articles(articles, None, None, overrides, [])
        self.assertEqual(len(issues), 1, "기각이 없는데 묶음이 갈렸다")


class GeneratedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data_dir = DATA_DIR
        cls.news = json.loads((data_dir / "news.json").read_text(encoding="utf-8"))
        cls.briefings = json.loads((data_dir / "briefings.json").read_text(encoding="utf-8"))
        cls.meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        cls.issue_audit = json.loads((data_dir / "issue_audit.json").read_text(encoding="utf-8"))
        cls.insights = json.loads((data_dir / "insights.json").read_text(encoding="utf-8"))
        cls.issue_catalog = json.loads((data_dir / "issues.json").read_text(encoding="utf-8"))
        cls.publications = json.loads((data_dir / "publications.json").read_text(encoding="utf-8"))

    def test_every_delivered_article_is_represented_once_in_its_briefing(self):
        for briefing in self.briefings:
            expected = sum(1 for article in self.news if article.get("briefing_date") == briefing["date"])
            current = sum(issue["current_article_count"] for issue in briefing["issues"])
            self.assertEqual(briefing["article_count"], expected)
            self.assertEqual(current, expected)

    def test_global_issue_catalog_contains_each_delivered_article_once(self):
        delivered_hashes = [article["hash"] for article in self.news if article.get("briefing_date")]
        catalog_hashes = [
            article["hash"]
            for issue in self.issue_catalog
            for article in issue["related_articles"]
        ]
        self.assertEqual(len(self.issue_catalog), self.meta["issue_catalog_total"])
        self.assertEqual(len({issue["issue_id"] for issue in self.issue_catalog}), len(self.issue_catalog))
        self.assertCountEqual(catalog_hashes, delivered_hashes)
        self.assertEqual(len(catalog_hashes), len(set(catalog_hashes)))

    def test_every_issue_card_carries_a_verification_state(self):
        for rows in [issue for briefing in self.briefings for issue in briefing["issues"]], self.issue_catalog:
            for issue in rows:
                state = issue["verification"]
                self.assertIn(state["status"], build_data.VERIFICATION_LABELS)
                self.assertEqual(state["label"], build_data.VERIFICATION_LABELS[state["status"]])
                self.assertTrue(state["checked_at"])
                self.assertLessEqual(
                    state["official_source_count"] + state["independent_source_count"],
                    state["source_count"],
                )
                if state["status"] == "corroborated":
                    self.assertGreaterEqual(state["independent_source_count"], 2)
                if state["status"] == "unverified":
                    self.assertEqual(state["official_source_count"], 0)
                    self.assertEqual(state["independent_source_count"], 0)

    def test_headlines_and_change_lines_stay_within_limits(self):
        for briefing in self.briefings:
            self.assertLessEqual(len(briefing["headline"]), build_data.HEADLINE_LIMIT)
            self.assertNotIn("→", briefing["headline"])
            for issue in briefing["issues"]:
                self.assertLessEqual(len(issue["latest_change"]), build_data.CHANGE_LINE_LIMIT + 1)

    def test_latest_briefing_keeps_previous_day_articles(self):
        latest = self.briefings[0]
        articles = [
            article
            for issue in latest["issues"]
            for article in issue["related_articles"]
            if article.get("briefing_date") == latest["date"]
        ]
        self.assertTrue(any(article["article_date"] < latest["date"] for article in articles))

    def test_selection_reasons_are_short(self):
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertLessEqual(len(issue["selection_reasons"]), 2)

    def test_local_taxonomy_enables_prototype_trend(self):
        self.assertEqual(self.meta["taxonomy_version"], "topic-v1-country-scope-v2")
        self.assertGreaterEqual(self.meta["topic_coverage"], 0.9)
        self.assertGreaterEqual(self.meta["country_coverage"], 0.9)
        self.assertTrue(self.meta["trend_ready"])

    def test_issue_rows_expose_topics_for_filtering(self):
        classified = [
            issue
            for briefing in self.briefings
            for issue in briefing["issues"]
            if issue.get("topics")
        ]
        self.assertGreater(len(classified), 0)

    def test_compact_flow_and_search_controls_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="globalSearchOpen"', html)
        self.assertIn('id="globalSearchDialog"', html)
        self.assertIn('id="topicSel"', html)
        # 흐름 카드의 제목은 이슈(사건) 제목이고, 클릭하면 상세로 간다.
        # 키워드 단위 해석 문장을 쓰면 한 사건이 키워드 수만큼 반복된다.
        self.assertIn('data-issue-id="${esc(item.issue_id)}"', script)
        self.assertIn("weekly_movers", script)
        self.assertIn('class="flow-keyword"', script)
        self.assertIn('class="event-block"', script)
        self.assertIn('event.key === "/"', script)

    def test_flow_takeaways_are_complete_sentences(self):
        takeaways = [item.get("takeaway", "") for item in self.insights.get("items", [])]
        self.assertTrue(takeaways)
        self.assertTrue(all(text.endswith((".", "!", "?")) for text in takeaways))
        self.assertTrue(all(not text.endswith("…") for text in takeaways))

    def test_featured_flows_cover_domestic_and_overseas_without_blind_top_three(self):
        featured = self.insights.get("featured_items", [])
        self.assertEqual(self.insights["selection_method"], "signal-region-evidence-diversity-v2-deduped")
        self.assertEqual(len(featured), 3)
        regions = {region for item in featured for region in item.get("evidence_regions", [])}
        self.assertIn("국내", regions)
        self.assertIn("해외", regions)
        self.assertTrue(all(item.get("region_scope") for item in featured))
        self.assertTrue(all(
            all(evidence.get("region") in {"국내", "해외"} for evidence in item.get("evidence", []))
            for item in featured
        ))

    def test_render_smoke_selectors_exist_in_the_page(self):
        """스모크가 없는 id 를 보면 조용히 실패한다 — 라이브가 멀쩡해도 CI 가 빨개진다.

        실제 사고: `#metaLine` 이 `#headerStatus` 로 개명됐는데 스모크만 옛 id 로
        남았다. `page.textContent(...).catch(() => "")` 가 없는 요소를 빈 문자열로
        삼키고 그 다음 정규식이 실패해, 2026-08 daily-brief 가 매일 실패했다.
        상시 빨간 워크플로는 진짜 장애와 구분되지 않는다.
        """
        smoke = (ROOT / "tests" / "render_smoke.mjs").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        page_ids = set(re.findall(r'id="([^"]+)"', html))
        referenced = set(re.findall(r'["\'`]#([A-Za-z][\w-]*)["\'\s\[]', smoke))
        missing = sorted(referenced - page_ids)
        self.assertEqual(missing, [], f"render_smoke.mjs 가 없는 id 를 참조한다: {missing}")

    def test_explanatory_copy_is_removed(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        combined = html + script
        self.assertNotIn("뉴스를 기사보다 이슈 단위로 읽습니다", combined)
        self.assertNotIn("발행일이 아니라 이 브리핑에서 다룬 사안을 기준으로 묶었습니다", combined)

    def test_search_scope_and_balanced_region_stats_are_explicit(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("기관, 호기, 주제로 검색", html)
        # 수집 규모·신선도 지표는 상태 스트립 한 곳에서만 말한다
        # (히어로의 '데이터 상태' 정의 목록은 같은 값을 되풀이해 제거했다).
        self.assertIn("마지막 수집", script)
        self.assertIn("1차 출처", script)

    def test_p1_copy_overlines_and_card_hierarchy(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        combined = html + script

        for phrase in (
            "프로토타입", "DAILY ISSUE BRIEF", "ISSUE TRACKER", "ISSUE ARCHIVE",
            "WEEKLY SIGNALS", "TOP 3", "ISSUE TIMELINE", "필터 초기화",
        ):
            self.assertNotIn(phrase, combined)
        overlines = {
            label.strip()
            for label in re.findall(r'<p class="eyebrow(?: dark)?">([A-Z ]+)', html)
        }
        self.assertEqual(overlines, {"TODAY", "THIS WEEK"})
        self.assertIn("원자력 정책·산업 이슈 트래커", html)
        self.assertIn("이번 주 이어지는 흐름", html)
        self.assertIn("Nuclens는 제목·요약·출처 링크만 제공합니다.", html)
        self.assertIn("분석 기간 ${dateLabel(start)}–${dateLabel(end)}", script)
        self.assertIn("중복 제거 적용 · 원본 ${articleCount}건 → 연결 이슈 ${issueCount}개", script)

        issue_card = script.split("function issueCard", 1)[1].split("function renderBriefingSidebar", 1)[0]
        self.assertIn("verificationBadge(issue)", issue_card)
        # 근거 줄은 '타임라인 N' 버튼과 같은 숫자를 반복해 제거했다. 출처 구성은
        # 상세에만 남는다.
        self.assertNotIn("issueEvidenceText(issue)", issue_card)
        self.assertIn('class="issue-change"', issue_card)
        self.assertNotIn('class="issue-meaning"', issue_card)
        self.assertNotIn('class="topic-row"', issue_card)
        self.assertNotIn('class="reason-row"', issue_card)
        for tone in ("importance-high", "importance-updated", "importance-standard"):
            self.assertIn(f".issue-card.{tone}", style)

    def test_issue_detail_dialog_and_url_state_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="issueDialog"', html)
        self.assertIn('id="issueDialogContent"', html)
        self.assertIn("function openIssueDialog", script)
        self.assertIn("const ISSUE_ROUTE", script)
        self.assertIn("function issuePath", script)
        self.assertIn('return `/issue/${encodeURIComponent(issueId)}`;', script)
        self.assertNotIn('params.set("issue", state.issueId)', script)
        self.assertIn('class="issue-detail-button"', script)
        self.assertIn('id="issueDialogTitle" tabindex="-1"', script)
        self.assertIn('class="dialog-meaning"', script)

    def test_p3_issue_pages_have_unique_open_graph_metadata(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        root_html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        issues = json.loads((DATA_DIR / "issues.json").read_text(encoding="utf-8"))
        issue_root = ROOT / "public" / "issue"
        pages = list(issue_root.glob("*/index.html"))
        self.assertEqual(len(pages), len(issues))
        self.assertIn('href="/style.css"', root_html)
        self.assertIn('src="/app.js"', root_html)
        self.assertIn('dataBase: "/data"', script)
        self.assertIn('fetch(`/data/${name}`', script)
        self.assertIn("new URL(issuePath(issueId), location.origin)", script)
        for issue in issues:
            page = issue_root / issue["issue_id"] / "index.html"
            self.assertTrue(page.exists(), issue["issue_id"])
            page_html = page.read_text(encoding="utf-8")
            issue_url = f'https://nuclens.pages.dev/issue/{issue["issue_id"]}'
            self.assertIn(f'<link rel="canonical" href="{issue_url}">', page_html)
            self.assertIn(f'<meta property="og:url" content="{issue_url}">', page_html)
            self.assertIn('<meta property="og:type" content="article">', page_html)
            self.assertIn(f'<title>{html_escape(issue["title"])} | Nuclens</title>', page_html)
            self.assertIn('type="application/ld+json"', page_html)

    def test_global_issue_search_view_and_url_filters_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        for element_id in (
            "view-search", "globalSearch", "archiveRegion", "archiveTopic",
            "archiveVerification", "archiveIssueList", "archiveMore",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('data-view="search"', html)
        self.assertIn("function renderArchiveSearch", script)
        self.assertIn('params.set("q", state.archiveQuery)', script)
        self.assertIn('loadJSON("issues.json")', script)

    def test_manifest_loading_and_operation_status_ui_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="systemStatus"', html)
        self.assertIn("async function initializeDataBase", script)
        self.assertIn('loadRootJSON("manifest.json", true)', script)
        manifest = json.loads((DATA_ROOT / "manifest.json").read_text(encoding="utf-8"))
        status = json.loads((DATA_ROOT / "status.json").read_text(encoding="utf-8"))
        meta = json.loads((DATA_ROOT / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["generation_id"])
        self.assertEqual(manifest["generation_id"], status["generation_id"])
        self.assertEqual(manifest["generation_id"], meta["generation_id"])

    def test_initial_data_connection_recovers_without_manual_reload(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("initRetryCount <= 5", script)
        self.assertIn("window.setTimeout(init, delay)", script)
        self.assertIn('id="retryInit"', script)
        self.assertIn('window.addEventListener("online"', script)
        self.assertIn("if (appReady || initLoading) return", script)
        self.assertIn("function renderSystemStatus", script)
        self.assertIn("window.setInterval(checkForNewGeneration, 60000)", script)

    def test_ongoing_issues_expose_tracking_metadata(self):
        briefings = json.loads((DATA_DIR / "briefings.json").read_text(encoding="utf-8"))
        ongoing = [issue for briefing in briefings for issue in briefing["issues"] if issue["status"] == "ongoing"]
        self.assertTrue(ongoing)
        self.assertTrue(all(issue["tracked_briefings"] >= 2 for issue in ongoing))
        self.assertTrue(all(issue["previous_article_count"] >= 1 for issue in ongoing))
        # 변화 문장은 요약을 되풀이할 때 비워진다. 남아 있으면 완결문이어야 한다.
        self.assertTrue(any(issue["latest_change"] for issue in ongoing))
        self.assertTrue(
            all(issue["latest_change"].endswith((".", "!", "?")) for issue in ongoing if issue["latest_change"])
        )

    def test_issue_detail_timeline_contains_every_linked_article(self):
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertEqual(len(issue["related_articles"]), issue["article_count"])

    def test_issue_matching_audit_is_generated(self):
        self.assertEqual(self.meta["issue_matching_version"], "hybrid-review-v4")
        self.assertIn("embedding_cache_entries", self.meta)
        self.assertGreater(self.meta["embedding_selected_count"], 0)
        self.assertGreaterEqual(self.meta["embedding_selected_coverage"], 0.95)
        self.assertIn("remote_embedding_selected_count", self.meta)
        self.assertEqual(self.issue_audit["matching_version"], "hybrid-review-v4")
        self.assertTrue(self.issue_audit["review_candidates"])
        self.assertTrue(all(row["review_state"] == "pending" for row in self.issue_audit["review_candidates"]))
        # 추적률 기준은 원격 Gemini 임베딩이 있는 빌드(CI)에만 적용한다.
        # 로컬 빌드는 폴백 벡터라 병합이 보수적이어서 구조적으로 기준 미달
        # (실측 0.125 — 코드 결함이 아니라 환경 차이).
        if self.meta.get("remote_embedding_selected_count", 0):
            self.assertGreaterEqual(self.meta["latest_briefing_tracking_rate"], 0.20)
        self.assertTrue(self.issue_audit["clusters"])
        self.assertTrue(all(cluster["matches"] for cluster in self.issue_audit["clusters"]))

    def test_manual_merge_overrides_are_auditable(self):
        approved = set(self.issue_audit["overrides"]["approved"])
        rejected = set(self.issue_audit["overrides"]["rejected"])
        pending = {row["candidate_id"] for row in self.issue_audit["review_candidates"]}
        self.assertTrue(approved)
        self.assertTrue(rejected)
        self.assertTrue(approved.isdisjoint(rejected | pending))
        methods = {
            match["method"]
            for cluster in self.issue_audit["clusters"]
            for match in cluster["matches"]
        }
        self.assertIn("manual_approved", methods)

    def test_generated_issue_clusters_have_no_country_or_facility_conflicts(self):
        by_hash = {article["hash"]: article for article in self.news}
        non_country_scopes = {"OTHER", "UNSPECIFIED", "GLOBAL", "EUROPE", "EU"}
        for cluster in self.issue_audit["clusters"]:
            members = [by_hash[member["hash"]] for member in cluster["members"]]
            for left, right in combinations(members, 2):
                left_countries = set(left.get("countries") or []) - non_country_scopes
                right_countries = set(right.get("countries") or []) - non_country_scopes
                if left_countries and right_countries and left_countries.isdisjoint(right_countries):
                    # 국경을 넘는 하나의 사건은 양국을 함께 명시한 중간 기사로 연결될
                    # 수 있다. 과거 EU_ETC 묶음 없이도 그 연결 근거가 있어야 한다.
                    has_cross_border_bridge = any(
                        left_countries & (set(member.get("countries") or []) - non_country_scopes)
                        and right_countries & (set(member.get("countries") or []) - non_country_scopes)
                        for member in members
                    )
                    self.assertTrue(has_cross_border_bridge)
                self.assertFalse(build_data._facility_conflict(left, right))

    def test_region_matches_confident_country_tags(self):
        self.assertEqual(self.meta["region_classification_version"], "country-first-v1")
        self.assertEqual(self.meta["region_country_mismatch_count"], 0)
        for article in self.news:
            countries = set(article.get("countries") or []) - {"OTHER"}
            if not countries:
                continue
            expected = "국내" if "KR" in countries else "해외"
            self.assertEqual(article["region"], expected, article["title_kr"])

    def test_brand_remains_private_with_open_graph_metadata(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("NUCLENS", html)
        self.assertIn('content="noindex,nofollow"', html)
        self.assertIn('name="color-scheme" content="light dark"', html)
        self.assertIn('name="description"', html)
        self.assertIn('<link rel="canonical" href="https://nuclens.pages.dev/">', html)
        for property_name in ("og:type", "og:site_name", "og:title", "og:description", "og:url"):
            self.assertIn(f'property="{property_name}"', html)
        for name in ("favicon.svg", "robots.txt"):
            self.assertTrue((ROOT / "public" / name).exists(), name)
        self.assertTrue((ROOT / "public" / "logo-mark.svg").exists())
        self.assertFalse((ROOT / "public" / "sitemap.xml").exists())
        self.assertIn("Disallow: /", (ROOT / "public" / "robots.txt").read_text(encoding="utf-8"))

    def test_stage_two_navigation_and_ai_disclosure(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn('data/${name}?t=', script)
        self.assertIn("window.scrollTo(0, 0)", script)
        self.assertIn('if (state.archiveQuery) params.set("q", state.archiveQuery)', script)
        self.assertIn('syncUrl("push")', script)
        self.assertIn('window.addEventListener("popstate"', script)
        # AI 생성 문장에는 반드시 표시가 붙는다. 개수를 1로 고정하면 렌더 지점이
        # 늘어나는 것 자체를 막을 뿐이라(다이얼로그 + 근거 패널), 지점마다 배지가
        # 붙어 있는지를 본다.
        self.assertGreaterEqual(script.count('class="ai-badge"'), 1)
        self.assertIn('Nuclens 해석 <span class="ai-badge">AI</span>', script)
        self.assertIn('${esc(model.impact.label)} <span class="ai-badge">AI</span>', script)
        self.assertIn(".ai-badge", style)

    def test_rss_and_report_copy_are_generated(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        rss_path = ROOT / "public" / "rss.xml"
        self.assertIn('type="application/rss+xml"', html)
        self.assertTrue(rss_path.exists())
        channel = ET.parse(rss_path).getroot().find("channel")
        self.assertIsNotNone(channel)
        self.assertTrue(channel.findall("item"))
        self.assertIn("function issueReportText", script)
        self.assertIn("• 변화:", script)
        self.assertIn('data-copy-issue="${esc(issue.issue_id)}"', script)

    def test_p2_daily_briefing_fields_are_generated(self):
        for briefing in self.briefings:
            self.assertTrue(briefing["headline"])
            self.assertIn("primary_source_count", briefing)
            self.assertIn("tracked_issue_count", briefing)
            self.assertEqual(len(briefing["highlight_issues"]), min(3, briefing["issue_count"]))
            # 요약과 같은 문장이면 변화 블록을 비운다(카드에 같은 문단 두 번 방지).
            for issue in briefing["issues"]:
                if issue["latest_change"]:
                    self.assertNotEqual(issue["latest_change"].rstrip(".!?"), issue["summary"].rstrip(".!?"))

    def test_p4_home_splits_changed_issues_from_the_rest(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        for element_id in ("changedIssues", "changedList", "changedCount", "briefingKicker"):
            self.assertIn(f'id="{element_id}"', html)
        # 히어로가 아래 카드 목록을 그대로 반복하던 블록은 제거했다.
        self.assertNotIn('id="briefingHighlights"', html)
        self.assertNotIn('id="sideStats"', html)
        self.assertIn("function changedIssues", script)

    def test_hero_does_not_repeat_the_status_strip(self):
        """히어로 '데이터 상태'는 상태 스트립과 같은 숫자 넷을 되풀이했다.

        실측(1440×900): 히어로 329px 중 213px를 중복 표시가 차지해 첫 화면에
        이슈 카드가 1장만 들어왔다. 스트립이 마지막 수집·수집 기사·연결 이슈·
        1차 출처를 이미 한 줄로 말한다 — 중복 표시는 정보가 아니다.
        """
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn('id="briefingStatus"', html)
        self.assertNotIn("hero-status", html)
        self.assertNotIn("renderBriefingStatus", script)
        self.assertNotIn(".hero-status", style)
        # 같은 숫자를 말하는 곳은 스트립 하나로 남는다
        self.assertIn("오늘 수집 기사", script)
        self.assertIn("연결된 이슈", script)

    def test_p4_briefings_declare_what_the_headline_is(self):
        for briefing in self.briefings:
            self.assertIn(briefing["headline_kind"],
                          {"change", "issue", "empty", "synthesis"})
            self.assertIn("changed_issue_count", briefing)
            # '무엇이 달라졌는가'는 헤드라인 이슈가 실제로 **이어지는** 이슈일 때만
            # 내건다. 예전에는 화살표(latest_change) 유무로 판정했는데, 화살표는
            # 요약 되풀이면 지워지므로 이어지는 이슈인데도 0이 될 수 있다.
            if briefing["headline_kind"] == "change":
                self.assertGreater(briefing["tracked_issue_count"], 0)

    def test_p5_detail_order_related_issues_and_mobile_actions(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for heading in ("한 줄 결론", "이번에 달라진 점", "Nuclens 해석", "사건 타임라인과 근거 원문", "관련 이슈"):
            self.assertIn(heading, script)
        self.assertIn("function relatedIssues", script)
        # 제목이 상세 진입점이므로 좁은 화면에서 타임라인 버튼을 숨겨도 길이 남는다.
        self.assertIn("issue-title-button", script)
        self.assertIn(".issue-actions .issue-detail-button { display: none; }", style)
        # JS 스크롤은 CSS의 모션 감소 설정을 자동으로 따르지 않는다.
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', script)

    def test_p5_single_source_is_stated_not_judged(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        # 단일 출처는 전체의 대다수라 경고로 표시하면 신호가 죽는다.
        self.assertEqual(build_data.VERIFICATION_LABELS["partial"], "단일 출처")
        self.assertNotIn("일부 확인", script + html)
        self.assertIn("const BADGE_STATUSES", script)
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertNotEqual(issue["verification"]["label"], "일부 확인")

    def test_selection_reasons_are_not_shown_yet(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        # 이유 문구가 사건 유형의 되풀이라 정보가 되지 않는다. 랭킹을 다시 설계할
        # 때까지 데이터로만 보관하고 화면에는 내보내지 않는다.
        self.assertNotIn('class="issue-why"', script)
        self.assertNotIn("이 이슈가 위에 있는 이유", script)
        for briefing in self.briefings:
            self.assertTrue(any(issue["selection_reasons"] for issue in briefing["issues"]))

    def test_weekly_charts_do_not_force_horizontal_scroll(self):
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        # 좁은 화면에서 표·그래프를 옆으로 밀지 않는다.
        self.assertIn("#topicChart svg { width: 100%; min-width: 0;", style)
        self.assertIn(".keyword-table { overflow-x: visible; }", style)
        self.assertIn('class="slope-legend"', script)
        # 주제명을 선 옆에 붙이면 가장 긴 라벨이 최소 폭을 정해버린다.
        self.assertNotIn("${esc(label)} ${row.now}", script)

    def test_domestic_issues_are_not_pushed_to_the_bottom(self):
        """봇이 국내·해외를 따로 뽑으므로 웹도 두 갈래를 유지해야 한다.

        raw 점수 하나로 합쳐 정렬하면 출처 등급 보너스가 없는 국내 이슈가
        통째로 하위권으로 밀린다(실측 8/1 브리핑에서 국내 3건이 6·8·9위).
        """
        for briefing in self.briefings:
            regions = [issue["region"] for issue in briefing["issues"]]
            if "국내" not in regions or len(regions) < 3:
                continue
            first_domestic = regions.index("국내")
            self.assertLessEqual(
                first_domestic, 2,
                f"{briefing['date']}: 국내 첫 이슈가 {first_domestic + 1}번째",
            )

    def test_interleave_keeps_each_region_in_its_own_order(self):
        rows = [
            {"region": "해외", "importance": "must_read", "sort_score": 9.0, "last_seen": "2026-08-01"},
            {"region": "해외", "importance": "nice_to_know", "sort_score": 8.0, "last_seen": "2026-08-01"},
            {"region": "국내", "importance": "nice_to_know", "sort_score": 3.0, "last_seen": "2026-08-01"},
            {"region": "국내", "importance": "nice_to_know", "sort_score": 1.0, "last_seen": "2026-08-01"},
        ]
        build_data.order_issue_rows(rows)
        self.assertEqual([r["region"] for r in rows], ["해외", "국내", "해외", "국내"])
        # 지역 안에서의 상대 순서는 그대로다
        self.assertEqual(rows[1]["region"], "국내")
        self.assertNotIn("sort_score", rows[0])

    def test_daily_lead_replaces_the_hero_sentence_when_present(self):
        # 제목이 "이슈 제목" 같은 형식이면 어떤 종합 문장도 구체성 검사를
        # 통과할 수 없다(공유할 낱말이 없다) — 실제와 비슷한 제목을 쓴다.
        issues = [{
            "issue_id": "i1", "status": "new", "latest_change": "",
            "title": "원안위, 고리 2호기 계속운전 심사 재개",
            "summary": "", "importance": "must_read", "region": "국내",
            "verification": {"status": "partial"}, "previous_article_count": 0,
        }]
        news = [{"briefing_date": "2026-08-01", "region": "국내"}]
        clusters = [{
            "issue_id": "i1", "first_seen": "2026-08-01",
            "members": [{
                "hash": "h1", "briefing_date": "2026-08-01", "article_date": "2026-08-01",
                "title_kr": "원안위, 고리 2호기 계속운전 심사 재개",
                "summary": "원안위가 심사를 재개했다.", "region": "국내",
            }],
        }]
        leads = {"2026-08-01": {"lead": "원안위가 고리 2호기 계속운전 심사를 재개했습니다."}}
        built = build_data.build_briefings(news, clusters, "", leads)
        self.assertEqual(built[0]["headline_kind"], "synthesis")
        self.assertIn("계속운전", built[0]["headline"])

    def test_weekly_flows_sharing_evidence_are_folded_together(self):
        """흐름 해석은 키워드마다 하나씩 나오므로 한 사건이 여러 번 재포장된다.

        실측(2026-08-03 라이브): '기후변화'와 '원전운영'이 근거 7건 중 4건을
        공유했다 — 둘 다 헝가리 가뭄으로 인한 원전 가동 중단 이야기였다.
        나머지 쌍은 1건(7~17%)이라 임계 0.4가 둘을 깨끗이 가른다.
        """
        def insight(keyword, hashes):
            return {"keyword": keyword, "direction": f"{keyword} 흐름",
                    "evidence": [{"hash": h} for h in hashes]}

        items = [
            insight("기후변화", ["a", "b", "c", "d", "e", "f", "g"]),
            insight("원전운영", ["a", "b", "c", "d", "x", "y", "z"]),   # 4/7 공유
            insight("전력시장", ["a", "p", "q", "r", "s", "t", "u"]),   # 1/7 공유
        ]
        kept = build_data.dedupe_insights(items)
        keywords = [row["keyword"] for row in kept]
        self.assertNotIn("원전운영", keywords, "같은 사건은 접혀야 한다")
        self.assertIn("전력시장", keywords, "1건 공유는 다른 흐름이다")
        folded = next(row for row in kept if row["keyword"] == "기후변화")
        self.assertEqual(folded["merged_keywords"], ["원전운영"],
                         "접힌 키워드는 표기해 정보를 버리지 않는다")

    def test_dedupe_keeps_the_flow_with_more_evidence(self):
        thin = {"keyword": "얇은", "evidence": [{"hash": "a"}, {"hash": "b"}]}
        thick = {"keyword": "두꺼운",
                 "evidence": [{"hash": h} for h in ("a", "b", "c", "d", "e")]}
        kept = build_data.dedupe_insights([thin, thick])
        self.assertEqual([row["keyword"] for row in kept], ["두꺼운"])

    def test_dedupe_leaves_unrelated_flows_alone(self):
        items = [{"keyword": "가", "evidence": [{"hash": "a"}]},
                 {"keyword": "나", "evidence": [{"hash": "b"}]},
                 {"keyword": "다", "evidence": []}]
        self.assertEqual(len(build_data.dedupe_insights(items)), 3)

    def test_vacuous_synthesis_is_rejected_in_favour_of_a_concrete_title(self):
        """아무 사실도 담지 못한 종합 문장은 구체적인 제목보다 못하다.

        실측(2026-08-03 라이브): 그날 이슈에 공통 주제가 없자 모델이 '비워
        두라'는 지시를 어기고 "국내외에서 원자력 및 에너지 정책과 현실에 대한
        다양한 논의와 상황 변화가 있었습니다"를 내놨다 — 이슈 제목과 공유하는
        의미 토큰이 0개다(같은 날 구체적 문장이라면 6~7개).
        """
        issue_rows = [
            {"issue_id": "i1", "previous_article_count": 0,
             "title": "중국 정부, 신규 원전 8기 건설 승인", "summary": ""},
            {"issue_id": "i2", "previous_article_count": 0,
             "title": "헝가리 원전, 가뭄으로 가동 중단", "summary": ""},
        ]
        vacuous = "국내외에서 원자력 및 에너지 정책과 현실에 대한 다양한 논의와 상황 변화가 있었습니다"
        concrete = "중국이 신규 원전 8기를 승인한 가운데 헝가리는 가뭄으로 가동을 중단했습니다"
        self.assertFalse(build_data.synthesis_is_substantive(vacuous, issue_rows))
        self.assertTrue(build_data.synthesis_is_substantive(concrete, issue_rows))
        self.assertFalse(build_data.synthesis_is_substantive("", issue_rows))

        news = [{"briefing_date": "2026-08-03", "region": "해외"}]
        clusters = [{
            "issue_id": "i1", "first_seen": "2026-08-03",
            "members": [{"hash": "h1", "briefing_date": "2026-08-03",
                         "article_date": "2026-08-03", "region": "해외",
                         "title_kr": "중국 정부, 신규 원전 8기 건설 승인",
                         "summary": "중국이 신규 원전 8기를 승인했다."}],
        }]
        built = build_data.build_briefings(
            news, clusters, "", {"2026-08-03": {"lead": vacuous}})
        self.assertNotEqual(built[0]["headline_kind"], "synthesis")
        self.assertIn("중국", built[0]["headline"])

    def test_overlength_synthesis_is_clamped_at_build_time(self):
        """생성 단계가 90자를 지키지만, 계약 위반 데이터가 와도 h1이 문단으로
        번지면 안 된다 (7/30 h1 171자 실사고의 마지막 방어선)."""
        long_lead = ("국내에서는 고리 2호기 계속운전 심사가 재개되었으며, 해외에서는 "
                     "프랑스 EDF 신규 건설과 미국 SMR 인허가 진전이 함께 진행되어 "
                     "정책 환경 전반이 크게 움직인 하루였습니다")
        clamped = build_data._fit_synthesis(long_lead)
        self.assertLessEqual(len(clamped), build_data.SYNTHESIS_LIMIT + 1)
        self.assertTrue(clamped)
        # 90자 이내 문장은 그대로 통과한다
        self.assertEqual(build_data._fit_synthesis("짧은 문장."), "짧은 문장.")
        self.assertEqual(build_data._fit_synthesis(None), "")

    def test_headline_evidence_maps_hashes_to_issue_cards(self):
        issue_rows = [
            {"issue_id": "i1", "title": "이슈 하나",
             "related_articles": [{"hash": "h1"}, {"hash": "h2"}]},
            {"issue_id": "i2", "title": "이슈 둘",
             "related_articles": [{"hash": "h3"}]},
        ]
        chips = build_data._evidence_chips(
            [{"hash": "h2"}, {"hash": "h1"}, {"hash": "h3"}, {"hash": "없는해시"}],
            issue_rows,
        )
        # 같은 이슈(h2·h1→i1)는 한 번만, 미매칭 hash 는 조용히 제외
        self.assertEqual([chip["issue_id"] for chip in chips], ["i1", "i2"])
        self.assertEqual(chips[0]["title"], "이슈 하나")

    def test_briefings_always_carry_headline_evidence_field(self):
        for briefing in self.briefings:
            self.assertIn("headline_evidence", briefing)
            self.assertIsInstance(briefing["headline_evidence"], list)

    def test_hero_evidence_chips_render_only_for_synthesis(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="headlineEvidence"', html)
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        # synthesis 가 아닐 때 칩을 보이면 근거 없는 문장에 근거가 달린다
        self.assertIn('briefing.headline_kind === "synthesis"', render)
        self.assertIn("headline_evidence", render)
        # 칩 클릭이 이슈 dialog 로 연결되도록 위임 대상에 등록돼야 한다
        self.assertIn('"headlineEvidence"', script)

    def test_empty_state_does_not_contradict_the_changed_section(self):
        """필터 결과가 위 구역에만 있을 때 아래에서 '없습니다'라고 하면 안 된다.

        실측: topic=fusion 이면 '지금 달라진 이슈'에 독일 핵융합 카드가 남는데
        '오늘 확인된 이슈'는 빈 상태를 띄워 한 화면이 스스로를 부정했다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("visibleChanged.length", render)
        self.assertIn("section-note", render)
        empty_index = render.index("조건에 맞는 이슈가 없습니다")
        guard_index = render.index("visibleChanged.length\n      ?")
        self.assertLess(guard_index, empty_index)

    def test_p2_structure_status_search_and_responsive_controls_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for element_id in (
            "systemStatus", "headerStatus", "globalSearchDialog", "briefingFilters",
            "issueSort", "issueViewToggle", "mobileTabs", "themeToggle", "view-saved",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("function renderSystemStatus", script)
        self.assertIn("function switchView", script)
        self.assertIn("nuclens-saved-issues", script)
        self.assertIn(':root[data-theme="dark"]', style)
        self.assertIn("@media (min-width: 1200px)", style)
        self.assertIn("@media (max-width: 767px)", style)
        self.assertIn(".mobile-tabs", style)

    def test_publications_view_is_always_generated(self):
        """발간물 파일은 0건이어도 항상 존재해야 한다.

        app.js 가 없는 JSON 을 만나면 전 화면이 죽는다(8/1 빈 화면 사고). 그래서
        수집 결과와 무관하게 build 가 빈 구조라도 반드시 써야 한다.
        """
        self.assertIsInstance(self.publications, dict)
        self.assertIsInstance(self.publications["items"], list)
        for item in self.publications["items"]:
            self.assertTrue(item["title"])
            self.assertTrue(item["url"].startswith("http"))
            self.assertIn("org_kr", item)
            self.assertIsInstance(item["is_new"], bool)

    def test_publications_loader_survives_missing_and_broken_files(self):
        original = build_data.BOT_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                build_data.BOT_DIR = Path(tmp)
                self.assertEqual(build_data.load_publications()["items"], [])
                (Path(tmp) / "publications.json").write_text("{깨진 JSON", encoding="utf-8")
                self.assertEqual(build_data.load_publications()["items"], [])
                (Path(tmp) / "publications.json").write_text(
                    json.dumps({"items": [
                        {"title": "정상 보고서", "url": "https://iaea.org/p/1", "date": "2099-01-01",
                         "org": "IAEA", "org_kr": "국제원자력기구", "kind": "publication"},
                        {"title": "", "url": "https://iaea.org/p/2"},
                        {"title": "URL 없음", "url": ""},
                    ]}, ensure_ascii=False), encoding="utf-8")
                view = build_data.load_publications()
                self.assertEqual([item["title"] for item in view["items"]], ["정상 보고서"])
                self.assertTrue(view["items"][0]["is_new"])
        finally:
            build_data.BOT_DIR = original

    def test_publications_tab_is_wired_and_failure_tolerant(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="pubs"', html)
        self.assertIn('id="view-pubs"', html)
        self.assertIn('"pubs"', script)
        self.assertIn("function renderPubs", script)
        # 발간물 로드 실패가 사이트 전체를 죽이면 안 된다
        self.assertIn('loadJSON("publications.json").catch(', script)
        # 렌더러는 데이터를 신뢰하지 않는다 — 배열에 null 이 섞이면 item.org_kr
        # 에서 TypeError 가 나고 탭이 멈춘다(2026-08-02 셀프 검증에서 실측).
        render = script.split("function renderPubs(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('typeof item === "object"', render)
        self.assertIn("item.title && item.url", render)
        # 모바일에서도 도달 가능해야 한다 — 데스크톱 전용이면 폰에서는 기능이
        # 아예 없는 것과 같다. 탭 수와 grid 열 수는 함께 움직여야 한다(실측
        # 360px에서 5열 72px, 라벨 잘림 0).
        mobile_nav = html.split('id="mobileTabs"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('data-view="pubs"', mobile_nav)
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(mobile_nav.count("<button"),
                         5, "모바일 탭 수가 바뀌면 grid-template-columns도 함께 고쳐야 한다")
        self.assertIn("grid-template-columns: repeat(5, 1fr)", style)

    def test_keei_candidates_narrow_but_never_decide(self):
        """점수는 후보만 좁힌다 — 판정은 LLM 몫이다.

        실측(2026-08-02): 코사인·IDF 점수 상위권을 벤더명만 같은 오매칭이
        차지했다. 점수로 자동 연결하면 틀린 연결이 카드에 박힌다.
        """
        issue_rows = [
            {"issue_id": "i1", "title": "한수원, 영덕군과 신규 원전 건설 협력 합의", "summary": ""},
            {"issue_id": "i2", "title": "미국 NRC, 환경영향평가 규정 개정 공청회", "summary": ""},
        ]
        publications = {"items": [{
            "url": "https://keei.re.kr/x", "title": "인사이트(2026.06.26.)",
            "date": "2026-06-26", "org_kr": "에너지경제연구원",
            "toc": {"issue_title": "전 세계 원전 현황",
                    "briefs": ["한수원, 신규 대형원전 부지로 경북 영덕군 선정",
                               "완전히 무관한 항목"]},
        }]}
        candidates = build_data.keei_candidates(issue_rows, build_data.keei_entries(publications))
        pairs = {(row["issue_id"], row["keei_item"]) for row in candidates}
        # 조사가 붙어 갈라진 '영덕군과'/'영덕군'을 접두 일치로 흡수해야 후보가 된다
        self.assertIn(("i1", "한수원, 신규 대형원전 부지로 경북 영덕군 선정"), pairs)
        self.assertTrue(all(row["pair_id"] for row in candidates))
        self.assertLessEqual(len(candidates), build_data.KEEI_CANDIDATE_CAP)

    def test_keei_shared_absorbs_korean_particles(self):
        shared = build_data._keei_shared({"영덕군과", "신규"}, {"영덕군", "신규", "선정"})
        self.assertEqual(shared, {"영덕군", "신규"})
        # 짧은 토큰까지 접두로 묶으면 아무 낱말이나 붙는다
        self.assertEqual(build_data._keei_shared({"가"}, {"가스"}), set())

    def test_keei_refs_attach_only_what_the_llm_approved(self):
        issue_rows = [{"issue_id": "i1",
                       "title": "한수원, 영덕군과 신규 원전 건설 협력 합의", "summary": ""}]
        publications = {"items": [{
            "url": "https://keei.re.kr/x", "title": "인사이트(2026.06.26.)",
            "date": "2026-06-26", "org_kr": "에너지경제연구원",
            "toc": {"issue_title": "",
                    "briefs": ["한수원, 신규 대형원전 부지로 경북 영덕군 선정"]},
        }]}
        original = build_data.keei_match.match_pairs
        try:
            # 판정이 없으면(키 없음·실패) 아무 것도 붙이지 않는다
            build_data.keei_match.match_pairs = lambda c, **kw: ({}, {"status": "no_api_key"})
            build_data.attach_keei_refs(issue_rows, publications)
            self.assertNotIn("keei_refs", issue_rows[0])

            # 승인된 것만 붙는다
            build_data.keei_match.match_pairs = lambda c, **kw: (
                {row["pair_id"]: True for row in c}, {"status": "ok"})
            stats = build_data.attach_keei_refs(issue_rows, publications)
            self.assertEqual(stats["attached"], 1)
            ref = issue_rows[0]["keei_refs"][0]
            self.assertEqual(ref["url"], "https://keei.re.kr/x")
            self.assertEqual(ref["item"], "한수원, 신규 대형원전 부지로 경북 영덕군 선정")
        finally:
            build_data.keei_match.match_pairs = original

    def test_material_pack_copy_gathers_report_source_material(self):
        """'보고서용 복사'는 카드 한 장 요약, '자료 팩 복사'는 초안 원재료다.

        동향분석 보고서를 쓰려면 타임라인·출처·수치가 필요한데 기존 복사는
        6줄 요약뿐이라 결국 화면을 다시 뒤져야 했다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function issueMaterialPack", script)
        self.assertIn("data-pack-issue", script)
        self.assertIn("function copyIssuePack", script)
        pack = script.split("function issueMaterialPack(", 1)[1].split("\nasync function", 1)[0]
        for section in ("사건 타임라인", "수치·일정", "검증 상태", "관련 발간물"):
            self.assertIn(section, pack, f"자료 팩에 '{section}' 이 없다")
        # AI 해석은 근거가 아니라 해석이므로 원재료에 섞지 않는다
        self.assertNotIn("implication", pack)

    def test_keei_refs_render_in_card_and_detail(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function keeiRefLine", script)
        self.assertIn("function keeiDialogSection", script)
        self.assertIn("keei_refs", script)
        # 목차 제목과 링크만 — 본문을 싣지 않는다(저작권)
        self.assertIn("목차와 원문 링크만 제공합니다", script)

    def test_p2_keyword_table_slope_graph_and_chart_evidence_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        for element_id in (
            "keywordSort", "keywordTable", "keywordInterpretation", "keywordEvidence",
            "countryInterpretation", "topicChart", "topicInterpretation", "topicEvidence",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for legacy_id in ("topTags", "risingTags", "newTags", "topicLegend"):
            self.assertNotIn(f'id="{legacy_id}"', html)
        self.assertIn("function renderKeywordTable", script)
        self.assertIn("function renderSlopeGraph", script)
        self.assertIn('class="slope-series"', script)

    def test_p2_archive_tracking_sort_filters_and_highlight_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        for element_id in ("archivePeriod", "archiveVerification", "archiveSort"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('class="tracking-period"', script)
        self.assertIn("function markMatch", script)
        self.assertIn("<mark>", script)

    def test_p2_loading_empty_and_error_states_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn("skeleton-card", html)
        self.assertIn('class="empty-state"', script)
        self.assertIn('class="error-state"', script)
        self.assertIn("다시 시도", script)
        self.assertIn("@keyframes skeleton-pulse", style)

    def test_ci_persists_embeddings_and_fails_on_web_smoke_errors(self):
        repo_root = ROOT.parent
        crawl = (repo_root / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        daily = (repo_root / ".github" / "workflows" / "daily-brief.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache/restore@v4", crawl)
        self.assertIn("actions/cache/save@v4", crawl)
        self.assertIn("Restore embeddings cache", daily)
        self.assertIn("gemini-embedding-2", crawl)
        self.assertIn("--window-days 21", crawl)
        self.assertIn("--require-nonzero", daily)
        self.assertNotIn("- name: Smoke test live site\n        continue-on-error: true", crawl)
        self.assertNotIn("- name: Render smoke (라이브 화면 검증)\n        if: always() && steps.claim.conclusion == 'success'\n        continue-on-error: true", daily)


class SelectionStatsTests(unittest.TestCase):
    """통계 레코드는 hash 가 없어 append 로 쌓인다 — 읽는 쪽이 하나를 고른다."""

    @staticmethod
    def _row(day, status, stamp, below=0):
        return {"record_type": "selection_stats", "date": day,
                "pipeline_status": status, "generated_at": stamp,
                "domestic": {"candidate_count": 3, "selected_count": 1,
                             "below_floor_count": below},
                "overseas": {"candidate_count": 5, "selected_count": 2,
                             "below_floor_count": 0}}

    def test_latest_generated_at_wins(self):
        rows = [self._row("2026-08-03", "ok", "2026-08-03T07:00:00+09:00"),
                self._row("2026-08-03", "ok", "2026-08-03T07:40:00+09:00", below=4)]
        picked = build_data.pick_selection_stats(rows)["2026-08-03"]
        self.assertEqual(picked["domestic"]["below_floor_count"], 4)

    def test_failed_rerun_does_not_override_ok(self):
        """실패한 재실행이 정상 기록을 덮으면 사이트가 멀쩡한 날을 장애로 표시한다."""
        rows = [self._row("2026-08-03", "ok", "2026-08-03T07:00:00+09:00"),
                self._row("2026-08-03", "error", "2026-08-03T09:00:00+09:00")]
        self.assertEqual(
            build_data.pick_selection_stats(rows)["2026-08-03"]["pipeline_status"], "ok")

    def test_ok_after_error_is_taken(self):
        rows = [self._row("2026-08-03", "error", "2026-08-03T07:00:00+09:00"),
                self._row("2026-08-03", "ok", "2026-08-03T09:00:00+09:00")]
        self.assertEqual(
            build_data.pick_selection_stats(rows)["2026-08-03"]["pipeline_status"], "ok")

    def test_article_rows_ignored(self):
        rows = [{"date": "2026-08-03", "hash": "abc", "score": 12.0}]
        self.assertEqual(build_data.pick_selection_stats(rows), {})


class EmptyBriefingRowTests(unittest.TestCase):
    """하한에 전부 걸린 날은 브리핑 행 자체가 안 생긴다 — 그러면 화면이 사유를 못 말한다.

    브리핑 날짜는 '발송된 기사'에서 나오므로(dates = news_items 의 briefing_date),
    선정이 0건이면 briefings 에 행이 없다. 통계만 있는 날을 빈 행으로 채운다.
    """

    def _news(self, day):
        return [{
            "hash": f"h{day}", "briefing_date": day, "article_date": day,
            "region": "해외", "title_kr": "제목", "summary": "요약",
            "topics": [], "canonical_tags": [], "source_type": "media",
            "evidence_role": "original", "url": "https://example.com/a",
            "publisher": "Example", "domain": "example.com",
        }]

    def _stats(self, day, below, candidates):
        half = {"candidate_count": candidates // 2, "selected_count": 0,
                "below_floor_count": below // 2}
        return {"date": day, "pipeline_status": "ok",
                "generated_at": f"{day}T07:30:00+09:00",
                "domestic": dict(half), "overseas": dict(half)}

    def test_all_cut_day_still_gets_a_row(self):
        stats = {"2026-08-01": self._stats("2026-08-01", 0, 2),
                 "2026-08-02": self._stats("2026-08-02", 6, 6)}
        rows = build_data.build_briefings(self._news("2026-08-01"), [], "", {}, stats)
        by_date = {row["date"]: row for row in rows}
        self.assertIn("2026-08-02", by_date)
        cut = by_date["2026-08-02"]
        self.assertEqual(cut["issue_count"], 0)
        self.assertEqual(cut["below_floor_count"], 6)
        self.assertEqual(cut["issues"], [])
        self.assertEqual(rows[0]["date"], "2026-08-02")  # briefings[0] 이 최신

    def test_does_not_backfill_before_the_data_window(self):
        stats = {"2026-05-01": self._stats("2026-05-01", 4, 4),
                 "2026-08-01": self._stats("2026-08-01", 0, 2)}
        rows = build_data.build_briefings(self._news("2026-08-01"), [], "", {}, stats)
        self.assertNotIn("2026-05-01", {row["date"] for row in rows})

    def test_selection_view_is_none_without_stats(self):
        """0 으로 채우면 '후보가 없었다'는 거짓 진술이 된다."""
        view = build_data.selection_view(None)
        self.assertIsNone(view["candidate_count"])
        self.assertIsNone(view["below_floor_count"])
        self.assertIsNone(view["pipeline_status"])

    def test_selection_view_sums_regions(self):
        view = build_data.selection_view(self._stats("2026-08-02", 6, 10))
        self.assertEqual(view["candidate_count"], 10)
        self.assertEqual(view["below_floor_count"], 6)
        self.assertEqual(view["pipeline_status"], "ok")


class SelectionOverrideTests(unittest.TestCase):
    """알고리즘 결과에 사람이 얹는 최종 판단. 적용 단위는 기사가 아니라 이슈다."""

    def _write(self, payload) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "selection_overrides.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return tmp

    def _load(self, payload):
        return build_data.load_selection_overrides(self._write(payload))

    def test_missing_date_is_ignored(self):
        """날짜가 없으면 한 번 승격한 이슈가 몇 달 뒤에도 맨 위에 남는다."""
        over = self._load({"promote": [{"hash8": "abcd1234"}]})
        self.assertEqual(over["promote"], {})

    def test_malformed_date_is_ignored(self):
        over = self._load({"promote": [{"hash8": "abcd1234", "date": "2026-8-3"}]})
        self.assertEqual(over["promote"], {})

    def test_demote_beats_promote_on_same_hash(self):
        over = self._load({
            "promote": [{"hash8": "abcd1234", "date": "2026-08-03"}],
            "demote": [{"hash8": "abcd1234", "date": "2026-08-03"}],
        })
        self.assertEqual(over["promote"], {})
        self.assertIn(("abcd1234", "2026-08-03"), over["demote"])

    def test_default_demote_action_is_hide(self):
        over = self._load({"demote": [{"hash8": "abcd1234", "date": "2026-08-03"}]})
        self.assertEqual(over["demote"][("abcd1234", "2026-08-03")], "hide_from_today")

    def test_unknown_action_falls_back_to_hide(self):
        over = self._load({"demote": [{"hash8": "abcd1234", "date": "2026-08-03",
                                       "action": "explode"}]})
        self.assertEqual(over["demote"][("abcd1234", "2026-08-03")], "hide_from_today")

    def test_promotion_injects_briefing_date(self):
        """미발송 기사는 briefing_date 가 없어 배열에 아예 없다 — 정렬로는 못 올린다."""
        over = self._load({"promote": [{"hash8": "abcd1234", "date": "2026-08-03"}]})
        visible = [{"hash": "abcd1234ffff", "briefing_date": None}]
        self.assertEqual(build_data.apply_promotions(visible, over), 1)
        self.assertEqual(visible[0]["briefing_date"], "2026-08-03")
        self.assertTrue(visible[0]["promoted_by_editor"])

    def test_promotion_of_unknown_hash_is_reported_not_fatal(self):
        over = self._load({"promote": [{"hash8": "nosuch01", "date": "2026-08-03"}]})
        visible = [{"hash": "abcd1234ffff", "briefing_date": None}]
        self.assertEqual(build_data.apply_promotions(visible, over), 0)
        self.assertEqual(visible[0]["briefing_date"], None)
        build_data.report_unmatched_overrides(over)  # 죽지 않는다

    def test_hide_applies_to_the_whole_cluster(self):
        """클러스터의 다른 멤버가 살아 있어도 이슈 카드가 사라져야 한다."""
        over = self._load({"demote": [{"hash8": "aaaa1111", "date": "2026-08-03",
                                       "action": "hide_from_today"}]})
        members = [{"hash": "aaaa1111zzzz"}, {"hash": "bbbb2222zzzz"}]
        self.assertEqual(build_data.override_verdict(members, "2026-08-03", over), "hide")

    def test_verdict_is_scoped_to_the_date(self):
        over = self._load({"demote": [{"hash8": "aaaa1111", "date": "2026-08-03"}]})
        members = [{"hash": "aaaa1111zzzz"}]
        self.assertEqual(build_data.override_verdict(members, "2026-08-02", over), "")

    def test_demote_only_keeps_the_issue(self):
        over = self._load({"demote": [{"hash8": "aaaa1111", "date": "2026-08-03",
                                       "action": "demote_only"}]})
        self.assertEqual(
            build_data.override_verdict([{"hash": "aaaa1111zz"}], "2026-08-03", over),
            "demote")

    def test_cluster_with_both_verdicts_demotes(self):
        over = self._load({
            "promote": [{"hash8": "aaaa1111", "date": "2026-08-03"}],
            "demote": [{"hash8": "bbbb2222", "date": "2026-08-03",
                        "action": "demote_only"}],
        })
        members = [{"hash": "aaaa1111zz"}, {"hash": "bbbb2222zz"}]
        self.assertEqual(build_data.override_verdict(members, "2026-08-03", over),
                         "demote")

    def test_pin_does_not_cross_regions(self):
        """해외 이슈를 올려도 국내 자리를 먹으면 안 된다."""
        rows = [
            {"region": "국내", "importance": "nice_to_know", "sort_score": 20.0,
             "last_seen": "2026-08-03", "editor_pin": 0, "title": "국내1"},
            {"region": "해외", "importance": "nice_to_know", "sort_score": 1.0,
             "last_seen": "2026-08-03", "editor_pin": 1, "title": "승격 해외"},
            {"region": "해외", "importance": "must_read", "sort_score": 30.0,
             "last_seen": "2026-08-03", "editor_pin": 0, "title": "해외 강자"},
        ]
        build_data.order_issue_rows(rows)
        overseas = [row["title"] for row in rows if row["region"] == "해외"]
        self.assertEqual(overseas[0], "승격 해외")   # 자기 지역 안에서는 최상단
        self.assertEqual(rows[0]["region"], "국내")  # 지역 맞물림은 그대로

    def test_pin_is_stripped_from_output(self):
        rows = [{"region": "국내", "importance": "must_read", "sort_score": 1.0,
                 "last_seen": "2026-08-03", "editor_pin": 1}]
        build_data.order_issue_rows(rows)
        self.assertNotIn("editor_pin", rows[0])
        self.assertNotIn("sort_score", rows[0])

    def test_repo_template_is_valid_and_empty(self):
        over = build_data.load_selection_overrides()
        self.assertEqual(over["promote"], {})
        self.assertEqual(over["demote"], {})

    # ---- build_briefings 통합 — 클러스터 누수 회귀 방지 --------------------

    @staticmethod
    def _member(article_hash, day, title):
        return {
            "hash": article_hash, "briefing_date": day, "article_date": day,
            "region": "해외", "title_kr": title, "title": title, "summary": "요약",
            "implication": "", "why_important": "", "importance": "nice_to_know",
            "topics": [], "canonical_tags": [], "tags": [],
            "source_type": "media", "evidence_role": "original",
            "url": "https://example.com/a", "publisher": "Example",
            "domain": "example.com", "selection_score": 10.0,
        }

    def _two_member_issue(self, day):
        members = [self._member("aaaa1111zzzz", day, "묶인 기사 하나"),
                   self._member("bbbb2222zzzz", day, "묶인 기사 둘")]
        return members, [{"issue_id": "issue-x", "first_seen": day, "members": members}]

    def test_hiding_one_member_removes_the_whole_issue_card(self):
        """멤버 하나만 지우면 다른 멤버가 briefing_date 를 갖고 있어 카드가 남는다."""
        day = "2026-08-03"
        members, issues = self._two_member_issue(day)
        over = self._load({"demote": [{"hash8": "aaaa1111", "date": day,
                                       "action": "hide_from_today"}]})
        rows = build_data.build_briefings(members, issues, "", {}, None, over)
        self.assertEqual(rows[0]["issue_count"], 0)
        self.assertEqual(rows[0]["issues"], [])

    def test_hidden_issue_articles_drop_from_counts(self):
        """카드는 사라졌는데 '오늘 수집 기사 N건'만 그대로면 화면이 자기모순."""
        day = "2026-08-03"
        members, issues = self._two_member_issue(day)
        over = self._load({"demote": [{"hash8": "aaaa1111", "date": day}]})
        rows = build_data.build_briefings(members, issues, "", {}, None, over)
        self.assertEqual(rows[0]["article_count"], 0)

    def test_without_override_the_issue_survives(self):
        day = "2026-08-03"
        members, issues = self._two_member_issue(day)
        rows = build_data.build_briefings(members, issues, "", {}, None, None)
        self.assertEqual(rows[0]["issue_count"], 1)
        self.assertEqual(rows[0]["article_count"], 2)


class OpenQuestionTests(unittest.TestCase):
    """이슈에 붙일 '아직 확정되지 않은 것'은 대표 기사가 아니라 이슈에서 고른다."""

    @staticmethod
    def _member(hash_, question, **kw):
        row = {"hash": hash_, "open_question": question, "briefing_date": "2026-08-03",
               "article_date": "2026-08-03", "source_type": "media",
               "evidence_role": "original"}
        row.update(kw)
        return row

    def test_none_when_no_member_has_one(self):
        members = [self._member("a", ""), self._member("b", None)]
        self.assertEqual(build_data.pick_open_question(members), "")

    def test_official_source_wins_over_representative(self):
        """미확정 내용은 대표 기사엔 없고 공식 기사에만 있는 경우가 흔하다."""
        members = [
            self._member("a", "언론이 쓴 미확정 문장"),
            self._member("b", "규제기관이 밝힌 미확정 문장", source_type="official"),
        ]
        self.assertEqual(build_data.pick_open_question(members),
                         "규제기관이 밝힌 미확정 문장")

    def test_tier1_wins_when_no_official(self):
        members = [
            self._member("a", "일반 매체 문장"),
            self._member("b", "전문지 문장", source_tier=1),
        ]
        self.assertEqual(build_data.pick_open_question(members), "전문지 문장")

    def test_falls_back_to_latest_filled(self):
        members = [
            self._member("a", "오래된 문장", article_date="2026-08-01"),
            self._member("b", "최신 문장", article_date="2026-08-03"),
        ]
        self.assertEqual(build_data.pick_open_question(members), "최신 문장")

    def test_whitespace_only_is_not_a_value(self):
        self.assertEqual(build_data.pick_open_question([self._member("a", "   ")]), "")

    def test_records_without_the_field_load_fine(self):
        """기존 아카이브 400여 건은 전부 이 필드가 없다."""
        self.assertEqual(build_data.pick_open_question([{"hash": "a"}]), "")


class OpenQuestionRenderTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_detail_only_not_card(self):
        """카드는 이미 3줄이고, 목록은 예외만 표시한다."""
        self.assertIn("아직 확정되지 않은 것", self.script)
        card_fn = self.script.split("function issueCard(")[1].split("\nfunction ")[0]
        self.assertNotIn("open_question", card_fn)

    def test_hidden_when_empty_and_escaped(self):
        self.assertIn("${issue.open_question ? `<p class=\"dialog-open\">", self.script)
        self.assertIn("esc(issue.open_question)", self.script)

    def test_report_pack_includes_it(self):
        self.assertIn("• 미확정: ${issue.open_question}", self.script)

    def test_style_exists(self):
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".dialog-open", style)


class WeeklyReportTests(unittest.TestCase):
    """주간 판세 — 문장마다 근거를 붙인다. 전역 목록만으로는 같은 칩이 반복된다."""

    ROWS = [
        {"issue_id": "issue-1", "title": "체코 두코바니 본계약", "last_seen": "2026-08-01",
         "related_articles": [{"hash": "aaaaaaaa1111"}]},
        {"issue_id": "issue-2", "title": "미국 NRC 규정 개정", "last_seen": "2026-07-30",
         "related_articles": [{"hash": "bbbbbbbb2222"}]},
        {"issue_id": "issue-3", "title": "지난달 이슈", "last_seen": "2026-07-01",
         "related_articles": [{"hash": "cccccccc3333"}]},
    ]

    def _store(self, tmp: Path, **overrides):
        report = {"week_id": "2026-W31", "week_start": "2026-07-27",
                  "week_end": "2026-08-02", "source_issue_count": 99,
                  "weekly_intro": "흐름",
                  "policy_shifts": [{"what": "변화", "so_what": "함의",
                                     "evidence_hashes": ["aaaaaaaa", "deadbeef"]}],
                  "theme_moves": [], "khnp_direct": "", "watchpoints": ["다음 주"],
                  "key_events": []}
        report.update(overrides)
        (tmp / "weekly_reports.json").write_text(
            json.dumps({"schema_version": 1, "reports": {"2026-W31": report}},
                       ensure_ascii=False), encoding="utf-8")

    def _load(self, tmp: Path, rows=None):
        original = build_data.BOT_DIR
        build_data.BOT_DIR = tmp
        try:
            return build_data.load_weekly_report(self.ROWS if rows is None else rows)
        finally:
            build_data.BOT_DIR = original

    def test_missing_file_returns_none(self):
        """리포트 없는 주(목요일 이전)에는 기존 정량 트렌드만 그린다."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._load(Path(tmp)))

    def test_short_hash_resolves_to_issue_chip(self):
        """봇은 hash 앞 8자리만 남긴다 — 전체 hash 색인으로는 하나도 안 걸린다."""
        with tempfile.TemporaryDirectory() as tmp:
            self._store(Path(tmp))
            report = self._load(Path(tmp))
            chips = report["policy_shifts"][0]["evidence"]
            self.assertEqual([c["issue_id"] for c in chips], ["issue-1"])
            self.assertNotIn("evidence_hashes", report["policy_shifts"][0])

    def test_unknown_hash_only_empties_the_chip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._store(Path(tmp), policy_shifts=[
                {"what": "변화", "evidence_hashes": ["deadbeef"]}])
            report = self._load(Path(tmp))
            self.assertEqual(report["policy_shifts"][0]["evidence"], [])
            self.assertEqual(report["policy_shifts"][0]["what"], "변화")

    def test_issue_count_recomputed_from_real_merges(self):
        """봇은 제목 정규화로 어림잡을 수밖에 없지만 웹에는 실제 병합 결과가 있다."""
        with tempfile.TemporaryDirectory() as tmp:
            self._store(Path(tmp))
            report = self._load(Path(tmp))
            self.assertEqual(report["source_issue_count"], 2)  # 지난달 이슈는 제외

    def test_corrupt_file_does_not_break_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "weekly_reports.json").write_text("{ 깨진", encoding="utf-8")
            self.assertIsNone(self._load(Path(tmp)))


class OpenQuestionRollupTests(unittest.TestCase):
    """'아직 결론 나지 않은 것' — 이슈 단위로 한 번씩, 최신순, 최대 5개."""

    @staticmethod
    def _row(issue_id, question, last_seen="2026-08-01", importance="nice_to_know"):
        return {"issue_id": issue_id, "title": f"제목 {issue_id}",
                "open_question": question, "last_seen": last_seen,
                "importance": importance}

    def test_deduped_by_text(self):
        rows = [self._row("a", "같은 문장"), self._row("b", "같은 문장"),
                self._row("c", "다른 문장")]
        out = build_data.collect_open_questions(rows)
        self.assertEqual([row["text"] for row in out], ["같은 문장", "다른 문장"])

    def test_capped(self):
        rows = [self._row(str(i), f"문장 {i}") for i in range(9)]
        self.assertEqual(len(build_data.collect_open_questions(rows)), 5)

    def test_latest_first(self):
        rows = [self._row("old", "옛 문장", "2026-07-01"),
                self._row("new", "새 문장", "2026-08-03")]
        self.assertEqual(build_data.collect_open_questions(rows)[0]["text"], "새 문장")

    def test_empty_when_no_questions(self):
        self.assertEqual(build_data.collect_open_questions([self._row("a", "")]), [])

    def test_each_item_carries_its_evidence(self):
        out = build_data.collect_open_questions([self._row("a", "문장")])
        self.assertEqual(out[0]["evidence"][0]["issue_id"], "a")


class WeeklyRenderTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_five_fixed_sections(self):
        for title in ("이번 주 판을 바꾼 것", "조용하지만 놓치면 안 되는 것",
                      "한수원에 직접 닿는 변화", "다음 주 하나만 본다면",
                      "아직 결론 나지 않은 것"):
            self.assertIn(title, self.script)

    def test_panel_hidden_without_data(self):
        self.assertIn("if (!report && !questions.length) { panel.hidden = true; return; }",
                      self.script)
        self.assertIn('id="weeklyReport"', self.html)

    def test_renders_before_existing_trend_charts(self):
        """기존 키워드·slope 는 아래로 — 리포트가 먼저 온다."""
        self.assertLess(self.html.index('id="weeklyReport"'),
                        self.html.index('id="keywordTable"'))
        trend_fn = self.script.split("function renderTrend()")[1]
        self.assertLess(trend_fn.index("renderWeeklyReport()"),
                        trend_fn.index("renderKeywordTable()"))

    def test_chips_are_clickable(self):
        """칩이 상세로 연결되려면 컨테이너가 위임 목록에 있어야 한다.

        목록 끝을 통째로 문자열 비교하면 컨테이너를 하나 추가할 때마다 깨진다.
        필요한 것은 '이 id 들이 위임돼 있는가'이므로 개별로 확인한다.
        """
        block = self.script.split("].forEach(id => {")[0].rsplit("[", 1)[-1]
        for container in ("weeklyReportBody", "insightList", "issueList", "evidenceRail"):
            self.assertIn(f'"{container}"', block, f"{container} 가 위임 목록에 없다")

    def test_chip_is_readable_on_light_panel(self):
        """히어로 칩은 어두운 배경 전용(흰 글자)이라 그대로 쓰면 안 보인다."""
        self.assertIn(".weekly-evidence .hero-evidence-chip", self.style)

    def test_chip_meets_mobile_tap_target(self):
        block = self.style.split(".weekly-evidence .hero-evidence-chip")[1].split("}")[0]
        self.assertIn("min-height: 44px", block)


class SystemStatusTests(unittest.TestCase):
    """수집기 heartbeat 와 브리핑 heartbeat 는 별개 신호다.

    '최신 기사 날짜'만으로 판정하면, 선정 하한 때문에 정상적으로 조용한 날을
    장애로 오판한다. 콘텐츠가 없는 것과 프로세스가 안 돈 것은 다르다.
    """

    NOW = build_data.datetime(2026, 8, 3, 22, 0, tzinfo=build_data.timezone.utc)

    def _records(self, hours_ago):
        stamp = (self.NOW - build_data.timedelta(hours=hours_ago)).isoformat()
        return [{"archived_at": stamp}]

    def _stats(self, hours_ago, status="ok"):
        stamp = (self.NOW - build_data.timedelta(hours=hours_ago)).isoformat()
        return {"2026-08-03": {"date": "2026-08-03", "pipeline_status": status,
                               "generated_at": stamp}}

    def test_healthy(self):
        out = build_data.system_status(self._records(1), self._stats(2), self.NOW)
        self.assertEqual(out["state"], "ok")
        self.assertTrue(out["watcher_running"])
        self.assertEqual(out["message"], "")

    def test_quiet_day_is_not_an_outage(self):
        """며칠째 새 기사가 없어도 브리핑이 돌았으면 정상이다."""
        out = build_data.system_status([], self._stats(2), self.NOW)
        self.assertEqual(out["state"], "ok")
        self.assertTrue(out["watcher_running"])

    def test_collector_stalled(self):
        out = build_data.system_status(self._records(12), self._stats(2), self.NOW)
        self.assertEqual(out["state"], "error")
        self.assertFalse(out["watcher_running"])

    def test_briefing_stalled_while_collector_alive(self):
        """수집은 도는데 브리핑만 멈춘 조합 — 가장 놓치기 쉬운 장애."""
        out = build_data.system_status(self._records(1), self._stats(50), self.NOW)
        self.assertEqual(out["state"], "ok")
        self.assertFalse(out["watcher_running"])
        self.assertIn("브리핑", out["message"])

    def test_pipeline_error_surfaces(self):
        out = build_data.system_status(self._records(1), self._stats(2, "error"),
                                       self.NOW)
        self.assertEqual(out["state"], "error")

    def test_last_success_is_last_ok_briefing_not_build_time(self):
        stats = {
            "2026-08-02": {"pipeline_status": "ok",
                           "generated_at": "2026-08-02T07:30:00+09:00"},
            "2026-08-03": {"pipeline_status": "error",
                           "generated_at": "2026-08-03T07:30:00+09:00"},
        }
        out = build_data.system_status(self._records(1), stats, self.NOW)
        self.assertEqual(out["last_success_at"], "2026-08-02T07:30:00+09:00")

    def test_no_stats_yet_does_not_claim_outage(self):
        """기능 도입 직후 — 통계가 아직 없다고 장애라고 하면 안 된다."""
        out = build_data.system_status(self._records(1), {}, self.NOW)
        self.assertEqual(out["state"], "ok")
        self.assertTrue(out["watcher_running"])


class EmptyBriefingStateTests(unittest.TestCase):
    """이슈 0건의 세 갈래가 app.js 에 실제로 들어 있는지 고정."""

    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_three_states_present(self):
        self.assertIn("브리핑 데이터가 아직 갱신되지 않았습니다", self.script)
        self.assertIn("오늘은 브리핑 기준을 넘는 이슈가 없습니다", self.script)
        self.assertIn("오늘 새로 확인된 브리핑 이슈가 없습니다", self.script)

    def test_hero_and_list_do_not_repeat_the_same_sentence(self):
        """히어로 h1 이 사유를 말하므로 목록은 '어디로 가면 되는가'만 담당한다."""
        self.assertIn('<div class="empty-state"><p>${view.detail}</p></div>', self.script)
        self.assertIn('document.getElementById("showChangedIssues").hidden = true;',
                      self.script)

    def test_below_floor_wording_is_candidate_not_collected(self):
        """below_floor_count 는 전체 수집 건수가 아니다 — '수집된 N건'은 거짓."""
        self.assertIn("검토한 후보 ${below}건", self.script)
        self.assertNotIn("수집된 ${below}건", self.script)

    def test_status_checked_before_declaring_quiet_day(self):
        self.assertIn("function pipelineTrouble()", self.script)
        self.assertIn("const trouble = pipelineTrouble();", self.script)

    def test_reuses_existing_view_switch_attribute(self):
        self.assertIn('data-go-view="search"', self.script)
        self.assertNotIn("data-goto-view", self.script)


if __name__ == "__main__":
    unittest.main()
