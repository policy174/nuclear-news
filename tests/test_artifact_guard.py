"""산출물 가드 계약 — 네트워크 0.

이 가드는 매일 아침 도는 잡을 빨갛게 만들 권한을 갖는다. 그래서 검사해야 할
것은 "고장을 잡는가"만이 아니라 **"멀쩡한 날 헛울리지 않는가"**다 — 헛울리는
경보는 꺼진 경보보다 나쁘다(사람이 무시하기 시작하면 진짜 고장도 묻힌다).
아래 절반이 오탐 방지 케이스인 이유가 그것이다.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import artifact_guard  # noqa: E402

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 13, 7, 30, tzinfo=KST)
TODAY = "2026-09-13"


def _issue(issue_id, *, pick=True, last_seen=TODAY, articles=True):
    return {
        "issue_id": issue_id,
        "report_pick": pick,
        "last_seen": last_seen,
        "related_articles": [{"hash": "a"}] if articles else [],
    }


class GuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.drafts = self.tmp / "report_drafts.json"
        patcher_data = patch.object(artifact_guard, "DATA", self.data)
        patcher_drafts = patch.object(artifact_guard, "DRAFTS_FILE", self.drafts)
        patcher_data.start(); patcher_drafts.start()
        self.addCleanup(patcher_data.stop)
        self.addCleanup(patcher_drafts.stop)
        self.addCleanup(self._tmp.cleanup)
        # 기본: 모든 검사가 통과하는 건강한 상태.
        # 이슈 카탈로그는 누적본(현재 477건)이라 **비면 그 자체가 고장**이다.
        # 그래서 건강한 기본값은 "report_pick 이 아닌 이슈 1건" — 카탈로그는
        # 차 있고 초안 대상은 없는, 조용한 날의 모습.
        self.write_core(briefing_date=TODAY)
        self.write_drafts(self.drafts, [])
        self.write_issues([_issue("i0", pick=False)])

    def write(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def write_core(self, *, briefing_date):
        self.write(self.data / "briefings.json", [{"date": briefing_date}])
        self.write(self.data / "news.json", [{"hash": "a"}])
        self.write(self.data / "meta.json", {"archive_total": 1})

    def write_issues(self, issues):
        self.write(self.data / "issues.json", issues)

    def write_drafts(self, path, ids):
        self.write(path, {"drafts": {i: {"lines": ["x"]} for i in ids}})

    # ---- 오탐 방지 (멀쩡한 날 울리면 안 되는 것) ----

    def test_healthy_day_passes(self):
        self.assertEqual(artifact_guard.check(None, now=NOW), [])

    def test_no_pending_targets_passes_even_with_zero_new_drafts(self):
        # 조용한 날: report_pick 대상이 없으면 초안이 안 생기는 게 정상이다.
        baseline = self.tmp / "before.json"
        self.write_drafts(baseline, ["i1"])
        self.write_drafts(self.drafts, ["i1"])
        self.write_issues([_issue("i1")])          # 이미 초안 보유 → pending 0
        self.assertEqual(artifact_guard.check(baseline, now=NOW), [])

    def test_missing_baseline_skips_the_draft_check(self):
        # 첫 도입일·수동 실행: 기준선이 없으면 증가를 판정할 수 없다 → 침묵.
        self.write_issues([_issue("i1")])          # pending 있지만 기준선 없음
        self.write_drafts(self.drafts, [])
        self.assertEqual(artifact_guard.check(self.tmp / "없음.json", now=NOW), [])

    def test_stale_report_pick_outside_the_window_is_not_a_target(self):
        old = (NOW - timedelta(days=artifact_guard.RECENT_DAYS + 5)).date().isoformat()
        baseline = self.tmp / "before.json"
        self.write_drafts(baseline, [])
        self.write_issues([_issue("i1", last_seen=old)])
        self.assertEqual(artifact_guard.check(baseline, now=NOW), [])

    def test_report_pick_without_articles_is_not_a_target(self):
        baseline = self.tmp / "before.json"
        self.write_drafts(baseline, [])
        self.write_issues([_issue("i1", articles=False)])
        self.assertEqual(artifact_guard.check(baseline, now=NOW), [])

    def test_new_id_passes_even_when_the_count_did_not_grow(self):
        # 캐시가 하나 만료되고 하나 생긴 날 — 개수는 그대로지만 일은 됐다.
        # 개수 비교였다면 여기서 헛울린다. 집합 비교인 이유.
        baseline = self.tmp / "before.json"
        self.write_drafts(baseline, ["i_old"])
        self.write_drafts(self.drafts, ["i_new"])
        self.write_issues([_issue("i_new"), _issue("i2")])
        self.assertEqual(artifact_guard.check(baseline, now=NOW), [])

    # ---- 진짜 고장 (반드시 잡아야 하는 것) ----

    def test_pending_targets_with_no_new_draft_fails(self):
        # 2026-09-08~12 실사고: 대상 26건 적체, generated 0 인데 잡은 초록이었다.
        baseline = self.tmp / "before.json"
        self.write_drafts(baseline, [])
        self.write_drafts(self.drafts, [])
        self.write_issues([_issue("i1"), _issue("i2")])
        failures = artifact_guard.check(baseline, now=NOW)
        self.assertEqual(len(failures), 1)
        self.assertIn("신규 초안 0건", failures[0])

    def test_missing_today_briefing_fails(self):
        self.write_core(briefing_date="2026-09-10")
        failures = artifact_guard.check(None, now=NOW)
        self.assertTrue(any("브리핑이" in f for f in failures), failures)

    def test_empty_core_file_fails(self):
        self.write(self.data / "news.json", [])
        failures = artifact_guard.check(None, now=NOW)
        self.assertTrue(any("news.json" in f for f in failures), failures)

    def test_missing_core_file_fails(self):
        (self.data / "issues.json").unlink()
        failures = artifact_guard.check(None, now=NOW)
        self.assertTrue(any("issues.json" in f for f in failures), failures)

    # ---- 오디오 신선도 ----

    def _write_audio(self, date):
        (self.data / "audio").mkdir(exist_ok=True)
        self.write(self.data / "audio" / "audio.json", {"date": date})

    def test_audio_missing_file_is_not_a_failure(self):
        # 캐시 미스와 생성 실패를 구별할 수 없다 — 없는 걸 실패로 세면
        # 캐시가 비는 날마다 헛울린다.
        self.assertEqual(artifact_guard.check(None, now=NOW), [])

    def test_audio_one_day_old_passes(self):
        # 하루 못 만드는 건 쿼터 빠듯한 날의 정상 범위. 알리지 않는다.
        self._write_audio("2026-09-12")
        self.assertEqual(artifact_guard.check(None, now=NOW), [])

    def test_audio_stale_for_three_days_fails(self):
        # 2026-09-06~12 엿새를 아무도 몰랐던 그 상태.
        self._write_audio("2026-09-10")
        failures = artifact_guard.check(None, now=NOW)
        self.assertTrue(any("오디오가 3일째" in f for f in failures), failures)

    def test_recent_days_matches_report_draft(self):
        # 두 곳이 갈라지면 가드가 report_draft 가 안 만드는 대상을 요구하게 된다.
        import report_draft
        self.assertEqual(artifact_guard.RECENT_DAYS, report_draft.RECENT_DAYS)


if __name__ == "__main__":
    unittest.main()
