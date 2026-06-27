"""
일일 통합 브리핑 (daily_brief) — digest_queue 를 투자 관점 카드로 발송.

배경:
    news_bot 이 RSS(WNN·IAEA·정책 피드 등)를 매시간 긁어 분석
    (title_kr / why_important / implication / domain / importance)해서
    digest_queue.json 에 쌓는다. 기존 digest_bot 은 이 큐를 섹션 리스트로 발송했다.

이 봇(digest_bot 대체):
    같은 큐를 받아 '무슨 일 / 왜 중요 / 💰 투자 관점 / 🇰🇷 한수원 시사점' 카드로,
    하루 1회 발송. 기존 분석(why_important·implication)은 재사용하고 **투자 관점만**
    새로 Gemini(REST + 429 재시도)로 보강 → 싸고, SDK 의존성 없음, 한도에 안 죽음.

설계 결정 (plans/2026-06-14-nuclear-bot-consolidation.md):
    (A) curated 분석 재사용 + 투자 줄만 추가  (B) WNN 은 curated 가 커버
    중복방지: digest_queue 발송 후 비움 (digest_bot 과 동일 메커니즘)

가드레일:
    stdlib + gemini_client(REST) + sources + telegram_send. google-genai SDK 안 씀.
    GEMINI 실패 시: 투자 줄 없이 발송(graceful). 큐 비었으면 발송 스킵.
"""

from __future__ import annotations

import argparse
import json
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
from telegram_send import send_long_text

ROOT = Path(__file__).parent
QUEUE_FILE = ROOT / "digest_queue.json"
SOCIAL_TOPICS_FILE = ROOT / "social_topics.json"
KST = timezone(timedelta(hours=9))

MAX_ITEMS = 10  # 소셜 섹션 상한

# 국내/해외 분리 발송 — 둘 다 양이 많아 각각 별도 브리핑 1개씩.
DOMESTIC_CAP = 8
FOREIGN_CAP = 8
_KR_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")

# 도메인 1차 소스 가중 (digest_bot.rank_item 차용)
PRIMARY_DOMAINS = ("iaea.org", "world-nuclear-news", "khnp.co.kr",
                   "nssc.go.kr", "motie.go.kr", "nrc.gov")


# 명백한 외국 뉴스 도메인 — 429 분류실패로 domestic 태그가 붙어도 해외로 교정
_FOREIGN_NEWS = ("world-nuclear-news", "ans.org", "iaea.org", "nrc.gov",
                 "energy.gov", "oecd-nea", "neimagazine", "reuters",
                 "bloomberg", "powermag", "utilitydive", "spectrum.ieee")


def region(art: dict) -> str:
    """기사를 국내/해외로 분류 (키워드 피드·오분류 모두 견고).

    1) section='khnp'(한수원이 주체) → 출처 불문 국내 (체코 수주 등)
    2) 명백한 외국 뉴스 도메인 → 해외 (429 오분류도 교정)
    3) 한국 도메인(.kr) → 국내
    4) section='international' → 해외
    5) 그 외(국내 키워드 피드 등 도메인 불명확) → 국내
    """
    dom = (art.get("domain") or "").lower()
    sec = art.get("section") or ""
    if sec == "khnp":
        return "국내"
    if any(f in dom for f in _FOREIGN_NEWS):
        return "해외"
    if any(h in dom for h in _KR_HINTS):
        return "국내"
    if sec == "international":
        return "해외"
    return "국내"


# ---- 큐 입출력 ---------------------------------------------------------------

def load_queue(path: Path = QUEUE_FILE) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_queue(items: list[dict], path: Path = QUEUE_FILE) -> None:
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def get_importance(item: dict) -> str:
    if "importance" in item:
        return item["importance"]
    cat = item.get("category", "")
    return cat if cat in {"must_read", "nice_to_know", "market", "noise"} else "nice_to_know"


def rank_item(art: dict) -> float:
    base = 10.0 if get_importance(art) == "must_read" else 5.0
    if art.get("section") == "khnp":
        base += 2.0
    if any(d in (art.get("domain", "") or "") for d in PRIMARY_DOMAINS):
        base += 2.0
    if art.get("related_reports"):
        base += 1.0
    return base


# ---- 투자 관점 보강 (REST + 재시도) -----------------------------------------

INVEST_SYSTEM_PROMPT = """당신은 원자력·에너지 뉴스를 투자 관점으로 번역하는 분석가입니다.
독자는 원자력 업계를 아는 투자자(한수원 실무자)입니다. Doomberg 같은 냉정한 톤.

기사 항목 N개를 받습니다. 각 항목에 '투자 관점' 한 문장을 답하세요 —
이 뉴스가 가리키는 **투자 테마·방향 + 수혜/피해 섹터**.

⚠️ 출력은 정확히 아래 JSON. 다른 텍스트(설명, 펜스 ```)는 금지.
{"investments": [{"idx": 0, "text": "한국어 1문장 또는 null"}]}

규칙:
1. text: 한국어 1문장. 예) "데이터센터 전력수요 테마 강화, 원전 재가동·SMR 밸류체인 수혜 / 가스 피크발전 압박".
2. ⚠️ 매수·매도·목표가 같은 투자 권유 절대 금지. 특정 종목 추천 아님 — 테마·방향만.
3. 제공된 제목·근거로 투자적으로 해석할 게 없으면 절대 지어내지 말고 null.
4. 모든 idx 가 정확히 한 번씩.

입력: 각 줄이 `[idx] 한국어제목 | 왜중요 | 요약`."""


