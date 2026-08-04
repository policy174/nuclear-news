"""오디오 브리핑 — 그날 브리핑을 2인 대담 MP3로 만든다.

문제: 임직원은 출근길·이동 중에 화면을 못 본다. 아침 브리핑이 텍스트로만
있으면 소비되지 않는 시간대가 있다 (2026-08-04 박제).

해결:
  - daily-brief 배포 스텝에서 build_data.py 직후 실행된다. 방금 빌드된
    briefings.json·issues.json(우리가 생성한 요약·해석 카드)만 재료로 쓴다 —
    기사 원문을 낭독하지 않으므로 저작권 문제가 없다.
  - Gemini 텍스트 모델이 HOST(진행자)·ANALYST(해설위원) 대담 대본을 쓰고,
    Gemini TTS 멀티스피커가 두 목소리로 합성한다. 배속은 여기서 만들지
    않는다 — 웹 플레이어의 playbackRate 가 맡는다 (음원은 1.0x 원본 유지).
  - 산출물은 web/public/data/audio/ (gitignore 안 — Pages 배포에만 실림).
    crawl.yml 짝수시 재배포에서 사라지지 않도록 Actions 캐시로 유지된다
    (embeddings.json 과 같은 패턴).

가드레일:
  - 대본 재료 밖 사실·미래 예측·투자 권고 금지 (daily_lead 와 동일 원칙).
  - 어떤 실패도 배포를 죽이면 안 된다 — main() 은 항상 exit 0.
  - 같은 날짜 재실행은 TTS 를 다시 부르지 않는다 (무료 티어 보호).
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gemini_client import GeminiError, call_json, is_available
import gemini_client

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
WEB_DATA = BASE / "web" / "public" / "data"
AUDIO_DIR = WEB_DATA / "audio"
META_FILE_NAME = "audio.json"
KST = timezone(timedelta(hours=9))

# 승인된 조합 (2026-08-04 샘플 청취 판정: v2=3.1 채택). 앞에서부터 시도한다.
TTS_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]
VOICES = {"HOST": "Kore", "ANALYST": "Charon"}

SPEAKER_RE = re.compile(r"^(HOST|ANALYST):\s*(.+)$")
MIN_LINES = 8          # 이보다 짧으면 대담이 아니라 낭독이다
MAX_SPOKEN = 2600      # 대사 합계 상한 (~4분 30초). TTS 1요청 안전 범위
DEEP_LIMIT = 3         # 대화로 깊게 다룰 이슈 수 (하이라이트)

_TTS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

SYSTEM_PROMPT = """당신은 한수원 임직원용 원자력·에너지 이슈 트래커 'Nuclens'(누클렌즈)의
아침 오디오 브리핑 대본 작가입니다. 아래 [재료]만 사용해 2인 대담 대본을 씁니다.

[형식 — 반드시 준수]
- 화자 2명: HOST(진행자)와 ANALYST(해설위원). 모든 대사는 "HOST: " 또는
  "ANALYST: "로 시작하는 한 줄. 다른 형식의 줄 금지.
- 구성: 짧은 인사(날짜·이슈 개수) → 하이라이트 이슈를 대화로 풀기 →
  나머지 이슈는 HOST가 헤드라인만 빠르게 훑기 → 한 문장 마무리.
- 분량: 대사 합계 1,200~1,500자. 청취 3분 내외.
- 존댓말. HOST는 청취자 눈높이에서 짧게 묻고("그게 왜 중요한 거죠?",
  "쉽게 말하면요?"), ANALYST가 풀어서 답합니다.

[내용 — 반드시 준수]
- 수치·일정·기관명·호기명은 재료 그대로 보존. 재료에 없는 사실 추가 금지.
- 미래 예측·전망·투자 권고 금지. "~할 전망", "~가 유망" 금지.
- 재료 문장을 그대로 읽지 말고 자연스러운 구어체로 재구성. 제목 재진술은
  실패입니다 — HOST가 제목을 말했으면 ANALYST는 배경과 의미를 말해야 합니다.
- 추임새(아, 네, 그렇군요)는 자연스럽게, 남발 금지.

