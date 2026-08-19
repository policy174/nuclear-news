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
  - 대본은 당일 이슈 **전부**를 다룬다: 하이라이트는 깊게, 나머지는 단신으로.
    분량은 이슈당 초 단위 airtime 을 배분하고 실측 발화율로 환산한다
    (평시 8~10분, NucBrief 의 airtime 배분 이식 2026-08-14).
  - 대본 생성은 섹션 2회 호출(하이라이트/단신)로 나누고, 응답을 issue ID
    기반으로 검증해 누락·중복·창작을 잡는다 — "전 이슈 커버"의 보장 지점.
  - 배속은 여기서 만들지 않는다 — 웹 플레이어의 playbackRate 가 맡는다.
  - 산출물은 web/public/data/audio/ (gitignore 안 — Pages 배포에만 실림).
    crawl.yml 짝수시 재배포에서 사라지지 않도록 Actions 캐시로 유지된다
    (embeddings.json 과 같은 패턴).

가드레일:
  - 대본 재료 밖 사실·미래 예측·투자 권고 금지 (daily_lead 와 동일 원칙).
  - 어떤 실패도 배포를 죽이면 안 된다 — main() 은 항상 exit 0.
  - 같은 날짜 재실행은 TTS 를 다시 부르지 않는다 (무료 티어 보호).
  - 부분이 무(無)보다 낫되, 배송 시간은 항상 예약된다 — TTS 는 hard deadline
    안쪽에서만 돌고, 완성된 청크가 하나라도 있으면 부분본으로 내보낸다.
    0청크일 때만 전날 오디오를 유지한다 (NucBrief 이식 2026-08-14).
"""

from __future__ import annotations

import array
import base64
import json
import re
import shutil
import subprocess
import sys
import time
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

# 빠른 브리핑(약 3분) — expert 완제·기록·발송이 끝난 뒤 잔여 예산이 있을 때만
# 시도하는 부가 산출물. 어떤 실패도 expert 를 건드리지 않는다.
# 1,300자는 '약 3분'의 제어값이지 계약이 아니다 — 한국어 TTS 실제 길이는
# 숫자·영문·쉼에 따라 흔들리므로 실측 duration 은 audio.json 이 말한다.
FAST_TARGET_CHARS = 1300
FAST_MIN_CHARS = 400          # 이보다 짧으면 브리핑이 아니라 예고편이다
FAST_MIN_REMAINING_SEC = 300  # LLM 1회 + TTS 2~3청크(pacer 21초) + mp3 변환
FAST_TTS_BUDGET_SEC = 240
FAST_RETRIES = 2              # fast 는 선택 산출물 — expert 의 예산을 굶기지 않는다

EXPERT_LABEL = "전문가 브리핑"
EXPERT_DESCRIPTION = "선정된 핵심 이슈의 정책·사업·기술 의미까지 통합 해설하는 브리핑입니다."
FAST_LABEL = "빠른 브리핑"
FAST_DESCRIPTION = "오늘의 핵심 원자력 뉴스를 약 3분 안팎으로 빠르게 훑는 라디오형 브리핑입니다."

FAST_SYSTEM_PROMPT = (
    "당신은 라디오 뉴스 브리핑 편집자입니다. 주어진 원자력 브리핑 대본을 "
    "핵심 이슈 중심으로 압축해 약 3분(1,200~1,400자) 분량의 빠른 브리핑을 만드세요.\n"
    "[규칙]\n"
    '- 반드시 JSON {"paragraphs": ["..."]} 형식으로만 출력\n'
    "- 각 원소는 진행자가 그대로 읽는 완결된 한국어 단락 (라벨·이모지·괄호 지문 금지)\n"
    "- 사실·수치·기관명은 원문 대본에 있는 것만 사용, 새 정보 금지\n"
    "- 인사말·마무리 금지 — 프레임은 코드가 붙인다\n"
)


def _script_model() -> str:
    return gemini_client._resolve("GEMINI_SCRIPT_MODEL", SCRIPT_MODEL_DEFAULT)

SPEAKER_RE = re.compile(r"^(HOST|ANALYST):\s*(.+)$")
# 줄머리 추임새. '네'·'예'는 뒤에 구두점이 붙은 것만 잡는다 — '네트워크',
# '예산' 같은 낱말을 자르면 안 된다.
_FILLER_RE = re.compile(
    r"^(?:아,\s*)?(?:네|예|그렇군요|그렇죠|맞습니다|알겠습니다)\s*[,.!]\s*")
# ── 길이 모델 — 문자 수가 아니라 초로 배분하고 실측 발화율로 환산한다 ──────
# 고정 문자 상한(1500자 ≈ 3분40초)은 이슈 9건 밖을 통째로 버렸다. 이슈당
# airtime(초)을 배분하면 기사 수만큼 분량이 자연히 자란다 (NucBrief 이식).
CHARS_PER_SEC = 6.75   # 실측 2026-08-14 단일 Kore: 1113자 / 165초
TARGET_SEC_MAX = 600   # 10분 하드 실링
TARGET_SEC_MIN = 480   # 평시(이슈 충분한 날) 8분 하한
DEEP_SEC = 90          # 하이라이트당 airtime
REST_SEC = 22          # 단신당 (2~3문장)
REST_MIN_MODE_SEC = 12 # 축소 후 단신당 예산이 이보다 작으면 '최소 1문장' 모드
FRAME_SEC = 30         # 오프닝+클로징+전환
MAX_SPOKEN = int(TARGET_SEC_MAX * CHARS_PER_SEC)   # 대사 합계 상한 (= 10분치)
# 모델은 [분량] 지시의 ~75%만 채운다 (eval 실측 ×0.73~0.80, 3회 재현).
# 프롬프트에 보여주는 숫자만 역보정하고, 검증·상한은 원래 목표 기준.
PROMPT_ASK_SCALE = 1.3
SECTION_FLOOR = 0.85   # 섹션 대사가 목표의 이 비율 미만이면 재요청 1회
SECTION_CEIL = 1.35    # 섹션 폭주 상한 (초과 시 재요청) — deep 에만 안 걸었더니
                       # 과부하 날 deep 이 ×1.51 로 넘쳤다 (2026-08-16 eval)
MAX_SPOKEN_GRACE = 1.15  # 조립 상한 유예 — 이 밖만 진짜 폭주로 차단
DEEP_LIMIT = 3         # 깊게 다룰 이슈 수 (하이라이트)
CHUNK_SPOKEN = 900     # TTS 1요청에 넣을 대사 글자 수 (~90초). 아래 주석 참조
CHUNK_GAP_SEC = 0.45   # 청크 사이 간격. 문장 사이 자연 무음(0.5~0.7초)에 맞춘다
SILENCE_LEVEL = 300    # s16 진폭 — 이보다 작으면 무음으로 본다 (약 -41 dBFS)
TRIM_FRAME_MS = 10
# 잘림 감지용. 실측 8.5자/초 근처(08-10: 대사 1910자 / 257초 = 7.4)라 넉넉히
# 잡고, 기대치의 이만큼도 안 되면 잘린 것으로 본다.
SPOKEN_CHARS_PER_SEC = 8.5
TRUNCATION_RATIO = 0.6

# ── TTS 안정화 (NucBrief 이식 2026-08-14) ─────────────────────────────────
TTS_MIN_INTERVAL_SEC = 21    # 프리뷰 TTS 무료 티어 ≈ 3 RPM — 호출 간 최소 간격
TTS_RETRY_BUDGET_SEC = 240   # 재시도 sleep 의 스테이지 공유 예산 (곱발산 방지)
TTS_HARD_BUDGET_SEC = 900    # TTS 스테이지 자체 wall-clock 상한 (15분)
AUDIO_RUN_BUDGET_SEC = 1500  # 프로세스 전체 wall-clock 상한 (25분)
SHIP_RESERVE_SEC = 180       # 인코딩·기록·텔레그램 전송 예약분
TTS_CALL_EST_SEC = 35        # deadline 잔여 검사용 1회 호출 추정치
TTS_CHUNK_RETRIES = 3        # 핀 모델 안에서 청크당 재시도 횟수
RESTART_THRESHOLD = 2        # 완료 청크가 이보다 적으면 폴백 시 처음부터,
                             # 이상이면 이어받는다 (음색 seam < 꼬리 잘림)

_TTS_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# 대본은 섹션 2회 호출로 나눈다 — 4~5천 자를 한 번에 뽑으면 출력 잘림·후반
# 품질 감쇠 위험이 있고, 섹션별로 issue ID 검증·재요청이 가능해진다.
# 출력은 이슈당 한 항목의 items 배열 — ID 집합 검증이 "전 이슈 커버"를 지킨다.
_PROMPT_INTRO = """당신은 한수원 임직원용 원자력·에너지 이슈 트래커 'Nuclens'(누클렌즈)의
아침 오디오 브리핑 원고 작가입니다. 진행자 한 명이 읽는 라디오 뉴스 원고를
아래 [재료]만 사용해 씁니다."""

_PROMPT_VOICE_RULES = """[말투 — 낭독용 구어체]
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
- 어떤 이슈에도 붙일 수 있는 일반론 문장 금지 — "매우 중요합니다",
  "기대됩니다", "의지를 보여줍니다", "귀추가 주목됩니다" 같은 문장은
  삭제 대상입니다. 그 문장을 지워도 정보가 줄지 않으면 쓰지 마세요.
