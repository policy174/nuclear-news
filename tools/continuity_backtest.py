"""연속성 판정 백테스트 — 지난 발송 이력으로 issue_continuity 를 채점한다.

5차 이식(P2) 게이트: ranking 배선 전에, 실제로 나간 브리핑에서 이 모듈이
'반복'이라 판정했을 쌍을 뽑아 사람이 채점한다. 오판(재탕→변화 / 변화→재탕)이
20건 중 2건을 넘으면 배선하지 않는다.

    python tools/continuity_backtest.py [--days 14] [--limit 20]

LLM 0회 · 네트워크 0회 — delivery_log.jsonl 만 읽는다. V1 발송 행에는
summary·tags·fingerprint 가 없어 제목·앵커 경로만 작동한다. 그 상태의 성능이
곧 '배선 직후 성능'이므로 게이트로서는 그게 맞다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import issue_continuity as continuity  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14, help="검사할 발송일 범위")
    parser.add_argument("--limit", type=int, default=20, help="채점표 최대 쌍 수")
    parser.add_argument("--out", default="docs/2026-09-07-continuity-backtest.md")
    args = parser.parse_args()

    cfg = continuity.resolve_config(None)
    lookback = int(cfg.get("lookback_days", 14))
    rows = continuity.load_recent_sent(args.days + lookback)
    if not rows:
        print("delivery_log 에 발송 행이 없음")
        return 1
    by_date: dict[str, list[dict]] = {}
    for row in rows:
        by_date.setdefault(str(row["date"])[:10], []).append(row)
    days = sorted(by_date)[-args.days:]

    verdicts: list[dict] = []
    for day in days:
        candidates = by_date[day]
        cutoff = (date.fromisoformat(day) - timedelta(days=lookback)).isoformat()
        recent = [r for r in rows
                  if cutoff <= str(r["date"])[:10] < day]
        if not recent:
            continue
        generic = continuity.generic_anchors(candidates + recent)
        for cand in candidates:
            verdict = continuity.verdict_for(cand, recent, cfg, day, generic)
            if verdict:
                verdicts.append({"day": day, "cand": cand, "v": verdict})

    total = sum(len(by_date[d]) for d in days)
    print(f"발송 {total}건 / {len(days)}일 중 매칭 {len(verdicts)}건")
    sample = verdicts[-args.limit:]

    lines = [
        "# 연속성 판정 백테스트 채점표 (5차 이식 P2 게이트)",
        "",
        f"- 대상: 최근 {len(days)}일 발송 {total}건, 매칭 {len(verdicts)}건 중 최근 {len(sample)}건",
        "- 채점 기준: 판정(progression·감점·drop)이 사람 눈에 맞으면 O, 틀리면 X + 사유",
        "- 통과 기준: X ≤ 2건. 통과 시 ranking 배선(V2 score_item·rank_and_select) 이식.",
        "- V1 발송 행엔 fingerprint·근거 목록이 없어 제목·앵커 경로만 작동한 결과다.",
        "",
        "| # | 발송일 | 오늘 제목 | 직전 제목 (일전) | 판정 | 근거 | 감점 | drop | 채점 |",
        "|---|--------|-----------|------------------|------|------|------|------|------|",
    ]
    for index, row in enumerate(sample, 1):
        v = row["v"]
        title = str(row["cand"].get("title_kr") or "")[:46]
        prior = f"{v['prior_title'][:46]} ({v['days_ago']}일전)"
        reasons = ";".join(v["match_reasons"])[:60]
        lines.append(
            f"| {index} | {row['day']} | {title} | {prior} "
            f"| {v['progression']}{(':' + v['progression_kind']) if v['progression_kind'] else ''} "
            f"| {reasons} | {v['penalty']} | {'Y' if v['drop'] else ''} |  |")
    out = ROOT / args.out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"채점표: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
