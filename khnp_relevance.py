"""카드 항목별 **조건부 필수성** — "이 기사라면 한수원 시사점이 있어야 하는가".

무엇이 문제였나
---------------
2026-08-16 발송분.

    2. 정부, AI 시대 대비 ESS 및 무탄소 전력 인프라 확대 전략 추진  ✅ 연합뉴스
       • 무슨 일: 정부가 AI 시대 국가 경쟁력 확보를 위해 2030년까지 재생에너지
         비중을 20% 이상으로 확대하고, 전력 안정성을 위한 ESS 인프라를 선제 구축한다.
       🔗 출처

`투자 관점`·`왜 중요`가 없는 것은 기사 성격상 정상이다(전자는 confidence 0 이면
생략, 후자는 must_read 전용). 그러나 **한수원 시사점**이 없는 것은 다르다. 그
기사의 detail 에는 "재생에너지와 원전을 기반으로 무탄소 전력 공급을 확대",
"재생에너지 설비 100GW" 가 들어 있다 — 재료가 없어서 빈 것이 아니다.

무슨 일이 있었나 (코드 기준)
----------------------------
카드의 `🇰🇷 한수원 시사점` = `art["implication"]` 이다
(`daily_brief.item_to_card` → `synthesize.format_cards_message`). 그런데 그 값을
만드는 곳은 수집 단계의 배치 큐레이션 프롬프트이고, 거기서 이 필드의 이름은
그냥 **"implication: AI 해석 1문장"** 이다 — 프롬프트 어디에도 한수원이라는
말이 없다. 라벨과 생성기가 서로 다른 것을 말하고 있었다.

게다가 이 필드는 뒤에서 세 번 비워질 수 있다.
  · `drop_interpretation_without_body` — 본문을 못 받았으면 통째로 비운다
  · `drop_hollow_implication` — 정보량 0 문장이면 비운다
  · `strip_unsourced_person_names` — 원문과 어긋난 실명을 걷어낸다
비우는 판단 자체는 옳다(빈칸이 빈껍데기보다 낫다). 문제는 **비운 다음에
"이 기사라면 있어야 하지 않나"를 되묻는 자리가 없다**는 것이다. `issue_insight.py`
가 비슷한 일을 하지만 그건 웹 전용이고 발송 **뒤**에 돈다.

그래서 무엇을 하나
------------------
모든 기사를 채우지 않는다. 채우면 지금 걷어내고 있는 그 빈껍데기가 돌아온다.
대신 **필요성을 등급으로 판정**하고, 필요성이 높은데 비어 있을 때만 되묻는다.

    required   — 한수원의 사업·정책 환경에 직접 걸린다. 비어 있으면 재생성한다.
    expected   — 간접적으로 걸린다. must_read 이거나 근거(detail)가 넉넉하면 재생성.
    optional   — 걸릴 수도 있다. 비어 있어도 그대로 둔다.
    not_required — 관련 없다. 억지로 만들지 않는다.

판정은 **키워드 하나로 하지 않는다.** 'ESS' 가 제목에 있다고 전부 required 면
배터리 회사 실적 기사까지 걸린다. 영역별 가중치를 합산하고, 서로 다른 영역이
겹칠 때 등급이 올라가게 둔다 — 한수원의 사업 환경은 '원전' 한 축이 아니라
전력시장·계통·무탄소 전원·수급이 함께 움직이는 자리이기 때문이다.

가드레일
--------
* stdlib 만. LLM 없음 — 이 파일은 "물어볼 가치가 있는가"만 정하고, 실제 문장
  생성은 호출부(daily_brief)가 한다.
* 판정은 결정적이고 설명 가능해야 한다. `reasons` 에 어느 영역이 걸렸는지 남긴다.
"""

from __future__ import annotations

import re

