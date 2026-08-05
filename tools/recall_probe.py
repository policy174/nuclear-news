"""재현율 프로브 — 누락이 어느 단계에서 났는지 가른다.

세 단계를 **따로** 잰다. 하나의 숫자로 합치면 "네이버에 있는데 우리에 없다"까지만
알 수 있고, 그게 수집 문제인지 랭킹 문제인지 알 수 없다.

    collect   기사가 archive/*.jsonl 에 들어왔는가        → 수집기·키워드 문제
    issue     issues.json 에 이슈로 존재하는가             → 클러스터링·큐레이션 문제
    exposure  그 날 브리핑에 노출됐는가                    → 하한·캡 문제

⚠️ 이것은 '네이버 검색 대비 수록률'이 아니다. 네이버 API 자체가 공식 보도자료·
미제휴 지역지·인덱싱 지연 기사를 누락하므로 그걸 정답지로 쓰면 재현율을 과대평가한다.
정답지는 ``tools/gold_events/<date>.json`` 의 **사람이 확인한** 이벤트 목록이다.

⚠️ 창(window)을 지킬 것. 브리핑은 전날 07:00 KST ~ 당일 07:00 KST 수집분으로 만들어진다.
창 밖 기사를 누락으로 세면 안 된다 — 실제로 08-05 저녁 기사를 08-05 07:23 브리핑과
대조해 재현율을 25%로 잘못 낸 적이 있다.

사용:
    python tools/recall_probe.py --date 2026-08-06
    python tools/recall_probe.py --date 2026-08-06 --data-dir web/public/data
    python tools/recall_probe.py --date 2026-08-06 --json      # 기계 판독용
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_quality import normalize_url  # noqa: E402

KST = timezone(timedelta(hours=9))
GOLD_DIR = ROOT / "tools" / "gold_events"
ARCHIVE_DIR = ROOT / "archive"
DEFAULT_DATA_DIR = ROOT / "web" / "public" / "data"

# 수집 시각(archived_at)은 발행 시각보다 늦다 — crawl 이 매시 정각이라 최대 1시간,
# GitHub cron 지연이 실측 50~66분이라 여유를 둔다. 창 끝에 걸친 기사를 '미수집'으로
# 잘못 세지 않기 위한 마진이다.
COLLECT_LAG = timedelta(hours=3)

_WS = re.compile(r"\s+")


def norm(text: object) -> str:
    """공백을 접고 소문자로 — '차등 요금'과 '차등요금'은 정답지에서 둘 다 적는다."""
    return _WS.sub(" ", str(text or "")).strip().lower()


def haystack(*parts: object) -> str:
    return norm(" ".join(str(p or "") for p in parts))


def matches(event: dict, text: str, urls: list[str]) -> bool:
    """정답지 이벤트가 이 레코드와 같은 사건인가."""
    gold_urls = {normalize_url(u) for u in event.get("source_urls", []) if u}
    if gold_urls & {normalize_url(u) for u in urls if u}:
        return True
    rule = event.get("match") or {}
    required = [norm(t) for t in rule.get("all", []) if t]
    optional = [norm(t) for t in rule.get("any", []) if t]
    if not required and not optional:
        return False
    if any(term not in text for term in required):
        return False
    if optional and not any(term in text for term in optional):
        return False
    return True


def load_gold(date: str) -> dict:
    path = GOLD_DIR / f"{date}.json"
    if not path.exists():
        raise SystemExit(f"정답지 없음: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_archive(window_from: datetime, window_to: datetime) -> list[dict]:
    """창 안에 수집된 아카이브 레코드. noise 도 포함한다 — 수집 여부를 재는 단계이지
    선별 여부를 재는 단계가 아니다."""
    rows: list[dict] = []
    for path in sorted(ARCHIVE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = parse_dt(row.get("archived_at"))
            if stamp is None or not (window_from <= stamp <= window_to + COLLECT_LAG):
                continue
            rows.append(row)
    return rows


def probe(date: str, data_dir: Path) -> dict:
    gold = load_gold(date)
    window = gold.get("window") or {}
    w_from = parse_dt(window.get("article_from")) or datetime.min.replace(tzinfo=KST)
    w_to = parse_dt(window.get("article_to")) or datetime.now(KST)

    archive_rows = load_archive(w_from, w_to)
    issues = json.loads((data_dir / "issues.json").read_text(encoding="utf-8"))
    briefings = json.loads((data_dir / "briefings.json").read_text(encoding="utf-8"))
    briefing = next((b for b in briefings if b.get("date") == date), None)
    exposed = (briefing or {}).get("issues", []) or []

    results = []
    for event in gold.get("events", []):
        hit_collect = next(
            (r for r in archive_rows
             if matches(event, haystack(r.get("title_kr"), r.get("title"),
                                        r.get("summary")), [r.get("url")])), None)
        hit_issue = next(
            (i for i in issues
             if matches(event, haystack(i.get("title"), i.get("summary")),
                        [(i.get("representative_article") or {}).get("url")])), None)
        hit_exposed = next(
            (i for i in exposed
             if matches(event, haystack(i.get("title"), i.get("summary")), [])), None)
        results.append({
            "event_id": event["event_id"],
            "label": event.get("label", ""),
            "required": bool(event.get("required")),
            "region": event.get("region", ""),
            "collected": bool(hit_collect),
            "clustered": bool(hit_issue),
            "exposed": bool(hit_exposed),
            "collected_as": (hit_collect or {}).get("title_kr") or (hit_collect or {}).get("title"),
            "collected_importance": (hit_collect or {}).get("importance"),
            "collected_publisher": (hit_collect or {}).get("publisher"),
            "issue_article_count": (hit_issue or {}).get("article_count"),
        })

    def rate(rows: list[dict], key: str) -> float:
        return round(sum(1 for r in rows if r[key]) / len(rows), 4) if rows else 0.0

    required = [r for r in results if r["required"]]
    return {
        "date": date,
        "briefing_present": briefing is not None,
        "archive_rows_in_window": len(archive_rows),
        "events_total": len(results),
        "events_required": len(required),
        "recall": {
            "collect": rate(required, "collected"),
            "issue": rate(required, "clustered"),
            "exposure": rate(required, "exposed"),
        },
        "recall_all_events": {
            "collect": rate(results, "collected"),
            "issue": rate(results, "clustered"),
            "exposure": rate(results, "exposed"),
        },
        "events": results,
    }


def render(report: dict) -> str:
    out = [
        f"재현율 프로브 — {report['date']}",
        f"  창 안 아카이브 {report['archive_rows_in_window']}건 / "
        f"정답지 {report['events_total']}건(필수 {report['events_required']})",
    ]
    if not report["briefing_present"]:
        out.append("  ⚠️ 이 날짜의 브리핑이 아직 없다 — exposure 는 판정 불가(전부 미노출로 계산됨)")
    out.append("")
    out.append(f"  {'수집':>4} {'이슈':>4} {'노출':>4}  이벤트")
    for e in report["events"]:
        mark = lambda v: " O  " if v else " ×  "  # noqa: E731
        star = "*" if e["required"] else " "
        out.append(f"  {mark(e['collected'])}{mark(e['clustered'])}{mark(e['exposed'])} "
                   f"{star}{e['label']}")
        if e["collected"] and not e["exposed"]:
            out.append(f"        └ 수집됨: [{e['collected_importance']}] "
                       f"{e['collected_publisher']} — {(e['collected_as'] or '')[:50]}")
    r = report["recall"]
    out += [
        "",
        f"  필수 이벤트 재현율 — 수집 {r['collect']:.0%} / 이슈 {r['issue']:.0%} / 노출 {r['exposure']:.0%}",
        "  (* = required)",
    ]
    return "\n".join(out)


def main() -> None:
    # Windows 기본 콘솔은 cp949 라 '—'·'×' 에서 UnicodeEncodeError 로 죽는다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description="3단계 재현율 측정")
    ap.add_argument("--date", required=True, help="브리핑 날짜 (YYYY-MM-DD)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                    help="issues.json·briefings.json 이 있는 디렉터리")
    ap.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = ap.parse_args()

    report = probe(args.date, Path(args.data_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))


if __name__ == "__main__":
    main()