[출력 — JSON 한 객체만]
{"script": "HOST: ...\\nANALYST: ..."}"""

STYLE_INSTRUCTION = (
    "다음은 한국어 아침 뉴스 브리핑 팟캐스트입니다. 밝고 또렷한 라디오 진행 "
    "톤으로, 약간 경쾌한 속도로 읽어주세요:\n\n"
)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def load_briefing(web_data: Path) -> tuple[dict, dict]:
    """최신 브리핑 행 + issue_id→이슈 사전. 없으면 ({}, {})."""
    briefings = _load_json(web_data / "briefings.json") or []
    issues = _load_json(web_data / "issues.json") or []
    rows = [row for row in briefings if isinstance(row, dict) and row.get("date")]
    if not rows:
        return {}, {}
    latest = max(rows, key=lambda row: row["date"])
    by_id = {i["issue_id"]: i for i in issues
             if isinstance(i, dict) and i.get("issue_id")}
    return latest, by_id


def _issue_block(issue: dict, deep: bool) -> str:
    parts = [f"제목: {issue.get('title', '')}",
             f"지역: {issue.get('region', '')}",
             f"요약: {issue.get('summary', '')}"]
    if deep:
        for key, label in (("latest_change", "최근 변화"), ("implication", "해석"),
                           ("why_important", "왜 중요한가")):
            value = issue.get(key)
            if value:
                parts.append(f"{label}: {value}")
    return "\n".join(parts)


def build_material(briefing: dict, by_id: dict) -> str:
    """하이라이트는 깊게, 나머지는 헤드라인만 — 라디오 브리핑 구조."""
    highlight_ids = [h.get("issue_id") for h in briefing.get("highlight_issues", [])
                     if isinstance(h, dict) and h.get("issue_id")][:DEEP_LIMIT]
    listed = [row.get("issue_id") for row in briefing.get("issues", [])
              if isinstance(row, dict) and row.get("issue_id")]
    if not highlight_ids:
        highlight_ids = listed[:DEEP_LIMIT]
    rest_ids = [i for i in listed if i not in highlight_ids]

    deep = [_issue_block(by_id[i], True) for i in highlight_ids if i in by_id]
    rest = [_issue_block(by_id[i], False) for i in rest_ids if i in by_id]
    weekday = "월화수목금토일"[datetime.strptime(briefing["date"], "%Y-%m-%d").weekday()]
    sections = [
        f"[날짜] {briefing['date']} ({weekday}요일 아침)",
        f"[오늘의 헤드라인] {briefing.get('headline', '')}",
        "[하이라이트 이슈 — 대화로 깊게 다룰 것]\n\n" + "\n\n---\n\n".join(deep),
    ]
    if rest:
        sections.append("[그 외 이슈 — 헤드라인 훑기용]\n\n" + "\n\n---\n\n".join(rest))
    return "\n\n".join(sections)


def validate_script(text: str) -> tuple[str, int]:
    """화자 형식 줄만 남긴 대본과 대사 글자 수. 대담이 못 되면 ValueError."""
    lines = []
    spoken = 0
    for raw in str(text or "").splitlines():
        match = SPEAKER_RE.match(raw.strip())
        if match:
            lines.append(f"{match.group(1)}: {match.group(2).strip()}")
            spoken += len(match.group(2).strip())
    if len(lines) < MIN_LINES:
        raise ValueError(f"화자 형식 줄 {len(lines)}개 — 대담 형식 미달")
    speakers = {line.split(":", 1)[0] for line in lines}
    if speakers != {"HOST", "ANALYST"}:
        raise ValueError(f"화자 구성 이상: {sorted(speakers)}")
    return "\n".join(lines), spoken


def generate_script(material: str) -> str:
    """대본 생성 + 재시도 사다리 1단 (daily_lead 패턴).

    thinking_budget=0 필수 — 대담 대본은 사고가 필요 없는 창작 출력인데
    thinking 을 켜 두면 예산(8192)을 thinking 이 먹고 대본이 잘린다
    (2026-08-04 CI 실사고: thoughts=7863, output=315).
    """
    result = call_json(SYSTEM_PROMPT, material, temperature=0.4,
                       max_output_tokens=8192, timeout=120.0, thinking_budget=0)
    try:
        script, spoken = validate_script(result.get("script"))
        if spoken <= MAX_SPOKEN:
            return script
        problem = f"대사 합계 {spoken}자로 상한 {MAX_SPOKEN}자를 넘었습니다"
    except ValueError as exc:
        problem = str(exc)

    retry_message = (
        f"{material}\n\n[재요청] 방금 출력에 문제가 있었습니다: {problem}.\n"
        "형식 규칙(모든 줄이 HOST:/ANALYST:)과 분량(1,200~1,500자)을 지켜 "
        "대본 전체를 다시 쓰세요."
    )
    result = call_json(SYSTEM_PROMPT, retry_message, temperature=0.4,
                       max_output_tokens=8192, timeout=120.0, thinking_budget=0)
    script, spoken = validate_script(result.get("script"))
    if spoken > MAX_SPOKEN:
        raise ValueError(f"재시도 후에도 {spoken}자 — 포기")
    return script


def tts_payload(script: str) -> dict:
    """TTS 요청 본문 — 화자 라벨과 voice 배정은 대본 형식과 맞물려야 한다."""
    return {
        "contents": [{"parts": [{"text": STYLE_INSTRUCTION + script}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                {"speaker": speaker,
                 "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
                for speaker, voice in VOICES.items()
            ]}},
        },
    }


def _tts_models() -> list[str]:
    override = gemini_client._resolve("GEMINI_TTS_MODEL")
    models = list(TTS_MODELS)
    if override:
        models = [override] + [m for m in models if m != override]
    return models


def call_tts(script: str) -> tuple[bytes, int]:
    """멀티스피커 합성 → (PCM s16le, sample rate). 모델 순서대로 폴백."""
    last_err: Exception | None = None
    for model in _tts_models():
        url = _TTS_ENDPOINT.format(model=model)
        request = urllib.request.Request(
            url,
            data=json.dumps(tts_payload(script)).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": gemini_client.API_KEY or ""},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read())
            part = payload["candidates"][0]["content"]["parts"][0]
            mime = part["inlineData"]["mimeType"]
            pcm = base64.b64decode(part["inlineData"]["data"])
            match = re.search(r"rate=(\d+)", mime)
            rate = int(match.group(1)) if match else 24000
            if not pcm:
                raise GeminiError(f"{model}: 오디오 0바이트")
            print(f"[audio] TTS {model} — {len(pcm) / 1024:.0f} KB, rate {rate}")
            return pcm, rate
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            last_err = GeminiError(f"{model}: HTTP {exc.code} {detail}")
            print(f"[audio] {last_err} — 다음 모델 폴백")
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError,
                json.JSONDecodeError) as exc:
            last_err = GeminiError(f"{model}: {type(exc).__name__}: {exc}")
            print(f"[audio] {last_err} — 다음 모델 폴백")
    raise last_err or GeminiError("TTS 모델 전부 실패")


def to_mp3(pcm: bytes, rate: int, out_path: Path) -> None:
    """PCM s16le mono → MP3 64k. ffmpeg 는 GitHub 러너·로컬 모두 존재."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 없음 — mp3 변환 불가")
    raw = out_path.with_suffix(".pcm")
    raw.write_bytes(pcm)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le",
             "-ar", str(rate), "-ac", "1", "-i", str(raw),
             "-b:a", "64k", str(out_path)],
            check=True,
        )
    finally:
        raw.unlink(missing_ok=True)


