# -*- coding: utf-8 -*-
"""이슈 단위 현안 분류 — tags.json 12 대분류를 LLM 이 고른다. 캐시는 issue_domains.json.

domain_rules(정규식)는 낱말로 고른다: '핵연료물질 사용 허가' 의결이 핵연료제조가
되고, '내진 데이터 조작 → 경영진 사임' 이 사고고장이 됐다(2026-09-15 지니:
"분류가 하나도 안 맞아"). tags.json 의 경계 규칙("낱말이 아니라 사건을 보고
고른다")은 정규식으로는 못 지킨다. 그래서 이슈마다 한 번 묻고 영구 캐시한다.

호출 예산: 한 회차 MAX_NEW_PER_RUN 건, BATCH_SIZE 건씩 한 호출. 최신 이슈부터.
밀린 이슈는 다음 빌드에서 채워진다. 캐시는 crawl/daily-brief 의 캐시 커밋
목록에 올려 두었다 — 목록에서 빠지면 매 빌드 처음부터 다시 묻는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import llm_cache

ROOT = Path(__file__).resolve().parent
TAGS_FILE = ROOT / "tags.json"
CACHE_FILE = ROOT / "issue_domains.json"
CACHE_KEY = "domains"
CACHE_COMMENT = "이슈 단위 현안 분류 캐시. 키는 issue_id, prompt_version 이 다르면 다시 묻는다."
PROMPT_VERSION = 1
BATCH_SIZE = 20
MAX_NEW_PER_RUN = 60
MODEL_DEFAULT = "gemini-2.5-flash-lite"


def taxonomy() -> dict[str, list[str]]:
    data = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    return {d["id"]: list(d["tags"]) for d in data["domains"]}


def _system_prompt() -> str:
    data = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    lines = ["너는 한국수력원자력 정책 부서의 편집자다. 원자력 뉴스 이슈를 아래 대분류 12개 중",
             "하나로 분류하고, 그 대분류의 세부 태그 중 하나를 고른다.",
             "", "대분류와 세부 태그:"]
    for d in data["domains"]:
        lines.append(f"- {d['id']}: {', '.join(d['tags'])}")
    lines += ["", "경계 규칙(반드시 지킨다):"]
    lines += [f"- {rule}" for rule in data.get("boundary_rules", [])]
    lines += [
        "",
        "- 낱말이 아니라 **사건**을 보고 고른다. 제목에 '핵연료'가 있어도 사건이 규제 절차면 규제다.",
        "- 사람의 거취·조직 개편이 사건이어도 그 원인이 된 사안(설비 안전·규제·사업)의 칸으로 간다.",
        "- 원자력과 무관한 이슈만 domain 을 빈 문자열로 둔다.",
        "",
        '출력은 JSON 하나: {"items": [{"idx": 0, "domain": "규제", "tag": "원안위"}]}',
        "입력에 준 idx 를 모두 포함한다. domain 과 tag 는 위 목록의 표기 그대로 쓴다.",
    ]
    return "\n".join(lines)


def build_user_message(rows: list[dict]) -> str:
    blocks = []
    for index, row in enumerate(rows):
        lines = [f"[{index}] 제목: {row.get('title') or ''}"]
        summary = " ".join(str(row.get("summary") or "").split())[:300]
        if summary:
            lines.append(f"    요약: {summary}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def parse(payload: object, rows: list[dict], tax: dict[str, list[str]]) -> dict[int, tuple[str, list[str]]]:
    """{idx: (domain, [tag])}. 목록에 없는 대분류는 버리고, 맞지 않는 태그만 뗀다."""
    out: dict[int, tuple[str, list[str]]] = {}
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
        domain = str(item.get("domain") or "").strip()
        if domain and domain not in tax:
            continue
        tag = str(item.get("tag") or "").strip()
        tags = [tag] if domain and tag in tax[domain] else []
        out[idx] = (domain, tags)
    return out


def _resolve_model() -> str:
    try:
        import gemini_client  # noqa: PLC0415
    except ImportError:
        return os.environ.get("GEMINI_DOMAIN_MODEL") or MODEL_DEFAULT
    return gemini_client._resolve("GEMINI_DOMAIN_MODEL", MODEL_DEFAULT)


def generate(rows: list[dict], *, client=None, cache_path: Path = CACHE_FILE,
             batch_size: int = BATCH_SIZE, max_new: int = MAX_NEW_PER_RUN,
             ) -> tuple[dict[str, tuple[str, list[str]]], dict]:
    """{issue_id: (domain, tags)} 와 통계. 판정이 없는 이슈는 사전에 없다."""
    stats = {"candidates": len(rows), "from_cache": 0, "asked": 0, "calls": 0,
             "deferred": 0, "failed": 0, "status": "ok", "model": ""}
    cache = llm_cache.load(cache_path, CACHE_KEY)
    result: dict[str, tuple[str, list[str]]] = {}
    todo: list[dict] = []
    for row in rows:
        issue_id = str(row.get("issue_id") or "")
        if not issue_id:
            continue
        # 키는 issue_id 만. 제목·요약 지문을 걸었더니 CI 재빌드마다 요약이 다시
        # 생성돼 80건 중 40건이 캐시를 못 맞혔다(2026-09-16 실측). 분류는 사건이
        # 바뀌지 않는 한 안 바뀌고, 프롬프트가 바뀌면 prompt_version 으로 무효화.
        entry = cache.get(issue_id)
        if llm_cache.is_current(entry, PROMPT_VERSION):
            stats["from_cache"] += 1
            result[issue_id] = (str(entry.get("domain") or ""), list(entry.get("tags") or []))
            continue
        todo.append(row)

    if len(todo) > max_new:
        todo.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
        stats["deferred"] = len(todo) - max_new
        stats["status"] = "throttled"
        todo = todo[:max_new]
    if not todo:
        return result, stats

    if client is None:
        try:
            import gemini_client as client  # noqa: PLC0415
        except ImportError:
            client = None
    if client is None or not client.is_available():
        stats["status"] = "no_api_key"
        stats["failed"] = len(todo)
        return result, stats

    tax = taxonomy()
    system_prompt = _system_prompt()
    model = _resolve_model()
    stats["model"] = model
    now = datetime.now(timezone.utc).isoformat()
    dirty = False
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        try:
            payload = client.call_json(system_prompt, build_user_message(chunk),
                                       temperature=0.0, max_output_tokens=4096,
                                       model=model, label="issue_domain")
        except Exception as exc:  # noqa: BLE001 — 분류 부재는 비치명(칩이 숨는다)
            stats["failed"] += len(chunk)
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        stats["calls"] += 1
        parsed = parse(payload, chunk, tax)
        for index, row in enumerate(chunk):
            if index not in parsed:
                stats["failed"] += 1
                continue   # 응답에 빠진 이슈는 캐시하지 않는다 — 다음에 다시 묻는다
            domain, tags = parsed[index]
            issue_id = str(row["issue_id"])
            result[issue_id] = (domain, tags)
            cache[issue_id] = {"domain": domain, "tags": tags,
                               "title": row.get("title") or "", "prompt_version": PROMPT_VERSION,
                               "model": model, "generated_at": now}
            stats["asked"] += 1
            dirty = True
    if dirty:
        llm_cache.save(cache, cache_path, key=CACHE_KEY, prompt_version=PROMPT_VERSION,
                       comment=CACHE_COMMENT, sort_keys=False)
    return result, stats
