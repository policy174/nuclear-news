"""KEEI 세계 원전시장 인사이트 목차 ↔ 뉴스 이슈 매칭을 LLM 이 판정한다.

배경 (2026-08-02 실측):
    임베딩·키워드 점수로는 "같은 사건"과 "같은 분야"가 갈리지 않는다. 로컬
    n-gram 코사인 상위권은 오히려 오매칭이 차지했고(벤더명만 같은 Rolls-Royce
    쌍 0.323 > 진짜 같은 사건인 EIB·체르나보다 쌍 0.239), IDF 가중 토큰 중복도
    3위부터 다른 규칙·다른 발전소가 섞였다. 발표·계획·건설·공청회 같은 흔한
    토큰이 점수를 지배하기 때문이다.

    그래서 issue_review.py 와 같은 구조를 쓴다: 파이썬이 후보를 좁히고, 판정은
    LLM 이 한다. KEEI 는 격주간이라 새 호가 나올 때만 후보가 생기고, 판정은
    캐시되므로 호출은 사실상 격주 몇 회다.

가드레일:
    - 키가 없거나 호출이 실패하면 **연결하지 않는다**. 틀린 연결은 누락보다
      해롭다 (issue_similarity·issue_review 와 같은 원칙).
    - 판정 실패는 캐시하지 않는다 — 다음 빌드에서 다시 시도한다.
    - stdlib + gemini_client 만 사용. build_data 를 import 하지 않는다(순환 방지).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "keei_llm_matches.json"

# 프롬프트를 고치면 올린다. 캐시된 옛 판정이 자동으로 무효가 된다.
PROMPT_VERSION = 1

BATCH_SIZE = 20

SYSTEM_PROMPT = """너는 원자력 산업 뉴스를 정리하는 편집자다.
A(뉴스 이슈 제목)와 B(에너지경제연구원 '세계 원전시장 인사이트' 목차 항목)가
**같은 사건**을 가리키는지 판정한다.

같은 사건이다 (same_event: true):
- 동일한 주체가 동일한 대상에 대해 벌인 하나의 사안을 양쪽이 가리킨다
- 표기가 달라도 같은 대상이면 같다 (체르나보다=Cernavodă, 미국 NRC=미 NRC)
- 진행 단계가 다른 후속 보도는 같은 사건이다 (신청 → 승인, 협상 → 계약)

다른 사건이다 (same_event: false):
- 주체는 같지만 안건이 다르다 (같은 규제기관의 서로 다른 규정·서로 다른 원전)
- 분야·기업·기술만 같고 사안이 다르다 (둘 다 SMR, 둘 다 Rolls-Royce)
- 대상 원전·호기·국가가 다르다
- 한쪽이 개별 사건이고 다른 쪽이 업계 전반의 동향·전망·통계다

판단이 서지 않으면 false 를 택한다. 틀린 연결이 놓치는 것보다 해롭다.

출력은 JSON 하나:
{"items": [{"idx": 0, "same_event": true, "reason": "20자 이내 근거"}]}
입력에 준 idx 를 모두 포함한다."""


def load_cache(path: Path = CACHE_FILE) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("matches")
    return entries if isinstance(entries, dict) else {}


def save_cache(cache: dict, path: Path = CACHE_FILE) -> None:
    payload = {
        "_comment": "KEEI 인사이트 ↔ 이슈 매칭 LLM 판정 캐시. 사람이 고쳐도 된다.",
        "prompt_version": PROMPT_VERSION,
        "matches": cache,
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
        lines.append(f"  A: {row.get('issue_title') or ''}")
        lines.append(f"  B: {row.get('keei_item') or ''}")
    return "\n".join(lines)


def _parse_response(payload: dict, count: int) -> dict[int, tuple[bool, str]]:
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
        out[idx] = (verdict, str(item.get("reason") or "")[:60])
    return out


def match_pairs(candidates: list[dict], *,
                cache_path: Path = CACHE_FILE,
                client=None,
                batch_size: int = BATCH_SIZE) -> tuple[dict[str, bool], dict]:
    """후보 쌍을 판정한다.

    Args:
        candidates: [{"pair_id", "issue_title", "keei_item"}, ...]

    Returns:
        (verdicts, stats) — verdicts 는 {pair_id: same_event}. 판정하지 못한
        쌍은 넣지 않는다(= 연결 안 함).
    """
    stats = {
        "prompt_version": PROMPT_VERSION,
        "candidates": len(candidates or []),
        "from_cache": 0, "asked": 0, "calls": 0,
        "approved": 0, "rejected": 0, "failed": 0,
        "status": "ok",
    }
    if not candidates:
        stats["status"] = "no_candidates"
        return {}, stats

    if client is None:
        import gemini_client as client  # 지연 import — 테스트에서 대역 주입 가능

    cache = load_cache(cache_path)
    cached_count = len(cache)
    verdicts: dict[str, bool] = {}
    todo: list[dict] = []
    for row in candidates:
        pair_id = row.get("pair_id")
        if not pair_id:
            continue
        hit = cached_verdict(cache, pair_id)
        if hit is None:
            todo.append(row)
        else:
            verdicts[pair_id] = hit
            stats["from_cache"] += 1

    if todo and not client.is_available():
        stats["status"] = "no_api_key"
        stats["failed"] = len(todo)
        todo = []

    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        stats["asked"] += len(chunk)
        try:
            payload = client.call_json(
                SYSTEM_PROMPT, build_user_message(chunk),
                temperature=0.0, max_output_tokens=8192,
            )
            stats["calls"] += 1
        except Exception as exc:  # 실패는 캐시하지 않는다 — 다음 빌드에서 재시도
            stats["failed"] += len(chunk)
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        parsed = _parse_response(payload, len(chunk))
        for idx, row in enumerate(chunk):
            verdict = parsed.get(idx)
            if verdict is None:
                stats["failed"] += 1
                continue
            same_event, reason = verdict
            verdicts[row["pair_id"]] = same_event
            cache[row["pair_id"]] = {
                "same_event": same_event,
                "reason": reason,
                "prompt_version": PROMPT_VERSION,
                "issue_title": row.get("issue_title", "")[:120],
                "keei_item": row.get("keei_item", "")[:120],
            }

    stats["approved"] = sum(1 for value in verdicts.values() if value)
    stats["rejected"] = sum(1 for value in verdicts.values() if not value)
    # 새 판정이 실제로 생겼을 때만 쓴다. 호출이 전부 실패했는데 파일을 쓰면
    # 빈 캐시가 생겨 실패를 성공처럼 남긴다.
    if len(cache) > cached_count:
        save_cache(cache, cache_path)
    return verdicts, stats
