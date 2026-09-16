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
import re
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
# 사이트가 매일 굽는 순위. web/build_data.py 가 배포 스텝에서 만든다(gitignore).
BRIEFINGS_FILE = ROOT / "web" / "public" / "data" / "briefings.json"

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
# 34자는 실측에서 두 번 연속 넘겼다(09-16: "2035년 경수형 SMR 상용화 목표…" 36자) —
# 두 번 실패면 카드가 통째로 빠진다. 진짜 한계는 렌더 가드(넘침 사각형)이므로
# 코드 상한은 한 줄 반 폭까지 열어 둔다.
FACT_MAX = 40      # 사실 불릿
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

# 카드 분류는 **사이트가 쓰는 그 분류**다. 기사마다 파이프라인이 이미 topics 를
# 붙여두므로(curated[hash].topics) LLM 에게 태그를 고르게 할 이유가 없다 —
# 고르게 하면 사이트와 다른 이름이 나오고 없는 태그를 지어낸다.
#
# 표시명은 web/public/app.js 의 TOPIC_LABELS 와 같아야 한다. 여기 복사본을 두되
# _self_check 가 app.js 를 파싱해 두 벌이 어긋났는지 검사한다(런타임은 파싱에
# 의존하지 않는다 — 파싱이 깨져도 카드는 나가야 한다).
TOPIC_LABELS = {
    "smr": "SMR", "newbuild": "신규 건설", "restart_lto": "계속운전·재가동",
    "fuel_cycle": "핵연료주기", "waste": "사용후핵연료·방폐", "finance": "원전금융·투자",
    "regulation": "규제·인허가", "power_market": "전력시장·요금",
    "datacenter_ai": "데이터센터·AI 전력", "fusion": "핵융합",
    "security_trade": "에너지안보·통상", "fukushima": "후쿠시마·처리수",
    "operations": "원전 운영", "safety": "안전·사건", "decommissioning": "해체·폐로",
    "workforce": "산업 인력", "policy_general": "원자력 정책", "research": "연구·기술",
    "applications": "비발전 활용",
}
APP_JS = ROOT / "web" / "public" / "app.js"

# sensitivity: 사고·안전·재난. 여기 걸리면 [[ ]] 강조와 수사적 표현을 금지한다.
#
# 어휘 부분일치로 판정하면 안 된다 — "사고" 가 "사고관리계획서" 에 걸려
# 계속운전 규제 기사가 사고 기사로 잡혔다(실측 2026-09-14). 큐레이션이 기사마다
# 이미 매겨둔 event_type 을 쓴다. 보조 어휘는 부분일치 사고가 없는 것만 남긴다.
# 사이트가 event_type=incident_safety 인 기사에 "safety" 토픽을 붙인다
# (web/build_data.py infer_topics). 그 판정을 그대로 쓴다.
SENSITIVE_TOPICS = {"safety"}
SENSITIVE_WORDS = ("피폭", "방사능 누출", "INES", "중대재해")

