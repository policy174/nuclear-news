"""오디오 브리핑 — 그날 브리핑을 단일 진행자 MP3로 만든다.

2인 대담은 2026-08-13 폐기. 화자 교대가 프롬프트(2회)·코드 게이트로도
자연스러워지지 않았고, 청취 판정이 "대화도 아니고 사람만 계속 바뀐다"였다.
hourlynews 와 같은 앵커 1인 구조로 전환 — 라디오 시간별 뉴스가 이 형식이다.

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

import array
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
# 1인 진행 전환 후에도 dict 를 유지한다 — audio.json 의 voices 필드 모양을
# 웹 플레이어가 보고 있을 수 있고, 진행자 목소리 교체가 값 하나 수정이 된다.
VOICES = {"HOST": "Kore"}

# 대본 생성은 기본 MODEL(2.5-flash)이 아니라 별도 무료 버킷을 쓴다 — 크롤
# 큐레이션·브리핑 체인이 쓰는 버킷은 저녁이면 고갈돼 하루 1회짜리 이 호출이
# 3연속 429 로 굶었다(2026-08-04 실측: 같은 시각 단독 프로브는 성공).
SCRIPT_MODEL_DEFAULT = "gemini-2.5-flash-lite"
SCRIPT_RETRIES = 6     # 기본 3(≈2분)으로는 2026-08-10 분당 한도 창을 못 넘겼다


def _script_model() -> str:
    return gemini_client._resolve("GEMINI_SCRIPT_MODEL", SCRIPT_MODEL_DEFAULT)

SPEAKER_RE = re.compile(r"^(HOST|ANALYST):\s*(.+)$")
# 줄머리 추임새. '네'·'예'는 뒤에 구두점이 붙은 것만 잡는다 — '네트워크',
# '예산' 같은 낱말을 자르면 안 된다.
_FILLER_RE = re.compile(
    r"^(?:아,\s*)?(?:네|예|그렇군요|그렇죠|맞습니다|알겠습니다)\s*[,.!]\s*")
MIN_LINES = 6          # 잘린 출력·한 덩어리 출력을 잡는 하한 (줄 = 문단)
MAX_SPOKEN = 1500      # 대사 합계 상한 (실측 기준 약 3분 40초)
DEEP_LIMIT = 3         # 대화로 깊게 다룰 이슈 수 (하이라이트)
REST_LIMIT = 6         # 단신은 최대 6건 — 전체 이슈 낭독을 막는다
CHUNK_SPOKEN = 900     # TTS 1요청에 넣을 대사 글자 수 (~90초). 아래 주석 참조
CHUNK_GAP_SEC = 0.45   # 청크 사이 간격. 문장 사이 자연 무음(0.5~0.7초)에 맞춘다
SILENCE_LEVEL = 300    # s16 진폭 — 이보다 작으면 무음으로 본다 (약 -41 dBFS)
TRIM_FRAME_MS = 10
# 잘림 감지용. 실측 8.5자/초 근처(08-10: 대사 1910자 / 257초 = 7.4)라 넉넉히
# 잡고, 기대치의 이만큼도 안 되면 잘린 것으로 본다.
SPOKEN_CHARS_PER_SEC = 8.5
TRUNCATION_RATIO = 0.6

_TTS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

SYSTEM_PROMPT = """당신은 한수원 임직원용 원자력·에너지 이슈 트래커 'Nuclens'(누클렌즈)의
아침 오디오 브리핑 원고 작가입니다. 진행자 한 명이 읽는 라디오 뉴스 원고를
아래 [재료]만 사용해 씁니다.

[형식 — 반드시 준수]
- 진행자는 한 명입니다. 모든 줄은 "HOST: "로 시작하는 한 문단입니다
  (라벨은 시스템 형식용이고 방송에서 읽히지 않습니다). 다른 형식의 줄 금지.
