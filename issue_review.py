"""이슈 병합 회색지대를 LLM 이 한 번에 판정한다.

배경:
    임계값 하나로는 "같은 사건"과 "같은 분야"가 갈리지 않는다. 실측(21일, 병합
    64쌍)에서 코사인 0.92 이상은 거의 전부 같은 사건이고 0.88 미만은 거의 전부
    다른 사건인데, 그 사이 7쌍에 국내 계속운전·전기본 후속처럼 진짜 이어지는
    이슈가 몰려 있었다. 임계값을 0.92 로 올리면 오병합은 사라지지만 이 구간도
    같이 잘린다.

    사람 검토 큐(issue_match_overrides.json)는 이미 있지만 138건이 전부 pending
    이라 실질 검수가 되지 않는다. 이 구간만 LLM 에게 묻는다.

    2026-08-03 재측정 — **"0.88 미만은 거의 전부 다른 사건"은 틀렸다.** 사람 검토
    큐가 544건까지 불어나는 동안 LLM 이 본 것은 14쌍뿐이었고(5 승인 / 9 기각),
    나머지 530건은 아무도 판정하지 않은 채 쌓였다. 그 안에 진짜 후속 보도가 있다:

        0.8513  "헝가리 총리, 팍스 원전 일요일 가동 중단 발표"(08-02)
              ↔ "그리스 산불, 가뭄으로 헝가리 원자력 발전소 가동 중단"(08-03)

    같은 사건인데 밴드 밖이라 영영 갈라진 채로 있었다. 하한을 0.84 로 내려 이
    구간을 LLM 에게 넘긴다. 0.82 까지 더 내리는 것은 보류했다 — 실측 표본에서
    0.82~0.84 는 "[시론] 호남 반도체 …" 대 전기본 기사처럼 **분야만 같은** 쌍이
    대부분이라 비용 대비 얻는 게 없다.

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

try:  # gemini_client 없이도 import 가능해야 한다 (테스트는 대역 클라이언트를 넣는다)
    from gemini_client import GeminiTruncated
except ImportError:  # pragma: no cover
    class GeminiTruncated(Exception):  # type: ignore[no-redef]
        """gemini_client 부재 시 자리표시자 — 아무것도 여기 걸리지 않는다."""

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "issue_llm_reviews.json"

# 자동 병합(>=0.92)과 자동 분리(<0.84) 사이. build_data.ISSUE_EMBEDDING_THRESHOLD
# 를 올리면 REVIEW_BAND_HIGH 도 같이 올려야 한다.
# 하한 0.88 → 0.84 (2026-08-03, 근거는 모듈 docstring).
REVIEW_BAND_LOW = 0.84
REVIEW_BAND_HIGH = 0.92

# 프롬프트를 고치면 올린다. 캐시된 옛 판정이 자동으로 무효가 된다.
PROMPT_VERSION = 1

# 한 번에 묻는 쌍 수. 한국어 판정 한 줄이 40~60 토큰이라 20쌍이면 출력이
# 1,500 토큰 안쪽이다. thinking 토큰이 출력 예산을 먹으므로 여유를 크게 둔다.
BATCH_SIZE = 20

# 2.5-flash 는 thinking 토큰이 maxOutputTokens 를 함께 잠식한다(news_bot 은 같은
# 이유로 BATCH_MAX_OUTPUT_TOKENS 를 16384 로 올렸다). 여기서도 천장을 맞춰 둔다 —
# 과금·지연은 실사용 토큰 기준이라 천장만 높이는 것은 비용이 아니다.
#
# **단, 이 값이 실제 사고를 고쳤다는 근거는 없다.** 2026-08-04 02:49 빌드가
# calls=0 / failed=40 으로 죽어서 잘림으로 추정했는데, **같은 8192 코드가 05:54
# 빌드에서 asked=40 / failed=0 으로 정상 통과했다.** 그 실패는 일시적이었다
# (한도 또는 타임아웃). 아래 분할 경로도 아직 실측으로 발동한 적이 없다.
# 원인을 실제로 말해주는 것은 stats.failure_reasons 다 — 다음에 죽으면 그걸 볼 것.
MAX_OUTPUT_TOKENS = 16384

# 잘림은 입력을 줄이면 사라진다. 같은 예산으로 다시 불러도 같은 자리에서 잘리므로
# 재시도가 아니라 분할이 답이다(news_bot.SPLITTABLE_FAILURES 와 같은 판단).
# 분할 예산을 묶어두는 이유는 20 → 1 까지 쪼개면 한 회차에 호출이 폭증하기 때문이다.
SPLIT_BUDGET = 4
MIN_SPLIT_SIZE = 2

# 한 빌드에서 **새로** 묻는 쌍의 상한. 하한을 0.84 로 내린 첫 빌드에는 밀려 있던
# 후보가 146건(실측) 한꺼번에 들어온다. 그걸 한 번에 물으면 8회 호출이 한 빌드에
# 몰리는데, 웹 빌드는 하루 12회 이상 돌고 같은 키를 크롤·브리핑이 나눠 쓴다.
# 판정은 캐시되므로 밀린 것은 몇 회차에 걸쳐 저절로 빠진다 — 급할 이유가 없다.
MAX_NEW_PAIRS_PER_RUN = 40

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


def classify_failure(exc: Exception) -> str:
    """호출 실패를 '다시 부를 가치가 있는가'로 나눈다.

    ``news_bot.classify_request_failure`` 와 같은 판단이다. 여기 따로 두는 이유는
    이 모듈이 build_data 를 import 하지 않는다는 가드레일 때문이다(순환 방지).
    """
    msg = str(exc)
    if "RESOURCE_EXHAUSTED" in msg or "HTTP 429" in msg:
        return "quota"
    if "timed out" in msg.lower() or "TimeoutError" in msg:
        return "timeout"
    return "other"


def _record_failure(stats: dict, chunk: list[dict], label: str,
                    exc: Exception | None = None) -> None:
    """실패를 사유별로 남긴다.

    예전에는 ``except Exception`` 이 사유를 통째로 지웠다. 그래서 2026-08-04 02:49
    빌드가 ``calls=0 / failed=40`` 으로 죽었을 때 **한도 소진인지 잘림인지 알 수
    없었고**, 대응이 정반대인 두 경우를 구분하려고 또 두 시간을 기다려야 했다.
    """
    stats["failed"] += len(chunk)
    stats["status"] = "partial_failure"
    stats["failure_reasons"][label] = stats["failure_reasons"].get(label, 0) + len(chunk)
    if exc is not None and not stats.get("failure_detail"):
        stats["failure_detail"] = f"{type(exc).__name__}: {str(exc)[:160]}"


def _ask_priority(row: dict) -> tuple:
    """새로 물어볼 쌍의 우선순위. 큰 것부터 묻는다.

    최신 날짜가 먼저인 이유는 두 가지다. 추적률이 **최신 브리핑**에서만 측정되고,
    21일 창 밖으로 밀려날 쌍에 호출을 쓰면 판정이 쓰이기 전에 버려진다.
    """
    diagnostics = row.get("diagnostics") or {}
    try:
        similarity = float(diagnostics.get("embedding_similarity") or 0.0)
    except (TypeError, ValueError):
        similarity = 0.0
    newest = max(str(row.get("left_date") or ""), str(row.get("right_date") or ""))
    return (newest, similarity)


def review_pairs(review_candidates: list[dict], *,
                 cache_path: Path = CACHE_FILE,
                 client=None,
                 batch_size: int = BATCH_SIZE,
                 max_new_pairs: int = MAX_NEW_PAIRS_PER_RUN,
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
        "deferred": 0,
        "splits": 0,
        "failure_reasons": {},
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

    # 상한을 넘긴 몫은 버리는 게 아니라 미룬다. 판정이 없는 쌍은 병합되지 않으므로
    # (verdicts 에 안 들어간다) 결과는 "이번 회차엔 아직 모름"이지 "다른 사건"이 아니다.
    if max_new_pairs is not None and len(todo) > max_new_pairs:
        todo.sort(key=_ask_priority, reverse=True)
        stats["deferred"] = len(todo) - max_new_pairs
        stats["status"] = "throttled"
        todo = todo[:max_new_pairs]

    if todo:
        if client is None:
            try:
                import gemini_client as client  # noqa: PLC0415
            except ImportError:
                client = None
        if client is None or not client.is_available():
            stats["status"] = "no_api_key"
            stats["failed"] = len(todo)
            stats["failure_reasons"]["no_api_key"] = len(todo)
            todo = []

    now = datetime.now(timezone.utc).isoformat()
    split_budget = SPLIT_BUDGET

    def ask(chunk: list[dict]) -> None:
        """chunk 하나를 판정한다. 잘림이면 절반으로 쪼개 다시 부른다."""
        nonlocal split_budget
        try:
            payload = client.call_json(
                SYSTEM_PROMPT,
                build_user_message(chunk),
                temperature=0.0,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
        except GeminiTruncated as exc:
            # 같은 예산으로 다시 부르면 같은 자리에서 잘린다 — 입력을 줄여야 한다.
            if len(chunk) >= MIN_SPLIT_SIZE * 2 and split_budget > 0:
                split_budget -= 1
                stats["splits"] += 1
                mid = len(chunk) // 2
                ask(chunk[:mid])
                ask(chunk[mid:])
                return
            _record_failure(stats, chunk, "truncated", exc)
            return
        except Exception as exc:  # noqa: BLE001 — 실패는 '병합 안 함'으로 흡수
            _record_failure(stats, chunk, classify_failure(exc), exc)
            return
        stats["calls"] += 1
        parsed = _parse_response(payload, len(chunk))
        for idx, row in enumerate(chunk):
            if idx not in parsed:
                stats["failed"] += 1
                stats["failure_reasons"]["unparsed"] = \
                    stats["failure_reasons"].get("unparsed", 0) + 1
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

    for start in range(0, len(todo), batch_size):
        ask(todo[start:start + batch_size])

    if stats["asked"]:
        save_cache(cache, cache_path)
    stats["approved"] = sum(1 for value in verdicts.values() if value)
    stats["rejected"] = sum(1 for value in verdicts.values() if not value)
    return verdicts, stats
