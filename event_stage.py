"""사건 단계(event stage) 판정 — "상태 변화는 별도 사건"을 기계가 지키게 한다.

왜 필요한가
-----------
V1 에서 가져온 빠른 제목 중복 알고리즘은 **문자열이 얼마나 닮았나**만 본다.
그런데 이 서비스의 사건 정의는 V2 에서 바뀌었다 — 같은 시설·같은 정책이라도
`심사 착수 → 최종 승인`, `가동 중단 → 재가동 승인` 처럼 **상태가 넘어가면 별개
사건**이다. 두 판정이 충돌하면 제목이 닮았다는 이유로 승인 기사가 심사 기사에
접히고, 그 승인은 AI story 판정을 보기도 전에 사라진다. 실제로 이 경로에서
사라지는 것이 하필 가장 중요한 뉴스다 — 단계가 넘어가는 순간이 곧 사건이므로.

그래서 제목 유사도 위에 **거부권**을 하나 더 얹는다. `_facility_conflict`
(호기가 다르면 같은 사건일 수 없다)와 같은 성격이다: 유사도가 아무리 높아도
단계가 갈리면 붙이지 않는다.

설계 원칙 — 과소검출 쪽으로 틀어 둔다
-------------------------------------
`stage_conflict()` 는 **양쪽 모두 단계를 말했고 겹치는 단계가 하나도 없을 때만**
참이다. 한쪽이라도 단계 표식이 없으면 판정하지 않는다(= 기존 동작 유지).

그래서 어휘를 넓게 잡는 것이 안전한 방향이다. 한 제목에서 표식이 더 많이
잡히면 집합이 커지고, 집합이 커지면 교집합이 생겨 거부권이 **덜** 발동한다.
'심사 결과 발표'처럼 두 단계에 걸친 표현은 {review, approval} 로 잡혀 어느
쪽과도 충돌하지 않는다 — 애매한 것을 갈라놓지 않는다는 뜻이다.

반대로 어휘를 좁게 잡으면 놓치는 것이 늘 뿐 오탐은 늘지 않는다. 이 비대칭이
설계의 전부다.

쓰지 않는 것
------------
`features.event_type` 은 사건의 **성격**(정책결정·규제조치·계약…)이지 단계가
아니다. 심사 착수와 최종 승인이 둘 다 `regulatory_action` 이라 여기서는
아무것도 가르지 못한다. 그래서 제목 표현만 본다.

요약·본문도 보지 않는다. 배경 설명이 과거 단계를 언급하는 일이 흔해서
(승인 기사의 본문은 거의 항상 심사 경위를 적는다) 집합이 뭉개진다. 단계를
말하는 자리는 제목이다.
"""

from __future__ import annotations

import re

# ---- 단계 어휘 -----------------------------------------------------------------
#
# 한국어 패턴은 **공백을 지운 제목**과 부분일치로 본다 ('가동 중단'/'가동중단'/
# '가동을 중단' 이 매체마다 갈리기 때문). 영어 패턴은 단어 경계로 본다 —
# 부분일치로 두면 'outage' 가 'coutage' 류에 걸리는 식의 사고가 난다.
#
# 순서는 의미가 없다. 매칭된 것을 전부 모아 집합으로 돌려준다.

STAGE_SPECS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "application", "신청·접수",
        ("신청서", "허가신청", "승인신청", "인가신청", "면허신청", "계속운전신청",
         "건설허가신청", "운영허가신청", "수출신청", "신청접수", "신청을접수",
         "신청서제출", "신청서를제출", "인허가신청"),
        ("application filed", "files application", "submits application",
         "licence application", "license application", "applies for"),
    ),
    (
        "review", "심사·심의",
        ("심사", "심의", "안전성평가", "적합성평가", "타당성조사", "예비타당성",
         "실사", "검증절차", "공청회", "주민설명회"),
        ("under review", "safety review", "regulatory review", "reviewing",
         "screening", "public hearing"),
    ),
    (
        "approval", "승인·인가",
        ("승인", "인가", "허가", "의결", "가결", "발급", "인증", "확정", "재가",
         "면허취득", "적합통보", "통과"),
        ("approved", "approval", "authorised", "authorized", "licence granted",
         "license granted", "green light", "greenlight", "certified", "cleared"),
    ),
    (
        "contract", "계약·수주",
        ("계약체결", "본계약", "가계약", "계약을체결", "수주", "낙찰", "발주",
         "협정체결", "협정을체결", "양해각서", "mou체결", "ppa체결", "공급계약"),
        ("signs contract", "contract award", "awarded a contract", "wins order",
         "signs agreement", "signed an agreement", "power purchase agreement"),
    ),
    (
        "construction", "착공",
        ("착공", "기공식", "첫콘크리트", "최초콘크리트", "본공사착수", "굴착개시",
         "부지정지공사"),
        ("construction start", "starts construction", "begins construction",
         "first concrete", "groundbreaking"),
    ),
    (
        "completion", "준공·가동개시",
        ("준공", "완공", "상업운전", "상업가동", "최초임계", "계통병입", "연료장전",
         "가동개시", "운전개시", "성능시험완료"),
        ("commercial operation", "first criticality", "grid connection",
         "fuel load", "commissioned", "enters service"),
    ),
    (
        "restart", "재가동·재개",
        ("재가동", "재기동", "가동재개", "운전재개", "재개", "출력복귀", "출력증강",
         "계통복구"),
        ("restart", "restarts", "resumes operation", "back online",
         "returns to service", "back in service"),
    ),
    (
        "shutdown", "정지·가동중단",
        ("가동중단", "가동중지", "가동정지", "운전정지", "발전정지", "수동정지",
         "자동정지", "영구정지", "출력감발", "출력저하", "셧다운", "폐쇄", "정지"),
        ("shutdown", "shut down", "taken offline", "goes offline", "halted",
         "outage", "suspended", "curtailed"),
    ),
    (
        "incident", "사고·고장",
        ("고장", "누출", "누설", "화재", "피폭", "균열", "중대사고", "원전사고",
         "방사선경보", "비상발령", "경보발령", "인명피해"),
        ("malfunction", "leak", "radiation alert", "emergency declared",
         "fire broke out"),
    ),
    (
        "cancellation", "취소·철회",
        ("취소", "철회", "무산", "백지화", "해지", "파기", "중도포기", "사업포기"),
        ("cancelled", "canceled", "scrapped", "withdrawn", "terminated",
         "called off"),
    ),
    (
        "investigation", "조사·수사",
        ("압수수색", "수사착수", "감사원", "특별점검", "현장조사", "조사착수",
         "청문회", "국정감사"),
        ("investigation launched", "probe into", "raided", "audit finds"),
    ),
)

