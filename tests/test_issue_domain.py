# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import issue_domain  # noqa: E402

TAX = {"규제": ["원안위", "인허가심사"], "안전성": ["사고고장"]}
ROWS = [{"issue_id": "a", "title": "원안위, 규정 개정안 의결", "summary": "핵연료물질 사용 허가"},
        {"issue_id": "b", "title": "하마오카 데이터 조작", "summary": ""}]


class FakeClient:
    calls = 0

    @staticmethod
    def is_available():
        return True

    @classmethod
    def call_json(cls, *_args, **_kw):
        cls.calls += 1
        return {"items": [{"idx": 0, "domain": "규제", "tag": "원안위"},
                          {"idx": 1, "domain": "없는분류", "tag": "x"}]}


class ParseTest(unittest.TestCase):
    def test_unknown_domain_dropped_and_bad_tag_stripped(self):
        payload = {"items": [{"idx": 0, "domain": "규제", "tag": "엉뚱"},
                             {"idx": 1, "domain": "없는분류", "tag": "원안위"},
                             {"idx": 9, "domain": "규제", "tag": "원안위"}]}
        self.assertEqual(issue_domain.parse(payload, ROWS, TAX), {0: ("규제", [])})

    def test_generate_caches_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            FakeClient.calls = 0
            out, stats = issue_domain.generate(ROWS, client=FakeClient, cache_path=path)
            self.assertEqual(out, {"a": ("규제", ["원안위"])})
            self.assertEqual((stats["calls"], stats["asked"], stats["failed"]), (1, 1, 1))
            saved = json.loads(path.read_text(encoding="utf-8"))["domains"]
            self.assertIn("a", saved)
            self.assertNotIn("b", saved)   # 응답에서 버린 이슈는 다음에 다시 묻는다
            out2, stats2 = issue_domain.generate(ROWS[:1], client=FakeClient, cache_path=path)
            self.assertEqual((stats2["from_cache"], stats2["calls"]), (1, 0))

    def test_prompt_lists_every_domain(self):
        prompt = issue_domain._system_prompt()
        for domain in issue_domain.taxonomy():
            self.assertIn(f"- {domain}:", prompt)


if __name__ == "__main__":
    unittest.main()
