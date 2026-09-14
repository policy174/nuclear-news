#!/usr/bin/env python3
"""카드뉴스 생성 — 오늘 발송분(outbox) → slides.json → PNG → 검증.

파이프라인 A·B·C·D 를 한 파일에 담는다 (발송은 send_album.py).

    A  카드 소재 선정 + 재료 확보
    B  Gemini 1회 호출 → 카피 생성 + 코드 검증 (실패 시 1회 재시도)
    C  node cards/build.js → cards/out/slide-NN.png
    D  PNG 게이트: 장수·파일명 연속성·최소 바이트

**이 스크립트는 실패해도 텍스트 브리핑을 막지 않는다.** 워크플로에서
비치명 스텝으로 부르고, 여기서는 실패를 정직하게 exit 1 로 알린다
(`|| echo "..."` 로 삼키는 쪽은 호출자다 — 종료 코드를 0 으로 만들지 말 것).

    python make_cards.py            # 오늘 outbox 기준
    python make_cards.py --check    # LLM 없이 검증기 자체 점검
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

# 기사 1건 = 카드 1장. 한 주제는 한 장 안에서 끝낸다 — 사실 불릿과 "왜 중요한가"
# 를 같은 장의 서로 다른 블록으로 나눠 담는다. 표지 1 + N + 마지막 1 = N+2 장.
MAX_CARDS = 3
TELEGRAM_ALBUM_MAX = 10

# 지시서는 20자였는데 실측상 통과가 안 난다 — "고리 3·4호기, 한빛 1·2호기 계속운전
# 심의 착수" 처럼 호기명이 둘 들어가면 29~35자에서 수렴하고, 재시도를 먹여도
# 호기명을 버리지 않는 한 못 줄인다(실측 2026-09-14: 두 번 다 29자).
# 한 글자 차이로 앨범 전체가 죽는 게 더 나쁘다.
#
# 80px·자간 -1.5 에서 한 줄에 약 14자. 34자면 세 줄인데, 불릿 카드는 세 줄도
# 들어간다(본문 칸 1190px 중 헤드라인 283 + 불릿 218 + 칩 60). **진짜 한계는
# 이 숫자가 아니라 build.js 의 넘침 가드다** — 실제 사각형을 재서 넘치면 죽인다.
# 여기 숫자는 렌더를 낭비하지 않기 위한 사전 거름망이다.
HEADLINE_TARGET = 20
HEADLINE_MAX = 34
SUBLINE_MAX = 50   # 표지 부제
FACT_MAX = 34      # 사실 불릿 한 줄
WHY_MAX = 40       # 의미 불릿 한 줄
# 한 장에 둘 다 들어가므로 각각 3개까지. 넘치는지는 build.js 넘침 가드가 잰다.
BULLETS_MIN, BULLETS_MAX = 2, 3

# curated 의 detail 이 이보다 짧으면 그 기사만 원문을 다시 탄다.
# 원문 본문은 저작권 계약상 저장하지 않는다(article_body.py:370) — 남는 건
# 큐레이션이 본문에서 뽑아둔 detail·why_important·implication·open_question 이다.
# 그래서 순서는 "기존 추출 결과 먼저, 결손일 때만 재수집" 이다.
THIN_DETAIL_CHARS = 120
BODY_CHARS_FOR_PROMPT = 1200

MIN_PNG_BYTES = 20_000  # 1080×1440 그라디언트 빈 카드가 대략 20KB. 그 아래면 빈 렌더.

SITE = "nuclens.pages.dev"
DELIVERY_NOTE = "크롤 완료 직후 발송"  # cron 고정 시각이 아니다 (daily-brief.yml 주 경로 = workflow_run)

# 사내 현안집 '표준 주제 축'(news_bot.py 큐레이션 프롬프트 (3)번) 을 그대로 쓴다.
# 새 분류 체계를 만들지 않는다.
TAGS = ("안전성", "전원계획", "계속운전", "경제성", "사후처리", "수용성", "거시·산업")

# sensitivity: 사고·안전·재난. 여기 걸리면 [[ ]] 강조와 수사적 표현을 금지한다.
#
# 어휘 부분일치로 판정하면 안 된다 — "사고" 가 "사고관리계획서" 에 걸려
# 계속운전 규제 기사가 사고 기사로 잡혔다(실측 2026-09-14). 큐레이션이 기사마다
# 이미 매겨둔 event_type 을 쓴다. 보조 어휘는 부분일치 사고가 없는 것만 남긴다.
SENSITIVE_EVENT_TYPES = {"incident_safety"}
SENSITIVE_WORDS = ("피폭", "방사능 누출", "INES", "중대재해")

SYSTEM_PROMPT = f"""너는 한국수력원자력 원자력정책실의 일일 카드뉴스 카피라이터다.
기사 1건당 카드 **한 장**을 만든다. 한 장 안에 ①무슨 일이 있었나(사실 불릿)
②왜 중요한가(의미 불릿)를 둘 다 담는다.

