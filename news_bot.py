import difflib
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

import feedparser
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
REPORTS_KB_FILE = Path("reports_kb.json")
EMBEDDINGS_CACHE_FILE = Path("embeddings.json")
SEMANTIC_DEDUP_THRESHOLD = 0.85
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
    "oecd-nea.org", "nrc.gov", "ans.org",
}

RSS_SOURCES = [
    {"url": "https://www.iaea.org/feeds/topnews", "name": "IAEA Top News",
     "domain_label": "iaea.org"},
    {"url": "http://www.world-nuclear-news.org/rss", "name": "WNN",
     "domain_label": "world-nuclear-news.org"},
    {"url": "https://www.ans.org/news/feed/", "name": "ANS Nuclear Newswire",
     "domain_label": "ans.org"},
    {"url": "https://news.google.com/rss/search?q=site:khnp.co.kr&hl=ko&gl=KR&ceid=KR:ko",
     "name": "한수원 보도자료", "domain_label": "khnp.co.kr"},
    {"url": "https://news.google.com/rss/search?q=site:nssc.go.kr&hl=ko&gl=KR&ceid=KR:ko",
     "name": "원안위 보도자료", "domain_label": "nssc.go.kr"},
    {"url": "https://news.google.com/rss/search?q=site:motie.go.kr&hl=ko&gl=KR&ceid=KR:ko",
     "name": "산업부 보도자료", "domain_label": "motie.go.kr"},
    {"url": "https://news.google.com/rss/search?q=site:kaeri.re.kr&hl=ko&gl=KR&ceid=KR:ko",
     "name": "원자력연구원 보도자료", "domain_label": "kaeri.re.kr"},
]
SMR_HINTS = ("smr", "small modular", "i-smr", "advanced reactor")

ANTI_TITLE_PATTERNS = [
    re.compile(r"\[(보도자료|알림|공지|기업\s*소식|새소식|광고|포토|화보|부고|기획|특집|인사|동정)\]"),
]
ANTI_KEYWORDS: list[str] = [
    "원자력병원", "원자력 병원", "원자력 시계",
    "인사 발령", "인사발령", "임원 인사", "신년사", "취임사",
    "채용 공고", "채용공고", "직원 채용", "신입 채용", "신입사원 채용",
    "경력 채용", "경력채용", "임원 채용", "인재 모집", "수시채용",
    "MOU 체결식", "협약 체결식", "기념식",
    "동호회", "체육대회", "야유회",
    "청사 이전", "사옥 이전", "조직 개편 안내",
]
MIN_DESCRIPTION_LEN = 30  # 본문 길이 필터 - 이보다 짧으면 stub으로 보고 드롭

KR_SLD = (".co.kr", ".or.kr", ".go.kr", ".ne.kr", ".re.kr", ".ac.kr")

CURATION_SYSTEM_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
의사결정자(본부장·부서장)에게 보고하는 분석관 톤으로 작성합니다. 일반 뉴스 큐레이션 톤 금지.

[3가지 분류 모두 수행]

A. importance (중요도) - 발송 방식 결정
- must_read: 즉시 알아야 함. 하루 평균 0~3건. 매우 엄격.
   · 정부·규제기관 공식 의결·고시·시행령·법안 본회의 통과
   · 주요국 행정명령·정책 발표
   · 신규 원전 부지 결정·인허가 발급, 계속운전 확정, SMR 표준설계인가 발급
   · 사고·중대 안전 이슈 (INES 등급, 정전, 누출)
   · 양자 협력 협정 체결·결정 (한미·미영 등)
   · 글로벌 수주 EPC 계약 체결·확정 (협상 단계는 nice_to_know)
   · 전력수급기본계획 확정·고시
