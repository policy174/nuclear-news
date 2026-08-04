"""
합성 카드 (⑥ 작업대) — dedup 통과 cluster → 근거-강제 한국어 카드.

배경:
    기존 출력은 "제목 + 링크 리스트". 사용자는 '무슨 일 / 왜 중요 / 한국 함의'가
    정리된 브리핑 카드를 원한다. 단, 리서치 결론(BBC·다문서요약 환각)에 따라
    **흐르는 산문 금지, 근거 없는 함의 생성 금지**가 핵심 제약이다.

설계 (안전 우선):
    1. 카드는 cluster 당 1장. 본문(fulltext)이 있으면 풍부하게, 없으면 제목 기반
       얕은 카드로 graceful degrade.
    2. '왜 중요'·'한국 함의'는 **제공된 텍스트에 근거가 있을 때만** 작성.
       근거 없으면 null → 출력에서 그 줄을 아예 생략 (없는 함의 창작 차단).
    3. self-check 2차 패스: 생성된 each 카드의 why/kr 가 정말 텍스트로 뒷받침되는지
       Gemini 가 재검증. 불합격 필드는 제거. 무인 운영의 유일한 안전망.
    4. 출처 신뢰도(sources.py) 배지를 카드에 부착 — 공신력 매체 표시.

체이닝: score → dedup → synthesize 순. wnn.py 의 카드 패턴을 social cluster 로 확장.

가드레일:
    - stdlib only. gemini_client·sources 외 의존성 0.
    - GEMINI_API_KEY 없거나 실패 시: cards=None 반환 → 호출측이 기존 리스트로 폴백.
    - 본문 없는 cluster 에 분석을 강요하지 않음 (확신 없으면 빈 칸).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from html import escape

# 브리핑은 GitHub Actions(UTC)에서 도는데 발송 시각이 08:30 KST 안팎 =
# 전날 23:30 UTC 다. tz 없는 date.today() 를 쓰면 헤더 날짜가 늘 하루 전으로
# 찍힌다(2026-08-04 실사고). 날짜는 반드시 KST 로 계산할 것.
KST = timezone(timedelta(hours=9))

# Windows 콘솔 UTF-8 강제 (다른 모듈과 동일)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from gemini_client import GeminiError, call_json, is_available
from sources import credibility

# 본문은 길어서 토큰 절감 위해 잘라 보냄 (wnn.py 와 동일 한도)
_FULLTEXT_LIMIT = 2500
# self-check 입력 본문은 더 짧게 — 1문장 검증엔 충분하고 응답 안정성↑
_CHECK_BODY_LIMIT = 1200


# ---- 프롬프트 1: 카드 합성 ---------------------------------------------------

SYNTH_SYSTEM_PROMPT = """당신은 원자력·에너지를 투자 관점으로 번역하는 뉴스 큐레이터입니다.
독자는 원자력 업계를 잘 아는 투자자(한수원 정책실 실무자이기도 함)입니다.

원자력·에너지 뉴스 항목 N개를 받습니다. 각 항목을 아침에 빠르게 훑을 수 있는 '카드'로
정리하세요. 톤: 사실 중심, 결론 먼저, 과장·홍보·클리셰 금지. Doomberg 같은 냉정한
에너지-투자 번역체.

