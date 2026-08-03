"""수집 단계(RSS 출처 판정) 테스트. 외부 호출 0.

회귀 방지 대상 (2026-07-31):
- Google News 검색 피드의 출처가 전건 news.google.co.kr 로 뭉개지던 문제
- 'RSS 경로면 score 10' 때문에 국내 일반 언론 기사가 1차 소스(TIER1)로
  프롬프트에 들어가 must_read 로 격상되던 문제
"""

import json
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot as nb  # noqa: E402
from gemini_client import GeminiError, GeminiTruncated  # noqa: E402


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


class TestChunkLossIsRecoveredOrRecorded(unittest.TestCase):
    """호출이 통째로 실패한 chunk 를 조용히 버리지 않는다.

    회귀 방지 (2026-08-03, 프로덕션 run 30772996756): 새 기사 5건이 담긴 chunk 가
    ``request:`` 실패로 날아갔고, 재시도 대상에서 ``request:`` 를 제외하는 규칙 때문에
    두 번째 기회도 없었다. 유실 흔적은 콘솔 한 줄뿐이었다.

    유실이 치명적인 이유: 그 기사들은 fallback 큐레이션(영문 제목·implication 공란·
    features 없음)으로 큐에 들어가고, 큐 적재 순간 ``sent`` 로 마킹돼 재수집이
    막히므로 영영 복구되지 않는다.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.log = Path(self._tmp.name) / "delivery_log.jsonl"
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _articles(n):
        return [{"hash": f"h{i}", "title": f"원전 정책 발표 {i}",
                 "description": "정부가 신규 원전 계획을 발표했다.",
                 "link": f"https://example.com/{i}",
                 "domain": "energy.gov", "publisher": "US DOE"} for i in range(n)]

    @staticmethod
    def _ok_items(user_message):
        """프롬프트에 실린 [idx] 개수만큼 정상 항목을 만들어 응답한다."""
        n = len(re.findall(r"^\[(\d+)\]", user_message, re.M))
        return {"items": [{
            "idx": i, "importance": "nice_to_know", "section": "international",
            "scope": "overseas", "category": "정책", "title_kr": f"신규 원전 계획 {i}",
            "summary": "정부가 신규 원전 계획을 발표했다.", "implication": "",
            "why_important": "", "tags": [], "topics": ["newbuild"],
            "countries": ["US"], "article_type": "policy",
            "event_date": "2026-08-01", "event_date_type": "announcement",
            "event_date_precision": "day", "event_date_source": "description",
            "related_reports": [], "features": {},
        } for i in range(n)]}

    def _run(self, failures, n=4, chunk=4, budget=6):
        """failures: 호출 순번(0-based) → 던질 예외. 나머지는 정상 응답."""
        calls = []

        def fake(system, user, **kw):
            calls.append(len(re.findall(r"^\[(\d+)\]", user, re.M)))
            exc = failures.get(len(calls) - 1)
            if exc:
                raise exc
            return self._ok_items(user)

        with patch.object(nb, "gemini_rest_available", return_value=True), \
                patch.object(nb, "gemini_call_json", side_effect=fake), \
                patch.object(nb, "BATCH_CHUNK", chunk), \
                patch.object(nb, "BATCH_SPLIT_BUDGET", budget), \
                patch.object(nb, "DELIVERY_LOG_FILE", self.log), \
                patch.object(nb.time, "sleep", lambda *a, **k: None):
            result = nb.curate_batch(self._articles(n), [])
        return result, calls

    def _records(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines() if l]

    def test_truncated_chunk_is_split_and_fully_recovered(self):
        result, calls = self._run({0: GeminiTruncated("MAX_TOKENS 출력 예산 소진 — thoughts=8192")})
        self.assertEqual(set(result), {"h0", "h1", "h2", "h3"},
                         "잘림은 입력을 줄이면 사라진다 — 한 건도 잃을 이유가 없다")
        self.assertEqual(calls, [4, 2, 2], "4건 실패 → 2/2 로 쪼개 재시도")
        self.assertEqual(self._records(), [], "복구했으면 유실 기록도 없어야 한다")

    def test_split_recurses_until_the_bad_article_is_isolated(self):
        """한 건이 문제여도 나머지는 살린다."""
        result, _ = self._run(
            {0: GeminiTruncated("MAX_TOKENS"), 1: GeminiTruncated("MAX_TOKENS")})
        self.assertEqual(set(result), {"h0", "h1", "h2", "h3"})

    def test_quota_failure_is_not_retried(self):
        """429 는 쪼개도 그대로다. 다시 부르면 남은 한도만 태운다 (기존 판단 유지)."""
        result, calls = self._run({0: GeminiError("HTTP 429: RESOURCE_EXHAUSTED")})
        self.assertEqual(result, {})
        self.assertEqual(calls, [4], "한도 소진에 추가 호출 금지")

    def test_quota_loss_still_leaves_a_durable_record(self):
        self._run({0: GeminiError("HTTP 429: RESOURCE_EXHAUSTED")})
        recs = self._records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["record_type"], "curation_failure")
        self.assertEqual(rec["lost"], 4)
        self.assertEqual(rec["candidates"], 4)
        self.assertEqual(rec["reasons"], {"quota": 4})
        self.assertEqual({i["hash"] for i in rec["items"]},
                         {"h0", "h1", "h2", "h3"})
        self.assertTrue(all(i["title"] and i["link"] for i in rec["items"]),
                        "사후에 '어떤 기사였나'를 되짚을 수 있어야 한다")

    def test_unknown_failure_is_not_retried_but_is_recorded(self):
        """원인 불명은 기존대로 재시도 안 함 — 다만 조용히 사라지지는 않는다."""
        result, calls = self._run({0: GeminiError("응답 구조 비정상: {...}")})
        self.assertEqual(result, {})
        self.assertEqual(calls, [4])
        self.assertEqual(self._records()[0]["reasons"], {"other": 4})

    def test_split_budget_exhaustion_records_the_loss(self):
        """예산이 없으면 버리되, 버렸다는 사실은 남긴다."""
        result, calls = self._run(
            {0: GeminiTruncated("MAX_TOKENS")}, budget=0)
        self.assertEqual(result, {})
        self.assertEqual(calls, [4])
        self.assertEqual(self._records()[0]["reasons"], {"truncated": 4})

    def test_failure_record_is_skipped_by_delivery_log_readers(self):
        """새 record_type 이 기사 집계를 오염시키면 안 된다."""
        self._run({0: GeminiError("HTTP 429: RESOURCE_EXHAUSTED")})
        rows = self._records()
        self.assertTrue(all(r.get("record_type") for r in rows))
        # daily_lead·metrics·build_data 는 전부 truthy record_type 을 건너뛴다.
        self.assertEqual([r for r in rows if not r.get("record_type")], [])

    def test_partial_chunk_failure_does_not_lose_the_good_ones(self):
        """뒤 chunk 만 실패해도 앞 chunk 결과는 유지된다."""
        result, _ = self._run(
            {1: GeminiError("HTTP 429: RESOURCE_EXHAUSTED")}, n=4, chunk=2)
        self.assertEqual(set(result), {"h0", "h1"})
        self.assertEqual(self._records()[0]["lost"], 2)


class TestRequestFailureClassification(unittest.TestCase):
    """대응이 정반대인 실패를 한 라벨로 묶으면 둘 중 하나는 반드시 틀린다."""

    def test_quota_labels(self):
        self.assertEqual(nb.classify_request_failure(
            GeminiError("HTTP 429: rate limit")), "quota")
        self.assertEqual(nb.classify_request_failure(
            GeminiError("RESOURCE_EXHAUSTED")), "quota")

    def test_timeout_labels(self):
        self.assertEqual(nb.classify_request_failure(
            GeminiError("TimeoutError: ")), "timeout")
        self.assertEqual(nb.classify_request_failure(
            GeminiError("URLError: <urlopen error timed out>")), "timeout")

    def test_unknown_defaults_to_other(self):
        self.assertEqual(nb.classify_request_failure(
            GeminiError("응답 구조 비정상")), "other")

    def test_only_size_shaped_failures_are_splittable(self):
        self.assertEqual(nb.SPLITTABLE_FAILURES, {"truncated", "timeout"})

    def test_mixed_or_partial_failures_are_not_request_level(self):
        """품질 게이트 실패가 섞이면 분할이 아니라 기존 재생성 경로로 가야 한다."""
        chunk = [{"hash": "a"}, {"hash": "b"}]
        self.assertEqual(nb.request_failure_reason(
            {"a": ["request:quota:x"], "b": ["summary:incomplete"]}, chunk), "")
        self.assertEqual(nb.request_failure_reason(
            {"a": ["request:quota:x"]}, chunk), "", "일부만 실패면 호출 실패가 아니다")
        self.assertEqual(nb.request_failure_reason(
            {"a": ["request:truncated:x"], "b": ["request:truncated:y"]}, chunk),
            "truncated")

    def test_duplicate_hash_in_chunk_still_counts_as_request_failure(self):
        """건수로 판정하면 중복 hash 인 chunk 가 재생성·기록 어디에도 안 걸려
        조용히 사라진다 — 고치려던 그 버그가 그대로 재현된다."""
        chunk = [{"hash": "a"}, {"hash": "a"}]
        self.assertEqual(nb.request_failure_reason(
            {"a": ["request:truncated:x"]}, chunk), "truncated")


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

    def test_reject_reason_separates_llm_null_from_gate(self):
        """계측의 존재 이유. 이 둘이 갈리지 않으면 대응을 정할 수 없다.

        LLM 이 안 쓴 것이면 프롬프트를, 게이트가 먹은 것이면 조건을 봐야 한다.
        2026-08-03 실측에서 must_read 51건이 전건 0인데 어느 쪽인지 몰랐다.
        """
        reason = nb.open_question_reject_reason
        self.assertEqual(reason({"open_question": None}, "must_read"), "llm_null")
        self.assertEqual(reason({"open_question": "   "}, "must_read"), "llm_null")
        self.assertEqual(
            reason({"open_question": "계약 시점은 미정이다."}, "must_read"), "no_source")

    def test_reject_reason_labels_every_branch(self):
        reason = nb.open_question_reject_reason
        self.assertEqual(reason(self.GOOD, "must_read"), "")          # 통과
        self.assertEqual(reason(self.GOOD, "nice_to_know"), "not_must_read")
        self.assertEqual(reason({"open_question": "가" * (nb.OPEN_QUESTION_LIMIT + 1),
                                 "open_question_source": "title"}, "must_read"), "too_long")
        self.assertEqual(reason({"open_question": "최종 계약은 언제 체결될까?",
                                 "open_question_source": "title"}, "must_read"), "is_question")
        self.assertEqual(reason({"open_question": "연내 착공에 들어갈 것으로 보인다.",
                                 "open_question_source": "title"}, "must_read"), "forecast")
        self.assertEqual(reason({"open_question": "향후 대응 방향에 관심이 쏠린다.",
                                 "open_question_source": "article_text"},
                                "must_read", "incident_safety"), "incident_no_uncertainty")

    def test_reject_reason_is_the_single_source_of_truth(self):
        """norm_open_question 이 사유 판정과 어긋나면 계측이 거짓말을 한다."""
        cases = [
            (self.GOOD, "must_read", ""),
            (self.GOOD, "nice_to_know", ""),
            ({"open_question": "가" * 99, "open_question_source": "title"}, "must_read", ""),
            ({"open_question": "언제 될까?", "open_question_source": "title"}, "must_read", ""),
            ({"open_question": "조사 중이다.", "open_question_source": "title"},
             "must_read", "incident_safety"),
            ({"open_question": "관심이 쏠린다.", "open_question_source": "title"},
             "must_read", "incident_safety"),
        ]
        for item, grade, event_type in cases:
            with self.subTest(item=item, grade=grade):
                rejected = bool(nb.open_question_reject_reason(item, grade, event_type))
                dropped = nb.norm_open_question(item, grade, event_type) == ("", "unknown")
                self.assertEqual(rejected, dropped)


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


class TestOpenQuestionStats(unittest.TestCase):
    """게이트 계측 기록 — 값이 0인 원인을 재현 없이 답할 수 있어야 한다."""

    def _write(self, verdicts):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "delivery_log.jsonl"
            ok = nb.append_open_question_stats(verdicts, path=path)
            rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] \
                if path.exists() else []
            return ok, rows

    def test_counts_by_reason_and_keeps_samples(self):
        ok, rows = self._write({
            "h1": {"reason": "", "text": "계약 시점 미정이다.", "source": "title"},
            "h2": {"reason": "llm_null", "text": "", "source": ""},
            "h3": {"reason": "is_question", "text": "언제 될까?", "source": "title"},
        })
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        rec = rows[0]
        self.assertEqual(rec["record_type"], "open_question_gate")
        self.assertEqual(rec["must_read"], 3)
        self.assertEqual(rec["accepted"], 1)
        self.assertEqual(rec["reasons"], {"accepted": 1, "llm_null": 1, "is_question": 1})
        # 통과분은 샘플에 안 담는다 — 보려는 건 '무엇이 걸렸나'다.
        self.assertEqual({s["reason"] for s in rec["samples"]}, {"llm_null", "is_question"})

    def test_empty_verdicts_write_nothing(self):
        ok, rows = self._write({})
        self.assertFalse(ok)
        self.assertEqual(rows, [])

    def test_record_type_lines_are_skipped_by_existing_readers(self):
        """기사 집계를 오염시키면 안 된다 — 기존 리더는 전부 truthy 검사다."""
        _ok, rows = self._write({"h1": {"reason": "llm_null", "text": "", "source": ""}})
        self.assertTrue(rows[0].get("record_type"))
        self.assertIsNone(rows[0].get("hash"))
        self.assertIsNone(rows[0].get("importance"))

class TestBatchTemplateDoesNotPrimeEmptyValues(unittest.TestCase):
    """배치 출력 예시에 구체적 빈 값을 박으면 모델이 그 값을 그대로 베낀다.

    프로덕션 큐레이션 경로는 ``curate_batch`` 하나뿐이다.
    그래서 ``BATCH_SUFFIX`` 의 예시 JSON이 실질 스키마 지시문이다. 다른 필드가
    ``"..."`` 플레이스홀더인데 특정 필드만 ``null`` 이면 그 필드는 항상 비어서
    돌아온다 — open_question 이 배선 완료 후에도 0건이던 경로다.
    """

    OPTIONAL_FIELDS = ("open_question", "open_question_source", "event_date",
                       "event_date_type", "event_date_precision", "event_date_source")

    def _batch_example(self) -> str:
        for line in nb.BATCH_SUFFIX.splitlines():
            if line.startswith('{"items"'):
                return line
        self.fail("BATCH_SUFFIX 에서 출력 예시 JSON 줄을 찾지 못했다")

    def test_no_field_is_primed_with_a_concrete_empty_value(self):
        example = self._batch_example()
        for field in self.OPTIONAL_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(f'"{field}": null', example)
                self.assertNotIn(f'"{field}": "unknown"', example)

    def test_optional_fields_offer_the_same_choices_as_the_base_prompt(self):
        """베이스 프롬프트(``CURATION_SYSTEM_PROMPT``)와 형태가 갈리면
        배치만 조용히 다른 스키마가 된다."""
        example = self._batch_example()
        for field in self.OPTIONAL_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', example)
                self.assertIn(f'"{field}"', nb.CURATION_SYSTEM_PROMPT)
                # 값 자리에 선택지를 보여주는가 ("a|b" 또는 "...|null")
                value = example.split(f'"{field}": ', 1)[1].split(", ")[0]
                self.assertIn("|", value, f"{field} 예시값이 선택지를 제시하지 않는다: {value}")


class TestFeaturesRecuration(unittest.TestCase):
    """features 결손이 재큐레이션 대상에 들어가는가 — 실패하면 조용히 영구화된다.

    features 가 없으면 ranking 이 _legacy_score() 로 빠져 event_weights 도
    feature 가중치도 반영되지 않는다. 그런데 curation_errors() 가 features 를
    안 봐서, 한 번 결손으로 캐시되면 다시 물어보지 않았다 — 같은 10건이 큐
    만료(3일)까지 매 회차 재등장했다. 근거: docs/score_distribution.md §4.
    """

    CACHED = {
        "summary": "정부가 신규 원전 계획을 발표했다.",
        "importance": "must_read",
        "section": "domestic",
        "category": "정책",
    }

    def test_complete_record_is_not_requeried(self):
        good = {**self.CACHED, "features": {"event_type": "policy_decision"}}
        self.assertFalse(nb.needs_recuration(good))

    def test_missing_features_triggers_recuration(self):
        self.assertTrue(nb.needs_recuration(dict(self.CACHED)))

    def test_retry_stops_at_limit(self):
        # LLM 이 끝내 features 를 주지 않는 항목을 매시간 다시 묻지 않는다.
        for attempts in range(nb.FEATURES_RETRY_LIMIT):
            with self.subTest(attempts=attempts):
                self.assertTrue(nb.needs_recuration(
                    {**self.CACHED, "features_attempts": attempts}))
        self.assertFalse(nb.needs_recuration(
            {**self.CACHED, "features_attempts": nb.FEATURES_RETRY_LIMIT}))
        self.assertFalse(nb.needs_recuration(
            {**self.CACHED, "features_attempts": nb.FEATURES_RETRY_LIMIT + 5}))

    def test_other_errors_are_not_capped_by_the_features_limit(self):
        # 요약이 깨진 항목은 시도 상한과 무관하게 계속 고쳐야 한다.
        broken = {**self.CACHED, "summary": "",
                  "features_attempts": nb.FEATURES_RETRY_LIMIT + 3}
        self.assertTrue(nb.needs_recuration(broken))

    def test_batch_response_without_features_is_regenerated(self):
        article = {
            "hash": "h1", "title": "정부가 신규 원전 계획을 발표",
            "description": "정부가 신규 원전 계획을 발표했다.",
            "domain": "energy.gov", "publisher": "US DOE",
        }

        def response(features):
            item = {
                "idx": 0, "importance": "nice_to_know", "section": "international",
                "scope": "overseas", "category": "정책",
                "title_kr": "정부, 신규 원전 계획 발표",
                "summary": "정부가 신규 원전 계획을 발표했다.",
                "implication": "", "why_important": "", "tags": [],
                "topics": ["newbuild"], "countries": ["US"], "article_type": "policy",
                "event_date": None, "event_date_type": "unknown",
                "event_date_precision": "unknown", "event_date_source": "unknown",
                "related_reports": [],
            }
            if features is not None:
                item["features"] = features
            return {"items": [item]}

        with patch.object(nb, "gemini_rest_available", return_value=True), patch.object(
            nb, "gemini_call_json",
            side_effect=[response(None), response({"event_type": "policy_decision"})],
        ) as call:
            result = nb.curate_batch([article], [])
        self.assertEqual(call.call_count, 2)
        self.assertIsInstance(result["h1"]["features"], dict)


class TestFallbackCuration(unittest.TestCase):
    """batch 실패분에 등급을 얹지 않는다 — 이 승격이 must_read 오염의 원인이었다."""

    def _article(self, domain="khnp.co.kr"):
        return {
            "hash": "h1", "title": "한수원, 신규 계약 체결",
            "description": "한수원이 신규 계약을 체결했다.",
            "domain": domain, "publisher": "한수원",
        }

    def test_primary_source_is_not_promoted_to_must_read(self):
        article = self._article()
        # 이 도메인이 실제로 1차 출처로 분류되는지 먼저 확인 — 아니면 이 테스트는
        # 아무것도 검증하지 않는다.
        self.assertTrue(nb.is_tier1_source(article))
        record = nb.fallback_curation(article)
        self.assertEqual(record["importance"], "nice_to_know")

    def test_fallback_carries_no_features(self):
        # features 가 있는 척하면 ranking 이 결손을 못 알아채고, 재큐레이션도
        # 안 걸린다. 없는 것을 없다고 두는 게 계약이다.
        self.assertNotIn("features", nb.fallback_curation(self._article()))

    def test_incomplete_snippet_is_quarantined(self):
        article = {**self._article(), "description": "한수원이 신규 계약을"}
        self.assertIsNone(nb.fallback_curation(article))

    def test_fallback_record_is_recuration_candidate(self):
        record = nb.fallback_curation(self._article())
        self.assertTrue(nb.needs_recuration(record))


if __name__ == "__main__":
    unittest.main()
