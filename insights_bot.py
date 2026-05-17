"""
주간 인사이트 추출 봇

매주 일요일 21시 (KST) 실행.
지난 7일 노션 데일리 핵심목표 달력 DB의 페이지 본문에서
감사일기 / 나와의 대화 / 오늘의 인사이트 / TO DO LIST 텍스트 수집 →
Gemini API로 패턴·키워드·추천 액션 추출 → 텔레그램 발송.

환경 변수:
    NOTION_TOKEN
    GEMINI_API_KEY
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID         (없으면 기본 채널)
    INSIGHTS_CHAT_ID         (선택, 개인 인사이트 전용 채널)
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Windows 콘솔 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# ----- 설정 -----
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("INSIGHTS_CHAT_ID") or os.environ["TELEGRAM_CHAT_ID"]

# 데일리 핵심목표 달력 DB
DAILY_DB_ID = "1624010f-1bdd-81ad-aee3-e39e4c5faa7b"

KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = 7

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# 추출할 토글 제목들
TARGET_TOGGLES = {"감사일기", "나와의 대화", "오늘의 인사이트", "TO DO LIST", "예산&정산"}


# ----- Notion fetch helpers -----
def notion_query_daily(start_date: str, end_date: str) -> list[dict]:
    """지난 7일치 데일리 row 가져오기"""
    url = f"https://api.notion.com/v1/databases/{DAILY_DB_ID}/query"
    body = {
        "filter": {
            "and": [
                {"property": "날짜", "date": {"on_or_after": start_date}},
                {"property": "날짜", "date": {"on_or_before": end_date}},
            ]
        },
        "sorts": [{"property": "날짜", "direction": "ascending"}],
        "page_size": 50,
    }
    r = requests.post(url, headers=NOTION_HEADERS, json=body, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def extract_text(rich_text_list: list[dict]) -> str:
    if not rich_text_list:
        return ""
    return "".join(rt.get("plain_text", "") for rt in rich_text_list)


def fetch_block_children(block_id: str) -> list[dict]:
    """블록 children 재귀적으로 가져오기 (페이지내 모든 텍스트)"""
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    r = requests.get(url, headers=NOTION_HEADERS, params={"page_size": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def extract_page_content(page_id: str) -> dict[str, str]:
    """페이지 본문에서 target 토글 내용 추출"""
    result = {k: "" for k in TARGET_TOGGLES}
    try:
        blocks = fetch_block_children(page_id)
        for b in blocks:
            if b.get("type") != "toggle":
                continue
            title = extract_text(b.get("toggle", {}).get("rich_text", []))
            # 일치하는 토글이름 찾기
            matched = None
            for tgt in TARGET_TOGGLES:
                if tgt in title:
                    matched = tgt
                    break
            if not matched:
                continue
            # 토글 안 내용 가져오기
            if b.get("has_children"):
                child_texts = []
                try:
                    children = fetch_block_children(b["id"])
                    for c in children:
                        ctype = c.get("type")
                        if ctype in ("paragraph", "bulleted_list_item", "to_do"):
                            t = extract_text(c.get(ctype, {}).get("rich_text", []))
                            if t.strip():
                                checked = ""
                                if ctype == "to_do":
                                    checked = "✅ " if c["to_do"].get("checked") else "▢ "
                                child_texts.append(checked + t)
                except Exception as e:
                    print(f"  Sub-fetch fail: {e}")
                result[matched] = "\n".join(child_texts)
    except Exception as e:
        print(f"Fetch fail for {page_id}: {e}")
    return result


# ----- Gemini API -----
def call_gemini(prompt: str) -> str:
    """Gemini 2.0 Flash (무료 티어)"""
    if not GEMINI_API_KEY:
        return "(Gemini API key 없음)"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-exp:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2048,
        },
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, timeout=60)
            r.raise_for_status()
            data = r.json()
            if "candidates" in data and data["candidates"]:
                parts = data["candidates"][0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return "(응답 없음)"
        except Exception as e:
            print(f"Gemini try {attempt+1} fail: {e}")
            time.sleep(2)
    return "(Gemini 호출 실패)"


# ----- Telegram send -----
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # 4096 자 제한, 여유 두고 분할
    CHUNK = 3800
    while text:
        chunk = text[:CHUNK]
        # 줄 단위 자르기
        if len(text) > CHUNK:
            last_nl = chunk.rfind("\n")
            if last_nl > CHUNK * 0.5:
                chunk = chunk[:last_nl]
        body = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=body, timeout=30)
        if r.status_code != 200:
            print(f"Telegram send fail: {r.status_code} {r.text}")
        text = text[len(chunk):]
        time.sleep(0.5)


# ----- Main -----
def main() -> int:
    now = datetime.now(KST)
    end_date = now.date()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS - 1)
    print(f"인사이트 추출: {start_date} ~ {end_date}")

    # 1. 노션에서 7일치 row 가져오기
    rows = notion_query_daily(start_date.isoformat(), end_date.isoformat())
    print(f"조회된 데일리 row: {len(rows)}")
    if not rows:
        send_telegram("📊 <b>주간 인사이트</b>\n\n지난 주 데일리 기록이 없어 추출할 게 없습니다.")
        return 0

    # 2. 각 row의 페이지 본문 텍스트 모으기
    weekly_data = []
    for row in rows:
        date_str = row["properties"].get("날짜", {}).get("date", {}).get("start", "")
        content = extract_page_content(row["id"])
        weekly_data.append({"date": date_str, **content})
        time.sleep(0.3)  # rate limit

    # 3. Gemini 프롬프트 생성
    days_text = []
    for d in weekly_data:
        block = [f"[{d['date']}]"]
        for k in ["TO DO LIST", "감사일기", "나와의 대화", "오늘의 인사이트", "예산&정산"]:
            v = d.get(k, "").strip()
            if v:
                block.append(f"- {k}: {v[:500]}")
        if len(block) > 1:
            days_text.append("\n".join(block))
    full_log = "\n\n".join(days_text)

    if not full_log.strip():
        send_telegram("📊 <b>주간 인사이트</b>\n\n지난 주 작성한 내용이 비어있습니다.")
        return 0

    prompt = f"""당신은 한 사람의 자기관리 코치입니다. 아래는 지난 7일간 사용자가 매일 작성한 일기·회고·할 일 기록입니다.
