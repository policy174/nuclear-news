"""이슈 병합 회색지대를 LLM 이 한 번에 판정한다.

배경:
    임계값 하나로는 "같은 사건"과 "같은 분야"가 갈리지 않는다. 실측(21일, 병합
    64쌍)에서 코사인 0.92 이상은 거의 전부 같은 사건이고 0.88 미만은 거의 전부
    다른 사건인데, 그 사이 7쌍에 국내 계속운전·전기본 후속처럼 진짜 이어지는
    이슈가 몰려 있었다. 임계값을 0.92 로 올리면 오병합은 사라지지만 이 구간도
    같이 잘린다.

    사람 검토 큐(issue_match_overrides.json)는 이미 있지만 138건이 전부 pending
    이라 실질 검수가 되지 않는다. 이 구간만 LLM 에게 묻는다.

설계:
    - 회색지대 쌍만 모아 배치 1회 호출. 실측 하루 0.33쌍이라 보통 호출 0~1회.
    - 판정은 issue_llm_reviews.json 에 캐시한다. 웹 빌드는 하루 12회 이상 돌기
      때문에 캐시가 없으면 같은 쌍을 하루에 열두 번 묻게 된다.
    - 키가 없거나 호출이 실패하면 **병합하지 않는다**. false merge 가 누락보다
      해롭다는 issue_similarity 의 원칙을 그대로 따른다. 판정 실패는 캐시하지
      않으므로 다음 빌드에서 다시 시도한다.

가드레일:
    - stdlib + gemini_client 만 사용. build_data 를 import 하지 않는다(순환 방지).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "issue_llm_reviews.json"

# 자동 병합(>=0.92)과 자동 분리(<0.88) 사이. build_data.ISSUE_EMBEDDING_THRESHOLD
# 를 올리면 REVIEW_BAND_HIGH 도 같이 올려야 한다.
REVIEW_BAND_LOW = 0.88
REVIEW_BAND_HIGH = 0.92

# 프롬프트를 고치면 올린다. 캐시된 옛 판정이 자동으로 무효가 된다.
PROMPT_VERSION = 1

# 한 번에 묻는 쌍 수. 한국어 판정 한 줄이 40~60 토큰이라 20쌍이면 출력이
# 1,500 토큰 안쪽이다. thinking 토큰이 출력 예산을 먹으므로 여유를 크게 둔다.
BATCH_SIZE = 20

SYSTEM_PROMPT = """너는 원자력 산업 뉴스를 정리하는 편집자다.
두 기사가 **같은 사건**을 다루는지 판정한다.

같은 사건이다 (same_event: true):
- 동일한 주체가 동일한 대상에 대해 벌인 하나의 사안
- 그 사안의 후속 보도. 진행 단계가 바뀐 것은 같은 사건이다
  (심의 착수 → 심의 지연 → 승인, 협상 → 계약 체결)
- 같은 사안을 다른 매체가 다시 쓴 것

다른 사건이다 (same_event: false):
- 주체는 같지만 안건이 다르다 (같은 규제기관의 서로 다른 규정 제안)
- 분야·주제만 같고 사안이 다르다 (둘 다 SMR, 둘 다 우라늄)
- 대상 원전·호기·국가가 다르다
- 한쪽이 개별 사건이고 다른 쪽이 업계 전반의 동향·전망·의견이다

판단이 서지 않으면 false 를 택한다. 잘못 합치는 것이 놓치는 것보다 해롭다.

