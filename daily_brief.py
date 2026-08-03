"""
일일 통합 브리핑 (daily_brief) — digest_queue 를 투자 관점 카드로 발송.

배경:
    news_bot 이 RSS(WNN·IAEA·정책 피드 등)를 매시간 긁어 분석해 digest_queue.json 에
    쌓는다. 이 봇이 그 큐를 '무슨 일 / 왜 중요 / 💰 투자 관점 / 🇰🇷 한수원 시사점'
    카드로 하루 1회 발송한다.

2026-07 개편 (설명 가능한 랭킹 + 발송 원자성):
    - 랭킹: ranking.py (LLM feature × ranking_config.json 가중치, 내역 로깅).
      features 없는 옛 큐 항목은 기존 rank_item 공식으로 하위 호환.
    - 투자 관점: 문장 생성이 아니라 구조화 필드(theme/mechanism/수혜유형/시계/확신)를
      뽑고 Python 이 문장을 조립. 근거 약하면(confidence 0) 줄 생략.
    - 보고서 추천: features 로 Python 이 후보를 먼저 거른 뒤에만 LLM 호출 (0건이 정상).
    - 발송 원자성 (outbox 패턴):
        --plan    선별→브리핑 생성→outbox.json(pending) 기록→큐에서 해당 항목 제거
        (워크플로가 outbox+큐를 먼저 push = delivery claim. push 실패 시 발송 안 함)
        --send    outbox 의 pending 브리핑만 발송, 결과를 outbox_result.json 에 기록
        --confirm outbox 에 발송 결과 병합 + delivery_log.jsonl 적재 (멱등)
      같은 날 재실행 시 이미 sent 인 브리핑은 재발송하지 않는다.

가드레일:
    stdlib + gemini_client(REST) + ranking + sources + telegram_send.
    GEMINI 실패 시: 투자 줄 없이 발송(graceful). 큐 비었으면 발송 스킵.
    telegram_send 는 lazy import (--plan 은 토큰 없이 동작해야 함).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from gemini_client import GeminiError, call_json, is_available
from sources import credibility
import ranking

ROOT = Path(__file__).parent
QUEUE_FILE = ROOT / "digest_queue.json"
SOCIAL_TOPICS_FILE = ROOT / "social_topics.json"
OUTBOX_FILE = ROOT / "outbox.json"
OUTBOX_RESULT_FILE = ROOT / "outbox_result.json"  # .gitignore — 같은 job 안 전달용
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"
KST = timezone(timedelta(hours=9))

MAX_ITEMS = 10  # 소셜 섹션 상한

# 국내/해외 분리 발송 — 둘 다 양이 많아 각각 별도 브리핑 1개씩.
# 국내는 사용자가 다른 경로로도 접하므로 적게(핵심만), 해외가 메인.
DOMESTIC_CAP = 3
FOREIGN_CAP = 6

# 발송 재시도 허용 창(시간). claim 후 발송 실패한 브리핑은 이 시간 안에만 재발송.
# 넘기면 stale_skipped — 낡은 브리핑 재발송·중복 발송 방지.
RESEND_WINDOW_H = 36

_KR_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")

# 명백한 외국 출처 — 429 분류실패로 domestic 태그가 붙어도 해외로 교정.
# 해외 보도자료 와이어(prnewswire 등)도 포함 — 해외 SMR 기업 발표가 이 경로로 들어온다.
_FOREIGN_NEWS = ("world-nuclear-news", "world-nuclear.org", "ans.org", "iaea.org",
                 "nrc.gov", "energy.gov", "oecd-nea", "nucnet", "neimagazine",
                 "reuters", "bloomberg", "powermag", "utilitydive", "spectrum.ieee",
                 "prnewswire", "globenewswire", "businesswire", "accesswire",
                 "newswire.ca")


_HANGUL = re.compile(r"[가-힣]")


def region(art: dict) -> str:
    """기사를 국내/해외 브리핑으로 분류. 기준은 '기사가 다루는 대상'(매체 국적 아님).

    1) scope('kr'|'overseas') — 큐레이션 LLM이 직접 판정한 값이 있으면 최우선
    2) section='khnp'(한수원이 주체) → 출처 불문 국내 (체코 수주 등)
    3) section='international' → 해외 (한국 매체가 쓴 해외 기사도 해외)
    4) section='domestic' → 국내. 단 명백한 외국 뉴스 도메인이면 오분류로 보고 해외 교정
    5) 지역 신호 없는 section(smr 등) → 도메인·제목 언어로 판단
    6) 아무 신호 없으면 해외 — 기본값을 국내로 두면 미국 SMR 기사가 국내로 섞임
    """
    scope = (art.get("scope") or "").lower()
    if scope == "kr":
        return "국내"
    if scope == "overseas":
        return "해외"

    dom = (art.get("domain") or "").lower()
    sec = art.get("section") or ""
    foreign_dom = any(f in dom for f in _FOREIGN_NEWS)
    if sec == "khnp":
        return "국내"
    if sec == "international":
        return "해외"
    if sec == "domestic":
        return "해외" if foreign_dom else "국내"
    if foreign_dom:
        return "해외"
    if any(h in dom for h in _KR_HINTS) or _HANGUL.search(art.get("title") or ""):
        return "국내"
    return "해외"


# ---- 큐/상태 입출력 -----------------------------------------------------------

# 경로 인자는 None 이면 호출 시점의 모듈 상수를 사용 (테스트에서 monkeypatch 가능하게)

def load_queue(path: Path | None = None) -> list[dict]:
    path = path or QUEUE_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_queue(items: list[dict], path: Path | None = None) -> None:
    path = path or QUEUE_FILE
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load_outbox(path: Path | None = None) -> dict | None:
    path = path or OUTBOX_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_outbox(outbox: dict, path: Path | None = None) -> None:
    path = path or OUTBOX_FILE
    path.write_text(json.dumps(outbox, ensure_ascii=False, indent=2), encoding="utf-8")


def get_importance(item: dict) -> str:
    if "importance" in item:
        return item["importance"]
    cat = item.get("category", "")
    return cat if cat in {"must_read", "nice_to_know", "market", "noise"} else "nice_to_know"


# ---- 투자 관점 (구조화 추출 → Python 이 문장 조립) ----------------------------
#
# 제목+요약만으로 깊은 분석은 불가능 → 문장을 길게 만들지 않고 구조를 강제한다.
# LLM 은 '어떤 메커니즘으로 돈의 흐름이 바뀌는지'만 답하고, 렌더링·생략 판단은 Python.

INVEST_THEMES = {"uranium", "smr", "export", "life_extension", "fuel_cycle", "waste",
                 "regulation", "supply_chain", "construction", "financing",
                 "decommissioning", "safety", "grid_demand", "none"}
INVEST_BENEFICIARIES = {"reactor_vendor", "epc", "fuel_supplier", "utility",
                        "uranium_miner", "smr_developer", "grid_equipment", "none"}
INVEST_HORIZONS = {"near", "mid", "long"}

_THEME_KR = {"uranium": "우라늄", "smr": "SMR", "export": "수출", "life_extension": "계속운전",
             "fuel_cycle": "핵연료주기", "waste": "방폐물", "regulation": "규제",
             "supply_chain": "공급망", "construction": "신규건설", "financing": "자금조달",
             "decommissioning": "해체", "safety": "안전", "grid_demand": "전력수요"}
_BEN_KR = {"reactor_vendor": "원자로 공급사", "epc": "EPC", "fuel_supplier": "핵연료 공급사",
           "utility": "발전사업자", "uranium_miner": "우라늄 생산자",
           "smr_developer": "SMR 개발사", "grid_equipment": "전력기기"}
_HORIZON_KR = {"near": "단기", "mid": "중기", "long": "장기"}

INVEST_SYSTEM_PROMPT = """당신은 원자력·에너지 뉴스를 투자 관점으로 번역하는 분석가입니다.
독자는 원자력 업계를 아는 투자자(한수원 실무자)입니다. Doomberg 같은 냉정한 톤.

