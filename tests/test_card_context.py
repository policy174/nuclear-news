"""카드의 재료와 스토리 신원 — `card_context`.

이 파일이 지키는 것은 셋이다.

**① 신원은 id 다.** 예전 `story_cards.pick_story` 는 이슈 제목과 thread 이벤트
제목의 exact match 로 이었다. 표시 제목은 움직이는 값이라(카드 제목은 최신 사건
제목을 걸고, 이슈 쪽은 `headline_display` 가 따로 있다) 그 열쇠는 조용히 빗나간다.

**② 자격은 사건 수가 아니라 관계다.** `MIN_EVENTS = 3` 은 "타임라인이 안 선다"는
렌더 사정에서 나온 숫자였다. 발표 → 시행은 두 칸으로도 이야기고, 같은 사안을
다섯 번 되풀이한 다섯 칸은 이야기가 아니다.

**③ 근거는 자기 사건 것만.** 한 행이 다른 사건의 기사를 들고 서면 안 된다.

라이브 실측 픽스처 (2026-09-20)
-------------------------------
`thread-5aaa7dea33e702ac` — SAR 시범사업. 사건 2건, 인접 관계 `stage_progress`.

    issue-099ce0d43f46036a  2026-08-24  기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용
    story-9e227f9daff25cd5  2026-09-19  기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행

**두 날짜 모두 첫 등장일이다.** 기사 보도일은 8/23 과 9/18 로 따로 있다. 한
타임라인에 8/23 → 9/19 를 세우면 두 종류의 날짜가 한 축에 섞이므로, 이 픽스처는
원장이 말하는 8/24 → 9/19 를 쓴다.

그리고 그날 SAR 은 **상위 3건이 아니었다.** 그래서 후보 선택 E2E 는 합성 top3 를
쓴다 — 실데이터로 "상위 3건에서 골랐다"를 주장하면 픽스처가 거짓말이 된다.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import card_context  # noqa: E402

SAR_AUG = "issue-099ce0d43f46036a"
SAR_SEP = "story-9e227f9daff25cd5"
SAR_THREAD = "thread-5aaa7dea33e702ac"
SAR_AUG_TITLE = "기후부, 전력망 효율 위해 9월부터 '계절별 송전용량' 시범 적용"
SAR_SEP_TITLE = "기후부, 전북 진안·충남 금산서 계절별 송전용량(SAR) 시범사업 시행"


def _flow_row(source_id, title, date, relation, hashes):
    return {"event_id": source_id, "source_event_id": source_id,
            "source_event_ids": [source_id], "evidence_hashes": list(hashes),
            "title": title, "date": date, "date_kind": "first_seen",
            "relation_to_next": relation, "relation_label": ""}


def sar_thread(**overrides) -> dict:
    thread = {
        "thread_id": SAR_THREAD, "title": SAR_SEP_TITLE,
        "first_seen": "2026-08-24", "last_seen": "2026-09-19",
        "lifespan_days": 26, "event_count": 2, "briefing_count": 4,
        "flow": [
            _flow_row(SAR_AUG, SAR_AUG_TITLE, "2026-08-24", "stage_progress",
                      ["099ce0d43f46036a", "eead02e2334a6e47"]),
            _flow_row(SAR_SEP, SAR_SEP_TITLE, "2026-09-19", "",
                      ["9e227f9daff25cd5", "9a94d3be8028be9e"]),
        ],
    }
    thread.update(overrides)
    return thread


def threads(*rows, **overrides) -> dict:
    payload = {"version": "thread-web-v2", "date_kind": "first_seen",
               "visible": True, "degraded": False, "status": "ok",
               "build": {"status": "ok"}, "threads": list(rows) or [sar_thread()]}
    payload.update(overrides)
    return payload


def issue(issue_id, title, *, thread_id="", url="https://example.test/a"):
    return {"issue_id": issue_id, "title": title, "thread_id": thread_id,
            "summary": "요약", "representative_article": {"url": url, "hash": "h"},
            "topics": ["power_market"], "tags": []}


class EligibilityTests(unittest.TestCase):
    def test_two_events_with_stage_progress_are_a_story(self):
        """SAR 회귀 — 이 한 줄이 `MIN_EVENTS = 3` 을 대신한다."""
        ok, reason = card_context.eligibility(sar_thread())
        self.assertTrue(ok, reason)
        self.assertIn("stage_progress", reason)

    def test_the_sar_timeline_keeps_one_calendar_and_one_order(self):
        thread = sar_thread()
        self.assertEqual([row["date"] for row in thread["flow"]],
                         ["2026-08-24", "2026-09-19"])
        self.assertTrue(all(row["date_kind"] == "first_seen" for row in thread["flow"]))

    def test_two_events_of_the_same_matter_are_not_a_story(self):
        """같은 사안이라는 말은 단계가 넘어갔다는 말이 아니다."""
        thread = sar_thread()
        thread["flow"][0]["relation_to_next"] = "same_matter"
        ok, reason = card_context.eligibility(thread)
        self.assertFalse(ok)
        self.assertIn("same_matter", reason)

    def test_two_events_with_no_judged_relation_are_not_a_story(self):
        thread = sar_thread()
        thread["flow"][0]["relation_to_next"] = ""
        ok, reason = card_context.eligibility(thread)
        self.assertFalse(ok)
        self.assertIn("빈칸", reason)

    def test_cause_effect_also_counts_as_progression(self):
        thread = sar_thread()
        thread["flow"][0]["relation_to_next"] = "cause_effect"
        self.assertTrue(card_context.eligibility(thread)[0])

    def test_three_restatements_are_not_a_story_either(self):
        """사건 수를 늘린다고 이야기가 되지 않는다 — 옛 기준이 통과시키던 모양."""
        thread = sar_thread(flow=[
            _flow_row("e1", "같은 얘기", "2026-08-24", "same_matter", ["h1"]),
            _flow_row("e2", "같은 얘기 2보", "2026-08-30", "same_matter", ["h2"]),
            _flow_row("e3", "같은 얘기 3보", "2026-09-05", "", ["h3"]),
        ])
        self.assertFalse(card_context.eligibility(thread)[0])

    def test_three_events_pass_when_one_step_moved(self):
        thread = sar_thread(flow=[
            _flow_row("e1", "신청", "2026-08-24", "same_matter", ["h1"]),
            _flow_row("e2", "재신청", "2026-08-30", "stage_progress", ["h2"]),
            _flow_row("e3", "심사 착수", "2026-09-05", "", ["h3"]),
        ])
        self.assertTrue(card_context.eligibility(thread)[0])

    def test_an_event_without_evidence_blocks_the_story(self):
        """근거 없는 행은 출처 없이 날짜와 제목만 주장하는 문장이 된다."""
        thread = sar_thread()
        thread["flow"][1]["evidence_hashes"] = []
        ok, reason = card_context.eligibility(thread)
        self.assertFalse(ok)
        self.assertIn("근거 없는", reason)

    def test_a_single_event_is_not_a_flow(self):
        thread = sar_thread(flow=[_flow_row("e1", "혼자", "2026-09-01", "", ["h1"])])
        self.assertFalse(card_context.eligibility(thread)[0])


class ThreadGateTests(unittest.TestCase):
    def test_a_hidden_payload_blocks_the_story(self):
        self.assertIn("숨김", card_context.story_blocked(threads(visible=False)))

    def test_a_v1_payload_blocks_the_story(self):
        """v1 에는 사건 신원이 없어 근거를 라우트 id 로 묶어야 한다 — 금지다."""
        self.assertIn("thread-web-v2",
                      card_context.story_blocked(threads(version="thread-web-v1")))

    def test_degraded_is_a_warning_not_a_gate(self):
        """**여기가 결정이 실린 자리다.**

        `degraded` 는 "이번 판정 회차가 온전하지 않았다"는 관측값이지 "이
        스레드의 관계가 틀렸다"는 말이 아니다. 라이브 실측 2026-09-20 이
        `visible=true · degraded=true`(build.status=no_api_key)였는데, 이걸
        hard gate 로 쓰면 그날 스토리가 0 이 된다. 게다가 그 상태는 손으로
        커밋한 로컬 빌드가 CI 원장을 덮었을 때도 나므로 고장과 구분되지 않는다.
        """
        payload = threads(degraded=True, build={"status": "no_api_key",
                                                "asked": 0, "failed": 3})
        self.assertEqual(card_context.story_blocked(payload), "")
        warnings = card_context.thread_warnings(payload)
        self.assertTrue(warnings)
        self.assertIn("no_api_key", warnings[0])

    def test_duplicate_thread_ids_are_refused_not_silently_first_wins(self):
        data = card_context.SiteData(
            date="2026-09-20", issues=[], source="today", generation_id="g",
            threads=threads(sar_thread(), sar_thread()))
        with self.assertRaises(card_context.ContextError):
            data.thread_index()


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.index = {SAR_THREAD: sar_thread()}

    def test_the_thread_id_slot_is_the_normal_path(self):
        row = issue(SAR_SEP, SAR_SEP_TITLE, thread_id=SAR_THREAD)
        self.assertIs(card_context.resolve_thread(row, self.index), self.index[SAR_THREAD])

    def test_a_matching_title_alone_never_connects(self):
        """제목이 같아도 id 칸이 비어 있고 사건 id 가 다르면 이어지지 않는다.

        예전 경로가 정확히 이 모양으로 이었다. 제목이 열쇠면 표시 제목이 바뀌는
        날 연결이 끊기고, 서로 다른 사건이 같은 제목을 쓰는 날 잘못 이어진다.
        """
        row = issue("story-전혀다른id", SAR_SEP_TITLE)
        self.assertIsNone(card_context.resolve_thread(row, self.index))

    def test_the_legacy_fallback_is_an_exact_source_event_id_match(self):
        """`thread_id` 칸이 없는 과거 briefings 행을 위한 유일한 폴백."""
        row = issue(SAR_AUG, "그날의 다른 제목")
        self.assertIs(card_context.resolve_thread(row, self.index), self.index[SAR_THREAD])

    def test_a_route_id_that_is_not_a_source_id_does_not_connect(self):
        row = issue("issue-흡수한쪽", SAR_SEP_TITLE)
        self.assertIsNone(card_context.resolve_thread(row, self.index))


class CandidateTests(unittest.TestCase):
    """후보는 **일일 카드 대상 그 목록 안에서** 순서대로 고른다."""

    def _data(self, issues):
        return card_context.SiteData(date="2026-09-20", issues=issues, source="today",
                                     generation_id="g", threads=threads())

    def test_the_first_eligible_issue_in_the_top_three_wins(self):
        top = [issue("story-1", "스토리 없는 1위"),
               issue(SAR_SEP, SAR_SEP_TITLE, thread_id=SAR_THREAD),
               issue("story-3", "스토리 없는 3위")]
        found = card_context.pick_story_candidate(self._data(top), top)
        self.assertIsNotNone(found)
        self.assertEqual(found.thread_id, SAR_THREAD)
        self.assertEqual(found.rank, 2)
        self.assertEqual([row["date"] for row in found.events],
                         ["2026-08-24", "2026-09-19"])

    def test_a_story_outside_the_top_three_is_not_reached(self):
        """SAR 은 2026-09-20 실데이터에서 상위 3건이 아니었다."""
        top = [issue(f"story-{i}", f"오늘 {i}위") for i in range(1, 4)]
        self.assertIsNone(card_context.pick_story_candidate(self._data(top), top))

    def test_an_ineligible_thread_is_reported_not_silently_skipped(self):
        thread = sar_thread()
        thread["flow"][0]["relation_to_next"] = "same_matter"
        data = card_context.SiteData(date="2026-09-20", issues=[], source="today",
                                     generation_id="g", threads=threads(thread))
        top = [issue(SAR_SEP, SAR_SEP_TITLE, thread_id=SAR_THREAD)]
        self.assertIsNone(card_context.pick_story_candidate(data, top))

    def test_a_hidden_payload_raises_so_the_caller_can_split_domains(self):
        data = card_context.SiteData(date="2026-09-20", issues=[], source="today",
                                     generation_id="g", threads=threads(visible=False))
        with self.assertRaises(card_context.ContextError):
            card_context.pick_story_candidate(data, [])


class LoadTests(unittest.TestCase):
    """정상 당일은 today.json, 과거 재생성만 briefings.json."""

    def _dir(self, stack, *, today=True, briefings=True):
        tmp = Path(stack.enter_context(TemporaryDirectory()))
        (tmp / "manifest.json").write_text(json.dumps({"generation_id": "20260920T0000Z"}),
                                           encoding="utf-8")
        (tmp / "threads.json").write_text(json.dumps(threads()), encoding="utf-8")
        if today:
            (tmp / "today.json").write_text(json.dumps(
                {"date": "2026-09-20", "issues": [issue(SAR_SEP, SAR_SEP_TITLE,
                                                        thread_id=SAR_THREAD)]}),
                encoding="utf-8")
        if briefings:
            (tmp / "briefings.json").write_text(json.dumps(
                [{"date": "2026-09-20", "issues": [issue(SAR_SEP, SAR_SEP_TITLE)]}]),
                encoding="utf-8")
        return tmp

    def test_today_json_is_preferred_because_only_it_carries_thread_id(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            tmp = self._dir(stack)
            data = card_context.load_site_data("2026-09-20", tmp)
        self.assertEqual(data.source, "today")
        self.assertEqual(data.issues[0]["thread_id"], SAR_THREAD)
        self.assertEqual(data.generation_id, "20260920T0000Z")

    def test_a_past_date_falls_back_to_briefings_and_says_so(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            tmp = self._dir(stack)
            data = card_context.load_site_data("2026-09-20", tmp)
            self.assertEqual(data.source, "today")
            (tmp / "today.json").unlink()
            past = card_context.load_site_data("2026-09-20", tmp)
        self.assertEqual(past.source, "briefings")
        self.assertTrue(past.warnings)
        # 그 행에는 칸이 없다 — 위 legacy 폴백이 그래서 존재한다.
        self.assertEqual(past.issues[0]["thread_id"], "")

    def test_a_missing_day_is_an_error_not_an_empty_card(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            tmp = self._dir(stack)
            with self.assertRaises(card_context.ContextError):
                card_context.load_site_data("2026-01-01", tmp)


if __name__ == "__main__":
    unittest.main()
