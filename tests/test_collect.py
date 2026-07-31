"""수집 단계(RSS 출처 판정) 테스트. 외부 호출 0.

회귀 방지 대상 (2026-07-31):
- Google News 검색 피드의 출처가 전건 news.google.co.kr 로 뭉개지던 문제
- 'RSS 경로면 score 10' 때문에 국내 일반 언론 기사가 1차 소스(TIER1)로
  프롬프트에 들어가 must_read 로 격상되던 문제
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot as nb  # noqa: E402


class _Entry(dict):
    """feedparser entry 흉내 — source 는 title/href 를 가진 dict."""


def _entry(title, source_title="", source_href=""):
    e = _Entry(title=title)
    if source_title or source_href:
        e["source"] = {"title": source_title, "href": source_href}
    return e


class TestPublisherResolution(unittest.TestCase):
    def test_publisher_extracted(self):
        e = _entry("원전 계속운전 심사 - 전기신문", "전기신문", "https://www.electimes.com")
        self.assertEqual(nb.publisher_of(e), ("전기신문", "electimes.com"))

    def test_no_source_element(self):
        self.assertEqual(nb.publisher_of(_entry("제목만 있음")), ("", ""))

    def test_title_suffix_stripped_repeatedly(self):
        # Google News 는 매체명을 두 번 붙이기도 한다 (실측)
        self.assertEqual(
            nb.strip_title_suffix('기후장관 "전력 충분" - 머니투데이 - 머니투데이', "머니투데이"),
            '기후장관 "전력 충분"')

    def test_title_suffix_kept_when_no_publisher(self):
        self.assertEqual(nb.strip_title_suffix("제목 - 어딘가", ""), "제목 - 어딘가")

    def test_keyword_feed_uses_real_publisher_domain(self):
        src = {"domain_label": "news.google.co.kr", "resolve_publisher": True}
        item = {"link": "https://news.google.com/rss/articles/CBMiXXX",
                "publisher_domain": "electimes.com"}
        self.assertEqual(nb.resolve_rss_domain(src, item), "electimes.com")

    def test_keyword_feed_falls_back_to_label(self):
        src = {"domain_label": "news.google.co.kr", "resolve_publisher": True}
        item = {"link": "https://news.google.com/rss/articles/CBMiXXX", "publisher_domain": ""}
        self.assertEqual(nb.resolve_rss_domain(src, item), "news.google.co.kr")

    def test_institution_feed_keeps_domain_label(self):
        # 기관 site: 피드는 domain_label 이 정답 — <source> 로 덮어쓰지 않는다
        src = {"domain_label": "khnp.co.kr"}
        item = {"link": "https://news.google.com/rss/articles/CBMiXXX",
                "publisher_domain": "somewhere.com"}
        self.assertEqual(nb.resolve_rss_domain(src, item), "khnp.co.kr")


class TestTier1Source(unittest.TestCase):
    def test_institution_domain_is_tier1_even_via_google_link(self):
        art = {"domain": "nssc.go.kr",
               "link": "https://news.google.com/rss/articles/CBMiXXX"}
        self.assertTrue(nb.is_tier1_source(art))

    def test_ordinary_korean_press_is_not_tier1(self):
        for dom in ("electimes.com", "mt.co.kr", "esnews.kr", "news.google.co.kr"):
            self.assertFalse(nb.is_tier1_source({"domain": dom, "link": f"https://{dom}/a"}),
                             f"{dom} 이 1차 소스로 잡힘")

    def test_reuters_is_not_tier1(self):
        # tier2 일반 언론 — 신뢰도 보너스는 받되 must_read 자동 격상은 안 됨
        self.assertFalse(nb.is_tier1_source(
            {"domain": "reuters.com", "link": "https://www.reuters.com/x"}))

    def test_registered_specialist_feeds_stay_tier1(self):
        for dom in ("energy.gov", "nucnet.org", "sfen.org", "world-nuclear-news.org"):
            self.assertTrue(nb.is_tier1_source({"domain": dom, "link": f"https://{dom}/a"}),
                            f"{dom} 이 1차 소스에서 빠짐")


class TestDefaultSection(unittest.TestCase):
    def test_korean_title_on_dotcom_domain(self):
        # 국내 매체 상당수가 .com — 도메인만 보면 해외로 샌다
        self.assertEqual(nb.default_section("electimes.com", "원전 계속운전 심사 지연"), "domestic")

    def test_english_title_on_unknown_domain(self):
        self.assertEqual(nb.default_section("county17.com", "BWXT plans fuel hub"), "international")

    def test_khnp_domain(self):
        self.assertEqual(nb.default_section("khnp.co.kr", "보도자료"), "khnp")


if __name__ == "__main__":
    unittest.main()
