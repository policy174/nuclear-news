"""흐름 해석 — 근거 번호 없이 온 응답은 파일을 덮지 않는다 (2026-09-26 배포 19시간 차단)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import trend_insights as t  # noqa: E402

KW = [{"keyword": "SMR", "count_now": 5, "count_prev": 1,
       "articles": [{"hash": "h1", "title_kr": "제목", "url": "u", "date": "2026-09-26"}]}]


class EvidenceTests(unittest.TestCase):
    def _gen(self, reply):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "trend_insights.json"
            out.write_text("OLD", encoding="utf-8")
            with mock.patch.object(t, "OUT_FILE", out), \
                 mock.patch.object(t, "is_available", return_value=True), \
                 mock.patch.object(t, "load_recent_articles", return_value=[]), \
                 mock.patch.object(t, "pick_keywords", return_value=KW), \
                 mock.patch.object(t, "call_json", return_value=reply):
                ok = t.generate()
            return ok, out.read_text(encoding="utf-8")

    def test_no_evidence_keeps_old_file(self):
        ok, text = self._gen({"items": [{"keyword": "SMR", "direction": "해석", "evidence_idx": []}]})
        self.assertFalse(ok)
        self.assertEqual(text, "OLD")

    def test_with_evidence_writes(self):
        ok, text = self._gen({"items": [{"keyword": "SMR", "direction": "해석", "evidence_idx": [0]}]})
        self.assertTrue(ok)
        self.assertIn('"h1"', text)


if __name__ == "__main__":
    unittest.main()
