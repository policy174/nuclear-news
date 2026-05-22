"""
Cross-topic 중복 제거.

문제: 같은 사건(예: "Microsoft–Constellation TMI 재가동 PPA")이 SMR · 원전일반 ·
AI거래 · 재가동 트렌드 등 여러 토픽에 동시 등장 → 텔레그램에 4번 반복 발송.

해결:
  1. URL 정규화 + 정확 일치 1차 dedup (utm·앵커·트래커 제거)
  2. Gemini가 의미 기반으로 "같은 사건" 그룹핑 (한 번의 API 호출)
  3. 각 그룹에서 boosted_score 최고치인 cluster만 "대표"로 통과,
     나머지는 발송 대상에서 제외

connect-ai의 `ceo-planner.md` 패턴 차용 — JSON-only 출력, 펜스 금지,
규칙 명시(같은 사건 정의), 환각 방지 룰.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Windows 콘솔 UTF-8 강제 (한국어 print시 cp949 에러 방지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from gemini_client import GeminiError, call_json, is_available


# ---- 프롬프트 (connect-ai 스타일 외부화 — 인라인 상수) ----------------------
#
# 향후 프롬프트가 2~3개 늘어나면 prompts/*.md 로 분리. 지금은 1개라 인라인.

DEDUP_SYSTEM_PROMPT = """당신은 원자력·에너지 뉴스 중복 제거 분류기입니다.

입력으로 헤드라인 N개를 받습니다. 같은 사건을 다루는 헤드라인끼리 그룹핑하세요.