# ---- 영역별 어휘 ---------------------------------------------------------------
#
# 사용자가 지목한 축을 그대로 옮긴다. 가중치는 "한수원의 사업·정책 환경에
# 얼마나 직접 걸리는가"다.
#
#   core   (3) — 한수원 자신 또는 원전 그 자체
#   market (2) — 전력시장·계통·수급·발전믹스처럼 원전의 자리를 정하는 구조
#   policy (2) — 무탄소 전원·에너지 정책·탄소중립
#   export (3) — 원전 수출·공급망 (한수원의 성장축)
#   demand (2) — AI 데이터센터 등 전력수요 변화
#   peer   (2) — 주요 발전사·전력 공기업
#   adjacent (1) — ESS·재생에너지·수소처럼 무탄소 전원 구성에서 원전과 맞물리는 것

RELEVANCE_DOMAINS: dict[str, tuple[int, tuple[str, ...]]] = {
    "core": (3, (
        "한수원", "한국수력원자력", "khnp", "원전", "원자력", "원자로", "smr",
        "소형모듈원자로", "i-smr", "핵연료", "사용후핵연료", "방폐장", "방사성폐기물",
        "계속운전", "수명연장", "해체", "가압경수로", "중수로", "혁신형",
        "nuclear", "reactor", "small modular",
    )),
    "market": (2, (
        "전력시장", "전력거래소", "smp", "계통", "전력망", "송전", "배전", "변전",
        "전력수급", "수급계획", "전력수요", "예비율", "발전믹스", "전원믹스",
        "기저부하", "용량요금", "전기요금", "요금인상", "요금인하", "계통연계",
        "grid", "power market", "transmission", "capacity market",
    )),
    "policy": (2, (
        "무탄소", "무탄소전원", "cfe", "탄소중립", "넷제로", "에너지정책",
        "에너지기본계획", "전력수급기본계획", "기후에너지환경부", "산업통상",
        "온실가스", "배출권", "rps", "재생에너지비중", "에너지믹스", "전기국가",
        "clean firm", "carbon free", "net zero", "energy policy",
    )),
    "export": (3, (
        "원전수출", "수출", "수주", "공급망", "기자재", "주기기", "체코", "폴란드",
        "두코바니", "네덜란드", "사우디", "uae", "바라카", "루마니아", "필리핀",
        "밸류체인", "국산화", "협력업체", "supply chain", "export",
    )),
    "demand": (2, (
        "데이터센터", "ai전력", "ai데이터센터", "전력수요증가", "반도체클러스터",
        "하이퍼스케일", "전력다소비", "data center", "datacentre", "ai power",
    )),
    "peer": (2, (
        "한전", "한국전력", "kepco", "남동발전", "중부발전", "서부발전", "남부발전",
        "동서발전", "발전공기업", "가스공사", "전력공기업", "지역난방공사",
        "한전기술", "한전kps", "한전원자력연료", "두산에너빌리티", "현대건설",
        "대우건설", "삼성물산",
    )),
    "adjacent": (1, (
        "ess", "에너지저장", "배터리", "재생에너지", "태양광", "풍력", "해상풍력",
        "수소", "암모니아", "양수발전", "가스발전", "lng", "석탄발전", "탈원전",
        "energy storage", "renewable", "hydrogen",
    )),
}

# 관련 없는 쪽으로 강하게 끄는 신호. 같은 단어가 위에 있어도 여기 걸리면 깎는다.
# (배터리 회사 실적·전기차 기사가 'ESS' 하나로 required 가 되는 것을 막는다.)
NEGATIVE_MARKERS: tuple[str, ...] = (
    "전기차", "ev 배터리", "배터리셀", "주가", "목표주가", "증권", "코스피",
    "실적발표", "영업이익", "분기실적", "인사", "부고", "채용공고", "수상",
    "스포츠", "연예", "날씨",
)

# 통제 topics 태그(news_bot VALID_TOPICS)와 영역의 대응. 텍스트 매칭보다
# 신뢰도가 높아 별도로 가산한다.
TOPIC_DOMAINS: dict[str, str] = {
    "smr": "core", "nuclear": "core", "reactor": "core", "fuel_cycle": "core",
    "waste": "core", "decommissioning": "core", "safety": "core",
    "regulation": "core", "life_extension": "core",
    "export": "export", "supply_chain": "export", "construction": "export",
    "policy": "policy", "climate": "policy",
    "grid": "market", "market": "market", "grid_demand": "demand",
    "renewables": "adjacent", "storage": "adjacent", "hydrogen": "adjacent",
}