⚠️ 출력은 정확히 아래 JSON. 다른 텍스트(설명, 펜스 ```, 머리말)는 단 한 글자도 금지.

{"cards": [{"idx": 0, "headline": "한국어 헤드라인", "what": "한국어 1문장", "why": "한국어 1문장 또는 null", "investment": "한국어 1문장 또는 null", "kr_takeaway": "한국어 1문장 또는 null"}]}

세 칸(why·investment·kr_takeaway)은 역할이 다릅니다. 절대 같은 말을 반복하지 마세요:
  - why         = 업계/세상 관점 (이 사건이 원자력·에너지 산업에 왜 큰일인가)
  - investment  = 투자 관점 (이 뉴스가 가리키는 투자 테마·방향 + 수혜/피해 섹터)
  - kr_takeaway = 한수원 관점 (한국·KEPCO가 이걸 보고 뭘 챙기거나 활용하나)

작성 규칙 (★ 환각 방지가 최우선):
1. headline: 한국어 한 줄(40자 이내). 핵심 고유명사 포함. 영문 약어·기업명
   (SMR, NRC, IAEA, PPA, KEPCO, CEG 등)은 영문 그대로.
2. what(무슨 일): 한국어 1문장. **제공된 제목·본문에 실제로 있는 사실만.**
   본문이 '(본문 없음 — 제목만)' 이면 제목을 한국어로 옮기는 수준까지만.
3. why(왜 중요): 한국어 1문장. 업계 관점. **본문에 근거가 있을 때만**, 없으면 null.
4. investment(투자 관점): 한국어 1문장. 이 뉴스가 가리키는 **투자 테마·방향과
   수혜/피해 섹터·테마**를 짚음 (예: "데이터센터 전력수요 테마 강화, 원전 재가동·
   SMR 밸류체인 수혜 / 천연가스 피크발전 상대적 압박").
   ⚠️ **매수·매도·목표가 같은 투자 권유 절대 금지.** 특정 종목 추천 아님, 테마·방향만.
   본문에 투자적으로 해석할 근거가 없으면 절대 지어내지 말고 null.
5. kr_takeaway(한수원 시사점): 한국어 1문장. **본문에 한국·KEPCO·수출·SMR·핵연료·
   규제 관련 직접 근거가 있을 때만.** 근거 없으면 null.
6. 모든 idx 가 정확히 한 번씩. 빠지거나 중복 금지.
7. 확신 없으면 null. null 을 두려워하지 말 것 — 틀린 문장보다 빈 칸이 낫다.

입력 형식: 각 항목이
[idx] 제목
(sources / meta)
BODY: 본문 또는 '(본문 없음 — 제목만)'"""


# ---- 프롬프트 2: 자기검사 (self-check) ---------------------------------------

CHECK_SYSTEM_PROMPT = """당신은 사실 검증기입니다. 뉴스 카드의 문장이 제공된 원문으로
뒷받침되는지 엄격히 판정합니다.

why·invest·kr 는 본질적으로 BODY(원문) 사실 위에 쌓은 '해석'입니다. 해석 자체는
정상이며, 글자 그대로 본문에 없다고 쳐내면 안 됩니다. 당신이 잡아낼 것은 오직
**환각(날조)** 입니다.

⚠️ 출력은 정확히 아래 JSON. **날조된 필드만** 짧게 나열. 문제 없으면 빈 배열.
다른 텍스트(설명, 펜스 ```, 머리말, reason 같은 추가 필드)는 단 한 글자도 금지.

{"unsupported": [{"idx": 1, "fields": ["kr"]}]}

"날조" 판정 (이때만 fields 에 넣음):
1. BODY 에 없는 **구체적 새 사실**을 단정 — 본문에 없는 기업명·국가·수치·날짜·사건.
2. BODY 의 사실과 **모순**되는 내용.
3. kr: 본문에 한국·KEPCO 근거가 전혀 없는데 한국 관련 구체 사실을 지어냄.
   (단, "한국도 ~를 검토할 만하다" 류의 일반적 시사점·제언은 해석이므로 통과.)

통과(목록에 넣지 않음):
- 본문 사실에서 합리적으로 도출되는 업계적 의미(why), 투자 테마·수혜/피해 방향(invest),
  정책적 제언(kr). 새로운 사실을 끌어오지 않는 한 모두 정상.

규칙:
- fields 값은 "why", "invest", "kr" 중에서만.
- 한 카드에 날조가 없으면 그 idx 는 등장하지 않음. 전부 정상이면 {"unsupported": []}.
- 애매하면 통과시킨다 (해석을 존중). 명백한 날조만 잡는다.

