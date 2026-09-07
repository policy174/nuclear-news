"""스토리 국면형 서사(chronicle_narrative.py) 계약 — report_draft 와 같은 골격.

외부 호출 0. 게이트는 개조식의 반대(완결 서술문 강제)라는 점, 전용 모델 버킷,
digest 캐시, publish_only 경로를 잠근다.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

import chronicle_narrative as cn

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

GOOD_PAYLOAD = {
    "phase_now": "재가동 승인 후 안정화 단계에 있다.",
    "narrative": [
        "8월 말 새울 3호기가 운전원 실수로 정지되며 사안이 시작되었다.",
        "9월 초 원안위가 재가동을 승인했고, 이후 시운전이 이어지고 있다.",
    ],
    "watchpoints": ["상업운전 전환 일정이 다음 분기 안에 확정되는지."],
}


def _chronicle(cid="chron-h1", updated="2026-09-07T06:00:00+00:00", n_events=3):
    return {
        "chronicle_id": cid,
        "title": "새울 3호기 시운전",
        "events": [
            {"hash": f"h{i}", "article_date": f"2026-09-0{i + 1}",
             "briefing_date": f"2026-09-0{i + 1}", "title_kr": f"기사 {i}",
             "url": f"https://x.test/{i}", "publisher": "예시일보"}
            for i in range(n_events)
        ],
        "first_seen": "2026-09-01",
        "last_seen": "2026-09-06",
        "updated_at": updated,
    }


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.kwargs = []

    def is_available(self):
        return True

    def call_json(self, *args, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        return self.payload


class GateTests(unittest.TestCase):
    def test_good_payload_passes(self):
        self.assertEqual([], cn.gate(GOOD_PAYLOAD))

    def test_bullet_marks_are_banned(self):
        bad = dict(GOOD_PAYLOAD, narrative=["□ 개조식 문장이 들어왔다."])
        self.assertTrue(any("금지 표기" in p for p in cn.gate(bad)))

    def test_narrative_must_end_as_a_sentence(self):
        bad = dict(GOOD_PAYLOAD, narrative=["명사형 종결", "두 번째 문단이다."])
        self.assertTrue(any("완결 서술문" in p for p in cn.gate(bad)))

    def test_empty_narrative_fails(self):
        self.assertTrue(cn.gate({"phase_now": "국면이다.", "narrative": []}))


class RunTests(unittest.TestCase):
    def _run(self, tmp, chronicles, client, **kw):
        with mock.patch.object(cn, "LEDGER_FILE", tmp / "chronicles.json"), \
             mock.patch.object(cn, "CACHE_FILE", tmp / "cache.json"), \
             mock.patch.object(cn, "PUBLIC_FILE", tmp / "public.json"):
            (tmp / "chronicles.json").write_text(
                json.dumps({"schema_version": 1, "chronicles": chronicles},
                           ensure_ascii=False), encoding="utf-8")
            return cn.run(client=client, now=NOW, **kw)

    def test_generates_and_caches(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_PAYLOAD)
            stats = self._run(tmp, {"chron-h1": _chronicle()}, client)
            self.assertEqual(stats["generated"], 1)
            self.assertEqual(stats["published"], 1)
            stats = self._run(tmp, {"chron-h1": _chronicle()}, client)
            self.assertEqual(stats["cached"], 1)
            self.assertEqual(client.calls, 1)

    def test_quiet_chronicles_are_not_targets(self):
        """오늘 이벤트가 없던 연대기는 재질의하지 않는다."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_PAYLOAD)
            stats = self._run(
                tmp, {"chron-h1": _chronicle(updated="2026-09-01T06:00:00+00:00")},
                client)
            self.assertEqual(stats["targets"], 0)
            self.assertEqual(client.calls, 0)

    def test_uses_a_dedicated_model_bucket(self):
        """model 을 명시해야 기본 경로 체인을 타지 않는다 — 큐레이션과 경합 0."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_PAYLOAD)
            self._run(tmp, {"chron-h1": _chronicle()}, client)
            self.assertEqual(client.kwargs[0].get("model"),
                             cn.CHRONICLE_MODEL_DEFAULT)

    def test_gate_failure_retries_once_then_caches_the_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient({"phase_now": "", "narrative": []})
            stats = self._run(tmp, {"chron-h1": _chronicle()}, client)
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["published"], 0)
            self.assertEqual(client.calls, 2)
            stats = self._run(tmp, {"chron-h1": _chronicle()}, client)
            self.assertEqual(stats["cached"], 1)
            self.assertEqual(client.calls, 2)

    def test_publish_only_uses_cache_without_calls(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_PAYLOAD)
            self._run(tmp, {"chron-h1": _chronicle()}, client)
            stats = self._run(tmp, {"chron-h1": _chronicle()}, None,
                              publish_only=True)
            self.assertEqual(stats["published"], 1)
            self.assertEqual(client.calls, 1)

    def test_per_run_cap(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            chronicles = {f"chron-h{i}": _chronicle(f"chron-h{i}")
                          for i in range(cn.MAX_PER_RUN + 2)}
            client = _FakeClient(GOOD_PAYLOAD)
            stats = self._run(tmp, chronicles, client)
            self.assertEqual(stats["generated"], cn.MAX_PER_RUN)

    def test_missing_ledger_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            with mock.patch.object(cn, "LEDGER_FILE", tmp / "none.json"), \
                 mock.patch.object(cn, "CACHE_FILE", tmp / "cache.json"), \
                 mock.patch.object(cn, "PUBLIC_FILE", tmp / "public.json"):
                stats = cn.run(client=None, now=NOW, publish_only=True)
            self.assertEqual(stats["status"], "ok")
            self.assertEqual(stats["published"], 0)


if __name__ == "__main__":
    unittest.main()
