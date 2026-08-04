"""
주간 판세 리포트 — 일일 브리핑(개별 사건 카드)의 상위 레이어.

역할 재정의 (2026-07):
    일일 브리핑이 '카드'라면 주간은 '판세'. 기사 재나열을 최소화하고
    ① 정책 변화 ② 투자 테마 강약 ③ 한국/한수원 직접 영향 ④ 다음 주 watchlist
    ⑤ 보고서 검토 후보 ⑥ 소스 coverage gap 을 종합한다.
    집계(섹션·테마·이벤트 유형·소스 커버리지)는 Python 이 계산해 프롬프트에 제공,
    LLM 은 그 위에서 서사만 쓴다. Gemini 호출은 기존과 동일하게 주 1회 1번.

2026-07 버그 수정:
    curated 스키마가 importance(등급)/category(정책·기술·시장·규제)로 분리된 뒤에도
    옛 필드(category)에서 등급을 찾고 있어 매주 0건 → 리포트가 조용히 스킵되던 회귀.
    이제 importance 우선, 옛 스키마(category 에 등급)도 하위 호환.
"""

from __future__ import annotations

import html
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent
CURATED_CACHE_FILE = ROOT / "curated.json"
SOURCES_FILE = ROOT / "sources.json"
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
WEEK_DAYS = 7

_GRADES = {"must_read", "nice_to_know", "market", "noise"}
SECTION_KR = {"smr": "SMR", "khnp": "한수원", "domestic": "국내 정책", "international": "해외"}

WEEKLY_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
지난 7일 수집 기사와 시스템이 계산한 집계를 받아 의사결정자용 **주간 판세 보고**를 씁니다.
개별 기사 요약의 나열이 아니라, 한 주의 흐름·방향·다음 주 대비가 핵심입니다.

[출력 형식] - 반드시 JSON 한 객체만. 다른 텍스트·펜스 금지. 문자열 값 안 줄바꿈 금지.
{
  "weekly_intro": "이번 주 핵심 흐름 3~4문장 (400자 이내, 분석관 보고 톤)",
  "policy_shifts": [{"what": "정책 변화 1문장", "so_what": "함의 1문장", "evidence_hashes": ["hash8"]}],
  "theme_moves": [{"theme": "투자 테마명", "direction": "강화|약화|유지", "why": "근거 1문장", "evidence_hashes": ["hash8"]}],
  "khnp_direct": "한국·한수원 직접 영향 종합 1~3문장 (없으면 빈 문자열)",
  "watchpoints": ["다음 주 모니터링 포인트 (각 1문장, 3~5개)"],
  "report_candidates": [{"topic": "보고서 주제", "basis": "누적 근거 1문장"}],
  "key_events": [{"hash": "...", "headline": "기사 원문 제목 그대로", "implication": "1문장"}]
}