SYSTEM_PROMPT = f"""너는 한국수력원자력 원자력정책실의 일일 카드뉴스 카피라이터다.
기사 1건당 카드 **한 장**을 만든다. 한 장 안에 ①무슨 일이 있었나(사실 불릿)
②왜 중요한가(의미 불릿)를 둘 다 담는다.

출력 형식(JSON 객체 하나):
{{"hook": {{"headline": "..."}},
  "steps": [{{"headline": "...", "facts": ["...", "..."], "why": ["...", "..."]}}]}}

- steps 는 입력 기사와 **같은 개수·같은 순서**로 만든다. 하나도 빠뜨리지 않는다.
- 분류(태그)는 코드가 붙이니 쓰지 않는다.
- hook.headline: 오늘 전체를 관통하는 한 줄 판단. 한글 {HEADLINE_TARGET}자 이내
  (최대 {HEADLINE_MAX}자, 넘기면 버려진다). 표지 부제는 코드가 만드니 쓰지 않는다.
- steps[].headline: 그 기사에서 **무슨 일이 있었나**. 같은 길이 규칙.
- steps[].facts: {BULLETS_MIN}~{BULLETS_MAX}개, 각 {FACT_MAX}자 이내. **날짜·기관·대상·결정·수치**처럼
  원문에 적힌 구체값만. 해석·전망·형용사 금지. 개조식 체언 종결.
  예) "9월 11일 제2026-14회 회의" / "2건 의결, 1건 재상정"
- steps[].why: {BULLETS_MIN}~{BULLETS_MAX}개, 각 {WHY_MAX}자 이내. 정책 영향 / 한수원 시사점 /
  다음 확인사항 순서를 권장한다. 입력의 why_important·implication·open_question 을
  재료로 쓰되 그대로 베끼지 말고 한 줄로 줄인다.
- 강조는 headline 에만 최대 한 곳 `[[대괄호]]`. 불릿에는 쓰지 않는다.
- 숫자·호기명·국가명·기관명은 원문 그대로 옮긴다. 반올림·추정·의역 금지.
  **입력에 없는 수치·날짜를 지어내지 않는다.** 재료가 부족하면 불릿 수를 줄인다.
- 입력 기사에 sensitive=true 가 붙었으면 `[[ ]]` 강조와 수사적 표현을 쓰지 않는다.
  사실 서술만.
- 사람인 척하는 페르소나·감탄사·이모지 금지. 개조식 체언 종결을 기본으로 한다.
- 글자 수는 코드로 다시 잰다. 넘기면 통째로 버려지니 짧게 쓴다."""


# ---- A. 카드 소재 선정 + 재료 확보 ---------------------------------------------


def is_sensitive(row: dict) -> bool:
    if SENSITIVE_TOPICS & set(row.get("topics") or []):
        return True
    text = f"{row.get('title', '')} {row.get('summary', '')}"
    return any(w in text for w in SENSITIVE_WORDS)


# 사건일이 브리핑 날짜에서 이만큼 넘게 떨어져 있으면 칩에 쓰지 않는다.
# 추출 오류가 카드에 그대로 찍힌다 — 실측 2026-09-14: "고리 3·4호기 계속운전
# 심의 착수"(2026-09-11 기사)의 event_date 가 2024-09-11 로 잡혀 카드에 2024 가
# 박혔다. 시행 예정일처럼 앞뒤로 벌어지는 정상 값도 있어 넉넉히 잡되, 해(年)가
# 틀린 급은 걸러낸다. 틀린 날짜를 보여주는 것보다 안 보여주는 게 낫다.
EVENT_DATE_MAX_DRIFT_DAYS = 400


