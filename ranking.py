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

# novelty·evidence_strength 는 LLM 에서 회수해 여기서 계산한다 (2026-08-01).
#
# 근거: delivery_log 157건 실측에서 novelty 는 151건 중 122건(81%)이 정확히 2점,
# evidence_strength 는 85%가 2~3점이었다. 비교 대상 없이 0~3 절대평가를 시키면
# 중앙값으로 수렴해 변별력이 사라진다(기여 표준편차 0.46 / 0.62 로 최하위).
# 둘 다 코드가 이미 아는 정보로 판정할 수 있다 —
#   novelty          : 같은 사건을 최근에 이미 다뤘는가 (prior_coverage)
#   evidence_strength: 확정 표현인가 전망인가 + 수치가 붙어 있는가
CODE_DERIVED_FEATURES = ("novelty", "evidence_strength")

_CONFIRMED_RE = re.compile(
    r"(했다|됐다|되었다|한다|밝혔다|발표|체결|의결|승인|인가|착공|준공|완료|"
    r"서명|확정|선정|가동|중단|취소|합의|출범|제출|통과)"
)
_SPECULATION_RE = re.compile(
    r"(전망|예상|검토|추진할|추진한다|계획이|가능성|관측|기대|우려|것으로 보인다|할 방침)"
)
_QUANTITY_RE = re.compile(
    r"\d[\d,.]*\s*(기|호기|GW|MW|㎿|kW|억|조|만|%|퍼센트|달러|유로|원|년|개월|주|일)"
)

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
    "tracking": {"follow_up": 1.5, "repeat": 0.5},
    "diversity": {"max_per_topic": 2, "penalty": 2.5},
    "duplicate_similarity": 0.82,
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


# (피드백 사전확률 기능은 2026-07-16 삭제 — 이벤트 0건, 사용자 결정. 히스토리 참조.)



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


def prior_coverage_count(title: str, prior_titles: list[str]) -> int:
    """같은 사건을 최근 아카이브에서 몇 건이나 다뤘는지 센다.

    후속·반복 보도 판정용이라 클러스터링만큼 엄밀할 필요는 없다. 발송 직전
    중복 제거와 같은 제목 유사도 기준(_same_event)을 재사용한다.
    """
    probe = {"title_kr": title}
    norm = _norm_title(probe)
    toks = _title_tokens(probe)
    if not norm and not toks:
        return 0
    count = 0
    for other in prior_titles:
        other_probe = {"title_kr": other}
        if _same_event(norm, toks, _norm_title(other_probe), _title_tokens(other_probe),
                       _DEFAULT_CONFIG["duplicate_similarity"]):
            count += 1
    return count


def _item_text(item: dict) -> str:
    return " ".join(str(item.get(key) or "") for key in ("title_kr", "title", "summary"))


def derive_evidence_strength(item: dict) -> int:
    """확정 표현·수치 유무로 근거 강도를 판정한다 (LLM 추측 대체).

    확정 사실 3 / 판단 유보 2 / 전망·검토 1. 수치가 하나도 없으면 한 단계 낮춘다.
    """
    text = _item_text(item)
    if not text.strip():
        return 0
    if _SPECULATION_RE.search(text):
        base = 1
    elif _CONFIRMED_RE.search(text):
        base = 3
    else:
        base = 2
    if not _QUANTITY_RE.search(text):
        base -= 1
    return max(0, min(3, base))


def derive_novelty(item: dict) -> int:
    """최근 아카이브에서 같은 사건을 몇 번 다뤘는지로 새 사실 여부를 판정한다.

    prior_coverage 는 news_bot 이 큐 적재 시 계산해 넣는다. 값이 없으면(구 큐
    항목) 판단하지 않고 중립값 2 를 쓴다.
    """
    prior = item.get("prior_coverage")
    if prior is None:
        return 2
    try:
        prior = int(prior)
    except (TypeError, ValueError):
        return 2
    if prior <= 0:
        return 3
    return 2 if prior <= 2 else 1


