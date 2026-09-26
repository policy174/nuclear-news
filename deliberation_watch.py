"""원전 공론화 신호 감시 — 공식·시민단체·학회 게시판을 매시간 읽고, 공론화 국면이
바뀌는 신호를 지니 개인 DM(TELEGRAM_OPS_CHAT_ID)으로 보낸다.

왜 따로 도나: 뉴스 크롤(3시간, Gemini 쿼터)과 섞지 않는다. 여기는 LLM 0회, 제목
키워드만 본다. 2026-09-22 기후부 공론화 추진계획 발표를 사이트가 알리지 못한 게
실제 공백이었다(plans/2026-09-25-nuclens-deliberation-response.md).

소스·키워드·알림 규칙 근거: reference/deliberation-prep/04_monitoring_sources.md
(2026-09-26 전 소스 실접속 확인). 정책브리핑 RSS 는 2026-07-01 폐지 — 목록 페이지를
읽는다. 기후부 도메인은 mcee.go.kr(RSS 없음).

알림 등급
  now    — 공식 소스의 절차 신호(위촉·구성·의제·질문·전문위원·공청회·누리집), 시민단체
           보이콧·철수, 원안위 계속운전·건설허가 안건, 국회 소관 상임위 공론화 일정.
           23~07시(KST)는 보이콧·철수·위촉만 즉시, 나머지는 아침 묶음으로.
  digest — 그 밖의 공론화 관련 글. 07시 이후 첫 실행에서 하루 한 번 묶어 보낸다.

상태(deliberation_watch_state.json)는 Actions 캐시에 둔다. 캐시가 날아가면 첫 실행처럼
조용히 다시 채운다(과거 글 폭탄 방지). 발송이 실패하면 그 글은 '본 것'으로 치지 않고
다음 실행에서 다시 보낸다.

로컬 점검: python deliberation_watch.py --dry-run   (발송 없이 판정만 출력)
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KST = timezone(timedelta(hours=9))
STATE_FILE = Path(__file__).with_name("deliberation_watch_state.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145 nuclens-watch"}
RECENT_HOURS = 72          # 검색 결과로 재노출된 옛 글(2022 공청회 공고 등) 차단
FAIL_ALERT_AT = 3          # 연속 실패(예외 또는 0건 파싱) 3회째에 한 번 알린다
SEEN_KEEP_DAYS = 90

# ---- 키워드 게이트 (04 ③ — 실제 제목 254건에 돌려 목표 신호 전부 포착 확인) --------
A = re.compile(
    r"공론화|숙의|시민참여단|시민대표단|공론조사|권고안|공개토론회"
    r"|질문\s?문안|설문\s?문항|정보\s?제공\s?(원칙|규칙|기준)"
    r"|전문위원(회)?|검증위원(회)?|위원(장)?\s?위촉"
    r"|보이콧|불참|철수|탈퇴"
    r"|전력수급\s?기본계획|전기본|공청회"
    r"|국민과?\s?함께\s?(논의|결정|정)|국민\s?의견\s?수렴|국민\s?참여"
    r"|조사기관|1차\s?조사"
)
B = re.compile(r"원전|원자력|핵발전|탈핵|신규\s?원전|SMR|소형모듈|전력수급")   # '핵' 단독 금지(북핵)
STRONG = re.compile(r"원전\s?공론화|공론화\s?위원회|시민참여단|12차\s?전(력수급)?기본")
EXCL = re.compile(r"사용후핵연료|고준위|원자력안전위원회\s?(위원|비상임)|채용|입찰|합격자|인턴|전입")

OFFICIAL_ACTION = re.compile(r"위촉|출범|구성|시민참여단|의제|질문|전문위원|정보\s?제공|공청회|홈페이지|누리집|조사기관")
NGO_EXIT = re.compile(r"보이콧|불참|철수|탈퇴|거부|중단하라")
URGENT = re.compile(r"보이콧|철수|위촉")
HOMEPAGE = re.compile(r"홈페이지|누리집")
NSSC_AGENDA = re.compile(r"계속운전|건설허가|운영허가")
ASSEMBLY_CMIT = re.compile(r"기후에너지환경노동|산업통상자원중소벤처기업|과학기술정보방송통신")
ASSEMBLY_TOPIC = re.compile(r"공론화|전력수급|원전|원자력")


def gate(title: str) -> str | None:
    if STRONG.search(title):
        return "STRONG"
    if EXCL.search(title):
        return None
    if A.search(title) and B.search(title):
        return "A&B"
    return None


def classify(item: dict) -> str | None:
    """'now' | 'digest' | None. item: title, group(official|ngo|industry|press|nssc|assembly), text."""
    title, group = item["title"], item["group"]
    if group == "nssc":   # 원안위 회의 게시판: 제목엔 회차만 있고 안건은 본문에
        return "now" if NSSC_AGENDA.search(title + " " + item.get("text", "")) else None
    if group == "assembly":
        return "now" if ASSEMBLY_CMIT.search(item.get("text", "")) and ASSEMBLY_TOPIC.search(title) else None
    if not gate(title):
        return None
    if HOMEPAGE.search(title) and "공론화" in title:
        return "now"
    if group == "official" and OFFICIAL_ACTION.search(title):
        return "now"
    if group == "ngo" and NGO_EXIT.search(title) and "공론화" in title:
        return "now"
    return "digest"


def norm_key(title: str) -> str:
    """같은 성명이 녹색연합·환경연합·에너지정의행동에 동시에 올라온다 — 제목을 정규화해 한 번만."""
    core = re.sub(r"\[[^\]]*\]|\([^)]*\)|[^0-9A-Za-z가-힣]", "", html.unescape(title)).lower()
    return hashlib.sha1(core.encode("utf-8")).hexdigest()[:16]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def parse_date(raw: str) -> datetime | None:
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw or "")
    if not m:
        return None
    return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=KST)


# ---- 파서 (04 부록 파싱 메모) -------------------------------------------------------

def parse_korea(page: str) -> list[dict]:
    rows = re.findall(
        r'<a href="(/briefing/pressReleaseView\.do\?newsId=\d+)[^"]*">.*?<strong>(.*?)</strong>'
        r'.*?<span class="source">\s*<span>([\d.\-]+)</span>\s*<span>(.*?)</span>', page, re.S)
    return [{"url": "https://www.korea.kr" + href, "title": clean(t), "date": parse_date(d),
             "source_label": clean(dept)} for href, t, d, dept in rows]


def parse_mcee(page: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<a title="([^"]+)" href="(/home/web/board/read\.do)(?:;jsessionid=[^?"]*)?\?([^"]*)"', page):
        query = html.unescape(m[3])
        board = re.search(r"boardId=(\d+)", query)
        if not board:
            continue
        date = re.search(r"\d{4}-\d{2}-\d{2}", page[m.end():m.end() + 4000])
        out.append({"url": "https://mcee.go.kr" + m[2] + "?" + query, "title": clean(m[1]),
                    "date": parse_date(date[0] if date else ""), "key_extra": board[1]})
    return out


def parse_kns(page: str) -> list[dict]:
    rows = re.findall(r'<a href="(/boards/chk_view/press/\d+)">\s*(.*?)\s*</a>.*?<span class="date">\s*(\d{4}-\d{2}-\d{2})', page, re.S)
    return [{"url": "https://www.kns.org" + href, "title": clean(t), "date": parse_date(d)} for href, t, d in rows]


def parse_rss(body: bytes, path_allow: tuple[str, ...] = ()) -> list[dict]:
    import feedparser
    out = []
    for e in feedparser.parse(body).entries:
        link = e.get("link", "")
        if path_allow and not any(p in link for p in path_allow):
            continue   # 에너지전환포럼 /report_en/ 스팸 차단
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        date = datetime(*stamp[:6], tzinfo=timezone.utc).astimezone(KST) if stamp else None
        out.append({"url": link, "title": clean(e.get("title", "")), "date": date,
                    "text": clean(e.get("summary", ""))[:300]})
    return out


def parse_nssc(payload: dict) -> list[dict]:
    rows = ((payload.get("data") or {}).get("list") or [])
    return [{"url": ("https://www.nssc.go.kr/ko/cms/FR_BBS_CON/BoardView.do?"
                     f"BBS_SEQ={r['BBS_SEQ']}&BOARD_SEQ=14&CONTENTS_NO=1&MENU_ID=170&SITE_NO=2"),
             "title": clean(r.get("SUBJECT", "")), "date": parse_date(r.get("WRITE_DATE", "")),
             "text": clean(r.get("CONTENTS", ""))[:2000]}
            for r in rows if isinstance(r, dict) and r.get("BBS_SEQ")]


# ---- 소스 ---------------------------------------------------------------------------

SOURCES = [
    # 공식
    {"name": "정책브리핑(기후부·국조실·원안위)", "group": "official", "kind": "korea",
     "url": "https://www.korea.kr/briefing/pressReleaseList.do?repCode=A00019,A00004,C00012&pageIndex=1"},
    {"name": "기후부 보도자료", "group": "official", "kind": "mcee",
     "url": "https://mcee.go.kr/home/web/board/list.do?menuId=10598&boardMasterId=939"},
    {"name": "기후부 공지·공고", "group": "official", "kind": "mcee",
     "url": "https://mcee.go.kr/home/web/board/list.do?menuId=10524&boardMasterId=39"},
    {"name": "원안위 회의", "group": "nssc", "kind": "nssc",
     "url": "https://www.nssc.go.kr/ajaxf/FR_BBS_SVC/BBSViewList.do"},
    # 시민단체
    {"name": "환경운동연합", "group": "ngo", "kind": "rss", "url": "https://kfem.or.kr/rss"},
    {"name": "녹색연합", "group": "ngo", "kind": "rss", "url": "https://www.greenkorea.org/feed/"},
    {"name": "녹색연합(공론화 태그)", "group": "ngo", "kind": "rss",
     "url": "https://www.greenkorea.org/tag/%EA%B3%B5%EB%A1%A0%ED%99%94/feed/", "allow_empty": True},
    # 에너지정의행동(http://energyjustice.kr/zbxe/rss)은 국내에선 되지만 Actions(해외 IP)에서
    # 0건(2026-09-26 첫 실행). 공동성명은 환경운동연합·녹색연합에 교차 게시돼 그쪽이 잡는다.
    {"name": "에너지전환포럼", "group": "ngo", "kind": "rss", "url": "https://www.energytransitionkorea.org/rss",
     "path_allow": ("/energypress/", "/agenda/", "/announcement/")},
    {"name": "참여연대", "group": "ngo", "kind": "rss", "url": "https://www.peoplepower21.org/feed"},
    # 학계
    {"name": "한국원자력학회", "group": "industry", "kind": "kns", "url": "https://www.kns.org/boards/lists/press"},
    # 백스톱 — 1차 소스 누락분, 전용 누리집 개설 기사
    {"name": "구글뉴스(원전 공론화)", "group": "press", "kind": "rss", "allow_empty": True,
     "url": "https://news.google.com/rss/search?q=%22%EC%9B%90%EC%A0%84+%EA%B3%B5%EB%A1%A0%ED%99%94%22+when:1d&hl=ko&gl=KR&ceid=KR:ko"},
]


def fetch(src: dict) -> list[dict]:
    if src["kind"] == "nssc":
        body = {"pageNo": "1", "pagePerCnt": "15", "MENU_ID": "170", "CONTENTS_NO": "", "SITE_NO": "2",
                "BOARD_SEQ": "14", "BBS_SEQ": "", "CATE_SEQ": "", "SEARCH_FLD": "", "SEARCH": ""}
        resp = requests.post(src["url"], data=body, headers=UA, timeout=25)
        resp.raise_for_status()
        return parse_nssc(resp.json())
    resp = requests.get(src["url"], headers=UA, timeout=25)
    resp.raise_for_status()
    if src["kind"] == "rss":
        return parse_rss(resp.content, src.get("path_allow", ()))
    resp.encoding = resp.apparent_encoding or "utf-8"
    return {"korea": parse_korea, "mcee": parse_mcee, "kns": parse_kns}[src["kind"]](resp.text)


def fetch_assembly(key: str, today: datetime) -> list[dict]:
    """열린국회 국회일정(위원회) — 인증키가 있어야 전체가 온다(무키는 5행). 오늘~7일 뒤."""
    out = []
    for offset in range(8):
        day = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        resp = requests.get("https://open.assembly.go.kr/portal/openapi/ALLSCHEDULE", headers=UA, timeout=25,
                            params={"KEY": key, "Type": "json", "pIndex": 1, "pSize": 100,
                                    "SCH_KIND": "위원회", "SCH_DT": day})
        resp.raise_for_status()
        blocks = resp.json().get("ALLSCHEDULE") or []
        rows = next((b["row"] for b in blocks if isinstance(b, dict) and "row" in b), [])
        for r in rows:
            title = clean(r.get("SCH_CN", ""))
            out.append({"url": "https://open.assembly.go.kr", "title": f"{day} {title}", "date": today,
                        "text": clean(r.get("CMIT_NM", "")), "source_label": clean(r.get("CMIT_NM", ""))})
    return out


# ---- 상태·발송 ------------------------------------------------------------------------

def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def fmt_item(it: dict) -> str:
    when = it["date"].strftime("%m-%d") if it.get("date") else ""
    label = it.get("source_label") or it["source"]
    return (f"• ({html.escape(label)}) <a href=\"{html.escape(it['url'], quote=True)}\">"
            f"{html.escape(it['title'])}</a> {when}")


def send_ops(text: str) -> None:
    import telegram_send as tg
    ops = os.environ.get("TELEGRAM_OPS_CHAT_ID") or getattr(tg, "OPS_CHAT_ID", None)
    if not ops:
        # 브리핑 채널은 임직원이 보는 곳 — 개인 감시 알림을 거기로 폴백하지 않는다
        raise RuntimeError("TELEGRAM_OPS_CHAT_ID 미설정 — 개인 DM 경로가 없어 발송하지 않음")
    tg.CHAT_ID = ops
    tg.send_long_text(text, parse_mode="HTML", disable_preview=True)


def run(now: datetime, dry_run: bool = False, sources=None, fetcher=fetch, sender=send_ops) -> dict:
    sources = SOURCES if sources is None else sources
    state = load_state()
    seeding = state is None
    state = state or {"seen": {}, "fails": {}, "pending": [], "last_digest": ""}
    alerts, found, health_lines = [], [], []

    batches = [(src, None) for src in sources]
    assembly_key = os.environ.get("ASSEMBLY_API_KEY")
    if assembly_key and sources is SOURCES:
        batches.append(({"name": "국회 상임위 일정", "group": "assembly", "kind": "assembly"}, assembly_key))

    for src, key in batches:
        name = src["name"]
        try:
            items = fetch_assembly(key, now) if key else fetcher(src)
            if not items and not src.get("allow_empty") and src["group"] != "assembly":
                raise RuntimeError("0건 파싱 — 게시판 구조 변경 의심")
            state["fails"].pop(name, None)
        except Exception as exc:
            count = state["fails"].get(name, 0) + 1
            state["fails"][name] = count
            print(f"  ! {name}: {type(exc).__name__}: {exc}"[:200])
            if count == FAIL_ALERT_AT:
                health_lines.append(f"• {html.escape(name)} {count}회 연속 실패: {html.escape(str(exc)[:120])}")
            continue
        for it in items:
            it.update(source=name, group=src["group"])
            if it.get("date") and now - it["date"] > timedelta(hours=RECENT_HOURS):
                continue
            key_ = norm_key(it["title"])
            if key_ in state["seen"]:
                continue
            level = classify(it)
            if level:
                found.append((level, key_, it))

    if seeding:
        # 첫 실행(또는 캐시 유실): 지금 있는 글은 전부 본 것으로. 동작 확인용 요약만 한 번 보낸다.
        for _, key_, _it in found:
            state["seen"][key_] = now.isoformat()
        ok = len(sources) - len(state["fails"])
        lines = [f"<b>[공론화 신호 감시] 시작</b> 소스 {ok}/{len(sources)}곳 정상"
                 + (" · 국회 일정 API 연결" if assembly_key else " · 국회 일정 API 키 없음(미연결)"),
                 f"최근 {RECENT_HOURS}시간 해당 글 {len(found)}건 — 이후 새 글만 알립니다."]
        lines += [fmt_item(it) for _, _, it in found[:15]]
        text = "\n".join(lines)
    else:
        quiet = now.hour >= 23 or now.hour < 7
        nows, digests = [], []
        for level, key_, it in found:
            if level == "now" and not (quiet and not URGENT.search(it["title"])):
                nows.append((key_, it))
            else:
                digests.append((key_, it))
        state["pending"] += [{"key": k, **{f: (v.isoformat() if isinstance(v, datetime) else v)
                                           for f, v in it.items()}} for k, it in digests]
        send_digest = now.hour >= 7 and state["last_digest"] != now.strftime("%Y-%m-%d") and state["pending"]
        parts = []
        if nows:
            parts.append("<b>[공론화 신호] 즉시</b>\n" + "\n".join(fmt_item(it) for _, it in nows))
        if send_digest:
            pend = [{**p, "date": datetime.fromisoformat(p["date"]) if p.get("date") else None} for p in state["pending"]]
            parts.append(f"<b>[공론화 동향] {now:%m-%d} 묶음 {len(pend)}건</b>\n" + "\n".join(fmt_item(p) for p in pend))
        if health_lines:
            parts.append("<b>[공론화 감시] 소스 점검 필요</b>\n" + "\n".join(health_lines))
        text = "\n\n".join(parts)

    if text and not dry_run:
        sender(text)   # 실패하면 예외 — 아래 '본 것' 처리를 건너뛰어 다음 실행에서 재발송
    if dry_run:
        print(text or "(보낼 것 없음)")
        return state
    if not seeding:
        for key_, _ in nows:
            state["seen"][key_] = now.isoformat()
        for p in state["pending"]:
            state["seen"][p["key"]] = now.isoformat()
        if send_digest:
            state["pending"], state["last_digest"] = [], now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    save_state(state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="발송·상태 저장 없이 판정만 출력")
    args = parser.parse_args()
    run(datetime.now(KST), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
