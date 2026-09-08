"""신규 이슈 탐색 — 사전에 **없는** 이름을 잠깐 쫓아 본다.

`discovery.py` 와 무엇이 다른가
--------------------------------
후속 발굴(discovery)은 **아는 것의 상태 변화**를 묻는다: 엔티티 레지스트리에
등재된 대상 × 사건어. 그래서 구조적으로 못 보는 것이 하나 있다 — **레지스트리에도
고정 키워드에도 없는 이름**. 처음 등장한 SMR 기업, 이번 회기에 발의된 법안,
기사 한 줄에만 스친 해외 원전은 아무도 묻지 않는 말이 된다. 그 이름이 고정
키워드로 올라오려면 사람이 기사를 읽고 `keywords.json` 을 고쳐야 하는데, 그건
**이미 늦은 뒤**다. 이 모듈은 그 사이를 메운다.

설계 원칙 (discovery 와 같은 계약)
----------------------------------
- **LLM 0회.** 추출도 채점도 전부 결정적이다. 다만 재료로 쓰는 `importance` 는
  큐레이션 LLM 이 이미 매긴 값이다 — 새로 묻지 않고 **있는 판단을 재사용**한다.
- **웹 산출물에 의존하지 않는다.** 재료는 `archive/*.jsonl` 과
  `entity_registry.json` 뿐이다.
- **예산이 먼저.** discovery 예산(`DAILY_QUERY_BUDGET`)을 **건드리지 않는다.**
  여기는 별도 통장이고, 별도 상태 파일(`adaptive_state.json`)에 따로 적는다.
  실제 병목은 네이버 한도가 아니라 늘어난 유입이 전부 타고 가는 Gemini 쿼터라,
  총량은 discovery 의 3분의 1 이하로 잡았다.
- **임시다.** 여기서 만든 검색어는 24~72시간짜리다. 성과가 있으면 연장하고
  없으면 스스로 사라진다. 영구 등재는 사람이 한다(고정 키워드 승격 ·
  entity_registry 승격) — 자동으로 사전을 늘리면 오탐이 영구화되고, 그건 이
  저장소가 엔티티 사전에 세워 둔 '오탐 > 누락' 원칙과 정면으로 충돌한다.

폭증 방지 — 이 모듈이 제일 조심하는 것
--------------------------------------
자동 생성 검색어는 **조용히 늘어난다.** 늘어난 유입은 전부 큐레이션을 타므로
그 사고는 쿼터 소진이나 잡음 증가로 며칠 뒤에야 드러난다. 그래서 상한이 넷이다.

  ① 하루 질의 총량(`DAILY_QUERY_BUDGET`) · 회차 상한(`PER_RUN_QUERY_CAP`)
  ② 살아 있는 검색어 정원(`MAX_ACTIVE_TERMS`)
  ③ 하루에 새로 만들 수 있는 검색어 수(`MAX_NEW_TERMS_PER_DAY`)
  ④ 검색어 하나가 평생 쓸 수 있는 질의 수(`MAX_QUERIES_PER_TERM`)

그리고 **중복 검색 금지**: 고정 키워드·discovery 쿼리·다른 임시 검색어와 같은
질의는 애초에 만들지 않는다(질의 문자열 정규화 비교). 폐기된 말은 냉각 기간
동안 다시 만들지 않는다 — 안 그러면 같은 헛방을 매일 새로 발견한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from entity_match import _entity_alias_entries, _entity_match_token

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "adaptive_state.json"

KST = timezone(timedelta(hours=9))

# ── 예산 ────────────────────────────────────────────────────────────────────
# discovery 는 하루 40 · 회차 6 이다. 여기는 그 3분의 1 이하로 시작한다: 이쪽은
# '아는 대상의 상태'가 아니라 '모르는 이름'을 묻는 것이라 헛방 비율이 구조적으로
# 높고, 헛방도 유입이 있으면 큐레이션 비용을 그대로 태운다.
DAILY_QUERY_BUDGET = 12
PER_RUN_QUERY_CAP = 3

# 살아 있는 임시 검색어 정원. 정원을 넘으면 새 말을 만들지 않는다 — 기존 말을
# 밀어내지 않는 이유는, 밀려난 말이 다음 회차에 같은 점수로 다시 후보가 되어
# 자리를 맞바꾸며 둘 다 제대로 못 쫓게 되기 때문이다.
MAX_ACTIVE_TERMS = 24
MAX_NEW_TERMS_PER_DAY = 6
MAX_QUERIES_PER_TERM = 8

# 같은 말을 이 시간 안에 다시 묻지 않는다. 크롤은 3시간 간격이라 이게 없으면
# 점수 높은 말 두셋이 하루 예산을 나눠 갖는다.
MIN_RERUN_HOURS = 6

# ── 수명 ────────────────────────────────────────────────────────────────────
# TTL 은 점수로 가른다. 근거가 얕은 말을 사흘씩 쫓으면 예산이 그쪽에 묶인다.
TTL_HOURS_HIGH = 72
TTL_HOURS_MID = 48
TTL_HOURS_LOW = 24
TTL_SCORE_HIGH = 6.0
TTL_SCORE_MID = 4.5

EXTEND_HOURS = 24            # 신규 유입이 있으면 이만큼 연장
MAX_LIFETIME_DAYS = 14       # 연장을 거듭해도 여기서 끝난다
ZERO_YIELD_LIMIT = 3         # 신규 0건이 이만큼 연속되면 만료 전이라도 폐기
RETIRE_COOLDOWN_DAYS = 14    # 폐기된 말을 다시 만들지 않는 기간

# 승격 후보 판정. '몇 건 물어 왔나'만 보면 한 사건이 크게 터진 날 하루치로
# 후보가 된다 — 그건 이 검색어가 계속 값어치가 있다는 뜻이 아니다. **다른 날에도**
# 물어 왔는지를 함께 본다.
PROMOTE_MIN_YIELDS = 3
PROMOTE_MIN_DAYS = 2
MAX_PROMOTE_CANDIDATES = 12

# ── 채점 ────────────────────────────────────────────────────────────────────
SEED_HOURS = 48              # 씨앗으로 볼 아카이브 창
EVIDENCE_KEEP = 3            # 콘솔에 보일 근거 기사 수
ANCHOR_WINDOW = 40           # 원자력 앵커를 찾을 좌우 문자 수

# ── 신규성 ──────────────────────────────────────────────────────────────────
# **이 모듈의 정밀도는 거의 전부 여기서 나온다.** 모양만 보고 이름을 뽑으면
# '산업통상자원부'·'소형모듈'·'LNG' 처럼 몇 주째 매일 나오는 말이 매번 최상위
# 후보로 올라온다 — 이름인 것은 맞지만 **새롭지 않아서** 물을 값이 없다.
# 씨앗 창보다 오래된 아카이브에 이미 있던 말은 후보에서 뺀다.
# (실측 2026-08-17, 아카이브 3,887건: 이 규칙 하나로 상위 후보 45개 중 잡음
#  32개가 사라지고 케머러·톈완·로비사·타이핑링 같은 실제 신규 원전만 남았다.)
NOVELTY_HISTORY_DAYS = 21
NOVELTY_MAX_PRIOR = 1        # 과거 창에 이만큼 넘게 나왔으면 새 이름이 아니다

# 문턱. 둘 다 넘어야 검색어가 된다.
MIN_SCORE = 3.0
MIN_ARTICLES = 2             # 단, must_read 근거가 있으면 1건도 통과

IMPORTANCE_WEIGHT = {"must_read": 3.0, "nice_to_know": 1.0, "noise": 0.2}

# 앵커에서 떨어진 자리에서 잡힌 이름은 원자력 문맥이 아닐 수 있다(같은 기사의
# 다른 문단, 기자 이름, 협찬 문구). 0 으로 죽이지 않고 깎기만 한다 — 제목이
# 짧아 앵커가 창 밖으로 나가는 경우가 실제로 있다.
OFF_ANCHOR_FACTOR = 0.35

_NUCLEAR_ANCHORS = (
    "원자력", "원전", "원자로", "핵연료", "방폐", "방사", "우라늄", "농축", "재처리",
    "사용후핵연료", "계속운전", "중수로", "경수로", "smr", "소형모듈", "핵폐기물",
)
# 앵커 낱말 자체가 후보로 올라오는 것을 막는 열쇠 집합(정의는 _compact 아래).

# 검색어 자체가 원자력 문맥을 담고 있으면 그대로 묻고, 아니면 한정어를 붙인다.
# '테라파워'를 그냥 던지면 회사 소개·주가 기사가 오고, '테라파워 원자력'은
# 원자력 문맥의 기사만 온다.
_QUERY_MARKERS = ("원전", "원자력", "원자로", "핵연료", "방폐", "smr", "우라늄", "호기")

SOURCES = ("naver",)

_SPACE_RE = re.compile(r"\s+")
_HANGUL_RE = re.compile(r"[가-힣]")


def _compact(text: object) -> str:
    """공백·구분자를 지운 소문자. 질의 중복 판정과 차단 목록 대조에 쓴다."""
    return re.sub(r"[^0-9a-z가-힣]", "", str(text or "").lower())


_ANCHOR_KEYS = frozenset(_compact(anchor) for anchor in _NUCLEAR_ANCHORS)


def _text(value: object, limit: int = 200) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()[:limit]


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fingerprint(query: str, source: str) -> str:
    return hashlib.sha1(f"{source}|{query}".encode("utf-8")).hexdigest()[:16]


# ── 이름 추출 ───────────────────────────────────────────────────────────────
#
# 한국어 원자력 보도에서 **새 고유명사가 실제로 등장하는 모양**만 골랐다. 일반
# 명사구 추출기가 아니다 — 재현율을 넓히면 잡음이 그대로 검색 예산이 된다.
#
# 가중치는 그 모양이 얼마나 '이름다운가'다. 'X 원전'·'한글명(Latin)' 은 거의
# 언제나 이름이고, 대문자 라틴 낱말은 매체명·일반명사와 섞이므로 낮게 준다.

_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    # 'X 원전' · 'X 원자력발전소' — 이 모듈이 제일 잡고 싶은 모양이다.
    # 낱말 사이 공백을 하나까지 허용한다. 한 낱말만 보면 '아보이티즈 파워와 원전'
    # 에서 '파워와'만 잡혀 이름이 반토막 난다(실측 — 필리핀 Aboitiz Power).
    ("plant", re.compile(
        r"([가-힣A-Za-z][가-힣A-Za-z0-9·]{1,11}(?:\s[가-힣A-Za-z0-9·]{1,10})?)"
        r"\s*(?:원전|원자력발전소)"), 1.2),
    # 'X 3호기' — 원전 이름이 '원전' 없이 나오는 가장 흔한 자리.
    ("plant", re.compile(
        r"([가-힣A-Za-z][가-힣A-Za-z0-9·]{1,11}(?:\s[가-힣A-Za-z0-9·]{1,10})?)"
        r"\s*\d+\s*호기"), 1.2),
    # 기관. 접미사가 붙는 말은 거의 예외 없이 조직명이다.
    ("org", re.compile(r"([가-힣]{2,10}(?:위원회|연구원|연구소|공단|공사|재단|기구|협회|학회))"), 0.9),
    # 정부 부처·청. 앞에 2자 이상을 요구해 '일부·내부·간부·본부'를 배제한다
    # (그 말들은 전부 1자 + 접미사라 이 모양에 들어오지 못한다).
    ("org", re.compile(r"([가-힣]{2,9}(?:부|청|처))(?:는|가|와|의|에|를|이|은)"), 0.8),
    # 정책·사업. '제12차 전력수급기본계획' 처럼 회차가 붙는 형태를 함께 잡는다.
    # '프로젝트·사업단'은 뺐다 — '메가프로젝트'처럼 접미사만 맞고 이름이 아닌
    # 말이 대량으로 들어온다(실측 54건).
    ("project", re.compile(
        r"((?:제\s?\d+\s?차\s?)?[가-힣A-Za-z0-9]{2,14}"
        r"(?:특별법|기본계획|로드맵|이니셔티브|실증사업|협정))"), 1.0),
    # 기업. 'X 컨소시엄'·'X 그룹'. 'X사는' 모양은 뺐다 — 한국어에서 '사'로 끝나는
    # 보통명사가 너무 많아 '경남도지(사는)'·'지역난방공(사와)' 처럼 낱말을 한 글자
    # 잘라 먹는다. 새 기업명은 아래 '한글명(Latin)' 모양에서 대부분 잡힌다.
    ("company", re.compile(
        r"([가-힣A-Za-z][가-힣A-Za-z0-9]{1,12})\s*(?:컨소시엄|그룹)"), 0.9),
    # 노형·모델명. AP1000 · BWRX-300 · Xe-100.
    ("tech", re.compile(r"\b([A-Z][A-Za-z]{0,6}-?\d{2,4})\b"), 1.0),
    # '한글명(Latin)' — 한국 언론이 **낯선 이름을 처음 소개할 때** 쓰는 모양이다.
    # 이 모듈이 노리는 바로 그 순간이라 가중치를 높게 준다.
    ("company", re.compile(r"[가-힣]{2,12}\s*\(([A-Za-z][A-Za-z0-9&.\- ]{2,24})\)"), 1.2),
    # 그 밖의 라틴 이름. **혼합 대소문자만** 받는다(`[A-Z][a-z]{2,}`) — 전부
    # 대문자인 말은 이름이 아니라 업계 약어다(실측: SEED·ESS·AIDC·LNG·ETF·GPU·
    # PPA·IPO·LFP·HALEU 가 전부 이 자리로 들어왔다). 진짜 새 약어는 위의
    # '한글명(Latin)' 모양으로 소개되므로 그쪽에서 잡힌다.
    ("company", re.compile(r"\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\b"), 0.6),
)

# 'X 원전' 앞자리를 채우는 수식어·국가명. 이걸 안 막으면 '신규 원전'·'체코 원전'
# 같은 말이 매일 최상위 후보로 올라온다 — 넓기만 하고 새롭지 않은 검색어다.
_MODIFIER_STOPWORDS = frozenset("""
신규 기존 국내 해외 차세대 대형 소형 중형 노후 국산 수출 수입 전체 일부 해당 이번
관련 우리 자국 현지 추가 최초 최신 글로벌 세계 유럽 아시아 중동 동유럽 서방 각국
미국 중국 일본 러시아 프랑스 영국 한국 독일 체코 폴란드 인도 캐나다 사우디 베트남
필리핀 스웨덴 핀란드 벨기에 스위스 네덜란드 이집트 남아공 튀르키예 방글라데시 우크라이나
헝가리 루마니아 슬로바키아 불가리아 아랍 북한 남한 대만 호주 브라질 멕시코 이란
가동 건설 운영 폐로 해체 문제 사고 안전 국가 지역 지방 사업 계획 정책 전력 발전
이후 이전 동안 때문 경우 당시 현재 최근 지난 올해 내년 작년 가운데 상반기 하반기
정부 당국 업계 여야 국회 이번주 이달 내달 향후 기존에 새로운
""".split())

# 이름 자리에 서지만 이름이 아닌 말. 특히 **원자력 일반명사**가 중요하다 —
# '체르나보다 원전 2호기' 에서 `X 호기` 모양은 '원전'을 잡고, '소형모듈원전'
# 에서 `X 원전` 모양은 '소형모듈'을 잡는다. 둘 다 검색어로는 무의미하다.
_TERM_STOPWORDS = frozenset("""
원전 원자력 원자로 발전소 호기 핵연료 방폐장 소형모듈 소형모듈원자로 대형원전
사업부 영업부 총무부 기획부 관리부 홍보부 재무부 인사부 생산부 지원부 국방부
전담부 실무진 관계부 담당부 소관부 본부 지사 지역본부 국립연구소 연구소 연구기관
국제기구 협력기구 관련기구 유관기구 관계기관 지자체 공공기관 국제협회 관련협회
만나 나서 밝혀 열려 앞둔 이어 맞아 통한 향한 관련한 대상 중심 기준 규모 이상 이하
""".split())

# 조사가 붙은 채로 잡힌 이름에 주는 감점. 자르지도 버리지도 않는 이유:
# '센다이·홋카이도·시가'처럼 **조사와 같은 글자로 끝나는 외래어 표기**가
# 실제로 있어서, 자르면 이름이 망가지고 버리면 진짜 원전을 놓친다.
# 대신 점수를 깎아 문턱(MIN_SCORE) 아래로 내린다 — 여러 기사에 반복해서
# 나오면 그때는 올라온다.
_JOSA_TAILS = ("이", "가", "은", "는", "을", "를", "과", "와", "의", "도")
JOSA_TAIL_FACTOR = 0.5

# 조사·용언 어미로 끝나는 토막. `X 원전` 모양은 **원전 바로 앞 낱말**을 잡으므로
# '가뭄으로 원전 가동'·'이를 위해 원전 건설' 같은 문장에서 이름이 아닌 말이
# 그대로 들어온다(실측: 가뭄으로·인한·위해·취득).
#
# 두 글자 이상 어미만 본다. 한 글자 조사(와·과·이·가·에·로)까지 자르면 외래어
# 표기가 다치기 때문이다 — '오나가와 원전'의 '와', '체르나보다'의 '다'.
# 남는 잡음('테라파워와')은 대개 등재 엔티티의 조사 붙은 형태라
# `entity_match` 의 조사 허용(≤3자)이 이미 걸러 낸다.
_ENDING_STOPWORDS = (
    "으로", "에서", "에게", "부터", "까지", "에는", "에도", "이나", "거나", "면서",
    "지만", "와의", "과의", "로의", "등의", "등을", "등이", "등과",
    "하여", "위해", "인한", "통해", "따라", "대한", "위한", "관한", "비해", "향한",
    "의한", "이며", "라며", "라는", "다는", "하는", "되는", "있는", "없는",
    "했다", "한다", "된다", "이다", "라고", "면서도", "취득", "포함", "제외",
    "하며", "되며", "으며", "하고", "했고", "이라", "하면", "되면", "이며",
    "하는데", "했는데", "밝힌", "말한", "따른", "맞춰", "두고",
)

# 라틴 낱말 중 이름이 아닌 것. 원자력 기사 제목·요약에 실제로 자주 나오는 말만.
_LATIN_STOPWORDS = frozenset("""
the and for with from that this news report reports daily times post press wire
nuclear energy power reactor reactors plant plants uranium fuel small modular
world global international national federal state government ministry agency
company corp corporation group holdings limited inc ltd plc
new next first second third year years month week today
project projects program programme policy market markets industry
photo video exclusive breaking update updates analysis opinion editorial
korea korean china chinese japan japanese russia russian france french
america american europe european britain british german germany india indian
seoul busan washington london paris beijing tokyo moscow brussels vienna
smr amr npp iaea nrc doe eia iea nea wna oecd eu us uk un ceo cfo cto mou
""".split())


def _importance_weight(value: object) -> float:
    return IMPORTANCE_WEIGHT.get(str(value or "").strip(), 0.2)


def _anchor_nearby(text: str, position: int) -> bool:
    lowered = text.lower()
    start = max(0, position - ANCHOR_WINDOW)
    window = lowered[start:position + ANCHOR_WINDOW]
    return any(anchor in window for anchor in _NUCLEAR_ANCHORS)


def _is_known_entity(term: str, alias_entries) -> bool:
    """이미 레지스트리에 있는 이름인가.

    두 가지로 본다.

    ① `entity_match` 의 토큰 판정 — 여기서 따로 만들면 '봇은 새 이름이라 하는데
       웹은 기존 엔티티로 붙이는' 어긋남이 생긴다.
    ② **한글 별칭 포함 관계** — '고리원자력본부'·'제12차 전력수급기본계획' 처럼
       등재 엔티티를 품고 있는 긴 말은 ①의 접두 판정(꼬리 ≤3자)에 안 걸린다.
       새 이름이 아니라 아는 대상의 다른 표기이므로 discovery 의 몫이다.
    """
    token = _text(term, 80)
    if not token:
        return True
    lowered = token.lower()
    for norm, is_hangul, _entity, _order in alias_entries:
        if _entity_match_token(token, norm, is_hangul):
            return True
        if is_hangul and len(norm) >= 2 and norm in lowered:
            return True
    return False


def _canonical_term(term: str) -> str:
    """붙어 온 조사를 뗀다 — 다만 **띄어쓴 이름에서만**.

    '아보이티즈 파워와 원전' 의 '와'는 조사지만, '오나가와 원전'·'홋카이도'의
    같은 글자는 이름의 일부다. 한 낱말짜리 이름에서는 둘을 구분할 방법이 없어
    자르지 않고 점수만 깎는다(`_JOSA_TAILS`). 두 낱말 이상이면 마지막 낱말이
    통째로 조사인 경우가 사실상 없으므로 안전하게 뗄 수 있다.
    """
    cleaned = _text(term, 60).strip(" ·-.")
    if " " not in cleaned or len(cleaned) < 4:
        return cleaned
    if cleaned.endswith(_JOSA_TAILS) and len(cleaned.rsplit(" ", 1)[-1]) > 1:
        return cleaned[:-1].strip()
    return cleaned


def _term_variants(raw: str) -> list[str]:
    """한 번의 포착에서 시도해 볼 이름들 — 앞의 것이 먼저다.

    두 낱말까지 잡는 모양은 앞 낱말을 덤으로 물고 오기 쉽다('정부가 소형모듈
    원전'). 그렇다고 한 낱말만 잡으면 '아보이티즈 파워'가 '파워와'로 반토막
    난다. 그래서 **여러 후보를 만들고 처음으로 검사를 통과하는 것**을 쓴다.
    """
    full = _text(raw, 60).strip(" ·-.")
    stripped = _canonical_term(full)
    # 순서가 판정이다. 조사를 뗀 형태를 먼저 보되, **뗀 것이 이름을 깎은 경우**
    # (일본 오나가와 → 일본 오나가)에는 앞의 후보들이 전부 걸러지고 뒤의 온전한
    # 낱말(오나가와)이 채택된다. 그래서 자른 형태의 마지막 낱말이 제일 뒤에 선다.
    out: list[str] = []
    for candidate in (stripped, full):
        if candidate and candidate not in out:
            out.append(candidate)
    if " " in full:
        trimmed = full
        while " " in trimmed and _is_stopword(trimmed.rsplit(" ", 1)[-1]):
            trimmed = trimmed.rsplit(" ", 1)[0]
        for candidate in (trimmed, full.rsplit(" ", 1)[-1], stripped.rsplit(" ", 1)[-1]):
            if candidate and candidate not in out:
                out.append(candidate)
    return out


def _is_stopword(word: str) -> bool:
    return (word in _MODIFIER_STOPWORDS or word in _TERM_STOPWORDS
            or _compact(word) in _ANCHOR_KEYS)


def _plausible_term(term: str, kind: str) -> bool:
    """이름으로 볼 수 있는가 — 길이·불용어·구성만 본다(문맥은 채점이 본다)."""
    cleaned = _text(term, 60).strip(" ·-.")
    if not cleaned:
        return False
    # 두 낱말짜리는 양 끝을 따로 본다. 마지막 낱말이 일반명사면 이름이 아니라
    # 명사구이고('영덕 신규'), 첫 낱말이 용언이면 문장 토막이다('통해 한국').
    if " " in cleaned:
        head, _, tail = cleaned.partition(" ")
        if _is_stopword(tail.strip()) or head.endswith(_ENDING_STOPWORDS):
            return False
    compact = _compact(cleaned)
    if not compact:
        return False
    # 원자력 일반명사 자체는 검색어가 못 된다 — 이미 고정 키워드가 다 덮는다.
    if compact in _ANCHOR_KEYS:
        return False
    # 숫자로 시작하는 말은 이름이 아니라 수량이다('7년간 원전', '3기 원전').
    if cleaned[0].isdigit():
        return False
    is_hangul = bool(_HANGUL_RE.search(cleaned))
    if is_hangul:
        if len(compact) < 2 or len(compact) > 20:
            return False
        if cleaned in _MODIFIER_STOPWORDS or cleaned in _TERM_STOPWORDS:
            return False
        # '신규 원전' 류를 한 번 더 막는다 — 수식어가 통째로 잡힌 경우와,
        # '신규·계속운전' 처럼 구분자로 이어 붙은 경우 둘 다.
        if any(cleaned.startswith(word) and
               (len(cleaned) - len(word) <= 1 or cleaned[len(word)] in "·-/, ")
               for word in _MODIFIER_STOPWORDS):
            return False
        if cleaned.endswith(_ENDING_STOPWORDS):
            return False
    else:
        words = cleaned.lower().split()
        if len(compact) < 3 or len(compact) > 30:
            return False
        # 첫 낱말이 일반명사면 이름이 아니라 명사구다('Nuclear Energy').
        # 전부 일반명사인 것도 마찬가지. 다만 'Kairos Power' 처럼 뒷낱말만
        # 일반명사인 조합은 이름이므로 살린다.
        if words and (words[0] in _LATIN_STOPWORDS
                      or all(word in _LATIN_STOPWORDS for word in words)):
            return False
    # 숫자만·기호만인 말은 검색어가 못 된다.
    return bool(re.search(r"[가-힣A-Za-z]", cleaned))


def build_query(term: str, kind: str) -> str:
    """검색어 하나 → 실제로 던질 질의.

    한정어를 붙이는 이유: 이름만 던지면 동명이의(회사 소개·인물·지명) 기사가
    그대로 들어오고, 그 기사는 앵커 필터를 통과하지 못해도 이미 검색 한 번을
    태운 뒤다. 'X 원자력' 은 재현율을 조금 깎고 정밀도를 크게 올린다.
    """
    cleaned = _text(term, 60)
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _QUERY_MARKERS):
        return cleaned
    if kind == "plant":
        return f"{cleaned} 원전"
    return f"{cleaned} 원자력"


def _history_blob(rows: list[dict], start: datetime, end: datetime) -> str:
    """씨앗 창보다 **오래된** 기사 본문을 한 덩어리로. 신규성 판정 재료다.

    후보마다 과거 기사를 다시 훑으면 O(후보 × 기사)가 된다. 한 번 이어 붙여
    두면 문자열 검색 한 번으로 끝난다 — 아카이브 3,887건이 1MB 남짓이라
    메모리도 문제가 안 된다.
    """
    parts: list[str] = []
    for row in rows:
        stamp = _parse_dt(row.get("archived_at"))
        if stamp is None or not (start <= stamp < end):
            continue
        parts.append(str(row.get("title_kr") or row.get("title") or ""))
        parts.append(str(row.get("summary") or ""))
    return " \n".join(parts).lower()


def extract_candidates(rows: list[dict], registry: list[dict],
                       now: datetime | None = None,
                       seed_hours: int = SEED_HOURS,
                       history_days: int = NOVELTY_HISTORY_DAYS) -> dict[str, dict]:
    """최근 아카이브에서 **레지스트리에 없는 새 이름** 후보를 뽑는다.

    아카이브를 쓰는 이유: RSS·공식기관·뉴스레터·검색으로 들어온 것이 전부
    큐레이션을 거쳐 여기 모이고(noise 등급까지 적재된다), 그래서 채널마다 따로
    긁을 필요가 없다. 대신 이번 회차 기사는 아직 적재 전이라 **최대 한 회차
    (3시간) 늦게** 보인다 — 24시간 TTL 짜리 추적에서는 감수할 만한 지연이다.

    `rows` 에는 **씨앗 창보다 오래된 기사도 함께** 넘긴다. 그 기사들은 후보가
    되지 않고 신규성 판정에만 쓰인다(`NOVELTY_HISTORY_DAYS`).

    반환: {term_key: {term, type, score, articles, domains, importance, evidence}}
    """
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(hours=seed_hours)
    history = _history_blob(rows, now - timedelta(days=history_days), cut)
    alias_entries = _entity_alias_entries(registry)

    found: dict[str, dict] = {}
    for row in rows:
        stamp = _parse_dt(row.get("archived_at"))
        if stamp is None or stamp < cut:
            continue
        title = str(row.get("title_kr") or row.get("title") or "")
        summary = str(row.get("summary") or "")
        text = f"{title} {summary}"
        if not text.strip():
            continue
        importance = str(row.get("importance") or "")
        weight = _importance_weight(importance)
        domain = str(row.get("domain") or "")

        # 한 기사 안에서 같은 말이 여러 번 잡혀도 한 번만 센다 — 제목과 요약에
        # 반복되는 이름이 두 배 점수를 받으면 '반복 보도'와 구별이 사라진다.
        seen_here: set[str] = set()
        for kind, pattern, pattern_weight in _PATTERNS:
            for match in pattern.finditer(text):
                term = ""
                for variant in _term_variants(match.group(1)):
                    if not _plausible_term(variant, kind):
                        continue
                    if _is_known_entity(variant, alias_entries):
                        continue
                    # 새롭지 않으면 여기서 끝. 이 한 줄이 이 모듈의 정밀도
                    # 대부분을 만든다(NOVELTY_HISTORY_DAYS 주석의 실측 참조).
                    if history.count(variant.lower()) > NOVELTY_MAX_PRIOR:
                        continue
                    term = variant
                    break
                if not term:
                    continue
                key = _compact(term)
                if key in seen_here:
                    continue
                seen_here.add(key)
                factor = 1.0 if _anchor_nearby(text, match.start(1)) else OFF_ANCHOR_FACTOR
                if len(term) >= 3 and term.endswith(_JOSA_TAILS):
                    factor *= JOSA_TAIL_FACTOR
                entry = found.setdefault(key, {
                    "term": term, "type": kind, "score": 0.0,
                    "articles": 0, "domains": [], "importance": "",
                    "evidence": [], "shape_weight": 0.0,
                })
                entry["score"] += pattern_weight * weight * factor
                entry["articles"] += 1
                if domain and domain not in entry["domains"]:
                    entry["domains"].append(domain)
                if _importance_weight(importance) > _importance_weight(entry["importance"]):
                    entry["importance"] = importance
                # 유형은 **가장 이름다운 모양**이 이긴다 — 'X 원전'으로도 'X사는'
                # 으로도 잡힌 말은 원전 쪽이 맞다. 유형별 최대 가중치로 비교하면
                # 같은 유형의 약한 모양(대문자 라틴 0.6)에 강한 모양의 자리가
                # 넘어가지 않으므로, 실제로 이긴 모양의 가중치를 들고 있는다.
                if pattern_weight > float(entry["shape_weight"]):
                    entry["type"] = kind
                    entry["shape_weight"] = pattern_weight
                if len(entry["evidence"]) < EVIDENCE_KEEP:
                    entry["evidence"].append({
                        "title": _text(title, 140),
                        "domain": domain,
                        "importance": importance,
                        "hash": _text(row.get("hash"), 64),
                    })

    # 매체가 여럿이면 그 이름은 한 기자의 표현이 아니다.
    for entry in found.values():
        entry["score"] += 0.5 * max(0, len(entry["domains"]) - 1)
        entry["score"] = round(entry["score"], 2)
    return found


def _eligible(entry: dict) -> bool:
    if entry["score"] < MIN_SCORE:
        return False
    if entry["articles"] >= MIN_ARTICLES:
        return True
    # 근거가 한 건이어도 must_read 면 통과시킨다 — 이 모듈이 존재하는 이유가
    # '큰 사건이 낯선 이름으로 처음 등장하는 순간'이다.
    return entry["importance"] == "must_read"


def _ttl_hours(score: float) -> int:
    if score >= TTL_SCORE_HIGH:
        return TTL_HOURS_HIGH
    if score >= TTL_SCORE_MID:
        return TTL_HOURS_MID
    return TTL_HOURS_LOW


# ── 상태 ────────────────────────────────────────────────────────────────────


def load_state(path: Path = STATE_FILE) -> dict:
    """상태 파일은 없어도 정상이다 — 첫 실행이거나 캐시가 날아간 경우."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    state = _empty_state()
    if isinstance(raw.get("terms"), dict):
        state["terms"] = {k: v for k, v in raw["terms"].items() if isinstance(v, dict)}
    if isinstance(raw.get("retired"), dict):
        state["retired"] = {k: v for k, v in raw["retired"].items() if isinstance(v, dict)}
    for key in ("spent", "minted"):
        if isinstance(raw.get(key), dict):
            state[key] = raw[key]
    return state


def _empty_state() -> dict:
    return {"version": 1, "terms": {}, "retired": {}, "spent": {}, "minted": {}}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _counter(state: dict, key: str, day: str) -> int:
    bucket = state.get(key) if isinstance(state.get(key), dict) else {}
    if bucket.get("date") != day:
        return 0
    try:
        return int(bucket.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _is_pinned(entry: dict) -> bool:
    """만료·냉각을 적용하지 않는 항목 — 사람이 넣었거나 사람이 고정했다."""
    return bool(entry.get("pinned")) or entry.get("origin") == "console"


def sweep(state: dict, now: datetime | None = None) -> dict:
    """만료·성과 없음·횟수 소진을 정리하고 승격 후보를 가른다.

    ⚠️ 신규 0건이 곧 실패는 아니다(discovery 와 같은 주의) — 이미 다 걷은
    사건이면 정상이다. 다만 임시 검색어는 **애초에 근거가 얕은 말**이라
    discovery 보다 빨리 접는다.
    """
    now = now or datetime.now(timezone.utc)
    terms = state.get("terms") or {}
    for key, entry in list(terms.items()):
        if _is_pinned(entry):
            continue
        created = _parse_dt(entry.get("created_at")) or now
        expires = _parse_dt(entry.get("expires_at"))
        reason = ""
        if int(entry.get("zero_streak") or 0) >= ZERO_YIELD_LIMIT:
            reason = "성과 없음"
        elif int(entry.get("queries_run") or 0) >= MAX_QUERIES_PER_TERM:
            reason = "질의 횟수 소진"
        elif now - created >= timedelta(days=MAX_LIFETIME_DAYS):
            reason = "추적 기간 상한"
        elif expires is not None and now >= expires:
            reason = "기간 만료"
        if not reason:
            continue
        # 승격 후보는 만료로 지우지 않는다 — 사람이 판단할 것이 남아 있다.
        if entry.get("status") == "promote_candidate" and reason == "기간 만료":
            continue
        _retire(state, key, entry, reason, now)

    # 승격 후보가 무한정 쌓이지 않게. 넘치면 성과가 가장 적은 것부터 접는다.
    candidates = [(key, entry) for key, entry in (state.get("terms") or {}).items()
                  if entry.get("status") == "promote_candidate" and not _is_pinned(entry)]
    if len(candidates) > MAX_PROMOTE_CANDIDATES:
        candidates.sort(key=lambda item: (int(item[1].get("new_articles") or 0),
                                          _text(item[1].get("created_at"), 40)))
        for key, entry in candidates[:len(candidates) - MAX_PROMOTE_CANDIDATES]:
            _retire(state, key, entry, "승격 후보 정원 초과", now)
    return state


def _retire(state: dict, key: str, entry: dict, reason: str, now: datetime) -> None:
    state.setdefault("retired", {})[key] = {
        "term": entry.get("term", ""),
        "type": entry.get("type", ""),
        "reason": reason,
        "retired_at": now.isoformat(),
        "until": (now + timedelta(days=RETIRE_COOLDOWN_DAYS)).isoformat(),
        "new_articles": int(entry.get("new_articles") or 0),
    }
    (state.get("terms") or {}).pop(key, None)


def _cooling(state: dict, key: str, now: datetime) -> bool:
    row = (state.get("retired") or {}).get(key)
    if not isinstance(row, dict):
        return False
    until = _parse_dt(row.get("until"))
    return bool(until and now < until)


# ── 계획 ────────────────────────────────────────────────────────────────────


def plan_queries(rows: list[dict],
                 registry: list[dict],
                 state: dict,
                 *,
                 fixed_queries: list[str] | tuple[str, ...] = (),
                 discovery_queries: list[str] | tuple[str, ...] = (),
                 console: dict | None = None,
                 now: datetime | None = None,
                 budget: int = DAILY_QUERY_BUDGET,
                 per_run_cap: int = PER_RUN_QUERY_CAP) -> tuple[list[dict], dict]:
    """이번 회차에 던질 임시 질의와 갱신된 상태. 네트워크를 타지 않는다.

    `fixed_queries` 는 고정 키워드, `discovery_queries` 는 discovery 가 쓰는 질의다.
    둘 다 **중복 검색을 막기 위한 재료**일 뿐 예산을 나눠 쓰지 않는다.

    `console` 은 `admin_overrides.learned_terms()` 의 결과다:
    `{"added": [...], "blocked": {compact...}, "pinned": {compact...}}`.
    """
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(KST).strftime("%Y-%m-%d")
    console = console or {}
    state.setdefault("terms", {})
    state.setdefault("retired", {})

    _apply_console(state, console, now)
    sweep(state, now)

    used = _counter(state, "spent", day)
    run_cap = min(per_run_cap, max(0, budget - used))
    if run_cap <= 0:
        # 물을 수 없으면 만들지도 않는다. TTL 은 만든 순간부터 흐르므로, 예산이
        # 마른 날에 만들어 두면 한 번도 못 물어보고 만료되는 검색어가 생긴다.
        # 씨앗 창이 48시간이라 그 이름은 내일 다시 후보로 올라온다 — 잃는 게 없다.
        state["spent"] = {"date": day, "count": used}
        return [], state

    # 이미 검색하는 말. 질의 문자열로 비교한다 — 검색어끼리 비교하면 '팍스'와
    # '팍스 원전'을 다른 말로 보지만 실제로 나가는 질의는 같아진다.
    known = {_compact(q) for q in fixed_queries}
    known |= {_compact(q) for q in discovery_queries}
    known |= {_compact(entry.get("query")) for entry in state["terms"].values()}
    known.discard("")

    _mint(state, rows, registry, known, console, day, now)

    # 회전 순서: 한 번도 안 물어본 말 → 가장 오래 안 물어본 말 → 점수 높은 말.
    rerun_cut = now - timedelta(hours=MIN_RERUN_HOURS)
    ready: list[tuple[datetime, float, str]] = []
    for key, entry in state["terms"].items():
        planned = _parse_dt(entry.get("planned_at"))
        if planned is not None and planned > rerun_cut:
            continue
        if not _is_pinned(entry) and int(entry.get("queries_run") or 0) >= MAX_QUERIES_PER_TERM:
            continue
        ready.append((planned or datetime.min.replace(tzinfo=timezone.utc),
                      -float(entry.get("score") or 0.0), key))
    ready.sort()

    queries: list[dict] = []
    for _planned, _score, key in ready[:run_cap]:
        entry = state["terms"][key]
        query = entry.get("query") or build_query(entry.get("term", ""), entry.get("type", ""))
        entry["query"] = query
        entry["planned_at"] = now.isoformat()
        entry["queries_run"] = int(entry.get("queries_run") or 0) + 1
        for source in SOURCES:
            queries.append({
                "query": query,
                "source": source,
                "term": entry.get("term", ""),
                "term_id": key,
                "type": entry.get("type", ""),
                "reason": f"{entry.get('status') or 'tracking'}:{entry.get('origin') or 'auto'}",
                "fingerprint": fingerprint(query, source),
            })
    state["spent"] = {"date": day, "count": used + len(queries)}
    return queries, state


def _apply_console(state: dict, console: dict, now: datetime) -> None:
    """사람이 내린 판정을 상태에 얹는다 — 차단·고정·직접 추가.

    차단은 **즉시** 듣는다. 관리자가 화면에서 뺀 말이 다음 회차에 한 번 더
    검색에 나가면, 그건 눌러도 아무 일이 안 일어난 것으로 읽힌다.
    """
    blocked = {_compact(value) for value in (console.get("blocked") or set())}
    blocked.discard("")
    for key, entry in list((state.get("terms") or {}).items()):
        if _compact(entry.get("term")) in blocked or key in blocked:
            _retire(state, key, entry, "관리자 삭제", now)
            # 관리자 삭제는 냉각이 아니라 차단이다 — 판정을 지우기 전에는
            # 다시 만들지 않는다(아래 mint 에서 blocked 를 다시 본다).

    pinned = {_compact(value) for value in (console.get("pinned") or set())}
    for entry in (state.get("terms") or {}).values():
        if _compact(entry.get("term")) in pinned:
            entry["pinned"] = True
        elif entry.get("origin") != "console":
            entry.pop("pinned", None)

    for row in (console.get("added") or []):
        term = _text(row.get("term") or row.get("value"), 60)
        if not term or _compact(term) in blocked:
            continue
        key = _compact(term)
        entry = (state.get("terms") or {}).get(key)
        if entry is None:
            state.setdefault("terms", {})[key] = {
                "term": term,
                "type": _text(row.get("type"), 20) or "manual",
                "query": _text(row.get("query"), 80) or build_query(term, "manual"),
                "origin": "console",
                "status": "tracking",
                "score": 0.0,
                "articles": 0,
                "domains": [],
                "importance": "",
                "evidence": [],
                "created_at": now.isoformat(),
                "expires_at": "",
                "queries_run": 0,
                "new_articles": 0,
                "yields": 0,
                "yield_days": [],
                "zero_streak": 0,
                "entry_id": _text(row.get("id"), 64),
            }
            # 사람이 넣은 말은 만료로 사라지지 않는다. 넣은 사람이 뺀다 —
            # 자동 폐기하면 판정 항목은 남아 있으므로 다음 회차에 되살아나고,
            # 그 왕복이 영원히 반복된다.
            state["terms"][key]["pinned"] = True
        else:
            entry["origin"] = entry.get("origin") or "console"
            entry["pinned"] = True
        (state.get("retired") or {}).pop(key, None)


def _mint(state: dict, rows: list[dict], registry: list[dict],
          known: set[str], console: dict, day: str, now: datetime) -> None:
    """새 임시 검색어를 만든다 — 상한 넷을 전부 통과한 것만."""
    terms = state.setdefault("terms", {})
    minted_today = _counter(state, "minted", day)
    room = min(MAX_ACTIVE_TERMS - len(terms), MAX_NEW_TERMS_PER_DAY - minted_today)
    if room <= 0:
        state["minted"] = {"date": day, "count": minted_today}
        return

    blocked = {_compact(value) for value in (console.get("blocked") or set())}
    candidates = extract_candidates(rows, registry, now)
    ranked = sorted(
        ((key, entry) for key, entry in candidates.items() if _eligible(entry)),
        key=lambda item: (-item[1]["score"], item[0]),
    )

    minted = 0
    for key, candidate in ranked:
        if minted >= room:
            break
        if key in terms or key in blocked or _cooling(state, key, now):
            continue
        query = build_query(candidate["term"], candidate["type"])
        if _compact(query) in known:
            continue
        known.add(_compact(query))
        ttl = _ttl_hours(candidate["score"])
        terms[key] = {
            "term": candidate["term"],
            "type": candidate["type"],
            "query": query,
            "origin": "auto",
            "status": "tracking",
            "score": candidate["score"],
            "articles": candidate["articles"],
            "domains": candidate["domains"][:6],
            "importance": candidate["importance"],
            "evidence": candidate["evidence"],
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=ttl)).isoformat(),
            "ttl_hours": ttl,
            "queries_run": 0,
            "new_articles": 0,
            "yields": 0,
            "yield_days": [],
            "zero_streak": 0,
        }
        (state.get("retired") or {}).pop(key, None)
        minted += 1
    state["minted"] = {"date": day, "count": minted_today + minted}


