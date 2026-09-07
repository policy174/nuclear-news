"""스토리(chronicle) 국면형 서사 생성 — 원장의 사건 타임라인을 읽을거리로.

report_draft.py 와 같은 post-build 생성층 계약:
    - build_data 는 결정적(LLM 0회)으로 남긴다. 이 모듈은 빌드 뒤에 돌며 원장
      (chronicles.json)을 읽어 별도 파일을 낸다. 프런트가 chronicle_id 로 조인.
    - 대상은 오늘 이벤트가 추가된 연대기만 + 회차 상한. 전 연대기에 돌리면
      무료 쿼터가 뉴스봇 크론을 굶는다.
    - 전용 모델 버킷(GEMINI_CHRONICLE_MODEL, 기본 gemini-3.5-flash — 2026-09-07
      실호출 200 확인, 미사용 버킷). model 을 명시하므로 gemini_client 의 기본
      경로 체인을 타지 않는다 — 큐레이션·발송과 쿼터 경합 0.
    - 문체는 보고서 개조식의 **반대**: 국면형 서사(발단→전개→현재 국면)를
      완결 서술문으로. 개조식 gate 는 재사용하지 않고 위생 규칙만 공유한다.

가드레일:
    - 타임라인에 나온 사실만. 없는 수치·일정·기관을 지어내지 않는다.
    - stdlib + gemini_client + llm_cache 만 사용. build_data import 금지(순환 방지).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import llm_cache

ROOT = Path(__file__).parent
# 원장은 repo root — web/public/data/chronicles.json(배포 사본)과 이름이 같다.
# 사본을 읽으면 gitignore 산출물이라 매 빌드 초기화된 것처럼 보인다.
LEDGER_FILE = ROOT / "chronicles.json"
CACHE_FILE = ROOT / "chronicle_narratives.json"
PUBLIC_FILE = ROOT / "web" / "public" / "data" / "chronicle_narratives.json"

# 프롬프트를 고치면 올린다 — 옛 서사가 자동 무효.
PROMPT_VERSION = 1
MAX_PER_RUN = 2
MAX_TIMELINE = 20
MAX_OUTPUT_TOKENS = 4096
CHRONICLE_MODEL_DEFAULT = "gemini-3.5-flash"

SYSTEM_PROMPT = """당신은 원자력 정책 전문 기자입니다. 아래 사건 타임라인을 재료로,
이 사안이 어떻게 흘러왔는지를 **국면형 서사**로 씁니다. 독자는 이 사안을 처음 보는
사내 구성원 — 지금까지의 흐름을 한 호흡에 따라잡게 하는 것이 목적입니다.

작성 규칙 (전부 필수):
- narrative: 2~4개 문단. 발단 → 전개 → 현재 국면 순서로, 각 문단은 완결된
  서술문("-다."로 종결). 문단마다 시점(날짜·기간)이 드러나야 합니다.
- phase_now: 지금 이 사안이 어느 국면인지 한 문장 (예: "재가동 승인 후 안정화 단계",
  "법안 발의 후 상임위 심사 대기"). 서술문으로 종결.
- watchpoints: 다음에 지켜볼 지점 1~2개, 각 한 문장.
- 수치·일정·기관명·인명은 타임라인 원문에 있는 것만. 지어내지 않습니다.
  타임라인이 침묵하는 기간은 "이후 N주간 후속 보도 없음"처럼 사실로만 씁니다.
- 금지: 줄표(—), 볼드(**), 이모지, "귀추가 주목"·"주목할 만" 류 클리셰,
  □·○ 같은 개조식 기호, 과장·추측 표현("~것으로 보인다" 남발 금지).