출력 형식(JSON 객체 하나):
{{"hook": {{"headline": "..."}},
  "steps": [{{"stepLabel": "...", "headline": "...",
             "facts": ["...", "..."], "why": ["...", "..."]}}]}}

- steps 는 입력 기사와 **같은 개수·같은 순서**로 만든다. 하나도 빠뜨리지 않는다.
- hook.headline: 오늘 전체를 관통하는 한 줄 판단. 한글 {HEADLINE_TARGET}자 이내
  (최대 {HEADLINE_MAX}자, 넘기면 버려진다). 표지 부제는 코드가 만드니 쓰지 않는다.
- steps[].headline: 그 기사에서 **무슨 일이 있었나**. 같은 길이 규칙.
- steps[].facts: {BULLETS_MIN}~{BULLETS_MAX}개, 각 {FACT_MAX}자 이내. **날짜·기관·대상·결정·수치**처럼
  원문에 적힌 구체값만. 해석·전망·형용사 금지. 개조식 체언 종결.
  예) "9월 11일 제2026-14회 회의" / "2건 의결, 1건 재상정"
- steps[].why: {BULLETS_MIN}~{BULLETS_MAX}개, 각 {WHY_MAX}자 이내. 정책 영향 / 한수원 시사점 /
  다음 확인사항 순서를 권장한다. 입력의 why_important·implication·open_question 을
  재료로 쓰되 그대로 베끼지 말고 한 줄로 줄인다.
- steps[].stepLabel: 다음 중 정확히 하나 — {", ".join(TAGS)}
- 강조는 headline 에만 최대 한 곳 `[[대괄호]]`. 불릿에는 쓰지 않는다.
- 숫자·호기명·국가명·기관명은 원문 그대로 옮긴다. 반올림·추정·의역 금지.
  **입력에 없는 수치·날짜를 지어내지 않는다.** 재료가 부족하면 불릿 수를 줄인다.
- 입력 기사에 sensitive=true 가 붙었으면 `[[ ]]` 강조와 수사적 표현을 쓰지 않는다.
  사실 서술만.