- nice_to_know: 맥락·동향. 정책 함의 있는 기사만.
- market: 주식·증권·테마주·시황·증권사 리포트
- noise: **적극 거름. 의심스러우면 noise.**
   · 보도자료 단순 재탕, 외신 단순 번역, 우라까이
   · 기업 PR·ESG·CSR 홍보, 행사 스케치, 시상식·축사
   · **정치 일반**: 대선·총선·지선, 정쟁, 정치인 갈등·비판 성명, 여야 공방
   · **원자력이 본질이 아닌 기사**: 원자력이 부수적으로만 언급되고 본문은 다른 주제 (산업 일반·외교 일반·거시 경제·사회 일반)
   · **단순 발언·견해**: 정책 의사결정자가 아닌 학자·시민단체·일반 칼럼니스트 발언
   · 학회 일반 (춘추계 학술대회 등 정책 함의 없는 단순 행사)
   · 지역 동향 (지역 행사·민원·동호회·시민단체 일반)
   · 인사 발령·동정·축하·부고
   · **기관 행정 일반**: 채용 공고·직원 모집·인재 채용·임원 인사, 청사 이전·조직 개편 단순 안내, 회계연도 일반 행정
   · **정부 사이트라 해도 본질이 회의 결과·의결·정책 발표가 아닌 경우** (예: 회의 결과인데 안건이 채용·청사·내부 행정·일반 공지)

** 의결·확정·체결·통과·발급된 사실만 must_read. 칼럼·전망·검토·예정은 절대 must_read 아님. **
** 원자력이 단순 키워드로만 등장하고 본질이 다른 주제면 무조건 noise. **

B. section (주제 영역) - 어느 섹션에 들어갈지
- smr: SMR/소형모듈원자로 관련 모든 뉴스 (행위자 무관). i-SMR, NuScale, TerraPower, X-energy, Holtec, Kairos, Oklo, AP300, eVinci, EU SMR 얼라이언스, 포스코 SMR, 현대건설 SMR 등.
- khnp: 한수원(한국수력원자력)이 주체이거나 핵심 행위자 (SMR 제외). 체코·폴란드 APR1400 수주, 신한울/새울/고리/한빛/한울 운영, 한수원 보도자료 등.
- domestic: 한국 정부·규제기관·국회 (한수원·SMR 제외). 산업부, 원안위(NSSC), KINS, 과기정통부, 국회 입법, 11차 전기본 등.
- international: 그 외 모든 글로벌 동향 (한국·SMR 무관). IAEA·NRC·DOE·EU·OECD/NEA, 외국 정부 정책, 해외 운영사 동향.

** 우선순위: SMR > 한수원 > 국내 > 해외. 같은 기사가 SMR이면서 한수원이면 SMR. **

C. category (세부 카테고리) - 4가지 중 하나
- 정책: 정부·국가 단위 의사결정, 외교, 다자기구 정책 결정 (IAEA, OECD/NEA 등)
- 기술: 노형·핵연료주기·안전기술·R&D·표준설계
- 시장: 신규 발주·EPC 계약·인수합병·자본·발전사업자 동향
- 규제: 인허가·안전기준·환경평가·NRC·NSSC 의결

[필드별 출력 - 모든 텍스트 필드는 한국어로 작성. 원문이 영문이어도 한국어로 옮길 것.]

- title_kr: 한국어 제목 (30~60자). 원문이 영문이면 자연스러운 한국어로 번역. 원문이 한국어면 핵심을 살린 정확한 한국어 제목. 인명·기관명 첫 등장 시 한글(원문) 병기.

- summary: 빈 문자열 (사용 안 함).

- implication: 시사점 1문장 (60자 이내). nice_to_know·must_read만 작성. 핵심 함의만 압축.

- why_important: must_read만 작성. **1~2문장, 150자 이내**. 분석관 톤. 격식체. 핵심 시사점만 압축. 절대 길게 풀어쓰지 말 것.

- watch_next: 빈 문자열 (사용 안 함).

- tags: # 으로 시작 3개 이내. 예: #한미협정 #체코수주 #SMR경쟁

- related_reports: 사용자 메시지에 [관련 사내 보고서] 섹션이 있고 실제 분석에 참조한 보고서가 있으면 보고서 제목 리스트(최대 2개). 참조 안 했거나 보고서 섹션이 없으면 빈 리스트.

[관련 사내 보고서 활용]
- 사용자 메시지 끝에 [관련 사내 보고서] 섹션이 있으면 분석에 활용.
- 동일 주제·맥락이면 implication 또는 why_important에 사내 시각과 일관성 있게 작성 (보고서를 명시적으로 인용할 필요는 없으나 톤·관점 통일).
- 보고서가 실제로 의미 있게 참조된 경우만 related_reports 채울 것. 단순 키워드 일치는 제외.

