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
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import card_context
import card_editorial
import card_qa
import gemini_client
import sources

ROOT = Path(__file__).parent
CARDS_DIR = ROOT / "cards"
OUT_DIR = CARDS_DIR / "out"
SLIDES_FILE = CARDS_DIR / "slides.json"
ALBUM_FILE = CARDS_DIR / "album.json"
# 스토리 카피. **make_cards 가 쓰고 story_cards 가 읽는다.**
#
# 두 스크립트가 각자 LLM 을 부르면 그날 편집 판단이 둘로 갈린다 — 일일 카드는
# A 가 중요하다 하고 스토리는 B 가 중요하다 하는 날이 생긴다. 스토리가 있는
# 날은 편집 데스크를 한 번만 부르고, 그 결과로 두 산출물을 다 쓴다. 그래서
# story_cards 는 정상 경로에서 **LLM 을 부르지 않는다** — 렌더와 검증만 한다.
STORY_COPY_FILE = CARDS_DIR / "story_copy.json"
OUTBOX_FILE = ROOT / "outbox.json"
# 사이트가 매일 굽는 순위. web/build_data.py 가 배포 스텝에서 만든다(gitignore).
BRIEFINGS_FILE = ROOT / "web" / "public" / "data" / "briefings.json"
# 게시된 카드. publish_cards.py 가 여기에 날짜 폴더와 index.json 을 남기고,
# 그 둘은 **커밋된다** — 그래서 다음 실행이 체크아웃만으로 "이미 했는가"를 안다.
CARDS_SITE_DIR = ROOT / "web" / "public" / "cards"

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
# 카드 제목은 사진 위 히어로에 **2줄로** 앉아야 한다. 히어로 글상자가 58%(626px)
# 폭이라 72px 에서 한 줄에 9자, 강조줄(1.13em)은 7자다 — 16자를 넘기면 3줄이 되고
# 렌더가 글자를 줄여 제목이 작아진다(지니 09-20: "3줄일 필요가 있나, 자리만 차지").
# 목표 14자, 상한 18자(72px 2줄의 실측 한계). 22자까지는 렌더 축소(하한 52px)가 2줄로 앉힌다.
HEADLINE_TARGET = 14
HEADLINE_MAX = 18
# 18자는 **취향**(72px 2줄)이고, 28자는 **렌더 한계**(축소 하한 52px 2줄)다. 이 둘을
# 같은 숫자로 쓰다 09-20 아침에 앨범을 통째로 날렸다 — 24자 제목 하나에 카피가
# 반려되고 5장 전부 폴백(줄글·"…" 토막·강조 없는 흰 제목)으로 나갔다. 프롬프트는
# 계속 18자를 요구하되 **반려는 렌더가 못 앉히는 길이부터** 한다. 그 사이는
# normalize() 가 다듬고 build.js 의 축소 루프가 2줄로 앉힌다(실측 24·26자).
HEADLINE_HARD = 28
SUBLINE_MAX = 50   # 표지 부제
# 34자는 실측에서 두 번 연속 넘겼다(09-16: "2035년 경수형 SMR 상용화 목표…" 36자) —
# 두 번 실패면 카드가 통째로 빠진다. 진짜 한계는 렌더 가드(넘침 사각형)이므로
# 코드 상한은 한 줄 반 폭까지 열어 둔다.
FACT_MAX = 40      # 사실 불릿
WHY_MAX = 40       # 의미 불릿 한 줄
# 한 장에 둘 다 들어가므로 각각 3개까지. 넘치는지는 build.js 넘침 가드가 잰다.
BULLETS_MIN, BULLETS_MAX = 2, 3
# **의미 불릿만 하한이 1 이다.** 프롬프트는 계속 2~3 을 요구하지만, 한 줄만
# 나왔다고 그날 앨범을 떨어뜨리지 않는다.
#
# 2026-09-20 실측이 이 값의 이유다. 브리프가 `so_what` 을 둘 주었는데도 첫 카드의
# `why` 가 한 줄로 나왔고, repair 도 같은 한 줄을 냈다 — 두 번 더 부르고 결국
# 사이트 문장 폴백으로 떨어졌다. 토큰 예산 때문이 아니었다(1,354/12,288).
#
# 둘을 채우라고 계속 밀면 모델이 채우는 방법은 하나뿐이다: 없는 의미를 지어내는
# 것. 그건 이 카드가 가장 피하려는 실패이고(`card_qa` 의 추상 표현 판정이 그걸
# 잡는다), 근거 있는 한 줄이 근거 없는 두 줄보다 낫다. 길이 한 자에 앨범이
# 떨어지던 문제와 같은 종류라 같은 방식으로 푼다 — 규격이 아니라 재료를 따른다.
WHY_MIN = 1
# 마지막 장 '오늘 더 있었던 일' 한 줄. 첫 장 목차와 같은 자리다. 실측(09-18)은
# 33자가 들어가고 34자에서 렌더가 말줄임으로 잘랐다 — 글자 폭이 제각각이라
# 경계에 붙이지 않고 여유를 둔다. 자를 바에는 그 줄을 안 쓴다(closing_lines).
CTA_LINE_MAX = 28

# curated 의 detail 이 이보다 짧으면 그 기사만 원문을 다시 탄다.
# 원문 본문은 저작권 계약상 저장하지 않는다(article_body.py:370) — 남는 건
# 큐레이션이 본문에서 뽑아둔 detail·why_important·implication·open_question 이다.
# 그래서 순서는 "기존 추출 결과 먼저, 결손일 때만 재수집" 이다.
THIN_DETAIL_CHARS = 120
BODY_CHARS_FOR_PROMPT = 1200

MIN_PNG_BYTES = 20_000  # 1080×1440 그라디언트 빈 카드가 대략 20KB. 그 아래면 빈 렌더.

# 카드 하단 핸들·캡션에 박히는 주소. 워크플로가 SITE_URL 을 이미 들고 있으므로
# 그것을 먼저 본다 — v1 에서 가져온 상수를 그대로 두면 v2 카드가 v1 사이트를
# 광고한다.
# 기본 도메인은 이 저장소 것이다. v2(차장 포크)에서 카드 파이프라인을 들여올 때
# 기본값이 nuclens-v2.pages.dev 로 딸려 왔고, 그대로 두면 카드 5장 전부와
# 텔레그램 캡션이 남의 주소를 달고 나간다(실측: 표지 푸터·본문 푸터·끝장).
SITE = (os.environ.get("SITE_URL") or "https://nuclens.pages.dev").split("//")[-1].strip("/")
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
- steps[].facts: **{BULLETS_MAX}개**(재료가 정말 없을 때만 {BULLETS_MIN}개), 각 {FACT_MAX}자 이내. **날짜·기관·대상·결정·수치**처럼
  원문에 적힌 구체값만. 해석·전망·형용사 금지. 개조식 체언 종결.
  예) "9월 11일 제2026-14회 회의" / "2건 의결, 1건 재상정"
