# -*- coding: utf-8 -*-
"""카드 카피의 **편집 품질**을 코드로 잰다.

기존 `validate` 가 재는 것과 다르다. 저쪽은 "규격에 맞는가" 를 본다 — 글자 수,
불릿 개수, 강조 표기, JSON 모양. 여기서 재는 것은 "**같은 말을 두 번 하지
않는가**" 와 "**입력에 없는 것을 적지 않았는가**" 다.

무엇을 잡고 무엇을 못 잡는가
----------------------------
정직하게 적어 둔다. 이 모듈이 잡는 것은 **어휘가 겹치는 중복**이다.

    제목  SAR 시범사업 착수
    사실  진안·금산에서 시범사업 착수
    의미  시범사업이 실제 착수됨        ← 잡는다

잡지 못하는 것은 **말을 바꾼 중복**이다.

    사실  진안·금산에서 시범사업 시행
    사실  두 지역에서 실제 사업 착수    ← 못 잡는다

두 문장은 같은 말이지만 겹치는 낱말이 없다. 이것을 잡으려면 의미 임베딩이
필요하고, 카드 한 장을 위해 그 계층을 세우는 것은 값이 맞지 않는다. 그쪽은
편집 판단(Narrator 의 `avoid_repeating`)이 맡고, 여기서는 **기계적으로 확실한
것만** 막는다. 못 잡는 것을 잡는 척하지 않는 것이 이 주석의 목적이다.

경고인가 실패인가
-----------------
판정을 두 층으로 나눈다.

    FAIL   입력에 없는 숫자·날짜 · 거의 같은 문장이 두 번 ·
           사실을 되풀이한 의미 · 알맹이 없는 추상 한 줄
    WARN   제목이 사실 첫 줄과 지나치게 닮음

무엇을 FAIL 로 둘지는 **실제로 발간된 카피에 대고 재서** 정했다. 임계값을
픽스처로 정하면 만든 사람이 상상한 실패만 잡는 숫자가 나오고, 오탐이 잦으면
repair 가 일상이 되어 호출량이 배로 는다. 위 네 가지는 지난 30일치 실제
카피에서 오탐이 0건이라 FAIL 이고, 한 가지는 절반이 걸려서 아예 뺐다 —
근거 수치는 아래 상수 주석과 tests/test_card_qa.py 에 박제한다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── 임계값 ────────────────────────────────────────────────────────────────
#
# **픽스처가 아니라 실제로 발간된 카피에 대고 쟀다** (라이브 2026-09-20,
# 지난 30 브리핑일의 상위 3건). 픽스처로 임계값을 정하면 만든 사람이 생각한
# 실패만 잡는 숫자가 나온다 — 알아야 하는 것은 **정상 카피가 어느 정도
# 닮는가** 이고, 임계값은 그 분포의 꼬리 밖에 있어야 한다.
#
#   사실문장(latest_change+summary) ↔ 의미문장(card_why)   n=85
#       median 0.132 · p95 0.267 · max 0.526 → 0.62 에서 오탐 0건
#   표시제목 ↔ summary                                      n=90
#       median 0.369 · p95 0.599 · max 0.708 → 0.78 에서 오탐 0건
#   같은 이슈의 기사 제목끼리 (중복 판정의 최악 조건)        n=123
#       median 0.244 · p95 0.585 · max 0.745 → 0.72 에서 1건,
#       그 1건은 진짜 중복이었다(두 매체가 같은 제목을 썼다)
#
# 두 척도를 **함께** 쓰고 낮은 쪽을 본다(min). 한쪽만 보면 오탐이 난다 —
# 기관명만 겹치는 두 문장은 토큰 포함율이 높고, 어미만 같은 두 문장은 글자
# 2-gram 이 높다. 둘 다 높아야 실제로 같은 말이다.
DUPLICATE = 0.72      # 같은 목록 안 두 불릿이 사실상 한 문장 (FAIL)
FACT_WHY = 0.62       # 사실 불릿과 의미 불릿이 같은 말 (FAIL)
HEADLINE_FACT = 0.78  # 제목이 사실 첫 줄의 복사 (WARN)

# **"제목이 원제의 기계적 축약인가" 는 검사하지 않는다.** 재 보고 뺐다.
# 같은 표본에서 파이프라인이 이미 만들어 쓰는 표시 제목과 원제의 유사도가
# median 0.726 이었고 90건 중 **44건(49%)이 0.78 을 넘었다**(p95 = 1.000 —
# 글자까지 같은 것이 여럿이다). 즉 이 검사를 켜면 절반이 걸린다. 경고로 둬도
# repair 가 일상이 되어 호출량이 배로 늘고, 경고가 흔해지면 아무도 안 본다.
#
# 원제 축약 문제가 없다는 말이 아니다. 그것은 **무엇을 말할지 고르는 단계**의
# 문제라 Narrator 가 `headline_angle`·`core_change` 를 따로 정하는 쪽으로
# 푼다(카피라이터에게 원제를 주고 "축약하지 마라" 하는 대신, 편집자가 각을
# 먼저 정한다). 유사도로 사후에 때리는 것은 자리가 틀렸다.

# 이보다 짧은 문장은 유사도를 재지 않는다. 짧으면 공통 낱말 하나가 점수를
# 끌어올려 기관명만 같아도 걸린다.
MIN_COMPARE_CHARS = 8


@dataclass
class Finding:
    level: str           # "fail" | "warn"
    code: str
    where: str
    message: str
    score: float | None = None

    def __str__(self) -> str:
        tail = f" ({self.score:.2f})" if self.score is not None else ""
        return f"{self.where}: {self.message}{tail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "fail"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    def fix_these(self) -> list[str]:
        """repair 호출에 그대로 실어 보낼 구체적인 지적."""
        return [str(f) for f in self.findings]

    def __bool__(self) -> bool:
        return bool(self.findings)


# ── 정규화 ────────────────────────────────────────────────────────────────

_ACCENT_RE = re.compile(r"\[\[|\]\]")
_PUNCT_RE = re.compile(r"[^\w\s가-힣]", re.UNICODE)

# 한국어 조사·어미 최소 정규화. **최소**가 요점이다 — 형태소 분석기를 넣지
# 않는다. 여기 목록은 "조사만 바꿔 같은 문장을 두 번 쓰는" 우회를 막을 만큼만
# 이고, 어간이 2자 미만으로 줄어드는 절단은 하지 않는다("에서"를 떼어 "진안"이
# "진"이 되면 안 된다).
_PARTICLES = ("에서는", "에서도", "으로는", "에게는", "에서", "으로", "에게", "부터",
              "까지", "보다", "라는", "이라", "은", "는", "이", "가", "을", "를",
              "의", "에", "와", "과", "도", "만", "로")
_ENDINGS = ("했습니다", "됐습니다", "합니다", "입니다", "습니다", "하였다", "되었다",
            "했다", "됐다", "한다", "된다", "이다", "임", "함", "됨")


def _strip_tail(token: str, tails: tuple[str, ...]) -> str:
    for tail in tails:
        if token.endswith(tail) and len(token) - len(tail) >= 2:
            return token[: -len(tail)]
    return token


def normalize(text: object) -> str:
    """NFKC · 강조 표기 · 문장부호 · 중복 공백을 걷는다."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _ACCENT_RE.sub("", value)
    value = _PUNCT_RE.sub(" ", value)
    return " ".join(value.split())