def _tracking_bonus(item: dict, cfg: dict) -> tuple[float, str]:
    """추적 중인 이슈가 다시 움직였을 때의 가점.

    점수가 기사 단위라 '이 이슈가 며칠째 이어지는 중'이 순위에 안 들어가고 있었다.
    추적이 이 서비스의 차별점이므로 후속 보도를 완전 신규보다 살짝 위에 둔다.
    반복만 되는 이슈(3회 초과)는 가점을 거의 주지 않는다.
    """
    prior = item.get("prior_coverage")
    if not prior:
        return 0.0, ""
    tracking = cfg.get("tracking") or {}
    try:
        prior = int(prior)
    except (TypeError, ValueError):
        return 0.0, ""
    if prior <= 2:
        return float(tracking.get("follow_up", 1.5)), "tracking:follow_up"
    return float(tracking.get("repeat", 0.5)), "tracking:repeat"


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


def score_item(item: dict, cfg: dict,
               now: datetime | None = None) -> tuple[float, dict]:
    """항목 1개 점수 + 설명 내역. features 없으면 legacy 공식."""
    now = now or datetime.now(timezone.utc)
    feats = sanitize_features(item.get("features"))

    if feats is None:
        score, breakdown = _legacy_score(item)
    else:
        # LLM 값이 남아 있어도 코드 판정으로 덮는다. 두 항목은 더 이상 프롬프트에
        # 없지만 옛 큐 항목에는 값이 실려 있다.
        feats["novelty"] = derive_novelty(item)
        feats["evidence_strength"] = derive_evidence_strength(item)
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

        track, track_key = _tracking_bonus(item, cfg)
        if track:
            score += track
            breakdown[track_key] = track

    decay = _time_decay(item, cfg, now)
    if decay:
        score -= decay
        breakdown["time_decay"] = round(-decay, 2)


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


# 같은 사건 판정 보조 ② — 지목한 호기.
#
# 위 두 기준은 제목을 통째로 비교한다. 매체마다 같은 발표에 서로 다른 곁가지를
# 붙이면(2026-08-06 원안위 발표를 매일일보는 '처벌법 개정', 전기신문은 'SMR 규제',
# YTN 은 '한빛 1·2호기' 와 묶어 실었다) 곁가지가 제목을 늘려 문자열 ratio 와 토큰
# 자카드를 동시에 끌어내린다. 실측 3건 상호 0.33~0.44 로 전부 미달 → 국내 3칸이
# 한 사건으로 채워졌다. 기준을 그 밑으로 내리는 건 답이 아니다: 0.50 에서 이미
# '러시아 드론 공격' 과 '자포리자 공격' 이 붙는다(아카이브 실측).
#
# 곁가지 길이와 무관한 신호로 판정한다 — 같은 호기를 같은 태그로 다루면 같은 사건.
# 태그까지 요구하는 이유는 하루 안에 같은 호기가 다른 맥락으로 등장할 때
# (계속운전 심사 ↔ 인근 주민 반대) 붙지 않게 하기 위함.
# 아카이브 740건/19일 재생: 신규 병합 3쌍, 전부 같은 원안위 발표. 오탐 0.

# 긴 이름을 먼저 둔다 — '신고리 5호기' 가 '고리5' 로 잡히면 다른 호기와 충돌한다.
_PLANT_NAMES = "신고리|신월성|신한울|새울|고리|월성|한빛|한울|영광|울진"
_FACILITY_RE = re.compile(rf"({_PLANT_NAMES})\s*([0-9][0-9,·~\-\s]*)\s*호기")


def _title_facilities(item: dict) -> frozenset[str]:
    """제목이 지목한 호기 집합. '고리 3·4호기' → {고리3, 고리4}."""
    title = item.get("title_kr") or item.get("title") or ""
    out: set[str] = set()
    for plant, nums in _FACILITY_RE.findall(title):
        out.update(f"{plant}{n}" for n in re.findall(r"[0-9]+", nums))
    return frozenset(out)


