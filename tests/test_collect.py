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

    def test_reject_reason_rides_on_the_record(self):
        """사유가 레코드에 실려야 사후에 원인을 짚을 수 있다.

        ``append_open_question_stats`` 가 delivery_log 에 집계를 남기지만 **크롤
        잡이 그 파일을 커밋하지 않아 러너와 함께 사라진다**(2026-08-04 실측:
        커밋된 173건이 전부 발송 기록, record_type 붙은 줄 0건). 아카이브는
        커밋되므로, 사유가 여기 없으면 다음에도 재현부터 해야 한다.
        """
        art = {"hash": "h1", "title": "t", "domain": "example.com"}
        gate_hit = nb.normalize_curation_item(
            {"importance": "must_read", "open_question": "최종 계약은 언제 체결될까?",
             "open_question_source": "article_text"}, art)
        self.assertEqual(gate_hit["open_question"], "")
        self.assertEqual(gate_hit["open_question_reject"], "is_question")
        # LLM 이 안 쓴 것과 게이트가 먹은 것은 대응이 정반대다 — 레코드만 보고
        # 갈릴 수 있어야 한다.
        llm_null = nb.normalize_curation_item(
            {"importance": "must_read", "open_question": None}, art)
        self.assertEqual(llm_null["open_question_reject"], "llm_null")
        accepted = nb.normalize_curation_item({"importance": "must_read", **self.GOOD}, art)
        self.assertEqual(accepted["open_question_reject"], "")

    def test_non_must_read_is_left_blank_not_labelled(self):
        """후보가 아닌 626건에 'not_must_read' 를 붙여도 정보가 없다."""
        out = nb.normalize_curation_item(
            {"importance": "nice_to_know", **self.GOOD},
            {"hash": "h1", "title": "t", "domain": "example.com"})
        self.assertEqual(out["open_question_reject"], "")


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


