"""story 근거 메타데이터 — V2 story_cluster.py 의 **부분 이식** (5차, 2026-09-07).

V2 원본(665줄)은 수집·랭킹·dedup 세 경로의 story 접기 전체를 담지만, V1 은
`issue_continuity` 가 쓰는 근거 교집합·story_id 부분만 들여온다. V1 파이프라인은
아직 story_members/raw_sources 를 만들지 않으므로 `evidence_overlap` 은 당분간
0 을 낸다 — issue_continuity 는 이를 '확인 안 됨'으로 보수적으로 처리한다(설계상
안전한 방향). 접기 경로까지 이식할 때 V2 원본으로 이 파일을 대체할 것.
"""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlparse

import story_identity

STORY_ID_PREFIX = "story-"


def _clean(value) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def fallback_story_id(article: dict) -> str:
    """옛 레코드·단독 story 의 안정 ID 폴백 (대표 기사 hash 기반)."""
    return story_identity.fallback_id(article)


def ensure_story_id(article: dict, *, source: str = "generated") -> str:
    """story id 가 없으면 부여한다. 다른 기사 것을 상속하지는 않는다."""
    return story_identity.ensure(article, source=source)


def raw_sources_of(article: dict) -> list[dict]:
    """수집 단계에서 이 기사에 접힌 근거들. 없으면 빈 목록."""
    vals = article.get("raw_sources")
    return [v for v in vals if isinstance(v, dict)] if isinstance(vals, list) else []


def source_identity(article: dict) -> str:
    """같은 매체의 전재가 coverage를 부풀리지 않도록 안정 식별자를 만든다."""
    publisher = _clean(article.get("publisher"))
    domain = _clean(article.get("domain"))
    if not domain:
        try:
            domain = urlparse(str(article.get("link") or article.get("url") or "")).netloc
            domain = domain.lower().removeprefix("www.")
        except (TypeError, ValueError):
            domain = ""
    return (publisher or domain or _clean(article.get("feed")) or "unknown").lower()


def source_tier(article: dict) -> int | None:
    """별도 등급표를 만들지 않고 기존 sources.py 판정을 재사용한다."""
    try:
        tier = int(article.get("source_tier"))
        if tier in (1, 2, 3):
            return tier
    except (TypeError, ValueError):
        pass
    try:
        from sources import credibility
        result = credibility({
            "title": article.get("title") or article.get("title_kr") or "",
            "url": article.get("link") or article.get("url") or "",
            "meta": article.get("publisher") or article.get("domain") or "",
        })
        tier = result.get("tier")
        return int(tier) if tier in (1, 2) else None
    except Exception:
        return None


def _source_record(article: dict) -> dict:
    return {
        "identity": source_identity(article),
        "publisher": _clean(article.get("publisher") or article.get("domain")
                            or article.get("feed"))[:100],
        "domain": _clean(article.get("domain"))[:120],
        "tier": source_tier(article),
        "evidence_role": _clean(article.get("evidence_role"))[:40],
    }