def record_results(state: dict, results: list[dict], now: datetime | None = None) -> dict:
    """성과를 적고 추적 기간을 연장하거나 접는다.

    results: {"term_id", "query", "result_count", "new_article_count"}
    """
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(KST).strftime("%Y-%m-%d")
    for result in results:
        key = result.get("term_id")
        entry = (state.get("terms") or {}).get(key)
        if not entry:
            continue
        new_count = int(result.get("new_article_count") or 0)
        entry["last_run"] = now.isoformat()
        entry["last_result_count"] = int(result.get("result_count") or 0)
        if new_count <= 0:
            entry["zero_streak"] = int(entry.get("zero_streak") or 0) + 1
            continue

        entry["zero_streak"] = 0
        entry["new_articles"] = int(entry.get("new_articles") or 0) + new_count
        entry["yields"] = int(entry.get("yields") or 0) + 1
        days = [d for d in (entry.get("yield_days") or []) if isinstance(d, str)]
        if day not in days:
            days.append(day)
        entry["yield_days"] = days[-14:]

        # 연장 — 다만 평생 상한(MAX_LIFETIME_DAYS) 을 넘기지 않는다.
        created = _parse_dt(entry.get("created_at")) or now
        hard_stop = created + timedelta(days=MAX_LIFETIME_DAYS)
        extended = min(now + timedelta(hours=EXTEND_HOURS), hard_stop)
        current = _parse_dt(entry.get("expires_at"))
        if current is None or extended > current:
            entry["expires_at"] = extended.isoformat()
        if entry.get("status") != "promote_candidate":
            entry["status"] = "extended"
        if (entry["yields"] >= PROMOTE_MIN_YIELDS
                and len(entry["yield_days"]) >= PROMOTE_MIN_DAYS):
            entry["status"] = "promote_candidate"
    return state


