"""
설명 가능한 랭킹 — LLM은 feature만 추출, 최종 점수·선별은 여기(Python)서 결정.

배경:
    기존 daily_brief.rank_item 은 must_read+10 / khnp+2 / 1차출처+2 방식.
    유지보수는 쉽지만 "왜 이 기사가 위인가"를 설명 못 하고, 조정 손잡이가 없다.

설계:
    - news_bot 의 batch 큐레이션이 기사마다 features(0~3 정수)를 함께 추출.
    - 이 모듈이 ranking_config.json 의 가중치로 점수화. 내역(breakdown)을 함께 반환
      → delivery_log.jsonl 에 남아 사후 검증 가능.
    - features 없는 옛 큐 항목은 **기존 rank_item 공식 그대로** 적용 (하위 호환).
    - 중복(후속보도) 클러스터링·주제 다양성·시간 감쇠·피드백 사전확률 포함.

가드레일:
    - stdlib + sources.py 만 사용. news_bot import 금지 (env 필수라 import 시 죽음).
    - config/피드백 파일이 없거나 깨져도 죽지 않고 기본값으로 동작.
"""

from __future__ import annotations

import difflib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sources import credibility

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "ranking_config.json"
FEEDBACK_DIR = ROOT / "feedback"
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"

KST = timezone(timedelta(hours=9))

# Gemini feature 스키마 — 범위 밖/누락은 sanitize 에서 방어
EVENT_TYPES = {
    "policy_decision", "regulatory_action", "contract_award", "project_milestone",
    "incident_safety", "corporate_move", "market_signal", "research_report",
    "opinion", "other",
}
SCALE_FEATURES = ("korea_relevance", "market_materiality", "policy_materiality",
                  "novelty", "evidence_strength", "report_worthiness")

# 기존 daily_brief.rank_item 의 1차 출처 목록 (legacy 경로 하위 호환용 — 수정 금지)
_LEGACY_PRIMARY_DOMAINS = ("iaea.org", "world-nuclear-news", "khnp.co.kr",
                           "nssc.go.kr", "motie.go.kr", "nrc.gov")

_DEFAULT_CONFIG = {
    "importance_base": {"must_read": 10, "nice_to_know": 5},
    "event_weights": {"other": 1},
    "feature_weights": {"korea_relevance": 1.2, "market_materiality": 1.0,
                        "policy_materiality": 1.0, "novelty": 0.8,
                        "evidence_strength": 0.8},
    "source_bonus": {"tier1": 3.0, "tier2": 1.5},
    "related_reports_bonus": 1.0,
    "time_decay": {"per_12h": 0.5, "max": 3.0},
    "diversity": {"max_per_topic": 2, "penalty": 2.5},
    "duplicate_similarity": 0.82,
    "feedback": {"min_samples": 5, "cap": 2.0, "halflife_days": 45},
}


def load_config(path: Path = CONFIG_FILE) -> dict:
    """ranking_config.json 로딩. 없거나 깨지면 내장 기본값 (동작 보장)."""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return dict(_DEFAULT_CONFIG)
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)
    merged = dict(_DEFAULT_CONFIG)
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        merged[k] = v
    return merged


# ---- feature 방어적 파싱 ------------------------------------------------------