def consolidate_story_metadata(representative: dict, members: list[dict], *,
                               relation: str = "duplicate", reason: str = "",
                               stage: str = "") -> dict:
    """접힌 기사를 버리지 않고 story의 출처·제목·hash 근거로 합친다.

    이 함수는 결정적이며 LLM을 호출하지 않는다. 제목 dedup과 semantic dedup이
    같은 계약을 쓰게 해 대표 교체 뒤에도 coverage와 continuity 근거가 남는다.
    """
    all_members = [representative] + [m for m in members if m is not representative]
    for article in all_members:
        ensure_story_id(article)

    sources: dict[str, dict] = {}
    hashes: list[str] = []
    titles: list[str] = []
    member_rows: list[dict] = []
    article_count = 0
    for article in all_members:
        try:
            article_count += max(1, int(article.get("story_article_count") or 1))
        except (TypeError, ValueError):
            article_count += 1
        inherited = article.get("story_sources") or [_source_record(article)]
        for source in inherited:
            if not isinstance(source, dict):
                continue
            ident = _clean(source.get("identity")) or source_identity(article)
            prior = sources.get(ident)
            if prior is None or (source.get("tier") and
                    (not prior.get("tier") or int(source["tier"]) < int(prior["tier"]))):
                sources[ident] = dict(source, identity=ident)
        own_hash = str(article.get("hash") or "")
        inherited_hashes = article.get("story_article_hashes") or [own_hash]
        hashes.extend(str(value) for value in inherited_hashes if str(value))
        title = _clean(article.get("title_kr") or article.get("title"))[:180]
        titles.extend(article.get("story_related_titles") or ([title] if title else []))
        inherited_members = article.get("story_members") or []
        member_rows.extend(row for row in inherited_members if isinstance(row, dict))
        if own_hash:
            member_rows.append({"hash": own_hash, "title": title,
                                "publisher": _source_record(article)["publisher"]})

    def unique(values):
        seen = set()
        out = []
        for value in values:
            key = str(value)
            if key and key not in seen:
                seen.add(key)
                out.append(value)
        return out

    source_list = sorted(sources.values(), key=lambda row: row.get("identity") or "")
    member_by_hash = {}
    for row in member_rows:
        if row.get("hash") and row["hash"] not in member_by_hash:
            member_by_hash[row["hash"]] = row
    representative["story_article_count"] = max(article_count, len(set(hashes)), 1)
    representative["story_article_hashes"] = unique(hashes)
    representative["story_outlet_count"] = len(source_list)
    representative["story_tier1_count"] = sum(1 for row in source_list if row.get("tier") == 1)
    representative["story_independent_outlet_count"] = sum(
        1 for row in source_list if row.get("evidence_role") == "independent")
    representative["story_sources"] = source_list
    representative["story_related_titles"] = unique(titles)[:12]
    representative["story_members"] = list(member_by_hash.values())[:16]
    representative["story_relation"] = relation
    if reason:
        representative["story_reason"] = _clean(reason)[:300]
    if stage:
        representative["story_dedup_stage"] = stage
    return representative


class EvidenceOverlap(NamedTuple):
    """두 story 가 공유하는 근거 기사.

    shared          — 겹친 기사 수.
    candidate_total — 오늘 story 의 근거 수(비율의 분모).
    ratio           — shared / candidate_total. 오늘 근거 중 어제에도 있던 몫.
    cross_cited     — 한쪽 카드 자신이 다른 쪽의 근거 목록에 있다. **단독 근거로
                      쓰지 말 것** (V2 테라파워 실측).
    """

    shared: int
    candidate_total: int
    ratio: float
    cross_cited: bool


def member_hashes(article: dict) -> frozenset[str]:
    """이 story 가 근거로 들고 있는 기사 hash 전부 (대표 자신 포함)."""
    if not isinstance(article, dict):
        return frozenset()
    out: set[str] = set()
    own = str(article.get("hash") or "")
    if own:
        out.add(own)
    for member in article.get("story_members") or []:
        if isinstance(member, dict) and str(member.get("hash") or ""):
            out.add(str(member["hash"]))
    for value in article.get("story_article_hashes") or []:
        if str(value or ""):
            out.add(str(value))
    for raw in raw_sources_of(article):
        if str(raw.get("hash") or ""):
            out.add(str(raw["hash"]))
    return frozenset(out)


def evidence_overlap(candidate: dict, prior: dict) -> EvidenceOverlap:
    """오늘 story 와 어제 story 가 근거를 얼마나 공유하는가."""
    cand = member_hashes(candidate)
    old = member_hashes(prior)
    if not cand or not old:
        return EvidenceOverlap(0, len(cand), 0.0, False)
    shared = cand & old
    cand_hash = str(candidate.get("hash") or "")
    prior_hash = str(prior.get("hash") or "")
    cross = bool((prior_hash and prior_hash in cand) or (cand_hash and cand_hash in old))
    return EvidenceOverlap(
        shared=len(shared),
        candidate_total=len(cand),
        ratio=round(len(shared) / len(cand), 3),
        cross_cited=cross,
    )
