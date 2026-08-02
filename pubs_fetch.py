"""국제기구·연구기관 발간물 수집 → publications.json (웹 '발간물' 탭 재료).

설계 원칙:
  - zero-LLM. 제목·날짜·링크·기관만 수집한다. 요약이 필요하면 사용자가 원문을 연다.
  - 뉴스 파이프라인(news_bot)과 완전 분리 — 아카이브·이슈 클러스터링·트렌드에
    유입되지 않는다. 발간물이 죽어도 뉴스는 무사하고, 그 역도 같다.
  - 소스별 try/except 격리. 한 소스의 HTML 변경이 나머지를 죽이면 안 된다.
  - 하루 1회 crawl.yml hour-gate 에서 돈다. 요청 소스당 1회.

소스 노트 (2026-08-02 실검증):
  - IAEA: /feeds/publications RSS. topnews·pressalerts 는 뉴스 성격이라 제외
    (topnews 는 이미 news_bot RSS_SOURCES 에 있다).
  - EIA: RSS 2종. pubDate 가 "Fri, 31 Jul 2026  09:00:00 EST" — 공백 2칸 +
    비표준 타임존이라 feedparser 날짜 파싱이 깨질 수 있어 정규식 폴백을 둔다.
  - OECD-NEA: RSS 없음. /jcms/p_23/news 서버렌더 HTML 을 정규식으로 읽는다
    (BeautifulSoup 금지 — email_ingest.py 의 regex-over-HTML 선례).
    pl_{ID} 가 단조증가라 최대 ID 상태로 신규를 판별한다.
    <title> 태그는 모든 경로에서 "Home" 이므로 제목은 링크 텍스트에서 뽑는다.
  - IEA: RSS 없음. /analysis?type=report 1페이지 서버렌더.
  - NRC: 데이터센터 IP 전면 403 — v1 제외.
  - KEEI 세계원전시장인사이트: 별도 파서 (keei_* 함수) — 국내 기관이지만
    같은 상태 파일·같은 편성으로 돈다.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

from data_quality import clean_text, normalize_url

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
OUT_FILE = BASE / "publications.json"
KST = timezone(timedelta(hours=9))

USER_AGENT = "nuclens-pubs/1.0 (+https://nuclens.pages.dev)"
TIMEOUT = 30
KEEP_DAYS = 180
MAX_ITEMS = 400

# EIA·IEA 는 에너지 전반을 다루므로 원자력 관련만 통과시킨다.
# IAEA·NEA·KEEI 인사이트는 기관 자체가 원자력이라 게이트 불필요.
NUCLEAR_KEYWORDS = (
    "nuclear", "uranium", "reactor", "smr", "fission", "fusion",
    "radioisotope", "radioactive", "atomic", "enrichment", "fuel cycle",
    "원전", "원자력",
)

_TAG_RE = re.compile(r"<[^>]+>")
# href 가 "jcms/pl_..." (선행 슬래시 없음)로 나오는 것을 실측 확인 — 둘 다 허용
_NEA_LINK_RE = re.compile(
    r'href="(?:https?://www\.oecd-nea\.org)?/?(jcms/pl_(\d+)/[^"#?]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
NEA_BOOTSTRAP_LIMIT = 10  # 첫 실행: 최신 ID 상위 N건만 (낮은 ID는 상시 내비 링크)
# 뉴스가 아닌 상시 페이지 링크 — 높은 pl_ID 를 달고도 등장한다 (실측 Accessibility)
_NEA_GENERIC_TITLES = {
    "accessibility", "contact", "contact us", "sitemap", "home", "news",
    "publications", "legal notice", "terms and conditions",
}
# 같은 기사로 향하는 버튼 앵커 — 제목 후보에서 제외 (실측 READ MORE / PREVIEW)
_NEA_BUTTON_TEXTS = {
    "read more", "preview", "learn more", "more", "download", "view", "details",
}
_IEA_LINK_RE = re.compile(
    r'href="(?:https?://www\.iea\.org)?(/reports/[^"#?]+)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_EIA_DATE_RE = re.compile(r"(\d{1,2})\s+(\w{3})\w*\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}


def _strip_tags(html: str) -> str:
    return clean_text(_TAG_RE.sub(" ", html or ""))


def _dedouble(text: str) -> str:
    """앵커 안에 제목이 두 번 들어간 카드('제목 제목') 실측 보정."""
    half, rest = text[: len(text) // 2].strip(), text[len(text) // 2:].strip()
    return half if half and half == rest else text


def _http_get(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def _item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _entry_date(entry) -> str:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return f"{parsed.tm_year:04d}-{parsed.tm_mon:02d}-{parsed.tm_mday:02d}"
    # EIA 폴백: "Fri, 31 Jul 2026  09:00:00 EST" (공백 2칸 + 비표준 TZ)
    raw = str(entry.get("published") or entry.get("updated") or "")
    match = _EIA_DATE_RE.search(raw)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name[:3].title())
        if month:
            return f"{int(year):04d}-{month:02d}-{int(day):02d}"
    return ""


def _passes_keyword_gate(title: str) -> bool:
    lowered = title.lower()
    return any(keyword in lowered for keyword in NUCLEAR_KEYWORDS)


def _make_item(org: str, org_kr: str, kind: str, title: str, url: str,
               date: str, **extra) -> dict | None:
    canonical = normalize_url(url)
    title = clean_text(title)
    if not canonical or not title:
        return None
    item = {
        "id": _item_id(canonical),
        "org": org,
        "org_kr": org_kr,
        "kind": kind,
        "title": title,
        "url": canonical,
        "date": date,
        "fetched_at": datetime.now(KST).strftime("%Y-%m-%d"),
    }
    item.update({k: v for k, v in extra.items() if v})
    return item


# ── 소스별 파서 ──────────────────────────────────────────────────────


def fetch_rss(url: str, org: str, org_kr: str, kind: str,
              keyword_gate: bool = False) -> list[dict]:
    feed = feedparser.parse(_http_get(url))
    items = []
    for entry in feed.entries[:40]:
        title = clean_text(entry.get("title"))
        if keyword_gate and not _passes_keyword_gate(title):
            continue
        item = _make_item(org, org_kr, kind, title, entry.get("link") or "",
                          _entry_date(entry))
        if item:
            items.append(item)
    return items


def fetch_nea(state: dict) -> list[dict]:
    """OECD-NEA 뉴스·발간물 — pl_{ID} 단조증가로 신규 판별.

    페이지에는 상시 내비게이션 링크(낮은 pl_ID)가 섞여 있다. 첫 실행은 최신 ID
    상위 N건만 취하고, 이후에는 max_seen 초과분만 취해 자연히 걸러진다.
    같은 pl_ID 가 이미지 링크(텍스트 없음)·버튼(READ MORE)·제목으로 여러 번
    등장하므로, 버튼 문구를 걸러낸 뒤 남은 후보 중 최단 텍스트를 제목으로 쓴다
    (긴 쪽은 카드 전체 텍스트가 딸려온 앵커).
    """
    html = _http_get("https://www.oecd-nea.org/jcms/p_23/news")
    max_seen = int(state.get("nea_max_id") or 0)
    candidates: dict[int, dict] = {}  # pl_id → {"path", "texts": [..]}
    for path, raw_id, link_html in _NEA_LINK_RE.findall(html):
        pl_id = int(raw_id)
        entry = candidates.setdefault(pl_id, {"path": path, "texts": []})
        text = _dedouble(_strip_tags(link_html))
        if text and text.lower() not in _NEA_BUTTON_TEXTS:
            entry["texts"].append(text)
    titles: dict[int, tuple[str, str]] = {}  # pl_id → (title, path)
    for pl_id, entry in candidates.items():
        if entry["texts"]:
            # 실 제목 후보 중 최단 — 긴 쪽은 카드 전체 텍스트가 딸려온 앵커다
            title = min(entry["texts"], key=len)
        else:  # 이미지·버튼 링크뿐이면 슬러그로 대체
            slug = entry["path"].rstrip("/").rsplit("/", 1)[-1]
            title = slug.replace("-", " ").strip().capitalize()
        if title.lower() in _NEA_GENERIC_TITLES:
            continue
        titles[pl_id] = (title, entry["path"])
    if candidates:
        # max 는 generic 필터 이전의 전체 후보 기준 — 필터된 ID가 다음 실행에서
        # 영원히 '신규'로 재등장하는 것을 막는다
        state["nea_max_id"] = max(max(candidates), max_seen)
    if not titles:
        return []
    if max_seen:
        fresh_ids = [pl_id for pl_id in titles if pl_id > max_seen]
    else:
        fresh_ids = sorted(titles, reverse=True)[:NEA_BOOTSTRAP_LIMIT]
    items = []
    for pl_id in sorted(fresh_ids, reverse=True):
        title, path = titles[pl_id]
        item = _make_item("OECD-NEA", "OECD 원자력기구", "news_or_report",
                          title, f"https://www.oecd-nea.org/{path}", "")
        if item:
            items.append(item)
    return items


def fetch_iea() -> list[dict]:
    html = _http_get("https://www.iea.org/analysis?type=report")
    items, seen_paths = [], set()
    for path, link_html in _IEA_LINK_RE.findall(html):
        if path in seen_paths:
            continue
        seen_paths.add(path)
        title = _strip_tags(link_html)
        if not title or not _passes_keyword_gate(title):
            continue
        item = _make_item("IEA", "국제에너지기구", "report",
                          title, f"https://www.iea.org{path}", "")
        if item:
            items.append(item)
    return items


SOURCES = [
    {"id": "iaea_publications",
     "fetch": lambda state: fetch_rss(
         "https://www.iaea.org/feeds/publications",
         "IAEA", "국제원자력기구", "publication")},
    {"id": "eia_today",
     "fetch": lambda state: fetch_rss(
         "https://www.eia.gov/rss/todayinenergy.xml",
         "EIA", "미국 에너지정보청", "analysis", keyword_gate=True)},
    {"id": "eia_press",
     "fetch": lambda state: fetch_rss(
         "https://www.eia.gov/rss/press_rss.xml",
         "EIA", "미국 에너지정보청", "press", keyword_gate=True)},
    {"id": "nea_news", "fetch": fetch_nea},
    {"id": "iea_reports", "fetch": lambda state: fetch_iea()},
]


# ── 상태 파일 ────────────────────────────────────────────────────────


def load_store() -> dict:
    try:
        raw = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {
                "items": [i for i in raw.get("items") or [] if isinstance(i, dict)],
                "state": raw.get("state") if isinstance(raw.get("state"), dict) else {},
                "last_checked": raw.get("last_checked")
                if isinstance(raw.get("last_checked"), dict) else {},
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"items": [], "state": {}, "last_checked": {}}


def save_store(store: dict) -> None:
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(OUT_FILE)


def prune(items: list[dict]) -> list[dict]:
    cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    kept = [item for item in items
            if (item.get("date") or item.get("fetched_at") or "") >= cutoff]
    kept.sort(key=lambda item: (item.get("date") or item.get("fetched_at") or "",
                                item.get("id") or ""), reverse=True)
    return kept[:MAX_ITEMS]


def run(sources: list[dict] | None = None) -> bool:
    store = load_store()
    seen_urls = {item.get("url") for item in store["items"]}
    now = datetime.now(KST).isoformat(timespec="seconds")
    total_new = 0
    for source in (sources if sources is not None else SOURCES):
        source_id = source["id"]
        try:
            fetched = source["fetch"](store["state"])
        except Exception as exc:  # 소스 격리 — 어떤 예외든 나머지는 계속
            store["last_checked"][source_id] = {
                "at": now, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
            }
            print(f"[pubs] {source_id} 실패 — 격리: {type(exc).__name__}: {exc}")
            continue
        new_items = [item for item in fetched if item["url"] not in seen_urls]
        for item in new_items:
            seen_urls.add(item["url"])
        store["items"].extend(new_items)
        store["last_checked"][source_id] = {
            "at": now, "ok": True, "new": len(new_items),
        }
        print(f"[pubs] {source_id}: 수집 {len(fetched)}건 중 신규 {len(new_items)}건")
        total_new += len(new_items)
    store["items"] = prune(store["items"])
    save_store(store)
    print(f"[pubs] 신규 {total_new}건, 보관 {len(store['items'])}건 → {OUT_FILE.name}")
    return True


if __name__ == "__main__":
    run()
