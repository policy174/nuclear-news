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
DIGEST_QUEUE_FILE = Path("digest_queue.json")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
TG_LIMIT = 3800

MAX_DIGEST_ITEMS = 12  # 하루 다이제스트 최대 아이템 수 (ANS 풍 압축 큐레이션)

SECTION_ORDER = ["khnp", "domestic", "international", "smr"]
SECTION_LABEL = {
    "khnp": "🇰🇷 한수원 동향",
    "domestic": "🏛️ 국내 동향",
    "international": "🌐 해외 동향",
    "smr": "🔋 SMR 동향",
}
CATEGORY_ORDER = ["정책", "기술", "시장", "규제"]
CATEGORY_EMOJI = {"정책": "🏛", "기술": "⚙️", "시장": "📈", "규제": "📋"}

DIGEST_BATCH_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
오늘 수집된 nice_to_know 등급 기사들을 검토해 의사결정자(본부장·부서장)용 일일 브리핑을 작성합니다.

[수행할 일]
1. intro: 오늘의 큰 흐름을 2~3개 정책 영역으로 묶어 한 단락 (3~4문장, 300자 이내). 분석관 보고 톤. 격식체 (~다 종결).
   - 분석 어휘: "시사점", "전략적", "정책환경", "기회 요인", "리스크"
2. policy_signal: 정부·규제기관·국제기구·주요 사업자 의사결정자(장관·위원장·DG·CEO 등)의 명시적 직접 인용("...")이 있는 발언이 있으면 1건 선정. 단순 정책 동향이나 일반 코멘트는 제외. 인용 가치 없으면 null.
3. tomorrow_watch: 다음 24~72시간 내 주목할 일정·발표 1~2건. 없으면 빈 문자열.

[policy_signal 형식·규칙]
- quote: 30단어 이내 직접 인용. 영문 원문이면 한국어 번역 (직역 우선)
- quote_original: 영문 원문 (없으면 빈 문자열)
- speaker: "라파엘 그로시(Rafael Grossi) IAEA 사무총장" 형식 (한글 + 원문 병기, 직위 포함)
- source_event: "5/6 COP 부속회의" 같은 발화 맥락
- implication: 20자 이내 시사점, KHNP 분석관 시각

[원칙]
- 일반 뉴스 큐레이션 톤 금지. KHNP 정책분석관 보고 톤.
- 원문에 없는 정보 추가 금지 (환각 금지).

