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

from data_quality import (
    clean_text,
    curation_errors,
    invalid_url_reason,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    split_title_publisher,
    title_key,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ARCHIVE_DIR = Path(__file__).parent / "archive"
REPAIRS_FILE = Path(__file__).parent / "archive_repairs.json"
RECORD_VERSION = 2


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


def load_recent_identities() -> dict[str, set[str]]:
    """최근 아카이브의 해시·정규화 URL·정확 제목 키를 함께 읽는다."""
    identities = {"hashes": set(), "urls": set(), "titles": set()}
    for path in _month_files_recent():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("hash"):
                identities["hashes"].add(record["hash"])
            normalized = normalize_url(record.get("url"))
            if normalized:
                identities["urls"].add(normalized)
            normalized_title = title_key(record.get("title"))
            if normalized_title:
                identities["titles"].add(normalized_title)
    return identities


def load_recent_titles(days: int = 21) -> list[str]:
    """최근 아카이브의 한국어 제목 목록. 랭킹의 prior_coverage 계산용.

    같은 사건을 이미 몇 번 다뤘는지 세는 데만 쓰므로 제목만 있으면 된다.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    titles = []
    for path in _month_files_recent():
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = str(record.get("archived_at") or record.get("pub") or "")[:10]
            if stamp and stamp < cutoff:
                continue
            title = record.get("title_kr") or record.get("title")
            if title:
                titles.append(title)
    return titles


def make_record(article: dict, cur: dict, archived_at: str) -> dict:
    """기사 원본(article) + 큐레이션 결과(cur) → 아카이브 레코드.

    본문(description)은 넣지 않는다. 웹 화면·트렌드에 필요한 필드만.
    """
    pub = article.get("pub")
    if isinstance(pub, datetime):
        pub = pub.isoformat()
    link = normalize_url(article.get("link") or cur.get("link") or "")
    title = article.get("title") or cur.get("title") or ""
    publisher = article.get("publisher") or cur.get("publisher") or ""
    domain = article.get("domain") or cur.get("domain") or ""
    if ("news.google." in domain or "news.google." in link) and not publisher:
        title, publisher = split_title_publisher(title)
    profile = source_profile(domain, publisher)
    event_fields = normalize_event_date_fields(cur)
    record = {
        "v": RECORD_VERSION,
        "hash": article.get("hash", ""),
        "archived_at": archived_at,
        "pub": pub or "",
        "url": link,
        "domain": domain,
        "feed": article.get("feed") or cur.get("feed") or "",
        "publisher": publisher or profile["publisher"],
        "source_type": profile["source_type"],
        "evidence_role": profile["evidence_role"],
        "source_tier": profile["source_tier"],
        "title": title,
        "title_kr": cur.get("title_kr", ""),
        "summary": cur.get("summary", ""),
        # 원문(대개 영문)에 들어가지 않고도 읽을 수 있는 3~5문장 요지.
        # **원문 본문 자체는 여기 넣지 않는다** — 저장하는 것은 한국어 요약뿐이다.
        "detail": cur.get("detail", ""),
        "implication": cur.get("implication", ""),
        "why_important": cur.get("why_important", ""),
        # '아직 확정되지 않은 것' — 사실도 해석도 아닌 세 번째 축.
        # 여기 화이트리스트에 없으면 아카이브에 안 남고 웹에서도 영영 못 본다.
        "open_question": cur.get("open_question", ""),
        "open_question_source": cur.get("open_question_source", "unknown"),
        # 빈 값이면 통과, 아니면 게이트에서 걸린 사유(llm_null·no_source·forecast…).
        # must_read 에만 채워진다. 이게 없으면 "LLM 이 안 썼다"와 "게이트가 먹었다"를
        # 사후에 가를 수 없다 — 둘은 대응이 정반대다(프롬프트 vs 게이트).
        "open_question_reject": cur.get("open_question_reject", ""),
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
    record.update(event_fields)
    return record


def append_records(records: list[dict]) -> int:
    """레코드를 월별 파일에 append. 반환값은 적재 건수."""
    if not records:
        return 0
    ARCHIVE_DIR.mkdir(exist_ok=True)
    identities = load_recent_identities()
    accepted: list[dict] = []
    for record in records:
        normalized = normalize_url(record.get("url"))
        normalized_title = title_key(record.get("title"))
        if invalid_url_reason(normalized):
            continue
        if (
            record.get("hash") in identities["hashes"]
            or normalized in identities["urls"]
            or normalized_title in identities["titles"]
        ):
            continue
        record = dict(record)
        record["url"] = normalized
        accepted.append(record)
        identities["hashes"].add(record.get("hash"))
        identities["urls"].add(normalized)
        identities["titles"].add(normalized_title)

    by_month: dict[str, list[dict]] = {}
    for r in accepted:
        by_month.setdefault(_month_key(r.get("archived_at", "")), []).append(r)
    for month, items in sorted(by_month.items()):
        path = ARCHIVE_DIR / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(accepted)


def _upgrade_record(record: dict) -> dict:
    """v1 레코드에 출처·사건일 계약을 채우고 안전한 URL/제목으로 올린다."""
    upgraded = dict(record)
    url = normalize_url(record.get("url"))
    domain = clean_text(record.get("domain")).lower()
    title = clean_text(record.get("title"))
    publisher = clean_text(record.get("publisher"))
    if ("news.google." in domain or "news.google." in url) and not publisher:
        title, publisher = split_title_publisher(title)
    profile = source_profile(domain, publisher)
    upgraded.update({
        "v": RECORD_VERSION,
        "url": url,
        "title": title,
        "publisher": publisher or profile["publisher"],
        "source_type": profile["source_type"],
        "evidence_role": profile["evidence_role"],
        "source_tier": profile["source_tier"],
    })
    upgraded.update(normalize_event_date_fields(record))

    try:
        repairs = json.loads(REPAIRS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        repairs = {}
    repair = repairs.get(record.get("hash"), {})
    if isinstance(repair, dict):
        if repair.get("drop"):
            upgraded["quality_drop"] = True
            upgraded["quality_drop_reason"] = repair.get("reason", "manual_quality_gate")
        for field in ("title_kr", "summary", "implication", "why_important",
                      "open_question"):
            if field in repair:
                upgraded[field] = clean_text(repair[field])

    # 해석 필드는 원문 사실이 아니므로 잘린 과거 문장을 추측해 보수하지 않고 숨긴다.
    for field in ("implication", "why_important"):
        field_errors = [error for error in curation_errors(upgraded) if error.startswith(field + ":")]
        if field_errors:
            upgraded[field] = ""
    return upgraded


def _record_quality_score(record: dict) -> tuple:
    return (
        1 if record.get("importance") != "noise" else 0,
        1 if not curation_errors(record, summary_limit=120) else 0,
        1 if record.get("pub") else 0,
        -(int(record.get("source_tier") or 3)),
        len(record.get("summary") or ""),
        record.get("archived_at") or "",
    )


def migrate_archive_quality(*, apply: bool = False) -> dict:
    """전체 아카이브를 v2 계약으로 이관하고 중복·오류 URL을 제거한다."""
    raw_records: list[dict] = []
    paths = sorted(ARCHIVE_DIR.glob("*.jsonl"))
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw_records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    upgraded = [_upgrade_record(record) for record in raw_records]
    manual_drops = [record for record in upgraded if record.get("quality_drop")]
    invalid_urls = [record for record in upgraded if invalid_url_reason(record.get("url"))]
    candidates = [
        record for record in upgraded
        if not record.get("quality_drop") and not invalid_url_reason(record.get("url"))
    ]
    candidates.sort(key=_record_quality_score, reverse=True)

    kept: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for record in candidates:
        url = record["url"]
        normalized_title = title_key(record.get("title"))
        if url in seen_urls or (normalized_title and normalized_title in seen_titles):
            continue
        kept.append(record)
        seen_urls.add(url)
        if normalized_title:
            seen_titles.add(normalized_title)

    kept.sort(key=lambda record: (record.get("archived_at") or "", record.get("hash") or ""))
    summary_failures = [
        record for record in kept
        if record.get("importance") != "noise"
        and any(
            error.startswith("summary:")
            for error in curation_errors(record, summary_limit=120)
        )
    ]
    stats = {
        "input": len(raw_records),
        "kept": len(kept),
        "invalid_url_removed": len(invalid_urls),
        "manual_quality_removed": len(manual_drops),
        "duplicates_removed": len(candidates) - len(kept),
        "summary_regeneration_required": len(summary_failures),
        "publisher_missing": sum(not record.get("publisher") for record in kept),
        "source_tier_missing": sum(record.get("source_tier") not in {1, 2, 3} for record in kept),
    }

    if apply:
        by_month: dict[str, list[dict]] = {}
        for record in kept:
            by_month.setdefault(_month_key(record.get("archived_at", "")), []).append(record)
        for path in paths:
            month = path.stem
            rows = by_month.pop(month, [])
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            temp_path.replace(path)
        for month, rows in by_month.items():
            path = ARCHIVE_DIR / f"{month}.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
    return stats


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
    elif "--migrate-quality" in sys.argv:
        stats = migrate_archive_quality(apply="--apply" in sys.argv)
        mode = "적용" if "--apply" in sys.argv else "미리보기"
        print(f"[archive] 품질 이관 {mode}: {json.dumps(stats, ensure_ascii=False)}")
    else:
        hashes = load_recent_hashes()
        print(f"[archive] 최근 2개월 적재 {len(hashes)}건, 디렉터리: {ARCHIVE_DIR}")
