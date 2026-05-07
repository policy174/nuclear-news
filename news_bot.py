import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

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

DOMAIN_SCORE = {
    "hani.co.kr": 9, "chosun.com": 9, "joongang.co.kr": 9,
    "donga.com": 9, "khan.co.kr": 9, "hankookilbo.com": 9,
    "kmib.co.kr": 9, "munhwa.com": 9, "seoul.co.kr": 9,
    "mk.co.kr": 8, "hankyung.com": 8, "etnews.com": 8,
    "sedaily.com": 8, "fnnews.com": 8, "edaily.co.kr": 7,
    "mt.co.kr": 7, "asiae.co.kr": 7, "businesspost.co.kr": 7,
    "electimes.com": 9, "ekn.kr": 9, "energy-news.co.kr": 8,
    "epj.co.kr": 8, "energytimes.kr": 8, "energydaily.co.kr": 7,
    "yna.co.kr": 8, "newsis.com": 7, "news1.kr": 7, "yonhapnewstv.co.kr": 7,
    "kbs.co.kr": 7, "imbc.com": 7, "sbs.co.kr": 7, "ytn.co.kr": 7,
    "jtbc.co.kr": 7, "tvchosun.com": 6, "ichannela.com": 6, "mbn.co.kr": 6,
    "newspim.com": 5, "ajunews.com": 5,
}
DEFAULT_SCORE = 4
MIN_SCORE = 4

ANTI_TITLE_PATTERNS = [
    re.compile(r"\[(보도자료|알림|공지|기업\s*소식|새소식|광고|포토|화보|부고)\]"),
]
ANTI_KEYWORDS = [
    "관련주", "테마주", "원전주", "원전株", "원자력주", "수혜주",
    "급등", "급락", "장 마감", "장 시작", "코스피", "코스닥",
    "상한가", "하한가", "장중", "낙폭",
]

KR_SLD = (".co.kr", ".or.kr", ".go.kr", ".ne.kr", ".re.kr", ".ac.kr")


def get_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if not host:
        return ""
    if host.endswith(KR_SLD):
        return ".".join(host.split(".")[-3:])
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_score(url: str) -> int:
    return DOMAIN_SCORE.get(get_domain(url), DEFAULT_SCORE)


def is_promotional(title: str, description: str) -> bool:
    if any(p.search(title) for p in ANTI_TITLE_PATTERNS):
        return True
    text = title + " " + description
    return any(kw in text for kw in ANTI_KEYWORDS)


def normalize_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]|\([^)]+\)", "", title)
    title = re.sub(r"[^\w가-힣]", "", title)
    return title.lower()


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


def passes_anchor_filter(title: str, description: str, anchors: list[str]) -> bool:
    if not anchors:
        return True
    haystack = (title + " " + description).lower()
    return any(a.lower() in haystack for a in anchors)


def collect_articles(feed_name: str, keywords: list[str], anchors: list[str], state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    by_title: dict[str, dict] = {}

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

            try:
                pub = parsedate_to_datetime(item["pubDate"])
            except Exception:
                continue
            if pub < cutoff:
                continue

            h = url_hash(link)
            if h in state["sent"]:
                continue

            title = strip_html(item.get("title", ""))
            desc = strip_html(item.get("description", ""))

            if is_promotional(title, desc):
                continue
            if not passes_anchor_filter(title, desc, anchors):
                continue

            score = domain_score(link)
            if score < MIN_SCORE:
                continue

            norm = normalize_title(title)
            if not norm:
                continue

            existing = by_title.get(norm)
            if existing and existing["score"] >= score:
                continue

            by_title[norm] = {
                "hash": h,
                "title": title,
                "link": link,
                "pub": pub,
                "matched": kw,
                "score": score,
                "domain": get_domain(link),
            }
        time.sleep(0.1)

    return sorted(by_title.values(), key=lambda x: x["pub"])


def format_message(feed_name: str, article: dict) -> str:
    title = html.escape(article["title"])
    return f"<b>[{feed_name}]</b> {title}\n{article['link']}"


def main() -> None:
    config = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    total_sent = 0

    for feed_name, feed_cfg in config.items():
        kw_list = feed_cfg["keywords"]
        anchors = feed_cfg.get("anchors", [])
        print(f"[{feed_name}] {len(kw_list)} keywords, {len(anchors)} anchors")
        articles = collect_articles(feed_name, kw_list, anchors, state)
        print(f"[{feed_name}] {len(articles)} new articles after filtering")

        for article in articles:
            send_telegram(format_message(feed_name, article))
            state["sent"][article["hash"]] = datetime.now(timezone.utc).isoformat()
            total_sent += 1

    save_state(state)
    print(f"Done. Sent {total_sent} messages.")


if __name__ == "__main__":
    main()
