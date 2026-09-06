"""report_draft 계약 — 개조식 게이트와 캐시·상한 규칙.

게이트는 khnp-report 스킬의 check_style 을 이식한 것이다. 여기서 잠그는 것은
**서술문이 초안으로 나가지 않는 것** — 게이트가 느슨해지면 "~논의가 본격화되었다"
같은 재진술이 개조식 라벨을 달고 그대로 배포된다.
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import report_draft as rd

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)

GOOD_LINES = [
    "1. 개요",
    " □ (배경) 발전공기업 5사 통합 법안 '26.8월 발의",
    " □ (경과) 국회 산자위 상정 논의 개시",
    "2. 주요 내용",
    " □ 통합 대상은 발전 5사, 한수원은 제외",
    "  ○ 법안은 '27년 시행을 목표로 명시",
    "3. 시사점",
    " □ (전력시장) 통합 법인의 시장 지위 변화가 정산 구조에 미칠 영향 검토 필요",
]


class GateTests(unittest.TestCase):
    def test_good_draft_passes(self):
        self.assertEqual(rd.gate(GOOD_LINES), [])

    def test_narrative_ending_caught(self):
        lines = list(GOOD_LINES)
        lines[4] = " □ 통합 대상은 발전 5사이며 한수원은 제외되었다."
        problems = rd.gate(lines)
        self.assertTrue(any("서술형" in p for p in problems))

    def test_level_skip_caught(self):
        lines = ["1. 개요", " ○ □ 없이 ○부터 시작", "3. 시사점", " □ (라벨) 검토 필요"]
        problems = rd.gate(lines)
        self.assertTrue(any("건너뛰기" in p for p in problems))

    def test_conclusion_label_required_after_first_box(self):
        lines = [
            "3. 시사점",
            " □ 첫 항목은 라벨 없이 재진술 가능",
            " □ 둘째 항목인데 라벨 없음",
        ]
        problems = rd.gate(lines)
        self.assertEqual(sum("라벨" in p for p in problems), 1)

    def test_banned_marks_caught(self):
        lines = list(GOOD_LINES)
        lines[4] = " □ 통합 — **귀추가 주목**되는 사안"
        self.assertTrue(any("금지" in p for p in rd.gate(lines)))

    def test_missing_conclusion_caught(self):
        self.assertIn("시사점 절 없음", rd.gate(GOOD_LINES[:6]))


def _issue(issue_id="issue-a", last_seen="2026-09-06", pick=True, hashes=("h1", "h2")):
    return {
        "issue_id": issue_id,
        "title": "테스트 이슈",
        "report_pick": pick,
        "report_pick_why": "정책 영향",
        "last_seen": last_seen,
        "related_articles": [
            {"hash": h, "article_date": "2026-09-01", "title_kr": "기사", "summary": "요약"}
            for h in hashes
        ],
    }


class TargetTests(unittest.TestCase):
    def test_only_recent_picks(self):
        rows = rd._targets(
            [_issue(), _issue("issue-old", last_seen="2026-07-01"),
             _issue("issue-nopick", pick=False)], NOW)
        self.assertEqual([r["issue_id"] for r in rows], ["issue-a"])

    def test_digest_tracks_membership(self):
        a, b = _issue(), _issue(hashes=("h1", "h3"))
        self.assertNotEqual(rd._digest(a), rd._digest(b))
        self.assertEqual(rd._digest(a), rd._digest(_issue(hashes=("h2", "h1"))))


class _FakeClient:
    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def is_available(self):
        return True

    def call_json(self, *args, **kwargs):
        self.calls += 1
        return {"lines": self.lines}


class RunTests(unittest.TestCase):
    def _run(self, tmp, issues, client):
        with mock.patch.object(rd, "ISSUES_FILE", tmp / "issues.json"), \
             mock.patch.object(rd, "CACHE_FILE", tmp / "cache.json"), \
             mock.patch.object(rd, "PUBLIC_FILE", tmp / "public.json"):
            (tmp / "issues.json").write_text(
                json.dumps(issues, ensure_ascii=False), encoding="utf-8")
            return rd.run(client=client, now=NOW)

    def test_generates_and_caches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_LINES)
            stats = self._run(tmp, [_issue()], client)
            self.assertEqual(stats["generated"], 1)
            self.assertEqual(stats["published"], 1)
            # 같은 재료로 다시 돌면 캐시 적중 — 호출 0회 추가.
            stats = self._run(tmp, [_issue()], client)
            self.assertEqual(stats["cached"], 1)
            self.assertEqual(client.calls, 1)

    def test_gate_failure_publishes_nothing_but_caches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            bad = [line if not line.startswith(" □ 통합") else " □ 통합되었다."
                   for line in GOOD_LINES]
            client = _FakeClient(bad)
            stats = self._run(tmp, [_issue()], client)
            self.assertEqual(stats["failed"], 1)
            self.assertEqual(stats["published"], 0)
            self.assertEqual(client.calls, 2)  # 되먹임 재시도 1회 포함
            # 실패도 캐시 — 같은 digest 로 재질의하지 않는다.
            stats = self._run(tmp, [_issue()], client)
            self.assertEqual(stats["cached"], 1)
            self.assertEqual(client.calls, 2)

    def test_publish_only_uses_cache_without_calls(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = _FakeClient(GOOD_LINES)
            self._run(tmp, [_issue()], client)
            # publish_only: 클라이언트 없이도 캐시에서 공개 파일이 나온다.
            with mock.patch.object(rd, "ISSUES_FILE", tmp / "issues.json"), \
                 mock.patch.object(rd, "CACHE_FILE", tmp / "cache.json"), \
                 mock.patch.object(rd, "PUBLIC_FILE", tmp / "public.json"):
                stats = rd.run(client=None, now=NOW, publish_only=True)
            self.assertEqual(stats["published"], 1)
            self.assertEqual(client.calls, 1)

    def test_per_run_cap(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            issues = [_issue(f"issue-{i}") for i in range(rd.MAX_PER_RUN + 2)]
            client = _FakeClient(GOOD_LINES)
            stats = self._run(tmp, issues, client)
            self.assertEqual(stats["generated"], rd.MAX_PER_RUN)


if __name__ == "__main__":
    unittest.main()
