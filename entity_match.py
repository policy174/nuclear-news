"""엔티티 레지스트리 매칭 — 봇·웹 공용.

원래 web/build_data.py 안에 있었으나 수집 계층(discovery)도 같은 판정이 필요해
끌어냈다. **웹 산출물에 의존하지 않는다** — 봇이 웹 빌드 결과를 역참조하면
계층이 뒤집히고, 크롤 시점에는 아직 존재하지도 않는다.

판정은 결정적이다: LLM 0회, 같은 입력이면 같은 출력.
원칙은 오탐 > 누락 — 잘못 붙은 엔티티는 그 엔티티 페이지 전체의 신뢰를 깎지만,
빠진 매칭은 별칭 추가로 언제든 복구된다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY_FILE = ROOT / "entity_registry.json"

# build_data 에도 같은 정규식이 있다. 한 줄짜리라 공유 import 로 묶는 대신 각자
# 두는 편이 import 순서 얽힘이 없다.
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# ───────────────────────── 엔티티 레지스트리 ─────────────────────────
# 원전·기업·기관·프로젝트를 추적 축으로 만드는 큐레이션 사전(저장소 루트
# entity_registry.json). 빌드마다 결정적으로 매칭한다 — LLM 0회, 같은 입력이면
# 같은 출력. 원칙은 오탐 > 누락: 잘못 붙은 엔티티는 그 엔티티 페이지 전체의
# 신뢰를 깎지만, 빠진 매칭은 별칭 추가로 언제든 복구된다.
ENTITY_TYPES = {"plant", "company", "org", "project"}
ENTITY_MATCH_POLICIES = {"token", "tag_only", "tag_or_unit_adjacent", "title_only"}
ENTITY_MIN_HANGUL = 2   # 한글 별칭 최소 길이
ENTITY_MIN_LATIN = 3    # 라틴 별칭 최소 길이 — edf·nrc(3자)는 통과, ge(2자)는 거부
# 한글 접두 일치에서 허용하는 꼬리(조사) 최대 길이: '웨스팅하우스가'(+1)·
# '한수원에서는'(+3) 은 흡수하고, '한전기술'(+2)처럼 낱말이 이어지는 경우는
# 긴 별칭 우선(longest-wins)으로 해소한다 — 합성어는 별도 등재가 원칙.
ENTITY_PARTICLE_MAX = 3


def _entity_norm_latin(text: str) -> str:
    """라틴 별칭·토큰 정규화 — 소문자화 후 구분 문자 제거.

    'Rolls-Royce SMR' → 'rollsroycesmr', 'X-energy' → 'xenergy'.
    비교는 항상 정규화된 토큰의 **완전 일치**다(부분 문자열 금지) — 토큰 경계가
    이미 잘려 있으므로 `(?<![a-z0-9])` 류 경계 검사와 같은 효과를 낸다.
    """
    return re.sub(r"[^0-9a-z가-힣]", "", str(text or "").lower())


# 라틴 토큰은 _TOKEN_RE 로 뽑으면 안 된다 — 'X-energy' 가 x/energy 로 갈라져
# 정규화 완전 일치가 영영 실패한다. 하이픈·점·& 를 낱말 내부 문자로 취급하는
# 전용 런(run) 추출을 쓴다: 'X-energy' → 1 토큰, 'Rolls-Royce' → 1 토큰.
_ENTITY_LATIN_RUN_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z&.\-]*")


def load_entity_registry(path: Path = DEFAULT_REGISTRY_FILE) -> list[dict]:
    """레지스트리를 관대하게 읽는다 — 없거나 깨져도 빌드는 계속된다(발간물과
    같은 계약). 알 수 없는 type·중복 id·빈 별칭은 건너뛰고 경고만 남긴다."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[entity_match] 엔티티 레지스트리 없음/손상 — 매칭 생략 ({error})")
        return []
    seen_ids: set[str] = set()
    entities = []
    for row in raw.get("entities") or []:
        if not isinstance(row, dict):
            continue
        entity_id = str(row.get("id") or "").strip()
        name_kr = str(row.get("name_kr") or "").strip()
        entity_type = str(row.get("type") or "").strip()
        policy = str(row.get("match_policy") or "token").strip()
        aliases = [str(a).strip() for a in (row.get("aliases") or []) if str(a).strip()]
        if not entity_id or not name_kr or not aliases:
            print(f"[entity_match] 엔티티 건너뜀(필수 필드 누락): {row.get('id')!r}")
            continue
        if entity_type not in ENTITY_TYPES:
            print(f"[entity_match] 엔티티 건너뜀(알 수 없는 type {entity_type!r}): {entity_id}")
            continue
        if policy not in ENTITY_MATCH_POLICIES:
            print(f"[entity_match] 엔티티 건너뜀(알 수 없는 match_policy {policy!r}): {entity_id}")
            continue
        if entity_id in seen_ids:
            print(f"[entity_match] 엔티티 건너뜀(중복 id): {entity_id}")
            continue
        seen_ids.add(entity_id)
        entities.append({
            "id": entity_id,
            "name_kr": name_kr,
            "name_en": str(row.get("name_en") or "").strip(),
            "type": entity_type,
            "countries": [str(c).strip() for c in (row.get("countries") or []) if str(c).strip()],
            "aliases": aliases,
            "match_policy": policy,
        })
    return entities


