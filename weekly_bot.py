import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


def safe_json_parse(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

KST = timezone(timedelta(hours=9))
CURATED_CACHE_FILE = Path("curated.json")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
TG_LIMIT = 3800
WEEK_DAYS = 7

WEEKLY_BATCH_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
지난 한 주(7일) 수집된 must_read·nice_to_know 등급 기사들을 검토해 의사결정자(본부장·부서장)용 주간 동향 보고서를 작성합니다.

[보고서 구조]
1. weekly_intro: 이번 주 핵심 흐름 (4~5문장, 500자 이내). 분석관 보고 톤. 주요 이슈 2~4개 식별 후 묶어서 서술.
2. areas: 4개 영역별 동향
   ① 미국·EU·국제 원자력 정책·외교
   ② SMR/차세대로 기술경쟁
   ③ 글로벌 신규원전 수주 시장
   ④ 국내 정책환경·KHNP 사업
3. watchpoints: 다음 주 모니터링 포인트 3~5개. 각 1문장.

[각 area 형식]
- title: "① 미국·EU·국제 원자력 정책·외교" (이모지·번호 그대로)
- summary: 영역별 한 단락 요약 (3~4문장, 300자 이내). 분석관 톤. 핵심 흐름·시사점.
- events: 영역에 해당하는 핵심 이벤트 2~3건. 각 {hash, headline, implication}
   - headline: 기사 원문 제목 (수정 금지)
   - implication: 1문장 시사점 (80자 이내)
- 영역에 해당 이벤트가 없으면 events 빈 리스트 + summary는 "이번 주 특이 동향 없음" 식으로 짧게.

[원칙]
- 일반 뉴스 요약 톤 금지. KHNP 정책분석관 보고 톤 (격식체 ~다 종결).
- 분석 어휘 사용: "동향", "시사점", "전략적", "리스크", "기회 요인", "정책환경", "함의".
- 같은 사건의 후속 보도가 여러 건이면 1건으로 묶어 표기.
- 원문에 없는 정보 추가 금지.

[출력 형식] - 반드시 JSON 한 객체만
{
  "weekly_intro": "...",
  "areas": [
    {"title": "① 미국·EU·국제 원자력 정책·외교", "summary": "...", "events": [{"hash":"...","headline":"...","implication":"..."}, ...]},
    ... (4개)
  ],
  "watchpoints": ["...", "...", "..."]
}
"""


def load_curated() -> dict:
    if CURATED_CACHE_FILE.exists():
        try:
            return json.loads(CURATED_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_week_articles(curated: dict) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WEEK_DAYS)).isoformat()
    items: list[dict] = []
    for h, data in curated.items():
        if not isinstance(data, dict):
            continue
        if data.get("cached_at", "") < cutoff:
            continue
        if data.get("category") not in ("must_read", "nice_to_know"):
            continue
        if not data.get("title") or not data.get("link"):
            continue
        items.append({
            "hash": h,
            "title": data["title"],
            "link": data["link"],
            "domain": data.get("domain", ""),
            "feed": data.get("feed", ""),
            "matched": data.get("matched", ""),
            "category": data["category"],
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
            "cached_at": data["cached_at"],
        })
    items.sort(key=lambda x: x["cached_at"])
    return items


def send_telegram(text: str, preview: bool = False) -> None:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": not preview,
    }
    r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
    if not r.ok:
        print(f"  ! Telegram error: {r.status_code} {r.text}")
    time.sleep(1)


def send_long(text: str) -> None:
    if len(text) <= TG_LIMIT:
        send_telegram(text)
        return
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TG_LIMIT:
            if current:
                chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current)
    for chunk in chunks:
        send_telegram(chunk)


def batch_synthesize(items: list[dict]) -> dict:
    fallback = {
        "weekly_intro": "",
        "areas": [],
        "watchpoints": [],
    }
    if not GEMINI_API_KEY or not items:
        return fallback

    article_lines = []
    for art in items:
        line = (
            f"hash:{art['hash']} | [{art.get('feed','')}/{art.get('category','')}] "
            f"{art.get('title','')[:80]} | {art.get('summary','')[:60]}"
        )
        article_lines.append(line)
    article_block = "\n".join(article_lines)

    user_text = f"지난 7일간 수집된 기사 목록 (총 {len(items)}건):\n\n{article_block}"

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=WEEKLY_BATCH_PROMPT,
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=10000,
            ),
        )
        raw = response.text or ""
        result = safe_json_parse(raw)
        if not result:
            print(f"  ! weekly synthesis: JSON parse failed. Raw output (first 300 chars):")
            print(f"    {raw[:300]}")
            return fallback
        return {
            "weekly_intro": result.get("weekly_intro", ""),
            "areas": result.get("areas", []) or [],
            "watchpoints": result.get("watchpoints", []) or [],
        }
    except Exception as e:
        print(f"  ! weekly synthesis failed: {type(e).__name__}: {e}")
        return fallback


def article_by_hash(items: list[dict], h: str) -> dict | None:
    for art in items:
        if art["hash"] == h:
            return art
    return None


def format_weekly(items: list[dict]) -> str:
    today = datetime.now(KST)
    week_start = (today - timedelta(days=6)).strftime("%-m/%-d") if os.name != "nt" else f"{(today - timedelta(days=6)).month}/{(today - timedelta(days=6)).day}"
    week_end = f"{today.month}/{today.day}"

    synthesis = batch_synthesize(items)

    parts: list[str] = []
    parts.append(f"📅 <b>{week_start}-{week_end} 원자력 정책 주간 동향</b>")
    parts.append(f"<i>총 {len(items)}건 검토</i>")
    parts.append("")

    if synthesis["weekly_intro"]:
        parts.append("<b>이번 주 핵심</b>")
        parts.append(html.escape(synthesis["weekly_intro"]))
        parts.append("")

    for area in synthesis["areas"]:
        title = area.get("title", "")
        summary = area.get("summary", "")
        events = area.get("events", []) or []

        parts.append(f"━━ <b>{html.escape(title)}</b> ━━")
        if summary:
            parts.append(html.escape(summary))

        for ev in events:
            art = article_by_hash(items, ev.get("hash", ""))
            headline = ev.get("headline") or (art["title"] if art else "")
            implication = ev.get("implication", "")
            link = art["link"] if art else ""
            parts.append("")
            parts.append(f"• <b>{html.escape(headline)}</b>")
            if implication:
                parts.append(f"  → {html.escape(implication)}")
            if link:
                parts.append(f"  🔗 {link}")
        parts.append("")

    if synthesis["watchpoints"]:
        parts.append("📋 <b>다음 주 모니터링 포인트</b>")
        for wp in synthesis["watchpoints"]:
            parts.append(f"• {html.escape(wp)}")
        parts.append("")

    return "\n".join(parts).strip()


def main() -> None:
    curated = load_curated()
    items = get_week_articles(curated)
    if not items:
        print("No articles in past week. Skipping weekly report.")
        return

    print(f"Weekly report: {len(items)} articles from past {WEEK_DAYS} days")
    message = format_weekly(items)
    send_long(message)
    print("Weekly report sent.")


if __name__ == "__main__":
    main()