# 등급 문턱. 서로 다른 영역이 둘 이상 걸려야 required 로 올라가게 잡았다 —
# 한 축만 스치는 기사(예: '태양광 보급률')는 expected 에서 멈춘다.
REQUIRED_SCORE = 5
EXPECTED_SCORE = 3
OPTIONAL_SCORE = 2

_WORD_RE = re.compile(r"\s+")

# 본문에 스치듯 나온 낱말은 제목·요약에 있는 낱말과 무게가 다르다. 실측
# 2026-08-16 큐: `앤트로픽, AI 스타트업 데카르트 8.5조원 인수 협상` 이
# required 로 잡혔다 — 본문 뒤쪽에 '전력수요'·'공급망'·'AI 데이터센터'가
# 지나가듯 나왔기 때문이다. 그 기사는 전력 기사가 아니라 M&A 기사다.
BODY_WEIGHT = 0.5


def _subject_text(article: dict) -> str:
    """이 기사가 **무엇에 관한 것인가** — 제목·요약·태그·통제 분류."""
    parts = [
        article.get("title_kr"), article.get("title"), article.get("summary"),
        " ".join(str(t).lstrip("#") for t in (article.get("tags") or [])),
        article.get("section"), article.get("category"),
    ]
    return _WORD_RE.sub("", " ".join(str(p or "") for p in parts)).lower()


def _body_text(article: dict) -> str:
    """근거는 되지만 주제는 아닌 부분."""
    parts = [article.get("detail"), article.get("why_important")]
    return _WORD_RE.sub("", " ".join(str(p or "") for p in parts)).lower()


def domain_hits(article: dict) -> tuple[dict[str, int], dict[str, int]]:
    """(제목·요약에서 걸린 영역, 본문에서만 걸린 영역). 각각 적중 수."""
    subject, body = _subject_text(article), _body_text(article)
    subject_hits: dict[str, int] = {}
    body_hits: dict[str, int] = {}
    for name, (_weight, terms) in RELEVANCE_DOMAINS.items():
        compact = [_WORD_RE.sub("", term) for term in terms]
        found = sum(1 for term in compact if term in subject)
        if found:
            subject_hits[name] = found
            continue
        found = sum(1 for term in compact if term in body)
        if found:
            body_hits[name] = found
    # 통제 topics 는 큐레이션이 붙인 분류라 주제 신호로 센다.
    for topic in (article.get("topics") or []):
        name = TOPIC_DOMAINS.get(str(topic).strip().lower())
        if name:
            subject_hits[name] = subject_hits.get(name, 0) + 1
            body_hits.pop(name, None)
    return subject_hits, body_hits


