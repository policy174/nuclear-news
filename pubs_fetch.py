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
    """IEA 보고서 목록 1페이지. 같은 경로가 카드·제목 앵커로 여러 번 나온다.

    카드 앵커에는 제목 뒤에 부속물이 딸려 온다(실측: "World Energy Outlook 2025
    Read more Flagship report — 12 November 2025"). NEA 와 같은 원칙으로 경로당
    최단 텍스트를 제목으로 쓴다.
    """
    html = _http_get("https://www.iea.org/analysis?type=report")
    by_path: dict[str, str] = {}
    for path, link_html in _IEA_LINK_RE.findall(html):
        text = _dedouble(_strip_tags(link_html))
        if not text or text.lower() in _NEA_BUTTON_TEXTS:
            continue
        if path not in by_path or len(text) < len(by_path[path]):
            by_path[path] = text
    items = []
    for path, title in by_path.items():
        if not _passes_keyword_gate(title):
            continue
        item = _make_item("IEA", "국제에너지기구", "report",
                          title, f"https://www.iea.org{path}", "")
        if item:
            items.append(item)
    return items


KEEI_LIST_URL = ("https://www.keei.re.kr/board.es"
                 "?mid=a10102050000&bid=0002&cg_code=C04")
KEEI_VIEW_URL = ("https://www.keei.re.kr/board.es"
                 "?mid=a10102050000&bid=0002&act=view&list_no={no}")
KEEI_PDF_URL = "https://www.keei.re.kr/boardDownload.es?bid=0002&list_no={no}&seq=1"
KEEI_BOOTSTRAP_LIMIT = 3   # 첫 실행에 최근 몇 호까지 가져올지
KEEI_MAX_DETAIL = 4        # 한 번에 상세(목차) 요청할 최대 호수

# 제목 안의 발행일 — 표기가 흔들린다: (2026.07.24.) / (2026.05.15) / (2025.6.20)
_KEEI_DATE_RE = re.compile(r"\((\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\)")
_KEEI_ROW_RE = re.compile(r"list_no=(\d+)[^>]*>(.{0,300}?)</a>", re.DOTALL)
_KEEI_TITLE_HINT = "세계 원전시장 인사이트"
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_KEEI_SECTION_RE = re.compile(r"^□\s*(현안이슈|주요단신)")
_KEEI_BULLET_RE = re.compile(r"^[•·]\s*(.+)")


def _keei_date(title: str) -> str:
    match = _KEEI_DATE_RE.search(title)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def keei_parse_toc(html: str) -> dict:
    """상세 페이지에서 목차만 뽑는다 — 제목 줄만, 본문 저장 금지(저작권).

    구조: `□ 현안이슈` → `• 심층 주제` / `□ 주요단신` → `• 국가별 항목`.
    현안이슈 아래의 `1. 들어가며` 같은 소절 번호는 버린다(원문 목차 재현이
    아니라 이슈 매칭용 신호만 필요하다).
    """
    lines = []
    for para in _PARA_RE.findall(html):
        text = clean_text(_TAG_RE.sub("", para)).replace("\xa0", " ").strip()
        if text:
            lines.append(text)
    toc: dict = {"issue_title": "", "briefs": []}
    section = ""
    for line in lines:
        heading = _KEEI_SECTION_RE.match(line.replace(" ", "", 1) if line.startswith("□") else line)
        if heading:
            section = heading.group(1)
            continue
        bullet = _KEEI_BULLET_RE.match(line)
        if not bullet or not section:
            continue
        text = clean_text(bullet.group(1))
        if not text or text.startswith("기타 단신"):
            continue
        if section == "현안이슈":
            if not toc["issue_title"]:
                toc["issue_title"] = text
        elif len(toc["briefs"]) < 30:
            toc["briefs"].append(text)
    return toc


