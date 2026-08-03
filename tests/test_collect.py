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
from unittest.mock import patch

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

    def test_specialist_media_is_ranked_but_not_called_primary(self):
        self.assertTrue(nb.is_tier1_source(
            {"domain": "energy.gov", "link": "https://energy.gov/a"}
        ))
        for dom in ("nucnet.org", "sfen.org", "world-nuclear-news.org", "ans.org"):
            article = {"domain": dom, "link": f"https://{dom}/a"}
            self.assertFalse(nb.is_tier1_source(article), f"{dom} 이 공식 원문으로 오표시됨")
            self.assertGreaterEqual(nb.source_score(dom), 8)


class TestDefaultSection(unittest.TestCase):
    def test_korean_title_on_dotcom_domain(self):
        # 국내 매체 상당수가 .com — 도메인만 보면 해외로 샌다
        self.assertEqual(nb.default_section("electimes.com", "원전 계속운전 심사 지연"), "domestic")

    def test_english_title_on_unknown_domain(self):
        self.assertEqual(nb.default_section("county17.com", "BWXT plans fuel hub"), "international")

    def test_khnp_domain(self):
        self.assertEqual(nb.default_section("khnp.co.kr", "보도자료"), "khnp")


class TestExactDedup(unittest.TestCase):
    def test_normalized_url_then_exact_title(self):
        articles = [
            {"title": "같은 기사", "link": "https://example.com//story?utm_source=a", "score": 5},
            {"title": "다른 제목", "link": "https://example.com/story", "score": 9},
            {"title": "다른 제목", "link": "https://other.example/story", "score": 4},
            {"title": "오류", "link": "https://example.com/Error/retry", "score": 10},
        ]
        kept = nb.dedup_exact_candidates(articles)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["score"], 9)
        self.assertEqual(kept[0]["link"], "https://example.com/story")


class TestCurationQualityGate(unittest.TestCase):
    def _article(self):
        return {
            "hash": "h1", "title": "정부가 신규 원전 계획을 발표",
            "description": "정부가 신규 원전 계획을 발표했다.",
            "domain": "energy.gov", "publisher": "US DOE",
        }

    @staticmethod
    def _response(summary):
        return {"items": [{
            "idx": 0, "importance": "nice_to_know", "section": "international",
            "scope": "overseas", "category": "정책", "title_kr": "정부, 신규 원전 계획 발표",
            "summary": summary, "implication": "", "why_important": "", "tags": [],
            "topics": ["newbuild"], "countries": ["US"], "article_type": "policy",
            "event_date": "2026-08-01", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "description",
            "related_reports": [], "features": {},
        }]}

    def test_incomplete_summary_is_regenerated_once(self):
        with patch.object(nb, "gemini_rest_available", return_value=True), patch.object(
            nb, "gemini_call_json",
            side_effect=[self._response("정부가 신규 원전 계획을 발표"), self._response("정부가 신규 원전 계획을 발표했다.")],
        ) as call:
            result = nb.curate_batch([self._article()], [])
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["h1"]["summary"], "정부가 신규 원전 계획을 발표했다.")
        self.assertEqual(result["h1"]["event_date"], "2026-08-01")

    def test_persistently_broken_summary_is_quarantined(self):
        bad = self._response("정부가 신규 원전 계획을 발표")
        with patch.object(nb, "gemini_rest_available", return_value=True), patch.object(
            nb, "gemini_call_json", side_effect=[bad, bad]
        ):
            self.assertEqual(nb.curate_batch([self._article()], []), {})


