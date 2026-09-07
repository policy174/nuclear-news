"""스토리(chronicle) 원장 계약.

여기서 잠그는 것:
  1. 매칭은 기사 hash 사전 조회 — issue_id 가 갈려도 hash 가 겹치면 같은 스토리.
  2. 승격 문턱 — tracked_briefings 3회+ 또는 report_pick 만 새 스토리가 된다.
  3. first-write-wins — 같은 hash 는 다시 적히지 않아 재실행이 멱등이다.
  4. evidence 멤버는 스토리를 오염시키지 않는다.
  5. 이벤트 상한(200) 초과분은 오래된 것부터 떨어진다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402

NOW = "2026-09-07T12:00:00+00:00"


def _article(article_hash, date="2026-09-01", role="card"):
    return {
        "hash": article_hash,
        "member_role": role,
        "article_date": date,
        "briefing_date": date,
        "title_kr": f"기사 {article_hash}",
        "url": f"https://x.test/{article_hash}",
        "publisher": "예시일보",
    }


def _issue(issue_id, articles, *, tracked=3, report_pick=False,
           title="이슈 제목", first_seen="2026-09-01", last_seen="2026-09-06"):
    return {
        "issue_id": issue_id,
        "title": title,
        "entity_ids": ["ent-saeul"],
        "tracked_briefings": tracked,
        "report_pick": report_pick,
        "related_articles": articles,
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


class TestChronicleLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "chronicles.json"

    def _run(self, catalog, now=NOW):
        return build_data.update_chronicle_ledger(catalog, now, path=self.path)

    def _ledger(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["chronicles"]

    def test_qualified_issue_creates_a_chronicle(self):
        mapping, view = self._run([_issue("issue-a", [_article("h1"), _article("h2", "2026-09-02")])])
        self.assertEqual({"issue-a": "chron-h1"}, mapping)
        chron = self._ledger()["chron-h1"]
        self.assertEqual(2, len(chron["events"]))
        self.assertEqual(view["chronicles"]["chron-h1"]["title"], "이슈 제목")

    def test_below_threshold_issue_is_not_promoted(self):
        mapping, _ = self._run([_issue("issue-a", [_article("h1")], tracked=1)])
        self.assertEqual({}, mapping)
        self.assertFalse(self.path.exists())

    def test_report_pick_overrides_the_threshold(self):
        mapping, _ = self._run([_issue("issue-a", [_article("h1")], tracked=1, report_pick=True)])
        self.assertEqual({"issue-a": "chron-h1"}, mapping)

    def test_hash_overlap_survives_issue_id_churn(self):
        """60일 창에서 첫 기사가 밀려 issue_id 가 바뀌어도 hash 겹침으로 이어진다."""
        self._run([_issue("issue-a", [_article("h1"), _article("h2")])])
        mapping, _ = self._run([
            _issue("issue-B-reborn", [_article("h2"), _article("h3", "2026-09-05")], tracked=1)
        ])
        # 문턱 미달이어도 기존 스토리에는 이어 붙는다 — 승격 문턱은 '신규 생성'에만.
        self.assertEqual({"issue-B-reborn": "chron-h1"}, mapping)
        hashes = {e["hash"] for e in self._ledger()["chron-h1"]["events"]}
        self.assertEqual({"h1", "h2", "h3"}, hashes)

    def test_rerun_is_idempotent(self):
        catalog = [_issue("issue-a", [_article("h1"), _article("h2")])]
        self._run(catalog)
        first = self.path.read_text(encoding="utf-8")
        self._run(catalog, now="2026-09-08T12:00:00+00:00")
        # 새 이벤트가 없으면 updated_at 도 원장도 그대로 — first-write-wins.
        self.assertEqual(first, self.path.read_text(encoding="utf-8"))

    def test_evidence_members_do_not_join_the_story(self):
        mapping, _ = self._run([_issue("issue-a", [
            _article("h1"), _article("noise", role="evidence")])])
        hashes = {e["hash"] for e in self._ledger()["chron-h1"]["events"]}
        self.assertEqual({"h1"}, hashes)

    def test_event_cap_drops_the_oldest(self):
        articles = [_article(f"h{i:03d}", f"2026-08-{(i % 28) + 1:02d}")
                    for i in range(build_data.CHRONICLE_EVENT_CAP + 5)]
        self._run([_issue("issue-a", articles)])
        events = self._ledger()[f"chron-{min(a['hash'] for a in articles)}"]["events"]
        self.assertEqual(build_data.CHRONICLE_EVENT_CAP, len(events))

    def test_corrupt_ledger_starts_fresh_without_crashing(self):
        self.path.write_text("{깨진 json", encoding="utf-8")
        mapping, _ = self._run([_issue("issue-a", [_article("h1")])])
        self.assertEqual({"issue-a": "chron-h1"}, mapping)


if __name__ == "__main__":
    unittest.main()