def prune_state(state: dict, now: datetime | None = None, keep_days: int = 60) -> dict:
    """폐기 기록을 영원히 들고 있지 않는다 — 상태 파일이 커밋되므로."""
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=keep_days)
    state["retired"] = {
        key: row for key, row in (state.get("retired") or {}).items()
        if (_parse_dt(row.get("retired_at")) or now) >= cut
    }
    return state


# ── 화면용 ──────────────────────────────────────────────────────────────────


STATUS_LABEL = {
    "tracking": "추적 중",
    "extended": "연장됨",
    "promote_candidate": "승격 후보",
}


def console_view(state: dict, now: datetime | None = None,
                 limit: int = 40) -> list[dict]:
    """운영 콘솔이 '학습된 검색어'로 그릴 목록.

    숫자만 주지 않는다 — 왜 이 말이 생겼는지(근거 기사)와 언제 사라지는지를
    함께 준다. 근거 없이 목록만 있으면 관리자는 지울지 둘지 판단할 수 없고,
    판단할 수 없는 목록은 결국 아무도 안 본다.
    """
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for key, entry in (state.get("terms") or {}).items():
        expires = _parse_dt(entry.get("expires_at"))
        pinned = _is_pinned(entry)
        # 승격 후보도 만료로 지우지 않는다(`sweep`). 남은 시간을 그대로 적으면
        # 화면이 '0시간 남음'이라 말하는데 실제로는 사람의 판단을 기다리는
        # 중이라, 관리자는 곧 사라질 말로 읽고 손을 안 댄다.
        held = pinned or entry.get("status") == "promote_candidate"
        rows.append({
            "id": key,
            "term": _text(entry.get("term"), 60),
            "query": _text(entry.get("query"), 80),
            "type": _text(entry.get("type"), 20),
            "origin": _text(entry.get("origin"), 20) or "auto",
            "status": _text(entry.get("status"), 20) or "tracking",
            "status_label": STATUS_LABEL.get(str(entry.get("status")), "추적 중"),
            "pinned": pinned,
            "score": float(entry.get("score") or 0.0),
            "seed_articles": int(entry.get("articles") or 0),
            "importance": _text(entry.get("importance"), 20),
            "queries_run": int(entry.get("queries_run") or 0),
            "new_articles": int(entry.get("new_articles") or 0),
            "yields": int(entry.get("yields") or 0),
            "yield_days": len(entry.get("yield_days") or []),
            "created_at": _text(entry.get("created_at"), 40),
            "expires_at": _text(entry.get("expires_at"), 40),
            # 화면에서 시각 계산을 다시 하지 않게 남은 시간을 여기서 낸다.
            "expires_in_hours": (None if held or expires is None
                                 else round((expires - now).total_seconds() / 3600, 1)),
            "evidence": [
                {"title": _text(item.get("title"), 140),
                 "domain": _text(item.get("domain"), 80),
                 "importance": _text(item.get("importance"), 20)}
                for item in (entry.get("evidence") or [])[:EVIDENCE_KEEP]
                if isinstance(item, dict)
            ],
            # entity_registry 승격 후보에게 주는 초안. 별칭·match_policy 는 사람이
            # 정해야 하므로 자동 등재는 하지 않는다(엔티티 사전의 '오탐 > 누락').
            "registry_draft": _registry_draft(entry) if entry.get("status") == "promote_candidate" else "",
        })
    rows.sort(key=lambda row: (
        {"promote_candidate": 0, "extended": 1, "tracking": 2}.get(row["status"], 3),
        -row["new_articles"], -row["score"], row["term"],
    ))
    return rows[:limit]