[원칙]
- **모든 텍스트 필드는 한국어**. 영문 원문 입력이 들어와도 한국어로 작성.
- 원문에 없는 정보 추가 금지 (환각 금지).
- 일반 뉴스 요약 톤 금지. KHNP 정책분석관 보고 톤.

[출력 형식] - 반드시 JSON 한 객체만
{
  "importance": "must_read|nice_to_know|market|noise",
  "section": "smr|khnp|domestic|international",
  "category": "정책|기술|시장|규제",
  "title_kr": "...",
  "summary": "...",
  "implication": "...",
  "why_important": "...",
  "watch_next": "...",
  "tags": ["#태그1", "#태그2"],
  "related_reports": ["보고서 제목 1", "..."]
}
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


def is_stub(description: str) -> bool:
    return len(description.strip()) < MIN_DESCRIPTION_LEN


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


def load_reports_kb() -> list[dict]:
    if REPORTS_KB_FILE.exists():
        try:
            data = json.loads(REPORTS_KB_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def find_relevant_reports(title: str, description: str, kb: list[dict], top_k: int = 3) -> list[dict]:
    """기사 제목·요약과 가장 관련 있는 사내 보고서 top_k개 반환 (점수 기반)."""
    if not kb:
        return []
    text = (title + " " + description).lower()
    scored: list[tuple[float, dict]] = []
    for report in kb:
        score = 0.0
        for tag in report.get("topic_tags") or []:
            if isinstance(tag, str) and tag.lower() in text:
                score += 3.0
        rtitle = (report.get("title") or "").lower()
        for word in re.findall(r"[가-힣]{2,}|[a-zA-Z]{3,}", rtitle):
            if word in text:
                score += 1.0
        rsum = (report.get("summary") or "").lower()
        for word in re.findall(r"[가-힣]{3,}", rsum)[:30]:
            if word in text:
                score += 0.3
        if score > 0:
            scored.append((score, report))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def load_embeddings_cache() -> dict:
    if EMBEDDINGS_CACHE_FILE.exists():
        try:
            return json.loads(EMBEDDINGS_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_embeddings_cache(cache: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    cache = {k: v for k, v in cache.items() if v.get("cached_at", "") > cutoff}
    EMBEDDINGS_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )


def cosine_sim(a: list[float], b: list[float]) -> float:
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def get_or_compute_embedding(text: str, cache_key: str, cache: dict) -> list[float] | None:
    if cache_key in cache and cache[cache_key].get("vec"):
        return cache[cache_key]["vec"]
    client = get_gemini()
    if not client:
        return None
    try:
        result = client.models.embed_content(
            model="text-embedding-004",
            contents=text,
        )
        if not result.embeddings:
            return None
        vec = list(result.embeddings[0].values)
        cache[cache_key] = {
            "vec": vec,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        return vec
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            print(f"  ! embedding quota exceeded")
        else:
            print(f"  ! embedding failed for '{text[:40]}': {type(e).__name__}")
        return None


def semantic_dedup(articles: list[dict], emb_cache: dict, threshold: float = SEMANTIC_DEDUP_THRESHOLD) -> list[dict]:
    """임베딩 cosine similarity로 의미 중복 제거. 점수 높은 것 우선 유지."""
    if len(articles) < 2:
        return articles

    enriched: list[tuple[dict, list[float] | None]] = []
    for art in articles:
        emb = get_or_compute_embedding(art["title"], art["hash"], emb_cache)
        enriched.append((art, emb))
        time.sleep(0.3)

    enriched.sort(key=lambda x: x[0]["score"], reverse=True)

    kept: list[tuple[dict, list[float] | None]] = []
    for art, emb in enriched:
        if emb is None:
            kept.append((art, emb))
            continue
        is_dup = False
        for kept_art, kept_emb in kept:
            if kept_emb is None:
                continue
            if cosine_sim(emb, kept_emb) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append((art, emb))

    return [art for art, _ in kept]


JUDGE_SYSTEM_PROMPT = """당신은 한국수력원자력 정책개발부 큐레이터입니다.
다음 기사가 정책분석관에게 업무상 의미 있는지 1차 판단하세요.

[유용함 (useful=1)]
- 원자력 정책·외교·기술·시장·규제 관련 실질 내용
- 정부·규제기관 의결·고시·법안·행정명령
- 한수원·KHNP 사업 동향, 글로벌 SMR·수주 시장
- 한미·미영·EU 양자/다자 협력
- 사용후핵연료·고준위방폐장·계속운전 등

[유용하지 않음 (useful=0)]
- 채용·인사 발령·동정·축사·기념식·시상
- 정치 일반 (대선·총선·정쟁·여야 공방)
- 원자력이 부수적으로만 언급되고 본질은 다른 주제 (산업 일반·외교 일반·거시경제)
- 단순 행사 스케치, 보도자료 단순 PR
- 학회 일반 (정책 함의 없는 학술 발표)
- 지역 동향 (지역 행사·민원·동호회·시민단체 일반)
- 채용공고, 청사 이전 등 일반 행정

[출력] JSON 하나만:
{"useful": 0 또는 1, "reason": "10자 이내"}"""


def llm_judge(title: str, description: str) -> tuple[bool, str]:
    """경량 사전 필터. 실패하면 보수적으로 통과(True) 반환."""
    client = get_gemini()
    if not client:
        return True, ""
    try:
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"제목: {title}\n요약: {description[:300]}",
            config=types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=80,
            ),
        )
        result = safe_json_parse(response.text or "")
        if not result:
            return True, ""
        useful = bool(int(result.get("useful", 1)))
        reason = (result.get("reason") or "")[:30]
        return useful, reason
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" not in msg and "429" not in msg:
            print(f"  ! judge failed for '{title[:30]}': {type(e).__name__}")
        return True, ""


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


def search_naver(query: str, negative_terms: str = "", display: int = 30) -> list[dict]:
    full_query = f"{query} {negative_terms}".strip() if negative_terms else query
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": full_query, "display": display, "sort": "date"}
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


VALID_IMPORTANCE = {"must_read", "nice_to_know", "market", "noise"}
VALID_SECTIONS = {"smr", "khnp", "domestic", "international"}
VALID_CATEGORIES = {"정책", "기술", "시장", "규제"}


def curate_with_llm(title: str, description: str, domain: str, force_must_read: bool = False, relevant_reports: list[dict] | None = None) -> dict:
    """LLM 호출. 실패 시 안전한 fallback 반환."""
    fallback = {
        "importance": "must_read" if force_must_read else "nice_to_know",
        "section": "domestic",
        "category": "정책",
        "title_kr": title,
        "summary": title[:50],
        "implication": "",
        "why_important": "",
        "watch_next": "",
        "tags": [],
        "related_reports": [],
    }
    client = get_gemini()
    if not client:
        return fallback

    user_text = f"제목: {title}\n요약: {description}\n출처: {domain}"
    if force_must_read:
        user_text += "\n\n참고: 이 기사는 정부·규제기관·국제기구 1차 소스입니다. **본문이 의결·정책 발표·중대 결정·인허가 등 정책 함의 있는 경우만 must_read**. 채용·일반 행정·공지·축사·시상 등은 noise로 분류하세요."

    if relevant_reports:
        user_text += "\n\n[관련 사내 보고서]\n"
        for r in relevant_reports:
            title_r = r.get("title", "")
            date_r = r.get("date", "")
            summary_r = (r.get("summary") or "")[:250]
            date_suffix = f" ({date_r})" if date_r else ""
            user_text += f"- 「{title_r}」{date_suffix}: {summary_r}\n"

    try:
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=CURATION_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=3000,
            ),
        )
        result = safe_json_parse(response.text or "")
        if not result:
            print(f"  ! curate JSON parse failed for '{title[:30]}'")
            return fallback
        importance = result.get("importance", "nice_to_know")
        # Tier 1이라도 LLM이 noise/market/nice_to_know로 분류하면 그대로 존중 (강제 must_read 안 함)
        section = result.get("section", "domestic")
        category = result.get("category", "정책")
        title_kr = (result.get("title_kr") or "").strip() or title
        return {
            "importance": importance if importance in VALID_IMPORTANCE else "nice_to_know",
            "section": section if section in VALID_SECTIONS else "domestic",
            "category": category if category in VALID_CATEGORIES else "정책",
            "title_kr": title_kr[:120],
            "summary": "",
            "implication": (result.get("implication") or "")[:80],
            "why_important": (result.get("why_important") or "")[:180],
            "watch_next": "",
            "related_reports": [r for r in (result.get("related_reports") or []) if isinstance(r, str)][:2],
            "tags": [t for t in result.get("tags", []) if isinstance(t, str)][:3],
        }
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            print(f"  ! curate quota exceeded — skipping rest of articles")
        else:
            print(f"  ! curate failed for '{title[:30]}': {type(e).__name__}: {msg[:200]}")
        return fallback