- 약어는 첫 등장에만 "소형모듈원자로, 에스엠알"처럼 풀고 이후에는
  "에스엠알"로만 말합니다. 같은 명칭을 연달아 두 번 읽지 마세요.

[출력 — JSON 한 객체만]
{"items": [{"id": "<재료의 ID 그대로>", "script": "HOST: ..."}]}
- [재료]의 모든 이슈를 입력 순서 그대로, 이슈당 정확히 한 항목으로 씁니다.
  이슈를 빼먹거나, 두 번 쓰거나, 재료에 없는 이슈를 만들면 안 됩니다.
- id 는 재료의 ID 값을 글자 그대로 복사합니다.
- script 에는 낭독할 문장만 씁니다 — 화자 라벨·머리기호·주석 없이,
  문단 구분이 필요하면 줄바꿈만 사용합니다.
- 인사·자기소개·마무리 문장 금지 — 오프닝과 클로징은 시스템이 붙입니다."""

SYSTEM_PROMPT_DEEP = f"""{_PROMPT_INTRO}
이번 요청은 그날의 **하이라이트 이슈**를 하나씩 깊게 다루는 본문입니다.

[형식 — 반드시 준수]
- 이슈 하나 = 항목 하나. 한 항목의 script 는 4~8문장, 여러 줄로 나눠 쓰세요
  (줄 하나 = 2~3문장 한 문단).
- 흐름: 무슨 일이 있었는지 → 배경·경과 → 지금 어느 단계인지. 자료에
  구체적인 다음 일정·판단 기준이 있을 때만 의미를 덧붙입니다.
- 제목을 읽고 끝내지 마세요. 그 제목이 말하는 사건이 무엇인지, 듣는 사람이
  처음 듣는다고 생각하고 풀어 말합니다. 요약·최근 변화에 담긴 사실을
  아끼지 말고 전부 풀어 쓰세요 — 재료 밖 사실만 금지입니다.