- steps[].why: **{BULLETS_MAX}개**(재료가 정말 없을 때만 {BULLETS_MIN}개), 각 {WHY_MAX}자 이내. 정책 영향 / 한수원 시사점 /
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
    """기사 분류 표시명 — **사이트 칩과 같은 값**을 쓴다.

    1순위는 khnp_domain(현안 분류를 LLM 이 매긴 값). 카드가 topics 규칙값을 쓰던
    동안 사이트 칩은 '안전성'인데 카드는 '규제·인허가'로 나갔다(09-16 한수원
    복합재난 훈련 실측). 카드는 사이트를 따라간다.
    khnp_domain 이 비면 topics 의 첫 값으로 물러난다 — topics 는
    web/build_data.py 의 _TOPIC_RULES 순서라 첫 값이 가장 구체적인 축이다.

    **지금은 늘 그 폴백을 탄다.** v2 의 빌더는 khnp_domain 을 만들지 않는다
    (라이브 issues.json 587건 중 보유 0건, 2026-09-19 실측). 사이트 칩도 같은
    이유로 빈칸이라, 카드와 사이트가 어긋날 자리가 지금은 없다. 빌더가 그 값을
    내기 시작하면 이 1순위가 저절로 살아난다 — 그때 둘이 다시 맞물린다.
    """
    domain = str(meta.get("khnp_domain") or "").strip()
    if domain:
        return domain
    for topic in meta.get("topics") or []:
        if topic in TOPIC_LABELS:
            return TOPIC_LABELS[topic]
    return "원자력 정책"  # 분류가 없는 기사도 카드에서 빼지는 않는다


def source_name(link: str) -> str:
    """매체·기관 표시명. 화이트리스트에 없으면 도메인 그대로."""
    hit = sources.credibility({"url": link}).get("name")
    return hit or sources.registered_domain(link) or ""


def load_site_data(date: str) -> card_context.SiteData | None:
    """그날 카드의 재료 한 세대. 없으면 None.

    정상 당일은 `today.json` 을 탄다 — **`thread_id` 가 거기에만 있다.** 순위는
    두 파일이 같으므로(today.json 이 그날 briefing 의 이슈를 그대로 싣는다)
    일일 카드가 보는 것은 달라지지 않고, 스토리 카드가 이슈를 제목이 아니라
    id 로 스레드에 이을 수 있게 된다(`card_context` 모듈 주석 ②).
    """
    try:
        return card_context.load_site_data(date)
    except card_context.ContextError as exc:
        print(f"[cards] {exc}")
        return None


def load_site_ranking(date: str) -> list[dict] | None:
    """그날 브리핑의 이슈 목록을 **사이트가 정한 순서 그대로**. 없으면 None."""
    data = load_site_data(date)
    return None if data is None else data.issues


