"""아카이브에 라벨 없이 남은 기사(features=None)를 소급 큐레이션한다.

큐레이션이 429 로 유실된 기사는 다음 크롤(6시간 lookback)이 다시 잡지만, 쿼터가
종일 막힌 날은 lookback 밖으로 밀려나 아카이브에 features=None 으로 영구 방치된다
(라이브 실측 2026-09-07: 미큐레이션 658건). 라벨이 없으면 이슈·헤드라인 후보가
못 되고, ``append_records()`` 가 hash 재적재를 차단하므로 아카이브 **라인 제자리
수선**만이 복구 경로다(선례: ``migrate_archive_quality`` 의 tmp→replace).

crawl.yml 이 UTC 07~09시(무료 일일 쿼터 리셋 00:00 PT = 07:00 UTC 직후)에 하루
1회 부른다. once-per-day 판정은 상태 파일 없이 ``backfilled_at`` 스탬프로 —
오늘 스탬프가 하나라도 있으면 이미 돈 것이다.

수선 커밋은 crawl.yml 의 기존 'Commit state'(git add archive/)에 편승한다.
별도 워크플로로 빼면 안 된다 — .gitattributes 의 ``archive/*.jsonl merge=union``
이 크롤 append 커밋과 rebase 로 만나면 수정 전·후 라인을 둘 다 남긴다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gemini_client
import news_archive

KST = timezone(timedelta(hours=9))

BACKFILL_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "100"))
# 이 횟수만큼 물어도 라벨을 못 받은 기사는 포기한다 — 영구 실패 건이 매일
# 호출을 태우면 백필이 백로그 대신 제자리를 돈다.
MAX_ATTEMPTS = 3


def _load_archive_lines() -> dict[Path, list[dict]]:
    """월별 파일 → 파싱된 레코드 목록. 라인 순서를 보존한다(제자리 수선용)."""
    out: dict[Path, list[dict]] = {}
    for path in sorted(news_archive.ARCHIVE_DIR.glob("*.jsonl")):
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        out[path] = rows
    return out


def select_targets(records: list[dict], limit: int = BACKFILL_LIMIT) -> list[dict]:
    """features 없는 레코드를 최신순으로 상한까지.

    최신 우선인 이유: 라벨의 효용(이슈·헤드라인 후보 자격)은 뉴스 창 안의
    최신 기사에 집중된다.
    """
    targets = [
        r for r in records
        if r.get("features") is None
        and not r.get("quality_drop")
        and int(r.get("features_attempts") or 0) < MAX_ATTEMPTS
    ]
    targets.sort(key=lambda r: str(r.get("archived_at") or ""), reverse=True)
    return targets[:limit]


def _pseudo_article(record: dict) -> dict:
    """아카이브 레코드 → curate_batch/make_record 가 받는 기사 dict."""
    return {
        "hash": record.get("hash", ""),
        "link": record.get("url", ""),
        "resolved_url": record.get("resolved_url", ""),
        "title": record.get("title", ""),
        "publisher": record.get("publisher", ""),
        "site_name": record.get("site_name", ""),
        "domain": record.get("domain", ""),
        "feed": record.get("feed", ""),
        "pub": record.get("pub") or None,
        # 본문은 아카이브에 없다(저작권 계약) — 요약이 있으면 그것이 단서.
        "description": record.get("summary") or "",
        "folded": record.get("folded") or [],
        "folded_count": int(record.get("folded_count") or 0),
    }


def rebuild_records(targets: list[dict], curated: dict[str, dict],
                    now_iso: str) -> dict[str, dict]:
    """{hash: 수선된 레코드}. 성공 건은 make_record 재조립, 실패 건은 시도 누적."""
    updated: dict[str, dict] = {}
    for record in targets:
        h = record.get("hash", "")
        cur = curated.get(h)
        if cur:
            new = news_archive.make_record(
                _pseudo_article(record), cur, record.get("archived_at", ""))
            new["backfilled_at"] = now_iso
        else:
            new = dict(record)
            new["features_attempts"] = int(record.get("features_attempts") or 0) + 1
        updated[h] = new
    return updated


def apply_updates(by_path: dict[Path, list[dict]], updated: dict[str, dict]) -> int:
    """수선 대상이 있는 월 파일만 라인 교체(tmp→replace). 반환은 교체 라인 수."""
    replaced = 0
    for path, rows in by_path.items():
        hit = False
        out_rows: list[dict] = []
        for row in rows:
            h = row.get("hash", "")
            if h in updated and row.get("features") is None:
                out_rows.append(updated[h])
                hit = True
                replaced += 1
            else:
                out_rows.append(row)
        if not hit:
            continue
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out_rows),
            encoding="utf-8",
        )
        temp_path.replace(path)
    return replaced


def main() -> int:
    if not gemini_client.is_available():
        print("[backfill] GEMINI_API_KEY 없음 — 종료")
        return 0

    # news_bot 은 import 시점에 NAVER 환경변수를 요구한다 — 큐레이션이 실제로
    # 필요한 여기서만 물린다(순수 함수 테스트가 env 없이 돌게).
    from news_bot import curate_batch, load_reports_kb

    by_path = _load_archive_lines()
    all_records = [r for rows in by_path.values() for r in rows]

    today = datetime.now(KST).date().isoformat()
    if any(str(r.get("backfilled_at") or "")[:10] == today for r in all_records):
        print(f"[backfill] 오늘({today}) 이미 실행됨 — 종료")
        return 0

    targets = select_targets(all_records)
    if not targets:
        print("[backfill] 미큐레이션 잔량 0 — 종료")
        return 0
    print(f"[backfill] 대상 {len(targets)}건 (상한 {BACKFILL_LIMIT})")

    articles = [_pseudo_article(r) for r in targets]
    bodies: dict[str, str] = {}
    try:
        from article_body import fetch_bodies
        bodies, stats = fetch_bodies(articles)
        print(f"[backfill] 본문 수집 {stats.get('ok', 0)}/{stats.get('attempted', 0)}건")
    except Exception as e:  # 본문은 보강 재료일 뿐 — 없어도 큐레이션은 돈다
        print(f"[backfill] 본문 수집 실패(비치명): {e}")

    curated = curate_batch(articles, load_reports_kb(), bodies)
    now_iso = datetime.now(KST).isoformat()
    updated = rebuild_records(targets, curated, now_iso)
    replaced = apply_updates(by_path, updated)

    ok = sum(1 for h in updated if h in curated)
    print(f"[backfill] 라벨 성공 {ok}건 / 시도 누적 {len(updated) - ok}건 / "
          f"라인 교체 {replaced}건")
    print(gemini_client.format_call_stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
