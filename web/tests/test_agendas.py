"""정책의제(agenda) 데이터 레이어 계약.

여기서 잠그는 것 세 가지 (계획 gleaming-waddling-pearl §C):
  1. 자동 연결은 '후보'이고 핀과 다른 칸에 실린다 — unpin 은 핀 취소이자
     후보 제외다.
  2. 깨진 핀은 조용히 사라지지 않는다 — broken_pins 로 나간다.
  3. 판단 로그의 근거 스냅샷은 first-write-wins — 이슈가 나중에 바뀌어도
     동결된 당시 모습이 유지된다.
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


def _issue(issue_id, topics=(), tags=(), entity_ids=(), last_seen="2026-09-06",
           change="변화 문장", title="이슈 제목"):
    return {
        "issue_id": issue_id,
        "title": title,
        "topics": list(topics),
        "tags": list(tags),
        "entity_ids": list(entity_ids),
        "last_seen": last_seen,
        "change_display": change,
        "verification": {"status": "corroborated", "official_source_count": 1},
        "representative_article": {
            "title_kr": f"{title} 기사", "url": "https://x.test/a",
            "article_date": "2026-09-01",
        },
    }


def _registry(**over):
    spec = {
        "id": "agenda-lto", "title": "계속운전", "question": "질문",
        "bottleneck": "병목", "bottleneck_reviewed_at": "2026-09-07",
        "transfer_conditions": "적용 조건", "next_check": "기본 다음 확인",
        "topics": ["restart_lto"], "tags": ["계속운전"], "entity_ids": [],
        "created_at": "2026-09-07",
    }
    spec.update(over)
    return [spec]


def _admin(**over):
    base = {"pins": {}, "unpins": {}, "logs": [], "next": {}}
    base.update(over)
    return base


def _build(catalog, registry=None, admin=None, events=(), snapshot_path=None):
    with tempfile.TemporaryDirectory() as raw:
        path = snapshot_path or Path(raw) / "snaps.json"
        return build_data.build_agendas_view(
            catalog, registry if registry is not None else _registry(),
            admin or _admin(), list(events), NOW, snapshot_path=path)


class LinkTests(unittest.TestCase):
    def test_auto_links_are_candidates_not_pins(self):
        view = _build([
            _issue("issue-a", topics=["restart_lto"]),
            _issue("issue-b", tags=["계속운전"]),
            _issue("issue-c", topics=["smr"]),
        ])
        agenda = view["agendas"][0]
        self.assertEqual(agenda["pinned_issue_ids"], [])
        self.assertEqual(sorted(agenda["candidate_issue_ids"]), ["issue-a", "issue-b"])
        self.assertEqual(agenda["candidate_count"], 2)

    def test_pin_moves_issue_out_of_candidates(self):
        view = _build(
            [_issue("issue-a", topics=["restart_lto"])],
            admin=_admin(pins={"agenda-lto": {"issue-a"}}))
        agenda = view["agendas"][0]
        self.assertEqual(agenda["pinned_issue_ids"], ["issue-a"])
        self.assertEqual(agenda["candidate_issue_ids"], [])
        self.assertEqual(agenda["evidence"]["issues"], 1)

    def test_unpin_excludes_from_candidates_too(self):
        view = _build(
            [_issue("issue-a", topics=["restart_lto"])],
            admin=_admin(unpins={"agenda-lto": {"issue-a"}}))
        agenda = view["agendas"][0]
        self.assertEqual(agenda["pinned_issue_ids"], [])
        self.assertEqual(agenda["candidate_issue_ids"], [])

    def test_broken_pin_is_reported_not_dropped(self):
        view = _build(
            [_issue("issue-a", topics=["restart_lto"])],
            admin=_admin(pins={"agenda-lto": {"issue-gone"}}))
        agenda = view["agendas"][0]
        self.assertEqual(agenda["broken_pins"], ["issue-gone"])
        self.assertEqual(view["coverage"]["broken_pins"], 1)

    def test_latest_news_prefers_pins(self):
        view = _build(
            [_issue("issue-old", topics=["restart_lto"], last_seen="2026-09-06",
                    change="후보 변화"),
             _issue("issue-pin", last_seen="2026-08-01", change="핀 변화")],
            admin=_admin(pins={"agenda-lto": {"issue-pin"}}))
        agenda = view["agendas"][0]
        self.assertEqual(agenda["latest_news"], "핀 변화")
        self.assertEqual(agenda["latest_news_issue_id"], "issue-pin")

    def test_next_check_kv_wins_over_registry_default(self):
        view = _build([_issue("issue-a", topics=["restart_lto"])],
                      admin=_admin(next={"agenda-lto": "갱신된 다음 확인"}))
        self.assertEqual(view["agendas"][0]["next_check"], "갱신된 다음 확인")

    def test_events_filtered_by_linked_issues(self):
        events = [
            {"id": "ev-1", "date": "2026-09-10", "label": "관련 일정", "issue_id": "issue-a"},
            {"id": "ev-2", "date": "2026-09-11", "label": "무관 일정", "issue_id": "issue-x"},
        ]
        view = _build([_issue("issue-a", topics=["restart_lto"])], events=events)
        self.assertEqual([ev["id"] for ev in view["agendas"][0]["events"]], ["ev-1"])

    def test_empty_registry_still_builds(self):
        view = _build([_issue("issue-a")], registry=[])
        self.assertEqual(view["agendas"], [])
        self.assertEqual(view["coverage"]["count"], 0)


class SnapshotTests(unittest.TestCase):
    def _log(self, log_id="log-1", issue_id="issue-a"):
        return {"id": log_id, "agenda_id": "agenda-lto", "issue_id": issue_id,
                "note": "판단", "created_at": "2026-09-07T10:00:00Z", "disabled": False}

    def test_snapshot_frozen_on_first_build_and_immutable(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "snaps.json"
            catalog = [_issue("issue-a", topics=["restart_lto"], title="처음 제목")]
            admin = _admin(logs=[self._log()])
            view = _build(catalog, admin=admin, snapshot_path=path)
            entry = view["agendas"][0]["log"][0]
            self.assertEqual(entry["evidence"]["issue_title"], "처음 제목")
            # 이슈 제목이 바뀐 두 번째 빌드 — 스냅샷은 처음 것이 남아야 한다.
            catalog2 = [_issue("issue-a", topics=["restart_lto"], title="바뀐 제목")]
            view2 = _build(catalog2, admin=admin, snapshot_path=path)
            entry2 = view2["agendas"][0]["log"][0]
            self.assertEqual(entry2["evidence"]["issue_title"], "처음 제목")

    def test_unresolvable_reference_retries_instead_of_freezing_empty(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "snaps.json"
            admin = _admin(logs=[self._log(issue_id="issue-later")])
            view = _build([_issue("issue-a", topics=["restart_lto"])],
                          admin=admin, snapshot_path=path)
            self.assertNotIn("evidence", view["agendas"][0]["log"][0])
            # 다음 빌드에 이슈가 나타나면 그때 얼린다.
            view2 = _build(
                [_issue("issue-later", topics=["restart_lto"], title="늦게 온 이슈")],
                admin=admin, snapshot_path=path)
            self.assertEqual(view2["agendas"][0]["log"][0]["evidence"]["issue_title"],
                             "늦게 온 이슈")

    def test_disabled_log_still_listed(self):
        log = self._log()
        log["disabled"] = True
        view = _build([_issue("issue-a", topics=["restart_lto"])],
                      admin=_admin(logs=[log]))
        self.assertTrue(view["agendas"][0]["log"][0]["disabled"])


class PrecedentTests(unittest.TestCase):
    def _registry(self, entries, tmp):
        path = Path(tmp) / "precedent_registry.json"
        path.write_text(json.dumps({"entries": entries}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_passthrough_and_broken_agenda_warning(self):
        entries = [
            {"id": "prec-a", "kind": "case", "title": "사례",
             "agenda_ids": ["agenda-lto", "agenda-gone"]},
            {"kind": "case", "title": "id 없는 행은 걸러짐"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            view = build_data.build_precedents_view(
                self._registry(entries, tmp), known_agenda_ids={"agenda-lto"})
        self.assertEqual([e["id"] for e in view["entries"]], ["prec-a"])

    def test_missing_registry_is_nonfatal(self):
        view = build_data.build_precedents_view(Path("no/such/registry.json"),
                                                known_agenda_ids=set())
        self.assertEqual(view, {"entries": []})


class AdminEntryTests(unittest.TestCase):
    def _overlay(self, entries, tmp):
        path = Path(tmp) / "overlay.json"
        path.write_text(json.dumps({"entries": entries}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_kinds_routed(self):
        entries = [
            {"kind": "agenda_pin", "value": "agenda-lto--issue-a", "id": "1",
             "created_at": "2026-09-07T01:00:00Z"},
            {"kind": "agenda_unpin", "value": "agenda-lto--issue-b", "id": "2",
             "created_at": "2026-09-07T01:00:00Z"},
            {"kind": "agenda_log", "value": "agenda-lto--issue-a", "id": "3",
             "reason": "판단 한 줄", "created_at": "2026-09-07T02:00:00Z"},
            {"kind": "agenda_next", "value": "agenda-lto", "id": "4",
             "reason": "오래된 확인", "created_at": "2026-09-06T00:00:00Z"},
            {"kind": "agenda_next", "value": "agenda-lto", "id": "5",
             "reason": "최신 확인", "created_at": "2026-09-07T00:00:00Z"},
            {"kind": "keyword_add", "value": "무관", "id": "6"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = build_data.load_admin_agenda_entries(self._overlay(entries, tmp))
        self.assertEqual(out["pins"], {"agenda-lto": {"issue-a"}})
        self.assertEqual(out["unpins"], {"agenda-lto": {"issue-b"}})
        self.assertEqual(len(out["logs"]), 1)
        self.assertEqual(out["logs"][0]["note"], "판단 한 줄")
        # agenda_next 는 created_at 최신 승자.
        self.assertEqual(out["next"], {"agenda-lto": "최신 확인"})

    def test_disabled_pin_skipped_but_disabled_log_kept(self):
        entries = [
            {"kind": "agenda_pin", "value": "agenda-lto--issue-a", "id": "1",
             "disabled": True},
            {"kind": "agenda_log", "value": "agenda-lto", "id": "2",
             "reason": "철회된 판단", "disabled": True,
             "created_at": "2026-09-07T00:00:00Z"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = build_data.load_admin_agenda_entries(self._overlay(entries, tmp))
        self.assertEqual(out["pins"], {})
        self.assertEqual(len(out["logs"]), 1)
        self.assertTrue(out["logs"][0]["disabled"])

    def test_missing_overlay_is_nonfatal(self):
        out = build_data.load_admin_agenda_entries(Path("no/such/overlay.json"))
        self.assertEqual(out, {"pins": {}, "unpins": {}, "logs": [], "next": {}})


if __name__ == "__main__":
    unittest.main()