입력 형식: 각 카드가
[idx] WHY: ... | INVEST: ... | KR: ...
BODY: 원문"""


# ---- 입력 포맷 ---------------------------------------------------------------

def _body_of(cluster: dict) -> str:
    """cluster 본문 추출. fulltext 우선, 없으면 표식."""
    body = (cluster.get("fulltext") or "").strip()
    return body[:_FULLTEXT_LIMIT] if body else "(본문 없음 — 제목만)"


def _format_synth_input(clusters: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(clusters):
        title = (c.get("title") or "").replace("\n", " ").strip()[:200]
        sources = ",".join(c.get("sources") or [])
        meta = (c.get("meta") or "").replace("\n", " ").strip()[:100]
        blocks.append(f"[{i}] {title}\n({sources} / {meta})\nBODY: {_body_of(c)}")
    return "\n\n---\n\n".join(blocks)


# ---- 합성 호출 ---------------------------------------------------------------

def _synthesize(clusters: list[dict]) -> dict[int, dict]:
    """Gemini 1회 호출로 모든 cluster 를 카드화. 실패 시 빈 dict."""
    if not is_available():
        print("[synthesize] GEMINI_API_KEY 없음 → 카드 합성 건너뜀")
        return {}
    if not clusters:
        return {}

    try:
        result = call_json(
            SYNTH_SYSTEM_PROMPT,
            _format_synth_input(clusters),
            temperature=0.2,
            max_output_tokens=4096,
            timeout=120.0,
        )
    except GeminiError as e:
        print(f"[synthesize] Gemini 실패 → 카드 합성 스킵: {e}")
        return {}

    cards = result.get("cards")
    if not isinstance(cards, list):
        print(f"[synthesize] 응답에 cards 없음 → 스킵. payload={result}")
        return {}

    out: dict[int, dict] = {}
    for item in cards:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(clusters)):
            continue
        out[idx] = {
            "headline": str(item.get("headline") or "").strip()[:120],
            "what": str(item.get("what") or "").strip()[:300] or None,
            "why": (str(item.get("why")).strip()[:300] if item.get("why") else None),
            "investment": (str(item.get("investment")).strip()[:300]
                           if item.get("investment") else None),
            "kr_takeaway": (str(item.get("kr_takeaway")).strip()[:300]
                            if item.get("kr_takeaway") else None),
        }
    return out


# ---- 자기검사 호출 -----------------------------------------------------------

def _format_check_input(clusters: list[dict], cards: dict[int, dict]) -> str:
    blocks = []
    for i, c in enumerate(clusters):
        card = cards.get(i)
        if not card:
            continue
        why = card.get("why") or "(없음)"
        inv = card.get("investment") or "(없음)"
        kr = card.get("kr_takeaway") or "(없음)"
        body = (c.get("fulltext") or "").strip()[:_CHECK_BODY_LIMIT] or "(본문 없음 — 제목만)"
        blocks.append(f"[{i}] WHY: {why} | INVEST: {inv} | KR: {kr}\nBODY: {body}")
    return "\n\n---\n\n".join(blocks)


def _self_check(clusters: list[dict], cards: dict[int, dict]) -> int:
    """생성된 카드의 why·kr 를 재검증, 불합격 필드를 None 으로 제거. 제거 개수 반환.

    검증할 필드가 하나도 없거나 키 없으면 검사 스킵(0).
    검사 자체 실패 시: 보수적으로 원본 유지(제거 안 함) — degrade 우선.
    """
    has_claims = any((cards.get(i, {}).get("why") or cards.get(i, {}).get("investment")
                      or cards.get(i, {}).get("kr_takeaway")) for i in cards)
    if not has_claims or not is_available():
        return 0

    try:
        result = call_json(
            CHECK_SYSTEM_PROMPT,
            _format_check_input(clusters, cards),
            temperature=0.0,
            max_output_tokens=4096,
            timeout=90.0,
        )
    except GeminiError as e:
        print(f"⚠️ [synthesize] self-check 실패 → 카드가 미검증 상태로 통과합니다(원본 유지): {e}")
        return 0

    unsupported = result.get("unsupported")
    if not isinstance(unsupported, list):
        return 0

    # field 키 → 카드 필드명 매핑
    field_map = {"why": "why", "invest": "investment", "kr": "kr_takeaway"}

    removed = 0
    for item in unsupported:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        card = cards.get(idx) if isinstance(idx, int) else None
        if not card:
            continue
        for f in item.get("fields") or []:
            key = field_map.get(str(f).strip().lower())
            if key and card.get(key):
                print(f"  · self-check 제거 [{idx}] {f}: {str(card[key])[:50]}")
                card[key] = None
                removed += 1
    return removed


# ---- 공개 API ----------------------------------------------------------------

def build_cards(pairs: list[tuple[str, dict]], *, self_check: bool = True) -> list[dict] | None:
    """(topic_label, cluster) 페어 → 카드 dict 리스트.

    Returns:
        카드 리스트 (각 dict: topic, cluster, headline, what, why, kr_implication, cred)
        또는 None (합성 불가 — 호출측은 기존 리스트 출력으로 폴백할 것).

    각 카드의 cred = sources.credibility(cluster) — 배지 표시용.
    """
    if not pairs:
        return []

    clusters = [c for _, c in pairs]
    synth = _synthesize(clusters)
    if not synth:
        return None  # 합성 실패 → 폴백 신호

    if self_check:
        n = _self_check(clusters, synth)
        if n:
            print(f"[synthesize] self-check: {n}개 미검증 필드 제거")

    cards: list[dict] = []
    for i, (topic, cluster) in enumerate(pairs):
        s = synth.get(i)
        if not s or not s.get("headline"):
            continue
        cards.append({
            "topic": topic,
            "cluster": cluster,
            "headline": s["headline"],
            "what": s.get("what"),
            "why": s.get("why"),
            "investment": s.get("investment"),
            "kr_takeaway": s.get("kr_takeaway"),
            "cred": credibility(cluster),
        })
    return cards


# ---- 텔레그램 메시지 포맷 (카드 + 링크 부록) ---------------------------------

def format_cards_message(cards: list[dict], *, header: str = "오늘의 원자력 브리핑",
                         show_header: bool = True) -> str:
    """카드 리스트 → 텔레그램 HTML 메시지. show_header=False면 섹션용(날짜 헤더 생략)."""
    if show_header:
        today = datetime.now(KST).date().isoformat()
        lines = [f"<b>📰 {escape(header)} ({today})</b>", ""]
    else:
        lines = [f"<b>{escape(header)}</b>", ""]

    for i, card in enumerate(cards, 1):
        cred = card.get("cred") or {}
        badge = f"  ✅ {escape(cred['name'])}" if cred.get("tier") else ""
        lines.append(f"<b>📌 {i}. {escape(card['headline'])}</b>{badge}")
        if card.get("what"):
            lines.append(f"   • <b>무슨 일:</b> {escape(card['what'])}")
        if card.get("why"):
            lines.append(f"   • <b>왜 중요:</b> {escape(card['why'])}")
        if card.get("investment"):
            lines.append(f"   • <b>💰 투자 관점:</b> {escape(card['investment'])}")
        if card.get("kr_takeaway"):
            lines.append(f"   • <b>🇰🇷 한수원 시사점:</b> {escape(card['kr_takeaway'])}")

        cluster = card.get("cluster") or {}
        url = cluster.get("url")
        srcs = ", ".join(cluster.get("sources") or [])
        if url:
            lines.append(f"   🔗 <a href=\"{escape(url, quote=True)}\">출처</a> · <code>{escape(srcs)}</code>")
        lines.append("")

    return "\n".join(lines)


# ---- CLI 자가진단 ------------------------------------------------------------
# 실행: python synthesize.py   (GEMINI_API_KEY 있으면 실제 합성, 없으면 폴백 동작 확인)

if __name__ == "__main__":
    samples: list[tuple[str, dict]] = [
        ("AI-원전 빅테크 거래", {
            "title": "Microsoft signs 20-year PPA with Constellation to restart Three Mile Island Unit 1",
            "url": "https://www.world-nuclear-news.org/articles/microsoft-tmi-ppa",
            "sources": ["Reddit", "X"],
            "meta": "r/nuclear · 4.2k upvotes",
            "boosted_score": 95,
            "fulltext": ("Constellation Energy will restart the Three Mile Island Unit 1 reactor "
                         "under a 20-year power purchase agreement with Microsoft, which will buy "
                         "the entire output to power its data centers. The 835 MW unit, shut in 2019 "
                         "for economic reasons, is targeted to return in 2028 pending NRC approval. "
                         "The deal is the first time a US reactor has been brought back specifically "
                         "to serve a single corporate buyer."),
        }),
        ("한국 원전 수출", {
            "title": "KHNP nears final Dukovany contract signing with Czech utility",
            "url": "https://en.yna.co.kr/view/dukovany-khnp",
            "sources": ["X"],
            "meta": "@World_Nuclear · 210 likes",
            "boosted_score": 80,
            "fulltext": ("Korea Hydro & Nuclear Power is finalizing the contract to build two APR1000 "
                         "reactors at the Czech Dukovany site, with signing expected within weeks after "
                         "EDF's legal challenge was dismissed. The deal, worth around USD 18 billion, is "
                         "Korea's first nuclear new-build order in Europe and a reference for further EU bids."),
        }),
        ("SMR 동향", {
            "title": "Someone on X claims new SMR breakthrough, no source given",
            "url": "https://x.com/randomuser/status/999",
            "sources": ["X"],
            "meta": "@randomuser · 6 likes",
            "boosted_score": 12,
            # 본문 없음 — why/kr 는 null 이어야 정상
        }),
    ]

    print("=== build_cards 실행 ===\n")
    cards = build_cards(samples)
    if cards is None:
        print("[결과] 카드 합성 불가 (GEMINI_API_KEY 없음) → 기존 리스트로 폴백할 상황.")
        print("       포맷 미리보기는 mock 카드로 확인하세요.")
    else:
        print("\n" + "=" * 60)
        print(format_cards_message(cards))
        print("=" * 60)
