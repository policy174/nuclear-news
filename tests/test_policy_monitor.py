"""공식자료 정규화·해시·후보 가드레일 테스트. 외부 호출 없음."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import policy_monitor


class TestNormalization(unittest.TestCase):
    def test_html_ignores_script_and_normalizes_whitespace(self):
        text = policy_monitor.html_to_text(
            b"<html><style>.x{}</style><body>A  B<script>ignore me</script><p>C</p></body></html>"
        )
        self.assertEqual(text, "A B C")

    def test_hash_is_stable_after_normalization(self):
        left = policy_monitor.content_hash(policy_monitor.normalize_text("A  B\nC"))
        right = policy_monitor.content_hash(policy_monitor.normalize_text("A B C"))
        self.assertEqual(left, right)

    def test_unicode_official_url_is_encoded_for_transport(self):
        self.assertEqual(
            policy_monitor.ascii_url("https://www.law.go.kr/법령/원자력안전법?항목=계속운전"),
            "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/%EC%9B%90%EC%9E%90%EB%A0%A5%EC%95%88%EC%A0%84%EB%B2%95?%ED%95%AD%EB%AA%A9=%EA%B3%84%EC%86%8D%EC%9A%B4%EC%A0%84",
        )


class TestExtractionGuardrails(unittest.TestCase):
    def test_filters_unknown_fields_and_missing_evidence(self):
        source = {
            "countryCode": "US", "url": "https://nrc.gov/a", "title": "NRC",
            "organization": "NRC",
        }

        def fake_llm(*args, **kwargs):
            return {"candidates": [
                {"fieldKey": "authority", "proposedValue": "NRC", "changeSummary": "변경", "confidence": 2,
                 "locator": "p.1", "excerpt": "The NRC approves", "language": "en"},
                {"fieldKey": "recent_trend", "proposedValue": "bad", "excerpt": "bad"},
                {"fieldKey": "procedure", "proposedValue": "no evidence", "excerpt": ""},
            ]}

        candidates, error = policy_monitor.extract_candidates(source, "document", [], fake_llm)
        self.assertIsNone(error)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["fieldKey"], "authority")
        self.assertEqual(candidates[0]["confidence"], 1.0)
        self.assertTrue(candidates[0]["evidence"]["official"])

    def test_llm_failure_returns_no_candidate(self):
        source = {"countryCode": "KR", "url": "https://law.go.kr", "title": "법령", "organization": "법제처"}

        def broken(*args, **kwargs):
            raise policy_monitor.GeminiError("offline")

        candidates, error = policy_monitor.extract_candidates(source, "document", [], broken)
        self.assertEqual(candidates, [])
        self.assertIn("실패", error)


if __name__ == "__main__":
    unittest.main()
