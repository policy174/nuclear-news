#!/usr/bin/env python3
"""카드 앨범 발송 — cards/album.json → 텔레그램 sendMediaGroup → outbox 기록.

    E  sendMediaGroup (앨범 1건, 캡션은 첫 장에만)
    F  텔레그램이 Message 배열을 돌려준 뒤에**만** outbox 에 성공 기록

telegram_send.py 는 import 시점에 토큰이 없으면 sys.exit 하므로(모듈 상단 가드)
여기서는 audio_brief.py 와 같은 방식으로 API 를 직접 부른다.

    python send_album.py
    python send_album.py --force   # 오늘 이미 보냈어도 다시 보낸다
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gemini_client

ROOT = Path(__file__).parent
ALBUM_FILE = ROOT / "cards" / "album.json"
OUTBOX_FILE = ROOT / "outbox.json"
KST = timezone(timedelta(hours=9))

# sendMediaGroup 은 항목 2~10개를 요구한다. 현재 구조는 최소 3장(hook+step+cta)
# 이라 항상 통과하지만, 단일 카드로 바꾸면 여기서 깨진다.
MIN_ITEMS, MAX_ITEMS = 2, 10


def send_media_group(files: list[Path], caption: str) -> list[dict]:
    """앨범 발송. 성공 시 Message 배열을 반환, 실패 시 예외."""
    import requests

    token = gemini_client._resolve("TELEGRAM_BOT_TOKEN")
    chat_id = gemini_client._resolve("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정")

    media, payload = [], {}
    for i, path in enumerate(files):
        key = f"file{i}"
        entry = {"type": "photo", "media": f"attach://{key}"}
        if i == 0:
            entry["caption"] = caption
            entry["parse_mode"] = "HTML"
        media.append(entry)
        payload[key] = (path.name, path.read_bytes(), "image/png")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMediaGroup",
        data={"chat_id": chat_id,
              "media": json.dumps(media, ensure_ascii=False)},
        files=payload,
        timeout=180,
    )
    body = response.json()
    result = body.get("result")
    if not (response.ok and body.get("ok") and isinstance(result, list) and result):
        raise RuntimeError(f"Telegram HTTP {response.status_code}: {str(body)[:300]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    # 스토리 카드뉴스(story_cards.py)도 같은 발송기를 쓴다. 앨범 파일과 중복 방지
    # 키만 갈라준다 — 발송 로직·검증이 둘로 갈리면 한쪽만 고쳐지는 날이 온다.
    ap.add_argument("--album", type=Path, default=ALBUM_FILE)
    ap.add_argument("--key", default="cards", help="outbox 중복 방지 키")
    args = ap.parse_args()

    album_file = args.album if args.album.is_absolute() else ROOT / args.album
    if not album_file.exists():
        print(f"[album] {album_file.name} 없음 — 생성기가 스킵했거나 실패했다")
        return 0
    album = json.loads(album_file.read_text(encoding="utf-8"))
    date = album["date"]
    files = [ROOT / f for f in album["files"]]

    outbox = json.loads(OUTBOX_FILE.read_text(encoding="utf-8")) if OUTBOX_FILE.exists() else {}
    if not args.force and (outbox.get(args.key) or {}).get("date") == date:
        print(f"[album] {date} {args.key} 는 이미 발송됨 — 스킵")
        return 0
    if outbox.get("date") != date:
        print(f"[album] album({date}) 과 outbox({outbox.get('date')}) 날짜 불일치 — 발송 중단")
        return 1

    missing = [f.name for f in files if not f.exists()]
    if missing:
        print(f"[album] PNG 누락 {missing} — 발송 중단")
        return 1
    if not MIN_ITEMS <= len(files) <= MAX_ITEMS:
        print(f"[album] 앨범 장수 {len(files)} 는 텔레그램 허용(2~10) 밖 — 발송 중단")
        return 1

    result = send_media_group(files, album["caption"])

    # F — Message 배열을 받은 뒤에만 기록한다. 기록이 곧 중복 방지 키다.
    outbox[args.key] = {
        "date": date,
        "sent_at": datetime.now(KST).isoformat(),
        "count": len(files),
        "message_ids": [m.get("message_id") for m in result],
    }
    OUTBOX_FILE.write_text(json.dumps(outbox, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[album] {len(files)}장 발송 완료 (message_id {outbox[args.key]['message_ids']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
