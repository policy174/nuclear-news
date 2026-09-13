#!/usr/bin/env python3
"""카드뉴스 생성 — 오늘 발송분(outbox) → slides.json → PNG → 검증.

파이프라인 A·B·C·D 를 한 파일에 담는다 (발송은 send_album.py).

    A  카드 소재 선정: outbox.items 에서 must_read 우선 1~3건
    B  Gemini 1회 호출 → 카피 생성 + 코드 검증 (실패 시 1회 재시도)
    C  node cards/build.js → cards/out/slide-NN.png
    D  PNG 게이트: 장수·파일명 연속성·최소 바이트

**이 스크립트는 실패해도 텍스트 브리핑을 막지 않는다.** 워크플로에서
비치명 스텝으로 부르고, 여기서는 실패를 정직하게 exit 1 로 알린다
(`|| echo "..."` 로 삼키는 쪽은 호출자다 — 종료 코드를 0 으로 만들지 말 것).

    python make_cards.py            # 오늘 outbox 기준
    python make_cards.py --dry-run  # LLM·렌더까지 하고 album.json 만 남김(같음)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gemini_client
import sources

ROOT = Path(__file__).parent
CARDS_DIR = ROOT / "cards"
OUT_DIR = CARDS_DIR / "out"
SLIDES_FILE = CARDS_DIR / "slides.json"
ALBUM_FILE = CARDS_DIR / "album.json"
OUTBOX_FILE = ROOT / "outbox.json"
CURATED_FILE = ROOT / "curated.json"

KST = timezone(timedelta(hours=9))

MAX_CARDS = 3
# 지시서는 20자였는데 실측상 통과가 안 난다 — 원안위·호기명이 들어간 국내 정책
# 헤드라인은 21~26자에서 수렴하고, 2회 재시도가 모두 길이로 죽었다.
# 68px·자간 -1 에서 한 줄에 약 14자가 들어가므로 28자 = 정확히 두 줄이다.
# 프롬프트 목표는 20자로 두고, 코드 한계만 두 줄 폭에 맞춘다.
HEADLINE_TARGET = 20
HEADLINE_MAX = 28
SUBLINE_MAX = 50  # 45자 목표, 한 문장이 한 글자 차이로 버려지는 걸 막는 여유
MIN_PNG_BYTES = 20_000  # 1080×1440 그라디언트 빈 카드가 대략 20KB. 그 아래면 빈 렌더.

SITE = "nuclens.pages.dev"

# 사내 현안집 '표준 주제 축'(news_bot.py 큐레이션 프롬프트 (3)번) 을 그대로 쓴다.
# 새 분류 체계를 만들지 않는다.
TAGS = ("안전성", "전원계획", "계속운전", "경제성", "사후처리", "수용성", "거시·산업")

# sensitivity: 사고·안전·재난·국가갈등. 여기 걸리면 [[ ]] 강조와 수사를 금지한다.
SENSITIVE_WORDS = (
    "사고", "피폭", "누출", "화재", "폭발", "지진", "사망", "부상", "재난",
    "오염수", "분쟁", "제재", "갈등", "고발", "소송",
)

SYSTEM_PROMPT = f"""너는 한국수력원자력 원자력정책실의 일일 카드뉴스 카피라이터다.
아래 기사들로 카드뉴스 문구를 만든다.

출력 형식(JSON 객체 하나):
{{"hook": {{"headline": "...", "subline": "..."}},
  "steps": [{{"stepLabel": "...", "headline": "...", "subline": "..."}}]}}

- steps 는 입력 기사와 **같은 개수·같은 순서**로 만든다. 하나도 빠뜨리지 않는다.
- hook.headline: 오늘 전체를 관통하는 한 줄 판단. 한글 {HEADLINE_TARGET}자 이내(최대 {HEADLINE_MAX}자, 넘기면 버려진다).
- steps[].headline: 그 기사의 사건 요약. 같은 길이 규칙.
- 모든 subline: 왜 중요한가를 담은 **한 문장**, {SUBLINE_MAX}자 이내.
- steps[].stepLabel: 다음 중 정확히 하나 — {", ".join(TAGS)}
- 강조는 headline 당 최대 한 곳만 `[[대괄호]]` 로 감싼다. subline 에는 쓰지 않는다.
- 숫자·호기명·국가명·기관명은 원문 그대로 옮긴다. 반올림·추정·의역 금지.
- 입력 기사에 sensitive=true 가 붙었으면 그 카드는 `[[ ]]` 강조와 수사적 표현을
  쓰지 않는다. 사실 서술만.
