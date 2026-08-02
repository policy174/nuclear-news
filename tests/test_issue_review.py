"""issue_review.py 단위 테스트 — 회색지대 선별·캐시·실패 시 보수 동작."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import issue_review


def candidate(pair_id="a--b", similarity=0.90, left="고리 3·4호기 계속운전 심의 지연",
              right="원전 4기 계속운전 절차 지연", blocked=None):
    return {
        "candidate_id": pair_id,
        "left_title": left,
        "right_title": right,
        "diagnostics": {
            "embedding_similarity": similarity,
            "blocked_by": blocked or [],
        },
    }


class FakeClient:
    """gemini_client 대역. 호출 기록과 응답을 통제한다."""

    MODEL = "fake-model"

    def __init__(self, responses=None, available=True, raises=False):
        self.responses = list(responses or [])
        self._available = available
        self.raises = raises
        self.calls = []

    def is_available(self):
        return self._available

    def call_json(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if self.raises:
            raise RuntimeError("429 rate limited")
        return self.responses.pop(0) if self.responses else {"items": []}


def verdict_response(count, same=True):
    return {"items": [{"idx": i, "same_event": same, "reason": "테스트"} for i in range(count)]}


class BandTests(unittest.TestCase):
    def test_band_is_low_inclusive_high_exclusive(self):
        self.assertTrue(issue_review.in_review_band({"embedding_similarity": 0.88}))
        self.assertTrue(issue_review.in_review_band({"embedding_similarity": 0.919}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": 0.92}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": 0.879}))

    def test_blocked_pairs_never_reach_llm(self):
        diag = {"embedding_similarity": 0.90, "blocked_by": ["facility_conflict"]}
        self.assertFalse(issue_review.in_review_band(diag))

    def test_missing_or_bad_similarity_is_excluded(self):
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": None}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": "높음"}))
        self.assertFalse(issue_review.in_review_band({}))
        self.assertFalse(issue_review.in_review_band(None))

    def test_select_pairs_filters_and_dedupes(self):
        rows = [
            candidate("in1", 0.90),
            candidate("in1", 0.90),          # 중복
            candidate("out_high", 0.95),
            candidate("out_low", 0.80),
            candidate("blocked", 0.90, blocked=["country_conflict"]),
            {"no_id": True},
        ]
        picked = issue_review.select_pairs(rows)
        self.assertEqual([row["candidate_id"] for row in picked], ["in1"])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmp.name) / "issue_llm_reviews.json"

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, rows, client, **kw):
        return issue_review.review_pairs(rows, cache_path=self.cache_path, client=client, **kw)

    def test_approved_pair_is_returned_and_cached(self):
        client = FakeClient([verdict_response(1, same=True)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual((stats["calls"], stats["approved"], stats["rejected"]), (1, 1, 0))
        self.assertTrue(self.cache_path.exists())
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertTrue(cached["reviews"]["p1"]["same_event"])

    def test_second_run_uses_cache_and_makes_no_call(self):
        self.review([candidate("p1")], FakeClient([verdict_response(1)]))
        client = FakeClient([verdict_response(1)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual(client.calls, [])
        self.assertEqual((stats["calls"], stats["from_cache"]), (0, 1))

    def test_prompt_version_change_invalidates_cache(self):
        self.review([candidate("p1")], FakeClient([verdict_response(1)]))
        raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        raw["reviews"]["p1"]["prompt_version"] = issue_review.PROMPT_VERSION - 1
        self.cache_path.write_text(json.dumps(raw), encoding="utf-8")
        client = FakeClient([verdict_response(1)])
        _verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(stats["calls"], 1)

    def test_rejected_pair_is_not_merged(self):
        client = FakeClient([verdict_response(1, same=False)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": False})
        self.assertEqual(stats["rejected"], 1)

    def test_missing_api_key_merges_nothing(self):
        client = FakeClient(available=False)
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(client.calls, [])

    def test_call_failure_merges_nothing_and_is_not_cached(self):
        client = FakeClient(raises=True)
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "partial_failure")
        self.assertEqual(stats["failed"], 1)
        self.assertFalse(self.cache_path.exists())

    def test_malformed_response_drops_only_the_bad_pair(self):
        client = FakeClient([{"items": [
            {"idx": 0, "same_event": True, "reason": "정상"},
            {"idx": 1, "same_event": "아마도"},          # bool 아님
            {"idx": 99, "same_event": True},             # 범위 밖
        ]}])
        rows = [candidate("p0"), candidate("p1"), candidate("p2")]
        verdicts, stats = self.review(rows, client)
        self.assertEqual(verdicts, {"p0": True})
        self.assertEqual(stats["failed"], 2)

    def test_batches_are_split_by_size(self):
        rows = [candidate(f"p{i}") for i in range(25)]
        client = FakeClient([verdict_response(20), verdict_response(5)])
        _verdicts, stats = self.review(rows, client, batch_size=20)
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["asked"], 25)

    def test_no_candidates_short_circuits(self):
        client = FakeClient()
        verdicts, stats = self.review([candidate("p1", similarity=0.5)], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "no_candidates")
        self.assertEqual(client.calls, [])

    def test_user_message_lists_every_pair_with_index(self):
        rows = [candidate("p0", left="A제목", right="B제목"),
                candidate("p1", left="C제목", right="D제목")]
        message = issue_review.build_user_message(rows)
        self.assertIn("[0]", message)
        self.assertIn("[1]", message)
        self.assertIn("A제목", message)
        self.assertIn("D제목", message)


if __name__ == "__main__":
    unittest.main(verbosity=1)