def _entity_alias_entries(registry: list[dict]) -> list[tuple[str, bool, dict]]:
    """(정규화 별칭, 한글 여부, 엔티티) 목록 — 긴 별칭이 먼저 오도록 정렬.

    같은 토큰에 '한전기술'과 '한전'이 둘 다 걸리면 긴 쪽이 이긴다(longest-wins).
    동률은 레지스트리 순서 — 같은 입력이면 항상 같은 출력이어야 한다.
    """
    entries = []
    for order, entity in enumerate(registry):
        for alias in entity["aliases"]:
            is_hangul = bool(re.search(r"[가-힣]", alias))
            norm = alias.lower() if is_hangul else _entity_norm_latin(alias)
            minimum = ENTITY_MIN_HANGUL if is_hangul else ENTITY_MIN_LATIN
            if len(norm) < minimum:
                print(f"[entity_match] 엔티티 별칭 무시(최소 길이 미달): {entity['id']}/{alias!r}")
                continue
            entries.append((norm, is_hangul, entity, order))
    entries.sort(key=lambda item: (-len(item[0]), item[3]))
    return [(norm, is_hangul, entity, order) for norm, is_hangul, entity, order in entries]


def _entity_match_token(token: str, alias: str, is_hangul: bool) -> bool:
    if is_hangul:
        lowered = token.lower()
        return lowered == alias or (
            lowered.startswith(alias)
            and len(lowered) - len(alias) <= ENTITY_PARTICLE_MAX
        )
    return _entity_norm_latin(token) == alias


def entity_ids_for_members(members: list[dict], alias_entries) -> tuple[list[str], list[dict]]:
    """클러스터 멤버(원기사)들에서 엔티티 id 목록을 결정적으로 뽑는다.

    재료는 제목(title_kr)·요약(summary)의 토큰과 canonical_tags 다 —
    _article_view 는 canonical_tags 를 싣지 않으므로 반드시 원 멤버에서 돈다.
    반환: (엔티티 id 목록[등장 순 고정], 매칭 근거 최소 레코드 목록).
    """
    matched: dict[str, int] = {}
    evidence: list[dict] = []

    def claim(entity: dict, order: int, alias: str, field: str) -> None:
        if entity["id"] in matched:
            return
        matched[entity["id"]] = order
        evidence.append({"entity_id": entity["id"], "matched_alias": alias, "source_field": field})

    for member in members:
        title = str(member.get("title_kr") or "")
        summary = str(member.get("summary") or "")
        tags = [str(tag) for tag in (member.get("canonical_tags") or [])]
        # 한글 토큰은 _TOKEN_RE, 라틴 토큰은 하이픈 보존 런으로 각각 뽑아 합친다.
        title_tokens = _TOKEN_RE.findall(title) + _ENTITY_LATIN_RUN_RE.findall(title)
        summary_tokens = _TOKEN_RE.findall(summary) + _ENTITY_LATIN_RUN_RE.findall(summary)
        # 같은 토큰은 가장 긴 별칭 하나만 가진다(longest-wins) — '한전기술' 토큰을
        # '한전기술'(정확)이 잡았으면 '한전'(접두)은 그 토큰을 다시 못 쓴다.
        # 별칭 목록이 길이 내림차순이므로 선점 집합 하나로 충분하다.
        claimed_tokens: set[str] = set()
        for norm, is_hangul, entity, order in alias_entries:
            policy = entity["match_policy"]
            if policy in ("tag_only", "tag_or_unit_adjacent"):
                # 일반명사와 겹치는 이름(고리·월성…)은 자유문에서 찾지 않는다.
                if any(tag.lower() == norm or tag.lower() == f"{norm}원전" for tag in tags):
                    claim(entity, order, norm, "tag")
                elif policy == "tag_or_unit_adjacent" and re.search(
                    rf"{re.escape(norm)}\s*\d+\s*호기", title, re.IGNORECASE
                ):
                    claim(entity, order, norm, "title_unit")
                continue
            token_fields = (("title", title_tokens),) if policy == "title_only" \
                else (("title", title_tokens), ("summary", summary_tokens), ("tag", tags))
            for field, tokens in token_fields:
                hits = [token for token in tokens
                        if token not in claimed_tokens
                        and _entity_match_token(token, norm, is_hangul)]
                if hits:
                    claimed_tokens.update(hits)
                    claim(entity, order, norm, field)
                    break
    # 반환 순서는 레지스트리 등재 순 — 같은 입력이면 항상 같은 출력.
    ordered_ids = sorted(matched, key=lambda entity_id: matched[entity_id])
    return ordered_ids, evidence