def fetch_rss(url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url, agent="nuclear-news-bot/1.0")
        out = []
        for entry in feed.entries:
            link = entry.get("link", "")
            title = entry.get("title", "")
            description = entry.get("summary", "") or entry.get("description", "")
            pub = None
            if entry.get("published_parsed"):
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif entry.get("updated_parsed"):
                pub = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            if not link or not title or not pub:
                continue
            out.append({
                "link": link,
                "title": strip_html(title),
                "description": strip_html(description),
                "pub": pub,
            })
        return out
    except Exception as e:
        print(f"  ! RSS fetch failed for {url}: {e}")
        return []


def assign_feed_from_title(title: str) -> str:
    t = title.lower()
    return "SMR" if any(h in t for h in SMR_HINTS) else "정책"


def collect_rss_articles(state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS * 4)
    by_title: dict[str, dict] = {}

    for src in RSS_SOURCES:
        items = fetch_rss(src["url"])
        print(f"[RSS] {src['name']}: {len(items)} entries")
        for item in items:
            if item["pub"] < cutoff:
                continue
            h = url_hash(item["link"])
            if h in state["sent"]:
                continue
            if is_promotional(item["title"], item["description"]):
                continue

            norm = normalize_title(item["title"])
            if not norm or norm in by_title:
                continue

            by_title[norm] = {
                "hash": h,
                "title": item["title"],
                "description": item["description"],
                "link": item["link"],
                "pub": item["pub"],
                "matched": src["name"],
                "score": 10,
                "domain": src.get("domain_label") or get_domain(item["link"]),
                "feed": assign_feed_from_title(item["title"]),
            }

    return list(by_title.values())


