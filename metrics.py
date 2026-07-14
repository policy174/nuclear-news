"""
오프라인 품질 지표 — delivery_log.jsonl + feedback/*.jsonl 로 봇을 사후 평가.

사용:
    python metrics.py            # 최근 30일
    python metrics.py --days 14

원칙:
    - 표본이 부족한 지표는 값 대신 "insufficient_data" 를 명시한다.
      (희소 데이터로 성급하게 가중치를 바꾸지 않기 위한 강제 장치)
    - 외부 호출 0. 로컬 파일만 읽는 순수 계산.

지표:
    delivered_per_day     하루 평균 발송 카드 수
    feedback_coverage     발송 카드 중 피드백이 달린 비율
    positive_rate         피드백 중 긍정(중요/투자/보고서) 비율   [min 20건]
    noise_rate            피드백 중 노이즈 비율                   [min 20건]
    precision_at_k        발송 카드 중 긍정 피드백 카드 비율      [min 20건]
    ndcg_at_k             피드백 있는 날짜별 순위 품질 평균       [min 5일]
    source_diversity      고유 도메인 수 / 발송 수
    topic_diversity       고유 theme(없으면 section) 수 / 발송 수
    invest_omission_rate  투자 관점이 생략된 카드 비율 (theme 없음)
    report_rec_precision  보고서 추천 중 📌 피드백을 받은 비율    [min 5건]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).parent
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
FEEDBACK_DIR = ROOT / "feedback"

INSUFFICIENT = "insufficient_data"
MIN_FEEDBACK = 20   # 피드백 비율 지표 최소 표본
MIN_NDCG_DAYS = 5   # nDCG 최소 표본 (피드백 있는 날짜 수)
MIN_REPORT_FB = 5   # 보고서 추천 정밀도 최소 표본

POSITIVE_LABELS = {"important", "invest", "report"}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def load_data(days: int, now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    now = now or datetime.now(timezone.utc)
    cutoff_date = (now - timedelta(days=days)).date().isoformat()
    delivered = [r for r in _load_jsonl(DELIVERY_LOG_FILE)
                 if r.get("date", "") >= cutoff_date]
    feedback: list[dict] = []
    if FEEDBACK_DIR.exists():
        for p in sorted(FEEDBACK_DIR.glob("*.jsonl")):
            feedback.extend(_load_jsonl(p))
    cutoff_iso = (now - timedelta(days=days)).isoformat()
    feedback = [e for e in feedback if e.get("ts", "") >= cutoff_iso]
    return delivered, feedback


def _ndcg_for_day(day_items: list[dict], fb_by_hash: dict[str, set[str]]) -> float | None:
    """하루 발송분(점수순 가정)의 nDCG. 피드백 없는 항목은 rel=0 취급.

    rel: 긍정 피드백=1, noise=-0 (0), 무피드백=0. 긍정이 하나도 없으면 None (계산 불가).
    """
    rels = []
    for it in day_items:
        labels = fb_by_hash.get((it.get("hash") or "")[:8], set())
        rels.append(1.0 if labels & POSITIVE_LABELS else 0.0)
    if not any(rels):
        return None
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rels))
    ideal = sorted(rels, reverse=True)
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg > 0 else None


def compute_metrics(delivered: list[dict], feedback: list[dict], days: int) -> dict:
    m: dict = {"window_days": days,
               "delivered_total": len(delivered),
               "feedback_total": len(feedback)}

    n_days = len({r.get("date") for r in delivered}) or 0
    m["delivered_per_day"] = round(len(delivered) / n_days, 2) if n_days else 0

    if delivered:
        domains = {(r.get("domain") or "").lower() for r in delivered if r.get("domain")}
        topics = {(r.get("theme") or r.get("section") or "") for r in delivered}
        topics.discard("")
        m["source_diversity"] = round(len(domains) / len(delivered), 3)
        m["topic_diversity"] = round(len(topics) / len(delivered), 3)
        m["invest_omission_rate"] = round(
            sum(1 for r in delivered if not r.get("theme")) / len(delivered), 3)
    else:
        m["source_diversity"] = m["topic_diversity"] = m["invest_omission_rate"] = INSUFFICIENT

    fb_by_hash: dict[str, set[str]] = {}
    for e in feedback:
        h8 = (e.get("hash") or "")[:8]
        if h8:
            fb_by_hash.setdefault(h8, set()).add(e.get("label", ""))

    if delivered:
        with_fb = sum(1 for r in delivered if (r.get("hash") or "")[:8] in fb_by_hash)
        m["feedback_coverage"] = round(with_fb / len(delivered), 3)
    else:
        m["feedback_coverage"] = INSUFFICIENT

    if len(feedback) >= MIN_FEEDBACK:
        pos = sum(1 for e in feedback if e.get("label") in POSITIVE_LABELS)
        noise = sum(1 for e in feedback if e.get("label") == "noise")
        m["positive_rate"] = round(pos / len(feedback), 3)
        m["noise_rate"] = round(noise / len(feedback), 3)
        if delivered:
            hit = sum(1 for r in delivered
                      if fb_by_hash.get((r.get("hash") or "")[:8], set()) & POSITIVE_LABELS)
            m["precision_at_k"] = round(hit / len(delivered), 3)
        else:
            m["precision_at_k"] = INSUFFICIENT
    else:
        m["positive_rate"] = m["noise_rate"] = m["precision_at_k"] = INSUFFICIENT

    # nDCG — 날짜·지역별 발송 순서(로그 기록 순 = 순위순) 기준
    by_day: dict[tuple[str, str], list[dict]] = {}
    for r in delivered:
        by_day.setdefault((r.get("date", ""), r.get("region", "")), []).append(r)
    ndcgs = [v for v in (_ndcg_for_day(items, fb_by_hash) for items in by_day.values())
             if v is not None]
    m["ndcg_at_k"] = (round(sum(ndcgs) / len(ndcgs), 3)
                      if len(ndcgs) >= MIN_NDCG_DAYS else INSUFFICIENT)

    report_fb = [e for e in feedback if e.get("label") == "report"]
    m["report_feedback_count"] = len(report_fb)
    m["report_rec_precision"] = INSUFFICIENT  # 추천↔피드백 조인은 표본 축적 후 (백로그)

    m["_note"] = (f"positive/noise/precision 은 피드백 {MIN_FEEDBACK}건, nDCG 는 "
                  f"피드백 있는 날짜 {MIN_NDCG_DAYS}일 이상일 때만 계산됩니다. "
                  "insufficient_data 인 동안은 가중치를 바꾸지 마세요.")
    return m


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스봇 오프라인 품질 지표")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    delivered, feedback = load_data(args.days)
    print(json.dumps(compute_metrics(delivered, feedback, args.days),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
