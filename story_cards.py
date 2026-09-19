#!/usr/bin/env python
"""스토리 카드뉴스 — 이슈 하나를 5장으로 푼다.

일일 카드(make_cards.py)가 그날 상위 3건을 한 장씩 훑는 물건이라면, 이건 **며칠에
걸쳐 이어진 이슈 하나**를 표지·타임라인·쟁점·의미·체크리스트로 끝까지 따라간다.

재료는 전부 chronicle 이다. 타임라인은 `events[]`(실제 기사 날짜·제목), 쟁점·의미는
`narrative`(국면 서사), 체크리스트는 `watchpoints`. 그래서 **없는 날짜를 지어낼 자리가
없다** — 프롬프트가 아니라 검증이 그걸 막는다(validate 의 when/숫자 대조).

    python story_cards.py --date 2026-09-18        # 그날 사이트 순위로 고른다
    python story_cards.py --date ... --dry         # 렌더 없이 카피만 본다
    python story_cards.py --check                  # 검증기 self-check

스토리가 없는 날은 **안 만든다**(exit 0). 부가물이라 억지로 채우지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import gemini_client
import make_cards as mc

ROOT = mc.ROOT
CHRONICLES = ROOT / "chronicles.json"
NARRATIVES = ROOT / "chronicle_narratives.json"
ALBUM_FILE = ROOT / "cards" / "story_album.json"

MIN_EVENTS = 3          # 이보다 적으면 타임라인이 안 선다
TIMELINE_ROWS = 4
ISSUE_COUNT = 3
PILLAR_COUNT = 3
CHECK_COUNT = 5

# 길이 상한 — 렌더가 줄이기 전에 코드가 막는다(LLM 은 한글 글자 수를 못 센다).
COVER_HEADLINE_MAX = 26
COVER_DECK_MAX = 90
BADGE_VALUE_MAX = 14
BADGE_LABEL_MAX = 20
LEDE_MAX = 30
WHEN_MAX = 16
WHAT_MAX = 34
NOTE_MAX = 40
ISSUE_TITLE_MAX = 14
ISSUE_POINT_MAX = 30
WHY_HEADLINE_MAX = 34
PILLAR_TITLE_MAX = 10
PILLAR_TEXT_MAX = 44
QUOTE_MAX = 48
CHECK_HEADLINE_MAX = 20
CHECK_TEXT_MAX = 34
ASIDE_MAX = 64

ISSUE_ICONS = ("coins", "plant", "doc", "market", "shield", "network")
PILLAR_ICONS = ("market", "shield", "network", "coins", "plant", "doc")

SYSTEM_PROMPT = f"""너는 한국수력원자력 원자력정책실의 카드뉴스 편집자다.
하나의 이슈가 며칠에 걸쳐 어떻게 움직였는지를 5장짜리 카드뉴스로 만든다.
입력은 그 이슈의 **사건 목록(events)**, **국면 서사(narrative)**, **관전 포인트
(watchpoints)** 다. 여기 없는 사실·날짜·수치를 새로 만들지 않는다.

JSON 만 출력한다. 스키마:
{{
 "cover":  {{"chip": 분류 한 단어(예 "해외이슈"), "topic": 보조 라벨(예 "미국 투자"),
            "headline": {COVER_HEADLINE_MAX}자 이내 제목. 질문형이 좋다. 강조는 `[[대괄호]]`로 한 곳만,
            "deck": {COVER_DECK_MAX}자 이내 두 문장. 무슨 일이 있었고 무엇이 쟁점인지,
            "badge": {{"value": 핵심 숫자({BADGE_VALUE_MAX}자 이내, 예 "2,000억 달러"),
                      "label": 그 숫자가 무엇인지({BADGE_LABEL_MAX}자 이내)}} 또는 null}},
 "facts":  {{"lede": {LEDE_MAX}자 이내 한 줄 요약(강조 한 곳 가능),
            "timeline": [{{"when": 날짜, "what": {WHAT_MAX}자 이내 그날 일어난 일}}] {TIMELINE_ROWS}개,
            "note": {NOTE_MAX}자 이내 한 줄 덧붙임(없으면 "")}},
 "issues": [{{"title": {ISSUE_TITLE_MAX}자 이내 쟁점 이름, "points": [{ISSUE_POINT_MAX}자 이내] 2개,
            "icon": {" | ".join(ISSUE_ICONS)} 중 하나}}] {ISSUE_COUNT}개,
 "why":    {{"headline": {WHY_HEADLINE_MAX}자 이내 한 문장(강조 한 곳 가능),
            "pillars": [{{"title": {PILLAR_TITLE_MAX}자 이내, "text": {PILLAR_TEXT_MAX}자 이내,
                        "icon": {" | ".join(PILLAR_ICONS)} 중 하나}}] {PILLAR_COUNT}개,
            "quotes": [{QUOTE_MAX}자 이내] 2개}},
 "check":  {{"headline": {CHECK_HEADLINE_MAX}자 이내,
            "checks": [{{"text": {CHECK_TEXT_MAX}자 이내, "done": true/false}}] {CHECK_COUNT}개,
            "aside": {ASIDE_MAX}자 이내 마무리 한 줄}}
}}