def _registry_draft(entry: dict) -> str:
    term = _text(entry.get("term"), 60)
    kind = _text(entry.get("type"), 20)
    entity_type = kind if kind in ("plant", "company", "org", "project") else "company"
    return json.dumps({
        "id": _compact(term)[:24] or "new-entity",
        "name_kr": term,
        "name_en": "",
        "type": entity_type,
        "countries": [],
        "aliases": [term],
    }, ensure_ascii=False)


def retired_view(state: dict, limit: int = 20) -> list[dict]:
    """최근 폐기 목록. '왜 그 검색어가 사라졌나'에 답할 수 있어야 한다."""
    rows = [{
        "id": key,
        "term": _text(row.get("term"), 60),
        "reason": _text(row.get("reason"), 40),
        "retired_at": _text(row.get("retired_at"), 40),
        "new_articles": int(row.get("new_articles") or 0),
    } for key, row in (state.get("retired") or {}).items()]
    rows.sort(key=lambda row: row["retired_at"], reverse=True)
    return rows[:limit]


def summary(state: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(KST).strftime("%Y-%m-%d")
    terms = state.get("terms") or {}
    return {
        "active": len(terms),
        "capacity": MAX_ACTIVE_TERMS,
        "promote_candidates": sum(1 for e in terms.values()
                                  if e.get("status") == "promote_candidate"),
        "spent_today": _counter(state, "spent", day),
        "daily_budget": DAILY_QUERY_BUDGET,
        "minted_today": _counter(state, "minted", day),
        "mint_cap": MAX_NEW_TERMS_PER_DAY,
        "retired": len(state.get("retired") or {}),
    }