- 줄 하나 = 이슈 하나(또는 헤드라인 묶음 하나). 한 줄은 2~5문장.
- 구성: 하이라이트 3건을 하나씩 풀기 → 나머지 이슈는 최대 6건만 한 문장씩
  헤드라인 훑기. 인사·자기소개·마무리 문장을 쓰지 마세요 — 오프닝과
  클로징은 시스템이 따로 붙입니다. 첫 줄부터 바로 첫 이슈입니다.
- 하이라이트 이슈 한 건의 흐름: 무슨 일이 있었는지 → 지금 어느 단계인지.
  자료에 구체적인 다음 일정·판단 기준이 있을 때만 의미를 덧붙입니다.
- 분량: [재료]의 [분량] 지시를 따릅니다.

[말투 — 낭독용 구어체]
- 존댓말. 다만 문어체 낭독이 아니라 사람이 말하는 문장으로.
- 종결어미를 다양하게. 모든 문장이 "-습니다"로 끝나면 통신문 낭독이
  됩니다. "-는데요", "-고요", "-거든요" 같은 연결 종결을 자연스럽게 섞고,
  같은 종결어미를 세 문장 연속 쓰지 마세요.
- 이슈 사이 전환은 한 마디로 부드럽게: "다음은 해외 소식인데요",
  "국내로 돌아오면" 같은 이정표는 환영입니다 (일반론 문장이 아닙니다).
- 명사 나열식 문장 금지. "보고회 개최 및 육성 방안 발표가 있었습니다"가
  아니라 "보고회를 열고 육성 방안을 발표했습니다"로.

[내용 — 반드시 준수]
- 수치·일정·기관명·호기명은 재료 그대로 보존. 재료에 없는 사실 추가 금지.
- 미래 예측·전망·투자 권고 금지. "~할 전망", "~가 유망" 금지.
- 사업단계를 혼용하지 마세요. 발표·협의·후보선정·부지허가·건설허가·착공·
  최초 콘크리트·상업운전은 서로 다른 단계입니다. 원전 사건도 자동정지·
  수동정지·예방정지·출력감발을 구분합니다.
- 제목을 읽고 끝내지 마세요. 그 제목이 말하는 사건이 무엇인지, 듣는 사람이
  처음 듣는다고 생각하고 풀어 말합니다.
- 어떤 이슈에도 붙일 수 있는 일반론 문장 금지 — "매우 중요합니다",
  "기대됩니다", "의지를 보여줍니다", "귀추가 주목됩니다" 같은 문장은
  삭제 대상입니다. 그 문장을 지워도 정보가 줄지 않으면 쓰지 마세요.
- 약어는 첫 등장에만 "소형모듈원자로, 에스엠알"처럼 풀고 이후에는
  "에스엠알"로만 말합니다. 같은 명칭을 연달아 두 번 읽지 마세요.