규칙:
- **timeline[].when 은 입력 events 의 날짜만 쓴다.** 그 날짜에 없던 일을 붙이지 않는다.
  마지막 칸은 가장 최근 사건이고, when 을 "현재 (9월 17일)" 처럼 써도 된다(날짜는 그대로).
- **badge.value 의 숫자는 입력에 나온 숫자여야 한다.** 없으면 badge 를 null 로 둔다.
- checks {CHECK_COUNT}개는 **반드시 섞는다**: 앞의 2~3개는 events 에 이미 있는 사실이라
  `"done": true`, 나머지 2~3개는 watchpoints 기반의 앞으로 볼 것이라 `"done": false`.
  전부 true 이거나 전부 false 면 그 카드는 버려진다.
- 문장은 카드뉴스 말투(~습니다/~입니다)로 짧게. 개조식 명사 나열은 쓰지 않는다.
- 해석·전망을 사실처럼 쓰지 않는다. 불확실한 건 "미정"·"확정되지 않았습니다"로 남긴다.
"""


# ---- A. 재료 ------------------------------------------------------------------


def load_chronicles() -> dict:
    if not CHRONICLES.exists():
        return {}
    return json.loads(CHRONICLES.read_text(encoding="utf-8")).get("chronicles") or {}


def load_narratives() -> dict:
    if not NARRATIVES.exists():
        return {}
    return json.loads(NARRATIVES.read_text(encoding="utf-8")).get("narratives") or {}


def pick_story(date: str) -> tuple[dict, dict] | None:
    """그날 사이트 순위 위에서부터 내려가며 **스토리가 붙은 첫 이슈**를 고른다.

    순위를 다시 매기지 않는다 — 카드가 사이트와 다른 걸 1위로 세우면 둘이 갈린다.
    """
    rows = mc.load_site_ranking(date)
    if not rows:
        return None
    chron, nar = load_chronicles(), load_narratives()
    if not chron:
        return None
    by_hash: dict[str, dict] = {}
    for c in chron.values():
        for ev in c.get("events") or []:
            by_hash.setdefault(str(ev.get("hash") or ""), c)
    for row in rows:
        rep = row.get("representative_article") or {}
        c = by_hash.get(str(rep.get("hash") or ""))
        if not c or len(c.get("events") or []) < MIN_EVENTS:
            continue
        n = nar.get(c.get("chronicle_id"))
        if not n or not (n.get("narrative") or {}).get("narrative"):
            continue
        return row, {"chronicle": c, "narrative": n["narrative"]}
    return None


def build_payload(row: dict, story: dict, date: str) -> dict:
    c, n = story["chronicle"], story["narrative"]
    events = sorted(c.get("events") or [], key=lambda e: str(e.get("article_date") or ""))
    rep = row.get("representative_article") or {}
    return {
        "date": date,
        "issue_title": row.get("title") or c.get("title"),
        "topic": mc.topic_label(row),
        "events": [{"date": e.get("article_date"), "title": e.get("title_kr")}
                   for e in events[-12:] if e.get("article_date")],
        "narrative": n.get("narrative") or [],
        "phase_now": n.get("phase_now") or "",
        "watchpoints": n.get("watchpoints") or [],
        "summary": rep.get("summary") or row.get("summary") or "",
        "why_important": row.get("why_important") or "",
    }


# ---- B. 검증 ------------------------------------------------------------------


_NUM_RE = re.compile(r"[0-9][0-9,.]*")


def _line(problems: list[str], where: str, text, limit: int, accent_ok: bool = False) -> None:
    if not isinstance(text, str) or not text.strip():
        problems.append(f"{where}: 비어 있음")
        return
    if not accent_ok and "[[" in text:
        problems.append(f"{where}: 강조 표기는 여기 못 쓴다")
    if text.count("[[") > 1:
        problems.append(f"{where}: 강조는 한 곳만")
    if mc.visible_len(text) > limit:
        problems.append(f'{where}: {mc.visible_len(text)}자 > {limit} — "{text[:22]}…"')


def _dates_in(payload: dict) -> set[str]:
    """events 날짜를 '9월 17일'·'2026-09-17'·'2026년 9월 17일' 어느 표기로 써도 맞도록."""
    out: set[str] = set()
    for ev in payload.get("events") or []:
        d = str(ev.get("date") or "")
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
        if not m:
            continue
        y, mo, da = m.group(1), int(m.group(2)), int(m.group(3))
        out |= {d, f"{y}년 {mo}월 {da}일", f"{mo}월 {da}일", f"{y}.{mo:02d}.{da:02d}"}
    return out


def validate(raw: dict, payload: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ["JSON 객체가 아님"]

    cover = raw.get("cover") or {}
    _line(problems, "cover.headline", cover.get("headline"), COVER_HEADLINE_MAX, accent_ok=True)
    _line(problems, "cover.deck", cover.get("deck"), COVER_DECK_MAX)
    _line(problems, "cover.chip", cover.get("chip"), 8)
    badge = cover.get("badge")
    if badge:
        _line(problems, "cover.badge.value", badge.get("value"), BADGE_VALUE_MAX)
        _line(problems, "cover.badge.label", badge.get("label"), BADGE_LABEL_MAX)
        # 숫자는 재료에 있던 것만. 카드에서 제일 크게 박히는 자리라 지어내면 바로 사고다.
        haystack = json.dumps(payload, ensure_ascii=False)
        nums = _NUM_RE.findall(str(badge.get("value") or ""))
        for num in nums:
            if num.replace(",", "") not in haystack.replace(",", ""):
                problems.append(f'cover.badge.value: "{num}" 은 입력에 없는 숫자')

    facts = raw.get("facts") or {}
    _line(problems, "facts.lede", facts.get("lede"), LEDE_MAX, accent_ok=True)
    if facts.get("note"):
        _line(problems, "facts.note", facts.get("note"), NOTE_MAX)
    timeline = facts.get("timeline")
    if not isinstance(timeline, list) or len(timeline) != TIMELINE_ROWS:
        problems.append(f"facts.timeline: {len(timeline) if isinstance(timeline, list) else '?'}개 "
                        f"— {TIMELINE_ROWS}개여야 한다")
    else:
        known = _dates_in(payload)
        for i, rowx in enumerate(timeline, start=1):
            if not isinstance(rowx, dict):
                problems.append(f"facts.timeline[{i}]: 객체가 아님")
                continue
            _line(problems, f"facts.timeline[{i}].when", rowx.get("when"), WHEN_MAX)
            _line(problems, f"facts.timeline[{i}].what", rowx.get("what"), WHAT_MAX)
            when = str(rowx.get("when") or "")
            if known and not any(k in when or when in k for k in known):
                problems.append(f'facts.timeline[{i}].when: "{when}" 은 events 에 없는 날짜')

    issues = raw.get("issues")
    if not isinstance(issues, list) or len(issues) != ISSUE_COUNT:
        problems.append(f"issues: {len(issues) if isinstance(issues, list) else '?'}개 "
                        f"— {ISSUE_COUNT}개여야 한다")
    else:
        for i, it in enumerate(issues, start=1):
            _line(problems, f"issues[{i}].title", (it or {}).get("title"), ISSUE_TITLE_MAX)
            pts = (it or {}).get("points")
            if not isinstance(pts, list) or not 1 <= len(pts) <= 2:
                problems.append(f"issues[{i}].points: 1~2개여야 한다")
            else:
                for j, t in enumerate(pts, start=1):
                    _line(problems, f"issues[{i}].points[{j}]", t, ISSUE_POINT_MAX)
            if (it or {}).get("icon") and it["icon"] not in ISSUE_ICONS:
                problems.append(f'issues[{i}].icon: "{it["icon"]}" 은 목록 밖')

    why = raw.get("why") or {}
    _line(problems, "why.headline", why.get("headline"), WHY_HEADLINE_MAX, accent_ok=True)
    pillars = why.get("pillars")
    if not isinstance(pillars, list) or len(pillars) != PILLAR_COUNT:
        problems.append(f"why.pillars: {PILLAR_COUNT}개여야 한다")
    else:
        for i, pl in enumerate(pillars, start=1):
            _line(problems, f"why.pillars[{i}].title", (pl or {}).get("title"), PILLAR_TITLE_MAX)
            _line(problems, f"why.pillars[{i}].text", (pl or {}).get("text"), PILLAR_TEXT_MAX)
            if (pl or {}).get("icon") and pl["icon"] not in PILLAR_ICONS:
                problems.append(f'why.pillars[{i}].icon: "{pl["icon"]}" 은 목록 밖')
    quotes = why.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 2:
        problems.append("why.quotes: 1~2개여야 한다")
    else:
        for i, q in enumerate(quotes, start=1):
            _line(problems, f"why.quotes[{i}]", q, QUOTE_MAX)

    check = raw.get("check") or {}
    _line(problems, "check.headline", check.get("headline"), CHECK_HEADLINE_MAX)
    _line(problems, "check.aside", check.get("aside"), ASIDE_MAX)
    checks = check.get("checks")
    if not isinstance(checks, list) or len(checks) != CHECK_COUNT:
        problems.append(f"check.checks: {CHECK_COUNT}개여야 한다")
    else:
        for i, ck in enumerate(checks, start=1):
            _line(problems, f"check.checks[{i}].text", (ck or {}).get("text"), CHECK_TEXT_MAX)
        if not any((ck or {}).get("done") for ck in checks):
            problems.append("check.checks: 이미 일어난 항목(done)이 하나도 없다")
        if all((ck or {}).get("done") for ck in checks):
            problems.append("check.checks: 앞으로 볼 항목(done=false)이 하나도 없다")
    return problems


# ---- C. 슬라이드 --------------------------------------------------------------


def build_slides(raw: dict, payload: dict) -> list[dict]:
    n = 5
    def num(i): return f"{i:02d} / {n:02d}"
    cover, facts = raw["cover"], raw["facts"]
    why, check = raw["why"], raw["check"]
    slides = [{
        "type": "story-cover", "slideNum": num(1),
        "chip": cover.get("chip") or payload["topic"], "topic": cover.get("topic") or "",
        "photo": None,                       # build.js 가 분류에서 고른다
        "headline": cover["headline"], "deck": cover["deck"],
        "badge": cover.get("badge") or None,
    }, {
        "type": "story-facts", "slideNum": num(2), "chip": "사실 정리",
        "headline": "무슨 일이 있었나?", "lede": facts.get("lede") or "",
        "timeline": facts["timeline"], "note": facts.get("note") or "",
    }, {
        "type": "story-issues", "slideNum": num(3), "chip": "핵심 쟁점",
        "headline": "지금 무엇이 논의되고 있나?", "issues": raw["issues"],
    }, {
        "type": "story-why", "slideNum": num(4), "chip": "왜 중요한가",
        "headline": why["headline"], "pillars": why["pillars"],
        "quotes": why.get("quotes") or [],
    }, {
        "type": "story-check", "slideNum": num(5), "chip": "앞으로 볼 것",
        "headline": check["headline"], "checks": check["checks"],
        "aside": check.get("aside") or "",
    }]
    # 표지 사진은 분류로 고른다 — 본문 문구를 훑으면 그날 기사에만 맞는 규칙이 된다.
    slides[0]["topic"] = slides[0]["topic"] or payload["topic"]
    slides[0]["photoTopic"] = payload["topic"]
    return slides


def ask_llm(payload: dict, problems: list[str] | None = None) -> dict:
    body = dict(payload)
    if problems:
        body["fix_these"] = problems
    return gemini_client.call_json(
        SYSTEM_PROMPT, json.dumps(body, ensure_ascii=False, indent=1),
        temperature=0.35, max_output_tokens=4096, thinking_budget=0,
        fallback_model=gemini_client.FALLBACK_MODEL, label="story-cards",
    )


# ---- D. main ------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="이 날짜의 사이트 순위로 고른다")
    ap.add_argument("--dry", action="store_true", help="렌더 없이 카피만 만든다")
    ap.add_argument("--copy-file", type=Path, help="사람이 쓴 카피 JSON(같은 검증을 거친다)")
    ap.add_argument("--check", action="store_true", help="검증기 self-check")
    args = ap.parse_args()
    if args.check:
        _self_check()
        print("self-check OK")
        return 0

    date = args.date or mc.datetime.now(mc.KST).strftime("%Y-%m-%d")
    hit = pick_story(date)
    if not hit:
        print(f"[story] {date}: 스토리가 붙은 이슈 없음 — 카드 안 만든다")
        return 0
    row, story = hit
    payload = build_payload(row, story, date)
    print(f"[story] {payload['issue_title'][:40]} | 이벤트 {len(payload['events'])}건 "
          f"| 관전 {len(payload['watchpoints'])}건")

    raw, problems = None, []
    if args.copy_file:
        raw = json.loads(args.copy_file.read_text(encoding="utf-8"))
        problems = validate(raw, payload)
        if problems:
            print("[story] --copy-file 검증 실패: " + "; ".join(problems[:8]))
            return 1
    else:
        for attempt in (1, 2):
            try:
                candidate = ask_llm(payload, problems)
            except Exception as exc:  # noqa: BLE001 — 부가물이라 원인만 남기고 건너뛴다
                print(f"[story] LLM 실패 ({attempt}/2): {exc}")
                continue
            problems = validate(candidate, payload)
            if not problems:
                raw = candidate
                break
            print(f"[story] 카피 검증 실패 ({attempt}/2): " + "; ".join(problems[:6]))
    if raw is None:
        # 폴백 카피를 만들지 않는다. 스토리 카드는 부가물이고, 재료를 기계적으로
        # 이어 붙이면 타임라인이 그럴듯한 거짓말이 된다.
        print("[story] 카피를 못 만들었다 — 오늘 스토리 카드는 건너뛴다")
        return 0

    slides = build_slides(raw, payload)
    if args.dry:
        print(json.dumps(slides, ensure_ascii=False, indent=1))
        return 0
    mc.render(slides)
    files = mc.gate(len(slides))
    plain = raw["cover"]["headline"].replace("[[", "").replace("]]", "")
    caption = "\n".join([f"[스토리] {plain}", raw["cover"]["deck"], "", mc.SITE])
    ALBUM_FILE.write_text(json.dumps({
        "date": date, "issue": payload["issue_title"],
        "caption": caption,
        "chronicle_id": story["chronicle"].get("chronicle_id"),
        "files": [str(f.relative_to(ROOT)).replace("\\", "/") for f in files],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[story] {len(files)}장 준비 완료 → {ALBUM_FILE.name}")
    return 0


def _self_check() -> None:
    """runnable check — 검증기가 실제로 막는지 본다."""
    payload = {"topic": "해외사업",
               "events": [{"date": "2026-09-08", "title": "미국, 원전 8기 제안"},
                          {"date": "2026-09-16", "title": "국회 보고 취소"},
                          {"date": "2026-09-17", "title": "MOU 서명 연기"}],
               "narrative": ["..."], "phase_now": "...", "watchpoints": ["..."]}
    ok = {
        "cover": {"chip": "해외이슈", "topic": "미국 투자",
                  "headline": "MOU 서명, 왜 [[연기됐나]]",
                  "deck": "정부가 국회 보고를 취소하고 서명을 미뤘습니다. 노형 배치가 쟁점입니다.",
                  "badge": None},
        "facts": {"lede": "서명은 미뤄졌습니다", "note": "",
                  "timeline": [{"when": "2026년 9월 8일", "what": "미국, 원전 8기 건설 제안"},
                               {"when": "9월 16일", "what": "국회 보고 취소"},
                               {"when": "9월 17일", "what": "MOU 서명 연기"},
                               {"when": "현재 (9월 17일)", "what": "새 일정 미정"}]},
        "issues": [{"title": "투자 규모", "points": ["규모가 조율 중입니다"], "icon": "coins"},
                   {"title": "지분 구조", "points": ["의결권 확보가 쟁점입니다"], "icon": "plant"},
                   {"title": "국회 절차", "points": ["보고 일정이 미정입니다"], "icon": "doc"}],
        "why": {"headline": "협력 조건이 [[여기서]] 갈립니다",
                "pillars": [{"title": "시장", "text": "참여 범위가 걸려 있습니다", "icon": "market"},
                            {"title": "통제권", "text": "지분이 수출 조건과 닿습니다", "icon": "shield"},
                            {"title": "산업", "text": "기자재 수주가 함께 움직입니다", "icon": "network"}],
                "quotes": ["지금은 최종 조율 단계입니다"]},
        "check": {"headline": "이것을 주목하세요", "aside": "협상은 진행 중입니다",
                  "checks": [{"text": "원전 8기 제안", "done": True},
                             {"text": "국회 보고 취소", "done": True},
                             {"text": "서명 일정 발표", "done": False},
                             {"text": "지분율 합의", "done": False},
                             {"text": "첫 송금 집행", "done": False}]},
    }
    assert validate(ok, payload) == [], validate(ok, payload)

    def mut(section, **kw):
        out = json.loads(json.dumps(ok))
        out[section].update(kw)
        return out

    # 없는 날짜를 타임라인에 세우면 막힌다 — 이 검증이 이 파일의 존재 이유다.
    bad = json.loads(json.dumps(ok))
    bad["facts"]["timeline"][1]["when"] = "2026년 9월 1일"
    assert any("events 에 없는 날짜" in p for p in validate(bad, payload)), "날짜 대조"

    # 입력에 없는 숫자를 배지에 박으면 막힌다
    bad = mut("cover", badge={"value": "9,900억 달러", "label": "투자 규모"})
    assert any("없는 숫자" in p for p in validate(bad, payload)), "숫자 대조"
    good = mut("cover", badge={"value": "8기", "label": "제안된 원전"})
    assert not [p for p in validate(good, payload) if "숫자" in p], "입력에 있는 숫자는 통과"

    # 길이·개수
    bad = mut("cover", headline="가" * (COVER_HEADLINE_MAX + 1))
    assert any("cover.headline" in p for p in validate(bad, payload))
    bad = json.loads(json.dumps(ok)); bad["issues"] = bad["issues"][:2]
    assert any("issues" in p for p in validate(bad, payload))
    bad = json.loads(json.dumps(ok)); bad["facts"]["timeline"] = bad["facts"]["timeline"][:3]
    assert any("timeline" in p for p in validate(bad, payload))
    bad = json.loads(json.dumps(ok)); bad["issues"][0]["icon"] = "rocket"
    assert any("목록 밖" in p for p in validate(bad, payload))

    # 체크리스트는 과거·미래가 모두 있어야 한다
    bad = json.loads(json.dumps(ok))
    for ck in bad["check"]["checks"]:
        ck["done"] = True
    assert any("done=false" in p for p in validate(bad, payload))
    bad = json.loads(json.dumps(ok))
    for ck in bad["check"]["checks"]:
        ck["done"] = False
    assert any("done" in p for p in validate(bad, payload))

    # 슬라이드 조립
    slides = build_slides(ok, payload)
    assert [s["type"] for s in slides] == ["story-cover", "story-facts", "story-issues",
                                           "story-why", "story-check"]
    assert slides[1]["timeline"] == ok["facts"]["timeline"]
    assert slides[4]["checks"] == ok["check"]["checks"]


if __name__ == "__main__":
    sys.exit(main())