[규칙]
- policy_shifts 2~4개, theme_moves 2~4개, report_candidates 0~3개 (없으면 빈 배열 — 억지 금지).
- **evidence_hashes**: 그 문장의 근거가 된 기사 hash. 입력 목록에 **실제로 있는 hash 만** 1~2개. 근거를 지목할 수 없으면 빈 배열. 지어내지 말 것.
- key_events 는 **최대 5건** — 주간 판세를 대표하는 사건만. 일일 브리핑 재탕 금지.
- 같은 사건의 후속 보도는 1건으로 취급.
- 원문·집계에 없는 정보 추가 금지 (환각 금지). 격식체(~다) 분석관 톤.
- theme_moves 의 theme 은 우라늄/SMR/수출/계속운전/핵연료/방폐/규제/공급망/신규건설/전력수요 등 투자 테마 어휘로."""


def load_curated() -> dict:
    if CURATED_CACHE_FILE.exists():
        try:
            return json.loads(CURATED_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _grade(data: dict) -> str:
    """등급 추출 — 현행 스키마는 importance, 옛 스키마는 category 에 등급이 있었음."""
    imp = data.get("importance")
    if imp in _GRADES:
        return imp
    cat = data.get("category")
    if cat in _GRADES:
        return cat
    return "nice_to_know"


def get_week_articles(curated: dict) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WEEK_DAYS)).isoformat()
    items: list[dict] = []
    for h, data in curated.items():
        if not isinstance(data, dict):
            continue
        if data.get("cached_at", "") < cutoff:
            continue
        if _grade(data) not in ("must_read", "nice_to_know"):
            continue
        if not data.get("title") or not data.get("link"):
            continue
        items.append({
            "hash": h,
            "title": data["title"],
            "title_kr": data.get("title_kr", ""),
            "link": data["link"],
            "domain": data.get("domain", ""),
            "feed": data.get("feed", ""),
            "section": data.get("section", ""),
            "grade": _grade(data),
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "features": data.get("features"),
            "cached_at": data["cached_at"],
        })
    items.sort(key=lambda x: x["cached_at"])
    return items


# ---- Python 집계 (LLM 은 이 위에서 서사만) -------------------------------------

def build_aggregates(items: list[dict]) -> dict:
    sections = Counter(SECTION_KR.get(a.get("section"), a.get("section") or "기타")
                       for a in items)
    events = Counter()
    report_cands = []
    for a in items:
        f = a.get("features") or {}
        if isinstance(f, dict):
            et = f.get("event_type")
            if et:
                events[et] += 1
            try:
                rw = int(f.get("report_worthiness", 0))
            except (TypeError, ValueError):
                rw = 0
            if rw >= 2:
                report_cands.append((a.get("title_kr") or a.get("title", ""))[:80])
    tags = Counter(t for a in items for t in (a.get("tags") or []) if isinstance(t, str))
    return {
        "total": len(items),
        "must_read": sum(1 for a in items if a["grade"] == "must_read"),
        "sections": dict(sections.most_common()),
        "event_types": dict(events.most_common(6)),
        "top_tags": [t for t, _ in tags.most_common(8)],
        "report_candidates": report_cands[:5],
    }


def coverage_gaps(items: list[dict]) -> list[str]:
    """sources.json tier1 매체 중 이번 주 0건인 곳 — 소스 공백 표시 (LLM 안 씀)."""
    try:
        cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    seen = " ".join((a.get("domain") or "").lower() for a in items)
    gaps = []
    for entry in cfg.get("tier1", []):
        dom = (entry.get("domain") or "").lower()
        if dom and dom not in seen:
            gaps.append(entry.get("name") or dom)
    return gaps


def followup_hits(items: list[dict]) -> list[str]:
    """지난주 watchpoint 사후 검증은 상태가 없어 불가 — 대신 이번 주 배송된 기사와
    겹치는 후속 흐름(동일 태그 3회 이상)을 반복 노출 신호로 표시."""
    tags = Counter(t for a in items for t in (a.get("tags") or []) if isinstance(t, str))
    return [f"{t} ({n}회)" for t, n in tags.most_common(5) if n >= 3]


# ---- 합성 + 포맷 ---------------------------------------------------------------

def batch_synthesize(items: list[dict], agg: dict) -> dict:
    fallback = {"weekly_intro": "", "policy_shifts": [], "theme_moves": [],
                "khnp_direct": "", "watchpoints": [], "report_candidates": [],
                "key_events": []}
    if not items or not os.environ.get("GEMINI_API_KEY", ""):
        return fallback

    lines = []
    for a in items:
        t = (a.get("title_kr") or a.get("title") or "")[:80]
        lines.append(f"hash:{a['hash'][:8]} | [{a.get('section','')}/{a['grade']}] "
                     f"{t} | {a.get('summary','')[:60]}")
    user_text = (f"[시스템 집계]\n{json.dumps(agg, ensure_ascii=False)}\n\n"
                 f"[지난 7일 기사 {len(items)}건]\n" + "\n".join(lines))

    try:
        from gemini_client import call_json
        result = call_json(WEEKLY_PROMPT, user_text,
                           temperature=0.3, max_output_tokens=10000, timeout=120.0)
    except Exception as e:  # noqa: BLE001
        print(f"  ! weekly synthesis failed: {type(e).__name__}: {e}")
        return fallback

    out = dict(fallback)
    for key in out:
        v = result.get(key)
        if isinstance(fallback[key], list):
            out[key] = v if isinstance(v, list) else []
        else:
            out[key] = str(v or "")
    out["key_events"] = out["key_events"][:5]
    out["report_candidates"] = out["report_candidates"][:3]
    prune_evidence_hashes(out, items)
    return out


def prune_evidence_hashes(synthesis: dict, items: list[dict]) -> None:
    """근거 hash 를 이번 주 입력에 실제로 있는 것만 남긴다.

    전역 key_events 만으로는 어떤 hash 가 어느 문장의 근거인지 알 수 없어 모든
    문장에 같은 칩이 붙는다. 문장별 evidence_hashes 를 받되, LLM 이 지어낸 hash 는
    화면에서 죽은 칩이 되므로 여기서 잘라낸다.
    """
    known = {str(item["hash"])[:8] for item in items if item.get("hash")}
    for key in ("policy_shifts", "theme_moves"):
        for row in synthesis.get(key) or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("evidence_hashes")
            # 순서를 보존하며 중복 제거한다. set 으로 걸러 내면 순서가 실행마다
            # 달라져 dirty 판정이 항상 참이 되고, 같은 리포트를 무한히 다시 쓴다.
            kept: list[str] = []
            for value in raw if isinstance(raw, list) else []:
                short = str(value)[:8]
                if short in known and short not in kept:
                    kept.append(short)
            row["evidence_hashes"] = kept[:2]


def article_by_hash8(items: list[dict], h8: str) -> dict | None:
    for art in items:
        if art["hash"][:8] == (h8 or "")[:8]:
            return art
    return None


# ---- 웹용 주간 리포트 저장 -----------------------------------------------------
#
# 지금까지 주간 리포트는 텔레그램 텍스트로만 나가고 사라졌다. 웹 '주간 흐름'
# 탭은 키워드·slope 같은 정량 관찰뿐이라, 정책 변화와 한수원 직접 영향을 해석하는
# 문단이 붙으면 뉴스 사이트에서 정책 브리핑 도구로 넘어간다.
# batch_synthesize 결과를 그대로 재사용하므로 Gemini 호출은 늘지 않는다.
WEEKLY_REPORTS_FILE = ROOT / "weekly_reports.json"


def week_id(day: datetime) -> str:
    """Asia/Seoul 기준 ISO 주차. UTC 로 계산하면 연말·주말 경계가 엇갈린다."""
    year, week, _ = day.astimezone(KST).isocalendar()
    return f"{year}-W{week:02d}"


def load_weekly_reports(path: Path | None = None) -> dict:
    path = path or WEEKLY_REPORTS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "reports": {}}
    reports = raw.get("reports")
    return {"schema_version": 1,
            "reports": reports if isinstance(reports, dict) else {}}


def save_weekly_report(synthesis: dict, agg: dict, items: list[dict],
                       now: datetime | None = None,
                       path: Path | None = None) -> bool:
    """이번 주 리포트를 저장. 저장했으면 True.

    저장 여부를 len(reports) 증가로 판정하면 안 된다 — 같은 주차 덮어쓰기는
    크기가 그대로라 영영 저장 안 된 것처럼 보인다. 명시적 dirty 플래그를 쓴다.
    """
    now = (now or datetime.now(KST)).astimezone(KST)
    path = path or WEEKLY_REPORTS_FILE
    store = load_weekly_reports(path)
    key = week_id(now)
    start = now - timedelta(days=6)
    entry = {
        "week_id": key,
        "week_start": start.date().isoformat(),
        "week_end": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "timezone": "Asia/Seoul",
        "schema_version": 1,
        # 기사 수가 아니라 병합된 고유 이슈 수. 기사 수를 쓰면 후속 보도가 많은
        # 주가 실제보다 풍성해 보인다.
        "source_issue_count": count_unique_issues(items),
        "article_count": agg.get("total", len(items)),
        **{key_name: synthesis.get(key_name) for key_name in (
            "weekly_intro", "policy_shifts", "theme_moves", "khnp_direct",
            "watchpoints", "report_candidates", "key_events")},
    }
    # 내용 비교에서 generated_at 은 뺀다 — 매 실행마다 달라지므로 포함하면
    # dirty 가 항상 참이 되고 같은 리포트를 무한히 다시 쓴다.
    def content(row: dict | None) -> dict:
        return {k: v for k, v in (row or {}).items() if k != "generated_at"}

    if content(store["reports"].get(key)) == content(entry):
        print(f"[weekly] {key} 리포트 변경 없음")
        return False
    store["reports"][key] = entry
    # 최근 26주만 보관 — 반년치면 화면·빌드에 충분하다.
    for stale in sorted(store["reports"])[:-26]:
        store["reports"].pop(stale, None)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"[weekly] {key} 리포트 저장 (이슈 {entry['source_issue_count']}건)")
    return True


def count_unique_issues(items: list[dict]) -> int:
    """제목 정규화로 같은 사건을 하나로 센다. 웹의 이슈 병합만큼 정교하진 않지만
    '후속 보도가 많은 주 = 풍성한 주'라는 착시는 막는다."""
    seen = set()
    for item in items:
        title = (item.get("title_kr") or item.get("title") or "").strip().lower()
        key = "".join(ch for ch in title if ch.isalnum())
        if key:
            seen.add(key[:40])
    return len(seen)


def format_weekly(items: list[dict], synthesis: dict | None = None) -> str:
    today = datetime.now(KST)
    start = today - timedelta(days=6)
    agg = build_aggregates(items)
    synthesis = synthesis if synthesis is not None else batch_synthesize(items, agg)

    parts: list[str] = []
    parts.append(f"📅 <b>{start.month}/{start.day}-{today.month}/{today.day} "
                 f"원자력 주간 판세</b>")
    parts.append(f"<i>총 {agg['total']}건 검토 · must_read {agg['must_read']}건</i>")
    parts.append("")

    if synthesis["weekly_intro"]:
        parts.append("<b>이번 주 핵심</b>")
        parts.append(html.escape(synthesis["weekly_intro"]))
        parts.append("")

    if synthesis["policy_shifts"]:
        parts.append("━━ <b>🏛 정책 변화</b> ━━")
        for p in synthesis["policy_shifts"][:4]:
            if not isinstance(p, dict) or not p.get("what"):
                continue
            parts.append(f"• <b>{html.escape(str(p['what']))}</b>")
            if p.get("so_what"):
                parts.append(f"  → {html.escape(str(p['so_what']))}")
        parts.append("")

    if synthesis["theme_moves"]:
        parts.append("━━ <b>💰 투자 테마 강약</b> ━━")
        arrow = {"강화": "▲", "약화": "▼", "유지": "―"}
        for t in synthesis["theme_moves"][:4]:
            if not isinstance(t, dict) or not t.get("theme"):
                continue
            d = arrow.get(str(t.get("direction", "")), "―")
            line = f"{d} <b>{html.escape(str(t['theme']))}</b>"
            if t.get("why"):
                line += f" — {html.escape(str(t['why']))}"
            parts.append(line)
        parts.append("")

    if synthesis["khnp_direct"]:
        parts.append("━━ <b>🇰🇷 한국·한수원 직접 영향</b> ━━")
        parts.append(html.escape(synthesis["khnp_direct"]))
        parts.append("")

    if synthesis["key_events"]:
        parts.append("━━ <b>📌 핵심 사건</b> (최대 5) ━━")
        for ev in synthesis["key_events"][:5]:  # 렌더링에서도 방어 (LLM 초과 응답 컷)
            if not isinstance(ev, dict):
                continue
            art = article_by_hash8(items, ev.get("hash", ""))
            headline = ev.get("headline") or (art["title"] if art else "")
            if not headline:
                continue
            parts.append(f"• <b>{html.escape(str(headline))}</b>")
            if ev.get("implication"):
                parts.append(f"  → {html.escape(str(ev['implication']))}")
            if art and art.get("link"):
                parts.append(f"  🔗 {art['link']}")
        parts.append("")

    if synthesis["report_candidates"]:
        parts.append("━━ <b>📝 보고서 검토 후보</b> ━━")
        for r in synthesis["report_candidates"]:
            if not isinstance(r, dict) or not r.get("topic"):
                continue
            line = f"• <b>{html.escape(str(r['topic']))}</b>"
            if r.get("basis"):
                line += f" — {html.escape(str(r['basis']))}"
            parts.append(line)
        parts.append("")

    if synthesis["watchpoints"]:
        parts.append("📋 <b>다음 주 모니터링 포인트</b>")
        for wp in synthesis["watchpoints"][:5]:
            parts.append(f"• {html.escape(str(wp))}")
        parts.append("")

    # ---- Python 계산 부록 (LLM 무관 — 항상 사실) ----
    repeats = followup_hits(items)
    if repeats:
        parts.append(f"🔁 <b>반복 등장</b>: {html.escape(', '.join(repeats))}")
    gaps = coverage_gaps(items)
    if gaps:
        parts.append(f"🕳 <b>이번 주 소스 공백</b>: {html.escape(', '.join(gaps[:6]))}")

    return "\n".join(parts).strip()


def main() -> None:
    curated = load_curated()
    items = get_week_articles(curated)
    if not items:
        print("No articles in past week. Skipping weekly report.")
        return

    print(f"Weekly report: {len(items)} articles from past {WEEK_DAYS} days")
    # 합성을 한 번만 돌려 텔레그램과 웹이 같은 결과를 쓴다 (Gemini 호출 +0).
    agg = build_aggregates(items)
    synthesis = batch_synthesize(items, agg)
    message = format_weekly(items, synthesis)
    save_weekly_report(synthesis, agg, items)

    from telegram_send import send_long_text  # lazy — 토큰 없는 로컬 테스트 대비
    results = send_long_text(message, parse_mode="HTML", disable_preview=True)
    ok = sum(1 for r in results if r.get("ok"))
    print(f"Weekly report sent ({ok}/{len(results)}).")


if __name__ == "__main__":
    main()