출력은 JSON 하나:
{"items": [{"idx": 0, "same_event": true, "reason": "20자 이내 근거"}]}
입력에 준 idx 를 모두 포함한다."""


def in_review_band(diagnostics: dict,
                   low: float = REVIEW_BAND_LOW,
                   high: float = REVIEW_BAND_HIGH) -> bool:
    """이 쌍이 LLM 검수 대상 구간인지."""
    if not isinstance(diagnostics, dict):
        return False
    if diagnostics.get("blocked_by"):
        return False
    similarity = diagnostics.get("embedding_similarity")
    if similarity is None:
        return False
    try:
        similarity = float(similarity)
    except (TypeError, ValueError):
        return False
    return low <= similarity < high


def select_pairs(review_candidates: list[dict],
                 low: float = REVIEW_BAND_LOW,
                 high: float = REVIEW_BAND_HIGH) -> list[dict]:
    """검토 후보 중 회색지대만 골라낸다. candidate_id 기준으로 중복 제거."""
    picked: list[dict] = []
    seen: set[str] = set()
    for row in review_candidates or []:
        if not isinstance(row, dict):
            continue
        pair_id = row.get("candidate_id")
        if not pair_id or pair_id in seen:
            continue
        if not in_review_band(row.get("diagnostics") or {}, low, high):
            continue
        seen.add(pair_id)
        picked.append(row)
    return picked


def load_cache(path: Path = CACHE_FILE) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("reviews")
    return entries if isinstance(entries, dict) else {}


def save_cache(cache: dict, path: Path = CACHE_FILE) -> None:
    payload = {
        "_comment": "이슈 병합 회색지대 LLM 판정 캐시. 사람이 고쳐도 된다.",
        "prompt_version": PROMPT_VERSION,
        "reviews": cache,
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def cached_verdict(cache: dict, pair_id: str) -> bool | None:
    entry = cache.get(pair_id)
    if not isinstance(entry, dict):
        return None
    if entry.get("prompt_version") != PROMPT_VERSION:
        return None
    verdict = entry.get("same_event")
    return verdict if isinstance(verdict, bool) else None


def build_user_message(pairs: list[dict]) -> str:
    lines = []
    for idx, row in enumerate(pairs):
        lines.append(f"[{idx}]")
        lines.append(f"  A: {row.get('left_title') or ''}")
        lines.append(f"  B: {row.get('right_title') or ''}")
    return "\n".join(lines)


def _parse_response(payload: dict, count: int) -> dict[int, tuple[bool, str]]:
    """응답에서 idx → (판정, 근거). 범위 밖·형식 오류는 버린다."""
    out: dict[int, tuple[bool, str]] = {}
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        verdict = item.get("same_event")
        if not isinstance(verdict, bool) or not 0 <= idx < count:
            continue
        reason = str(item.get("reason") or "")[:60]
        out[idx] = (verdict, reason)
    return out


def review_pairs(review_candidates: list[dict], *,
                 cache_path: Path = CACHE_FILE,
                 client=None,
                 batch_size: int = BATCH_SIZE,
                 low: float = REVIEW_BAND_LOW,
                 high: float = REVIEW_BAND_HIGH) -> tuple[dict[str, bool], dict]:
    """회색지대 쌍을 판정한다.

    Returns:
        (verdicts, stats) — verdicts 는 {pair_id: same_event}. 판정하지 못한
        쌍은 아예 넣지 않는다(= 병합 안 함).
    """
    pairs = select_pairs(review_candidates, low, high)
    stats = {
        "band": [low, high],
        "prompt_version": PROMPT_VERSION,
        "candidates": len(pairs),
        "from_cache": 0,
        "asked": 0,
        "calls": 0,
        "approved": 0,
        "rejected": 0,
        "failed": 0,
        "status": "ok",
    }
    if not pairs:
        stats["status"] = "no_candidates"
        return {}, stats

    cache = load_cache(cache_path)
    verdicts: dict[str, bool] = {}
    todo: list[dict] = []
    for row in pairs:
        hit = cached_verdict(cache, row["candidate_id"])
        if hit is None:
            todo.append(row)
        else:
            verdicts[row["candidate_id"]] = hit
            stats["from_cache"] += 1

    if todo:
        if client is None:
            try:
                import gemini_client as client  # noqa: PLC0415
            except ImportError:
                client = None
        if client is None or not client.is_available():
            stats["status"] = "no_api_key"
            stats["failed"] = len(todo)
            todo = []

    now = datetime.now(timezone.utc).isoformat()
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        try:
            payload = client.call_json(
                SYSTEM_PROMPT,
                build_user_message(chunk),
                temperature=0.0,
                max_output_tokens=8192,
            )
        except Exception:  # noqa: BLE001 — 실패는 '병합 안 함'으로 흡수
            stats["failed"] += len(chunk)
            stats["status"] = "partial_failure"
            continue
        stats["calls"] += 1
        parsed = _parse_response(payload, len(chunk))
        for idx, row in enumerate(chunk):
            if idx not in parsed:
                stats["failed"] += 1
                continue
            verdict, reason = parsed[idx]
            verdicts[row["candidate_id"]] = verdict
            stats["asked"] += 1
            cache[row["candidate_id"]] = {
                "same_event": verdict,
                "reason": reason,
                "left_title": row.get("left_title"),
                "right_title": row.get("right_title"),
                "embedding_similarity": (row.get("diagnostics") or {}).get("embedding_similarity"),
                "prompt_version": PROMPT_VERSION,
                "model": getattr(client, "MODEL", ""),
                "reviewed_at": now,
            }

    if stats["asked"]:
        save_cache(cache, cache_path)
    stats["approved"] = sum(1 for value in verdicts.values() if value)
    stats["rejected"] = sum(1 for value in verdicts.values() if not value)
    return verdicts, stats