def _norm_tags(item: dict) -> frozenset[str]:
    """'#계속운전' → '계속운전'. 매체마다 태그 표기가 갈려 앞의 # 만 벗긴다."""
    return frozenset(str(t).lstrip("#").strip().lower()
                     for t in (item.get("tags") or []) if str(t).strip())


def _same_facility_event(fac_a: frozenset[str], tags_a: frozenset[str],
                         fac_b: frozenset[str], tags_b: frozenset[str]) -> bool:
    """같은 호기 + 같은 태그. 한쪽이라도 비면 판정하지 않는다(보수적)."""
    return bool(fac_a & fac_b) and bool(tags_a & tags_b)


def cluster_duplicates(items: list[dict], scores: dict[str, float],
                       threshold: float = 0.82) -> tuple[list[dict], list[dict]]:
    """제목 유사도(문자열 ratio + 토큰 자카드)로 같은 사건을 묶고 점수 최고 1건만 유지.

    Returns:
        (kept, dropped) — dropped 각 항목에 `dup_of`(대표 기사 hash)가 붙는다.
        대표가 발송되면 그 중복들도 함께 큐에서 정리하기 위함.
    """
    ordered = sorted(items, key=lambda a: scores.get(a.get("hash", ""), 0), reverse=True)
    kept: list[dict] = []
    # (norm, tokens, facilities, tags, hash)
    kept_sig: list[tuple[str, set[str], frozenset[str], frozenset[str], str]] = []
    dropped: list[dict] = []
    for art in ordered:
        norm = _norm_title(art)
        toks = _title_tokens(art)
        facs = _title_facilities(art)
        tags = _norm_tags(art)
        rep_hash = None
        for kn, kt, kf, kg, kh in kept_sig:
            if _same_event(norm, toks, kn, kt, threshold) or \
                    _same_facility_event(facs, tags, kf, kg):
                rep_hash = kh
                break
        if rep_hash is not None:
            d = dict(art)
            d["dup_of"] = rep_hash
            dropped.append(d)
            continue
        kept.append(art)
        if norm or toks:
            kept_sig.append((norm, toks, facs, tags, art.get("hash", "")))
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


# ---- 선정 하한 ----------------------------------------------------------------
#
# 캡(국내 3 / 해외 6)은 상한이어야 하는데 하한처럼 작동해 왔다 — 조용한 날에도
# 자리를 채우느라 nice_to_know 가 올라갔다(실측: 국내 must_read 는 19일 중 11일이
# 0건인데 매일 3건이 나갔다).
#
# 다만 **절대 점수 하한은 쓸 수 없다.** must_read 의 37%가 features 결손으로
# _legacy_score() 경로를 타 등급 기본값(10점)에 고정되기 때문이다. 하한을 10 이상으로
# 걸면 "중요하지 않은 기사"가 아니라 "큐레이션이 실패한 기사"를 자르게 되고, 그 실패는
# 로그에 아무 흔적을 남기지 않는다. 실측으로 floor=12 에서 '한빛 1·2호기 계속운전
# 청신호'(11.6, must_read) 같은 국내 핵심 뉴스가 탈락했다.
# 근거: docs/2026-08-03-selection-floor-backtest.md
#
# 그래서 하한에는 **면제 1종**을 둔다 — features 결손.
#
# 등급 면제(must_read 무조건 통과)는 2026-08-03 에 제거했다. 20회차 표본에서 한 번도
# 발동하지 않는 조항이었고(features 있는 must_read 중 하한 미만 0건 — 결손 면제가 이미
# 같은 항목을 전부 통과시킨다), 결손이 고쳐지면 "must_read 는 점수와 무관하게 전량
# 통과"만 남아 명세 P1(채우지 않는다)을 그 등급 전체에 대해 무효화한다.
# 등급을 점수 위에 두려면 등급을 믿을 수 있어야 하는데, must_read 의 상당수는 LLM
# 판정이 아니라 큐레이션 실패 폴백이 붙인 값이었다(S1 에서 차단).
# 근거: docs/score_distribution.md §7-2, docs/AS_IS.md C1′.


