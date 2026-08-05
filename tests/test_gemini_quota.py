"""429 재시도 판정 — 일일 한도와 분당 한도는 처방이 정반대다."""
import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client as gc

DAILY_BODY = """{"error":{"code":429,"message":"You exceeded your current quota",
"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[
{"quotaMetric":"generativelanguage.googleapis.com/generate_content_free_tier_requests",
"quotaId":"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}"""

MINUTE_BODY = """{"error":{"code":429,"message":"You exceeded your current quota",
"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure","violations":[
{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}"""


class TestDailyQuotaDetection(unittest.TestCase):
    def test_daily_marker_is_detected(self):
        self.assertTrue(gc._is_daily_quota(DAILY_BODY))

    def test_minute_quota_is_not_daily(self):
        self.assertFalse(gc._is_daily_quota(MINUTE_BODY))

    def test_unparseable_body_defaults_to_retryable(self):
        # 판정 불가면 분당으로 보고 재시도한다 — 재시도 가능한 것을 못 하는 쪽이
        # 오늘 안 풀릴 것을 붙잡고 늘어지는 쪽보다 낫다.
        self.assertFalse(gc._is_daily_quota("HTTP 429 rate limited"))


class TestDailyQuotaIsNotRetried(unittest.TestCase):
    """일일 한도를 재시도하면 한 번 실패한 호출이 쿼터를 4배로 먹고 잡 시간까지
    잡아먹는다. 실측 2026-08-06: 6 chunk 가 전부 20+40+60초씩 자면서 크롤이
    3분에서 16분으로 늘었다."""

    def _run(self, body):
        calls = {"n": 0, "slept": []}

        def fake_urlopen(*a, **kw):
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {},
                                         BytesIO(body.encode("utf-8")))

        with patch.object(gc, "API_KEY", "test-key"), \
             patch.object(gc.urllib.request, "urlopen", fake_urlopen), \
             patch.object(gc.time, "sleep", lambda s: calls["slept"].append(s)):
            with self.assertRaises(gc.GeminiError):
                gc.call_json("system", "user", retries=3)
        return calls

    def test_daily_quota_fails_fast(self):
        calls = self._run(DAILY_BODY)
        self.assertEqual(1, calls["n"], "일일 한도인데 재시도했다")
        self.assertEqual([], calls["slept"], "일일 한도인데 백오프로 잤다")

    def test_minute_quota_still_retries(self):
        calls = self._run(MINUTE_BODY)
        self.assertGreater(calls["n"], 1, "분당 한도는 재시도해야 한다")
        self.assertTrue(calls["slept"])

    def test_error_message_keeps_the_quota_detail(self):
        """160자로 자르면 quotaId 가 잘려 어느 한도인지 로그로 못 가린다."""
        def fake_urlopen(*a, **kw):
            raise urllib.error.HTTPError("u", 429, "Too Many", {},
                                         BytesIO(DAILY_BODY.encode("utf-8")))
        with patch.object(gc, "API_KEY", "test-key"), \
             patch.object(gc.urllib.request, "urlopen", fake_urlopen), \
             patch.object(gc.time, "sleep", lambda s: None):
            try:
                gc.call_json("system", "user", retries=3)
            except gc.GeminiError as error:
                self.assertIn("quotaId", str(error))