class TestCrawlWorkflowKeepsDiagnostics(unittest.TestCase):
    """크롤이 남기는 진단 기록이 커밋돼야 한다.

    2026-08-04 실측: 커밋된 delivery_log.jsonl 173건이 **전부 발송 기록**이고
    record_type 이 붙은 줄은 0건이었다. 크롤 잡의 git add 목록에 이 파일이 없어
    큐레이션 유실 기록(7b28329)과 open_question 게이트 계측(871388c)이 도입
    이후 한 줄도 남지 않았다. 둘 다 "다음에 또 나면 재현부터 하지 말자"고 만든
    기능이라, 커밋되지 않으면 존재 이유가 없다.
    """

    ROOT = Path(__file__).parent.parent

    def test_crawl_commits_the_delivery_log(self):
        yml = (self.ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        self.assertIn("delivery_log.jsonl", yml)
        # 없는 파일 하나가 스텝 전체를 죽이면 안 된다(weekly.yml 과 같은 관행).
        self.assertIn("[ -f delivery_log.jsonl ]", yml)
        # 단순 push 는 daily-brief 와 겹치는 시각에 실패하고, 크롤은 이미 Gemini
        # 호출을 마친 뒤라 그 시각 수집이 통째로 사라진다.
        self.assertIn("git rebase origin/main", yml)

    def test_llm_caches_are_committed(self):
        """캐시가 커밋되지 않으면 같은 것을 매 빌드(하루 12회+)마다 다시 묻는다.

        크롤 잡의 산출물은 러너와 함께 사라진다 — git add 목록에 없으면 캐시가
        없는 것과 같고, 무료 쿼터를 태워 429 를 부른다(issue_review 가 실제로
        그렇게 죽었다).
        """
        for name in ("crawl.yml", "daily-brief.yml"):
            yml = (self.ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for cache in ("issue_llm_reviews.json", "keei_llm_matches.json",
                          "issue_insights.json"):
                self.assertIn(cache, yml, f"{name} 에 {cache} 커밋이 빠졌다")

    def test_discovery_state_is_committed(self):
        """discovery 상태도 같은 함정에 걸린다 — 커밋 안 하면 매 시각 같은 쿼리를
        다시 던지고, 헛도는 조합을 영영 못 재운다(zero_yield_streak 이 늘 0)."""
        yml = (self.ROOT / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        self.assertIn("discovery_state.json", yml)

    def test_append_only_logs_merge_by_union(self):
        """rebase 가 붙으려면 append 충돌이 자동 해소돼야 한다.

        crawl 과 daily-brief 가 같은 파일 끝에 각자 줄을 붙이므로 기본 병합기는
        멈춘다. union 은 양쪽에서 추가된 줄을 둘 다 남긴다 — 줄 하나가 레코드
        하나인 JSONL 에 맞는 동작이고, 실측으로 확인했다(중복 없이 3줄 보존).
        """
        attrs = (self.ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("delivery_log.jsonl merge=union", attrs)
        self.assertIn("archive/*.jsonl merge=union", attrs)


class TestSourceKeywordGate(unittest.TestCase):
    """Google News 가 괄호 키워드를 무시하는 매체를 위한 수집 단계 게이트.

    실측 2026-08-05: `site:euractiv.com (nuclear OR reactor OR SMR OR uranium)
    when:2d` 가 23건을 돌려주는데 원자력 기사는 3건뿐이었다(양모·Ozempic·셍겐
    혼입). 같은 실패로 Le Figaro·電気新聞이 후보에서 탈락한 전례가 있어, 피드를
    버리는 대신 제목·요약에서 한 번 더 거른다.
    """

    def test_gate_is_opt_in(self):
        """require_keywords 가 없는 출처는 전건 통과해야 한다(기존 19개 출처)."""
        self.assertTrue(nb.passes_source_keyword_gate({}, {"title": "anything", "description": ""}))

    def test_gate_keeps_nuclear_and_drops_noise(self):
        src = {"require_keywords": nb.NUCLEAR_TITLE_KEYWORDS}
        keep = "Hungary avoids nuclear shutdown by 'millimetres' as Danube rises"
        drop = "Can wool help beat fast fashion? Farmers say EU rules stand in the way"
        self.assertTrue(nb.passes_source_keyword_gate(src, {"title": keep, "description": ""}))
        self.assertFalse(nb.passes_source_keyword_gate(src, {"title": drop, "description": ""}))

    def test_gate_reads_the_description_too(self):
        """제목이 짧은 뉴스레터 형식('The Brief – …')은 본문에 키워드가 있다."""
        src = {"require_keywords": nb.NUCLEAR_TITLE_KEYWORDS}
        item = {"title": "The Brief", "description": "Europe's parched reactor fleet"}
        self.assertTrue(nb.passes_source_keyword_gate(src, item))


class TestReferenceSiteCoverage(unittest.TestCase):
    """부서 「세계원전시장 인사이트」 절차서의 주요 기사 검색 사이트 대조.

    빠져 있던 Euractiv·NEI Magazine 을 넣은 뒤 다시 빠지지 않게 잠근다.
    UxC·BNEF·PRIS·Nuclear Asia 는 접근 경로가 없어 의도적 제외 — 사유는
    news_bot.py 의 주석에 있다.
    """

    def test_reference_sites_are_collected(self):
        urls = " ".join(source["url"] for source in nb.RSS_SOURCES)
        for domain in ("world-nuclear-news.org", "nucnet.org", "ans.org",
                       "powermag.com", "neimagazine.com", "euractiv.com"):
            self.assertIn(domain, urls, f"{domain} 수집원이 빠졌다")

    def test_euractiv_carries_a_keyword_gate(self):
        """게이트 없이 넣으면 원자력 무관 기사 20건이 매 크롤마다 큐레이션에 들어간다."""
        euractiv = [s for s in nb.RSS_SOURCES if "euractiv.com" in s["url"]]
        self.assertEqual(len(euractiv), 1)
        self.assertTrue(euractiv[0].get("require_keywords"))

    def test_new_sources_are_registered_in_sources_json(self):
        raw = json.loads((Path(nb.__file__).parent / "sources.json").read_text(encoding="utf-8"))
        domains = {entry["domain"] for group in ("tier1", "tier2", "tier3") for entry in raw[group]}
        self.assertIn("euractiv.com", domains)
        self.assertIn("neimagazine.com", domains)


if __name__ == "__main__":
    unittest.main()


class TestNaverQueryHasNoExclusionOperator(unittest.TestCase):
    """🔴 네이버 검색 API 는 '-' 를 제외 연산자로 처리하지 않는다.

    추가 검색어로 AND 결합해 버리므로 negative_terms 를 쿼리에 붙이면 결과가
    붕괴한다. 실측(2026-08-06, openapi.naver.com/v1/search/news.json):

        '계속운전'                                     total 360,614
        '계속운전 -주가 -채용 … -기념식'(프로덕션 9개)  total **0**
        '원자력 정책'                                  total 299,455 · 최신 당일
        '원자력 정책 -인사 -부고'                       total 82 · 최신 5개월 전

    국내 수집이 네이버가 아니라 Google News 국내 피드 하나로 연명하던 원인이다.
    다시 붙이면 조용히 같은 상태로 돌아가므로 쿼리 문자열을 직접 검사한다.
    """

    def _captured_query(self, keyword):
        seen = {}

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"items": []}

        def fake_get(url, headers=None, params=None, timeout=None):
            seen.update(params or {})
            return _Resp()

        with patch.dict(sys.modules):
            import requests
            with patch.object(requests, "get", fake_get):
                nb.search_naver(keyword)
        return seen.get("query", "")

    def test_query_is_the_keyword_verbatim(self):
        self.assertEqual("계속운전", self._captured_query("계속운전"))

    def test_search_naver_takes_no_negative_terms_argument(self):
        # 시그니처에 남겨 두면 호출부가 다시 넘긴다.
        with self.assertRaises(TypeError):
            nb.search_naver("계속운전", negative_terms="-주가")

    def test_keywords_json_negatives_never_reach_a_query(self):
        raw = json.loads((Path(nb.__file__).parent / "keywords.json").read_text(encoding="utf-8"))
        feeds = raw if isinstance(raw, list) else raw.get("feeds", raw.get("keywords", []))
        for feed in feeds:
            for kw in feed.get("keywords", []):
                self.assertNotIn("-", self._captured_query(kw),
                                 msg=f"'{kw}' 쿼리에 제외 연산자가 섞였다")


class TestRejectedTitles(unittest.TestCase):
    """검색 단계에서 못 거른 것을 수집 후에 결정적으로 거른다.

    ⚠️ 어휘를 손으로 베끼지 말 것. **프로덕션 keywords.json 에서 읽는다.**
    예전에는 여기 목록이 하드코딩이라 keywords.json 에 무엇이 들어 있든 테스트가
    통과했다. 그 사이 '공모'가 방폐장 부지공모 기사를 통째로 죽이고 있었다.
    """

    NEG = nb.parse_negative_terms(
        json.loads((Path(nb.__file__).parent / "keywords.json")
                   .read_text(encoding="utf-8"))["정책"]["negative_terms"])

    def test_personnel_list_titles_are_dropped(self):
        # 실측: '원자력 정책' 최신순 30건 중 20건 이상이 이 꼴이었다.
        for title in ("[인사] 경북도 (과장급)", "[인사]경북도",
                      "[8월 5일 인사종합] 신한투자증권 외",
                      "[오늘의 인사 및 동정] 8월 5일",
                      "【인사】경북도(과장급)", "[人事] 산업통상자원부"):
            with self.subTest(title=title):
                self.assertTrue(nb.is_rejected_title(title, self.NEG))

    def test_leading_keyword_without_brackets_is_kept(self):
        # 대괄호를 선택으로 두면 이런 정상 기사가 통째로 잘린다.
        self.assertFalse(nb.is_rejected_title("인사 정책 개편으로 원전 인력 확충", self.NEG))

    def test_unrelated_bracket_prefix_is_kept(self):
        self.assertFalse(nb.is_rejected_title("[증권소식] 금융위, 발행어음 인가", self.NEG))

    def test_real_nuclear_headlines_survive(self):
        for title in ("원안위, 고리 3·4호기 계속운전 하반기 심의 예정",
                      "한수원, 필리핀 아보이티즈 파워와 원전사업 협력 확대",
                      "고창군 한빛원전 범군민대책위, 고준위 건식저장정책 건의",
                      "전력거래소, 여름철 전력수급 대비 한빛원전 현장 점검"):
            with self.subTest(title=title):
                self.assertFalse(nb.is_rejected_title(title, self.NEG))

    def test_negative_vocabulary_still_applies_to_titles(self):
        # keywords.json 의 어휘를 버리지 않고 제목 제외로 재사용한다.
        for title in ("한수원 2026년 상반기 신입사원 채용 공고",
                      "두산에너빌리티 주가 급등", "[부고] 前 한수원 사장 모친상"):
            with self.subTest(title=title):
                self.assertTrue(nb.is_rejected_title(title, self.NEG))

    def test_site_selection_open_call_is_never_rejected(self):
        """'공모'는 이 도메인에서 방폐장 부지공모다. 제외어에 넣으면 안 된다.

        2026-08-06 실측 — 네이버 쿼리를 수리하면서 negative_terms 를 제목 제외어로
        재활용했는데, 그 목록의 '공모'(의도는 공모주)가 아래를 전부 죽이고 있었다.
        고준위 방폐장 부지 선정은 이 브리핑의 핵심 사안이다.
        """
        for title in ('"공모 탈락해도 비용 보전"… 지자체 부담 줄여 방폐장 후보지 발굴 속도',
                      "방폐장 부지 공모, 참여만해도 30억내외 지급",
                      "[단독]고준위 방폐장 공모만 해도 '수십억'…파격 착수금 검토",
                      "고리원자력본부, 지역상생 사업 공모 첫날 설명회",
                      "SMR 실증단지 공모 추진"):
            with self.subTest(title=title):
                self.assertFalse(nb.is_rejected_title(title, self.NEG))

    def test_share_offering_is_still_rejected(self):
        """'공모' 대신 '공모주'로 좁혔으므로 원래 의도는 살아 있어야 한다."""
        self.assertTrue(nb.is_rejected_title("두산에너빌리티 공모주 청약 경쟁률 급등", self.NEG))

    def test_nuclear_hospital_survives(self):
        """'병원'도 같은 오작동이었다 — 원자력의학원·원자력병원은 이 도메인 기관이다."""
        self.assertFalse(nb.is_rejected_title("원자력병원, 중입자 치료 임상 착수", self.NEG))

    def test_public_deliberation_keywords_are_registered(self):
        """신규 원전 공론화는 12차 전기본의 핵심 절차다 — 키워드가 있어야 한다.

        2026-08-06 실측: 8월 아카이브 571건 중 '공론' 포함 0건, '전기본' 0건.
        8/4 대통령 업무보고(신규 원전 공론화 방식 확정)가 통째로 빠져 있었다.
        """
        raw = json.loads((Path(nb.__file__).parent / "keywords.json")
                         .read_text(encoding="utf-8"))
        joined = " ".join(raw["정책"]["keywords"])
        for term in ("공론화", "12차 전기본"):
            with self.subTest(term=term):
                self.assertIn(term, joined)

    def test_deliberation_headlines_pass_the_anchor_filter(self):
        """공론화 기사는 '원전' 앵커로 이미 걸린다 — 앵커를 넓힐 필요가 없다."""
        anchors = json.loads((Path(nb.__file__).parent / "keywords.json")
                             .read_text(encoding="utf-8"))["정책"]["anchors"]
        for title in ("“신규 원전 공론화 착수하고 산업용 전기 차등 인하할 것”",
                      '김성환 기후장관 "12차 전기본에 원전·SMR 추가 포함 여부, 이달 공론화"'):
            with self.subTest(title=title):
                self.assertFalse(nb.is_rejected_title(title, self.NEG))
                self.assertTrue(nb.passes_anchor_filter(title, "", anchors))

    def test_only_the_title_is_inspected(self):
        # 본문까지 보면 "…채용 확대에 따른 원전 인력" 같은 맥락 언급으로 정상 기사가 날아간다.
        self.assertFalse(nb.is_rejected_title(
            "원안위, 신규 원전 안전기준 개정", self.NEG))

    def test_parse_negative_terms(self):
        self.assertEqual(["주가", "채용"], nb.parse_negative_terms("-주가 -채용"))
        self.assertEqual([], nb.parse_negative_terms(""))
        self.assertEqual([], nb.parse_negative_terms("   "))


class TestHomonymAnchor(unittest.TestCase):
    """'원전' 앵커에 걸리는 동음이의를 앵커 단계에서 지운다.

    2026-08-05 '원전' 최신순 실측에 「기원전 8세기 서사시」·「호메로스를 원전으로
    한 각색」이 섞였다. 제외어 수리로 네이버 유입이 늘면 이런 기사도 같이 늘고,
    전부 LLM 큐레이션까지 가면 무료 쿼터를 태운다.
    """

    ANCHORS = ["원자력", "원전", "원자로", "한빛", "전력수급"]

    def test_historical_bce_is_not_a_nuclear_anchor(self):
        self.assertFalse(nb.passes_anchor_filter(
            "학자는 혹평 vs 관객은 열광…해외서 핫한 '오디세이' 논쟁",
            "기원전 8세기 무렵의 서사시를 현대적으로 각색한 작품", self.ANCHORS))

    def test_source_text_homonym_is_not_a_nuclear_anchor(self):
        self.assertFalse(nb.passes_anchor_filter(
            "놀란의 메시지는 '전쟁 속죄'",
            "호메로스의 '오디세이아'를 원전(原典)으로 한 작품이다", self.ANCHORS))

    def test_real_nuclear_articles_still_pass(self):
        self.assertTrue(nb.passes_anchor_filter(
            "전력거래소, 여름철 전력수급 대비 한빛원전 발전설비 현장 점검",
            "김성진 이사장은 한빛원전 1·2호기 계속운전 사업을 점검했다", self.ANCHORS))

    def test_homonym_plus_real_mention_still_passes(self):
        # 동음이의를 지워도 진짜 언급이 남아 있으면 통과해야 한다.
        self.assertTrue(nb.passes_anchor_filter(
            "기원전부터 이어진 에너지의 역사, 그리고 원자력",
            "원자력 발전의 미래를 묻는다", self.ANCHORS))


class TestKeywordCoverage(unittest.TestCase):
    """2026-08-06 정답지에서 '수집조차 안 됐다'로 잡힌 주제를 키워드가 담는가.

    근거: docs/2026-08-06-recall-baseline.md — 수집 실패 4건 중 3건이 국내이고
    전부 전력시장·지역 수용성·산업정책이었다.
    """

    @classmethod
    def setUpClass(cls):
        raw = json.loads((Path(nb.__file__).parent / "keywords.json").read_text(encoding="utf-8"))
        cls.policy = " ".join(raw["정책"]["keywords"])

    def test_power_market_topics_are_covered(self):
        for term in ("차등 전기요금", "전력수급", "송전망", "데이터센터"):
            with self.subTest(term=term):
                self.assertIn(term, self.policy)

    def test_local_acceptance_topics_are_covered(self):
        for term in ("주민설명회", "공청회", "수용성", "대책위"):
            with self.subTest(term=term):
                self.assertIn(term, self.policy)

    def test_spent_fuel_storage_is_covered(self):
        for term in ("건식저장", "저장조"):
            with self.subTest(term=term):
                self.assertIn(term, self.policy)

    def test_unit_level_continued_operation_is_covered(self):
        # '원전 계속운전' 하나로는 개별 호기 일정 보도가 안 걸린다.
        for term in ("한빛 계속운전", "고리 계속운전"):
            with self.subTest(term=term):
                self.assertIn(term, self.policy)


class TestDiscoveryPlanning(unittest.TestCase):
    """후속 발굴 쿼리 생성 — 결정적, LLM 0회, 네트워크 0회.

    존재 이유: 2026-08-05 브리핑이 팍스 원전을 '마지막 터빈 안전하게 가동 중'으로
    노출한 그날, 헝가리는 44년 만에 그 원전을 세웠다. 국내 보도(뉴시스·JTBC)가
    있었지만 고정 피드 어디에도 안 걸렸다. '팍스 원전 가동 중단' 을 실제로 던지면
    그 기사들이 전부 나온다.
    """

    @classmethod
    def setUpClass(cls):
        import discovery
        import entity_match
        cls.d = discovery
        cls.registry = entity_match.load_entity_registry()

    def _rows(self, *specs):
        base = "2026-08-05T22:00:00+00:00"
        return [{"archived_at": spec.get("at", base),
                 "title_kr": spec["title"],
                 "summary": spec.get("summary", ""),
                 "canonical_tags": spec.get("tags", []),
                 "importance": spec.get("importance", "nice_to_know")} for spec in specs]

    @property
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc)

    def _plan(self, rows, state=None, budget=None):
        kwargs = {} if budget is None else {"budget": budget}
        return self.d.plan_queries(rows, self.registry,
                                   state or {"version": 1, "queries": {}},
                                   now=self.now, **kwargs)

    def test_state_change_wording_makes_an_entity_a_seed(self):
        rows = self._rows({"title": "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표"})
        queries, _ = self._plan(rows)
        self.assertIn("팍스 원전 가동 중단", [q["query"] for q in queries])

    def test_budget_is_never_exceeded(self):
        rows = self._rows(*[{"title": f"팍스 원전 {i}호기 가동 중"} for i in range(50)])
        queries, _ = self._plan(rows, budget=7)
        self.assertLessEqual(len(queries), 7)

    def test_one_entity_cannot_eat_the_whole_budget(self):
        """깊이 우선이면 홀텍 16 · 원안위 10 으로 예산이 말라 팍스를 못 물었다."""
        rows = self._rows(
            {"title": "홀텍, 오이스터 크릭 해체 승인", "importance": "must_read"},
            {"title": "원자력안전위원회, 계속운전 심사 착수", "importance": "must_read"},
            {"title": "헝가리 팍스 원전 가동 중 발표"},
        )
        queries, _ = self._plan(rows, budget=12)
        counts = {}
        for q in queries:
            counts[q["entity_id"]] = counts.get(q["entity_id"], 0) + 1
        self.assertLessEqual(max(counts.values()), self.d.MAX_QUERIES_PER_ENTITY)
        self.assertIn("paks", counts)

    def test_generic_names_are_asked_by_full_name(self):
        """고리·월성은 match_policy 가 자유문 매칭을 막아 둔 이름이다 — 별칭('고리')
        으로 물으면 밧줄·고리 같은 일반명사 기사가 쏟아진다."""
        rows = self._rows({"title": "고리 3·4호기 계속운전 심의 예정",
                           "tags": ["고리원전"]})
        queries, _ = self._plan(rows)
        kori = [q["query"] for q in queries if q["entity_id"] == "kori"]
        self.assertTrue(kori)
        for query in kori:
            self.assertTrue(query.startswith("고리 원전"), query)

    def test_event_terms_are_limited_by_entity_type(self):
        # 기관에 '재가동'을, 원전에 '수주'를 묻는 건 헛방이다.
        rows = self._rows({"title": "원자력안전위원회 전체회의 개최"})
        queries, _ = self._plan(rows)
        for q in (q for q in queries if q["entity_id"] == "nssc"):
            self.assertNotIn("가동 중단", q["query"])

    def test_zero_yield_streak_cools_a_query_down(self):
        rows = self._rows({"title": "헝가리 팍스 원전 가동 중"})
        state = {"version": 1, "queries": {}}
        queries, state = self._plan(rows, state)
        target = queries[0]
        for _ in range(self.d.ZERO_YIELD_LIMIT):
            state = self.d.record_results(
                state, [{**target, "result_count": 3, "new_article_count": 0}], now=self.now)
        again, _ = self._plan(rows, state)
        self.assertNotIn(target["fingerprint"], [q["fingerprint"] for q in again])

    def test_a_yielding_query_is_not_cooled(self):
        rows = self._rows({"title": "헝가리 팍스 원전 가동 중"})
        state = {"version": 1, "queries": {}}
        queries, state = self._plan(rows, state)
        target = queries[0]
        state = self.d.record_results(
            state, [{**target, "result_count": 3, "new_article_count": 2}], now=self.now)
        self.assertNotIn("next_eligible_at", state["queries"][target["fingerprint"]])
        again, _ = self._plan(rows, state)
        self.assertIn(target["fingerprint"], [q["fingerprint"] for q in again])

    def test_empty_archive_is_not_an_error(self):
        queries, _ = self._plan([])
        self.assertEqual([], queries)

    def test_planning_is_deterministic(self):
        rows = self._rows({"title": "헝가리 팍스 원전 가동 중"},
                          {"title": "체르나보다 원전 냉각수 부족"})
        first, _ = self._plan(rows)
        second, _ = self._plan(rows)
        self.assertEqual([q["query"] for q in first], [q["query"] for q in second])

    def test_state_pruning_keeps_the_file_bounded(self):
        from datetime import timedelta
        old = (self.now - timedelta(days=90)).isoformat()
        state = {"version": 1, "queries": {"a": {"last_run": old},
                                           "b": {"last_run": self.now.isoformat()}}}
        pruned = self.d.prune_state(state, now=self.now)
        self.assertEqual({"b"}, set(pruned["queries"]))


class TestDailyQuotaDoesNotDegradeArticles(unittest.TestCase):
    """일일 한도 소진 중에는 fallback 강등으로 큐에 넣지 않는다.

    fallback_curation 은 importance=nice_to_know + features 없음을 만든다. 그러면
    ①비원자력 기사가 noise 판정을 못 받고 들어오고(노이즈 필터가 곧 LLM 이다)
    ②features 결손이라 ranking.floor_verdict 의 면제로 하한을 우회하며
    ③sent 마킹 14일 + 아카이브 hash 스킵 때문에 영영 다시 큐레이션되지 않는다.

    실측 2026-08-06: 한 크롤에서 54/54건이 이 경로로 강등됐고 그중 16건이 사람이
    골라내야 하는 잡음이었다(비트코인·메모리·강남 부자 노인).
    분당 한도는 다음 시각에 풀리므로 fallback 이 맞다 — 일일 한도만 다르다.
    """

    def test_flag_starts_false(self):
        self.assertFalse(nb.DAILY_QUOTA_EXHAUSTED)

    def test_daily_quota_body_is_recognised(self):
        import gemini_client as gc
        self.assertTrue(gc._is_daily_quota(
            '{"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}'))
        self.assertFalse(gc._is_daily_quota(
            '{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}'))

    def test_fallback_still_exists_for_non_quota_failures(self):
        # 한도와 무관한 실패(잘림·타임아웃)까지 버리면 조용한 날 브리핑이 빈다.
        article = {"title": "원안위, 고리 3·4호기 계속운전 심의 예정",
                   "description": "원자력안전위원회가 고리 3·4호기 계속운전 심의를 하반기에 진행한다.",
                   "domain": "yna.co.kr", "publisher": "연합뉴스"}
        self.assertIsNotNone(nb.fallback_curation(article))
