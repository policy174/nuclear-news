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

    def test_no_candidate_still_says_why_for_every_rank(self):
        """2026-09-21: 상위 3건이 전부 빠졌는데 로그에는 이유가 없었다.

        1위는 그날 새 id 로 조폐되어 thread_id 가 빈칸, 2위는 원래 스레드 없음,
        3위는 자격 미달 — 세 가지가 다 다른 원인인데 밖으로는 같은 None 이었다.
        """
        thread = sar_thread()
        thread["flow"][0]["relation_to_next"] = "same_matter"
        data = card_context.SiteData(date="2026-09-21", issues=[], source="today",
                                     generation_id="g", threads=threads(thread))
        top = [issue("story-fc64f50e257b3a0a", "정부, 2000억 달러 규모 대미투자 협상"),
               issue("story-5b2aa08eeeb78c3e", "북한, IAEA 결의안 거부", thread_id="thread-ghost"),
               issue(SAR_SEP, SAR_SEP_TITLE, thread_id=SAR_THREAD)]
        reasons: list[str] = []
        self.assertIsNone(card_context.pick_story_candidate(data, top, reasons))
        self.assertEqual(len(reasons), 3)
        self.assertIn("#1 정부, 2000억 달러 규모 대미투자 → 스레드 없음", reasons[0])
        self.assertIn("issue_id=story-fc64f50e257b3a0a", reasons[0])
        self.assertIn("thread_id=빈칸", reasons[0])
        self.assertIn("thread_id=thread-ghost — 원장에 없는 스레드", reasons[1])
        self.assertIn(f"#3 {SAR_SEP_TITLE[:20]} → {SAR_THREAD}: 진행 관계 없음", reasons[2])

    def test_reasons_are_optional_and_a_found_candidate_leaves_them_short(self):
        top = [issue("story-1", "스토리 없는 1위"),
               issue(SAR_SEP, SAR_SEP_TITLE, thread_id=SAR_THREAD)]
        reasons: list[str] = []
        found = card_context.pick_story_candidate(self._data(top), top, reasons)
        self.assertIsNotNone(found)
        self.assertEqual(len(reasons), 1)  # 1위가 왜 아닌지만 남는다
        self.assertIsNotNone(card_context.pick_story_candidate(self._data(top), top))

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


# 2026-09-26 대미 전략투자 스레드(thread-69334fcff32b6984). 원장의 제목·날짜·인접
# 관계를 그대로 옮겼다(08-17·08-18 같은 제목 두 건은 flow 가 한 칸으로 접는다).
US_INVEST_FLOW = [
    ("story-a", "2026-08-17", "정부, 2000억 달러 규모 대미 전략투자 첫 사업 막판 조율", "stage_progress"),
    ("story-b", "2026-08-30", "한미 원전 노형 배분 등 이견으로 대미 투자 MOU 서명 연기", ""),
    ("story-c", "2026-09-11", "미국, 한국의 대미 투자 지연에 불만…일본과 비교하며 속도 압박", ""),
    ("story-d", "2026-09-12", "한-미, 대미 에너지 투자 협상 막판 조율", ""),
    ("story-e", "2026-09-25", "한미 투자 패키지 중 APR1400 미국 도입 합의 지연", "cause_effect"),
    ("story-f", "2026-09-25", "정부, 대미 전략투자 연간 200억 달러 제한 재확인", "same_matter"),
    ("story-g", "2026-09-26", "정부, 대미 전략투자 첫 사업으로 텍사스 가스복합발전소 건설 확정", ""),
]


def us_invest_rows():
    return [_flow_row(sid, title, date, rel, [sid]) for sid, date, title, rel in US_INVEST_FLOW]


