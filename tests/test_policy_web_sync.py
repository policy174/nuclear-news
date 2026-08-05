"""정책 웹 뉴스 동기화 계약 테스트. 외부 호출 없음."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import policy_web_sync


class TestArchiveMapping(unittest.TestCase):
    def test_maps_archive_fields_and_features(self):
        item = policy_web_sync.archive_record_to_article({
            "hash": "1234567890abcdef",
            "archived_at": "2026-07-31T00:00:00+00:00",
            "pub": "2026-07-30T00:00:00+00:00",
            "url": "https://example.com/a",
            "title": "Original",
            "title_kr": "계속운전 결정",
            "topics": ["restart_lto"],
            "countries": ["US"],
            "features": {"event_type": "policy_decision", "policy_materiality": 2},
        })
        self.assertEqual(item["publishedAt"], "2026-07-30T00:00:00+00:00")
        self.assertEqual(item["eventType"], "policy_decision")
        self.assertEqual(item["policyMateriality"], 2)

    def test_rejects_non_http_or_short_hash(self):
        self.assertIsNone(policy_web_sync.archive_record_to_article({
            "hash": "short", "url": "https://example.com", "title": "x",
        }))
        self.assertIsNone(policy_web_sync.archive_record_to_article({
            "hash": "12345678", "url": "file:///secret", "title": "x",
        }))

    def test_batch_id_is_deterministic(self):
        rows = [{"hash": "a"}, {"hash": "b"}]
        self.assertEqual(policy_web_sync.batch_id(rows), policy_web_sync.batch_id(rows))
        self.assertNotEqual(policy_web_sync.batch_id(rows), policy_web_sync.batch_id(list(reversed(rows))))


if __name__ == "__main__":
    unittest.main()
