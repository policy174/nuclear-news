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
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape as html_escape
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_quality import (  # noqa: E402
    curation_errors,
    invalid_url_reason,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    split_title_publisher,
    title_key,
)
from embedding_pipeline import EMBEDDING_MODEL, cached_vector  # noqa: E402
import issue_review  # noqa: E402
import keei_match  # noqa: E402

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
ISSUE_EMBEDDING_THRESHOLD = 0.92
ISSUE_EMBEDDING_CANDIDATE_THRESHOLD = 0.70
LOCAL_EMBEDDING_CANDIDATE_THRESHOLD = 0.18
LOCAL_EMBEDDING_DIMENSION = 1024
MATCH_OVERRIDES_FILE = BOT_DIR / "issue_match_overrides.json"
SITE_URL = os.environ.get("SITE_URL", "https://nuclens.pages.dev").rstrip("/")
KST = timezone(timedelta(hours=9))

# 히어로 h1과 변화 문장의 하드 상한. 넘기면 카드가 아니라 문단이 된다.
# 70자는 1280px 히어로에서 두 줄. 요약이 이보다 길면 이슈 제목으로 넘어간다.
HEADLINE_LIMIT = 70
CHANGE_LINE_LIMIT = 140

# 라벨은 판정이 아니라 사실 진술이다. 단일 출처 보도는 결함이 아니라 흔한 정상
# 상태(실측 84%)라서 '일부 확인' 같은 부정 프레이밍을 쓰지 않는다.
VERIFICATION_LABELS = {
    "official": "공식 확인",
    "corroborated": "복수 출처 확인",
    "partial": "단일 출처",
    "unverified": "확인 중",
}

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

# 국가 코드는 ISO 3166-1 alpha-2를 쓴다. 기업 국적이 아니라 실제 정책 관할,
# 사업 부지, 사건 무대가 텍스트에 드러나는 경우만 추론한다.
_COUNTRY_RULES = {
    "KR": ("한국", "대한민국", "한수원", "khnp", "원안위", "고리", "월성", "한울", "신한울", "새울", "영덕", "경주"),
    "US": (
        "미국", "u.s.", "united states", "미 에너지부", "미 원자력규제위원회", "백악관",
        "로스앨러모스", "패듀카", "사바나강", "오이스터크릭", "화이트메사", "샌디아",
        "텍사스", "버지니아", "아이다호",
    ),
    "CA": ("캐나다", "온타리오", "서스캐처원", "브루스 파워", "달링턴"),
    "FR": ("프랑스", "플라망빌", "팔리", "마르쿨", "카다라슈"),
    "GB": ("영국", "united kingdom", "잉글랜드", "스코틀랜드", "웨일스", "사이즈웰", "힝클리", "헤이샴", "하틀풀"),
    "DE": ("독일", "germany", "도이칠란트", "막스 플랑크", "벤델슈타인"),
    "ES": ("스페인", "spain"),
    "RS": ("세르비아", "serbia"),
    "HU": ("헝가리", "hungary", "팍스 원전"),
    "RO": ("루마니아", "romania", "체르나보다"),
    "CZ": ("체코", "czech", "두코바니", "테멜린"),
    "PL": ("폴란드", "poland"),
    "SE": ("스웨덴", "sweden"),
    "NL": ("네덜란드", "netherlands", "보르셀레"),
    "FI": ("핀란드", "finland", "올킬루오토"),
    "SK": ("슬로바키아", "slovakia", "모호프체"),
    "BG": ("불가리아", "bulgaria", "코즐로두이"),
    "UA": ("우크라이나", "ukraine", "자포리자"),
    "BE": ("벨기에", "belgium"),
    "IT": ("이탈리아", "italy"),
    "PT": ("포르투갈", "portugal"),
    "CH": ("스위스", "switzerland"),
    "NO": ("노르웨이", "norway"),
    "DK": ("덴마크", "denmark"),
    "JP": ("일본", "후쿠시마", "도쿄전력", "tepco"),
    "RU": ("러시아", "russia"),
    "CN": ("중국", "china"),
    "AR": ("아르헨티나", "argentina", "아투차"),
    "IN": ("인도", "india"),
    "AU": ("호주", "australia"),
    "BR": ("브라질", "brazil"),
    "ZA": ("남아공", "남아프리카공화국", "south africa"),
    "SA": ("사우디", "saudi arabia"),
    "AE": ("아랍에미리트", "uae", "바라카"),
    "TR": ("튀르키예", "터키", "turkey", "아쿠유"),
    "KZ": ("카자흐스탄", "kazakhstan"),
    "UZ": ("우즈베키스탄", "uzbekistan"),
}
_EU_INSTITUTION_RULES = (
    "유럽연합", "european union", "eu 집행위", "eu 집행위원회", "유럽위원회",
    "유럽의회", "european commission", "european parliament", "euratom",
)
_EUROPE_REGION_RULES = ("유럽", "범유럽", "europe-wide", "pan-european")
_GLOBAL_RULES = (
    "글로벌", "전 세계", "세계 원자력", "세계원자력", "국제원자력기구", "iaea",
    "world nuclear association", "세계은행", "oecd/nea",
)
_EUROPEAN_COUNTRY_CODES = {
    "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE",
    "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
    "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RS", "SK",
    "SI", "ES", "SE", "CH", "UA", "GB",
}
_COUNTRY_ALIASES = {"UK": "GB"}
_LEGACY_COUNTRY_BUCKETS = {"EU_ETC", "OTHER"}
_COUNTRY_TOKEN_RULES = {
    "US": ("nrc", "doe", "pjm", "inl", "llnl", "inpo"),
}
_GLOBAL_TOKEN_RULES = ("iter",)


def _normalize_archive_record(record: dict) -> dict:
    """구버전 레코드를 웹 빌드의 현재 출처·사건일 계약으로 읽는다."""
    normalized = dict(record)
    normalized["url"] = normalize_url(record.get("url"))
    title = record.get("title") or ""
    publisher = record.get("publisher") or ""
    domain = record.get("domain") or ""
    if ("news.google." in domain or "news.google." in normalized["url"]) and not publisher:
        title, publisher = split_title_publisher(title)
    profile = source_profile(domain, publisher)
    normalized.update({
        "title": title,
        "publisher": publisher or profile["publisher"],
        "source_type": record.get("source_type") or profile["source_type"],
        "evidence_role": record.get("evidence_role") or profile["evidence_role"],
        "source_tier": record.get("source_tier") or profile["source_tier"],
    })
    normalized.update(normalize_event_date_fields(record))
    return normalized


def validate_archive_records(records: list[dict]) -> None:
    """중복·오류 URL·불완전 문장이 있으면 배포 빌드를 중단한다."""
    errors: list[str] = []
    seen_urls: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    for record in records:
        article_hash = record.get("hash") or "(no-hash)"
        url = record.get("url") or ""
        url_error = invalid_url_reason(url)
        if url_error:
            errors.append(f"{article_hash}:url:{url_error}")
        elif url in seen_urls:
            errors.append(f"{article_hash}:duplicate_url:{seen_urls[url]}")
        else:
            seen_urls[url] = article_hash

        normalized_title = title_key(record.get("title"))
        if normalized_title and normalized_title in seen_titles:
            errors.append(f"{article_hash}:duplicate_title:{seen_titles[normalized_title]}")
        elif normalized_title:
            seen_titles[normalized_title] = article_hash

        if record.get("source_tier") not in {1, 2, 3}:
            errors.append(f"{article_hash}:source_tier:missing")
        if not record.get("publisher"):
            errors.append(f"{article_hash}:publisher:missing")
        if record.get("importance") != "noise":
            # v1 아카이브의 완결문은 최대 120자를 허용하되 신규 생성기는 80자
            # 게이트를 적용한다. 과거 문장을 잘라 맞추는 데이터 훼손을 피한다.
            errors.extend(
                f"{article_hash}:{error}"
                for error in curation_errors(record, summary_limit=120)
            )

    if errors:
        preview = " | ".join(errors[:20])
        raise ValueError(f"data quality gate failed ({len(errors)}): {preview}")


def load_archive() -> list[dict]:
    records = []
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
            if not article_hash:
                continue
            records.append(_normalize_archive_record(record))
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
        # record_type 이 붙은 줄은 기사가 아니라 부가 레코드(selection_stats).
        if delivery.get("record_type"):
            continue
        article_hash = delivery.get("hash")
        briefing_date = delivery.get("date")
        if article_hash and briefing_date:
            out[article_hash] = delivery
    return out


# 선정 통계는 hash 가 없어 (date, hash) 멱등이 안 걸린다. 워크플로 재실행이 같은
# 날짜에 여러 줄을 남기므로 읽는 쪽에서 하나를 고른다.
#   ① pipeline_status 가 좋은 것 우선 (실패한 재실행이 정상 기록을 덮지 않게)
#   ② 같은 등급이면 generated_at 이 늦은 것
_PIPELINE_RANK = {"ok": 3, "partial": 2, "error": 1}


def pick_selection_stats(rows: list[dict]) -> dict[str, dict]:
    """날짜 → 그날의 대표 selection_stats 레코드."""
    best: dict[str, dict] = {}
    for row in rows:
        if row.get("record_type") != "selection_stats":
            continue
        day = row.get("date") or ""
        if not day:
            continue
        current = best.get(day)
        if current is None or _stats_key(row) > _stats_key(current):
            best[day] = row
    return best


def _stats_key(row: dict) -> tuple:
    return (_PIPELINE_RANK.get(row.get("pipeline_status") or "", 0),
            row.get("generated_at") or "")


# 상태 판정은 두 개의 독립 heartbeat 로 한다.
#
#   수집기      = 아카이브 최신 archived_at (crawl 이 매시간 append — 선정과 무관)
#   브리핑 파이프라인 = selection_stats.generated_at + pipeline_status
#
# "최신 기사 날짜"만 보고 판정하면 안 된다. 선정 하한을 도입한 뒤에는 며칠간 새
# 브리핑 항목이 없는 게 정상일 수 있고, 그걸 장애로 표시하면 컷오프 도입의 취지가
# 무너진다. **콘텐츠가 없는 것과 프로세스가 안 돈 것은 별개다.**
COLLECTOR_STALE_HOURS = 6      # crawl 은 매시간 — 6시간이면 확실히 멈춘 것
BRIEFING_STALE_HOURS = 36      # daily-brief 는 하루 1회 — 36시간이면 한 회차를 건너뛴 것


def _latest_archive_stamp(records: list[dict]) -> str:
    stamps = [str(r.get("archived_at") or "") for r in records if r.get("archived_at")]
    return max(stamps) if stamps else ""