STAGE_LABELS: dict[str, str] = {sid: label for sid, label, _, _ in STAGE_SPECS}

_SPACE_RE = re.compile(r"\s+")
# 영어 패턴은 단어 경계로만 매칭한다. 패턴 자체에 공백이 있으므로 escape 후 조립.
_EN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (sid, re.compile(r"\b" + re.escape(pat) + r"\b"))
    for sid, _label, _ko, en in STAGE_SPECS
    for pat in en
)


def _compact(text: str) -> str:
    """공백을 지운 소문자 문자열. '가동 중단' 과 '가동중단' 을 같게 본다."""
    return _SPACE_RE.sub("", str(text or "")).lower()


def _spaced(text: str) -> str:
    return _SPACE_RE.sub(" ", str(text or "")).strip().lower()


def detect_stages(*texts: object) -> frozenset[str]:
    """제목 문자열들에서 사건 단계 집합을 뽑는다. 표식이 없으면 빈 집합."""
    compact = "".join(_compact(t) for t in texts if t)
    spaced = " ".join(_spaced(t) for t in texts if t)
    if not compact:
        return frozenset()

    found: set[str] = set()
    for sid, _label, ko_patterns, _en in STAGE_SPECS:
        for pat in ko_patterns:
            if pat in compact:
                found.add(sid)
                break
    for sid, regex in _EN_PATTERNS:
        if sid in found:
            continue
        if regex.search(spaced):
            found.add(sid)
    return frozenset(found)


def article_stages(article: dict) -> frozenset[str]:
    """기사 하나의 단계 집합. 한국어 제목과 원문 제목을 함께 본다.

    번역 제목만 보면 원문에만 있는 표현(restart / shutdown)을 놓치고, 원문만 보면
    국내 기사가 통째로 빠진다. 둘을 합쳐서 본다 — 합치면 집합이 커지고, 집합이
    커지면 거부권은 덜 발동한다(안전한 방향).
    """
    if not isinstance(article, dict):
        return frozenset()
    return detect_stages(article.get("title_kr"), article.get("title"))


def stage_conflict(left: frozenset[str] | set[str],
                   right: frozenset[str] | set[str]) -> bool:
    """양쪽 다 단계를 말했는데 겹치는 단계가 없으면 같은 사건일 수 없다.

    한쪽이라도 비면 판정하지 않는다(보수적) — 표식이 없다는 것은 '단계가 없다'가
    아니라 '못 읽었다'이기 때문이다.
    """
    return bool(left) and bool(right) and not (set(left) & set(right))


def articles_conflict(left: dict, right: dict) -> bool:
    """기사 두 건이 서로 다른 사건 단계인가."""
    return stage_conflict(article_stages(left), article_stages(right))


def describe(stages: frozenset[str] | set[str]) -> str:
    """진단 화면에 쓸 한국어 라벨. '심사·심의 + 승인·인가' 형태."""
    return " + ".join(STAGE_LABELS.get(s, s) for s in sorted(stages))


def veto_record(left: dict, right: dict, *, stage: str) -> dict:
    """거부권이 발동한 쌍을 관리자 진단 화면이 읽을 형태로 남긴다.

    "왜 두 기사가 합쳐졌나"만큼 "왜 분리됐나"도 되짚을 수 있어야 한다 — 분리는
    화면에 아무 흔적을 남기지 않으므로, 남기지 않으면 영원히 안 보인다.
    """
    lst, rst = article_stages(left), article_stages(right)
    return {
        "kind": "event_stage",
        "stage": stage,
        "left_hash": str(left.get("hash") or ""),
        "right_hash": str(right.get("hash") or ""),
        "left_title": str(left.get("title_kr") or left.get("title") or "")[:120],
        "right_title": str(right.get("title_kr") or right.get("title") or "")[:120],
        "left_stages": sorted(lst),
        "right_stages": sorted(rst),
        "left_stage_label": describe(lst),
        "right_stage_label": describe(rst),
        "explanation": f"사건 단계가 다름 — {describe(lst)} ↔ {describe(rst)}",
    }
