"""아카이브 기사에 og:image 를 뒤늦게 붙인다.

왜 필요한가 — 크롤의 본문 수집은 **새로 들어온 기사에만** 돈다
(`news_bot`: `article_body.fetch_bodies(new_articles)`). 그래서 크롤에 og:image
추출을 붙여도 그 뒤에 들어오는 기사에만 생긴다. 그런데 화면이 사진을 쓰는
자리는 **전환점**이고, 전환점은 대개 며칠~몇 주 전 기사라 이미 아카이브에 있다
— 그대로 두면 표지와 필름의 사진이 영영 빈 칸이다.

아카이브 파일은 건드리지 않는다(append-only 가 이 프로젝트의 계약).
`archive_source_backfill.json` 에 옆으로 얹고, 빌드가 빈자리만 메운다
(`web/build_data._normalize_archive_record`) — site_name·resolved_url 이 이미
쓰는 길과 같다.

    python backfill_og_image.py                # 최근 60일, 최대 400건
    python backfill_og_image.py --days 14      # 범위 좁히기
    python backfill_og_image.py --limit 50     # 건수 제한
    python backfill_og_image.py --dry-run      # 쓰지 않고 세기만

멱등하다 — 이미 값이 있거나 이미 시도해 실패로 기록된 해시는 건너뛴다.
실패를 기록하는 이유는 og:image 가 없는 매체를 매번 다시 때리지 않기 위해서다.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import article_body

ROOT = Path(__file__).parent
ARCHIVE_DIR = ROOT / "archive"
BACKFILL_FILE = ROOT / "archive_source_backfill.json"
# 값이 없다는 것을 기록하는 표식. 빈 문자열이면 빌드의 `if filled.get(field)` 에
# 걸려 무시되므로 결과는 '사진 없음'이고, 우리는 재시도를 안 하게 된다.
TRIED_NONE = ""


def load_backfill() -> dict:
    try:
        data = json.loads(BACKFILL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def archive_records(days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = []
    for path in sorted(ARCHIVE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 아카이브 레코드는 `pub`(발행 시각)을 쓴다 — `article_date` 는
            # 빌드가 만드는 파생 필드라 여기에 없다(실측 2026-09 7,623줄 전부 None).
            stamp = str(record.get("pub") or record.get("archived_at") or "")[:10]
            if stamp >= cutoff:
                rows.append(record)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    # 화면이 사진을 쓰는 자리는 이슈에 붙은 기사뿐이다. 아카이브 전체를 훑으면
    # 실측상 4배를 더 때리고도 화면에는 차이가 없다 — 기본값으로 두지는 않되
    # (이슈 목록은 빌드 산출물이라 없을 수 있다) 있으면 이쪽이 훨씬 싸다.
    ap.add_argument("--from-issues", metavar="PATH",
                    nargs="?", const="web/public/data/issues.json",
                    help="이 issues.json 에 실린 기사만 대상으로 삼는다")
    args = ap.parse_args()

    backfill = load_backfill()
    wanted: set[str] | None = None
    if args.from_issues:
        try:
            data = json.loads(Path(args.from_issues).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # 죽지 않는다. issues.json 은 build_data 의 **산출물**이라 첫 빌드
            # 전에는 없고, 그때 백필이 통째로 꺼지면 사진이 영영 안 붙는다
            # (2026-09-21 CI 첫 실행이 정확히 이렇게 조용히 스킵됐다).
            print(f"[og] --from-issues 를 못 읽어 아카이브 전체로 진행한다: {exc}")
            data = None
        if data is None:
            rows = []
        else:
            rows = data if isinstance(data, list) else (data.get("issues") or [])
        if rows:
            wanted = {str(a.get("hash") or "")
                      for issue in rows for a in (issue.get("related_articles") or [])}
            wanted.discard("")
            print(f"[og] 이슈에 붙은 기사 {len(wanted)}건으로 대상을 좁힌다")
    todo = []
    for record in archive_records(args.days):
        h = str(record.get("hash") or "")
        url = str(record.get("resolved_url") or record.get("url") or record.get("link") or "")
        if not h or not url:
            continue
        if record.get("og_image"):
            continue                       # 크롤이 이미 붙였다
        if "og_image" in (backfill.get(h) or {}):
            continue                       # 이미 시도했다(값이 없어도 기록은 남는다)
        if wanted is not None and h not in wanted:
            continue
        if article_body.is_blocked(url):
            continue
        todo.append((h, url))

    # 같은 기사가 아카이브에 여러 줄로 있을 수 있다 — 해시로 한 번만.
    seen: set[str] = set()
    todo = [(h, u) for h, u in todo if not (h in seen or seen.add(h))][:args.limit]
    print(f"[og] 대상 {len(todo)}건 (최근 {args.days}일 / 상한 {args.limit})")
    if args.dry_run or not todo:
        return 0

    import requests
    session = requests.Session()

    def work(item):
        h, url = item
        try:
            resp = session.get(url, timeout=article_body.FETCH_TIMEOUT,
                               headers={"User-Agent": article_body.UA,
                                        "Accept-Language": "ko,en;q=0.8"})
            if resp.status_code >= 400:
                return h, TRIED_NONE
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return h, article_body.extract_og_image(resp.text)
        except Exception:                  # noqa: BLE001 — 사진 부재는 비치명
            return h, TRIED_NONE

    got = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for h, img in pool.map(work, todo):
            backfill.setdefault(h, {})["og_image"] = img
            got += bool(img)

    BACKFILL_FILE.write_text(json.dumps(backfill, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
    print(f"[og] 사진 확보 {got}/{len(todo)} ({got / len(todo) * 100:.0f}%) → {BACKFILL_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