[출력 — JSON 한 객체만]
{"script": "HOST: ...\\nHOST: ..."}"""

# 낭독 지시. 청취자는 출근길의 한수원 임직원이고 듣는 목적이 정보라 또렷함이
# 우선이지만, '차분·또렷'만 남기니 통신문 낭독이 됐다(2026-08-13 청취 판정:
# 너무 딱딱함). 정보의 정확함은 대본이 지키고, 목소리는 사람답게 간다.
STYLE_INSTRUCTION = (
    "다음은 진행자 한 명이 전하는 한국어 아침 원자력·에너지 브리핑입니다. "
    "신뢰감 있는 아침 라디오 진행처럼 자연스럽고 부드럽게, 서두르지 않되 "
    "생기 있게 말합니다. 수치·기관명·호기명·날짜는 분명하게 발음하고, "
    "과장된 감탄이나 웃음은 넣지 않습니다. "
    "대본을 요약하거나 바꾸지 말고 그대로 읽어주세요:\n\n"
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
    if deep and issue.get("latest_change"):
        # 기존 implication/why_important에는 기사와 무관한 일반론이 섞인 이력이 있다.
        # 오디오는 원문을 다시 확인할 수 없으므로 사실 필드와 최근 변화만 사용한다.
        parts.append(f"최근 변화: {issue['latest_change']}")
    return "\n".join(parts)


def _issue_ids(briefing: dict) -> tuple[list, list]:
    """(하이라이트 id, 나머지 id) — 재료 조립과 턴 상한이 같은 셈을 쓴다."""
    highlight_ids = [h.get("issue_id") for h in briefing.get("highlight_issues", [])
                     if isinstance(h, dict) and h.get("issue_id")][:DEEP_LIMIT]
    listed = [row.get("issue_id") for row in briefing.get("issues", [])
              if isinstance(row, dict) and row.get("issue_id")]
    if not highlight_ids:
        highlight_ids = listed[:DEEP_LIMIT]
    return highlight_ids, [i for i in listed if i not in highlight_ids][:REST_LIMIT]


def build_material(briefing: dict, by_id: dict) -> str:
    """하이라이트는 깊게, 나머지는 헤드라인만 — 라디오 브리핑 구조."""
    highlight_ids, rest_ids = _issue_ids(briefing)
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
    # 분량은 그날 이슈 수에 비례한다 — 고정 1,200~1,500자는 이슈 8건 날과
    # 18건 날을 같은 틀에 밀어 넣어, 많은 날은 목표를 뚫고(실측 1,952자)
    # 적은 날은 부풀렸다. 하한은 대담 성립선, 상한은 MAX_SPOKEN 안쪽.
    low, high = spoken_target(len(deep), len(rest))
    sections.append(f"[분량] 대사 합계 {low:,}~{high:,}자.")
    return "\n\n".join(sections)


def spoken_target(deep_count: int, rest_count: int) -> tuple[int, int]:
    """(하한, 상한) 대사 글자 수. 실측 6.7자/초 기준 약 3분을 겨냥한다."""
    high = 200 + 270 * deep_count + 45 * min(rest_count, REST_LIMIT)
    high = max(1050, min(high, MAX_SPOKEN - 100))
    return max(900, high - 250), high


# 모델이 그래도 써넣은 인사·마무리 줄을 골라내는 패턴. 프레임은 코드가 붙이므로
# 대본 쪽 것은 중복이다.
_FRAME_LINE_RE = re.compile(
    r"안녕하십니까|안녕하세요|브리핑입니다|브리핑을 시작|여기까지입니다"
    r"|마치겠습니다|감사합니다|청취해 주셔서|함께해 주셔서")


def frame_lines(briefing: dict) -> tuple[str, str]:
    """오프닝·클로징 대사 — LLM 이 아니라 코드가 만든다 (hourlynews 패턴).

    인사말은 매일 같은 문장이어야 하는 고정 프레임인데, 이걸 생성에 맡기니
    날마다 인사 두 줄(정보 0)이 붙거나 예고 문장이 늘어졌다. hourlynews 는
    인트로·아웃트로를 config 고정 문자열로 붙이고 LLM 은 본문만 쓴다 — 같은
    구조로 간다.

    오프닝은 날짜뿐이다. 처음엔 "오늘의 핵심은 '헤드라인'입니다"로 헤드라인을
    접붙였는데, 헤드라인은 출처 꼬리표·중첩 따옴표가 붙는 개조식이라 낭독하면
    "…개최 (산업부) 입니다"가 됐다(2026-08-13 실사고). 핵심 이슈는 어차피
    본문 첫 줄이 완결 문장으로 시작하므로 프레임이 앞지를 이유가 없다.
    """
    date = datetime.strptime(briefing["date"], "%Y-%m-%d")
    weekday = "월화수목금토일"[date.weekday()]
    opening = f"{date.month}월 {date.day}일 {weekday}요일 Nuclens 오디오 브리핑입니다."
    return f"HOST: {opening}", "HOST: 오늘 브리핑은 여기까지입니다."


def apply_frame(script: str, briefing: dict) -> str:
    """본문 앞뒤에 고정 프레임을 붙이고, 모델이 쓴 인사·마무리 줄은 걷어낸다."""
    opening, closing = frame_lines(briefing)
    body = [line for line in script.splitlines()
            if not _FRAME_LINE_RE.search(line.split(":", 1)[1])]
    return "\n".join([opening, *body, closing])


def strip_filler(text: str) -> str:
    """줄머리 추임새를 뗀다 — 대담체를 살리라고 열어 뒀더니 남발됐다.

    2026-08-10 대본 26줄 중 13줄이 추임새로 시작했고 '네,' 만 12번이었다
    (08-08 은 17줄 중 4줄). 프롬프트에 '남발 금지'는 이미 있었고 지켜지지
    않았다 — 확률적 지시로 안 되는 것은 코드로 자른다. 줄이 통째로
    추임새뿐이면 남긴다(뗄 내용이 없으면 빈 대사가 된다).
    """
    stripped = _FILLER_RE.sub("", text, count=1).strip()
    return stripped or text


def validate_script(text: str) -> tuple[str, int]:
    """화자 형식 줄만 남긴 원고와 대사 글자 수. 원고가 못 되면 ValueError.

    1인 진행 전환 후에도 ANALYST 라벨은 버리지 않고 HOST 로 흡수한다 —
    모델이 옛 형식으로 회귀해도 내용은 살리는 쪽이 낫다.
    """
    lines = []
    spoken = 0
    for raw in str(text or "").splitlines():
        match = SPEAKER_RE.match(raw.strip())
        if match:
            spoken_text = strip_filler(match.group(2).strip())
            lines.append(f"HOST: {spoken_text}")
            spoken += len(spoken_text)
    if len(lines) < MIN_LINES:
        raise ValueError(f"화자 형식 줄 {len(lines)}개 — 원고 형식 미달")
    return "\n".join(lines), spoken


def _script_models() -> list[str]:
    """대본 모델 사다리 — 전용 버킷이 막히면 공용 버킷으로 넘어간다.

    2026-08-10 실사고: flash-lite 가 분당 한도(limit 20)에 걸려 3연속 429 로
    대본이 실패했고, 오디오 스텝이 비치명이라 워크플로는 success 로 끝나
    그날 오디오만 조용히 빠졌다. 정작 그 잡 자신의 호출은 분당 2회였다 —
    같은 키를 쓰는 다른 소비자가 버킷을 먹었다는 뜻이라, 버티는 것 말고
    버킷을 옮기는 길도 있어야 한다.
    """
    models = [_script_model()]
    if gemini_client.MODEL and gemini_client.MODEL not in models:
        models.append(gemini_client.MODEL)
    return models


def _call_script(message: str) -> dict:
    """대본 1회 호출 — 모델 사다리 + 넉넉한 재시도.

    하루 1회짜리 마지막 스텝이라 느려도 된다. call_json 은 서버가 알려주는
    대기 시간을 그대로 자므로 SCRIPT_RETRIES 회면 분당 한도 몇 창은 넘긴다.
    """
    last_err: Exception | None = None
    for model in _script_models():
        try:
            return call_json(SYSTEM_PROMPT, message, temperature=0.4,
                             max_output_tokens=8192, timeout=120.0,
                             thinking_budget=0, model=model,
                             retries=SCRIPT_RETRIES, label="audio_brief")
        except GeminiError as exc:
            last_err = exc
            print(f"[audio] 대본 {model} 실패 — 다음 모델 폴백: {str(exc)[:160]}")
    raise last_err or GeminiError("대본 모델 전부 실패")


def generate_script(material: str) -> str:
    """원고 생성 + 재시도 사다리 1단 (daily_lead 패턴).

    thinking_budget=0 필수 — 원고는 사고가 필요 없는 창작 출력인데
    thinking 을 켜 두면 예산(8192)을 thinking 이 먹고 원고가 잘린다
    (2026-08-04 CI 실사고: thoughts=7863, output=315).
    """
    result = _call_script(material)
    try:
        script, spoken = validate_script(result.get("script"))
        if spoken <= MAX_SPOKEN:
            return script
        problem = f"대사 합계 {spoken}자로 상한 {MAX_SPOKEN}자를 넘었습니다"
    except ValueError as exc:
        problem = str(exc)

    retry_message = (
        f"{material}\n\n[재요청] 방금 출력에 문제가 있었습니다: {problem}.\n"
        "형식 규칙(모든 줄이 HOST: 로 시작)과 [분량] 지시를 지켜 "
        "원고 전체를 다시 쓰세요."
    )
    result = _call_script(retry_message)
    script, spoken = validate_script(result.get("script"))
    if spoken > MAX_SPOKEN:
        raise ValueError(f"재시도 후에도 {spoken}자 — 포기")
    return script


def split_script(script: str, limit: int = CHUNK_SPOKEN) -> list[str]:
    """대본을 화자 줄 경계에서 청크로 나눈다.

    4분치를 TTS 1요청으로 합성하면 뒤로 갈수록 소리가 먹고 작아진다
    (2026-08-08 배포분 실측: 첫 30초 평균 -17.6 dB / 3kHz 이상 -32.9 dB →
    마지막 30초 -40.2 dB / -68.8 dB. mp3 는 64k CBR 이라 변환 탓이 아니고
    소스 PCM 이 그렇다). 요청을 나누면 청크마다 새로 시작한다.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in script.splitlines():
        match = SPEAKER_RE.match(line)
        spoken = len(match.group(2)) if match else len(line)
        if current and size + spoken > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += spoken
    if current:
        chunks.append("\n".join(current))
    return chunks