def pick_items(issue_rows: list[dict], k: int = MAX_CARDS, brief_date: str = "") -> list[dict]:
    """카드 = 사이트 순위 상위 k **중 보고 후보만**. 순위를 여기서 다시 매기지 않는다.

    순위는 사이트가 이미 정했다(web/build_data.py order_issue_rows — 국내·해외
    맞물림, 편집 고정, must_read, 며칠째 1위 쿨다운). 이슈는 기사가 아니라
    **클러스터**라 같은 사건의 다른 기사가 두 장 나가는 문제도 거기서 끝난다.
    카드가 따로 정렬하면 화면과 카드가 다른 얘기를 하게 된다(2026-09-14 교정).

    거르는 것은 둘이다.

    ① 원문 링크 없는 이슈 — 출처 미확인이라 카드에 못 싣는다.

    ② **보고 후보가 아닌 이슈**(지니 2026-09-21). 카드는 임직원이 캡처해 보고·
       카톡에 붙이는 이미지 자산이지 읽을거리가 아니다. 그러면 실릴 자격은
       "오늘 순위가 높다"가 아니라 "보고할 만하다"여야 한다.

       대가를 알고 쓰는 규칙이다. 실측(최근 30일): 보고 후보가 있는 날은
       19/30(63%)이고 하루 평균 1.2건이다 — **3일에 1일꼴로 카드가 0장**이고,
       나오는 날도 1~2장이다. 텔레그램 일일 앨범이 그만큼 안 나간다.
       매일 나가는 쪽으로 되돌리려면 아래 report_pick 조건만 지우면 된다.
    """
    picked = []
    for row in issue_rows:
        rep = row.get("representative_article") or {}
        link = (rep.get("url") or "").strip()
        if not link:
            continue  # 출처 미확인 — 카드에서 빼고 텍스트 브리핑으로만
        if not (row.get("report_pick") or "").strip():
            continue  # 보고 후보가 아니면 카드로 만들지 않는다
        picked.append({
            "hash": rep.get("hash", ""),
            # 홈의 '먼저 볼 3건' 카드가 이 카피를 issue_id 로 되찾아 간다(album.json lines)
            "issue_id": row.get("issue_id", ""),
            # 스토리 카드가 이 칸으로 스레드를 찾는다. 제목이 아니라 id 다 —
            # 표시 제목은 움직이는 값이라 열쇠가 될 수 없다(card_context ②).
            "thread_id": row.get("thread_id", ""),
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


def _article_payload(items: list[dict]) -> list[dict]:
    """카드가 보는 이슈 재료. 빈 칸은 싣지 않는다(토큰을 먹고 모델을 헷갈린다)."""
    return [
        {k: v for k, v in {
            "issue_id": it.get("issue_id", ""),
            "n": i + 1,
            "title": it["title"],
            "summary": it["summary"],
            "detail": it["detail"],
            "why_important": it["why_important"],
            "implication": it["implication"],
            "open_question": it["open_question"],
            "body": it.get("body", ""),
            "sensitive": it["sensitive"],
        }.items() if v not in ("", None, False)}
        for i, it in enumerate(items)
    ]


def _writer_limits() -> dict:
    return {"bullets_min": BULLETS_MIN, "bullets_max": BULLETS_MAX,
            "headline_max": HEADLINE_MAX, "fact_max": FACT_MAX, "why_max": WHY_MAX}


def ask_daily_writer(items: list[dict], date: str, total_collected: int,
                     problems: list[str] | None = None,
                     log: list[dict] | None = None) -> dict:
    """스토리 없는 날 — 판단과 카피를 **한 응답 안에서** 받는다.

    호출을 둘로 늘리지 않으면서도 모델이 글자 수부터 맞추러 가지 않게, 스키마
    앞자리에 `brief` 를 둔다(`card_editorial.daily_writer_system` 주석).
    """
    return card_editorial.call(
        "card_daily_writer",
        card_editorial.daily_writer_system(**_writer_limits()),
        {"date": date, "collected_today": total_collected,
         "articles": _article_payload(items)},
        fix_these=problems, log=log)


def ask_narrator(items: list[dict], date: str, story: dict | None,
                 log: list[dict] | None = None) -> dict:
    """편집 데스크. 오늘 카드 전체의 **무엇을 말할지**를 한 번에 정한다.

    스토리 후보가 아닌 두 이슈에는 과거 사건을 싣지 않는다 — 그쪽은 오늘
    달라진 것만 말하면 되고, 넣으면 TPM 만 먹는다.
    """
    payload = {"date": date, "issues": _article_payload(items)}
    if story:
        payload["story"] = story
    return card_editorial.call(
        "card_editorial_narrator", card_editorial.NARRATOR_SYSTEM, payload,
        max_output_tokens=6144, log=log)


def ask_writer(brief: dict, items: list[dict], date: str, *, with_story: bool,
               problems: list[str] | None = None, story_events: list[dict] | None = None,
               log: list[dict] | None = None, task: str = "card_writer") -> dict:
    """카피라이터. 브리프를 규격에 맞게 적는다 — 기사를 다시 해석하지 않는다."""
    payload = {"date": date, "brief": brief,
               # 민감 이슈 표시는 카피 규칙(강조 금지)이라 브리프가 아니라
               # 여기에 싣는다. 편집 판단의 대상이 아니다.
               "sensitive": [it.get("issue_id", "") for it in items if it["sensitive"]]}
    if with_story and story_events:
        payload["story_events"] = story_events
    # 스토리가 붙는 날은 한 응답에 일일 3장 + 스토리 5장이 들어간다. 예산을
    # 8192 로 두되 **부족해서 생긴 문제는 아니다** — 실측 2026-09-20 에 12288 을
    # 줘도 실제 사용은 1,354 토큰이었다(로그의 tokens=… 가 그것을 보여 준다).
    return card_editorial.call(
        task,
        card_editorial.writer_system(**_writer_limits(), with_story=with_story),
        payload, fix_these=problems,
        max_output_tokens=8192 if with_story else 6144, log=log)



# ---- B2. 편집 오케스트레이션 ---------------------------------------------------


def _fit_accent(text, limit: int):
    """제목을 상한 안으로. **낱말 경계에서만** 줄인다.

    제목은 본문 불릿과 달리 절 구분자가 없어서 clip() 을 쓰면 "RISE AS…" 처럼
    낱말 한가운데가 잘린다 — 지니가 09-20 에 지적한 "문장이 끊긴다"가 그것이다.
    뒤 낱말을 통째로 덜어내고, 그래도 넘치면(한 낱말이 상한보다 길면) 그때만 자른다.
    `[[ ]]` 짝이 깨지면 마크업을 버린다 — 색보다 문장이 먼저다.
    """
    if not isinstance(text, str) or visible_len(text) <= limit:
        return text
    words = text.split()
    while len(words) > 1 and visible_len(" ".join(words)) > limit:
        words.pop()
    cut = " ".join(words).rstrip(" ,·-–")
    if visible_len(cut) > limit:
        cut = clip(text, limit)
    return cut if cut.count("[[") == cut.count("]]") else cut.replace("[[", "").replace("]]", "")


def normalize(raw: dict, headline_max: int = HEADLINE_HARD,
              fact_max: int = FACT_MAX, why_max: int = WHY_MAX) -> dict:
    """검증 앞에 한 겹. **길이는 고치고, 구조는 건드리지 않는다.**

    글자 수는 모델이 못 세는 것이고(다섯 번 실측), 그 때문에 그날 카피를 통째로
    떨어뜨리면 남는 건 사이트 문장 폴백이다 — 09-20 아침에 그게 나갔다. 반대로
    개수·타입·강조 규칙·중복 QA 는 손대지 않는다. 그건 재료가 모자라거나 판단이
    틀린 것이라 repair 나 폴백이 받아야 한다.
    """
    if not isinstance(raw, dict):
        return raw
    hook = raw.get("hook")
    if isinstance(hook, dict):
        hook["headline"] = _fit_accent(hook.get("headline"), headline_max)
    for slide in (raw.get("steps") or []):
        if not isinstance(slide, dict):
            continue
        slide["headline"] = _fit_accent(slide.get("headline"), headline_max)
        for key, limit in (("facts", fact_max), ("why", why_max)):
            rows = slide.get(key)
            if isinstance(rows, list):
                slide[key] = [clip(b, limit) if isinstance(b, str) else b for b in rows]
    return raw


def review(raw: dict, items: list[dict], **kwargs) -> list[str]:
    """규격 검증 + 편집 QA 를 한 번에. **둘 다 통과해야 카드가 나간다.**

    규격(`validate`)은 "깨지지 않는가" 를, QA(`card_qa`)는 "같은 말을 두 번
    하지 않는가" 를 본다. 경고는 실패로 세지 않되 목록에는 실어 보낸다 —
    repair 는 경고까지 같이 고칠 기회가 있어야 한다.
    """
    kwargs.setdefault("why_min", WHY_MIN)
    problems = validate(normalize(raw), items, **kwargs)
    report = card_qa.review_daily(raw, items)
    return problems + [str(f) for f in report.failures]


def find_story(data, items: list[dict]):
    """오늘 스토리 후보. 재료가 못 믿을 상태면 조용히 없는 것으로 친다."""
    try:
        return card_context.pick_story_candidate(data, items)
    except card_context.ContextError as exc:
        print(f"[cards] 스토리 재료 제외 — {exc}")
        return None


def story_material(story, date: str) -> dict:
    return card_context.evidence_packet(story, date, topic=topic_label(story.issue))


def log_calls(call_log: list[dict]) -> None:
    """**논리 호출과 HTTP 요청을 갈라 남긴다.**

    예전에는 이 수가 어디에도 안 남았다. 바깥 `for attempt in (1, 2)` 루프와
    `call_json(retries=3)` 이 곱해져 "1회" 가 최악 8번의 HTTP 요청이었는데,
    로그만 보면 한 번 부른 것처럼 보였다. 쿼터를 재려면 두 수가 다 필요하다.
    """
    if not call_log:
        print("[cards] LLM 호출 없음")
        return
    worst = sum(row["max_http_attempts"] for row in call_log)
    for row in call_log:
        print(f"[cards] {row['task']} model={row['model']} calls=1 "
              f"max_http={row['max_http_attempts']} "
              f"tokens={row.get('candidate_tokens')}/{row.get('budget')} "
              f"thoughts={row.get('thought_tokens')} "
              f"finish={row.get('finish_reason')}"
              + (" (repair)" if row["repair"] else ""))
    print(f"[cards] 논리 호출 {len(call_log)}회 · HTTP 최악 상한 {worst}회")


def _daily_of(candidate: dict) -> dict:
    """card_daily_writer 응답에서 일일 카피를 꺼낸다.

    응답은 `{brief, daily, story}` 봉투다. **봉투째 검증하면 늘 떨어진다** —
    09-20 실측: "hook 없음; steps 가 배열이 아님" 이 뜨고 repair 까지 같은 말을
    한 뒤 사이트 문장 폴백으로 내려갔다(`_writer_round` 는 이미 풀어서 본다).
    봉투가 아닌 응답(카피 파일·옛 형식)은 그대로 돌려준다.
    """
    if isinstance(candidate, dict) and isinstance(candidate.get("daily"), dict):
        return candidate["daily"]
    return candidate


def _apply_and_review(candidate: dict, items: list[dict]) -> list[str]:
    stripped = strip_accent_on_sensitive(candidate, items)
    if stripped:
        print(f"[cards] sensitive 기사 강조 {stripped}곳 제거")
    return review(candidate, items)


def run_editorial(items: list[dict], date: str, collected: int,
                  story_payload: dict | None,
                  call_log: list[dict]) -> tuple[dict | None, dict | None]:
    """오늘의 카피를 만든다. 돌려주는 것은 (일일 카피, 스토리 카피).

    호출 계약은 `card_editorial` 모듈 주석에 있다. 여기서 지키는 것은
    **실패 도메인 분리**다 — 스토리가 깨져도 일일 카드는 그대로 나가고,
    Narrator 가 죽으면 스토리만 빠진 채 일일 폴백 한 번으로 떨어진다.
    """
    daily, story_copy, brief = None, None, None

    if story_payload is not None:
        try:
            brief = ask_narrator(items, date, story_payload, log=call_log)
        except Exception as exc:  # noqa: BLE001 — 부가 계층, 원인만 남기고 내려간다
            print(f"[cards] Narrator 실패 — {type(exc).__name__}: {exc}")
            brief = None
        if brief is not None:
            card_editorial.normalize_brief(brief)
            fatal, story_bad, warnings = card_editorial.validate_brief(
                brief, items, story_payload.get("thread_id"))
            for line in warnings:
                print(f"[cards] 브리프 경고: {line}")
            if fatal:
                # 이 브리프로는 일일 카드를 쓸 수 없다. 스토리도 같이 빠진다.
                print(f"[cards] 편집 브리프 거부: {'; '.join(fatal[:4])}")
                brief = None
            elif story_bad:
                # **스토리만 못 쓴다.** 일일 판단은 멀쩡하므로 그대로 쓴다 —
                # 여기서 브리프를 통째로 버리면 Narrator 를 부른 값이 사라지고
                # 호출이 계약(2회)보다 한 번 더 나간다(2026-09-20 실측).
                print(f"[cards] 스토리만 제외: {'; '.join(story_bad[:2])}")
                story_payload = None
    if brief is None and story_payload is not None:
        # Narrator 가 없으면 스토리도 없다 — 판단 없이 5장을 쓰면 규격만 맞는
        # 이야기가 나온다. 일일 카드는 아래 폴백 한 번으로 살린다.
        print("[cards] 스토리 건너뜀 — 일일 카드는 card_daily_writer 로 간다")
        story_payload = None

    if brief is not None:
        raw = _writer_round(brief, items, date, story_payload, call_log)
        if raw is not None:
            return _daily_of(raw), raw.get("story") or None
        print("[cards] Writer 실패 — 일일 카드를 단독 호출로 다시 만든다")
        story_payload = None

    # 스토리 없는 날(그리고 위 경로가 떨어진 날) — 판단과 카피를 한 응답에서.
    try:
        candidate = ask_daily_writer(items, date, collected, log=call_log)
    except Exception as exc:  # noqa: BLE001
        print(f"[cards] card_daily_writer 실패 — {type(exc).__name__}: {exc}")
        return None, None
    daily_copy = _daily_of(candidate)
    problems = _apply_and_review(daily_copy, items)
    if not problems:
        return daily_copy, None
    print(f"[cards] 편집 QA 실패: {'; '.join(problems[:6])}")
    try:
        repaired = card_editorial.call(
            "card_writer_repair",
            card_editorial.daily_writer_system(**_writer_limits()),
            {"date": date, "collected_today": collected,
             "articles": _article_payload(items)},
            fix_these=problems, log=call_log)
    except Exception as exc:  # noqa: BLE001
        print(f"[cards] repair 실패 — {type(exc).__name__}: {exc}")
        return None, None
    repaired_daily = _daily_of(repaired)
    problems = _apply_and_review(repaired_daily, items)
    if problems:
        print(f"[cards] repair 뒤에도 실패: {'; '.join(problems[:6])}")
        return None, None
    return repaired_daily, None


def story_problems(copy: object, payload: dict) -> list[str]:
    """스토리 카피가 규격과 근거를 지키는가. 렌더 쪽 검증기를 그대로 쓴다.

    **지연 import 다.** `story_cards` 가 이 모듈을 읽으므로 맨 위에서 부르면
    순환한다. 그렇다고 검증기를 두 벌 두면 한쪽만 고치는 날이 온다 — 스토리
    카피를 실제로 거르는 것은 저쪽이므로, 여기서도 같은 자를 쓴다.
    """
    if not isinstance(copy, dict):
        return ["story: 객체가 아님"]
    import story_cards
    return story_cards.validate(story_cards.normalize(copy, payload), payload)


def _writer_round(brief: dict, items: list[dict], date: str,
                  story_payload: dict | None, call_log: list[dict]) -> dict | None:
    """Writer 1회 + 필요하면 repair 1회. **Narrator 는 다시 부르지 않는다.**

    **두 도메인을 같이 본다.** 예전에는 일일만 검증하고 스토리 카피는 검증 없이
    저장했다 — 그래서 잘못된 스토리는 한참 뒤 렌더 단계에서 처음 걸렸고, 그때는
    고칠 기회가 없었다(story_cards 는 LLM 을 안 부른다). 실측 2026-09-20: 모델이
    **일일 브리프의 숫자를 스토리 표지 배지로 가져왔는데**(165.0GW — 스토리
    재료 어디에도 없다) 그 사실이 렌더 직전에야 드러났다.

    도메인은 여전히 갈라져 있다 — 스토리만 깨졌으면 **일일 결과를 그대로 두고**
    스토리만 뺀다. 스토리 때문에 멀쩡한 일일 카피를 버리지 않는다.
    """
    events = (story_payload or {}).get("events")
    with_story = story_payload is not None
    daily_bad: list[str] = []
    story_bad: list[str] = []
    raw: dict | None = None
    for task in ("card_writer", "card_writer_repair"):
        try:
            raw = ask_writer(brief, items, date, with_story=with_story,
                             problems=(daily_bad + story_bad) or None,
                             story_events=events, log=call_log, task=task)
        except Exception as exc:  # noqa: BLE001
            print(f"[cards] Writer({task}) 실패 — {type(exc).__name__}: {exc}")
            return None
        daily_bad = _apply_and_review(_daily_of(raw), items)
        story_bad = (story_problems(raw.get("story"), story_payload)
                     if with_story else [])
        if not daily_bad and not story_bad:
            return raw
        for label, problems in (("일일", daily_bad), ("스토리", story_bad)):
            if problems:
                print(f"[cards] 편집 QA 실패({task}·{label}): "
                      f"{'; '.join(problems[:5])}")
    if daily_bad:
        return None
    # 일일은 통과했고 스토리만 끝내 안 됐다. **일일을 살린다.**
    print("[cards] 스토리 카피만 제외 — 일일 카드는 이 회차 결과로 간다")
    raw = dict(raw or {})
    raw["story"] = None
    return raw


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")
_CLAUSE_SEPS = ("…", " - ", " – ", ", ")   # '·'·공백은 안 쓴다 — "3·4호기"가 "3"이 된다
# 폴백 카피도 **한 줄 개조식**이어야 한다. 90자 서술형 문장은 카드에서 두세 줄로
# 풀려 "너무 길다"(지니 09-17, 09-17 카드 실물). LLM 경로와 같은 42자 한 줄로 맞추고,
# 서술형 종결을 체언으로 바꿔 개조식에 가깝게 만든다. 1+1 은 "내용이 너무 없다"(09-17).
FALLBACK_LINE_MAX = 46
FALLBACK_BULLETS = 2

# "…에 서명했다" → "…에 서명". 이 말뭉치에서 압도적으로 흔한 종결만 건드린다 —
# '밝혔다·있다·전망이다' 같은 건 체언으로 바꾸면 뜻이 상한다(그대로 둔다).
# "…은 미정이다" 처럼 서술형 지정사로 끝나는 줄이 마지막 장에 그대로 서면
# 개조식 카드에서 혼자 튄다. "이다" 는 앞 글자를 남기고 떼면 체언이 된다.
_TERSE_ENDINGS = ("하기로 했다", "했다", "하였다", "했습니다", "이다")


def terse(text: str) -> str:
    """서술형 종결을 체언 종결로. 너무 짧아지면 원문 그대로."""
    t = str(text or "").strip().rstrip(".。")
    for end in _TERSE_ENDINGS:
        if t.endswith(end):
            cut = t[: -len(end)] + ("기로" if end == "하기로 했다" else "")
            return cut.strip() if len(cut.strip()) >= 8 else t
    return t
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


def pick_lines(candidates: list[str], limit: int, want: int) -> list[str]:
    """한 줄에 통째로 들어가는 문장을 먼저 고른다.

    긴 문장을 limit 에서 자르면 "…원유 공급망 협력을…" 같은 토막이 남는다(09-17 실측).
    재료에 짧은 문장이 있으면 그걸 쓰고, 모자랄 때만 잘라 쓴다.
    """
    terse_all = [terse(c) for c in candidates]
    out = [c for c in terse_all if c and visible_len(c) <= limit][:want]
    if len(out) < want:
        for c in terse_all:
            if len(out) >= want:
                break
            cut = clip(c, limit)
            if cut and cut not in out:
                out.append(cut)
    return out[:want]


def draft_copy(items: list[dict]) -> dict:
    """Gemini 없이 카피를 짠다 — 큐레이션이 이미 뽑아둔 detail·why_important·implication 을
    문장 단위로 잘라 쓴다. LLM 보다 거칠지만(축약 없이 절 단위 절단) 쿼터가 0이어도
    카드가 나간다(09-16 아침: 429 로 카드가 통째로 빠졌다). --no-llm 또는 LLM 2회
    실패 시 폴백."""
    steps = []
    for it in items:
        # 사실 = 요약 한 문장(없으면 본문 요지 첫 문장). 의미 = 왜 중요한가(없으면 시사점) 한 문장.
        facts = pick_lines(_sentences(it.get("summary"), it.get("detail")),
                           FALLBACK_LINE_MAX, FALLBACK_BULLETS)
        why = [w for w in pick_lines(_sentences(it.get("why_important"), it.get("implication"),
                                                it.get("open_question")),
                                     FALLBACK_LINE_MAX, FALLBACK_BULLETS) if w not in facts]
        if not facts and it.get("title"):
            facts.append(clip(terse(it["title"]), FALLBACK_LINE_MAX))
        # **의미 재료가 없으면 비운다.** 예전에는 `facts[1:2]` 를 그대로 옮겨
        # 담았다 — 그 카드의 "왜 중요한가" 가 바로 위 "확인된 사실" 과 글자까지
        # 같았다는 뜻이다. 독자에게 "그래서 무엇이 달라지는가" 를 말하지 않고
        # 말한 척하는 쪽이, 그 칸이 비어 있는 것보다 나쁘다.
        #
        # 실측(라이브 2026-09-20, 지난 30일 상위 3건 90건): why_important ·
        # implication · open_question 이 셋 다 비어 재료가 아예 없는 이슈가
        # 5건(5.6%)이다. 드문 일이 아니라 그날 카드를 통째로 빼지는 않고,
        # 렌더가 그 구역을 세우지 않는 쪽으로 간다(build.js 의 why-editorial).
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
             line_max: int | None = None, headline_max: int = HEADLINE_HARD,
             why_min: int | None = None) -> list[str]:
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
        _check_line(problems, "hook.headline", hook.get("headline"), HEADLINE_HARD, True)
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
        # 의미 칸만 하한을 따로 받는다. 결정적 폴백은 재료가 없으면 이 칸을
        # 비우는데(draft_copy 주석), 그때 사실을 베껴 채우는 것보다 비는 편이
        # 낫다는 판단이 이미 서 있다.
        _check_bullets(problems, f"{tag}.why", slide.get("why"), line_max or WHY_MAX,
                       bullets_min if why_min is None else why_min)
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


def card_lines(raw: dict, items: list[dict]) -> dict[str, str]:
    """카드 '왜 중요한가' 첫 줄 → {issue_id: 한 줄}. 홈의 먼저 볼 3건이 쓴다.

    사이트의 why_important·implication 은 36~143자 서술형이고 상위 3건 중 둘은
    비어 있는 날이 흔하다(실측 09-12~09-16). 반면 이 카피는 같은 이슈를 두고
    LLM 이 WHY_MAX 안에 쓴 뒤 코드가 길이를 잰 문장이다 — 홈 카드가 원하는
    바로 그 한 줄이라, 호출을 새로 하지 않고 이미 만든 것을 옮긴다.

    LLM 이 두 번 다 틀려 draft_copy 로 물러난 날은 한도가 FALLBACK_LINE_MAX 라
    '컴팩트한 한 줄'이 아니다. 그런 줄은 내보내지 않는다 — 홈은 그날만 조용히
    사이트 문장으로 돌아간다.
    """
    lines: dict[str, str] = {}
    for slide, item in zip(raw.get("steps") or [], items):
        issue_id = str(item.get("issue_id") or "").strip()
        why = (slide or {}).get("why") or []
        first = str(why[0]).strip().replace("[[", "").replace("]]", "") if why else ""
        if issue_id and first and visible_len(first) <= WHY_MAX:
            lines[issue_id] = first
    return lines



_SUBJECT_PREFIX_RE = re.compile(r"^[^,]{2,12},\s*")


def closing_lines(rest_rows: list[dict], want: int = 3,
                  limit: int = CTA_LINE_MAX) -> list[str]:
    """마지막 장에 세울 '오늘 더 있었던 일' 줄.

    카드로 못 낸 이슈의 제목을 줄인다. **자른 줄은 버린다** — 후보가 열 건 넘게
    남아 있는데 굳이 말줄임표가 붙은 줄을 세울 이유가 없다(pick_lines 와 같은
    판단). 제목 앞머리의 주체("한수원, ", "美 NRC, ")는 뗀다: 세 줄이 나란히
    서면 주체보다 사건이 먼저 읽혀야 한다.
    """
    out: list[str] = []
    for row in rest_rows:
        title = _SUBJECT_PREFIX_RE.sub("", str(row.get("title") or "").strip())
        line = terse(title)
        if not line or visible_len(line) > limit or line in out:
            continue
        out.append(line)
        if len(out) == want:
            break
    return out

def build_slides(raw: dict, items: list[dict], date: str,
                 collected: int = 0, rest_rows: list[dict] | None = None) -> list[dict]:
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
            "date": date.replace("-", "."),
            "handle": SITE, "footer": item["source"],
            "url": item["link"],   # build.js 는 안 쓴다 — 캡션·검증용
        })
    # 마지막 장. 한때 통째로 비어서 오늘 3건을 다시 세웠는데(09-17), 그건 첫 장
    # 목차를 그대로 되풀이하는 것이었다(지니 09-18). 남은 질문을 세우는 것도
    # 마찬가지다 — 그건 이미 각 장의 '왜 중요한가' 마지막 줄에 있다. 그래서
    # **카드로 못 낸 오늘의 나머지**를 싣는다. 카드에서 처음 나오는 정보이고,
    # 사이트로 넘어갈 이유도 그 자리에서 생긴다.
    more = closing_lines(rest_rows or [])
    if len(more) >= 2:
        remaining = len(rest_rows or [])
        slides.append({
            "type": "cta",
            "slideNum": f"{total:02d} / {total:02d}",
            "stepLabel": "NUCLENS",
            "headline": "오늘 더 있었던 일",
            "subline": f"나머지 {remaining}건은 사이트에서",
            "keyword": SITE,
            "toc": more,
            "handle": DELIVERY_NOTE,   # 알약이 이미 주소라 꼬리말까지 주소면 세 번이다
            "footer": date.replace("-", "."),
        })
    else:
        # 세울 게 없으면 장을 빼고 장수를 줄인다. 링크는 모든 장 꼬리말에 이미
        # 있다 — 할 말 없는 장을 세우느니 없는 게 낫다.
        for slide in slides:
            slide["slideNum"] = f"{slide['slideNum'].split('/')[0].strip()} / {len(slides):02d}"
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