기사 항목 N개를 받습니다. 각 항목에 대해 **구조화된 투자 판단 필드**만 답하세요.
문장 생성은 시스템이 합니다 — 당신은 필드만.

⚠️ 출력은 정확히 아래 JSON. 다른 텍스트(설명, 펜스 ```)는 금지.
{"investments": [{"idx": 0, "theme": "...", "mechanism": "...", "beneficiary_type": "...", "risk_side": "...", "time_horizon": "...", "confidence": 0}]}

필드 규칙:
1. theme: uranium|smr|export|life_extension|fuel_cycle|waste|regulation|supply_chain|construction|financing|decommissioning|safety|grid_demand|none
   — 투자적으로 해석할 게 없으면 반드시 "none" (지어내지 말 것).
2. mechanism: **돈의 흐름이 왜 바뀌는지** 한국어 1문장(90자 이내). 비용·수주·공급 제약·
   규제·자본지출·연료 수요·프로젝트 일정 중 무엇을 통해 경제적 영향이 생기는지.
   "테마 강화" 같은 추상어 금지. 제목·요약에서 확인 안 되는 인과는 금지.
3. beneficiary_type: reactor_vendor|epc|fuel_supplier|utility|uranium_miner|smr_developer|grid_equipment|none
   — 기업명 아님, 유형만. ⚠️ 특정 종목·매수·매도 언급 절대 금지.
4. risk_side: 불리해지는 쪽(한국어 짧게, 예: "가스 피크발전") 또는 "none".
5. time_horizon: near(1년 내)|mid(1~3년)|long(3년+).
6. confidence: 2=확정 사실 기반 / 1=합리적 해석 / 0=근거 약함(이 항목은 발송에서 생략됨).
7. 모든 idx 가 정확히 한 번씩.

입력: 각 줄이 `[idx] 한국어제목 | 왜중요 | 요약`."""


def _sanitize_invest(raw: dict) -> dict | None:
    """투자 구조화 필드 방어적 파싱. 쓸 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    theme = raw.get("theme")
    theme = theme if isinstance(theme, str) and theme in INVEST_THEMES else "none"
    mech = str(raw.get("mechanism") or "").strip()[:180]
    ben = raw.get("beneficiary_type")
    ben = ben if isinstance(ben, str) and ben in INVEST_BENEFICIARIES else "none"
    risk = str(raw.get("risk_side") or "").strip()[:60]
    if risk.lower() == "none":
        risk = ""
    hor = raw.get("time_horizon")
    hor = hor if isinstance(hor, str) and hor in INVEST_HORIZONS else "mid"
    try:
        conf = int(raw.get("confidence"))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(2, conf))
    return {"theme": theme, "mechanism": mech, "beneficiary_type": ben,
            "risk_side": risk, "time_horizon": hor, "confidence": conf}


def render_investment(struct: dict | None) -> str | None:
    """구조화 필드 → 한국어 투자 관점 한 줄. 근거 약하면 None (줄 생략).

    생략 조건: struct 없음 / theme none / mechanism 비어있음 / confidence 0.
    confidence 1 이면 단정 대신 관찰 수준임을 표기.
    """
    if not struct:
        return None
    if struct["theme"] == "none" or not struct["mechanism"] or struct["confidence"] == 0:
        return None
    parts = [struct["mechanism"].rstrip(".")]
    if struct["beneficiary_type"] != "none":
        parts.append(f"— {_BEN_KR[struct['beneficiary_type']]} 수혜")
    if struct["risk_side"]:
        parts.append(f"/ {struct['risk_side']} 부담")
    tail = f"({_THEME_KR.get(struct['theme'], struct['theme'])}·{_HORIZON_KR[struct['time_horizon']]}"
    if struct["confidence"] == 1:
        tail += "·확신 낮음"
    tail += ")"
    parts.append(tail)
    return " ".join(parts)[:300]


def enrich_investment(items: list[dict]) -> dict[int, dict]:
    """선별된 항목들에 구조화 투자 필드 부여. 실패/키없음 시 빈 dict(보강 없이 진행)."""
    if not is_available() or not items:
        if not is_available():
            print("[daily_brief] GEMINI_API_KEY 없음 → 투자 관점 보강 건너뜀")
        return {}

    lines = []
    for i, art in enumerate(items):
        title = (art.get("title_kr") or art.get("title") or "").replace("\n", " ")[:120]
        why = (art.get("why_important") or art.get("implication") or "").replace("\n", " ")[:160]
        summ = (art.get("summary") or "").replace("\n", " ")[:80]
        lines.append(f"[{i}] {title} | {why} | {summ}")

    try:
        result = call_json(
            INVEST_SYSTEM_PROMPT, "\n".join(lines),
            temperature=0.2, max_output_tokens=4096, timeout=120.0,
        )
    except GeminiError as e:
        print(f"[daily_brief] 투자 보강 실패 → 투자 줄 없이 발송: {e}")
        return {}

    out: dict[int, dict] = {}
    for it in result.get("investments") or []:
        if not isinstance(it, dict):
            continue
        idx = it.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            continue
        struct = _sanitize_invest(it)
        if struct:
            out[idx] = struct
    return out


# ---- 보고서 검토 추천 (Python 게이트 → 후보 있을 때만 LLM) --------------------
#
# 기존엔 선별 전체를 매일 LLM에 물었다. 이제 features 로 후보를 먼저 거른다:
# 후보 0건이면 Gemini 호출 자체가 없다 (0건이 정상 상태).

REPORT_MAX_PER_DAY = 2
REPORTS_KB_FILE = ROOT / "reports_kb.json"
_REPORT_STRONG_EVENTS = {"policy_decision", "regulatory_action", "contract_award",
                         "incident_safety"}
_NEG_EXAMPLE_SIM = 0.7  # negative example 과 이 이상 비슷한 제목은 후보 제외


def _load_kb_negative_examples() -> list[str]:
    """reports_kb.json 의 negative_examples — '예전엔 이 정도는 보고서감이 아니었다'.

    파일 없으면 빈 리스트 (게이트는 features 만으로 동작).
    """
    try:
        kb = json.loads(REPORTS_KB_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for report in kb if isinstance(kb, list) else []:
        for ex in (report.get("negative_examples") or []):
            if isinstance(ex, str) and len(ex.strip()) >= 6:
                out.append(ex.strip())
    return out


def _matches_negative_example(title: str, negatives: list[str]) -> bool:
    import difflib
    t = (title or "").strip()
    if not t:
        return False
    return any(difflib.SequenceMatcher(None, t, neg).ratio() >= _NEG_EXAMPLE_SIM
               for neg in negatives)


def gate_report_candidates(items: list[dict],
                           negatives: list[str] | None = None) -> list[dict]:
    """보고서감 후보 게이트 (Python, 테스트 가능).

    - features 있는 항목: report_worthiness>=2 AND (must_read 또는 정책영향>=2 또는
      강한 이벤트 유형)일 때만 후보. features 없는 옛 항목: must_read 만 후보 (과도기).
    - reports_kb.json 의 negative_examples 와 유사한 제목은 제외 (과잉 추천 억제).
    """
    negatives = _load_kb_negative_examples() if negatives is None else negatives
    out = []
    for a in items:
        title = a.get("title_kr") or a.get("title") or ""
        if negatives and _matches_negative_example(title, negatives):
            continue
        f = ranking.sanitize_features(a.get("features"))
        if f is None:
            if get_importance(a) == "must_read":
                out.append(a)
            continue
        strong = (get_importance(a) == "must_read"
                  or f["policy_materiality"] >= 2
                  or f["event_type"] in _REPORT_STRONG_EVENTS)
        if f["report_worthiness"] >= 2 and strong:
            out.append(a)
    return out


REPORT_SYSTEM_PROMPT = """당신은 한국수력원자력 원자력정책실 정책개발부의 시니어 분석관입니다.
아래는 이미 1차 심사를 통과한 '보고서 후보' 사안들입니다. 이 중 **부서가 별도 보고서
(심층 분석)로 다룰 만큼 큼직한 사안**만 최종 추천하세요. 조건:
- 한수원·한국 원전 정책에 직접적 의사결정 영향이 있는가
- 단순 반복·전망·의견 기사가 아니라 새로운 상황 변화인가
- 공식 발표·규제 결정·계약·법 개정 등 검토할 근거가 충분한가

⚠️ 출력은 정확히 아래 JSON. 추천할 게 없으면 반드시 {"reports": []}.
다른 텍스트(설명, 펜스 ```)는 금지. **각 문자열 값 안에 줄바꿈 절대 금지 — 모두 한 줄로.**