def tokens(text: object) -> list[str]:
    """정규화 + 조사·어미 절단. 순서를 보존한다(문자 n-gram 이 쓴다)."""
    out = []
    for token in normalize(text).split():
        token = _strip_tail(_strip_tail(token, _ENDINGS), _PARTICLES)
        if token:
            out.append(token)
    return out


def _bigrams(text: str) -> set[str]:
    flat = text.replace(" ", "")
    return {flat[i:i + 2] for i in range(len(flat) - 1)} if len(flat) > 1 else set()


def similarity(left: object, right: object) -> float:
    """0~1. **두 척도의 낮은 쪽**이다.

    토큰 포함율은 "짧은 쪽이 긴 쪽에 통째로 들어 있는가" 를 본다 — 제목이
    사실의 축약인 경우를 잡는 척도다. 글자 2-gram 은 표현이 조금 흔들려도
    같은 말인 경우를 잡는다. 둘 다 높을 때만 같은 말로 본다.
    """
    left_tokens, right_tokens = set(tokens(left)), set(tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    normalized_left, normalized_right = normalize(left), normalize(right)
    if min(len(normalized_left.replace(" ", "")),
           len(normalized_right.replace(" ", ""))) < MIN_COMPARE_CHARS:
        return 0.0
    containment = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    left_grams, right_grams = _bigrams(normalized_left), _bigrams(normalized_right)
    if not left_grams or not right_grams:
        return 0.0
    dice = 2 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams))
    return round(min(containment, dice), 4)


