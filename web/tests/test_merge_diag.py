"""병합 진단 계약 (C단계).

왕복이 핵심이다: 콘솔이 KV 에 적은 쌍 판정을 build_data 가 **같은 pair id
계약으로** 읽어 병합/분리에 반영해야 한다. 여기서 잠그는 것은 ①판정 해석
②분리 우선 ③투영이 화면에 필요한 것만 담는가 셋.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402


def overlay(*entries):
    tmp = Path(tempfile.mkdtemp()) / "admin_overrides.json"
    tmp.write_text(json.dumps({"version": 1, "entries": list(entries)}), encoding="utf-8")
    return tmp


def pair_entry(kind, value, **extra):
    return {"id": f"id-{value}", "kind": kind, "group": "병합", "value": value, **extra}


class AdminPairJudgmentTests(unittest.TestCase):
    def test_join_and_split_are_read(self):
        path = overlay(pair_entry("pair_join", "aaaa--bbbb"),
                       pair_entry("pair_split", "cccc--dddd"))
        result = build_data.load_admin_pair_judgments(path)
        self.assertEqual(result["approved"], {"aaaa--bbbb"})
        self.assertEqual(result["rejected"], {"cccc--dddd"})

    def test_pair_id_is_normalised_to_sorted_order(self):
        # 화면이 어느 쪽을 먼저 눌렀든 저장·조회 키가 같아야 한다.
        path = overlay(pair_entry("pair_join", "bbbb--aaaa"))
        self.assertEqual(build_data.load_admin_pair_judgments(path)["approved"],
                         {build_data._pair_id("aaaa", "bbbb")})

    def test_disabled_and_non_pair_entries_ignored(self):
        path = overlay(pair_entry("pair_join", "aaaa--bbbb", disabled=True),
                       pair_entry("keyword_add", "이집트 원전"),
                       pair_entry("pair_split", "--"))
        result = build_data.load_admin_pair_judgments(path)
        self.assertEqual(result, {"approved": set(), "rejected": set()})

    def test_missing_file_is_empty_not_crash(self):
        result = build_data.load_admin_pair_judgments(Path("no-such-overlay.json"))
        self.assertEqual(result, {"approved": set(), "rejected": set()})

    def test_split_beats_join(self):
        # 잘못 붙은 카드가 잘못 갈라진 카드보다 신뢰를 크게 깎는다.
        merged = build_data.merge_pair_judgments(
            {"approved": {"a--b"}, "rejected": set()},
            {"approved": set(), "rejected": {"a--b"}})
        self.assertEqual(merged["approved"], set())
        self.assertEqual(merged["rejected"], {"a--b"})

    def test_file_and_console_sources_combine(self):
        merged = build_data.merge_pair_judgments(
            {"approved": {"a--b"}, "rejected": set()},
            {"approved": {"c--d"}, "rejected": {"e--f"}})
        self.assertEqual(merged["approved"], {"a--b", "c--d"})
        self.assertEqual(merged["rejected"], {"e--f"})


class MergeDiagProjectionTests(unittest.TestCase):
    AUDIT = {
        "clusters": [{
            "issue_id": "issue-1", "first_seen": "2026-08-01", "last_seen": "2026-08-02",
            "members": [{"hash": "aaaa", "article_date": "2026-08-01", "title": "원문 제목"},
                        {"hash": "bbbb", "article_date": "2026-08-02", "title": "후속 보도"}],
            "matches": [{"hash": "bbbb", "reference_hash": "aaaa", "score": 0.49,
                         "title_ratio": 0.66, "embedding_similarity": None,
                         "method": "title_tags", "blocked_by": []}],
        }],
        "review_candidates": [
            {"candidate_id": "cccc--dddd", "candidate_score": 0.2,
             "left_hash": "cccc", "right_hash": "dddd", "left_title": "낮은 점수",
             "right_title": "낮은 점수 2", "left_date": "1", "right_date": "2", "diagnostics": {}},
            {"candidate_id": "eeee--ffff", "candidate_score": 0.64,
             "left_hash": "eeee", "right_hash": "ffff", "left_title": "높은 점수",
             "right_title": "높은 점수 2", "left_date": "1", "right_date": "2",
             "diagnostics": {"title_ratio": 0.5, "tag_shared": 0}},
        ],
    }
    RECORDS = [
        {"hash": "aaaa", "title_kr": "원문 제목", "publisher": "연합뉴스",
         "archived_at": "2026-08-01T00:00:00+00:00", "folded_count": 2,
         "folded": [{"hash": "zzzz", "title": "접힌 기사", "publisher": "조선", "stage": "title_similar"}]},
        {"hash": "bbbb", "title_kr": "후속 보도", "publisher": "한겨레",
         "archived_at": "2026-08-02T00:00:00+00:00", "folded_count": 0, "folded": []},
    ]

    def build(self):
        return build_data.build_merge_diagnostics(
            self.AUDIT, self.RECORDS, {"approved": {"a--b"}, "rejected": set()})

    def test_merged_carries_members_and_pair_id(self):
        diag = self.build()
        issue = diag["merged"][0]
        self.assertEqual(len(issue["members"]), 2)
        self.assertEqual(issue["members"][0]["publisher"], "연합뉴스")   # 레코드에서 보강
        self.assertEqual(issue["matches"][0]["pair_id"], build_data._pair_id("aaaa", "bbbb"))

    def test_near_miss_sorted_by_score_desc(self):
        diag = self.build()
        self.assertEqual(diag["near_miss"][0]["pair_id"], "eeee--ffff")
        self.assertGreater(diag["near_miss"][0]["score"], diag["near_miss"][1]["score"])

    def test_why_drops_empty_values(self):
        why = self.build()["merged"][0]["matches"][0]["why"]
        self.assertIn("score", why)
        self.assertNotIn("embedding_similarity", why)   # None 은 싣지 않는다
        self.assertNotIn("blocked_by", why)             # 빈 리스트도

    def test_folds_only_include_articles_with_ledger(self):
        diag = self.build()
        self.assertEqual(len(diag["folds"]), 1)
        self.assertEqual(diag["folds"][0]["hash"], "aaaa")
        self.assertEqual(diag["folds"][0]["folded_count"], 2)

    def test_counts_separate_empty_ledger_from_missing_ledger(self):
        # 0건이 '접힌 게 없다'인지 '아직 안 쌓였다'인지 화면이 갈라야 한다.
        counts = self.build()["counts"]
        self.assertEqual(counts["records"], 2)
        self.assertEqual(counts["records_with_ledger"], 2)
        legacy = build_data.build_merge_diagnostics(
            self.AUDIT, [{"hash": "old", "title": "장부 이전 레코드"}],
            {"approved": set(), "rejected": set()})
        self.assertEqual(legacy["counts"]["records_with_ledger"], 0)


if __name__ == "__main__":
    unittest.main()
