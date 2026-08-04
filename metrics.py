"""
오프라인 품질 지표 — delivery_log.jsonl 로 봇을 사후 평가.

사용:
    python metrics.py            # 최근 30일
    python metrics.py --days 14

원칙:
    - 표본이 부족한 지표는 값 대신 "insufficient_data" 를 명시한다.
      (희소 데이터로 성급하게 가중치를 바꾸지 않기 위한 강제 장치)
    - 외부 호출 0. 로컬 파일만 읽는 순수 계산.

지표:
    delivered_per_day     하루 평균 발송 카드 수
    source_diversity      고유 도메인 수 / 발송 수
    topic_diversity       고유 theme(없으면 section) 수 / 발송 수
    invest_omission_rate  투자 관점이 생략된 카드 비율 (theme 없음)
    report_rec_count      보고서 추천 발송 건수

(피드백 기반 지표(positive/noise/precision/nDCG)는 2026-07-16 피드백 기능
 삭제와 함께 제거 — 이벤트 0건. 재도입 시 git 히스토리 참조.)
"""

from __future__ import annotations

import argparse
import json
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

INSUFFICIENT = "insufficient_data"
MIN_DELIVERED = 20  # 분포 지표 최소 표본


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


def load_data(days: int, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cutoff_date = (now - timedelta(days=days)).date().isoformat()
    # record_type 이 있는 줄은 기사가 아니라 부가 레코드(selection_stats 등)다.
    # 날짜만 보고 거르면 delivered_total 이 하루 1건씩 부풀어 오른다.
    return [r for r in _load_jsonl(DELIVERY_LOG_FILE)
            if not r.get("record_type") and r.get("date", "") >= cutoff_date]


def compute_metrics(delivered: list[dict], days: int) -> dict:
    m: dict = {"window_days": days, "delivered_total": len(delivered)}

    n_days = len({r.get("date") for r in delivered}) or 0
    m["delivered_per_day"] = round(len(delivered) / n_days, 2) if n_days else 0

    if len(delivered) >= MIN_DELIVERED:
        domains = {(r.get("domain") or "").lower() for r in delivered if r.get("domain")}
        topics = {(r.get("theme") or r.get("section") or "") for r in delivered}
        topics.discard("")
        m["source_diversity"] = round(len(domains) / len(delivered), 3)
        m["topic_diversity"] = round(len(topics) / len(delivered), 3)
        m["invest_omission_rate"] = round(
            sum(1 for r in delivered if not r.get("theme")) / len(delivered), 3)
    else:
        m["source_diversity"] = m["topic_diversity"] = m["invest_omission_rate"] = INSUFFICIENT

    m["report_rec_count"] = sum(1 for r in delivered if r.get("region") == "보고서추천")

    m["_note"] = (f"분포 지표는 발송 {MIN_DELIVERED}건 이상일 때만 계산됩니다. "
                  "insufficient_data 인 동안은 가중치를 바꾸지 마세요.")
    return m


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스봇 오프라인 품질 지표")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    delivered = load_data(args.days)
    print(json.dumps(compute_metrics(delivered, args.days),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
