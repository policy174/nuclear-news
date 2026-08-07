"""원문 본문을 큐레이션 직전에 가져온다. 저장하지 않고 프롬프트에만 쓴다.

배경 (2026-08-07 사용자 지적):
    "지금 ai가 대충 제목만 보고 요약하는 것 같아서 내용이 제대로 안 담겨있는 경우가
     많음. 예를들면 이런거는 가동중단이라고 적혀있으면서 밑에는 가동중단을 막았다고
     적혀있음."

    실측이 맞았다. ``curate_batch`` 가 모델에 준 것은 ``제목[:150]`` + RSS
    ``description[:200]`` + 출처 세 줄뿐이고, RSS description 은 Google News 경유
    기사(전체의 51%)에서 제목의 재탕이다. 그래서 아카이브 1,007건에서
    **요약의 57%가 제목 재진술**이고 **제목에 없는 수치를 담은 요약은 12%**뿐이었다.
    모델이 게을러서가 아니라 읽을 것을 준 적이 없다.

    같은 결핍이 모순도 만든다. 제목이 '3기 가동 중단'인데 해석이 '가동 중단을 피했다'로
    붙은 것은, 두 기사 모두 제목 한 줄씩만 있어서 어느 쪽이 최신 상태인지 판정할 근거가
    프롬프트 안에 없었기 때문이다.

설계:
    - **본문은 저장하지 않는다.** 아카이브·큐·웹 산출물 어디에도 넣지 않는다.
      저작권 판단(2026-07-31)을 뒤집지 않는다 — 남는 것은 한국어 요약뿐이다.
    - 실패는 전부 조용히 통과. 본문이 없으면 지금과 똑같이 제목·description 으로
      큐레이션한다. 수집·발송이 본문 때문에 멈추는 일은 없어야 한다.
    - Gemini 호출 수는 늘지 않는다. chunk 당 입력 토큰만 늘어난다(무료 티어 한도는
      RPM·RPD 가 먼저 걸린다 — 2026-08-06 실측).
    - stdlib + requests 만. trafilatura·readability 도입은 계속 보류.

Google News 우회:
    수집 URL 의 51%가 ``news.google.com/rss/articles/...`` 다. 이 주소는 302 가 아니라
    JS 인터스티셜을 준다 — 그냥 GET 하면 본문이 아니라 구글 페이지를 읽는다.
    기사 페이지에서 ``data-n-a-sg``(서명)·``data-n-a-ts``(시각)를 뽑아
    ``batchexecute`` 에 되물으면 실제 주소가 나온다(실측 6/6 성공).
    **이 두 속성명이 사라지면 조용히 전부 실패한다** — 그래서 통계를 찍는다.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

try:  # requests 는 news_bot 이 이미 의존하지만, 단독 import 시 죽지 않게 둔다
    import requests
except ImportError:  # pragma: no cover - 실행 환경엔 항상 있다
    requests = None  # type: ignore[assignment]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 프롬프트에 넣을 본문 길이. 15건 chunk 라 1,500자면 입력이 2만자대로 올라간다.
# 리드와 첫 본문 몇 문단에 사실이 몰려 있으므로 앞에서 자른다.
MAX_BODY_CHARS = 1500
# 이보다 짧으면 '본문 없음'으로 본다. 저작권 안내문·쿠키 배너만 긁힌 경우가 여기 걸린다.
MIN_BODY_CHARS = 220

FETCH_TIMEOUT = 12.0
WORKERS = 8
# 한 크롤에서 본문을 시도할 최대 건수. MAX_CURATION_PER_RUN(80) 과 맞춰 둔다.
MAX_FETCH_PER_RUN = int(os.environ.get("MAX_BODY_FETCH_PER_RUN", "80"))

# 본문이 유료·봇차단으로 막힌 것이 확정된 도메인. 시도해도 401·403 만 받으므로
# 시간만 태운다. **여기 넣기 전에 반드시 실측할 것** — 넣으면 영영 안 부른다.
BLOCKED_DOMAINS = {
    "reuters.com",      # 401 (실측 2026-08-07, 5/5)
    "ft.com",           # 페이월
    "markets.ft.com",
    "bloomberg.com",
    "wsj.com",
    "nikkei.com",
    "denkishimbun.com",
}

_DROP_TAGS = re.compile(
    r"(?is)<(script|style|noscript|svg|iframe|form|nav|aside|header|footer|figure|figcaption)\b.*?</\1>"
)
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_TAG = re.compile(r"(?s)<[^>]+>")
_ARTICLE = re.compile(r"(?is)<article\b[^>]*>(.*?)</article>")
_P_BLOCK = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
# <p> 를 안 쓰고 <br> 로 줄을 나누는 국내 매체가 많다(실측 thin 16건의 대부분).
_BLOCK_SPLIT = re.compile(r"(?is)</?(?:br|p|div|li|h[1-6]|tr|section)\b[^>]*>")
_META_DESC = re.compile(
    r"""(?is)<meta[^>]+(?:property|name)\s*=\s*["'](?:og:description|description)["'][^>]*>"""
)
_META_CONTENT = re.compile(r"""(?is)content\s*=\s*["'](.*?)["']""")
_WS = re.compile(r"\s+")

