"""원자력 뉴스 아카이브를 이슈 중심 웹 데이터로 빌드한다.

원본 봇 저장소는 읽기만 한다. 이 프로토타입 디렉터리의 ``public/data``에만
결과를 쓴다. ``BOT_DIR`` 환경 변수로 원본 봇 저장소 위치를 지정할 수 있다.

출력:
  - news.json: 기사 발행일 기준 전체 피드
  - briefings.json: 발송일 기준 브리핑 + 이슈 묶음
  - issues.json: 전체 기간에서 중복 제거한 고유 이슈 카탈로그
  - trend.json: 집계 데이터
  - insights.json: 봇이 생성한 흐름 해석
  - issue_audit.json: 날짜 간 이슈 연결 근거와 차단 진단
  - meta.json: 생성 시각, 건수, 통제 태그 커버리지

이슈 묶음은 외부 API를 호출하지 않는 보수적 MVP다. 제목·태그 유사도가 충분히
높을 때만 합치며, 불확실하거나 계산에 실패한 기사는 단독 이슈로 남긴다.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

SITE_DIR = Path(__file__).resolve().parent
# 봇 저장소 web/ 아래로 이식됨 (2026-08-01) — 기본값은 저장소 루트(부모 폴더).
# 프로토타입 원본 위치에서 쓸 때는 BOT_DIR 환경 변수로 지정.
BOT_DIR = Path(os.environ.get("BOT_DIR", SITE_DIR.parent))
OUT_DIR = Path(os.environ.get("OUTPUT_DIR", SITE_DIR / "public" / "data"))
GENERATION_ID = os.environ.get("GENERATION_ID", "")

SHOW_MARKET = False
NEWS_WINDOW_DAYS = 60
ISSUE_WINDOW_DAYS = 21
ISSUE_EMBEDDING_THRESHOLD = 0.84
KST = timezone(timedelta(hours=9))

_KR_DOMAIN_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")
_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_NORM_RE = re.compile(r"[^0-9a-z가-힣]")

_FACILITY_NAMES = (
    "신월성", "신한울", "신고리", "후쿠시마", "자포리자", "체르노빌",
    "올킬루오토", "플라망빌", "힝클리", "사이즈웰", "두코바니", "테믈린",
    "세르나보다", "알마라즈", "바라카", "아투차", "엠발세", "보글틀",
    "새울", "고리", "월성", "한빛", "한울", "타이산", "파크스",
)
_FACILITY_PATTERN = "|".join(re.escape(name) for name in sorted(_FACILITY_NAMES, key=len, reverse=True))
_FACILITY_RE = re.compile(_FACILITY_PATTERN, re.IGNORECASE)
_UNIT_RE = re.compile(rf"({_FACILITY_PATTERN})\s*(\d+)\s*호기", re.IGNORECASE)

_GENERIC_TAGS = {
    "원전", "원자력", "에너지", "정책", "에너지정책", "원전정책", "해외원전",
    "국내원전", "산업동향", "시장동향", "기술개발", "국제협력", "안전",
    # 기관명만 같다고 같은 이슈는 아니다. 원안위가 다룬 서로 다른 사건이
    # 한 묶음으로 합쳐지는 false merge를 막는다.
    "원안위", "nssc", "iaea", "한수원", "khnp", "미국nrc", "미국doe", "정부",
}
_TAG_ALIASES = {
    "doe": "미국doe",
    "미에너지부": "미국doe",
    "미국에너지부": "미국doe",
    "nrc": "미국nrc",
    "미원자력규제위원회": "미국nrc",
    "전기본": "전력수급기본계획",
    "12차전기본": "12차전력수급기본계획",
}

_EVENT_REASON_LABELS = {
    "policy_decision": "정책 결정",
    "regulatory_action": "규제 조치",
    "contract_award": "계약 체결",
    "project_milestone": "사업 진전",
    "incident_safety": "안전 사건",
    "corporate_move": "기업 동향",
    "research_report": "연구·보고서",
    "market_signal": "시장 신호",
}

# 기존 자유 태그·제목을 통제 주제로 옮기는 프로토타입용 로컬 분류표. 원본
# 아카이브는 수정하지 않고 생성 JSON에만 적용한다. 규칙은 구체 표현 위주로 두어
# 단순히 "원전"이 들어갔다는 이유만으로 주제를 붙이지 않는다.
_TOPIC_RULES = {
    "fukushima": ("후쿠시마", "alps", "처리수", "오염수"),
    "fusion": ("핵융합", "fusion", "iter", "tokamak", "토카막"),
    "smr": ("smr", "소형모듈", "소형 모듈", "mmr", "마이크로원자로", "advanced reactor"),
    "restart_lto": ("계속운전", "계속 운전", "수명연장", "수명 연장", "재가동", "life extension", "restart"),
    "newbuild": ("신규원전", "신규 원전", "원전건설", "원전 건설", "new nuclear", "nuclear program", "nuclear programme"),
    "fuel_cycle": ("핵연료", "haleu", "우라늄", "uranium", "농축", "연료주기", "fuel cycle"),
    "waste": ("사용후핵연료", "방사성폐기물", "방폐", "고준위", "폐기물 처분", "decommission"),
    "regulation": ("규제", "인허가", "허가 연장", "nrc", "원안위", "nssc", "행정예고", "입법예고", "안전심사"),
    "datacenter_ai": ("데이터센터", "데이터 센터", "ai 전력", "인공지능 전력", "빅테크", "hyperscaler"),
    "power_market": ("전력수급", "전기본", "전력시장", "전력망", "전기요금", "전력 수요", "전력공급"),
    "finance": (
        "원전금융", "프로젝트 금융", "자금조달", "투자계약", "글로벌원전투자", "민간금융",
        "투자 유치", "eib", "대출", "ppa", "전력구매계약",
    ),
    "security_trade": (
        "원전수출", "수출 계약", "원자력협력", "핵협력", "협력 협정", "에너지안보",
        "공급망", "통상", "제재", "양자협정", "안전조치 협정",
    ),
    "operations": ("원전운영", "설비이용률", "운영효율", "가동중단", "장기운전", "wano", "리튜빙", "설비개선", "개보수"),
    "safety": ("원전안전", "핵안전", "안전사고", "화재", "비상대비", "방사선안전"),
    "decommissioning": ("원전해체", "원전 해체", "해체 작업", "폐로"),
    "workforce": ("원전인력", "원전 인력", "인력증가", "인력동향", "전문인력"),
    "policy_general": ("원자력정책", "미국원자력정책", "미국정책", "원자력확대", "에너지전환", "에너지로드맵", "원자력혁신"),
    "research": ("원자력연구", "연구개발", "r&d", "센서기술", "핵과학", "시험 시설", "기술실증"),
    "applications": ("원자력수소", "원자력 기반 수소", "동위원소", "방사선 활용", "핵 과학 활용"),
}

_COUNTRY_RULES = {
    "KR": ("한국", "대한민국", "한수원", "khnp", "원안위", "고리", "월성", "한울", "신한울", "새울", "영덕", "경주"),
    "US": ("미국", "u.s.", "usa", "nrc", "doe", "백악관", "nasa", "로스앨러모스", "롱비치", "미 공군"),
    "FR": ("프랑스", "edf", "framatome", "오라노", "orano"),
    "UK": ("영국", "rolls-royce", "롤스로이스"),
    "JP": ("일본", "후쿠시마", "alps", "도쿄전력", "tepco"),
    "RU": ("러시아", "rosatom", "로사톰"),
    "CN": ("중국", "cnnc", "cgtn"),
    "EU": ("유럽연합", "european union", "eu 집행위", "유럽위원회"),
    "EU_ETC": (
        "독일", "스페인", "세르비아", "헝가리", "루마니아", "체코", "폴란드", "스웨덴",
        "네덜란드", "핀란드", "슬로바키아", "불가리아", "우크라이나", "벨기에", "이탈리아",
    ),
    "OTHER": ("캐나다", "아르헨티나", "인도", "호주", "브라질", "남아공", "사우디", "uae", "튀르키예"),
}


def load_archive() -> list[dict]:
    records, seen = [], set()
    archive_dir = BOT_DIR / "archive"
    for path in sorted(archive_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            article_hash = record.get("hash")
            if not article_hash or article_hash in seen:
                continue
            seen.add(article_hash)
            records.append(record)
    return records


def load_deliveries() -> dict[str, dict]:
    """기사 hash별 마지막 발송 메타를 읽는다.

    발송일만 배지처럼 사용하지 않고 점수 내역과 함께 보존한다. 동일 기사가 다시
    발송된 경우 마지막 정상 레코드를 사용한다.
    """
    out: dict[str, dict] = {}
    path = BOT_DIR / "delivery_log.jsonl"
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            delivery = json.loads(line)
        except json.JSONDecodeError:
            continue
        article_hash = delivery.get("hash")
        briefing_date = delivery.get("date")
        if article_hash and briefing_date:
            out[article_hash] = delivery
    return out


def region_of(record: dict) -> str:
    scope = (record.get("scope") or "").lower()
    if scope == "kr":
        return "국내"
    if scope == "overseas":
        return "해외"
    domain = (record.get("domain") or "").lower()
    return "국내" if any(hint in domain for hint in _KR_DOMAIN_HINTS) else "해외"


def date_of(record: dict) -> str:
    for key in ("pub", "archived_at"):
        value = record.get(key) or ""
        try:
            return datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return ""


def selection_reasons(delivery: dict | None) -> list[str]:
    """내부 점수 내역을 카드용 설명 배지 최대 2개로 바꾼다."""
    if not delivery:
        return []
    breakdown = delivery.get("breakdown") or {}
    reasons: list[str] = []

    event_rows = []
    for key, value in breakdown.items():
        if not key.startswith("event:"):
            continue
        event = key.split(":", 1)[1]
        label = _EVENT_REASON_LABELS.get(event)
        if label:
            event_rows.append((float(value or 0), label))
    if event_rows:
        reasons.append(max(event_rows)[1])

    if float(breakdown.get("source_tier1") or 0) > 0:
        reasons.append("1차 출처")
    elif float(breakdown.get("korea_relevance") or 0) >= 2.4:
        reasons.append("국내 관련성 높음")
    elif float(breakdown.get("policy_materiality") or 0) >= 2:
        reasons.append("정책 영향 큼")
    elif float(breakdown.get("evidence_strength") or 0) >= 1.6:
        reasons.append("근거 강도 높음")

    if not reasons and delivery.get("score") is not None:
        reasons.append("브리핑 우선순위")
    return list(dict.fromkeys(reasons))[:2]


def _canonical_tag(tag: object) -> str:
    value = str(tag or "").strip().lstrip("#").lower().replace(" ", "")
    return _TAG_ALIASES.get(value, value)


def _taxonomy_text(record: dict) -> str:
    values = [
        record.get("title_kr") or record.get("title") or "",
        record.get("title") or "",
        record.get("summary") or "",
        record.get("implication") or "",
        record.get("section") or "",
        " ".join(str(tag).lstrip("#") for tag in (record.get("tags") or [])),
    ]
    return " ".join(values).lower()


def infer_topics(record: dict) -> tuple[list[str], str]:
    native = [str(topic) for topic in (record.get("topics") or []) if str(topic).strip()]
    if native:
        return list(dict.fromkeys(native))[:3], "native"

    text = _taxonomy_text(record)
    topics = [topic for topic, needles in _TOPIC_RULES.items() if any(needle in text for needle in needles)]

    event_type = ((record.get("features") or {}).get("event_type") or "").strip()
    if event_type == "regulatory_action" and "regulation" not in topics:
        topics.append("regulation")
    if event_type == "incident_safety" and "safety" not in topics:
        topics.append("safety")
    if event_type == "policy_decision" and not topics:
        topics.append("policy_general")
    if event_type == "research_report" and not topics:
        topics.append("research")
    if (record.get("section") or "").lower() == "smr" and "smr" not in topics:
        topics.append("smr")
    return topics[:3], "heuristic-v1" if topics else "unclassified"


def infer_countries(record: dict) -> tuple[list[str], str]:
    native = [str(country) for country in (record.get("countries") or []) if str(country).strip()]
    if native:
        return list(dict.fromkeys(native)), "native"

    text = _taxonomy_text(record)
    countries = [country for country, needles in _COUNTRY_RULES.items() if any(needle in text for needle in needles)]
    if not countries:
        countries = ["KR"] if region_of(record) == "국내" else ["OTHER"]
    return countries, "heuristic-v1"


def _strong_tags(article: dict) -> set[str]:
    tags = set(article.get("canonical_tags") or [])
    if not tags:
        tags = {_canonical_tag(tag) for tag in article.get("tags") or []}
    return {tag for tag in tags if tag and tag not in _GENERIC_TAGS}


def _title_norm(article: dict) -> str:
    title = (article.get("title_kr") or article.get("title") or "").lower()
    return _NORM_RE.sub("", title)


def _tokens(article: dict) -> set[str]:
    text = f"{article.get('title_kr') or article.get('title') or ''} {article.get('summary') or ''}"
    return {token.lower()[:8] for token in _TOKEN_RE.findall(text) if len(token) >= 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def load_embeddings_cache() -> dict[str, list[float]]:
    """원본 봇의 임베딩 캐시를 읽기 전용으로 정규화한다.

    캐시가 없거나 손상됐으면 빈 사전을 반환한다. 프로토타입은 임베딩을 새로
    생성하지 않으므로 API 비용이나 원본 파일 변경이 발생하지 않는다.
    """
    path = Path(os.environ.get("EMBEDDINGS_FILE", BOT_DIR / "embeddings.json"))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    embeddings: dict[str, list[float]] = {}
    for article_hash, payload in raw.items():
        vector = payload.get("vec") if isinstance(payload, dict) else payload
        if isinstance(vector, list) and vector and all(isinstance(value, (int, float)) for value in vector):
            embeddings[str(article_hash)] = [float(value) for value in vector]
    return embeddings


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if not left_norm or not right_norm:
        return None
    return dot / (left_norm * right_norm)


def _facility_signature(article: dict) -> tuple[set[str], set[str]]:
    text = " ".join([
        str(article.get("title_kr") or article.get("title") or ""),
        " ".join(str(tag).lstrip("#") for tag in (article.get("tags") or [])),
    ]).lower()
    plants = {match.group(0).lower() for match in _FACILITY_RE.finditer(text)}
    units = {
        f"{match.group(1).lower()}-{match.group(2)}"
        for match in _UNIT_RE.finditer(text)
    }
    return plants, units


def _facility_conflict(left: dict, right: dict) -> bool:
    left_plants, left_units = _facility_signature(left)
    right_plants, right_units = _facility_signature(right)
    if left_units and right_units:
        left_by_plant = defaultdict(set)
        right_by_plant = defaultdict(set)
        for unit in left_units:
            plant, number = unit.rsplit("-", 1)
            left_by_plant[plant].add(number)
        for unit in right_units:
            plant, number = unit.rsplit("-", 1)
            right_by_plant[plant].add(number)
        for plant in left_by_plant.keys() & right_by_plant.keys():
            if left_by_plant[plant].isdisjoint(right_by_plant[plant]):
                return True
    return bool(left_plants and right_plants and left_plants.isdisjoint(right_plants))


def _country_conflict(left: dict, right: dict) -> bool:
    left_countries = set(left.get("countries") or []) - {"OTHER"}
    right_countries = set(right.get("countries") or []) - {"OTHER"}
    return bool(left_countries and right_countries and left_countries.isdisjoint(right_countries))


def issue_similarity(
    left: dict,
    right: dict,
    embeddings: dict[str, list[float]] | None = None,
) -> tuple[bool, float, dict]:
    """두 기사가 같은 이슈인지 보수적으로 판정한다.

    false merge가 누락보다 해롭기 때문에 넓은 주제 태그 하나만으로는 합치지 않는다.
    반환 진단값은 테스트와 임계값 조정에 사용한다.
    """
    left_title, right_title = _title_norm(left), _title_norm(right)
    title_ratio = (
        difflib.SequenceMatcher(None, left_title, right_title).ratio()
        if left_title and right_title else 0.0
    )
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    token_ratio = _jaccard(left_tokens, right_tokens)
    left_tags, right_tags = _strong_tags(left), _strong_tags(right)
    tag_shared = len(left_tags & right_tags)
    tag_ratio = _jaccard(left_tags, right_tags)
    left_topics, right_topics = set(left.get("topics") or []), set(right.get("topics") or [])
    topic_shared = len(left_topics & right_topics)
    embedding_similarity = cosine_similarity(
        (embeddings or {}).get(str(left.get("hash") or "")),
        (embeddings or {}).get(str(right.get("hash") or "")),
    )
    country_conflict = _country_conflict(left, right)
    facility_conflict = _facility_conflict(left, right)
    blocked_by = []
    if country_conflict:
        blocked_by.append("country_conflict")
    if facility_conflict:
        blocked_by.append("facility_conflict")

    score = 0.55 * title_ratio + 0.25 * token_ratio + 0.20 * tag_ratio
    method = "none"
    matched = False
    if not blocked_by:
        if title_ratio >= 0.78:
            matched, method = True, "title"
        elif tag_shared >= 2 and (title_ratio >= 0.32 or token_ratio >= 0.20):
            matched, method = True, "tags"
        # 구체 태그가 같고 제목 절반 이상이 겹치면 같은 후속 이슈로 본다.
        # 실측 예: "12차 전기본 … 정책 혼선"과
        # "12차 전력수급기본계획 … 정부 부처 간 혼선".
        elif tag_shared >= 1 and title_ratio >= 0.55:
            matched, method = True, "title_tags"
        elif (
            embedding_similarity is not None
            and embedding_similarity >= ISSUE_EMBEDDING_THRESHOLD
            and (tag_shared >= 1 or topic_shared >= 1 or title_ratio >= 0.30)
        ):
            matched, method = True, "embedding"
            score = max(score, embedding_similarity)
    elif blocked_by:
        method = "blocked"

    left_plants, left_units = _facility_signature(left)
    right_plants, right_units = _facility_signature(right)
    return matched, round(score, 4), {
        "title_ratio": round(title_ratio, 4),
        "token_ratio": round(token_ratio, 4),
        "tag_ratio": round(tag_ratio, 4),
        "tag_shared": tag_shared,
        "topic_shared": topic_shared,
        "embedding_similarity": round(embedding_similarity, 4) if embedding_similarity is not None else None,
        "method": method,
        "blocked_by": blocked_by,
        "left_facilities": sorted(left_units or left_plants),
        "right_facilities": sorted(right_units or right_plants),
    }


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _representative_key(article: dict) -> tuple:
    return (
        1 if article.get("importance") == "must_read" else 0,
        float(article.get("selection_score") or 0),
        1 if article.get("source_tier") == 1 else 0,
        len(article.get("summary") or ""),
        article.get("article_date") or "",
    )


def flow_takeaway(direction: object, limit: int = 86) -> str:
    """긴 흐름 해석에서 중간 절단 없이 완결된 첫 문장을 만든다.

    원문 첫 문장이 이미 짧으면 그대로 쓴다. 길면서 쉼표로 사건이 이어질 때는
    첫 절만 취하고 연결 어미를 종결 어미로 바꾼다. 안전하게 종결할 수 없는
    문장은 억지로 자르지 않고 첫 문장 전체를 유지한다.
    """
    text = re.sub(r"\s+", " ", str(direction or "")).strip()
    if not text:
        return ""
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    if len(first_sentence) <= limit:
        return first_sentence

    first_clause = re.split(r"[,;]\s*", first_sentence, maxsplit=1)[0].strip()
    if not first_clause or len(first_clause) < 28:
        return first_sentence

    ending_rules = (
        (r"고 있으며$", "고 있습니다."),
        (r"해 왔으며$", "해 왔습니다."),
        (r"했으며$", "했습니다."),
        (r"됐으며$", "됐습니다."),
        (r"였으며$", "였습니다."),
        (r"이며$", "입니다."),
        (r"하고$", "했습니다."),
        (r"되고$", "됐습니다."),
        (r"되어$", "됐습니다."),
    )
    for pattern, replacement in ending_rules:
        if re.search(pattern, first_clause):
            completed = re.sub(pattern, replacement, first_clause)
            return completed if len(completed) <= limit else first_sentence
    return first_sentence


def _evidence_overlap(left: dict, right: dict) -> float:
    left_hashes = {row.get("hash") for row in left.get("evidence", []) if row.get("hash")}
    right_hashes = {row.get("hash") for row in right.get("evidence", []) if row.get("hash")}
    if not left_hashes or not right_hashes:
        return 0.0
    return len(left_hashes & right_hashes) / min(len(left_hashes), len(right_hashes))


def _insight_signal_score(item: dict) -> float:
    current = float(item.get("count_now") or 0)
    previous = float(item.get("count_prev") or 0)
    evidence_count = len(item.get("evidence") or [])
    return current + max(0.0, current - previous) * 0.6 + min(evidence_count, 6) * 0.15


def select_featured_insights(items: list[dict], limit: int = 3) -> list[dict]:
    """강도·근거 중복·국내외 커버리지를 함께 보는 주간 대표 흐름 선택."""
    candidates = [item for item in items if item.get("direction") and item.get("evidence")]
    selected: list[dict] = []
    covered_regions: set[str] = set()

    while candidates and len(selected) < limit:
        def adjusted(item: dict) -> tuple:
            regions = set(item.get("evidence_regions") or [])
            new_regions = regions - covered_regions
            region_bonus = 2.5 * len(new_regions) if selected else 0.0
            if "해외" in new_regions and item.get("region_scope") == "해외":
                region_bonus += 0.35
            redundancy = max((_evidence_overlap(item, other) for other in selected), default=0.0)
            score = _insight_signal_score(item) + region_bonus - 6.0 * redundancy
            return (score, _insight_signal_score(item), item.get("keyword") or "")

        best = max(candidates, key=adjusted)
        candidates.remove(best)
        selected.append(best)
        covered_regions.update(best.get("evidence_regions") or [])
    return selected


def prepare_insights(insights: dict, news_items: list[dict]) -> dict:
    """흐름 근거에 지역 메타를 붙이고 다양화된 대표 3개를 만든다."""
    by_hash = {item["hash"]: item for item in news_items}
    items = []
    for raw_item in insights.get("items", []):
        item = dict(raw_item)
        evidence = []
        seen = set()
        for raw_evidence in item.get("evidence") or []:
            article_hash = raw_evidence.get("hash")
            if not article_hash or article_hash in seen:
                continue
            seen.add(article_hash)
            article = by_hash.get(article_hash) or {}
            evidence.append({
                **raw_evidence,
                "region": article.get("region", ""),
                "countries": article.get("countries") or [],
                "topics": article.get("topics") or [],
                "publisher": article.get("publisher", ""),
                "domain": article.get("domain", ""),
            })
        regions = {row["region"] for row in evidence if row.get("region") in {"국내", "해외"}}
        item["evidence"] = evidence
        item["evidence_regions"] = sorted(regions, key=lambda value: (value != "국내", value))
        item["domestic_evidence_count"] = sum(1 for row in evidence if row.get("region") == "국내")
        item["overseas_evidence_count"] = sum(1 for row in evidence if row.get("region") == "해외")
        item["region_scope"] = (
            "국내·해외" if regions == {"국내", "해외"}
            else next(iter(regions), "범위 미분류")
        )
        item["takeaway"] = flow_takeaway(item.get("direction"))
        item["signal_score"] = round(_insight_signal_score(item), 3)
        items.append(item)

    prepared = dict(insights)
    prepared["items"] = items
    prepared["featured_items"] = select_featured_insights(items)
    prepared["selection_method"] = "signal-region-evidence-diversity-v1"
    return prepared


def cluster_selected_articles(
    news_items: list[dict],
    embeddings: dict[str, list[float]] | None = None,
) -> list[dict]:
    """발송된 기사들을 최근 이슈 묶음으로 연결한다.

    issue_id는 최초 기사 hash에서 만들어 안정적으로 유지한다. 대표 기사는 더 좋은
    출처나 중요 기사로 바뀔 수 있지만 issue_id는 바뀌지 않는다.
    """
    selected = [item for item in news_items if item.get("briefing_date")]
    selected.sort(key=lambda item: (item["briefing_date"], item["article_date"], item["hash"]))
    issues: list[dict] = []

    for article in selected:
        article_day = _parse_day(article.get("briefing_date", ""))
        best_issue = None
        best_score = -1.0
        best_diag = None

        for issue in issues:
            last_day = _parse_day(issue["last_seen"])
            if article_day and last_day and (article_day - last_day).days > ISSUE_WINDOW_DAYS:
                continue
            # 대표 기사 한 건만 보면 표현이 단계적으로 바뀌는 A→B→C 후속 보도가
            # 끊길 수 있다. 최근 기사 3건 중 가장 가까운 연결을 사용한다.
            for reference in issue["members"][-3:]:
                matched, score, diag = issue_similarity(article, reference, embeddings)
                if matched and score > best_score:
                    best_issue, best_score = issue, score
                    best_diag = {**diag, "reference_hash": reference["hash"]}

        if best_issue is None:
            issues.append({
                "issue_id": f"issue-{article['hash']}",
                "first_seen": article["briefing_date"],
                "last_seen": article["briefing_date"],
                "representative": article,
                "members": [article],
                "match_diagnostics": [],
            })
            continue

        best_issue["members"].append(article)
        best_issue["last_seen"] = article["briefing_date"]
        best_issue["match_diagnostics"].append({
            "hash": article["hash"],
            "score": best_score,
            **(best_diag or {}),
        })
        if _representative_key(article) > _representative_key(best_issue["representative"]):
            best_issue["representative"] = article

    return issues


def _article_view(article: dict) -> dict:
    return {
        "hash": article["hash"],
        "article_date": article["article_date"],
        "briefing_date": article.get("briefing_date"),
        "title_kr": article["title_kr"],
        "summary": article.get("summary", ""),
        "domain": article.get("domain", ""),
        "publisher": article.get("publisher", ""),
        "region": article.get("region", ""),
        "countries": article.get("countries") or [],
        "topics": article.get("topics") or [],
        "url": article.get("url", ""),
        "importance": article.get("importance", ""),
    }


def build_briefings(news_items: list[dict], issues: list[dict]) -> list[dict]:
    dates = sorted({item["briefing_date"] for item in news_items if item.get("briefing_date")}, reverse=True)
    briefings = []

    for briefing_date in dates:
        current_articles = [item for item in news_items if item.get("briefing_date") == briefing_date]
        issue_rows = []
        for issue in issues:
            current = [member for member in issue["members"] if member["briefing_date"] == briefing_date]
            if not current:
                continue
            history = [member for member in issue["members"] if member["briefing_date"] < briefing_date]
            representative = max(current, key=_representative_key)
            regions = {member.get("region") for member in current if member.get("region")}
            reasons = []
            for member in sorted(current, key=_representative_key, reverse=True):
                reasons.extend(member.get("selection_reasons") or [])

            topic_counts = Counter(
                topic for member in history + current for topic in (member.get("topics") or [])
            )
            tag_counts = Counter(
                tag for member in history + current for tag in (member.get("canonical_tags") or [])
                if tag not in _GENERIC_TAGS
            )

            timeline = sorted(history + current,
                              key=lambda member: (member["article_date"], member["briefing_date"], member["hash"]),
                              reverse=True)
            tracked_briefings = len({member["briefing_date"] for member in timeline})
            issue_rows.append({
                "issue_id": issue["issue_id"],
                "status": "ongoing" if history else "new",
                "first_seen": issue["first_seen"],
                "last_seen": briefing_date,
                "title": representative["title_kr"],
                "summary": representative.get("summary", ""),
                "implication": representative.get("implication") or representative.get("why_important") or "",
                "region": "국내·해외" if len(regions) > 1 else next(iter(regions), ""),
                "importance": representative.get("importance", ""),
                "selection_reasons": list(dict.fromkeys(reasons))[:2],
                "topics": [topic for topic, _ in topic_counts.most_common(3)],
                "tags": [tag for tag, _ in tag_counts.most_common(6)],
                "current_article_count": len(current),
                "previous_article_count": len(history),
                "tracked_briefings": tracked_briefings,
                "article_count": len(timeline),
                "representative_article": _article_view(representative),
                "related_articles": [_article_view(member) for member in timeline],
                "sort_score": float(representative.get("selection_score") or 0),
            })

        issue_rows.sort(
            key=lambda row: (row["importance"] == "must_read", row["sort_score"], row["last_seen"]),
            reverse=True,
        )
        for row in issue_rows:
            row.pop("sort_score", None)

        briefings.append({
            "date": briefing_date,
            "article_count": len(current_articles),
            "issue_count": len(issue_rows),
            "domestic_count": sum(1 for item in current_articles if item.get("region") == "국내"),
            "overseas_count": sum(1 for item in current_articles if item.get("region") == "해외"),
            "highlights": [row["title"] for row in issue_rows[:3]],
            "issues": issue_rows,
        })
    return briefings


def build_issue_catalog(issues: list[dict], latest_briefing_date: str) -> list[dict]:
    latest_day = _parse_day(latest_briefing_date)
    rows = []
    for issue in issues:
        timeline = sorted(
            issue["members"],
            key=lambda member: (member["article_date"], member["briefing_date"], member["hash"]),
            reverse=True,
        )
        briefing_dates = sorted({member["briefing_date"] for member in timeline})
        last_seen = max(briefing_dates)
        current = [member for member in timeline if member["briefing_date"] == last_seen]
        history = [member for member in timeline if member["briefing_date"] < last_seen]
        representative = max(current, key=_representative_key)
        regions = {member.get("region") for member in timeline if member.get("region")}
        topic_counts = Counter(topic for member in timeline for topic in (member.get("topics") or []))
        tag_counts = Counter(
            tag for member in timeline for tag in (member.get("canonical_tags") or [])
            if tag not in _GENERIC_TAGS
        )
        reasons = []
        for member in sorted(timeline, key=_representative_key, reverse=True):
            reasons.extend(member.get("selection_reasons") or [])
        last_day = _parse_day(last_seen)
        days_since_update = (
            (latest_day - last_day).days if latest_day and last_day else None
        )
        rows.append({
            "issue_id": issue["issue_id"],
            "status": "ongoing" if len(briefing_dates) > 1 else "new",
            "lifecycle": "active" if days_since_update is not None and days_since_update <= 7 else "quiet",
            "days_since_update": days_since_update,
            "first_seen": min(briefing_dates),
            "last_seen": last_seen,
            "title": representative["title_kr"],
            "summary": representative.get("summary", ""),
            "implication": representative.get("implication") or representative.get("why_important") or "",
            "region": "국내·해외" if len(regions) > 1 else next(iter(regions), ""),
            "regions": sorted(regions),
            "importance": representative.get("importance", ""),
            "selection_reasons": list(dict.fromkeys(reasons))[:2],
            "topics": [topic for topic, _ in topic_counts.most_common(3)],
            "tags": [tag for tag, _ in tag_counts.most_common(8)],
            "current_article_count": len(current),
            "previous_article_count": len(history),
            "tracked_briefings": len(briefing_dates),
            "briefing_count": len(briefing_dates),
            "article_count": len(timeline),
            "representative_article": _article_view(representative),
            "related_articles": [_article_view(member) for member in timeline],
            "sort_score": float(representative.get("selection_score") or 0),
        })
    rows.sort(
        key=lambda row: (row["last_seen"], row["importance"] == "must_read", row["sort_score"], row["article_count"]),
        reverse=True,
    )
    for row in rows:
        row.pop("sort_score", None)
    return rows


def build() -> None:
    records = load_archive()
    deliveries = load_deliveries()
    now = datetime.now(KST)
    cutoff_news = (now - timedelta(days=NEWS_WINDOW_DAYS)).strftime("%Y-%m-%d")

    visible = []
    for record in records:
        importance = record.get("importance", "")
        if importance == "noise" or (importance == "market" and not SHOW_MARKET):
            continue
        article_date = date_of(record)
        if not article_date:
            continue
        delivery = deliveries.get(record.get("hash", ""))
        topics, topic_source = infer_topics(record)
        countries, country_source = infer_countries(record)
        canonical_tags = list(dict.fromkeys(
            _canonical_tag(tag) for tag in (record.get("tags") or []) if _canonical_tag(tag)
        ))
        visible.append({
            "hash": record.get("hash", ""),
            "date": article_date,
            "article_date": article_date,
            "briefing_date": delivery.get("date") if delivery else None,
            "region": region_of(record),
            "importance": importance,
            "section": record.get("section", ""),
            "category": record.get("category", ""),
            "title_kr": record.get("title_kr") or record.get("title", ""),
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "implication": record.get("implication", ""),
            "why_important": record.get("why_important", ""),
            "tags": record.get("tags") or [],
            "canonical_tags": canonical_tags,
            "topics": topics,
            "countries": countries,
            "topic_source": topic_source,
            "country_source": country_source,
            "features": record.get("features") or {},
            "article_type": record.get("article_type", ""),
            "url": record.get("url", ""),
            "domain": record.get("domain", ""),
            "publisher": record.get("publisher", ""),
            "source_tier": record.get("source_tier"),
            "selection_score": delivery.get("score") if delivery else None,
            "selection_reasons": selection_reasons(delivery),
            # 기존 프론트와의 호환용. 새 화면은 briefing_date를 사용한다.
            "promoted": delivery.get("date") if delivery else None,
        })
    visible.sort(key=lambda item: (item["article_date"], item.get("briefing_date") or ""), reverse=True)
    news_items = [item for item in visible if item["article_date"] >= cutoff_news]

    embeddings = load_embeddings_cache()
    issues = cluster_selected_articles(news_items, embeddings)
    briefings = build_briefings(news_items, issues)
    issue_catalog = build_issue_catalog(
        issues,
        briefings[0]["date"] if briefings else "",
    )

    # 트렌드는 기존 집계를 유지하되 커버리지가 낮으면 프론트에서 숨길 수 있게 메타를 제공한다.
    trend_pool = news_items
    day7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    day14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    day30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    tags_7, tags_prev7, tags_30, tags_all_before7 = Counter(), Counter(), Counter(), Counter()
    topic_by_week: dict[str, Counter] = defaultdict(Counter)
    country_30 = Counter()
    for record in trend_pool:
        record_date = record.get("article_date", "")
        if not record_date:
            continue
        tags = list(record.get("canonical_tags") or [])
        tags = [tag for tag in tags if tag]
        if record_date >= day7:
            tags_7.update(tags)
        elif record_date >= day14:
            tags_prev7.update(tags)
        if record_date >= day30:
            tags_30.update(tags)
            country_30.update(record.get("countries") or [])
        if record_date < day7:
            tags_all_before7.update(tags)
        iso = datetime.strptime(record_date, "%Y-%m-%d").isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
        for topic in record.get("topics") or []:
            topic_by_week[week][topic] += 1

    rising = []
    for tag, count in tags_7.items():
        previous = tags_prev7.get(tag, 0)
        if count >= 3 and count > previous:
            rising.append({"tag": tag, "now": count, "prev": previous})
    rising.sort(key=lambda item: (item["now"] - item["prev"], item["now"]), reverse=True)

    new_tags = [
        {"tag": tag, "count": count}
        for tag, count in tags_7.most_common()
        if tag not in tags_all_before7 and count >= 2
    ]

    weeks = sorted(topic_by_week.keys())[-8:]
    topic_totals = Counter()
    for week in weeks:
        topic_totals.update(topic_by_week[week])
    topic_series = {
        topic: [topic_by_week[week].get(topic, 0) for week in weeks]
        for topic, _ in topic_totals.most_common(6)
    }

    trend = {
        "top_tags_7d": [{"tag": tag, "count": count} for tag, count in tags_7.most_common(10)],
        "top_tags_30d": [{"tag": tag, "count": count} for tag, count in tags_30.most_common(10)],
        "rising": rising[:10],
        "new_tags": new_tags[:10],
        "countries_30d": [{"country": country, "count": count} for country, count in country_30.most_common(10)],
        "weeks": weeks,
        "topic_series": topic_series,
    }

    topic_coverage = (sum(1 for item in news_items if item["topics"]) / len(news_items)) if news_items else 0
    country_coverage = (sum(1 for item in news_items if item["countries"]) / len(news_items)) if news_items else 0
    heuristic_topic_count = sum(1 for item in news_items if item["topic_source"] == "heuristic-v1")
    heuristic_country_count = sum(1 for item in news_items if item["country_source"] == "heuristic-v1")
    selected_items = [item for item in news_items if item.get("briefing_date")]
    embedded_selected_count = sum(1 for item in selected_items if item["hash"] in embeddings)
    match_methods = Counter(
        diag.get("method", "none")
        for issue in issues
        for diag in issue.get("match_diagnostics", [])
    )
    cross_date_issue_count = sum(
        1 for issue in issues
        if len({member["briefing_date"] for member in issue["members"]}) > 1
    )
    meta = {
        "generation_id": GENERATION_ID,
        "generated_at": now.isoformat(),
        "archive_total": len(records),
        "visible_total": len(news_items),
        "briefing_total": len(briefings),
        "issue_catalog_total": len(issue_catalog),
        "latest_briefing_date": briefings[0]["date"] if briefings else "",
        "date_min": min((item["article_date"] for item in visible), default=""),
        "date_max": max((item["article_date"] for item in visible), default=""),
        "importance_counts": dict(Counter(record.get("importance", "") for record in records)),
        "topic_coverage": round(topic_coverage, 4),
        "country_coverage": round(country_coverage, 4),
        "taxonomy_version": "prototype-heuristic-v1",
        "heuristic_topic_count": heuristic_topic_count,
        "heuristic_country_count": heuristic_country_count,
        "trend_ready": topic_coverage >= 0.8 and country_coverage >= 0.8 and len(weeks) >= 2,
        "issue_matching_version": "hybrid-guarded-v2",
        "issue_window_days": ISSUE_WINDOW_DAYS,
        "embedding_cache_entries": len(embeddings),
        "embedding_selected_count": embedded_selected_count,
        "embedding_selected_coverage": round(
            embedded_selected_count / len(selected_items), 4
        ) if selected_items else 0,
        "issue_match_methods": dict(match_methods),
        "cross_date_issue_count": cross_date_issue_count,
    }

    insights_path = BOT_DIR / "trend_insights.json"
    try:
        insights = json.loads(insights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        insights = {"generated_at": "", "items": []}
    insights = prepare_insights(insights, news_items)

    issue_audit = {
        "generated_at": now.isoformat(),
        "matching_version": "hybrid-guarded-v2",
        "embedding_threshold": ISSUE_EMBEDDING_THRESHOLD,
        "embedding_cache_entries": len(embeddings),
        "clusters": [
            {
                "issue_id": issue["issue_id"],
                "first_seen": issue["first_seen"],
                "last_seen": issue["last_seen"],
                "briefing_dates": sorted({member["briefing_date"] for member in issue["members"]}),
                "members": [
                    {
                        "hash": member["hash"],
                        "briefing_date": member["briefing_date"],
                        "article_date": member["article_date"],
                        "title": member["title_kr"],
                        "countries": member.get("countries") or [],
                        "facilities": sorted(set().union(*_facility_signature(member))),
                    }
                    for member in issue["members"]
                ],
                "matches": issue.get("match_diagnostics", []),
            }
            for issue in issues if len(issue["members"]) > 1
        ],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = (
        ("news.json", news_items),
        ("briefings.json", briefings),
        ("issues.json", issue_catalog),
        ("trend.json", trend),
        ("meta.json", meta),
        ("insights.json", insights),
        ("issue_audit.json", issue_audit),
    )
    for name, payload in outputs:
        (OUT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    selected_count = sum(briefing["article_count"] for briefing in briefings)
    issue_count = sum(briefing["issue_count"] for briefing in briefings)
    print(
        f"[build] 아카이브 {len(records)}건 → 표시 {len(news_items)}건 → "
        f"브리핑 기사 {selected_count}건 / 이슈 카드 {issue_count}개 → {OUT_DIR}"
    )


if __name__ == "__main__":
    build()
