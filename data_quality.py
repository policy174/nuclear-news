"""Nuclens 수집·아카이브·웹 빌드가 공유하는 데이터 품질 계약.

이 모듈은 외부 호출 없이 결정적으로 동작한다. 수집 단계에서 잘못된 데이터를
차단하고, 과거 레코드를 같은 규칙으로 이관할 수 있도록 순수 함수만 둔다.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_SOURCES_PATH = Path(__file__).with_name("sources.json")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "share", "shared",
    "_branch_match_id", "_ga", "igshid", "feature",
}

VALID_SOURCE_TYPES = {
    "official",
    "specialist_media",
    "general_media",
    "press_release",
    "unknown",
}
VALID_EVIDENCE_ROLES = {"primary", "independent", "distributed_claim", "unknown"}
VALID_EVENT_DATE_TYPES = {
    "announcement", "occurrence", "effective", "deadline", "scheduled", "unknown",
}
VALID_EVENT_DATE_PRECISIONS = {"day", "month", "year", "unknown"}
VALID_EVENT_DATE_SOURCES = {"title", "description", "article_text", "unknown"}

_ERROR_PATH_RE = re.compile(r"(?:^|/)error(?:/|$)", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_MULTI_SLASH_RE = re.compile(r"/{2,}")
_TITLE_SEPARATORS = (" - ", " – ", " — ")
_SENTENCE_END_RE = re.compile(r"(?:다|요|음|함|됨|임)$")
_CLOSERS = "\"'”’)]}」』"
_PUNCTUATION = ".!?…。！？"


def clean_text(value: object) -> str:
    """HTML entity와 연속 공백만 정리하고 문장을 임의로 자르지 않는다."""
    if not isinstance(value, str):
        return ""
    return _SPACE_RE.sub(" ", html.unescape(value)).strip()


def normalize_url(url: str | None) -> str:
    """추적 파라미터만 제거한 안정적인 기사 URL을 반환한다.

    기사 식별에 필요한 일반 쿼리는 유지한다. 경로의 이중 슬래시는 하나로 줄여
    ``/articles``와 ``//articles``가 다른 기사로 저장되는 문제를 막는다.
    """
    raw = clean_text(url)
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"

    path = _MULTI_SLASH_RE.sub("/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query_items.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlunparse((scheme, host, path, "", urlencode(query_items, doseq=True), ""))


def legacy_url_hash(url: str | None) -> str:
    """기존 sent.json과의 이행 호환을 위한 원문 URL 해시."""
    return hashlib.sha1(clean_text(url).encode("utf-8")).hexdigest()[:16]


def url_hash(url: str | None) -> str:
    """정규화 URL 기반의 새 기사 식별자."""
    normalized = normalize_url(url)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def invalid_url_reason(url: str | None) -> str:
    """공개 데이터에 넣을 수 없는 URL이면 이유를 반환한다."""
    normalized = normalize_url(url)
    if not normalized:
        return "invalid_url"
    if _ERROR_PATH_RE.search(urlparse(normalized).path):
        return "error_path"
    return ""


def title_key(title: object) -> str:
    """제목 완전일치 2차 중복 검사용 키. 문장 의미는 바꾸지 않는다."""
    return clean_text(title).casefold()


def split_title_publisher(title: object, publisher: object = "") -> tuple[str, str]:
    """Google News의 ``제목 - 매체명`` 표기에서 제목과 발행처를 분리한다.

    RSS ``source``가 있으면 그 값을 우선하고, 없을 때만 마지막 꼬리를 발행처로
    추정한다. 호출부는 Google News 항목에만 무출처 추정을 적용해야 한다.
    """
    cleaned_title = clean_text(title)
    cleaned_publisher = clean_text(publisher)
    if cleaned_publisher:
        for separator in _TITLE_SEPARATORS:
            suffix = separator + cleaned_publisher
            while cleaned_title.casefold().endswith(suffix.casefold()):
                cleaned_title = cleaned_title[: -len(suffix)].rstrip()
        return cleaned_title or cleaned_publisher, cleaned_publisher

    best: tuple[int, str, str] | None = None
    for separator in _TITLE_SEPARATORS:
        pos = cleaned_title.rfind(separator)
        if pos <= 0:
            continue
        candidate = cleaned_title[pos + len(separator):].strip()
        if not (2 <= len(candidate) <= 60) or any(mark in candidate for mark in "!?。！？"):
            continue
        if best is None or pos > best[0]:
            best = (pos, separator, candidate)
    if best is None:
        return cleaned_title, ""

    pos, separator, inferred = best
    headline = cleaned_title[:pos].rstrip()
    suffix = separator + inferred
    while headline.casefold().endswith(suffix.casefold()):
        headline = headline[: -len(suffix)].rstrip()
    return headline or inferred, inferred


@lru_cache(maxsize=1)
def _source_indexes() -> tuple[dict[str, dict], dict[str, dict]]:
    try:
        config = json.loads(_SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}

    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for legacy_tier, key in ((1, "tier1"), (2, "tier2"), (3, "tier3")):
        for entry in config.get(key, []):
            if not isinstance(entry, dict):
                continue
            profile = {
                "publisher": clean_text(entry.get("name")),
                "domain": clean_text(entry.get("domain")).lower(),
                "source_type": entry.get("source_type") or "unknown",
                "evidence_role": entry.get("evidence_role") or "unknown",
                "source_tier": int(entry.get("rank_tier") or legacy_tier),
            }
            domain = profile["domain"]
            if domain:
                by_domain[domain] = profile
            names = [profile["publisher"], *(entry.get("aliases") or [])]
            for name in names:
                normalized = clean_text(name).casefold()
                if normalized:
                    by_name[normalized] = profile
    return by_domain, by_name


def source_profile(domain: object = "", publisher: object = "") -> dict:
    """출처 성격과 근거 역할을 분리해 반환한다.

    숫자 ``source_tier``는 기존 랭킹 호환용이다. 화면의 '공식 출처' 표시는
    반드시 ``evidence_role == 'primary'``를 기준으로 해야 한다.
    """
    by_domain, by_name = _source_indexes()
    normalized_domain = clean_text(domain).lower()
    if normalized_domain.startswith("www."):
        normalized_domain = normalized_domain[4:]

    match = by_domain.get(normalized_domain)
    if match is None:
        for known_domain, profile in by_domain.items():
            if normalized_domain.endswith("." + known_domain):
                match = profile
                break
    if match is None:
        match = by_name.get(clean_text(publisher).casefold())
    if match is not None:
        result = dict(match)
        if publisher:
            result["publisher"] = clean_text(publisher)
        if normalized_domain:
            result["domain"] = normalized_domain
        return result

    if normalized_domain.endswith((".go.kr", ".gov", ".gov.uk")):
        source_type, role, tier = "official", "primary", 1
    elif normalized_domain in {"globenewswire.com", "prnewswire.com", "businesswire.com"}:
        source_type, role, tier = "press_release", "distributed_claim", 3
    elif normalized_domain or publisher:
        source_type, role, tier = "general_media", "independent", 3
    else:
        source_type, role, tier = "unknown", "unknown", 3

    return {
        "publisher": clean_text(publisher) or normalized_domain or "출처 미확인",
        "domain": normalized_domain,
        "source_type": source_type,
        "evidence_role": role,
        "source_tier": tier,
    }


def is_complete_sentence(value: object) -> bool:
    """한국어 완결형 서술문인지 보수적으로 검사한다."""
    text = clean_text(value).rstrip(_CLOSERS).rstrip()
    text = text.rstrip(_PUNCTUATION).rstrip(_CLOSERS).rstrip()
    return bool(text and _SENTENCE_END_RE.search(text))


def first_complete_sentence(value: object, max_length: int = 80) -> str:
    """원문 스니펫에서 제한 안의 첫 완결문을 추출한다. 새 사실은 만들지 않는다."""
    text = clean_text(value)
    if not text:
        return ""
    for match in re.finditer(r".+?(?:[.!?…。！？](?=\s|$)|$)", text):
        candidate = match.group(0).strip()
        if 0 < len(candidate) <= max_length and is_complete_sentence(candidate):
            return candidate
    return ""


def normalize_event_date_fields(payload: dict) -> dict:
    """명시적 사건일만 허용하고 날짜 의미·정밀도·근거를 함께 정규화한다."""
    raw_date = clean_text(payload.get("event_date"))
    event_type = clean_text(payload.get("event_date_type")).lower() or "unknown"
    precision = clean_text(payload.get("event_date_precision")).lower() or "unknown"
    source = clean_text(payload.get("event_date_source")).lower() or "unknown"

    if raw_date:
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            raw_date = ""
    if not raw_date:
        event_type = precision = source = "unknown"
    return {
        "event_date": raw_date or None,
        "event_date_type": event_type if event_type in VALID_EVENT_DATE_TYPES else "unknown",
        "event_date_precision": precision if precision in VALID_EVENT_DATE_PRECISIONS else "unknown",
        "event_date_source": source if source in VALID_EVENT_DATE_SOURCES else "unknown",
    }


# 원인·다음 절차·수치를 담으려면 60자로는 모자란다. 늘리되 카드 두 번째 줄이
# 감당하는 길이 안에서(실측 카드 폭 기준 90자가 두 줄).
IMPLICATION_LIMIT = 90

# 정보량 0인 해석 문장의 종결부. 실측 2026-08-05 라이브 issues.json: implication 이
# 있는 64건 중 **31건(48%)**이 이 꼴로 끝났고, 사용자가 직접 지적한 문장
# ("헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다.")도 여기 걸린다.
#
# 이 판정은 **재생성 사유가 아니다.** curation_errors 에 넣으면 재생성 1회 뒤에도
# 남을 때 기사가 통째로 격리돼(→ 영문 제목 폴백 큐레이션) 문체 문제로 기사를 잃는다.
# 대신 해석만 버린다 — 빈칸이 빈껍데기보다 낫다는 사이트 원칙("예외만 표시한다")과
# 같은 방향이고 추가 LLM 호출이 0이다.
_HOLLOW_IMPLICATION_RE = re.compile(
    r"(?:"
    r"시사(?:한다|합니다|해\s*준다|하고\s*있다)"
    r"|보여\s*(?:준다|줍니다|주고\s*있다)"
    r"|기대(?:된다|됩니다|를\s*모은다)"
    r"|전망(?:된다|됩니다|이다|입니다)"
    r"|예상(?:된다|됩니다)"
    r"|기여할\s*것(?:이다|입니다|으로\s*(?:보인다|전망된다|예상된다))"
    r"|중요(?:하다|합니다|성이\s*(?:크다|부각된다|증대된다))"
    r"|필요(?:하다|합니다|가\s*있다|가\s*있습니다|성이\s*(?:크다|제기된다))"
    # '…할 필요가 있다/있습니다' 는 주목·주시·검토 어느 동사에도 붙는다 —
    # 동사마다 어미 조합을 나열하면 하나씩 빠진다(실측: 주시할 필요가 '있습니다').
    r"|(?:주목|주시|점검|검토|모니터링)(?:이|을|를|해야|할)?\s*"
    r"(?:필요(?:가\s*)?(?:하다|합니다|있다|있습니다)|된다|됩니다|한다|합니다)"
    r"|참고(?:할\s*수\s*)?(?:있다|있습니다|해야\s*한다|해야\s*합니다)"
    r"|파악(?:할\s*수\s*)?(?:있다|있습니다)"
    r"|요구(?:된다|됩니다)"
    r")\s*[.。]?\s*$"
)

# 상투적 어미로 끝나도 **수량·시점**이 실려 있으면 살린다 — "언제·얼마"는 정보다.
# 맨 숫자를 보면 안 된다: 'AP1000', 'AP300', '고리 3·4호기' 같은 노형·호기 이름이
# 전부 통과해 버린다(실측으로 잡은 오탐). 단위·시점 표지가 붙은 숫자만 센다.
_QUANTITY_RE = re.compile(
    r"\d\s*(?:년|월|일|분기|%|퍼센트|억|조|만|천|배|MW|GW|kW|TWh|MWh"
    r"|기|개|건|차|호기|명|달러|원|유로)"
)


def implication_is_hollow(implication: object) -> bool:
    """해석 문장이 상투적 종결부로 끝나 정보를 더하지 않으면 참."""
    text = clean_text(implication)
    if not text:
        return False
    if not _HOLLOW_IMPLICATION_RE.search(text):
        return False
    return not _QUANTITY_RE.search(text)

# implication 을 요구하는 등급. market·noise 는 카드에 해석을 싣지 않으므로 제외한다
# (noise 는 애초에 발송되지 않고, market 은 시세·실적 나열이라 해석할 사건이 없다).
IMPLICATION_REQUIRED_GRADES = ("must_read", "nice_to_know")


def curation_errors(
    payload: dict,
    *,
    require_summary: bool = True,
    require_features: bool = False,
    require_implication: bool = False,
    summary_limit: int = 80,
) -> list[str]:
    """공개 가능한 큐레이션 결과가 아니면 필드별 오류를 반환한다.

    ``require_features``·``require_implication`` 은 기본값이 꺼져 있다. 이 함수는
    성격이 다른 두 곳에서 쓰이기 때문이다.

      - **게시 자격** (아카이브 적재·배포 게이트·품질 점수): features 나 implication 이
        없어도 제목·요약·링크는 멀쩡하므로 내보내도 된다. 끄고 쓴다. **여기서 켜면
        이미 쌓인 결손 레코드가 아카이브에서 통째로 탈락해 웹 데이터가 조용히
        줄어든다** — 결손을 고치려다 데이터를 지우는 셈이라 절대 켜지 않는다.
      - **큐레이션 완결성** (batch 응답 검증·재큐레이션 판정): 결손이면 다시 물어봐야
        하는 자리. 켜고 쓴다.

    features 가 없으면 ranking 이 ``_legacy_score()`` 로 빠져 event_weights 도 feature
    가중치도 반영되지 않는다. implication 이 없으면 카드 둘째 줄이 ``summary`` 로
    물러나는데, summary 는 제목을 어순만 바꾼 문장이 대부분이라(2026-08-03 브리핑
    실측 8건 중 5건) 화면에 정보가 한 줄 사라진다. 근거: web/public/app.js issueCard.

    실효 지점은 **batch 응답 검증**이다. 기사는 큐에 적재되는 순간 ``sent`` 로
    마킹돼 다시 수집되지 않으므로, 결손인 채 큐에 들어가면 그 뒤에는 고칠 기회가
    없다. 캐시·큐에 들어가기 전에 재생성시켜야 한다.
    근거: docs/score_distribution.md §4.
    """
    errors: list[str] = []
    summary = clean_text(payload.get("summary"))
    implication = clean_text(payload.get("implication"))
    why_important = clean_text(payload.get("why_important"))

    if require_summary and not summary:
        errors.append("summary:missing")
    elif summary and (len(summary) > summary_limit or not is_complete_sentence(summary)):
        errors.append(f"summary:incomplete_or_over_{summary_limit}")
    if implication and (len(implication) > IMPLICATION_LIMIT
                        or not is_complete_sentence(implication)):
        errors.append(f"implication:incomplete_or_over_{IMPLICATION_LIMIT}")
    elif (require_implication and not implication
            and payload.get("importance") in IMPLICATION_REQUIRED_GRADES):
        errors.append("implication:missing")
    if why_important and (len(why_important) > 150 or not is_complete_sentence(why_important)):
        errors.append("why_important:incomplete_or_over_150")

    if require_features and not isinstance(payload.get("features"), dict):
        errors.append("features:missing")

    normalized_event = normalize_event_date_fields(payload)
    if payload.get("event_date") and not normalized_event["event_date"]:
        errors.append("event_date:invalid")
    return errors
