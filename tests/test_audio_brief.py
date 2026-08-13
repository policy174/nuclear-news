"""audio_brief.py 단위 테스트 — 대담 형식 게이트·비치명 계약·중복 생성 방지.

핵심 계약 4개:
  ① 대본은 반드시 HOST/ANALYST 2인 대담 — 형식 미달이면 TTS 를 부르지 않는다.
  ② 어떤 실패도 기존 오디오를 지우지 않는다 (배포마다 캐시로 돌아오는 파일).
  ③ 같은 날짜 재실행은 Gemini 를 다시 부르지 않는다 (무료 티어 보호).
  ④ 실패는 종료 코드로 나간다 — 비치명 처리는 호출자(워크플로) 몫이다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
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


# 두 줄씩 블록 교대(10줄 = 5턴) — 탁구식 픽스처는 턴 게이트에 걸린다.
GOOD_SCRIPT = "\n".join(
    [f"HOST: 질문 {i}입니다. 그게 왜 중요한 거죠?" if (i // 2) % 2 == 0
     else f"ANALYST: 핵심은 이렇습니다. 2033년 준공 목표가 확정됐다는 점이죠. ({i})"
     for i in range(10)]
)


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

    def _fake_tts(self, script, models=None):
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

    # ── 재료 조립 ─────────────────────────────────────────────

    def test_material_deep_for_highlights_shallow_for_rest(self):
        self.write_data()
        briefing, by_id = audio_brief.load_briefing(audio_brief.WEB_DATA)
        material = audio_brief.build_material(briefing, by_id)
        deep_part = material.split("[그 외 이슈")[0]
        rest_part = material.split("[그 외 이슈")[1]
        self.assertIn("왜 중요한가", deep_part)
        self.assertIn("해석", deep_part)
        self.assertNotIn("왜 중요한가", rest_part)
        self.assertIn("중국 원자로 승인", rest_part)

    def test_load_briefing_picks_latest_date(self):
        rows = [briefing_row("2026-08-03"), briefing_row("2026-08-04")]
        (audio_brief.WEB_DATA / "briefings.json").write_text(
            json.dumps(rows), encoding="utf-8")
        (audio_brief.WEB_DATA / "issues.json").write_text("[]", encoding="utf-8")
        briefing, _ = audio_brief.load_briefing(audio_brief.WEB_DATA)
        self.assertEqual(briefing["date"], "2026-08-04")

    # ── 대본 검증 게이트 ──────────────────────────────────────

    def test_validate_script_keeps_only_speaker_lines(self):
        noisy = "## 대본\n" + GOOD_SCRIPT + "\n(끝)"
        script, spoken = audio_brief.validate_script(noisy)
        self.assertTrue(all(line.startswith(("HOST:", "ANALYST:"))
                            for line in script.splitlines()))
        self.assertEqual(len(script.splitlines()), 10)
        self.assertGreater(spoken, 0)

    def test_validate_script_strips_leading_fillers(self):
        """2026-08-10 대본 26줄 중 13줄이 '네,'로 시작했다. 프롬프트의
        '남발 금지'로는 안 됐으므로 코드가 자른다."""
        noisy = "\n".join([
            "HOST: 안녕하십니까? 오늘 브리핑을 시작합니다.",
            "ANALYST: 네, 오늘 이슈는 세 가지입니다.",
            "HOST: 그렇군요. 첫 소식부터 보겠습니다.",
            "ANALYST: 아, 네, 원안위가 오늘 발표했습니다.",
            "HOST: 맞습니다! 그 부분이 핵심입니다.",
            "ANALYST: 예. 계속운전 심사가 하반기로 잡혔습니다.",
            "HOST: 다음 소식입니다.",
            "ANALYST: 네트워크 투자도 함께 발표됐습니다.",
        ])
        script, _ = audio_brief.validate_script(noisy)
        bodies = [line.split(": ", 1)[1] for line in script.splitlines()]
        self.assertEqual(bodies[1], "오늘 이슈는 세 가지입니다.")
        self.assertEqual(bodies[2], "첫 소식부터 보겠습니다.")
        self.assertEqual(bodies[3], "원안위가 오늘 발표했습니다.")
        self.assertEqual(bodies[4], "그 부분이 핵심입니다.")
        self.assertEqual(bodies[5], "계속운전 심사가 하반기로 잡혔습니다.")
        # 낱말 첫머리는 건드리지 않는다 — '네트워크'·'예산'
        self.assertEqual(bodies[7], "네트워크 투자도 함께 발표됐습니다.")

    def test_strip_filler_keeps_line_that_is_only_filler(self):
        """뗄 내용이 없으면 빈 대사가 된다 — 그럴 땐 그대로 둔다."""
        self.assertEqual(audio_brief.strip_filler("네."), "네.")
        self.assertEqual(audio_brief.strip_filler("그렇군요."), "그렇군요.")

    def test_frame_is_deterministic_and_model_frame_lines_dropped(self):
        """오프닝·클로징은 코드가 붙인다(hourlynews 패턴) — 인사를 생성에
        맡기니 날마다 정보 0짜리 인사 두 줄이 붙었다. 모델이 그래도 쓴
        인사·마무리 줄은 중복이라 걷어낸다."""
        briefing = briefing_row()
        body = "\n".join([
            "HOST: 안녕하십니까? 브리핑을 시작하겠습니다.",
            "ANALYST: 원안위가 오늘 심사 결과를 발표했습니다.",
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

    def test_material_spoken_target_scales_with_issue_count(self):
        """분량 목표는 이슈 수에 비례한다 — 고정 목표는 많은 날을 뚫고
        적은 날을 부풀렸다."""
        lo_small, hi_small = audio_brief.spoken_target(3, 5)
        lo_big, hi_big = audio_brief.spoken_target(3, 15)
        self.assertLess(hi_small, hi_big)
        self.assertLessEqual(hi_big, audio_brief.MAX_SPOKEN - 200)
        self.write_data()
        briefing, by_id = audio_brief.load_briefing(audio_brief.WEB_DATA)
        self.assertIn("[분량]", audio_brief.build_material(briefing, by_id))

    
    
    
    
    def test_validate_script_absorbs_analyst_lines(self):
        """1인 진행 전환 후 모델이 옛 대담 형식으로 회귀해도 내용을 살린다."""
        legacy = "\n".join(
            [f"HOST: 사실 {i}입니다." if i % 2 == 0 else f"ANALYST: 해설 {i}입니다."
             for i in range(10)])
        script, _ = audio_brief.validate_script(legacy)
        self.assertTrue(all(line.startswith("HOST: ") for line in script.splitlines()))
        self.assertIn("해설 1입니다", script)

    def test_validate_script_rejects_too_few_lines(self):
        with self.assertRaises(ValueError):
            audio_brief.validate_script("HOST: 안녕하세요.\nANALYST: 네.")

    def test_generate_script_disables_thinking(self):
        """thinking 토큰이 출력 예산을 잠식해 대본이 잘린 CI 실사고(2026-08-04,
        thoughts=7863/8192) 재발 방지 — 대본 호출은 반드시 thinking_budget=0."""
        self.responses = [{"script": GOOD_SCRIPT}]
        audio_brief.generate_script("재료")
        self.assertEqual(self.call_kwargs[0].get("thinking_budget"), 0)

    def test_generate_script_uses_isolated_quota_bucket(self):
        """대본은 기본 MODEL(크롤·브리핑 체인 공용 버킷)이 아니라 별도 모델
        버킷을 쓴다 — 공용 버킷은 저녁이면 고갈돼 3연속 429 실사고(2026-08-04)."""
        self.responses = [{"script": GOOD_SCRIPT}]
        audio_brief.generate_script("재료")
        self.assertEqual(self.call_kwargs[0].get("model"),
                         audio_brief.SCRIPT_MODEL_DEFAULT)

    def test_generate_script_falls_back_to_shared_bucket_on_429(self):
        """2026-08-10: 전용 버킷(flash-lite)이 분당 한도에 걸려 대본이 죽고
        그날 오디오만 조용히 빠졌다. 버티는 것만으로 안 되면 버킷을 옮긴다."""
        first = {"n": 0}

        def flaky(system_prompt, user_message, **kwargs):
            self.call_kwargs.append(kwargs)
            if kwargs.get("model") == audio_brief.SCRIPT_MODEL_DEFAULT:
                first["n"] += 1
                raise GeminiError("HTTP 429: limit 20")
            return {"script": GOOD_SCRIPT}

        audio_brief.call_json = flaky
        script = audio_brief.generate_script("재료")
        self.assertIn("HOST:", script)
        self.assertEqual(first["n"], 1)
        self.assertEqual(self.call_kwargs[-1]["model"], audio_brief.gemini_client.MODEL)

    def test_generate_script_waits_longer_than_default(self):
        """대본은 하루 1회·마지막 스텝이라 느려도 된다 — 기본 재시도로는
        분당 한도 창을 못 넘긴 실사고가 있었다."""
        self.responses = [{"script": GOOD_SCRIPT}]
        audio_brief.generate_script("재료")
        self.assertEqual(self.call_kwargs[0].get("retries"),
                         audio_brief.SCRIPT_RETRIES)
        self.assertGreater(audio_brief.SCRIPT_RETRIES, 3)

    def test_generate_script_retries_once_on_bad_format(self):
        self.responses = [{"script": "그냥 낭독문입니다."}, {"script": GOOD_SCRIPT}]
        script = audio_brief.generate_script("재료")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("[재요청]", self.calls[1])
        self.assertIn("HOST:", script)

    # ── 프롬프트 회귀 (c82a09f 게토차: 예시의 빈 값은 그대로 배껴진다) ──

    def test_prompt_output_example_does_not_prime_empty_values(self):
        example = audio_brief.SYSTEM_PROMPT.split("[출력")[-1]
        for poison in ('""', "null", "unknown", "N/A"):
            self.assertNotIn(poison, example)
        self.assertIn("...", example)

    # ── TTS 계약 ─────────────────────────────────────────────

    
    def test_split_script_chunks_at_speaker_lines(self):
        """긴 대본은 여러 요청으로 나눈다 — 4분을 1요청으로 뽑으면 뒤쪽이
        먹고 작아진다(2026-08-08 실측: 마지막 30초 -40.2 dB vs 첫 30초 -17.6)."""
        long_script = "\n".join(
            [f"HOST: {'가' * 200} {i}" if i % 2 == 0 else f"ANALYST: {'나' * 200} {i}"
             for i in range(10)])
        chunks = audio_brief.split_script(long_script)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(chunks).splitlines(), long_script.splitlines())
        for chunk in chunks:
            self.assertTrue(all(audio_brief.SPEAKER_RE.match(line)
                                for line in chunk.splitlines()))
        self.assertTrue(all(
            sum(len(audio_brief.SPEAKER_RE.match(line).group(2))
                for line in chunk.splitlines()) <= audio_brief.CHUNK_SPOKEN
            for chunk in chunks[:-1]))

    def test_split_script_keeps_short_script_whole(self):
        self.assertEqual(audio_brief.split_script(GOOD_SCRIPT), [GOOD_SCRIPT])

    def test_tts_payload_single_speaker_chunk_drops_labels(self):
        """끝부분 헤드라인 훑기는 HOST 단독 청크가 된다. 멀티스피커 모드가
        아니면 'HOST:' 라벨을 그대로 읽어버리므로 접두어를 떼고 보낸다."""
        payload = audio_brief.tts_payload("HOST: 첫 소식입니다.\nHOST: 다음 소식입니다.")
        speech = payload["generationConfig"]["speechConfig"]
        self.assertNotIn("multiSpeakerVoiceConfig", speech)
        self.assertEqual(
            speech["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
            audio_brief.VOICES["HOST"])
        self.assertNotIn("HOST:", payload["contents"][0]["parts"][0]["text"])

    def test_synthesize_concatenates_chunks(self):
        long_script = "\n".join(
            [f"HOST: {'가' * 200} {i}" if i % 2 == 0 else f"ANALYST: {'나' * 200} {i}"
             for i in range(10)])
        pcm, rate = audio_brief.synthesize(long_script)
        chunks = audio_brief.split_script(long_script)
        self.assertEqual(len(self.tts_calls), len(chunks))
        self.assertEqual(rate, 24000)
        gap = int(rate * audio_brief.CHUNK_GAP_SEC) * 2
        self.assertEqual(len(pcm),
                         sum(len(fake_pcm(c)) for c in chunks) + (len(chunks) - 1) * gap)

    def test_synthesize_pins_one_model_for_whole_script(self):
        """모델이 다르면 음색이 다르다 — 청크마다 폴백을 따로 태우면 한 파일
        안에서 화자가 바뀐다. 실패하면 다음 모델로 **처음부터** 다시 만든다."""
        chunks = len(audio_brief.split_script(LONG_SCRIPT))
        first, second = audio_brief._tts_models()[:2]
        seen = []

        def flaky(chunk, models=None):
            model = (models or [])[0]
            seen.append(model)
            if model == first:
                raise GeminiError(f"{model}: HTTP 429")
            return fake_pcm(chunk), 24000

        audio_brief.call_tts = flaky
        pcm, rate = audio_brief.synthesize(LONG_SCRIPT)
        self.assertEqual(seen[0], first)            # 1번 모델에서 시작
        self.assertEqual(seen.count(first), 1)      # 실패 즉시 접는다
        self.assertEqual(seen.count(second), chunks)  # 처음부터 전부 다시
        self.assertEqual(len(set(seen[1:])), 1)     # 한 대본 = 한 모델

    def test_synthesize_detects_silent_truncation(self):
        """Gemini TTS 는 긴 요청을 오류 없이 잘라 돌려준다. 대사 길이 대비
        음원이 너무 짧으면 모델 실패로 취급한다."""
        long_script = "\n".join(
            [f"HOST: {'가' * 200} {i}" if i % 2 == 0 else f"ANALYST: {'나' * 200} {i}"
             for i in range(10)])
        audio_brief.call_tts = lambda chunk, models=None: (b"\x00" * 48000, 24000)
        with self.assertRaises(GeminiError) as ctx:
            audio_brief.synthesize(long_script)   # 대사 900자에 1초짜리 음원
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

        def padded(chunk, models=None):
            return pad + body + pad, rate

        audio_brief.call_tts = padded
        self.addCleanup(setattr, audio_brief, "_check_not_truncated",
                        audio_brief._check_not_truncated)
        audio_brief._check_not_truncated = lambda *a, **k: None
        pcm, _ = audio_brief.synthesize(LONG_SCRIPT)
        chunks = len(audio_brief.split_script(LONG_SCRIPT))
        gap = int(rate * audio_brief.CHUNK_GAP_SEC) * 2
        self.assertEqual(len(pcm), chunks * len(body) + (chunks - 1) * gap)

    def test_synthesize_rejects_rate_mismatch(self):
        """레이트가 섞인 채 이어붙이면 뒷부분이 배속으로 재생된다."""
        seen = {"n": 0}

        def mixed_rate(chunk, models=None):
            seen["n"] += 1
            rate = 24000 if seen["n"] % 3 == 1 else 16000
            return fake_pcm(chunk, rate=rate, factor=1.2), rate

        audio_brief.call_tts = mixed_rate
        with self.assertRaises(GeminiError) as ctx:
            audio_brief.synthesize(LONG_SCRIPT)
        self.assertIn("샘플레이트", str(ctx.exception))

    def test_tts_model_env_override_goes_first(self):
        os.environ["GEMINI_TTS_MODEL"] = "gemini-test-tts"
        self.addCleanup(os.environ.pop, "GEMINI_TTS_MODEL", None)
        models = audio_brief._tts_models()
        self.assertEqual(models[0], "gemini-test-tts")
        self.assertEqual(models[1:], audio_brief.TTS_MODELS)

    # ── generate() 계약 ──────────────────────────────────────

    def test_generate_happy_path_writes_meta_and_script(self):
        self.write_data()
        self.responses = [{"script": GOOD_SCRIPT}]
        self.assertTrue(audio_brief.generate())
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(meta["date"], "2026-08-04")
        self.assertEqual(meta["file"], "briefing-2026-08-04.mp3")
        self.assertGreater(meta["duration_sec"], 0)
        self.assertTrue((audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").exists())
        self.assertTrue((audio_brief.AUDIO_DIR / "script-2026-08-04.txt").exists())

    def test_generate_cleans_previous_dates(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-03.mp3").write_bytes(b"old")
        (audio_brief.AUDIO_DIR / "script-2026-08-03.txt").write_text("old", encoding="utf-8")
        self.responses = [{"script": GOOD_SCRIPT}]
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
        self.responses = [{"script": GOOD_SCRIPT}]
        self.assertTrue(audio_brief.generate())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "briefing-2026-08-04.mp3")
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertIn("telegram_sent_at", meta)

    def test_skip_path_recovers_unsent_audio(self):
        """생성은 됐는데 발송 전에 죽은 실행(429 등)을 다음 실행이 회수한다."""
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3"})
        self.assertTrue(audio_brief.generate())
        self.assertEqual(len(self.sent), 1)   # 발송만 재시도
        self.assertEqual(self.tts_calls, [])  # TTS 재호출 0
        meta = json.loads((audio_brief.AUDIO_DIR / "audio.json")
                          .read_text(encoding="utf-8"))
        self.assertIn("telegram_sent_at", meta)

    def test_send_failure_leaves_meta_unmarked(self):
        self.write_data()
        self.responses = [{"script": GOOD_SCRIPT}]
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

    def test_generate_force_regenerates(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3"})
        self.responses = [{"script": GOOD_SCRIPT}]
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
        self.responses = [{"script": GOOD_SCRIPT}]
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
