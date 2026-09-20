#!/usr/bin/env python
"""스토리 카드뉴스 — 이슈 하나를 5장으로 푼다.

일일 카드(make_cards.py)가 그날 상위 3건을 한 장씩 훑는 물건이라면, 이건 **며칠에
걸쳐 이어진 이슈 하나**를 표지·타임라인·쟁점·의미·체크리스트로 끝까지 따라간다.

재료는 전부 원장에서 온다. 어느 이슈가 어느 스토리인지, 그 스토리가 자격을
갖췄는지는 `card_context` 가 정하고 — **제목이 아니라 id 로 정한다** — 이 파일은
거기서 받은 사건 목록에 카피를 입힌다. 타임라인은 그 사건들의 날짜·제목,
쟁점·의미는 각 사건의 `implication`·`summary`, 체크리스트는 `open_question`·
`why_important` 다. 그래서 **없는 날짜를 지어낼 자리가 없다** — 프롬프트가 아니라
검증이 그걸 막는다(validate 의 when/숫자 대조).

사건 하나의 재료는 그 사건의 `source_event_id` 에서만 온다. 다른 사건의 기사를
끌어와 한 행을 채우지 않는다(`card_context` 모듈 주석 ③).

    python story_cards.py --date 2026-09-18        # 그날 사이트 순위로 고른다
    python story_cards.py --date ... --dry         # 렌더 없이 카피만 본다
    python story_cards.py --check                  # 검증기 self-check

스토리가 없는 날은 **안 만든다**(exit 0). 부가물이라 억지로 채우지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import card_context
import card_qa

import make_cards as mc

ROOT = mc.ROOT
ALBUM_FILE = ROOT / "cards" / "story_album.json"
# 일일 카드와 PNG 폴더를 나눈다. 같은 cards/out 을 쓰면 나중에 도는 쪽이 앞 앨범을
# 지우고, 그러면 게시·재시도 순서에 따라 엉뚱한 PNG 가 사이트로 간다.
OUT_DIR = ROOT / "cards" / "out-story"


def use_story_out_dir() -> None:
    """렌더 대상을 스토리 폴더로 돌린다. **import 시점이 아니라 여기서 한다.**

    예전에는 이 두 줄이 모듈 맨 위에 있었다. 이 파일이 별도 프로세스로만 불릴
    때는 맞는 자리였는데, `make_cards` 가 스토리 카피를 검증하려고 이 모듈을
    지연 import 하기 시작하면서 **import 만으로 일일 카드의 렌더 대상이 바뀌었다.**

    2026-09-20 실측: 일일 슬라이드가 `cards/out-story/` 로 구워졌고, 장수 게이트는
    빈 `cards/out/` 을 보고 `PNG 장수 불일치: 0 ≠ 5` 로 죽었다. 카피는 논리 2회로
    멀쩡히 나왔는데 워크플로는 빨간불이었다.

    모듈 import 는 다른 모듈의 전역을 건드리지 않는다.
    """
    os.environ.setdefault("CARDS_OUT", OUT_DIR.name)
    mc.OUT_DIR = OUT_DIR

# `MIN_EVENTS = 3` 은 여기 없다. **자격은 사건 수가 아니라 관계가 정한다** —
# 발표 → 시행처럼 단계가 넘어간 두 칸은 이야기이고, 같은 사안을 다섯 번 되풀이한
# 다섯 칸은 이야기가 아니다. 판정은 `card_context.eligibility` 한 곳에 있다.
#
# 남은 숫자들은 **재료의 상한**이다. 프롬프트에 원문을 무한히 밀어 넣지 않는다.
# 규격 숫자와 프롬프트는 `card_editorial` 에 함께 산다 — 프롬프트가 이 숫자를
# 문장으로 적어야 해서 둘이 갈리면 모델이 코드와 다른 규격을 듣는다. 여기서는
# 검증이 쓸 이름만 가져온다(두 벌을 두면 한쪽만 고치는 날이 온다).
from card_editorial import (  # noqa: E402
    ASIDE_MAX, BADGE_LABEL_MAX, BADGE_VALUE_MAX, CHECK_COUNT, CHECK_HEADLINE_MAX,
    CHECK_TEXT_MAX, COVER_DECK_MAX, COVER_HEADLINE_MAX, ISSUE_COUNT, ISSUE_ICONS,
    ISSUE_POINT_MAX, ISSUE_TITLE_MAX, LEDE_MAX, NOTE_MAX, PILLAR_COUNT,
    PILLAR_ICONS, PILLAR_TEXT_MAX, PILLAR_TITLE_MAX, QUOTE_MAX, TIMELINE_ROWS,
    WHAT_MAX, WHEN_MAX, WHY_HEADLINE_MAX,
)


# ---- A. 재료 ------------------------------------------------------------------

def build_payload(candidate: card_context.StoryCandidate, date: str) -> dict:
    """Evidence Packet. 실제 조립은 `card_context` 가 한다.

    여기 두면 make_cards 가 같은 것을 쓰려다 순환 import 가 된다(story_cards 가
    make_cards 를 읽는다). 재료 조립은 원래 card_context 의 일이다.
    """
    return card_context.evidence_packet(candidate, date, topic=mc.topic_label(candidate.issue))


# ---- B. 검증 ------------------------------------------------------------------


_NUM_RE = re.compile(r"[0-9][0-9,.]*")


# 모델은 상한 근처에서 1~2자를 넘긴다(실측: cover.headline 27/26, lede 36/30 —
# 재시도해도 같은 자리에서 걸린다). 그 한 글자 때문에 카드를 통째로 버리면 그날
# 스토리가 안 나간다(폴백이 없는 트랙이다). **길이에만** 2자를 연다 —
# 날짜·숫자 대조에는 여유를 주지 않는다. 그건 지어내기 방지라 성격이 다르다.
LEN_SLACK = 2


def _line(problems: list[str], where: str, text, limit: int, accent_ok: bool = False) -> None:
    if not isinstance(text, str) or not text.strip():
        problems.append(f"{where}: 비어 있음")
        return
    if not accent_ok and "[[" in text:
        problems.append(f"{where}: 강조 표기는 여기 못 쓴다")
    if text.count("[[") > 1:
        problems.append(f"{where}: 강조는 한 곳만")
    if mc.visible_len(text) > limit + LEN_SLACK:
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



def _event_for_row(row: dict, payload: dict) -> dict | None:
    """타임라인 한 행이 가리키는 **그 사건**. 날짜로 찾는다.

    카피는 `source_event_id` 를 적지 않는다(화면에 안 나가는 값을 쓰게 하면
    그것부터 지어낸다). 대신 행의 `when` 이 어느 사건의 날짜인지로 되짚는다 —
    날짜는 이미 "입력 events 의 것만" 으로 검증되는 값이다.
    """
    when = str(row.get("when") or "")
    for event in payload.get("events") or ():
        stamp = str(event.get("date") or "")
        if not stamp:
            continue
        year, month, day = stamp[:4], int(stamp[5:7]), int(stamp[8:10])
        for form in (stamp, f"{year}년 {month}월 {day}일", f"{month}월 {day}일",
                     f"{year}.{month:02d}.{day:02d}"):
            if form in when or when in form:
                return event
    return None


def _fit(text, limit: int) -> str:
    """상한 안으로. 절 경계에서 끊고, 못 끊으면 그대로 둔다(검증이 잡는다)."""
    if not isinstance(text, str):
        return text
    return text if mc.visible_len(text) <= limit else mc.clip(text, limit)


def normalize(raw: dict, payload: dict) -> dict:
    """LLM 출력을 카드 규격으로 다듬는다. **버리는 것과 고치는 것을 가른다.**

    길이 초과는 고친다 — 한 글자 넘겼다고 그날 스토리를 통째로 빼는 건 손해다
    (실측: cover.headline 이 26자 상한에서 29~32자로 세 번 연속 걸렸다).
    반대로 **입력에 없는 날짜·숫자는 고치지 않고 버린다** — 그건 길이 문제가
    아니라 지어낸 것이고, 이 카드의 존재 이유가 '날짜를 지어낼 자리가 없다'는
    점이기 때문이다. 아래는 전부 자르기이고, 지어내기 판정은 validate 가 한다.
    """
    if not isinstance(raw, dict):
        return raw
    out = json.loads(json.dumps(raw, ensure_ascii=False))
    cover = out.get("cover") or {}
    cover["headline"] = _fit(cover.get("headline"), COVER_HEADLINE_MAX)
    cover["deck"] = _fit(cover.get("deck"), COVER_DECK_MAX)
    if isinstance(cover.get("badge"), dict):
        cover["badge"]["label"] = _fit(cover["badge"].get("label"), BADGE_LABEL_MAX)

    facts = out.get("facts") or {}
    facts["lede"] = _fit(facts.get("lede"), LEDE_MAX)
    if facts.get("note"):
        facts["note"] = _fit(facts["note"], NOTE_MAX)
    known = _dates_in(payload)
    rows = [r for r in (facts.get("timeline") or []) if isinstance(r, dict)]
    if known:
        # 입력에 없는 날짜의 행은 **버린다**(자르지 않는다). 모델이 events 가
        # 모자랄 때 "현재 (9월 9일)" 같은 행을 만들어 채우는 것을 봤다.
        rows = [r for r in rows
                if any(k in str(r.get("when") or "") or str(r.get("when") or "") in k
                       for k in known)]
    want = min(TIMELINE_ROWS, len(payload.get("events") or [])) or TIMELINE_ROWS
    for r in rows:
        r["what"] = _fit(r.get("what"), WHAT_MAX)
        r["when"] = _fit(r.get("when"), WHEN_MAX)
    facts["timeline"] = rows[:want]

    for it in (out.get("issues") or []):
        if isinstance(it, dict):
            it["title"] = _fit(it.get("title"), ISSUE_TITLE_MAX)
            it["points"] = [_fit(t, ISSUE_POINT_MAX) for t in (it.get("points") or [])][:2]

    why = out.get("why") or {}
    why["headline"] = _fit(why.get("headline"), WHY_HEADLINE_MAX)
    if why.get("quote"):
        why["quote"] = _fit(why["quote"], QUOTE_MAX)
    for pl in (why.get("pillars") or []):
        if isinstance(pl, dict):
            pl["title"] = _fit(pl.get("title"), PILLAR_TITLE_MAX)
            pl["text"] = _fit(pl.get("text"), PILLAR_TEXT_MAX)

    check = out.get("check") or {}
    check["headline"] = _fit(check.get("headline"), CHECK_HEADLINE_MAX)
    for it in (check.get("checks") or []):
        if isinstance(it, dict):
            it["text"] = _fit(it.get("text"), CHECK_TEXT_MAX)
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
        #
        # 예전에는 `num in haystack` 이었다. 부분 문자열이라 **"17" 이 "170" 안에
        # 있다고 근거로 인정됐다.** 값과 단위를 한 덩어리로 묶어 비교한다.
        haystack = json.dumps(payload, ensure_ascii=False)
        for problem in card_qa.ungrounded(badge.get("value"), haystack):
            problems.append(f"cover.badge.value: {problem}")

    facts = raw.get("facts") or {}
    _line(problems, "facts.lede", facts.get("lede"), LEDE_MAX, accent_ok=True)
    if facts.get("note"):
        _line(problems, "facts.note", facts.get("note"), NOTE_MAX)
    timeline = facts.get("timeline")
    # events 가 4건 미만인 스토리도 있다 — 그럴 땐 있는 만큼이 정답이다.
    # v1 은 정확히 4행을 요구했다. v2 에서는 normalize 가 **입력에 없는 날짜의 행을
    # 버리기** 때문에 3행으로 내려올 수 있다(모델이 "현재 (9월 9일)" 같은 행을 즐겨
    # 만든다). 지어낸 행을 지우고 남은 3행이 4행을 채운 거짓말보다 낫다 — 범위로 본다.
    want = min(TIMELINE_ROWS, len(payload.get("events") or [])) or TIMELINE_ROWS
    lo = 2 if want > 2 else want
    if not isinstance(timeline, list) or not lo <= len(timeline) <= want:
        problems.append(f"facts.timeline: {len(timeline) if isinstance(timeline, list) else '?'}개 "
                        f"— {lo}~{want}개여야 한다")
    else:
        known = _dates_in(payload)
        for i, rowx in enumerate(timeline, start=1):
            if not isinstance(rowx, dict):
                problems.append(f"facts.timeline[{i}]: 객체가 아님")
                continue
            _line(problems, f"facts.timeline[{i}].when", rowx.get("when"), WHEN_MAX)
            _line(problems, f"facts.timeline[{i}].what", rowx.get("what"), WHAT_MAX)
            # **한 행은 자기 사건만 인용한다.** 행의 날짜로 어느 event 인지
            # 정하고, 그 event 의 재료에 없는 숫자를 쓰면 다른 날 사건의 근거를
            # 끌어온 것이다(cross-event leakage). 스토리 전체의 '왜 중요한가'만
            # 여러 사건을 함께 인용할 수 있다.
            own = _event_for_row(rowx, payload)
            if own is not None:
                material = json.dumps(own, ensure_ascii=False)
                for problem in card_qa.ungrounded(rowx.get("what"), material):
                    problems.append(f"facts.timeline[{i}].what: {problem} "
                                    f"(이 행은 {own.get('date')} 사건이다)")
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


def load_story_copy(date: str) -> tuple[dict, dict] | None:
    """make_cards 가 남긴 (evidence packet, 카피). 없으면 오늘은 스토리가 없다.

    **여기서 LLM 을 부르지 않는다.** 그날의 편집 판단은 make_cards 가 이미
    한 번 했고(`card_editorial` 모듈 주석), 이 스크립트가 다시 부르면 같은 날
    두 산출물이 서로 다른 판단 위에 서게 된다.
    """
    if not mc.STORY_COPY_FILE.exists():
        return None
    try:
        saved = json.loads(mc.STORY_COPY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[story] {mc.STORY_COPY_FILE.name} 을 읽지 못했다: {exc}")
        return None
    if str(saved.get("date") or "") != date:
        print(f"[story] 저장된 카피는 {saved.get('date')} 것이다 — 오늘({date}) 것이 아니다")
        return None
    payload, copy = saved.get("payload"), saved.get("copy")
    if not isinstance(payload, dict) or not isinstance(copy, dict):
        print("[story] 저장된 카피의 모양이 아니다")
        return None
    return payload, copy


# ---- D. main ------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="이 날짜의 스토리 카피를 렌더한다")
    ap.add_argument("--dry", action="store_true", help="렌더 없이 카피만 본다")
    ap.add_argument("--copy-file", type=Path, help="사람이 쓴 카피 JSON(같은 검증을 거친다)")
    ap.add_argument("--check", action="store_true", help="검증기 self-check")
    args = ap.parse_args()
    if args.check:
        _self_check()
        print("self-check OK")
        return 0

    date = args.date or mc.datetime.now(mc.KST).strftime("%Y-%m-%d")
    saved = load_story_copy(date)
    if saved is None:
        print(f"[story] {date}: 오늘 스토리 카피가 없다 — 카드 안 만든다")
        return 0
    payload, raw = saved
    if args.copy_file:
        raw = json.loads(args.copy_file.read_text(encoding="utf-8"))
    print(f"[story] {payload['issue_title'][:36]} | thread={payload.get('thread_id')} "
          f"| 사건 {len(payload['events'])}건 | 관전 {len(payload['watchpoints'])}건")

    raw = normalize(raw, payload)
    problems = validate(raw, payload)
    if problems:
        # 폴백 카피를 만들지 않는다. 재료를 기계적으로 이어 붙이면 타임라인이
        # 그럴듯한 거짓말이 된다. 스토리만 빠지고 일일 카드는 이미 나갔다.
        print("[story] 카피 검증 실패: " + "; ".join(problems[:8]))
        return 0


    slides = build_slides(raw, payload)
    if args.dry:
        print(json.dumps(slides, ensure_ascii=False, indent=1))
        return 0
    use_story_out_dir()
    mc.render(slides)
    files = mc.gate(len(slides))
    plain = raw["cover"]["headline"].replace("[[", "").replace("]]", "")
    caption = "\n".join([f"[스토리] {plain}", raw["cover"]["deck"], "", mc.SITE])
    ALBUM_FILE.write_text(json.dumps({
        "date": date, "issue": payload["issue_title"],
        "caption": caption,
        "thread_id": payload.get("thread_id", ""),
        "issue_id": payload.get("issue_id", ""),
        "files": [str(f.relative_to(ROOT)).replace("\\", "/") for f in files],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[story] {len(files)}장 준비 완료 → {ALBUM_FILE.name}")
    return 0


def _self_check() -> None:
    """runnable check — 검증기가 실제로 막는지 본다."""
    payload = {"topic": "해외사업",
               # 타임라인 4행 규격을 그대로 검사하려면 이벤트도 4건이어야 한다
               # (events 가 더 적은 날은 그 개수만큼만 쓴다 — 아래에서 따로 본다).
               "events": [{"date": "2026-09-08", "title": "미국, 원전 8기 제안"},
                          {"date": "2026-09-12", "title": "산업부, 협상 진행 확인"},
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
    # 길이는 2자까지 봐준다(LEN_SLACK) — 그 안쪽은 통과하고 normalize 가 잘라 넣는다.
    edge = mut("cover", headline="가" * (COVER_HEADLINE_MAX + LEN_SLACK))
    assert not any("cover.headline" in p for p in validate(edge, payload)), "여유 안쪽은 통과"
    fixed = normalize(edge, payload)
    assert mc.visible_len(fixed["cover"]["headline"]) <= COVER_HEADLINE_MAX, fixed["cover"]["headline"]
    # 지어낸 날짜 행은 자르는 게 아니라 **버린다**.
    invented = json.loads(json.dumps(ok))
    invented["facts"]["timeline"].append({"when": "현재 (9월 9일)", "what": "협상 계속"})
    assert len(normalize(invented, payload)["facts"]["timeline"]) == TIMELINE_ROWS, "지어낸 행 제거"
    bad = mut("cover", headline="가" * (COVER_HEADLINE_MAX + LEN_SLACK + 1))
    assert any("cover.headline" in p for p in validate(bad, payload))
    bad = json.loads(json.dumps(ok)); bad["issues"] = bad["issues"][:2]
    assert any("issues" in p for p in validate(bad, payload))
    # 3행까지는 봐준다(지어낸 행을 버린 결과일 수 있다). 2행 미만이면 타임라인이 아니다.
    assert not any("timeline" in p for p in
                   validate({**ok, "facts": {**ok["facts"],
                                             "timeline": ok["facts"]["timeline"][:3]}}, payload))
    bad = json.loads(json.dumps(ok)); bad["facts"]["timeline"] = bad["facts"]["timeline"][:1]
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
