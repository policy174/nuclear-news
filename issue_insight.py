"""이슈 타임라인을 근거로 카드 두 번째 줄(= 이 뉴스가 무슨 뜻인가)을 만든다.

배경 (2026-08-05 사용자 지적):
    "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표
     → AI 헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다."
    "이거 보면 내용이 너무 없어. 사실 이게 가뭄 때문에 그런거고 그거에 대한
     팔로잉도 안되는거잖아."

    빈껍데기 해석을 지우기만 하면 그 자리가 비고, 카드는 제목을 바꿔 쓴 요약이나
    직전 브리핑 문장으로 물러난다. 사용자가 두 번째로 지적한 것이 그 대체물이다 —
    "직전 브리핑 내용이 왜 들어가, 그럴거면 그 전꺼를 보겠지." 맞는 말이다.
    필요한 것은 지운 자리를 **내용으로 채우는 것**이다.

    재료는 이미 있다. 기사 하나(로이터 헤드라인)에는 가뭄이 안 나오지만, 그 기사가
    속한 이슈 클러스터에는 다뉴브강 수위 저하부터 가동 중단 예고까지 다 들어 있다.
    그래서 이 해석은 **기사 단위가 아니라 이슈 단위**로 만든다. news_bot 의
    큐레이션 프롬프트로는 원리상 불가능한 일이다 — 그쪽은 기사 하나만 본다.

설계:
    - 타임라인이 2건 이상인 이슈만 대상. 1건짜리는 **맥락이 없으므로 만들지 않는다** —
      재료 없이 쓰게 하면 지금 고치려는 그 빈껍데기가 그대로 돌아온다.
    - 배치 1회 호출. 클러스터 내용(멤버 hash 집합)이 그대로면 다시 묻지 않는다.
      웹 빌드는 하루 12회 이상 도는데 이슈 대부분은 며칠씩 그대로다.
    - 실패는 조용히 통과. 해석이 없으면 카드가 요약으로 물러날 뿐이다.

가드레일:
    - **타임라인에 나온 사실만.** 없는 원인·수치·기관을 지어내지 않는다.
    - 제목을 바꿔 말하지 않는다. 제목이 '무엇'이면 이 문장은 '왜 그렇게 됐나'.
    - 예측·권고·투자 판단 금지 (trend_insights·daily_lead 와 같은 계약).
    - stdlib + gemini_client 만 사용. build_data 를 import 하지 않는다(순환 방지).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from data_quality import clean_text, implication_is_hollow

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "issue_insights.json"

# 프롬프트를 고치면 올린다. 캐시된 옛 문장이 자동으로 무효가 된다.
PROMPT_VERSION = 2

# 카드 두 번째 줄은 2줄(모바일 3줄)에서 잘린다. 잘린 분석문은 완결된 요약보다
# 나쁘다는 것이 이 저장소의 기존 판단이다(app.js issueCard 주석).
MAX_LENGTH = 90

BATCH_SIZE = 12
# 타임라인 2건 이상인 이슈는 실측 113개 중 27개다. 한 회차 상한을 넉넉히 두되
# 무한정 늘어나지 않게 막는다.
MAX_NEW_PER_RUN = 40
MAX_OUTPUT_TOKENS = 8192

# 무료 티어 쿼터는 모델별 버킷이다. 크롤 큐레이션·트렌드·리드가 쓰는 기본
# 2.5-flash 버킷에 얹으면 저녁마다 굶는다(issue_review 가 실제로 그렇게 죽었다).
INSIGHT_MODEL_DEFAULT = "gemini-2.5-flash-lite"

SYSTEM_PROMPT = """너는 한국수력원자력 정책 부서에 원자력 뉴스를 정리해 주는 편집자다.

이슈마다 제목과 **경과**(같은 사건을 다룬 기사들의 시간순 목록)를 준다.
읽는 사람이 제목만 보고는 "그래서 이게 무슨 얘기지?"에 답할 수 없는 경우가 많다.
경과를 근거로 그 답을 한 문장으로 쓴다.