def _write_meta(meta: dict) -> None:
    """원자적 기록 — 배포 중 잘린 audio.json 이 플레이어를 깨면 안 된다."""
    target = AUDIO_DIR / META_FILE_NAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)


def generate(force: bool = False) -> bool:
    if not is_available():
        print("[audio] GEMINI_API_KEY 없음 — 스킵")
        return False
    briefing, by_id = load_briefing(WEB_DATA)
    if not briefing:
        print("[audio] briefings.json 없음/비어 있음 — build_data 이후에 실행돼야 한다")
        return False
    date = briefing["date"]
    file_name = f"briefing-{date}.mp3"
    mp3_path = AUDIO_DIR / file_name

    existing = _load_json(AUDIO_DIR / META_FILE_NAME) or {}
    if not force and existing.get("date") == date and mp3_path.exists():
        print(f"[audio] {date} 이미 생성됨 ({file_name}) — 스킵")
        return True

    material = build_material(briefing, by_id)
    if "제목:" not in material:
        print("[audio] 재료에 이슈가 없음 — 스킵")
        return False

    try:
        script = generate_script(material)
    except (GeminiError, ValueError) as exc:
        print(f"[audio] 대본 실패 — 기존 오디오 유지: {exc}")
        return False

    try:
        pcm, rate = call_tts(script)
    except GeminiError as exc:
        print(f"[audio] TTS 실패 — 기존 오디오 유지: {exc}")
        return False

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        to_mp3(pcm, rate, mp3_path)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[audio] mp3 변환 실패 — 기존 오디오 유지: {exc}")
        return False

    duration = int(len(pcm) / 2 / rate)
    _write_meta({
        "date": date,
        "file": file_name,
        "duration_sec": duration,
        "generated_at": datetime.now(KST).isoformat(),
        "script_chars": sum(len(line.split(":", 1)[1]) for line in script.splitlines()),
        "voices": VOICES,
    })
    # 대본을 함께 남긴다 — 프롬프트 적중 여부를 라이브 산출물로 검증하는
    # 진단 요령(issue_audit.json 패턴). 화면은 이 파일을 쓰지 않는다.
    (AUDIO_DIR / f"script-{date}.txt").write_text(script, encoding="utf-8")
    # 옛 날짜 산출물 정리 — 캐시·배포에 실리는 것은 최신 1개면 충분하다
    for old in AUDIO_DIR.glob("briefing-*.mp3"):
        if old.name != file_name:
            old.unlink(missing_ok=True)
    for old in AUDIO_DIR.glob("script-*.txt"):
        if old.name != f"script-{date}.txt":
            old.unlink(missing_ok=True)
    print(f"[audio] {date} 완료 — {file_name} "
          f"({mp3_path.stat().st_size / 1024:.0f} KB, {duration}초)")
    return True


if __name__ == "__main__":
    # 어떤 실패도 배포를 죽이면 안 된다 — 오디오는 부가 기능이다.
    try:
        generate(force="--force" in sys.argv)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[audio] 예상 밖 실패 — 비치명 처리: {exc}")
    sys.exit(0)
