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


REAL_RPM_BODY = """{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.\n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-2.5-flash\nPlease retry in 42.364493284s.","status":"RESOURCE_EXHAUSTED"}}"""


class TestRetryDelayIsHonoured(unittest.TestCase):
    """429 는 얼마나 기다릴지를 서버가 알려준다. 고정 사다리를 쓰면 안 된다.

    실측 2026-08-06 크롤(run 31062468331): `limit: 20, model: gemini-2.5-flash` +
    `Please retry in 42.364493284s.` 인데 사다리는 20초에 첫 재시도를 냈다.
    **첫 두 번(20s·40s)은 실패가 보장돼 있고 각각 요청을 하나씩 더 태운다** —
    분당 한도를 맞고 있는 와중에 그 한도를 더 깎는다.
    """

    def test_message_hint_is_parsed(self):
        delay = gc._retry_delay_seconds(REAL_RPM_BODY)
        self.assertIsNotNone(delay)
        self.assertGreater(delay, 42.0, "서버가 요구한 시간보다 일찍 깨면 또 튕긴다")

    def test_retry_info_field_is_preferred(self):
        self.assertEqual(8.0, gc._retry_delay_seconds('{"retryDelay": "7s"}'))

    def test_absent_hint_falls_back_to_the_ladder(self):
        self.assertIsNone(gc._retry_delay_seconds("HTTP 429 rate limited"))

    def test_absurd_value_is_capped(self):
        # 파싱이 어긋나면 잡을 통째로 잡아먹는다.
        self.assertEqual(gc.RETRY_DELAY_MAX, gc._retry_delay_seconds("retry in 9999s"))

    def test_zero_is_ignored(self):
        self.assertIsNone(gc._retry_delay_seconds("retry in 0s"))

    def test_sleep_uses_the_server_value_not_the_ladder(self):
        slept = []

        def fake_urlopen(*a, **kw):
            raise urllib.error.HTTPError("u", 429, "Too Many", {},
                                         BytesIO(REAL_RPM_BODY.encode("utf-8")))

        with patch.object(gc, "API_KEY", "test-key"), \
             patch.object(gc.urllib.request, "urlopen", fake_urlopen), \
             patch.object(gc.time, "sleep", lambda s: slept.append(s)):
            with self.assertRaises(gc.GeminiError):
                gc.call_json("system", "user", retries=2)
        self.assertTrue(slept)
        for value in slept:
            self.assertGreater(value, 42.0, f"사다리 값({value}s)이 그대로 쓰였다")

    def test_rpm_body_is_not_mistaken_for_daily(self):
        # limit/model 만 있고 PerDay 표지가 없다 — 재시도해야 하는 종류다.
        self.assertFalse(gc._is_daily_quota(REAL_RPM_BODY))
