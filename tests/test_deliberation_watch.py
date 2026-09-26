"""공론화 신호 감시 — 게이트·등급·첫 실행 무음·발송 실패 재발송."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deliberation_watch as w  # noqa: E402

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=w.KST)


class GateTests(unittest.TestCase):
    def test_real_titles_from_source_survey(self):
        # 04_monitoring_sources.md ③ 의 실제 제목들
        self.assertTrue(w.gate("미래 전력수급에서 원전의 역할, 국민과 함께 논의한다"))  # '공론화' 없는 정부 제목
        self.assertTrue(w.gate("전국 시민사회, 핵발전 확대에 짜맞춘 정부 공론화계획 규탄"))
        self.assertTrue(w.gate("[보도자료]국민이 이미 답한 질문을 다시 묻는 원전 공론화, 이번에는 종결짓자"))
        self.assertIsNone(w.gate("원자력안전위원회 비상임위원 위촉"))
        self.assertIsNone(w.gate("북핵 협상 보이콧"))
        self.assertIsNone(w.gate("'기본사회' 국민숙의단 91% 공감"))
        self.assertIsNone(w.gate("사용후핵연료 관리정책 재검토위원회 공론화 결과"))

    def test_levels(self):
        off = {"group": "official", "text": ""}
        self.assertEqual(w.classify({**off, "title": "원전 공론화위원회 위원 위촉"}), "now")
        self.assertEqual(w.classify({**off, "title": "12차 전기본 석탄발전 조기폐지방안 논의"}), "digest")
        ngo = {"group": "ngo", "text": ""}
        self.assertEqual(w.classify({**ngo, "title": "답정너 원전 공론화 불참 선언"}), "now")
        self.assertEqual(w.classify({**ngo, "title": "원전 공론화, 검증 안 된 전력수요 전제"}), "digest")
        self.assertEqual(w.classify({"group": "nssc", "title": "제2026-16회 원자력안전위원회",
                                     "text": "고리3호기 계속운전 허가(안)"}), "now")
        self.assertEqual(w.classify({"group": "assembly", "title": "2026-10-14 원전 공론화 현안질의",
                                     "text": "기후에너지환경노동위원회"}), "now")

    def test_crosspost_dedupe(self):
        self.assertEqual(w.norm_key("[성명] 결론 정해놓고 명분만 쌓는 '핵발전 공론화' 즉각 중단하라!"),
                         w.norm_key("결론 정해놓고 명분만 쌓는 ‘핵발전 공론화’ 즉각 중단하라"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        w.STATE_FILE = Path(self.tmp.name) / "state.json"
        self.sent = []
        self.items = {"src": []}
        self.src = [{"name": "src", "group": "official", "kind": "x", "allow_empty": True}]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, now, fail=False):
        def sender(text):
            if fail:
                raise RuntimeError("telegram down")
            self.sent.append(text)
        return w.run(now, sources=self.src, fetcher=lambda s: [dict(i) for i in self.items[s["name"]]],
                     sender=sender)

    def test_seed_is_silent_then_alerts_only_new(self):
        self.items["src"] = [{"url": "u1", "title": "원전 공론화위원회 출범", "date": NOW}]
        self._run(NOW)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("시작", self.sent[0])          # 첫 실행은 요약 한 통뿐
        self._run(NOW + timedelta(hours=1))
        self.assertEqual(len(self.sent), 1)          # 같은 글은 다시 안 보냄
        self.items["src"].append({"url": "u2", "title": "원전 공론화 전문위원회 구성", "date": NOW})
        self._run(NOW + timedelta(hours=2))
        self.assertIn("즉시", self.sent[-1])
        self.assertIn("전문위원회 구성", self.sent[-1])

    def test_failed_send_is_retried(self):
        self._run(NOW)                                # 빈 상태로 시드
        self.items["src"] = [{"url": "u3", "title": "원전 공론화 시민참여단 구성 착수", "date": NOW}]
        with self.assertRaises(RuntimeError):
            self._run(NOW + timedelta(hours=1), fail=True)
        self._run(NOW + timedelta(hours=2))
        self.assertIn("시민참여단", self.sent[-1])

    def test_quiet_hours_hold_non_urgent_until_morning_digest(self):
        self._run(NOW)
        night = datetime(2026, 10, 15, 2, 0, tzinfo=w.KST)
        self.items["src"] = [{"url": "u4", "title": "원전 공론화 의제 확정", "date": night}]
        self._run(night)
        self.assertNotIn("의제 확정", self.sent[-1])
        self._run(datetime(2026, 10, 15, 7, 17, tzinfo=w.KST))
        self.assertIn("묶음", self.sent[-1])
        self.assertIn("의제 확정", self.sent[-1])


if __name__ == "__main__":
    unittest.main()