def collect_articles(feed_name: str, keywords: list[str], anchors: list[str], state: dict, negative_terms: str = "") -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    by_title: dict[str, dict] = {}

    for kw in keywords:
        try:
            items = search_naver(kw, negative_terms=negative_terms)
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
            if is_stub(desc):
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


SECTION_LABEL = {
    "khnp": "🇰🇷 한수원",
    "domestic": "🏛️ 국내",
    "international": "🌐 해외",
    "smr": "🔋 SMR",
}
CATEGORY_EMOJI = {"정책": "🏛", "기술": "⚙️", "시장": "📈", "규제": "📋"}


def format_must_read(article: dict, curation: dict) -> str:
    section = curation.get("section", "domestic")
    category = curation.get("category", "정책")
    section_lbl = SECTION_LABEL.get(section, section)
    cat_emoji = CATEGORY_EMOJI.get(category, "📌")

    original_title = article["title"]
    title_kr = curation.get("title_kr") or original_title
    show_original = title_kr.strip() != original_title.strip()

    why = html.escape(curation.get("why_important", ""))
    related = curation.get("related_reports") or []

    parts = [f"🔴 <b>[{section_lbl}] {cat_emoji} [{category}]</b> {html.escape(title_kr)}"]
    if show_original:
        parts.append(f"\n<i>{html.escape(original_title)}</i>")
    if why:
        parts.append(f"\n💡 {why}")
    if related:
        report_str = ", ".join(html.escape(r) for r in related)
        parts.append(f"\n📚 관련 사내 보고서: <i>{report_str}</i>")
    parts.append(f"\n🔗 {article['link']}")
    return "".join(parts)


