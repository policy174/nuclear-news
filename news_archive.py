"""
뉴스 영구 아카이브 (웹사이트 데이터 기반).

문제: curated.json 은 14일 만료라 트렌드 분석("한 달간 SMR 언급 추이")의 재료가
안 쌓인다. 웹 확장(my-projects/nuclear-news-web)의 최대 병목.

해결:
  - 매시간 크롤에서 큐레이션된 기사를 noise 포함 전부 archive/YYYY-MM.jsonl 에
    append-only 적재. 만료 없음. "git이 DB" 철학 유지 (crawl.yml 이 커밋).
  - 레코드는 자기완결 — 웹사이트가 이 파일들만 읽으면 목록·요약·트렌드를 만들 수 있다.
  - 브리핑 발송(승격) 여부는 여기 저장하지 않는다 — delivery_log.jsonl 과 hash 조인.

가드레일:
  - stdlib only. 외부 의존성 0.
  - 적재 실패가 크롤·발송을 죽이면 안 된다 (호출부 try/except 방어).
  - 멱등: 최근 2개월 파일의 hash 를 로드해 재적재 차단.
  - 원문 본문은 저장하지 않는다 (저작권 — 제목·요약·링크만).

1회성 이관: python news_archive.py --backfill
  curated.json(14일 캐시)을 아카이브로 옮겨 초기 데이터를 확보한다.
  과거 항목엔 통제 태그(topics 등)가 없어 빈 값 — 신규 적재분부터 채워진다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources import credibility

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ARCHIVE_DIR = Path(__file__).parent / "archive"
RECORD_VERSION = 1


def _month_key(iso_ts: str) -> str:
    """ISO 타임스탬프 → 'YYYY-MM'. 파싱 실패 시 현재 월."""
    try:
        return iso_ts[:7] if len(iso_ts) >= 7 and iso_ts[4] == "-" else _now_month()
    except Exception:
        return _now_month()


def _now_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _month_files_recent() -> list[Path]:
    """중복 체크 대상: 최근 2개 월 파일. (기사가 월 경계를 넘어 재등장하는 경우 대비)"""
    now = datetime.now(timezone.utc)
    months = {now.strftime("%Y-%m")}
    prev = now.replace(day=1) - timedelta(days=1)
    months.add(prev.strftime("%Y-%m"))
    return [ARCHIVE_DIR / f"{m}.jsonl" for m in sorted(months)]


def load_recent_hashes() -> set[str]:
    """최근 2개월 아카이브의 hash 집합. 깨진 라인은 건너뛴다."""
    hashes: set[str] = set()
    for path in _month_files_recent():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                h = json.loads(line).get("hash")
                if h:
                    hashes.add(h)
            except json.JSONDecodeError:
                continue
    return hashes


def make_record(article: dict, cur: dict, archived_at: str) -> dict:
    """기사 원본(article) + 큐레이션 결과(cur) → 아카이브 레코드.

    본문(description)은 넣지 않는다. 웹 화면·트렌드에 필요한 필드만.
    """
    pub = article.get("pub")
    if isinstance(pub, datetime):
        pub = pub.isoformat()
    link = article.get("link") or cur.get("link") or ""
    title = article.get("title") or cur.get("title") or ""
    tier = None
    try:
        tier = credibility({"url": link, "title": title, "meta": ""})["tier"]
    except Exception:
        pass
    return {
        "v": RECORD_VERSION,
        "hash": article.get("hash", ""),
        "archived_at": archived_at,
        "pub": pub or "",
        "url": link,
        "domain": article.get("domain") or cur.get("domain") or "",
        "feed": article.get("feed") or cur.get("feed") or "",
        "source_tier": tier,
        "title": title,
        "title_kr": cur.get("title_kr", ""),
        "summary": cur.get("summary", ""),
        "implication": cur.get("implication", ""),
        "why_important": cur.get("why_important", ""),
        "importance": cur.get("importance", ""),
        "section": cur.get("section", ""),
        "scope": cur.get("scope", ""),
        "category": cur.get("category", ""),
        "tags": cur.get("tags") or [],
        "topics": cur.get("topics") or [],
        "countries": cur.get("countries") or [],
        "article_type": cur.get("article_type", ""),
        "features": cur.get("features"),
    }


def append_records(records: list[dict]) -> int:
    """레코드를 월별 파일에 append. 반환값은 적재 건수."""
    if not records:
        return 0
    ARCHIVE_DIR.mkdir(exist_ok=True)
    by_month: dict[str, list[dict]] = {}
    for r in records:
        by_month.setdefault(_month_key(r.get("archived_at", "")), []).append(r)
    for month, items in sorted(by_month.items()):
        path = ARCHIVE_DIR / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


# ---- 1회성 백필 --------------------------------------------------------------

def backfill_from_curated(curated_path: Path | None = None) -> int:
    """curated.json 의 캐시 항목을 아카이브로 이관 (이미 있는 hash 는 스킵)."""
    path = curated_path or Path(__file__).parent / "curated.json"
    try:
        curated = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[archive] curated.json 로딩 실패: {e}")
        return 0
    existing = load_recent_hashes()
    records = []
    for h, cur in curated.items():
        if h in existing:
            continue
        pseudo_article = {
            "hash": h,
            "link": cur.get("link", ""),
            "title": cur.get("title", ""),
            "domain": cur.get("domain", ""),
            "feed": cur.get("feed", ""),
            "pub": None,  # curated 캐시엔 원문 게시일이 없음
        }
        records.append(make_record(pseudo_article, cur, cur.get("cached_at", "")))
    n = append_records(records)
    print(f"[archive] 백필 완료: curated {len(curated)}건 중 {n}건 적재 (기존 {len(existing)}건 스킵)")
    return n


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        backfill_from_curated()
    else:
        hashes = load_recent_hashes()
        print(f"[archive] 최근 2개월 적재 {len(hashes)}건, 디렉터리: {ARCHIVE_DIR}")
