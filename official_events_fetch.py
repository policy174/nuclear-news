#!/usr/bin/env python3
"""공식 일정 수집 — 기관이 스스로 공지한 행사·마감을 official_events.json 에 쌓는다.

기사 문장에서 캐는 clause 경로(web/event_calendar.py)와 달리, 여기는 주최가
직접 적은 일정이라 시간·주최·장소가 붙는다. 소스 4개:

  assembly_events  국회도서관 AMPOS 세미나일정 (ampos.nanet.go.kr:7443)
                   — 유일하게 time/host/place 가 다 나온다. 전 분야 세미나가
                   실리므로 키워드 게이트 필수. per-event 퍼머링크가 없어
                   인용 URL 은 목록 페이지다(실측: 상세 링크 자체가 없음).
  kaif_calendar    원자력산업협회 행사 캘린더 (kaif.or.kr ?c=240)
                   — 국제회의·다일 range. 외부 행사 홈페이지가 있으면 그것을
                   url 로 쓴다(협회 페이지는 목록일 뿐이라 근거로 약하다).
  kaif_notice      같은 CMS 공지 (?c=193) — 제목 꼬리 "(~ 9. 10.)" 이 마감.
  kns_notice       원자력학회 공지 (kns.org) — 제목 괄호 "(9.9(수) 14:00,
                   대한상공회의소)" 에서 날짜·시간·장소를 읽는다.

pubs_fetch.py 의 관례를 그대로 쓴다: 소스별 try/except 격리(한 소스가 죽어도
나머지·기존 항목 유지), last_checked 에 parsed/new 기록("0건 신규"와 "파서
사망"을 구분), .json.tmp 원자 쓰기, --once-per-day 게이트.

실측 함정 (2026-09-06):
- kaif 는 `/ko?c=240` 을 302 로 `ko?c=240/` 에 보내 자체 문자 필터에 걸린다.
  **`/ko/?c=240` (슬래시 포함) 만 200.** 행 데이터는 ax.204.php POST 이고
  Referer 없으면 "허용하지 않는 도메인" 거절.
- AMPOS 는 GET + fromDate=endDate 범위로 일정이 나오고 10건/페이지 페이지네이션.
  curMonth 는 무시되고 오늘 날짜로 떨어진다(그 함정 때문에 범위 질의로 간다).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import urllib3

from data_quality import clean_text

# AMPOS(:7443)만 사설 체인이라 verify=False 로 받는다(_get 참조).
# 그 한 호스트 때문에 실행마다 경고가 쌓이는 것을 막는다.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
OUT_FILE = BASE / "official_events.json"
KST = timezone(timedelta(hours=9))

USER_AGENT = "nuclens-events/1.0 (+https://nuclens.pages.dev)"
TIMEOUT = 30
KEEP_GRACE_DAYS = 7        # 끝난 지 7일 넘은 일정은 스토어에서 내린다
WINDOW_DAYS = 30           # 수집 창 — 화면 창(build 의 [today,+30d])과 같은 폭
AMPOS_MAX_PAGES = 10       # 10건/페이지 실측, 30일 창 72건 → 8페이지. 폭주 방어.

# AMPOS 는 전 분야 국회 세미나가 다 실린다(실측 9/6: 경찰개혁·한부모 등).
# 에너지·원자력 어휘가 제목에 없으면 우리 달력의 일정이 아니다.
ASSEMBLY_KEYWORDS = (
    "원자력", "원전", "에너지", "전력", "전기요금", "SMR", "smr",
    "방사성", "방폐", "핵연료", "핵융합", "우라늄", "송전", "전력망",
    "재생에너지", "탄소중립", "기후에너지", "발전소", "계속운전",
)

_TAG_RE = re.compile(r"<[^>]+>")

# "2026년 09월 06일 (일) 14:00" — 시간은 없을 수 있다.
AMPOS_DATE_RE = re.compile(
    r"(?P<y>\d{4})년\s*(?P<m>\d{1,2})월\s*(?P<d>\d{1,2})일"
    r"(?:\s*\([월화수목금토일]\))?\s*(?P<time>\d{1,2}:\d{2})?")
AMPOS_TITLE_RE = re.compile(r'<p\s+style\s*=\s*"[^"]*">\s*(.*?)\s*</p>', re.DOTALL)
AMPOS_FACT_RE = re.compile(r"<li[^>]*>\s*<span>(장소|주최)</span>(.*?)</li>", re.DOTALL)

# "2026.11.16 ~ 2026.11.18" 또는 단일 "2026.11.16"
KAIF_RANGE_RE = re.compile(
    r"(?P<y1>\d{4})\.(?P<m1>\d{1,2})\.(?P<d1>\d{1,2})"
    r"(?:\s*~\s*(?P<y2>\d{4})\.(?P<m2>\d{1,2})\.(?P<d2>\d{1,2}))?")
# 공지 제목 꼬리 "(~ 9. 10.)" / "(~ 9. 8. 15:00)" — 시각은 버린다(마감 '일'이 계약).
KAIF_DEADLINE_RE = re.compile(
    r"\(\s*~\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*\.?\s*(?:\d{1,2}:\d{2})?\s*\)")
KAIF_NOTICE_LINK_RE = re.compile(
    r'<a\s+href="\?c=193&s=&gp=(?P<gp>\d+)&gbn=view&ix=(?P<ix>\d+)">(?P<title>.*?)</a>',
    re.DOTALL)

# KNS 제목 괄호: "…개최(9.9(수) 14:00, 대한상공회의소)" — 요일·시간·장소 전부 선택.
KNS_META_RE = re.compile(
    r"\(\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*"
    r"(?:\([월화수목금토일]\))?\s*,?\s*"
    r"(?:(?P<hh>\d{1,2}):(?P<mm>\d{2}))?\s*"
    r"(?:,\s*(?P<place>[^()]+?))?\s*\)\s*$")
KNS_LINK_RE = re.compile(
    r'href="(/boards/(?:chk_)?view/notice/(?P<id>\d+))"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL)


def _strip_tags(html: str) -> str:
    return clean_text(_TAG_RE.sub(" ", html or ""))


def _get(url: str, referer: str | None = None) -> str:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
        headers["X-Requested-With"] = "XMLHttpRequest"
    # AMPOS(:7443)는 사설 체인 인증서 — verify 를 끄는 대신 그 호스트만 예외.
    verify = "ampos.nanet.go.kr" not in url
    response = requests.get(url, headers=headers, timeout=TIMEOUT, verify=verify)
    response.raise_for_status()
    return response.text


def _post(url: str, data: dict, referer: str) -> str:
    response = requests.post(url, data=data, timeout=TIMEOUT, headers={
        "User-Agent": USER_AGENT, "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.kaif.or.kr",
    })
    response.raise_for_status()
    return response.text


def _hash(source_id: str, key: str) -> str:
    return "of-" + hashlib.sha1(f"{source_id}|{key}".encode("utf-8")).hexdigest()[:12]


def _nearest_year(month: int, day: int, anchor: date) -> str | None:
    """연도 없는 월·일 — anchor 에 가장 가까운 해로 읽는다(12월→1월 롤오버 안전).
    web/event_calendar.py 의 _resolve 와 같은 규칙."""
    candidates = []
    for off in (-1, 0, 1):
        try:
            candidates.append(date(anchor.year + off, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    best = min(candidates, key=lambda c: (abs((c - anchor).days), (anchor - c).days))
    return best.isoformat()


def _event(source_id: str, key: str, *, day: str, end: str | None = None,
           kind: str = "point", label: str, url: str, publisher: str,
           time_: str = "", host: str = "", place: str = "",
           notice_title: str = "") -> dict:
    label = clean_text(label)[:80]
    return {
        "hash": _hash(source_id, key),
        "date": day,
        "end_date": end or day,
        "kind": kind,
        "label": label,
        "title": label,
        "notice_title": clean_text(notice_title),
        "time": time_,
        "host": clean_text(host),
        "organizer": clean_text(host),
        "place": clean_text(place),
        "url": url,
        "publisher": publisher,
        "source_id": source_id,
        "origin": "official",
        "source_kind": "official",
    }


# ── 국회 AMPOS ────────────────────────────────────────────────────────────

AMPOS_LIST_URL = "https://ampos.nanet.go.kr:7443/seminarList.do"
AMPOS_INNER_URL = "https://ampos.nanet.go.kr:7443/seminarScheduleListInner.do"


def parse_assembly(html: str) -> list[dict]:
    """일정 partial 의 <tr> 블록 → 행. 키워드 게이트는 fetch 쪽에서."""
    rows = []
    for block in html.split("<tr>")[1:]:
        dm = AMPOS_DATE_RE.search(block)
        tm = AMPOS_TITLE_RE.search(block)
        if not dm or not tm:
            continue
        title = _strip_tags(tm.group(1))
        if not title:
            continue
        facts = {name: _strip_tags(value)
                 for name, value in AMPOS_FACT_RE.findall(block)}
        try:
            day = date(int(dm.group("y")), int(dm.group("m")),
                       int(dm.group("d"))).isoformat()
        except ValueError:
            continue
        rows.append(_event(
            "assembly_events", f"{day}|{title}", day=day, kind="point",
            label=title, url=AMPOS_LIST_URL, publisher="국회",
            time_=dm.group("time") or "",
            host=facts.get("주최", ""), place=facts.get("장소", "")))
    return rows


def fetch_assembly(state: dict) -> list[dict]:
    today = datetime.now(KST).date()
    end = today + timedelta(days=WINDOW_DAYS)
    rows: list[dict] = []
    for page in range(1, AMPOS_MAX_PAGES + 1):
        html = _get(
            f"{AMPOS_INNER_URL}?searchGubun=cal&curPage={page}&curMonth=&fileNo="
            f"&searchType=&queryText=&fromDate={today}&endDate={end}&sort=asc",
            referer=AMPOS_LIST_URL)
        parsed = parse_assembly(html)
        if not parsed:
            break
        rows.extend(parsed)
        if len(parsed) < 10:   # 10건/페이지 실측 — 덜 차면 마지막 페이지
            break
    gated = [r for r in rows if any(k in r["label"] for k in ASSEMBLY_KEYWORDS)]
    print(f"[events] assembly: 전체 {len(rows)}건 중 키워드 통과 {len(gated)}건")
    return gated


# ── 원자력산업협회 ────────────────────────────────────────────────────────

KAIF_CAL_PAGE = "https://www.kaif.or.kr/ko/?c=240"
KAIF_CAL_AJAX = "https://www.kaif.or.kr/common/plugin/kaif/ax.204.php"
KAIF_NOTICE_PAGE = "https://www.kaif.or.kr/ko/?c=193"


def parse_kaif_calendar(html: str) -> list[dict]:
    """ax.204.php 응답의 표: 번호|구분|행사명|기간|장소|웹사이트(선택 링크)."""
    rows = []
    for block in html.split("<tr>")[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", block, re.DOTALL)
        if len(cells) < 5:
            continue
        title = _strip_tags(cells[2])
        dm = KAIF_RANGE_RE.search(_strip_tags(cells[3]))
        if not title or not dm:
            continue
        try:
            start = date(int(dm.group("y1")), int(dm.group("m1")),
                         int(dm.group("d1"))).isoformat()
            end = (date(int(dm.group("y2")), int(dm.group("m2")),
                        int(dm.group("d2"))).isoformat()
                   if dm.group("y2") else start)
        except ValueError:
            continue
        link = re.search(r'href="(https?://[^"]+)"', block)
        rows.append(_event(
            "kaif_calendar", f"{start}|{title}", day=start, end=end,
            kind="range" if end > start else "point", label=title,
            url=link.group(1) if link else KAIF_CAL_PAGE,
            publisher="한국원자력산업협회",
            place=_strip_tags(cells[4])))
    return rows


def fetch_kaif_calendar(state: dict) -> list[dict]:
    html = _post(KAIF_CAL_AJAX, {
        "c": "204", "s": "", "gbn": "list", "sp": "", "sw": "", "cidx": "",
        "bbsid": "240", "sdate": "", "edate": "", "ps": "40",
        "w1": "", "w2": "", "w3": "", "gp": "1", "ix": "",
    }, referer=KAIF_CAL_PAGE)
    return parse_kaif_calendar(html)


def parse_kaif_notice(html: str, anchor: date) -> list[dict]:
    """공지 목록 — 제목 꼬리의 "(~ 9. 10.)" 만 마감 일정으로 세운다."""
    rows = []
    for m in KAIF_NOTICE_LINK_RE.finditer(html):
        title = _strip_tags(m.group("title"))
        dl = KAIF_DEADLINE_RE.search(title)
        if not dl:
            continue
        day = _nearest_year(int(dl.group("m")), int(dl.group("d")), anchor)
        if not day:
            continue
        label = clean_text(KAIF_DEADLINE_RE.sub("", title))
        rows.append(_event(
            "kaif_notice", f"{day}|{label}", day=day, kind="deadline",
            label=label, notice_title=title,
            url=f"https://www.kaif.or.kr/ko?c=193&gbn=view&gp={m.group('gp')}"
                f"&ix={m.group('ix')}&s=",
            publisher="한국원자력산업협회"))
    return rows


def fetch_kaif_notice(state: dict) -> list[dict]:
    html = _get(KAIF_NOTICE_PAGE)
    return parse_kaif_notice(html, datetime.now(KST).date())


# ── 원자력학회 ────────────────────────────────────────────────────────────

KNS_LIST_URL = "https://www.kns.org/boards/lists/notice"


def parse_kns_notice(title: str, notice_id: str, anchor: date) -> dict | None:
    """제목 괄호에서 날짜·시간·장소를 읽는다 — 괄호에 날짜가 없으면 일정 아님."""
    title = _strip_tags(title)
    m = KNS_META_RE.search(title)
    if not m:
        return None
    day = _nearest_year(int(m.group("m")), int(m.group("d")), anchor)
    if not day:
        return None
    label = clean_text(KNS_META_RE.sub("", title))
    time_ = f"{int(m.group('hh')):02d}:{m.group('mm')}" if m.group("hh") else ""
    return _event(
        "kns_notice", notice_id, day=day, kind="point", label=label,
        notice_title=title, time_=time_, host="한국원자력학회",
        place=clean_text(m.group("place") or ""),
        url=f"https://www.kns.org/boards/view/notice/{notice_id}",
        publisher="한국원자력학회")


def fetch_kns(state: dict) -> list[dict]:
    html = _get(KNS_LIST_URL)
    anchor = datetime.now(KST).date()
    rows = []
    seen = set()
    for m in KNS_LINK_RE.finditer(html):
        if m.group("id") in seen:
            continue
        seen.add(m.group("id"))
        row = parse_kns_notice(m.group("title"), m.group("id"), anchor)
        if row:
            rows.append(row)
    return rows


SOURCES = [
    {"id": "assembly_events", "fetch": fetch_assembly},
    {"id": "kaif_calendar", "fetch": fetch_kaif_calendar},
    {"id": "kaif_notice", "fetch": fetch_kaif_notice},
    {"id": "kns_notice", "fetch": fetch_kns},
]


# ── 스토어 (pubs_fetch 와 같은 계약) ─────────────────────────────────────

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


def prune(items: list[dict], today: date | None = None) -> list[dict]:
    today = today or datetime.now(KST).date()
    cutoff = (today - timedelta(days=KEEP_GRACE_DAYS)).isoformat()
    kept = [item for item in items if (item.get("end_date") or "") >= cutoff]
    kept.sort(key=lambda item: (item.get("date") or "", item.get("hash") or ""))
    return kept


def collected_today(store: dict, today: str | None = None) -> bool:
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
        print("[events] 오늘 이미 수집함 — 스킵")
        return False
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
            print(f"[events] {source_id} 실패 — 격리: {type(exc).__name__}: {exc}")
            continue
        by_hash = {item.get("hash"): item for item in store["items"]}
        new_items, enriched = [], 0
        for item in fetched:
            existing = by_hash.get(item["hash"])
            if existing is None:
                item["first_seen"] = now[:10]
                new_items.append(item)
                continue
            # 이미 있는 일정이라도 이번에 새로 얻은 사실(시간·장소·주최)은 채운다.
            for key in ("time", "place", "host", "organizer", "url", "notice_title"):
                if item.get(key) and not existing.get(key):
                    existing[key] = item[key]
                    enriched += 1
        store["items"].extend(new_items)
        store["last_checked"][source_id] = {
            "at": now, "ok": True, "new": len(new_items),
            # "0건 신규"와 "파서가 죽어 아무것도 못 읽음"을 구분하는 신호.
            "parsed": len(fetched),
        }
        print(f"[events] {source_id}: 수집 {len(fetched)}건 중 신규 {len(new_items)}건"
              f"{f', 보강 {enriched}건' if enriched else ''}")
        total_new += len(new_items)
    store["items"] = prune(store["items"])
    save_store(store)
    print(f"[events] 신규 {total_new}건, 보관 {len(store['items'])}건 → {OUT_FILE.name}")
    return True


if __name__ == "__main__":
    run(once_per_day="--once-per-day" in sys.argv)
