# -*- coding: utf-8 -*-
"""순위 변형 백테스트 — 게시된 briefings.json + delivery_log 점수로 정렬 입력을
복원하고, `order_issue_rows` 변형들을 나란히 돌려 지표를 비교한다.

    python web/tools/rank_backtest.py            # 지표표 + 최근 3일 상위 5
    python web/tools/rank_backtest.py --day 2026-09-16

**왜 이 도구가 있나.** 순위는 홈·텔레그램 브리핑·카드뉴스가 함께 쓴다. 한 줄
고치면 세 화면이 같이 움직이는데, 지금까지 그 영향을 매번 손으로 세고 있었다
(09-16 "60일 중 37일 변동" 같은 숫자가 커밋 메시지에만 남았다). 다음 사람이
같은 질문에 같은 방법으로 답할 수 있어야 한다.

**하네스 자기검증.** 맨 먼저 CUR(현행 정렬키)이 게시된 순서를 재현하는지 찍는다.
재현이 깨지면 그 아래 숫자는 전부 무의미하므로 그때는 지표를 읽지 말 것 —
복원식(rows_for)이 실제 빌드와 어긋났다는 뜻이다.

**한계.** 오병합 차단(2026-09-17)의 효과는 여기서 잴 수 없다. 임베딩 벡터가
아카이브에 없어 어떤 병합이 풀리는지 재현이 안 된다. 이 도구가 재는 것은
`order_issue_rows` 안쪽, 즉 **클러스터가 이미 정해진 뒤의 줄 세우기**다.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIEFINGS = ROOT / "web" / "public" / "data" / "briefings.json"
DELIVERY_LOG = ROOT / "delivery_log.jsonl"
COOLDOWN_DAYS = 3


def load_scores(path: Path) -> tuple[dict, dict]:
    """delivery_log 의 기사 단위 점수·내역. (date, hash) → score / breakdown."""
    scores: dict[tuple[str, str], float] = {}
    breakdowns: dict[tuple[str, str], dict] = {}
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("record_type") is not None or row.get("score") is None:
                continue
            key = (row.get("date"), row.get("hash"))
            if not key[1]:
                continue
            scores[key] = float(row["score"])
            breakdowns[key] = row.get("breakdown") or {}
    return scores, breakdowns


def rows_for(briefing: dict, scores: dict, breakdowns: dict) -> list[dict]:
    """게시된 이슈 → order_issue_rows 가 받는 모양으로 복원."""
    out: list[dict] = []
    for issue in briefing["issues"]:
        rep = issue.get("representative_article") or {}
        key = (briefing["date"], rep.get("hash"))
        breakdown = breakdowns.get(key, {})
        out.append({
            "issue_id": issue["issue_id"],
            "title": issue["title"],
            "region": issue.get("region") or "",
            "importance": issue.get("importance") or "",
            "article_count": issue.get("article_count") or 0,
            "sort_score": scores.get(key, 0.0),
            # 축 하나를 빼고 다시 세우려고 기여분을 따로 들고 있는다. breakdown 은
            # 가산식이라(합 = score) 빼기만 하면 그 축이 없던 점수가 된다.
            "evidence": float(breakdown.get("evidence_strength") or 0),
            "scheduled": 1 if rep.get("event_date_type") == "scheduled" else 0,
            "last_seen": issue.get("last_seen") or "",
            "published_pos": len(out),
        })
    return out


def order(rows: list[dict], recent_top: set[str], *,
          cooldown_exempts_must_read: bool = True,
          multi_article_bonus: float | None = None,
          drop_evidence: bool = False) -> list[dict]:
    """web/build_data.order_issue_rows 의 이식본. 옵션이 변형을 만든다.

    기본값은 **현재 배포된 정렬키**다(2026-09-17 쿨다운 면제 반영).
    multi_article_bonus 가 None 이면 기사 2건 불리언이 하드 게이트로 남는다.
    """
    def score(row: dict) -> float:
        value = row["sort_score"] - (row["evidence"] if drop_evidence else 0.0)
        if multi_article_bonus is not None and row["article_count"] >= 2:
            value += multi_article_bonus
        return value

    def within_region(row: dict) -> tuple:
        must_read = row["importance"] == "must_read"
        cooled = row["issue_id"] in recent_top
        if cooldown_exempts_must_read and must_read:
            cooled = False
        gate = (row["article_count"] >= 2) if multi_article_bonus is None else False
        return (0, must_read, not cooled, gate, score(row), row["last_seen"])

    domestic = sorted((r for r in rows if r["region"] == "국내"),
                      key=within_region, reverse=True)
    overseas = sorted((r for r in rows if r["region"] != "국내"),
                      key=within_region, reverse=True)
    rank = {r["issue_id"]: i for group in (domestic, overseas) for i, r in enumerate(group)}
    return sorted(rows, key=lambda r: (
        rank[r["issue_id"]],
        0 if r["importance"] == "must_read" else 1,
        r["scheduled"],
        -score(r),
    ))


VARIANTS: dict[str, dict] = {
    "배포본": dict(),
    "P2 근거강도빼기": dict(drop_evidence=True),
    "P3 게이트→+2": dict(multi_article_bonus=2),
    "P3 게이트→+3": dict(multi_article_bonus=3),
    "P3 게이트→+4": dict(multi_article_bonus=4),
    "P2+P3(+3)": dict(drop_evidence=True, multi_article_bonus=3),
    "P2+P3(+2)": dict(drop_evidence=True, multi_article_bonus=2),
}


def run(briefings: list[dict], scores: dict, breakdowns: dict, opts: dict) -> dict:
    """쿨다운은 **그 변형 자신의 1번 이력**을 따른다 — 현행 이력을 쓰면 변형이
    만든 새 1번이 다음 날 쿨다운에 안 걸려 반복을 과소평가한다."""
    history: list[str | None] = []
    out: dict[str, list[dict]] = {}
    for briefing in briefings:
        rows = rows_for(briefing, scores, breakdowns)
        if not rows:
            history.append(None)
            continue
        recent = {h for h in history[-COOLDOWN_DAYS:] if h}
        ordered = order(rows, recent, **opts)
        out[briefing["date"]] = ordered
        history.append(ordered[0]["issue_id"])
    return out


# 의례성 기사 — 행사·훈련처럼 "누가 무엇을 했다"가 아니라 "누가 무엇을 열었다"인
# 것. 지니 지적(2026-09-17)의 표적이다: "그 뉴스를 누가 본다고."
CEREMONY_RE = re.compile(
    r"훈련|행사|개최|기념|캠페인|협약식|봉사|후원|시상|수상|간담회|워크숍|세미나|"
    r"발대식|출범식|위촉|공모전")


def metrics(result: dict, baseline: dict | None, score_of) -> tuple[int, collections.Counter]:
    days = [d for d in sorted(result) if len(result[d]) >= 4]
    counts: collections.Counter = collections.Counter()
    for i, day in enumerate(days):
        rows = result[day]
        top3 = rows[:3]
        top_ids = {r["issue_id"] for r in top3}
        counts["must_in_top3"] += sum(1 for r in top3 if r["importance"] == "must_read")

        # '최고점'은 **그 변형 자신의 점수**로 잰다. 배포본 점수로 재면 축을 하나
        # 뺀 변형이 자동으로 나빠 보인다 — 바꾸려는 그 축으로 채점하는 꼴이다.
        graded = [r for r in rows if r["importance"] == "must_read"]
        if graded and max(graded, key=score_of)["issue_id"] not in top_ids:
            counts["최고점MR누락"] += 1
        if rows and max(rows, key=score_of)["issue_id"] not in top_ids:
            counts["최고점누락"] += 1
        # 지니가 실제로 물은 것 — 행사·훈련 기사가 '먼저 볼 3건'에 들었는가
        if any(CEREMONY_RE.search(r["title"]) for r in top3):
            counts["의례성top3"] += 1
        # 단신 역전 — 상위 3건의 1건짜리 무등급이 같은 지역 3건+ 이슈보다 위
        for r in top3:
            if r["article_count"] > 1 or r["importance"] == "must_read":
                continue
            pos = rows.index(r)
            if any(q["article_count"] >= 3 and (q["region"] == "국내") == (r["region"] == "국내")
                   for q in rows[pos + 1:]):
                counts["단신역전"] += 1
                break
        if any(r["region"] == "국내" for r in top3):
            counts["국내top3"] += 1
        if i and result.get(days[i - 1]) and result[days[i - 1]][0]["issue_id"] == rows[0]["issue_id"]:
            counts["1번반복"] += 1
        if baseline is not None and \
                [r["issue_id"] for r in top3] != [r["issue_id"] for r in baseline[day][:3]]:
            counts["top3변동"] += 1
    return len(days), counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", action="append", default=[],
                        help="이 날짜의 변형별 상위 5건을 찍는다 (여러 번 가능)")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    briefings = sorted(json.loads(BRIEFINGS.read_text(encoding="utf-8")),
                       key=lambda b: b["date"])
    scores, breakdowns = load_scores(DELIVERY_LOG)
    results = {name: run(briefings, scores, breakdowns, opts)
               for name, opts in VARIANTS.items()}

    def scorer(opts: dict):
        def inner(row: dict) -> float:
            value = row["sort_score"] - (row["evidence"] if opts.get("drop_evidence") else 0.0)
            bonus = opts.get("multi_article_bonus")
            if bonus is not None and row["article_count"] >= 2:
                value += bonus
            return value
        return inner

    base = results["배포본"]
    joined = sum(1 for rows in base.values() for r in rows if r["sort_score"])
    total = sum(len(rows) for rows in base.values())
    print(f"[하네스] 브리핑 {len(base)}일 · 이슈 {total}건 · 점수 복원 {joined}건 "
          f"({joined / max(total, 1) * 100:.0f}%)")
    # 쿨다운 면제 배포(2026-09-17) 전 게시분과는 상위 3건이 어긋나는 날이 있다.
    # 그 차이가 12일 안팎이면 정상 — 그보다 크면 복원식을 의심할 것.
    drift = sum(1 for day, rows in base.items()
                if [r["issue_id"] for r in rows[:3]]
                != [r["issue_id"] for r in sorted(rows, key=lambda r: r["published_pos"])[:3]])
    print(f"[하네스] 게시 상위3 과 어긋나는 날 {drift}/{len(base)} "
          f"(쿨다운 면제 배포 전 게시분이라 차이가 나는 것이 정상)")

    header = (f"\n{'변형':18s} {'MR/top3':>8s} {'최고점MR누락':>12s} {'최고점누락':>10s} "
              f"{'의례성top3':>10s} {'단신역전':>8s} {'국내top3':>9s} {'1번반복':>8s} {'top3변동':>9s}")
    print(header)
    for name, result in results.items():
        n, m = metrics(result, base if name != "배포본" else None, scorer(VARIANTS[name]))
        print(f"{name:18s} {m['must_in_top3'] / n:8.2f} {m['최고점MR누락']:9d}/{n} "
              f"{m['최고점누락']:7d}/{n} {m['의례성top3']:7d}/{n} {m['단신역전']:8d} "
              f"{m['국내top3']:6d}/{n} {m['1번반복']:8d} {m['top3변동']:9d}")

    for day in args.day or sorted(base)[-1:]:
        print(f"\n== {day}")
        for name, result in results.items():
            rows = result.get(day) or []
            print(f"  [{name}]")
            for i, r in enumerate(rows[:args.top], 1):
                mark = "M" if r["importance"] == "must_read" else "n"
                print(f"     {i}. {mark} {r['region'][:2]:2s} a{r['article_count']:<2d} "
                      f"{r['sort_score']:5.1f} (근거 {r['evidence']:.0f}) {r['title'][:38]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
