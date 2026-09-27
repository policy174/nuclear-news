# -*- coding: utf-8 -*-
"""카드 PNG 를 사이트로 — cards/album.json → web/public/cards/<date>/NN.png + index.json.

텔레그램에 보낸 것과 같은 파일을 홈의 '카드뉴스로 보기' 띠가 넘겨 본다(지니 09-16).
web/public/data 와 달리 이 폴더는 **커밋한다**: 크롤 배포가 매시 web/public 을
통째로 올리므로, 커밋에 없는 파일은 다음 배포에서 사라진다.
보관은 KEEP_DAYS 일 — 그 뒤 폴더는 지운다. ponytail: 5장×300KB×14일 ≈ 20MB 상한,
이력이 하루 1.5MB 씩 자라는 건 감수. 더 크면 Pages 외부 저장으로.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date as date_type, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ALBUM_FILE = ROOT / "cards" / "album.json"
SITE_DIR = ROOT / "web" / "public" / "cards"
KEEP_DAYS = 14


STORY_SUFFIX = "-story"


def publish(album: dict, site_dir: Path, album_root: Path, today: date_type | None = None,
            kind: str = "daily") -> dict:
    """앨범 PNG 를 사이트로. kind="story" 면 <date>-story 폴더에 따로 올린다.

    같은 날 두 앨범(일일 5장 + 스토리 5장)이 나가므로 폴더를 갈라야 한다 — 한
    폴더에 섞으면 띠가 10장을 한 덩어리로 세우고 장수 표기도 어긋난다.
    """
    day = str(album["date"])
    files = [album_root / f for f in album.get("files") or []]
    if not files or not all(f.exists() for f in files):
        raise FileNotFoundError("album.json 의 파일이 없다 — 렌더가 안 끝났다")
    out = site_dir / (day + STORY_SUFFIX if kind == "story" else day)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    names = []
    for i, src in enumerate(files, 1):
        name = f"{i:02d}.png"
        shutil.copyfile(src, out / name)
        names.append(name)

    cutoff = (today or date_type.today()) - timedelta(days=KEEP_DAYS)
    for folder in site_dir.iterdir():
        # 스토리 폴더도 같이 지운다 — 날짜 접두사가 같으니 기준도 같다.
        if folder.is_dir() and folder.name[:10] < cutoff.isoformat() and (
                len(folder.name) == 10 or folder.name.endswith(STORY_SUFFIX)):
            shutil.rmtree(folder)

    def scan(story: bool) -> dict:
        out = {}
        for d in site_dir.iterdir():
            if not d.is_dir():
                continue
            if story and not d.name.endswith(STORY_SUFFIX):
                continue
            if not story and len(d.name) != 10:
                continue
            names = sorted(f.name for f in d.glob("*.png"))
            if names:
                out[d.name[:10]] = names
        return dict(sorted(out.items()))

    dates = scan(story=False)
    stories = scan(story=True)
    # 카드 카피의 '왜' 한 줄도 같이 싣는다 — 홈의 먼저 볼 3건이 쓴다(make_cards.card_lines).
    # 옛 index.json 의 다른 날짜 줄은 보존하고, 남아 있는 날짜치만 남긴다.
    lines = dict((json.loads((site_dir / "index.json").read_text(encoding="utf-8")).get("lines") or {})
                 if (site_dir / "index.json").exists() else {})
    if album.get("lines"):
        lines[day] = album["lines"]
    lines = {k: v for k, v in lines.items() if k in dates and v}
    index = {"latest": max(dates) if dates else day,
             "dates": dates, "stories": stories,
             "lines": dict(sorted(lines.items()))}
    (site_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8")
    return index


def record_story(album: dict, path: Path) -> None:
    """실제로 사이트에 나간 스토리 카드만 이력에 적는다(card_context.repeat_verdict 가 읽는다)."""
    import card_context  # noqa: PLC0415 — 일일 게시 경로는 이 모듈이 필요 없다
    history = card_context.record_story_card(
        card_context.load_story_history(path), date=str(album["date"]),
        thread_id=str(album["thread_id"]), issue_id=str(album.get("issue_id") or ""),
        ids=list(album.get("event_ids") or []))
    card_context.save_story_history(history, path)
    print(f"[cards] 스토리 이력: {album['date']} {album['thread_id']} "
          f"사건 {len(album.get('event_ids') or [])}건 (누적 {len(history)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--album", type=Path, default=ALBUM_FILE)
    ap.add_argument("--site-dir", type=Path, default=SITE_DIR)
    ap.add_argument("--kind", choices=("daily", "story"), default="daily")
    args = ap.parse_args()
    if not args.album.exists():
        print("[cards] album.json 없음 — 게시할 카드가 없다. 스킵")
        return 0
    album = json.loads(args.album.read_text(encoding="utf-8"))
    index = publish(album, args.site_dir, args.album.resolve().parents[1], kind=args.kind)
    if args.kind == "story" and album.get("thread_id"):
        record_story(album, args.site_dir / "story_history.json")
    bucket = index["stories"] if args.kind == "story" else index["dates"]
    print(f"[cards] 사이트 게시({args.kind}): {album['date']} "
          f"{len(bucket.get(album['date']) or [])}장 (보관 {len(bucket)}일치) → {args.site_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
