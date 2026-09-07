"""Canonical story identity authority.

Every production mutation of ``story_id`` lives here.  Deduplication may create a
display group, but it cannot transfer an identity between its members.  Persistent
records created before identity contract v2 are treated as legacy evidence: they may
be read and matched, but their ID is inherited only after independent identity
evidence succeeds and the recent registry contains no conflicting owner.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import story_fingerprint

STORY_ID_PREFIX = "story-"
IDENTITY_VERSION = 2
CANONICAL_TRUST = "canonical"
LEGACY_TRUST = "legacy"
_GENERIC_ASSET_WORDS = {
    "npp", "plant", "plants", "power", "nuclear", "reactor", "reactors",
    "project", "projects", "facility", "facilities", "station", "unit", "units",
    "data", "center", "centre", "grid", "smr", "hpp", "hydropower", "investment",
}


def _clean(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def fallback_id(article: dict) -> str:
    current = _clean(article.get("story_id"))
    if current:
        return current
    seed = _clean(article.get("hash"))
    return f"{STORY_ID_PREFIX}{seed}" if seed else ""


def _write(article: dict, story_id: str, *, source: str, trust: str,
           decision: str) -> str:
    """The only primitive allowed to mutate canonical identity fields."""
    story_id = _clean(story_id)
    if not story_id:
        return ""
    article["story_id"] = story_id
    article["story_id_source"] = source
    article["story_id_trust"] = trust
    article["story_identity_version"] = IDENTITY_VERSION
    article["story_identity_decision"] = decision
    return story_id


def ensure(article: dict, *, source: str = "generated") -> str:
    """Give an article its own hash-derived identity without inheriting another one."""
    current = _clean(article.get("story_id"))
    if current:
        # Never silently bless a pre-v2 persistent ID.  In-memory IDs written by this
        # module already carry the version/trust fields and pass through unchanged.
        if int(article.get("story_identity_version") or 0) >= IDENTITY_VERSION:
            return current
        article.setdefault("story_id_trust", LEGACY_TRUST)
        return current
    return _write(
        article,
        fallback_id(article),
        source=source,
        trust=CANONICAL_TRUST,
        decision="fresh_article_hash",
    )


def prepare_candidate(article: dict) -> str:
    """Remove an unverified carried ID from an active candidate before matching."""
    current = _clean(article.get("story_id"))
    if current and not is_canonical(article):
        seed = _clean(article.get("hash"))
        if seed:
            return _write(
                article,
                f"{STORY_ID_PREFIX}{seed}",
                source="generated",
                trust=CANONICAL_TRUST,
                decision="legacy_rejected_fresh_hash",
            )
    return ensure(article)


def mark_persistent(article: dict) -> str:
    """Load an old delivery/archive record without upgrading its provenance."""
    current = _clean(article.get("story_id"))
    if not current:
        # An old record with no ID gets a deterministic compatibility identity, but it
        # remains legacy until a new match supplies independent evidence.
        seed = _clean(article.get("hash"))
        if seed:
            article["story_id"] = f"{STORY_ID_PREFIX}{seed}"
            article.setdefault("story_id_source", "legacy_delivery")
            current = article["story_id"]
    if current and int(article.get("story_identity_version") or 0) < IDENTITY_VERSION:
        article["story_id_trust"] = LEGACY_TRUST
    return current


def is_canonical(article: dict) -> bool:
    return bool(
        _clean(article.get("story_id"))
        and int(article.get("story_identity_version") or 0) >= IDENTITY_VERSION
        and article.get("story_id_trust") == CANONICAL_TRUST
    )


def trusted_same_id(left: dict, right: dict) -> bool:
    left_id = _clean(left.get("story_id"))
    return bool(left_id and left_id == _clean(right.get("story_id"))
                and is_canonical(left) and is_canonical(right))


def _named_facilities(row: dict) -> set[str]:
    fingerprint = row.get("story_fingerprint")
    if not isinstance(fingerprint, dict):
        return set()
    out: set[str] = set()
    for value in story_fingerprint.axis_values(fingerprint, "assets"):
        words = set(re.findall(r"[a-z0-9]+", value.lower()))
        if words & {"npp", "plant", "station", "unit"}:
            specific = words - _GENERIC_ASSET_WORDS
            if specific:
                out.add(" ".join(sorted(specific)))
    return out


def _fingerprint_conflict(left: dict, right: dict) -> tuple[bool, list[str]]:
    comparison = story_fingerprint.compare(
        left.get("story_fingerprint"), right.get("story_fingerprint"))
    contested = set(comparison.contested)
    # Scope country and concrete asset/project are global identity vetoes.  Actors by
    # themselves can legitimately change between follow-up reports, so actor conflict
    # becomes blocking only together with another concrete contested identity axis.
    reasons: list[str] = []
    if "countries" in contested:
        reasons.append("country_conflict")
    left_assets = _named_facilities(left)
    right_assets = _named_facilities(right)
    facility_conflict = bool(
        "assets" in contested and left_assets and right_assets
        and left_assets.isdisjoint(right_assets)
    )
    if facility_conflict:
        reasons.append("facility_project_conflict")
    if "actors" in contested and facility_conflict:
        reasons.append("project_actor_conflict")
    return bool(reasons), reasons


def registry_conflicts(story_id: str, owner: dict,
                       records: Iterable[dict]) -> list[dict]:
    out: list[dict] = []
    for other in records:
        if other is owner or _clean(other.get("story_id")) != story_id:
            continue
        conflict, reasons = _fingerprint_conflict(owner, other)
        if conflict:
            out.append({
                "story_id": story_id,
                "left_hash": _clean(owner.get("hash")),
                "right_hash": _clean(other.get("hash")),
                "reasons": reasons,
            })
    return out


@dataclass(frozen=True)
class Resolution:
    inherited: bool
    story_id: str
    status: str
    reasons: tuple[str, ...] = ()


def inherit(candidate: dict, prior: dict, *, records: Iterable[dict],
            fingerprint_confirmed: bool, evidence_confirmed: bool,
            contested_axes: Iterable[str] = ()) -> Resolution:
    """Resolve a proposed history inheritance conservatively.

    A legacy ID is never evidence.  Fingerprint/evidence confirmation is evaluated by
    the caller, and this authority additionally rejects contaminated registries.
    """
    prepare_candidate(candidate)
    prior_id = _clean(prior.get("story_id")) or mark_persistent(prior)
    contested = tuple(sorted(set(contested_axes)))
    if not prior_id or contested:
        return Resolution(False, _clean(candidate.get("story_id")),
                          "identity_conflict", contested)
    if prior.get("story_registry_conflicted"):
        return Resolution(False, _clean(candidate.get("story_id")),
                          "legacy_rejected", ("registry_conflict",))
    if not (fingerprint_confirmed or evidence_confirmed):
        return Resolution(False, _clean(candidate.get("story_id")), "uncertain")
    conflicts = registry_conflicts(prior_id, prior, records)
    if conflicts:
        reasons = tuple(sorted({reason for row in conflicts for reason in row["reasons"]}))
        return Resolution(False, _clean(candidate.get("story_id")),
                          "legacy_rejected", reasons)
    inherited = _write(
        candidate,
        prior_id,
        source="history",
        trust=CANONICAL_TRUST,
        decision="confirmed_continuity",
    )
    return Resolution(True, inherited, "confirmed")


def display_group_id(members: Iterable[dict]) -> str:
    identities = sorted(filter(None, (ensure(row) for row in members)))
    seed = "\0".join(identities).encode("utf-8")
    return "display-" + hashlib.sha256(seed).hexdigest()[:16]


def audit_registry(records: Iterable[dict]) -> dict:
    rows = [row for row in records if isinstance(row, dict)]
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        story_id = _clean(row.get("story_id")) or mark_persistent(row)
        if story_id:
            groups[story_id].append(row)

    conflicts: list[dict] = []
    uncertain: list[dict] = []
    for story_id, owners in groups.items():
        pair_conflicts = []
        for index, left in enumerate(owners):
            for right in owners[index + 1:]:
                conflict, reasons = _fingerprint_conflict(left, right)
                if conflict:
                    pair_conflicts.append({
                        "story_id": story_id,
                        "left_hash": _clean(left.get("hash")),
                        "right_hash": _clean(right.get("hash")),
                        "reasons": reasons,
                    })
        conflicts.extend(pair_conflicts)
        if len(owners) > 1 and not pair_conflicts:
            uncertain.append({"story_id": story_id, "owners": len(owners)})

    reason_counts: dict[str, int] = defaultdict(int)
    for row in conflicts:
        for reason in row["reasons"]:
            reason_counts[reason] += 1
    conflicted_ids = {row["story_id"] for row in conflicts}
    return {
        "legacy_story_count": len(groups),
        "preservable_story_count": len(groups) - len(conflicted_ids),
        "identity_conflict_story_count": len(conflicted_ids),
        "country_conflict_count": reason_counts["country_conflict"],
        "facility_conflict_count": reason_counts["facility_project_conflict"],
        "project_actor_conflict_count": reason_counts["project_actor_conflict"],
        "heterogeneous_story_count": sum(
            1 for owners in groups.values()
            if max((int(row.get("story_article_count") or 1) for row in owners), default=1) >= 8
        ),
        "automatic_split_count": len(conflicted_ids),
        "uncertain_story_count": len(uncertain),
        "conflicts": conflicts,
        "uncertain": uncertain,
    }
