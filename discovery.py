"""후속 발굴 — 이미 다룬 이슈가 지금도 유효한지 다시 확인한다.

이건 '기사를 더 긁는 수집기'가 아니라 **상태 갱신 루프**다. 사이트가 옛 상태를
계속 보여주는 동안 현실이 바뀌면 정보가 없는 것보다 나쁘다 — 틀린 현재 상태를
전달하게 된다. 실제로 2026-08-05 브리핑은 팍스 원전을 "마지막 터빈 안전하게
가동 중"으로 노출했는데, 같은 날 헝가리는 44년 만에 그 원전을 세웠다. 국내
보도(뉴시스 17:56·JTBC 19:58)가 있었지만 어느 고정 피드에도 걸리지 않았다.

설계 원칙
- **LLM 0회.** 쿼리 생성은 전부 결정적이다.
- **웹 산출물에 의존하지 않는다.** issues.json 은 크롤 이후에 만들어지므로
  크롤 시점에는 없다. 재료는 archive/*.jsonl 과 entity_registry.json 뿐이다.
- **예산이 먼저, 조합은 나중.** 엔티티 64개 × 사건어 7개 = 448 조합이라 전부
  돌릴 수 없다. 우선순위로 줄을 세우고 상한에서 자른다.
- **성과 없는 쿼리는 재운다.** dedup 은 같은 기사가 두 번 들어오는 것만 막지
  매일 같은 검색을 반복하는 것은 못 막는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from entity_match import _entity_alias_entries, entity_ids_for_members

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "discovery_state.json"

KST = timezone(timedelta(hours=9))

# 하루에 내보낼 쿼리 상한. 네이버 무료 한도(일 25,000)에는 여유가 크지만, 늘어난
# 유입은 전부 LLM 큐레이션을 타므로 실제 제약은 Gemini 쿼터다.
DAILY_QUERY_BUDGET = 30

# 우선순위 창
MUST_READ_HOURS = 48
RECENT_DAYS = 7
REAPPEAR_DAYS = 21

# 성과 없는 쿼리 냉각
ZERO_YIELD_LIMIT = 3        # 신규 0건이 이만큼 연속되면
COOLDOWN_DAYS = 7           # 이만큼 쉰다

# 진행 중임을 드러내는 표현. 이 말이 붙은 기사는 **상태가 바뀔 수 있는** 사건이라
# 후속을 물으러 갈 값어치가 있다. 반대로 '체결했다·발급했다'로 끝난 사건은
# 후속이 있어도 별개 사건이다.
_ONGOING_RE = re.compile(
    r"가동\s*중|운전\s*중|심의\s*중|검토\s*중|협상\s*중|추진\s*중|복구\s*중|건설\s*중"
    r"|승인\s*대기|예정|전망|추진한다|앞두고|위기|중단\s*위기|논의"
)

# entity type → 물어볼 사건군. 모든 엔티티에 모든 사건군을 곱하면 예산이 즉시
# 마른다. 원전에 '수주'를 묻거나 기관에 '재가동'을 묻는 건 대개 헛방이다.
EVENT_GROUPS: dict[str, list[str]] = {
    "operation_change": ["가동 중단", "운전 정지", "재가동", "출력 감발", "정비"],
    "regulatory": ["승인", "허가", "심의", "의결", "상정"],
    "commercial": ["계약", "협약", "수주", "MOU"],
    "project": ["착공", "준공", "지연", "투자"],
    "safety": ["사고", "고장", "누설", "점검"],
}
TYPE_EVENTS: dict[str, list[str]] = {
    "plant": ["operation_change", "regulatory", "safety"],
    "company": ["commercial", "project"],
    "org": ["regulatory"],
    "project": ["project", "commercial", "regulatory"],
}

# 같은 우선순위 안에서 어떤 종류를 먼저 묻는가. 설비·프로젝트는 **상태가 바뀌는**
# 대상이라 후속 발굴의 본령이고, 기관(org)은 '원자력안전위원회 승인'처럼 쿼리가
# 넓어 헛방이 많다. 팍스(plant)가 홀텍(company)·원안위(org) 뒤로 밀려 예산에서
# 잘리던 것을 이 순서로 바로잡았다.
TYPE_RANK = {"plant": 0, "project": 1, "company": 2, "org": 3}

# 한 엔티티가 한 회차에 가져갈 수 있는 쿼리 수. 라운드로빈이라 굳이 없어도 되지만,
# 엔티티가 두셋뿐인 조용한 날에 한 대상에 예산을 다 붓는 것을 막는다.
MAX_QUERIES_PER_ENTITY = 4

# 쿼리는 한국어로만 낸다. 해외 엔티티라도 영문 전문지(WNN·NucNet·Reuters)는 이미
# 고정 RSS 로 구독하고 있어 그쪽에서 겹칠 뿐이고, 실제로 비어 있던 것은 **같은
# 사건의 국내 보도**였다(팍스 중단을 뉴시스·JTBC 가 썼지만 안 들어왔다).
#
# 네이버 하나로 시작한다. '팍스 원전 가동 중단' 을 실제로 던져 놓친 기사가 전부
# 나오는 것을 확인했다(뉴시스 17:56 "44년 만에 5일부터 중단"·디지털타임스·전자신문,
# 덤으로 체르나보다 1기 중단까지). 출처를 하나 더 붙이면 예산이 절반으로 줄고
# 이득은 아직 미검증이라, Google News 는 유입·중복 실측 후에 켠다.
SOURCES = ("naver",)


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_recent_archive_rows(days: int = REAPPEAR_DAYS,
                             archive_dir: Path | None = None,
                             now: datetime | None = None) -> list[dict]:
    """씨앗이 될 아카이브 레코드. noise 도 읽는다 — 등급은 우선순위에서 가린다.

    아카이브가 없거나 깨져도 빈 목록을 낸다. discovery 는 비치명 경로다.
    """
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=days)
    directory = archive_dir or (ROOT / "archive")
    rows: list[dict] = []
    try:
        paths = sorted(directory.glob("*.jsonl"))
    except OSError:
        return rows
    for path in paths[-3:]:          # 월 파일 3개면 21일 창을 항상 덮는다
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = _parse_dt(row.get("archived_at"))
            if stamp is not None and stamp >= cut:
                rows.append(row)
    return rows


def fingerprint(query: str, source: str) -> str:
    return hashlib.sha1(f"{source}|{query}".encode("utf-8")).hexdigest()[:16]


def load_state(path: Path = STATE_FILE) -> dict:
    """상태 파일은 없어도 정상이다 — 첫 실행이거나 캐시가 날아간 경우."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "queries": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("queries"), dict):
        return {"version": 1, "queries": {}}
    return raw


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _is_cooling(entry: dict, now: datetime) -> bool:
    until = _parse_dt(entry.get("next_eligible_at"))
    return bool(until and now < until)