⚠️ 출력은 정확히 아래 JSON 형식. 다른 텍스트(설명, 펜스 ```, 머리말, 꼬리말)는 단 한 글자도 금지.

{"groups": [[0, 3, 7], [1], [2, 5], [4], [6]]}

규칙:
1. 각 그룹은 같은 사건(같은 회사·시설·정책에 대한 같은 행동·발표·결정)을 다루는 헤드라인 인덱스의 배열.
2. 혼자인 사건은 단일 원소 그룹 [idx] 으로 표현.
3. 모든 인덱스가 정확히 한 그룹에만 등장해야 함. 빠지거나 중복 금지.
4. "같은 사건" 판정 기준 (엄격):
   - 같은 주체(회사·국가·기관) + 같은 객체(시설·정책·계약) + 같은 행동(발표·승인·취소·재가동)
   - 비슷한 토픽(예: 둘 다 SMR) 이지만 다른 회사·다른 프로젝트면 → 다른 그룹
   - 같은 사건의 후속 업데이트(예: "발표" → "공식 확정")는 → 같은 그룹
5. 확신이 없으면 같은 그룹으로 묶지 말고 분리. (오버그루핑이 더 나쁨)

입력 형식: 각 줄이 `[idx] 제목 | 메타`."""


# ---- URL 정규화 -------------------------------------------------------------

# 트래킹 파라미터 (제거 대상)
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
    "ref", "ref_src", "ref_url", "source", "share", "shared",
    "_branch_match_id", "_ga", "igshid", "feature",
}


def normalize_url(url: str | None) -> str:
    """URL을 캐노니컬 형태로 변환. None이면 빈 문자열."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip().lower()

    scheme = "https" if p.scheme in ("http", "https", "") else p.scheme
    netloc = p.netloc.lower()
    # m.example.com, www.example.com → example.com
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.startswith("m."):
        netloc = netloc[2:]
    # 모바일 트위터 → x.com
    if netloc in ("twitter.com", "mobile.twitter.com", "nitter.net"):
        netloc = "x.com"
    if netloc in ("youtu.be",):
        netloc = "youtube.com"

    path = p.path.rstrip("/")

    # 트래킹 쿼리 제거 + 알파벳 순 정렬 (안정적 비교)
    qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
          if k.lower() not in _TRACKING_PARAMS]
    qs.sort()
    query = urlencode(qs)

    # fragment 제거
    return urlunparse((scheme, netloc, path, "", query, ""))


# ---- 단계 ①: URL 일치 dedup ------------------------------------------------

def _url_groups(clusters: list[dict]) -> dict[str, list[int]]:
    """정규화된 URL이 같은 cluster들을 묶음. URL 없는 건 각자 단독."""
    out: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        norm = normalize_url(c.get("url"))
        key = norm if norm else f"__no_url_{i}"
        out.setdefault(key, []).append(i)
    return out


# ---- 단계 ②: LLM 의미 dedup ------------------------------------------------

def _format_cluster_line(idx: int, cluster: dict) -> str:
    title = (cluster.get("title") or "").replace("\n", " ").strip()[:180]
    meta = (cluster.get("meta") or "").replace("\n", " ").strip()[:80]
    return f"[{idx}] {title} | {meta}"


def _llm_semantic_groups(clusters: list[dict]) -> list[list[int]]:
    """Gemini에게 그룹핑 요청. 실패 시 모든 cluster를 단독 그룹으로 fallback."""
    if not is_available():
        # API 키 없으면 LLM 단계 skip — 각자 단독 그룹
        print("[dedup] GEMINI_API_KEY 없음 → 의미 dedup 건너뜀 (URL 단계만 적용)")
        return [[i] for i in range(len(clusters))]

    if len(clusters) <= 1:
        return [[i] for i in range(len(clusters))]

    lines = [_format_cluster_line(i, c) for i, c in enumerate(clusters)]
    payload = "\n".join(lines)

    try:
        result = call_json(
            DEDUP_SYSTEM_PROMPT,
            payload,
            temperature=0.05,
            max_output_tokens=4096,
            timeout=90.0,
        )
    except GeminiError as e:
        print(f"[dedup] Gemini 실패 → 단독 그룹 fallback: {e}")
        return [[i] for i in range(len(clusters))]

    groups = result.get("groups")
    if not isinstance(groups, list):
        print(f"[dedup] 응답에 groups 없음 → fallback. payload={result}")
        return [[i] for i in range(len(clusters))]

    # 검증: 모든 인덱스가 정확히 한 번 등장하는지
    seen: set[int] = set()
    cleaned: list[list[int]] = []
    for g in groups:
        if not isinstance(g, list):
            continue
        valid = [i for i in g if isinstance(i, int) and 0 <= i < len(clusters) and i not in seen]
        if valid:
            cleaned.append(valid)
            seen.update(valid)
    # 빠진 인덱스는 단독 그룹으로 보충
    for i in range(len(clusters)):
        if i not in seen:
            cleaned.append([i])

    return cleaned


# ---- 단계 ③: 두 단계 합성 + 대표 선정 -------------------------------------

def dedup_clusters(
    topic_clusters: list[tuple[str, dict]]
) -> tuple[list[tuple[str, dict]], list[tuple[str, dict, str, str]]]:
    """모든 토픽의 (topic_label, cluster) 페어를 받아 dedup.

    Returns:
        kept_pairs: 그룹별 대표 (topic_label, cluster) 리스트
        dropped:    제거된 항목들 (topic_label, cluster, kept_topic, reason)
                    — 로깅·디버깅용
    """
    if not topic_clusters:
        return [], []

    n = len(topic_clusters)
    clusters_only = [c for _, c in topic_clusters]

    # ① URL 정확 일치 dedup으로 1차 그룹 형성
    url_buckets = _url_groups(clusters_only)
    # bucket이 1개짜리뿐이면 LLM 단계로 바로 가도 됨.
    # 다수의 URL 중복이 있어도 LLM 단계에서 일관되게 처리 가능하지만,
    # 비용 절감 위해 이미 같은 URL인 건 LLM에 보낼 필요 없음 → 그룹 대표만 보냄.

    representative_indices: list[int] = []
    url_group_map: dict[int, list[int]] = {}  # rep_idx → all member indices
    for key, members in url_buckets.items():
        # 대표 = 그룹 내 boosted_score 최고
        rep = max(members, key=lambda i: clusters_only[i].get("boosted_score",
                                                              clusters_only[i].get("score", 0)))
        representative_indices.append(rep)
        url_group_map[rep] = members

    # ② 대표들끼리 LLM 의미 dedup
    rep_clusters = [clusters_only[i] for i in representative_indices]
    sem_groups = _llm_semantic_groups(rep_clusters)

    # 의미 그룹 → 원본 인덱스로 펼치기
    final_groups: list[list[int]] = []
    for sem_g in sem_groups:
        merged: list[int] = []
        for local_idx in sem_g:
            global_rep = representative_indices[local_idx]
            merged.extend(url_group_map[global_rep])
        final_groups.append(merged)

    # ③ 각 그룹에서 boosted_score 최고치를 대표로
    kept_pairs: list[tuple[str, dict]] = []
    dropped: list[tuple[str, dict, str, str]] = []
    for g in final_groups:
        winner_idx = max(g, key=lambda i: clusters_only[i].get("boosted_score",
                                                               clusters_only[i].get("score", 0)))
        kept_topic, kept_cluster = topic_clusters[winner_idx]
        kept_pairs.append((kept_topic, kept_cluster))

        # 같은 그룹에 다른 토픽 cluster가 있었으면 dropped 기록
        for j in g:
            if j == winner_idx:
                continue
            t_lbl, c = topic_clusters[j]
            url_same = normalize_url(c.get("url")) == normalize_url(kept_cluster.get("url"))
            reason = "url" if url_same and c.get("url") else "semantic"
            dropped.append((t_lbl, c, kept_topic, reason))

    return kept_pairs, dropped


# ---- CLI 자가진단 ----------------------------------------------------------

if __name__ == "__main__":
    # 샘플 데이터로 dedup 동작 확인
    samples: list[tuple[str, dict]] = [
        ("SMR 동향", {"title": "Microsoft signs PPA with Constellation for Three Mile Island restart",
                      "url": "https://example.com/tmi-microsoft", "score": 50, "boosted_score": 75, "meta": "r/nuclear"}),
        ("재가동 트렌드", {"title": "TMI restart deal: Microsoft × Constellation 20-year PPA",
                          "url": "https://other.com/tmi-deal?utm_source=x", "score": 40, "boosted_score": 55, "meta": "@MarkNelson"}),
        ("SMR 동향", {"title": "NuScale Romania VOYGR project advances to FEED",
                      "url": "https://example.com/nuscale-romania", "score": 30, "boosted_score": 45, "meta": ""}),
        ("AI-원전 빅테크 거래", {"title": "Hyperscalers race for nuclear: Amazon, Microsoft, Google compared",
                                  "url": "https://news.com/hyperscaler-nuclear", "score": 25, "boosted_score": 40, "meta": ""}),
    ]
    kept, dropped = dedup_clusters(samples)
    print(f"\n=== KEPT ({len(kept)}) ===")
    for t, c in kept:
        print(f"  [{t}] {c['title'][:80]}")
    print(f"\n=== DROPPED ({len(dropped)}) ===")
    for t, c, kept_t, why in dropped:
        print(f"  [{t}] {c['title'][:60]} → merged into [{kept_t}] ({why})")
