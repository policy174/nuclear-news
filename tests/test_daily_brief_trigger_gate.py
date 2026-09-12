import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import daily_brief_trigger_gate as gate  # noqa: E402


class DailyBriefTriggerGateTests(unittest.TestCase):
    def decide(self, event, *, conclusion="success", now="2026-08-27T21:10:00+00:00",
               outbox=None):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.json"
            if outbox is not None:
                path.write_text(json.dumps(outbox), encoding="utf-8")
            return gate.decide(
                event_name=event,
                workflow_conclusion=conclusion,
                now=datetime.fromisoformat(now),
                outbox_path=path,
            )

    def test_primary_schedule_always_runs(self):
        self.assertTrue(self.decide("schedule")[0])

    def test_manual_dispatch_always_runs(self):
        self.assertTrue(self.decide("workflow_dispatch")[0])

    def test_successful_crawl_recovers_missing_morning_brief(self):
        # 21:10 UTC == 06:10 KST
        self.assertTrue(self.decide("workflow_run", outbox={
            "date": "2026-08-27", "status": "sent"
        })[0])

    def test_fallback_skips_when_today_is_already_sent(self):
        self.assertFalse(self.decide("workflow_run", outbox={
            "date": "2026-08-28", "status": "sent"
        })[0])

    def test_fallback_skips_failed_crawl(self):
        self.assertFalse(self.decide("workflow_run", conclusion="failure")[0])

    def test_fallback_skips_outside_morning_window(self):
        # 09:10 UTC == 18:10 KST
        self.assertFalse(self.decide(
            "workflow_run", now="2026-08-28T09:10:00+00:00"
        )[0])


if __name__ == "__main__":
    unittest.main()