{"reports": [{"idx": 0, "topic": "보고서 주제", "why": "왜 지금 보고서감인지 1-2문장", "angles": ["추천 각도1", "각도2"]}]}

규칙:
1. idx: 입력 항목 번호. 입력에 없는 idx 금지.
2. topic: 보고서 제목처럼 (한국어, 핵심 고유명사 포함).
3. why: 전략적·정책적 함의 중심. 부서 분석관 톤.
4. angles: 2-3개. 보고서에서 다룰 구체적 관점.
5. 최대 2건. 없으면 빈 배열을 두려워 말 것 — 0건이 정상.

입력: 각 줄이 `[idx] 제목 | 왜중요 | 섹션`."""


def build_report_recs(items: list[dict]) -> tuple[str, dict]:
    """보고서감 추천 메시지 + 판단 근거 diag. 후보 0건이면 LLM 호출 없이 빈 결과."""
    candidates = gate_report_candidates(items)
    diag = {"candidates": [c.get("hash", "")[:8] for c in candidates], "recommended": []}
    if not candidates:
        print("[daily_brief] 보고서 후보 0건 (게이트) → 추천 섹션·LLM 호출 생략")
        return "", diag
    if not is_available():
        return "", diag

    lines = []
    for i, a in enumerate(candidates):
        t = (a.get("title_kr") or a.get("title") or "").replace("\n", " ")[:100]
        why = (a.get("why_important") or a.get("implication") or "").replace("\n", " ")[:140]
        lines.append(f"[{i}] {t} | {why} | {a.get('section','')}")

    try:
        result = call_json(REPORT_SYSTEM_PROMPT, "\n".join(lines),
                           temperature=0.2, max_output_tokens=4096, timeout=90.0)
    except GeminiError as e:
        print(f"[daily_brief] 보고서 추천 실패 → 섹션 생략: {e}")
        return "", diag

    reports = []
    for r in (result.get("reports") or []):
        if not isinstance(r, dict) or not r.get("topic"):
            continue
        idx = r.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(candidates)):
            continue
        reports.append((idx, r))
    reports = reports[:REPORT_MAX_PER_DAY]  # 하루 0~2건 강제
    if not reports:
        return "", diag

    from html import escape
    today = datetime.now(KST).date().isoformat()
    out = [f"<b>📝 보고서 검토 추천 ({today})</b>",
           "<i>오늘 동향 중 부서 보고서로 다룰 만한 사안</i>", ""]
    for n, (idx, r) in enumerate(reports, 1):
        out.append(f"<b>{n}. {escape(str(r['topic']).strip())}</b>")
        if r.get("why"):
            out.append(f"   • <b>왜:</b> {escape(str(r['why']).strip())}")
        angles = [str(x).strip() for x in (r.get("angles") or []) if str(x).strip()]
        if angles:
            out.append(f"   • <b>추천 각도:</b> {escape(' / '.join(angles[:3]))}")
        out.append("")
        diag["recommended"].append({"hash": candidates[idx].get("hash", "")[:8],
                                    "topic": str(r["topic"])[:80]})
    print(f"[daily_brief] 보고서 추천 {len(reports)}건 (후보 {len(candidates)}건 중)")
    return "\n".join(out).strip(), diag


# ---- 항목 → 카드 -------------------------------------------------------------

def _korean_or_none(s: str | None) -> str | None:
    """한글이 포함된 실제 한국어 문자열일 때만 반환 (영문·빈값·깨진 fallback 차단)."""
    s = (s or "").strip()
    return s if s and any("가" <= c <= "힣" for c in s) else None


def item_to_card(art: dict, investment: str | None) -> dict:
    """curated 항목을 synthesize.format_cards_message 호환 카드로."""
    link = art.get("link", "")
    # 매체명(전기신문)이 있으면 도메인보다 우선 — Google News 경유 기사는 도메인만
    # 보면 news.google.co.kr 이라 어느 매체인지 알 수 없다.
    label = art.get("publisher") or art.get("domain") or art.get("feed") or "RSS"
    cluster = {
        "url": link,
        "sources": [label],
        "title": art.get("title", ""),
        "meta": label,
    }
    return {
        "topic": art.get("section", ""),
        "cluster": cluster,
        "headline": art.get("title_kr") or art.get("title", ""),
        "what": _korean_or_none(art.get("summary")),
        "why": (art.get("why_important") or "").strip() or None,
        "investment": investment,
        "kr_takeaway": (art.get("implication") or "").strip() or None,
        "cred": credibility(cluster),
    }


# (피드백 inline keyboard 기능은 2026-07-16 사용자 결정으로 완전 삭제 — 브리핑을
#  어지럽혔고 수집된 이벤트도 0건. 재도입 시 git 히스토리의 feedback_ingest.py 참조.)


# ---- 소셜 수집 (원자력정책실 동향봇 통합, 수동 실행 전용) ----------------------

def collect_social(saved_raw: list[Path] | None = None,
                   top_per_topic: int = 5) -> list[tuple[str, dict]]:
    """소셜(Reddit/X/YT) 클러스터 수집 → (label, cluster) 페어.

    saved_raw 주면 그 raw 파일들 파싱(테스트), 아니면 social_topics.json 토픽마다
    last30days 실제 실행. Evidence 텍스트가 cluster['fulltext'] 로 들어가 grounding 됨.
    """
    import send_research as sr

    pairs: list[tuple[str, dict]] = []
    if saved_raw:
        for p in saved_raw:
            clusters = sr.parse_clusters(Path(p).read_text(encoding="utf-8"))
            kept, _ = sr.filter_and_rank(clusters, limit=top_per_topic)
            pairs += [("소셜", c) for c in kept]
        return pairs

    if not SOCIAL_TOPICS_FILE.exists():
        return pairs
    topics = json.loads(SOCIAL_TOPICS_FILE.read_text(encoding="utf-8")).get("topics", [])
    for t in topics:
        try:
            raw = sr.run_research(t["label"], t["subqueries"],
                                  t.get("subreddits", "nuclear,energy"))
            clusters = sr.parse_clusters(raw.read_text(encoding="utf-8"))
            kept, _ = sr.filter_and_rank(clusters, limit=top_per_topic)
            pairs += [(t["label"], c) for c in kept]
        except Exception as e:  # noqa: BLE001 — 토픽 1개 실패가 전체를 막지 않게
            print(f"[daily_brief] 소셜 '{t['label']}' 수집 실패: {e}")
    return pairs


# ---- 계획 수립 (선별 → 브리핑 텍스트 → outbox dict) ----------------------------

def empty_reason(diag: dict) -> str:
    """선정 0건일 때의 사유 문장. '수집이 없었다'와 '기준 미달'은 다른 상태다."""
    below = len(diag.get("dropped_below_floor") or [])
    if below:
        return (f"오늘은 브리핑 기준을 넘는 이슈가 없습니다. "
                f"검토한 후보 {below}건은 웹 아카이브에서 확인할 수 있습니다.")
    return "오늘 새로 확인된 브리핑 이슈가 없습니다."


def region_stats(diag: dict, selected: list[dict], pool: list[dict] | None = None) -> dict:
    """그날 그 지역의 선정 통계.

    features 결손은 하한 판정에서 면제되므로(ranking.floor_verdict) 컷오프 수치만
    봐서는 보이지 않는다. 결손이 줄고 있는지를 회차 단위로 남긴다 —
    수집 로그(news_bot)는 전체 큐 기준이고 이건 그날 후보 풀 기준이다.
    """
    return {
        "candidate_count": int(diag.get("candidate_count") or 0),
        "selected_count": len(selected),
        "below_floor_count": len(diag.get("dropped_below_floor") or []),
        "features_missing": sum(
            1 for a in (pool or []) if not isinstance(a.get("features"), dict)),
    }


def plan_briefs(queue: list[dict],
                social_pairs: list[tuple[str, dict]] | None = None,
                now: datetime | None = None) -> dict:
    """큐(+소셜) → outbox dict (아직 파일로 저장 안 함).

    반환 outbox 구조:
        status: "empty" | "pending"
        briefs: [{name, text, keyboard, status}]
        items:  선별 항목 메타 (delivery_log 용)
        prune_hashes: 큐에서 제거할 hash (선별 + 그 중복 + noise/market)
    """
    from synthesize import format_cards_message, build_cards

    now = now or datetime.now(timezone.utc)
    today = datetime.now(KST).date().isoformat()
    base = {
        "schema_version": 1,
        "date": today,
        "created_at": now.isoformat(),
    }

    if not queue and not social_pairs:
        # 파이프라인은 정상적으로 돌았고 후보가 없었을 뿐이다 — 웹이 이걸
        # '데이터 갱신 실패'와 구분할 수 있도록 0 통계를 남긴다.
        zero = {"candidate_count": 0, "selected_count": 0, "below_floor_count": 0,
                "features_missing": 0}
        return {**base, "status": "empty", "briefs": [], "items": [],
                "selection_stats": {"domestic": dict(zero), "overseas": dict(zero)},
                "prune_hashes": []}

    cfg = ranking.load_config()
    junk_hashes = [a.get("hash", "") for a in queue
                   if get_importance(a) in ("noise", "market")]
    items = [a for a in queue if get_importance(a) not in ("noise", "market")]

    dom_pool = [a for a in items if region(a) == "국내"]
    forn_pool = [a for a in items if region(a) == "해외"]
    dom, dom_diag = ranking.rank_and_select(
        dom_pool, DOMESTIC_CAP, cfg, now, ranking.resolve_floor(cfg, "domestic"))
    forn, forn_diag = ranking.rank_and_select(
        forn_pool, FOREIGN_CAP, cfg, now, ranking.resolve_floor(cfg, "overseas"))
    print(f"[daily_brief] 국내 {len(dom)}건 / 해외 {len(forn)}건 선별 "
          f"(중복 제거 {len(dom_diag['dropped_duplicates']) + len(forn_diag['dropped_duplicates'])}건, "
          f"하한 미달 {len(dom_diag['dropped_below_floor']) + len(forn_diag['dropped_below_floor'])}건)")

    # 투자 보강 — 양쪽 선별분 한 번에 (무료 티어 호출 절감)
    allsel = dom + forn
    inv = enrich_investment(allsel)
    for i, art in enumerate(allsel):
        art["investment_struct"] = inv.get(i)  # 다양성·weekly 집계에서도 사용

    dom_cards = [item_to_card(a, render_investment(a.get("investment_struct")))
                 for a in dom]
    forn_cards = [item_to_card(a, render_investment(a.get("investment_struct")))
                  for a in forn]
    n_omitted = sum(1 for a in allsel
                    if render_investment(a.get("investment_struct")) is None)
    if allsel:
        print(f"[daily_brief] 투자 관점: {len(allsel) - n_omitted}건 표기 / {n_omitted}건 근거 부족 생략")

    briefs: list[dict] = []
    # 국내·해외 둘 다 항상 발송 — 사용자가 같은 시간에 둘 다 기대. 없으면 안내 메시지.
    if dom_cards:
        dom_msg = format_cards_message(dom_cards, header="🇰🇷 원자력 국내 브리핑")
    else:
        dom_msg = (f"<b>📰 🇰🇷 원자력 국내 브리핑 ({today})</b>\n\n"
                   f"<i>{empty_reason(dom_diag)}</i>")
    briefs.append({"name": "국내", "text": dom_msg, "status": "pending"})

    forn_msg = (format_cards_message(forn_cards, header="🌐 원자력 해외 브리핑")
                if forn_cards else "")
    if social_pairs:
        social_cards = build_cards(social_pairs[:MAX_ITEMS], self_check=False) or []
        if social_cards:
            sec = format_cards_message(
                social_cards, header="━━ 🔥 소셜 화제 (Reddit·X) ━━", show_header=False)
            forn_msg = (forn_msg + "\n" + sec) if forn_msg else sec
            print(f"[daily_brief] 소셜 카드 {len(social_cards)}개 (해외 브리핑에 추가)")
    if not forn_msg:
        forn_msg = (f"<b>📰 🌐 원자력 해외 브리핑 ({today})</b>\n\n"
                    f"<i>{empty_reason(forn_diag)}</i>")
    briefs.append({"name": "해외", "text": forn_msg, "status": "pending"})

    # 보고서 검토 추천 — Python 게이트 통과 후보 있을 때만 LLM (없으면 미발송)
    rec, report_diag = build_report_recs(allsel)
    if rec:
        briefs.insert(0, {"name": "보고서추천", "text": rec, "status": "pending"})

    # delivery_log 용 항목 메타 (점수 내역 = '왜 이 기사가 올라왔나' 증거)
    def _item_meta(a: dict, reg: str, diag: dict) -> dict:
        h = a.get("hash", "")
        return {
            "hash": h,
            "title_kr": (a.get("title_kr") or a.get("title") or "")[:100],
            "region": reg,
            "section": a.get("section", ""),
            # LLM 판정 scope (없으면 region()이 휴리스틱으로 결정한 것 — 오분류 추적용)
            "scope": a.get("scope", ""),
            "domain": a.get("domain", ""),
            "theme": (a.get("investment_struct") or {}).get("theme", ""),
            "score": diag["scores"].get(h),
            "breakdown": diag["breakdowns"].get(h),
        }

    out_items = ([_item_meta(a, "국내", dom_diag) for a in dom]
                 + [_item_meta(a, "해외", forn_diag) for a in forn])

    # 큐 정리 대상: 선별본 + 선별본의 중복(후속보도) + noise/market.
    # 선별 안 된 항목은 큐에 남아 다음날 재경쟁 (시간 감쇠·3일 자동정리로 상한).
    selected_hashes = {a.get("hash", "") for a in allsel}
    dup_hashes = [d["hash"] for d in
                  dom_diag["dropped_duplicates"] + forn_diag["dropped_duplicates"]
                  if d.get("dup_of") in selected_hashes]
    prune = sorted((selected_hashes | set(dup_hashes) | set(junk_hashes)) - {""})

    return {**base, "status": "pending", "briefs": briefs, "items": out_items,
            "report_diag": report_diag,
            "selection_stats": {
                "domestic": region_stats(dom_diag, dom, dom_pool),
                "overseas": region_stats(forn_diag, forn, forn_pool),
            },
            "dropped_duplicates": dom_diag["dropped_duplicates"] + forn_diag["dropped_duplicates"],
            "prune_hashes": prune}


def prune_queue(queue: list[dict], prune_hashes: set[str]) -> list[dict]:
    return [a for a in queue if a.get("hash", "") not in prune_hashes]


# ---- 발송/확정 ----------------------------------------------------------------

def _outbox_age_hours(outbox: dict, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(outbox.get("created_at", ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    return (now - created).total_seconds() / 3600


def send_outbox(outbox: dict, now: datetime | None = None) -> list[dict]:
    """outbox 의 pending/failed 브리핑 발송. 결과 리스트 반환 + outbox 상태 갱신.

    - 이미 sent 인 브리핑은 건드리지 않음 → 같은 날 재실행해도 중복 발송 없음.
    - RESEND_WINDOW_H 를 넘긴 outbox 는 stale_skipped (낡은 브리핑 중복 방지).
    """
    import time
    from telegram_send import send_long_text  # lazy — plan 은 토큰 없이 동작

    now = now or datetime.now(timezone.utc)
    stale = _outbox_age_hours(outbox, now) > RESEND_WINDOW_H
    results: list[dict] = []
    first_send = True
    for i, brief in enumerate(outbox.get("briefs", [])):
        if brief.get("status") not in ("pending", "failed"):
            continue
        if stale:
            brief["status"] = "stale_skipped"
            results.append({"idx": i, "status": "stale_skipped"})
            print(f"[daily_brief] {brief.get('name')} 브리핑 — {RESEND_WINDOW_H}h 초과, 재발송 생략")
            continue
        if not first_send:
            time.sleep(2)  # 텔레그램 rate limit
        first_send = False
        try:
            resp = send_long_text(brief["text"], parse_mode="HTML")
            ok = sum(1 for r in resp if r.get("ok"))
            success = ok == len(resp) and ok > 0
        except Exception as e:  # noqa: BLE001 — 브리핑 1개 실패가 나머지를 막지 않게
            print(f"[daily_brief] {brief.get('name')} 발송 오류: {type(e).__name__}: {str(e)[:150]}")
            success = False
        brief["status"] = "sent" if success else "failed"
        if success:
            brief["sent_at"] = now.isoformat()
        results.append({"idx": i, "status": brief["status"],
                        "sent_at": brief.get("sent_at")})
        print(f"[daily_brief] {brief.get('name')} 브리핑 발송 → {brief['status']}")
    _update_overall_status(outbox)
    return results


def _update_overall_status(outbox: dict) -> None:
    statuses = [b.get("status") for b in outbox.get("briefs", [])]
    if not statuses:
        outbox["status"] = outbox.get("status", "empty")
    elif all(s in ("sent", "stale_skipped") for s in statuses):
        outbox["status"] = "sent"
    elif any(s == "sent" for s in statuses):
        outbox["status"] = "partial"
    else:
        outbox["status"] = "pending"


def apply_send_results(outbox: dict, results: list[dict]) -> dict:
    """outbox_result.json 의 결과를 outbox 에 병합 (멱등 — 재적용해도 동일)."""
    briefs = outbox.get("briefs", [])
    for r in results:
        idx = r.get("idx")
        if isinstance(idx, int) and 0 <= idx < len(briefs):
            briefs[idx]["status"] = r.get("status", briefs[idx].get("status"))
            if r.get("sent_at"):
                briefs[idx]["sent_at"] = r["sent_at"]
    _update_overall_status(outbox)
    return outbox


def append_selection_stats(outbox: dict, path: Path | None = None,
                           now: datetime | None = None) -> bool:
    """그날의 선정 통계를 delivery_log.jsonl 에 한 줄 남긴다.

    웹이 '오늘 0건'을 안전하게 렌더하려면 후보가 몇 건이었고 하한에서 몇 건이
    빠졌는지 알아야 하는데, rank_and_select 의 진단은 런타임 값이라 나중에 도는
    build_data 가 알 방법이 없다. 그래서 여기서 계약으로 남긴다.

    기사 레코드와 달리 hash 가 없어 (date, hash) 멱등이 안 걸린다. 재실행하면
    줄이 늘어나는데, 로그는 지우지 않고 **읽는 쪽이** generated_at 최신 +
    pipeline_status 우선순위로 고른다(build_data.pick_selection_stats).
    """
    stats = outbox.get("selection_stats")
    if not isinstance(stats, dict):
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    failed = any(b.get("status") == "failed" for b in outbox.get("briefs", []))
    rec = {
        "record_type": "selection_stats",
        "date": outbox.get("date", ""),
        "generated_at": now.astimezone(KST).isoformat(),
        "pipeline_status": "partial" if failed else "ok",
        **{k: v for k, v in stats.items()},
    }
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def append_delivery_log(outbox: dict, path: Path | None = None) -> int:
    """발송 성공(sent)한 브리핑의 항목들을 delivery_log.jsonl 에 적재 (멱등).

    이미 (date, hash) 가 기록돼 있으면 건너뜀. 적재 건수 반환.
    """
    path = path or DELIVERY_LOG_FILE
    sent_regions = {b.get("name") for b in outbox.get("briefs", [])
                    if b.get("status") == "sent"}
    if not sent_regions:
        return 0
    existing: set[tuple[str, str]] = set()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    existing.add((rec.get("date", ""), rec.get("hash", "")))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
    added = 0
    with path.open("a", encoding="utf-8") as fp:
        for item in outbox.get("items", []):
            if item.get("region") not in sent_regions:
                continue
            key = (outbox.get("date", ""), item.get("hash", ""))
            if key in existing:
                continue
            rec = {"date": outbox.get("date", ""), **item}
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing.add(key)
            added += 1
    return added


# ---- CLI 서브커맨드 ------------------------------------------------------------

def cmd_plan() -> int:
    """선별→outbox(pending) 기록→큐 정리. 발송은 하지 않는다 (claim 단계).

    멱등성:
        - 오늘 outbox 가 이미 있으면(충돌 재시도·재실행) 재계획 없이 재사용,
          큐 정리만 다시 적용 (git reset 으로 큐가 되돌아온 경우 대비). Gemini 재호출 0.
        - 어제 outbox 가 아직 pending(발송 실패)이고 36h 이내면 덮어쓰지 않고 보존
          → 이번 실행은 그 재발송에 사용. 오늘 기사는 큐에 남아 내일 발송.
    """
    today = datetime.now(KST).date().isoformat()
    existing = load_outbox()
    if existing:
        if existing.get("date") == today and existing.get("status") != "empty":
            queue = load_queue()
            pruned = prune_queue(queue, set(existing.get("prune_hashes", [])))
            if len(pruned) != len(queue):
                save_queue(pruned)
                print(f"[daily_brief] plan 재사용 — 큐 정리 재적용 {len(queue)}→{len(pruned)}")
            else:
                print("[daily_brief] plan 재사용 (오늘 outbox 이미 존재)")
            return 0
        if (existing.get("status") in ("pending", "partial")
                and _outbox_age_hours(existing) <= RESEND_WINDOW_H):
            print("[daily_brief] 직전 outbox 미발송분 있음 → 새 계획 생략, 재발송 대기")
            return 0

    queue = load_queue()
    outbox = plan_briefs(queue)
    save_outbox(outbox)
    if outbox["status"] == "empty":
        print("[daily_brief] 큐 비어있음 → outbox=empty (발송 스킵)")
        return 0
    pruned = prune_queue(queue, set(outbox["prune_hashes"]))
    save_queue(pruned)
    print(f"[daily_brief] plan 완료 — 브리핑 {len(outbox['briefs'])}개, "
          f"큐 {len(queue)}→{len(pruned)}")
    return 0


def cmd_send() -> int:
    """outbox 의 pending 브리핑 발송, 결과를 outbox + outbox_result.json 에 기록."""
    outbox = load_outbox()
    if not outbox or outbox.get("status") == "empty":
        print("[daily_brief] outbox 없음/empty → 발송 스킵")
        return 0
    if outbox.get("status") == "sent":
        print("[daily_brief] 이미 전부 발송됨 → 스킵 (중복 방지)")
        return 0
    results = send_outbox(outbox)
    save_outbox(outbox)
    OUTBOX_RESULT_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    failed = [r for r in results if r["status"] == "failed"]
    return 1 if failed else 0


def cmd_confirm() -> int:
    """발송 결과를 outbox 에 병합(멱등) + delivery_log 적재. git reset 후 재적용용."""
    outbox = load_outbox()
    if not outbox:
        print("[daily_brief] outbox 없음 → confirm 스킵")
        return 0
    if OUTBOX_RESULT_FILE.exists():
        try:
            results = json.loads(OUTBOX_RESULT_FILE.read_text(encoding="utf-8"))
            if isinstance(results, list):
                apply_send_results(outbox, results)
        except (OSError, json.JSONDecodeError):
            print("[daily_brief] outbox_result 파싱 실패 → outbox 자체 상태 사용")
    n = append_delivery_log(outbox)
    # 발송이 전부 실패해도 통계는 남긴다 — 웹이 '조용한 날'과 '파이프라인 실패'를
    # 구분하려면 오늘 파이프라인이 돌았다는 사실 자체가 필요하다.
    append_selection_stats(outbox)
    save_outbox(outbox)
    print(f"[daily_brief] confirm — 상태 {outbox.get('status')}, delivery_log +{n}건")
    return 0


# ---- 메인 (수동/로컬 실행 — 기존 UX 유지) --------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="일일 통합 카드 브리핑")
    parser.add_argument("--plan", action="store_true", help="선별→outbox 기록 (발송 없음)")
    parser.add_argument("--send", action="store_true", help="outbox pending 발송")
    parser.add_argument("--confirm", action="store_true", help="발송 결과 병합+delivery_log")
    parser.add_argument("--dry-run", action="store_true", help="발송 없이 출력")
    parser.add_argument("--from-curated", action="store_true",
                        help="테스트용: digest_queue 대신 curated.json 전체를 입력으로")
    parser.add_argument("--keep-queue", action="store_true",
                        help="발송 후 큐를 비우지 않음 (테스트용)")
    parser.add_argument("--with-social", action="store_true",
                        help="소셜(last30days) 실제 수집해 합침 (느림, 스킬 필요)")
    parser.add_argument("--social-raw", nargs="*", default=None,
                        help="테스트용: 저장된 raw 파일로 소셜 섹션 구성")
    args = parser.parse_args()

    if args.plan:
        return cmd_plan()
    if args.send:
        return cmd_send()
    if args.confirm:
        return cmd_confirm()

    # ---- 수동 실행 경로: 계획+발송+로그를 한 번에 (git claim 없음) ----
    if args.from_curated:
        raw = json.loads((ROOT / "curated.json").read_text(encoding="utf-8"))
        queue = list(raw.values()) if isinstance(raw, dict) else raw
        # curated 항목엔 hash 필드가 없음 (key 가 hash) — 보강
        if isinstance(raw, dict):
            for h, item in raw.items():
                if isinstance(item, dict):
                    item.setdefault("hash", h)
    else:
        queue = load_queue()

    social_pairs = None
    if args.social_raw:
        social_pairs = collect_social(saved_raw=[Path(p) for p in args.social_raw])
        print(f"[daily_brief] 소셜(저장본) {len(social_pairs)}건")
    elif args.with_social:
        social_pairs = collect_social()
        print(f"[daily_brief] 소셜(라이브) {len(social_pairs)}건")

    if not queue and not social_pairs:
        print("[daily_brief] 큐·소셜 모두 비어있음 → 발송 스킵")
        return 0

    print(f"[daily_brief] curated 입력 {len(queue)}건")
    outbox = plan_briefs(queue, social_pairs=social_pairs)
    if outbox["status"] == "empty" or not outbox["briefs"]:
        print("[daily_brief] 발송할 내용 없음 → 스킵")
        return 0

    if args.dry_run:
        for brief in outbox["briefs"]:
            print("\n" + "=" * 60 + f"  [{brief['name']} 브리핑]")
            print(brief["text"])
        # 점수 내역 미리보기 — '왜 이 기사가 올라왔나'
        for item in outbox["items"]:
            print(f"  · [{item['region']}] {item['score']} {item['title_kr'][:50]} "
                  f"{item.get('breakdown')}")
        print(f"\n[dry-run] 브리핑 {len(outbox['briefs'])}개 (발송·큐 정리 생략)")
        return 0

    send_outbox(outbox)
    append_delivery_log(outbox)
    append_selection_stats(outbox)
    if not args.from_curated and not args.keep_queue:
        pruned = prune_queue(queue, set(outbox["prune_hashes"]))
        save_queue(pruned)
        print(f"[daily_brief] 큐 정리 {len(queue)}→{len(pruned)}")
    return 0 if outbox.get("status") == "sent" else 1


if __name__ == "__main__":
    sys.exit(main())
