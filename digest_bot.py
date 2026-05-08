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

DIGEST_BATCH_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
오늘 수집된 nice_to_know 등급 기사들을 검토해 의사결정자(본부장·부서장)용 일일 브리핑을 작성합니다.

[모니터링 핵심 토픽]
① 미국·EU·국제 원자력 정책·외교 (NRC·DOE 정책, 한미·미영 ATOMBRIDGE·AUKUS, EU SMR 얼라이언스, IAEA)
② SMR/차세대로 기술경쟁 (i-SMR·NuScale·TerraPower·X-energy·Holtec, 포스코·현대건설·두산)
③ 글로벌 신규원전 수주 (체코·폴란드·UAE·사우디·베트남)
④ 국내 정책환경·KHNP 사업 (전기본, 계속운전, 신한울, 사용후핵연료, 고준위방폐장)
⑤ 핵연료주기·기후·에너지 전망 (123협정, 러시아 폐쇄형, COP·NDC, IEA WEO, WNA, AI in nuclear)

[수행할 일]
1. intro: 오늘의 큰 흐름을 2~3개 정책 영역으로 묶어 한 단락 (3~4문장, 300자 이내). 분석관 보고 톤.
2. policy_signal: 정부·규제기관·국제기구·주요 사업자 의사결정자(장관·위원장·DG·CEO 등)의 명시적 직접 인용("...")이 있는 발언이 있으면 1건 선정. 형식 아래 참조. 명시적 인용이 없으면 null. 단순 정책 동향이나 일반 코멘트는 제외.
3. main: 의사결정에 가장 영향 큰 1건의 hash 선정 + 3~4문장 심층 분석 (300자 이내).
   - 구성: (a) 핵심 사실 → (b) KHNP·한국 정책환경 시사점 → (c) 후속 모니터링 포인트
4. sub: 다음으로 중요한 2~3건의 hash 선정 + 각 1문장 시사점 (100자 이내).
5. checkpoint_hashes: 나머지 기사 hash 목록 (제목만 노출).
6. market 카테고리는 따로 처리하므로 무시.

[policy_signal 형식·규칙]
- quote: 30단어 이내 직접 인용. 영문 원문이면 한국어 번역 (직역 우선, 핵심 단어 보존)
- speaker: "라파엘 그로시(Rafael Grossi) IAEA 사무총장" 형식 (한글 + 원문 병기, 직위 포함)
- source_event: "5/6 COP 부속회의" 같은 발화 맥락
- implication: 20자 이내 시사점, KHNP 분석관 시각
- 인용 가치 없으면 null로 둘 것. 억지 선정 금지.

[원칙·톤]
- 일반 뉴스 요약 톤 금지. KHNP 정책분석관 보고 톤 (격식체, ~다 종결).
- 분석 어휘 사용: "시사점", "전략적", "정책환경", "기회 요인", "리스크", "후속 조치", "함의", "정책적 의미"
- 원문에 없는 정보 추가 금지 (환각 금지).
- 분량 제약 엄격 준수.

[출력 형식] - 반드시 JSON 한 객체만
{
  "intro": "...",
  "policy_signal": {"quote": "...", "speaker": "...", "source_event": "...", "implication": "..."} 또는 null,
  "main": {"hash": "abc123", "analysis": "..."},
  "sub": [{"hash": "def456", "analysis": "..."}, ...],
  "checkpoint_hashes": ["...", "..."]
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
    """전체 큐를 LLM에 batch 호출하여 intro + main/sub/checkpoint 분류·분석."""
    fallback = {
        "intro": "",
        "policy_signal": None,
        "main": None,
        "sub": [],
        "checkpoint_hashes": [a["hash"] for a in queue_items],
    }
    if not GEMINI_API_KEY or not queue_items:
        return fallback

    article_lines = []
    for art in queue_items:
        line = f"hash:{art['hash']} | [{art.get('feed','')}] {art.get('title','')[:80]} | {art.get('summary','')[:60]}"
        article_lines.append(line)
    article_block = "\n".join(article_lines)

    user_text = f"오늘 수집된 기사 목록 (총 {len(queue_items)}건):\n\n{article_block}"

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=DIGEST_BATCH_PROMPT,
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=8000,
            ),
        )
        raw = response.text or ""
        result = safe_json_parse(raw)
        if not result:
            print(f"  ! batch curation: JSON parse failed. Raw output (first 300 chars):")
            print(f"    {raw[:300]}")
            return fallback
        return {
            "intro": result.get("intro", ""),
            "policy_signal": result.get("policy_signal"),
            "main": result.get("main"),
            "sub": result.get("sub", []) or [],
            "checkpoint_hashes": result.get("checkpoint_hashes", []) or [],
        }
    except Exception as e:
        print(f"  ! batch curation failed: {type(e).__name__}: {e}")
        return fallback


