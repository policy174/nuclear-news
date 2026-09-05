"""event_calendar 계약 — 문장 하나에서 날짜와 이름을 같은 절에서 짝짓는다.

정직성 계약: 창([today, today+30]) 밖은 세우지 않고 dropped 로 센다.
'9월 중'은 events 가 아니라 month_notes 다 — 달력 칸에 넣는 순간 1일 일정이 된다.
"""
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import event_calendar  # noqa: E402

TODAY = date(2026, 9, 4)


def _article(summary, pub, hash_="h1", title="한수원 부지 공모 관련 기사", **kw):
    row = {
        "hash": hash_,
        "article_date": pub,
        "title_kr": title,
        "title": title,
        "summary": summary,
        "detail": "",
        "url": f"https://example.test/{hash_}",
        "publisher": "테스트일보",
        "topics": ["regulation"],
    }
    row.update(kw)
    return row


def _build(articles, today=TODAY, **kw):
    return event_calendar.build(articles, today, **kw)


class ExtractionTests(unittest.TestCase):
    def test_range_clause(self):
        cal = _build([_article(
            "한국수력원자력이 신규 원전 부지 유치 신청을 9월 10일부터 10월 2일까지 접수한다.",
            "2026-09-01")])
        self.assertEqual(len(cal["events"]), 1)
        ev = cal["events"][0]
        self.assertEqual(ev["kind"], "range")
        self.assertEqual(ev["date"], "2026-09-10")
        self.assertEqual(ev["end_date"], "2026-10-02")
        self.assertEqual(ev["origin"], "clause")
        self.assertIn("접수", ev["label"])

    def test_range_day_shorthand_inherits_month(self):
        # "9월 8일부터 12일까지" — 끝날짜가 달을 상속한다.
        cal = _build([_article("정기 점검을 9월 8일부터 12일까지 시행한다.", "2026-09-01")])
        ev = cal["events"][0]
        self.assertEqual(ev["kind"], "range")
        self.assertEqual(ev["date"], "2026-09-08")
        self.assertEqual(ev["end_date"], "2026-09-12")

    def test_deadline_only(self):
        cal = _build([_article("사업자 공모 서류를 9월 15일까지 제출해야 한다.", "2026-09-01")])
        ev = cal["events"][0]
        self.assertEqual(ev["kind"], "deadline")
        self.assertEqual(ev["date"], ev["end_date"])
        self.assertEqual(ev["date"], "2026-09-15")

    def test_point_needs_event_keyword(self):
        # 사건 낱말 없는 날짜는 배경이지 일정이 아니다.
        with_kw = _build([_article("규제 혁신 세미나를 9월 10일 개최한다.", "2026-09-01")])
        without_kw = _build([_article("9월 10일 기준 가동률은 82%였다.", "2026-09-01")])
        self.assertEqual(len(with_kw["events"]), 1)
        self.assertEqual(with_kw["events"][0]["kind"], "point")
        self.assertEqual(len(without_kw["events"]), 0)

    def test_year_rollover(self):
        cal = _build([_article("차기 전력수급기본계획 공청회를 1월 10일 개최한다.", "2026-12-20")],
                     today=date(2026, 12, 22))
        self.assertEqual(cal["events"][0]["date"], "2027-01-10")

    def test_past_event_dropped_not_rolled_forward(self):
        # 발행일 기준 최근접 연도로 읽으므로 8월 행사는 과거가 되고 창이 거른다.
        cal = _build([_article("행사를 8월 5일부터 7일까지 개최했다.", "2026-08-08")])
        self.assertEqual(len(cal["events"]), 0)
        self.assertEqual(cal["dropped"]["out_of_window"], 1)

    def test_jinan_point_reference_skipped(self):
        cal = _build([_article("지난 9월 2일 개최한 설명회에서 의견이 나왔다.", "2026-09-03")])
        self.assertEqual(len(cal["events"]), 0)

    def test_far_future_counts_dropped(self):
        cal = _build([_article("차기 총회를 12월 20일 개최한다.", "2026-09-01")])
        self.assertEqual(len(cal["events"]), 0)
        self.assertEqual(cal["dropped"]["out_of_window"], 1)

    def test_ongoing_range_kept(self):
        # 이미 시작한 기간 — 시작일이 창 앞이어도 끝이 창 안이면 남는다.
        cal = _build([_article("특별법 하위법령을 8월 20일부터 9월 20일까지 입법예고한다.",
                               "2026-08-18")])
        self.assertEqual(len(cal["events"]), 1)
        self.assertEqual(cal["events"][0]["date"], "2026-08-20")

    def test_dedup_merges_sources(self):
        clause = "신규 부지 유치 신청을 9월 10일부터 10월 2일까지 접수한다."
        cal = _build([
            _article(clause, "2026-09-02", hash_="h1"),
            _article(clause, "2026-08-30", hash_="h2"),
        ])
        self.assertEqual(len(cal["events"]), 1)
        ev = cal["events"][0]
        self.assertEqual(ev["source_count"], 2)
        self.assertEqual(ev["first_seen"], "2026-08-30")

    def test_label_carries_no_date_expression(self):
        cal = _build([_article("주민 공청회를 9월 12일 개최한다.", "2026-09-01")])
        label = cal["events"][0]["label"]
        self.assertNotRegex(label, r"\d+월\s*\d+일")

    def test_month_note_not_event(self):
        cal = _build([_article("최종 결과는 9월 중 발표할 예정이다.", "2026-09-01")])
        self.assertEqual(len(cal["events"]), 0)
        self.assertEqual(len(cal["month_notes"]), 1)
        self.assertEqual(cal["month_notes"][0]["month"], "2026-09")

    def test_day_only_start_range(self):
        # "27일부터 9월 10일까지" — 시작일의 달은 발행일에서 가장 가까운 달.
        cal = _build([_article("영덕군이 27일부터 9월 10일까지 명칭 공모를 진행한다.",
                               "2026-08-25")])
        ev = cal["events"][0]
        self.assertEqual(ev["kind"], "range")
        self.assertEqual(ev["date"], "2026-08-27")
        self.assertEqual(ev["end_date"], "2026-09-10")
        self.assertNotIn("27일부터", ev["label"])

    def test_long_range_is_state_not_event(self):
        # 3년 임기·장기 계약은 일정이 아니라 상태 — 달력에 세우지 않는다.
        cal = _build([_article("위원 임기는 9월 10일부터 2029년 9월 9일까지 위촉된다.",
                               "2026-09-01")])
        self.assertEqual(len(cal["events"]), 0)
        self.assertEqual(cal["dropped"]["long_range"], 1)

    def test_similar_labels_merge_to_one_event(self):
        cal = _build([
            _article("영광 한빛원전 2호기가 9월 11일 가동을 중단한다.", "2026-09-01", hash_="h1"),
            _article("한국수력원자력 한빛원전 2호기가 9월 11일 가동을 멈춘다.", "2026-09-02", hash_="h2"),
        ])
        self.assertEqual(len(cal["events"]), 1)
        self.assertEqual(cal["events"][0]["source_count"], 2)

    def test_event_id_stable_and_prefixed(self):
        args = [_article("주민 설명회를 9월 12일 개최한다.", "2026-09-01")]
        first = _build(args)["events"][0]["id"]
        second = _build(args)["events"][0]["id"]
        self.assertEqual(first, second)
        self.assertRegex(first, r"^ev-[0-9a-f]{12}$")

    def test_build_meta(self):
        cal = _build([])
        self.assertEqual(cal["start"], "2026-09-04")
        self.assertEqual(cal["end"], "2026-10-04")
        self.assertEqual(cal["days"], 30)
        self.assertEqual(cal["events"], [])
        self.assertEqual(cal["month_notes"], [])

    def test_issue_and_story_ids_attached(self):
        cal = _build(
            [_article("주민 설명회를 9월 12일 개최한다.", "2026-09-01", hash_="abc")],
            story_ids={"abc": "story-abc"}, issue_ids={"abc": "issue-1"})
        ev = cal["events"][0]
        self.assertEqual(ev["story_id"], "story-abc")
        self.assertEqual(ev["issue_id"], "issue-1")
        self.assertEqual(ev["sources"][0]["story_id"], "story-abc")


if __name__ == "__main__":
    unittest.main()