def main() -> None:
    config = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    curated = load_curated()
    queue = load_queue()
    reports_kb = load_reports_kb()
    print(f"Loaded {len(reports_kb)} reports from KB")

    sent_immediate = 0
    queued = 0
    dropped = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    all_candidates: list[dict] = []

    for feed_name, feed_cfg in config.items():
        kw_list = feed_cfg["keywords"]
        anchors = feed_cfg.get("anchors", [])
        negative_terms = feed_cfg.get("negative_terms", "")
        print(f"[{feed_name}] {len(kw_list)} keywords (neg: '{negative_terms}')")
        articles = collect_articles(feed_name, kw_list, anchors, state, negative_terms=negative_terms)
        print(f"[{feed_name}] {len(articles)} candidates from Naver")
        all_candidates.extend(articles)

    rss_articles = collect_rss_articles(state)
    print(f"[RSS] {len(rss_articles)} candidates")
    all_candidates.extend(rss_articles)

    deduped: dict[str, dict] = {}
    for art in all_candidates:
        norm = normalize_title(art["title"])
        if not norm:
            continue
        existing = deduped.get(norm)
        if existing and existing["score"] >= art["score"]:
            continue
        deduped[norm] = art

    # Fuzzy dedup — 우라까이·받아쓰기 catch
    sorted_by_score = sorted(deduped.values(), key=lambda x: x["score"], reverse=True)
    fuzzy_kept: list[dict] = []
    fuzzy_norms: list[str] = []
    for art in sorted_by_score:
        norm = normalize_title(art["title"])
        is_dup = False
        for kept_norm in fuzzy_norms:
            if difflib.SequenceMatcher(None, norm, kept_norm).ratio() >= 0.82:
                is_dup = True
                break
        if not is_dup:
            fuzzy_kept.append(art)
            fuzzy_norms.append(norm)

    print(f"After dedup: {len(deduped)} unique titles → {len(fuzzy_kept)} after fuzzy dedup")

    emb_cache = load_embeddings_cache()
    semantically_unique = semantic_dedup(fuzzy_kept, emb_cache)
    save_embeddings_cache(emb_cache)
    print(f"After semantic dedup: {len(semantically_unique)} articles")

    final_articles = sorted(semantically_unique, key=lambda x: x["pub"])

    for article in final_articles:
        h = article["hash"]

        if h in curated:
            cur = curated[h]
        else:
            force_t1 = is_tier1(article["link"]) or article["score"] >= 10
            # 1차 사전 필터: LLM-as-Judge로 명백한 노이즈 차단 (Tier 1은 스킵, 무조건 통과)
            if not force_t1:
                useful, judge_reason = llm_judge(article["title"], article["description"])
                time.sleep(5)
                if not useful:
                    print(f"  ✗ judge skip: '{article['title'][:30]}' ({judge_reason})")
                    cur = {
                        "importance": "noise",
                        "section": "domestic",
                        "category": "정책",
                        "title_kr": article["title"],
                        "summary": "",
                        "implication": "",
                        "why_important": "",
                        "watch_next": "",
                        "tags": [],
                        "related_reports": [],
                        "cached_at": now_iso,
                        "title": article["title"],
                        "link": article["link"],
                        "feed": article["feed"],
                        "domain": article["domain"],
                        "matched": article["matched"],
                        "_judge_reason": judge_reason,
                    }
                    curated[h] = cur
                    state["sent"][h] = now_iso
                    dropped += 1
                    continue

            relevant = find_relevant_reports(article["title"], article["description"], reports_kb)
            cur = curate_with_llm(
                article["title"], article["description"],
                article["domain"],
                force_must_read=force_t1,
                relevant_reports=relevant,
            )
            cur["cached_at"] = now_iso
            cur["title"] = article["title"]
            cur["link"] = article["link"]
            cur["feed"] = article["feed"]
            cur["domain"] = article["domain"]
            cur["matched"] = article["matched"]
            curated[h] = cur
            time.sleep(5)

        importance = cur.get("importance", "nice_to_know")

        if importance == "noise":
            state["sent"][h] = now_iso
            dropped += 1
            continue

        if importance == "must_read":
            ok = send_telegram(format_must_read(article, cur))
            if ok:
                state["sent"][h] = now_iso
                sent_immediate += 1
        else:
            queue.append({
                "hash": h,
                "title": article["title"],
                "title_kr": cur.get("title_kr") or article["title"],
                "link": article["link"],
                "domain": article["domain"],
                "feed": article["feed"],
                "matched": article["matched"],
                "importance": importance,
                "section": cur.get("section", "domestic"),
                "category": cur.get("category", "정책"),
                "summary": cur.get("summary", ""),
                "implication": cur.get("implication", ""),
                "watch_next": cur.get("watch_next", ""),
                "tags": cur.get("tags", []),
                "related_reports": cur.get("related_reports") or [],
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