class TimelineSelectionTests(unittest.TestCase):
    """7~8건 중 타임라인 4칸을 **코드가** 고른다 — 모델에게 맡기면 기준이 날마다 다르다."""

    def test_the_real_thread_keeps_the_turning_points(self):
        rows = us_invest_rows()
        picked = [rows[i]["source_event_id"] for i in card_context.select_timeline(rows, 4)]
        # 모델은 09-25 APR1400 지연(원인→결과 전환점)을 빼고 관계 판정도 없는
        # 09-11 속도 압박을 넣었다. 기준으로 고르면 흐름이 선다.
        self.assertEqual(picked, ["story-a", "story-b", "story-e", "story-g"])

    def test_the_first_and_today_are_always_kept(self):
        rows = us_invest_rows()
        for limit in (2, 3, 4):
            picked = card_context.select_timeline(rows, limit)
            self.assertEqual(len(picked), limit)
            self.assertEqual(picked[0], 0)
            self.assertEqual(picked[-1], len(rows) - 1)

    def test_fewer_events_than_slots_keeps_all(self):
        rows = us_invest_rows()[:3]
        self.assertEqual(card_context.select_timeline(rows, 4), [0, 1, 2])

    def test_a_restatement_loses_to_the_step_it_repeats(self):
        rows = us_invest_rows()
        score_f, why_f = card_context.timeline_score(rows, 5)
        score_e, _ = card_context.timeline_score(rows, 4)
        self.assertIn("같은사안", why_f)
        self.assertLess(score_f, score_e)

    def test_khnp_relevance_breaks_a_tie_between_unjudged_rows(self):
        rows = [_flow_row(f"s{i}", title, f"2026-09-{10 + i:02d}", "", [f"h{i}"])
                for i, title in enumerate(["출발", "미국 투자 압박 발언",
                                           "원전 노형 배분 협의 착수", "재정 당국 입장 발표",
                                           "오늘 사건"])]
        picked = card_context.select_timeline(rows, 3)
        self.assertEqual([rows[i]["source_event_id"] for i in picked], ["s0", "s2", "s4"])

    def test_the_packet_carries_only_the_picked_events_and_the_rest_as_background(self):
        rows = us_invest_rows()
        thread = sar_thread(flow=rows)
        candidate = card_context.StoryCandidate(issue=issue("story-g", "텍사스"), thread=thread,
                                                rank=1, events=rows)
        packet = card_context.evidence_packet(candidate, "2026-09-26", topic="정책")
        self.assertEqual([e["source_event_id"] for e in packet["events"]],
                         ["story-a", "story-b", "story-e", "story-g"])
        self.assertEqual([b["date"] for b in packet["background"]],
                         ["2026-09-11", "2026-09-12", "2026-09-25"])
        # 관계는 타임라인에서도 이웃일 때만 — 08-17 → 08-30 은 원래 이웃이라 남고,
        # 09-25 APR1400 → 09-26 은 사이(200억 제한)를 건너뛰었으므로 비운다.
        relations = [e["relation_to_next"] for e in packet["events"]]
        self.assertEqual(relations, ["stage_progress", "", "", ""])


class NarratorPickTests(unittest.TestCase):
    """오늘 사건은 코드가 못 박고, 나머지 칸은 편집 데스크가 고른다. 틀리면 코드 선택."""

    def packet(self):
        rows = us_invest_rows()
        thread = sar_thread(flow=rows)
        candidate = card_context.StoryCandidate(issue=issue("story-g", "텍사스"), thread=thread,
                                                rank=1, events=rows)
        return card_context.evidence_packet(candidate, "2026-09-26", topic="정책")

    def test_every_event_is_a_numbered_candidate(self):
        packet = self.packet()
        self.assertEqual([c["n"] for c in packet["candidates"]], list(range(1, 8)))
        self.assertEqual(packet["code_pick"], [0, 1, 4, 6])

    def test_a_valid_model_pick_is_used_and_today_is_always_last(self):
        packet = self.packet()
        line = card_context.choose_timeline(packet, [1, 2, 5])
        self.assertIn("사용: 모델", line)
        self.assertEqual([e["source_event_id"] for e in packet["events"]],
                         ["story-a", "story-b", "story-e", "story-g"])
        self.assertEqual(packet["timeline_pick"]["code"], [1, 2, 5, 7])

    def test_the_model_may_or_may_not_list_today(self):
        for pick in ([1, 3, 5], [1, 3, 5, 7]):
            with self.subTest(pick=pick):
                self.assertEqual(card_context.check_model_pick(pick, 7)[0], [0, 2, 4, 6])

    def test_a_broken_pick_falls_back_to_the_code_pick(self):
        for pick in (None, "1,2,3", [1, 2], [1, 1, 3], [0, 2, 3], [1, 2, 9], [True, 2, 3]):
            with self.subTest(pick=pick):
                packet = self.packet()
                line = card_context.choose_timeline(packet, pick)
                self.assertIn("사용: 코드", line)
                self.assertEqual(packet["timeline_pick"]["final"], [1, 2, 5, 7])
                self.assertEqual(packet["events"][-1]["source_event_id"], "story-g")

    def test_a_short_story_takes_every_event(self):
        rows = us_invest_rows()[:3]
        packet = card_context.evidence_packet(
            card_context.StoryCandidate(issue=issue("x", "x"), thread=sar_thread(flow=rows),
                                        rank=1, events=rows), "2026-09-26")
        card_context.choose_timeline(packet, [])
        self.assertEqual(len(packet["events"]), 3)

    def test_a_distant_repeat_is_marked_and_scored_down(self):
        """09-23: 08-11 과 08-20 이 같은 제목인데 인접 관계로는 안 잡혔다."""
        rows = [_flow_row(f"s{i}", title, f"2026-08-{10 + i:02d}", "", [f"h{i}"])
                for i, title in enumerate(["공론화 방침", "신규 원전 공론화 결정", "전기본 토론회",
                                           "신규 원전 공론화 결정", "전기본 반영 논의"])]
        packet = card_context.evidence_packet(
            card_context.StoryCandidate(issue=issue("x", "x"), thread=sar_thread(flow=rows),
                                        rank=1, events=rows), "2026-09-26")
        self.assertEqual(packet["candidates"][3].get("repeats"), 2)
        first, _ = card_context.timeline_score(rows, 1)
        again, why = card_context.timeline_score(rows, 3)
        self.assertIn("되풀이", why)
        # 같은 제목의 뒤 사건은 앞 사건보다 되풀이 감점만큼 낮다(직전 가점은 따로 더한다).
        self.assertEqual(again, first + card_context.LEAD_IN_POINTS - card_context.RESTATED_PENALTY)


if __name__ == "__main__":
    unittest.main()