def article_by_hash(items: list, h: str) -> dict | None:
    for art in items:
        if art.get("hash") == h:
            return art
    return None


def fmt_tags(tags: list) -> str:
    return " ".join(html.escape(t) for t in tags or [])


def format_digest(queue: list) -> str:
    today = datetime.now(KST)
    nice_items = [a for a in queue if a.get("category") != "market"]
    market_items = [a for a in queue if a.get("category") == "market"]

    curation = batch_curate(nice_items)

    parts: list[str] = []
    parts.append(f"☀️ <b>{today.month}/{today.day} 원자력 일일 브리핑</b>")
    parts.append("")

    if curation["intro"]:
        parts.append(f"<i>{html.escape(curation['intro'])}</i>")
        parts.append("")

    sig = curation.get("policy_signal")
    if sig and sig.get("quote") and sig.get("speaker"):
        parts.append("■ <b>금일의 정책 시그널</b>")
        parts.append(f"<i>\"{html.escape(sig['quote'])}\"</i>")
        speaker_line = f"— {html.escape(sig['speaker'])}"
        if sig.get("source_event"):
            speaker_line += f", {html.escape(sig['source_event'])}"
        parts.append(speaker_line)
        if sig.get("implication"):
            parts.append(f"<b>시사점:</b> {html.escape(sig['implication'])}")
        parts.append("")

    used_hashes = set()
    main = curation.get("main")
    if main and main.get("hash"):
        art = article_by_hash(nice_items, main["hash"])
        if art:
            used_hashes.add(art["hash"])
            tags = fmt_tags(art.get("tags", []))
            parts.append("🔷 <b>오늘의 메인</b>")
            parts.append(f"<b>[{html.escape(art.get('feed',''))}] {html.escape(art.get('title',''))}</b>")
            if main.get("analysis"):
                parts.append(html.escape(main["analysis"]))
            meta = []
            if tags:
                meta.append(tags)
            if art.get("domain"):
                meta.append(f"<i>{html.escape(art['domain'])}</i>")
            if meta:
                parts.append(" · ".join(meta))
            parts.append(f"🔗 {art.get('link','')}")
            parts.append("")

    sub_list = curation.get("sub", [])
    if sub_list:
        parts.append("🔹 <b>서브</b>")
        for sub in sub_list:
            art = article_by_hash(nice_items, sub.get("hash", ""))
            if not art:
                continue
            used_hashes.add(art["hash"])
            tags = fmt_tags(art.get("tags", []))
            parts.append(f"• <b>[{html.escape(art.get('feed',''))}] {html.escape(art.get('title',''))}</b>")
            if sub.get("analysis"):
                parts.append(f"  → {html.escape(sub['analysis'])}")
            meta_bits = []
            if tags:
                meta_bits.append(tags)
            if art.get("domain"):
                meta_bits.append(f"<i>{html.escape(art['domain'])}</i>")
            if meta_bits:
                parts.append(f"  {' · '.join(meta_bits)}")
            parts.append(f"  🔗 {art.get('link','')}")
        parts.append("")

    checkpoint = [a for a in nice_items if a["hash"] not in used_hashes]
    if checkpoint:
        parts.append("📋 <b>체크포인트</b>")
        for art in checkpoint:
            tags = fmt_tags(art.get("tags", []))
            line = f"• [{html.escape(art.get('feed',''))}] {html.escape(art.get('title',''))}"
            if tags:
                line += f" {tags}"
            parts.append(line)
            parts.append(f"  {art.get('link','')}")
        parts.append("")

    if market_items:
        parts.append("📈 <b>시장·주식</b> (참고용)")
        for art in market_items:
            title = html.escape(art.get("title", ""))
            domain = html.escape(art.get("domain", ""))
            parts.append(f"• {title} <i>({domain})</i>")
            parts.append(f"  {art.get('link', '')}")
        parts.append("")

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
