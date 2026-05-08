import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

KST = timezone(timedelta(hours=9))
DIGEST_QUEUE_FILE = Path("digest_queue.json")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
TG_LIMIT = 3800


def load_queue() -> list:
    if DIGEST_QUEUE_FILE.exists():
        try:
            return json.loads(DIGEST_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_queue(queue: list) -> None:
    DIGEST_QUEUE_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def send_telegram(text: str) -> None:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
    if not r.ok:
        print(f"  ! Telegram error: {r.status_code} {r.text}")
    time.sleep(1)


def send_long(text: str) -> None:
    if len(text) <= TG_LIMIT:
        send_telegram(text)
        return
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TG_LIMIT:
            if current:
                chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current)
    for chunk in chunks:
        send_telegram(chunk)


def format_digest(queue: list) -> str:
    today = datetime.now(KST)
    nice_items = [a for a in queue if a.get("category") != "market"]
    market_items = [a for a in queue if a.get("category") == "market"]

    header = f"☀️ <b>{today.month}/{today.day} 원자력 일일 브리핑</b> ({len(nice_items)}건)"
    if market_items:
        header += f" + 시장·주식 {len(market_items)}건"

    parts = [header, ""]

    by_feed: dict[str, list] = {}
    for art in nice_items:
        by_feed.setdefault(art.get("feed", "기타"), []).append(art)

    feed_order = ["정책", "SMR"]
    feeds_sorted = [f for f in feed_order if f in by_feed] + [
        f for f in by_feed if f not in feed_order
    ]

    for feed in feeds_sorted:
        items = by_feed[feed]
        parts.append(f"━━ {feed} ━━")
        for i, art in enumerate(items, 1):
            title = html.escape(art.get("title", ""))
            summary = html.escape(art.get("summary", ""))
            tags = " ".join(html.escape(t) for t in art.get("tags", []))
            domain = html.escape(art.get("domain", ""))
            parts.append(f"{i}. <b>{title}</b>")
            if summary:
                parts.append(f"   → {summary}")
            meta_bits = []
            if tags:
                meta_bits.append(tags)
            if domain:
                meta_bits.append(f"<i>{domain}</i>")
            if meta_bits:
                parts.append(f"   {' · '.join(meta_bits)}")
            parts.append(f"   🔗 {art.get('link', '')}")
            parts.append("")
        parts.append("")

    if market_items:
        parts.append("📈 <b>시장·주식</b> (참고용)")
        for art in market_items:
            title = html.escape(art.get("title", ""))
            domain = html.escape(art.get("domain", ""))
            parts.append(f"• {title} <i>({domain})</i>")
            parts.append(f"  {art.get('link', '')}")
        parts.append("")

    return "\n".join(parts).strip()


def main() -> None:
    queue = load_queue()
    if not queue:
        print("Queue is empty. Skipping digest.")
        return

    print(f"Digest: {len(queue)} items")
    message = format_digest(queue)
    send_long(message)
    save_queue([])
    print("Digest sent and queue cleared.")


if __name__ == "__main__":
    main()
