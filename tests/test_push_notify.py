# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import push_notify  # noqa: E402


class PayloadTest(unittest.TestCase):
    def test_top3_titles_in_site_order(self):
        briefings = [
            {"date": "2026-09-15", "issues": [{"title": "옛것"}]},
            {"date": "2026-09-16", "issues": [{"title": "첫째 " + "가" * 60}, {"title": "둘째"},
                                              {"title": "셋째"}, {"title": "넷째"}]},
        ]
        p = push_notify.build_payload(briefings)
        self.assertTrue(p["title"].startswith("Nuclens 09/16 브리핑 · 4건"))
        lines = p["body"].split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("1. 첫째") and len(lines[0]) <= 44)
        self.assertEqual(lines[2], "3. 셋째")
        self.assertEqual(p["tag"], "nuclens-brief-2026-09-16")

    def test_empty_falls_back(self):
        p = push_notify.build_payload([])
        self.assertIn("올라왔습니다", p["body"])


if __name__ == "__main__":
    unittest.main()