def plausible_event_date(raw: str, brief_date: str) -> str:
    """칩에 쓸 사건일. 브리핑 날짜에서 너무 멀면 빈 문자열."""
    if not raw:
        return ""
    try:
        event = datetime.strptime(raw[:10], "%Y-%m-%d")
        brief = datetime.strptime(brief_date[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    if abs((event - brief).days) > EVENT_DATE_MAX_DRIFT_DAYS:
        return ""
    return raw[:10].replace("-", ".")


def topic_label(meta: dict) -> str:
    """기사 분류 표시명. 파이프라인이 매긴 topics 의 첫 값을 사이트와 같은 이름으로.

    topics 는 web/build_data.py 의 _TOPIC_RULES 순서로 담기므로 첫 값이 가장
    구체적인 축이다(예: restart_lto → regulation → power_market).
    """
    for topic in meta.get("topics") or []:
        if topic in TOPIC_LABELS:
            return TOPIC_LABELS[topic]
    return "원자력 정책"  # 분류가 없는 기사도 카드에서 빼지는 않는다


def source_name(link: str) -> str:
    """매체·기관 표시명. 화이트리스트에 없으면 도메인 그대로."""
    hit = sources.credibility({"url": link}).get("name")
    return hit or sources.registered_domain(link) or ""


def load_site_ranking(date: str) -> list[dict] | None:
    """그날 브리핑의 이슈 목록을 **사이트가 정한 순서 그대로**. 없으면 None."""
    if not BRIEFINGS_FILE.exists():
        return None
    for briefing in json.loads(BRIEFINGS_FILE.read_text(encoding="utf-8")):
        if briefing.get("date") == date:
            return briefing.get("issues") or []
    return None


def pick_items(issue_rows: list[dict], k: int = MAX_CARDS, brief_date: str = "") -> list[dict]:
    """카드 = 사이트 순위 상위 k. 여기서 다시 고르지 않는다.

    순위는 사이트가 이미 정했다(web/build_data.py order_issue_rows — 국내·해외
    맞물림, 편집 고정, must_read, 며칠째 1위 쿨다운). 이슈는 기사가 아니라
    **클러스터**라 같은 사건의 다른 기사가 두 장 나가는 문제도 거기서 끝난다.
    카드가 따로 정렬하면 화면과 카드가 다른 얘기를 하게 된다(2026-09-14 교정).

    하는 일은 원문 링크 없는 이슈를 건너뛰는 것뿐이다.
    """
    picked = []
    for row in issue_rows:
        rep = row.get("representative_article") or {}
        link = (rep.get("url") or "").strip()
        if not link:
            continue  # 출처 미확인 — 카드에서 빼고 텍스트 브리핑으로만
        picked.append({
            "hash": rep.get("hash", ""),
            "title": row.get("title", ""),
            "summary": row.get("summary", ""),
            # 큐레이션이 본문에서 뽑아둔 결과. 카드의 주 재료다.
            "detail": row.get("detail") or "",
            "why_important": row.get("why_important") or "",
            "implication": row.get("implication") or "",
            "open_question": row.get("open_question") or "",
            "link": link,
            "importance": row.get("importance", "nice_to_know"),
            "sensitive": is_sensitive(row),
            "event_date": plausible_event_date(rep.get("event_date") or "", brief_date),
            "source": rep.get("publisher") or source_name(link),
            "topic": topic_label(row),
            # 칩은 항상 #태그 꼴로 — 이슈에 따라 "#원안위" / "smr특별법" 이 섞여 온다
            "tag": (lambda t: "#" + t.lstrip("#") if t else "")(next(iter(row.get("tags") or []), "")),
        })
        if len(picked) == k:
            break
    return picked


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
        # 폴백은 flash-lite 까지만. 기본 체인의 세 번째 gemini-3.5-flash-lite 는
        # thinkingBudget 을 HTTP 400 (INVALID_ARGUMENT) 으로 거부한다(실측 2026-09-14/15,
        # 카드 2회 시도 중 1회가 여기서 죽었다). 체인 자체는 gemini_client 소관.
        fallback_model=gemini_client.FALLBACK_MODEL,
        label="cards",
    )


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")
_CLAUSE_SEPS = ("…", " - ", " – ", ", ")   # '·'·공백은 안 쓴다 — "3·4호기"가 "3"이 된다
# 폴백 카피는 축약하지 않는다. 문장을 통째로 두고(최대 두 줄 반) 넘치면 렌더 가드가 잡는다.
FALLBACK_LINE_MAX = 96
FALLBACK_HEADLINE_MAX = 44   # 제목은 축약 없이 세 줄까지 — "…지분…" 같은 잘린 제목보다 낫다


def clip(text: str, limit: int) -> str:
    """limit 안으로 줄이되 절 경계에서 끊는다. 말줄임 없는 문장이 잘린 문장보다 낫다."""
    text = " ".join(str(text or "").split()).rstrip(".。")
    if visible_len(text) <= limit:
        return text
    for sep in _CLAUSE_SEPS:
        if sep not in text:
            continue
        parts = text.split(sep)
        acc = parts[0]
        for part in parts[1:]:
            nxt = acc + sep + part
            if visible_len(nxt) > limit:
                break
            acc = nxt
        acc = acc.strip(" ,·-–")
        # 첫 절부터 상한을 넘으면 이 구분자로는 못 자른다 — 다음 구분자로
        if visible_len(acc) <= limit - 1 and visible_len(acc) >= max(8, limit // 3):
            return acc + "…"   # 절이 이어졌음을 남긴다 — "확인되어" 로 끝나면 미완성으로 읽힌다
    return text[:limit - 1].rstrip(" ,·") + "…"


def _sentences(*fields: str) -> list[str]:
    out: list[str] = []
    for field in fields:
        for sent in _SENT_SPLIT_RE.split(str(field or "")):
            sent = " ".join(sent.split()).strip()
            if len(sent) >= 6 and sent not in out:
                out.append(sent)
    return out


def draft_copy(items: list[dict]) -> dict:
    """Gemini 없이 카피를 짠다 — 큐레이션이 이미 뽑아둔 detail·why_important·implication 을
    문장 단위로 잘라 쓴다. LLM 보다 거칠지만(축약 없이 절 단위 절단) 쿼터가 0이어도
    카드가 나간다(09-16 아침: 429 로 카드가 통째로 빠졌다). --no-llm 또는 LLM 2회
    실패 시 폴백."""
    steps = []
    for it in items:
        # 사실 = 요약 한 문장 + 본문 요지 첫 문장(둘). 의미 = 왜 중요한가·시사점 첫 문장.
        facts = [clip(x, FALLBACK_LINE_MAX) for x in _sentences(it.get("summary"), it.get("detail"))]
        facts = [f for f in facts if f][:2]
        why = [clip(x, FALLBACK_LINE_MAX) for x in _sentences(it.get("why_important"), it.get("implication"),
                                                              it.get("open_question"))]
        why = [w for w in why if w and w not in facts][:2]
        if not facts and it.get("title"):
            facts.append(clip(it["title"], FALLBACK_LINE_MAX))
        if not why:
            why = [f for f in facts[1:2]] or [clip(it.get("title", ""), FALLBACK_LINE_MAX)]
        steps.append({"headline": clip(it.get("title", ""), FALLBACK_HEADLINE_MAX),
                      "facts": facts, "why": why})
    return {"hook": {"headline": f"오늘 먼저 볼 원자력 현안 {len(items)}건"}, "steps": steps}


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


def _check_bullets(problems: list[str], where: str, bullets, limit: int,
                   bullets_min: int = BULLETS_MIN) -> None:
    if not isinstance(bullets, list):
        problems.append(f"{where}: 배열이 아님")
        return
    if not bullets_min <= len(bullets) <= BULLETS_MAX:
        problems.append(f"{where}: {len(bullets)}개 — {bullets_min}~{BULLETS_MAX}개여야 한다")
    for i, b in enumerate(bullets[:BULLETS_MAX]):
        _check_line(problems, f"{where}[{i + 1}]", b, limit, allow_accent=False)


def validate(raw: dict, items: list[dict], bullets_min: int = BULLETS_MIN,
             line_max: int | None = None, headline_max: int = HEADLINE_MAX) -> list[str]:
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
        _check_line(problems, f"{tag}.headline", slide.get("headline"), headline_max, True)
        _check_bullets(problems, f"{tag}.facts", slide.get("facts"), line_max or FACT_MAX, bullets_min)
        _check_bullets(problems, f"{tag}.why", slide.get("why"), line_max or WHY_MAX, bullets_min)
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
            "stepLabel": item["topic"],
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
    ap.add_argument("--date", help="outbox 대신 이 날짜의 사이트 순위로 만든다(로컬 검증용, --force 포함)")
    ap.add_argument("--no-llm", action="store_true",
                    help="Gemini 를 부르지 않고 사이트 문장(detail·why_important)으로 카피를 짠다")
    args = ap.parse_args()

    if args.date:
        date, outbox = args.date, {}
    else:
        if not OUTBOX_FILE.exists():
            print("[cards] outbox.json 없음 — 브리핑이 아직 안 돌았다. 스킵")
            return 0
        outbox = json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
        date = outbox.get("date") or datetime.now(KST).strftime("%Y-%m-%d")
        if outbox.get("status") not in ("sent", "partial"):
            print(f"[cards] 텍스트 브리핑 상태 '{outbox.get('status')}' — 카드 스킵")
            return 0
    if not (args.force or args.date) and (outbox.get("cards") or {}).get("date") == date:
        print(f"[cards] {date} 카드는 이미 발송됨 — 스킵")
        return 0

    rows = load_site_ranking(date)
    if rows is None:
        # 사이트 데이터가 없거나 오늘 날짜가 아니다. 배포 스텝(build_data)이 먼저
        # 돌아야 한다. 어제 순위로 카드를 만드는 것보다 안 만드는 게 낫다.
        print(f"[cards] 사이트 순위 없음 — {BRIEFINGS_FILE.relative_to(ROOT)} 에 {date} 브리핑이 없다. "
              "배포 스텝(web/build_data.py)이 먼저 돌아야 한다")
        return 1
    items = pick_items(rows, brief_date=date)
    if not items:
        print("[cards] 카드로 낼 이슈 없음 — 텍스트 브리핑만. 억지로 채우지 않는다")
        return 0
    print(f"[cards] 사이트 순위 상위 {len(items)}건: " +
          " / ".join(f"{i['topic']} {i['title'][:24]}" for i in items))
    attach_bodies(items)

    collected = sum(s.get("candidate_count", 0)
                    for s in (outbox.get("selection_stats") or {}).values()
                    if isinstance(s, dict))
    if not collected:
        # --date 로컬 실행이나 stats 없는 outbox — "오늘 수집 0건 중 3건"이 찍혔다(09-16).
        collected = sum(int(r.get("article_count") or 0) for r in rows)

    raw = None
    last_problems: list[str] = []
    for attempt in (() if args.no_llm else (1, 2)):
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
        # LLM 이 없거나(쿼터 0) 두 번 다 틀렸다 — 사이트 문장으로 대체한다. 거칠어도
        # 카드가 안 나가는 것보다 낫다(09-16 아침 실사고). 렌더 넘침 가드는 그대로.
        candidate = draft_copy(items)
        problems = validate(candidate, items, bullets_min=1, line_max=FALLBACK_LINE_MAX,
                            headline_max=FALLBACK_HEADLINE_MAX)
        if problems:
            print(f"[cards] LLM 없이도 못 만듦: {'; '.join(problems[:6])} — 카드 건너뜀")
            return 1
        print("[cards] " + ("--no-llm" if args.no_llm else "LLM 2회 실패") + " → 사이트 문장으로 카피 대체")
        raw = candidate

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
              "event_date": "2026.09.11", "source": "원안위", "tag": "#원안위",
              "topic": "규제·인허가"}]
    ok = {
        "hook": {"headline": "짧은 판단"},
        "steps": [{
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
    assert any("개수" in p for p in validate({**ok, "steps": ok["steps"] * 2}, items))
    # sensitive 강조는 검증 실패가 아니라 코드가 벗긴다
    import copy
    dirty = copy.deepcopy(ok)
    assert strip_accent_on_sensitive(dirty, [{**items[0], "sensitive": True}]) == 1
    assert "[[" not in dirty["steps"][0]["headline"]
    assert strip_accent_on_sensitive(copy.deepcopy(ok), items) == 0

    # [[ ]] 는 글자 수에서 빠진다 — 정확히 한계면 통과해야 한다
    edge = mutate(headline="[[" + "가" * HEADLINE_MAX + "]]")
    assert validate(edge, items) == [], validate(edge, items)
    assert visible_len("[[가나]]다") == 3

    # 분류는 LLM 이 아니라 코드가 붙인다
    assert topic_label({"topics": ["restart_lto", "regulation"]}) == "계속운전·재가동"
    assert topic_label({"topics": ["없는토픽"]}) == "원자력 정책"

    # 사이트 순위를 그대로 — 재정렬 없음, 링크 없는 이슈만 건너뜀
    rows = [
        {"title": "링크 없음", "topics": ["smr"], "representative_article": {}},
        {"title": "해외 1위", "topics": ["security_trade"], "importance": "nice_to_know",
         "representative_article": {"url": "http://a", "hash": "a", "publisher": "WNN"}},
        {"title": "국내 1위", "topics": ["restart_lto"], "importance": "must_read",
         "representative_article": {"url": "http://b", "hash": "b"}},
    ]
    got = pick_items(rows, k=3, brief_date="2026-09-14")
    assert [g["hash"] for g in got] == ["a", "b"], "사이트 순서 유지 + 링크 없는 건 제외"
    assert got[0]["source"] == "WNN" and got[0]["topic"] == "에너지안보·통상"
    assert is_sensitive({"topics": ["safety"], "title": "", "summary": ""}) is True
    assert is_sensitive({"topics": ["regulation"], "title": "사고관리계획서", "summary": ""}) is False
    assert load_site_ranking("1999-01-01") in (None, [])

    # 해가 틀린 사건일은 칩에서 뺀다 (2026 브리핑에 2024 가 박히던 실사고)
    assert plausible_event_date("2026-09-11", "2026-09-12") == "2026.09.11"
    assert plausible_event_date("2024-09-11", "2026-09-12") == ""
    assert plausible_event_date("2026-03-11", "2026-09-12") == "2026.03.11"
    assert plausible_event_date("", "2026-09-12") == ""
    assert plausible_event_date("없는날짜", "2026-09-12") == ""

    # 표시명이 사이트와 어긋나면 여기서 죽는다 (런타임은 이 파싱에 의존하지 않는다)
    if APP_JS.exists():
        block = re.search(r"const TOPIC_LABELS = \{(.*?)\};", APP_JS.read_text(encoding="utf-8"), re.S)
        assert block, "app.js 에서 TOPIC_LABELS 를 못 찾음 — 이름이 바뀌었나?"
        site = dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block.group(1)))
        assert site == TOPIC_LABELS, (
            "분류 표시명이 web/public/app.js 와 어긋남: "
            f"{set(site.items()) ^ set(TOPIC_LABELS.items())}")

    # 장수 산식: 표지1 + 2N + 마지막1
    built = build_slides(ok, items, "2026-09-14", 645)
    assert len(built) == len(items) + 2 == 3, len(built)
    assert [s["type"] for s in built] == ["hook", "step", "cta"]
    assert built[1]["points"] and built[1]["why"], "한 장에 사실·의미가 둘 다"
    assert "07:25" not in json.dumps(built, ensure_ascii=False), "고정 발송 시각 문구 잔존"
    # LLM 없는 폴백 — 긴 제목·긴 문장도 상한 안으로 절 단위 절단, 검증 통과
    long_item = {"title": "日 하마오카 원전, 내진 데이터 조작 사실로 드러나 경영진 사임 그리고 추가 조사 착수",
                 "detail": "주부전력은 9월 15일 하마오카 원전의 내진 평가 자료 일부가 조작됐다고 인정했다. "
                           "사장과 원자력본부장이 사임했다. 규제위원회는 추가 조사에 착수했다.",
                 "why_important": "일본 원전 재가동 심사 전반의 신뢰 문제로 번질 수 있다.",
                 "implication": "", "open_question": "", "summary": ""}
    fb = draft_copy([long_item])
    assert validate(fb, [long_item], bullets_min=1, line_max=FALLBACK_LINE_MAX,
                    headline_max=FALLBACK_HEADLINE_MAX) == [], fb
    assert visible_len(fb["steps"][0]["headline"]) <= FALLBACK_HEADLINE_MAX
    assert clip("가나다라", 34) == "가나다라"
    long_sent = "일본 주부전력은 하마오카 원전 3·4호기의 재가동 심사에 제출한 지진 데이터 일부를 조작했다고 인정하고 경영진 사임을 발표했다"
    assert visible_len(clip(long_sent, FACT_MAX)) <= FACT_MAX, clip(long_sent, FACT_MAX)
    assert "3·4호기" in clip("하마오카 원전 3·4호기 재가동 심사 지연", 20)   # '·'에서 안 자른다
    print("self-check OK")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _self_check()
    else:
        sys.exit(main())
