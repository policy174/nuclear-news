import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

KST = timezone(timedelta(hours=9))
LOOKBACK_HOURS = 6
DEDUP_RETENTION_DAYS = 14
STATE_FILE = Path("sent.json")
KEYWORDS_FILE = Path("keywords.json")
CURATED_CACHE_FILE = Path("curated.json")
DIGEST_QUEUE_FILE = Path("digest_queue.json")

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

DOMAIN_SCORE = {
    "hani.co.kr": 9, "chosun.com": 9, "joongang.co.kr": 9,
    "donga.com": 9, "khan.co.kr": 9, "hankookilbo.com": 9,
    "kmib.co.kr": 9, "munhwa.com": 9, "seoul.co.kr": 9,
    "mk.co.kr": 8, "hankyung.com": 8, "etnews.com": 8,
    "sedaily.com": 8, "fnnews.com": 8, "edaily.co.kr": 7,
    "mt.co.kr": 7, "asiae.co.kr": 7, "businesspost.co.kr": 7,
    "electimes.com": 9, "ekn.kr": 9, "energy-news.co.kr": 8,
    "epj.co.kr": 8, "energytimes.kr": 8, "energydaily.co.kr": 7,
    "yna.co.kr": 8, "newsis.com": 7, "news1.kr": 7, "yonhapnewstv.co.kr": 7,
    "kbs.co.kr": 7, "imbc.com": 7, "sbs.co.kr": 7, "ytn.co.kr": 7,
    "jtbc.co.kr": 7, "tvchosun.com": 6, "ichannela.com": 6, "mbn.co.kr": 6,
    "newspim.com": 5, "ajunews.com": 5,
}
DEFAULT_SCORE = 4
MIN_SCORE = 4

TIER1_DOMAINS = {
    "nssc.go.kr", "motie.go.kr", "msit.go.kr", "korea.kr",
    "khnp.co.kr", "kaeri.re.kr", "kins.re.kr", "korad.or.kr",
    "iaea.org", "world-nuclear.org", "world-nuclear-news.org",
    "oecd-nea.org", "nrc.gov",
}

ANTI_TITLE_PATTERNS = [
    re.compile(r"\[(보도자료|알림|공지|기업\s*소식|새소식|광고|포토|화보|부고)\]"),
]
ANTI_KEYWORDS: list[str] = []  # 주식 관련은 LLM이 market 카테고리로 분류

KR_SLD = (".co.kr", ".or.kr", ".go.kr", ".ne.kr", ".re.kr", ".ac.kr")

CURATION_SYSTEM_PROMPT = """당신은 한국 원자력정책실의 뉴스 큐레이터입니다. 정책 실무자에게 진짜 핵심만 골라내야 합니다.

[must_read] - 즉시 알아야 하는 핵심. 하루 평균 0~3건. 매우 엄격하게 선별.
- 정부·규제기관 공식 의결·고시·시행령·법안 본회의 통과 (원안위, 산업부, 과기정통부, 국회, IAEA, NRC, KINS)
- 신규 원전 부지 결정·인허가 발급, 계속운전 확정, SMR 표준설계인가 발급
- 사고·중대 안전 이슈 (INES 등급 사건, 정전, 고장, 누출)
- 한미 원자력협정·재처리·농축 등 외교 핵심 결정·체결
- 체코·폴란드·UAE·사우디 원전 수출 계약 체결·확정 (협상 단계는 nice_to_know)
- 전력수급기본계획 확정·공청회·고시
- 국회 본회의 통과 법안·예산 확정 (단순 발의·심사는 nice_to_know)

[nice_to_know] - 알아두면 좋음. 대부분이 여기 해당.
- 정책 동향·진척 보도, 기관 보고서·연구 결과
- 의원 법안 발의·심사 (통과 전 단계)
- 칼럼·사설·전문가 인터뷰·기명 기고 (must_read 아님)
- 외국 SMR 회사 마일스톤 (NuScale, TerraPower 등)
- 학회 발표, 분기 실적, 회의 결과 (의결 아닌 일반 회의)
- 단순 사업 진척 보고·중간 단계

[market] - 주식·증권·시장 관련. 링크만 추적.
- 원전株, 테마주, 관련주, 수혜주
- 기업 주가 분석·전망, 증권사 리포트
- 코스피/코스닥 시황 코멘트

[noise] - 거를 것
- 보도자료 단순 재탕, 외신 단순 번역
- 기업 PR·ESG·CSR 단순 홍보, 행사 스케치, 사진 기사
- 중복·우라까이

[중요 원칙]
- must_read 분류는 매우 엄격하게. 의심스러우면 nice_to_know.
- 칼럼·사설·전문가 의견은 절대 must_read 아님.
- "발표 예정", "전망", "분석", "검토" 같은 추측·전망성 기사는 must_read 아님.
- 의결·확정·체결·통과 같은 완료된 의사결정만 must_read.

[출력 규칙]
- 반드시 JSON 한 객체만 출력 (다른 텍스트 금지)
- 원문에 없는 정보는 절대 추가하지 말 것 (환각 금지)
- summary: 한 문장 50자 이내, 핵심만
- why_important: must_read 일 때만 작성. 정책실 실무자 시각에서 2~3문장 200자 이내. 다른 경우 빈 문자열
- tags: # 으로 시작하는 3개 이내

[출력 형식]
{"category":"must_read|nice_to_know|market|noise","summary":"...","why_important":"...","tags":["#태그1","#태그2"]}
"""