[출력 형식] - 반드시 JSON 한 객체만
{
  "intro": "...",
  "policy_signal": {"quote": "...", "quote_original": "...", "speaker": "...", "source_event": "...", "implication": "..."} 또는 null,
  "tomorrow_watch": "..."
}
"""


def load_queue() -> list:
    if DIGEST_QUEUE_FILE.exists():
        try:
            return json.loads(DIGEST_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_queue(queue: list) -> None:
    DIGEST_QUEUE_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_importance(item: dict) -> str:
    """신·구 형식 호환 — 'importance' 신, 'category'(레거시) 구 형식."""
    if "importance" in item:
        return item["importance"]
    cat = item.get("category", "")
    if cat in {"must_read", "nice_to_know", "market", "noise"}:
        return cat
    return "nice_to_know"


def send_telegram(text: str, preview: bool = True) -> None:
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


def send_long(text: str, preview: bool = False) -> None:
    if len(text) <= TG_LIMIT:
        send_telegram(text, preview=preview)
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
        send_telegram(chunk, preview=preview)


def batch_curate(queue_items: list) -> dict:
    fallback = {"intro": "", "policy_signal": None, "tomorrow_watch": ""}
    if not GEMINI_API_KEY or not queue_items:
        return fallback

    article_lines = []
    for art in queue_items:
        section = art.get("section", "domestic")
        cat = art.get("category", "정책")
        impl = art.get("implication", "")[:60]
        line = f"[{section}/{cat}] {art.get('title','')[:80]} | {art.get('summary','')[:50]} | {impl}"
        article_lines.append(line)
    article_block = "\n".join(article_lines)
    user_text = f"오늘 수집된 기사 목록 (총 {len(queue_items)}건):\n\n{article_block}"

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=DIGEST_BATCH_PROMPT,
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=2500,
            ),
        )
        raw = response.text or ""
        result = safe_json_parse(raw)
        if not result:
            print(f"  ! batch curation: JSON parse failed. Raw output: {raw[:300]}")
            return fallback
        return {
            "intro": result.get("intro", ""),
            "policy_signal": result.get("policy_signal"),
            "tomorrow_watch": result.get("tomorrow_watch", ""),
        }
    except Exception as e:
        print(f"  ! batch curation failed: {type(e).__name__}: {str(e)[:200]}")
        return fallback


def fmt_tags(tags: list) -> str:
    return " ".join(html.escape(t) for t in tags or [])


def render_item(art: dict) -> list[str]:
    original_title = art.get("title", "")
    title_kr = art.get("title_kr") or original_title
    show_original = title_kr.strip() != original_title.strip()

    implication = html.escape(art.get("implication", ""))
    domain = html.escape(art.get("domain", ""))
    related = art.get("related_reports") or []

    lines = [f"<b>{html.escape(title_kr)}</b>"]
    if show_original:
        lines.append(f"  <i>{html.escape(original_title)}</i>")
    if implication:
        lines.append(f"  → {implication}")
    if related:
        lines.append(f"  📚 <i>{', '.join(html.escape(r) for r in related)}</i>")
    if domain:
        lines.append(f"  <i>{domain}</i>")
    lines.append(f"  🔗 {art.get('link','')}")
    return lines


def rank_item(art: dict) -> float:
    imp = get_importance(art)
    base = 10.0 if imp == "must_read" else 5.0
    # 한수원 섹션 가중
    if art.get("section") == "khnp":
        base += 2.0
    # 1차 소스 가중 (도메인 점수 활용)
    domain = art.get("domain", "")
    if any(d in domain for d in ["iaea.org", "world-nuclear-news", "khnp.co.kr", "nssc.go.kr", "motie.go.kr", "nrc.gov"]):
        base += 2.0
    # 관련 보고서 매칭 가중
    if art.get("related_reports"):
        base += 1.0
    return base


def format_digest(queue: list) -> str:
    today = datetime.now(KST)
    nice_items_all = [a for a in queue if get_importance(a) != "market"]
    market_items = [a for a in queue if get_importance(a) == "market"]

    # 하루 상한 적용 — ANS 풍 압축 큐레이션
    if len(nice_items_all) > MAX_DIGEST_ITEMS:
        ranked = sorted(nice_items_all, key=rank_item, reverse=True)
        nice_items = ranked[:MAX_DIGEST_ITEMS]
        print(f"Capped: {len(nice_items_all)} candidates → top {MAX_DIGEST_ITEMS}")
    else:
        nice_items = nice_items_all

    curation = batch_curate(nice_items)

    parts: list[str] = []
    parts.append(f"☀️ <b>{today.month}/{today.day} 원자력 일일 브리핑</b>")
    excluded = len(nice_items_all) - len(nice_items)
    count_line = f"<i>총 {len(nice_items)}건"
    if excluded > 0:
        count_line += f" (후보 {len(nice_items_all)}건 중 상위 선별)"
    if market_items:
        count_line += f" + 시장 {len(market_items)}건"
    count_line += "</i>"
    parts.append(count_line)
    parts.append("")

    if curation["intro"]:
        parts.append(html.escape(curation["intro"]))
        parts.append("")

    sig = curation.get("policy_signal")
    if sig and sig.get("quote") and sig.get("speaker"):
        parts.append("━━━━━━━━━━")
        parts.append("💬 <b>오늘의 한 줄</b>")
        parts.append("")
        parts.append(f'<i>"{html.escape(sig["quote"])}"</i>')
        speaker_line = f"— {html.escape(sig['speaker'])}"
        if sig.get("source_event"):
            speaker_line += f", {html.escape(sig['source_event'])}"
        parts.append(speaker_line)
        if sig.get("quote_original"):
            parts.append("")
            parts.append(f'<code>{html.escape(sig["quote_original"])}</code>')
        if sig.get("implication"):
            parts.append("")
            parts.append(f"📌 {html.escape(sig['implication'])}")
        parts.append("")

    by_section: dict[str, list] = {s: [] for s in SECTION_ORDER}
    for art in nice_items:
        sec = art.get("section", "domestic")
        if sec not in by_section:
            sec = "domestic"
        by_section[sec].append(art)

    for section in SECTION_ORDER:
        items = by_section[section]
        if not items:
            continue

        parts.append("━━━━━━━━━━")
        parts.append(f"<b>{SECTION_LABEL[section]}</b> ({len(items)}건)")
        parts.append("")

        by_category: dict[str, list] = {c: [] for c in CATEGORY_ORDER}
        for a in items:
            c = a.get("category", "정책")
            if c not in by_category:
                c = "정책"
            by_category[c].append(a)

        for category in CATEGORY_ORDER:
            cat_items = by_category[category]
            if not cat_items:
                continue
            parts.append(f"{CATEGORY_EMOJI[category]} <b>[{category}]</b>")
            for i, art in enumerate(cat_items, 1):
                item_lines = render_item(art)
                parts.append(f"{i}. {item_lines[0]}")
                parts.extend(item_lines[1:])
                parts.append("")

    if market_items:
        parts.append("━━━━━━━━━━")
        parts.append("📈 <b>시장·주식</b> (참고용)")
        parts.append("")
        for art in market_items:
            title = html.escape(art.get("title", ""))
            domain = html.escape(art.get("domain", ""))
            parts.append(f"• {title} <i>({domain})</i>")
            parts.append(f"  🔗 {art.get('link', '')}")
        parts.append("")

    if curation.get("tomorrow_watch"):
        parts.append("━━━━━━━━━━")
        parts.append("🔍 <b>내일 주목</b>")
        parts.append(html.escape(curation["tomorrow_watch"]))

    return "\n".join(parts).strip()


def main() -> None:
    queue = load_queue()
    if not queue:
        print("Queue is empty. Skipping digest.")
        return

    print(f"Digest: {len(queue)} items")
    message = format_digest(queue)
    send_long(message, preview=False)
    save_queue([])
    print("Digest sent and queue cleared.")


if __name__ == "__main__":
    main()