def _seed_articles(archive_rows: list[dict], now: datetime) -> list[tuple[int, dict]]:
    """(우선순위, 기사) — 낮을수록 먼저. 상태가 바뀔 만한 것부터 묻는다."""
    seeds: list[tuple[int, dict]] = []
    must_read_cut = now - timedelta(hours=MUST_READ_HOURS)
    recent_cut = now - timedelta(days=RECENT_DAYS)
    reappear_cut = now - timedelta(days=REAPPEAR_DAYS)

    for row in archive_rows:
        stamp = _parse_dt(row.get("archived_at"))
        if stamp is None or stamp < reappear_cut:
            continue
        text = f"{row.get('title_kr') or ''} {row.get('summary') or ''}"
        importance = str(row.get("importance") or "")
        if importance == "must_read" and stamp >= must_read_cut:
            seeds.append((0, row))
        elif stamp >= recent_cut and _ONGOING_RE.search(text):
            seeds.append((1, row))
        elif stamp >= recent_cut and importance in ("must_read", "nice_to_know"):
            seeds.append((2, row))
        elif importance == "must_read":
            seeds.append((3, row))
    seeds.sort(key=lambda item: (item[0], _parse_dt(item[1].get("archived_at")) or now),
               reverse=False)
    return seeds


def plan_queries(archive_rows: list[dict],
                 registry: list[dict],
                 state: dict,
                 now: datetime | None = None,
                 budget: int = DAILY_QUERY_BUDGET) -> tuple[list[dict], dict]:
    """검색할 쿼리 목록과 갱신된 상태를 낸다. 네트워크를 타지 않는다(테스트 가능).

    반환 쿼리: {"query", "source", "entity_id", "reason", "fingerprint"}
    """
    now = now or datetime.now(timezone.utc)
    alias_entries = _entity_alias_entries(registry)
    by_id = {entity["id"]: entity for entity in registry}

    # 1) 어떤 엔티티를 물을지 — 씨앗 기사 우선순위 → 종류 순으로 줄을 세운다.
    ranked: dict[str, tuple[int, int, int]] = {}
    for order, (priority, row) in enumerate(_seed_articles(archive_rows, now)):
        entity_ids, _ = entity_ids_for_members([row], alias_entries)
        for entity_id in entity_ids:
            entity = by_id.get(entity_id)
            if not entity or not TYPE_EVENTS.get(entity["type"]):
                continue
            key = (priority, TYPE_RANK.get(entity["type"], 9), order)
            if entity_id not in ranked or key < ranked[entity_id]:
                ranked[entity_id] = key
    entity_order = sorted(ranked, key=lambda eid: ranked[eid])

    # 2) 엔티티마다 물어볼 쿼리 줄 — 아직 내보내지 않는다.
    per_entity: dict[str, list[dict]] = {}
    seen_fp: set[str] = set()
    for entity_id in entity_order:
        entity = by_id[entity_id]
        priority, _type_rank, _order = ranked[entity_id]
        # 범용어(고리·월성) 는 별칭이 아니라 정식 명칭으로 묻는다 —
        # match_policy 가 자유문 매칭을 막아 둔 이름들이다.
        name = entity["name_kr"]
        pending: list[dict] = []
        for group in TYPE_EVENTS[entity["type"]]:
            for term in EVENT_GROUPS.get(group, []):
                for source in SOURCES:
                    query = f"{name} {term}"
                    fp = fingerprint(query, source)
                    if fp in seen_fp or _is_cooling(state["queries"].get(fp) or {}, now):
                        continue
                    seen_fp.add(fp)
                    pending.append({
                        "query": query, "source": source, "entity_id": entity_id,
                        "reason": f"p{priority}:{group}", "fingerprint": fp,
                    })
        per_entity[entity_id] = pending[:MAX_QUERIES_PER_ENTITY]

    # 3) 라운드로빈 — 한 대상이 예산을 독식하면 정작 상태가 뒤집힌 다른 대상을
    #    영영 못 묻는다. 실측: 깊이 우선이면 홀텍 16 · 원안위 10 으로 예산이 마르고
    #    팍스는 한 번도 안 나왔다.
    queries: list[dict] = []
    for depth in range(MAX_QUERIES_PER_ENTITY):
        for entity_id in entity_order:
            bucket = per_entity.get(entity_id) or []
            if depth < len(bucket):
                queries.append(bucket[depth])
                if len(queries) >= budget:
                    return queries, state
    return queries, state