각 이슈마다 insight 한 문장을 만든다.

규칙:
- 90자 이내, 완결형 서술문 한 문장. 문자열을 자르지 말 것.
- **제목을 바꿔 말하지 않는다.** 제목이 '무엇'이면 insight 는 '왜 그렇게 됐나',
  '무엇이 걸려 있었나', '다음은 무엇인가' 중 하나다.
    제목: 헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표
    나쁨: 헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다.
          (제목을 추상어로 바꿔 말했을 뿐 — 정보가 0이다)
    좋음: 다뉴브강 수위 저하로 냉각수 취수가 막혀 예고됐던 전면 정지를 피한 것이다.
          (경과에 있는 원인을 끌어와 제목이 왜 뉴스인지 설명한다)
- **경과에 적힌 사실만 쓴다.** 경과에 없는 원인·수치·기관·날짜를 지어내지 말 것.
- 예측·권고·투자 판단 금지. "주목해야 한다", "기대된다", "시사한다" 같은
  맺음말로 끝내지 말 것 — 그렇게 끝나는 문장은 대개 내용이 없다.
- 경과가 제목과 같은 말의 반복뿐이어서 더 보탤 사실이 없으면 **빈 문자열**로 둔다.
  억지로 채우지 말 것. 빈칸이 빈껍데기보다 낫다. 실제로 절반 가까이가 이 경우다 —
  빈 문자열은 실패가 아니라 정상 응답이다.
    제목: IAEA 사무총장, 우크라이나 상황 관련 성명 발표
    경과: (같은 성명 보도 2건뿐)
    나쁨: 우크라이나 내 원자력 시설의 안전 및 안보 상황에 대한 성명을 발표했다.
          (제목에 있는 말만 늘려 썼다)
    좋음: ""
    제목: 미국 에너지부, 원자력 혁신 캠퍼스 유치 후보지 5개 주 선정 발표
    나쁨: 원자력 라이프사이클 혁신 캠퍼스 유치를 위한 후보지로 5개 주를 선정했다.
          (제목의 동어반복 — '5개 주'도 제목에 이미 있다)
    좋음: ""