def tts_payload(script: str) -> dict:
    """TTS 요청 본문 — 단일 화자. 'HOST: ' 라벨은 형식용이라 떼고 보낸다
    (멀티스피커 모드가 아니면 라벨을 그대로 읽는다)."""
    text = "\n".join(match.group(2) for match in
                     (SPEAKER_RE.match(line) for line in script.splitlines())
                     if match)
    return {
        "contents": [{"parts": [{"text": STYLE_INSTRUCTION + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": VOICES["HOST"]}}},
        },
    }


def _tts_models() -> list[str]:
    override = gemini_client._resolve("GEMINI_TTS_MODEL")
    models = list(TTS_MODELS)
    if override:
        models = [override] + [m for m in models if m != override]
    return models


def call_tts(script: str, models: list[str] | None = None) -> tuple[bytes, int]:
    """멀티스피커 합성 → (PCM s16le, sample rate). 모델 순서대로 폴백.

    models 를 주면 그 목록만 쓴다 — 한 대본 안에서 모델이 섞이지 않게
    synthesize 가 모델을 고정해 내려보낸다.
    """
    last_err: Exception | None = None
    for model in (models or _tts_models()):
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


def _check_not_truncated(index: int, chunk: str, pcm: bytes, rate: int) -> None:
    """대사 길이 대비 음원이 너무 짧으면 잘린 것으로 본다.

    Gemini TTS 는 긴 요청을 **오류 없이** 잘라서 돌려준다. 우리 실패는 전부
    조용한 종류였다(꼬리 감쇠·429 로 통째 누락) — 문장 중간에서 끊긴 브리핑이
    아무 신호 없이 나가는 것도 같은 함정이라 여기서 센다.
    """
    spoken = sum(len(match.group(2)) for match in
                 (SPEAKER_RE.match(line) for line in chunk.splitlines()) if match)
    if spoken < 200:
        return
    expected = spoken / SPOKEN_CHARS_PER_SEC
    actual = len(pcm) / 2 / rate
    if actual < expected * TRUNCATION_RATIO:
        raise GeminiError(
            f"청크 {index} 잘림 의심 — 대사 {spoken}자에 음원 {actual:.0f}초"
            f"(기대 {expected:.0f}초 이상)")


def trim_silence(pcm: bytes, rate: int) -> bytes:
    """앞뒤 무음을 떼어낸다 (s16le mono).

    TTS 청크는 제 나름의 앞뒤 여백을 달고 온다. 거기에 우리 간격까지 더해지면
    이음새가 파일에서 제일 긴 정적이 된다 — 2026-08-10 실측으로 경계 두 곳이
    0.92초·0.96초로 전체 1·2위였고, 문장 사이 자연 무음은 0.5~0.7초였다.
    모델이 바뀌는 지점에 죽은 자리가 생기는 셈이라 여백을 걷어내고 간격을
    우리가 정한 값 하나로 통일한다.

    통째로 무음이면 원본을 그대로 준다 — 빈 바이트를 이어붙이면 그 청크가
    사라진 것을 아무도 모른다.
    """
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    frame = max(1, rate * TRIM_FRAME_MS // 1000)
    start, end = 0, len(samples)
    while start < end and max(
            (abs(x) for x in samples[start:start + frame]), default=0) < SILENCE_LEVEL:
        start += frame
    while end > start and max(
            (abs(x) for x in samples[max(start, end - frame):end]), default=0) < SILENCE_LEVEL:
        end -= frame
    if start >= end:
        return pcm
    return samples[start:end].tobytes()


def synthesize(script: str) -> tuple[bytes, int]:
    """대본을 청크로 나눠 합성하고 PCM 을 이어붙인다.

    청크 PCM 은 전부 같은 포맷(s16le mono, 같은 rate)이라 바이트 연결로 충분하다.
    레이트가 섞이면 이어붙인 결과가 배속으로 재생되므로 그때는 실패시킨다.

    **모델은 대본 하나에 하나만 쓴다.** 청크마다 폴백을 따로 태우면 3번 청크만
    다른 모델로 넘어가 한 파일 안에서 목소리가 바뀐다 — 모델이 다르면 같은
    voiceName 이라도 음색이 다르다. 그래서 실패하면 다음 모델로 **처음부터**
    다시 만든다. 이미 만든 청크를 버리는 값보다 화자가 중간에 바뀌는 값이 크다.
    """
    chunks = split_script(script)
    print(f"[audio] 대본 {len(script)}자 → TTS 청크 {len(chunks)}개")
    last_err: Exception | None = None
    for model in _tts_models():
        pieces: list[bytes] = []
        rate = 0
        try:
            for index, chunk in enumerate(chunks, 1):
                pcm, chunk_rate = call_tts(chunk, models=[model])
                if rate and chunk_rate != rate:
                    raise GeminiError(
                        f"청크 {index} 샘플레이트 불일치: {chunk_rate} != {rate}")
                rate = chunk_rate
                _check_not_truncated(index, chunk, pcm, rate)
                if pieces:
                    pieces.append(b"\x00" * (int(rate * CHUNK_GAP_SEC) * 2))
                pieces.append(trim_silence(pcm, rate))
            return b"".join(pieces), rate
        except GeminiError as exc:
            last_err = exc
            print(f"[audio] {model} 실패 — 다음 모델로 대본 처음부터: {exc}")
    raise last_err or GeminiError("TTS 모델 전부 실패")


def to_mp3(pcm: bytes, rate: int, out_path: Path) -> None:
    """PCM s16le mono → MP3 64k. ffmpeg 는 GitHub 러너·로컬 모두 존재.

    dynaudnorm 은 청크 사이 레벨 차를 평탄화한다 — 요청이 나뉘면 청크마다
    시작 음량이 조금씩 다르다. 감쇠 자체를 여기서 되살릴 수는 없다(실측:
    -40 dB 까지 죽은 꼬리는 정규화해도 고역이 안 돌아온다). 그건 split_script 몫.

    loudnorm 은 그 뒤에 절대 레벨을 팟캐스트 표준(-16 LUFS)으로 맞추고
    트루피크를 -1.5 dBTP 로 눌러 준다. dynaudnorm 만 걸면 날마다 기준이
    떠다니고 피크가 -1.1 dBFS 까지 붙어 mp3 인코딩에서 클리핑 여지가 남는다.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 없음 — mp3 변환 불가")
    raw = out_path.with_suffix(".pcm")
    raw.write_bytes(pcm)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le",
             "-ar", str(rate), "-ac", "1", "-i", str(raw),
             "-af", "dynaudnorm=f=250:g=15:p=0.9:m=6,"
                    "loudnorm=I=-16:TP=-1.5:LRA=11",
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


def send_telegram_audio(mp3_path: Path, meta: dict) -> bool:
    """오디오를 텔레그램 브리핑 채널로 발송. 실패해도 비치명 — 다음 실행이 재시도.

    telegram_send.py 는 import 시점에 토큰이 없으면 sys.exit 하므로(모듈 상단
    가드) 여기서는 sendAudio 를 직접 부른다. requests 는 이미 requirements 에
    있다. 텔레그램 오디오 플레이어는 자체 배속(1/1.5/2×)을 제공한다.
    """
    token = gemini_client._resolve("TELEGRAM_BOT_TOKEN")
    chat_id = gemini_client._resolve("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[audio] 텔레그램 미설정 — 발송 스킵")
        return False
    minutes, seconds = divmod(int(meta.get("duration_sec") or 0), 60)
    caption = (
        f"🎧 {meta.get('date', '')} 오디오 브리핑 ({minutes}분 {seconds:02d}초)\n"
        "하이라이트 대담 + 나머지 헤드라인 · nuclens.pages.dev"
    )
    import requests
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendAudio",
            data={
                "chat_id": chat_id,
                "caption": caption,
                "title": f"Nuclens 브리핑 {meta.get('date', '')}",
                "performer": "Nuclens",
                "duration": int(meta.get("duration_sec") or 0),
            },
            files={"audio": (mp3_path.name, mp3_path.read_bytes(), "audio/mpeg")},
            timeout=120,
        )
        payload = response.json()
        if not (response.ok and payload.get("ok")):
            print(f"[audio] 텔레그램 발송 실패 — HTTP {response.status_code}: "
                  f"{str(payload)[:200]}")
            return False
    except Exception as exc:  # noqa: BLE001 — 발송은 부가 기능, 어떤 예외도 비치명
        print(f"[audio] 텔레그램 발송 실패 — {type(exc).__name__}: {exc}")
        return False
    print(f"[audio] 텔레그램 발송 완료 ({mp3_path.name})")
    return True


def _mark_sent(meta: dict) -> None:
    meta["telegram_sent_at"] = datetime.now(KST).isoformat()
    _write_meta(meta)


def generate(force: bool = False, send: bool = True) -> bool:
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
        # 생성은 됐는데 발송이 안 된 채 끝난 실행(429 등)을 여기서 회수한다.
        if not existing.get("telegram_sent_at"):
            if send_telegram_audio(mp3_path, existing):
                _mark_sent(existing)
        else:
            print(f"[audio] {date} 이미 생성·발송됨 ({file_name}) — 스킵")
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
    script = apply_frame(script, briefing)

    try:
        pcm, rate = synthesize(script)
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
    meta = {
        "date": date,
        "file": file_name,
        "duration_sec": duration,
        "generated_at": datetime.now(KST).isoformat(),
        "script_chars": sum(len(line.split(":", 1)[1]) for line in script.splitlines()),
        "voices": VOICES,
    }
    _write_meta(meta)
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
    if send and send_telegram_audio(mp3_path, meta):
        _mark_sent(meta)
    return True


if __name__ == "__main__":
    # 어떤 실패도 배포를 죽이면 안 된다 — 오디오는 부가 기능이다. 다만 **성패는
    # 종료 코드로 알린다.** 예전엔 무조건 0 이라 워크플로의
    # `python audio_brief.py || echo "실패"` 가 한 번도 실행된 적이 없었고,
    # 429 로 그날 오디오가 통째로 빠져도 스텝은 성공으로 보였다
    # (2026-08-12 실사고: 19초 만에 조용히 종료, 워크플로는 success).
    # 호출자는 여전히 `||` 로 받아 넘긴다 — 비치명 계약은 호출자 쪽에 있다.
    ok = False
    try:
        ok = generate(force="--force" in sys.argv,
                      send="--no-send" not in sys.argv)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[audio] 예상 밖 실패 — 비치명 처리: {exc}")
    sys.exit(0 if ok else 1)
