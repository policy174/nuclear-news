"""뉴스 JSONL 원장을 정책 웹의 조회용 DB로 멱등 동기화한다.

운영 원장은 계속 ``archive/*.jsonl``이며 웹 동기화 실패는 원장이나 텔레그램
발송 상태를 변경하지 않는다. 매 실행마다 최근 레코드를 다시 보내므로 일시적인
웹 장애는 다음 실행에서 자연스럽게 복구된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
import urllib.error
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).parent
ARCHIVE_DIR = ROOT / "archive"
MAX_BATCH_SIZE = 200


def _iso(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 8:
        return text
    return fallback


def _valid_http_url(value: object) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def archive_record_to_article(record: dict) -> dict | None:
    """아카이브 한 줄을 웹 수집 계약으로 변환한다. 부적합 레코드는 건너뛴다."""
    article_hash = str(record.get("hash") or "").strip()
    url = str(record.get("url") or "").strip()
    if len(article_hash) < 8 or not _valid_http_url(url):
        return None
    archived_at = _iso(record.get("archived_at"), datetime.now(timezone.utc).isoformat())
    features = record.get("features") if isinstance(record.get("features"), dict) else {}
    title_original = str(record.get("title") or "").strip()
    title_ko = str(record.get("title_kr") or title_original).strip()
    if not title_ko:
        return None
    return {
        "hash": article_hash,
        "publishedAt": _iso(record.get("pub"), archived_at),
        "briefingDate": record.get("briefing_date") or None,
        "titleKo": title_ko,
        "titleOriginal": title_original,
        "summary": str(record.get("summary") or ""),
        "implication": str(record.get("implication") or ""),
        "whyImportant": str(record.get("why_important") or ""),
        "url": url,
        "domain": str(record.get("domain") or ""),
        "publisher": str(record.get("publisher") or record.get("domain") or ""),
        "region": str(record.get("section") or ""),
        "importance": str(record.get("importance") or ""),
        "topics": list(record.get("topics") or []),
        "countries": list(record.get("countries") or []),
        "sourceTier": record.get("source_tier") if record.get("source_tier") in {1, 2, 3} else None,
        "eventType": str(features.get("event_type") or ""),
        "policyMateriality": int(features.get("policy_materiality") or 0),
        "archivedAt": archived_at,
    }


def load_recent_articles(lookback_days: int | None = 7, now: datetime | None = None) -> list[dict]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days) if lookback_days else None
    latest_by_hash: dict[str, dict] = {}
    paths = sorted(ARCHIVE_DIR.glob("*.jsonl"), reverse=True)
    for path in paths if cutoff is None else paths[:3]:
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            archived_at = str(record.get("archived_at") or "")
            try:
                parsed_at = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
                if cutoff is not None and parsed_at < cutoff:
                    continue
            except (TypeError, ValueError):
                pass
            article = archive_record_to_article(record)
            if article:
                latest_by_hash[article["hash"]] = article
    return sorted(latest_by_hash.values(), key=lambda item: (item["archivedAt"], item["hash"]))


def _batches(items: list[dict], size: int = MAX_BATCH_SIZE) -> Iterable[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def batch_id(articles: list[dict]) -> str:
    digest = hashlib.sha256("\n".join(item["hash"] for item in articles).encode()).hexdigest()[:24]
    return f"news-{digest}-{len(articles)}"


def post_batch(base_url: str, token: str, articles: list[dict], timeout: float = 30) -> dict:
    identity = batch_id(articles)
    body = json.dumps({"batchId": identity, "articles": articles}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/ingest/news",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Batch-ID": identity,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def sync_recent_archive(base_url: str, token: str, lookback_days: int | None = 7) -> tuple[int, int]:
    articles = load_recent_articles(lookback_days)
    sent = signals = 0
    for items in _batches(articles):
        result = post_batch(base_url, token, items)
        sent += int(result.get("articles") or 0)
        signals += int(result.get("signals") or 0)
    return sent, signals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--all", action="store_true", help="전체 JSONL 원장을 최초 1회 적재")
    args = parser.parse_args(argv)
    base_url = os.getenv("POLICY_WEB_URL", "").strip()
    token = os.getenv("POLICY_INGEST_TOKEN", "").strip()
    if not base_url or not token:
        print("[policy-sync] POLICY_WEB_URL/POLICY_INGEST_TOKEN 미설정 — 동기화 건너뜀")
        return 0
    try:
        lookback = None if args.all else max(1, args.lookback_days)
        sent, signals = sync_recent_archive(base_url, token, lookback)
        print(f"[policy-sync] 기사 {sent}건 upsert, 정책 신호 {signals}건 처리")
        return 0
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        print(f"[policy-sync] 실패(뉴스봇 운영에는 영향 없음): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