이를 분석해서 다음을 추출해주세요. 따뜻하지만 솔직한 톤으로.

[추출할 것]
1. **반복되는 키워드 5개** — 자주 언급되는 단어·주제 (한 줄씩)
2. **숨겨진 패턴 3개** — 사용자가 자기 자신은 잘 모르는 패턴 (예: "수요일마다 컨디션 다운", "감사일기에 가족 자주 등장" 등)
3. **이번 주의 한 줄 정의** — 이번 주를 한 단어/한 줄로 표현한다면
4. **다음 주 추천 액션 2개** — 데이터 기반 구체적 제안
5. **칭찬할 점 1개** — 사용자가 잘한 것 (구체적으로)

[형식]
- 한국어 / 친구처럼 따뜻한 톤 / 이모지 적당히
- HTML 텍스트 (Telegram parse_mode=HTML 용)
- <b>제목</b>, 개행은 \\n
- 마크다운(**, ##) 금지
- 텍스트만 (코드블록 X)

[데이터]
{full_log}

[출력 시작]
"""

    print("Gemini 호출 중...")
    insight = call_gemini(prompt)

    # 4. 텔레그램 발송
    header = (
        f"📊 <b>주간 인사이트</b>  "
        f"<i>({start_date} ~ {end_date})</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    full_msg = header + insight.strip()
    send_telegram(full_msg)
    print("발송 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
