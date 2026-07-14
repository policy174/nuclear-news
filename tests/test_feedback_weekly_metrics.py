"""feedback_ingest / weekly_bot / metrics / gemini_client 단위 테스트."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))

import feedback_ingest as fi
import gemini_client
import metrics
import weekly_bot

NOW_ISO = "2026-07-12T22:00:00+00:00"


def _update(uid, data="fb:abcd1234:important", from_id=7):
    return {"update_id": uid,
            "callback_query": {"id": f"cq{uid}", "from": {"id": from_id},
                               "data": data}}


class TestFeedbackIngest(unittest.TestCase):
    def test_parse_callback(self):
        self.assertEqual(fi.parse_callback("fb:abcd1234:noise"), ("abcd1234", "noise"))
        self.assertIsNone(fi.parse_callback("fb:abcd1234:buy_now"))  # 미정의 라벨
        self.assertIsNone(fi.parse_callback("hello"))
        self.assertIsNone(fi.parse_callback("fb::important"))

    def test_duplicate_update_id_skipped(self):
        seen_ids, seen_triples = {100}, set()
        events, _, max_id = fi.extract_events(
            [_update(100), _update(101)], seen_ids, seen_triples, NOW_ISO)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["update_id"], 101)
        self.assertEqual(max_id, 101)

    def test_offset_rollback_recovery(self):
        """state push 실패로 offset 이 과거로 → 월 파일 update_id 검사가 중복 차단."""
        month_ids = {200, 201}
        events, _, _ = fi.extract_events(
            [_update(200), _update(201), _update(202)], set(month_ids), set(), NOW_ISO)
        self.assertEqual([e["update_id"] for e in events], [202])

    def test_same_user_same_button_twice_recorded_once(self):
        events, _, _ = fi.extract_events(
            [_update(300), _update(301)], set(), set(), NOW_ISO)  # 같은 data·같은 유저
        self.assertEqual(len(events), 1)

    def test_answers_collected_even_for_duplicates(self):
        _, answers, _ = fi.extract_events([_update(1), _update(2)], set(), set(), NOW_ISO)
        self.assertEqual(len(answers), 2)  # 스피너 해제는 중복이어도 응답


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
        m = metrics.compute_metrics([], [], 30)
        self.assertEqual(m["positive_rate"], "insufficient_data")
        self.assertEqual(m["ndcg_at_k"], "insufficient_data")

    def test_computed_when_enough(self):
        delivered = [{"date": "2026-07-10", "hash": f"h{i:02d}" + "x" * 6,
                      "region": "해외", "domain": f"d{i}.com", "theme": "smr",
                      "section": "smr"} for i in range(10)]
        feedback = ([{"ts": NOW_ISO, "hash": f"h{i:02d}" + "x" * 6, "label": "important"}
                     for i in range(10)]
                    + [{"ts": NOW_ISO, "hash": f"h{i:02d}" + "x" * 6, "label": "noise"}
                       for i in range(10)])
        m = metrics.compute_metrics(delivered, feedback, 30)
        self.assertEqual(m["positive_rate"], 0.5)
        self.assertEqual(m["noise_rate"], 0.5)
        self.assertIsInstance(m["precision_at_k"], float)
        self.assertEqual(m["invest_omission_rate"], 0.0)

    def test_ndcg_perfect_ranking(self):
        fb = {"aaaa1111": {"important"}}
        day = [{"hash": "aaaa1111"}, {"hash": "bbbb2222"}]
        self.assertEqual(metrics._ndcg_for_day(day, fb), 1.0)
        day_bad = [{"hash": "bbbb2222"}, {"hash": "aaaa1111"}]
        self.assertLess(metrics._ndcg_for_day(day_bad, fb), 1.0)


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


if __name__ == "__main__":
    unittest.main()