- 사람인 척하는 페르소나·감탄사·이모지 금지. 개조식 체언 종결을 기본으로 한다.
- 글자 수는 코드로 다시 잰다. 넘기면 통째로 버려지니 짧게 쓴다."""


# ---- A. 카드 소재 선정 + 재료 확보 ---------------------------------------------


def is_sensitive(item: dict, meta: dict) -> bool:
    if (meta.get("features") or {}).get("event_type") in SENSITIVE_EVENT_TYPES:
        return True
    text = f"{item.get('title_kr', '')} {item.get('summary', '')}"
    return any(w in text for w in SENSITIVE_WORDS)


def source_name(link: str) -> str:
    """매체·기관 표시명. 화이트리스트에 없으면 도메인 그대로."""
    hit = sources.credibility({"url": link}).get("name")
    return hit or sources.registered_domain(link) or ""


def pick_items(outbox: dict, curated: dict, k: int = MAX_CARDS) -> list[dict]:
    """카드 레이어의 선별. 기존 랭킹이 **발송하기로 정한 것** 안에서만 고른다.

    중복 제거·주제 다양성 감점·지역별 캡은 이미 ranking 단계에서 끝났다. 여기서
    새로 하는 일은 세 가지뿐이다: ①원문 링크 없는 건 제외 ②must_read 우선
    ③점수순 상위 k. 국내·해외를 합쳐 전역 정렬하므로 **기존 지역 안배는 유지되지
    않는다** — 3장 전부 해외가 될 수 있다. 의도된 단순화다.
    # ponytail: 지역 안배가 필요해지면 국내/해외 각각에서 뽑아 교대로 배치할 것
    """
    picked = []
    for item in outbox.get("items", []):
        meta = curated.get(item.get("hash"), {})
        link = (meta.get("link") or "").strip()
        if not link:
            continue  # 출처 미확인 — 카드에서 빼고 텍스트 브리핑으로만
        picked.append({
            "hash": item["hash"],
            "title": item.get("title_kr", ""),
            "summary": item.get("summary", ""),
            # 큐레이션이 본문에서 뽑아둔 결과. 카드의 주 재료다.
            "detail": meta.get("detail") or "",
            "why_important": meta.get("why_important") or "",
            "implication": meta.get("implication") or "",
            "open_question": meta.get("open_question") or "",
            "link": link,
            "importance": meta.get("importance", "nice_to_know"),
            "score": float(item.get("score") or 0),
            "sensitive": is_sensitive(item, meta),
            "event_date": (item.get("event_date") or "").replace("-", "."),
            "source": source_name(link),
            "tag": next(iter(item.get("tags") or []), ""),
        })
    picked.sort(key=lambda x: (x["importance"] != "must_read", -x["score"]))
    return picked[:k]


def attach_bodies(items: list[dict]) -> None:
    """detail 이 얇은 기사만 원문을 다시 탄다. 실패해도 비치명.

    본문은 이 실행 안에서만 쓰고 어디에도 저장하지 않는다 — article_body 의
    계약이 그렇다(저작권). slides.json 에도 넣지 않는다.
    """
    thin = [it for it in items if len(it["detail"]) < THIN_DETAIL_CHARS]
    if not thin:
        return
    try:
        import article_body
        bodies, stats = article_body.fetch_bodies(
            [{"hash": it["hash"], "link": it["link"], "title": it["title"]} for it in thin]
        )
        print("[cards] " + article_body.format_stats(stats))
    except Exception as exc:  # noqa: BLE001 — 본문 부재는 비치명, 기존 필드로 간다
        print(f"[cards] 본문 재수집 실패 — 기존 추출 결과로 계속 "
              f"({type(exc).__name__}: {exc})")
        return
    for it in thin:
        body = bodies.get(it["hash"])
        if body:
            it["body"] = body[:BODY_CHARS_FOR_PROMPT]


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
            {k: v for k, v in {
                "n": i + 1,
                "title": it["title"],
                "summary": it["summary"],
                "detail": it["detail"],
                "why_important": it["why_important"],
                "implication": it["implication"],
                "open_question": it["open_question"],
                "body": it.get("body", ""),
                "sensitive": it["sensitive"],
            }.items() if v not in ("", None)}
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
        max_output_tokens=4096,
        thinking_budget=0,
        label="cards",
    )


def _check_line(problems: list[str], where: str, text, limit: int,
                allow_accent: bool) -> None:
    if not text or not isinstance(text, str):
        problems.append(f"{where}: 비어 있음")
        return
    if visible_len(text) > limit:
        problems.append(f"{where}: {visible_len(text)}자 > {limit} — \"{text[:24]}…\"")
        return
    if not allow_accent and "[[" in text:
        problems.append(f"{where}: 불릿에는 강조를 쓰지 않는다")
    elif allow_accent and (text.count("[[") != text.count("]]") or text.count("[[") > 1):
        problems.append(f"{where}: 강조 표기 오류")


def _check_bullets(problems: list[str], where: str, bullets, limit: int) -> None:
    if not isinstance(bullets, list):
        problems.append(f"{where}: 배열이 아님")
        return
    if not BULLETS_MIN <= len(bullets) <= BULLETS_MAX:
        problems.append(f"{where}: {len(bullets)}개 — {BULLETS_MIN}~{BULLETS_MAX}개여야 한다")
    for i, b in enumerate(bullets[:BULLETS_MAX]):
        _check_line(problems, f"{where}[{i + 1}]", b, limit, allow_accent=False)


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
    else:
        _check_line(problems, "hook.headline", hook.get("headline"), HEADLINE_MAX, True)
    if not isinstance(steps, list):
        return problems + ["steps 가 배열이 아님"]
    if len(steps) != len(items):
        problems.append(f"steps 개수 {len(steps)} ≠ 기사 {len(items)}")

    for i, slide in enumerate(steps):
        tag = f"step{i + 1}"
        if not isinstance(slide, dict):
            problems.append(f"{tag}: 객체가 아님")
            continue
        _check_line(problems, f"{tag}.headline", slide.get("headline"), HEADLINE_MAX, True)
        _check_bullets(problems, f"{tag}.facts", slide.get("facts"), FACT_MAX)
        _check_bullets(problems, f"{tag}.why", slide.get("why"), WHY_MAX)
        if slide.get("stepLabel") not in TAGS:
            problems.append(f"{tag}: stepLabel '{slide.get('stepLabel')}' 은 허용 태그 아님")
    return problems


def strip_accent_on_sensitive(raw: dict, items: list[dict]) -> int:
    """사고·안전 기사의 `[[ ]]` 강조를 벗긴다. 벗긴 개수를 반환.

    LLM 이 이 규칙을 두 번 연속 어겼다(2026-09-14). 검증 실패로 앨범 전체를
    떨어뜨리는 건 과하다 — 규칙의 목적은 수사 억제이고, 표기 제거로 달성된다.
    """
    stripped = 0
    for slide, item in zip(raw.get("steps") or [], items):
        if not item["sensitive"] or not isinstance(slide, dict):
            continue
        for field in ("headline",):
            text = slide.get(field)
            if isinstance(text, str) and "[[" in text:
                slide[field] = text.replace("[[", "").replace("]]", "")
                stripped += 1
    return stripped


def build_slides(raw: dict, items: list[dict], date: str,
                 collected: int = 0) -> list[dict]:
    """검증 통과한 카피 → build.js 가 먹는 slides 배열.

    한 주제는 한 장 안에서 끝낸다. 빽빽해지지 않는 이유는 블록을 나누기
    때문이다 — 사실 불릿은 맨몸으로, 의미는 색 깔린 패널 안에.

    원문 URL 은 LLM 이 아니라 여기서 붙인다 — 긴 URL 을 LLM 에 베끼게 하면
    오타가 난다. steps 와 items 는 개수·순서가 검증된 뒤다.
    """
    total = len(items) + 2
    slides = [{
        "type": "hook",
        "slideNum": f"01 / {total:02d}",
        "stepLabel": "NUCLENS 브리핑",
        "date": date.replace("-", "."),
        "label": "원자력 정책 브리핑",
        "toc": [c["headline"].replace("[[", "").replace("]]", "") for c in raw["steps"]],
        "headline": raw["hook"]["headline"],
        # 부제는 코드가 만든다 — 수집·선정 건수는 사실이라 LLM 을 통과시킬 이유가 없고,
        # 실제로 두 번 연속 50자 한도를 넘겨 앨범 전체를 떨어뜨렸다(2026-09-14).
        "subline": f"오늘 수집 {collected:,}건 중 {len(items)}건",
        "handle": SITE,
    }]
    for i, (copy, item) in enumerate(zip(raw["steps"], items), start=1):
        slides.append({
            "type": "step",
            "slideNum": f"{i + 1:02d} / {total:02d}",
            "idx": f"{i:02d}",
            "stepLabel": copy["stepLabel"],
            "headline": copy["headline"],
            "points": copy["facts"],
            "whyLabel": "왜 중요한가",
            "why": copy["why"],
            "meta": [m for m in (item["event_date"], item["tag"]) if m],
            "handle": SITE, "footer": item["source"],
            "url": item["link"],   # build.js 는 안 쓴다 — 캡션·검증용
        })
    slides.append({
        "type": "cta",
        "slideNum": f"{total:02d} / {total:02d}",
        "stepLabel": "NUCLENS",
        "headline": "전체 보기",
        "subline": "오늘 브리핑 전문과 지난 이슈 흐름",
        "keyword": SITE,
        "handle": DELIVERY_NOTE,   # 알약이 이미 주소라 꼬리말까지 주소면 세 번이다
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
    subprocess.run(["node", "build.js", "slides.json"], cwd=CARDS_DIR, check=True)


# ---- D. PNG 게이트 ------------------------------------------------------------


def gate(expected: int) -> list[Path]:
    """장수·파일명 연속성·최소 바이트. 하나라도 어긋나면 예외."""
    if expected > TELEGRAM_ALBUM_MAX:
        raise RuntimeError(f"앨범 {expected}장 — 텔레그램 한도 {TELEGRAM_ALBUM_MAX}장 초과")
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
    for i, s in enumerate([x for x in slides if x.get("url")], start=1):
        title = html.escape(s["headline"].replace("[[", "").replace("]]", ""))
        lines.append(f"{i}. {title} · <a href=\"{html.escape(s['url'], quote=True)}\">원문</a>")
    lines += ["", f"전체 보기 · {SITE}"]
    return "\n".join(lines)[:1024]  # 텔레그램 캡션 한도


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
    attach_bodies(items)

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
        stripped = strip_accent_on_sensitive(candidate, items)
        if stripped:
            print(f"[cards] sensitive 기사 강조 {stripped}곳 제거")
        last_problems = validate(candidate, items)
        if not last_problems:
            raw = candidate
            break
        print(f"[cards] 카피 검증 실패 ({attempt}/2): {'; '.join(last_problems[:6])}")
    if raw is None:
        print("[cards] 2회 실패 — 카드를 건너뛰고 텍스트 브리핑만 나간다")
        return 1

    slides = build_slides(raw, items, date, collected)
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
              "importance": "must_read", "score": 1.0, "hash": "h", "detail": "",
              "why_important": "", "implication": "", "open_question": "",
              "event_date": "2026.09.11", "source": "원안위", "tag": "#원안위"}]
    ok = {
        "hook": {"headline": "짧은 판단"},
        "steps": [{
            "stepLabel": "계속운전",
            "headline": "[[원안위]] 심의 착수",
            "facts": ["9월 11일 제2026-14회 회의", "2건 의결, 1건 재상정"],
            "why": ["설계수명 만료 4기 일정에 직결", "재상정분 결과는 미확정"],
        }],
    }
    assert validate(ok, items) == [], validate(ok, items)

    def mutate(**kw):
        return {**ok, "steps": [{**ok["steps"][0], **kw}]}

    assert any("headline" in p for p in validate(mutate(headline="가" * (HEADLINE_MAX + 1)), items))
    assert any("facts" in p for p in validate(mutate(facts=["가" * (FACT_MAX + 1), "나"]), items))
    assert any("facts" in p for p in validate(mutate(facts=["하나뿐"]), items)), "불릿 최소 개수"
    assert any("facts" in p for p in validate(mutate(facts=["가", "나", "다", "라"]), items)), "불릿 최대 개수"
    assert any("why" in p for p in validate(mutate(why=["가" * (WHY_MAX + 1), "나"]), items))
    assert any("강조" in p for p in validate(mutate(facts=["[[강조]] 금지", "나"]), items))
    assert any("stepLabel" in p for p in validate(mutate(stepLabel="아무거나"), items))
    assert any("개수" in p for p in validate({**ok, "steps": ok["steps"] * 2}, items))
    # sensitive 강조는 검증 실패가 아니라 코드가 벗긴다
    import copy
    dirty = copy.deepcopy(ok)
    assert strip_accent_on_sensitive(dirty, [{**items[0], "sensitive": True}]) == 1
    assert "[[" not in dirty["steps"][0]["headline"]
    assert strip_accent_on_sensitive(copy.deepcopy(ok), items) == 0
    assert is_sensitive({"title_kr": "사고관리계획서 심사"}, {"features": {"event_type": "regulatory_action"}}) is False
    assert is_sensitive({"title_kr": "정기 점검"}, {"features": {"event_type": "incident_safety"}}) is True

    # [[ ]] 는 글자 수에서 빠진다 — 정확히 한계면 통과해야 한다
    edge = mutate(headline="[[" + "가" * HEADLINE_MAX + "]]")
    assert validate(edge, items) == [], validate(edge, items)
    assert visible_len("[[가나]]다") == 3

    # 장수 산식: 표지1 + 2N + 마지막1
    built = build_slides(ok, items, "2026-09-14", 645)
    assert len(built) == len(items) + 2 == 3, len(built)
    assert [s["type"] for s in built] == ["hook", "step", "cta"]
    assert built[1]["points"] and built[1]["why"], "한 장에 사실·의미가 둘 다"
    assert "07:25" not in json.dumps(built, ensure_ascii=False), "고정 발송 시각 문구 잔존"
    print("self-check OK")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _self_check()
    else:
        sys.exit(main())