def enrich_investment(items: list[dict]) -> dict[int, str]:
    """각 항목에 투자 관점 한 문장 부여. 실패/키없음 시 빈 dict(보강 없이 진행)."""
    if not is_available() or not items:
        if not is_available():
            print("[daily_brief] GEMINI_API_KEY 없음 → 투자 관점 보강 건너뜀")
        return {}

    lines = []
    for i, art in enumerate(items):
        title = (art.get("title_kr") or art.get("title") or "").replace("\n", " ")[:120]
        why = (art.get("why_important") or "").replace("\n", " ")[:160]
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

    out: dict[int, str] = {}
    for it in result.get("investments") or []:
        if not isinstance(it, dict):
            continue
        idx = it.get("idx")
        txt = it.get("text")
        if isinstance(idx, int) and 0 <= idx < len(items) and txt:
            out[idx] = str(txt).strip()[:300]
    return out


# ---- 보고서 검토 추천 (있을 때만) -------------------------------------------

REPORT_SYSTEM_PROMPT = """당신은 한국수력원자력 원자력정책실 정책개발부의 시니어 분석관입니다.
오늘의 원자력 동향 목록을 받습니다. 이 중 **부서가 별도 보고서(심층 분석)로 다룰 만큼
큼직한 사안**만 골라 추천하세요. 평범한 뉴스·루틴 진행상황은 절대 추천하지 마세요.

보고서감 기준 (엄격 — 애매하면 넣지 말 것):
- 정책 대전환, 대형 계약·수주, 중대 안전·규제 변화
- 새로운 전략적 기회/위협, 시장 구조 변화, 한국 원전·수출에 큰 함의

⚠️ 출력은 정확히 아래 JSON. 보고서감이 없으면 반드시 빈 배열 {"reports": []}.
다른 텍스트(설명, 펜스 ```)는 금지. **각 문자열 값 안에 줄바꿈 절대 금지 — 모두 한 줄로.**

{"reports": [{"topic": "보고서 주제", "why": "왜 지금 보고서감인지 1-2문장", "angles": ["추천 각도1", "각도2"]}]}

규칙:
1. topic: 보고서 제목처럼 (한국어, 핵심 고유명사 포함).
2. why: 전략적·정책적 함의 중심. 부서 분석관 톤.
3. angles: 2-3개. 보고서에서 다룰 구체적 관점.
4. 진짜 큼직한 것만. 하루 0~2건이 정상. 없으면 빈 배열을 두려워 말 것.

입력: 각 줄이 `[중요도] 제목 | 왜중요 | 섹션`."""


def build_report_recs(items: list[dict]) -> str:
    """오늘 동향 중 '보고서감' 사안 추천 메시지. 없으면 빈 문자열(섹션 미발송)."""
    if not is_available() or not items:
        return ""
    lines = []
    for a in items:
        t = (a.get("title_kr") or a.get("title") or "").replace("\n", " ")[:100]
        why = (a.get("why_important") or "").replace("\n", " ")[:140]
        lines.append(f"[{get_importance(a)}] {t} | {why} | {a.get('section','')}")

    try:
        result = call_json(REPORT_SYSTEM_PROMPT, "\n".join(lines),
                           temperature=0.2, max_output_tokens=4096, timeout=90.0)
    except GeminiError as e:
        print(f"[daily_brief] 보고서 추천 실패 → 섹션 생략: {e}")
        return ""

    reports = [r for r in (result.get("reports") or []) if isinstance(r, dict) and r.get("topic")]
    if not reports:
        return ""

    from html import escape
    from datetime import date
    out = [f"<b>📝 보고서 검토 추천 ({date.today().isoformat()})</b>",
           "<i>오늘 동향 중 부서 보고서로 다룰 만한 사안</i>", ""]
    for i, r in enumerate(reports, 1):
        out.append(f"<b>{i}. {escape(str(r['topic']).strip())}</b>")
        if r.get("why"):
            out.append(f"   • <b>왜:</b> {escape(str(r['why']).strip())}")
        angles = [str(x).strip() for x in (r.get("angles") or []) if str(x).strip()]
        if angles:
            out.append(f"   • <b>추천 각도:</b> {escape(' / '.join(angles[:3]))}")
        out.append("")
    print(f"[daily_brief] 보고서 추천 {len(reports)}건")
    return "\n".join(out).strip()


# ---- 항목 → 카드 -------------------------------------------------------------

