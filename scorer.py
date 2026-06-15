"""
AI 점수 매기기 (Phase 1 — Horizon `src/ai/analyzer.py` 패턴 이식).

문제: last30days 엔진이 매긴 `score` 와 룰베이스 `boosted_score` 는
"참여도 + 신뢰 핸들 보너스" 기반. "원자력 정책 관점에서 오늘 알아야 할 정도"
를 반영 못 함. 일반 PR, 잡담, 인기 자작 콘텐츠가 상위에 노출됨.

해결:
  1. 헤드라인 N개를 1회 Gemini 호출에 배치로 보냄 (Horizon analyze_batch 패턴)
  2. 각 헤드라인에 0-10 `ai_score`, `ai_reason`, `ai_tags` 부여
  3. threshold 미달은 의미 dedup 단계 전에 제외 → 노이즈 컷 + Gemini 호출 토큰 절감

Horizon `src/ai/prompts.py` 일반 루브릭(9-10 groundbreaking ...)을
원자력·에너지 도메인 루브릭으로 리라이트. 프롬프트는 인라인 — dedup.py와 통일.

가드레일:
  - stdlib only. gemini_client 외 의존성 추가 금지.
  - GEMINI_API_KEY 없거나 호출 실패 시: 모든 cluster 통과 (필터 미적용).
    dedup.py의 fallback 정책과 동일.
"""

from __future__ import annotations

import sys

# Windows 콘솔 UTF-8 강제 (dedup.py와 동일)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from gemini_client import GeminiError, call_json, is_available


# ---- 프롬프트 (인라인 상수 — dedup.py와 통일된 스타일) -----------------------

SCORE_SYSTEM_PROMPT = """당신은 원자력 정책·산업 동향 분석가입니다.

입력으로 헤드라인 N개를 받습니다. 각 헤드라인에 대해 "한국 원자력정책실 실무자가
오늘 알아야 할 정도"를 0-10 점수로 평가하세요.

⚠️ 출력은 정확히 아래 JSON 형식. 다른 텍스트(설명, 펜스 ```, 머리말, 꼬리말)는 단 한 글자도 금지.

{"scores": [{"idx": 0, "score": 8.5, "reason": "한 문장", "tags": ["SMR", "정책"]}, ...]}

점수 루브릭 (원자력·에너지 도메인):
  9-10 (필독): 글로벌급 사건. 대형 사고, 신규국 첫 원전 가동·계약,
              빅테크-원전 메가딜(MS-TMI, Amazon-X-energy 수준), 주요국 정책 대전환,
              핵무기·확산 관련 안보 이슈
  7-8  (중요): SMR 라이선스·FOAK 진척, 주요 PPA·M&A, 폐쇄·재가동 결정,
              정부 신규 정책·예산, 핵연료 공급망 이슈, IAEA·NRC 주요 결정,
              한국 원전·SMR 관련 동향
  5-6  (참고): 기존 프로젝트 진전, 산업 데이터·전망 보고서, 주요 인사,
              학계·연구 발표, 지역·주(state) 정책 동향
  3-4  (낮음): 루틴 PR, 일반 산업 보도, 개인 의견 기사
  0-2  (노이즈): 광고, 잡담, 무관 토픽, 음모론, 게임·취미·자작 콘텐츠

규칙:
1. score 는 0-10 사이 정수 또는 소수점 1자리 (예: 7.5).
2. reason 은 한국어 한 문장(50자 이내). 왜 그 점수인지 핵심만.
3. tags 는 1-3개. 한국어. 예: ["SMR", "PPA", "미국"], ["사고", "유럽"], ["규제"].
4. 모든 idx 가 정확히 한 번씩 등장. 빠지거나 중복 금지.
5. 확신 없으면 5-6점(참고). 극단값(9-10, 0-2)은 진짜 그 정도일 때만.

입력 형식: 각 줄이 `[idx] 제목 | sources | meta`."""


# ---- 배치 호출 ---------------------------------------------------------------

def _format_line(idx: int, cluster: dict) -> str:
    title = (cluster.get("title") or "").replace("\n", " ").strip()[:180]
    sources = ",".join(cluster.get("sources") or [])
    meta = (cluster.get("meta") or "").replace("\n", " ").strip()[:80]
    return f"[{idx}] {title} | {sources} | {meta}"


def _call_gemini(clusters: list[dict]) -> dict[int, dict]:
    """Gemini 1회 호출로 모든 cluster에 점수 부여. 실패 시 빈 dict 반환."""
    if not is_available():
        print("[scorer] GEMINI_API_KEY 없음 → 점수 매기기 건너뜀 (필터 미적용)")
        return {}
    if not clusters:
        return {}

    lines = [_format_line(i, c) for i, c in enumerate(clusters)]
    payload = "\n".join(lines)

    try:
        result = call_json(
            SCORE_SYSTEM_PROMPT,
            payload,
            temperature=0.1,
            max_output_tokens=4096,
            timeout=90.0,
        )
    except GeminiError as e:
        print(f"[scorer] Gemini 실패 → 점수 단계 스킵 (필터 미적용): {e}")
        return {}

    scores = result.get("scores")
    if not isinstance(scores, list):
        print(f"[scorer] 응답에 scores 없음 → 스킵. payload={result}")
        return {}

    out: dict[int, dict] = {}
    for item in scores:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        score = item.get("score")
        if not isinstance(idx, int) or not isinstance(score, (int, float)):
            continue
        if not (0 <= idx < len(clusters)):
            continue
        # 점수 범위 클램프 — LLM이 12점, -1점 같은 거 뱉어도 안전
        score_f = max(0.0, min(10.0, float(score)))
        out[idx] = {
            "ai_score": score_f,
            "ai_reason": str(item.get("reason") or "")[:200],
            "ai_tags": [str(t)[:30] for t in (item.get("tags") or [])][:3],
        }
    return out


