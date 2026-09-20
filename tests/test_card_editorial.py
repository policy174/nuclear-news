# -*- coding: utf-8 -*-
"""카드 편집 계층의 **호출 계약** — `card_editorial` · `make_cards.run_editorial`.

여기서 지키는 것은 셋이다.

**① 호출 수.** 스토리 없는 날 1회, 있는 날 2회, QA 실패 시 +1. 스토리가 없는
날까지 편집 데스크를 따로 불러 2회로 늘리지 않는다.

**② 논리 호출 ≠ HTTP 요청.** 예전에는 바깥 `for attempt in (1, 2)` 루프와
`call_json(retries=3)` 이 곱해져 "1회" 가 최악 8번의 HTTP 요청이었고, 그 수가
로그 어디에도 없었다. 쿼터를 재려면 두 수가 다 필요하다.

**③ 실패 도메인 분리.** 스토리가 깨져도 일일 카드는 그대로 나간다. Narrator 가
죽으면 스토리만 빠지고 일일은 단독 호출로 살아난다.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import card_editorial  # noqa: E402
import llm_policy  # noqa: E402
import make_cards  # noqa: E402

ITEMS = [
    {"issue_id": "issue-a", "title": "기후부, SAR 시범사업 시행",
     "summary": "진안과 금산에서 1년간 시행한다", "detail": "", "why_important": "",
     "implication": "", "open_question": "", "sensitive": False, "body": "",
     "event_date": "", "source": "전기신문", "topic": "전력시장·요금", "hash": "h1",
     "link": "https://x.test/1", "importance": "must_know", "tag": ""},
]


def brief(issue_ids=("issue-a",), story_thread=None) -> dict:
    out = {
        "schema_version": card_editorial.BRIEF_SCHEMA_VERSION,
        "issues": [{
            "issue_id": issue_id,
            "core_change": "계획 단계였던 SAR 이 현장 실증으로 넘어갔다",
            "headline_angle": "계획 넘어 현장",
            "must_know_facts": ["진안·금산에서 착수", "실증기간 1년"],
            "so_what": [{"type": "policy_effect", "text": "계절 기준으로 전환"}],
            "watchpoint": "실증 결과",
            "avoid_repeating": ["시범사업 착수"],
        } for issue_id in issue_ids],
        "story": None,
    }
    if story_thread:
        out["story"] = {"thread_id": story_thread, "story_subject": "SAR",
                        "key_change": "계획 → 시행",
                        "so_what": ["계절 기준으로 전환"]}
    return out


def copy(issue_ids=("issue-a",), with_story=False) -> dict:
    out = {"daily": {
        "hook": {"headline": "오늘 먼저 볼 현안"},
        "steps": [{"issue_id": issue_id, "headline": "SAR, 계획 넘어 현장",
                   "facts": ["진안·금산에서 착수", "실증기간 1년"],
                   "why": ["송전용량이 계절 기준으로 전환", "실증 결과로 확대 여부 판단"]}
                  for issue_id in issue_ids]}, "story": None}
    if with_story:
        out["story"] = {"cover": {"headline": "SAR"}}
    return out


class ModelPolicyTests(unittest.TestCase):
    """**프로필을 등록하는 데 그치지 않고 실제 호출에 연결한다.**

    예전 카드 호출은 `model=` 을 안 넘겨 `gemini_client.MODEL` 전역에 기댔고,
    스토리는 워크플로 env 로 `gemini-3-flash-preview` 를 박았다. "이 카드는
    어느 모델이 썼는가" 가 코드가 아니라 환경에 흩어져 있었다.
    """

    def test_all_four_card_profiles_are_registered(self):
        for task in ("card_editorial_narrator", "card_writer",
                     "card_daily_writer", "card_writer_repair"):
            with self.subTest(task=task):
                self.assertTrue(llm_policy.profile(task).model())

    def test_the_narrator_thinks_one_tier_above_the_writer(self):
        """무엇을 말할지 고르는 일과 그것을 적는 일은 다른 과제다.

        모델 **이름**은 못 박지 않는다. 이 저장소로 들여올 때 v2 의 기본값
        (narrator=gemini-3.5-flash-lite / writer=gemini-3.1-flash-lite)을
        그대로 쓰지 않기로 했다 — 이 키에서 3.5-flash-lite 는 400 을 돌려준
        전력이 있다(카드 함정 목록). 모델 교체는 카드 이식과 별개 결정이라
        `gemini_client.synthesis_model()` 이 이 저장소의 MODEL 로 되돌린다.

        그래서 여기서 잠글 것은 이름이 아니라 **관계**다: 편집 데스크와
        카피라이터가 같은 자리를 쓰지 않는다는 것, 둘 다 해석된다는 것.
        """
        narrator = llm_policy.profile("card_editorial_narrator").model()
        writer = llm_policy.profile("card_writer").model()
        self.assertTrue(narrator, "narrator 모델이 해석되지 않는다")
        self.assertTrue(writer, "writer 모델이 해석되지 않는다")
        self.assertNotEqual(narrator, writer,
                            "편집 판단과 받아쓰기가 같은 자리를 쓴다")

    def test_the_preview_model_default_is_gone(self):
        snapshot = llm_policy.production_policy_snapshot()
        for name, row in snapshot.items():
            if name.startswith("card_"):
                with self.subTest(task=name):
                    self.assertNotIn("preview", row["model"])

    def test_the_call_actually_passes_the_profile_model(self):
        with mock.patch("gemini_client.call_json", return_value={}) as called:
            card_editorial.call("card_editorial_narrator", "sys", {"a": 1})
        # 이름을 못 박지 않는 이유는 위 test_the_narrator_thinks_one_tier_above_the_writer
        # 참고. 잠글 것은 "정책이 정한 모델이 실제 호출까지 간다"는 사실이다.
        self.assertEqual(called.call_args.kwargs["model"],
                         llm_policy.profile("card_editorial_narrator").model())
        self.assertEqual(called.call_args.kwargs["label"], "cards:card_editorial_narrator")

    def test_transport_retries_are_explicit_not_the_library_default(self):
        """논리 1회가 몇 번의 HTTP 요청이 되는지를 코드가 정한다."""
        with mock.patch("gemini_client.call_json", return_value={}) as called:
            card_editorial.call("card_writer", "sys", {})
        self.assertEqual(called.call_args.kwargs["retries"], card_editorial.CARD_LLM_RETRIES)
        self.assertLessEqual(card_editorial.CARD_LLM_RETRIES, 1)


class BriefContractTests(unittest.TestCase):
    """브리프는 문자열 모음이 아니라 계약이다. **판정은 세 갈래다.**"""

    def check(self, brief_obj, items=None, thread_id=None):
        card_editorial.normalize_brief(brief_obj)
        return card_editorial.validate_brief(brief_obj, items or ITEMS, thread_id)

    def test_a_good_brief_passes(self):
        self.assertEqual(self.check(brief()), ([], [], []))

    def test_a_missing_issue_is_fatal_for_the_daily_card(self):
        fatal, story_bad, _ = self.check(brief(issue_ids=()))
        self.assertTrue(any("issue_id" in p for p in fatal))
        self.assertEqual(story_bad, [])

    def test_a_reordered_brief_is_fatal(self):
        """순서가 틀리면 Writer 가 A 이슈의 판단으로 B 카드를 쓴다."""
        items = ITEMS + [dict(ITEMS[0], issue_id="issue-b")]
        fatal, _, _ = self.check(brief(issue_ids=("issue-b", "issue-a")), items)
        self.assertTrue(any("issue_id" in p for p in fatal))

    def test_missing_facts_are_fatal_because_the_writer_has_nothing_else(self):
        payload = brief()
        payload["issues"][0]["must_know_facts"] = []
        fatal, _, _ = self.check(payload)
        self.assertTrue(any("must_know_facts" in p for p in fatal))

    def test_a_story_for_a_thread_we_did_not_ask_about_drops_only_the_story(self):
        fatal, story_bad, _ = self.check(
            brief(story_thread="thread-다른것"), thread_id="thread-우리것")
        self.assertEqual(fatal, [])
        self.assertTrue(any("thread_id" in p for p in story_bad))

    def test_a_story_invented_without_a_candidate_drops_only_the_story(self):
        fatal, story_bad, _ = self.check(brief(story_thread="thread-x"))
        self.assertEqual(fatal, [])
        self.assertTrue(any("후보가 없는데" in p for p in story_bad))

    def test_a_missing_story_when_we_had_a_candidate_drops_only_the_story(self):
        fatal, story_bad, _ = self.check(brief(), thread_id="thread-우리것")
        self.assertEqual(fatal, [])
        self.assertTrue(any("story 가 없다" in p for p in story_bad))

    def test_a_thin_so_what_is_a_warning_not_a_failure(self):
        """**이 한 줄이 2026-09-20 스토리를 죽였다.**

        일일 쪽 칸 하나가 비었다는 이유로 멀쩡한 스토리 브리프까지 통째로
        버렸고, 호출도 계약(2회)보다 한 번 더 나갔다. 얇으면 카드가 얇아지지만
        QA 와 repair 가 받는다 — 여기서 죽이면 그 대가로 스토리까지 빠진다.
        """
        payload = brief()
        payload["issues"][0]["so_what"] = []
        fatal, story_bad, warnings = self.check(payload)
        self.assertEqual((fatal, story_bad), ([], []))
        self.assertTrue(any("so_what" in w for w in warnings))


class AliasTests(unittest.TestCase):
    """**모델은 출력 스키마 이름보다 눈앞의 입력 이름을 따라 적는다.**

    2026-09-20 첫 프로덕션 실행에서 세 이슈와 스토리 블록 전부가 `why_it_matters`
    대신 `why_important` 를 냈다. 잘린 것도 사고가 예산을 먹은 것도 아니었다
    (finish=STOP · output 1441/6144 · thoughts=0). 입력 기사 칸에 `why_important`
    가 있었던 것이 원인이다. 이름을 입력 어디에도 없는 `so_what` 으로 바꾸고,
    그래도 동의어로 흔들릴 때를 위해 별칭을 받는다.
    """

    def test_the_canonical_name_appears_in_no_input_field(self):
        article_fields = set(ITEMS[0])
        self.assertNotIn("so_what", article_fields)
        # 예전 이름은 입력에 실제로 있었다 — 그것이 충돌의 원인이다.
        self.assertIn("why_important", article_fields)

    def test_the_prompt_asks_for_the_canonical_name_only(self):
        for prompt in (card_editorial.NARRATOR_SYSTEM,
                       card_editorial.daily_writer_system(2, 3, 18, 40, 40)):
            with self.subTest(prompt=prompt[:20]):
                self.assertIn('"so_what"', prompt)
                self.assertNotIn('"why_it_matters"', prompt)

    def test_the_observed_alias_is_accepted(self):
        payload = brief()
        row = payload["issues"][0]
        row["why_important"] = row.pop("so_what")
        self.assertEqual(self.check(payload), ([], [], []))

    def check(self, payload):
        card_editorial.normalize_brief(payload)
        return card_editorial.validate_brief(payload, ITEMS, None)

    def test_a_list_of_plain_strings_also_counts(self):
        """모양까지 틀렸다고 버리지 않는다 — 내용이 있으면 쓴다."""
        payload = brief()
        payload["issues"][0]["so_what"] = ["계절 기준으로 전환"]
        self.assertEqual(self.check(payload), ([], [], []))


class CallCountTests(unittest.TestCase):
    def _run(self, story_payload, responses, story_bad=()):
        calls: list[dict] = []
        with mock.patch.object(make_cards, "story_problems",
                               side_effect=lambda *_: list(story_bad)),                 mock.patch.object(make_cards, "card_editorial") as fake:
            fake.validate_brief = card_editorial.validate_brief
            fake.daily_writer_system = lambda **_: "sys"
            fake.writer_system = lambda **_: "sys"
            fake.NARRATOR_SYSTEM = "sys"
            fake.call = mock.Mock(side_effect=responses)
            daily, story = make_cards.run_editorial(ITEMS, "2026-09-20", 10,
                                                    story_payload, calls)
        return daily, story, fake.call

    def test_a_day_without_a_story_costs_one_logical_call(self):
        daily, story, call = self._run(None, [copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args_list[0].args[0], "card_daily_writer")

    def test_a_day_with_a_story_costs_two(self):
        payload = {"thread_id": "thread-x", "events": [{"date": "2026-09-19"}]}
        daily, story, call = self._run(payload, [brief(story_thread="thread-x"),
                                                 copy(with_story=True)])
        self.assertIsNotNone(daily)
        self.assertIsNotNone(story)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_writer"])

    def test_a_narrator_failure_drops_the_story_and_keeps_the_daily_card(self):
        """**일일 카드는 핵심 산출물이다.** 스토리 때문에 같이 빠지지 않는다."""
        payload = {"thread_id": "thread-x", "events": []}
        daily, story, call = self._run(
            payload, [RuntimeError("no key"), copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_daily_writer"])

    def test_a_brief_that_fails_its_contract_also_drops_only_the_story(self):
        payload = {"thread_id": "thread-x", "events": []}
        daily, story, call = self._run(
            payload, [brief(story_thread="thread-엉뚱한것"), copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        self.assertEqual(call.call_count, 2)

    def test_a_qa_failure_costs_exactly_one_repair(self):
        """**Narrator 는 다시 부르지 않는다.** 판단은 그대로 두고 문구만 고친다."""
        bad = copy()["daily"]
        bad["steps"][0]["why"] = ["진안·금산에서 착수", "실증기간 1년"]  # facts 의 되풀이
        daily, story, call = self._run(None, [bad, copy()["daily"]])
        self.assertIsNotNone(daily)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_daily_writer", "card_writer_repair"])

    def test_a_failure_that_survives_the_repair_gives_up_instead_of_shipping_it(self):
        bad = copy()["daily"]
        bad["steps"][0]["why"] = ["진안·금산에서 착수", "실증기간 1년"]
        daily, story, call = self._run(None, [bad, bad])
        self.assertIsNone(daily)      # 호출자가 결정적 폴백으로 떨어진다
        self.assertEqual(call.call_count, 2)


class StoryDomainTests(unittest.TestCase):
    """스토리 카피도 **저장 전에** 검증받고 repair 기회를 갖는다.

    PR #155 는 일일만 검증하고 스토리 카피는 그대로 저장했다. 그래서 잘못된
    스토리는 한참 뒤 렌더 단계에서 처음 걸렸고, 그때는 고칠 길이 없었다
    (story_cards 는 LLM 을 안 부른다). 실측 2026-09-20: 모델이 **일일 브리프의
    숫자를 스토리 표지 배지로 가져왔는데**(165.0GW — 스토리 재료 어디에도 없다)
    그 사실이 렌더 직전에야 드러나 그날 스토리가 0 장이었다.
    """

    def _run(self, responses, story_bad_sequence):
        calls: list[dict] = []
        seq = iter(story_bad_sequence)
        with mock.patch.object(make_cards, "story_problems",
                               side_effect=lambda *_: list(next(seq, []))),                 mock.patch.object(make_cards, "card_editorial") as fake:
            fake.validate_brief = card_editorial.validate_brief
            fake.normalize_brief = card_editorial.normalize_brief
            fake.daily_writer_system = lambda **_: "sys"
            fake.writer_system = lambda **_: "sys"
            fake.NARRATOR_SYSTEM = "sys"
            fake.call = mock.Mock(side_effect=responses)
            payload = {"thread_id": "thread-x", "events": [{"date": "2026-09-19"}]}
            daily, story = make_cards.run_editorial(ITEMS, "2026-09-20", 10,
                                                    payload, calls)
        return daily, story, fake.call

    def test_a_bad_story_copy_is_caught_at_the_writer_not_at_the_renderer(self):
        daily, story, call = self._run(
            [brief(story_thread="thread-x"), copy(with_story=True),
             copy(with_story=True)],
            [["cover.badge.value: 입력에 없는 숫자"], []])
        self.assertIsNotNone(story)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_writer", "card_writer_repair"])

    def test_a_story_that_survives_the_repair_is_dropped_and_the_daily_kept(self):
        """**일일 카드는 살린다.** 스토리 때문에 멀쩡한 일일 카피를 버리지 않는다."""
        daily, story, call = self._run(
            [brief(story_thread="thread-x"), copy(with_story=True),
             copy(with_story=True)],
            [["안 됨"], ["여전히 안 됨"]])
        self.assertIsNotNone(daily)
        self.assertIsNone(story)
        # Narrator 를 다시 부르지 않는다. 일일 단독 호출로도 안 내려간다.
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ["card_editorial_narrator", "card_writer", "card_writer_repair"])


class WhyCountTests(unittest.TestCase):
    """**의미 불릿 하한은 1 이다.** 규격이 아니라 재료를 따른다.

    2026-09-20 실측: 브리프가 `so_what` 을 둘 주었는데도 첫 카드의 `why` 가 한
    줄로 나왔고 repair 도 같은 한 줄을 냈다 — 두 번 더 부르고 결국 사이트 문장
    폴백으로 떨어졌다. 토큰 예산 때문이 아니었다(1,354/12,288). 둘을 채우라고
    계속 밀면 모델이 채우는 방법은 없는 의미를 지어내는 것뿐이고, 그건 이 카드가
    가장 피하려는 실패다.
    """

    def _card(self, why):
        return {"hook": {"headline": "오늘 먼저 볼 현안"},
                "steps": [{"issue_id": "issue-a", "headline": "SAR 현장 적용",
                           "facts": ["진안·금산에서 착수", "실증기간 1년"],
                           "why": why}]}

    def test_one_grounded_why_is_accepted(self):
        self.assertEqual(
            make_cards.review(self._card(["송전용량이 계절 기준으로 전환"]), ITEMS), [])

    def test_an_empty_why_is_still_refused(self):
        self.assertTrue(make_cards.review(self._card([]), ITEMS))

    def test_the_prompt_still_asks_for_two_or_three(self):
        """하한만 낮췄다. 모델에게는 계속 2~3 을 요구한다."""
        prompt = card_editorial.writer_system(
            make_cards.BULLETS_MIN, make_cards.BULLETS_MAX, 18, 40, 40,
            with_story=False)
        self.assertIn("2~3개", prompt)
        self.assertEqual(make_cards.WHY_MIN, 1)

    def test_padding_is_still_caught(self):
        """한 줄을 허용한다고 억지 두 줄이 통과하지는 않는다."""
        self.assertTrue(make_cards.review(
            self._card(["송전용량이 계절 기준으로 전환", "정책적 의미가 큽니다"]), ITEMS))


class ImportSideEffectTests(unittest.TestCase):
    """**모듈 import 는 다른 모듈의 전역을 건드리지 않는다.**

    `make_cards` 는 스토리 카피를 검증하려고 `story_cards` 를 지연 import 한다.
    그 파일은 오래 맨 위에서 `mc.OUT_DIR` 을 스토리 폴더로 바꿔 왔는데, 별도
    프로세스로만 불릴 때는 맞는 자리였다. 지연 import 가 생기면서 **import 만으로
    일일 카드의 렌더 대상이 바뀌었다.**

    2026-09-20 실측: 일일 슬라이드가 `cards/out-story/` 로 구워졌고 장수 게이트가
    빈 `cards/out/` 을 보고 `PNG 장수 불일치: 0 ≠ 5` 로 죽었다. 카피는 논리 2회로
    멀쩡히 나왔는데 워크플로는 빨간불이었다.
    """

    def test_importing_story_cards_does_not_move_the_daily_render_target(self):
        import importlib
        before = make_cards.OUT_DIR
        import story_cards
        importlib.reload(story_cards)
        self.assertEqual(make_cards.OUT_DIR, before)
        self.assertEqual(make_cards.OUT_DIR.name, "out")

    def test_the_story_path_still_renders_to_its_own_folder(self):
        import story_cards
        before = make_cards.OUT_DIR
        try:
            story_cards.use_story_out_dir()
            self.assertEqual(make_cards.OUT_DIR.name, "out-story")
        finally:
            make_cards.OUT_DIR = before

    def test_validating_a_story_copy_leaves_the_target_alone(self):
        """`story_problems` 가 부르는 지연 import 도 마찬가지다."""
        before = make_cards.OUT_DIR
        make_cards.story_problems({"cover": {}}, {"events": []})
        self.assertEqual(make_cards.OUT_DIR, before)


class CallLogTests(unittest.TestCase):
    def test_the_log_separates_logical_calls_from_http_attempts(self):
        rows = []
        with mock.patch("gemini_client.call_json", return_value={}):
            card_editorial.call("card_writer", "sys", {}, log=rows)
            card_editorial.call("card_writer_repair", "sys", {},
                                fix_these=["x"], log=rows)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["repair"])
        for row in rows:
            self.assertEqual(row["max_http_attempts"], card_editorial.CARD_LLM_RETRIES + 1)
            # 모델 이름은 정책이 정한다(이식 메모: 위 ModelPolicyTests).
            self.assertEqual(row["model"], llm_policy.profile("card_writer").model())

    def test_the_worst_case_is_far_below_the_old_one(self):
        """예전: 바깥 2회 × call_json 기본 4회 = 8. 지금: 논리 2회 × 2 = 4."""
        worst_story_day = 2 * (card_editorial.CARD_LLM_RETRIES + 1)
        self.assertLessEqual(worst_story_day, 4)


if __name__ == "__main__":
    unittest.main()
