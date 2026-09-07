"""story 근거 메타데이터 — V2 story_cluster.py 의 **부분 이식** (5차, 2026-09-07).

V2 원본(665줄)은 수집·랭킹·dedup 세 경로의 story 접기 전체를 담지만, V1 은
`issue_continuity` 가 쓰는 근거 교집합·story_id 부분만 들여온다. V1 파이프라인은
아직 story_members/raw_sources 를 만들지 않으므로 `evidence_overlap` 은 당분간
0 을 낸다 — issue_continuity 는 이를 '확인 안 됨'으로 보수적으로 처리한다(설계상
안전한 방향). 접기 경로까지 이식할 때 V2 원본으로 이 파일을 대체할 것.
"""

from __future__ import annotations

from typing import NamedTuple

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
