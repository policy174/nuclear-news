"""audio_brief.py 단위 테스트 — 전 이슈 커버·비치명 계약·중복 생성 방지.

핵심 계약 5개:
  ① 대본은 단일 HOST — 섹션 items 가 issue ID 완전성 검증을 통과해야 TTS 를 부른다.
  ② 어떤 실패도 기존 오디오를 지우지 않는다 — 단 완성 청크가 있으면 부분 배송한다.
  ③ 같은 날짜 재실행은 Gemini 를 다시 부르지 않는다 (무료 티어 보호).
  ④ 실패는 종료 코드로 나간다 — 비치명 처리는 호출자(워크플로) 몫이다.
  ⑤ 부분이 무(無)보다 낫되, 배송 시간은 항상 예약된다 (hard deadline).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import audio_brief
from gemini_client import GeminiError


def briefing_row(date="2026-08-04"):
    return {
        "date": date,
        "headline": "한수원, 포천양수발전소 본공사 착수",
        "highlight_issues": [{"issue_id": "issue-1", "title": "포천양수 착공"}],
        "issues": [{"issue_id": "issue-1"}, {"issue_id": "issue-2"}],
    }


def issue(issue_id, title):
    return {
        "issue_id": issue_id,
        "title": title,
        "region": "국내",
        "summary": f"{title}에 대한 요약. 2033년 준공 목표로 승인되었다.",
        "latest_change": "본공사가 시작됐다.",
        "implication": "양수발전 확충이 전력망 유연성 확보와 맞물린다.",
        "why_important": "국내 신규 대형 전원 착공은 드문 사건이다.",
    }


def spoken_chars(script):
    return sum(len(m.group(2)) for m in
               (audio_brief.SPEAKER_RE.match(line) for line in script.splitlines()) if m)


def fake_pcm(script, rate=24000, factor=1.0):
    """대사 길이에 맞는 그럴듯한 길이의 PCM (s16le mono)."""
    seconds = max(1.0, spoken_chars(script) / audio_brief.SPOKEN_CHARS_PER_SEC * factor)
    return b"\x00" * (int(rate * seconds) * 2)


LONG_SCRIPT = "\n".join(
    [f"HOST: {'가' * 200} {i}" if i % 2 == 0 else f"ANALYST: {'나' * 200} {i}"
     for i in range(10)])

def _padded(text, chars):
    """분량 게이트(SECTION_FLOOR)를 통과하는 길이의 대사 본문."""
    filler = " 세부 내용이 이어지는 문장입니다."
    while len(text) < chars:
        text += filler
    return text


# 기본 픽스처(briefing_row + write_data 기본값)에 맞춘 섹션 응답:
# deep = issue-1, rest = issue-2. 대본 생성은 (deep, rest) 두 번 호출하고,
# 재료·검증의 ID 는 실제 issue_id 가 아니라 섹션 내 위치 번호다 (hex 오타 방지).
# 대사 길이는 섹션 예산(deep 607자·rest 148자)의 하한을 넘긴다.
DEEP_ITEMS = [{"id": "1",
               "script": ("HOST: " + _padded(
                   "포천양수발전소 본공사가 시작됐습니다. 2033년 준공 목표가 "
                   "확정된 상태인데요, 지금은 착공 단계입니다.", 550))}]
REST_ITEMS = [{"id": "1",
               "script": "HOST: " + _padded(
                   "중국에서 신규 원자로가 승인됐습니다.", 150)}]


def script_responses():
    """happy path 는 스크립트 응답 2개(deep items + rest items)를 소비한다."""
    return [{"items": [dict(i) for i in DEEP_ITEMS]},
            {"items": [dict(i) for i in REST_ITEMS]}]


class FakeTime:
    """time.monotonic/sleep 대역 — sleep 이 실제로 잠들면 테스트가 느려진다."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class AudioBriefTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._orig = (audio_brief.WEB_DATA, audio_brief.AUDIO_DIR)
        audio_brief.WEB_DATA = base / "data"
        audio_brief.AUDIO_DIR = base / "data" / "audio"
        audio_brief.WEB_DATA.mkdir(parents=True)
        self._orig_fns = (audio_brief.is_available, audio_brief.call_json,
                          audio_brief.call_tts, audio_brief.to_mp3,
                          audio_brief.send_telegram_audio)
        self.addCleanup(self._restore)
        self.calls = []
        self.call_kwargs = []
        self.responses = []
        self.tts_calls = []
        self.tts_models = []
        self.sent = []
        self.send_ok = True
        audio_brief.is_available = lambda: True
        audio_brief.call_json = self._fake_call
        audio_brief.call_tts = self._fake_tts
        audio_brief.to_mp3 = self._fake_mp3
        audio_brief.send_telegram_audio = self._fake_send
        audio_brief._last_tts_at = 0.0

    def _restore(self):
        audio_brief.WEB_DATA, audio_brief.AUDIO_DIR = self._orig
        (audio_brief.is_available, audio_brief.call_json,
         audio_brief.call_tts, audio_brief.to_mp3,
         audio_brief.send_telegram_audio) = self._orig_fns

    def _fake_send(self, mp3_path, meta):
        self.sent.append((mp3_path.name, dict(meta)))
        return self.send_ok

    def _fake_call(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        self.call_kwargs.append(kwargs)
        if not self.responses:
            raise GeminiError("429")
        return self.responses.pop(0)

    def _fake_tts(self, script, models=None, **kwargs):
        self.tts_calls.append(script)
        self.tts_models.append(list(models or []))
        # 대사 길이에 비례한 PCM — 짧게 돌려주면 잘림 감지가 물어야 정상이다.
        return fake_pcm(script), 24000

    def _fake_mp3(self, pcm, rate, out_path):
        out_path.write_bytes(b"mp3")

    def write_data(self, briefing=None, issues=None):
        (audio_brief.WEB_DATA / "briefings.json").write_text(
            json.dumps([briefing or briefing_row()], ensure_ascii=False),
            encoding="utf-8")
        rows = issues if issues is not None else [
            issue("issue-1", "포천양수 착공"), issue("issue-2", "중국 원자로 승인")]
        (audio_brief.WEB_DATA / "issues.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def load(self):
        return audio_brief.load_briefing(audio_brief.WEB_DATA)

    def use_fake_time(self, now=1000.0):
        fake = FakeTime(now)
        self.addCleanup(setattr, audio_brief, "time", audio_brief.time)
        audio_brief.time = fake
        return fake

    # ── 재료 조립 ─────────────────────────────────────────────

    def test_material_deep_has_change_rest_does_not(self):
        self.write_data()
        briefing, by_id = self.load()
        deep = audio_brief.build_deep_material(briefing, by_id, ["issue-1"], 90.0)
        rest = audio_brief.build_rest_material(44.0, by_id, ["issue-2"])
        self.assertIn("최근 변화", deep)
        self.assertIn("ID: 1", deep)
        # 실제 issue_id(긴 hex)는 재료에 싣지 않는다 — 모델이 베끼다 한 글자
        # 틀린 실사고(2026-08-15 eval) 뒤로 위치 번호만 쓴다.
        self.assertNotIn("ID: issue-1", deep)
        self.assertNotIn("왜 중요한가", deep)
        self.assertNotIn("해석", deep)
        self.assertIn("ID: 1", rest)
        self.assertIn("중국 원자로 승인", rest)
        self.assertNotIn("최근 변화", rest)

    def test_material_covers_all_issues(self):
        """단신 상한(옛 REST_LIMIT=6)은 이슈 7건 이후를 통째로 버렸다 —
        이제 전 이슈가 재료에 실린다."""
        briefing = briefing_row()
        briefing["issues"] = [{"issue_id": f"issue-{i}"} for i in range(1, 10)]
        issues = [issue(f"issue-{i}", f"이슈 {i}") for i in range(1, 10)]
        self.write_data(briefing, issues)
        loaded, by_id = self.load()
        deep_ids, rest_ids = audio_brief._issue_ids(loaded)
        self.assertEqual(deep_ids, ["issue-1"])
        self.assertEqual(rest_ids, [f"issue-{i}" for i in range(2, 10)])
        rest = audio_brief.build_rest_material(100.0, by_id, rest_ids)
        for i in range(2, 10):
            self.assertIn(f"이슈 {i}", rest)

    def test_load_briefing_picks_latest_date(self):
        rows = [briefing_row("2026-08-03"), briefing_row("2026-08-04")]
        (audio_brief.WEB_DATA / "briefings.json").write_text(
            json.dumps(rows), encoding="utf-8")
        (audio_brief.WEB_DATA / "issues.json").write_text("[]", encoding="utf-8")
        briefing, _ = audio_brief.load_briefing(audio_brief.WEB_DATA)
        self.assertEqual(briefing["date"], "2026-08-04")

    # ── 길이 모델 (초 배분 × 실측 발화율) ─────────────────────

    def test_spoken_target_normal_day_lands_in_8_to_10_minutes(self):
        """평시(3 deep + 10 rest)는 480~520초 — eval 기준(480~600초) 안."""
        low, high = audio_brief.spoken_target(3, 10)
        self.assertEqual(low, int(480 * audio_brief.CHARS_PER_SEC))
        self.assertEqual(high, int(520 * audio_brief.CHARS_PER_SEC))

    def test_spoken_target_light_day_allows_shorter(self):
        low, high = audio_brief.spoken_target(3, 2)
        self.assertLess(high, int(480 * audio_brief.CHARS_PER_SEC))
        self.assertEqual(low, int(int(344 * 0.85) * audio_brief.CHARS_PER_SEC))

    def test_spoken_target_never_exceeds_ceiling(self):
        for rest in (17, 40, 100):
            _, high = audio_brief.spoken_target(3, rest)
            self.assertLessEqual(high, audio_brief.MAX_SPOKEN)

    def test_section_budgets_scale_down_on_overload(self):
        """총예산만 자르면 섹션 prompt 예산과 모순 — 배분 단계에서 비례 축소."""
        deep_sec, rest_sec = audio_brief.section_budgets(3, 17)
        self.assertLessEqual(deep_sec + rest_sec,
                             audio_brief.TARGET_SEC_MAX - audio_brief.FRAME_SEC + 1e-6)
        self.assertLess(deep_sec, audio_brief.DEEP_SEC * 3)   # scale < 1
        # (3,17)은 단신당 ~19.5초 — 최소 1문장 모드가 발동하면 안 된다
        self.assertGreaterEqual(rest_sec / 17, audio_brief.REST_MIN_MODE_SEC)

    def test_rest_min_mode_fires_only_on_extreme_overload(self):
        """(3,40)은 단신당 ~10.9초 < 12초 — 최소 1문장 모드 발동."""
        _, rest_sec = audio_brief.section_budgets(3, 40)
        self.assertLess(rest_sec / 40, audio_brief.REST_MIN_MODE_SEC)
        briefing = briefing_row()
        briefing["highlight_issues"] = [
            {"issue_id": f"issue-{i}"} for i in range(1, 4)]
        briefing["issues"] = [{"issue_id": f"issue-{i}"} for i in range(1, 44)]
        issues = [issue(f"issue-{i}", f"이슈 {i}") for i in range(1, 44)]
        self.write_data(briefing, issues)
        loaded, by_id = self.load()
        deep_ids, rest_ids = audio_brief._issue_ids(loaded)
        self.responses = [
            {"items": [{"id": str(n),
                        "script": "HOST: " + _padded(f"{n}번 심층입니다.", 300)}
                       for n in range(1, len(deep_ids) + 1)]},
            {"items": [{"id": str(n),
                        "script": "HOST: " + _padded(f"{n}번 단신입니다.", 70)}
                       for n in range(1, len(rest_ids) + 1)]},
        ]
        captured = []
        orig_section = audio_brief.generate_section

        def spy(system_prompt, material, expected_ids, **kwargs):
            captured.append(system_prompt)
            return orig_section(system_prompt, material, expected_ids, **kwargs)

        self.addCleanup(setattr, audio_brief, "generate_section", orig_section)
        audio_brief.generate_section = spy
        audio_brief.generate_script(loaded, by_id)
        self.assertIn("**1문장**", captured[1])

    # ── 대본 검증 게이트 (issue ID 완전성) ────────────────────

    def test_validate_items_happy_path_strips_and_flattens(self):
        lines, spoken = audio_brief.validate_items(
            [{"id": "a", "script": "HOST: 첫 소식입니다.\nHOST: 둘째 줄입니다."},
             {"id": "b", "script": "ANALYST: 네, 해설도 살립니다."}],
            ["a", "b"])
        self.assertEqual(lines[0], "HOST: 첫 소식입니다.")
        self.assertTrue(all(line.startswith("HOST: ") for line in lines))
        self.assertEqual(lines[2], "HOST: 해설도 살립니다.")   # ANALYST 흡수 + 추임새 제거
        self.assertEqual(spoken, sum(len(l.split(": ", 1)[1]) for l in lines))

    def test_validate_items_rejects_missing_id(self):
        with self.assertRaises(ValueError) as ctx:
            audio_brief.validate_items(
                [{"id": "a", "script": "HOST: x"}], ["a", "b"])
        self.assertIn("누락", str(ctx.exception))
        self.assertIn("'b'", str(ctx.exception))

    def test_validate_items_rejects_extra_lines_hiding_missing_issue(self):
        """min_lines 방식의 구멍: 한 이슈가 세 줄 쓰고 다른 이슈를 빼먹어도
        줄 수는 통과했다 — ID 검증은 이 케이스를 잡는다."""
        with self.assertRaises(ValueError) as ctx:
            audio_brief.validate_items(
                [{"id": "a", "script": "HOST: 하나.\nHOST: 둘.\nHOST: 셋."}],
                ["a", "b"])
        self.assertIn("누락", str(ctx.exception))

    def test_validate_items_rejects_duplicate_and_invented(self):
        with self.assertRaises(ValueError) as ctx:
            audio_brief.validate_items(
                [{"id": "a", "script": "HOST: x"},
                 {"id": "a", "script": "HOST: y"},
                 {"id": "ghost", "script": "HOST: z"}],
                ["a", "b"])
        message = str(ctx.exception)
        self.assertIn("중복", message)
        self.assertIn("창작", message)
        self.assertIn("ghost", message)

    def test_validate_items_rejects_wrong_order(self):
        with self.assertRaises(ValueError) as ctx:
            audio_brief.validate_items(
                [{"id": "b", "script": "HOST: x"},
                 {"id": "a", "script": "HOST: y"}],
                ["a", "b"])
        self.assertIn("순서", str(ctx.exception))

    def test_validate_items_normalizes_labels(self):
        """HOST: 라벨은 모델에게 시키지 않고 코드가 붙인다 — 수십 번 전사시키면
        'HOS:'·라벨 누락 오타가 난다(2026-08-16 eval 실사고, hex ID 와 같은 교훈).
        순수 낭독문·정상 라벨·오타 라벨 전부 같은 결과가 돼야 한다."""
        for script in ("그냥 낭독문입니다.", "HOST: 그냥 낭독문입니다.",
                       "HOS: 그냥 낭독문입니다.", "호스트: 그냥 낭독문입니다."):
            lines, _ = audio_brief.validate_items(
                [{"id": "a", "script": script}], ["a"])
            self.assertEqual(lines, ["HOST: 그냥 낭독문입니다."])

    def test_validate_items_strips_leading_fillers(self):
        """2026-08-10 대본 26줄 중 13줄이 '네,'로 시작했다. 프롬프트의
        '남발 금지'로는 안 됐으므로 코드가 자른다."""
        lines, _ = audio_brief.validate_items(
            [{"id": "a", "script": "HOST: 네, 원안위가 오늘 발표했습니다."},
             {"id": "b", "script": "HOST: 네트워크 투자도 함께 발표됐습니다."}],
            ["a", "b"])
        self.assertEqual(lines[0], "HOST: 원안위가 오늘 발표했습니다.")
        # 낱말 첫머리는 건드리지 않는다 — '네트워크'
        self.assertEqual(lines[1], "HOST: 네트워크 투자도 함께 발표됐습니다.")

    def test_strip_filler_keeps_line_that_is_only_filler(self):
        """뗄 내용이 없으면 빈 대사가 된다 — 그럴 땐 그대로 둔다."""
        self.assertEqual(audio_brief.strip_filler("네."), "네.")
        self.assertEqual(audio_brief.strip_filler("그렇군요."), "그렇군요.")

    def test_frame_is_deterministic_and_model_frame_lines_dropped(self):
        """오프닝·클로징은 apply_frame 이 단일 소유자로 정확히 한 번 붙인다 —
        조립은 본문(deep+전환+rest)만 만든다. 모델이 그래도 쓴 인사·마무리
        줄은 중복이라 걷어낸다."""
        briefing = briefing_row()
        body = "\n".join([
            "HOST: 안녕하십니까? 브리핑을 시작하겠습니다.",
            "HOST: 원안위가 오늘 심사 결과를 발표했습니다.",
            "HOST: 오늘 브리핑은 여기까지입니다. 감사합니다.",
        ])
        framed = audio_brief.apply_frame(body, briefing)
        lines = framed.splitlines()
        self.assertEqual(lines[0], "HOST: 8월 4일 화요일 Nuclens 오디오 브리핑입니다.")
        self.assertEqual(lines[-1], "HOST: 오늘 브리핑은 여기까지입니다.")
        self.assertEqual(len(lines), 3)                 # 인사·중복 마무리 제거
        self.assertIn("원안위", framed)

    def test_frame_never_embeds_headline(self):
        """개조식 헤드라인(출처 꼬리표·중첩 따옴표 포함)을 문장에 접붙이면
        "…개최 (산업부) 입니다"가 된다(2026-08-13 실사고). 오프닝은 날짜뿐."""
        briefing = dict(briefing_row(),
                        headline="첨단기술 '7대 SEED' 보고회 개최 (산업부)")
        opening, _ = audio_brief.frame_lines(briefing)
        self.assertEqual(opening, "HOST: 8월 4일 화요일 Nuclens 오디오 브리핑입니다.")

    # ── 대본 생성 (섹션 2회 + 조립) ───────────────────────────

    def test_generate_script_assembles_deep_transition_rest(self):
        self.write_data()
        briefing, by_id = self.load()
        self.responses = script_responses()
        script = audio_brief.generate_script(briefing, by_id)
        lines = script.splitlines()
        self.assertEqual(len(self.calls), 2)            # deep + rest 각 1회
        self.assertIn(audio_brief.TRANSITION_LINE, lines)
        self.assertLess(lines.index(audio_brief.TRANSITION_LINE),
                        len(lines) - 1)                 # 전환 뒤에 단신이 있다
        self.assertNotIn("HOST: HOST:", script)         # HOST: 이중 접두 없음
        # 오프닝·클로징은 조립에 없다 — apply_frame 몫
        self.assertNotIn("브리핑입니다", script)
        self.assertNotIn("여기까지", script)

    def test_generate_script_skips_llm_for_empty_rest(self):
        """빈 섹션은 API 호출 0회 — 이슈가 하이라이트뿐인 날."""
        briefing = briefing_row()
        briefing["issues"] = [{"issue_id": "issue-1"}]
        self.write_data(briefing, [issue("issue-1", "포천양수 착공")])
        loaded, by_id = self.load()
        self.responses = [{"items": [dict(i) for i in DEEP_ITEMS]}]
        script = audio_brief.generate_script(loaded, by_id)
        self.assertEqual(len(self.calls), 1)            # rest 호출 없음
        self.assertNotIn(audio_brief.TRANSITION_LINE, script)

    def test_generate_script_raises_on_no_issues(self):
        self.write_data(dict(briefing_row(), issues=[], highlight_issues=[]), [])
        loaded, by_id = self.load()
        with self.assertRaises(ValueError):
            audio_brief.generate_script(loaded, by_id)
        self.assertEqual(self.calls, [])

    def test_generate_script_disables_thinking(self):
        """thinking 토큰이 출력 예산을 잠식해 대본이 잘린 CI 실사고(2026-08-04,
        thoughts=7863/8192) 재발 방지 — 대본 호출은 반드시 thinking_budget=0."""
        self.write_data()
        briefing, by_id = self.load()
        self.responses = script_responses()
        audio_brief.generate_script(briefing, by_id)
        self.assertEqual(self.call_kwargs[0].get("thinking_budget"), 0)

    def test_generate_script_uses_isolated_quota_bucket(self):
        """대본은 기본 MODEL(크롤·브리핑 체인 공용 버킷)이 아니라 별도 모델
        버킷을 쓴다 — 공용 버킷은 저녁이면 고갈돼 3연속 429 실사고(2026-08-04)."""
        self.write_data()
        briefing, by_id = self.load()
        self.responses = script_responses()
        audio_brief.generate_script(briefing, by_id)
        self.assertEqual(self.call_kwargs[0].get("model"),
                         audio_brief.SCRIPT_MODEL_DEFAULT)

    def test_generate_script_falls_back_to_shared_bucket_on_429(self):
        """2026-08-10: 전용 버킷(flash-lite)이 분당 한도에 걸려 대본이 죽고
        그날 오디오만 조용히 빠졌다. 버티는 것만으로 안 되면 버킷을 옮긴다."""
        self.write_data()
        briefing, by_id = self.load()
        responses = script_responses()

        def flaky(system_prompt, user_message, **kwargs):
            self.call_kwargs.append(kwargs)
            if kwargs.get("model") == audio_brief.SCRIPT_MODEL_DEFAULT:
                raise GeminiError("HTTP 429: limit 20")
            return responses.pop(0)

        audio_brief.call_json = flaky
        script = audio_brief.generate_script(briefing, by_id)
        self.assertIn("HOST:", script)
        self.assertEqual(self.call_kwargs[-1]["model"], audio_brief.gemini_client.MODEL)

    def test_generate_script_waits_longer_than_default(self):
        """대본은 하루 1회·마지막 스텝이라 느려도 된다 — 기본 재시도로는
        분당 한도 창을 못 넘긴 실사고가 있었다."""
        self.write_data()
        briefing, by_id = self.load()
        self.responses = script_responses()
        audio_brief.generate_script(briefing, by_id)
        self.assertEqual(self.call_kwargs[0].get("retries"),
                         audio_brief.SCRIPT_RETRIES)
        self.assertGreater(audio_brief.SCRIPT_RETRIES, 3)

    def test_generate_section_reprompts_with_missing_ids(self):
        """검증 실패 시 해당 섹션만 재요청 1회 — 재요청 메시지에 누락 id 명시."""
        self.write_data()
        briefing, by_id = self.load()
        self.responses = [
            {"items": []},                              # deep 1차 — 누락
            {"items": [dict(i) for i in DEEP_ITEMS]},   # deep 재요청 성공
            {"items": [dict(i) for i in REST_ITEMS]},   # rest 1차 성공
        ]
        script = audio_brief.generate_script(briefing, by_id)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("[재요청]", self.calls[1])
        self.assertIn("누락: ['1']", self.calls[1])
        self.assertIn("HOST:", script)

    def test_generate_section_reprompts_on_short_output(self):
        """모델은 [분량]의 ~75%만 채운다 — 하한 미달이면 수치를 담아 재요청
        1회, 재요청 후에도 짧으면 수용한다 (짧은 브리핑 > 없는 브리핑)."""
        self.write_data()
        briefing, by_id = self.load()
        short_deep = {"items": [{"id": "1", "script": "HOST: 아주 짧은 심층."}]}
        self.responses = [short_deep, dict(short_deep),
                          {"items": [dict(i) for i in REST_ITEMS]}]
        script = audio_brief.generate_script(briefing, by_id)
        self.assertEqual(len(self.calls), 3)            # deep + 재요청 + rest
        self.assertIn("미달", self.calls[1])
        self.assertIn("아주 짧은 심층", script)          # 재요청 후에도 수용

    def test_prompt_ask_is_inflated(self):
        """프롬프트의 [분량] 숫자는 이행률 역보정(×1.3)이 걸려 있다 —
        검증·상한은 원래 목표 기준이라 숫자가 달라야 정상."""
        self.write_data()
        _, by_id = self.load()
        material = audio_brief.build_rest_material(22.0, by_id, ["issue-2"])
        ask = int(int(22.0 * audio_brief.CHARS_PER_SEC) * audio_brief.PROMPT_ASK_SCALE)
        self.assertIn(f"{ask:,}자", material)

    def _overload_day(self, deep_chars, rest_chars):
        """3 deep + 40 rest 과부하 날 — 섹션별 ceil 안쪽 크기의 응답 세트."""
        briefing = briefing_row()
        briefing["highlight_issues"] = [
            {"issue_id": f"issue-{i}", "title": f"이슈 {i}"} for i in (1, 2, 3)]
        briefing["issues"] = [{"issue_id": f"issue-{i}"} for i in range(1, 44)]
        issues = [issue(f"issue-{i}", f"이슈 {i}") for i in range(1, 44)]
        self.write_data(briefing, issues)
        self.responses = [
            {"items": [{"id": str(n),
                        "script": "HOST: " + _padded(f"{n}번 심층.", deep_chars)[:deep_chars]}
                       for n in (1, 2, 3)]},
            {"items": [{"id": str(n),
                        "script": "HOST: " + _padded(f"{n}번 단신.", rest_chars)[:rest_chars]}
                       for n in range(1, 41)]},
        ]
        return self.load()

    def test_assembly_grace_accepts_mild_overrun_blocks_runaway(self):
        """섹션이 각자 ceil 안이라 트림·재요청 없이 통과해도 합계가 상한을 넘을
        수 있다 — 유예(×1.15)까지 수용, 그 밖 폭주만 차단. raise 하면 그날
        오디오가 통째로 사라진다."""
        briefing, by_id = self._overload_day(370, 85)   # 합계 ~4528자 — 유예 내
        script = audio_brief.generate_script(briefing, by_id)
        total = spoken_chars(script)
        self.assertGreater(total, audio_brief.MAX_SPOKEN)
        self.assertLessEqual(total, audio_brief.MAX_SPOKEN * audio_brief.MAX_SPOKEN_GRACE)
        briefing, by_id = self._overload_day(405, 99)   # 합계 ~5193자 — 폭주
        with self.assertRaises(ValueError):
            audio_brief.generate_script(briefing, by_id)

    def test_runaway_section_is_sentence_trimmed(self):
        """재요청 후에도 섹션이 ceil 을 넘으면 문장 트림 — 이슈(줄)는 절대
        버리지 않고 이슈당 예산으로 줄인다 (2026-08-16 eval: 재요청 응답
        5,505자 폭주 실사고)."""
        huge = {"items": [{"id": "1", "script": "HOST: " + _padded("일번.", 800)},
                          {"id": "2", "script": "HOST: " + _padded("이번.", 800)}]}
        self.responses = [huge, {"items": [dict(i) for i in huge["items"]]}]
        lines, spoken = audio_brief.generate_section(
            audio_brief.SYSTEM_PROMPT_REST, "재료", ["1", "2"],
            high_chars=300, ceil_ratio=audio_brief.SECTION_CEIL)
        self.assertEqual(len(lines), 2)                  # 이슈 수 보존
        self.assertLessEqual(spoken, 300 * audio_brief.SECTION_CEIL)
        self.assertIn("일번.", lines[0])

    # ── 프롬프트 회귀 (c82a09f 게토차: 예시의 빈 값은 그대로 배껴진다) ──

    def test_prompt_output_example_does_not_prime_empty_values(self):
        for prompt in (audio_brief.SYSTEM_PROMPT_DEEP, audio_brief.SYSTEM_PROMPT_REST):
            example = prompt.split("[출력")[-1]
            for poison in ('""', "null", "unknown", "N/A"):
                self.assertNotIn(poison, example)
            self.assertIn("...", example)

    # ── TTS 계약 ─────────────────────────────────────────────

    def test_split_script_chunks_at_speaker_lines(self):
        """긴 대본은 여러 요청으로 나눈다 — 4분을 1요청으로 뽑으면 뒤쪽이
        먹고 작아진다(2026-08-08 실측: 마지막 30초 -40.2 dB vs 첫 30초 -17.6)."""
        chunks = audio_brief.split_script(LONG_SCRIPT)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks).splitlines(), LONG_SCRIPT.splitlines())
        for chunk in chunks:
            self.assertTrue(all(audio_brief.SPEAKER_RE.match(line)
                                for line in chunk.splitlines()))
        self.assertTrue(all(
            sum(len(audio_brief.SPEAKER_RE.match(line).group(2))
                for line in chunk.splitlines()) <= audio_brief.CHUNK_SPOKEN
            for chunk in chunks[:-1]))

    def test_split_script_keeps_short_script_whole(self):
        short = "HOST: 짧은 브리핑입니다.\nHOST: 여기서 끝입니다."
        self.assertEqual(audio_brief.split_script(short), [short])

    def test_tts_payload_single_speaker_chunk_drops_labels(self):
        """멀티스피커 모드가 아니면 'HOST:' 라벨을 그대로 읽어버리므로
        접두어를 떼고 보낸다."""
        payload = audio_brief.tts_payload("HOST: 첫 소식입니다.\nHOST: 다음 소식입니다.")
        speech = payload["generationConfig"]["speechConfig"]
        self.assertNotIn("multiSpeakerVoiceConfig", speech)
        self.assertEqual(
            speech["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
            audio_brief.VOICES["HOST"])
        self.assertNotIn("HOST:", payload["contents"][0]["parts"][0]["text"])

    def test_synthesize_concatenates_chunks(self):
        pcm, rate, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        chunks = audio_brief.split_script(LONG_SCRIPT)
        self.assertEqual(len(self.tts_calls), len(chunks))
        self.assertEqual((done, total, reason), (len(chunks), len(chunks), None))
        self.assertEqual(rate, 24000)
        gap = int(rate * audio_brief.CHUNK_GAP_SEC) * 2
        self.assertEqual(len(pcm),
                         sum(len(fake_pcm(c)) for c in chunks) + (len(chunks) - 1) * gap)

    def test_synthesize_restarts_when_nothing_done(self):
        """0청크에서 실패하면 다음 모델이 처음부터 만든다."""
        chunks = len(audio_brief.split_script(LONG_SCRIPT))
        first, second = audio_brief._tts_models()[:2]
        seen = []

        def flaky(chunk, models=None, **kwargs):
            model = (models or [])[0]
            seen.append(model)
            if model == first:
                raise GeminiError(f"{model}: HTTP 429")
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = flaky
        _, _, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        self.assertEqual(seen.count(first), 1)
        self.assertEqual(seen.count(second), chunks)
        self.assertEqual((done, total, reason), (chunks, chunks, None))

    def test_synthesize_restarts_at_one_chunk_boundary(self):
        """RESTART_THRESHOLD=2 의 핵심 경계 — 1청크만 완료된 채 폴백하면
        그 청크를 버리고 처음부터 다시 만든다 (깨끗한 재시작이 쌈)."""
        first, second = audio_brief._tts_models()[:2]
        chunks = audio_brief.split_script(LONG_SCRIPT)
        seen = []

        def flaky(chunk, models=None, **kwargs):
            model = (models or [])[0]
            seen.append((model, chunk))
            if model == first and chunk != chunks[0]:
                raise GeminiError(f"{model}: HTTP 429")
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = flaky
        _, _, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        first_calls = [c for m, c in seen if m == first]
        second_calls = [c for m, c in seen if m == second]
        self.assertEqual(first_calls, chunks[:2])       # 1청크 성공 + 2청크 실패
        self.assertEqual(second_calls, chunks)          # 폐기 후 처음부터 전부
        self.assertEqual((done, total, reason), (len(chunks), len(chunks), None))

    def test_synthesize_resumes_when_two_chunks_done(self):
        """2청크 이상 완료 후 폴백은 실패한 청크부터 **이어받는다** —
        재시작만 있으면 7세그먼트가 21렌더가 된다 (NucBrief 실측)."""
        first, second = audio_brief._tts_models()[:2]
        chunks = audio_brief.split_script(LONG_SCRIPT)
        self.assertGreaterEqual(len(chunks), 3)
        seen = []

        def flaky(chunk, models=None, **kwargs):
            model = (models or [])[0]
            seen.append((model, chunk))
            if model == first and chunk == chunks[2]:
                raise GeminiError(f"{model}: HTTP 429")
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = flaky
        _, _, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        second_calls = [c for m, c in seen if m == second]
        self.assertEqual(second_calls, chunks[2:])      # 3번 청크부터만
        self.assertEqual((done, total, reason), (len(chunks), len(chunks), None))

    def test_synthesize_ships_partial_when_all_models_fail_late(self):
        """전 모델이 뒤쪽 청크에서 실패하면 완성분을 부분 배송한다 —
        0청크일 때만 raise."""
        chunks = audio_brief.split_script(LONG_SCRIPT)
        fail_at = chunks[2]

        def flaky(chunk, models=None, **kwargs):
            if chunk == fail_at:
                err = GeminiError("HTTP 429")
                err.reason = "rate_limit"
                raise err
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = flaky
        pcm, rate, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        self.assertEqual((done, total), (2, len(chunks)))
        self.assertEqual(reason, "rate_limit")
        self.assertGreater(len(pcm), 0)

    def test_synthesize_raises_when_zero_chunks(self):
        def dead(chunk, models=None, **kwargs):
            raise GeminiError("HTTP 500")

        audio_brief.call_tts = dead
        with self.assertRaises(GeminiError):
            audio_brief.synthesize(LONG_SCRIPT)

    def test_synthesize_stops_at_hard_deadline_and_ships_partial(self):
        """deadline 을 넘기면 신규 호출 없이 멈춘다 — 완성분은 부분 배송,
        사유는 hard_deadline."""
        fake = self.use_fake_time()

        def slow(chunk, models=None, **kwargs):
            fake.now += 30.0
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = slow
        deadline = fake.now + 80.0   # 1청크(30초) 뒤 잔여 검사(56초)에 걸린다
        pcm, rate, done, total, reason = audio_brief.synthesize(LONG_SCRIPT, deadline)
        self.assertEqual(done, 1)
        self.assertLess(done, total)
        self.assertEqual(reason, "hard_deadline")

    def test_synthesize_detects_silent_truncation(self):
        """Gemini TTS 는 긴 요청을 오류 없이 잘라 돌려준다. 대사 길이 대비
        음원이 너무 짧으면 청크 실패로 취급한다 — 전 모델이 같은 증상이면
        0청크라 raise."""
        audio_brief.call_tts = lambda chunk, models=None, **kw: (b"\x00" * 48000, 24000)
        with self.assertRaises(GeminiError) as ctx:
            audio_brief.synthesize(LONG_SCRIPT)   # 대사 900자에 1초짜리 음원
        self.assertIn("잘림", str(ctx.exception))

    def test_truncation_check_passes_on_plausible_length(self):
        chunk = "HOST: " + "가" * 850
        pcm = b"\x00" * (2 * 24000 * 100)         # 100초
        audio_brief._check_not_truncated(1, chunk, pcm, 24000)  # 예외 없음

    def test_trim_silence_strips_both_ends(self):
        """이음새가 파일에서 제일 긴 정적이 되던 것(2026-08-10 경계 0.92·0.96초
        vs 문장 사이 0.5~0.7초) — 청크가 달고 오는 여백을 걷어낸다."""
        rate = 24000
        quiet = b"\x00\x00" * rate          # 1초 무음
        loud = (b"\x00\x40" * rate)         # 1초 유음 (진폭 0x4000)
        trimmed = audio_brief.trim_silence(quiet + loud + quiet, rate)
        self.assertAlmostEqual(len(trimmed) / 2 / rate, 1.0, places=1)

    def test_trim_silence_keeps_all_silent_chunk(self):
        """통째로 무음이면 원본을 준다 — 빈 바이트를 이어붙이면 그 청크가
        사라진 것을 아무도 모른다."""
        pcm = b"\x00\x00" * 24000
        self.assertEqual(audio_brief.trim_silence(pcm, 24000), pcm)

    def test_synthesize_uses_one_gap_between_chunks(self):
        """이음새 간격은 TTS 여백이 아니라 우리가 정한 값 하나여야 한다."""
        rate = 24000
        pad = b"\x00\x00" * (rate // 2)     # 청크마다 앞뒤 0.5초 여백
        body = b"\x00\x40" * rate

        def padded(chunk, models=None, **kwargs):
            return pad + body + pad, rate

        audio_brief.call_tts = padded
        self.addCleanup(setattr, audio_brief, "_check_not_truncated",
                        audio_brief._check_not_truncated)
        audio_brief._check_not_truncated = lambda *a, **k: None
        pcm, _, _, _, _ = audio_brief.synthesize(LONG_SCRIPT)
        chunks = len(audio_brief.split_script(LONG_SCRIPT))
        gap = int(rate * audio_brief.CHUNK_GAP_SEC) * 2
        self.assertEqual(len(pcm), chunks * len(body) + (chunks - 1) * gap)

    def test_synthesize_rate_mismatch_ships_partial(self):
        """레이트가 섞인 채 이어붙이면 뒷부분이 배속으로 재생된다 — 섞이는
        청크는 실패로 치고, 전 모델이 같은 증상이면 완성분만 부분 배송한다."""
        seen = {"n": 0}

        def mixed_rate(chunk, models=None, **kwargs):
            seen["n"] += 1
            rate = 24000 if seen["n"] % 3 == 1 else 16000
            return fake_pcm(chunk, rate=rate, factor=1.2), rate

        audio_brief.call_tts = mixed_rate
        pcm, rate, done, total, reason = audio_brief.synthesize(LONG_SCRIPT)
        self.assertLess(done, total)
        self.assertEqual(reason, "provider_error")

    def test_tts_model_env_override_goes_first(self):
        os.environ["GEMINI_TTS_MODEL"] = "gemini-test-tts"
        self.addCleanup(os.environ.pop, "GEMINI_TTS_MODEL", None)
        models = audio_brief._tts_models()
        self.assertEqual(models[0], "gemini-test-tts")
        self.assertEqual(models[1:], audio_brief.TTS_MODELS)

    # ── call_tts 재시도·페이싱·예산 ──────────────────────────

    def _http_error(self, code, body):
        return urllib.error.HTTPError(
            "https://tts", code, "err", None, io.BytesIO(body.encode("utf-8")))

    def _patch_urlopen(self, handler):
        original = audio_brief.urllib.request.urlopen
        self.addCleanup(setattr, audio_brief.urllib.request, "urlopen", original)
        audio_brief.urllib.request.urlopen = handler

    def test_call_tts_retries_within_model_using_server_delay(self):
        """429 는 서버가 알려주는 대기 시간을 그대로 잔다 — 지수 백오프는
        같은 분당 창을 두드린다 (NucBrief 박제)."""
        fake = self.use_fake_time()
        attempts = {"n": 0}

        def urlopen(request, timeout=None):
            attempts["n"] += 1
            raise self._http_error(429, '{"retryDelay": "42s"}')

        self._patch_urlopen(urlopen)
        budget = audio_brief.RetryBudget(audio_brief.TTS_RETRY_BUDGET_SEC)
        with self.assertRaises(GeminiError) as ctx:
            self._orig_fns[2]("HOST: 테스트.", models=["m1"], budget=budget,
                              deadline=fake.now + 900)
        self.assertEqual(attempts["n"], audio_brief.TTS_CHUNK_RETRIES)
        self.assertEqual(getattr(ctx.exception, "reason", None), "rate_limit")
        # 서버 지연(42s + 버퍼 1s)이 sleep 에 반영됐는가 (페이서 sleep 제외)
        self.assertIn(43.0, fake.sleeps)

    def test_call_tts_daily_quota_fails_fast(self):
        """일일 한도는 오늘 안 풀린다 — 재시도 없이 즉시 넘긴다."""
        fake = self.use_fake_time()
        attempts = {"n": 0}

        def urlopen(request, timeout=None):
            attempts["n"] += 1
            raise self._http_error(
                429, '{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}')

        self._patch_urlopen(urlopen)
        budget = audio_brief.RetryBudget(audio_brief.TTS_RETRY_BUDGET_SEC)
        with self.assertRaises(GeminiError) as ctx:
            self._orig_fns[2]("HOST: 테스트.", models=["m1"], budget=budget,
                              deadline=fake.now + 900)
        self.assertEqual(attempts["n"], 1)
        self.assertEqual(getattr(ctx.exception, "reason", None), "daily_quota")

    def test_call_tts_stops_when_retry_budget_exhausted(self):
        """예산이 바닥이면 sleep 없이 중단 — 곱발산 방지의 실제 동작."""
        fake = self.use_fake_time()
        attempts = {"n": 0}

        def urlopen(request, timeout=None):
            attempts["n"] += 1
            raise self._http_error(429, '{"retryDelay": "42s"}')

        self._patch_urlopen(urlopen)
        budget = audio_brief.RetryBudget(0)
        with self.assertRaises(GeminiError):
            self._orig_fns[2]("HOST: 테스트.", models=["m1"], budget=budget,
                              deadline=fake.now + 900)
        self.assertEqual(attempts["n"], 1)
        self.assertNotIn(43.0, fake.sleeps)

    def test_call_tts_retry_sleep_clamped_by_deadline(self):
        """retry sleep 은 min(delay, 잔여) 을 먼저 구해 예산에서 차감한다 —
        delay 원값으로 take 하면 예산을 과다 차감한다."""
        fake = self.use_fake_time()

        def urlopen(request, timeout=None):
            raise self._http_error(429, '{"retryDelay": "42s"}')

        self._patch_urlopen(urlopen)
        budget = audio_brief.RetryBudget(audio_brief.TTS_RETRY_BUDGET_SEC)
        with self.assertRaises(GeminiError):
            self._orig_fns[2]("HOST: 테스트.", models=["m1"], budget=budget,
                              deadline=fake.now + 10.0)   # 잔여 10초뿐
        # 서버 지연(43초) 원값으로 잔 적이 없다 — 잔여로 클램프됐다
        self.assertNotIn(43.0, fake.sleeps)
        # 예산도 잔여(≤10초)만큼만 차감됐다 — delay 원값 차감이면 43이 빠진다
        self.assertGreaterEqual(budget.remaining,
                                audio_brief.TTS_RETRY_BUDGET_SEC - 21.0)

    def test_pace_tts_enforces_min_interval(self):
        fake = self.use_fake_time()
        audio_brief._last_tts_at = 0.0
        audio_brief._pace_tts()                 # 첫 호출 — 대기 없음
        first_sleeps = list(fake.sleeps)
        audio_brief._pace_tts()                 # 연속 호출 — 간격 강제
        new_sleeps = fake.sleeps[len(first_sleeps):]
        self.assertTrue(any(s >= audio_brief.TTS_MIN_INTERVAL_SEC - 1e-6
                            for s in new_sleeps))

    # ── generate() 계약 ──────────────────────────────────────

    def test_generate_happy_path_writes_meta_and_script(self):
        self.write_data()
        self.responses = script_responses()
        self.assertTrue(audio_brief.generate())
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["date"], "2026-08-04")
        self.assertEqual(meta["file"], "briefing-2026-08-04.mp3")
        self.assertGreater(meta["duration_sec"], 0)
        self.assertNotIn("partial", meta)               # 정상 실행은 기존 스키마
        self.assertTrue((audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").exists())
        self.assertTrue((audio_brief.AUDIO_DIR / "script-2026-08-04.txt").exists())

    def test_generate_partial_ship_records_reason(self):
        """완성 청크가 있으면 부분 배송 — meta 에 사유·재시도 가능성 기록."""
        self.write_data()
        self.responses = script_responses()
        original = audio_brief.synthesize
        self.addCleanup(setattr, audio_brief, "synthesize", original)
        audio_brief.synthesize = lambda script, deadline=None: (
            b"\x00" * 48000, 24000, 2, 6, "rate_limit")
        self.assertTrue(audio_brief.generate())         # 부분도 성공이다
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertTrue(meta["partial"])
        self.assertEqual(meta["chunks_done"], 2)
        self.assertEqual(meta["chunks_total"], 6)
        self.assertEqual(meta["partial_reason"], "rate_limit")
        self.assertTrue(meta["retryable"])
        self.assertEqual(len(self.sent), 1)             # 부분본도 발송된다

    def test_generate_partial_daily_quota_not_retryable(self):
        self.write_data()
        self.responses = script_responses()
        self.addCleanup(setattr, audio_brief, "synthesize", audio_brief.synthesize)
        audio_brief.synthesize = lambda script, deadline=None: (
            b"\x00" * 48000, 24000, 1, 6, "daily_quota")
        self.assertTrue(audio_brief.generate())
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["partial_reason"], "daily_quota")
        self.assertFalse(meta["retryable"])

    def test_generate_writes_script_before_tts(self):
        """대본 전문은 TTS 실패와 무관하게 남는다 — 부분 오디오의 나머지를
        텍스트가 보완한다."""
        self.write_data()
        self.responses = script_responses()

        def dead(script, deadline=None):
            raise GeminiError("TTS 전멸")

        self.addCleanup(setattr, audio_brief, "synthesize", audio_brief.synthesize)
        audio_brief.synthesize = dead
        self.assertFalse(audio_brief.generate())
        self.assertTrue((audio_brief.AUDIO_DIR / "script-2026-08-04.txt").exists())

    def test_generate_tts_deadline_reserves_ship_time(self):
        """선행 단계(대본 생성)가 오래 걸리면 TTS 몫이 줄어든다 —
        SHIP_RESERVE_SEC 가 선언이 아니라 계산에 들어가는 지점."""
        fake = self.use_fake_time()
        self.write_data()
        start = fake.now
        captured = {}

        def slow_script(briefing, by_id):
            fake.now += 700.0                           # 선행 단계 700초
            return "HOST: 본문입니다. 실제 내용이 들어갑니다."

        def spy_synthesize(script, deadline=None):
            captured["deadline"] = deadline
            return b"\x00" * 48000, 24000, 1, 1, None

        self.addCleanup(setattr, audio_brief, "generate_script",
                        audio_brief.generate_script)
        self.addCleanup(setattr, audio_brief, "synthesize", audio_brief.synthesize)
        audio_brief.generate_script = slow_script
        audio_brief.synthesize = spy_synthesize
        self.assertTrue(audio_brief.generate())
        expected = start + audio_brief.AUDIO_RUN_BUDGET_SEC - audio_brief.SHIP_RESERVE_SEC
        self.assertEqual(captured["deadline"], expected)
        self.assertLess(captured["deadline"],
                        fake.now + audio_brief.TTS_HARD_BUDGET_SEC)

    def test_generate_cleans_previous_dates(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-03.mp3").write_bytes(b"old")
        (audio_brief.AUDIO_DIR / "script-2026-08-03.txt").write_text("old", encoding="utf-8")
        self.responses = script_responses()
        self.assertTrue(audio_brief.generate())
        self.assertFalse((audio_brief.AUDIO_DIR / "briefing-2026-08-03.mp3").exists())
        self.assertFalse((audio_brief.AUDIO_DIR / "script-2026-08-03.txt").exists())

    def test_generate_skips_when_up_to_date(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3",
                                 "telegram_sent_at": "2026-08-04T07:30:00+09:00"})
        self.assertTrue(audio_brief.generate())
        self.assertEqual(self.calls, [])      # Gemini 호출 0
        self.assertEqual(self.tts_calls, [])  # TTS 호출 0
        self.assertEqual(self.sent, [])       # 재발송 0

    # ── 텔레그램 발송 계약 ───────────────────────────────────

    def test_generate_sends_telegram_and_marks_meta(self):
        self.write_data()
        self.responses = script_responses()
        self.assertTrue(audio_brief.generate())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "briefing-2026-08-04.mp3")
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertIn("telegram_sent_at", meta)

    def test_skip_path_recovers_unsent_audio(self):
        """생성은 됐는데 발송 전에 죽은 실행(429 등)을 다음 실행이 회수한다.
        부분본도 같은 경로 — 자동 업그레이드는 하지 않는다 (쿼터 보호)."""
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3",
                                 "partial": True, "chunks_done": 2, "chunks_total": 6})
        self.assertTrue(audio_brief.generate())
        self.assertEqual(len(self.sent), 1)   # 발송만 재시도
        self.assertEqual(self.tts_calls, [])  # TTS 재호출 0 — 업그레이드 안 함
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertIn("telegram_sent_at", meta)

    def test_send_failure_leaves_meta_unmarked(self):
        self.write_data()
        self.responses = script_responses()
        self.send_ok = False
        self.assertTrue(audio_brief.generate())  # 발송 실패는 생성 성공을 못 뒤집는다
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertNotIn("telegram_sent_at", meta)

    def test_send_telegram_audio_skips_without_env(self):
        mp3 = audio_brief.WEB_DATA / "x.mp3"
        mp3.write_bytes(b"mp3")
        original = audio_brief.gemini_client._resolve
        audio_brief.gemini_client._resolve = lambda key, default=None: None
        try:
            self.assertFalse(self._orig_fns[4](mp3, {"date": "2026-08-04"}))
        finally:
            audio_brief.gemini_client._resolve = original

    def test_send_telegram_audio_partial_caption(self):
        """부분본 캡션은 '부분 n/m' 과 사이트 안내를 담는다."""
        mp3 = audio_brief.WEB_DATA / "x.mp3"
        mp3.write_bytes(b"mp3")
        captured = {}

        class FakeResponse:
            ok = True
            status_code = 200

            def json(self):
                return {"ok": True}

        fake_requests = types.SimpleNamespace(
            post=lambda url, data=None, files=None, timeout=None:
                (captured.update(data) or FakeResponse()))
        original_module = sys.modules.get("requests")
        sys.modules["requests"] = fake_requests
        original_resolve = audio_brief.gemini_client._resolve
        audio_brief.gemini_client._resolve = lambda key, default=None: "dummy"
        try:
            ok = self._orig_fns[4](mp3, {
                "date": "2026-08-04", "duration_sec": 200, "partial": True,
                "chunks_done": 2, "chunks_total": 6})
        finally:
            audio_brief.gemini_client._resolve = original_resolve
            if original_module is not None:
                sys.modules["requests"] = original_module
            else:
                sys.modules.pop("requests", None)
        self.assertTrue(ok)
        self.assertIn("부분 2/6", captured["caption"])
        self.assertIn("nuclens.pages.dev", captured["caption"])

    def test_generate_force_regenerates(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3"})
        self.responses = script_responses()
        self.assertTrue(audio_brief.generate(force=True))
        self.assertEqual(len(self.tts_calls), 1)

    def test_generate_force_without_send_replaces_web_audio_only(self):
        """품질 재생성은 텔레그램 중복 발송 없이 웹 음원만 바꾼다."""
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"old")
        audio_brief._write_meta({"date": "2026-08-04",
                                 "file": "briefing-2026-08-04.mp3",
                                 "telegram_sent_at": "2026-08-04T07:30:00+09:00"})
        self.responses = script_responses()
        self.assertTrue(audio_brief.generate(force=True, send=False))
        self.assertEqual(len(self.tts_calls), 1)
        self.assertEqual(self.sent, [])
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertNotIn("telegram_sent_at", meta)

    def test_generate_fail_soft_keeps_existing_audio(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-03.mp3").write_bytes(b"old")
        audio_brief._write_meta({"date": "2026-08-03", "file": "briefing-2026-08-03.mp3"})
        self.responses = []  # call_json 이 GeminiError 를 던진다
        self.assertFalse(audio_brief.generate())
        self.assertTrue((audio_brief.AUDIO_DIR / "briefing-2026-08-03.mp3").exists())
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["date"], "2026-08-03")

    def test_generate_without_briefings_is_noop(self):
        self.assertFalse(audio_brief.generate())
        self.assertEqual(self.calls, [])


class ExitCodeContractTests(unittest.TestCase):
    """실패는 종료 코드로 나가야 한다.

    2026-08-12: 대본이 429 로 굶어 그날 오디오가 통째로 빠졌는데 워크플로는
    success 였다. `sys.exit(0)` 이 무조건이라 `|| echo "실패"` 도, 그 뒤에 붙일
    어떤 재시도도 절대 실행될 수 없는 구조였다. 종료 코드가 진실을 말해야
    워크플로가 재시도를 걸 수 있다.
    """

    def _run(self, env_extra: dict) -> int:
        env = {**os.environ, "GEMINI_API_KEY": "", **env_extra}
        # .env 가 있는 개발 머신에서 키가 되살아나지 않게 임시 디렉터리에서 돈다.
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [sys.executable, str(ROOT / "audio_brief.py")],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=60,
            ).returncode

    def test_no_api_key_exits_nonzero(self):
        self.assertEqual(self._run({}), 1)


if __name__ == "__main__":
    unittest.main()
