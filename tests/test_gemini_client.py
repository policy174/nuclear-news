"""Gemini 호출 계측 — 429 의 범인을 세어서 가린다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client


class TestCallInstrumentation(unittest.TestCase):
    """429 는 분당 20회였는데 그 1분에 누가 몇 번 불렀는지가 로그에 없었다.

    원인을 두 번 잘못 짚은 뒤에 넣은 계측이다 — ①"일일 한도 소진"(틀림, RPM)
    ②"격리 항목 개별 재시도"(틀림, 품질 게이트 재생성은 배치 1회).
    """

    def setUp(self):
        gemini_client.reset_call_log()

    def tearDown(self):
        gemini_client.reset_call_log()

    def test_empty_log_reports_zero(self):
        self.assertEqual(gemini_client.call_stats()["total"], 0)
        self.assertIn("0회", gemini_client.format_call_stats())

    def test_counts_split_by_model_and_label(self):
        gemini_client._record_call("gemini-2.5-flash", "curation")
        gemini_client._record_call("gemini-2.5-flash", "curation:retry")
        gemini_client._record_call("gemini-2.5-flash-lite", "issue_review")
        stats = gemini_client.call_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["per_model"]["gemini-2.5-flash"], 2)
        self.assertEqual(stats["per_model"]["gemini-2.5-flash-lite"], 1)
        self.assertEqual(stats["per_label"]["curation:retry"], 1)

    def test_peak_uses_a_sliding_window_not_clock_minutes(self):
        """'시:분' 경계로 세면 59초와 61초에 걸친 폭주를 못 잡는다.

        한도는 슬라이딩 60초로 걸리므로 여기서도 그렇게 잰다.
        """
        base = 1000.0
        gemini_client._CALL_LOG.extend(
            (base + offset, "m", "x") for offset in (0.0, 30.0, 59.0, 61.0, 200.0))
        peak = gemini_client.call_stats()["peak_per_minute"]["m"]
        # 0·30·59 가 한 창에 들어간다. 61 은 0 에서 60 초를 넘겨 빠진다.
        self.assertEqual(peak, 3)

    def test_counter_is_bounded(self):
        for _ in range(gemini_client.CALL_LOG_LIMIT + 50):
            gemini_client._record_call("m", "x")
        self.assertEqual(len(gemini_client._CALL_LOG), gemini_client.CALL_LOG_LIMIT)

    def test_retry_is_counted_as_its_own_call(self):
        """재시도도 한도를 깎는다. attempt 를 안 세면 'chunk 4회'가 거짓말이 된다."""
        import inspect
        source = inspect.getsource(gemini_client.call_json)
        self.assertIn("_record_call", source)
        self.assertIn("retry", source)