def _hours_since(stamp: str, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 3600.0


def system_status(records: list[dict], selection_stats: dict, now: datetime) -> dict:
    """status.json 본문. app.js renderSystemStatus 가 이 계약을 이미 렌더한다."""
    ok_days = {day: row for day, row in selection_stats.items()
               if row.get("pipeline_status") == "ok"}
    last_ok = max((row.get("generated_at") or "" for row in ok_days.values()),
                  default="")
    latest_brief = max(selection_stats) if selection_stats else ""
    latest_row = selection_stats.get(latest_brief) or {}

    collector_age = _hours_since(_latest_archive_stamp(records), now)
    briefing_age = _hours_since(latest_row.get("generated_at") or "", now)

    state, message, watcher = "ok", "", True

    if collector_age is not None and collector_age > COLLECTOR_STALE_HOURS:
        state, watcher = "error", False
        message = f"수집이 {collector_age:.0f}시간째 멈춰 있습니다"
    elif latest_row.get("pipeline_status") == "error":
        state = "error"
        message = "브리핑 선정이 실패했습니다"
    elif briefing_age is None and selection_stats:
        watcher = False
        message = "브리핑 실행 기록을 찾지 못했습니다"
    elif briefing_age is not None and briefing_age > BRIEFING_STALE_HOURS:
        watcher = False
        message = f"브리핑이 {briefing_age / 24:.0f}일째 갱신되지 않았습니다"
    elif latest_row.get("pipeline_status") == "partial":
        message = "브리핑 일부가 발송되지 않았습니다"

    return {
        "state": state,
        # 마지막 '정상 브리핑' 시각. 빌드 시각이 아니다 — 빌드는 실패한 날에도 돈다.
        # 통계가 아직 없는 구간(기능 도입 직후)에서는 수집 시각으로 내려간다.
        "last_success_at": last_ok or _latest_archive_stamp(records) or now.isoformat(),
        "watcher_running": watcher,
        "message": message,
        "collector_stamp": _latest_archive_stamp(records),
        "briefing_date": latest_brief,
    }


def load_selection_stats() -> dict[str, dict]:
    path = BOT_DIR / "delivery_log.jsonl"
    if not path.exists():
        return {}
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return pick_selection_stats(rows)


def infer_region(record: dict, countries: list[str] | None = None) -> tuple[str, str]:
    """기사의 대상 지역을 수집 경로가 아니라 기사 내용 기준으로 정규화한다.

    명시적인 scope가 있으면 우선 사용한다. 그 외에는 국가 태그를 우선하고,
    국가를 특정하지 못한 경우에만 section과 domain을 보조 신호로 사용한다.
    Google News 한국 도메인에 실린 해외 기사까지 국내로 잡히던 오류를 막는다.
    """
    scope = (record.get("scope") or "").lower()
    if scope == "kr":
        return "국내", "scope"
    if scope == "overseas":
        return "해외", "scope"

    confident_countries = {
        str(country).strip().upper()
        for country in (countries or [])
        if str(country).strip().upper() not in {"", "OTHER"}
    }
    if confident_countries:
        return ("국내" if "KR" in confident_countries else "해외"), "countries"

    section = (record.get("section") or "").lower()
    if section in {"domestic", "khnp"}:
        return "국내", "section"
    if section in {"international", "overseas", "global"}:
        return "해외", "section"

    domain = (record.get("domain") or "").lower()
    return (
        "국내" if any(hint in domain for hint in _KR_DOMAIN_HINTS) else "해외",
        "domain",
    )


def region_of(record: dict, countries: list[str] | None = None) -> str:
    return infer_region(record, countries)[0]


def date_of(record: dict) -> str:
    for key in ("pub", "archived_at"):
        value = record.get(key) or ""
        try:
            return datetime.fromisoformat(value).astimezone(KST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return ""


def selection_reasons(delivery: dict | None, source: dict | None = None) -> list[str]:
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

    if source and source.get("evidence_role") == "primary":
        reasons.append("공식 원문")
    elif source and source.get("source_type") == "specialist_media" and float(
        breakdown.get("source_tier1") or 0
    ) > 0:
        reasons.append("전문 매체")
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


def _country_scopes_from_text(text: str) -> list[str]:
    """텍스트에서 국가와 명시적 지역 범위를 서로 다른 축으로 판정한다."""
    concrete = [
        country
        for country, needles in _COUNTRY_RULES.items()
        if any(needle in text for needle in needles)
        or any(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
            for token in _COUNTRY_TOKEN_RULES.get(country, ())
        )
    ]
    if len(concrete) > 2:
        # 0~2개 스키마에서 임의의 두 국가만 남기지 않는다. 유럽 국가만으로 된
        # 다국가 기사면 지리적 유럽, 그 밖의 다국가 기사면 글로벌로 올린다.
        scopes = [
            "EUROPE" if set(concrete).issubset(_EUROPEAN_COUNTRY_CODES) else "GLOBAL"
        ]
    else:
        scopes = concrete

    if any(needle in text for needle in _EU_INSTITUTION_RULES):
        scopes.append("EU")
    if scopes:
        return list(dict.fromkeys(scopes))[:2]
    if any(needle in text for needle in _EUROPE_REGION_RULES):
        return ["EUROPE"]
    if any(needle in text for needle in _GLOBAL_RULES) or any(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text)
        for token in _GLOBAL_TOKEN_RULES
    ):
        return ["GLOBAL"]
    return []


def infer_countries(record: dict) -> tuple[list[str], str]:
    text = _taxonomy_text(record)
    raw_native = [
        str(country).strip().upper()
        for country in (record.get("countries") or [])
        if str(country).strip()
    ]
    if raw_native:
        has_legacy_bucket = bool(set(raw_native) & _LEGACY_COUNTRY_BUCKETS)
        normalized = [_COUNTRY_ALIASES.get(country, country) for country in raw_native]

        # EU_ETC/OTHER는 과거의 모호한 묶음이다. 기존 동반 태그까지 신뢰하지 않고
        # 제목·요약의 실제 관할/부지를 기준으로 전체 범위를 다시 판정한다.
        if has_legacy_bucket:
            refined = _country_scopes_from_text(text)
            return (refined or ["UNSPECIFIED"]), "legacy-refined-v2"

        # 과거 EU 태그가 단순한 '유럽' 기사에도 쓰였다. EU 기관·공동정책이
        # 명시되지 않으면 국가 또는 지리적 EUROPE 범위로 바로잡는다.
        if "EU" in normalized and not any(needle in text for needle in _EU_INSTITUTION_RULES):
            concrete_native = [country for country in normalized if country != "EU"]
            refined = list(dict.fromkeys(concrete_native + _country_scopes_from_text(text)))[:2]
            return (refined or ["UNSPECIFIED"]), "eu-refined-v2"

        deduped = list(dict.fromkeys(normalized))[:2]
        source = "native-normalized-v2" if deduped != list(dict.fromkeys(raw_native))[:2] else "native"
        return deduped, source

    countries = _country_scopes_from_text(text)
    if not countries:
        countries = ["KR"] if region_of(record) == "국내" else ["UNSPECIFIED"]
    return countries, "heuristic-v2"


def count_country_issues(issues: list[dict], since_date: str) -> Counter:
    """기간 내 연결 이슈를 국가·지역별로 중복 없이 센다.

    같은 이슈의 기사가 여러 번 보도돼도 한 국가에는 1건만 더한다. 한 이슈가
    복수 국가에 걸치면 해당 국가마다 1건씩 집계하므로 전체 합은 이슈 수보다 클 수 있다.
    """
    counts = Counter()
    for issue in issues:
        scopes = {
            country
            for member in issue.get("members", [])
            if (member.get("article_date") or "") >= since_date
            for country in (member.get("countries") or [])
        }
        counts.update(scopes)
    return counts


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


def _text_tokens(text: object) -> set[str]:
    return {token.lower()[:8] for token in _TOKEN_RE.findall(str(text or "")) if len(token) >= 2}


def _is_restatement(before: object, after: object, threshold: float = 0.45) -> bool:
    """두 문장이 같은 사실을 다시 쓴 것인지 판단한다.

    후속 보도의 요약과 직전 브리핑의 요약이 표현만 다른 같은 사실인 경우가 잦다.
    이때 '이전 → 현재'로 이어 붙이면 같은 내용을 두 번 읽히므로 변화로 취급하지
    않는다. 임계값 0.45는 봇의 패러프레이즈 dedup과 같은 기준이다.
    """
    left = _text_tokens(before)
    right = _text_tokens(after)
    if not left or not right:
        return True
    if _jaccard(left, right) >= threshold:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return len(shorter & longer) / len(shorter) >= 0.8


def load_embeddings_cache() -> dict[str, list[float]]:
    """현행 Gemini 모델의 임베딩 캐시만 읽기 전용으로 정규화한다.

    진단 한 줄을 반드시 남긴다. 파이프라인이 coverage 1.0 을 보고하는데도
    ``embedding_cache_entries`` 가 0 으로 나오는 상태를 2026-08-03 에 만났고,
    경로·모델명·파일 존재 중 무엇이 어긋났는지 로그가 없어 가릴 수 없었다.
    빈 dict 는 '파일이 없다'와 '전부 모델 불일치로 탈락했다'를 구분하지 못한다.
    """
    path = Path(os.environ.get("EMBEDDINGS_FILE", BOT_DIR / "embeddings.json"))
    diag: dict[str, object] = {"path": str(path), "exists": path.exists(),
                               "model_wanted": EMBEDDING_MODEL}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        diag["error"] = f"{type(exc).__name__}: {exc}"
        print("[build_data:embeddings] " + json.dumps(diag, ensure_ascii=False))
        return {}
    embeddings: dict[str, list[float]] = {}
    models_seen: Counter = Counter()
    for article_hash, payload in raw.items():
        if isinstance(payload, dict):
            models_seen[str(payload.get("model"))] += 1
        vector = cached_vector(payload, model=EMBEDDING_MODEL)
        if vector:
            embeddings[str(article_hash)] = vector
    diag.update(raw_entries=len(raw), accepted=len(embeddings),
                models_seen=dict(models_seen.most_common(5)))
    print("[build_data:embeddings] " + json.dumps(diag, ensure_ascii=False))
    return embeddings


def _local_embedding_features(article: dict) -> Counter:
    """API 장애 때도 후보 탐색을 계속할 수 있는 언어 독립 특징 벡터."""
    features = Counter()
    title = _title_norm(article)
    summary = _NORM_RE.sub("", str(article.get("summary") or "").lower())
    for ngram_size, weight in ((2, 1.6), (3, 2.2), (4, 1.2)):
        for index in range(max(0, len(title) - ngram_size + 1)):
            features[f"t{ngram_size}:{title[index:index + ngram_size]}"] += weight
    for index in range(max(0, len(summary) - 3 + 1)):
        features[f"s3:{summary[index:index + 3]}"] += 0.45
    for token in _tokens(article):
        features[f"w:{token}"] += 1.0
    for tag in _strong_tags(article):
        features[f"tag:{tag}"] += 4.0
    for topic in article.get("topics") or []:
        features[f"topic:{topic}"] += 2.2
    return features


def build_local_embeddings(articles: list[dict]) -> dict[str, list[float]]:
    """문자 n-gram TF-IDF를 feature hashing해 21일 후보 탐색 벡터를 만든다.

    Gemini 벡터와 다른 공간이므로 둘을 섞지 않는다. 이 로컬 벡터는 낮은 임계값
    후보를 만드는 데만 쓰고, 자동 병합은 기존 보수 규칙/Gemini가 담당한다.
    """
    feature_rows = {
        str(article.get("hash") or ""): _local_embedding_features(article)
        for article in articles if article.get("hash")
    }
    document_frequency = Counter()
    for features in feature_rows.values():
        document_frequency.update(features.keys())
    total = max(1, len(feature_rows))
    embeddings = {}
    for article_hash, features in feature_rows.items():
        vector = [0.0] * LOCAL_EMBEDDING_DIMENSION
        for feature, term_weight in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % LOCAL_EMBEDDING_DIMENSION
            inverse_frequency = math.log((1 + total) / (1 + document_frequency[feature])) + 1.0
            vector[index] += float(term_weight) * inverse_frequency
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            embeddings[article_hash] = [value / norm for value in vector]
    return embeddings


def _pair_id(left_hash: object, right_hash: object) -> str:
    left, right = sorted((str(left_hash or ""), str(right_hash or "")))
    return f"{left}--{right}"


def load_match_overrides(path: Path = MATCH_OVERRIDES_FILE) -> dict[str, set[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}

    def keys(name: str) -> set[str]:
        result = set()
        for row in raw.get(name) or []:
            if isinstance(row, str) and "--" in row:
                result.add(row)
            elif isinstance(row, dict):
                result.add(_pair_id(row.get("left_hash"), row.get("right_hash")))
        return {value for value in result if not value.startswith("--") and not value.endswith("--")}

    return {"approved": keys("approved"), "rejected": keys("rejected")}


# ---- 편집 override -------------------------------------------------------------
#
# 알고리즘 결과에 사람이 최종 판단을 얹는 자리. 텔레그램 브리핑은 07:25 무인
# 발송이라 개입할 창이 없지만, 웹은 발송 뒤에도 고칠 수 있다 — 잘못 올라온 카드를
# 내리고 놓친 이슈를 올리는 게 실제로 가능한 유일한 지점이다.
#
# 적용은 반드시 2단계다.
#   ① 클러스터링 전 — promote 대상에 briefing_date 를 주입한다. 브리핑 이슈는
#      '발송된 기사'에서만 나오므로(delivery_log 조인), 이걸 안 하면 미발송 기사는
#      배열에 없어서 정렬로는 절대 올릴 수 없다.
#   ② 클러스터링 후 — hash 가 속한 **이슈 클러스터 전체**에 적용한다. 기사 하나만
#      건드리면 같은 클러스터의 다른 멤버가 briefing_date 를 갖고 있어 카드가 그대로
#      남는다. 사용자에게 보이는 단위가 이슈 카드이므로 판정 단위도 이슈여야 한다.
SELECTION_OVERRIDES_FILE = BOT_DIR / "selection_overrides.json"

HIDE_ACTION = "hide_from_today"
DEMOTE_ACTIONS = {HIDE_ACTION, "demote_only"}


def _short_hash(value: object) -> str:
    return str(value or "").strip().lower()[:8]


def load_selection_overrides(path: Path = SELECTION_OVERRIDES_FILE) -> dict:
    """{'promote': {(hash8, date): reason}, 'demote': {(hash8, date): action}}.

    date 는 필수다. 없으면 한 번 승격한 이슈가 몇 달 뒤에도 맨 위에 남는다.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    promote: dict[tuple[str, str], str] = {}
    demote: dict[tuple[str, str], str] = {}
    skipped = 0
    for name, sink in (("promote", promote), ("demote", demote)):
        for row in raw.get(name) or []:
            if not isinstance(row, dict):
                skipped += 1
                continue
            key = _short_hash(row.get("hash8") or row.get("hash"))
            day = str(row.get("date") or "").strip()
            if len(key) < 8 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                skipped += 1
                continue
            if name == "demote":
                action = str(row.get("action") or HIDE_ACTION)
                sink[(key, day)] = action if action in DEMOTE_ACTIONS else HIDE_ACTION
            else:
                sink[(key, day)] = str(row.get("reason") or "")
    # 같은 hash 가 양쪽에 있으면 demote 가 이긴다 — 실수로 내리는 쪽이
    # 실수로 올리는 쪽보다 안전하다.
    conflicts = set(promote) & set(demote)
    for key in conflicts:
        promote.pop(key, None)
    if skipped or conflicts:
        print(f"[overrides] 무시 {skipped}건 (hash8/date 누락) / "
              f"promote·demote 충돌 {len(conflicts)}건 → demote 우선")
    return {"promote": promote, "demote": demote, "matched": set()}


def apply_promotions(visible: list[dict], overrides: dict) -> int:
    """1단계 — promote 대상을 그날 브리핑 후보로 끌어올린다(클러스터링 전)."""
    promote = overrides.get("promote") or {}
    if not promote:
        return 0
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for item in visible:
        by_hash[_short_hash(item.get("hash"))].append(item)
    count = 0
    for (key, day), _reason in promote.items():
        for item in by_hash.get(key, []):
            if item.get("briefing_date") != day:
                item["briefing_date"] = day
                item["promoted_by_editor"] = True
            overrides["matched"].add((key, day))
            count += 1
    return count


def override_verdict(members: list[dict], briefing_date: str, overrides: dict) -> str:
    """2단계 — 이슈 클러스터 단위 판정. '' | 'promote' | 'hide' | 'demote'.

    한 클러스터에 promote 와 demote 가 섞이면 demote 가 이긴다(로더와 같은 원칙).
    """
    keys = {(_short_hash(m.get("hash")), briefing_date) for m in members}
    demote = overrides.get("demote") or {}
    promote = overrides.get("promote") or {}
    hit_demote = [demote[k] for k in keys if k in demote]
    hit_promote = [k for k in keys if k in promote]
    for key in keys:
        if key in demote or key in promote:
            overrides["matched"].add(key)
    if hit_demote:
        if hit_promote:
            print(f"[overrides] {briefing_date} 한 이슈에 promote·demote 공존 → demote 적용")
        return "hide" if HIDE_ACTION in hit_demote else "demote"
    return "promote" if hit_promote else ""


def report_unmatched_overrides(overrides: dict) -> None:
    """없는 hash 는 조용히 무시하되 흔적은 남긴다 — 오타를 영영 모르면 안 된다."""
    everything = set(overrides.get("promote") or {}) | set(overrides.get("demote") or {})
    missing = sorted(everything - (overrides.get("matched") or set()))
    if missing:
        preview = ", ".join(f"{h}@{d}" for h, d in missing[:5])
        print(f"[overrides] 해당 날짜 데이터에 없는 항목 {len(missing)}건 무시: {preview}")


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
    local_embeddings: dict[str, list[float]] | None = None,
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
    local_embedding_similarity = cosine_similarity(
        (local_embeddings or {}).get(str(left.get("hash") or "")),
        (local_embeddings or {}).get(str(right.get("hash") or "")),
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
        # 보조 조건(tag/topic/title)은 게이트 역할을 못 했다. topics 가 통제 어휘
        # 12개라 원자력 기사 둘이면 topic_shared>=1 이 사실상 항상 참이었고,
        # 실측 자동 병합 60건 중 38건이 그 조건만으로 통과했다. 남는 판정이
        # 코사인 하나뿐이었는데 0.82 는 한국어 원자력 요약문에서
        # "같은 사건"이 아니라 "같은 분야"를 잡는 높이다(오병합 쌍의 코사인
        # 중앙값 0.856, 제목 유사도 중앙값 0.24). 게이트를 걷어내고 코사인만
        # 0.92 로 올린다 — 0.92 미만은 사람/LLM 검수 큐로 보낸다.
        elif (
            embedding_similarity is not None
            and embedding_similarity >= ISSUE_EMBEDDING_THRESHOLD
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
        "local_embedding_similarity": (
            round(local_embedding_similarity, 4)
            if local_embedding_similarity is not None else None
        ),
        "method": method,
        "blocked_by": blocked_by,
        "left_facilities": sorted(left_units or left_plants),
        "right_facilities": sorted(right_units or right_plants),
    }


def is_review_candidate(diagnostics: dict) -> tuple[bool, str, float]:
    """자동 병합 아래 구간을 사람 확인 큐로 보낸다."""
    if diagnostics.get("blocked_by"):
        return False, "", 0.0
    remote = diagnostics.get("embedding_similarity")
    local = diagnostics.get("local_embedding_similarity")
    title_ratio = float(diagnostics.get("title_ratio") or 0)
    token_ratio = float(diagnostics.get("token_ratio") or 0)
    contextual = bool(
        diagnostics.get("tag_shared")
        or diagnostics.get("topic_shared")
        or title_ratio >= 0.28
        or token_ratio >= 0.16
    )
    if not contextual:
        return False, "", 0.0
    if remote is not None and remote >= ISSUE_EMBEDDING_CANDIDATE_THRESHOLD:
        return True, "gemini_candidate", float(remote)
    if (
        local is not None
        and local >= LOCAL_EMBEDDING_CANDIDATE_THRESHOLD
        and (
            (diagnostics.get("tag_shared") and title_ratio >= 0.20)
            or (
                diagnostics.get("topic_shared")
                and (title_ratio >= 0.25 or token_ratio >= 0.12)
            )
            or title_ratio >= 0.45
        )
    ):
        return True, "local_candidate", float(local)
    return False, "", 0.0


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


# 두 흐름이 근거를 이만큼 공유하면 같은 사건을 키워드만 바꿔 되풀이한 것이다.
# 흐름 해석은 키워드마다 하나씩 만들어지는데, 한 사건이 여러 키워드를 달고
# 있으면 같은 이야기가 그 수만큼 재포장된다.
# 실측(2026-08-03 라이브): '기후변화'와 '원전운영'이 근거 7건 중 4건(57%)을
# 공유했다 — 둘 다 헝가리 가뭄으로 인한 원전 가동 중단 이야기였다.
# 나머지 쌍은 1건(7~17%)이라 경계가 뚜렷하다.
INSIGHT_DUPLICATE_RATIO = 0.4


def dedupe_insights(items: list[dict]) -> list[dict]:
    """근거가 크게 겹치는 흐름을 접는다. 근거가 많은 쪽을 남긴다."""
    ordered = sorted(
        items,
        key=lambda item: (len(item.get("evidence") or []), item.get("signal_score") or 0),
        reverse=True,
    )
    kept: list[dict] = []
    for item in ordered:
        hashes = {row.get("hash") for row in item.get("evidence") or [] if row.get("hash")}
        if not hashes:
            kept.append(item)
            continue
        duplicate_of = None
        for other in kept:
            other_hashes = {row.get("hash") for row in other.get("evidence") or [] if row.get("hash")}
            shared = hashes & other_hashes
            if shared and len(shared) / min(len(hashes), len(other_hashes)) >= INSIGHT_DUPLICATE_RATIO:
                duplicate_of = other
                break
        if duplicate_of is None:
            kept.append(item)
        else:
            # 접힌 키워드는 남은 흐름에 함께 표기한다 — 정보를 버리지 않는다
            merged = duplicate_of.setdefault("merged_keywords", [])
            keyword = item.get("keyword")
            if keyword and keyword not in merged:
                merged.append(keyword)
    # 원래 순서(입력 순)를 유지해 화면 배치가 흔들리지 않게 한다
    order = {id(item): index for index, item in enumerate(items)}
    return sorted(kept, key=lambda item: order.get(id(item), 0))


WEEKLY_MOVER_COUNT = 4


def build_weekly_movers(issue_catalog: list[dict], end_date: str,
                        days: int = 7) -> list[dict]:
    """이번 주 가장 크게 움직인 이슈.

    흐름 해석을 **키워드 단위**로 만들던 것을 이슈(사건) 단위로 바꾼다.
    키워드 단위에서는 한 사건이 달고 있는 키워드 수만큼 같은 이야기가 재포장됐다
    (실측 2026-08-03: 헝가리 가뭄 원전 중단 하나가 기후변화·원전운영·전력시장·
    에너지안보 네 흐름에 동시 등장). 이슈는 이미 사건 단위로 묶여 있으므로
    중복이 구조적으로 생기지 않는다.

    '움직임'은 이번 주에 실제로 쌓인 양으로 잰다 — 이번 주 원문 수, 며칠에 걸쳐
    보도됐는지, 서로 다른 매체가 몇 곳인지. 해석 문장을 붙이지 않는다.
    """
    end = _parse_day(end_date)
    if not end:
        return []
    start = (end - timedelta(days=days - 1)).isoformat()

    movers = []
    for issue in issue_catalog:
        in_week = [
            article for article in issue.get("related_articles") or []
            if str(article.get("briefing_date") or "") >= start
        ]
        if not in_week:
            continue
        publishers = {
            (article.get("publisher") or article.get("domain") or "").strip()
            for article in in_week
        }
        publishers.discard("")
        days_covered = len({article.get("briefing_date") for article in in_week})
        movers.append({
            "issue_id": issue["issue_id"],
            "title": issue["title"],
            "summary": issue.get("summary", ""),
            "region": issue.get("region", ""),
            "topics": issue.get("topics") or [],
            "week_article_count": len(in_week),
            "week_days": days_covered,
            "publisher_count": len(publishers),
            "total_article_count": issue.get("article_count", len(in_week)),
            "is_continuing": bool(issue.get("first_seen", "") < start),
            "first_seen": issue.get("first_seen", ""),
            "last_seen": issue.get("last_seen", ""),
            "verification": issue.get("verification") or {},
            # 이 이슈가 이번 주에 실제로 무엇으로 구성됐는지 — 해석 대신 사실
            "events": [
                {"date": article.get("article_date", ""),
                 "title": article.get("title_kr", ""),
                 "publisher": article.get("publisher") or article.get("domain") or "",
                 "url": article.get("url", "")}
                for article in sorted(
                    in_week, key=lambda a: str(a.get("article_date") or ""), reverse=True)[:4]
            ],
        })

    # 많이·여러 날·여러 매체에서 다뤄진 순. 국내 이슈는 업무 관련성이 높아
    # 동률일 때 앞세운다.
    movers.sort(key=lambda row: (
        row["week_article_count"], row["week_days"], row["publisher_count"],
        row["region"] == "국내",
    ), reverse=True)
    return movers[:WEEKLY_MOVER_COUNT]


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
            article = by_hash.get(article_hash)
            # 아카이브 품질 마이그레이션에서 삭제·병합된 기사는 더 이상
            # 공개 근거가 아니다. 빈 메타로 남기지 말고 인사이트에서도 제거한다.
            if article is None:
                continue
            seen.add(article_hash)
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

    items = dedupe_insights(items)

    prepared = dict(insights)
    prepared["items"] = items
    prepared["featured_items"] = select_featured_insights(items)
    prepared["selection_method"] = "signal-region-evidence-diversity-v2-deduped"
    return prepared


def cluster_selected_articles(
    news_items: list[dict],
    embeddings: dict[str, list[float]] | None = None,
    local_embeddings: dict[str, list[float]] | None = None,
    match_overrides: dict[str, set[str]] | None = None,
    review_candidates: list[dict] | None = None,
) -> list[dict]:
    """발송된 기사들을 최근 이슈 묶음으로 연결한다.

    issue_id는 최초 기사 hash에서 만들어 안정적으로 유지한다. 대표 기사는 더 좋은
    출처나 중요 기사로 바뀔 수 있지만 issue_id는 바뀌지 않는다.
    """
    selected = [item for item in news_items if item.get("briefing_date")]
    selected.sort(key=lambda item: (item["briefing_date"], item["article_date"], item["hash"]))
    issues: list[dict] = []
    overrides = match_overrides or {"approved": set(), "rejected": set()}
    # '다른 사건'으로 이미 판정된 쌍. 사람 판정과 LLM 판정을 같이 본다.
    veto_pairs = set(overrides.get("rejected") or ()) | set(overrides.get("llm_rejected") or ())
    candidate_rows = review_candidates if review_candidates is not None else []
    seen_candidates = {_pair_id(row.get("left_hash"), row.get("right_hash")) for row in candidate_rows}

    for article in selected:
        article_day = _parse_day(article.get("briefing_date", ""))
        best_issue = None
        best_score = -1.0
        best_diag = None

        for issue in issues:
            last_day = _parse_day(issue["last_seen"])
            if article_day and last_day and (article_day - last_day).days > ISSUE_WINDOW_DAYS:
                continue
            # 클러스터 전체 거부권 — 쌍 단위 판정은 전이적이지 않다.
            #
            # A=B 와 A=C 를 각각 승인해도 B≠C 라면 셋을 한 묶음으로 만들면 안 된다.
            # 아래 매칭은 멤버 하나만 맞으면 합류시키는 탐욕적 구조라, 거부된 짝이
            # 같은 이슈 안에 있어도 다른 멤버를 통해 들어올 수 있다.
            #
            # 실제 사고(2026-08-03 라이브, issue-6b93ed7e22e9bb4b): 서로 다른 NRC
            # 규정 제정 2건(환경영향평가 / 방사성 물질 운송)이 '공청회서 신규 규정
            # 제안'이라는 일반적 제목을 경유해 한 이슈로 합쳐졌다. LLM 은 그 둘을
            # "서로 다른 규정 제안"으로 **정확히 기각한 상태였다** — 판정기가 아니라
            # 판정을 이어붙이는 이 지점이 문제였다.
            if veto_pairs and any(
                _pair_id(article["hash"], member["hash"]) in veto_pairs
                for member in issue["members"]
            ):
                continue
            # 대표 기사 한 건만 보면 표현이 단계적으로 바뀌는 A→B→C 후속 보도가
            # 끊길 수 있다. 최근 기사 3건 중 가장 가까운 연결을 사용한다.
            for reference in issue["members"][-3:]:
                pair_id = _pair_id(article["hash"], reference["hash"])
                matched, score, diag = issue_similarity(
                    article, reference, embeddings, local_embeddings
                )
                if pair_id in overrides.get("rejected", set()):
                    continue
                if pair_id in overrides.get("approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 1.0)
                    diag = {**diag, "method": "manual_approved"}
                # 회색지대(0.88~0.92)를 LLM 이 같은 사건으로 판정한 쌍. 사람 승인과
                # 구분해 audit 에 남긴다. 사람 승인(1.0)보다 낮은 점수를 줘서
                # 같은 기사가 양쪽에 붙을 때 사람 판정이 이기게 한다.
                elif pair_id in overrides.get("llm_approved", set()) and not diag.get("blocked_by"):
                    matched, score = True, max(score, 0.99)
                    diag = {**diag, "method": "llm_approved"}
                elif not matched:
                    is_candidate, candidate_method, candidate_score = is_review_candidate(diag)
                    if is_candidate and pair_id not in seen_candidates:
                        seen_candidates.add(pair_id)
                        candidate_rows.append({
                            "candidate_id": pair_id,
                            "left_hash": reference["hash"],
                            "right_hash": article["hash"],
                            "left_date": reference.get("briefing_date"),
                            "right_date": article.get("briefing_date"),
                            "left_title": reference.get("title_kr") or reference.get("title"),
                            "right_title": article.get("title_kr") or article.get("title"),
                            "candidate_method": candidate_method,
                            "candidate_score": round(candidate_score, 4),
                            "diagnostics": diag,
                            "review_state": "pending",
                        })
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
        "source_type": article.get("source_type", "unknown"),
        "evidence_role": article.get("evidence_role", "unknown"),
        "source_tier": article.get("source_tier", 3),
        "article_type": article.get("article_type", ""),
        "event_date": article.get("event_date"),
        "event_date_type": article.get("event_date_type", "unknown"),
        "region": article.get("region", ""),
        "countries": article.get("countries") or [],
        "topics": article.get("topics") or [],
        "url": article.get("url", ""),
        "importance": article.get("importance", ""),
    }


def latest_change_line(current: list[dict], history: list[dict]) -> str:
    """추적 이슈의 이번 브리핑 신규 사실을 완결된 한 문장으로 만든다."""
    if not current:
        return ""
    newest = max(
        current,
        key=lambda member: (member.get("article_date") or "", _representative_key(member)),
    )
    text = newest.get("summary") or newest.get("title_kr") or newest.get("title") or ""
    change = flow_takeaway(text, limit=112).strip()
    if not history:
        if change and not change.endswith((".", "!", "?")):
            change += "."
        return change

    previous = max(
        history,
        key=lambda member: (member.get("briefing_date") or "", member.get("article_date") or "", _representative_key(member)),
    )
    previous_text = previous.get("summary") or previous.get("title_kr") or previous.get("title") or ""
    before = flow_takeaway(previous_text, limit=48).strip().rstrip(".!?")
    after = change.rstrip(".!?")
    if before and after and not _is_restatement(before, after):
        combined = f"{before} → {after}"
        if len(combined) <= CHANGE_LINE_LIMIT:
            change = combined
    if change and not change.endswith((".", "!", "?")):
        change += "."
    return change


def change_line_for_card(current: list[dict], history: list[dict], summary: str) -> str:
    """요약을 그대로 되풀이하는 변화 문장은 비운다.

    단독 기사 이슈는 '변화'가 요약과 같은 문장일 수밖에 없다. 이때 변화 블록을
    남기면 같은 문단이 카드에 두 번 뜬다. 화살표가 있는 문장은 이전 상태 대비
    새 정보이므로 그대로 둔다.
    """
    change = latest_change_line(current, history)
    if not change or "→" in change:
        return change
    return "" if _is_restatement(summary, change) else change


def _is_primary_source(article: dict) -> bool:
    return article.get("evidence_role") == "primary" or article.get("source_tier") == 1


def _source_identity(article: dict) -> str:
    """같은 매체가 쓴 여러 기사를 하나의 출처로 묶는 키."""
    publisher = _NORM_RE.sub("", str(article.get("publisher") or "").lower())
    if publisher:
        return f"pub:{publisher}"
    domain = str(article.get("domain") or "").lower()
    # 구글 뉴스는 집계 도메인이라 매체를 식별하지 못한다. 매체명이 비어 있으면
    # 서로 다른 출처로 합치지 않고 기사 단위로 남긴다(과대 계상 방지).
    if not domain or "news.google." in domain:
        return f"hash:{article.get('hash') or id(article)}"
    return f"dom:{domain}"


def _is_official_source(article: dict) -> bool:
    """규제기관·사업자의 공식 문서인지."""
    return article.get("evidence_role") == "primary" or article.get("source_type") == "official"


def _is_independent_source(article: dict) -> bool:
    """독립 취재 보도인지. 보도자료 전재(distributed_claim)는 재인용으로 제외한다."""
    return article.get("evidence_role") == "independent"


def pick_open_question(members: list[dict]) -> str:
    """이슈에 붙일 '아직 확정되지 않은 것' 한 문장.

    대표 기사 필드를 그대로 복사하면 안 된다 — 미확정 내용이 대표 기사에는 없고
    같은 이슈의 다른 공식 기사에만 있는 경우가 흔하다. 공식 → tier1 → 최신 순으로
    비어 있지 않은 첫 문장을 고른다.
    """
    def latest_first(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=_representative_key, reverse=True)

    filled = [m for m in members if str(m.get("open_question") or "").strip()]
    if not filled:
        return ""
    for group in (
        [m for m in filled if _is_official_source(m)],
        [m for m in filled if m.get("source_tier") == 1],
        filled,
    ):
        for member in latest_first(group):
            return str(member["open_question"]).strip()
    return ""


def verification_state(articles: list[dict], checked_at: str = "") -> dict:
    """이슈 근거를 4단계 검증 상태로 요약한다.

    - official: 규제기관·사업자 공식 문서로 확인
    - corroborated: 재인용 관계를 제거한 독립 출처 2곳 이상이 일치
    - partial: 독립 출처 1곳만 확인
    - unverified: 배포 자료 재인용뿐이거나 근거가 부족

    근거가 없으면 문장을 지어내지 않고 unverified로 남긴다.
    """
    official = {_source_identity(article) for article in articles if _is_official_source(article)}
    independent = {
        _source_identity(article) for article in articles if _is_independent_source(article)
    } - official
    all_sources = {_source_identity(article) for article in articles}

    if official:
        status = "official"
    elif len(independent) >= 2:
        status = "corroborated"
    elif len(independent) == 1:
        status = "partial"
    else:
        status = "unverified"

    return {
        "status": status,
        "label": VERIFICATION_LABELS[status],
        "source_count": len(all_sources),
        "independent_source_count": len(independent),
        "official_source_count": len(official),
        "checked_at": checked_at,
    }


EMPTY_HEADLINE = "오늘 새로 연결된 원자력 이슈가 없습니다"


def _fit_headline(candidates: list[object]) -> str:
    """후보 문장 중 히어로 한 줄에 들어가는 첫 문장을 고른다."""
    fallback = ""
    for candidate in candidates:
        headline = flow_takeaway(candidate, limit=HEADLINE_LIMIT).strip().rstrip(".!?")
        if not headline:
            continue
        if len(headline) <= HEADLINE_LIMIT:
            return headline
        fallback = fallback or headline
    if not fallback:
        return ""
    # flow_takeaway가 안전하게 종결하지 못한 문장은 원문을 지키느라 길이를 넘긴다.
    # 히어로 h1이 문단으로 번지지 않도록 마지막 단계에서만 말줄임한다.
    return f"{fallback[:HEADLINE_LIMIT - 1].rstrip()}…"


SYNTHESIS_LIMIT = 90  # 봇 종합 문장 상한 — daily_lead.LEAD_LIMIT와 동일 계약
# 종합 문장이 그날 이슈 제목과 공유해야 하는 최소 의미 토큰 수.
# 하루 이슈에 공통 주제가 없으면 모델이 "비워 두라"는 지시를 어기고 최대한
# 일반적인 문장으로 뭉갠다. 실측(2026-08-03 라이브):
#   "국내외에서 원자력 및 에너지 정책과 현실에 대한 다양한 논의와 상황 변화가
#    있었습니다" → 이슈 제목과 공유 토큰 0개
# 같은 날 구체적 문장이라면 6~7개가 겹친다. 0~1개면 아무 말도 안 한 것이다.
SYNTHESIS_MIN_SHARED = 2
_CLAUSE_BOUNDARIES = ("며, ", "고, ", "지만 ", "으나 ", ", ")


def _fit_synthesis(text: str) -> str:
    """봇이 만든 종합 문장을 빌드 단계에서 한 번 더 길이 검증한다.

    생성 단계(daily_lead.py)가 90자를 지키지만, 계약 위반 데이터가 와도
    히어로 h1이 문단으로 번지지 않도록 절 경계에서 자른다.
    """
    text = " ".join(str(text or "").split()).strip()
    if not text or len(text) <= SYNTHESIS_LIMIT:
        return text
    window = text[:SYNTHESIS_LIMIT]
    best = -1
    for sep in _CLAUSE_BOUNDARIES:
        pos = window.rfind(sep)
        if pos > best:
            best = pos + len(sep.rstrip())
    if best > 20:
        return window[:best].rstrip().rstrip(",")
    return window[: SYNTHESIS_LIMIT - 1].rstrip() + "…"


def synthesis_is_substantive(lead: str, issue_rows: list[dict]) -> bool:
    """종합 문장이 실제로 무언가를 말하는지.

    그날 이슈 제목과 의미 토큰을 나눠 갖지 못하면 구체적인 사실을 하나도
    담지 못한 문장이다. 그런 문장은 제목 폴백(구체적 이슈 제목)보다 못하다.
    """
    if not lead:
        return False
    titles = " ".join(str(row.get("title") or "") for row in issue_rows)
    shared = _keei_shared(_keei_match_tokens(lead), _keei_match_tokens(titles))
    return len(shared) >= SYNTHESIS_MIN_SHARED


def _evidence_chips(evidence: list, issue_rows: list[dict]) -> list[dict]:
    """종합 문장의 근거 기사 hash를 그날 이슈 카드로 연결한다 (최대 3개)."""
    hash_to_issue: dict[str, dict] = {}
    for row in issue_rows:
        for article in row.get("related_articles") or []:
            article_hash = article.get("hash")
            if article_hash and article_hash not in hash_to_issue:
                hash_to_issue[article_hash] = row
    chips: list[dict] = []
    seen_issues: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        row = hash_to_issue.get(item.get("hash") or "")
        if not row or row["issue_id"] in seen_issues:
            continue
        seen_issues.add(row["issue_id"])
        chips.append({"issue_id": row["issue_id"], "title": row["title"]})
        if len(chips) >= 3:
            break
    return chips


_REPEAT_SHARED_TOKENS = 3  # 어제 헤드라인과 이만큼 겹치면 같은 사건으로 본다


def _is_repeat_of(title: str, previous_headline: str) -> bool:
    """어제 히어로가 말한 것과 같은 사건인지."""
    previous = _keei_match_tokens(previous_headline)
    if not previous:
        return False
    return len(_keei_shared(_keei_match_tokens(title), previous)) >= _REPEAT_SHARED_TOKENS


def daily_lead(issue_rows: list[dict], previous_headline: str = "") -> dict:
    """히어로 문장과 그 문장의 성격(kind)을 함께 만든다.

    kind가 오버라인 문구를 정한다. 실제로 이어지는 이슈일 때만 change로 표시한다.

    설계 근거 (라이브 실측 2026-08-02):
      - 예전에는 `latest_change` 화살표 뒤쪽을 헤드라인으로 썼는데, 그건 **생성
        문장**이라 "…발표했습니다", "…경고했다" 같은 기사체가 그대로 h1 에
        올라왔다. 반면 이슈 **제목**은 이미 개조식이라 훨씬 헤드라인답다
        (실측 비교: change 경로 8/1·8/2 vs issue 경로 7/31).
      - 어제와 같은 이슈가 오늘도 1위면 이틀 연속 같은 문장이 떴다(헝가리 원전
        가동 중단이 8/1·8/2 연속). '무엇이 달라졌는가'라고 묻고 어제와 같은
        답을 하면 제목이 거짓말이 된다 → 전날 헤드라인과 겹치는 이슈는 건너뛴다.
    """
    if not issue_rows:
        return {"headline": EMPTY_HEADLINE, "kind": "empty"}

    fresh = [row for row in issue_rows
             if not _is_repeat_of(str(row.get("title") or ""), previous_headline)]
    if not fresh:  # 전부 어제와 겹치면 순위를 그대로 따른다(억지로 비우지 않는다)
        fresh = issue_rows

    lead = fresh[0]
    headline = _fit_headline([lead.get("title"), lead.get("summary")])
    if not headline:
        return {"headline": EMPTY_HEADLINE, "kind": "empty"}
    # 이어지는 이슈면 '무엇이 달라졌는가', 처음 잡힌 이슈면 '오늘의 핵심 이슈'
    kind = "change" if lead.get("previous_article_count") else "issue"
    return {"headline": headline, "kind": kind}


def daily_headline(issue_rows: list[dict]) -> str:
    return daily_lead(issue_rows)["headline"]


def order_issue_rows(issue_rows: list[dict]) -> None:
    """브리핑 이슈를 국내·해외 순위를 번갈아 가며 배열한다 (제자리 정렬).

    봇은 국내와 해외를 **별도 풀에서 각자 캡으로** 뽑는다(국내 3 / 해외 6).
    그런데 웹이 이걸 raw 점수 하나로 다시 줄 세우면서 문제가 생겼다 — 출처 등급
    보너스(tier1 +3.0)가 국제 전문지 전용이라 국내 매체는 구조적으로 0점이고,
    그 결과 국내 이슈가 통째로 하위권으로 밀렸다(실측 8/1 브리핑에서 국내 3건이
    6·8·9위). 점수를 손대 공신력 등급을 왜곡하는 대신, 봇이 이미 만든 두 갈래
    구조를 화면에서도 유지한다: 각 지역 안의 순위(1위끼리, 2위끼리)를 맞물린다.
    """
    def within_region(row: dict) -> tuple:
        # 편집 고정(editor_pin)은 **자기 지역 안에서만** 작동한다. 지역 맞물림
        # 구조를 넘어 끌어올리면 해외 이슈가 국내 자리를 먹는다.
        return (row.get("editor_pin", 0), row["importance"] == "must_read",
                row["sort_score"], row["last_seen"])

    domestic = sorted((r for r in issue_rows if r["region"] == "국내"),
                      key=within_region, reverse=True)
    overseas = sorted((r for r in issue_rows if r["region"] != "국내"),
                      key=within_region, reverse=True)
    rank = {id(row): index for group in (domestic, overseas)
            for index, row in enumerate(group)}

    issue_rows.sort(key=lambda row: (
        rank[id(row)],
        0 if row["importance"] == "must_read" else 1,
        -row["sort_score"],
    ))
    for row in issue_rows:
        row.pop("sort_score", None)
        row.pop("editor_pin", None)   # 정렬 전용 — 화면에 편집 흔적을 남기지 않는다


PUBLICATION_NEW_DAYS = 14  # 이 기간 안의 발간물에 NEW 뱃지

# 기관 표기는 화면에서 정규화한다. 수집 시점 라벨이 그대로 굳으면 이름을 바꿔도
# 과거 항목은 옛 이름으로 남아 필터에 같은 기관이 두 개로 갈린다.
# 기관명은 정식 명칭 + 영문 약자로 통일한다. 약자만 아는 사람과 한글 명칭만 아는
# 사람이 갈리는데, 발간물 목록은 원문을 찾아가는 통로라 양쪽 다 필요하다.
# 빌드 시점에 매핑하므로 이미 수집된 항목도 함께 교정된다.
PUBLICATION_ORG_ALIASES = {
    "에경연": "에너지경제연구원(KEEI)",
    "에너지경제연구원": "에너지경제연구원(KEEI)",
    "OECD 원자력기구": "OECD 원자력기구(NEA)",
    "국제원자력기구": "국제원자력기구(IAEA)",
    "국제에너지기구": "국제에너지기구(IEA)",
    "미국 에너지정보청": "미국 에너지정보청(EIA)",
}

# 발간물 탭은 "보고서로 쓸 만한 문서인가"를 판단하는 자리다. 기관 피드에는 그
# 판단에 쓸 수 없는 종류가 섞여 들어온다 — 실측 29건 중 12건(41%)이었다.
#
# 두 갈래로 나눠 거른다. 갈래를 합치지 않는 이유는 오탐이 났을 때 어느 규칙이
# 잡았는지 로그로 바로 알기 위해서다.
#
#   EVENT   행사·교육·인사 소식. 문서가 아니라 일정이다.
#           (Joshikai 10주년, NextGen 여름학교, TCOFF-2 진행상황 회의…)
#   NONPOWER  IAEA 발간물의 절반은 FAO 공동 프로그램(농업·식품·수자원·축산)이다.
#           원자력 기술을 쓰지만 발전·정책과 접점이 없다.
#           (Plant Breeding / Insect Pest Control / Soils / Food Safety 뉴스레터…)
#
# 제목 규칙이라 완벽하지 않다. 정확한 판정은 pubs_translate 가 번역과 같은 배치
# 호출에서 매기는 `off_topic` 이고(추가 호출 0회), 그 값이 있으면 우선한다.
# 규칙은 이미 수집된 항목을 즉시 교정하는 폴백이다.
PUBLICATION_EVENT_RE = re.compile(
    r"(summer school|winter school|training course|workshop|webinar|symposium"
    r"|joshikai|mentoring|mentorship|stem leaders|diversity|internship|scholarship"
    r"|award|prize|anniversary|celebrat|members meet|meet in \w+ to review"
    r"|kicks? off|welcomes? new|appoint|obituary|in memoriam)",
    re.IGNORECASE,
)
PUBLICATION_NONPOWER_RE = re.compile(
    r"(plant breeding|insect pest|soils newsletter|animal production"
    r"|food safety|food irradiation|crop |livestock|fertili[sz]er"
    r"|freshwater|groundwater|nitrate|zoonot|veterinar)",
    re.IGNORECASE,
)


def gist_adds_nothing(gist: str, title_kr: str) -> bool:
    """gist 가 한국어 제목을 되풀이하기만 하면 참.

    v1 프롬프트가 "제목에서 읽어낼 수 있는 범위만"을 너무 곧이곧대로 받아 제목을
    한국어로 다시 쓴 것을 gist 로 냈다(실측: "원자력 안전을 위한 핵심 실험
    데이터세트 보존" → "원자력 안전 핵심 실험 데이터세트 보존"). 같은 말을 두 줄
    쓰면 목록만 길어지고 판단에는 보탬이 없다.

    v2 프롬프트가 문서 성격·범위를 쓰도록 바뀌었지만, 이미 캐시된 v1 gist 는
    다음 번역까지 남는다 — 그동안 화면에서 가린다.
    """
    gist, title_kr = (gist or "").strip(), (title_kr or "").strip()
    if not gist or not title_kr:
        return False
    squeeze = lambda text: "".join(text.split())
    a, b = squeeze(gist), squeeze(title_kr)
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


def publication_drop_reason(item: dict) -> str:
    """제외 사유. 빈 문자열이면 표시한다."""
    verdict = item.get("off_topic")
    if verdict is True:
        return str(item.get("off_topic_reason") or "off_topic")
    if verdict is False:
        # LLM 이 "관련 있음"으로 봤다면 제목 규칙으로 뒤집지 않는다. 규칙은
        # 제목 낱말만 보므로 'Workshop on Regulatory Harmonisation' 같은 것을
        # 잘못 잡는다 — 판정이 있는 항목에서는 규칙을 아예 태우지 않는다.
        return ""
    # 판정이 없는 항목(v1 캐시·번역 실패·한국어 원문)만 제목 규칙으로 거른다.
    title = str(item.get("title") or "")
    if PUBLICATION_EVENT_RE.search(title):
        return "event"
    if PUBLICATION_NONPOWER_RE.search(title):
        return "nonpower"
    return ""

# KEEI 인사이트 목차 ↔ 이슈 매칭.
#
# 점수만으로는 판정할 수 없다는 것이 실측으로 확인됐다(2026-08-02): 로컬 n-gram
# 코사인 상위권을 벤더명만 같은 오매칭이 차지했고(Rolls-Royce 0.323 > 진짜 같은
# 사건인 EIB·체르나보다 0.239), IDF 가중 토큰 중복도 3위부터 다른 규칙·다른
# 발전소가 섞였다. 발표·계획·건설 같은 흔한 토큰이 점수를 지배한다.
# 그래서 파이썬은 후보만 좁히고 판정은 keei_match(LLM)에 맡긴다.
KEEI_CANDIDATE_MIN_SHARED = 2      # 의미 토큰 공유 최소 개수
KEEI_CANDIDATES_PER_ISSUE = 2      # 이슈당 LLM 에 물어볼 최대 후보
# 총 상한은 품질 필터가 아니라 폭주 방지선이다. 점수는 진짜 매칭을 상위로
# 올리지 못한다(실측: 진짜 쌍이 134개 중 82위) — 상한을 낮게 잡으면 그냥
# 진짜 매칭을 버리는 셈이다. 판정은 캐시되고 KEEI 는 격주간이라 첫 빌드
# 이후 증분은 새 호 몫뿐이다.
KEEI_CANDIDATE_CAP = 150
KEEI_REFS_PER_ISSUE = 2


def _keei_match_tokens(text: str) -> set[str]:
    """매칭 판정에 쓸 의미 토큰 — 일반어는 버린다."""
    return {token for token in _text_tokens(text) if token not in _GENERIC_TAGS}


def _keei_shared(left: set[str], right: set[str]) -> set[str]:
    """공유 토큰 — 한국어 조사가 붙어 갈라진 같은 낱말을 접두 일치로 흡수한다.

    실측: '영덕군과' 와 '영덕군' 이 다른 토큰이 되어 같은 사건이 후보에서
    탈락할 뻔했다. 조사 목록을 두는 대신(원자'로' 처럼 낱말 끝과 구분이 안 됨)
    한쪽이 다른 쪽의 접두인 경우를 같은 낱말로 본다. 후보 생성 단계라
    과대 매칭은 LLM 이 걸러 주므로 재현율을 택한다.
    """
    shared = set()
    for token in left:
        if token in right:
            shared.add(token)
            continue
        for other in right:
            if len(token) >= 2 and len(other) >= 2 and (
                    token.startswith(other) or other.startswith(token)):
                shared.add(min(token, other, key=len))
                break
    return shared


def keei_entries(publications: dict) -> list[dict]:
    """발간물에서 KEEI 목차 항목을 펼친다. 제목 줄만 — 본문은 저장하지 않는다."""
    entries = []
    for publication in publications.get("items") or []:
        toc = publication.get("toc")
        if not isinstance(toc, dict):
            continue
        for text in [toc.get("issue_title") or ""] + list(toc.get("briefs") or []):
            text = str(text or "").strip()
            if text:
                entries.append({"text": text, "publication": publication})
    return entries


def keei_candidates(issue_rows: list[dict], entries: list[dict]) -> list[dict]:
    """IDF 가중 토큰 중복으로 LLM 에 물어볼 후보만 좁힌다.

    이 점수는 순위를 매기는 용도일 뿐 판정이 아니다 — 최종 판정은 LLM 이 한다.
    """
    if not issue_rows or not entries:
        return []
    issue_tokens = [
        (row, _keei_match_tokens(f"{row['title']} {row.get('summary', '')}"))
        for row in issue_rows
    ]
    entry_tokens = [(entry, _keei_match_tokens(entry["text"])) for entry in entries]

    document_frequency = Counter()
    for _, tokens in issue_tokens + entry_tokens:
        document_frequency.update(tokens)
    total = len(issue_tokens) + len(entry_tokens)

    def inverse_frequency(token: str) -> float:
        return math.log((1 + total) / (1 + document_frequency[token])) + 1.0

    candidates = []
    for row, tokens in issue_tokens:
        scored = []
        for entry, other in entry_tokens:
            shared = _keei_shared(tokens, other)
            if len(shared) < KEEI_CANDIDATE_MIN_SHARED:
                continue
            weight = sum(inverse_frequency(token) for token in shared)
            scored.append((weight / max(1.0, math.sqrt(len(other))), entry))
        scored.sort(key=lambda item: -item[0])
        for weight, entry in scored[:KEEI_CANDIDATES_PER_ISSUE]:
            candidates.append({
                "score": weight,
                "pair_id": f"{row['issue_id']}--{hashlib.sha1(entry['text'].encode('utf-8')).hexdigest()[:10]}",
                "issue_id": row["issue_id"],
                "issue_title": row["title"],
                "keei_item": entry["text"],
                "publication": entry["publication"],
            })
    # 상한에 걸릴 때만 점수를 쓴다 — 없는 것보다는 나은 순서일 뿐이다.
    candidates.sort(key=lambda row: (-row["score"], row["pair_id"]))
    kept = candidates[:KEEI_CANDIDATE_CAP]
    kept.sort(key=lambda row: row["pair_id"])  # 결정적 순서 — 캐시·배치 안정
    return kept


def attach_keei_refs(issue_rows: list[dict], publications: dict) -> dict:
    """같은 사건을 다루는 이슈 카드에 KEEI 인사이트 참조를 붙인다.

    LLM 이 same_event 로 판정한 것만 붙인다. 키가 없거나 호출이 실패하면 아무
    것도 붙이지 않는다 — 틀린 연결은 누락보다 해롭다.
    """
    entries = keei_entries(publications)
    candidates = keei_candidates(issue_rows, entries)
    if not candidates:
        return {"candidates": 0, "attached": 0, "status": "no_candidates"}

    verdicts, stats = keei_match.match_pairs([
        {"pair_id": row["pair_id"], "issue_title": row["issue_title"],
         "keei_item": row["keei_item"]}
        for row in candidates
    ])

    by_issue: dict[str, list[dict]] = defaultdict(list)
    for row in candidates:
        if verdicts.get(row["pair_id"]):
            by_issue[row["issue_id"]].append(row)

    attached = 0
    for row in issue_rows:
        matches = by_issue.get(row["issue_id"])
        if not matches:
            continue
        refs, seen = [], set()
        for match in matches:
            publication = match["publication"]
            if publication["url"] in seen:
                continue
            seen.add(publication["url"])
            refs.append({
                "title": publication.get("title", ""),
                "url": publication.get("url", ""),
                "date": publication.get("date", ""),
                "org_kr": publication.get("org_kr", ""),
                "item": match["keei_item"],
            })
            if len(refs) >= KEEI_REFS_PER_ISSUE:
                break
        row["keei_refs"] = refs
        attached += 1
    stats["attached"] = attached
    return stats


def load_publications(now: datetime | None = None) -> dict:
    """pubs_fetch.py 가 커밋한 발간물 상태 파일 → 웹 표시용 뷰.

    파일이 없거나 깨져도 빈 구조를 반환한다 — 발간물 부재가 사이트를 죽이면
    안 된다 (빈 배열이라도 publications.json 은 항상 생성되는 계약).
    """
    empty = {"generated_at": "", "items": [], "sources": {}}
    try:
        raw = json.loads((BOT_DIR / "publications.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(raw, dict):
        return empty
    now = now or datetime.now(KST)
    new_cutoff = (now - timedelta(days=PUBLICATION_NEW_DAYS)).strftime("%Y-%m-%d")
    items = []
    dropped: dict[str, int] = {}
    echoed = 0
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        reason = publication_drop_reason(item)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        display_date = str(item.get("date") or item.get("fetched_at") or "")
        org_kr = str(item.get("org_kr") or "")
        view = {
            "id": item.get("id") or "",
            "org": item.get("org") or "",
            "org_kr": PUBLICATION_ORG_ALIASES.get(org_kr, org_kr),
            "kind": item.get("kind") or "",
            "title": title,
            "url": url,
            "date": display_date,
            "is_new": bool(display_date and display_date >= new_cutoff),
        }
        for optional in ("pdf_url", "toc", "title_kr", "gist"):
            if item.get(optional):
                view[optional] = item[optional]
        if gist_adds_nothing(view.get("gist", ""), view.get("title_kr", "")):
            view.pop("gist", None)
            echoed += 1
        items.append(view)
    # 조용히 자르지 않는다 — 규칙이 과하게 잡으면 이 줄에서 먼저 티가 난다.
    if dropped:
        detail = " / ".join(f"{key} {count}건" for key, count in sorted(dropped.items()))
        print(f"[build_data] 발간물 제외 {sum(dropped.values())}건 ({detail})")
    if echoed:
        print(f"[build_data] 발간물 gist 숨김 {echoed}건 (제목 재진술)")
    return {
        "generated_at": now.isoformat(),
        "items": items,
        "sources": raw.get("last_checked") or {},
    }


def load_daily_leads() -> dict[str, dict]:
    """봇이 하루 1회 생성한 '오늘의 한 문장'. 없으면 빈 dict (히어로가 폴백)."""
    try:
        raw = json.loads((BOT_DIR / "daily_leads.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    leads = raw.get("leads")
    return leads if isinstance(leads, dict) else {}


def load_weekly_report(issue_rows: list[dict]) -> dict | None:
    """봇이 금요일에 저장한 주간 판세 리포트 → 화면용.

    문장마다 evidence_hashes 를 이슈 상세 링크로 바꾼다. 전역 key_events 만으로는
    어떤 근거가 어느 문장 것인지 알 수 없어 모든 문장에 같은 칩이 붙는다.
    """
    try:
        raw = json.loads((BOT_DIR / "weekly_reports.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reports = raw.get("reports")
    if not isinstance(reports, dict) or not reports:
        return None
    report = dict(reports[max(reports)])

    # 봇은 hash 앞 8자리만 남긴다(프롬프트 토큰 절약). 이슈 카탈로그는 전체
    # hash 로 색인돼 있어 _evidence_chips 를 그대로 쓰면 하나도 안 걸린다.
    by_short: dict[str, dict] = {}
    for row in issue_rows:
        for article in row.get("related_articles") or []:
            short = str(article.get("hash") or "")[:8]
            if short and short not in by_short:
                by_short[short] = row

    def chips(short_hashes) -> list[dict]:
        # 매핑 실패는 칩만 비우고 넘어간다 — 화면 전체가 깨지면 안 된다.
        out, seen = [], set()
        for short in short_hashes or []:
            row = by_short.get(str(short)[:8])
            if not row or row["issue_id"] in seen:
                continue
            seen.add(row["issue_id"])
            out.append({"issue_id": row["issue_id"], "title": row["title"]})
        return out[:2]

    for key in ("policy_shifts", "theme_moves"):
        rows = [r for r in (report.get(key) or []) if isinstance(r, dict)]
        for row in rows:
            row["evidence"] = chips(row.get("evidence_hashes"))
            row.pop("evidence_hashes", None)
        report[key] = rows
    report["key_events"] = [r for r in (report.get("key_events") or [])
                            if isinstance(r, dict)]
    report["watchpoints"] = [str(w) for w in (report.get("watchpoints") or []) if w]
    # 이슈 수는 여기서 다시 센다. 봇은 제목 정규화로 어림잡을 수밖에 없지만
    # (임베딩·LLM 병합 결과가 없다) 웹에는 실제 병합 결과가 있다.
    merged = merged_issue_count(issue_rows, report.get("week_start"), report.get("week_end"))
    if merged is not None:
        report["source_issue_count"] = merged
    return report


def merged_issue_count(issue_rows: list[dict], start: object, end: object) -> int | None:
    """그 주에 움직인 고유 이슈 수. 기사 수를 쓰면 후속 보도가 많은 주가
    실제보다 풍성해 보인다."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    count = 0
    for row in issue_rows:
        last_seen = str(row.get("last_seen") or "")
        if start <= last_seen <= end:
            count += 1
    return count


def collect_open_questions(issue_rows: list[dict], limit: int = 5) -> list[dict]:
    """그 주의 '아직 확정되지 않은 것' 모음.

    그냥 모으면 같은 내용이 여러 기사에서 중복된다. 이슈 단위로 한 번씩만 세고,
    최신·중요도순 상위 몇 개만 남긴다.
    """
    seen: set[str] = set()
    out: list[dict] = []
    ordered = sorted(
        issue_rows,
        key=lambda row: (row.get("last_seen") or "",
                         row.get("importance") == "must_read"),
        reverse=True,
    )
    for row in ordered:
        text = str(row.get("open_question") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"text": text,
                    "evidence": [{"issue_id": row["issue_id"], "title": row["title"]}]})
        if len(out) >= limit:
            break
    return out


def selection_view(stats: dict | None) -> dict:
    """봇이 남긴 선정 통계를 브리핑 행에 실을 형태로.

    이슈 0건일 때 '기준 미달'·'후보 없음'·'파이프라인 실패'를 화면에서 가르려면
    이 값이 필요하다. 통계가 없는 날(기능 도입 이전)은 None 으로 둬서 프론트가
    단정하지 않게 한다 — 0 으로 채우면 '후보가 없었다'는 거짓 진술이 된다.
    """
    if not stats:
        return {"candidate_count": None, "below_floor_count": None,
                "pipeline_status": None, "pipeline_ran_at": None}

    def total(key: str) -> int:
        return sum(int((stats.get(region) or {}).get(key) or 0)
                   for region in ("domestic", "overseas"))

    return {
        "candidate_count": total("candidate_count"),
        "below_floor_count": total("below_floor_count"),
        "pipeline_status": stats.get("pipeline_status"),
        "pipeline_ran_at": stats.get("generated_at"),
    }


def empty_briefing_row(briefing_date: str, stats: dict | None) -> dict:
    """선정이 통째로 0건인 날의 브리핑 행.

    브리핑 날짜는 '발송된 기사'에서만 나오기 때문에(dates = news_items 의
    briefing_date), 하한에 전부 걸린 날은 briefings 에 행 자체가 생기지 않는다.
    그러면 화면이 below_floor_count 를 볼 수 없어 '기준 미달' 상태가 영영 안 뜬다.
    후보가 있었다는 기록이 있으면 빈 행이라도 남긴다.
    """
    return {
        "date": briefing_date,
        "article_count": 0,
        "issue_count": 0,
        **selection_view(stats),
        "domestic_count": 0,
        "overseas_count": 0,
        "primary_source_count": 0,
        "tracked_issue_count": 0,
        "verified_issue_count": 0,
        "headline": "",
        "headline_kind": "empty",
        "headline_evidence": [],
        "changed_issue_count": 0,
        "highlights": [],
        "highlight_issues": [],
        "issues": [],
    }


def build_briefings(news_items: list[dict], issues: list[dict], checked_at: str = "",
                    daily_leads: dict | None = None,
                    selection_stats: dict | None = None,
                    selection_overrides: dict | None = None) -> list[dict]:
    # 오래된 날부터 돈다 — 히어로가 '어제 무엇을 말했는지' 알아야 같은 사건을
    # 이틀 연속 올리지 않는다. 반환 직전에 최신순으로 뒤집는다(briefings[0] 이
    # 최신이라는 계약은 스모크·앱이 함께 의존한다).
    dates = sorted({item["briefing_date"] for item in news_items if item.get("briefing_date")})
    briefings = []
    previous_headline = ""

    for briefing_date in dates:
        current_articles = [item for item in news_items if item.get("briefing_date") == briefing_date]
        issue_rows = []
        hidden_hashes: set[str] = set()
        for issue in issues:
            current = [member for member in issue["members"] if member["briefing_date"] == briefing_date]
            if not current:
                continue
            # 편집 override ② — 판정 단위는 기사가 아니라 **이슈 클러스터**다.
            # 기사 하나만 지우면 같은 클러스터의 다른 멤버가 briefing_date 를 갖고
            # 있어 카드가 그대로 남는다. 이슈 병합은 LLM 검수까지 거친 2차 결과이므로
            # 여기(=최종 클러스터)에서 적용해야 올바른 묶음에 걸린다.
            verdict = override_verdict(current, briefing_date, selection_overrides or {})
            if verdict == "hide":
                hidden_hashes.update(str(member.get("hash") or "") for member in current)
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
                # 대표 기사가 아니라 이슈 전체에서 고른다 — 미확정 내용은 공식
                # 기사에만 있고 대표 기사에는 없는 경우가 흔하다.
                "open_question": pick_open_question(timeline),
                "latest_change": change_line_for_card(
                    current, history, representative.get("summary", "")
                ),
                "verification": verification_state(timeline, checked_at),
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
                # 사람이 고정한 순위. 화면에는 표시하지 않는다 — 편집 사유는
                # 대외 공개용 문장이 아니다.
                "editor_pin": {"promote": 1, "demote": -1}.get(verdict, 0),
            })

        # 숨긴 이슈의 기사는 그날 집계에서도 빠져야 한다 — 카드는 사라졌는데
        # '오늘 수집 기사 N건'만 그대로면 화면이 스스로를 부정한다.
        if hidden_hashes:
            current_articles = [item for item in current_articles
                                if str(item.get("hash") or "") not in hidden_hashes]

        order_issue_rows(issue_rows)

        lead = daily_lead(issue_rows, previous_headline)
        # 봇이 그날 이슈 전체를 보고 만든 종합 문장이 있으면 그것이 히어로가 된다.
        # 이슈 한 건의 문장으로는 '오늘 무엇이 달라졌는가'에 답할 수 없다.
        headline_evidence: list[dict] = []
        stored_lead = (daily_leads or {}).get(briefing_date) or {}
        synthesis = _fit_synthesis(stored_lead.get("lead"))
        # 공허한 종합 문장은 쓰지 않는다 — 구체적인 이슈 제목보다 못하다.
        if synthesis and synthesis_is_substantive(synthesis, issue_rows):
            lead = {"headline": synthesis.rstrip(".!?"), "kind": "synthesis"}
            headline_evidence = _evidence_chips(
                stored_lead.get("evidence") or [], issue_rows
            )
        previous_headline = lead["headline"]
        briefings.append({
            "date": briefing_date,
            "article_count": len(current_articles),
            "issue_count": len(issue_rows),
            **selection_view((selection_stats or {}).get(briefing_date)),
            "domestic_count": sum(1 for item in current_articles if item.get("region") == "국내"),
            "overseas_count": sum(1 for item in current_articles if item.get("region") == "해외"),
            "primary_source_count": sum(1 for item in current_articles if _is_primary_source(item)),
            "tracked_issue_count": sum(1 for row in issue_rows if row.get("previous_article_count", 0) > 0),
            "verified_issue_count": sum(
                1 for row in issue_rows
                if row["verification"]["status"] in {"official", "corroborated"}
            ),
            "headline": lead["headline"],
            "headline_kind": lead["kind"],
            "headline_evidence": headline_evidence,
            "changed_issue_count": sum(
                1 for row in issue_rows if "→" in str(row.get("latest_change") or "")
            ),
            "highlights": [row["title"] for row in issue_rows[:3]],
            "highlight_issues": [
                {"issue_id": row["issue_id"], "title": row["title"]}
                for row in issue_rows[:3]
            ],
            "issues": issue_rows,
        })
    # 하한에 전부 걸려 발송이 0건인 날은 위 루프가 못 만든다(날짜 자체가 기사에서
    # 나오므로). 통계에만 남은 날을 빈 행으로 채워 화면이 사유를 말할 수 있게 한다.
    # 기사가 하나도 없던 날짜 범위 밖까지 거슬러 올라가지는 않는다.
    if selection_stats and dates:
        floor_date = min(dates)
        for day, stats in selection_stats.items():
            if day in dates or day < floor_date:
                continue
            briefings.append(empty_briefing_row(day, stats))
        briefings.sort(key=lambda row: row["date"])
    briefings.reverse()  # 최신순 — briefings[0] 이 최신이라는 계약
    return briefings


