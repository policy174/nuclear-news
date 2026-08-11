import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest import mock
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
for _key in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_key, "test")
import news_bot  # noqa: E402

# 데이터 지표 게이트(추적률 등)를 건너뛴다. 배포 워크플로 전용이다.
#
# 왜 필요한가: 추적률은 "오늘 뉴스가 어떻게 묶였나"의 결과지 화면 코드의 성질이
# 아니다. 이걸 배포 경로에서 게이트로 쓰면 뉴스가 한산한 날에는 CSS 오타 수정도
# 배포가 막힌다(실측 2026-08-03: 0.125 로 deploy-web.yml 이 통째로 실패).
#
# 값을 낮춰 통과시키지 않는 이유: 0.20 은 한때 실제로 달성했던 수치이고
# (메모리 28.57%), 지금 0.125 인 건 Phase 0-B 병합 판정기가 미완이라는 신호다.
# 골대를 옮기면 그 신호가 사라진다. 게이트는 그대로 두고 **어디서 켜는지**만 나눈다.
#
#   crawl.yml · 로컬       → 켠다 (데이터 품질을 보는 자리)
#   deploy-web.yml         → 끈다 (화면 코드를 배포하는 자리)
SKIP_DATA_GATES = os.environ.get("NUCLENS_SKIP_DATA_GATES") == "1"


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_luminance(foreground), _luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _theme_tokens(css: str) -> dict[str, dict[str, str]]:
    """``:root`` 와 다크 블록의 색 토큰을 테마별로 갈라 읽는다.

    ``test_muted_text_meets_wcag_aa_on_paper`` 처럼 파일 전체를 ``dict()`` 로
    말면 나중 값이 앞을 덮어써 라이트 팔레트가 사라진다. 어두운 표면 위 대비는
    두 테마에서 서로 다른 배경을 쓰므로 양쪽 값이 다 필요하다.
    """
    blocks = {}
    for name, selector in (("light", ":root {"), ("dark", ':root[data-theme="dark"] {')):
        start = css.index(selector) + len(selector)
        body = css[start:css.index("}", start)]
        blocks[name] = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", body))
    # 다크는 라이트를 부분 덮어쓴다 — 짝이 없는 토큰은 라이트 값을 그대로 물려받는다.
    return {"light": blocks["light"], "dark": {**blocks["light"], **blocks["dark"]}}