# ── 숫자·날짜 접지 ─────────────────────────────────────────────────────────
#
# **부분 문자열로 재지 않는다.** 예전 검사는 `"17" in haystack` 이었고, 그러면
# "170" 안의 "17" 이 근거로 인정된다. 값과 단위를 한 덩어리로 묶어 비교한다.

_UNIT = r"(?:GW|MW|kW|TW|㎿|㎾|GWh|MWh|원|달러|억|조|만|퍼센트|%|년|개월|일|건|기|호기|명|배|km|㎞|t|톤)"
_NUMBER_RE = re.compile(rf"(\d[\d,]*(?:\.\d+)?)\s*({_UNIT})?")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_KR_DATE_RE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_DOT_DATE_RE = re.compile(r"(?:(\d{4})\s*\.\s*)?(\d{1,2})\s*\.\s*(\d{1,2})\b")


def date_atoms(text: object) -> set[tuple[int, int]]:
    """(월, 일). 연도는 비교에 안 쓴다 — 표기마다 있기도 없기도 하다.

    `2026-09-19` · `9월 19일` · `9.19` · `2026년 9월 19일` 을 같은 날로 본다.
    """
    value = unicodedata.normalize("NFKC", str(text or ""))
    out: set[tuple[int, int]] = set()
    for pattern in (_ISO_DATE_RE, _KR_DATE_RE, _DOT_DATE_RE):
        for match in pattern.finditer(value):
            month, day = int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                out.add((month, day))
    return out


def number_atoms(text: object) -> set[tuple[str, str]]:
    """(값, 단위). 날짜로 읽히는 숫자는 뺀다 — 그쪽은 `date_atoms` 가 본다."""
    value = unicodedata.normalize("NFKC", str(text or ""))
    spans = [match.span() for pattern in (_ISO_DATE_RE, _KR_DATE_RE, _DOT_DATE_RE)
             for match in pattern.finditer(value)]
    out: set[tuple[str, str]] = set()
    for match in _NUMBER_RE.finditer(value):
        if any(start <= match.start() < end for start, end in spans):
            continue
        raw = match.group(1).replace(",", "")
        number = raw.rstrip("0").rstrip(".") if "." in raw else raw
        out.add((number or "0", (match.group(2) or "").replace("％", "%")))
    return out


def ungrounded(text: object, haystack: str, *,
               allow_numbers: frozenset[str] = frozenset()) -> list[str]:
    """이 문장이 입력에 없는 숫자·날짜를 주장하는가."""
    out: list[str] = []
    source_numbers = number_atoms(haystack)
    bare_source = {value for value, _ in source_numbers}
    for value, unit in number_atoms(text):
        if value in allow_numbers:
            continue
        if (value, unit) in source_numbers:
            continue
        # 단위가 달라도 값이 입력에 있으면 통과시킨다. 「1년간」과 「1년」,
        # 「3호기」와 「3·4호기」처럼 같은 값이 다른 꼴로 적히는 경우가 흔하고,
        # 거기까지 막으면 정상 카피가 줄줄이 걸린다.
        if value in bare_source:
            continue
        out.append(f'"{value}{unit}" 은 입력에 없는 숫자')
    source_dates = date_atoms(haystack)
    for month, day in date_atoms(text):
        if (month, day) not in source_dates:
            out.append(f'"{month}월 {day}일" 은 입력에 없는 날짜')
    return out


