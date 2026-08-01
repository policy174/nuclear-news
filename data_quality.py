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


def curation_errors(
    payload: dict,
    *,
    require_summary: bool = True,
    summary_limit: int = 80,
) -> list[str]:
    """공개 가능한 큐레이션 결과가 아니면 필드별 오류를 반환한다."""
    errors: list[str] = []
    summary = clean_text(payload.get("summary"))
    implication = clean_text(payload.get("implication"))
    why_important = clean_text(payload.get("why_important"))

    if require_summary and not summary:
        errors.append("summary:missing")
    elif summary and (len(summary) > summary_limit or not is_complete_sentence(summary)):
        errors.append(f"summary:incomplete_or_over_{summary_limit}")
    if implication and (len(implication) > 60 or not is_complete_sentence(implication)):
        errors.append("implication:incomplete_or_over_60")
    if why_important and (len(why_important) > 150 or not is_complete_sentence(why_important)):
        errors.append("why_important:incomplete_or_over_150")

    normalized_event = normalize_event_date_fields(payload)
    if payload.get("event_date") and not normalized_event["event_date"]:
        errors.append("event_date:invalid")
    return errors