def floor_verdict(item: dict, scores: dict[str, float],
                  floor: dict | None) -> tuple[bool, str]:
    """하한 통과 여부와 사유. floor 는 {등급: 하한} dict (또는 None=미적용)."""
    if not floor:
        return True, "no_floor"
    # features 가 없으면 점수가 등급 기본값에 고정된다(_legacy_score). 데이터 결손을
    # 중요도로 오독하지 않도록 하한 판정에서 빼고 통과시킨다. 이 면제가 없으면
    # 하한이 "중요하지 않은 기사"가 아니라 "큐레이션이 실패한 기사"를 자른다.
    if sanitize_features(item.get("features")) is None:
        return True, "exempt_no_features"
    limit = floor.get(_get_importance(item))
    if limit is None:
        return True, "no_limit_for_grade"
    return scores.get(item.get("hash", ""), 0.0) >= float(limit), "below_floor"


def resolve_floor(cfg: dict, region_key: str) -> dict | None:
    """ranking_config 의 selection_floor 를 {등급: 하한} 으로 편다.

    설정 형태: {"nice_to_know": {"domestic": 14.0, "overseas": 14.0}}
    region_key 는 "domestic" | "overseas".
    """
    raw = cfg.get("selection_floor")
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for grade, value in raw.items():
        if grade.startswith("_"):
            continue
        if isinstance(value, dict):
            value = value.get(region_key)
        if isinstance(value, (int, float)):
            out[grade] = float(value)
    return out or None


# ---- 종합 파이프라인 (daily_brief 에서 호출) ------------------------------------

DEFAULT_CAPS = {"domestic": {"base": 3, "max": 8}, "overseas": {"base": 6, "max": 12}}


def resolve_caps(cfg: dict, region_key: str) -> dict | None:
    """ranking_config 의 selection_caps 를 그 지역 몫으로 편다.

    설정이 없으면 None 을 내고 호출부의 기존 상수(국내3/해외6)가 그대로 쓰인다 —
    설정 파일이 없거나 깨져도 어제와 같이 동작해야 한다.
    """
    raw = cfg.get("selection_caps")
    if not isinstance(raw, dict):
        return None
    spec = raw.get(region_key)
    if not isinstance(spec, dict):
        return None
    base = int(spec.get("base", DEFAULT_CAPS.get(region_key, {}).get("base", 3)))
    return {
        "base": base,
        "max": int(spec.get("max", base)),
        "must_read_bonus_per": int(spec.get("must_read_bonus_per", 1)),
        "must_read_bonus_max": int(spec.get("must_read_bonus_max", 2)),
    }