class BrandAccessibilityTests(unittest.TestCase):
    def test_pretendard_variable_is_self_hosted(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        # 배포되는 것은 부분집합이다(2026-08-10) — 원본은 재생성용으로만 남는다.
        # 자체 호스팅·라이선스 동봉이라는 이 테스트의 목적은 그대로다.
        font = (
            ROOT
            / "public"
            / "fonts"
            / "pretendard"
            / "v1.3.9"
            / "PretendardVariable.subset.woff2"
        )
        license_file = font.with_name("OFL.txt")

        self.assertIn('@font-face', css)
        self.assertIn('font-family: "Pretendard Variable";', css)
        self.assertIn('font-weight: 45 920;', css)
        self.assertIn('font-display: swap;', css)
        self.assertIn(
            'url("fonts/pretendard/v1.3.9/PretendardVariable.subset.woff2")',
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

    def test_verification_badges_meet_wcag_aa_on_rail_head(self):
        """근거 패널 머리말은 두 테마 모두 --c-primary(딥 포레스트)다.

        배지 색은 밝은 배경을 전제로 정해져 있어 그대로 넘어오면 네 상태가
        모두 AA 에 못 미쳤다(공식 확인 2.76:1, 확인 중 2.69:1). 위 종이 대비
        검사는 --c-text-muted / --c-bg 한 쌍만 보므로 이 조합은 걸리지 않는다.
        네 상태 × 두 테마 여덟 조합을 전부 센다.
        """
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        themes = _theme_tokens(css)

        for status in build_data.VERIFICATION_LABELS:
            rule = re.search(
                rf"\.rail-badges\s+\.verification-badge\.v-{status}\b[^{{}}]*\{{([^}}]*)\}}",
                css,
            )
            self.assertIsNotNone(rule, f"rail 전용 오버라이드 없음: .v-{status}")
            color = re.search(r"color:\s*(?:var\(--([\w-]+)\)|(#[0-9a-fA-F]{6}))", rule.group(1))
            self.assertIsNotNone(color, f"rail .v-{status} 규칙에 color 선언 없음")

            for theme, tokens in themes.items():
                token, literal = color.groups()
                foreground = literal or tokens.get(token)
                self.assertIsNotNone(foreground, f"{theme}: 미정의 토큰 --{token}")
                ratio = _contrast(foreground, tokens["c-primary"])
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f"{theme} rail .v-{status}: {foreground} on {tokens['c-primary']}"
                    f" = {ratio:.2f}:1",
                )

    def test_verification_states_stay_distinct_without_color(self):
        """색을 걷어내도 4단계가 남아야 한다(WCAG 1.4.1).

        rail 에서는 네 상태가 색 두 가지로 묶이므로(확인 계열 / 미확인 계열)
        구분을 지는 것은 기호와 문구다. 배지 마크업이 둘을 함께 싣는지 본다.
        """
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        views = dict(
            re.findall(r"(\w+):\s*\{\s*mark:\s*\"([^\"]+)\"", app)
        )

        for status, label in build_data.VERIFICATION_LABELS.items():
            self.assertTrue(views.get(status), f"VERIFICATION_VIEW.{status} 에 mark 없음")
            self.assertIn(f'label: "{label}"', app)

        # 기호는 겹칠 수 있다(공식·복수 출처 모두 ✓). 그때 구분을 지는 건 문구다.
        self.assertEqual(
            len(set(build_data.VERIFICATION_LABELS.values())),
            len(build_data.VERIFICATION_LABELS),
        )
        self.assertRegex(
            app, r'class="verification-badge v-\$\{esc\(state\.status\)\}"[^`]*'
                 r'\$\{view\.mark\}\s\$\{esc\(view\.label\)\}',
        )

    def test_rendered_text_has_12_5px_minimum(self):
        """12.5px 하한. 예외는 두지 않는다.

        ``font-size:`` 만 보면 ``font: 11px var(--ff-base)`` 축약형이 통째로
        빠져나간다 — 통합 시안이 정체성을 9~11px 모노 오버라인에 걸어둔 탓에
        이 구멍으로 40건 중 17건이 검사를 우회할 수 있었다. 오버라인은 크기가
        아니라 굵기·자간·색으로 구분한다.

        타입 토큰(--t-*) 도입 후에는 리터럴만 봐서는 안 된다 — 크기가
        ``var(--t-*)`` 로 우회하면 리터럴 검사가 공허해진다. 토큰 정의값의
        px 와 clamp() 최소값까지 같은 하한으로 검사한다.
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
        # 타입 토큰 정의값 — 고정 px 와 clamp 최소값(첫 인자) 둘 다.
        token_px_sizes = [
            float(value)
            for value in re.findall(r"--t-[\w-]+:\s*(\d+(?:\.\d+)?)px\s*;", css)
        ]
        token_clamp_min_sizes = [
            float(value)
            for value in re.findall(
                r"--t-[\w-]+:\s*clamp\(\s*(\d+(?:\.\d+)?)px", css
            )
        ]
        self.assertTrue(token_px_sizes, "--t-* px 토큰 정의가 없다")
        self.assertTrue(token_clamp_min_sizes, "--t-* clamp 토큰 정의가 없다")
        too_small = [
            size
            for size in css_sizes + shorthand_sizes + inline_svg_sizes
            + token_px_sizes + token_clamp_min_sizes
            if size < 12.5
        ]
        self.assertEqual(too_small, [], f"12.5px 미만 글자 크기: {too_small}")
        self.assertIn("--t-min: 12.5px", css)
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


class OfficialDirectSourceTests(unittest.TestCase):
    def test_khnp_board_fixture_uses_the_official_detail_url(self):
        page = '''<li class="p-media"><a href="./selectBbsNttView.do?key=2289&amp;bbsNo=71&amp;nttNo=7">
          <em class="p-media__heading-text title"><span>보도</span> 한수원 공식 발표</em>
          <p class="txt">발표 본문</p><time>2026-08-07</time></a></li>'''
        rows = news_bot.parse_khnp_board(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "한수원 공식 발표")
        self.assertIn("khnp.co.kr/main/selectBbsNttView.do", rows[0]["link"])
        self.assertEqual(rows[0]["publisher_domain"], "khnp.co.kr")

    def test_kaeri_board_fixture_uses_the_official_detail_url(self):
        page = '''<li class="item"><a href="/board/view?linkId=9&amp;menuId=MENU00326">
          <strong>원자력연 공식 발표</strong><span class="desc">발표 본문</span></a>
          <dl><dd>2026.08.06</dd></dl></li>'''
        rows = news_bot.parse_kaeri_board(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "원자력연 공식 발표")
        self.assertIn("kaeri.re.kr/board/view", rows[0]["link"])

    def test_nssc_json_fixture_is_primary_and_has_a_stable_view_url(self):
        rows = news_bot.parse_nssc_rows([{
            "BBS_SEQ": 47015, "SUBJECT": "원안위원장, 원전 현장 점검",
            "CONTENTS": "<p>점검 내용</p>", "WRITE_DATE": "2026.08.06",
        }])
        self.assertEqual(len(rows), 1)
        self.assertIn("BBS_SEQ=47015", rows[0]["link"])
        self.assertEqual(rows[0]["publisher"], "원자력안전위원회")
        self.assertTrue(news_bot.is_tier1_source({
            "domain": rows[0]["publisher_domain"], "publisher": rows[0]["publisher"]
        }))

    def test_all_four_domestic_official_sources_bypass_google_news(self):
        self.assertEqual(len(news_bot.OFFICIAL_DIRECT_SOURCES), 4)
        self.assertTrue(all("news.google." not in source["url"] for source in news_bot.OFFICIAL_DIRECT_SOURCES))
        self.assertEqual(
            {source["domain_label"] for source in news_bot.OFFICIAL_DIRECT_SOURCES},
            {"khnp.co.kr", "nssc.go.kr", "motir.go.kr", "kaeri.re.kr"},
        )

    def test_google_link_replacement_requires_title_publisher_and_domain(self):
        google = {
            "link": "https://news.google.com/rss/articles/x", "title": "원전 계획 발표",
            "publisher": "원자력안전위원회", "domain": "nssc.go.kr",
        }
        direct = {
            "link": "https://www.nssc.go.kr/ko/cms/FR_BBS_CON/BoardView.do?BBS_SEQ=1",
            "title": "원전 계획 발표", "publisher": "원자력안전위원회", "domain": "nssc.go.kr",
        }
        self.assertTrue(news_bot.canonical_replacement_allowed(google, direct))
        for field, value in (("title", "다른 발표"), ("publisher", "다른 기관"), ("domain", "example.com")):
            changed = {**direct, field: value}
            self.assertFalse(news_bot.canonical_replacement_allowed(google, changed), field)

    def test_zero_yield_official_sources_are_recorded_in_state_and_warned(self):
        """게시판이 죽으면 예외 없이 0건이 된다. state 기록과 경고가 유일한 신호다."""
        state = {"sent": {}}
        buffer = io.StringIO()
        with mock.patch.object(news_bot, "RSS_SOURCES", []), \
             mock.patch.object(news_bot, "fetch_official_direct", return_value=[]), \
             contextlib.redirect_stdout(buffer):
            news_bot.collect_rss_articles(state)

        yields = state["source_yield"]
        self.assertEqual(
            set(yields["counts"]),
            {source["name"] for source in news_bot.OFFICIAL_DIRECT_SOURCES},
        )
        self.assertEqual(set(yields["counts"].values()), {0})
        self.assertEqual(yields["kept"], {})
        self.assertTrue(yields["at"])
        self.assertIn("::warning title=공식기관 직접 수집 0건", buffer.getvalue())
        for source in news_bot.OFFICIAL_DIRECT_SOURCES:
            self.assertIn(source["name"], buffer.getvalue())

    def test_official_boards_use_a_wider_cutoff_than_rss(self):
        """게시판은 날짜만 준다. RSS 와 같은 24시간 창을 쓰면 당일 게시물만 잡힌다."""
        self.assertGreater(news_bot.OFFICIAL_LOOKBACK_DAYS * 24, news_bot.LOOKBACK_HOURS * 4)

        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        board_item = {
            "title": "원안위, 원전 정기검사 결과 공개",
            "description": "검사 결과 본문",
            "link": "https://www.nssc.go.kr/ko/cms/FR_BBS_CON/BoardView.do?BBS_SEQ=1",
            "pub": three_days_ago,
            "publisher": "원자력안전위원회",
            "publisher_domain": "nssc.go.kr",
        }
        state = {"sent": {}}
        with mock.patch.object(news_bot, "RSS_SOURCES", []), \
             mock.patch.object(news_bot, "fetch_official_direct", return_value=[board_item]), \
             contextlib.redirect_stdout(io.StringIO()):
            articles = news_bot.collect_rss_articles(state)

        self.assertTrue(articles, "3일 전 공식 보도자료가 cutoff 에서 떨어졌다")
        self.assertEqual(articles[0]["link"], board_item["link"])
        self.assertEqual(set(state["source_yield"]["kept"].values()), {1})


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


class TodayAgendaContractTests(unittest.TestCase):
    def test_today_agenda_is_a_decision_screen_not_a_title_index(self):
        """오늘 3분은 결론과 다음 확인만 담는다.

        제목 목차였을 때 두 가지가 깨졌다. ① 같은 제목이 목차와 카드에 두 번
        나갔다. ② 목차는 필터·정렬 이전의 briefing.issues 를 슬라이스하는데 카드는
        그 뒤에 걸러지고 다시 정렬돼서, 필터가 걸리면 목차 03 과 카드 03 이 서로
        다른 이슈였고 앵커가 DOM 에 없는 카드를 가리키기도 했다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        agenda = script.split("function renderTodayAgenda(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("issue-card-${esc(issue.issue_id)}", agenda, "목차 앵커가 돌아왔다")
        self.assertNotIn("issue.title", agenda, "카드 제목을 다시 나열한다")
        self.assertIn("policy_shifts", agenda)
        self.assertIn("watchpoints", agenda)
        # 카드에 이미 있는 문장은 걸러서 낸다.
        self.assertIn("dropTextsAlreadyOnCards", agenda)
        for element_id in ("agendaConclusions", "agendaConclusionList", "agendaWatch", "agendaWatchList"):
            self.assertIn(f'id="{element_id}"', html)

    def test_standard_card_title_is_two_lines_on_desktop(self):
        """제목이 유일한 '무슨 일'이 된 뒤로 한 줄 말줄임은 사건을 지운다."""
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".issue-card:not(.front) .issue-title-button", style)
        rule = style.split(".issue-card:not(.front) .issue-title-button", 1)[1].split("}", 1)[0]
        self.assertIn("-webkit-line-clamp: 2", rule)
        self.assertNotIn("white-space: nowrap", rule)


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

    def test_same_mou_rewording_is_a_restatement(self):
        before = "한수원이 필리핀 아보이티즈파워와 원자력 기술 협력을 위한 양해각서(MOU)를 체결했다."
        after = "한국수력원자력은 아보이티즈 파워와 원전 사업 협력을 위한 MOU를 체결했다."
        self.assertTrue(build_data._is_restatement(before, after))

    def test_same_reactor_approval_rewording_is_a_restatement(self):
        before = "중국 정부가 화룽1호 6기와 궈허1호 2기 등 총 8기 원자로 건설을 승인했다."
        after = "중국 정부가 신규 원자로 8기 건설을 공식 승인했다."
        self.assertTrue(build_data._is_restatement(before, after))

    def test_same_criticality_event_rewording_is_a_restatement(self):
        before = "오클로의 그로브스 동위원소 시험로가 원자로 파일럿 프로그램에서 5번째로 임계에 도달했다."
        after = "오클로가 그로브스 동위원소 시험로에서 첫 임계를 달성했다고 발표했다."
        self.assertTrue(build_data._is_restatement(before, after))

    def test_same_scheduled_review_rewording_is_a_restatement(self):
        before = "원안위가 고리 3·4호기 계속운전 심의를 올해 하반기에 진행할 예정이다."
        after = "고리 3·4호기는 올해 원안위 계속운전 심사에 상정될 예정이다."
        self.assertTrue(build_data._is_restatement(before, after))

    def test_tentative_to_final_decision_is_not_a_restatement(self):
        before = "원안위가 신규 원전 건설 허가를 검토할 예정이다."
        after = "원안위가 신규 원전 건설 허가를 최종 의결했다."
        self.assertFalse(build_data._is_restatement(before, after))

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

    # ── change_display: 카드 표시 전용 필드 (2026-08-04) ──────────────
    # 화살표 문장의 뒤쪽(B)은 현재 요약으로 만들어져 카드의 제목·둘째 줄과
    # 구조적으로 겹친다(실측: 8/4 브리핑 8건 중 2건 summary 포함률 1.00).
    # latest_change 원본은 changed_issue_count·RSS 가 세므로 그대로 두고,
    # 카드는 이 필드를 쓴다.

    def test_card_display_folds_arrow_tail_that_restates_the_summary(self):
        summary = "헝가리 총리가 다뉴브강의 낮은 수위로 원자력 발전소 가동이 중단될 수 있다고 경고했다."
        change = f"다뉴브강 수위가 역대 최저치를 기록했습니다. → {summary}"
        shown = build_data.card_change_display(
            change, "헝가리, 가뭄으로 팍스 원전 가동 중단 위기", "", summary)
        self.assertNotIn("→", shown)
        # 라벨은 문장에 섞지 않는다 — 화면이 change_kind 를 보고 고른다.
        self.assertNotIn("직전 브리핑", shown)
        self.assertIn("역대 최저치", shown)

    def test_a_previous_state_line_is_labelled_as_one(self):
        """라이브 실측(2026-08-10) 10/160: '달라진 것' 라벨 아래 **바뀌기 전** 상태만
        서 있었다. 훑어보는 사람이 옛 상태를 오늘 일로 읽는다. 문장이 어느 쪽인지를
        데이터가 말해야 화면이 라벨을 고를 수 있다.
        """
        summary = "그리스 정부가 SMR 도입 타당성 검토를 위한 국가 전담 연구그룹을 신설했다."
        rows = [{
            "title": "그리스, SMR 도입 타당성 검토 위한 국가 전담 연구그룹 신설",
            "latest_change": f"그리스 국무회의는 SMR 잠재력 탐색을 위한 범부처 위원회를 구성했다 → {summary}",
        }]
        build_data.finalize_card_fields(rows)
        self.assertEqual(rows[0]["change_kind"], "previous")
        self.assertIn("범부처 위원회", rows[0]["change_display"])

        # 화살표가 통째로 남는 날은 그대로 '달라진 것'이다.
        rows = [{"title": "관련 없는 제목",
                 "latest_change": "가동을 멈췄다 → 재가동을 승인받았다"}]
        build_data.finalize_card_fields(rows)
        self.assertEqual(rows[0]["change_kind"], "change")

    def test_the_front_end_picks_the_label_from_change_kind(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('issue.change_kind === "previous" ? "직전까지"', script)
        # 라벨을 세우는 세 자리가 전부 이 헬퍼를 거쳐야 한 곳만 고쳐도 안 갈라진다.
        self.assertEqual(script.count("issueChangeLabel(issue,"), 3)

    def test_card_display_empties_when_both_sides_restate(self):
        summary = ("독일이 2040년대 유럽 최초의 상업용 핵융합 발전소 운영을 "
                   "목표로 3개의 국가 허브 계획을 발표했다.")
        before = ("독일이 2040년대 유럽 최초의 상업용 핵융합 발전소 운영을 "
                  "목표로 계획을 발표했다")
        shown = build_data.card_change_display(
            f"{before} → {summary}", "독일 핵융합 국가 허브", "", summary)
        self.assertEqual(shown, "")

    def test_card_display_keeps_arrow_with_genuinely_new_tail(self):
        change = "원안위가 심사를 시작했다. → 한수원이 체코 두코바니 신규 원전 본계약에 서명했다."
        shown = build_data.card_change_display(
            change, "신한울 3호기 인허가", "국내 인허가 일정의 분수령이다",
            "원안위가 신한울 3호기 건설 허가 심사를 진행 중이다.")
        self.assertEqual(shown, change)

    def test_card_display_passes_non_arrow_lines_through(self):
        self.assertEqual(build_data.card_change_display("", "t", "i", "s"), "")
        self.assertEqual(
            build_data.card_change_display("새 부지 조사가 시작됐다.", "t", "i", "s"),
            "새 부지 조사가 시작됐다.")


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
        self.assertEqual(state["label"], "공식 원문 포함")
        self.assertEqual(state["official_source_count"], 1)
        self.assertEqual(state["checked_at"], "2026-08-01T09:00:00+09:00")
        self.assertEqual(state["checks"][0]["kind"], "official")
        self.assertTrue(state["checks"][0]["passed"])

    def test_two_independent_publishers_are_corroborated(self):
        state = build_data.verification_state([
            self._article("h1", "로이터"), self._article("h2", "연합뉴스"),
        ])
        self.assertEqual(state["status"], "corroborated")
        self.assertEqual(state["independent_source_count"], 2)
        self.assertEqual(state["checks"][1]["kind"], "multi")
        self.assertTrue(state["checks"][1]["passed"])

    def test_verification_labels_describe_evidence_without_claiming_agreement(self):
        """한 이슈의 타임라인은 관련 사건도 품으므로 출처 수를 '주장 일치'라 부르지 않는다."""
        self.assertEqual(build_data.VERIFICATION_LABELS["official"], "공식 원문 포함")
        self.assertEqual(build_data.VERIFICATION_LABELS["corroborated"], "독립 출처 2곳+")
        state = build_data.verification_state([
            self._article("h1", "로이터"), self._article("h2", "연합뉴스"),
        ])
        self.assertEqual(build_data.verification_state([
            self._article("official", "원안위", evidence_role="primary", source_type="official"),
        ])["checks"][0]["label"], "공식 원문 포함")
        self.assertEqual(state["checks"][1]["label"], "독립 출처 2곳 이상 연결")

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

    def test_unselected_article_attaches_as_evidence_without_creating_a_card_issue(self):
        cards = [
            {
                "hash": "card-a", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "한수원 체코 원전 본계약 후속 절차 착수",
                "summary": "한수원이 체코 원전 본계약 후속 절차에 착수했다.",
                "tags": ["#체코원전"], "topics": ["newbuild"], "countries": ["KR", "CZ"],
            },
            {
                "hash": "card-b", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "미국 NRC 신규 규제 지침 공개",
                "summary": "미국 NRC가 신규 규제 지침을 공개했다.",
                "tags": ["#NRC"], "topics": ["regulation"], "countries": ["US"],
            },
        ]
        evidence = {
            "hash": "evidence-a", "article_date": "2026-08-07",
            "title_kr": "체코 원전 본계약 후속 일정 발표",
            "summary": "체코 원전 본계약의 후속 일정이 발표됐다.",
            "tags": ["#체코원전"], "topics": ["newbuild"], "countries": ["KR", "CZ"],
            "importance": "standard",
        }
        embeddings = {
            "card-a": [1.0, 0.0], "card-b": [0.0, 1.0], "evidence-a": [0.99, 0.01],
        }
        issues = build_data.cluster_selected_articles(cards, embeddings)
        p0_snapshot = build_data.card_cluster_snapshot(issues)
        before = [[m["hash"] for m in issue["members"]] for issue in issues]
        attached = build_data.attach_evidence_articles(cards + [evidence], issues, embeddings)
        self.assertEqual(attached, 1)
        self.assertEqual([[m["hash"] for m in issue["members"]] for issue in issues], before)
        guard = build_data.assert_card_clusters_unchanged(p0_snapshot, issues)
        self.assertTrue(guard["passed"])
        self.assertEqual(guard["card_count"], 2)
        self.assertEqual(len(issues), 2)
        target = next(issue for issue in issues if issue["issue_id"] == "issue-card-a")
        self.assertEqual([m["hash"] for m in target["evidence_members"]], ["evidence-a"])

    def test_same_run_guard_fails_if_card_membership_changes(self):
        cards = [
            {
                "hash": "card-a", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "원전 A 건설 승인", "tags": ["#원전A"],
                "topics": ["newbuild"], "countries": ["KR"],
            },
            {
                "hash": "card-b", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "원전 B 운영 허가", "tags": ["#원전B"],
                "topics": ["regulation"], "countries": ["US"],
            },
        ]
        issues = build_data.cluster_selected_articles(cards)
        p0_snapshot = build_data.card_cluster_snapshot(issues)
        issues[0]["members"].append(issues[1]["members"][0])
        with self.assertRaisesRegex(ValueError, "p1_card_cluster_regression"):
            build_data.assert_card_clusters_unchanged(p0_snapshot, issues)

    def test_evidence_article_cannot_bridge_two_card_clusters(self):
        cards = [
            {
                "hash": "left", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "원전 금융 지원 제도 A", "tags": ["#금융A"],
                "topics": ["finance"], "countries": ["US"],
            },
            {
                "hash": "right", "briefing_date": "2026-08-08", "article_date": "2026-08-08",
                "title_kr": "원전 금융 지원 제도 B", "tags": ["#금융B"],
                "topics": ["finance"], "countries": ["FR"],
            },
        ]
        bridge = {
            "hash": "bridge", "article_date": "2026-08-07",
            "title_kr": "원전 금융 지원 제도 후속", "tags": ["#금융A", "#금융B"],
            "topics": ["finance"], "countries": ["US"], "importance": "standard",
        }
        pair = build_data._pair_id
        overrides = {
            "approved": {pair("left", "bridge"), pair("right", "bridge")},
            "rejected": set(), "llm_approved": set(), "llm_rejected": set(),
        }
        issues = build_data.cluster_selected_articles(cards)
        build_data.attach_evidence_articles(cards + [bridge], issues, None, None, overrides)
        self.assertEqual(len(issues), 2)
        self.assertEqual(sorted(len(issue["members"]) for issue in issues), [1, 1])
        self.assertEqual(sum(len(issue.get("evidence_members") or []) for issue in issues), 1)


class RenderSmokeContractTests(unittest.TestCase):
    """렌더 스모크 자체의 계약 — 빌드 산출물 없이도 도는 정적 검사.

    스모크가 조용히 무의미해지는 실패(없는 id 참조, 오지 않는 networkidle 대기,
    스켈레톤 오검출)는 라이브가 멀쩡해도 CI 를 상시 빨갛게 만들거나 반대로
    깨진 화면을 통과시킨다. 그래서 데이터와 무관하게 항상 검사한다.
    """

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

    def test_render_smoke_does_not_wait_for_network_idle(self):
        """`networkidle` 은 이 앱에서 오지 않을 수 있다 — 기다리면 CI 가 죽는다.

        앱은 60초 주기로 meta.json 을 폴링하고(checkForNewGeneration) 폰트·오디오
        등 부가 요청이 물려 있어 "네트워크가 조용해지는 순간"이 보장되지 않는다.
        2026-08-04 daily-brief 가 page.goto(waitUntil:"networkidle") 60초 타임아웃으로
        연속 실패했다(라이브 화면은 멀쩡했다). 로드 완료는 네트워크가 아니라
        렌더러 출력 노드로 판정한다.
        """
        smoke = (ROOT / "tests" / "render_smoke.mjs").read_text(encoding="utf-8")
        # 주석에는 "networkidle 을 쓰지 마라"가 적혀 있어야 하므로 코드만 검사한다.
        code = "\n".join(re.sub(r"//.*", "", line) for line in smoke.splitlines())
        self.assertNotIn("networkidle", code)
        self.assertIn('waitUntil: "domcontentloaded"', code)
        self.assertIn("waitForFunction", code)
        self.assertIn("skeleton-list", code)

    def test_render_smoke_ignores_skeleton_cards(self):
        """#issueList 에는 index.html 이 박아 둔 스켈레톤 카드가 있다.

        그대로 세면 renderBriefing 이 죽어도 article 이 잡혀 스모크가 통과한다 —
        '정적 마크업이 든 컨테이너를 검사하면 공허하다'는 이 파일의 원칙 그대로다.
        """
        smoke = (ROOT / "tests" / "render_smoke.mjs").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("skeleton-card", html)
        self.assertIn("#issueList article:not(.skeleton-card)", smoke)


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

    def test_card_slots_never_repeat_the_same_sentence(self):
        """제목 · 달라진 것 · 왜 중요해요 · 다음 확인은 서로 다른 말을 해야 한다.

        의미 중복은 문자열로 못 잡는다(어순·어미만 바꾸면 유사도가 0.32까지
        떨어진다). 여기서 막는 것은 **정확히 같은 문장**뿐이다 — 스펙의
        '먼저 정확히 동일한 문장을 차단하고, 의미 중복은 수동 검토' 그대로다.
        """
        for briefing in self.briefings:
            for issue in briefing.get("issues") or []:
                slots = {
                    "title": issue.get("title"),
                    "change_display": issue.get("change_display"),
                    "card_why": issue.get("card_why"),
                    "open_question": issue.get("open_question"),
                }
                seen: dict[str, str] = {}
                for name, text in slots.items():
                    text = str(text or "").strip()
                    if len(text) < 20:
                        continue
                    self.assertNotIn(
                        text, seen,
                        f"{briefing['date']} {issue.get('issue_id')}: "
                        f"{seen.get(text)} 와 {name} 이 같은 문장이다",
                    )
                    seen[text] = name

    def test_card_why_is_a_single_build_owned_field(self):
        """화면이 or 폴백을 쌓으면 역할 분리 계약이 두 곳으로 흩어진다."""
        rows = [row for briefing in self.briefings for row in (briefing.get("issues") or [])]
        rows += self.issue_catalog
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("card_why", row, f"{row.get('issue_id')} 에 card_why 가 없다")
            self.assertIn("change_display", row)

    def test_every_delivered_article_is_represented_once_in_its_briefing(self):
        """편집자가 내린 이슈는 빼고 센다.

        `selection_overrides.json` 의 `hide_from_today` 는 문서화된 편집 도구다
        ("GitHub 웹에서 직접 편집하면 다음 빌드부터 반영된다"). 그런데 이 검사가
        발송분과 화면분을 1:1 로 못 박고 있어서 **그 도구를 쓰면 빌드가 깨졌다** —
        2026-08-11 에 실제로 그랬다(날조 카드 하나를 내리자 16 != 17). 배포 경로에
        데이터 조건이 사는 것을 라운드 5 에서 두 번 걷어냈는데 여기 하나가 더 있었다.
        """
        hidden = set(build_data.load_selection_overrides()["demote"])
        for briefing in self.briefings:
            delivered = [article for article in self.news
                         if article.get("briefing_date") == briefing["date"]]
            expected = sum(1 for article in delivered
                           if (article.get("hash", "")[:8], briefing["date"]) not in hidden)
            current = sum(issue["current_article_count"] for issue in briefing["issues"])
            self.assertEqual(briefing["article_count"], expected)
            self.assertEqual(current, expected)

    def test_global_issue_catalog_contains_each_delivered_article_once(self):
        delivered_hashes = [article["hash"] for article in self.news if article.get("briefing_date")]
        catalog_hashes = [
            article["hash"]
            for issue in self.issue_catalog
            for article in issue["related_articles"]
            if article.get("member_role") == "card"
        ]
        evidence_hashes = [
            article["hash"]
            for issue in self.issue_catalog
            for article in issue["related_articles"]
            if article.get("member_role") == "evidence"
        ]
        self.assertEqual(len(self.issue_catalog), self.meta["issue_catalog_total"])
        self.assertEqual(len({issue["issue_id"] for issue in self.issue_catalog}), len(self.issue_catalog))
        self.assertCountEqual(catalog_hashes, delivered_hashes)
        self.assertEqual(len(catalog_hashes), len(set(catalog_hashes)))
        self.assertTrue(evidence_hashes)
        self.assertFalse(set(evidence_hashes) & set(delivered_hashes))
        self.assertEqual(len(evidence_hashes), len(set(evidence_hashes)))

    def test_p1_keeps_the_p0_latest_briefing_and_weekly_order(self):
        baseline = json.loads((ROOT / "tests" / "p0_baseline_2026-08-08.json").read_text(encoding="utf-8"))
        # 날짜별 파일은 사람이 검토한 P0 감사 기록이다. 자동 회귀 가드는 매 빌드에서
        # P1 직전·직후 스냅샷을 직접 비교하므로 수집 데이터가 갱신되어도 같은 입력
        # 세대의 카드 순서·소속·대표 제목 변경을 놓치지 않는다.
        for key in ("date", "issue_ids", "cards", "weekly_mover_ids"):
            self.assertIn(key, baseline)
        guard = self.meta["p1_regression"]
        self.assertTrue(guard["passed"])
        self.assertEqual(guard["definition_version"], "same-run-card-cluster-v1")
        self.assertEqual(guard["card_count"], self.meta["issue_catalog_total"])
        self.assertRegex(guard["signature"], r"^[0-9a-f]{16}$")

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

    def test_topic_coverage_measures_curated_articles_only(self):
        """분류율은 큐레이션을 받은 기사에 대해서만 잰다.

        2026-08-06: 429(RPM)로 한 배치가 통째로 미큐레이션 상태로 들어와 표시
        393건 중 무분류 41건이 됐고 topic_coverage 0.8957 로 배포가 막혔다.
        그런데 41건 중 37건은 큐레이션을 아예 못 받은 fallback 껍데기였고 진짜
        분류 실패는 4건이었다. 분모에 섞으면 큐레이션 장애가 분류기 버그로 읽힌다.
        """
        curated = [item for item in self.news if item.get("curated")]
        self.assertTrue(curated, "큐레이션된 기사가 하나도 없다")
        expected = sum(1 for item in curated if item["topics"]) / len(curated)
        self.assertAlmostEqual(self.meta["topic_coverage"], round(expected, 4), places=4)

    def test_uncurated_articles_are_counted_not_hidden(self):
        """미큐레이션은 분모에서 빼되 **센다**. 안 세면 조용히 사라진다."""
        self.assertIn("uncurated_count", self.meta)
        actual = sum(1 for item in self.news if not item.get("curated"))
        self.assertEqual(self.meta["uncurated_count"], actual)

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
        # 4주 주제 변화는 흐름 탭이 주인이다 — 오늘 화면에 같은 표를 두면 같은
        # 숫자가 두 탭에 뜬다. 오늘은 '무슨 일', 흐름은 '어느 방향'.
        self.assertNotIn('id="homeTopicFlow"', html)
        self.assertIn('id="trendTopicFlowRows"', html)
        self.assertIn("Nuclens는 제목·요약·출처 링크만 제공합니다.", html)
        self.assertIn("분석 기간 ${dateLabel(start)}–${dateLabel(end)}", script)
        self.assertIn("중복 제거 적용 · 원본 ${articleCount}건 → 연결 이슈 ${issueCount}개", script)

        issue_card = script.split("function issueCard", 1)[1].split("function renderBriefingSidebar", 1)[0]
        self.assertIn("verificationBadge(issue)", issue_card)
        # 근거 줄은 '타임라인 N' 버튼과 같은 숫자를 반복해 제거했다. 출처 구성은
        # 상세에만 남는다.
        self.assertNotIn("issueEvidenceText(issue)", issue_card)
        # '변화' 줄(= 직전 브리핑 문장)도 뺐다. 사용자 지적(2026-08-05):
        # "직전 브리핑 내용이 왜 들어가, 그럴거면 그 전꺼를 보겠지 당연히."
        # 카드가 답할 것은 '이 뉴스가 무슨 뜻인가'다 — 그 자리는 issue_insight 가
        # 이슈 타임라인으로 채운다. 지난 문장은 상세의 사건 타임라인에 그대로 있다.
        self.assertNotIn('class="issue-change"', issue_card)
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

    def test_latest_issue_detail_uses_one_canonical_record_across_entry_paths(self):
        """검색·오늘·탐색에서 같은 issue_id를 열면 최신 누적 근거가 같아야 한다."""
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        resolver = script.split("function currentIssueById", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("const catalogIssue", resolver)
        self.assertIn("latest_briefing_date", resolver)
        self.assertIn("return catalogIssue || briefingIssue", resolver)
        display = script.split("function briefingIssuesForDisplay", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("state.issues.find", display)
        render = script.split("function renderBriefing()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("briefingIssuesForDisplay(briefing).filter", render)

    def test_skip_link_moves_keyboard_focus_to_main(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<main id="main" class="wrap page-shell" tabindex="-1">', html)
        binding = script.split("function bind()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('document.querySelector(".skip-link")', binding)
        self.assertIn('main.focus({ preventScroll: true })', binding)

    def test_audio_brief_player_is_wired(self):
        """오디오 브리핑 — 마크업·배속·비치명 로드·날짜 대조가 맞물려 있는지.

        음원은 1.0x 원본 하나뿐이고 배속은 playbackRate 가 맡는다(2026-08-04
        박제: 출근길 청취). audio/audio.json 이 없으면 플레이어만 숨어야 한다
        — publications 와 같은 비치명 계약.
        """
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for element_id in ("audioBrief", "audioToggle", "audioRates", "audioTime", "audioEl"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('loadRootJSON("audio/audio.json", true)', script)
        self.assertIn("const AUDIO_RATES = [1, 1.25, 1.5, 2]", script)
        self.assertIn("nuclens-audio-rate", script)
        self.assertIn("playbackRate", script)
        # 배속은 순환 버튼이 아니라 선택지 전부 펼친 세그먼트(사용자 피드백 8/5)
        for rate in ("1", "1.25", "1.5", "2"):
            self.assertIn(f'data-rate="{rate}"', html)
        self.assertIn("syncAudioRateButtons", script)
        # 날짜가 다른 브리핑에서는 숨는다 — renderBriefing 모든 경로에서 판정
        self.assertIn("renderAudioBrief(briefing)", script)
        self.assertIn("meta.date === briefing.date", script)
        # 모바일이 본 무대 — hero-actions 처럼 숨기지 말고 44px 터치 타깃
        self.assertIn(".hero-audio button { min-height: 44px; }", style)
        self.assertNotIn(".hero-audio { display: none", style)

    def test_card_change_display_is_wired_over_latest_change(self):
        """카드·상세·복사의 단일 접점(issueChangeText)이 표시 전용 필드를 우선한다.

        undefined/"" 구분 필수 — 빌드가 의도적으로 비운 변화 문장이
        latest_change 폴백으로 되살아나면 재진술 게이트가 무효가 된다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("issue.change_display !== undefined", script)

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

    def test_p4_brief_pages_have_unique_open_graph_metadata_and_boot_date(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        briefings = json.loads((DATA_DIR / "briefings.json").read_text(encoding="utf-8"))
        brief_root = ROOT / "public" / "brief"
        pages = list(brief_root.glob("*/index.html"))
        self.assertEqual(len(pages), len(briefings))
        self.assertIn("const BRIEF_ROUTE", script)
        self.assertIn("briefDateFromLocation() || params.get(\"date\")", script)
        for briefing in briefings:
            day = briefing["date"]
            page = brief_root / day / "index.html"
            self.assertTrue(page.exists(), day)
            page_html = page.read_text(encoding="utf-8")
            brief_url = f"https://nuclens.pages.dev/brief/{day}"
            self.assertIn(f'<link rel="canonical" href="{brief_url}">', page_html)
            self.assertIn(f'<meta property="og:url" content="{brief_url}">', page_html)
            self.assertIn('<meta property="og:type" content="article">', page_html)
            self.assertIn('"@type":"Report"', page_html)
            self.assertIn('content="noindex,nofollow"', page_html)

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
        self.assertTrue(self.issue_audit["clusters"])
        self.assertTrue(all(cluster["matches"] for cluster in self.issue_audit["clusters"]))

    @unittest.skipIf(SKIP_DATA_GATES, "배포 경로에서는 데이터 지표를 게이트로 쓰지 않는다")
    def test_tracking_rate_meets_target(self):
        """이슈 추적률 게이트. **배포가 아니라 데이터 품질을 보는 자리다.**

        원격 Gemini 임베딩이 있는 빌드에만 적용한다 — 로컬 폴백 벡터는 병합이
        보수적이라 구조적으로 낮게 나온다(환경 차이지 코드 결함이 아니다).

        **최신 브리핑 하나가 아니라 최근 7회차 누적으로 잰다.** 2026-08-03 에
        하루치로 재던 이 게이트가 0.125 로 죽었는데, 같은 날 17일 전부를 재보니
        8일이 0.000 이고 ≥0.20 은 10일(59%)에서 실패했다. 분모가 이슈 8개라
        1건 차이로 0.125 씩 튄다 — 그 값은 병합기가 아니라 **그날 뉴스가 한산했나**
        를 말한다. 누적 7일 0.193 / 14일 0.120 이 병합기의 실제 성질이다.

        임계값 0.20 은 그대로다. 내려 통과시키지 말 것 — 그러면 신호가 사라진다.
        올려야 할 것은 지표지 기준선이 아니다.
        """
        if not self.meta.get("remote_embedding_selected_count", 0):
            self.skipTest("로컬 폴백 벡터 빌드 — 추적률 기준 적용 대상이 아니다")
        if self.meta.get("tracking_window_briefings", 0) < build_data.TRACKING_WINDOW_BRIEFINGS:
            self.skipTest("브리핑 회차가 창보다 적다 — 누적 분모가 안 만들어진다")
        self.assertGreaterEqual(self.meta["tracking_window_rate"], 0.20)

    def test_tracking_window_is_wide_enough_to_be_a_signal(self):
        """게이트가 다시 하루치로 좁아지지 않게 잠근다.

        분모가 작으면 이 지표는 병합기가 아니라 그날 뉴스량을 재게 된다.
        """
        self.assertGreaterEqual(build_data.TRACKING_WINDOW_BRIEFINGS, 7)
        if self.meta.get("tracking_window_briefings", 0) < build_data.TRACKING_WINDOW_BRIEFINGS:
            self.skipTest("브리핑 회차가 창보다 적다")
        self.assertGreaterEqual(self.meta["tracking_window_issue_count"], 30)
        self.assertEqual(
            self.meta["tracking_window_rate"],
            round(
                self.meta["tracking_window_tracked_issue_count"]
                / self.meta["tracking_window_issue_count"], 4
            ),
        )

    def test_atlas_readiness_is_measured_not_asserted(self):
        """이슈 지도 착수 조건을 계기판으로 싣는다.

        **값을 게이트로 걸지 않는다.** 오늘(2026-08-03) 추적률을 배포 게이트로 썼다가
        뉴스가 한산한 날 CSS 오타 수정까지 막힌 일이 있었다. 여기서 검사하는 것은
        '배관이 붙어 있는가'지 '수치가 목표에 닿았는가'가 아니다 — 후자는
        meta.atlas_readiness 를 사람이 읽고 판단한다.
        """
        atlas = self.meta["atlas_readiness"]
        self.assertEqual(atlas["definition_version"], "card-evidence-v2")
        self.assertEqual(atlas["metric_basis"]["state_sort_briefing_count"], "card_members")
        self.assertEqual(atlas["metric_basis"]["related_articles_verification"], "card_plus_evidence_members")
        self.assertIn("multi_card_article_rate", atlas)
        self.assertIn("multi_evidence_article_rate", atlas)
        self.assertEqual(atlas["issue_total"], self.meta["issue_catalog_total"])
        self.assertEqual(
            set(atlas["node_counts"]),
            {name for name, _ in build_data.ATLAS_NODES},
        )
        self.assertEqual(set(atlas["node_rates"]), set(atlas["node_counts"]))
        for name, count in atlas["node_counts"].items():
            self.assertLessEqual(count, atlas["issue_total"], name)
        self.assertLessEqual(atlas["full_path_issues"], atlas["three_plus_issues"])
        # ready 는 blocking_nodes 의 반대말이어야 한다 — 둘이 어긋나면 계기판이 거짓말
        self.assertEqual(atlas["ready"], not atlas["blocking_nodes"])

    def test_atlas_blocking_nodes_match_the_documented_thresholds(self):
        """문턱을 조용히 낮춰서 '착수 가능'을 만들지 못하게 잠근다.

        PHASE_PLAN §S4 가 착수 판단을 open_question·related_articles 두 값으로
        못박았다. 여기 숫자를 고치려면 그 문서도 같이 고쳐야 한다.
        """
        self.assertGreaterEqual(build_data.ATLAS_MIN_OPEN_QUESTION, 1)
        self.assertGreaterEqual(build_data.ATLAS_MIN_RELATED_RATE, 0.20)
        atlas = self.meta["atlas_readiness"]
        expected = []
        if atlas["node_counts"]["open_question"] < build_data.ATLAS_MIN_OPEN_QUESTION:
            expected.append("open_question")
        if atlas["node_rates"]["related_articles"] < build_data.ATLAS_MIN_RELATED_RATE:
            expected.append("related_articles")
        self.assertEqual(atlas["blocking_nodes"], expected)

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
            # 국가 태그로 판정된 기사에만 이 규칙이 적용된다. infer_region 은
            # 명시적 scope 를 국가보다 먼저 보므로(scope → countries → section →
            # domain), scope 로 판정된 기사를 여기서 검사하면 **더 정확한 판단을
            # 오류로 센다**. 실측: "엔터지, 홀텍 SMR-300 배치 검토 위해 현대건설과
            # 협력"(countries=[KR, US], scope=overseas) — 미국 배치 기사에
            # 현대건설이 참여하는 것이라 해외가 맞다.
            if article.get("region_source") != "countries":
                continue
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
        # 라벨이 '산업 영향'→'시사점'으로 바뀌고 '왜 중요한가'가 갈라져 나왔다
        # (2026-08-04). 이름표가 부서 업무에 가까워질수록 이 배지가 더 중요하다 —
        # 라벨만 바꾸고 배지를 놓치면 AI 해석이 공식 견해로 읽힌다.
        for marked in ('왜 중요한가 <span class="ai-badge">AI</span>',
                       '시사점 <span class="ai-badge">AI</span>',
                       '${esc(model.why.label)} <span class="ai-badge">AI</span>',
                       '${esc(model.impact.label)} <span class="ai-badge">AI</span>'):
            self.assertIn(marked, script)
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
            # 카드에 보이는 문장과 같으면 변화 블록을 비운다. 기준선이 summary 에서
            # 제목·card_why 로 바뀌었다(2026-08-08) — summary 는 이제 카드에 없으므로
            # 그걸 기준으로 지우면 카드의 유일한 사실 문장을 지우게 된다.
            for issue in briefing["issues"]:
                display = str(issue.get("change_display") or "").strip().rstrip(".!?")
                if not display:
                    continue
                self.assertNotEqual(display, str(issue["title"]).strip().rstrip(".!?"))
                self.assertNotEqual(display, str(issue.get("card_why") or "").strip().rstrip(".!?"))

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
        # 같은 숫자를 말하는 곳은 한 곳으로 남는다
        self.assertIn("마지막 수집", script)
        self.assertIn("오늘 기사", script)
        # 정상인 날의 스트립은 걷는다. 상태값은 거의 매일 ok 인데 화면 최상단
        # 54px(390px 기준 2줄)를 늘 켜 두면 사용자가 처음 보는 것이 파이프라인
        # 계측값이 된다 — 이 저장소가 검증 배지에 세운 규칙과 같다.
        self.assertIn(".status-strip.ok { display: none; }", style)
        # 걷힌 자리를 메우도록 헤더 상태 버튼은 이름을 따로 갖는다(좁은 화면에서
        # 라벨 span 이 접혀 접근 가능한 이름이 사라지던 것).
        self.assertIn('aria-label="데이터 상태"', html)
        # 정상일 때의 문구에서 뺀 둘 — 0 건인 날이 대부분이라 결함처럼 읽혔고,
        # 갱신 일정은 읽는 사람이 할 일이 없다. 상태 다이얼로그에는 남는다.
        self.assertNotIn("1차 출처 ${", script)
        self.assertNotIn("다음 갱신 2시간 이내`", script)

    def test_p4_briefings_declare_what_the_headline_is(self):
        for briefing in self.briefings:
            self.assertIn(briefing["headline_kind"],
                          {"change", "issue", "empty", "synthesis"})
            self.assertIn("changed_issue_count", briefing)
            # headline 은 아카이브 목록과 RSS 가 쓰므로 계속 채운다. 히어로만 안 쓴다.
            self.assertTrue(briefing["headline"])
            # '무엇이 달라졌는가'는 헤드라인 이슈가 실제로 **이어지는** 이슈일 때만
            # 내건다. 예전에는 화살표(latest_change) 유무로 판정했는데, 화살표는
            # 요약 되풀이면 지워지므로 이어지는 이슈인데도 0이 될 수 있다.
            if briefing["headline_kind"] == "change":
                self.assertGreater(briefing["tracked_issue_count"], 0)

    def test_p5_detail_order_related_issues_and_mobile_actions(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for heading in ("한 줄 결론", "이번에 달라진 점", "왜 중요한가", "시사점",
                        "사건 타임라인과 근거 원문", "관련 이슈"):
            self.assertIn(heading, script)
        self.assertIn("function relatedIssues", script)
        # 제목이 상세 진입점이므로 좁은 화면에서 타임라인 버튼을 숨겨도 길이 남는다.
        self.assertIn("issue-title-button", script)
        self.assertRegex(style, r"\.issue-actions \.issue-detail-button[^{]*\{\s*display: none;")
        # JS 스크롤은 CSS의 모션 감소 설정을 자동으로 따르지 않는다.
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', script)

    def test_card_body_is_three_labelled_slots_not_a_paragraph(self):
        """카드는 문단 하나가 아니라 라벨 붙은 세 칸이다.

        전신 계약은 '두 번째 줄은 무엇이 아니라 왜'(implication 한 줄, summary
        폴백)였다. 그 줄 하나로는 훑는 사람이 '무엇이 달라졌나 / 왜 중요한가 /
        다음에 뭘 보나' 세 질문에 답할 수 없어서, 2026-08-08 개편에서 칸을 갈랐다.
        summary 는 카드에서 완전히 내려갔다 — 제목을 어순만 바꿔 다시 쓴 문장이
        대부분(8/3 실측 8건 중 5건)이라는 판단은 그대로 유효하고, 이제 그 문장은
        상세에만 있다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        card = script.split("function issueCard(", 1)[1].split("\nfunction ", 1)[0]
        # 변화 칸의 라벨은 change_kind 에 따라 갈린다(직전 상태면 '직전까지') —
        # 기본값이 '달라진 것' 이라는 것까지가 계약이다.
        self.assertIn('cardRow(issueChangeLabel(issue, "달라진 것")', card,
                      "카드에 '달라진 것' 칸이 없다")
        for label in ("왜 중요해요", "다음 확인"):
            self.assertIn(f'cardRow("{label}"', card, f"카드에 '{label}' 칸이 없다")
        # 역할 분리는 build_data.finalize_card_fields 가 확정한다. 화면에서 or 로
        # 다시 고르면 계약이 두 곳으로 흩어진다 — 스테일 데이터 대비 ?? 하나만 둔다.
        self.assertIn("issue.card_why ??", card)
        self.assertNotIn("issue.summary", card, "장문 summary 가 카드로 돌아왔다")
        self.assertIn(".issue-line {", style)
        self.assertIn(".issue-line-label {", style)
        # 검색 하이라이트 판정도 화면에 실제로 뜨는 문장을 기준으로 한다
        self.assertIn("${changeText} ${whyText} ${nextText}", card)
        # AI 가 쓴 문장에는 고지가 따라붙는다. 배지 규칙('예외만 표시')과 달리
        # 이건 신호가 아니라 고지라서 전 카드에 붙는다.
        self.assertIn('<span class="ai-badge">AI</span>', card)

    def test_hero_h1_is_the_fixed_weekly_product_promise(self):
        """h1 은 일별 기사 제목이나 daily_lead 가 아니라 고정 제품 문구다."""
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('id="briefingTitle">이번 주 원자력, 무엇이 달라졌나</h1>', html)
        self.assertIn('textContent = "이번 주 원자력, 무엇이 달라졌나"', render)
        self.assertNotIn('getElementById("briefingTitle").innerHTML', render)
        render_code = "\n".join(re.sub(r"//.*$", "", line) for line in render.splitlines())
        self.assertNotIn("daily_lead", render_code)

    def test_hero_does_not_repeat_what_the_lead_card_already_says(self):
        """'왜'는 한 화면에 한 번만 선다.

        이 브랜치의 원본(8551f68, 8/3)은 히어로를 줄인 자리에 implication 한 줄
        (hero-lead-meta)을 넣었다. 그 뒤 선두 카드가 들어와(PR #3) 같은 문장을
        '시사점' 블록으로 바로 아래에 세운다 — 모바일 첫 화면에서 같은 문장을 두
        번 읽히게 되고, 그건 이 작업이 고치려던 바로 그 증상이다. 히어로는 킥커와
        제목까지만 맡는다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn("heroLeadMeta", html)
        self.assertNotIn(".hero-lead-why", style)
        code = "\n".join(re.sub(r"//.*$", "", line) for line in script.splitlines())
        self.assertNotIn("hero-lead-meta", code)
        # 히어로 축소(3ff0907)를 되돌리는 크기 재지정이 없어야 한다
        self.assertNotIn("clamp(23px, 6vw, 30px)", style)

    def test_list_actions_drop_share_and_keep_source(self):
        """카드 8장마다 같은 액션 줄이 반복되면 훑는 눈에 내용보다 먼저 걸린다.

        상세를 열어 보지도 않은 이슈를 공유하는 행동은 드물다(공유는 상세 모달에
        그대로 있다). 원문은 남긴다 — 이 서비스의 마지막 행동이라 목록에서 끊으면
        흐름이 끊긴다.
        """
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".issue-actions [data-share-issue] { display: none; }", style)
        self.assertIn(".issue-actions { display: grid; grid-template-columns: repeat(2, 1fr)", style)

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

    def test_stored_daily_lead_no_longer_becomes_the_hero_sentence(self):
        """히어로 문장은 더 이상 LLM 종합 문장(daily_leads.json)에서 오지 않는다.

        실측(8/4·8/5): 모델은 하루 이슈에 공통점이 없어도 반드시 한 문장을 써야
        해서 관계없는 두 건을 '가운데'로 이어 붙였다. 지금 히어로는 아무 말도
        하지 않는다 — headline 은 아카이브 목록과 RSS 를 위해 계속 채운다.
        """
        issues_day = [{
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
        self.assertNotEqual(built[0]["headline_kind"], "synthesis")
        # headline 자체는 계속 채운다 — 아카이브 목록(bt-headline)이 쓴다.
        self.assertIn("계속운전", built[0]["headline"])
        del issues_day

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

    def test_hero_evidence_chips_are_not_rendered(self):
        """근거 칩은 히어로가 문장을 낼 때 그 출처를 보이려던 것이다.

        낼 문장이 없으니 칩도 없다. 컨테이너는 index.html 이 참조하므로 남긴다.
        """
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="headlineEvidence"', html)
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("evidenceBox.hidden = true;", render)
        self.assertNotIn("hero-evidence-chip", render)

    def test_weekly_hero_is_visible_and_keeps_the_audio_brief(self):
        """주간 고정 HERO를 보이되 daily_lead 문장과 오디오는 건드리지 않는다."""
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn('aria-labelledby="briefingTitle"', html)
        self.assertIn('hero.classList.add("lead-issue", "weekly-hero")', script)
        self.assertIn('hero.classList.remove("no-lead")', script)
        self.assertIn(".briefing-hero.weekly-hero", css)
        # no-lead / lead-issue 어느 쪽도 오디오를 걷어내면 안 된다.
        self.assertNotIn(".briefing-hero.no-lead .hero-audio", css)
        self.assertNotIn(".briefing-hero.lead-issue .hero-audio", css)
        self.assertIn("audioBrief", script)

    def test_empty_state_does_not_contradict_the_changed_section(self):
        """필터 결과가 위 구역에만 있을 때 아래에서 '없습니다'라고 하면 안 된다.

        실측: topic=fusion 이면 '지금 달라진 이슈'에 독일 핵융합 카드가 남는데
        '오늘 확인된 이슈'는 빈 상태를 띄워 한 화면이 스스로를 부정했다.

        가드에 조건이 더 붙을 수 있으므로(선두 카드로 옮겨간 경우 등) 줄바꿈까지
        문자열로 고정하지 않는다 — 지켜야 할 것은 서식이 아니라 '빈 상태보다
        먼저 다른 구역을 확인한다'는 순서다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("section-note", render)
        empty_index = render.index("조건에 맞는 이슈가 없습니다")
        guard = re.search(r"visibleChanged\.length[^\n]*\n\s*\?", render)
        self.assertIsNotNone(guard, "빈 상태 앞에 visibleChanged 를 확인하는 가드가 없다")
        self.assertLess(guard.start(), empty_index)

    def test_lead_card_is_wired_and_not_duplicated_below(self):
        """선두 이슈는 자기 자리에 서고, 아래 두 목록에서는 빠져야 한다.

        같은 이슈가 한 화면에 두 번 서면 '8개 이슈' 개수 표시가 실제 카드 수와
        어긋난다. 그리고 새 컨테이너를 만들면 handleIssueAction 위임 목록에
        id 를 넣어야 카드 안의 버튼(타임라인·저장·공유)이 산다.
        """
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="leadIssue"', html)
        self.assertIn('id="leadCard"', html)
        self.assertIn("function leadCard(", script)
        delegation = script.split("handleIssueAction);", 1)[0]
        self.assertIn('"leadCard"', delegation)
        render = script.split("function renderBriefing(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("issue.issue_id !== leadId", render)
        # 선두는 편집 판단이라 정렬 토글보다 먼저 정해진다 — '최신순'으로 바꿨다고
        # 가장 먼저 볼 이슈가 달라지면 그건 판단이 아니라 정렬 결과다.
        self.assertLess(render.index("const lead = issues[0]"),
                        render.index('state.issueSort === "latest"'))

    def test_lead_card_skips_blocks_that_have_no_data(self):
        """빈 블록은 세우지 않는다.

        latest_change 는 실측 6%, open_question 은 0% 다. '변화 없음' 같은 줄을
        매일 세우면 그 자리가 신호가 아니라 배경이 된다 — 카드에서 선정 이유·
        단일 출처 배지를 뺀 것과 같은 원칙이다.
        """
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        lead = script.split("function leadCard(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn(".filter(Boolean)", lead)
        for field in ("model.why ?", "model.impact ?", "model.change ?", "model.openQuestion ?"):
            self.assertIn(field, lead)
        self.assertIn('label: issueChangeLabel(issue, "달라진 것")', lead)
        # summary('무슨 일')는 평소에 안 세운다 — 변화 문장이 그 요약으로 만들어지므로
        # 두 블록이 구조적으로 같은 말이었다(2026-08-08 실측: 선두 카드와 근거 패널이
        # 20자 넘게 동일).
        #
        # 다만 **바닥은 있어야 한다.** 그 규칙은 '변화 문장이 있다'는 전제 위에 있었고,
        # 전제가 깨지는 날이 실제로 왔다: 2026-08-11 빌드에서 8/10 브리핑의
        # latest_change 가 12건 중 0건이라 데스크톱 선두 카드가 제목만 남고 아래가
        # 통째로 비었다(82자 summary 를 쥔 채로). 중복 금지는 겹칠 것이 있을 때만
        # 성립한다 — summary 는 세울 블록이 하나도 없을 때만 등장해야 한다.
        summary_lines = [line for line in lead.splitlines() if 'label: "무슨 일"' in line]
        self.assertEqual(len(summary_lines), 1, "summary 블록은 폴백 한 자리에만 있어야 한다")
        self.assertIn("!shown.length", summary_lines[0],
                      "summary 는 세울 블록이 0개일 때만 세운다")
        # 주석에서는 이 표현을 설명해도 되지만 화면에 나가는 문자열이면 안 된다.
        code = "\n".join(re.sub(r"//.*$", "", line) for line in script.splitlines())
        self.assertNotIn("어제와 달라진", code)

    def test_lead_card_type_stays_above_the_minimum(self):
        """12.5px 미만 금지는 선두 카드에도 그대로 적용된다.

        토큰 도입 후 .lead-* 의 크기가 var(--t-*) 로 적힐 수 있다 —
        :root 정의를 해석해 실제 px 로 풀어서 검사한다(clamp 는 최소값).
        """
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        tokens = dict(re.findall(r"(--t-[\w-]+):\s*([^;]+);", style))

        def resolve(rule_body):
            sizes = [float(s) for s in re.findall(r"font-size:\s*([\d.]+)px", rule_body)]
            for name in re.findall(r"font-size:\s*var\((--t-[\w-]+)\)", rule_body):
                value = tokens.get(name, "")
                match = re.search(r"clamp\(\s*([\d.]+)px", value) or re.search(
                    r"([\d.]+)px", value
                )
                self.assertIsNotNone(match, f"{name} 토큰을 px 로 풀 수 없다")
                sizes.append(float(match.group(1)))
            return sizes

        lead_rules = re.findall(r"\.lead-[^{]*\{[^}]*\}", style)
        self.assertTrue(lead_rules, ".lead-* 규칙이 없다")
        sizes = [size for rule in lead_rules for size in resolve(rule)]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), 12.5)

    def test_p2_structure_status_search_and_responsive_controls_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        for element_id in (
            "systemStatus", "headerStatus", "globalSearchDialog", "briefingFilters",
            "issueSort", "issueViewToggle", "mobileTabs", "themeToggle", "search-saved",
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

    def test_publications_drop_events_and_nonpower_but_llm_verdict_wins(self):
        """발간물 탭은 '보고서로 쓸 만한가'를 판단하는 자리다.

        실측(2026-08-03): 29건 중 11건이 행사·교육 소식이거나 FAO 공동 프로그램
        (농업·식품·수자원) 뉴스레터였다. 제목 규칙으로 거르되, 규칙은 낱말만 보므로
        LLM 판정이 있는 항목에서는 규칙을 태우지 않는다 — 'Workshop on Regulatory
        Harmonisation' 같은 진짜 정책 문서를 규칙이 되돌려 지우면 안 된다.
        """
        drop = build_data.publication_drop_reason
        self.assertEqual(drop({"title": "Inaugural NextGen Nuclear Leaders Summer School held"}), "event")
        self.assertEqual(drop({"title": "TCOFF-2 project members meet in Tokyo to review progress"}), "event")
        self.assertEqual(drop({"title": "Insect Pest Control Newsletter No. 106"}), "nonpower")
        self.assertEqual(drop({"title": "Cooperative Approaches to the Back End of the Nuclear Fuel Cycle"}), "")

        # LLM 이 관련 있다고 판정하면 제목에 workshop 이 있어도 남는다
        self.assertEqual(drop({"title": "Workshop on Regulatory Harmonisation", "off_topic": False}), "")
        # 반대로 제목 규칙에 안 걸려도 LLM 이 걸러내면 제외된다
        self.assertEqual(
            drop({"title": "Nuclear Knowledge Fair 2026", "off_topic": True,
                  "off_topic_reason": "행사 소식"}),
            "행사 소식")

    def test_publication_gist_is_hidden_when_it_only_echoes_the_title(self):
        """같은 말을 두 줄 쓰면 목록만 길어진다 — 실측 15건 중 10건이 제목 재진술."""
        echo = build_data.gist_adds_nothing
        self.assertTrue(echo("원자력 안전 핵심 실험 데이터세트 보존",
                             "원자력 안전을 위한 핵심 실험 데이터세트 보존"))
        self.assertTrue(echo("임계 안전성 과제 및 발전 논의", "임계 안전성 과제 및 발전 논의"))
        # 문서 성격·범위를 더하면 남긴다
        self.assertFalse(echo("SMR 배치를 앞당기기 위한 규제·공급망 과제 정리",
                              "SMR(소형모듈원자로) 가속화"))
        # 한쪽이 비면 판정하지 않는다 (없는 것을 지웠다고 세지 않게)
        self.assertFalse(echo("", "제목"))
        self.assertFalse(echo("요약", ""))

    def test_publication_org_labels_carry_the_english_acronym(self):
        """약자만 아는 사람과 한글 명칭만 아는 사람이 갈린다 — 둘 다 적는다."""
        aliases = build_data.PUBLICATION_ORG_ALIASES
        self.assertEqual(aliases["에경연"], "에너지경제연구원(KEEI)")
        self.assertEqual(aliases["에너지경제연구원"], "에너지경제연구원(KEEI)")
        for org_kr in ("OECD 원자력기구", "국제원자력기구", "국제에너지기구", "미국 에너지정보청"):
            self.assertRegex(aliases[org_kr], r"\([A-Z\-]+\)$")
        # 이미 수집된 항목도 빌드 시점에 교정된다
        for item in self.publications["items"]:
            self.assertNotEqual(item["org_kr"], "에경연")

    def test_publications_tab_is_wired_and_failure_tolerant(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="report"', html)
        self.assertIn('id="view-report"', html)
        self.assertIn('"report"', script)
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
        self.assertIn('data-view="report"', mobile_nav)
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(mobile_nav.count("<button"),
                         4, "모바일 탭 수가 바뀌면 grid-template-columns도 함께 고쳐야 한다")
        self.assertIn("grid-template-columns: repeat(4, 1fr)", style)

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

    def test_scheduled_event_does_not_take_the_lead(self):
        """아직 안 열린 회의를 '가장 먼저 볼 이슈'로 세우지 않는다.

        사용자 지적(2026-08-10): must_read 가 0건인 날 selection_score 만 남는데
        그 점수가 '국내 정책 결정'에 크게 가중돼(korea_relevance 3) 회의 예고
        기사가 1번 자리를 가져갔다. 점수가 더 높아도 예고는 뒤로 민다.
        """
        rows = [
            {"region": "국내", "importance": "nice_to_know", "sort_score": 22.7,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "회의 예고",
             "representative_article": {"event_date_type": "scheduled"}},
            {"region": "해외", "importance": "nice_to_know", "sort_score": 16.7,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "실제로 벌어진 일",
             "representative_article": {"event_date_type": "occurrence"}},
        ]
        build_data.order_issue_rows(rows)
        self.assertEqual(rows[0]["title"], "실제로 벌어진 일")

    def test_scheduled_demotion_never_beats_importance(self):
        """등급이 먼저다 — must_read 예고가 nice_to_know 사건에 밀리면 안 된다."""
        rows = [
            {"region": "국내", "importance": "must_read", "sort_score": 1.0,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "중대 예고",
             "representative_article": {"event_date_type": "scheduled"}},
            {"region": "국내", "importance": "nice_to_know", "sort_score": 99.0,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "잡담",
             "representative_article": {"event_date_type": "occurrence"}},
        ]
        build_data.order_issue_rows(rows)
        self.assertEqual(rows[0]["title"], "중대 예고")

    def test_missing_event_type_is_not_treated_as_scheduled(self):
        """event_date_type 은 실측 선두 24건 중 22건이 unknown 이다. 모르는 것을
        예고로 몰면 규칙이 사실상 전체 순서를 뒤집는다."""
        rows = [
            {"region": "국내", "importance": "nice_to_know", "sort_score": 20.0,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "판정 없음"},
            {"region": "해외", "importance": "nice_to_know", "sort_score": 10.0,
             "last_seen": "2026-08-10", "editor_pin": 0, "title": "발생",
             "representative_article": {"event_date_type": "occurrence"}},
        ]
        build_data.order_issue_rows(rows)
        self.assertEqual(rows[0]["title"], "판정 없음")

    def test_pin_is_stripped_from_output(self):
        rows = [{"region": "국내", "importance": "must_read", "sort_score": 1.0,
                 "last_seen": "2026-08-03", "editor_pin": 1}]
        build_data.order_issue_rows(rows)
        self.assertNotIn("editor_pin", rows[0])
        self.assertNotIn("sort_score", rows[0])

    def test_repo_template_parses_into_the_expected_shape(self):
        """**비어 있어야 한다고 못 박지 않는다.** 이 파일은 쓰라고 있는 자리다.

        예전 이름은 `..._is_valid_and_empty` 였고 항목이 하나라도 들어오면 실패했다.
        즉 문서가 권하는 대로 편집자가 한 건 내리는 순간 CI 가 빨개지고 배포가
        막혔다(2026-08-11 실측). 검증할 값어치가 있는 것은 '비었나'가 아니라
        '읽히는 모양인가'다.
        """
        over = build_data.load_selection_overrides()
        for bucket in ("promote", "demote"):
            self.assertIsInstance(over[bucket], dict)
            for key, action in over[bucket].items():
                self.assertIsInstance(key, tuple)
                self.assertEqual(len(key), 2)
                hash8, day = key
                self.assertRegex(hash8, r"^[0-9a-f]{8}$")
                self.assertRegex(day, r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(str(action))

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

    def test_card_shows_it_as_the_next_check_slot(self):
        """'다음 확인'은 카드의 세 번째 칸이다.

        예전 계약은 '상세 전용, 카드에는 안 낸다'였다. 카드가 요약 한 줄이던
        구조에서는 맞았지만, 2026-08-08 개편의 카드는 '다음에 뭘 확인하나'에
        답하는 것이 세 칸 중 하나다. 채움률이 낮아(실측 168건 중 6건) 대부분
        숨겨지는데, 그건 화면이 아니라 큐레이션에서 채울 구멍이다.
        """
        self.assertIn("아직 확정되지 않은 것", self.script)
        card_fn = self.script.split("function issueCard(")[1].split("\nfunction ")[0]
        self.assertIn("issue.open_question", card_fn)
        self.assertIn('cardRow("다음 확인", nextText)', card_fn)
        # 값이 없으면 칸 자체가 안 선다 — 빈 라벨은 신호가 아니라 배경이 된다.
        self.assertIn("text\n    ? `<p class=\"issue-line\">", self.script)

    def test_hidden_when_empty_and_escaped(self):
        self.assertIn("${issue.open_question ? `<p class=\"dialog-open\">", self.script)
        self.assertIn("esc(issue.open_question)", self.script)

    def test_report_pack_includes_it(self):
        self.assertIn("• 미확정: ${issue.open_question}", self.script)

    def test_style_exists(self):
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".dialog-open", style)


class InterpretationSplitTests(unittest.TestCase):
    """AI 해석 두 줄은 각자의 축으로 선다.

    2026-08-04 이전에는 빌드가 `implication or why_important` 로 둘을 뭉갰고,
    화면은 그 결과를 '산업 영향'이라는 제3의 이름으로 내보냈다. must_read 55건
    중 정상은 1건이었다 — 22건은 긴 쪽이 버려졌고 19건은 왜 중요가 시사점
    라벨을 달았다 (docs/2026-08-04-gap-review.md).
    """

    def test_two_axes_survive_as_two_fields(self):
        # implication 예시는 구체적인 사실을 담은 문장이어야 한다. 예전 예시
        # ("…유럽 시장 확대가 예상됩니다")는 빈껍데기 게이트에 걸려 이 테스트가
        # 두 축 보존이 아니라 게이트 동작을 재는 테스트로 바뀌어 버렸다.
        row = build_data.split_interpretation({
            "implication": "체코 정부가 추가 2기 부지를 지정하며 발주 일정이 내년 상반기로 앞당겨졌다.",
            "why_important": "두코바니 후속 사업의 발주 방식이 한국형 노형의 유럽 재진입 조건을 좌우한다.",
        })
        self.assertEqual(len([value for value in row if value]), 2)

    def test_why_important_alone_is_not_relabelled_as_implication(self):
        """예전 폴백의 실제 피해 — 19건이 남의 이름표를 달고 나갔다."""
        implication, why_important = build_data.split_interpretation(
            {"implication": "", "why_important": "장기 운영 허가의 선례가 된다."})
        self.assertEqual(implication, "")
        self.assertEqual(why_important, "장기 운영 허가의 선례가 된다.")

    def test_restated_pair_keeps_the_longer_line(self):
        """같은 말이면 한 줄만. 남기는 쪽은 긴 쪽 — 짧은 쪽을 남기면 원래 손실 그대로다."""
        long_line = "미국 에너지부의 시험용 원자로 가동 승인은 차세대 원자로 기술 개발의 중요한 이정표이며, 향후 상용화에 긍정적입니다."
        short_line = "미국 에너지부의 시험용 원자로 가동 승인은 차세대 원자로 기술 개발의 이정표입니다."
        implication, why_important = build_data.split_interpretation(
            {"implication": short_line, "why_important": long_line})
        self.assertEqual(why_important, long_line)
        self.assertEqual(implication, "")

    def test_atlas_counts_either_field_so_the_split_is_not_read_as_a_drop(self):
        """노드가 묻는 건 'AI 해석이 있는가' 하나다. 한쪽만 세면 분리가 후퇴로 보인다."""
        node = dict((name, test) for name, test in build_data.ATLAS_NODES)["implication"]
        self.assertTrue(node({"why_important": "왜 중요한지의 설명", "implication": ""}))
        self.assertTrue(node({"implication": "시사점 한 줄", "why_important": ""}))
        self.assertFalse(node({"implication": "", "why_important": ""}))

    def test_the_web_calls_the_line_what_telegram_calls_it(self):
        """같은 문장을 텔레그램은 '시사점', 웹은 '산업 영향'이라 부르던 것을 끝낸다."""
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        code = "\n".join(re.sub(r"//.*$", "", line) for line in script.splitlines())
        self.assertNotIn("산업 영향", code)
        # 한 필드에 이름이 셋이면(산업 영향·Nuclens 해석·시사점) 화면마다 다른
        # 것으로 읽힌다. 다이얼로그도 같은 이름을 쓴다.
        self.assertNotIn("Nuclens 해석", code)
        self.assertIn('label: "시사점"', script)

    def test_both_lines_get_their_own_block_on_the_lead_card_and_rail(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        lead = script.split("function leadCard(", 1)[1].split("\nfunction ", 1)[0]
        for field in ("model.why ?", "model.impact ?"):
            self.assertIn(field, lead, f"선두 카드에 {field} 블록이 없다")
        rail = script.split("function renderEvidenceRail(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("model.why ?", rail)
        self.assertIn("model.impact ?", rail)
        # AI 해석이라는 표시는 라벨이 바뀌어도 남아야 한다 — 회사 화면에서 이
        # 문장이 공식 견해로 읽히면 라벨을 고친 의미가 없다.
        self.assertEqual(rail.count('class="ai-badge"'), 2)


class ReportPickTests(unittest.TestCase):
    """보고서 추천의 주제·이유·각도를 빌드와 보고서 탭까지 보존한다."""

    def test_topic_comes_from_the_articles_not_the_screen(self):
        picked = build_data.pick_report_topic([
            {"article_date": "2026-08-01", "report_pick": ""},
            {"article_date": "2026-08-03", "report_pick": "중국 신규 원전 8기 승인의 정책 함의"},
        ])
        self.assertEqual(picked, "중국 신규 원전 8기 승인의 정책 함의")

    def test_no_pick_is_an_empty_string_not_a_placeholder(self):
        self.assertEqual(build_data.pick_report_topic([{"article_date": "2026-08-03"}]), "")

    def test_report_metadata_comes_from_the_same_latest_article(self):
        picked = build_data.pick_report_metadata([
            {"article_date": "2026-08-01", "report_pick": "옛 후보", "report_pick_why": "옛 이유"},
            {"article_date": "2026-08-03", "report_pick": "새 후보", "report_pick_why": "새 이유",
             "report_pick_angles": ["정책", "산업", ""]},
        ])
        self.assertEqual(picked, ("새 후보", "새 이유", ["정책", "산업"]))

    def test_badge_stays_compact_while_report_view_shows_angles(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function reportPickBadge", script)
        self.assertIn("보고서 검토 추천", script)
        badge = script.split("function reportPickBadge(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("esc(topic)", badge, "추천 주제는 title 속성으로도 이스케이프해야 한다")
        self.assertNotIn("report_pick_angles", badge)
        report = script.split("function renderReportCandidates(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("report_pick_why", report)
        self.assertIn("report_pick_angles", report)
        self.assertIn("보고서 자료팩 복사", report)

    def test_badge_is_legible_on_the_dark_evidence_rail(self):
        """근거 패널 머리는 딥 포레스트 배경이다 — 밝은 배경용 색을 그대로 쓰면 묻힌다."""
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".report-pick-badge", style)
        self.assertIn(".rail-badges .report-pick-badge", style)


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


class TopicWeekAggregationTests(unittest.TestCase):
    """주제별 주간 추이는 이슈를 센다. 기사×주제 쌍이 아니다.

    실측 2026-08-08: 쌍 기준 주별 합계 22 / 59 / 86 / 580 — 그 주 실제 이슈는
    73건인데 한 주제가 185건으로 떴다. 아카이브가 최근 2주만 밀도 있어서 화살표가
    보도량이 아니라 수집량을 말하고 있었다. 이슈 기준으로는 80 / 85 / 102.
    """

    FULL_WEEK = ["2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
                 "2026-07-24", "2026-07-25", "2026-07-26"]

    @staticmethod
    def _issue(topics, first, last):
        return {"topics": topics, "first_seen": first, "last_seen": last}

    def test_one_issue_counts_once_per_week_no_matter_how_many_articles(self):
        rows = [self._issue(["smr"], "2026-07-20", "2026-07-24")]
        weeks, series = build_data.build_topic_weeks(rows, self.FULL_WEEK)
        self.assertEqual(weeks, ["2026-W30"])
        self.assertEqual(series["smr"], [1])

    def test_multi_topic_issue_lands_in_each_topic(self):
        rows = [self._issue(["smr", "newbuild"], "2026-07-20", "2026-07-20")]
        _, series = build_data.build_topic_weeks(rows, self.FULL_WEEK)
        self.assertEqual(series["smr"], [1])
        self.assertEqual(series["newbuild"], [1])

    def test_partial_weeks_are_dropped(self):
        """브리핑 2일짜리 주는 화살표가 보도량이 아니라 달력을 말한다."""
        dates = ["2026-07-18", "2026-07-19"] + self.FULL_WEEK
        rows = [self._issue(["smr"], "2026-07-18", "2026-07-24")]
        weeks, series = build_data.build_topic_weeks(rows, dates)
        self.assertEqual(weeks, ["2026-W30"], "W29 는 브리핑 2일뿐인데 남았다")
        self.assertEqual(series["smr"], [1])

    def test_issue_spanning_two_full_weeks_counts_in_both(self):
        dates = self.FULL_WEEK + ["2026-07-27", "2026-07-28", "2026-07-29",
                                  "2026-07-30", "2026-07-31", "2026-08-01"]
        rows = [self._issue(["smr"], "2026-07-24", "2026-07-28")]
        weeks, series = build_data.build_topic_weeks(rows, dates)
        self.assertEqual(weeks, ["2026-W30", "2026-W31"])
        self.assertEqual(series["smr"], [1, 1])

    def test_fallback_buckets_are_excluded(self):
        """잔여 버킷의 증감은 분류기 정확도지 보도량이 아니다.

        `policy_general`·`research` 는 다른 주제가 하나도 안 붙었을 때만 달린다.
        실측 2026-08-08 라이브: '원자력 정책 ▼ 16% → 1%' — 정책 보도가 사라진 게
        아니라 구체 주제로 더 잘 붙은 것이었다.
        """
        rows = [self._issue(["policy_general"], "2026-07-20", "2026-07-20"),
                self._issue(["research"], "2026-07-20", "2026-07-20"),
                self._issue(["smr"], "2026-07-20", "2026-07-20")]
        _, series = build_data.build_topic_weeks(rows, self.FULL_WEEK)
        self.assertEqual(list(series), ["smr"])
        # 폴백인 이유가 코드에 남아 있어야 한다 — 규칙이 바뀌면 제외 목록도 바뀐다.
        source = (ROOT / "build_data.py").read_text(encoding="utf-8")
        for topic in build_data.TOPIC_TREND_EXCLUDED:
            self.assertIn(f'not topics:\n        topics.append("{topic}")', source,
                          f"{topic} 은 더 이상 폴백이 아니다 — 제외 목록에서 빼라")

    def test_untagged_issues_are_skipped_not_counted_as_zero_topic(self):
        rows = [self._issue([], "2026-07-20", "2026-07-20"),
                self._issue(["smr"], "2026-07-20", "2026-07-20")]
        _, series = build_data.build_topic_weeks(rows, self.FULL_WEEK)
        self.assertEqual(list(series), ["smr"])

    @unittest.skipIf(SKIP_DATA_GATES, "배포 경로에서는 데이터 지표를 게이트로 쓰지 않는다")
    def test_live_data_weeks_are_within_the_front_end_gate(self):
        """실데이터에서 주별 합계가 2배 안에 들어와야 화면이 방향을 말한다.

        **이건 데이터 품질을 보는 자리이지 배포를 막는 자리가 아니다**
        (2026-08-11 발견). deploy-web.yml 은 `build_data.py` 로 데이터를 구운 **뒤에**
        테스트를 돌리고 그 다음에 배포하므로, 이 검사가 배포 경로에서 살아 있으면
        **뉴스가 한 주에 몰린 것만으로 CSS 오타 수정도 배포가 막힌다.** 실제로
        그 상태였다 — 주별 합계 [56, 50, 101], 비 2.02.

        `test_tracking_rate_meets_target` 이 2026-08-03 에 같은 이유로 이미 꺼졌는데
        (워크플로 주석이 그 사고를 적어 두고 있다) 이 검사만 표식이 빠져 있었다.
        같은 종류는 같은 취급을 받아야 한다.

        화면은 이 왜곡에 이미 견딘다 — 주별 **비중**으로 정규화하고 `8pp 이상·표본
        8건 이상`일 때만 방향을 말한다. 그래서 이 값이 넘어도 거짓 방향이 뜨지는
        않는다. 로컬·수동 실행에서는 계속 켜져 있어 눈에 띈다.
        """
        catalog = json.loads((ROOT / "public" / "data" / "issues.json").read_text(encoding="utf-8"))
        briefings = json.loads((ROOT / "public" / "data" / "briefings.json").read_text(encoding="utf-8"))
        weeks, series = build_data.build_topic_weeks(
            catalog, [row["date"] for row in briefings])
        if len(weeks) < 2:
            self.skipTest("온전한 주가 2개 미만")
        totals = [sum(values[i] for values in series.values()) for i in range(len(weeks))]
        self.assertLessEqual(max(totals) / min(totals), 2,
                             f"주별 합계 {totals} — 모수가 기울어 방향을 말할 수 없다")
        # 이슈 단위이므로 한 주의 합계가 카탈로그 전체 이슈 수를 넘을 수 없다
        # (한 이슈가 주제 여럿에 가더라도 주제 상위 6개만 남기므로).
        self.assertLess(max(totals), len(catalog))


class OpenQuestionRollupTests(unittest.TestCase):
    """trend.json 의 `open_questions` — 이슈 단위로 한 번씩, 최신순, 최대 5개.

    화면에서는 내렸다(주간 판세 코너 제거, 2026-08-08). 같은 문장이 카드의
    '다음 확인' 칸에 이미 있다. 데이터는 계속 굽는다 — 채움률이 올라오면
    쓸 데가 생긴다.
    """

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

    def test_weekly_report_only_owns_the_two_week_corners(self):
        """주간 판세는 오늘 화면이 담당하는 문장을 다시 내지 않는다.

        고정 코너는 다섯이었다. '이번 주 판을 바꾼 것'(weekly_intro +
        policy_shifts)과 '다음 주 하나만 본다면'(watchpoints)은 오늘 화면의
        '이번 주 결론'·'이번 주 해설'·'다음 확인'과 같은 재료다 — 실측
        2026-08-08: 흐름 첫 화면 산문 여섯 문단 중 일곱 문장이 오늘 탭과 글자
        그대로 동일했다. 탭을 옮겼는데 같은 글이 다시 나오면 깊이가 아니라 반복이다.
        '아직 결론 나지 않은 것'(open_questions)도 같은 이유로 뺐다 — 같은 문장이
        선두 카드의 '다음 확인' 칸과 상세 모달에 이미 나오고, 채움률은 6/168 이다.
        남는 것은 테마 강약과 한수원 직접 영향 둘뿐이다.
        """
        weekly = self.script.split("function renderWeeklyReport(", 1)[1].split("\nfunction ", 1)[0]
        # 왜 뺐는지는 주석에 남아 있어야 한다. 검사 대상은 실행되는 코드뿐이다.
        code = "\n".join(re.sub(r"//.*$", "", line) for line in weekly.splitlines())
        for title in ("조용하지만 놓치면 안 되는 것", "한수원에 직접 닿는 변화"):
            self.assertIn(f'weeklySection("{title}"', code)
        self.assertEqual(code.count("weeklySection("), 2,
                         "주간 판세 고정 코너는 둘이다")
        for title in ("이번 주 판을 바꾼 것", "다음 주 하나만 본다면",
                      "아직 결론 나지 않은 것"):
            self.assertNotIn(f'weeklySection("{title}"', code,
                             f"'{title}' 이 오늘 화면과 겹친 채로 돌아왔다")
        for field in ("weekly_intro", "policy_shifts", "watchpoints", "open_questions"):
            self.assertNotIn(field, code, f"{field} 은 오늘 화면 소유다")

    def test_flow_tab_opens_with_indicators_not_prose(self):
        """설명문을 읽기 전에 방향과 크기를 알아볼 수 있어야 한다."""
        self.assertIn('id="trendTopicFlow"', self.html)
        self.assertLess(self.html.index('id="trendTopicFlow"'),
                        self.html.index('id="weeklyReport"'))
        trend_fn = self.script.split("function renderTrend()", 1)[1].split("\nfunction ", 1)[0]
        self.assertLess(trend_fn.index("renderTrendTopicFlow"), trend_fn.index("renderWeeklyReport"))
        # 오늘 화면과 흐름 탭이 같은 계산을 쓴다 — 임계값이 갈라지면 같은 표가
        # 서로 다른 판단을 낸다.
        self.assertIn("function topicFlowRows()", self.script)
        # 방향 판단에는 표본이 따라붙는다 — 표본 없이 화살표만 보면 잡음을 추세로 읽는다.
        self.assertIn("topic-sample", self.script)
        self.assertIn("topic-delta", self.script)

    def test_topic_direction_is_gated_on_comparable_week_samples(self):
        """모수가 주마다 다르면 ▲▼ 는 보도량이 아니라 수집량 변화를 말한다.

        실측 2026-08-08: topic_series 주별 합계 22 / 59 / 86 / 580. 원인은
        빌드에서 고쳤지만(build_topic_weeks) 게이트는 남긴다 — 수집이 다시
        기울면 화면이 먼저 입을 다무는 쪽이 낫다.
        """
        self.assertIn("function topicWeeksComparable(", self.script)
        flow = self.script.split("function topicFlowRows()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("if (!topicWeeksComparable(totals)) return [];", flow)
        # 같은 재료를 쓰는 슬로프 그래프도 같은 게이트를 지난다.
        slope = self.script.split("function renderSlopeGraph()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("topicWeeksComparable(weekTotals)", slope)

    def test_topic_span_follows_the_weeks_the_build_shipped(self):
        """제목·pp 폭이 '4주'로 굳어 있으면 3주치를 4주라고 읽게 된다.

        빌드가 부분 주를 버리므로(브리핑 6일 미만) 온전한 주는 3개일 수도 4개일
        수도 있다. 화면은 받은 만큼만 말한다.
        """
        self.assertIn("function topicFlowSpan()", self.script)
        flow = self.script.split("function topicFlowRows()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("const span = topicFlowSpan();", flow)
        self.assertNotIn("values.length >= 4", flow, "폭이 4주로 굳어 있다")
        render = self.script.split("function renderTrendTopicFlow()", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("trendTopicFlowTitle", render, "제목이 실제 주 수를 말해야 한다")

    def test_topic_unit_on_screen_matches_the_build(self):
        """화면 문구와 집계 단위가 갈라지면 이슈 총수보다 큰 '건수'가 다시 뜬다."""
        self.assertIn('"topic_series_unit": "issue"', (ROOT / "build_data.py").read_text(encoding="utf-8"))
        note = self.html.split('id="topicChart"', 1)[0][-400:]
        self.assertIn("이슈 수", note)
        row = self.script.split("function topicFlowRow(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("표본 이슈 ${row.sample}건", row)
        self.assertNotIn("기사·주제", row)

    def test_arrow_and_numbers_describe_the_same_span(self):
        """▼ 옆에 오르는 두 수가 붙으면 안 된다.

        실측 2026-08-08: 화살표는 3주 변화(-10pp)인데 옆 문구는 '지난주 18% →
        이번 주 19%' 였다. 한 줄에 서로 다른 두 비교가 있으면 독자가 어느 쪽을
        믿어야 할지 모른다.
        """
        row = self.script.split("function topicFlowRow(", 1)[1].split("\nfunction ", 1)[0]
        code = "\n".join(line for line in row.splitlines() if "<!--" not in line and "-->" not in line)
        self.assertIn("row.shares[0]", code, "비교 시작점이 span 시작이 아니다")
        self.assertNotIn("row.shares.at(-2)", code, "지난주 기준 비교가 돌아왔다")

    def test_panel_hidden_without_the_weekly_report(self):
        """'주간 판세'는 주간 리포트가 실제로 있을 때만 뜬다.

        원래 가드는 `!report && !questions.length` 였다. 그런데
        weekly_reports.json 이 3개월째 생성된 적이 없어(weekly.yml 미가동)
        실제로는 5칸 중 '아직 결론 나지 않은 것' 한 칸만 '주간 판세' 제목을
        달고 떠 있었다 — 제목이 약속한 것의 1/5. 그 한 칸의 문장도 근거 이슈
        제목의 서술문 전환에 가깝고(실측 유사도 0.32·0.48), 같은
        open_question 이 선두 카드와 상세 모달에 이미 나온다.
        """
        self.assertIn("if (!report) { panel.hidden = true; return; }", self.script)
        self.assertNotIn("if (!report && !questions.length)", self.script)
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


class ExploreHubTests(unittest.TestCase):
    """탐색 발견 허브 + ent 딥링크 엔티티 페이지의 배선 계약."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_hub_and_entity_header_exist_and_are_wired(self):
        for element_id in ("exploreHub", "entityHeader", "hubEntities"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn("function renderExploreHub", self.script)
        self.assertIn("function renderEntityHeader", self.script)
        self.assertIn("function handleHubAction", self.script)
        # 허브는 이슈 액션 위임(allowlist)이 아니라 전용 리스너를 쓴다.
        self.assertIn('["exploreHub", "entityHeader"].forEach', self.script)
        self.assertNotIn('"exploreHub", "entityHeader", "issueList"', self.script)

    def test_entities_json_is_loaded_with_catch(self):
        # 새 JSON fetch 는 반드시 .catch() — 8/1 빈 화면 사고 계약.
        self.assertIn('loadJSON("entities.json").catch(() => null)', self.script)

    def test_ent_param_round_trips(self):
        self.assertIn('params.set("ent", state.archiveEntity)', self.script)
        self.assertIn('params.get("ent")', self.script)
        # 잠금 라인은 바이트 그대로 남는다.
        self.assertIn('if (state.archiveQuery) params.set("q", state.archiveQuery);', self.script)

    def test_entity_filter_is_first_and_clears_with_the_rest(self):
        script = self.script
        matches = script.index("function archiveIssueMatches")
        self.assertIn("state.archiveEntity && !(issue.entity_ids || []).includes(state.archiveEntity)",
                      script[matches:matches + 600])
        clear = script.index("function clearArchiveFilters")
        self.assertIn('state.archiveEntity = "";', script[clear:clear + 400])

    def test_zero_count_entities_stay_out_of_the_hub(self):
        self.assertIn("entity.issue_count > 0", self.script)

    def test_hub_offers_only_what_nothing_else_offers(self):
        """허브에 남는 그룹은 '대상' 하나다.

        주제 칩은 바로 아래 주제 필터 셀렉트와, 국가·출처 칩은 통합 검색과
        같은 일을 했다. 칩을 한 번 누르면 필터가 걸려 허브가 통째로 숨으므로,
        중복 경로를 한 번 보여주려고 첫 화면을 쓴 셈이었다(8/3 '자주 찾는
        주제' 제거와 같은 판단). 원전·기업·기관만 검색 말고 둘러볼 경로가 없다.
        """
        for element_id in ("hubTopics", "hubCountries", "hubSources"):
            self.assertNotIn(element_id, self.html)
            self.assertNotIn(element_id, self.script)
        # 허브가 만드는 칩은 대상 하나뿐 — 죽은 분기(data-hub-q)도 남기지 않는다.
        hub = self.script[self.script.index("function renderExploreHub"):]
        hub = hub[:hub.index("function renderEntityHeader")]
        self.assertIn("data-hub-ent", hub)
        self.assertNotIn("data-hub-topic", hub)
        self.assertNotIn("data-hub-q", self.script)
        # 엔티티 헤더의 '자주 함께 등장한 주제'는 계속 주제 칩을 쓴다.
        self.assertIn("data-hub-topic", self.script)

    def test_recent_capture_wording_not_last_confirmed(self):
        # '마지막 확인'은 사용자 확인 시각으로 오독된다 — 보도 포착일은 '최근 포착'.
        self.assertIn("최근 포착", self.script)
        render = self.script[self.script.index("function renderEntityHeader"):]
        render = render[:render.index("function renderArchiveSearch")]
        self.assertNotIn("마지막 확인", render)

    def test_together_topics_need_three_issues(self):
        self.assertIn("connected.length >= 3", self.script)

    def test_new_copy_is_centralized(self):
        self.assertIn("const STRINGS = {", self.script)
        self.assertIn("ENTITY_TYPE_LABELS", self.script)

    def test_hub_chips_meet_touch_target(self):
        chip = self.style[self.style.index(".hub-chip {"):]
        chip = chip[:chip.index("}")]
        self.assertIn("min-height: 44px", chip)

    def test_tab_labels_renamed(self):
        self.assertIn(">탐색</button>", self.html)
        self.assertIn(">오늘</button>", self.html)
        self.assertNotIn(">이슈 아카이브<", self.html)


class SearchDialogTests(unittest.TestCase):
    """통합 검색의 즉시 결과·키보드·최근 검색 계약."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_results_listbox_is_wired(self):
        self.assertIn('id="globalSearchResults"', self.html)
        self.assertIn('role="listbox"', self.html)
        self.assertIn('aria-controls="globalSearchResults"', self.html)
        self.assertIn("function renderSearchResults", self.script)
        self.assertIn('"ArrowDown"', self.script)
        self.assertIn("aria-activedescendant", self.script)

    def test_scores_are_constants_not_judgement(self):
        self.assertIn("const SEARCH_SCORE = {", self.script)
        self.assertIn("issueTitleExact: 100", self.script)
        self.assertIn("entityAliasExact: 85", self.script)

    def test_submit_path_is_untouched_when_nothing_selected(self):
        # 무선택 Enter 는 기존 경로 그대로 — 이 넷은 한 덩어리로 남아야 한다.
        self.assertIn('state.archiveQuery = normalizedSearch(document.getElementById("globalSearch").value);', self.script)
        self.assertIn('placeholder="기관, 호기, 주제로 검색"', self.html)

    def test_recent_searches_are_editable_and_bounded(self):
        self.assertIn("nuclens-recent-searches", self.script)
        self.assertIn("data-recent-remove", self.script)
        self.assertIn("data-recent-clear", self.script)
        # 1글자·공백 미저장, MRU 8
        self.assertIn("value.length < 2", self.script)
        self.assertIn(".slice(0, 8)", self.script)

    def test_pub_search_reads_toc_briefs_with_single_snippet(self):
        self.assertIn("item.toc?.briefs", self.script)
        self.assertIn(".find(line => searchHit(line, variants))", self.script)

    def test_unit_suffix_query_falls_back_to_plant_name(self):
        self.assertIn("호기$", self.script)

    def test_reopen_makes_no_fetch(self):
        # 검색 렌더 경로에는 fetch 가 없어야 한다 — 재오픈 네트워크 0 계약.
        search_block = self.script[self.script.index("const SEARCH_SCORE"):self.script.index("function openGlobalSearch")]
        self.assertNotIn("fetch(", search_block)
        self.assertNotIn("loadJSON", search_block)


class BriefingTimelineTests(unittest.TestCase):
    """흐름 탭 '지난 브리핑' 목록의 배선 계약."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_timeline_exists_after_trend_charts(self):
        self.assertIn('id="briefingTimeline"', self.html)
        self.assertIn('id="briefingTimelineList"', self.html)
        # DOM 순서: weeklyReport → keywordTable → briefingTimeline (잠금 순서 뒤 append)
        self.assertLess(self.html.index('id="weeklyReport"'), self.html.index('id="keywordTable"'))
        self.assertLess(self.html.index('id="keywordTable"'), self.html.index('id="briefingTimeline"'))

    def test_renders_regardless_of_trend_ready(self):
        body = self.script[self.script.index("function renderTrend()"):]
        body = body[:body.index("\n}")]
        # 이른 return(trend_ready) 앞에서 그린다 — 조용한 날에도 시간 축은 남는다.
        self.assertLess(body.index("renderBriefingTimeline()"), body.index("trend_ready"))
        # 잠금 호출 순서는 그대로.
        self.assertLess(body.index("renderWeeklyReport()"), body.index("renderKeywordTable()"))

    def test_rows_jump_to_that_days_briefing(self):
        self.assertIn('data-go-date="${esc(briefing.date)}"', self.script)
        self.assertIn('briefingTimelineList").addEventListener', self.script)

    def test_empty_rows_state_only_what_data_says(self):
        block = self.script[self.script.index("function renderBriefingTimeline"):]
        block = block[:block.index("function renderTrend")]
        self.assertIn("생성된 브리핑이 없습니다", block)
        self.assertIn("below_floor_count", block)
        self.assertIn("pipeline_status", block)

    def test_tab_renamed_to_flow(self):
        self.assertIn(">흐름</button>", self.html)
        self.assertNotIn("주간 흐름", self.html)


class PubShelfTests(unittest.TestCase):
    """발간물 표지 서가의 계약 — CSS-only, 스파인은 장식(의미 없음)."""

    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")

    def test_cover_object_keeps_smoke_class(self):
        # render_smoke 가 #pubsList .pub-item 을 센다 — 클래스는 남는다.
        self.assertIn('class="pub-item pub-cover', self.script)

    def test_cover_height_follows_content(self):
        """표지 높이를 비율로 못 박지 않는다.

        1 / 1.35 고정은 제목 한 줄짜리와 다섯 줄짜리를 같은 상자에 넣어, 짧은
        쪽 표지의 21~57%(실측 67~182px)를 설명 없는 빈칸으로 남겼다. 서가의
        책은 저마다 두께가 다르다 — 높이는 내용이 정한다.
        되돌리려면 그 빈칸을 무엇으로 채울지부터 정할 것.
        """
        self.assertNotIn("aspect-ratio", self.style.split(".pub-cover .cover-face")[1][:400])
        # 바닥으로 밀던 auto 여백도 함께 사라져야 의미가 있다(밀 바닥이 없다).
        foot = self.style.split(".cover-foot {")[1][:260]
        self.assertNotIn("margin: auto", foot)

    def test_spine_colors_reuse_locked_palette_only(self):
        spines = re.findall(r"--spine:\s*([^;]+);", self.style)
        self.assertTrue(spines)
        for value in spines:
            self.assertRegex(value.strip(), r"^var\(--c-", f"스파인에 팔레트 밖 색: {value}")
        self.assertIn("const PUB_ORG_CLASS", self.script)
        for org in ("IAEA", "OECD-NEA", "KEEI", "EIA", "IEA"):
            self.assertIn(f'"{org}"', self.script)

    def test_cover_hover_answers_in_both_themes(self):
        """표지 hover 는 두 테마 모두에서 눈에 보이게 응답한다.

        원래는 ':root[data-theme="dark"] a.cover-face:hover { transform: none;'
        를 문자열로 잠갔다. 전제는 "다크는 --sh-* 가 none 이라 들려도 안 보이니
        보더를 밝혀 대신한다"였는데, 하드 오프셋 전환으로 그 전제가 거짓이 됐다 —
        --sh-* 가 --c-edge 를 따라가고 다크는 그것을 밝은 값으로 뒤집는다.
        지키려던 것은 폴백 규칙 자체가 아니라 '다크에서 hover 가 죽지 않는다'다.
        """
        hover = re.search(r"a\.cover-face:hover \{([^}]*)\}", self.style).group(1)
        self.assertIn("box-shadow: var(--sh-", hover)
        self.assertIn("transform:", hover)
        # 다크가 --sh-* 를 none 으로 되돌리면 들림이 통째로 사라진다.
        # 주석에도 토큰 이름이 나오므로 선언만 본다.
        dark = self.style.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
        dark = re.sub(r"/\*.*?\*/", "", dark, flags=re.S)
        self.assertNotRegex(dark, r"--sh-\d:", "다크가 그림자 토큰을 다시 덮어쓰면 hover 가 죽는다")
        self.assertIn("--c-edge:", dark)

    def test_reduced_motion_kills_the_lift(self):
        reduced = self.style[self.style.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn("a.cover-face:hover { transform: none;", reduced)

    def test_mobile_is_single_column_shelf_list(self):
        mobile = self.style[self.style.index("@media (max-width: 767px)"):]
        self.assertIn(".pubs-list { grid-template-columns: 1fr;", mobile)
        # 비율을 풀어 주던 모바일 예외는 이제 필요 없다 — 기본이 그렇다.
        self.assertNotIn("aspect-ratio", mobile)

    def test_new_marker_is_dot_plus_text(self):
        # 점만 있는 신규 표시는 의미를 설명하지 않는다 — 텍스트 병기 + 접근명.
        self.assertIn('aria-label="최근 14일 이내 발간"', self.script)
        self.assertIn("최근 발간", self.script)

    def test_intro_names_keei_in_full(self):
        self.assertIn("에너지경제연구원 — 제목과 원문 링크만 제공합니다.", self.html)


class SavedFollowTests(unittest.TestCase):
    """저장 탭 접근성·톰스톤·엔티티 팔로우의 계약."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_desktop_tab_reaches_saved_view(self):
        # 768~1199px 는 하단 탭도 사이드바도 없어 저장 뷰가 도달 불가였다.
        self.assertIn('id="headerSaved"', self.html)
        self.assertIn('data-go-saved', self.html)
        self.assertIn('id="search-saved"', self.html)
        # 저장은 탐색 안으로 합쳐졌고 모바일 탭은 4개다.
        mobile_nav = self.html.split('id="mobileTabs"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(mobile_nav.count("<button"), 4)

    def test_saved_meta_snapshot_and_tombstone(self):
        self.assertIn("nuclens-saved-meta", self.script)
        self.assertIn("function savedTombstone", self.script)
        self.assertIn("재구성되어 현재 목록에 없습니다", self.script)
        self.assertIn("data-requery", self.script)

    def test_follow_is_entity_only_with_per_entity_seen(self):
        self.assertIn("nuclens-follows", self.script)
        self.assertIn("nuclens-follow-seen", self.script)
        self.assertIn("function toggleFollow", self.script)
        self.assertIn("function entityNewIssueCount", self.script)
        # 배지 셈: 이슈 포착일(last_seen) > 사용자 확인일 — 사전순 날짜 비교.
        self.assertIn("issue.last_seen > seen", self.script)

    def test_saved_view_entry_does_not_mark_all_seen(self):
        # 저장 화면 진입만으로 전체 확인 처리 금지 — renderSaved/renderFollowPanel
        # 경로에 markEntitySeen 이 없어야 한다.
        for name in ("function renderSaved", "function renderFollowPanel"):
            body = self.script[self.script.index(name):]
            body = body[:body.index("\nfunction ")]
            self.assertNotIn("markEntitySeen", body, f"{name} 가 확인 처리를 한다")

    def test_entity_page_view_marks_seen_only_on_search_view(self):
        body = self.script[self.script.index("function renderEntityHeader"):]
        body = body[:body.index("function renderArchiveSearch")]
        self.assertIn('state.view === "search"', body)
        self.assertIn("markEntitySeen", body)

    def test_follow_toggle_does_not_reset_filters(self):
        body = self.script[self.script.index("function handleHubAction"):]
        head = body[:body.index("state.archiveQuery")]
        self.assertIn("data-follow-toggle", head)
        self.assertIn("return;", head)


class MotionTests(unittest.TestCase):
    """모션은 토큰 경유 + reduced-motion 일괄 무력화 계약."""

    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_js_motion_goes_through_the_helper(self):
        self.assertIn("function prefersReducedMotion", self.script)
        # 원시 smooth 리터럴 소멸 — 모든 JS 스크롤이 헬퍼의 삼항을 거친다.
        self.assertNotIn('behavior: "smooth"', self.script)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', self.script)

    def test_view_and_dialog_motion_use_tokens(self):
        self.assertIn(".view-in { animation: view-in var(--mo-2)", self.style)
        self.assertIn("dialog[open] { animation: dialog-in var(--mo-2)", self.style)
        self.assertIn("@keyframes view-in", self.style)

    def test_local_storage_reads_are_hardened(self):
        # JSON 을 읽는 모든 지점이 try/catch 아래에 있어야 한다 — 깨진 저장값이
        # 앱을 죽이면 안 된다. (theme 은 문자열 그대로라 파싱이 없다.)
        for key in ("nuclens-saved-issues", "nuclens-saved-meta",
                    "nuclens-follows", "nuclens-follow-seen", "nuclens-recent-searches",
                    "nuclens-recent-issues", "nuclens-last-visit"):
            index = self.script.index(f'localStorage.getItem("{key}"')
            self.assertIn("try {", self.script[max(0, index - 240):index],
                          f"{key} 읽기가 try 밖에 있다")


class TokenSystemTests(unittest.TestCase):
    """디자인 토큰 4계(간격·타입·모션·z)의 존재와, 토큰 도입이 기존 잠금을
    건드리지 않았음을 함께 검사한다."""

    @classmethod
    def setUpClass(cls):
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_token_scales_exist(self):
        for token in (
            "--sp-1:", "--sp-4:", "--sp-6:", "--sp-8:", "--sp-14:", "--sp-20:",
            "--t-min:", "--t-caption:", "--t-body:", "--t-card:",
            "--t-title:", "--t-lead:", "--t-hero:",
            "--mo-1:", "--mo-2:", "--mo-3:", "--mo-ease:", "--mo-ease-out:",
            "--z-pop:", "--z-topbar:", "--z-tabs:", "--z-scrim:",
            "--z-sheet:", "--z-toast:", "--z-skip:",
        ):
            self.assertIn(token, self.style, f"{token} 토큰이 없다")

    def test_spacing_scale_is_4px_multiples(self):
        for name, value in re.findall(r"--sp-(\d+):\s*(\d+)px", self.style):
            self.assertEqual(
                int(value), int(name) * 4,
                f"--sp-{name} 는 {int(name) * 4}px 여야 한다 (현재 {value}px)",
            )

    def test_hero_token_keeps_the_shrunken_decision(self):
        """--t-hero 는 3ff0907(히어로 축소)의 실측 결정을 박제한 값이다.
        58px 디스플레이 크기로 되돌리려면 이 테스트와 그 커밋 메시지를 먼저 읽을 것."""
        match = re.search(r"--t-hero:\s*clamp\(\s*[\d.]+px,\s*[^,]+,\s*([\d.]+)px\s*\)", self.style)
        self.assertIsNotNone(match, "--t-hero clamp 정의가 없다")
        self.assertLessEqual(float(match.group(1)), 30)

    def test_locked_foundations_survive_tokenization(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.style)
        self.assertIn("@media (min-width: 1200px)", self.style)
        self.assertIn("@media (max-width: 767px)", self.style)
        self.assertIn("--r-1: 0", self.style)
        self.assertIn("outline: 2px solid var(--c-focus);", self.style)
        self.assertIn("box-shadow: var(--fo-ring);", self.style)


class HardEdgeSystemTests(unittest.TestCase):
    """네오브루탈리즘 전환 — 눈에 보이는 것의 전부가 이 값들이다.

    문자열이 아니라 관계를 잠근다. 이 파일의 디자인 테스트가 두 번 깨진 이유는
    (`"padding: var(--sp-5)"` 같은) 리터럴을 붙잡아서였다 — 토큰 이름이 한 번
    바뀌면 지키려던 것과 무관하게 빨개진다. 여기서는 대비·blur·존재만 본다.
    """

    @classmethod
    def setUpClass(cls):
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_border_and_shadow_tokens_exist_in_both_themes(self):
        for token in ("--bd-1:", "--bd-2:", "--bd-3:", "--c-edge:", "--c-signal-ink:"):
            self.assertIn(token, self.style, f"{token} 토큰이 없다")
        # 다크가 --c-edge 를 뒤집지 않으면 하드 그림자가 어두운 종이에 묻혀 사라진다.
        dark = self.style.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
        self.assertIn("--c-edge:", dark, "다크 블록에 --c-edge 재정의가 없다")

    def test_shadows_are_hard_offsets_with_zero_blur(self):
        for step in (1, 2, 3):
            match = re.search(rf"--sh-{step}:\s*([^;]+);", self.style)
            self.assertIsNotNone(match, f"--sh-{step} 정의가 없다")
            self.assertRegex(
                match.group(1).strip(),
                r"^\d+px \d+px 0 var\(--c-edge\)$",
                f"--sh-{step} 는 blur 0 인 --c-edge 오프셋이어야 한다",
            )
        # 다크의 `--sh-*: none` 세 줄이 돌아오면 다크에서 상자 위계가 통째로 죽는다.
        self.assertNotIn("--sh-1: none", self.style)

    def test_edge_ink_clears_aa_in_both_themes(self):
        for theme, tokens in _theme_tokens(self.style).items():
            self.assertGreaterEqual(
                _contrast(tokens["c-edge"], tokens["c-bg"]), 7.0,
                f"{theme}: --c-edge 가 종이에서 떨어지지 않는다",
            )

    def test_signal_is_never_a_bare_boundary(self):
        """라임은 종이 위 1.20:1 이다 — 테두리·아웃라인으로 쓰면 경계가 안 보인다.

        블록으로만 쓰고 잉크 글자나 잉크 테두리를 반드시 동반한다(WCAG 1.4.11).
        느낌상 맞고 다크 스크린샷에서는 멀쩡해 보이기 때문에 사람 눈으로는 못 막는다.
        """
        for prop in (
            "border", "border-top", "border-right", "border-bottom",
            "border-left", "border-color", "outline",
        ):
            self.assertNotRegex(
                self.style, rf"[^-]{prop}:[^;]*var\(--c-signal\)",
                f"{prop} 에 --c-signal 을 썼다 — 라임은 경계선이 될 수 없다",
            )

    def test_ink_on_signal_clears_aa(self):
        tokens = _theme_tokens(self.style)["light"]
        self.assertGreaterEqual(
            _contrast(tokens["c-signal-ink"], tokens["c-signal"]), 4.5,
            "라임 슬래브 위 글자가 AA 에 못 미친다",
        )

    def test_ink_on_signal_is_pinned_not_inherited_from_the_theme(self):
        """라임 위에 글자를 얹는 규칙은 --c-signal-ink 를 써야 한다.

        --c-signal 은 두 테마에서 같은 #cce969 인데 --c-primary 는 테마마다
        갈라진다(#12251e / #1a3329). 라임 칩의 글자색으로 --c-primary 를 쓰면
        라이트에서는 우연히 --c-signal-ink 와 같은 값이라 통과하고, 다크에서만
        조용히 달라진다. 배경이 고정이면 글자도 고정이라야 한다 —
        --c-verified-on-primary 를 만든 것과 같은 이유다.

        글자가 없는 라임(탭 밑줄, 상태 LED, 모바일 활성 바)은 채움이지 슬래브가
        아니라 color 를 안 쓴다 — 여기서 걸러낸다. 경계선으로서의 라임은
        test_signal_is_never_a_bare_boundary 가 따로 막는다.
        """
        pattern = re.compile(r"([^{}]+)\{([^}]*background:\s*var\(--c-signal\)[^}]*)\}")
        blocks = pattern.findall(self.style)
        self.assertTrue(blocks, "라임 슬래브가 하나도 없다 — 라임이 구조색이 아니다")
        texted = [(sel, body) for sel, body in blocks if re.search(r"[^-]color:", body)]
        self.assertTrue(texted, "글자를 얹은 라임 블록이 하나도 없다")
        for selector, body in texted:
            self.assertRegex(
                body, r"[^-]color:\s*var\(--c-signal-ink\)",
                f"{selector.strip().splitlines()[-1].strip()} 의 라임 위 글자색이 고정되지 않았다",
            )

    def test_no_selector_list_dangles_into_an_at_rule(self):
        """쉼표로 끝난 선택자 목록은 바로 뒤의 @media 를 통째로 삼킨다.

        실제 사건(C1 작업 중): 쉼표 목록의 **마지막** 항목이던
        `:root[data-theme="dark"] .filter-tab.active { box-shadow: none; }` 를
        규칙째 지우면서 앞 줄의 쉼표가 남았다. 파서는 다음 토큰인
        `@media (min-width: 1200px)` 를 선택자의 일부로 먹었고, 그 블록이
        CSSOM 에서 사라져 사이드바가 1440px 에서 숨었다. railIsActive() 가
        사이드바 computed display 를 읽으므로 선두 카드가 해석 블록을 다시
        펼쳤고 첫 행이 82px 아래로 밀렸다.

        브레이스도 주석도 균형이 맞아 눈으로도 린트로도 안 걸린다.
        """
        stripped = re.sub(r"/\*.*?\*/", "", self.style, flags=re.S)
        self.assertNotRegex(
            stripped, r",\s*@",
            "선택자 목록이 쉼표로 끝난 채 at-rule 을 만난다 — 그 블록이 통째로 죽는다",
        )
        self.assertNotRegex(
            stripped, r",\s*}",
            "선택자 목록이 쉼표로 끝난 채 블록이 닫힌다",
        )

    def test_no_color_hex_hides_inside_a_media_query_root(self):
        """미디어쿼리 :root 안에 --c-* hex 를 두면 대비 테스트가 조용히 뒤집힌다.

        test_muted_text_meets_wcag_aa_on_paper 는
        `dict(re.findall(r"--([\\w-]+):\\s*(#[0-9a-fA-F]{6})", css))` 를 **파일
        전체**에 돌린다 — 나중 값이 앞을 덮어쓰므로, 모바일 블록에 색 hex 를 하나
        두면 그 테스트가 재는 배경이 바뀌어 버린다. 실패하지도 않고, 다른 쌍을
        재기 시작할 뿐이다.

        모바일에서 토큰을 강등하는 것 자체는 옳다(--bd-3 3 → 2px, 그림자 축소).
        단위 없는/px 토큰만 두면 정규식이 못 보므로 안전하다.
        """
        for match in re.finditer(r"@media[^{]*\{", self.style):
            start = match.end()
            depth, index = 1, start
            while depth and index < len(self.style):
                if self.style[index] == "{":
                    depth += 1
                elif self.style[index] == "}":
                    depth -= 1
                index += 1
            body = self.style[start:index]
            for root in re.finditer(r":root\s*\{([^}]*)\}", body):
                self.assertNotRegex(
                    root.group(1), r"--[\w-]+:\s*#[0-9a-fA-F]{6}",
                    "미디어쿼리 :root 에 색 hex 가 있다 — 대비 테스트가 이걸 마지막 값으로 읽는다",
                )

    def test_mobile_block_is_the_only_one_and_comes_first(self):
        """모바일 규칙은 블록 하나에 모은다 — 테스트 셋이 위치로 잘라 읽는다.

        test_chrome_is_ink_not_page_tinted · test_mobile_topbar_keeps_the_site_descriptor ·
        test_mobile_is_single_column_shelf_list 는 전부
        ``style.index("@media (max-width: 767px)")`` 또는 ``split(..., 1)[1]`` 로
        **처음 나오는 위치**부터를 모바일 규칙 전체로 본다. 그 앞에 두 번째
        모바일 블록을 만들거나, 심지어 그 문자열을 주석에 적기만 해도 세 테스트가
        엉뚱한 구간을 검사하고 한꺼번에 실패한다 — 실제로 흐름 탭 작업에서 이렇게
        4건이 동시에 깨졌고, 원인이 '그 함정을 경고하려고 쓴 주석'이었다.

        가로모드 변형(``and (orientation: landscape)``)은 조건이 더 붙어 뒤에
        오므로 여는 괄호 직전 문자열로 구분한다.
        """
        opener = "@media (max-width: 767px) {"
        self.assertEqual(
            self.style.count(opener), 1,
            "모바일 블록이 하나가 아니다 — 규칙은 기존 블록 안에 넣어야 한다",
        )
        # 주석에 문자열만 적어도 앞자리를 뺏는다. 처음 나온 자리가 곧 블록이어야 한다.
        self.assertEqual(
            self.style.index("@media (max-width: 767px)"), self.style.index(opener),
            "여는 블록보다 앞에 같은 문자열이 있다(주석 포함) — 슬라이스가 어긋난다",
        )

    def test_radius_escapes_are_closed(self):
        """--r-* 를 0 으로 잠가놓고 리터럴 라운드가 여섯 군데 살아 있었다.

        4px ×3(세그먼티드 버튼·kbd·스켈레톤), 2px ×1(차트 범례), 16px ×2(모바일
        바텀시트). 파일 주석은 "이 사이트에서 둥근 것은 상태 점(LED)뿐"이라고
        적어놨지만 사실이 아니었다 — 토큰만 잠그면 리터럴로 새는 걸 못 막는다.

        허용: var(--r-*) · 0 · 50%(LED·아바타 같은 진짜 원) · inherit.
        """
        allowed = re.compile(r"^(0|50%|inherit|(var\(--r-\d\)|0)( (var\(--r-\d\)|0)){0,3})$")
        for value in re.findall(r"border-radius:\s*([^;]+);", self.style):
            value = value.strip()
            self.assertRegex(
                value, allowed,
                f"토큰 밖 라운드: border-radius: {value}",
            )

    def test_the_page_has_exactly_one_box(self):
        """상자는 선두 카드 하나뿐이다.

        상자가 하나면 위계지만 둘이 되는 순간 배경이 된다. 목록 행·배지가
        그림자를 얻으면 스캔 목록이 스티커 시트가 되고, 그게 이 방향이 실패하는
        가장 흔한 방식이다. 행은 형제끼리 border-top 으로만 갈린다.
        """
        for selector in (".issue-card", ".verification-badge", ".report-pick-badge"):
            match = re.search(rf"^\{selector} \{{([^}}]*)\}}", self.style, re.M)
            self.assertIsNotNone(match, f"{selector} 규칙을 못 찾았다")
            self.assertNotIn(
                "box-shadow", match.group(1),
                f"{selector} 에 그림자가 붙었다 — 반복 요소는 형제를 덮는다",
            )


class FacilityEntitySignalTests(unittest.TestCase):
    """설비·프로젝트 엔티티 공유는 LLM 검수 우선순위 신호다(병합 판정은 아니다).

    재현 사건(2026-08-05 라이브): 팍스 원전 가뭄 클러스터와 그 후속 보도가 코사인
    0.8716 로 검수 밴드[0.84, 0.92] 안에 있었는데, 그날 검수 20쌍이 전부 429 로
    죽어 판정이 없었고 후속이 신규 이슈로 갈라졌다. 사용자 지적 "팔로잉이 안 된다".

    실측 근거(판정 완료 185쌍): 설비·프로젝트 공유 3건 전부 같은 사건·오탐 0.
    기관·기업까지 넣으면 40건 중 3건이라 판별력이 사라진다.
    """

    @classmethod
    def setUpClass(cls):
        registry = build_data.load_entity_registry()
        cls.aliases = build_data.facility_alias_entries(registry)

    def _article(self, article_hash, title, countries):
        return {"hash": article_hash, "title_kr": title, "summary": "",
                "topics": [], "tags": [], "countries": countries}

    def test_registry_narrows_to_plants_and_projects(self):
        registry = build_data.load_entity_registry()
        narrowed = [e for e in registry if e["type"] in build_data.FOLLOW_UP_ENTITY_TYPES]
        self.assertTrue(narrowed)
        self.assertLess(len(narrowed), len(registry), "기관·기업이 걸러지지 않았다")

    def test_paks_follow_up_pair_shares_a_facility(self):
        left = self._article("h1", "헝가리, 가뭄으로 팍스 원전 가동 중단 위기 직면", ["HU"])
        right = self._article("h2", "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표", ["HU"])
        entities = build_data.facility_entities_by_hash([left, right], self.aliases)
        _matched, _score, diagnostics = build_data.issue_similarity(
            left, right, None, None, entities)
        self.assertEqual(diagnostics["shared_facility_entities"], ["paks"])

    def test_shared_regulator_is_not_a_facility_signal(self):
        """NRC 를 신호로 쓰면 미국 규제 기사가 전부 한 이슈로 묶인다(실측 오탐 40건)."""
        left = self._article("h3", "미국 NRC, 방사성 물질 운송 규정 현대화 제안 및 의견 수렴", ["US"])
        right = self._article("h4", "미국 NRC, 환경영향평가 규정 개정 제안 규칙 공청회 개최", ["US"])
        entities = build_data.facility_entities_by_hash([left, right], self.aliases)
        _matched, _score, diagnostics = build_data.issue_similarity(
            left, right, None, None, entities)
        self.assertEqual(diagnostics["shared_facility_entities"], [])

    def test_facility_share_alone_does_not_merge(self):
        """표본 3건짜리 신호로 병합하면 같은 발전소의 다른 사건이 합쳐진다."""
        left = self._article("h5", "팍스 원전 2호기 계획예방정비 착수", ["HU"])
        right = self._article("h6", "팍스 원전 신규 부지 환경영향평가 공청회", ["HU"])
        entities = build_data.facility_entities_by_hash([left, right], self.aliases)
        matched, _score, diagnostics = build_data.issue_similarity(
            left, right, None, None, entities)
        self.assertEqual(diagnostics["shared_facility_entities"], ["paks"])
        self.assertFalse(matched, "설비 공유만으로 병합되면 안 된다")

    def test_signal_is_optional(self):
        """facility_entities 를 안 주는 기존 호출부가 깨지면 안 된다."""
        left = self._article("h7", "팍스 원전 가동 중단", ["HU"])
        right = self._article("h8", "팍스 원전 재가동", ["HU"])
        _matched, _score, diagnostics = build_data.issue_similarity(left, right)
        self.assertEqual(diagnostics["shared_facility_entities"], [])


class PublicationRelevanceTests(unittest.TestCase):
    """발간물을 정책·시장 / 기술문서로 갈라 기술문서만 접는다.

    실측 2026-08-05 라이브: off_topic 게이트(행사·농업)를 통과한 19건 중 12건이
    전산유체역학 코드 검증·붕괴열 시뮬레이션·흑연 조사 크리프·계측제어 요구공학
    같은 **연구·설계 실무자용** 문서였고, 그것이 서가 앞줄을 차지해 정책 자료가
    묻혔다. 원자력 문서가 맞으니 지울 수는 없다 — 접는다.
    """

    def test_llm_verdict_wins_over_the_title_rule(self):
        """규칙이 LLM 판정을 뒤집으면 안 된다(off_topic 과 같은 계약)."""
        item = {"title": "Decay heat simulation benchmark", "relevance": "policy"}
        self.assertEqual(build_data.publication_relevance(item), "policy")

    def test_title_rule_is_the_fallback_when_no_verdict(self):
        """v2 캐시가 남아 있는 동안 화면이 먼저 정리되게 하는 폴백."""
        item = {"title_kr": "원자로 안전 문제에 대한 전산유체역학(CFD) 코드 검증"}
        self.assertEqual(build_data.publication_relevance(item), "technical")

    def test_unknown_verdict_falls_back_rather_than_crashing(self):
        item = {"title": "SMR deployment", "relevance": "완전히 모르는 값"}
        self.assertEqual(build_data.publication_relevance(item), "policy")

    def test_ambiguous_items_are_not_folded(self):
        """잘못 접는 쪽이 해롭다 — 판단이 안 서면 앞줄에 남긴다."""
        item = {"title": "Nuclear Law Institute opens applications"}
        self.assertEqual(build_data.publication_relevance(item), "policy")

    def test_policy_titles_are_not_folded_by_the_rule(self):
        for title in ("소형모듈원자로(SMR) 가속화",
                      "중국의 원자력 발전 용량, 2016년 이후 거의 두 배 증가",
                      "[격주간] 세계 원전시장 인사이트(2026.07.24.)"):
            self.assertEqual(build_data.publication_relevance({"title_kr": title}),
                             "policy", title)

    def test_generated_publications_carry_relevance(self):
        data = json.loads((DATA_DIR / "publications.json").read_text(encoding="utf-8"))
        self.assertIn("relevance_counts", data)
        for item in data["items"]:
            self.assertIn(item.get("relevance"), build_data.PUBLICATION_RELEVANCE_VALUES)


class PublicationFoldRenderTests(unittest.TestCase):
    """접힘 UI 계약 — 렌더러·CSS 양쪽."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_renderer_folds_only_technical(self):
        self.assertIn('item.relevance === "technical"', self.app)
        self.assertIn("pub-technical-shelf", self.app)

    def test_fold_spans_the_shelf_grid(self):
        """서가 그리드의 자식이라 열을 넘기지 않으면 한 칸에 찌그러진다."""
        self.assertIn(".pub-technical { grid-column: 1 / -1", self.style)

    def test_summary_draws_its_own_marker(self):
        """display:flex 가 기본 펼침 마커를 지운다 — 실측으로 잡은 회귀."""
        self.assertIn(".pub-technical > summary::before", self.style)
        self.assertIn(".pub-technical[open] > summary::before", self.style)

    def test_summary_meets_the_mobile_touch_target(self):
        self.assertIn("min-height: 44px", self.style.split(".pub-technical > summary")[1][:400])


class FirstScreenContentFirstTests(unittest.TestCase):
    """첫 화면은 도구(플레이어)가 아니라 콘텐츠(선두 이슈)로 시작한다 (2026-08-06).

    실측: 날짜 바로 아래에 오디오 플레이어 두 줄이 첫 콘텐츠로 섰고, 선두
    이슈의 제목은 화면 어디에도 없었다(h1 이 sr 전용 날짜가 되면서 leadCard 의
    제목 억제 전제가 죽은 코드로 남은 탓). 읽을 것이 먼저, 들을지는 그다음 선택.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_lead_card_always_carries_its_title(self):
        """선두 카드의 h3 는 조건 없이 선다.

        예전 억제 조건(sameAsHeadline)은 히어로 h1 이 이슈 제목을 싣던 시절의
        것이다. h1 이 날짜 라벨이 된 지금 이 h3 가 제목이 서는 유일한 자리다 —
        조건이 돌아오면 headline_kind="issue" 인 날(대부분) 제목이 또 사라진다.
        """
        lead = self.script.split("function leadCard(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('issue-title-button', lead)
        self.assertNotIn("sameAsHeadline", self.script)
        title_line = next(line for line in lead.splitlines() if "issue-title-button" in line)
        self.assertNotIn("? ", title_line.split("<h3>")[0], "제목 렌더에 조건이 다시 붙었다")

    def test_audio_sits_below_the_hero_actions(self):
        """플레이어는 히어로의 마지막 줄이다 — 날짜와 콘텐츠 사이에 끼지 않는다."""
        self.assertLess(self.html.index('class="hero-actions"'),
                        self.html.index('id="audioBrief"'))

    def test_audio_rates_stay_folded_until_playback_on_mobile(self):
        """좁은 화면의 배속 세그먼트는 재생 시작 후에만 펼쳐진다.

        재생 전 두 줄 ~130px 는 첫 화면의 첫 콘텐츠가 플레이어라는 뜻이다.
        세그먼트 마크업 자체는 그대로다(8/5 피드백: 순환 버튼 금지) — 접는 건
        노출 시점뿐. 날짜를 옮기면 새 음원이므로 접힌 상태로 되돌린다.
        """
        self.assertIn(".hero-audio:not(.started) .hero-audio-rates { display: none; }",
                      self.style)
        self.assertIn('classList.add("started")', self.script)
        self.assertIn('classList.remove("started")', self.script)

    def test_mobile_topbar_keeps_the_site_descriptor(self):
        """모바일 상단바에도 '무슨 사이트인가' 한 줄이 선다.

        처음 링크를 받아 여는 화면이 모바일인데 정체성 문장이 데스크톱에만
        있었다. 태블릿(≤1100px)은 탭과 폭을 다투니 접힌 채 둔다.
        """
        mobile = self.style.split("@media (max-width: 767px)", 1)[1]
        brand_small = mobile.split(".brand-copy small {", 1)[1].split("}", 1)[0]
        self.assertIn("display: block", brand_small)


class RevisitPathTests(unittest.TestCase):
    """재방문 가치 — 최근 본 이슈 · '지난 확인 이후' 요약 · 행 전체 클릭.

    셋 다 클라이언트 전용(localStorage)이라 빌드 산출물 없이 소스만 검사한다.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_recent_issue_trail_is_wired(self):
        # 기록: 다이얼로그·근거 패널 두 열람 경로 모두에서 남는다.
        dialog = self.script.split("function openIssueDialog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("recordRecentIssue(", dialog)
        action = self.script.split("function handleIssueAction(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("recordRecentIssue(", action)
        # 표시: 저장 탭에서 그리고, 클릭 위임 목록에 컨테이너가 올라 있어야
        # 산다(위임 목록 누락은 이 저장소의 단골 사고 경로다).
        self.assertIn('"recentIssueList"', self.script)
        self.assertIn('id="recentPanel"', self.html)
        # 발자취는 저장이 아니다 — 사라진 id 는 톰스톤 없이 떨어진다.
        render = self.script.split("function renderRecentIssues(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn(".filter(Boolean)", render)
        # 새 키는 README 표에 문서화한다(기존 키들과 같은 계약).
        self.assertIn("nuclens-recent-issues", self.readme)
        self.assertIn("nuclens-last-visit", self.readme)

    def test_return_note_shows_once_per_visit(self):
        render = self.script.split("function renderReturnNote(", 1)[1].split("\nfunction ", 1)[0]
        # 판정보다 먼저 기준점을 오늘로 옮긴다 — 그래야 한 방문에 한 번만 뜬다.
        self.assertLess(render.index('setItem("nuclens-last-visit"'), render.index("box.hidden"))
        # 셀 것이 하나도 없으면 조용히 사라진다. 빈 배너는 공지가 아니라 소음이다.
        self.assertIn("box.hidden = true", render)
        # 이동 버튼은 실제 놓친 브리핑이 있을 때만 선다(죽은 컨트롤 금지).
        self.assertIn("missed.length > 1", render)

    def test_issue_row_click_defers_to_title_button(self):
        # 행 전체 클릭은 제목 버튼에 위임한다 — 경로가 갈라지면 rail/다이얼로그
        # 분기가 두 곳에 복제된다.
        action = self.script.split("function handleIssueAction(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('closest("[data-issue-card]")', action)
        self.assertIn('closest("a, button")', action)
        self.assertIn("isCollapsed", action, "드래그 선택을 클릭으로 오인하면 안 된다")
        # 커서는 실제로 클릭되는 행에만 준다.
        self.assertIn(".issue-card[data-issue-card] { cursor: pointer; }", self.style)

    def test_keyword_table_drops_zero_rows(self):
        # 실측: '한수원 0 0 0 신규 근거 0건' — 언급 0·전주 0 행이 신규 배지와
        # 근거 버튼까지 달고 표에 섰다. 0·0 행은 정보가 아니다.
        rows = self.script.split("function keywordRows(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("row.now > 0 || row.prev > 0", rows)

    def test_trend_intro_wraps_overline_and_title(self):
        # .trend-intro 는 flex space-between — 래퍼 없이 두 형제를 두면
        # 오버라인과 h1 이 화면 양끝으로 갈라진다(1440px 실측 사고).
        intro = self.html.split('class="trend-intro"', 1)[1].split("</h1>", 1)[0]
        self.assertIn("<div>", intro)


class FirstScreenDensityTests(unittest.TestCase):
    """첫 화면에 목록이 보여야 한다.

    실측(1440×900): 선두 카드가 564px 이라 이슈 행이 **0개** 보였다. 원인은
    카드가 해석 5블록을 통째로 들고 있었기 때문이고, 그렇게 된 이유는 근거
    패널이 (중복을 피하려고) 다른 이슈를 보여주고 있었기 때문이다.
    패널이 선두 이슈를 맡으면 카드는 사실만 남기면 된다 → 290px, 2행 노출.
    """

    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

    def test_rail_defaults_to_the_lead_issue(self):
        sidebar = self.script.split("function renderBriefingSidebar(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("state.railIssueId = leadId", sidebar)
        # 예전 규칙(선두를 피해 그다음 이슈를 잡는다)이 돌아오면 안 된다.
        self.assertNotIn("issue.issue_id !== leadId", sidebar)

    def test_lead_card_drops_interpretation_only_where_the_rail_shows_it(self):
        lead = self.script.split("function leadCard(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('group: "fact"', lead)
        self.assertIn('group: "read"', lead)
        # 접는 조건은 폭이 아니라 '패널이 실제로 보이는가' 다 — 값이 두 곳에
        # 있으면 갈라진다(railIsActive 는 렌더 결과를 직접 본다).
        self.assertIn("railIsActive()", lead)
        self.assertIn('block.group === "fact"', lead)

    def test_narrow_widths_keep_every_block(self):
        """패널이 없는 폭에서 해석이 사라지면 2026-08-03 모바일 감사의 재발이다.

        조건이 railIsActive() 하나이므로 패널이 없으면 자동으로 전부 선다.
        경계를 넘을 때 다시 그리지 않으면 리사이즈한 사용자만 해석을 잃는다.
        """
        self.assertIn('matchMedia("(min-width: 1200px)")', self.script)
        self.assertIn("railScreen.addEventListener", self.script)
        # CSS 쪽 경계값과 같은 숫자를 쓰는지 — 어긋나면 해석이 증발하는 구간이 생긴다.
        self.assertIn("@media (min-width: 1200px)", self.style)


class VisualSystemTests(unittest.TestCase):
    """2026-08-06 시각 개편 — 잉크 크롬 · 대비 사다리 · 구역 번호 · 목록 밀도.

    이 저장소는 리디자인이 "체감 안 됨"으로 두 번 되돌아왔다(8/3 `243f84e`,
    8/5 `3c6d828`). 원인은 매번 같았다: 기능은 늘었는데 화면의 뼈대(팔레트·
    크롬·밀도)가 그대로였다. 그래서 여기서는 **눈에 보이는 값**을 잠근다.
    """

    @classmethod
    def setUpClass(cls):
        cls.style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")

    def _rule(self, selector):
        """선택자 하나의 선언 블록을 문자열로 꺼낸다(첫 번째 정의 기준)."""
        index = self.style.index(selector + " {")
        return self.style[index:self.style.index("}", index)]

    def test_chrome_is_ink_not_page_tinted(self):
        """상단바·하단 탭은 지면이 아니라 크롬이다 — 배경과 같은 톤에 blur 만
        얹으면 스크롤할 때 도구와 내용의 경계가 사라진다."""
        topbar = self._rule(".topbar")
        self.assertIn("background: var(--c-primary)", topbar)
        self.assertNotIn("backdrop-filter", topbar)
        mobile = self.style.split("@media (max-width: 767px)", 1)[1]
        tabs = mobile[mobile.index(".mobile-tabs {"):]
        self.assertIn("background: var(--c-primary)", tabs[:tabs.index("}")])

    def test_mobile_footer_content_clears_fixed_tabs(self):
        mobile = self.style.split("@media (max-width: 767px)", 1)[1]
        footer = mobile[mobile.index(".foot {"):mobile.index("}", mobile.index(".foot {"))]
        self.assertIn("padding-bottom: calc(68px + env(safe-area-inset-bottom))", footer)
        self.assertIn("margin-bottom: calc(-68px - env(safe-area-inset-bottom))", footer)

    def test_focus_ring_survives_on_ink_chrome(self):
        """포커스 링 안쪽 고리는 배경색으로 링을 띄운다. 잉크 크롬 위에서
        밝은 --c-bg 가 그대로 오면 흰 테가 둘러쳐진다."""
        self.assertIn(".topbar :focus-visible", self.style)
        self.assertIn(".mobile-tabs :focus-visible", self.style)
        index = self.style.index(".mobile-tabs :focus-visible")
        self.assertIn("var(--c-primary)", self.style[index:index + 160])

    def test_paper_and_surface_are_separated(self):
        """종이(--c-bg)와 표면(--c-surface)이 붙어 있으면 패널·입력·표지가
        배경에서 떨어지지 않는다. 예전 값은 명도차가 4% 안이었다."""
        def token(name):
            return re.search(rf"--{name}:\s*(#[0-9a-f]{{3,6}})", self.style).group(1)
        gap = _luminance("#ffffff" if token("c-surface") == "#fff" else token("c-surface")) \
            - _luminance(token("c-bg"))
        self.assertGreater(gap, 0.08, "표면이 종이에서 충분히 떨어지지 않는다")

    def test_sections_are_numbered_not_repeated_overlines(self):
        """같은 오버라인 세 벌 대신 모노 번호로 구역을 가른다.

        오버라인 어휘 자체는 TODAY·THIS WEEK 두 종으로 잠겨 있고(별도 테스트),
        여기서는 '한 화면에서 되풀이하지 않는다'를 지킨다.
        """
        self.assertEqual(self.html.count('class="eyebrow">TODAY'), 1)
        self.assertGreaterEqual(self.html.count('class="sec-no"'), 5)
        self.assertIn("font-family: var(--ff-mono)", self._rule(".sec-no"))
        # 구역 머리는 잉크 괘선으로 시작한다 — 번호만 붙이면 목록의 일부로 읽힌다.
        #
        # 폭은 --bd-* 로 풀어서 재고, 색은 잉크 계열이면 통과시킨다. 예전에는
        # "border-top: 2px solid var(--c-primary)" 를 문자열로 붙잡았는데,
        # 그러면 토큰 이름 한 번 바뀔 때마다 지키려던 것과 무관하게 빨개진다.
        # 지키려는 것은 '구역이 잉크 괘선으로 열린다', 그것뿐이다.
        widths = dict(re.findall(r"--bd-(\d):\s*(\d+)px", self.style))
        rule = self._rule(".section-heading:not(.compact)")
        match = re.search(
            r"border-top:\s*(?:var\(--bd-(\d)\)|(\d+)px)\s+solid\s+var\(--c-([\w-]+)\)",
            rule,
        )
        self.assertIsNotNone(match, f"구역 머리에 잉크 괘선이 없다: {rule}")
        width = int(widths[match.group(1)]) if match.group(1) else int(match.group(2))
        self.assertGreaterEqual(width, 2, "구역 괘선이 헤어라인으로 내려앉으면 목록과 안 갈린다")
        self.assertIn(match.group(3), {"primary", "primary-strong", "edge"})

    def test_issue_rows_are_dense_and_react_as_one(self):
        """행 높이를 정하던 두 값(세로 액션 스택·32px 패딩)을 되돌리지 않는다.

        예전에는 `"padding: var(--sp-5)"` 를 문자열로 붙잡았다. 그러면 토큰
        이름이 한 번 바뀔 때마다 지키려던 것과 무관하게 빨개진다. 지키려는 건
        숫자 20 이 아니라 **행 높이 예산**이다 — 1440×900 에서 목록 영역이 받는
        높이가 약 285px 이라, 표준 행(제목 한 줄 + 요약 한 줄 + 사유 칩)이 130px 을 넘으면
        두 번째 행이 폴드 아래로 내려간다. 그래서 계산으로 잠근다.
        """
        tokens = {
            name: int(value)
            for name, value in re.findall(r"--sp-(\d+):\s*(\d+)px", self.style)
        }
        tokens.update({
            f"bd{name}": int(value)
            for name, value in re.findall(r"--bd-(\d):\s*(\d+)px", self.style)
        })

        def px(raw):
            raw = raw.strip()
            token = re.fullmatch(r"var\(--sp-(\d+)\)", raw)
            if token:
                return tokens[token.group(1)]
            token = re.fullmatch(r"var\(--bd-(\d)\)", raw)
            if token:
                return tokens[f"bd{token.group(1)}"]
            return int(re.fullmatch(r"(\d+)px", raw).group(1))

        card = self._rule(".issue-card")
        pad = px(re.search(r"padding:\s*([^\s;]+)", card).group(1))
        border = px(re.search(r"border-top:\s*([^\s;]+)\s+solid", card).group(1))
        title_size = px(re.search(r"--t-card:\s*([\d.]+px)", self.style).group(1))
        h3 = self._rule(".issue-card h3")
        gap = px(re.search(r"margin:\s*0 0 (\d+px)", h3).group(1))
        title_lh = float(re.search(r"line-height:\s*([\d.]+)", h3).group(1))
        # 카드 본문은 라벨 붙은 세 칸(.issue-line)이다. 예전엔 요약 한 줄이라
        # .issue-summary 를 읽었는데, 그 규칙은 이제 카드에 안 쓰인다.
        line_rule = re.search(r"^\.issue-line \{([^}]*)\}", self.style, re.M).group(1)
        body_lh = float(re.search(r"line-height:\s*([\d.]+)", line_rule).group(1))
        line_gap = px(re.search(r"margin:\s*0 0 (\d+px)", line_rule).group(1))
        body_size = px(re.search(r"--t-body:\s*([\d.]+px)", self.style).group(1))
        topic_chip = self._rule(".topic-chip")
        reason_chip = self._rule(".issue-reason-chip.topic-chip")
        min_size = float(re.search(r"--t-min:\s*([\d.]+)px", self.style).group(1))
        chip_lh = float(re.search(r"line-height:\s*([\d.]+)", reason_chip).group(1))
        chip_pad = float(re.search(r"padding:\s*([\d.]+)px", topic_chip).group(1))
        chip_border = px(re.search(r"border:\s*([^\s;]+)\s+solid", topic_chip).group(1))
        chip_height = min_size * chip_lh + chip_pad * 2 + chip_border * 2
        self.assertLessEqual(chip_height, 26, "사유 칩이 26px 밀도 계약을 넘는다")
        self.assertIn("margin: 0", reason_chip)
        self.assertNotIn("topic-row", self._rule(".issue-reason-row"))

        # 130px 계약은 카드가 요약 한 줄이던 시절의 것이다. 2026-08-08 개편으로
        # 카드는 제목 2줄 + 라벨 붙은 세 칸 + 사유 칩이 됐고, 그 구조에서 130px 은
        # 산술적으로 불가능하다. 계약을 완화한 게 아니라 새 구조로 다시 계산했다 —
        # 상한을 지우면 다음 사람이 네 번째 칸을 얹는다.
        title_lines, body_lines = 2, 3
        height = (pad * 2 + border + title_size * title_lh * title_lines + gap
                  + (body_size * body_lh + line_gap) * body_lines + chip_height)
        self.assertLessEqual(
            height, 220,
            f"세 칸이 다 찬 카드가 {height:.0f}px — 220px 을 넘으면 폴드에 두 장이 안 들어온다",
        )
        # 라벨 열이 접히면 역할 구분이 사라진다.
        self.assertIn("white-space: nowrap", self._rule(".issue-line-label"))
        # 각 칸은 정확히 한 줄. 문단 두 개가 연달아 서는 것을 CSS 에서 막는다.
        self.assertIn("-webkit-line-clamp: 1", self._rule(".issue-line-text"))

        actions = self._rule(".issue-list .issue-card .issue-actions")
        self.assertIn("flex-direction: row", actions, "액션이 다시 세로로 쌓인다")
        # hover 는 세 값이 함께 움직인다 — 하나만 변하면 '켜졌나' 싶다.
        hover = self._rule(".issue-card:hover")
        self.assertRegex(hover, r"border-top-color:\s*var\(--c-(primary|edge)\)")
        self.assertIn("background:", hover)
        self.assertIn(".issue-card:hover .issue-index", self.style)

    def test_tab_underline_animates_without_layout_shift(self):
        """활성 표시는 나타났다 사라지는 요소가 아니라 열고 닫는 선이다."""
        base = self._rule(".main-tab::after")
        self.assertIn("transform: scaleX(0)", base)
        self.assertIn("transition: transform var(--mo-2)", base)
        self.assertIn(".main-tab.active::after { transform: scaleX(1); }", self.style)



class ArticleDetailSurfacesTests(unittest.TestCase):
    """원문 요지(detail)가 수집에서 화면까지 살아서 도착하는가.

    사용자 요구(2026-08-07): "실제 기사들이 영문으로 되어있는 경우가 많아서 실제를
    들어가서 보기 어려운 경우가 많거든." 그래서 원문에 안 들어가도 되는 분량을
    만들었는데, **화면까지 배선되지 않으면 데이터에만 있고 아무도 못 본다** —
    이 저장소에서 selection_reasons 가 실제로 그랬다(생성만 되고 한 번도 노출 안 됨).
    """

    def test_pick_detail_prefers_the_representative_then_the_newest(self):
        # 요지는 제 기사 제목과 겹쳐야 통과한다(usable_detail) — 픽스처도
        # 실제 기사처럼 어휘를 공유하게 둔다.
        old = {"article_date": "2026-08-01", "title_kr": "옛 기사 팍스 원전 점검",
               "detail": "옛 기사 팍스 원전 점검이 시작됐다는 요지다."}
        new = {"article_date": "2026-08-06", "title_kr": "새 기사 팍스 원전 재가동",
               "detail": "새 기사 팍스 원전 재가동이 확정됐다는 요지다."}
        representative = {"title_kr": "대표 팍스 원전 기사",
                          "detail": "대표 팍스 원전 기사의 요지다."}

        detail, source = build_data.pick_detail([old, new], representative)
        self.assertEqual(detail, "대표 팍스 원전 기사의 요지다.")
        # 대표 기사면 출처를 적지 않는다 — 그 제목이 바로 위 h2 다.
        self.assertEqual(source, "")

        # 대표에 요지가 없으면 **가장 최신** 기사에서 가져온다. 오래된 멤버를
        # 쓰면 제목은 새 사건인데 내용은 옛 상태인 조합이 나온다.
        detail, source = build_data.pick_detail([old, new],
                                                {"title_kr": "대표 팍스 원전 기사"})
        self.assertEqual(detail, "새 기사 팍스 원전 재가동이 확정됐다는 요지다.")
        self.assertEqual(source, "새 기사 팍스 원전 재가동")

    def test_missing_detail_is_not_an_error(self):
        # 2026-08-07 이전 아카이브에는 detail 이 없다. 빈 값이 정상이다.
        self.assertEqual(build_data.pick_detail([{"article_date": "2026-08-01"}], {}),
                         ("", ""))

    def test_article_view_carries_detail_into_the_timeline(self):
        view = build_data._article_view({
            "hash": "h1", "article_date": "2026-08-06", "title_kr": "팍스 원전 재가동",
            "detail": "팍스 원전 재가동이 확정됐다는 본문에서 뽑은 요지다.",
        })
        self.assertEqual(view["detail"],
                         "팍스 원전 재가동이 확정됐다는 본문에서 뽑은 요지다.")

    def test_a_detail_about_a_different_article_is_dropped(self):
        """2026-08-10 라이브: '한수원, 신규 대형 원전 및 SMR 부지 후보지 선정'
        이슈의 '기사 내용'이 해외건설 수주 이야기였다. 수집 판정을 고쳐도
        아카이브에 남은 기록은 안 고쳐지므로 화면으로 나가는 자리에서 막는다.
        """
        wrong = {
            "hash": "h2", "article_date": "2026-08-10",
            "title_kr": "한수원, 신규 대형 원전 및 SMR 부지 후보지 선정",
            "detail": ("국내 건설사들이 올해 해외건설 수주 500억 달러 목표 달성을 위해 "
                       "대형 프로젝트 확보에 나서고 있으며, 상반기 수주액이 크게 감소했다."),
        }
        self.assertEqual(build_data.usable_detail(wrong), "")
        self.assertEqual(build_data._article_view(wrong)["detail"], "")
        # 이슈 상세에도 실리지 않는다.
        self.assertEqual(build_data.pick_detail([wrong], wrong), ("", ""))

    def test_known_hallucinated_archive_title_is_corrected_at_the_source(self):
        """폴리뉴스 원문에 없던 영덕·기장 후보지 단정을 아카이브에 남기지 않는다."""
        records = [json.loads(line) for line in (ROOT.parent / "archive" / "2026-08.jsonl")
                   .read_text(encoding="utf-8").splitlines() if line.strip()]
        record = next(row for row in records if row["hash"] == "9b85b2a6e2593847")
        self.assertIn("K건설", record["title_kr"])
        self.assertIn("원전", record["summary"])
        self.assertIn("전력 인프라", record["summary"])
        for invented in ("영덕", "기장", "후보지 선정"):
            self.assertNotIn(invented, record["title_kr"] + record["summary"])
        self.assertEqual(record["importance"], "nice_to_know")

    def test_the_dialog_actually_renders_it(self):
        app = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn("dialog-detail", app)
        self.assertIn("기사 내용", app)
        self.assertIn(".dialog-detail", css)
        # 타임라인 각 기사도 자기 요지를 펼칠 수 있어야 한다.
        self.assertIn("timeline-detail", app)
        self.assertIn(".timeline-detail", css)


class CollectionTimestampTests(unittest.TestCase):
    """'마지막 수집'은 수집기가 마지막으로 돈 시각이어야 한다.

    2026-08-11 21:49 KST 에 화면이 `마지막 수집 07:05` 였다. 실제 수집은 20:35 였고
    데이터도 최신이었다 — 화면이 `last_success_at` 을 쓰고 있었는데 그 필드는
    build_data 가 **"마지막 정상 브리핑"**(하루 1회)으로 정의한 값이다. 수집은
    매시간이라 오후 내내 아침 시각이 떠 있고, 보는 사람은 14시간 밀린 줄 안다.

    `status.json` 은 `collector_stamp` 를 이미 싣고 있었는데 화면이 안 썼다.
    """

    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_the_collection_line_reads_the_collector_stamp(self):
        self.assertIn("state.systemStatus?.collector_stamp", self.script)
        # 수집 문구가 브리핑 시각을 쓰면 안 된다.
        for line in self.script.splitlines():
            if "마지막 수집" in line and "//" not in line.split("마지막 수집")[0]:
                self.assertNotIn("briefedAt", line, f"수집 문구가 브리핑 시각을 쓴다: {line.strip()[:70]}")

    def test_the_briefing_lines_still_read_the_briefing_time(self):
        # 반대쪽도 어긋나면 안 된다 — 오류 문구는 '마지막 정상 브리핑'이다.
        self.assertIn("마지막 정상 브리핑", self.script)
        self.assertNotIn("마지막 정상 수집", self.script)

    def test_the_status_dialog_shows_both(self):
        """둘은 다른 값이고 다른 주기다. 하나만 보이면 나머지가 어긋나도 모른다."""
        self.assertIn("<dt>마지막 수집</dt>", self.script)
        self.assertIn("<dt>마지막 정상 브리핑</dt>", self.script)

    def test_the_build_emits_the_collector_stamp(self):
        source = (ROOT / "build_data.py").read_text(encoding="utf-8")
        self.assertIn('"collector_stamp"', source)


class SavedIssuesPackTests(unittest.TestCase):
    """브리핑은 이슈 하나로 끝나지 않는다.

    시나리오 D(정책 브리핑)를 실제로 해보니 계속운전 하나를 좇는데 관련 이슈가
    3건이었고, `자료 팩 복사` 는 하나씩만 나와서 세 번 복사해 손으로 붙여야 했다.
    붙이는 동안 순서·중복·출처가 흐트러진다.
    """

    STUBS = """
const state = {
  issues: [
    {issue_id: 'a', title: '첫째 이슈', last_seen: '2026-08-09'},
    {issue_id: 'b', title: '둘째 이슈', last_seen: '2026-08-11'},
    {issue_id: 'c', title: '저장 안 함', last_seen: '2026-08-10'},
  ],
  savedIds: new Set(['a', 'b']),
  meta: {latest_briefing_date: '2026-08-11'},
};
const location = {origin: 'https://nuclens.pages.dev'};
const dateLabel = v => v;
const issueMaterialPack = issue => '# ' + issue.title;
"""

    def _pack(self, extra: str = "") -> str:
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"^function savedIssuesPack\(\) \{.*?^\}", script, re.S | re.M)
        self.assertTrue(fn, "savedIssuesPack 을 못 찾았다")
        body = (self.STUBS + fn.group(0) + extra
                + "\nprocess.stdout.write(savedIssuesPack());")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(body)
            path = handle.name
        try:
            out = subprocess.run(["node", path], capture_output=True, text=True,
                                 encoding="utf-8")
        finally:
            os.unlink(path)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return out.stdout

    def test_it_gathers_only_the_saved_issues_newest_first(self):
        pack = self._pack()
        self.assertTrue(pack.startswith("# 저장한 이슈 2건"))
        self.assertNotIn("저장 안 함", pack)
        self.assertLess(pack.index("둘째"), pack.index("첫째"))

    def test_it_opens_with_a_table_of_contents(self):
        # 브리핑을 쓰는 사람이 팩의 모양을 먼저 본다 — 몇 건이고 무엇인지.
        pack = self._pack()
        self.assertIn("## 목차", pack)
        self.assertIn("01. 둘째 이슈", pack)
        self.assertIn("02. 첫째 이슈", pack)

    def test_each_issue_keeps_its_own_pack_format(self):
        """조립만 한다 — 여기서 형식을 새로 지으면 단건 팩과 두 벌이 되고 갈라진다."""
        pack = self._pack()
        self.assertEqual(pack.split("\n---\n").__len__(), 2)

    def test_nothing_saved_means_nothing_copied(self):
        pack = self._pack("\nstate.savedIds = new Set();")
        self.assertEqual(pack, "")

    def test_the_button_is_wired_and_hidden_when_empty(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        markup = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-pack-saved", markup)
        self.assertIn('closest("[data-pack-saved]")', script)
        # 묘비만 남은 목록에서 누르면 빈 문서가 복사된다.
        self.assertIn("packButton.hidden = issues.length === 0", script)


class SourceBackfillTests(unittest.TestCase):
    """자료 팩(정책 브리핑의 산출물)이 인용하는 줄이 인용답지 않았다.

        - 8월 4일 · 원안위, 계속운전 원전의 … (v.daum.net)
          https://news.google.com/rss/articles/CBMiT0FVX3lxTE5hekxXbS1ZNC1o…

    포털 호스트명이 매체명 자리에 있고 링크는 리다이렉트다. `site_name`·
    `resolved_url` 은 2026-08-11 수집분부터 붙으므로 그 전 기록은 영영 빈다 —
    실측 표시 기사 1,136건 중 777건이 해당하고 **그중 645건이 최근 7일분**이라
    "오래된 것만"이 아니었다.
    """

    def setUp(self):
        build_data._SOURCE_BACKFILL = None
        self.addCleanup(setattr, build_data, "_SOURCE_BACKFILL", None)

    def test_a_backfilled_name_replaces_a_hostname(self):
        build_data._SOURCE_BACKFILL = {"h1": {"site_name": "노컷뉴스"}}
        row = build_data._normalize_archive_record({
            "hash": "h1", "publisher": "v.daum.net", "domain": "daum.net",
            "url": "https://news.google.com/rss/articles/X", "title": "제목"})
        self.assertEqual(row["publisher"], "노컷뉴스")

    def test_a_backfilled_url_becomes_the_citable_address(self):
        build_data._SOURCE_BACKFILL = {
            "h1": {"resolved_url": "https://www.yna.co.kr/view/AKR1"}}
        row = build_data._normalize_archive_record({
            "hash": "h1", "publisher": "연합뉴스", "domain": "yna.co.kr",
            "url": "https://news.google.com/rss/articles/X", "title": "제목"})
        self.assertEqual(build_data.source_url(row),
                         "https://www.yna.co.kr/view/AKR1")
        # dedup 키는 그대로다 — 바꾸면 같은 기사가 새 기사로 다시 들어온다.
        self.assertIn("news.google.com", row["url"])

    def test_the_record_wins_over_the_backfill(self):
        # 백필은 빈자리만 메운다. 나중에 수집된 값이 있으면 그쪽이 최신이다.
        build_data._SOURCE_BACKFILL = {"h1": {"site_name": "옛이름"}}
        row = build_data._normalize_archive_record({
            "hash": "h1", "site_name": "새이름", "publisher": "edaily.co.kr",
            "domain": "edaily.co.kr", "url": "https://a/b", "title": "제목"})
        self.assertEqual(row["publisher"], "새이름")

    def test_a_real_publisher_name_is_never_overwritten(self):
        build_data._SOURCE_BACKFILL = {"h1": {"site_name": "노컷뉴스"}}
        row = build_data._normalize_archive_record({
            "hash": "h1", "publisher": "전기신문", "domain": "electimes.com",
            "url": "https://a/b", "title": "제목"})
        self.assertEqual(row["publisher"], "전기신문")

    def test_a_missing_file_is_not_an_error(self):
        self.assertIsInstance(build_data.source_backfill(), dict)


class SearchMatchingTests(unittest.TestCase):
    """검색에서 0건은 "그런 이슈가 없다"로 읽힌다 — 리서치 도구에서 가장 나쁜 실패다.

    2026-08-11 실측: `계속운전` 11건인데 `원안위 계속운전` **0건**이었다. 제목이
    `원안위, 계속운전 원전의…` 라 쉼표 하나에 막힌 것이다. 이어 붙인 텍스트 한
    덩어리에 대한 substring 이라 낱말 사이에 무엇이든 끼면 통째로 빗나갔다.
    """

    @staticmethod
    def _run(expr: str) -> str:
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        picked = []
        for name in ("searchNormalize", "queryTokens", "matchesQuery"):
            block = re.search(rf"^function {name}\(.*?^}}", script, re.S | re.M)
            assert block, f"{name} 를 못 찾았다"
            picked.append(block.group(0))
        source = "\n".join(picked) + f"\nprocess.stdout.write(String({expr}));"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(source)
            path = handle.name
        try:
            out = subprocess.run(["node", path], capture_output=True, text=True,
                                 encoding="utf-8")
        finally:
            os.unlink(path)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return out.stdout.strip()

    TITLE = "원안위, 계속운전 원전의 신규 원전 수준 안전성 확인 발표"

    def test_two_words_separated_by_punctuation_still_match(self):
        self.assertEqual(
            self._run(f"matchesQuery({self.TITLE!r}, '원안위 계속운전')"), "true")

    def test_a_single_word_behaves_as_before(self):
        self.assertEqual(self._run(f"matchesQuery({self.TITLE!r}, '계속운전')"), "true")

    def test_it_is_still_an_AND_not_an_OR(self):
        # 낱말 하나만 맞는 이슈까지 끌어오면 검색이 잡음이 된다.
        self.assertEqual(
            self._run(f"matchesQuery({self.TITLE!r}, '고리 계속운전')"), "false")

    def test_unit_punctuation_does_not_block_the_match(self):
        self.assertEqual(
            self._run("matchesQuery('고리 3·4호기 계속운전 허가', '고리 34호기')"), "true")

    def test_an_empty_query_matches_everything(self):
        self.assertEqual(self._run(f"matchesQuery({self.TITLE!r}, '')"), "true")

    def test_the_results_page_no_longer_uses_a_single_substring(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        # 두 자리(카드 하이라이트 판정·이슈 필터)가 모두 헬퍼를 거쳐야 한 곳만
        # 고쳐도 결과 페이지와 안내 문구가 갈라지지 않는다.
        self.assertNotIn(".includes(state.archiveQuery)", script)
        self.assertGreaterEqual(script.count("matchesQuery("), 3)


class CountryRepairTests(unittest.TestCase):
    """큐레이션이 나라를 틀리게 붙이는 일은 드물지만 한 건이 세 군데를 망가뜨린다.

    2026-08-11 실사고: IAEA 주최 국제 논의 기사(dt.co.kr)에 `KR` 이 붙었다. 요약에
    한국 행위자가 없는데도. 그 태그 하나가 ①지역을 국내로 바꾸고 ②국가 지도의
    한국 칸을 부풀리고 ③같은 사건을 다룬 미국 기사와 한 이슈로 묶일 때 국경
    충돌로 잡혀 **배포 게이트를 막았다.**

    드문 오판은 규칙을 풀어서 고치지 않는다 — 판정을 고친다.
    """

    def test_a_repair_wins_over_the_curated_tags(self):
        build_data._COUNTRY_REPAIRS = {"h1": ["GLOBAL"]}
        try:
            countries, source = build_data.infer_countries(
                {"hash": "h1", "countries": ["KR"], "title_kr": "IAEA 국제 논의"})
            self.assertEqual(countries, ["GLOBAL"])
            self.assertEqual(source, "manual-repair")
        finally:
            build_data._COUNTRY_REPAIRS = None

    def test_a_repair_also_wins_over_a_stale_scope(self):
        """`scope` 는 큐레이션이 명시할 때만 채워지는 신뢰 낮은 필드인데(실측 157건
        중 148건 None) infer_region 맨 앞에 있어서, 고쳐 놓은 나라가 옛 scope 하나에
        다시 덮이고 있었다.
        """
        record = {"hash": "h1", "scope": "kr"}
        self.assertEqual(build_data.region_of(record, ["GLOBAL"]), "국내")
        self.assertEqual(
            build_data.region_of(record, ["GLOBAL"], "manual-repair"), "해외")

    def test_records_without_a_repair_are_untouched(self):
        build_data._COUNTRY_REPAIRS = {"other": ["GLOBAL"]}
        try:
            countries, source = build_data.infer_countries(
                {"hash": "h1", "countries": ["KR"], "title_kr": "한수원 영덕 부지"})
            self.assertEqual(countries, ["KR"])
            self.assertNotEqual(source, "manual-repair")
        finally:
            build_data._COUNTRY_REPAIRS = None

    def test_the_repair_file_parses_and_the_live_entry_is_applied(self):
        repairs = build_data.country_repairs()
        self.assertIn("b55e201374267ece", repairs)
        article = next((row for row in self.__class__._news()
                        if row["hash"] == "b55e201374267ece"), None)
        if article is None:
            self.skipTest("생성 데이터 없음")
        self.assertEqual(article["countries"], ["GLOBAL"])
        self.assertEqual(article["region"], "해외")

    @staticmethod
    def _news():
        path = ROOT / "public" / "data" / "news.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))


class TodayAgendaPlacementTests(unittest.TestCase):
    """좁은 화면에서 '오늘 3분'은 오늘의 선두 이슈 **아래**로 간다.

    실측(2026-08-11) 블록 높이 / 선두 이슈 위치 — 1440×900 은 296px/733px 인데
    375×812 은 700px/1,105px(1.36 화면)이다. 글이 좁은 폭에서 접히며 블록이 두 배
    넘게 불어 첫 화면이 통째로 '이번 주' 요약이 됐다. 탭 이름은 '오늘'이고 안쪽
    라벨은 요일과 무관하게 매일 `이번 주 결론` 이다.
    """

    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_the_move_happens_only_on_narrow_screens(self):
        self.assertIn("function placeTodayAgenda", self.script)
        body = self.script.split("function placeTodayAgenda", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("narrowScreen.matches", body)
        # 내용을 숨기는 방식은 쓰지 않는다 — 주간 watchpoints 는 카드의
        # open_question 이 비어 있어 화면에서 그 질문에 답하는 유일한 자리다.
        self.assertNotIn("hidden = true", body)

    def test_it_runs_after_the_lead_visibility_is_decided(self):
        """앞에서 부르면 첫 렌더에서 leadIssue 가 아직 hidden 이라 조건이 늘 거짓이다
        (실제로 그렇게 넣었다가 자리가 안 바뀌었다).
        """
        decided = self.script.index('document.getElementById("leadIssue").hidden = !lead;')
        called = self.script.index("placeTodayAgenda();", decided)
        self.assertGreater(called, decided)

    def test_the_breakpoint_change_moves_it_back(self):
        # 안 하면 리사이즈한 사람만 어긋난 채 본다.
        self.assertIn('narrowScreen.addEventListener("change", placeTodayAgenda)', self.script)


class WeeklyThemeLabelTests(unittest.TestCase):
    """한수원 임직원용 서비스가 투자 시그널을 주는 모양새는 기획 단계부터 걸려 있던
    우려다. 담는 내용(theme_moves)은 그대로 두고 프레이밍만 중화한다
    (2026-08-11 사용자 결정). 실제로 뜨는 이름은 SMR·계속운전·전력수요처럼
    주제어이지 종목이 아니다.
    """

    def test_the_web_label_is_neutral(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"주제별 강약"', script)
        code = "\n".join(re.sub(r"//.*$", "", line) for line in script.splitlines())
        self.assertNotIn("투자 테마", code)


class CountryLabelTests(unittest.TestCase):
    """나라를 말하는 표가 셋이고, 어긋나면 내부 코드가 그대로 화면이 된다.

    2026-08-11 라이브: 지도 타일 툴팁이 `MX 0건`·`AT 0건` 이었다. 나머지 37개는
    `브라질 0건` 처럼 한국어인데 둘만 코드였다 — COUNTRY_GRID·COUNTRY_REGION 에는
    넣고 COUNTRY_LABELS 에 안 넣어서다. 화면 코드가 `COUNTRY_LABELS[c] || c` 로
    물러나므로 **조용히** 코드가 나간다. 나라를 하나 더할 때마다 재발할 자리라
    값이 아니라 계약을 잠근다.
    """

    @staticmethod
    def _keys(script: str, name: str) -> set:
        block = re.search(name + r"\s*=\s*\{(.*?)\n\};", script, re.S)
        assert block, f"{name} 를 못 찾았다"
        return set(re.findall(r"([A-Z_]{2,})\s*:", block.group(1)))

    def setUp(self):
        self.script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")

    def test_every_mapped_country_has_a_korean_name(self):
        labels = self._keys(self.script, "COUNTRY_LABELS")
        for name in ("COUNTRY_GRID", "COUNTRY_REGION"):
            missing = self._keys(self.script, name) - labels
            self.assertEqual(missing, set(),
                             f"{name} 에만 있고 이름이 없는 코드: {sorted(missing)}")

    def test_the_grid_and_the_continent_buckets_cover_the_same_countries(self):
        grid = self._keys(self.script, "COUNTRY_GRID")
        region = self._keys(self.script, "COUNTRY_REGION")
        self.assertEqual(grid, region,
                         "지도에 서는 나라와 대륙 합계에 세는 나라가 다르면 "
                         "타일 합과 막대 합이 어긋난다")

    def test_the_label_only_entries_are_the_documented_non_geographic_ones(self):
        # EU·유럽·글로벌·미분류는 좌표가 없어 격자에 없다 — 주석이 그렇게 적어 뒀다.
        extra = self._keys(self.script, "COUNTRY_LABELS") - self._keys(self.script, "COUNTRY_GRID")
        self.assertEqual(extra, {"EU", "EUROPE", "GLOBAL", "UNSPECIFIED"})


class PublicationTitleTests(unittest.TestCase):
    """발간물 목록은 "이 문서를 열어 볼 값어치가 있나"를 고르는 자리다.

    라이브 실측(2026-08-10) 20건 중 17건의 제목이 기관명으로 시작했고, 바로 위
    줄에 같은 기관 바이라인이 또 있었다. 모든 행이 같은 10~22자로 시작하니
    정작 다른 부분이 오른쪽으로 밀린다.
    """

    def test_the_org_byline_is_not_repeated_at_the_head_of_the_title(self):
        self.assertEqual(
            build_data.strip_org_prefix(
                "국제원자력기구(IAEA) 원자력 시설의 기후 관련 외부 사건에서 얻은 교훈",
                "IAEA", "국제원자력기구(IAEA)"),
            "원자력 시설의 기후 관련 외부 사건에서 얻은 교훈")

    def test_a_different_spelling_of_the_same_org_is_still_caught(self):
        """제목은 '경제협력개발기구 원자력기구(OECD-NEA)', 바이라인은
        'OECD 원자력기구(NEA)' 였다 — 두 기관처럼 읽힌다. 한국어 표기는 번역마다
        흔들리므로 판정은 괄호 안 약자로 한다.
        """
        self.assertEqual(
            build_data.strip_org_prefix(
                "경제협력개발기구 원자력기구(OECD-NEA) 소형모듈원전(SMR) 가속화",
                "OECD-NEA", "OECD 원자력기구(NEA)"),
            "소형모듈원전(SMR) 가속화")

    def test_it_does_not_eat_a_title_that_merely_opens_with_a_parenthesis(self):
        # 기관 약자가 아닌 괄호는 제목의 일부다.
        self.assertEqual(
            build_data.strip_org_prefix("소형모듈원자로(SMR) 배치 가속화",
                                        "IAEA", "국제원자력기구(IAEA)"),
            "소형모듈원자로(SMR) 배치 가속화")

    def test_a_title_that_is_only_the_org_name_survives(self):
        # 자르고 나면 아무것도 안 남는 경우 — 빈 제목보다 중복이 낫다.
        self.assertEqual(
            build_data.strip_org_prefix("국제원자력기구(IAEA)",
                                        "IAEA", "국제원자력기구(IAEA)"),
            "국제원자력기구(IAEA)")

    def test_the_prompt_keeps_the_org_out_of_the_title_slot(self):
        """근본 원인은 입력 형식이었다 — `(OECD-NEA) Accelerating SMRs` 처럼
        괄호가 제목 바로 앞에 붙어 모델이 그것까지 제목으로 읽었다.
        """
        source = (ROOT.parent / "pubs_translate.py").read_text(encoding="utf-8")
        self.assertIn("발행기관=", source)
        self.assertIn("발행기관을 제목 앞에 붙이지 않는다", source)


class WebFontWeightTests(unittest.TestCase):
    """폰트 한 벌이 첫 로드 전송량의 77% 였다(2026-08-10 실측 2,057,688 바이트).

    woff2 는 이미 압축돼 있어 엣지 gzip/br 이 더 줄여 주지 않는다 — 파일을 줄이는
    수밖에 없다. 지면이 24일 동안 쓴 서로 다른 한글 음절은 896자이고 전부
    KS X 1001 안에 있어서, 2,350자만 남겨도 2.6배 여유다.
    """

    FONT_DIR = ROOT / "public" / "fonts" / "pretendard" / "v1.3.9"
    SUBSET = FONT_DIR / "PretendardVariable.subset.woff2"

    def test_the_stylesheet_loads_the_subset_not_the_full_font(self):
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertIn("PretendardVariable.subset.woff2", css)
        # 원본은 재생성용으로만 저장소에 남는다. @font-face 가 그걸 가리키면
        # 감축이 조용히 되돌아간다.
        self.assertNotIn('url("fonts/pretendard/v1.3.9/PretendardVariable.woff2")', css)

    def test_the_preload_points_at_the_same_file_as_font_face(self):
        """실사고(2026-08-10): @font-face 만 바꾸고 index.html 의 preload 를 두니
        브라우저가 **원본 2MB 를 그대로 받고** 쓰지도 않았다(콘솔 경고로 발각).
        preload 와 @font-face 가 어긋나면 감축이 통째로 무효다.
        """
        head = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("PretendardVariable.subset.woff2", head)
        self.assertNotIn('href="/fonts/pretendard/v1.3.9/PretendardVariable.woff2"', head)

    def test_the_subset_is_committed_and_stays_small(self):
        self.assertTrue(self.SUBSET.exists(),
                        "web/tools/subset_font.py 로 생성해 커밋할 것")
        size = self.SUBSET.stat().st_size
        # 여유를 크게 둔 상한 — 원본(2.0MB)으로 되돌아가는 것만 잡으면 된다.
        self.assertLess(size, 1_100_000, f"부분집합이 {size:,} 바이트로 불었다")

    def test_the_subset_still_covers_ks_x_1001(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:                      # CI 는 fontTools 를 안 넣는다
            self.skipTest("fontTools 없음 — 계약 검사만 수행")
        sys.path.insert(0, str(ROOT / "tools"))
        import subset_font

        cmap = set(TTFont(self.SUBSET).getBestCmap())
        missing = subset_font.ksx1001_syllables() - cmap
        self.assertEqual(missing, set(), "KS X 1001 음절이 빠졌다")
        # 지면 크롬(한글 라벨)도 전부 있어야 한다.
        chrome = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        chrome += (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        lost = {c for c in chrome if 0xAC00 <= ord(c) <= 0xD7A3 and ord(c) not in cmap}
        self.assertEqual(lost, set(), f"화면 문구가 빠졌다: {sorted(lost)[:20]}")


if __name__ == "__main__":
    unittest.main()