def sanitize_features(raw) -> dict | None:
    """Gemini 가 준 features 를 검증·클램프. dict 아니면 None (=features 없음)."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    et = raw.get("event_type")
    out["event_type"] = et if isinstance(et, str) and et in EVENT_TYPES else "other"
    for key in SCALE_FEATURES:
        v = raw.get(key)
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = 0
        out[key] = max(0, min(3, v))
    return out


# ---- 피드백 사전확률 (도메인/theme 단위) --------------------------------------
#
# feedback/YYYY-MM.jsonl 의 이벤트를 delivery_log.jsonl 로 기사 속성(domain·theme)에
# 조인. 표본이 min_samples 미만이면 0 (희소 데이터가 순위를 흔들지 않게),
# 보정폭은 ±cap, halflife_days 지수 감쇠.

_FEEDBACK_VALUE = {"important": 1.0, "noise": -1.0, "invest": 0.5, "report": 0.5}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def load_feedback_events(feedback_dir: Path | None = None) -> list[dict]:
    feedback_dir = feedback_dir or FEEDBACK_DIR
    events: list[dict] = []
    if not feedback_dir.exists():
        return events
    for p in sorted(feedback_dir.glob("*.jsonl")):
        events.extend(_load_jsonl(p))
    return events


def load_delivery_index(log_file: Path | None = None) -> dict[str, dict]:
    """hash8 → {domain, theme, section} (피드백 조인용)."""
    log_file = log_file or DELIVERY_LOG_FILE
    idx: dict[str, dict] = {}
    for rec in _load_jsonl(log_file):
        h8 = (rec.get("hash") or "")[:8]
        if h8:
            idx[h8] = {"domain": rec.get("domain") or "",
                       "theme": rec.get("theme") or "",
                       "section": rec.get("section") or ""}
    return idx


def build_feedback_priors(events: list[dict], delivery_idx: dict[str, dict],
                          cfg: dict, now: datetime | None = None) -> dict:
    """{'domain': {dom: prior}, 'theme': {theme: prior}} — 각 prior ∈ [-cap, +cap]."""
    fb_cfg = cfg.get("feedback") or {}
    min_n = int(fb_cfg.get("min_samples", 5))
    cap = float(fb_cfg.get("cap", 2.0))
    halflife = float(fb_cfg.get("halflife_days", 45))
    now = now or datetime.now(timezone.utc)

    acc: dict[str, dict[str, list[float]]] = {"domain": {}, "theme": {}}
    for ev in events:
        h8 = (ev.get("hash") or "")[:8]
        label = ev.get("label") or ""
        val = _FEEDBACK_VALUE.get(label)
        meta = delivery_idx.get(h8)
        if val is None or not meta:
            continue
        try:
            ts = datetime.fromisoformat(ev.get("ts", ""))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - ts).total_seconds() / 86400)
        except (ValueError, TypeError):
            age_days = 0.0
        decay = 0.5 ** (age_days / halflife) if halflife > 0 else 1.0
        for dim in ("domain", "theme"):
            key = meta.get(dim) or ""
            if key:
                acc[dim].setdefault(key, []).append(val * decay)

    priors: dict = {"domain": {}, "theme": {}}
    for dim, table in acc.items():
        for key, vals in table.items():
            if len(vals) < min_n:
                continue  # 표본 부족 → 미적용
            mean = sum(vals) / len(vals)  # ∈ [-1, 1]
            priors[dim][key] = max(-cap, min(cap, mean * cap))
    return priors


def feedback_prior_for(item: dict, priors: dict) -> float:
    parts = []
    dom_p = (priors.get("domain") or {}).get((item.get("domain") or "").lower())
    theme = ((item.get("investment_struct") or {}).get("theme")
             or (item.get("features") or {}).get("theme") or "")
    theme_p = (priors.get("theme") or {}).get(theme)
    if dom_p is not None:
        parts.append(dom_p)
    if theme_p is not None:
        parts.append(theme_p)
    return sum(parts) / len(parts) if parts else 0.0


# ---- 점수화 -------------------------------------------------------------------

def _get_importance(item: dict) -> str:
    if "importance" in item:
        return item["importance"]
    cat = item.get("category", "")
    return cat if cat in {"must_read", "nice_to_know", "market", "noise"} else "nice_to_know"


def _legacy_score(item: dict) -> tuple[float, dict]:
    """features 없는 옛 큐 항목 — 기존 daily_brief.rank_item 공식 그대로."""
    base = 10.0 if _get_importance(item) == "must_read" else 5.0
    breakdown = {"legacy": True, "importance": base}
    if item.get("section") == "khnp":
        base += 2.0
        breakdown["khnp_section"] = 2.0
    if any(d in (item.get("domain", "") or "") for d in _LEGACY_PRIMARY_DOMAINS):
        base += 2.0
        breakdown["primary_domain"] = 2.0
    if item.get("related_reports"):
        base += 1.0
        breakdown["related_reports"] = 1.0
    return base, breakdown


def _time_decay(item: dict, cfg: dict, now: datetime) -> float:
    td = cfg.get("time_decay") or {}
    per_12h = float(td.get("per_12h", 0.5))
    cap = float(td.get("max", 3.0))
    try:
        qt = datetime.fromisoformat(item.get("queued_at", ""))
        if qt.tzinfo is None:
            qt = qt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    age_h = max(0.0, (now - qt).total_seconds() / 3600)
    return min(cap, per_12h * (age_h / 12.0))


def score_item(item: dict, cfg: dict, priors: dict | None = None,
               now: datetime | None = None) -> tuple[float, dict]:
    """항목 1개 점수 + 설명 내역. features 없으면 legacy 공식."""
    now = now or datetime.now(timezone.utc)
    feats = sanitize_features(item.get("features"))

    if feats is None:
        score, breakdown = _legacy_score(item)
    else:
        breakdown = {}
        imp_base = cfg.get("importance_base") or {}
        score = float(imp_base.get(_get_importance(item), imp_base.get("nice_to_know", 5)))
        breakdown["importance"] = score

        ew = cfg.get("event_weights") or {}
        e = float(ew.get(feats["event_type"], ew.get("other", 1)))
        score += e
        breakdown[f"event:{feats['event_type']}"] = e

        fw = cfg.get("feature_weights") or {}
        for key in ("korea_relevance", "market_materiality", "policy_materiality",
                    "novelty", "evidence_strength"):
            contrib = feats[key] * float(fw.get(key, 0))
            if contrib:
                score += contrib
                breakdown[key] = round(contrib, 2)

        cred = credibility({"url": item.get("link", ""), "title": item.get("title", ""),
                            "meta": item.get("domain", "")})
        sb = cfg.get("source_bonus") or {}
        if cred.get("tier") == 1:
            b = float(sb.get("tier1", 3.0))
            score += b
            breakdown["source_tier1"] = b
        elif cred.get("tier") == 2:
            b = float(sb.get("tier2", 1.5))
            score += b
            breakdown["source_tier2"] = b

        if item.get("related_reports"):
            b = float(cfg.get("related_reports_bonus", 1.0))
            score += b
            breakdown["related_reports"] = b

    decay = _time_decay(item, cfg, now)
    if decay:
        score -= decay
        breakdown["time_decay"] = round(-decay, 2)

    if priors:
        p = feedback_prior_for(item, priors)
        if p:
            score += p
            breakdown["feedback_prior"] = round(p, 2)

    return round(score, 3), breakdown


# ---- 중복(후속보도) 클러스터링 --------------------------------------------------
#
# 수집층 fuzzy/semantic dedup 은 "그 시간의 crawl 안"에서만 동작. 하루치 큐에는
# 같은 사건을 다른 매체가 다시 쓴 기사(후속·우라까이)가 남는다 → 발송 직전에 잡는다.

_norm_re1 = re.compile(r"\[[^\]]+\]|\([^)]+\)")
_norm_re2 = re.compile(r"[^\w가-힣]")
_token_re = re.compile(r"[\w가-힣]+")

# 토큰 자카드 보조 판정 — 같은 사건을 다른 문장으로 쓴 패러프레이즈 대응.
# (실측: '반도체 특구 원전 18기' 동일 사건 2건이 문자열 ratio 0.52 로 0.82 미달,
#  토큰 자카드는 0.46. 짧은 제목 우연 일치 방지로 공유 토큰 4개 이상도 요구.)
_TOKEN_JACCARD_THRESHOLD = 0.45
_TOKEN_MIN_SHARED = 4


def _norm_title(item: dict) -> str:
    t = item.get("title_kr") or item.get("title") or ""
    return _norm_re2.sub("", _norm_re1.sub("", t)).lower()


def _title_tokens(item: dict) -> set[str]:
    """어절 앞 2글자 집합 (조사·어미 차이 완화)."""
    t = item.get("title_kr") or item.get("title") or ""
    return {w[:2].lower() for w in _token_re.findall(t) if len(w) >= 2}


def _same_event(norm_a: str, toks_a: set[str], norm_b: str, toks_b: set[str],
                threshold: float) -> bool:
    if norm_a and norm_b and \
            difflib.SequenceMatcher(None, norm_a, norm_b).ratio() >= threshold:
        return True
    if toks_a and toks_b:
        inter = toks_a & toks_b
        union = toks_a | toks_b
        if len(inter) >= _TOKEN_MIN_SHARED and len(inter) / len(union) >= _TOKEN_JACCARD_THRESHOLD:
            return True
    return False


def cluster_duplicates(items: list[dict], scores: dict[str, float],
                       threshold: float = 0.82) -> tuple[list[dict], list[dict]]:
    """제목 유사도(문자열 ratio + 토큰 자카드)로 같은 사건을 묶고 점수 최고 1건만 유지.

    Returns:
        (kept, dropped) — dropped 각 항목에 `dup_of`(대표 기사 hash)가 붙는다.
        대표가 발송되면 그 중복들도 함께 큐에서 정리하기 위함.
    """
    ordered = sorted(items, key=lambda a: scores.get(a.get("hash", ""), 0), reverse=True)
    kept: list[dict] = []
    kept_sig: list[tuple[str, set[str], str]] = []  # (norm, tokens, hash)
    dropped: list[dict] = []
    for art in ordered:
        norm = _norm_title(art)
        toks = _title_tokens(art)
        rep_hash = None
        for kn, kt, kh in kept_sig:
            if _same_event(norm, toks, kn, kt, threshold):
                rep_hash = kh
                break
        if rep_hash is not None:
            d = dict(art)
            d["dup_of"] = rep_hash
            dropped.append(d)
            continue
        kept.append(art)
        if norm or toks:
            kept_sig.append((norm, toks, art.get("hash", "")))
    return kept, dropped


# ---- 다양성 고려 top-k 선별 -----------------------------------------------------

def _topic_of(item: dict) -> str:
    """다양성 기준 키 — theme(투자 구조화) 있으면 theme, 없으면 section."""
    theme = ((item.get("investment_struct") or {}).get("theme") or "").strip()
    return theme if theme and theme != "none" else (item.get("section") or "etc")


def select_diverse(items: list[dict], scores: dict[str, float], k: int,
                   cfg: dict) -> list[dict]:
    """greedy 선별: 같은 topic 이 max_per_topic 개 차면 이후 후보는 penalty 감점.

    동점 규칙: 조정점수 → 원점수 → queued_at 최신 → hash (결정적).
    """
    div = cfg.get("diversity") or {}
    max_per = int(div.get("max_per_topic", 2))
    penalty = float(div.get("penalty", 2.5))

    remaining = list(items)
    selected: list[dict] = []
    topic_count: dict[str, int] = {}

    while remaining and len(selected) < k:
        def adjusted(a: dict) -> tuple:
            s = scores.get(a.get("hash", ""), 0.0)
            t = _topic_of(a)
            adj = s - (penalty if topic_count.get(t, 0) >= max_per else 0.0)
            return (adj, s, a.get("queued_at") or "", a.get("hash") or "")

        best = max(remaining, key=adjusted)
        remaining.remove(best)
        selected.append(best)
        t = _topic_of(best)
        topic_count[t] = topic_count.get(t, 0) + 1
    return selected


# ---- 종합 파이프라인 (daily_brief 에서 호출) ------------------------------------

def rank_and_select(items: list[dict], k: int, cfg: dict | None = None,
                    now: datetime | None = None,
                    use_feedback: bool = True) -> tuple[list[dict], dict]:
    """점수화 → 중복 클러스터 → 다양성 top-k.

    Returns:
        (선정 리스트, 진단 dict: scores/breakdowns/dropped_duplicates)
    """
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    priors = None
    if use_feedback:
        events = load_feedback_events()
        if events:
            priors = build_feedback_priors(events, load_delivery_index(), cfg, now)

    scores: dict[str, float] = {}
    breakdowns: dict[str, dict] = {}
    for a in items:
        h = a.get("hash", "")
        s, b = score_item(a, cfg, priors, now)
        scores[h] = s
        breakdowns[h] = b

    kept, dropped = cluster_duplicates(items, scores,
                                       float(cfg.get("duplicate_similarity", 0.82)))
    selected = select_diverse(kept, scores, k, cfg)
    diag = {
        "scores": scores,
        "breakdowns": breakdowns,
        "dropped_duplicates": [{"hash": d.get("hash", ""),
                                "dup_of": d.get("dup_of", ""),
                                "title": (d.get("title_kr") or d.get("title") or "")[:80]}
                               for d in dropped],
    }
    return selected, diag
