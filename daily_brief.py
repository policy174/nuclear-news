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

MAX_ITEMS = 12  # 소셜 섹션 상한

# 섹션 그룹 — 한국(한수원·국내) 슬롯을 보장해 외국 기사에 안 밀리게.
SECTION_ORDER = ["khnp", "domestic", "smr", "international"]
SECTION_LABEL = {
    "khnp": "🇰🇷 한수원", "domestic": "🏛️ 국내",
    "smr": "🔋 SMR", "international": "🌐 해외",
}
SECTION_CAP = {"khnp": 3, "domestic": 3, "smr": 3, "international": 5}

# 도메인 1차 소스 가중 (digest_bot.rank_item 차용)
PRIMARY_DOMAINS = ("iaea.org", "world-nuclear-news", "khnp.co.kr",
                   "nssc.go.kr", "motie.go.kr", "nrc.gov")


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


# ---- 항목 → 카드 -------------------------------------------------------------

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
        "what": (art.get("summary") or "").strip() or None,
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

def build_brief(queue: list[dict],
                social_pairs: list[tuple[str, dict]] | None = None) -> tuple[str, int]:
    """큐(+소셜) → (카드 메시지, 카드 수). 섹션별 그룹(한국 보장)."""
    from synthesize import format_cards_message, build_cards
    from datetime import date

    items = [a for a in queue if get_importance(a) != "noise"]

    # 섹션별로 묶고 각 섹션 상한만큼 선별 — 한수원·국내가 외국에 안 밀리게 보장
    by_sec: dict[str, list[dict]] = {s: [] for s in SECTION_ORDER}
    for a in items:
        sec = a.get("section") or "international"
        by_sec[sec if sec in by_sec else "international"].append(a)

    selected: list[tuple[str, dict]] = []
    for s in SECTION_ORDER:
        top = sorted(by_sec[s], key=rank_item, reverse=True)[:SECTION_CAP[s]]
        selected += [(s, a) for a in top]
        if by_sec[s]:
            print(f"[daily_brief] {SECTION_LABEL[s]}: {len(by_sec[s])}건 → {len(top)}건")

    # 투자 보강 — 선별된 것 전체 1회 호출
    sel_arts = [a for _, a in selected]
    inv = enrich_investment(sel_arts)

    # 섹션별 카드 렌더
    cards_by_sec: dict[str, list[dict]] = {s: [] for s in SECTION_ORDER}
    for i, (s, a) in enumerate(selected):
        cards_by_sec[s].append(item_to_card(a, inv.get(i)))

    parts = [f"<b>📰 원자력 일일 브리핑 ({date.today().isoformat()})</b>", ""]
    total = 0
    for s in SECTION_ORDER:
        if cards_by_sec[s]:
            parts.append(format_cards_message(
                cards_by_sec[s], header=f"━━ {SECTION_LABEL[s]} ━━", show_header=False))
            total += len(cards_by_sec[s])
    msg = "\n".join(parts)

    # 소셜 섹션 (Evidence 본문 기반 풀합성 카드)
    # self_check=False — 무료 티어 호출 수 절감. 합성 프롬프트의 근거-강제로 1차 방어.
    if social_pairs:
        social_cards = build_cards(social_pairs[:MAX_ITEMS], self_check=False) or []
        if social_cards:
            msg += "\n" + format_cards_message(
                social_cards, header="━━ 🔥 소셜 화제 (Reddit·X) ━━", show_header=False)
            total += len(social_cards)
            print(f"[daily_brief] 소셜 카드 {len(social_cards)}개 추가")

    return msg, total


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
    message, n = build_brief(queue, social_pairs=social_pairs)

    if args.dry_run:
        print("\n" + "=" * 60)
        print(message)
        print("=" * 60)
        print(f"\n[dry-run] 카드 {n}개, {len(message)}자 (발송 생략)")
        return 0

    results = send_long_text(message, parse_mode="HTML")
    ok = sum(1 for r in results if r.get("ok"))
    print(f"[daily_brief] 발송 {ok}/{len(results)} ({n}개 카드)")
    if not args.from_curated and not args.keep_queue:
        save_queue([])
        print("[daily_brief] 큐 비움")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