def build_issue_catalog(issues: list[dict], latest_briefing_date: str, checked_at: str = "") -> list[dict]:
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
            "open_question": pick_open_question(timeline),
            "latest_change": change_line_for_card(
                current, history, representative.get("summary", "")
            ),
            "verification": verification_state(timeline, checked_at),
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


def _issue_meta_description(issue: dict) -> str:
    description = " ".join(
        str(issue.get("summary") or issue.get("latest_change") or "원자력 정책·산업 이슈의 변화와 근거를 추적합니다.").split()
    )
    return description if len(description) <= 170 else f"{description[:167].rstrip()}…"


def build_issue_pages(issue_catalog: list[dict]) -> int:
    """이슈별 OG 메타데이터를 가진 정적 진입 페이지를 생성한다."""
    public_dir = (SITE_DIR / "public").resolve()
    issue_dir = (public_dir / "issue").resolve()
    if issue_dir.parent != public_dir or issue_dir.name != "issue":
        raise RuntimeError(f"unsafe issue page directory: {issue_dir}")
    if issue_dir.exists():
        shutil.rmtree(issue_dir)
    issue_dir.mkdir(parents=True)

    template = (public_dir / "index.html").read_text(encoding="utf-8")
    generated = 0
    for issue in issue_catalog:
        issue_id = str(issue.get("issue_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue_id):
            continue
        title = str(issue.get("title") or "Nuclens 이슈")
        description = _issue_meta_description(issue)
        issue_url = f"{SITE_URL}/issue/{quote(issue_id, safe='-_')}"
        page = template
        replacements = {
            '<meta name="description" content="Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다.">':
                f'<meta name="description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:type" content="website">': '<meta property="og:type" content="article">',
            '<meta property="og:title" content="Nuclens · 원자력 정책·산업 이슈 트래커">':
                f'<meta property="og:title" content="{html_escape(title, quote=True)} | Nuclens">',
            '<meta property="og:description" content="원자력 이슈를 연결하고, 변화를 추적합니다.">':
                f'<meta property="og:description" content="{html_escape(description, quote=True)}">',
            '<meta property="og:url" content="https://nuclens.pages.dev/">':
                f'<meta property="og:url" content="{html_escape(issue_url, quote=True)}">',
            '<link rel="canonical" href="https://nuclens.pages.dev/">':
                f'<link rel="canonical" href="{html_escape(issue_url, quote=True)}">',
            '<title>Nuclens · 원자력 정책·산업 이슈 트래커</title>':
                f'<title>{html_escape(title)} | Nuclens</title>',
        }
        for old, new in replacements.items():
            if old not in page:
                raise RuntimeError(f"issue page metadata template is missing: {old}")
            page = page.replace(old, new, 1)
        structured_data = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": description,
            "datePublished": issue.get("first_seen") or "",
            "dateModified": issue.get("last_seen") or "",
            "mainEntityOfPage": issue_url,
            "publisher": {"@type": "Organization", "name": "Nuclens", "url": SITE_URL},
        }
        json_ld = json.dumps(structured_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        page = page.replace("</head>", f'  <script type="application/ld+json">{json_ld}</script>\n</head>', 1)
        page_dir = issue_dir / issue_id
        page_dir.mkdir()
        (page_dir / "index.html").write_text(page, encoding="utf-8")
        generated += 1
    return generated


def build_rss(briefings: list[dict], generated_at: datetime) -> bytes:
    """최신 이슈 카드를 보고서형 RSS 2.0으로 직렬화한다."""
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "nuclens 원자력 정책 브리핑"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = "이슈 단위로 추적하는 원자력 정책 브리핑"
    ET.SubElement(channel, "language").text = "ko"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(generated_at)
    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {"href": f"{SITE_URL}/rss.xml", "rel": "self", "type": "application/rss+xml"},
    )

    for briefing in briefings[:14]:
        briefing_date = briefing.get("date") or ""
        try:
            published = datetime.combine(date.fromisoformat(briefing_date), datetime.min.time(), KST)
        except ValueError:
            published = generated_at
        for issue in briefing.get("issues", [])[:20]:
            issue_id = str(issue.get("issue_id") or "")
            link = f"{SITE_URL}/issue/{quote(issue_id, safe='-_')}"
            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "title").text = str(issue.get("title") or "")
            ET.SubElement(item, "link").text = link
            ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"{issue_id}:{briefing_date}"
            ET.SubElement(item, "pubDate").text = format_datetime(published)
            description = []
            if issue.get("summary"):
                description.append(f"핵심: {issue['summary']}")
            if issue.get("latest_change"):
                description.append(f"새로 확인: {issue['latest_change']}")
            if issue.get("implication"):
                description.append(f"의미(AI 해석): {issue['implication']}")
            ET.SubElement(item, "description").text = "\n".join(description)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build() -> None:
    records = load_archive()
    validate_archive_records(records)
    deliveries = load_deliveries()
    now = datetime.now(KST)
    generation_id = GENERATION_ID or now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        region, region_source = infer_region(record, countries)
        canonical_tags = list(dict.fromkeys(
            _canonical_tag(tag) for tag in (record.get("tags") or []) if _canonical_tag(tag)
        ))
        visible.append({
            "hash": record.get("hash", ""),
            "date": article_date,
            "article_date": article_date,
            "briefing_date": delivery.get("date") if delivery else None,
            "region": region,
            "region_source": region_source,
            "importance": importance,
            "section": record.get("section", ""),
            "category": record.get("category", ""),
            "title_kr": record.get("title_kr") or record.get("title", ""),
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "implication": record.get("implication", ""),
            "why_important": record.get("why_important", ""),
            "open_question": record.get("open_question", ""),
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
            "source_type": record.get("source_type", "unknown"),
            "evidence_role": record.get("evidence_role", "unknown"),
            "event_date": record.get("event_date"),
            "event_date_type": record.get("event_date_type", "unknown"),
            "event_date_precision": record.get("event_date_precision", "unknown"),
            "event_date_source": record.get("event_date_source", "unknown"),
            "selection_score": delivery.get("score") if delivery else None,
            "selection_reasons": selection_reasons(delivery, record),
            # 기존 프론트와의 호환용. 새 화면은 briefing_date를 사용한다.
            "promoted": delivery.get("date") if delivery else None,
        })
    # 편집 override ① — 클러스터링 전에 promote 대상을 그날 후보로 올린다.
    # 정렬 단계에서 하면 늦다: 미발송 기사는 briefing_date 가 없어 배열에 아예 없다.
    selection_overrides = load_selection_overrides()
    promoted = apply_promotions(visible, selection_overrides)
    if promoted:
        print(f"[overrides] 편집 승격 {promoted}건")

    visible.sort(key=lambda item: (item["article_date"], item.get("briefing_date") or ""), reverse=True)
    news_items = [item for item in visible if item["article_date"] >= cutoff_news]

    embeddings = load_embeddings_cache()
    local_embeddings = build_local_embeddings(news_items)
    match_overrides = load_match_overrides()
    review_candidates: list[dict] = []
    issues = cluster_selected_articles(
        news_items,
        embeddings,
        local_embeddings,
        match_overrides,
        review_candidates,
    )

    # 1차 묶음에서 나온 회색지대 쌍을 LLM 에 한 번 물어보고, 같은 사건으로
    # 판정된 것만 오버라이드로 넣어 다시 묶는다. 클러스터링은 순수 계산이라
    # 두 번 돌려도 비용이 없다. 판정이 0건이면 2차 실행 자체를 건너뛴다.
    llm_verdicts, llm_stats = issue_review.review_pairs(review_candidates)
    llm_approved = {pair_id for pair_id, same in llm_verdicts.items() if same}
    # 기각도 2차 묶음에 반영한다. 승인만 넘기면 "다른 사건"이라는 판정이 버려져,
    # 유사도만으로 붙는 경로가 그대로 살아 과병합이 난다(위 거부권 주석 참고).
    # ``same`` 이 None 인 실패 건은 어느 쪽으로도 쓰지 않는다.
    llm_rejected = {pair_id for pair_id, same in llm_verdicts.items() if same is False}
    if llm_approved or llm_rejected:
        match_overrides = {
            **match_overrides,
            "llm_approved": llm_approved,
            "llm_rejected": llm_rejected,
        }
        review_candidates = []
        issues = cluster_selected_articles(
            news_items,
            embeddings,
            local_embeddings,
            match_overrides,
            review_candidates,
        )
    print(f"[build_data] 이슈 병합 LLM 검수: 후보 {llm_stats['candidates']}쌍 "
          f"(캐시 {llm_stats['from_cache']} / 신규 {llm_stats['asked']} / "
          f"호출 {llm_stats['calls']}회) → 병합 {llm_stats['approved']} "
          f"분리 {llm_stats['rejected']} 실패 {llm_stats['failed']} [{llm_stats['status']}]")

    review_candidates.sort(
        key=lambda row: (row.get("candidate_score") or 0, row.get("right_date") or ""),
        reverse=True,
    )
    checked_at = now.isoformat()
    selection_stats = load_selection_stats()
    briefings = build_briefings(news_items, issues, checked_at, load_daily_leads(),
                                selection_stats, selection_overrides)
    report_unmatched_overrides(selection_overrides)
    issue_catalog = build_issue_catalog(
        issues,
        briefings[0]["date"] if briefings else "",
        checked_at,
    )
    publications = load_publications(now)
    keei_stats = attach_keei_refs(issue_catalog, publications)
    print(f"[build_data] KEEI 매칭: 후보 {keei_stats.get('candidates', 0)}쌍 "
          f"(캐시 {keei_stats.get('from_cache', 0)} / 질의 {keei_stats.get('asked', 0)} / "
          f"호출 {keei_stats.get('calls', 0)}회) → 연결 {keei_stats.get('attached', 0)}건 "
          f"[{keei_stats.get('status', '')}]")
    keei_by_issue = {
        row["issue_id"]: row["keei_refs"]
        for row in issue_catalog if row.get("keei_refs")
    }
    for briefing in briefings:
        for row in briefing["issues"]:
            refs = keei_by_issue.get(row["issue_id"])
            if refs:
                row["keei_refs"] = refs

    # 트렌드는 기존 집계를 유지하되 커버리지가 낮으면 프론트에서 숨길 수 있게 메타를 제공한다.
    trend_pool = news_items
    day7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    day14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    day30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    tags_7, tags_prev7, tags_30, tags_all_before7 = Counter(), Counter(), Counter(), Counter()
    topic_by_week: dict[str, Counter] = defaultdict(Counter)
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
    country_issue_30 = count_country_issues(issues, day30)

    trend = {
        # 금요일 주간 판세 리포트. 없으면 None → 프론트가 기존 정량 트렌드만 그린다
        # (목요일에 빈 탭이 되지 않게 하는 폴백).
        "weekly_report": load_weekly_report(issue_catalog),
        # 이번 주 움직인 이슈 — 키워드 단위 흐름 해석을 대체한다(중복 제거)
        "weekly_movers": build_weekly_movers(
            issue_catalog, briefings[0]["date"] if briefings else ""),
        "open_questions": collect_open_questions(issue_catalog),
        "top_tags_7d": [{"tag": tag, "count": count} for tag, count in tags_7.most_common(10)],
        "top_tags_30d": [{"tag": tag, "count": count} for tag, count in tags_30.most_common(10)],
        "rising": rising[:10],
        "new_tags": new_tags[:10],
        "countries_30d": [
            {"country": country, "count": count}
            for country, count in country_issue_30.most_common(10)
        ],
        "countries_30d_unit": "issue",
        "countries_30d_counting": "distinct_issue_per_country",
        "weeks": weeks,
        "topic_series": topic_series,
    }

    topic_coverage = (sum(1 for item in news_items if item["topics"]) / len(news_items)) if news_items else 0
    country_coverage = (
        sum(
            1 for item in news_items
            if set(item["countries"]) - {"UNSPECIFIED"}
        ) / len(news_items)
    ) if news_items else 0
    country_unspecified_count = sum(
        1 for item in news_items if "UNSPECIFIED" in set(item["countries"])
    )
    heuristic_topic_count = sum(1 for item in news_items if item["topic_source"] == "heuristic-v1")
    heuristic_country_count = sum(
        1 for item in news_items if not item["country_source"].startswith("native")
    )
    region_source_counts = Counter(item.get("region_source", "unknown") for item in news_items)
    region_country_mismatch_count = sum(
        1
        for item in news_items
        if (set(item.get("countries") or []) - {"OTHER"})
        and (
            ("KR" in set(item.get("countries") or []) and item.get("region") != "국내")
            or ("KR" not in set(item.get("countries") or []) and item.get("region") != "해외")
        )
    )
    selected_items = [item for item in news_items if item.get("briefing_date")]
    remote_embedded_selected_count = sum(
        1 for item in selected_items if item["hash"] in embeddings
    )
    embedded_selected_count = sum(
        1 for item in selected_items if item["hash"] in local_embeddings
    )
    match_methods = Counter(
        diag.get("method", "none")
        for issue in issues
        for diag in issue.get("match_diagnostics", [])
    )
    cross_date_issue_count = sum(
        1 for issue in issues
        if len({member["briefing_date"] for member in issue["members"]}) > 1
    )
    latest_briefing = briefings[0] if briefings else {"issues": []}
    latest_tracked_issue_count = sum(
        1 for issue in latest_briefing.get("issues", [])
        if issue.get("previous_article_count", 0) > 0
    )
    latest_issue_count = len(latest_briefing.get("issues", []))
    meta = {
        "generation_id": generation_id,
        "generated_at": now.isoformat(),
        "archive_total": len(records),
        "visible_total": len(news_items),
        "briefing_total": len(briefings),
        "issue_catalog_total": len(issue_catalog),
        "latest_briefing_date": briefings[0]["date"] if briefings else "",
        "date_min": min((item["article_date"] for item in visible), default=""),
        "date_max": max((item["article_date"] for item in visible), default=""),
        "importance_counts": dict(Counter(record.get("importance", "") for record in records)),
        "source_type_counts": dict(Counter(record.get("source_type", "unknown") for record in records)),
        "evidence_role_counts": dict(Counter(record.get("evidence_role", "unknown") for record in records)),
        "publisher_coverage": round(
            sum(1 for record in records if record.get("publisher")) / len(records), 4
        ) if records else 0,
        "topic_coverage": round(topic_coverage, 4),
        "country_coverage": round(country_coverage, 4),
        "taxonomy_version": "topic-v1-country-scope-v2",
        "heuristic_topic_count": heuristic_topic_count,
        "heuristic_country_count": heuristic_country_count,
        "country_unspecified_count": country_unspecified_count,
        "region_classification_version": "country-first-v1",
        "region_source_counts": dict(region_source_counts),
        "region_country_mismatch_count": region_country_mismatch_count,
        "trend_ready": topic_coverage >= 0.8 and country_coverage >= 0.8 and len(weeks) >= 2,
        "issue_matching_version": "hybrid-review-v4",
        "issue_window_days": ISSUE_WINDOW_DAYS,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_cache_entries": len(embeddings),
        "remote_embedding_selected_count": remote_embedded_selected_count,
        "local_embedding_selected_count": embedded_selected_count,
        "embedding_selected_count": embedded_selected_count,
        "embedding_selected_coverage": round(
            embedded_selected_count / len(selected_items), 4
        ) if selected_items else 0,
        "issue_match_methods": dict(match_methods),
        "cross_date_issue_count": cross_date_issue_count,
        "latest_briefing_issue_count": latest_issue_count,
        "latest_briefing_tracked_issue_count": latest_tracked_issue_count,
        "latest_briefing_tracking_rate": round(
            latest_tracked_issue_count / latest_issue_count, 4
        ) if latest_issue_count else 0,
        "issue_review_candidate_count": len(review_candidates),
        "issue_match_approved_count": len(match_overrides["approved"]),
        "issue_match_rejected_count": len(match_overrides["rejected"]),
    }

    insights_path = BOT_DIR / "trend_insights.json"
    try:
        insights = json.loads(insights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        insights = {"generated_at": "", "items": []}
    insights = prepare_insights(insights, news_items)

    issue_audit = {
        "generated_at": now.isoformat(),
        "matching_version": "hybrid-review-v4",
        "issue_window_days": ISSUE_WINDOW_DAYS,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_threshold": ISSUE_EMBEDDING_THRESHOLD,
        "embedding_candidate_threshold": ISSUE_EMBEDDING_CANDIDATE_THRESHOLD,
        "local_embedding_candidate_threshold": LOCAL_EMBEDDING_CANDIDATE_THRESHOLD,
        "embedding_cache_entries": len(embeddings),
        "embedding_selected_count": embedded_selected_count,
        "remote_embedding_selected_count": remote_embedded_selected_count,
        "llm_review": llm_stats,
        "llm_approved": sorted(llm_approved),
        # 기각도 남긴다 — 거부권이 실제로 걸렸는지 audit 만 보고 확인할 수 있어야 한다.
        "llm_rejected": sorted(llm_rejected),
        "review_candidates": review_candidates,
        "overrides": {
            "approved": sorted(match_overrides["approved"]),
            "rejected": sorted(match_overrides["rejected"]),
        },
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

    # Cloudflare Pages의 flat 배포도 manifest/status를 항상 제공한다. 프론트가
    # 존재하지 않는 선택 파일을 매번 요청해 404를 남기지 않도록 하는 계약이다.
    manifest = {
        "generation_id": generation_id,
        "generated_at": now.isoformat(),
        "base_path": "",
    }
    status = {**system_status(records, selection_stats, now),
              "generation_id": generation_id}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = (
        ("news.json", news_items),
        ("briefings.json", briefings),
        ("issues.json", issue_catalog),
        ("trend.json", trend),
        ("meta.json", meta),
        ("insights.json", insights),
        ("publications.json", publications),
        ("issue_audit.json", issue_audit),
        ("manifest.json", manifest),
        ("status.json", status),
    )
    for name, payload in outputs:
        (OUT_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    issue_page_count = build_issue_pages(issue_catalog)
    (SITE_DIR / "public" / "rss.xml").write_bytes(build_rss(briefings, now))

    selected_count = sum(briefing["article_count"] for briefing in briefings)
    issue_count = sum(briefing["issue_count"] for briefing in briefings)
    print(
        f"[build] 아카이브 {len(records)}건 → 표시 {len(news_items)}건 → "
        f"브리핑 기사 {selected_count}건 / 이슈 카드 {issue_count}개 / 상세 페이지 {issue_page_count}개 → {OUT_DIR}"
    )


if __name__ == "__main__":
    build()
