"""AI 설명 점검 감시 — 공식 자료 변경과 AI 답변 변화를 개인 DM 과 /ai-check/ 에 남긴다.

3시간마다 공식 게시판을 보고, 새 글이 있으면 관련 질문을 다시 묻는다. 월요일에는 전체 질문을
다시 물어 주간 요약을 보낸다. 답변 원문은 저장하지 않는다(저장소 공개). 차이는 확인할 단서이지
오류 판정이 아니다. 상태는 Actions 캐시, 화면 자료는 web/public/ai-check/data.json.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "web" / "public" / "ai-check" / "data.json"
STATE = ROOT / "ai_check_state.json"
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145 nuclens-ai-check"}
TAGS = ("대조 문서와 불일치", "대조 문서에서 미확인", "단정 수준 차이", "대체로 일치")
RELEVANT = re.compile(r"원전|원자력|고준위|사용후핵연료|방폐|공론화|전력수급|전기본|SMR")
REQUERY_WORDS = ("공모", "주민투표", "지원", "저장", "재처리", "공론화", "권고", "계속운전", "수도권", "경주", "의견")
FAIL_ALERT_AT = 16  # 3시간 주기로 이틀
NEWS_ISSUES = [
    ("입지·지역 부담", r"주민|부지|유치|입지|지역|반대|영덕|기장|영광|고창|울진|경주|울주|한빛|삼척|지자체|의회"),
    ("정보·절차 신뢰", r"공론화|숙의|시민참여단|토론회|여론조사|설명회|투명|공정|의견수렴|청원"),
    ("전력수요", r"전력\s?수요|전력난|전력\s?부족|데이터센터|반도체|메가\s?프로젝트|전기본|전력수급"),
    ("안전", r"안전|사고|방사능|방사선|지진|후쿠시마|오염수|고장|규제"),
    ("사용후핵연료", r"사용후핵연료|고준위|방폐장|핵폐기물|건식저장|폐기물"),
    ("지원·보상", r"지원금|보상|특별지원|인센티브|지원사업"),
    ("전기요금·비용", r"전기\s?요금|전기세|건설비|비용|경제성"),
    ("대안 비교", r"재생에너지|태양광|풍력|ESS|LNG|송전망|탄소중립|에너지\s?믹스"),
]
NEWS_REL = re.compile(r"원전|원자력|핵발전|사용후핵연료|고준위|방폐장|SMR|소형모듈|전기본")
NEWS_NOISE = re.compile(r"주가|관련주|특징주|테마주|ETF|잠수함|코스피|코스닥|목표가|증시|수주")


def parse_hrwc(page: str, board: str) -> list[dict]:
    out = []
    for m in re.finditer(r'fn_detail\((\d+)\)"[^>]*>\s*<span class="title-text">(.*?)</span>', page, re.S):
        title = html.unescape(re.sub(r"\s+", " ", m[2])).strip()
        out.append({"id": f"hrwc-{board}-{m[1]}", "title": title, "source": "고준위위",
                    "url": f"https://www.hrwc.go.kr/board/{board}/detail.do?postNo={m[1]}" + ("&subType=annc" if board == "law" else "")})
    return out


def _get(url: str) -> str:
    err = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            err = e
    raise err


def fetch_official() -> tuple[list[dict], list[str]]:
    import deliberation_watch as dw
    items, failed = [], []
    for name, fn in [
        ("고준위위 보도자료", lambda: parse_hrwc(_get("https://www.hrwc.go.kr/board/press/list.do"), "press")),
        ("고준위위 공고", lambda: parse_hrwc(_get("https://www.hrwc.go.kr/board/law/list.do?subType=annc"), "law")),
        ("기후부 보도자료", lambda: [{"id": "mcee-" + i["url"], "title": i["title"], "url": i["url"], "source": "기후부"}
                               for i in dw.parse_mcee(_get("https://mcee.go.kr/home/web/board/list.do?menuId=10598&boardMasterId=939"))]),
    ]:
        try:
            got = fn()
            if not got:
                raise ValueError("0건")
            items += got
        except Exception as e:
            failed.append(f"{name}: {type(e).__name__}")
    return [i for i in items if i["source"] == "고준위위" or RELEVANT.search(i["title"])], failed


def related(title: str, questions: list[dict], limit: int = 3) -> list[dict]:
    words = [w for w in REQUERY_WORDS if w in title]
    return [q for q in questions if any(w in q["q"] for w in words)][:limit]


def _gemini(body: dict) -> dict:
    key = os.environ["GEMINI_API_KEY"]
    models = [os.environ.get("AI_CHECK_MODEL") or "gemini-3.5-flash", "gemini-2.5-flash"]
    last = None
    for model in dict.fromkeys(models):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=120)
        if r.status_code == 200:
            return r.json()
        last = f"Gemini HTTP {r.status_code}"
        if r.status_code not in (404, 400):
            break
    raise RuntimeError(last)


def _text(res: dict) -> str:
    return "".join(p.get("text", "") for p in res["candidates"][0]["content"]["parts"])


def judge_payload(q: dict, answer: str, baseline: list[dict]) -> dict:
    prompt = (
        "원전 정책 질문에 대한 AI 답변을 공식 기준과 대조한다. 오류로 단정하지 않는다. 대응·홍보 조언을 쓰지 않는다.\n"
        f"태그는 다음 중 하나: {', '.join(TAGS)}.\n"
        "changed는 이전 요약과 비교해 결론, 수치, 정책 범위가 달라졌을 때만 true. 표현 차이는 false.\n"
        'JSON만 출력: {"summary":"80자 이내 답변 요약","tag":"태그","changed":true,"why":"60자 이내 사유"}\n\n'
        f"질문: {q['q']}\n공식 설명: {q['official']}\n공식 기준값: {json.dumps(baseline, ensure_ascii=False)}\n"
        f"이전 요약: {(q.get('latest') or {}).get('summary') or q['a'].get('Gemini', '')}\n\n답변:\n{answer[:6000]}"
    )
    return {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}


def check_question(q: dict, baseline: list[dict], call=_gemini, today: str = "") -> dict:
    res = call({"contents": [{"parts": [{"text": q["q"]}]}], "tools": [{"google_search": {}}],
                "generationConfig": {"temperature": 0.2}})
    answer = _text(res).strip()
    if not answer:
        raise ValueError("빈 답변")
    chunks = res["candidates"][0].get("groundingMetadata", {}).get("groundingChunks", [])
    sources = list(dict.fromkeys(c.get("web", {}).get("title", "") for c in chunks if c.get("web")))[:4]
    v = json.loads(_text(call(judge_payload(q, answer, baseline))))
    if v.get("tag") not in TAGS or not v.get("summary"):
        raise ValueError("판정 형식 오류")
    return {"date": today, "service": "Gemini API", "summary": str(v["summary"])[:120], "tag": v["tag"],
            "changed": bool(v.get("changed")), "why": str(v.get("why", ""))[:90], "sources": sources}


def news_snapshot(now: datetime, archive: Path = ROOT / "archive") -> dict:
    since = now - timedelta(days=7)
    seen, rows = set(), {n: [] for n, _ in NEWS_ISSUES}
    for f in sorted(archive.glob("*.jsonl"))[-2:]:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                at = datetime.fromisoformat(r.get("pub") or r["archived_at"])
            except (ValueError, KeyError):
                continue
            if at.tzinfo is None:
                continue
            title = r.get("title_kr") or r.get("title") or ""
            if at < since or title in seen or not NEWS_REL.search(title) or NEWS_NOISE.search(title):
                continue
            seen.add(title)
            for n, p in NEWS_ISSUES:
                if re.search(p, title):
                    rows[n].append({"t": title[:90], "u": r.get("resolved_url") or r.get("url") or "", "at": at.isoformat()})
    out = []
    for n, rs in rows.items():
        rs.sort(key=lambda x: x["at"], reverse=True)
        out.append({"name": n, "count": len(rs), "top": [{"t": x["t"], "u": x["u"]} for x in rs[:3]]})
    out.sort(key=lambda x: -x["count"])
    return {"as_of": f"{now.year}.{now.month}.{now.day}.", "rows": out}


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def add_signal(data: dict, now: datetime, level: str, title: str, detail: str, url: str = "") -> None:
    sig = {"date": f"{now.year}.{now.month}.{now.day}.", "level": level, "title": title[:90], "detail": detail[:180]}
    if url:
        sig["url"] = url
    data["signals"] = [sig] + data["signals"][:39]


def apply_result(data: dict, q: dict, res: dict, now: datetime) -> str | None:
    """결과를 질문에 붙이고, 알릴 만한 변화면 한 줄을 돌려준다."""
    first = not q.get("latest")
    q["latest"] = {k: res[k] for k in ("date", "service", "summary", "tag", "why", "sources")}
    if res["tag"] == "대조 문서와 불일치" and q["tag"] != res["tag"]:
        level = "신규"
    elif res["changed"] and not first:
        level = "변화"
    else:
        return None
    add_signal(data, now, level, q["q"], f"{res['service']}: {res['summary']}")
    return f"· {'기준과 다름' if level == '신규' else '달라짐'}: {q['q']}\n  {res['summary']}"


def monday_key(now: datetime) -> str:
    return (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")


def run(now: datetime, *, dry: bool = False, force_weekly: bool = False, fetcher=fetch_official, call=_gemini, sender=None,
        state_file: Path = STATE, data_file: Path = DATA, news=news_snapshot) -> dict:
    data = json.loads(data_file.read_text(encoding="utf-8"))
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
    first = state is None
    state = state or {"seen": [], "fails": 0, "alerted": False, "pending": [], "weekly": "", "news": ""}
    today = f"{now.year}.{now.month}.{now.day}."
    msgs, questions = list(state["pending"]), data["questions"]
    items, failed = fetcher()
    if failed and not items:
        state["fails"] += 1
    else:
        state["fails"], state["alerted"] = 0, False
    if state["fails"] >= FAIL_ALERT_AT and not state["alerted"]:
        msgs.append("[AI 설명 점검] 공식 게시판을 이틀째 읽지 못합니다: " + esc(", ".join(failed)))
        state["alerted"] = True
    seen = set(state["seen"])
    new = [i for i in items if i["id"] not in seen]
    state["seen"] = ([i["id"] for i in new] + state["seen"])[:400]
    if first:
        msgs.append("[AI 설명 점검] 연결 완료\n공식 게시판 감시를 시작했습니다.\n화면: https://nuclens.pages.dev/ai-check/"
                    + (f"\n읽지 못한 곳: {esc(', '.join(failed))}" if failed else ""))
        new = []
    for it in new[:5]:
        add_signal(data, now, "신규", it["title"], f"{it['source']} 게시. 관련 질문을 다시 확인합니다.", it["url"])
        lines = [f"[공식 변경] {esc(it['source'])}", f'<a href="{esc(it["url"])}">{esc(it["title"])}</a>']
        for q in related(it["title"], questions):
            try:
                line = apply_result(data, q, check_question(q, data["baseline"], call, today), now)
                lines.append(esc(line) if line else f"· 변화 없음: {esc(q['q'])}")
            except Exception as e:
                lines.append(f"· 재조회 실패: {esc(q['q'])} ({type(e).__name__})")
        msgs.append("\n".join(lines))
    week = monday_key(now)
    if force_weekly or (now.weekday() == 0 and now.hour >= 7 and state["weekly"] != week):
        changes, errs = [], 0
        for q in questions:
            try:
                line = apply_result(data, q, check_question(q, data["baseline"], call, today), now)
                if line:
                    changes.append(esc(line))
            except Exception:
                errs += 1
            time.sleep(0 if dry or call is not _gemini else 4)
        state["weekly"] = week
        focus = [q for q in questions if (q.get("latest") or {}).get("tag") == "대조 문서와 불일치"][:3]
        body = ["[이번 주 AI 설명 점검]", f"{len(questions) - errs}개 질문 조회(Gemini API, 소비자 화면과 다를 수 있음)"]
        body += changes[:6] or ["· 결론·수치·범위가 달라진 답변 없음"]
        if focus:
            body += ["다음에 볼 질문:"] + [f"· {esc(q['q'])}" for q in focus]
        if errs:
            body.append(f"조회 실패 {errs}개")
        body.append("https://nuclens.pages.dev/ai-check/")
        msgs.append("\n".join(body))
    if state["news"] != now.strftime("%Y-%m-%d"):
        data["news"] = news(now)
        state["news"] = now.strftime("%Y-%m-%d")
    data["as_of"] = today
    if 7 <= now.hour < 23 and msgs:
        for m in msgs:
            if dry:
                print(m)
            else:
                (sender or _send)(m)
        msgs = []
    state["pending"] = msgs
    if not dry:
        data_file.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return {"new": len(new), "failed": failed, "pending": len(msgs)}


def _send(text: str) -> None:
    import deliberation_watch as dw
    dw.send_ops(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    a = ap.parse_args()
    print(run(datetime.now(KST), dry=a.dry_run, force_weekly=a.weekly))
    return 0


if __name__ == "__main__":
    sys.exit(main())