class TestOpenQuestionGate(unittest.TestCase):
    """'아직 확정되지 않은 것' — 위험은 불확실성 표시가 아니라 추측 생성이다."""

    GOOD = {"open_question": "최종 계약 체결 시점은 아직 확정되지 않았다.",
            "open_question_source": "article_text"}

    def test_must_read_with_evidence_passes(self):
        self.assertEqual(nb.norm_open_question(self.GOOD, "must_read"),
                         (self.GOOD["open_question"], "article_text"))

    def test_nice_to_know_always_null(self):
        self.assertEqual(nb.norm_open_question(self.GOOD, "nice_to_know"), ("", "unknown"))

    def test_unknown_source_is_dropped(self):
        """근거 위치를 못 대면 버린다 — 근거 없는 그럴듯한 문장이 가장 나쁘다."""
        item = {**self.GOOD, "open_question_source": "unknown"}
        self.assertEqual(nb.norm_open_question(item, "must_read"), ("", "unknown"))

    def test_missing_source_is_dropped(self):
        self.assertEqual(
            nb.norm_open_question({"open_question": self.GOOD["open_question"]}, "must_read"),
            ("", "unknown"))

    def test_forecast_sentence_is_rejected(self):
        """'~할 것으로 보인다'는 미확정 사항이 아니라 예측이다."""
        item = {"open_question": "연내 착공에 들어갈 것으로 보인다.",
                "open_question_source": "article_text"}
        self.assertEqual(nb.norm_open_question(item, "must_read"), ("", "unknown"))

    def test_question_form_is_rejected(self):
        item = {"open_question": "최종 계약은 언제 체결될까?",
                "open_question_source": "article_text"}
        self.assertEqual(nb.norm_open_question(item, "must_read"), ("", "unknown"))

    def test_overlong_is_rejected_not_truncated(self):
        item = {"open_question": "가" * (nb.OPEN_QUESTION_LIMIT + 1),
                "open_question_source": "title"}
        self.assertEqual(nb.norm_open_question(item, "must_read"), ("", "unknown"))

    def test_incident_safety_needs_explicit_uncertainty(self):
        """사고·안전은 전면 금지가 아니라 강화 게이트.

        명시적 미확정 표현이 있으면 통과한다 — 숨기면 확정된 사건으로 오해된다.
        """
        explicit = {"open_question": "사고 원인과 설비 손상 범위는 아직 조사 중이다.",
                    "open_question_source": "article_text"}
        self.assertEqual(
            nb.norm_open_question(explicit, "must_read", "incident_safety")[0],
            explicit["open_question"])

    def test_incident_safety_without_marker_is_dropped(self):
        vague = {"open_question": "향후 대응 방향에 관심이 쏠린다.",
                 "open_question_source": "article_text"}
        self.assertEqual(nb.norm_open_question(vague, "must_read", "incident_safety"),
                         ("", "unknown"))

    def test_non_incident_does_not_need_the_marker(self):
        self.assertEqual(
            nb.norm_open_question(self.GOOD, "must_read", "contract_award")[0],
            self.GOOD["open_question"])

    def test_normalize_curation_item_wires_the_gate(self):
        item = {"importance": "must_read", "summary": "정부가 계획을 발표했다.",
                "features": {"event_type": "incident_safety", "korea_relevance": 0,
                             "market_materiality": 0, "policy_materiality": 0,
                             "novelty": 0, "evidence_strength": 0},
                **self.GOOD}
        out = nb.normalize_curation_item(item, {"title": "t", "domain": "example.com"})
        # incident_safety + 명시적 표현 없음 → 버려진다
        self.assertEqual(out["open_question"], "")
        self.assertEqual(out["open_question_source"], "unknown")

    def test_archive_record_carries_the_field(self):
        """화이트리스트에 없으면 아카이브에 안 남고 웹에서 영영 못 본다."""
        import news_archive
        record = news_archive.make_record(
            {"hash": "h1", "title": "T", "link": "https://example.com/a",
             "domain": "example.com"},
            {"importance": "must_read", **self.GOOD},
            "2026-08-03T00:00:00+00:00")
        self.assertEqual(record["open_question"], self.GOOD["open_question"])
        self.assertEqual(record["open_question_source"], "article_text")


if __name__ == "__main__":
    unittest.main()