# ── 가짜 중요성 ────────────────────────────────────────────────────────────
#
# 이 문구들이 **혼자** 서면 낮은 품질이다. 구체적인 결과가 뒤따르면 문장 전체를
# 평가하므로, 문구를 금지어로 두지 않고 "근거가 붙었는가" 를 본다.
#
# **어간으로 적는다.** 처음에는 활용형을 적었다가("주목된다"·"의미가 크")
# 「정책적 의미가 큽니다」를 놓쳤다 — 카드 말투가 ~습니다 라 활용형 목록은
# 늘 한 칸씩 모자란다. 어간이 넓어 보이는 것("주목")은 아래 `_ANCHOR` 가
# 받는다: 구체적인 행동·숫자·날짜가 같이 있으면 추상 문구가 혼자 선 것이 아니다.
_VAGUE = (
    "귀추", "주목", "관심이 필요", "관심 필요", "관심을 모으",
    "의미가 크", "의미가 큽", "의미가 큰", "의미가 작지 않",
    "중요하다", "중요합니다", "중요한 의미", "정책적 의미",
    "영향이 예상", "영향을 미칠", "영향이 클", "파장이 예상",
    "논의가 지속", "논의될 전망", "지켜볼 필요", "향후 전망",
    "기대",
    "필요해 보", "전망이다", "전망입니다",
)
# 구체적인 닻. 이 가운데 하나라도 있으면 추상 문구가 혼자 선 것이 아니다.
_ANCHOR = ("확대", "축소", "전환", "적용", "착수", "승인", "체결", "시행", "개정",
           "인하", "인상", "중단", "재개", "지정", "선정", "제출", "의결")


# 내용을 싣지 않는 낱말. 추상 문구가 **혼자** 섰는지를 재려면 이런 것을 빼고
# 남은 알맹이를 세야 한다.
_FILLER = frozenset({
    "향후", "관련", "이번", "해당", "특히", "다만", "또한", "등", "및", "것",
    "수", "더", "매우", "크게", "직접적인", "전반적인", "지속적인", "전망",
    "예상", "가능성", "부분", "차원", "측면", "상황", "경우",
})

# 알맹이가 이보다 많으면 추상 문구가 혼자 선 것이 아니다.
#
# 이 칸이 생긴 이유: 실제 발간된 `card_why` 85건에 대고 재니 2건(2.4%)이
# 걸렸는데 둘 다 오탐이었다 — 「…수출 통제 및 인증 절차에 직접적인 영향을
# 미칠 것」처럼 **무엇에 영향을 주는지 다 적어 놓고** 끝맺음만 관용구였다.
# 문구를 금지어로 두면 그런 문장까지 잡는다. 잡아야 하는 것은 「정책적 의미가
# 큽니다」처럼 **알맹이가 없는** 한 줄이다. 아래 값으로 그 2건은 통과하고
# 위 추상 목록 여섯 가지는 그대로 걸린다.
VAGUE_MAX_CONTENT = 6


def is_vague(text: object) -> bool:
    """근거 없는 추상 표현 한 줄인가."""
    value = normalize(text)
    if not any(phrase in value for phrase in _VAGUE):
        return False
    if number_atoms(value) or date_atoms(value):
        return False
    if any(word in value for word in _ANCHOR):
        return False
    content = [token for token in tokens(value) if token not in _FILLER]
    return len(content) <= VAGUE_MAX_CONTENT


# ── 카드 한 장 판정 ────────────────────────────────────────────────────────

def _dupes(report: Report, where: str, bullets: list, threshold: float, code: str) -> None:
    for i, left in enumerate(bullets):
        for j, right in enumerate(bullets[i + 1:], start=i + 1):
            score = similarity(left, right)
            if score >= threshold:
                report.findings.append(Finding(
                    "fail", code, f"{where}[{i + 1}]↔[{j + 1}]",
                    f'거의 같은 문장이다 — "{normalize(left)[:22]}…"', score))