출력은 JSON 하나:
{"items": [{"idx": 0, "insight": "..."}]}
입력에 준 idx 를 모두 포함한다."""


# 제목 재진술 판정. 첫 실행 실측(2026-08-05, 7건)에서 프롬프트로 금지했는데도
# 4건이 제목을 바꿔 쓴 문장이었다 — 모델은 "쓸 말이 없으면 빈 문자열"보다 뭐라도
# 쓰는 쪽을 고른다.
#
# **유사도만으로는 안 갈린다.** 실측:
#   0.63 "한수원, 포천양수발전소 본공사 착수…2033년 준공 목표"
#      → "1조 7,508억 원 규모의 포천양수발전소 본공사가 시작되어…"   ← 좋은 문장
#   0.42 "IAEA 사무총장, 우크라이나 상황 관련 성명 발표"
#      → "우크라이나 내 원자력 시설의 안전 및 안보 상황에 대한 성명"  ← 재진술
# 겹침이 큰 쪽이 오히려 정보를 담고 있었다. 갈라주는 것은 **제목에 없는 수치**다.
_RESTATEMENT_RATIO = 0.55
_NON_WORD_RE = re.compile(r"[^가-힣A-Za-z0-9]")
# 수량·시점 표지가 붙은 숫자만 센다(data_quality 와 같은 판단 — 맨 숫자를 세면
# 'AP1000'·'5개 주' 같은 이름·제목 복사분이 전부 통과한다).
_QUANTITY_RE = re.compile(
    r"\d[\d,.]*\s*(?:년|년대|월|일|분기|%|퍼센트|억|조|만|천|배|MW|GW|kW|TWh|MWh"
    r"|기|개|건|차|호기|명|달러|원|유로)"
)


def _restates_title(insight: str, title: str) -> bool:
    """제목을 바꿔 썼을 뿐이면 참 — 단, 제목에 없는 수치가 있으면 살린다."""
    left = _NON_WORD_RE.sub("", insight)
    right = _NON_WORD_RE.sub("", title)
    if not left or not right:
        return False
    if difflib.SequenceMatcher(None, left, right).ratio() < _RESTATEMENT_RATIO:
        return False
    title_quantities = set(_QUANTITY_RE.findall(title))
    return not (set(_QUANTITY_RE.findall(insight)) - title_quantities)


def _resolve_model() -> str:
    try:
        import gemini_client  # noqa: PLC0415
    except ImportError:
        return os.environ.get("GEMINI_INSIGHT_MODEL") or INSIGHT_MODEL_DEFAULT
    return gemini_client._resolve("GEMINI_INSIGHT_MODEL", INSIGHT_MODEL_DEFAULT)


def issue_timeline(row: dict) -> list[dict]:
    """카탈로그 행이 이미 싣고 있는 시간순 기사 목록."""
    return [article for article in (row.get("related_articles") or [])
            if isinstance(article, dict)]


def timeline_digest(members: list[dict]) -> str:
    """클러스터 내용 지문. 멤버가 늘거나 바뀌면 달라진다."""
    hashes = sorted(str(member.get("hash") or "") for member in members)
    raw = "|".join(hashes) + f"|v{PROMPT_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_cache(path: Path = CACHE_FILE) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("insights") if isinstance(raw, dict) else None
    return entries if isinstance(entries, dict) else {}


def save_cache(cache: dict, path: Path = CACHE_FILE) -> None:
    payload = {
        "_comment": "이슈 단위 카드 해석 캐시. 키는 issue_id, digest 가 다르면 다시 묻는다.",
        "prompt_version": PROMPT_VERSION,
        "insights": cache,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def needs_insight(row: dict) -> bool:
    """해석이 비었거나 빈껍데기이고, 타임라인에 재료가 있는 이슈."""
    if len(issue_timeline(row)) < 2:
        # 기사 1건짜리는 끌어올 맥락이 없다. 재료 없이 쓰게 하면 빈껍데기가 돌아온다.
        return False
    current = clean_text(row.get("implication"))
    return not current or implication_is_hollow(current)


def apply(rows: list[dict], insights: dict[str, str]) -> int:
    """이미 만들어 둔 해석을 행에 심는다. 새로 묻지 않는다.

    같은 이슈가 브리핑 행(날짜별)과 카탈로그 행(전체)에 따로 만들어지는데,
    브리핑 행의 타임라인은 그날까지의 부분집합이라 digest 가 다르다. 그 행으로
    다시 물으면 같은 이슈를 날짜 수만큼 중복 질의하게 된다 — 생성은 카탈로그
    행에서 한 번만 하고, 여기서는 issue_id 로 나눠 준다.
    """
    applied = 0
    for row in rows:
        insight = insights.get(str(row.get("issue_id") or ""))
        if not insight:
            continue
        current = clean_text(row.get("implication"))
        if current and not implication_is_hollow(current):
            continue
        row["implication"] = insight
        # 어디서 온 문장인지 남긴다 — 기사 단위 큐레이션과 섞이면 나중에 품질
        # 문제를 어느 프롬프트에서 고쳐야 할지 알 수 없다.
        row["implication_source"] = "issue_timeline"
        applied += 1
    return applied


def build_user_message(rows: list[dict]) -> str:
    blocks = []
    for index, row in enumerate(rows):
        lines = [f"[{index}] 제목: {row.get('title', '')}"]
        summary = clean_text(row.get("summary"))
        if summary:
            lines.append(f"    요약: {summary}")
        lines.append("    경과:")
        for member in issue_timeline(row):
            date = member.get("article_date") or member.get("briefing_date") or ""
            title = clean_text(member.get("title_kr") or member.get("title"))
            member_summary = clean_text(member.get("summary"))
            lines.append(f"      {date} {title}")
            if member_summary and member_summary != title:
                lines.append(f"        {member_summary}")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


def _parse(payload: object, rows: list[dict]) -> tuple[dict[int, str], dict[str, int]]:
    out: dict[int, str] = {}
    rejected = {"too_long": 0, "hollow": 0, "restates_title": 0}
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(rows):
            continue
        insight = " ".join(str(item.get("insight") or "").split()).strip()
        if not insight:
            continue
        if len(insight) > MAX_LENGTH:
            # 길이 초과분은 자르지 않는다 — 잘린 분석문은 완결된 요약보다 나쁘다.
            rejected["too_long"] += 1
            continue
        if implication_is_hollow(insight):
            # 프롬프트가 금지한 맺음말이 그대로 나온 경우. 쓰지 않는다.
            rejected["hollow"] += 1
            continue
        if _restates_title(insight, str(rows[idx].get("title") or "")):
            rejected["restates_title"] += 1
            continue
        out[idx] = insight
    return out, rejected


def generate(rows: list[dict], *, client=None, cache_path: Path = CACHE_FILE,
             batch_size: int = BATCH_SIZE,
             max_new: int = MAX_NEW_PER_RUN) -> tuple[dict[str, str], dict]:
    """rows(이슈 카탈로그 행)에 대한 {issue_id: insight} 와 통계.

    반환된 문장만 쓴다. 없는 이슈는 화면이 요약으로 물러난다.
    """
    stats = {"candidates": 0, "from_cache": 0, "asked": 0, "calls": 0,
             "deferred": 0, "failed": 0, "empty": 0, "status": "ok",
             "rejected": {}, "model": "", "prompt_version": PROMPT_VERSION}
    candidates = [row for row in rows if needs_insight(row)]
    stats["candidates"] = len(candidates)
    if not candidates:
        stats["status"] = "no_candidates"
        return {}, stats

    cache = load_cache(cache_path)
    insights: dict[str, str] = {}
    todo: list[dict] = []
    for row in candidates:
        issue_id = str(row.get("issue_id") or "")
        digest = timeline_digest(issue_timeline(row))
        entry = cache.get(issue_id)
        if isinstance(entry, dict) and entry.get("digest") == digest:
            stats["from_cache"] += 1
            if entry.get("insight"):
                insights[issue_id] = entry["insight"]
            continue
        todo.append({**row, "_digest": digest})

    if len(todo) > max_new:
        # 최신 이슈부터. 밀린 것은 다음 빌드에서 저절로 빠진다.
        todo.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
        stats["deferred"] = len(todo) - max_new
        stats["status"] = "throttled"
        todo = todo[:max_new]

    if todo:
        if client is None:
            try:
                import gemini_client as client  # noqa: PLC0415
            except ImportError:
                client = None
        if client is None or not client.is_available():
            stats["status"] = "no_api_key"
            stats["failed"] = len(todo)
            return insights, stats

    model = _resolve_model()
    stats["model"] = model
    now = datetime.now(timezone.utc).isoformat()
    dirty = False

    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        try:
            payload = client.call_json(
                SYSTEM_PROMPT,
                build_user_message(chunk),
                temperature=0.1,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — 해석 부재는 비치명
            stats["failed"] += len(chunk)
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        stats["calls"] += 1
        parsed, rejected = _parse(payload, chunk)
        for reason, count in rejected.items():
            if count:
                stats["rejected"][reason] = stats["rejected"].get(reason, 0) + count
        for index, row in enumerate(chunk):
            issue_id = str(row.get("issue_id") or "")
            insight = parsed.get(index, "")
            if insight:
                insights[issue_id] = insight
                stats["asked"] += 1
            else:
                stats["empty"] += 1
            # 빈 결과도 캐시한다. 안 그러면 재료 없는 이슈를 매 빌드마다 다시 묻는다.
            cache[issue_id] = {
                "digest": row["_digest"],
                "insight": insight,
                "title": row.get("title", ""),
                "prompt_version": PROMPT_VERSION,
                "model": model,
                "generated_at": now,
            }
            dirty = True

    # len(cache) 증가로 판정하면 덮어쓰기가 영영 저장되지 않는다(2026-08-02 게토차).
    if dirty:
        save_cache(cache, cache_path)
    return insights, stats