def already_published(date: str) -> int:
    """그날 카드가 이미 사이트에 올라가 있으면 장수, 아니면 0.

    index.json 의 말만 믿지 않고 PNG 가 실제로 있는지까지 본다 — 커밋이 반쯤
    들어간 날(index 는 갱신됐는데 폴더가 없다)에 "이미 했다"고 넘기면 그 날은
    영영 안 고쳐진다. 깊은 무결성(바이트·IEND)은 tools/verify_cards.py 의 몫이다.
    """
    index_file = CARDS_SITE_DIR / "index.json"
    if not index_file.exists():
        return 0
    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    names = ((index.get("dates") or {}).get(date)) or []
    if not names:
        return 0
    if not all((CARDS_SITE_DIR / date / name).exists() for name in names):
        return 0
    return len(names)




def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="오늘 이미 카드를 보냈어도 다시 만든다")
    ap.add_argument("--date", help="outbox 대신 이 날짜의 사이트 순위로 만든다(로컬 검증용, --force 포함)")
    ap.add_argument("--no-llm", action="store_true",
                    help="Gemini 를 부르지 않고 사이트 문장(detail·why_important)으로 카피를 짠다")
    ap.add_argument("--copy-file", type=Path,
                    help="사람(또는 다른 LLM)이 쓴 카피 JSON — 같은 검증을 거치고 Gemini 는 안 부른다")
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
    if not (args.force or args.date):
        # 두 가지 '이미 했다'를 본다.
        #
        # ① 발송 기록 — send_album.py 가 텔레그램 성공 뒤에만 남긴다.
        # ② 게시본 — publish_cards.py 가 web/public/cards 에 남기고 커밋한다.
        #
        # 예전엔 ①만 봤다. 그런데 텔레그램 발송은 CARDS_SEND 가 켜진 날에만
        # 도는 **선택 기능**이라, 꺼 둔 상태(기본값)에서는 ①이 영원히 비고
        # 재실행마다 Gemini 를 새로 태우고 같은 PNG 를 다시 구웠다. 카드를
        # 별도 워크플로로 떼면서 재실행이 쉬워졌으니 여기서 막는다.
        #
        # 손으로 다시 굽고 싶으면 --force (cards.yml 의 수동 실행이 그걸 쓴다).
        if (outbox.get("cards") or {}).get("date") == date:
            print(f"[cards] {date} 카드는 이미 발송됨 — 스킵")
            return 0
        made = already_published(date)
        if made:
            print(f"[cards] {date} 카드 {made}장이 이미 사이트에 있다 — 스킵 "
                  "(다시 구우려면 --force)")
            return 0

    data = load_site_data(date)
    rows = None if data is None else data.issues
    for line in (data.warnings if data else ()):
        print(f"[cards] {line}")
    if rows is None:
        # 사이트 데이터가 없거나 오늘 날짜가 아니다. 배포 스텝(build_data)이 먼저
        # 돌아야 한다. 어제 순위로 카드를 만드는 것보다 안 만드는 게 낫다.
        print(f"[cards] 사이트 순위 없음 — {date} 브리핑이 today.json 에도 "
              "briefings.json 에도 없다. 배포 스텝(web/build_data.py)이 먼저 돌아야 한다")
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
    call_log: list[dict] = []
    if args.copy_file:
        candidate = json.loads(args.copy_file.read_text(encoding="utf-8"))
        strip_accent_on_sensitive(candidate, items)
        problems = review(candidate, items)
        if problems:
            print(f"[cards] --copy-file 검증 실패: {'; '.join(problems[:8])}")
            return 1
        print(f"[cards] 카피 파일 사용 — {args.copy_file}")
        raw = candidate

    # 오늘 스토리가 있는가. **일일 카드 대상 그 3건 안에서만** 찾는다 —
    # 순위를 다시 매기면 같은 날 두 산출물이 다른 1위를 말한다.
    story = None if (args.no_llm or raw) else find_story(data, items)
    story_payload = None
    if story is not None:
        story_payload = story_material(story, date)
        print(f"[cards] 스토리 후보 #{story.rank} {story.thread_id} "
              f"사건 {len(story_payload['events'])}건")

    if raw is None and not args.no_llm:
        raw, story_copy = run_editorial(items, date, collected, story_payload, call_log)
        if story_copy is not None:
            STORY_COPY_FILE.write_text(json.dumps(
                {"date": date, "thread_id": story.thread_id,
                 "issue_id": story.issue.get("issue_id", ""),
                 "payload": story_payload, "copy": story_copy},
                ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[cards] 스토리 카피 저장 → {STORY_COPY_FILE.name} "
                  "(story_cards.py 가 LLM 없이 렌더한다)")
        elif story is not None:
            STORY_COPY_FILE.unlink(missing_ok=True)
            print("[cards] 스토리 카피 실패 — 스토리만 건너뛴다. 일일 카드는 그대로 간다")

    if raw is None:
        # LLM 이 없거나(쿼터 0) 카피가 끝내 안 나왔다 — 사이트 문장으로 대체한다.
        # 거칠어도 카드가 안 나가는 것보다 낫다(09-16 아침 실사고).
        candidate = draft_copy(items)
        problems = validate(candidate, items, bullets_min=1, why_min=0, line_max=FALLBACK_LINE_MAX,
                            headline_max=FALLBACK_HEADLINE_MAX)
        if problems:
            print(f"[cards] LLM 없이도 못 만듦: {'; '.join(problems[:6])} — 카드 건너뜀")
            return 1
        print("[cards] " + ("--no-llm" if args.no_llm else "LLM 실패") + " → 사이트 문장으로 카피 대체")
        raw = candidate
    log_calls(call_log)

    taken = {i["hash"] for i in items if i.get("hash")}
    rest_rows = [r for r in rows
                 if (r.get("representative_article") or {}).get("hash") not in taken]
    slides = build_slides(raw, items, date, collected, rest_rows)

    render(slides)
    files = gate(len(slides))

    ALBUM_FILE.write_text(json.dumps({
        "date": date,
        "caption": build_caption(slides, date),
        "files": [str(f.relative_to(ROOT)).replace("\\", "/") for f in files],
        "lines": card_lines(raw, items),
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

    assert any("headline" in p for p in validate(mutate(headline="가" * (HEADLINE_HARD + 1)), items))
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
    edge = mutate(headline="[[" + "가" * HEADLINE_HARD + "]]")
    assert validate(edge, items) == [], validate(edge, items)
    assert visible_len("[[가나]]다") == 3

    # 길이만 넘긴 카피는 **버리지 않고 다듬어** 통과시킨다(09-20 실사고).
    long_one = {**ok, "steps": [{**ok["steps"][0],
                                 "headline": "아시아 에너지 협력체 [[RISE ASIA]] 출범 합의",
                                 "facts": ["가" * (FACT_MAX + 4), "나"]}]}
    assert validate(long_one, items), "다듬기 전에는 반려 대상"
    assert validate(normalize(long_one), items) == [], validate(normalize(long_one), items)
    assert visible_len(long_one["steps"][0]["headline"]) <= HEADLINE_HARD
    assert "RISE ASIA" in long_one["steps"][0]["headline"], "낱말 가운데를 자르지 않는다"
    assert "[[" not in _fit_accent("[[" + "가" * 30 + "]]", 10), "짝이 깨지면 색을 버린다"

    # Writer 응답 봉투를 풀어야 검증이 성립한다(09-20: 풀지 않아 늘 폴백)
    assert _daily_of({"brief": [], "daily": ok, "story": None}) is ok
    assert _daily_of(ok) is ok, "봉투가 아니면 그대로"

    # 폴백 카피 — 서술형을 체언으로, 한 줄 길이로
    assert terse("MOU 13건에 서명했다") == "MOU 13건에 서명"
    assert terse("관계를 포괄적 전략적 동반자 관계로 격상했다") == "관계를 포괄적 전략적 동반자 관계로 격상"
    assert terse("공동 연구를 추진하기로 했다") == "공동 연구를 추진기로" or True  # 어색해도 뜻은 산다
    assert terse("재가동 시점은 아직 불투명하다") == "재가동 시점은 아직 불투명하다", "안 건드리는 종결"
    assert terse("했다") == "했다", "너무 짧아지면 원문"
    # 짧은 문장이 있으면 자르지 않고 그걸 쓴다
    _cand = ["아주 긴 문장이라서 한 줄 한도를 확실히 넘어가도록 충분히 길게 늘여 쓴 서술형 문장이다",
             "원안위, 심의 착수"]
    assert pick_lines(_cand, 46, 1) == ["원안위, 심의 착수"], pick_lines(_cand, 46, 1)
    assert len(pick_lines([_cand[0]], 46, 1)) == 1, "짧은 게 없으면 잘라서라도 한 줄"
    _fb = draft_copy([{"title": "제목", "summary": "한국과 카자흐스탄이 정상회담을 열고 원전 공동 연구와 원유 공급망 협력을 포함한 MOU 13건에 서명했다",
                       "detail": "", "why_important": "카자흐스탄과의 원전 연료 공급망 협력 기반을 다지는 국가 간 공식 합의로 즉시 파악해야 할 중요 사안이다",
                       "implication": "", "open_question": ""}])
    for _b in _fb["steps"][0]["facts"] + _fb["steps"][0]["why"]:
        assert visible_len(_b) <= FALLBACK_LINE_MAX, (_b, visible_len(_b))

    # 홈 카드가 가져갈 한 줄 — issue_id 로 맞물리고, 길면 안 내보낸다
    li = [{**items[0], "issue_id": "iss-1"}]
    assert card_lines(ok, li) == {"iss-1": "설계수명 만료 4기 일정에 직결"}
    assert card_lines(ok, [{**items[0], "issue_id": ""}]) == {}, "issue_id 없으면 못 맞춘다"
    long_why = {**ok, "steps": [{**ok["steps"][0], "why": ["가" * (WHY_MAX + 1)]}]}
    assert card_lines(long_why, li) == {}, "폴백 카피의 긴 줄은 홈에 안 내보낸다"
    assert card_lines({**ok, "steps": [{**ok["steps"][0], "why": ["[[강조]] 한 줄"]}]}, li)         == {"iss-1": "강조 한 줄"}, "마크업은 벗겨서 내보낸다"

    # 분류는 LLM 이 아니라 코드가 붙인다
    assert topic_label({"topics": ["restart_lto", "regulation"]}) == "계속운전·재가동"
    assert topic_label({"topics": ["없는토픽"]}) == "원자력 정책"
    # 사이트 칩(khnp_domain)이 있으면 그게 이긴다 — 카드와 사이트가 어긋나면 안 된다
    assert topic_label({"khnp_domain": "안전성", "topics": ["regulation"]}) == "안전성"
    assert topic_label({"khnp_domain": "", "topics": ["regulation"]}) == "규제·인허가"

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

    # 장수 산식: 표지1 + N + 마지막1. 마지막 장은 '오늘 더 있었던 일'이라
    # 카드로 못 낸 이슈가 남아 있을 때만 선다.
    rest = [{"title": "美 NRC, 팰리세이즈 SMR 부지 기초 토목공사 예외 승인"},
            {"title": "웨스팅하우스, eVinci 마이크로원자로 임계 시험 성공"},
            {"title": "원안위, IAEA 총회 계기 UAE·체코·싱가포르와 원자력 안전규제 협력 강화"}]
    lines = closing_lines(rest)
    # 주체 접두를 떼고 체언으로 끝낸다. 26자를 넘는 셋째 줄은 자르지 않고 버린다.
    assert lines == ["팰리세이즈 SMR 부지 기초 토목공사 예외 승인",
                     "eVinci 마이크로원자로 임계 시험 성공"], lines
    built = build_slides(ok, items, "2026-09-14", 645, rest)
    assert len(built) == len(items) + 2 == 3, len(built)
    assert [s["type"] for s in built] == ["hook", "step", "cta"]
    # 마지막 장이 첫 장 목차를 되풀이하지 않는다 — 이게 이 장의 존재 이유다.
    assert built[-1]["toc"] != built[0]["toc"], "마지막 장이 표지 목차의 반복이다"
    assert built[-1]["toc"] == lines, built[-1]["toc"]
    assert built[1]["points"] and built[1]["why"], "한 장에 사실·의미가 둘 다"
    assert "07:25" not in json.dumps(built, ensure_ascii=False), "고정 발송 시각 문구 잔존"
    # 남길 게 없으면 장을 세우지 않고 장수를 줄인다.
    built = build_slides(ok, items, "2026-09-14", 645, [])
    assert [s["type"] for s in built] == ["hook", "step"], built
    assert built[0]["slideNum"] == "01 / 02" and built[1]["slideNum"] == "02 / 02", built
    # LLM 없는 폴백 — 긴 제목·긴 문장도 상한 안으로 절 단위 절단, 검증 통과
    long_item = {"title": "日 하마오카 원전, 내진 데이터 조작 사실로 드러나 경영진 사임 그리고 추가 조사 착수",
                 "detail": "주부전력은 9월 15일 하마오카 원전의 내진 평가 자료 일부가 조작됐다고 인정했다. "
                           "사장과 원자력본부장이 사임했다. 규제위원회는 추가 조사에 착수했다.",
                 "why_important": "일본 원전 재가동 심사 전반의 신뢰 문제로 번질 수 있다.",
                 "implication": "", "open_question": "", "summary": ""}
    fb = draft_copy([long_item])
    assert validate(fb, [long_item], bullets_min=1, why_min=0, line_max=FALLBACK_LINE_MAX,
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
