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

# 한 회차에 **다시** 물을 쌍의 상한. 거부 판정은 이슈 제목이 바뀌면 무효가
# 되는데(cached_verdict), 제목은 클러스터가 기사를 흡수할 때마다 다시 생성되므로
# 요동치는 날에는 무효화가 몰린다. 이 모듈은 기본 모델(gemini-2.5-flash)을 쓰고
# 그 버킷은 크롤 큐레이션과 공유한다 — 2026-08-06 에 분당 20회를 여섯 번 넘긴
# 바로 그 버킷이다. 밀린 몫은 버리는 게 아니라 다음 빌드로 미룬다(판정이 없으면
# 연결하지 않으므로 결과는 '아직 모름'이지 '다른 사건'이 아니다).
MAX_REASK_PER_RUN = 10

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


def cached_verdict(cache: dict, pair_id: str,
                   issue_title: object = None) -> bool | None:
    """캐시된 판정. 없거나 무효면 None(= 다시 묻는다).

    **거부 판정은 이슈 제목이 바뀌면 무효가 된다.** 판정은 그때의 제목에 대한
    것이지 이 쌍에 대한 영구 사실이 아니다. 이슈 제목은 클러스터가 기사를
    흡수할 때마다 다시 생성되므로 어제 "다른 사건"이던 쌍이 오늘 같은 사건으로
    수렴할 수 있다 — issue_review 가 같은 결함으로 브리핑에 중복 카드를 냈다.

    실측 2026-08-06 (캐시 169건, 이슈가 살아있는 쌍 147건):
        제목 드리프트 25건 / 그중 거부 24건
        놓치고 있던 진짜 매칭:
            현재 이슈  "중국 타이핑링 2호기 원자력발전소 상업운전 개시"
            KEEI      "중국 Taipingling 원전 2호기, 최초 계통연결 완료"

    ⚠️ issue_review 는 '두 제목의 어휘 겹침 상승'으로 무효화하는데, **여기서는
    그 신호를 쓰면 안 된다.** 위 쌍의 겹침 상승은 +0.039 라 issue_review 의
    문턱(+0.10)에 한참 못 미친다 — KEEI 목차는 로마자(Taipingling), 이슈는
    한글(타이핑링)이라 어휘가 겹치지 않기 때문이다. 이 모듈이 애초에 LLM 을
    쓰는 이유가 그것이다(docstring 의 n-gram 코사인 실패 기록).

    KEEI 항목은 **발간된 목차라 바뀌지 않는다.** 변하는 쪽이 이슈 제목뿐이므로
    "제목이 바뀌었나"가 여기서는 정확한 신호다.
    """
    entry = cache.get(pair_id)
    if not isinstance(entry, dict):
        return None
    if entry.get("prompt_version") != PROMPT_VERSION:
        return None
    verdict = entry.get("same_event")
    if not isinstance(verdict, bool):
        return None
    if verdict is False and issue_title is not None:
        stored = str(entry.get("issue_title") or "").strip()
        # 캐시는 제목을 120자로 잘라 저장한다. 현재 제목도 같게 잘라 비교하지
        # 않으면 긴 제목이 매번 '바뀌었다'로 잡혀 무한 재질의가 된다.
        if stored and stored != str(issue_title or "").strip()[:120]:
            return None
    return verdict


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
        "reasked": 0, "reask_deferred": 0,
        "approved": 0, "rejected": 0, "failed": 0,
        "status": "ok",
    }
    if not candidates:
        stats["status"] = "no_candidates"
        return {}, stats

    if client is None:
        import gemini_client as client  # 지연 import — 테스트에서 대역 주입 가능

    cache = load_cache(cache_path)
    cache_dirty = False
    verdicts: dict[str, bool] = {}
    todo: list[dict] = []
    reask: list[dict] = []
    for row in candidates:
        pair_id = row.get("pair_id")
        if not pair_id:
            continue
        hit = cached_verdict(cache, pair_id, row.get("issue_title"))
        if hit is None:
            # 캐시에 있는데 여기로 왔으면 제목이 바뀌어 판정이 무효가 된 것이다.
            # 새 쌍과 섞지 않고 따로 담아 상한을 건다 — 제목은 이슈가 기사를
            # 흡수할 때마다 다시 생성되므로, 요동치는 날 재질의가 폭주하면
            # 큐레이션과 같은 분당 버킷을 두고 다툰다.
            (reask if pair_id in cache else todo).append(row)
        else:
            verdicts[pair_id] = hit
            stats["from_cache"] += 1

    if len(reask) > MAX_REASK_PER_RUN:
        stats["reask_deferred"] = len(reask) - MAX_REASK_PER_RUN
        reask = reask[:MAX_REASK_PER_RUN]
    stats["reasked"] = len(reask)
    todo = reask + todo

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
                label="keei_match",
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
            cache_dirty = True
            cache[row["pair_id"]] = {
                "same_event": same_event,
                "reason": reason,
                "prompt_version": PROMPT_VERSION,
                "issue_title": row.get("issue_title", "")[:120],
                "keei_item": row.get("keei_item", "")[:120],
            }

    stats["approved"] = sum(1 for value in verdicts.values() if value)
    stats["rejected"] = sum(1 for value in verdicts.values() if not value)
    # 판정이 하나라도 새로 생겼을 때만 쓴다.
    #   - 호출이 전부 실패했는데 쓰면 빈 캐시가 실패를 성공처럼 남긴다.
    #   - 크기 비교(len 증가)로 판정하면 **덮어쓰기만 하는 경우를 놓친다**:
    #     PROMPT_VERSION 을 올리면 기존 key 를 같은 key 로 다시 채우므로 크기가
    #     그대로라 저장이 안 되고, 다음 빌드도 똑같이 재질의한다(빌드 13회/일
    #     × 7 = 91 calls/일이 무기한, 로그엔 이상 없음). 실측 재현함.
    if cache_dirty:
        save_cache(cache, cache_path)
    return verdicts, stats
