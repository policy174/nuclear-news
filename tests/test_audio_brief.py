"""audio_brief.py 단위 테스트 — 대담 형식 게이트·비치명 계약·중복 생성 방지.

핵심 계약 3개:
  ① 대본은 반드시 HOST/ANALYST 2인 대담 — 형식 미달이면 TTS 를 부르지 않는다.
  ② 어떤 실패도 기존 오디오를 지우지 않는다 (배포마다 캐시로 돌아오는 파일).
  ③ 같은 날짜 재실행은 Gemini 를 다시 부르지 않는다 (무료 티어 보호).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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


GOOD_SCRIPT = "\n".join(
    [f"HOST: 질문 {i}입니다. 그게 왜 중요한 거죠?" if i % 2 == 0
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
                          audio_brief.call_tts, audio_brief.to_mp3)
        self.addCleanup(self._restore)
        self.calls = []
        self.responses = []
        self.tts_calls = []
        audio_brief.is_available = lambda: True
        audio_brief.call_json = self._fake_call
        audio_brief.call_tts = self._fake_tts
        audio_brief.to_mp3 = self._fake_mp3

    def _restore(self):
        audio_brief.WEB_DATA, audio_brief.AUDIO_DIR = self._orig
        (audio_brief.is_available, audio_brief.call_json,
         audio_brief.call_tts, audio_brief.to_mp3) = self._orig_fns

    def _fake_call(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if not self.responses:
            raise GeminiError("429")
        return self.responses.pop(0)

    def _fake_tts(self, script):
        self.tts_calls.append(script)
        return b"\x00" * 48000, 24000  # 1초 분량 PCM

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

    def test_validate_script_rejects_monologue(self):
        mono = "\n".join(f"HOST: 문장 {i}입니다." for i in range(10))
        with self.assertRaises(ValueError):
            audio_brief.validate_script(mono)

    def test_validate_script_rejects_too_few_lines(self):
        with self.assertRaises(ValueError):
            audio_brief.validate_script("HOST: 안녕하세요.\nANALYST: 네.")

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

    def test_tts_payload_speakers_match_script_labels(self):
        payload = audio_brief.tts_payload("HOST: 안녕하세요.\nANALYST: 네.")
        config = payload["generationConfig"]
        self.assertEqual(config["responseModalities"], ["AUDIO"])
        speakers = {entry["speaker"] for entry in
                    config["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]}
        self.assertEqual(speakers, {"HOST", "ANALYST"})
        self.assertEqual(speakers, set(audio_brief.VOICES))

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
        self.assertEqual(meta["duration_sec"], 1)
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
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3"})
        self.assertTrue(audio_brief.generate())
        self.assertEqual(self.calls, [])      # Gemini 호출 0
        self.assertEqual(self.tts_calls, [])  # TTS 호출 0

    def test_generate_force_regenerates(self):
        self.write_data()
        audio_brief.AUDIO_DIR.mkdir(parents=True)
        (audio_brief.AUDIO_DIR / "briefing-2026-08-04.mp3").write_bytes(b"mp3")
        audio_brief._write_meta({"date": "2026-08-04", "file": "briefing-2026-08-04.mp3"})
        self.responses = [{"script": GOOD_SCRIPT}]
        self.assertTrue(audio_brief.generate(force=True))
        self.assertEqual(len(self.tts_calls), 1)

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


if __name__ == "__main__":
    unittest.main()
