"""
출처 신뢰도 가중치 (source credibility weighting).

문제: last30days score 와 룰베이스 boosted_score 는 "참여도 + 신뢰 X핸들 + 정책
서브레딧" 기반이라, 정작 World Nuclear News·NucNet·IAEA 같은 공신력 전문 매체에서
온 기사인지 여부를 반영하지 못한다. 부서가 평소 신뢰하는 출처가 상위로 올라오게
하고 싶다.

해결:
  - sources.json 에 tier1(전문 권위)·tier2(신뢰 일반) 도메인 화이트리스트를 둔다.
  - cluster 의 URL 도메인을 먼저 매칭, 못 찾으면 제목·메타 텍스트에서 매체명/핸들
    (aliases)을 찾는다.
  - 매칭되면 tier 별 보너스 점수를 boosted_score 에 더한다 (quality_boost 와 합산).

가드레일:
  - stdlib only. 외부 의존성 0.
  - sources.json 없거나 깨져도 죽지 않고 보너스 0 으로 graceful degrade.
  - 코드 수정 없이 sources.json 만 편집해 출처 추가/조정 (keywords.json 과 동일 철학).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# Windows 콘솔 UTF-8 강제 (다른 모듈과 동일)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_SOURCES_PATH = Path(__file__).parent / "sources.json"


# ---- 설정 로딩 ---------------------------------------------------------------

def _load_config() -> dict:
    """sources.json 로딩. 실패 시 빈 설정(보너스 0)으로 폴백."""
    try:
        return json.loads(_SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[sources] sources.json 로딩 실패 → 출처 가중치 미적용: {e}")
        return {}


_CFG = _load_config()
TIER1_BONUS = int(_CFG.get("tier1_bonus", 40))
TIER2_BONUS = int(_CFG.get("tier2_bonus", 20))


def _build_indexes(cfg: dict) -> tuple[dict[str, tuple[int, str, str, str]], list[tuple[str, int, str, str, str]]]:
    """설정 → (도메인 인덱스, alias 인덱스).

    도메인 인덱스: {등록도메인: (tier, name)}
    alias 인덱스:  [(소문자 alias, tier, name)]  — 텍스트 부분일치용, 긴 것 우선
    """
    domain_idx: dict[str, tuple[int, str, str, str]] = {}
    alias_idx: list[tuple[str, int, str, str, str]] = []
    for legacy_tier, key in ((1, "tier1"), (2, "tier2"), (3, "tier3")):
        for entry in cfg.get(key, []):
            tier = int(entry.get("rank_tier") or legacy_tier)
            dom = (entry.get("domain") or "").lower().strip()
            name = entry.get("name") or dom
            source_type = entry.get("source_type") or "unknown"
            evidence_role = entry.get("evidence_role") or "unknown"
            if dom:
                domain_idx[dom] = (tier, name, source_type, evidence_role)
            for a in entry.get("aliases", []):
                a = (a or "").strip().lower()
                if a:
                    alias_idx.append((a, tier, name, source_type, evidence_role))
    # 긴 alias 먼저 매칭 (예: "@NEI" 보다 "Nuclear Energy Institute" 우선)
    alias_idx.sort(key=lambda x: len(x[0]), reverse=True)
    return domain_idx, alias_idx


_DOMAIN_IDX, _ALIAS_IDX = _build_indexes(_CFG)


# ---- 도메인 추출 -------------------------------------------------------------

def registered_domain(url: str | None) -> str:
    """URL → 'world-nuclear-news.org' 같은 등록 도메인(소문자). 못 구하면 빈 문자열.

    www.·m.·amp. 접두 제거. 서브도메인은 매칭 단계에서 suffix 로 처리.
    """
    if not url:
        return ""
    try:
        netloc = urlparse(url.strip()).netloc.lower()
    except Exception:
        return ""
    if not netloc:
        # 스킴 없는 'world-nuclear-news.org/x' 형태 대비
        netloc = url.strip().lower().split("/")[0]
    netloc = netloc.split("@")[-1].split(":")[0]  # user@ / :port 제거
    for prefix in ("www.", "m.", "amp."):
        if netloc.startswith(prefix):
            netloc = netloc[len(prefix):]
    return netloc


# ---- 차단 출처 ---------------------------------------------------------------
# 화이트리스트로는 못 막는 자리다. 실측 2026-08-21: mshale.com 이 한국 방송사
# 유튜브 클립 제목을 그대로 재배포했고("[🔴속보] 신규 대형원전 부지로 경북 영덕
# 선정… / 연합뉴스TV(YonhapnewsTV) Mill…"), 본문이 없어 제목만 본 큐레이션이
# must_read 를 줬다. 몇 달 지난 사건이 그대로 주간 판세의 1번 정책변화와 보고서
# 후보로 올라갔다. URL 은 매번 다르고 제목 끝에 무작위 영단어+videoId 가 붙어
# dedup 4중(URL·제목·유사도·임베딩)이 전부 비껴간다 — 같은 낡은 사건이 8/13,
# 8/14, 8/21 세 번 새 기사로 들어왔다. 사후 필터가 아니라 출처를 끊어야 한다.
_BLOCKED = frozenset(
    (d or "").lower().strip().lstrip(".")
    for d in _CFG.get("blocked", []) if (d or "").strip()
)


def is_blocked_source(url_or_domain: str | None) -> bool:
    """재배포·스팸으로 확정된 출처인가. 서브도메인 포함.

    URL 도 등록 도메인 문자열도 받는다 — Google News 경유 기사는 link 가
    news.google.com 이고 실제 출처는 수집 때 확정한 domain 필드에만 있다.
    """
    host = registered_domain(url_or_domain)
    if not host or not _BLOCKED:
        return False
    return any(host == d or host.endswith("." + d) for d in _BLOCKED)


def _match_domain(url: str | None) -> tuple[int, str, str, str] | None:
    """URL 도메인이 화이트리스트에 있으면 (tier, name). suffix 매칭으로 서브도메인 허용."""
    host = registered_domain(url)
    if not host:
        return None
    if host in _DOMAIN_IDX:
        return _DOMAIN_IDX[host]
    # 서브도메인: news.bloomberg.com → bloomberg.com
    for dom, val in _DOMAIN_IDX.items():
        if host == dom or host.endswith("." + dom):
            return val
    return None


def _match_alias(text: str) -> tuple[int, str, str, str] | None:
    """제목·메타 텍스트에서 매체명/핸들 alias 부분일치. 가장 신뢰도 높은(tier 작은) 것."""
    if not text:
        return None
    low = text.lower()
    best: tuple[int, str, str, str] | None = None
    for alias, tier, name, source_type, evidence_role in _ALIAS_IDX:
        if alias in low:
            if best is None or tier < best[0]:
                best = (tier, name, source_type, evidence_role)
    return best


# ---- 공개 API ----------------------------------------------------------------

def credibility(cluster: dict) -> dict:
    """cluster 의 출처 신뢰도 평가.

    Returns dict:
        bonus:  int   — boosted_score 에 더할 점수 (매칭 없으면 0)
        tier:   int|None  — 1 또는 2, 매칭 없으면 None
        name:   str|None  — 매칭된 매체 표시명
        via:    str|None  — 'domain' (URL 매칭) 또는 'mention' (텍스트 매칭)
    """
    # 1순위: URL 도메인 (가장 확실)
    hit = _match_domain(cluster.get("url"))
    via = "domain"
    # 2순위: 제목 + 메타 텍스트에서 매체명 언급
    if hit is None:
        text = f"{cluster.get('title', '')} {cluster.get('meta', '')}"
        hit = _match_alias(text)
        via = "mention"

    if hit is None:
        return {
            "bonus": 0, "tier": None, "name": None, "via": None,
            "source_type": "unknown", "evidence_role": "unknown",
        }

    tier, name, source_type, evidence_role = hit
    bonus = TIER1_BONUS if tier == 1 else TIER2_BONUS if tier == 2 else 0
    return {
        "bonus": bonus, "tier": tier, "name": name, "via": via,
        "source_type": source_type, "evidence_role": evidence_role,
    }


def credibility_bonus(cluster: dict) -> int:
    """boosted_score 합산용 — 보너스 점수만 반환."""
    return credibility(cluster)["bonus"]


# ---- CLI 자가진단 ------------------------------------------------------------
# 실행: python sources.py   (API 키 불필요 — 순수 도메인 로직)

if __name__ == "__main__":
    samples = [
        {"title": "Microsoft signs PPA for Three Mile Island restart",
         "url": "https://www.world-nuclear-news.org/articles/microsoft-tmi", "meta": ""},
        {"title": "NuScale VOYGR update", "url": "https://reddit.com/r/nuclear/x",
         "meta": "r/nuclear · 4.2k upvotes · via NucNet"},
        {"title": "Korea wins Dukovany contract", "url": "https://en.yna.co.kr/view/x",
         "meta": ""},
        {"title": "Reuters: EDF delays Hinkley Point C again",
         "url": "https://news.bloomberg.com/articles/edf-hinkley", "meta": "@BloombergNRG"},
        {"title": "Just built a reactor in Factorio", "url": "https://reddit.com/r/factorio/x",
         "meta": "r/factorio"},
        {"title": "IAEA reviews Zaporizhzhia safety", "url": "https://x.com/IAEAorg/status/123",
         "meta": "@IAEAorg · 850 likes"},
    ]
    print(f"=== sources.json: tier1 {len(_CFG.get('tier1', []))}개(+{TIER1_BONUS}), "
          f"tier2 {len(_CFG.get('tier2', []))}개(+{TIER2_BONUS}) ===\n")
    for c in samples:
        r = credibility(c)
        badge = f"✅ {r['name']} (tier{r['tier']}, +{r['bonus']}, {r['via']})" if r["tier"] else "— 일반 출처 (+0)"
        print(f"  {badge}")
        print(f"    {c['title'][:60]}")