def relevance(article: dict) -> dict:
    """한수원 관련성 판정. {"level", "score", "subject_score", "domains", "reasons"}.

    점수는 영역 가중치의 합이되 **한 영역이 여러 번 걸려도 한 번만** 센다.
    같은 단어가 반복되는 것은 새 근거가 아니다. 대신 서로 다른 영역이 겹치면
    그만큼 올라간다 — 전력시장 얘기이면서 원전 얘기인 기사가 진짜 한수원 기사다.

    본문에서만 걸린 영역은 절반만 센다. 그리고 주제(제목·요약)에 아무 축도
    없으면 등급을 expected 위로 올리지 않는다 — 본문에 그 낱말이 지나갔다는
    것은 '관련 있다'가 아니라 '언급했다'이다.
    """
    subject_hits, body_hits = domain_hits(article)
    hits = {**body_hits, **subject_hits}
    subject_score = sum(RELEVANCE_DOMAINS[n][0] for n in subject_hits
                        if n in RELEVANCE_DOMAINS)
    score = subject_score + BODY_WEIGHT * sum(
        RELEVANCE_DOMAINS[n][0] for n in body_hits if n in RELEVANCE_DOMAINS)

    text = _subject_text(article) + _body_text(article)
    negatives = [m for m in NEGATIVE_MARKERS if _WORD_RE.sub("", m) in text]
    # 부정 신호는 core/export 가 없을 때만 깎는다. '원전 수출 기업 실적' 은
    # 실적 기사라도 한수원 공급망 기사다.
    if negatives and not (hits.keys() & {"core", "export"}):
        score -= 2 * len(negatives)
    score = max(0.0, score)

    # 원전·SMR 그 자체를 **다루는** 기사는 점수와 무관하게 required 다. 사용자가
    # 지목한 목록의 첫 줄이고, 여기서 '한수원과 무슨 상관인가'를 되물을 일이
    # 없다. 합산 점수만 쓰면 `원안위, 신한울 3호기 운영허가 심사 착수` 처럼
    # 축이 하나뿐인 정통 원전 기사가 expected 에 머문다(core 3점).
    # 수출·공급망은 축 하나만으로는 부족하다 — '수출'은 원자력 밖에서도 흔하다.
    if "core" in subject_hits or ("export" in subject_hits and len(hits) >= 2):
        level = "required"
    elif score >= REQUIRED_SCORE and subject_score > 0:
        level = "required"
    elif score >= EXPECTED_SCORE:
        level = "expected"
    elif score >= OPTIONAL_SCORE:
        level = "optional"
    else:
        level = "not_required"
    reasons = [f"{name}×{count}" for name, count in sorted(subject_hits.items())]
    reasons += [f"body:{name}×{count}" for name, count in sorted(body_hits.items())]
    if negatives:
        reasons.append(f"negative:{','.join(negatives[:3])}")
    return {"level": level, "score": round(score, 1),
            "subject_score": subject_score, "domains": sorted(hits),
            "reasons": reasons}


# ---- 항목별 필수성 --------------------------------------------------------------

def has_body(article: dict) -> bool:
    """해석을 쓸 근거가 실제로 있는가.

    `news_bot.drop_interpretation_without_body` 는 본문을 못 받은 기사의 해석을
    통째로 비운다 — 제목 한 줄로 '왜 중요한가'를 쓰면 지어내기 때문이다(실측
    사고 있음). 그 계약을 여기서 뒤집으면 안 된다. 재생성 대상은 **본문이
    실제로 붙은 기사**로 한정한다.
    """
    return len(str(article.get("detail") or "").strip()) >= 40


def implication_requirement(article: dict) -> dict:
    """`🇰🇷 한수원 시사점` 이 이 기사에 있어야 하는가.

    Returns:
        {"level", "regenerate", "score", "domains", "reasons", "current"}
        `regenerate=True` 면 호출부가 한 번 더 생성을 시도한다. 그래도 근거 있는
        문장이 안 나오면 빈칸이 정답이다 — 이 판정은 '물어볼 가치'까지다.
    """
    verdict = relevance(article)
    current = str(article.get("implication") or "").strip()
    importance = str(article.get("importance") or article.get("category") or "")

    regenerate = False
    if current:
        pass                                   # 이미 있으면 건드리지 않는다
    elif not has_body(article):
        verdict["reasons"].append("no_body")   # 근거 없이 만들지 않는다
    elif verdict["level"] == "required":
        regenerate = True
    elif verdict["level"] == "expected":
        # 간접 관련은 **주제 자체가 이 영역일 때**나 must_read 일 때만 되묻는다.
        # 본문에 낱말이 지나갔다는 이유로 전부 되물으면 호출이 늘고, 늘어난
        # 만큼 억지 문장이 늘어난다 — 이 파일이 막으려는 바로 그것이다.
        regenerate = (importance == "must_read"
                      or verdict["subject_score"] >= EXPECTED_SCORE)
    return {**verdict, "regenerate": regenerate, "current": current}