def record_results(state: dict, results: list[dict], now: datetime | None = None) -> dict:
    """쿼리별 성과를 남기고 헛도는 쿼리를 재운다.

    results: {"fingerprint", "query", "source", "result_count", "new_article_count"}

    ⚠️ 신규 0건이 곧 실패는 아니다 — 이미 다 걷은 사건이면 정상이다. 다만 그게
    반복되면 그 조합은 당분간 물을 값이 없다.
    """
    now = now or datetime.now(timezone.utc)
    for result in results:
        fp = result.get("fingerprint")
        if not fp:
            continue
        entry = dict(state["queries"].get(fp) or {})
        entry["query"] = result.get("query", entry.get("query", ""))
        entry["source"] = result.get("source", entry.get("source", ""))
        entry["last_run"] = now.isoformat()
        entry["result_count"] = int(result.get("result_count") or 0)
        entry["new_article_count"] = int(result.get("new_article_count") or 0)
        if entry["new_article_count"] > 0:
            entry["zero_yield_streak"] = 0
            entry.pop("next_eligible_at", None)
        else:
            entry["zero_yield_streak"] = int(entry.get("zero_yield_streak") or 0) + 1
            if entry["zero_yield_streak"] >= ZERO_YIELD_LIMIT:
                entry["next_eligible_at"] = (now + timedelta(days=COOLDOWN_DAYS)).isoformat()
        state["queries"][fp] = entry
    return state


def prune_state(state: dict, now: datetime | None = None, keep_days: int = 60) -> dict:
    """오래 안 쓴 항목을 지운다 — 상태 파일이 커밋되므로 무한히 자라면 안 된다."""
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=keep_days)
    state["queries"] = {
        fp: entry for fp, entry in state["queries"].items()
        if (_parse_dt(entry.get("last_run")) or now) >= cut
    }
    return state