def _korean_or_none(s: str | None) -> str | None:
    """한글이 포함된 실제 한국어 문자열일 때만 반환 (영문·빈값·깨진 fallback 차단)."""
    s = (s or "").strip()
    return s if s and any("가" <= c <= "힣" for c in s) else None


def item_to_card(art: dict, investment: str | None) -> dict:
    """curated 항목을 synthesize.format_cards_message 호환 카드로."""
    link = art.get("link", "")
    cluster = {
        "url": link,
        "sources": [art.get("domain") or art.get("feed") or "RSS"],
        "title": art.get("title", ""),
        "meta": art.get("domain", ""),
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


# ---- 소셜 수집 (원자력정책실 동향봇 통합) ------------------------------------

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


# ---- 메인 -------------------------------------------------------------------

def build_briefs(queue: list[dict],
                 social_pairs: list[tuple[str, dict]] | None = None) -> list[tuple[str, str]]:
    """큐(+소셜) → [(지역명, 메시지)]. 국내·해외 2개 브리핑으로 분리.

    소셜(Reddit/X)은 대부분 글로벌이라 해외 브리핑에 붙임.
    """
    from synthesize import format_cards_message, build_cards

    items = [a for a in queue if get_importance(a) != "noise"]
    dom = sorted([a for a in items if region(a) == "국내"], key=rank_item, reverse=True)[:DOMESTIC_CAP]
    forn = sorted([a for a in items if region(a) == "해외"], key=rank_item, reverse=True)[:FOREIGN_CAP]
    print(f"[daily_brief] 국내 {len(dom)}건 / 해외 {len(forn)}건 선별")

    # 투자 보강 — 양쪽 선별분 한 번에 (무료 티어 호출 절감)
    allsel = dom + forn
    inv = enrich_investment(allsel)
    dom_cards = [item_to_card(a, inv.get(i)) for i, a in enumerate(dom)]
    forn_cards = [item_to_card(a, inv.get(len(dom) + i)) for i, a in enumerate(forn)]

    from datetime import date
    today = date.today().isoformat()

    briefs: list[tuple[str, str]] = []
    # 국내·해외 둘 다 항상 발송 — 사용자가 같은 시간에 둘 다 기대. 없으면 안내 메시지.
    if dom_cards:
        briefs.append(("국내", format_cards_message(dom_cards, header="🇰🇷 원자력 국내 브리핑")))
    else:
        briefs.append(("국내",
            f"<b>📰 🇰🇷 원자력 국내 브리핑 ({today})</b>\n\n"
            "<i>오늘은 별도로 잡힌 국내 동향이 없습니다.</i>"))

    forn_msg = format_cards_message(forn_cards, header="🌐 원자력 해외 브리핑") if forn_cards else ""
    if social_pairs:
        social_cards = build_cards(social_pairs[:MAX_ITEMS], self_check=False) or []
        if social_cards:
            sec = format_cards_message(
                social_cards, header="━━ 🔥 소셜 화제 (Reddit·X) ━━", show_header=False)
            forn_msg = (forn_msg + "\n" + sec) if forn_msg else sec
            print(f"[daily_brief] 소셜 카드 {len(social_cards)}개 (해외 브리핑에 추가)")
    if not forn_msg:
        forn_msg = (f"<b>📰 🌐 원자력 해외 브리핑 ({today})</b>\n\n"
                    "<i>오늘은 별도로 잡힌 해외 동향이 없습니다.</i>")
    briefs.append(("해외", forn_msg))

    # 보고서 검토 추천 — 큼직한 사안 있을 때만 맨 앞에 (없으면 미발송)
    rec = build_report_recs(allsel)
    if rec:
        briefs.insert(0, ("보고서추천", rec))

    return briefs


def main() -> int:
    parser = argparse.ArgumentParser(description="일일 통합 카드 브리핑")
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

    if args.from_curated:
        raw = json.loads((ROOT / "curated.json").read_text(encoding="utf-8"))
        queue = list(raw.values()) if isinstance(raw, dict) else raw
    else:
        queue = load_queue()

    # 소셜 수집 (옵션)
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
    briefs = build_briefs(queue, social_pairs=social_pairs)
    if not briefs:
        print("[daily_brief] 발송할 내용 없음 → 스킵")
        return 0

    if args.dry_run:
        for name, msg in briefs:
            print("\n" + "=" * 60 + f"  [{name} 브리핑]")
            print(msg)
        print(f"\n[dry-run] 브리핑 {len(briefs)}개 (발송 생략)")
        return 0

    import time
    all_ok = True
    for i, (name, msg) in enumerate(briefs):
        if i > 0:
            time.sleep(2)  # 텔레그램 rate limit
        results = send_long_text(msg, parse_mode="HTML")
        ok = sum(1 for r in results if r.get("ok"))
        print(f"[daily_brief] {name} 브리핑 발송 {ok}/{len(results)}")
        all_ok = all_ok and ok == len(results)

    if not args.from_curated and not args.keep_queue:
        save_queue([])
        print("[daily_brief] 큐 비움")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
