import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

KST = timezone(timedelta(hours=9))
LOOKBACK_HOURS = 6
DEDUP_RETENTION_DAYS = 14
STATE_FILE = Path("sent.json")
KEYWORDS_FILE = Path("keywords.json")

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"sent": {}}


def save_state(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    state["sent"] = {h: ts for h, ts in state["sent"].items() if ts > cutoff}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def search_naver(query: str, display: int = 30) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": "date"}
    r = requests.get(NAVER_URL, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("items", [])


def send_telegram(text: str) -> None:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
    if not r.ok:
        print(f"  ! Telegram error: {r.status_code} {r.text}")
    time.sleep(1)


def collect_articles(feed_name: str, keywords: list[str], state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    seen_in_run: set[str] = set()
    fresh: list[dict] = []

    for kw in keywords:
        try:
            items = search_naver(kw)
        except Exception as e:
            print(f"  ! [{feed_name}] '{kw}' search failed: {e}")
            continue

        for item in items:
            link = item.get("originallink") or item.get("link")
            if not link:
                continue
            h = url_hash(link)
            if h in state["sent"] or h in seen_in_run:
                continue

            try:
                pub = parsedate_to_datetime(item["pubDate"])
            except Exception:
                continue
            if pub < cutoff:
                continue

            seen_in_run.add(h)
            fresh.append({
                "hash": h,
                "title": strip_html(item.get("title", "")),
                "link": link,
                "pub": pub,
                "matched": kw,
            })
        time.sleep(0.1)

    fresh.sort(key=lambda x: x["pub"])
    return fresh


def format_message(feed_name: str, article: dict) -> str:
    title = html.escape(article["title"])
    link = article["link"]
    pub_local = article["pub"].astimezone(KST).strftime("%m/%d %H:%M")
    return (
        f"<b>[{feed_name}]</b> {title}\n"
        f"<i>{pub_local} · {article['matched']}</i>\n"
        f"{link}"
    )


def main() -> None:
    keywords = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    total_sent = 0

    for feed_name, kw_list in keywords.items():
        print(f"[{feed_name}] {len(kw_list)} keywords")
        articles = collect_articles(feed_name, kw_list, state)
        print(f"[{feed_name}] {len(articles)} new articles")

        for article in articles:
            send_telegram(format_message(feed_name, article))
            state["sent"][article["hash"]] = datetime.now(timezone.utc).isoformat()
            total_sent += 1

    save_state(state)
    print(f"Done. Sent {total_sent} messages.")


if __name__ == "__main__":
    main()
