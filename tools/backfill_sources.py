#!/usr/bin/env python3
"""이미 쌓인 아카이브의 매체명·원문 주소를 뒤늦게 채운다.

왜: `site_name`·`resolved_url` 은 2026-08-11 부터 수집되므로 그 전 기록에는 없다.
그런데 자료 팩(정책 브리핑의 산출물)이 그 기록을 인용한다 — 실측으로 이렇게 나온다.

    - 8월 4일 · 원안위, 계속운전 원전의 … (v.daum.net)
      https://news.google.com/rss/articles/CBMiT0FVX3lxTE5hekxXbS1ZNC1o…

포털 호스트명이 매체명 자리에 있고 링크는 리다이렉트다. 인용이 인용답지 않다.
표시 기사 1,136건 중 **777건**이 여기 해당하고 그중 **645건이 최근 7일분**이라
"오래된 것만 그렇다"가 아니다.

무엇을 하나: 대상 기사의 페이지를 한 번씩 받아 `og:site_name`(매체명)과 Google
News 리다이렉트의 실주소를 얻어 **사이드카 파일**에 적는다.

    web/../archive_source_backfill.json   {hash: {site_name, resolved_url}}

**아카이브는 건드리지 않는다.** 2,500건짜리 append-only 파일을 다시 쓰는 것은
되돌리기 어렵고, 빌드가 매번 아카이브 전체를 지나가므로 옆에 얹기만 하면 된다.
이미 값이 있는 기사는 건너뛴다(멱등).

쓰는 법:
    python tools/backfill_sources.py            # 대상만 세고 끝
    python tools/backfill_sources.py --run      # 실제 수집
    python tools/backfill_sources.py --run --limit 200
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import article_body  # noqa: E402
from news_archive import looks_like_hostname  # noqa: E402

OUT = ROOT / "archive_source_backfill.json"
ARCHIVE = ROOT / "archive"
# 크롤보다 조심스럽게 — 한 번에 몰아 받는 작업이라 동시성을 낮춘다.
WORKERS = 6


def load_records() -> list[dict]:
    records = []
    for path in sorted(ARCHIVE.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def needs_work(record: dict, done: dict) -> bool:
    if record.get("hash") in done:
        return False
    # noise 는 화면에 서지 않는다 — 인용에 쓰이지 않는 것을 받으러 가지 않는다.
    # (아카이브는 트렌드 재료로 noise 도 전부 담는다.)
    if record.get("importance") == "noise" or record.get("quality_drop"):
        return False
    if record.get("site_name") and record.get("resolved_url"):
        return False
    wants_name = looks_like_hostname(record.get("publisher") or "")
    wants_url = article_body.is_google_news(record.get("url") or "")
    return bool(wants_name or wants_url)


def main() -> int:
    run = "--run" in sys.argv
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    try:
        done = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        done = {}

    targets = [r for r in load_records() if needs_work(r, done)]
    # 최근 것부터 — 사람이 지금 읽는 구간이 먼저 좋아져야 한다.
    targets.sort(key=lambda r: str(r.get("archived_at") or ""), reverse=True)
    if limit:
        targets = targets[:limit]

    print(f"대상 {len(targets)}건 (이미 채운 것 {len(done)}건)")
    if not run:
        print("실제로 받으려면 --run")
        return 0

    import requests
    session_pool: list = []

    def work(record: dict) -> tuple[str, dict]:
        if not session_pool:
            session_pool.append(requests.Session())
        meta: dict = {}
        # 본문은 버린다 — 여기서 필요한 것은 곁다리(매체명·실주소)뿐이다.
        article_body.fetch_one(record.get("url") or "", session_pool[0],
                               str(record.get("title") or ""), meta)
        got = {}
        if meta.get("site_name"):
            got["site_name"] = meta["site_name"]
        resolved = meta.get("url") or ""
        if resolved and resolved != (record.get("url") or ""):
            got["resolved_url"] = resolved
        return record.get("hash", ""), got

    filled_name = filled_url = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for index, (article_hash, got) in enumerate(pool.map(work, targets), 1):
            if article_hash and got:
                done[article_hash] = got
                filled_name += 1 if got.get("site_name") else 0
                filled_url += 1 if got.get("resolved_url") else 0
            if index % 100 == 0:
                print(f"  {index}/{len(targets)} … 매체명 {filled_name} / 주소 {filled_url}")
                OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")

    OUT.write_text(json.dumps(done, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"매체명 {filled_name}건 · 실주소 {filled_url}건 → {OUT.name} (총 {len(done)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
