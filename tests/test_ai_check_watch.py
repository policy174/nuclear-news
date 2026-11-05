"""AI 설명 점검 감시 — 게시판 파싱·첫 실행·야간 보류·실패 격리."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ai_check_watch as w  # noqa: E402

BASE = {"as_of": "", "signals": [], "baseline": [], "questions": [
    {"q": "공모에 신청이 없으면?", "official": "x", "tag": "대조 문서에서 미확인", "a": {"Gemini": "재공모"}},
    {"q": "경주는 왜 빠지나", "official": "y", "tag": "대체로 일치", "a": {"Gemini": "법 규정"}},
]}


def fake_call(body):
    if "tools" in body:
        return {"candidates": [{"content": {"parts": [{"text": "답변"}]}, "groundingMetadata": {"groundingChunks": [{"web": {"title": "a.kr"}}]}}]}
    verdict = {"summary": "요약", "tag": "대체로 일치", "changed": False, "why": ""}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(verdict, ensure_ascii=False)}]}}]}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.data, self.state = self.d / "data.json", self.d / "s.json"
        self.data.write_text(json.dumps(BASE, ensure_ascii=False), encoding="utf-8")
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, now, items, failed=(), **kw):
        return w.run(now, fetcher=lambda: (items, list(failed)), call=fake_call, sender=self.sent.append,
                     state_file=self.state, data_file=self.data, news=lambda n: {"rows": []}, **kw)

    def test_parse_hrwc(self):
        page = '<a onclick="fn_detail(10)"\n class="title-link">\n <span class="title-text">배제기준 확정(&#039;26.10.30)</span></a>'
        got = w.parse_hrwc(page, "press")
        self.assertEqual(got[0]["id"], "hrwc-press-10")
        self.assertIn("'26.10.30", got[0]["title"])

    def test_first_run_is_quiet_about_old_posts(self):
        item = {"id": "a", "title": "공모 안내", "url": "https://x", "source": "고준위위"}
        self.go(datetime(2026, 11, 5, 10, tzinfo=w.KST), [item])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("연결 완료", self.sent[0])
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8"))["signals"], [])

    def test_new_post_requeries_related_question(self):
        now = datetime(2026, 11, 5, 10, tzinfo=w.KST)
        self.go(now, [])
        self.go(now, [{"id": "b", "title": "공모 방안 공고", "url": "https://x", "source": "고준위위"}])
        self.assertIn("공식 변경", self.sent[-1])
        self.assertIn("변화 없음", self.sent[-1])
        q = json.loads(self.data.read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual(q["latest"]["summary"], "요약")

    def test_night_holds_message(self):
        self.go(datetime(2026, 11, 5, 2, tzinfo=w.KST), [])
        self.assertEqual(self.sent, [])
        self.go(datetime(2026, 11, 5, 8, tzinfo=w.KST), [])
        self.assertEqual(len(self.sent), 1)

    def test_weekly_runs_once(self):
        mon = datetime(2026, 11, 9, 8, tzinfo=w.KST)
        self.go(mon, [])
        self.go(mon, [])
        self.assertEqual(sum("이번 주" in m for m in self.sent), 1)

    def test_bad_verdict_is_rejected(self):
        bad = lambda b: {"candidates": [{"content": {"parts": [{"text": '{"summary":"x","tag":"틀림"}'}]}}]}
        with self.assertRaises(ValueError):
            w.check_question(BASE["questions"][0], [], bad, "d")


if __name__ == "__main__":
    unittest.main()
