"""미큐레이션 백필 — 아카이브 라인 제자리 수선 계약.

curated.json 이 아니라 아카이브 레코드가 큐레이션 여부의 정본이다
(build_data 는 record.features 로 판정). append_records 가 hash 재적재를
차단하므로 라인 교체가 유일한 복구 경로다.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import backfill_curation as bf
import news_archive


def _rec(h, *, features=None, attempts=0, archived_at="2026-09-01T10:00:00+09:00",
         **extra):
    rec = {
        "v": 2, "hash": h, "archived_at": archived_at,
        "url": f"https://example.com/{h}", "domain": "example.com",
        "publisher": "예시일보", "title": f"기사 {h}", "title_kr": "",
        "summary": "", "features": features,
    }
    if attempts:
        rec["features_attempts"] = attempts
    rec.update(extra)
    return rec


CUR = {
    "title_kr": "수선된 제목", "summary": "요약 문장.", "importance": "nice_to_know",
    "section": "국내", "scope": "kr", "category": "정책", "tags": [], "topics": [],
    "countries": ["한국"], "article_type": "news",
    "features": {"event_type": "policy_decision", "korea_relevance": 2,
                 "market_materiality": 0, "policy_materiality": 2,
                 "report_worthiness": 0},
}


class TestSelectTargets(unittest.TestCase):
    def test_only_unlabeled_within_attempt_cap_newest_first(self):
        records = [
            _rec("aaa", archived_at="2026-09-01T10:00:00+09:00"),
            _rec("bbb", archived_at="2026-09-03T10:00:00+09:00"),
            _rec("cured", features={"event_type": "other"}),      # 라벨 있음 — 제외
            _rec("gaveup", attempts=3),                           # 상한 도달 — 제외
            _rec("dropped", quality_drop="manual"),               # 격리 — 제외
        ]
        targets = bf.select_targets(records, limit=10)
        self.assertEqual(["bbb", "aaa"], [r["hash"] for r in targets])

    def test_limit_caps_the_batch(self):
        records = [_rec(f"h{i}") for i in range(5)]
        self.assertEqual(2, len(bf.select_targets(records, limit=2)))


class TestRebuildAndApply(unittest.TestCase):
    def test_success_replaces_line_and_failure_bumps_attempts(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            rows = [_rec("okhash"), _rec("failhash", attempts=1),
                    _rec("cured", features={"event_type": "other"})]
            month = archive / "2026-09.jsonl"
            month.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8")

            with patch.object(news_archive, "ARCHIVE_DIR", archive):
                by_path = bf._load_archive_lines()
                targets = bf.select_targets(
                    [r for rs in by_path.values() for r in rs])
                updated = bf.rebuild_records(
                    targets, {"okhash": dict(CUR)},
                    now_iso="2026-09-07T16:05:00+09:00")
                replaced = bf.apply_updates(by_path, updated)

            self.assertEqual(2, replaced)
            out = [json.loads(line) for line in
                   month.read_text(encoding="utf-8").splitlines()]
            by_hash = {r["hash"]: r for r in out}
            # 성공 건: 라벨이 채워지고 스탬프가 남는다 (once-per-day 근거)
            self.assertIsNotNone(by_hash["okhash"]["features"])
            self.assertEqual("수선된 제목", by_hash["okhash"]["title_kr"])
            self.assertEqual("2026-09-07T16:05:00+09:00",
                             by_hash["okhash"]["backfilled_at"])
            # 원래 archived_at 보존 — 수선이 수집 이력으로 위장하면 안 된다
            self.assertEqual("2026-09-01T10:00:00+09:00",
                             by_hash["okhash"]["archived_at"])
            # 실패 건: 시도만 누적 — 상한(3)에 닿으면 다음 실행에서 제외된다
            self.assertIsNone(by_hash["failhash"]["features"])
            self.assertEqual(2, by_hash["failhash"]["features_attempts"])
            # 무관 라인은 그대로
            self.assertEqual({"event_type": "other"}, by_hash["cured"]["features"])

    def test_untouched_month_is_not_rewritten(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "2026-08.jsonl").write_text(
                json.dumps(_rec("old", features={"event_type": "other"}),
                           ensure_ascii=False) + "\n", encoding="utf-8")
            before = (archive / "2026-08.jsonl").read_text(encoding="utf-8")
            with patch.object(news_archive, "ARCHIVE_DIR", archive):
                by_path = bf._load_archive_lines()
                replaced = bf.apply_updates(by_path, {})
            self.assertEqual(0, replaced)
            self.assertEqual(before,
                             (archive / "2026-08.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