def review_step(step: dict, source: str, *,
                allow_numbers: frozenset[str] = frozenset(),
                where: str = "step") -> Report:
    """카드 한 장. `source` 는 그 카드에 넣어 준 입력 전체를 이어 붙인 문자열."""
    report = Report()
    headline = step.get("headline") or ""
    facts = [b for b in (step.get("facts") or []) if isinstance(b, str)]
    why = [b for b in (step.get("why") or []) if isinstance(b, str)]

    _dupes(report, f"{where}.facts", facts, DUPLICATE, "dup_fact")
    _dupes(report, f"{where}.why", why, DUPLICATE, "dup_why")

    # 사실 ↔ 의미. 이 카드에서 가장 중요한 검사다 — `확인된 사실` 과
    # `왜 중요한가` 가 같은 말이면 카드가 한 칸을 낭비한 것이 아니라,
    # 독자에게 "그래서 무엇이 달라지는가" 를 끝내 말하지 않은 것이다.
    for i, fact in enumerate(facts):
        for j, line in enumerate(why):
            score = similarity(fact, line)
            if score >= FACT_WHY:
                report.findings.append(Finding(
                    "fail", "fact_why_overlap", f"{where}.why[{j + 1}]",
                    f"facts[{i + 1}] 의 재진술이다 — 그래서 무엇이 달라지는지를 써야 한다",
                    score))

    if facts:
        score = similarity(headline, facts[0])
        if score >= HEADLINE_FACT:
            report.findings.append(Finding(
                "warn", "headline_copies_fact", f"{where}.headline",
                "사실 첫 줄과 거의 같다 — 오늘 달라진 것을 앞세울 것", score))

    for j, line in enumerate(why):
        if is_vague(line):
            report.findings.append(Finding(
                "fail", "vague_why", f"{where}.why[{j + 1}]",
                f'근거 없는 추상 표현이다 — "{normalize(line)[:22]}"'))

    for label, bullets in (("headline", [headline]), ("facts", facts), ("why", why)):
        for i, text in enumerate(bullets, start=1):
            for problem in ungrounded(text, source, allow_numbers=allow_numbers):
                slot = f"{where}.{label}" + (f"[{i}]" if label != "headline" else "")
                report.findings.append(Finding("fail", "ungrounded", slot, problem))
    return report


def review_daily(raw: dict, items: list[dict]) -> Report:
    """일일 카드 전체. 카드마다 **자기 이슈의 입력만** 근거로 본다.

    다른 이슈의 입력까지 합쳐 재면 A 카드가 B 기사의 숫자를 써도 통과한다 —
    카드 사이의 근거 누수가 정확히 그 모양이다.
    """
    report = Report()
    steps = raw.get("steps") or []
    # 훅의 "현안 3건" 같은 값은 입력이 아니라 **카드가 센 수**다. 접지 검사에서
    # 빼지 않으면 표지가 늘 걸린다.
    computed = frozenset({str(len(items)), str(len(steps))})

    # 표지도 카드 카피다. 근거는 오늘 이슈 전체 — 표지는 한 이슈를 대표하지
    # 않으므로 여기서만 합쳐 본다.
    everything = " ".join(str(item.get(field) or "") for item in items for field in (
        "title", "summary", "detail", "why_important", "implication",
        "open_question", "body", "event_date", "source"))
    hook = (raw.get("hook") or {}).get("headline")
    for problem in ungrounded(hook, everything, allow_numbers=computed):
        report.findings.append(Finding("fail", "ungrounded", "hook.headline", problem))

    for index, (step, item) in enumerate(zip(steps, items), start=1):
        if not isinstance(step, dict):
            continue
        source = " ".join(str(item.get(field) or "") for field in (
            "title", "summary", "detail", "why_important", "implication",
            "open_question", "body", "event_date", "source"))
        report.findings.extend(review_step(
            step, source, allow_numbers=computed, where=f"step{index}").findings)
    return report
