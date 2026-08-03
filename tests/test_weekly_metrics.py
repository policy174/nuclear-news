"""weekly_bot / metrics / gemini_client 단위 테스트."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_client
import metrics
import weekly_bot

NOW_ISO = "2026-07-12T22:00:00+00:00"


def _update(uid, data="fb:abcd1234:important", from_id=7):
    return {"update_id": uid,
            "callback_query": {"id": f"cq{uid}", "from": {"id": from_id},
                               "data": data}}


class TestWeekly(unittest.TestCase):
    def _curated(self, grade_field):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "h1" * 8: {grade_field: "must_read", "title": "T1", "title_kr": "티1",
                       "link": "https://a.com/1", "domain": "world-nuclear-news.org",
                       "section": "international", "summary": "s", "tags": ["#SMR"],
                       "cached_at": now,
                       "features": {"event_type": "contract_award",
                                    "report_worthiness": 2}},
            "h2" * 8: {grade_field: "noise", "title": "T2", "link": "https://a.com/2",
                       "cached_at": now},
            "h3" * 8: {grade_field: "nice_to_know", "title": "T3", "link": "https://a.com/3",
                       "domain": "yna.co.kr", "section": "khnp", "cached_at": now,
                       "tags": ["#SMR", "#체코수주"]},
        }

    def test_regression_importance_field(self):
        """회귀 수정 검증: 현행 스키마(importance)에서 기사가 잡혀야 함 (기존 0건 버그)."""
        items = weekly_bot.get_week_articles(self._curated("importance"))
        self.assertEqual(len(items), 2)  # noise 제외

    def test_legacy_category_grade_schema(self):
        items = weekly_bot.get_week_articles(self._curated("category"))
        self.assertEqual(len(items), 2)

    def test_old_articles_excluded(self):
        c = self._curated("importance")
        for v in c.values():
            v["cached_at"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.assertEqual(weekly_bot.get_week_articles(c), [])

    def test_aggregates(self):
        items = weekly_bot.get_week_articles(self._curated("importance"))
        agg = weekly_bot.build_aggregates(items)
        self.assertEqual(agg["total"], 2)
        self.assertEqual(agg["must_read"], 1)
        self.assertEqual(agg["event_types"].get("contract_award"), 1)
        self.assertEqual(len(agg["report_candidates"]), 1)

    def test_synthesize_no_key_fallback(self):
        old = os.environ.pop("GEMINI_API_KEY", None)
        try:
            out = weekly_bot.batch_synthesize(
                weekly_bot.get_week_articles(self._curated("importance")), {})
            self.assertEqual(out["policy_shifts"], [])
        finally:
            if old:
                os.environ["GEMINI_API_KEY"] = old

    def test_format_weekly_is_landscape_not_relisting(self):
        """weekly 는 판세 구조 — key_events 5건 제한, 일일 카드 재나열 아님."""
        items = weekly_bot.get_week_articles(self._curated("importance"))
        orig = weekly_bot.batch_synthesize
        weekly_bot.batch_synthesize = lambda i, a: {
            "weekly_intro": "핵심 흐름", "khnp_direct": "직접 영향",
            "policy_shifts": [{"what": "정책A", "so_what": "함의A"}],
            "theme_moves": [{"theme": "SMR", "direction": "강화", "why": "근거"}],
            "watchpoints": ["다음주 포인트"],
            "report_candidates": [{"topic": "보고서감", "basis": "누적"}],
            "key_events": [{"hash": "h" * 8, "headline": f"E{i}", "implication": "x"}
                           for i in range(9)],  # 9건 줘도
        }
        try:
            msg = weekly_bot.format_weekly(items)
        finally:
            weekly_bot.batch_synthesize = orig
        self.assertIn("정책 변화", msg)
        self.assertIn("투자 테마 강약", msg)
        self.assertIn("▲", msg)
        shown = sum(1 for i in range(9) if f"E{i}" in msg)
        self.assertLessEqual(shown, 5)  # key_events 는 최대 5건으로 컷


class TestMetrics(unittest.TestCase):
    def test_insufficient_data(self):
        m = metrics.compute_metrics([], 30)
        self.assertEqual(m["source_diversity"], "insufficient_data")
        self.assertEqual(m["invest_omission_rate"], "insufficient_data")

    def test_computed_when_enough(self):
        delivered = [{"date": "2026-07-10", "hash": f"h{i:02d}" + "x" * 6,
                      "region": "해외", "domain": f"d{i}.com", "theme": "smr",
                      "section": "smr"} for i in range(20)]
        m = metrics.compute_metrics(delivered, 30)
        self.assertEqual(m["invest_omission_rate"], 0.0)
        self.assertIsInstance(m["source_diversity"], float)
        self.assertEqual(m["report_rec_count"], 0)


class TestGeminiSalvage(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(gemini_client._salvage_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_preamble(self):
        self.assertEqual(gemini_client._salvage_json('결과입니다: {"a": 1} 끝'), {"a": 1})

    def test_raw_newline_in_string(self):
        self.assertEqual(gemini_client._salvage_json('{"a": "줄\n바꿈"}'),
                         {"a": "줄 바꿈"})

    def test_hopeless_raises(self):
        with self.assertRaises(Exception):
            gemini_client._salvage_json("완전 깨진 응답")


class TestWeeklyReportStore(unittest.TestCase):
    """주간 판세를 웹이 쓸 수 있게 저장한다. Gemini 호출은 늘지 않는다."""

    ITEMS = [{"hash": "aaaaaaaa1111", "title": "체코 두코바니 본계약"},
             {"hash": "bbbbbbbb2222", "title": "체코 두코바니 본계약"},
             {"hash": "cccccccc3333", "title": "미국 NRC 규정 개정"}]

    def _synthesis(self, intro="이번 주 흐름"):
        return {"weekly_intro": intro,
                "policy_shifts": [{"what": "변화", "so_what": "함의",
                                   "evidence_hashes": ["aaaaaaaa"]}],
                "theme_moves": [], "khnp_direct": "", "watchpoints": [],
                "report_candidates": [], "key_events": []}

    def test_kst_iso_week_boundary(self):
        """UTC 로 계산하면 주 경계가 엇갈린다.

        ISO 주차는 월요일에 넘어가므로 위험 구간은 KST 월요일 오전이다.
        KST 2027-01-04(월) 08:00 = UTC 2027-01-03(일) 23:00 → 2027-W01 vs 2026-W53.
        연말까지 걸쳐 있어 년·주 둘 다 어긋나는 최악의 사례다.
        두 값이 실제로 다른지 먼저 확인해 테스트가 우연히 통과하지 않게 한다.
        """
        kst_monday = datetime(2027, 1, 4, 8, 0, tzinfo=weekly_bot.KST)
        utc_year, utc_week, _ = kst_monday.astimezone(timezone.utc).isocalendar()
        self.assertEqual((utc_year, utc_week), (2026, 53))  # UTC 로 재면 전년 53주
        self.assertEqual(weekly_bot.week_id(kst_monday), "2027-W01")

    def test_saves_then_reports_no_change(self):
        """dirty 를 len(reports) 로 판정하면 덮어쓰기가 영영 저장 안 된다."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            now = datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST)
            agg = {"total": 3}
            self.assertTrue(weekly_bot.save_weekly_report(
                self._synthesis(), agg, self.ITEMS, now, path))
            # generated_at 은 매번 달라지므로 비교에서 빼야 한다
            later = now + timedelta(hours=2)
            self.assertFalse(weekly_bot.save_weekly_report(
                self._synthesis(), agg, self.ITEMS, later, path))

    def test_same_week_overwrite_is_detected(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            now = datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST)
            weekly_bot.save_weekly_report(self._synthesis(), {"total": 3},
                                          self.ITEMS, now, path)
            self.assertTrue(weekly_bot.save_weekly_report(
                self._synthesis("내용이 바뀌었다"), {"total": 3},
                self.ITEMS, now, path))
            store = weekly_bot.load_weekly_reports(path)
            self.assertEqual(len(store["reports"]), 1)
            self.assertEqual(store["reports"]["2026-W32"]["weekly_intro"], "내용이 바뀌었다")

    def test_new_week_adds_an_entry(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            weekly_bot.save_weekly_report(
                self._synthesis(), {"total": 3}, self.ITEMS,
                datetime(2026, 8, 3, 17, 0, tzinfo=weekly_bot.KST), path)
            weekly_bot.save_weekly_report(
                self._synthesis(), {"total": 3}, self.ITEMS,
                datetime(2026, 8, 10, 17, 0, tzinfo=weekly_bot.KST), path)
            self.assertEqual(sorted(weekly_bot.load_weekly_reports(path)["reports"]),
                             ["2026-W32", "2026-W33"])

    def test_source_issue_count_is_not_article_count(self):
        """기사 수를 쓰면 후속 보도가 많은 주가 실제보다 풍성해 보인다."""
        self.assertEqual(weekly_bot.count_unique_issues(self.ITEMS), 2)

    def test_evidence_hashes_pruned_to_known_and_ordered(self):
        synthesis = {"policy_shifts": [{"what": "x", "evidence_hashes": [
            "cccccccc", "deadbeef", "aaaaaaaa", "cccccccc"]}],
            "theme_moves": []}
        weekly_bot.prune_evidence_hashes(synthesis, self.ITEMS)
        # 지어낸 hash 는 화면에서 죽은 칩이 되므로 잘라낸다. 순서는 보존한다
        # (set 으로 걸러 내면 실행마다 순서가 달라져 dirty 가 항상 참이 된다).
        self.assertEqual(synthesis["policy_shifts"][0]["evidence_hashes"],
                         ["cccccccc", "aaaaaaaa"])

    def test_prune_is_deterministic(self):
        first, second = [], []
        for sink in (first, second):
            synthesis = {"policy_shifts": [{"what": "x", "evidence_hashes": [
                "cccccccc", "bbbbbbbb", "aaaaaaaa"]}], "theme_moves": []}
            weekly_bot.prune_evidence_hashes(synthesis, self.ITEMS)
            sink.extend(synthesis["policy_shifts"][0]["evidence_hashes"])
        self.assertEqual(first, second)

    def test_corrupt_store_falls_back_to_empty(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "weekly_reports.json"
            path.write_text("{ 깨진", encoding="utf-8")
            self.assertEqual(weekly_bot.load_weekly_reports(path)["reports"], {})

    def test_format_weekly_accepts_precomputed_synthesis(self):
        """합성을 한 번만 돌려 텔레그램과 웹이 같은 결과를 쓴다 (호출 +0)."""
        items = [{"hash": "aaaaaaaa1111", "title": "T", "title_kr": "제목",
                  "link": "https://example.com/a", "domain": "example.com",
                  "feed": "", "section": "international", "grade": "must_read",
                  "summary": "요약", "tags": [], "features": None,
                  "cached_at": datetime.now(timezone.utc).isoformat()}]
        text = weekly_bot.format_weekly(items, self._synthesis("고정 문구"))
        self.assertIn("고정 문구", text)


class TestWeeklyWorkflow(unittest.TestCase):
    def test_workflow_can_commit_and_rebases_on_conflict(self):
        root = Path(__file__).parent.parent
        yml = (root / ".github" / "workflows" / "weekly.yml").read_text(encoding="utf-8")
        self.assertIn("contents: write", yml)
        # 파일별 가드 — 없는 파일 하나가 스텝 전체를 죽이면 안 된다
        self.assertIn("[ -f weekly_reports.json ]", yml)
        # 단순 push 반복은 다른 워크플로가 먼저 커밋했으면 3번 다 실패한다
        self.assertIn("git rebase origin/main", yml)


if __name__ == "__main__":
    unittest.main()