- 사람인 척하는 페르소나·감탄사·이모지 금지. 개조식 체언 종결을 기본으로 한다.
- 글자 수는 코드로 다시 잰다. 넘기면 통째로 버려지니 짧게 쓴다."""


# ---- A. 카드 소재 선정 --------------------------------------------------------


def is_sensitive(item: dict) -> bool:
    text = f"{item.get('title_kr', '')} {item.get('summary', '')}"
    return any(w in text for w in SENSITIVE_WORDS)


def source_name(link: str) -> str:
    """매체·기관 표시명. 화이트리스트에 없으면 도메인 그대로."""
    hit = sources.credibility({"url": link}).get("name")
    return hit or sources.registered_domain(link) or ""


def pick_items(outbox: dict, curated: dict, k: int = MAX_CARDS) -> list[dict]:
    """outbox 발송분 중 must_read 우선 상위 k 건. 원문 링크 없는 건은 제외."""
    picked = []
    for item in outbox.get("items", []):
        meta = curated.get(item.get("hash"), {})
        link = (meta.get("link") or "").strip()
        if not link:
            continue  # 출처 미확인 — 카드에서 빼고 텍스트 브리핑으로만 (§5)
        picked.append({
            "hash": item["hash"],
            "title": item.get("title_kr", ""),
            "summary": item.get("summary", ""),
            "link": link,
            "importance": meta.get("importance", "nice_to_know"),
            "score": float(item.get("score") or 0),
            "sensitive": is_sensitive(item),
            # 카드 아래 칩. 정책 카드에선 '언제 누가'가 장식이 아니라 본문이다.
            "event_date": (item.get("event_date") or "").replace("-", "."),
            "source": source_name(link),
            "tag": next(iter(item.get("tags") or []), ""),
        })
    picked.sort(key=lambda x: (x["importance"] != "must_read", -x["score"]))
    return picked[:k]


# ---- B. 카피 생성 + 검증 ------------------------------------------------------


def visible_len(text: str) -> int:
    """화면에 보이는 글자 수. `[[ ]]` 네 글자는 마크업이라 세지 않는다."""
    return len(text.replace("[[", "").replace("]]", ""))


def ask_llm(items: list[dict], date: str, total_collected: int,
            problems: list[str] | None = None) -> dict:
    payload = {
        "date": date,
        "collected_today": total_collected,
        "articles": [
            {"n": i + 1, "title": it["title"], "summary": it["summary"],
             "sensitive": it["sensitive"]}
            for i, it in enumerate(items)
        ],
    }
    if problems:
        # 재시도에 실패 사유를 그대로 돌려준다. LLM 은 한글 글자 수를 못 세므로
        # "짧게 써라" 를 반복하는 것보다 "이 문장이 29자였다" 가 훨씬 잘 듣는다.
        payload["fix_these"] = problems
    # thinking_budget=0 — 정형 출력이라 사고가 필요 없고, thinking 토큰이 출력
    # 예산을 잠식하면 MAX_TOKENS 로 잘린다 (gemini_client 주석 참고).
    return gemini_client.call_json(
        SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False, indent=1),
        temperature=0.3,
        max_output_tokens=2048,
        thinking_budget=0,
        label="cards",
    )


def validate(raw: dict, items: list[dict]) -> list[str]:
    """LLM 출력 검증. 문제 목록을 반환 — 비어 있으면 통과.

    "JSON only" 라고 써도 LLM 은 글자 수를 못 세고 태그를 지어낸다. 코드로 잰다.
    (코드펜스·머리말 제거는 gemini_client.call_json 이 이미 한다.)
    """
    problems: list[str] = []
    hook = raw.get("hook")
    steps = raw.get("steps")
    if not isinstance(hook, dict):
        problems.append("hook 없음")
    if not isinstance(steps, list):
        return problems + ["steps 가 배열이 아님"]
    if len(steps) != len(items):
        problems.append(f"steps 개수 {len(steps)} ≠ 기사 {len(items)}")

    for name, slide in [("hook", hook)] + [(f"step{i+1}", s) for i, s in enumerate(steps)]:
        if not isinstance(slide, dict):
            problems.append(f"{name}: 객체가 아님")
            continue
        head = slide.get("headline")
        sub = slide.get("subline")
        if not head or not isinstance(head, str):
            problems.append(f"{name}: headline 없음")
        elif visible_len(head) > HEADLINE_MAX:
            problems.append(f"{name}: headline {visible_len(head)}자 > {HEADLINE_MAX}")
        elif head.count("[[") != head.count("]]") or head.count("[[") > 1:
            problems.append(f"{name}: 강조 표기 오류")
        if not sub or not isinstance(sub, str):
            problems.append(f"{name}: subline 없음")
        elif visible_len(sub) > SUBLINE_MAX:
            problems.append(f"{name}: subline {visible_len(sub)}자 > {SUBLINE_MAX}")
        if name != "hook" and slide.get("stepLabel") not in TAGS:
            problems.append(f"{name}: stepLabel '{slide.get('stepLabel')}' 은 허용 태그 아님")

    # sensitive 기사에 강조·수사 금지
    for i, (slide, item) in enumerate(zip(steps, items)):
        if item["sensitive"] and isinstance(slide, dict) and "[[" in str(slide.get("headline", "")):
            problems.append(f"step{i+1}: sensitive 기사에 강조 사용")
    return problems


def build_slides(raw: dict, items: list[dict], date: str) -> list[dict]:
    """검증 통과한 카피 → build.js 가 먹는 slides 배열.

    원문 URL 은 LLM 이 아니라 여기서 붙인다 — 긴 URL 을 LLM 에 베끼게 하면
    오타가 난다. steps 와 items 는 개수·순서가 검증된 뒤다.
    """
    total = len(items) + 2  # hook 1 + step N + cta 1
    slides = [{
        "type": "hook",
        "slideNum": f"01 / {total:02d}",
        "stepLabel": "NUCLENS 브리핑",
        "date": date.replace("-", "."),
        "label": "원자력 정책 브리핑",
        # 커버 목차 — 앨범에 뭐가 들었는지 첫 장에서 보여준다
        "toc": [c["headline"].replace("[[", "").replace("]]", "")
                for c in raw["steps"]],
        "headline": raw["hook"]["headline"],
        "subline": raw["hook"]["subline"],
        "handle": SITE,
    }]
    for i, (copy, item) in enumerate(zip(raw["steps"], items), start=2):
        slides.append({
            "type": "step",
            "slideNum": f"{i:02d} / {total:02d}",
            "idx": f"{i - 1:02d}",
            "stepLabel": copy["stepLabel"],
            "headline": copy["headline"],
            "subline": copy["subline"],
            "meta": [m for m in (item["event_date"], item["tag"]) if m],
            "handle": SITE,
            "footer": item["source"],
            "url": item["link"],   # build.js 는 안 쓴다 — 캡션·검증용
        })
    slides.append({
        "type": "cta",
        "slideNum": f"{total:02d} / {total:02d}",
        "stepLabel": "NUCLENS",
        "headline": "전체 보기",
        "subline": "오늘 브리핑 전문과 지난 이슈 흐름",
        "keyword": SITE,
        "handle": "매일 07:25 발송",   # 알약이 이미 주소라 꼬리말까지 주소면 세 번이다
        "footer": date.replace("-", "."),
    })
    return slides


# ---- C. 렌더 ------------------------------------------------------------------


def render(slides: list[dict]) -> None:
    if OUT_DIR.exists():
        for old in OUT_DIR.glob("slide-*.png"):
            old.unlink()  # 어제 PNG 가 남아 장수 검증을 속이면 안 된다
    SLIDES_FILE.write_text(json.dumps(slides, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    # build.js 는 theme.json·out/ 을 cwd 기준으로 찾는다.
    subprocess.run([_node(), "build.js", "slides.json"], cwd=CARDS_DIR, check=True)


def _node() -> str:
    return "node"


# ---- D. PNG 게이트 ------------------------------------------------------------


def gate(expected: int) -> list[Path]:
    """장수·파일명 연속성·최소 바이트. 하나라도 어긋나면 예외."""
    files = [OUT_DIR / f"slide-{i:02d}.png" for i in range(1, expected + 1)]
    actual = sorted(OUT_DIR.glob("slide-*.png"))
    if len(actual) != expected:
        raise RuntimeError(f"PNG 장수 불일치: {len(actual)} ≠ {expected}")
    for f in files:
        if not f.exists():
            raise RuntimeError(f"PNG 누락: {f.name} (파일명 연속성 깨짐)")
        size = f.stat().st_size
        if size < MIN_PNG_BYTES:
            raise RuntimeError(f"PNG 너무 작음: {f.name} {size}B < {MIN_PNG_BYTES}B "
                               "(빈 렌더 의심)")
    return files


# ---- 캡션 ---------------------------------------------------------------------


def build_caption(slides: list[dict], date: str) -> str:
    """앨범 첫 장에 붙는 캡션. 원문 링크는 카드가 아니라 여기에 담는다."""
    import html

    lines = [f"🗂 <b>{date} Nuclens 카드 브리핑</b>", ""]
    for i, s in enumerate([x for x in slides if x["type"] == "step"], start=1):
        title = html.escape(s["headline"].replace("[[", "").replace("]]", ""))
        lines.append(f"{i}. {title} · <a href=\"{html.escape(s['url'], quote=True)}\">원문</a>")
    lines += ["", f"전체 보기 · {SITE}"]
    caption = "\n".join(lines)
    return caption[:1024]  # 텔레그램 캡션 한도


# ---- main ---------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="오늘 이미 카드를 보냈어도 다시 만든다")
    args = ap.parse_args()

    if not OUTBOX_FILE.exists():
        print("[cards] outbox.json 없음 — 브리핑이 아직 안 돌았다. 스킵")
        return 0
    outbox = json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
    date = outbox.get("date") or datetime.now(KST).strftime("%Y-%m-%d")

    if outbox.get("status") not in ("sent", "partial"):
        print(f"[cards] 텍스트 브리핑 상태 '{outbox.get('status')}' — 카드 스킵")
        return 0
    if not args.force and (outbox.get("cards") or {}).get("date") == date:
        print(f"[cards] {date} 카드는 이미 발송됨 — 스킵")
        return 0

    curated = json.loads(CURATED_FILE.read_text(encoding="utf-8"))
    items = pick_items(outbox, curated)
    if not items:
        print("[cards] 카드로 낼 이슈 없음 — 텍스트 브리핑만. 억지로 채우지 않는다")
        return 0
    print(f"[cards] 소재 {len(items)}건: " +
          " / ".join(f"{i['importance']} {i['title'][:24]}" for i in items))

    collected = sum(s.get("candidate_count", 0)
                    for s in (outbox.get("selection_stats") or {}).values()
                    if isinstance(s, dict))

    raw = None
    last_problems: list[str] = []
    for attempt in (1, 2):
        try:
            candidate = ask_llm(items, date, collected, problems=last_problems)
        except Exception as exc:  # noqa: BLE001 — 카드는 부가 기능, 원인만 남긴다
            print(f"[cards] LLM 호출 실패 ({attempt}/2) — {type(exc).__name__}: {exc}")
            continue
        last_problems = validate(candidate, items)
        if not last_problems:
            raw = candidate
            break
        print(f"[cards] 카피 검증 실패 ({attempt}/2): {'; '.join(last_problems[:6])}")
    if raw is None:
        print("[cards] 2회 실패 — 카드를 건너뛰고 텍스트 브리핑만 나간다")
        return 1

    slides = build_slides(raw, items, date)
    render(slides)
    files = gate(len(slides))

    ALBUM_FILE.write_text(json.dumps({
        "date": date,
        "caption": build_caption(slides, date),
        "files": [str(f.relative_to(ROOT)).replace("\\", "/") for f in files],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[cards] {len(files)}장 준비 완료 → {ALBUM_FILE.name}")
    return 0


def _self_check() -> None:
    """runnable check — 검증기가 실제로 막는지 본다. `python make_cards.py --check`"""
    items = [{"title": "a", "summary": "", "link": "http://x", "sensitive": False,
              "importance": "must_read", "score": 1.0, "hash": "h"}]
    ok = {"hook": {"headline": "짧은 판단", "subline": "한 문장."},
          "steps": [{"stepLabel": "계속운전", "headline": "[[원안위]] 심의",
                     "subline": "왜 중요한지 한 문장."}]}
    assert validate(ok, items) == [], validate(ok, items)

    long_head = dict(ok, steps=[dict(ok["steps"][0], headline="가" * (HEADLINE_MAX + 1))])
    assert any("headline" in p for p in validate(long_head, items))

    # [[ ]] 는 글자 수에서 빠진다 — 정확히 20자면 통과해야 한다
    edge = dict(ok, steps=[dict(ok["steps"][0], headline="[[" + "가" * HEADLINE_MAX + "]]")])
    assert validate(edge, items) == [], validate(edge, items)

    bad_tag = dict(ok, steps=[dict(ok["steps"][0], stepLabel="아무거나")])
    assert any("stepLabel" in p for p in validate(bad_tag, items))

    count = dict(ok, steps=ok["steps"] * 2)
    assert any("개수" in p for p in validate(count, items))

    sensitive = [dict(items[0], sensitive=True)]
    assert any("sensitive" in p for p in validate(ok, sensitive))

    assert visible_len("[[가나]]다") == 3
    print("self-check OK")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _self_check()
    else:
        sys.exit(main())