def get_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if not host:
        return ""
    if host.endswith(KR_SLD):
        return ".".join(host.split(".")[-3:])
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_score(url: str) -> int:
    d = get_domain(url)
    if d in TIER1_DOMAINS:
        return 10
    return DOMAIN_SCORE.get(d, DEFAULT_SCORE)


def is_tier1(url: str) -> bool:
    return get_domain(url) in TIER1_DOMAINS


def is_promotional(title: str, description: str) -> bool:
    if any(p.search(title) for p in ANTI_TITLE_PATTERNS):
        return True
    text = title + " " + description
    return any(kw in text for kw in ANTI_KEYWORDS)


def normalize_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]|\([^)]+\)", "", title)
    title = re.sub(r"[^\w가-힣]", "", title)
    return title.lower()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    return load_json(STATE_FILE, {"sent": {}})


def save_state(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    state["sent"] = {h: ts for h, ts in state["sent"].items() if ts > cutoff}
    save_json(STATE_FILE, state)


def load_curated() -> dict:
    return load_json(CURATED_CACHE_FILE, {})


def save_curated(curated: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    curated = {k: v for k, v in curated.items() if v.get("cached_at", "") > cutoff}
    save_json(CURATED_CACHE_FILE, curated)


def load_queue() -> list:
    return load_json(DIGEST_QUEUE_FILE, [])


def save_queue(queue: list) -> None:
    save_json(DIGEST_QUEUE_FILE, queue)


def search_naver(query: str, display: int = 30) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": "date"}
    r = requests.get(NAVER_URL, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("items", [])


def send_telegram(text: str) -> bool:
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
    if not r.ok:
        print(f"  ! Telegram error: {r.status_code} {r.text}")
        return False
    time.sleep(1)
    return True


def passes_anchor_filter(title: str, description: str, anchors: list[str]) -> bool:
    if not anchors:
        return True
    haystack = (title + " " + description).lower()
    return any(a.lower() in haystack for a in anchors)


_gemini_client = None


def get_gemini():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        return _gemini_client
    except Exception as e:
        print(f"  ! Gemini init failed: {e}")
        return None


def curate_with_llm(title: str, description: str, domain: str, force_must_read: bool = False) -> dict:
    """LLM 호출. 실패 시 안전한 fallback 반환."""
    fallback = {
        "category": "must_read" if force_must_read else "nice_to_know",
        "summary": title[:50],
        "why_important": "",
        "tags": [],
    }
    client = get_gemini()
    if not client:
        return fallback

    user_text = f"제목: {title}\n요약: {description}\n출처: {domain}"
    if force_must_read:
        user_text += "\n\n참고: 이 기사는 정부·규제기관·국제기구 1차 소스이므로 must_read로 분류하고 summary와 why_important만 작성하세요."

    try:
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=CURATION_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=400,
            ),
        )
        result = json.loads(response.text)
        category = result.get("category", "nice_to_know")
        if force_must_read:
            category = "must_read"
        return {
            "category": category if category in {"must_read", "nice_to_know", "market", "noise"} else "nice_to_know",
            "summary": (result.get("summary") or "")[:80],
            "why_important": (result.get("why_important") or "")[:300],
            "tags": [t for t in result.get("tags", []) if isinstance(t, str)][:3],
        }
    except Exception as e:
        print(f"  ! curate failed for '{title[:30]}': {e}")
        return fallback


def collect_articles(feed_name: str, keywords: list[str], anchors: list[str], state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    by_title: dict[str, dict] = {}

    for kw in keywords:
        try:
            items = search_naver(kw)
        except Exception as e:
            print(f"  ! [{feed_name}] '{kw}' search failed: {e}")
            continue

        for item in items:
            link = item.get("originallink") or item.get("link")
            if not link:
                continue

            try:
                pub = parsedate_to_datetime(item["pubDate"])
            except Exception:
                continue
            if pub < cutoff:
                continue

            h = url_hash(link)
            if h in state["sent"]:
                continue

            title = strip_html(item.get("title", ""))
            desc = strip_html(item.get("description", ""))

            if is_promotional(title, desc):
                continue
            if not passes_anchor_filter(title, desc, anchors):
                continue

            score = domain_score(link)
            if score < MIN_SCORE:
                continue

            norm = normalize_title(title)
            if not norm:
                continue

            existing = by_title.get(norm)
            if existing and existing["score"] >= score:
                continue

            by_title[norm] = {
                "hash": h,
                "title": title,
                "description": desc,
                "link": link,
                "pub": pub,
                "matched": kw,
                "score": score,
                "domain": get_domain(link),
                "feed": feed_name,
            }
        time.sleep(0.1)

    return sorted(by_title.values(), key=lambda x: x["pub"])


def format_must_read(article: dict, curation: dict) -> str:
    feed = article["feed"]
    tags_prefix = f"[{feed}]"
    if article["matched"] == "한국수력원자력":
        tags_prefix += "[한수원]"

    title = html.escape(article["title"])
    summary = html.escape(curation.get("summary", ""))
    why = html.escape(curation.get("why_important", ""))
    tag_str = " ".join(curation.get("tags", []))

    parts = [f"🔴 <b>{tags_prefix}</b> {title}"]
    if summary:
        parts.append(f"\n📌 {summary}")
    if why:
        parts.append(f"\n💡 {why}")
    if tag_str:
        parts.append(f"\n🏷 {html.escape(tag_str)}")
    parts.append(f"\n🔗 {article['link']}")
    return "".join(parts)


def main() -> None:
    config = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    curated = load_curated()
    queue = load_queue()

    sent_immediate = 0
    queued = 0
    dropped = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for feed_name, feed_cfg in config.items():
        kw_list = feed_cfg["keywords"]
        anchors = feed_cfg.get("anchors", [])
        print(f"[{feed_name}] {len(kw_list)} keywords")
        articles = collect_articles(feed_name, kw_list, anchors, state)
        print(f"[{feed_name}] {len(articles)} candidates after Phase 1 filter")

        for article in articles:
            h = article["hash"]

            if h in curated:
                cur = curated[h]
            else:
                cur = curate_with_llm(
                    article["title"], article["description"],
                    article["domain"],
                    force_must_read=is_tier1(article["link"]),
                )
                cur["cached_at"] = now_iso
                curated[h] = cur
                time.sleep(7)  # Gemini 무료 티어 RPM 제한 (10 RPM)

            category = cur.get("category", "nice_to_know")

            if category == "noise":
                state["sent"][h] = now_iso
                dropped += 1
                continue

            if category == "must_read":
                ok = send_telegram(format_must_read(article, cur))
                if ok:
                    state["sent"][h] = now_iso
                    sent_immediate += 1
            else:
                queue.append({
                    "hash": h,
                    "title": article["title"],
                    "link": article["link"],
                    "domain": article["domain"],
                    "feed": article["feed"],
                    "matched": article["matched"],
                    "category": category,
                    "summary": cur.get("summary", ""),
                    "tags": cur.get("tags", []),
                    "queued_at": now_iso,
                })
                state["sent"][h] = now_iso
                queued += 1

    save_state(state)
    save_curated(curated)
    save_queue(queue)
    print(f"Done. immediate={sent_immediate} queued={queued} dropped={dropped}")


if __name__ == "__main__":
    main()