- 분량: [재료]의 [분량] 지시가 문장 수 감각보다 우선합니다. 이슈당 지시된
  글자 수를 채우세요 — 짧게 끝내는 것이 가장 흔한 실패입니다.

{_PROMPT_VOICE_RULES}"""

SYSTEM_PROMPT_REST = f"""{_PROMPT_INTRO}
이번 요청은 하이라이트 이후에 이어지는 **단신 묶음**입니다.

[형식 — 반드시 준수]
- 이슈 하나 = 항목 하나. 한 항목의 script 는 한 줄, 2~3문장.
- 단신도 제목 낭독이 아닙니다 — 무슨 일이 있었는지 한 번에 알아듣게.
- 분량: [재료]의 [분량] 지시가 문장 수 감각보다 우선합니다. 이슈당 지시된
  글자 수를 채우세요 — 한 문장으로 끝내는 것이 가장 흔한 실패입니다.

{_PROMPT_VOICE_RULES}"""

# 과부하(축소 후 단신당 예산 < REST_MIN_MODE_SEC) 시 형식 줄만 교체한다 —
# 문장 수보다 전 이슈 커버가 우선이다.
SYSTEM_PROMPT_REST_MIN = SYSTEM_PROMPT_REST.replace(
    "한 항목의 script 는 한 줄, 2~3문장.",
    "한 항목의 script 는 한 줄, **1문장** — 오늘은 이슈가 많아 짧게 갑니다.")

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


def _issue_block(issue_id: str, issue: dict, deep: bool) -> str:
    parts = [f"ID: {issue_id}",
             f"제목: {issue.get('title', '')}",
             f"지역: {issue.get('region', '')}",
             f"요약: {issue.get('summary', '')}"]
    if deep and issue.get("latest_change"):
        # 기존 implication/why_important에는 기사와 무관한 일반론이 섞인 이력이 있다.
        # 오디오는 원문을 다시 확인할 수 없으므로 사실 필드와 최근 변화만 사용한다.
        parts.append(f"최근 변화: {issue['latest_change']}")
    return "\n".join(parts)


def _issue_ids(briefing: dict) -> tuple[list, list]:
    """(하이라이트 id, 나머지 id 전부) — 단신을 자르지 않는다 (전 이슈 커버)."""
    highlight_ids = [h.get("issue_id") for h in briefing.get("highlight_issues", [])
                     if isinstance(h, dict) and h.get("issue_id")][:DEEP_LIMIT]
    listed = [row.get("issue_id") for row in briefing.get("issues", [])
              if isinstance(row, dict) and row.get("issue_id")]
    if not highlight_ids:
        highlight_ids = listed[:DEEP_LIMIT]
    return highlight_ids, [i for i in listed if i not in highlight_ids]


def section_budgets(deep_count: int, rest_count: int) -> tuple[float, float]:
    """(하이라이트 초, 단신 초). 총합이 상한을 넘으면 비례 축소한다.

    상한을 총합에서만 자르면 섹션 prompt 예산은 여전히 원래 크기를 요구해
    말이 안 맞는다 — 축소는 배분 단계에서 일어나야 한다.
    """
    content_sec = DEEP_SEC * deep_count + REST_SEC * rest_count
    available_sec = TARGET_SEC_MAX - FRAME_SEC
    scale = min(1.0, available_sec / max(content_sec, 1))
    return DEEP_SEC * deep_count * scale, REST_SEC * rest_count * scale


def spoken_target(deep_count: int, rest_count: int) -> tuple[int, int]:
    """(하한, 상한) 대사 글자 수 — 초로 배분하고 실측 발화율로 환산.

    평시(이슈가 충분한 날)는 8분 하한을 보장하고, 이슈가 적은 날은 짧아지는
    것을 수용한다. 검증 기준은 문자 수가 아니라 최종 duration 480~600초.
    """
    deep_sec, rest_sec = section_budgets(deep_count, rest_count)
    target_sec = min(FRAME_SEC + deep_sec + rest_sec, TARGET_SEC_MAX)
    if target_sec >= TARGET_SEC_MIN:
        low_sec = max(TARGET_SEC_MIN, int(target_sec * 0.92))
    else:
        low_sec = int(target_sec * 0.85)
    return int(low_sec * CHARS_PER_SEC), int(target_sec * CHARS_PER_SEC)


# 재료의 ID 는 실제 issue_id(긴 hex)가 아니라 섹션 내 위치 번호("1","2"…)다.
# 모델이 hex 를 베끼다 한 글자를 틀리는 실사고(2026-08-15 eval:
# c4c4→c4e4, 누락+창작 판정)가 있었다 — 한 자리 번호는 오타가 불가능하다.
def _alias_ids(ids: list) -> list[str]:
    return [str(n) for n in range(1, len(ids) + 1)]


def _ask_line(high: int, count: int) -> str:
    """[분량] 지시문. 모델 이행률(~75%)을 역보정한 숫자를 보여준다."""
    ask = int(high * PROMPT_ASK_SCALE)
    per = ask // max(count, 1)
    return (f"[분량] 이슈당 약 {per:,}자씩, 대사 합계 {int(ask * 0.85):,}~{ask:,}자. "
            "이 글자 수 지시가 문장 수보다 우선입니다.")


def build_deep_material(briefing: dict, by_id: dict, ids: list,
                        deep_sec: float) -> str:
    """하이라이트 섹션 재료 — 날짜·헤드라인 맥락 + 깊은 블록.

    deep_sec 는 section_budgets 가 배분한(축소 반영) 초 — 여기서 다시 계산하면
    과부하 날의 축소가 deep 에 안 먹는다 (2026-08-15 eval 실사고: 4,359자 초과).
    [분량]은 이슈당 글자 수를 명시한다 — 합계만 주면 모델이 문장 수 감각으로
    짧게 끝낸다 (2026-08-14 eval 실측: 지시 1,822자에 567자, ×0.31).
    """
    blocks = [_issue_block(alias, by_id[i], True)
              for alias, i in zip(_alias_ids(ids), ids)]
    weekday = "월화수목금토일"[datetime.strptime(briefing["date"], "%Y-%m-%d").weekday()]
    high = int(deep_sec * CHARS_PER_SEC)
    return "\n\n".join([
        f"[날짜] {briefing['date']} ({weekday}요일 아침)",
        f"[오늘의 헤드라인] {briefing.get('headline', '')}",
        "[하이라이트 이슈 — 하나씩 깊게]\n\n" + "\n\n---\n\n".join(blocks),
        _ask_line(high, len(ids)),
    ])


def build_rest_material(rest_sec: float, by_id: dict, ids: list) -> str:
    """단신 섹션 재료. rest_sec 는 section_budgets 가 배분한(축소 반영) 초."""
    blocks = [_issue_block(alias, by_id[i], False)
              for alias, i in zip(_alias_ids(ids), ids)]
    high = int(rest_sec * CHARS_PER_SEC)
    return "\n\n".join([
        "[단신 이슈 — 순서대로 전부]\n\n" + "\n\n---\n\n".join(blocks),
        _ask_line(high, len(ids)),
    ])


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


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def trim_to_budget(lines: list[str], high_chars: int) -> tuple[list[str], int]:
    """섹션이 상한을 넘으면 이슈당 예산으로 각 줄을 문장 단위 트림.

    재요청 후에도 모델이 폭주하면(2026-08-16 eval: 재요청 응답 5,505자) 말릴
    수단이 이것뿐이다. 줄(=이슈)은 절대 버리지 않는다 — 전 이슈 커버 우선.
    """
    texts = [line.split(":", 1)[1].strip() for line in lines]
    spoken = sum(len(t) for t in texts)
    if spoken <= high_chars * SECTION_CEIL:
        return lines, spoken
    per = max(30, high_chars // max(len(texts), 1))
    out, total = [], 0
    for text in texts:
        if len(text) > per:
            kept = ""
            for sent in _SENT_SPLIT.split(text):
                if kept and len(kept) + len(sent) + 1 > per:
                    break
                kept = f"{kept} {sent}".strip()
            text = kept or text[:per]
        out.append(f"HOST: {text}")
        total += len(text)
    print(f"[audio] 분량 트림 — {spoken}자 → {total}자 (이슈당 ~{per}자)")
    return out, total


# 모델이 붙여 보낼 수 있는 화자 라벨(오타 포함) — 떼고 우리가 다시 붙인다.
# HOST: 를 수십 번 전사시키니 'HOS:'·라벨 누락이 나왔다(2026-08-16 eval).
# 보일러플레이트 전사는 시키지 않는다 — hex ID 와 같은 교훈.
_LABEL_RE = re.compile(r"^(?:HOST|ANALYST|HOS|호스트|진행자)\s*:\s*", re.IGNORECASE)


def validate_items(items, expected_ids: list) -> tuple[list[str], int]:
    """(HOST 줄 목록, 대사 글자 수). issue ID 완전성이 어긋나면 ValueError.

    "당일 이슈 전부"의 실제 보장 지점 — 누락·중복·창작·순서를 전부 잡는다.
    min_lines 방식은 한 이슈가 세 줄 쓰고 다른 이슈를 빼먹어도 통과시켰다.
    script 는 순수 낭독문으로 받고 HOST: 라벨은 여기서 코드가 붙인다.
    """
    if not isinstance(items, list):
        raise ValueError("items 가 배열이 아님")
    got_ids = [str(item.get("id") or "").strip()
               for item in items if isinstance(item, dict)]
    if len(got_ids) != len(items):
        raise ValueError("항목 형식 오류 — 객체가 아닌 항목 존재")
    expected = list(expected_ids)
    missing = [i for i in expected if i not in got_ids]
    duplicated = sorted({i for i in got_ids if got_ids.count(i) > 1})
    invented = [i for i in got_ids if i not in expected]
    if missing or duplicated or invented:
        raise ValueError(
            f"이슈 ID 불일치 — 누락: {missing or '없음'} / "
            f"중복: {duplicated or '없음'} / 창작: {invented or '없음'}")
    if got_ids != expected:
        raise ValueError(f"이슈 순서 불일치 — 출력 순서: {got_ids}")
    lines: list[str] = []
    spoken = 0
    for item in items:
        script = item.get("script")
        if isinstance(script, list):
            # 모델이 script 를 줄 배열로 돌려주는 회귀가 있다(2026-08-14 eval
            # 실측, 과부하 corpus) — 내용은 맞으므로 형식만 받아준다.
            script = "\n".join(str(part) for part in script)
            item = dict(item, script=script)
        for raw in str(item.get("script") or "").splitlines():
            raw = _LABEL_RE.sub("", raw.strip()).strip()
            if not raw:
                continue
            spoken_text = strip_filler(raw)
            lines.append(f"HOST: {spoken_text}")
            spoken += len(spoken_text)
        if not str(item.get("script") or "").strip():
            raise ValueError(f"{item['id']}: 대사 없음")
    return lines, spoken


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


def _call_script(system_prompt: str, message: str) -> dict:
    """대본 1회 호출 — 모델 사다리 + 넉넉한 재시도.

    하루 1회짜리 마지막 스텝이라 느려도 된다. call_json 은 서버가 알려주는
    대기 시간을 그대로 자므로 SCRIPT_RETRIES 회면 분당 한도 몇 창은 넘긴다.

    thinking_budget=0 필수 — 원고는 사고가 필요 없는 창작 출력인데
    thinking 을 켜 두면 예산(8192)을 thinking 이 먹고 원고가 잘린다
    (2026-08-04 CI 실사고: thoughts=7863, output=315).
    """
    last_err: Exception | None = None
    for model in _script_models():
        try:
            return call_json(system_prompt, message, temperature=0.4,
                             max_output_tokens=8192, timeout=120.0,
                             thinking_budget=0, model=model,
                             retries=SCRIPT_RETRIES, label="audio_brief")
        except GeminiError as exc:
            last_err = exc
            print(f"[audio] 대본 {model} 실패 — 다음 모델 폴백: {str(exc)[:160]}")
    raise last_err or GeminiError("대본 모델 전부 실패")


TRANSITION_LINE = "HOST: 이어서 나머지 소식들을 짧게 전해드립니다."


def generate_section(system_prompt: str, material: str, expected_ids: list,
                     high_chars: int | None = None,
                     ceil_ratio: float | None = None) -> tuple[list[str], int]:
    """섹션 하나 생성 + ID·분량 검증 + 재요청 1단.

    빈 섹션은 API 를 부르지 않는다 — 0deep/0rest 날에 불필요한 호출·검증
    실패가 없어야 한다.

    분량 게이트는 closed-loop 다: 목표 미달(SECTION_FLOOR)·폭주(ceil_ratio)면
    실제 수치를 담아 재요청한다. 재요청 후에도 어긋나면 형식·ID 만 지켰다면
    수용한다 — 짧은 브리핑이 없는 브리핑보다 낫다 (부분 배송과 같은 원칙).
    """
    if not expected_ids:
        return [], 0
    result = _call_script(system_prompt, material)
    problem = None
    try:
        lines, spoken = validate_items(result.get("items"), expected_ids)
        if high_chars and spoken < high_chars * SECTION_FLOOR:
            problem = (f"대사 합계가 {spoken}자로 목표에 크게 미달합니다. "
                       "[분량]의 이슈당 글자 수를 채우세요")
        elif high_chars and ceil_ratio and spoken > high_chars * ceil_ratio:
            problem = (f"대사 합계가 {spoken}자로 목표를 크게 초과합니다. "
                       "[분량] 범위로 줄이세요")
        if problem is None:
            return lines, spoken
    except ValueError as exc:
        problem = str(exc)
    retry_message = (
        f"{material}\n\n[재요청] 방금 출력에 문제가 있었습니다: {problem}.\n"
        "[재료]의 모든 이슈를 입력 순서대로 정확히 한 번씩, [분량] 지시를 "
        "지켜 다시 쓰세요."
    )
    result = _call_script(system_prompt, retry_message)
    lines, spoken = validate_items(result.get("items"), expected_ids)
    if high_chars:
        # 미달은 수용(짧은 브리핑 > 없는 브리핑), 폭주는 문장 트림으로 봉쇄 —
        # 재요청 응답에는 더 물을 기회가 없다.
        lines, spoken = trim_to_budget(lines, high_chars)
        if spoken < high_chars * SECTION_FLOOR:
            print(f"[audio] 분량 경고 — 재요청 후에도 {spoken}자 "
                  f"(목표 {high_chars}자) — 수용")
    return lines, spoken


def generate_script(briefing: dict, by_id: dict) -> str:
    """섹션 2회 호출(하이라이트/단신) → 코드 조립.

    오프닝·클로징은 여기서 붙이지 않는다 — apply_frame 이 단일 소유자다.
    전환 라인도 코드 소유 (frame_lines 패턴).
    """
    deep_ids, rest_ids = _issue_ids(briefing)
    deep_ids = [i for i in deep_ids if i in by_id]
    rest_ids = [i for i in rest_ids if i in by_id]
    if not deep_ids and not rest_ids:
        raise ValueError("재료에 이슈가 없음")
    deep_sec, rest_sec = section_budgets(len(deep_ids), len(rest_ids))
    min_mode = bool(rest_ids) and rest_sec / len(rest_ids) < REST_MIN_MODE_SEC
    # 검증은 실제 issue_id 가 아니라 재료에 적힌 위치 번호로 한다 — 재료가
    # 이슈당 번호 하나를 1:1 로 붙이므로 번호 집합 완전성 = 이슈 완전성이다.
    deep_lines, deep_spoken = generate_section(
        SYSTEM_PROMPT_DEEP,
        build_deep_material(briefing, by_id, deep_ids, deep_sec),
        _alias_ids(deep_ids), high_chars=int(deep_sec * CHARS_PER_SEC),
        ceil_ratio=SECTION_CEIL)
    rest_prompt = SYSTEM_PROMPT_REST_MIN if min_mode else SYSTEM_PROMPT_REST
    rest_lines, rest_spoken = generate_section(
        rest_prompt, build_rest_material(rest_sec, by_id, rest_ids),
        _alias_ids(rest_ids), high_chars=int(rest_sec * CHARS_PER_SEC),
        ceil_ratio=SECTION_CEIL)
    lines = list(deep_lines)
    spoken = deep_spoken + rest_spoken
    if deep_lines and rest_lines:
        lines.append(TRANSITION_LINE)
        spoken += len(TRANSITION_LINE.split(":", 1)[1].strip())
    lines.extend(rest_lines)
    # 상한은 유예를 두고 진짜 폭주만 막는다 — 10~11분대 오디오는 짧은 초과일
    # 뿐 사고가 아니고, 여기서 raise 하면 그날 오디오가 통째로 사라진다.
    if spoken > MAX_SPOKEN * MAX_SPOKEN_GRACE:
        raise ValueError(f"대사 합계 {spoken}자 — 상한 유예까지 초과, 폭주 차단")
    if spoken > MAX_SPOKEN:
        print(f"[audio] 대사 합계 {spoken}자 — 상한 {MAX_SPOKEN}자 초과지만 "
              "유예 내라 수용")
    return "\n".join(lines)


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


class RetryBudget:
    """재시도 sleep 의 스테이지 공유 예산 (NucBrief 이식).

    재시도×모델×청크가 곱으로 불어나면 sleep 만 몇 분씩 쌓인다 — 스테이지
    전체가 풀 하나를 나눠 쓰면 총 대기 시간이 상수로 캡된다.
    """

    def __init__(self, total: float):
        self.remaining = float(total)

    def take(self, seconds: float) -> float:
        granted = max(0.0, min(float(seconds), self.remaining))
        self.remaining -= granted
        return granted


_last_tts_at = 0.0


def _pace_tts() -> None:
    """TTS 호출 간 최소 간격 강제 — 프리뷰 TTS 무료 티어는 분당 3회 수준이다.

    청크 2개 시절엔 연속 호출이 운으로 살았지만 6개는 못 산다.
    """
    # ponytail: 단일 스레드 타임스탬프 페이서 — 스레드가 생기면 NucBrief 의 락 페이서로
    global _last_tts_at
    wait = _last_tts_at + TTS_MIN_INTERVAL_SEC - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_tts_at = time.monotonic()


def call_tts(script: str, models: list[str] | None = None,
             budget: RetryBudget | None = None,
             deadline: float | None = None) -> tuple[bytes, int]:
    """단일 화자 합성 → (PCM s16le, sample rate).

    models 를 주면 그 목록만 쓴다 — 한 대본 안에서 모델이 섞이지 않게
    synthesize 가 모델을 고정해 내려보낸다.

    한 모델 안에서 최대 TTS_CHUNK_RETRIES 회 재시도한다. 429 는 서버가
    알려주는 대기 시간을 그대로 자되(무료 티어는 분당 한도라 지수 백오프는
    같은 창을 두드린다), sleep 은 budget·deadline 양쪽에 클램프된다.
    일일 한도는 재시도 없이 즉시 넘긴다 — 오늘 안 풀린다.

    실패 GeminiError 에는 .reason(rate_limit|daily_quota|provider_error)이
    실린다 — 부분 배송 메타의 partial_reason 이 이 값을 쓴다.
    """
    last_err: GeminiError | None = None
    for model in (models or _tts_models()):
        for attempt in range(TTS_CHUNK_RETRIES):
            _pace_tts()
            url = _TTS_ENDPOINT.format(model=model)
            request = urllib.request.Request(
                url,
                data=json.dumps(tts_payload(script)).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": gemini_client.API_KEY or ""},
            )
            retryable = False
            delay = 20.0
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    payload = json.loads(response.read())
                part = payload["candidates"][0]["content"]["parts"][0]
                mime = part["inlineData"]["mimeType"]
                pcm = base64.b64decode(part["inlineData"]["data"])
                match = re.search(r"rate=(\d+)", mime)
                rate = int(match.group(1)) if match else 24000
                if not pcm:
                    raise KeyError("오디오 0바이트")
                print(f"[audio] TTS {model} — {len(pcm) / 1024:.0f} KB, rate {rate}")
                return pcm, rate
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_err = GeminiError(f"{model}: HTTP {exc.code} {detail[:200]}")
                if exc.code == 429 and gemini_client._is_daily_quota(detail):
                    last_err.reason = "daily_quota"
                    print(f"[audio] {last_err} — 일일 한도, 재시도 없이 넘김")
                    break
                last_err.reason = ("rate_limit" if exc.code == 429
                                   else "provider_error")
                retryable = True
                delay = gemini_client._retry_delay_seconds(detail) or 20.0
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError,
                    json.JSONDecodeError) as exc:
                last_err = GeminiError(f"{model}: {type(exc).__name__}: {exc}")
                last_err.reason = "provider_error"
                retryable = True
                delay = 0.0   # 네트워크류는 대기 없이 — 페이서 간격이면 충분
            if not retryable or attempt + 1 >= TTS_CHUNK_RETRIES:
                print(f"[audio] {last_err} — 재시도 소진")
                break
            usable = delay
            if deadline is not None:
                usable = min(usable, max(0.0, deadline - time.monotonic()))
            granted = budget.take(usable) if budget else usable
            if delay > 0 and granted <= 0:
                print(f"[audio] {last_err} — 재시도 예산/데드라인 소진")
                break
            print(f"[audio] {last_err} — {granted:.0f}초 후 재시도 "
                  f"({attempt + 2}/{TTS_CHUNK_RETRIES})")
            if granted > 0:
                time.sleep(granted)
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


def _join_pieces(pieces: list[bytes], rate: int) -> bytes:
    """청크 PCM 을 CHUNK_GAP_SEC 간격으로 이어붙인다."""
    out: list[bytes] = []
    for index, piece in enumerate(pieces):
        if index:
            out.append(b"\x00" * (int(rate * CHUNK_GAP_SEC) * 2))
        out.append(piece)
    return b"".join(out)


def synthesize(script: str, tts_deadline: float | None = None,
               ) -> tuple[bytes, int, int, int, str | None]:
    """대본을 청크로 나눠 합성 → (PCM, rate, 완료 청크, 전체 청크, 중단 사유).

    청크 PCM 은 전부 같은 포맷(s16le mono, 같은 rate)이라 바이트 연결로 충분하다.
    레이트가 섞이면 이어붙인 결과가 배속으로 재생되므로 그 청크는 실패로 친다.

    폴백 정책 (NucBrief 이식): 핀 모델 안에서 청크당 재시도를 먼저 쓰고,
    모델을 갈아탈 때 완료 청크가 RESTART_THRESHOLD 미만이면 처음부터
    다시 만들지만(깨끗한 재시작이 쌈), 이상이면 실패한 청크부터 **이어받는다**.
    처음부터 정책만 있으면 7세그먼트가 21렌더가 되고(NucBrief 실측) 그
    재렌더가 쿼터·시간을 태워 꼬리 잘림의 최대 원인이 된다 — 3분 지점의
    음색 seam 이 꼬리 잘림보다 낫다.

    deadline 을 넘기면 신규 호출 없이 멈춘다 — 완성분은 부분 배송된다.
    0청크일 때만 raise.
    """
    chunks = split_script(script)
    print(f"[audio] 대본 {len(script)}자 → TTS 청크 {len(chunks)}개")
    deadline = (tts_deadline if tts_deadline is not None
                else time.monotonic() + TTS_HARD_BUDGET_SEC)
    budget = RetryBudget(TTS_RETRY_BUDGET_SEC)
    pieces: list[bytes] = []
    models_used: set[str] = set()
    rate = 0
    stop_reason: str | None = None
    last_err: GeminiError | None = None
    for model in _tts_models():
        if len(pieces) == len(chunks) or stop_reason == "hard_deadline":
            break
        if pieces and len(pieces) < RESTART_THRESHOLD:
            print(f"[audio] 완료 {len(pieces)}청크뿐 — {model} 로 처음부터 재시작")
            pieces, models_used, rate = [], set(), 0
        for index in range(len(pieces) + 1, len(chunks) + 1):
            if time.monotonic() + TTS_MIN_INTERVAL_SEC + TTS_CALL_EST_SEC > deadline:
                stop_reason = "hard_deadline"
                print(f"[audio] TTS 데드라인 — {len(pieces)}/{len(chunks)} 에서 중단")
                break
            chunk = chunks[index - 1]
            try:
                pcm, chunk_rate = call_tts(chunk, models=[model],
                                           budget=budget, deadline=deadline)
                if rate and chunk_rate != rate:
                    raise GeminiError(
                        f"청크 {index} 샘플레이트 불일치: {chunk_rate} != {rate}")
                _check_not_truncated(index, chunk, pcm, chunk_rate)
            except GeminiError as exc:
                last_err = exc
                stop_reason = getattr(exc, "reason", "provider_error")
                print(f"[audio] {model} 청크 {index} 실패 — 다음 모델: {exc}")
                break
            rate = chunk_rate
            pieces.append(trim_silence(pcm, rate))
            models_used.add(model)
            stop_reason = None
    if not pieces:
        raise last_err or GeminiError("TTS 모델 전부 실패")
    if len(models_used) > 1:
        print(f"[audio] 경고 — 모델 {len(models_used)}개가 한 파일에 기여 "
              f"(음색 seam 가능): {sorted(models_used)}")
    if len(pieces) < len(chunks) and stop_reason is None:
        stop_reason = "provider_error"
    return (_join_pieces(pieces, rate), rate, len(pieces), len(chunks),
            stop_reason if len(pieces) < len(chunks) else None)


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


def condense_script(script: str) -> str:
    """expert 대본 → 약 3분 빠른 브리핑 대본 (LLM 1회, 재료 재구성 없음).

    이미 검증된 expert 대본만 입력으로 쓴다 — 사실 검증을 두 번 하지 않고,
    build 재료를 다시 조립하는 경로도 만들지 않는다.
    """
    spoken = "\n".join(
        match.group(2) for match in
        (SPEAKER_RE.match(line) for line in script.splitlines()) if match
    )
    result = call_json(
        FAST_SYSTEM_PROMPT, f"[원문 대본]\n{spoken}", temperature=0.4,
        max_output_tokens=4096, timeout=120.0, thinking_budget=0,
        model=_script_model(), retries=FAST_RETRIES, label="audio_fast")
    paragraphs = [strip_filler(str(p).strip())
                  for p in (result.get("paragraphs") or []) if str(p).strip()]
    if not paragraphs:
        raise ValueError("빠른 브리핑: 빈 출력")
    lines = [f"HOST: {_LABEL_RE.sub('', p)}" for p in paragraphs]
    lines, total = trim_to_budget(lines, FAST_TARGET_CHARS)
    if total < FAST_MIN_CHARS:
        raise ValueError(f"빠른 브리핑: {total}자로 과소 (하한 {FAST_MIN_CHARS})")
    return "\n".join(lines)


def generate_fast_variant(script: str, briefing: dict, meta: dict,
                          run_started_at: float) -> bool:
    """fast variant 생성·기록. expert-only v2 메타가 이미 디스크에 있는 뒤에만
    부른다 — 여기서의 어떤 실패도 비치명이고 expert 를 건드리지 않는다."""
    date = meta["date"]
    remaining = run_started_at + AUDIO_RUN_BUDGET_SEC - time.monotonic()
    if remaining < FAST_MIN_REMAINING_SEC:
        print(f"[audio] fast 스킵 — 잔여 예산 {remaining:.0f}초 "
              f"(< {FAST_MIN_REMAINING_SEC}초)")
        return False
    fast_file = f"briefing-{date}-fast.mp3"
    try:
        fast_script = apply_frame(condense_script(script), briefing)
        (AUDIO_DIR / f"script-{date}-fast.txt").write_text(fast_script, encoding="utf-8")
        deadline = min(
            time.monotonic() + FAST_TTS_BUDGET_SEC,
            run_started_at + AUDIO_RUN_BUDGET_SEC - SHIP_RESERVE_SEC,
        )
        pcm, rate, done, total, _stop = synthesize(fast_script, deadline)
        if done < total:
            # 3분짜리가 중간에 끊기면 브리핑이 아니다 — expert 완본이 있으니 버린다.
            print(f"[audio] fast 부분 생성({done}/{total}) — 버리고 expert 만 유지")
            return False
        to_mp3(pcm, rate, AUDIO_DIR / fast_file)
    except (GeminiError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[audio] fast 실패 — expert 만 유지: {exc}")
        return False
    meta.setdefault("variants", {})["fast"] = {
        "date": date,
        "key": "fast",
        "label": FAST_LABEL,
        "description": FAST_DESCRIPTION,
        "file": fast_file,
        "duration_sec": int(len(pcm) / 2 / rate),
        "generated_at": datetime.now(KST).isoformat(),
        "script_chars": sum(
            len(line.split(":", 1)[1]) for line in fast_script.splitlines()),
        "voices": VOICES,
    }
    _write_meta(meta)
    print(f"[audio] fast 완료 — {fast_file} "
          f"({meta['variants']['fast']['duration_sec']}초)")
    return True


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
    if meta.get("partial"):
        caption = (
            f"🎧 {meta.get('date', '')} 오디오 브리핑 "
            f"(부분 {meta.get('chunks_done')}/{meta.get('chunks_total')} · "
            f"{minutes}분 {seconds:02d}초)\n"
            "전체 내용은 nuclens.pages.dev"
        )
    else:
        caption = (
            f"🎧 {meta.get('date', '')} 오디오 브리핑 ({minutes}분 {seconds:02d}초)\n"
            "하이라이트 심층 + 전체 헤드라인 · nuclens.pages.dev"
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
    run_started_at = time.monotonic()
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
        # 부분본도 여기 걸린다 — 자동 업그레이드는 하지 않는다(쿼터 보호),
        # 수동 업그레이드는 workflow_dispatch force_audio.
        if not existing.get("telegram_sent_at"):
            if send_telegram_audio(mp3_path, existing):
                _mark_sent(existing)
        else:
            print(f"[audio] {date} 이미 생성·발송됨 ({file_name}) — 스킵")
        return True

    try:
        script = generate_script(briefing, by_id)
    except (GeminiError, ValueError) as exc:
        print(f"[audio] 대본 실패 — 기존 오디오 유지: {exc}")
        return False
    script = apply_frame(script, briefing)

    # 대본을 TTS **전에** 남긴다 — TTS 가 부분 실패해도 전문은 항상 살아서
    # 부분 오디오의 나머지를 텍스트로 보완한다 (진단 겸용, issue_audit 패턴).
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    (AUDIO_DIR / f"script-{date}.txt").write_text(script, encoding="utf-8")

    tts_deadline = min(
        time.monotonic() + TTS_HARD_BUDGET_SEC,
        run_started_at + AUDIO_RUN_BUDGET_SEC - SHIP_RESERVE_SEC,
    )
    try:
        pcm, rate, done, total, stop_reason = synthesize(script, tts_deadline)
    except GeminiError as exc:
        print(f"[audio] TTS 실패 — 기존 오디오 유지: {exc}")
        return False

    try:
        to_mp3(pcm, rate, mp3_path)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[audio] mp3 변환 실패 — 기존 오디오 유지: {exc}")
        return False

    duration = int(len(pcm) / 2 / rate)
    generated_at = datetime.now(KST).isoformat()
    script_chars = sum(len(line.split(":", 1)[1]) for line in script.splitlines())
    # v1 톱레벨 필드는 전부 유지한다(구 프런트·캐시 호환) — expert 를 미러링.
    # v2 는 format_version + variants 만 얹는다.
    meta = {
        "date": date,
        "file": file_name,
        "duration_sec": duration,
        "generated_at": generated_at,
        "script_chars": script_chars,
        "voices": VOICES,
        "format_version": 2,
        "variants": {
            "expert": {
                "date": date,
                "key": "expert",
                "label": EXPERT_LABEL,
                "description": EXPERT_DESCRIPTION,
                "file": file_name,
                "duration_sec": duration,
                "generated_at": generated_at,
                "script_chars": script_chars,
                "voices": VOICES,
            },
        },
    }
    if done < total:
        # 사유·재시도 가능성을 남긴다 — 운영 판단(수동 재생성 여부)의 근거.
        # daily_quota 만 재시도 불가 — 오늘 안 풀린다.
        partial_fields = {
            "partial": True,
            "chunks_done": done,
            "chunks_total": total,
            "partial_reason": stop_reason or "provider_error",
            "retryable": stop_reason != "daily_quota",
        }
        meta.update(partial_fields)
        meta["variants"]["expert"].update(partial_fields)
    _write_meta(meta)
    # 옛 날짜 산출물 정리 — 캐시·배포에 실리는 것은 최신 날짜 것이면 충분하다.
    # 오늘 것은 fast 포함 둘 다 지키지 않으면 --force 재생성이 fast 를 먹는다.
    keep_mp3 = {file_name, f"briefing-{date}-fast.mp3"}
    keep_script = {f"script-{date}.txt", f"script-{date}-fast.txt"}
    for old in AUDIO_DIR.glob("briefing-*.mp3"):
        if old.name not in keep_mp3:
            old.unlink(missing_ok=True)
    for old in AUDIO_DIR.glob("script-*.txt"):
        if old.name not in keep_script:
            old.unlink(missing_ok=True)
    label = f"부분 {done}/{total}" if done < total else "완료"
    print(f"[audio] {date} {label} — {file_name} "
          f"({mp3_path.stat().st_size / 1024:.0f} KB, {duration}초)")
    if send and send_telegram_audio(mp3_path, meta):
        _mark_sent(meta)
    # fast 는 expert 가 완본일 때만 — 부분본이 났다는 것은 쿼터가 이미 빠듯하다는
    # 뜻이라 추가 호출이 내일 몫까지 굶긴다.
    if done == total:
        generate_fast_variant(script, briefing, meta, run_started_at)
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
