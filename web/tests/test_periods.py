"""기간 집계(periods) 계약.

Date contract: 모든 경계는 Asia/Seoul calendar date, inclusive.
정직성 계약: 직전 구간이 archive 에 온전히 없으면 previous_count/delta 는
null — 없는 비교를 지어내지 않는다. 빈 버킷은 0으로 채우지 않는다.
identity 계약: 스토리 정의는 _story_groups 하나뿐 — 카드 롤업(WS1)과 기간
집계가 같은 수를 세는지 여기서 잠근다.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402

KST = build_data.KST


def _member(hash_, title, date, publisher="p", tags=(), topics=(), countries=(),
            summary="s", tier=2):
    return {
        "hash": hash_,
        "title_kr": title,
        "summary": summary,
        "article_date": date,
        "briefing_date": date,
        "publisher": publisher,
        "canonical_tags": list(tags),
        "topics": list(topics),
        "countries": list(countries),
        "source_tier": tier,
    }


def _issue(issue_id, members):
    return {"issue_id": issue_id, "members": members, "evidence_members": []}


def _build(issues, briefing_dates, now):
    return build_data.build_trend_periods(
        issues, build_data.story_id_map(issues), briefing_dates, now)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=KST)


class PeriodAggregationTests(unittest.TestCase):
    def test_clamped_incomplete_period(self):
        issues = [_issue("i1", [_member("a1", "원전 계약 체결", "2026-08-10")])]
        periods = _build(issues, ["2026-07-18", "2026-08-10"], NOW)
        p90 = periods["90"]
        self.assertEqual(p90["requested_start"], "2026-05-22")
        self.assertEqual(p90["start"], "2026-07-18")
        self.assertFalse(p90["complete_period"])
        self.assertEqual(p90["available_days"], 33)
        self.assertEqual(p90["days"], 90)

    def test_complete_period_has_delta_and_new(self):
        issues = [
            _issue("i1", [_member("a1", "가나 원전 협약 서명", "2026-08-18", tags=["smr"])]),
            _issue("i2", [_member("a2", "나미비아 우라늄 증산 발표", "2026-08-08", tags=["smr", "우라늄"])]),
        ]
        periods = _build(issues, ["2026-06-01", "2026-08-18"], NOW)
        p7 = periods["7"]
        self.assertTrue(p7["complete_period"])
        self.assertTrue(p7["previous_period_complete"])
        by_tag = {row["tag"]: row for row in p7["tag_comparison"]}
        self.assertEqual(by_tag["smr"]["previous_count"], 1)
        self.assertEqual(by_tag["smr"]["delta"], 0)
        self.assertFalse(by_tag["smr"]["new"])

    def test_tag_cloud_matches_comparison_contract(self):
        # 워드클라우드 재료 — tag_comparison 과 같은 필드 계약, 상한 40.
        issues = [
            _issue("i1", [_member("a1", "가나 원전 협약 서명", "2026-08-18", tags=["smr"])]),
            _issue("i2", [_member("a2", "나미비아 우라늄 증산 발표", "2026-08-08", tags=["smr", "우라늄"])]),
        ]
        periods = _build(issues, ["2026-06-01", "2026-08-18"], NOW)
        cloud = periods["7"]["tag_cloud"]
        self.assertLessEqual(len(cloud), 40)
        by_tag = {row["tag"]: row for row in cloud}
        self.assertEqual(by_tag["smr"]["count"], 1)
        self.assertEqual(by_tag["smr"]["previous_count"], 1)
        self.assertEqual(by_tag["smr"]["delta"], 0)
        self.assertFalse(by_tag["smr"]["new"])
        comparison = {row["tag"]: row for row in periods["7"]["tag_comparison"]}
        for tag, row in comparison.items():
            self.assertEqual(by_tag[tag], row)

    def test_tag_cloud_null_when_previous_incomplete(self):
        issues = [_issue("i1", [_member("a1", "원전 계약 체결", "2026-08-10", tags=["smr"])])]
        periods = _build(issues, ["2026-07-18"], NOW)
        row = periods["30"]["tag_cloud"][0]
        self.assertIsNone(row["previous_count"])
        self.assertIsNone(row["delta"])
        self.assertFalse(row["new"])

    def test_incomplete_previous_yields_null_not_fabrication(self):
        issues = [_issue("i1", [_member("a1", "원전 계약 체결", "2026-08-10", tags=["smr"])])]
        periods = _build(issues, ["2026-07-18"], NOW)
        p30 = periods["30"]
        self.assertFalse(p30["previous_period_complete"])
        row = p30["tag_comparison"][0]
        self.assertIsNone(row["previous_count"])
        self.assertIsNone(row["delta"])
        self.assertFalse(row["new"])
        self.assertEqual(p30["previous_top_tags"], [])

    def test_empty_buckets_are_omitted(self):
        # 8/1 하루만 기사 → 7일 구간(1일 버킷)의 timeline 에 빈 날이 서지 않는다.
        issues = [_issue("i1", [_member("a1", "원전 계약 체결", "2026-08-13")])]
        periods = _build(issues, ["2026-07-01", "2026-08-13"], NOW)
        timeline = periods["7"]["timeline"]
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["start"], "2026-08-13")
        self.assertEqual(timeline[0]["story_count"], 1)

    def test_utc_datetime_folds_into_kst_day(self):
        # 2026-08-18 16:30 UTC = 2026-08-19 01:30 KST → 8/19 로 세야 한다.
        member = _member("a1", "원전 계약 체결", "2026-08-18T16:30:00+00:00")
        issues = [_issue("i1", [member])]
        periods = _build(issues, ["2026-07-01"], NOW)
        timeline = periods["7"]["timeline"]
        self.assertEqual(timeline[-1]["start"], "2026-08-19")

    def test_period_story_count_matches_card_rollup(self):
        # 같은 사건 3매체 보도 → 카드 롤업도 1스토리, 기간 집계도 1스토리.
        members = [
            _member("a1", "한수원, 체코 신규 원전 계약 체결", "2026-08-18", publisher="p1"),
            _member("a2", "한수원 체코 신규 원전 계약 체결 확정", "2026-08-18", publisher="p2"),
            _member("a3", "체코 신규 원전, 한수원과 계약 체결", "2026-08-19", publisher="p3"),
        ]
        issues = [_issue("i1", members)]
        card_count, card_outlets = build_data.story_rollup(members, members[0])
        periods = _build(issues, ["2026-07-01"], NOW)
        p7 = periods["7"]
        self.assertEqual(card_count, 3)
        self.assertEqual(p7["story_count"], 1)
        self.assertEqual(p7["multi_source_story_count"], 1)
        self.assertEqual(p7["average_outlets"], card_outlets)

    def test_no_briefings_returns_empty(self):
        self.assertEqual(_build([], [], NOW), {})

    def test_official_and_contract_counts(self):
        official = _member("a1", "원안위, 계속운전 심사 결과 의결", "2026-08-18", tier=1)
        official["evidence_role"] = "primary"
        bare = _member("a2", "업계 소식 단신", "2026-08-17", publisher="", summary="")
        issues = [_issue("i1", [official]), _issue("i2", [bare])]
        p7 = _build(issues, ["2026-07-01"], NOW)["7"]
        self.assertEqual(p7["story_count"], 2)
        self.assertEqual(p7["official_story_count"], 1)
        self.assertEqual(p7["tier1_story_count"], 1)
        self.assertEqual(p7["story_contract_count"], 1)
        self.assertEqual(p7["story_contract_coverage"], 0.5)


class WeeklyReportsDictTests(unittest.TestCase):
    def test_week_id_is_week_start_saturday(self):
        reports = build_data.load_weekly_reports([])
        for week_start, report in reports.items():
            self.assertEqual(week_start, report.get("week_start"))
            parsed = datetime.strptime(week_start, "%Y-%m-%d")
            self.assertEqual(parsed.weekday(), 5, "week_start 는 토요일이어야 한다")

    def test_single_report_is_latest_of_dict(self):
        reports = build_data.load_weekly_reports([])
        single = build_data.load_weekly_report([])
        if reports:
            self.assertEqual(single, reports[max(reports)])
        else:
            self.assertIsNone(single)


class GeneratedPeriodsSchemaTests(unittest.TestCase):
    """실제 빌드 산출물의 periods·story 스키마 잠금 (GeneratedDataTests 와 같은
    계약 — 빌드가 선행돼야 한다)."""

    REQUIRED_PERIOD_FIELDS = {
        "unit", "days", "requested_start", "start", "end", "available_days",
        "complete_period", "archive_first_briefing_date", "briefing_day_count",
        "story_count", "multi_source_story_count", "official_story_count",
        "tier1_story_count", "average_outlets", "story_contract_count",
        "story_contract_coverage", "publishers", "countries", "top_tags",
        "top_topics", "previous_period_complete", "previous_top_tags",
        "tag_comparison", "timeline",
    }

    @classmethod
    def setUpClass(cls):
        import json
        data_dir = ROOT / "public" / "data"
        cls.trend = json.loads((data_dir / "trend.json").read_text(encoding="utf-8"))
        cls.issues = json.loads((data_dir / "issues.json").read_text(encoding="utf-8"))

    def test_periods_has_five_windows_with_full_fields(self):
        periods = self.trend.get("periods")
        self.assertEqual(set(periods), {"7", "30", "90", "180", "365"})
        for key, period in periods.items():
            missing = self.REQUIRED_PERIOD_FIELDS - set(period)
            self.assertFalse(missing, f"periods[{key}] 누락 필드: {missing}")
            self.assertEqual(period["unit"], "briefing_story")
            self.assertGreaterEqual(period["story_count"], period["multi_source_story_count"])
            if period["requested_start"] < period["archive_first_briefing_date"]:
                self.assertFalse(period["complete_period"])
            if not period["previous_period_complete"]:
                for row in period["tag_comparison"]:
                    self.assertIsNone(row["previous_count"])
                    self.assertIsNone(row["delta"])

    def test_weekly_reports_is_dict_and_contains_latest(self):
        reports = self.trend.get("weekly_reports")
        self.assertIsInstance(reports, dict)
        if reports:
            self.assertEqual(self.trend.get("weekly_report"), reports[max(reports)])

    def test_issue_rows_carry_story_rollup(self):
        rows = self.issues if isinstance(self.issues, list) else self.issues.get("issues", [])
        for row in rows:
            self.assertIsInstance(row.get("story_article_count"), int)
            self.assertIsInstance(row.get("story_outlet_count"), int)
            self.assertLessEqual(row["story_outlet_count"], row["story_article_count"])
            self.assertLessEqual(row["story_article_count"], row["article_count"])


if __name__ == "__main__":
    unittest.main()