# ---- 공개 API ----------------------------------------------------------------

def score_clusters(
    topic_clusters: list[tuple[str, dict]],
    *,
    threshold: float = 6.0,
) -> tuple[list[tuple[str, dict]], list[tuple[str, dict, float, str]]]:
    """모든 토픽의 (label, cluster) 페어를 받아 점수 매기고 threshold 필터.

    `dedup_clusters` 와 동일 시그니처로 받아 chain 가능하게 설계.
    score → dedup 순서로 호출해야 의미 dedup 비용 절감 효과 발생.

    Args:
        topic_clusters: (label, cluster_dict) 리스트
        threshold: 이 값 미만은 dropped 로 분리 (기본 6.0)

    Returns:
        kept:    (label, cluster) — cluster 에 ai_score/ai_reason/ai_tags 추가됨
        dropped: (label, cluster, ai_score, ai_reason) — 로깅·디버깅용

    Fallback (dedup.py 와 동일 정책):
        - GEMINI_API_KEY 미설정 또는 호출 실패: 모든 cluster 를 kept 로 통과.
          이때 cluster 에 ai_score 필드는 추가되지 않음. 후속 코드는
          `cluster.get("ai_score")` 로 안전하게 접근할 것.
    """
    if not topic_clusters:
        return [], []

    clusters_only = [c for _, c in topic_clusters]
    scores_by_idx = _call_gemini(clusters_only)

    # Gemini 호출 자체가 실패 → 모두 통과 (필터 미적용)
    if not scores_by_idx:
        return list(topic_clusters), []

    kept: list[tuple[str, dict]] = []
    dropped: list[tuple[str, dict, float, str]] = []
    for i, (label, cluster) in enumerate(topic_clusters):
        s = scores_by_idx.get(i)
        if s is None:
            # 부분 누락: LLM 응답에 idx 가 빠진 경우 → 안전하게 통과
            kept.append((label, cluster))
            continue
        # cluster 에 점수 부착 (in-place mutation)
        cluster["ai_score"] = s["ai_score"]
        cluster["ai_reason"] = s["ai_reason"]
        cluster["ai_tags"] = s["ai_tags"]

        if s["ai_score"] >= threshold:
            kept.append((label, cluster))
        else:
            dropped.append((label, cluster, s["ai_score"], s["ai_reason"]))

    return kept, dropped


# ---- CLI 자가진단 ----------------------------------------------------------
#
# 실행: python scorer.py
# GEMINI_API_KEY 설정되어 있으면 실제 호출, 없으면 통과 동작 확인.

if __name__ == "__main__":
    samples: list[tuple[str, dict]] = [
        ("SMR 동향", {
            "title": "Microsoft signs 20-year PPA with Constellation for Three Mile Island restart",
            "url": "https://example.com/tmi",
            "sources": ["Reddit", "X"],
            "meta": "r/nuclear · 4.2k upvotes",
            "score": 80,
        }),
        ("SMR 동향", {
            "title": "Just built a nuclear reactor in Factorio, AMA",
            "url": "https://reddit.com/r/factorio/x",
            "sources": ["Reddit"],
            "meta": "r/factorio · 320 upvotes",
            "score": 15,
        }),
        ("규제", {
            "title": "NRC approves NuScale VOYGR design certification update",
            "url": "https://nrc.gov/x",
            "sources": ["X"],
            "meta": "@NRCgov · 850 likes",
            "score": 40,
        }),
        ("정책", {
            "title": "Op-ed: why we should consider nuclear (general blog)",
            "url": "https://blog.example.com/op-ed",
            "sources": ["Reddit"],
            "meta": "r/energy · 12 upvotes",
            "score": 8,
        }),
    ]
    kept, dropped = score_clusters(samples, threshold=6.0)
    print(f"\n=== KEPT ({len(kept)}) ===")
    for t, c in kept:
        s = c.get("ai_score", "N/A")
        r = c.get("ai_reason", "(점수 없음 — fallback 통과)")
        tags = c.get("ai_tags", [])
        print(f"  [{t}] {c['title'][:70]}")
        print(f"        → {s}점 · {r} · {tags}")
    print(f"\n=== DROPPED ({len(dropped)}) ===")
    for t, c, s, r in dropped:
        print(f"  [{t}] {c['title'][:60]}  → {s}점: {r}")