# 본문에 섞여 들어오는 상투구. 한 줄 통째로 버린다.
_JUNK_LINE = re.compile(
    r"(?i)(무단\s*전재|재배포\s*금지|저작권자\s*©|ⓒ\s*\w+|구독하기|구독 신청|"
    r"기사제보|보도자료|카카오톡|네이버에서|뉴스레터|앱 다운로드|"
    r"all rights reserved|sign up|subscribe|newsletter|advertisement|"
    r"cookie|privacy policy|terms of (use|service)|"
    r"please enable js|ad ?blocker|follow us on)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_SENT_END = re.compile(r"(?<=[.!?。])\s|(?<=다\.)\s|(?<=요\.)\s")


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = _domain(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


# --------------------------------------------------------------------------
# Google News 주소 복원
# --------------------------------------------------------------------------

_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def is_google_news(url: str) -> bool:
    return "news.google." in (url or "") and "/articles/" in (url or "")


def resolve_google_news(url: str, session) -> str | None:
    """Google News RSS 주소 → 실제 기사 주소. 실패하면 None."""
    try:
        article_id = url.split("/articles/")[1].split("?")[0]
    except IndexError:
        return None
    try:
        page = session.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": UA})
        signature = _SIG_RE.search(page.text)
        timestamp = _TS_RE.search(page.text)
        if not signature or not timestamp:
            # 구글이 마크업을 바꾸면 여기서 전부 죽는다. 통계로 드러나게 둔다.
            return None
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            article_id, int(timestamp.group(1)), signature.group(1),
        ])
        payload = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = session.post(
            _BATCH_URL, data={"f.req": payload}, timeout=FETCH_TIMEOUT,
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        if resp.status_code != 200:
            return None
        for line in resp.text.splitlines():
            if "garturlres" not in line:
                continue
            return json.loads(json.loads(line)[0][2])[1]
    except Exception:  # noqa: BLE001 — 본문 부재는 비치명
        return None
    return None


# --------------------------------------------------------------------------
# 본문 추출
# --------------------------------------------------------------------------

def _clean_line(raw: str) -> str:
    text = html_module.unescape(_TAG.sub(" ", raw))
    return _WS.sub(" ", text).strip()


def _usable(line: str) -> bool:
    if len(line) < 40:
        return False
    if _JUNK_LINE.search(line):
        return False
    if _EMAIL.search(line) and len(line) < 80:
        return False
    return True


def _collect(fragment: str, splitter) -> list[str]:
    lines = []
    for raw in splitter(fragment):
        line = _clean_line(raw)
        if _usable(line) and line not in lines:
            lines.append(line)
    return lines


def _meta_description(page: str) -> str:
    for tag in _META_DESC.finditer(page):
        content = _META_CONTENT.search(tag.group(0))
        if not content:
            continue
        line = _clean_line(content.group(1))
        if len(line) >= 60:
            return line
    return ""


def extract_text(page_html: str, *, limit: int = MAX_BODY_CHARS) -> str:
    """HTML → 본문 텍스트. 못 뽑으면 빈 문자열.

    ``<p>`` → 블록 태그 분해 → meta description 순으로 물러난다. 국내 매체 상당수가
    ``<p>`` 없이 ``<br>`` 로만 줄을 나눠서(실측) 두 번째 경로가 실제로 필요하다.
    """
    if not page_html:
        return ""
    cleaned = _DROP_TAGS.sub(" ", _COMMENT.sub(" ", page_html))

    # <article> 이 있으면 그 안이 본문일 확률이 높다. 없으면 문서 전체.
    scopes = [m.group(1) for m in _ARTICLE.finditer(cleaned)] or [cleaned]
    scope = max(scopes, key=len)

    lines = _collect(scope, lambda frag: _P_BLOCK.findall(frag))
    if len(" ".join(lines)) < MIN_BODY_CHARS:
        lines = _collect(scope, _BLOCK_SPLIT.split)
    body = "\n".join(lines).strip()

    if len(body) < MIN_BODY_CHARS:
        fallback = _meta_description(page_html)
        # meta description 한 줄이라도 제목 재탕보다는 낫다. 다만 본문으로
        # 착각하지 않게 짧으면 버린다.
        body = fallback if len(fallback) >= 60 else ""

    if not body:
        return ""
    if len(body) <= limit:
        return body
    # 문장 중간에서 자르면 모델이 잘린 절을 사실로 읽는다. 경계에서 끊는다.
    head = body[:limit]
    parts = _SENT_END.split(head)
    if len(parts) > 1:
        head = " ".join(parts[:-1])
    return head.strip()


# --------------------------------------------------------------------------
# 수집
# --------------------------------------------------------------------------

# 제목 토큰. 한국어는 조사가 붙어 완전 일치가 안 되므로(선행 사례: keei_match 에서
# '영덕군과' ≠ '영덕군' 때문에 진짜 매칭이 후보에서 탈락할 뻔했다) 접두로 맞춘다.
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")
# 제목 토큰의 이 비율만큼은 본문에 있어야 같은 기사로 본다. 낮게 잡는다 —
# 목적은 '다른 기사를 긁어왔다'를 걸러내는 것이지 요약 품질 판정이 아니다.
_RELEVANCE_RATIO = 0.30
_RELEVANCE_MIN_HITS = 2


def matches_title(body: str, title: str) -> bool:
    """본문이 그 제목의 기사인가.

    필요한 이유: 프롬프트가 "제목과 본문이 어긋나면 본문이 우선"이라고 지시하므로,
    엉뚱한 페이지(섹션 목록·관련기사 블록·리다이렉트된 다른 글)를 긁어오면 그
    오류가 그대로 요약이 된다. 실측에서 서로 다른 두 URL 이 같은 본문을 돌려주고
    요약까지 같아진 사례가 있었다. 판정할 수 없으면 **본문을 버린다** — 본문
    없이 돌아가는 경로는 이미 있고 그쪽이 안전하다.
    """
    if not title:
        return True
    tokens = [t for t in _TOKEN_RE.findall(title)]
    if not tokens:
        return True
    haystack = body.lower()
    hits = 0
    for token in set(tokens):
        needle = token.lower()
        # 조사 한 글자를 떼고도 본다('원전이' → '원전').
        if needle in haystack or (len(needle) > 2 and needle[:-1] in haystack):
            hits += 1
    unique = len(set(tokens))
    return hits >= max(_RELEVANCE_MIN_HITS, round(unique * _RELEVANCE_RATIO))


def fetch_one(url: str, session, title: str = "") -> tuple[str, str]:
    """(본문, 상태). 상태는 통계·진단용이며 호출자는 본문만 보면 된다."""
    if not url:
        return "", "no_url"
    if is_google_news(url):
        resolved = resolve_google_news(url, session)
        if not resolved:
            return "", "google_unresolved"
        url = resolved
    if is_blocked(url):
        return "", "blocked_domain"
    try:
        resp = session.get(
            url, timeout=FETCH_TIMEOUT,
            headers={"User-Agent": UA,
                     "Accept-Language": "ko,en-US;q=0.8,en;q=0.6"},
        )
    except Exception as exc:  # noqa: BLE001
        return "", f"error_{type(exc).__name__}"
    if resp.status_code >= 400:
        return "", f"http_{resp.status_code}"
    # 인코딩 추정 실패로 한글이 깨지면 본문이 통째로 쓰레기가 된다.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    body = extract_text(resp.text)
    if not body:
        return "", "thin"
    if not matches_title(body, title):
        return "", "title_mismatch"
    return body, "ok"


def fetch_bodies(articles: list[dict], *, max_fetch: int = MAX_FETCH_PER_RUN,
                 workers: int = WORKERS, session_factory=None) -> tuple[dict[str, str], dict]:
    """{hash: 본문} 과 통계. 실패한 기사는 키가 없다(호출자는 그냥 건너뛴다)."""
    stats = {"attempted": 0, "ok": 0, "chars": 0, "reasons": {}}
    if not articles or requests is None:
        return {}, stats

    targets = articles[:max_fetch]
    stats["attempted"] = len(targets)
    if len(articles) > len(targets):
        stats["deferred"] = len(articles) - len(targets)

    local = threading.local()
    make_session = session_factory or requests.Session

    def session_for_thread():
        got = getattr(local, "session", None)
        if got is None:
            got = make_session()
            local.session = got
        return got

    def work(article: dict) -> tuple[str, str, str]:
        body, status = fetch_one(article.get("link") or "", session_for_thread(),
                                 str(article.get("title") or ""))
        return article.get("hash", ""), body, status

    bodies: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(work, targets))
    except Exception as exc:  # noqa: BLE001 — 본문 부재는 비치명
        stats["reasons"]["pool_error"] = f"{type(exc).__name__}"
        return {}, stats

    for article_hash, body, status in results:
        stats["reasons"][status] = stats["reasons"].get(status, 0) + 1
        if body and article_hash:
            bodies[article_hash] = body
            stats["ok"] += 1
            stats["chars"] += len(body)
    if stats["ok"]:
        stats["avg_chars"] = stats["chars"] // stats["ok"]
    return bodies, stats


def format_stats(stats: dict) -> str:
    reasons = stats.get("reasons") or {}
    detail = " ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
    rate = (stats["ok"] * 100 // stats["attempted"]) if stats.get("attempted") else 0
    return (f"[body] 본문 {stats.get('ok', 0)}/{stats.get('attempted', 0)}건 ({rate}%) "
            f"평균 {stats.get('avg_chars', 0)}자 | {detail}")