출력은 정확히 아래 JSON 하나. 다른 텍스트·펜스 금지.
{"phase_now": "...", "narrative": ["문단1", "문단2"], "watchpoints": ["...", "..."]}"""

# 위생 규칙 — report_draft._BANNED 와 같은 계열 + 개조식 기호 금지.
_BANNED = re.compile(r"—|\*\*|귀추가 주목|주목할 만|[□○]|[\U0001F300-\U0001FAFF]")
# 서사는 완결 서술문이어야 한다 — 개조식 게이트(_NARRATIVE_END)의 정반대.
_SENTENCE_END = re.compile(r"(다|음|함)\s*[.。]\s*$")


def gate(payload: dict) -> list[str]:
    """위반 목록. 비어 있으면 통과."""
    problems: list[str] = []
    narrative = [str(p).strip() for p in (payload.get("narrative") or []) if str(p).strip()]
    phase = str(payload.get("phase_now") or "").strip()
    if not narrative:
        problems.append("narrative 비어 있음")
    if not (2 <= len(narrative) <= 4):
        problems.append(f"narrative 문단 수 {len(narrative)} — 2~4개여야 함")
    if not phase:
        problems.append("phase_now 없음")
    for label, texts in (("narrative", narrative), ("phase_now", [phase] if phase else []),
                         ("watchpoints", [str(w).strip() for w in payload.get("watchpoints") or []])):
        for text in texts:
            if _BANNED.search(text):
                problems.append(f"{label}: 금지 표기(줄표·볼드·클리셰·개조식 기호)")
            if label != "watchpoints" and not _SENTENCE_END.search(text):
                problems.append(f"{label}: 완결 서술문(-다.)으로 끝나지 않음")
    return problems


# ---- 재료 --------------------------------------------------------------------

def _digest(chron: dict) -> str:
    hashes = sorted(str(e.get("hash") or "") for e in chron.get("events") or [])
    seed = "|".join([str(chron.get("chronicle_id") or ""), *hashes])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _timeline_text(chron: dict) -> str:
    rows = sorted(
        chron.get("events") or [],
        key=lambda e: (str(e.get("article_date") or ""), str(e.get("hash") or "")),
    )[-MAX_TIMELINE:]
    lines = []
    for event in rows:
        date = str(event.get("article_date") or "")[:10]
        title = str(event.get("title_kr") or "").replace("\n", " ")
        publisher = str(event.get("publisher") or "")
        lines.append(f"- {date} {title} ({publisher})")
    return "\n".join(lines)


def _user_message(chron: dict) -> str:
    return "\n".join([
        f"사안 제목: {chron.get('title', '')}",
        f"추적 기간: {chron.get('first_seen', '')} ~ {chron.get('last_seen', '')}",
        "사건 타임라인 (오래된 순):",
        _timeline_text(chron),
    ])


def _targets(chronicles: dict, today: datetime) -> list[dict]:
    """오늘 이벤트가 추가된 연대기만 — 조용한 연대기는 캐시된 서사로 충분하다."""
    today_str = today.astimezone(timezone.utc).date().isoformat()
    rows = [
        chron for chron in chronicles.values()
        if str(chron.get("updated_at") or "")[:10] == today_str
        and len(chron.get("events") or []) >= 2
    ]
    rows.sort(key=lambda c: str(c.get("last_seen") or ""), reverse=True)
    return rows


def _resolve_model() -> str:
    try:
        import gemini_client  # noqa: PLC0415
    except ImportError:
        return os.environ.get("GEMINI_CHRONICLE_MODEL") or CHRONICLE_MODEL_DEFAULT
    return gemini_client._resolve("GEMINI_CHRONICLE_MODEL", CHRONICLE_MODEL_DEFAULT)


# ---- 생성 --------------------------------------------------------------------

def _ask(client, chron: dict) -> tuple[dict, list[str]]:
    """(서사 payload, 위반 목록). 게이트 실패 1회는 위반을 되먹여 재시도."""
    message = _user_message(chron)
    feedback = ""
    problems: list[str] = []
    for _ in range(2):
        payload = client.call_json(
            SYSTEM_PROMPT,
            message + feedback,
            temperature=0.3,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            model=_resolve_model(),
            label="chronicle_narrative",
        )
        problems = gate(payload if isinstance(payload, dict) else {})
        if not problems:
            return {
                "phase_now": str(payload.get("phase_now") or "").strip(),
                "narrative": [str(p).strip() for p in payload.get("narrative") or []],
                "watchpoints": [str(w).strip() for w in payload.get("watchpoints") or []][:2],
            }, []
        feedback = ("\n\n직전 출력의 위반 — 고쳐서 다시 작성:\n"
                    + "\n".join(problems[:8]))
    return {}, problems


def run(*, client=None, now: datetime | None = None, publish_only: bool = False) -> dict:
    """publish_only=True 면 LLM 호출 없이 캐시에서 공개 파일만 다시 쓴다 —
    매시간 crawl 배포가 서사 파일을 빠뜨리지 않게 하는 경로(쿼터 소비 0)."""
    now = now or datetime.now(timezone.utc)
    stats = {"targets": 0, "cached": 0, "generated": 0, "failed": 0, "status": "ok"}

    try:
        ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        chronicles = ledger.get("chronicles") or {}
    except (OSError, json.JSONDecodeError):
        chronicles = {}
    if not isinstance(chronicles, dict):
        stats["status"] = "bad_ledger_file"
        return stats

    targets = _targets(chronicles, now)
    stats["targets"] = len(targets)
    cache = llm_cache.load(CACHE_FILE, "narratives")

    if client is None and not publish_only:
        try:
            import gemini_client as client  # noqa: PLC0415
        except ImportError:
            client = None
    can_call = (not publish_only) and client is not None and client.is_available()

    calls = 0
    for chron in targets:
        cid = str(chron.get("chronicle_id") or "")
        digest = _digest(chron)
        entry = cache.get(cid)
        if (llm_cache.is_current(entry, PROMPT_VERSION)
                and entry.get("digest") == digest):
            stats["cached"] += 1
            continue
        if not can_call or calls >= MAX_PER_RUN:
            continue
        calls += 1
        try:
            narrative, problems = _ask(client, chron)
        except Exception as exc:  # noqa: BLE001 — 서사 부재는 비치명
            stats["failed"] += 1
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        # 실패도 캐시한다 — 같은 재료(digest)로 매 회차 재질의하지 않기 위해.
        cache[cid] = {
            "prompt_version": PROMPT_VERSION,
            "digest": digest,
            "narrative": narrative,
            "title": chron.get("title", ""),
            "problems": problems,
            "generated_at": now.isoformat(),
        }
        if narrative:
            stats["generated"] += 1
        else:
            stats["failed"] += 1

    if not publish_only:
        llm_cache.save(cache, CACHE_FILE, key="narratives", prompt_version=PROMPT_VERSION,
                       comment="스토리 국면형 서사 캐시 — chronicle_narrative.py")

    # 공개 파일에는 성공한 서사만, 원장에 아직 있는 연대기만 싣는다.
    narratives = {
        cid: {**entry["narrative"], "generated_at": entry.get("generated_at", "")}
        for cid, entry in cache.items()
        if entry.get("narrative") and cid in chronicles
        and llm_cache.is_current(entry, PROMPT_VERSION)
    }
    try:
        PUBLIC_FILE.write_text(
            json.dumps({"generated_at": now.isoformat(), "narratives": narratives},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    except OSError:
        stats["status"] = "write_failed"
    stats["published"] = len(narratives)
    return stats


if __name__ == "__main__":
    result = run(publish_only="--publish-only" in sys.argv)
    print(f"[chronicle_narrative] {result}")
    # 쿼터·재료 부재는 비치명(서사 없이 배포) — exit 0. 예상 밖 상태만 실패로.
    sys.exit(1 if result["status"] in ("bad_ledger_file", "write_failed") else 0)