def fetch_keei(state: dict) -> list[dict]:
    """에너지경제연구원 세계 원전시장 인사이트 — 격주간, list_no 로 신규 판별.

    list_no 는 게시판 전체 공용 단조증가 시퀀스라 "저장된 최대값 초과 = 신규"가
    항상 성립한다. 발행이 21일 벌어지는 경우가 있어 고정 주기 스케줄 대신
    상태 비교로 감지한다.
    """
    html = _http_get(KEEI_LIST_URL)
    max_seen = int(state.get("keei_max_list_no") or 0)
    rows: dict[int, str] = {}
    for raw_no, link_html in _KEEI_ROW_RE.findall(html):
        title = clean_text(_TAG_RE.sub(" ", link_html))
        if _KEEI_TITLE_HINT not in title:
            continue
        list_no = int(raw_no)
        if list_no not in rows or len(title) > len(rows[list_no]):
            rows[list_no] = title
    if not rows:
        return []
    if max_seen:
        fresh = sorted((no for no in rows if no > max_seen), reverse=True)
    else:
        fresh = sorted(rows, reverse=True)[:KEEI_BOOTSTRAP_LIMIT]
    # 지난 실행에서 상세 상한에 걸려 목차를 못 채운 호를 먼저 처리한다.
    # 워터마크는 최댓값으로 오르므로 여기서 챙기지 않으면 그 호들은 다시는
    # '신규'가 아니게 되어 목차를 영영 못 얻고, 목차가 없으면 keei_entries()
    # 가 건너뛰어 이슈 매칭에도 들어가지 못한다.
    pending = [no for no in (state.get("keei_pending_toc") or []) if isinstance(no, int)]
    fresh = sorted(set(fresh) | set(pending), reverse=True)
    items, still_pending = [], []
    for index, list_no in enumerate(fresh):
        title = rows.get(list_no)
        if not title:
            # 목록 1페이지에서 밀려난 호 — 10건이면 격주간 기준 약 5개월치라
            # 여기 걸리면 그만큼 오래 멈춰 있었다는 뜻. 제목을 못 얻으므로 포기한다.
            continue
        toc = {}
        # 상세(목차)는 호마다 요청이 하나씩 더 붙으므로 최신 몇 호만 가져온다.
        # 다만 **항목 자체는 전부 내보낸다** — 여기서 자르면 워터마크는 최댓값으로
        # 올라가는데 잘린 호는 다음 실행에서 '신규'가 아니라서 영구 유실된다
        # (실측: 6호가 한꺼번에 올라온 상황에서 2호가 사라졌다).
        if index < KEEI_MAX_DETAIL:
            try:
                toc = keei_parse_toc(_http_get(KEEI_VIEW_URL.format(no=list_no)))
            except Exception as exc:  # 목차 실패는 항목 자체를 버릴 이유가 아니다
                print(f"[pubs] keei 목차 추출 실패(list_no={list_no}): {type(exc).__name__}")
        has_toc = bool(toc.get("issue_title") or toc.get("briefs"))
        if not has_toc:
            still_pending.append(list_no)
        item = _make_item(
            "KEEI", "에경연", "keei_insight", title,
            KEEI_VIEW_URL.format(no=list_no), _keei_date(title),
            pdf_url=KEEI_PDF_URL.format(no=list_no),
            toc=toc if has_toc else None,
        )
        if item:
            items.append(item)
    state["keei_max_list_no"] = max(max(rows), max_seen)
    state["keei_pending_toc"] = still_pending[:KEEI_BOOTSTRAP_LIMIT * 4]
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
    {"id": "keei_insight", "fetch": fetch_keei},
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


def collected_today(store: dict, today: str | None = None) -> bool:
    """오늘 이미 성공적으로 수집했는지.

    워크플로에서 `date -u +%H` 로 시간을 재면 cron 지연에 그날 수집이 통째로
    빠진다(GitHub cron 은 상시 밀린다. 20:00 스케줄이 21:0x 에 이 스텝에
    도달하면 스킵되고, 21:00 실행도 스킵). IAEA·EIA 는 RSS 최신 40건만
    노출하고 워터마크가 없어서 며칠 밀리면 그 사이 발간물이 영구 유실된다.
    그래서 시각이 아니라 '오늘 했는가'로 판단한다.
    """
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    for entry in (store.get("last_checked") or {}).values():
        if not isinstance(entry, dict) or not entry.get("ok"):
            continue
        if str(entry.get("at") or "").startswith(today):
            return True
    return False


def run(sources: list[dict] | None = None, *, once_per_day: bool = False) -> bool:
    store = load_store()
    if once_per_day and collected_today(store):
        print("[pubs] 오늘 이미 수집함 — 스킵")
        return False
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
        by_url = {item.get("url"): item for item in store["items"]}
        new_items, enriched = [], 0
        for item in fetched:
            existing = by_url.get(item["url"])
            if existing is None:
                new_items.append(item)
                seen_urls.add(item["url"])
                continue
            # 이미 있는 항목이라도 이번에 새로 얻은 필드(주로 목차)는 채워 준다.
            # URL 만 보고 통째로 버리면 나중에 붙인 목차가 영영 반영되지 않는다.
            for key in ("toc", "pdf_url", "date"):
                if item.get(key) and not existing.get(key):
                    existing[key] = item[key]
                    enriched += 1
        store["items"].extend(new_items)
        store["last_checked"][source_id] = {
            "at": now, "ok": True, "new": len(new_items),
            # "0건 신규"와 "파서가 죽어 아무것도 못 읽음"을 구분하는 신호.
            # regex-over-HTML 소스는 사이트 개편 한 번에 조용히 죽는다.
            "parsed": len(fetched),
        }
        print(f"[pubs] {source_id}: 수집 {len(fetched)}건 중 신규 {len(new_items)}건"
              f"{f', 보강 {enriched}건' if enriched else ''}")
        total_new += len(new_items)
    store["items"] = prune(store["items"])
    # 영문 제목만으로는 무슨 문서인지 알 수 없다는 피드백(2026-08-02) → 한국어
    # 제목·한 줄 설명을 붙인다. 신규분만 대상이고, 실패하면 원문 제목으로 뜬다.
    try:
        import pubs_translate
        result = pubs_translate.translate(store["items"])
        print(f"[pubs] 한국어 해석: 대상 {result['candidates']}건 / "
              f"번역 {result['translated']}건 / 호출 {result['calls']}회 [{result['status']}]")
    except Exception as exc:
        print(f"[pubs] 한국어 해석 스킵 — {type(exc).__name__}: {exc}")
    save_store(store)
    print(f"[pubs] 신규 {total_new}건, 보관 {len(store['items'])}건 → {OUT_FILE.name}")
    return True


if __name__ == "__main__":
    run(once_per_day="--once-per-day" in sys.argv)