def decide_cap(spec: dict, eligible: int, must_read: int, surge_bonus: int = 0) -> tuple[int, dict]:
    """그날 몇 건까지 내보낼지. 계단형 규칙이 아니라 가산형이다.

    캡이 상한이 아니라 **하한처럼** 작동하고 있었다. 국내3/해외6 이 상수라 보도량과
    무관하게 매일 같은 양이 나갔다 — 실측 2026-07-25~08-06 12일 연속 7~9건, 그중
    8/6 은 후보 94건 중 38건이 하한 미달이고도 적격 56건에서 9건만 나갔다.

    계단형(eligible>=4 면 4, must_read>=2 면 5 …)도 생각했으나 경계값이 자의적이고
    한 번 정하면 근거 없이 굳는다. 가산형은 각 항의 기여가 그대로 보여 사후 조정이
    쉽다. surge_bonus 는 R7 에서 붙는다 — 지금은 항상 0 이라, 캡 확대의 원인이
    'must_read 가 많아서'로만 설명된다(원인이 둘이면 어느 쪽인지 못 가른다).
    """
    base = int(spec.get("base", 3))
    max_cap = int(spec.get("max", base))
    mr_bonus = min(int(spec.get("must_read_bonus_max", 2)),
                   must_read * int(spec.get("must_read_bonus_per", 1)))
    # 보도량 항 — must_read 만으로는 캡이 거의 안 움직인다. 실측 백테스트(2026-08-06
    # 큐 82건): 하한을 넘긴 적격이 국내 16 · 해외 13 인데 must_read 는 1 과 0 이라
    # 가산이 +1/+0 에 그쳤다. must_read 는 드물게 붙는 등급이라(19일 중 11일 0건)
    # 그것만 보면 '오늘 실제로 얼마나 많은 일이 있었나'를 못 센다.
    # 기준선을 넘긴 후보가 쌓인 만큼만 늘린다 — 조용한 날은 min(eligible) 이 막는다.
    step = int(spec.get("eligible_bonus_step", 5))
    eb_max = int(spec.get("eligible_bonus_max", 3))
    el_bonus = min(eb_max, max(0, eligible - base) // step) if step > 0 else 0
    raw = base + mr_bonus + el_bonus + surge_bonus
    cap = max(0, min(raw, eligible, max_cap))
    return cap, {
        "base": base, "max": max_cap, "eligible": eligible, "must_read": must_read,
        "must_read_bonus": mr_bonus, "eligible_bonus": el_bonus, "surge_bonus": surge_bonus,
        "cap_before_limits": raw, "cap_applied": cap,
    }


def rank_and_select(items: list[dict], k: int, cfg: dict | None = None,
                    now: datetime | None = None,
                    floor: dict | None = None,
                    cap_spec: dict | None = None,
                    surge_bonus: int = 0) -> tuple[list[dict], dict]:
    """점수화 → 중복 클러스터 → (하한) → 다양성 top-k.

    하한은 **다양성 선별 앞에서** 건다. 뒤에 걸면 같은 topic 이 겹쳐 받은 페널티가
    하한 판정에 섞여 들어가 '중요도가 낮아서'가 아니라 '주제가 겹쳐서' 잘린다.

    Args:
        floor: {등급: 하한 점수}. None 이면 기존 동작 그대로 (하위 호환).

    Returns:
        (선정 리스트, 진단 dict: scores/breakdowns/dropped_duplicates/
         dropped_below_floor/candidate_count)
    """
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)

    scores: dict[str, float] = {}
    breakdowns: dict[str, dict] = {}
    for a in items:
        h = a.get("hash", "")
        s, b = score_item(a, cfg, now)
        scores[h] = s
        breakdowns[h] = b

    kept, dropped = cluster_duplicates(items, scores,
                                       float(cfg.get("duplicate_similarity", 0.82)))

    below: list[dict] = []
    if floor:
        passing = []
        for a in kept:
            ok, _reason = floor_verdict(a, scores, floor)
            if ok:
                passing.append(a)
            else:
                below.append({
                    "hash": a.get("hash", ""),
                    "grade": _get_importance(a),
                    "score": round(scores.get(a.get("hash", ""), 0.0), 2),
                    "title": (a.get("title_kr") or a.get("title") or "")[:80],
                })
        kept = passing

    # 캡은 **하한 통과 뒤** 결정한다. 앞에서 정하면 하한에 걸려 사라질 후보까지
    # 세어 캡이 부풀고, 정작 내보낼 것이 없는 날에 자리만 비운다.
    cap_detail = None
    if cap_spec:
        must_read = sum(1 for a in kept if _get_importance(a) == "must_read")
        k, cap_detail = decide_cap(cap_spec, len(kept), must_read, surge_bonus)

    selected = select_diverse(kept, scores, k, cfg)
    diag = {
        "scores": scores,
        "breakdowns": breakdowns,
        "cap": cap_detail,
        "candidate_count": len(items),
        "dropped_below_floor": below,
        "dropped_duplicates": [{"hash": d.get("hash", ""),
                                "dup_of": d.get("dup_of", ""),
                                "title": (d.get("title_kr") or d.get("title") or "")[:80]}
                               for d in dropped],
    }
    return selected, diag
