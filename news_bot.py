import difflib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse, quote_plus

# batch 큐레이션용 REST 클라이언트 (429 백오프 재시도 내장 — SDK 무재시도 문제 회피)
import gemini_client
from gemini_client import (
    GeminiError,
    GeminiTruncated,
    call_json as gemini_call_json,
    is_available as gemini_rest_available,
)
from ranking import prior_coverage_count, sanitize_features
import entity_match
import news_archive
from data_quality import (
    clean_text,
    curation_errors,
    first_complete_sentence,
    implication_is_hollow,
    invalid_url_reason,
    legacy_url_hash,
    normalize_event_date_fields,
    normalize_url,
    source_profile,
    split_title_publisher,
    title_key,
    url_hash as canonical_url_hash,
)
from embedding_pipeline import (
    DEFAULT_CACHE_FILE as EMBEDDINGS_CACHE_FILE,
    get_or_compute_embedding as pipeline_get_or_compute_embedding,
    load_cache as load_embedding_store,
    save_cache as save_embedding_store,
)

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

KST = timezone(timedelta(hours=9))
LOOKBACK_HOURS = 6
DEDUP_RETENTION_DAYS = 14
STATE_FILE = Path("sent.json")
KEYWORDS_FILE = Path("keywords.json")
REPORTS_KB_FILE = Path("reports_kb.json")
SEMANTIC_DEDUP_THRESHOLD = 0.85
CURATED_CACHE_FILE = Path("curated.json")
DIGEST_QUEUE_FILE = Path("digest_queue.json")
# 브리핑 발송 기록과 같은 파일을 쓴다. 크롤 단계의 큐레이션 유실도 결국 '그날 무엇이
# 브리핑에 못 올라갔나'의 일부라 같은 타임라인에 있어야 대조가 된다.
DELIVERY_LOG_FILE = Path("delivery_log.jsonl")

NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

DOMAIN_SCORE = {
    "hani.co.kr": 9, "chosun.com": 9, "joongang.co.kr": 9,
    "donga.com": 9, "khan.co.kr": 9, "hankookilbo.com": 9,
    "kmib.co.kr": 9, "munhwa.com": 9, "seoul.co.kr": 9,
    "mk.co.kr": 8, "hankyung.com": 8, "etnews.com": 8,
    "sedaily.com": 8, "fnnews.com": 8, "edaily.co.kr": 7,
    "mt.co.kr": 7, "asiae.co.kr": 7, "businesspost.co.kr": 7,
    "electimes.com": 9, "ekn.kr": 9, "energy-news.co.kr": 8,
    "epj.co.kr": 8, "energytimes.kr": 8, "energydaily.co.kr": 7,
    "yna.co.kr": 8, "newsis.com": 7, "news1.kr": 7, "yonhapnewstv.co.kr": 7,
    "kbs.co.kr": 7, "imbc.com": 7, "sbs.co.kr": 7, "ytn.co.kr": 7,
    "jtbc.co.kr": 7, "tvchosun.com": 6, "ichannela.com": 6, "mbn.co.kr": 6,
    "newspim.com": 5, "ajunews.com": 5,
}
DEFAULT_SCORE = 4
MIN_SCORE = 4

# 공식 원문을 제공하는 정부·규제기관·국제기구·사업자.
# 전문언론(WNN·NucNet·ANS)은 신뢰도는 높아도 원발표처가 아니므로 포함하지 않는다.
TIER1_DOMAINS = {
    "nssc.go.kr", "motie.go.kr", "msit.go.kr", "korea.kr",
    "khnp.co.kr", "kaeri.re.kr", "kins.re.kr", "korad.or.kr",
    "iaea.org", "world-nuclear.org", "oecd-nea.org", "nrc.gov",
    "energy.gov", "iea.org", "nei.org",
}

# 기관 site: 검색도 Google News '관련도순' 문제 동일 (2026-07-10 게토차:
# 검색 RSS 쓸 땐 반드시 when: 연산자). 보도자료는 인덱싱이 늦을 수 있어 2d 버퍼.
RSS_SOURCES = [
    {"url": "https://www.iaea.org/feeds/topnews", "name": "IAEA Top News",
     "domain_label": "iaea.org"},
    {"url": "http://www.world-nuclear-news.org/rss", "name": "WNN",
     "domain_label": "world-nuclear-news.org"},
    {"url": "https://www.ans.org/news/feed/", "name": "ANS Nuclear Newswire",
     "domain_label": "ans.org"},
    {"url": "https://news.google.com/rss/search?q=site:khnp.co.kr%20when:2d&hl=ko&gl=KR&ceid=KR:ko",
     "name": "한수원 보도자료", "domain_label": "khnp.co.kr"},
    {"url": "https://news.google.com/rss/search?q=site:nssc.go.kr%20when:2d&hl=ko&gl=KR&ceid=KR:ko",
     "name": "원안위 보도자료", "domain_label": "nssc.go.kr"},
    {"url": "https://news.google.com/rss/search?q=site:motie.go.kr%20when:2d&hl=ko&gl=KR&ceid=KR:ko",
     "name": "산업부 보도자료", "domain_label": "motie.go.kr"},
    {"url": "https://news.google.com/rss/search?q=site:kaeri.re.kr%20when:2d&hl=ko&gl=KR&ceid=KR:ko",
     "name": "원자력연구원 보도자료", "domain_label": "kaeri.re.kr"},
]

# ---- 해외 Tier 1 추가 (2026-07-31) ------------------------------------------
# 사내 카톡방 7개월 큐레이션 분석(nuclear-news-web/research/)의 실측 빈도 상위 출처.
# 전용 RSS가 검증된 곳은 직접, 없는 곳은 검증된 Google News site:+when: 패턴으로 우회.
# 보류: NHK(구글 인덱싱 부실·일반 피드 노이즈 과다), NRC 직접 피드(403), 電気新聞·FT(페이월).
RSS_SOURCES += [
    # 원자력 전문 통신 — 카톡방 최다 출처(7개월 402회). 공개 피드 검증 완료(15건/pub 정상)
    {"url": "https://www.nucnet.org/feed", "name": "NucNet",
     "domain_label": "nucnet.org"},
    # 프랑스 원자력학회 — EPR2·SMR·프랑스 정책 (프랑스어 → Gemini가 한국어 요약)
    {"url": "https://www.sfen.org/feed/", "name": "SFEN",
     "domain_label": "sfen.org"},
    # 미 에너지부 공식 — 전 에너지원 피드라 비원자력 포함, 큐레이션 noise 필터가 거름
    {"url": "https://www.energy.gov/rss.xml", "name": "DOE",
     "domain_label": "energy.gov"},
]
# Reuters는 공개 RSS 폐지, La Tribune은 섹션 피드 없음 → Google News 우회 (실측 12~18건/일)
_REUTERS_Q = quote_plus('site:reuters.com ("nuclear power" OR reactor OR SMR OR uranium) when:1d')
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_REUTERS_Q}&hl=en-US&gl=US&ceid=US:en",
    "name": "Reuters 원자력", "domain_label": "reuters.com",
})
_LATRIBUNE_Q = quote_plus("site:latribune.fr (nucléaire OR EDF OR EPR) when:2d")
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_LATRIBUNE_Q}&hl=fr&gl=FR&ceid=FR:fr",
    "name": "La Tribune 원자력", "domain_label": "latribune.fr",
})

# ---- 사내 큐레이션 코퍼스 격차 보완 (2026-08-01) ------------------------------
# 동료 큐레이션 1,874건(nuclear-news-web/research/evernote-details.json)에 나오지만
# 봇이 걷지 않던 매체. 후보를 전부 실측한 뒤 통과한 것만 넣는다.
#
# 넣지 않은 것과 이유 (재시도 전에 이 목록부터 볼 것):
#   NHK(코퍼스 74건)  구글이 site:nhk.or.jp 에 원자력 쿼리를 못 태운다. 실측 6건이
#                     전부 지역방송 편성표. 직접 피드(cat0)는 일반 뉴스라 노이즈 과다.
#   KBA Europe(43건)  직접 RSS 500, 구글 인덱싱 0건. 접근 경로 자체가 없다.
#   電気新聞(31건)     페이월. site: 쿼리로 100건 나오지만 지진·정전 등 일반 전력
#                     기사고 원자력 필터가 먹지 않는다.
#   National Interest(21건) 잠수함·지정학 기사 위주로 주제가 어긋난다.
#   Le Figaro(9건)    site: 쿼리가 키워드를 못 거른다(화재·풍력·Fed 혼입).
RSS_SOURCES += [
    # 전력 전문지 — 실측 10건 중 8건이 원자력. 코퍼스 21건.
    {"url": "https://www.powermag.com/feed/", "name": "POWER Magazine",
     "domain_label": "powermag.com"},
    # 에너지 섹션 피드 — 비원자력이 섞이지만 DOE 피드와 같이 큐레이션 noise 필터가
    # 거른다. 코퍼스 27건.
    {"url": "https://www.lemonde.fr/energies/rss_full.xml", "name": "Le Monde 에너지",
     "domain_label": "lemonde.fr"},
]
# FT·Les Échos·E&E News는 공개 RSS가 없거나 403 → 검증된 Google News site: 패턴.
# FT는 페이월이라 본문이 없다. 제목·헤드라인 수준의 추적용으로만 쓴다.
_FT_Q = quote_plus('site:ft.com ("nuclear power" OR reactor OR SMR OR uranium) when:2d')
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_FT_Q}&hl=en-US&gl=US&ceid=US:en",
    "name": "FT 원자력", "domain_label": "ft.com",
})
_LESECHOS_Q = quote_plus("site:lesechos.fr (nucléaire OR EDF OR EPR) when:2d")
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_LESECHOS_Q}&hl=fr&gl=FR&ceid=FR:fr",
    "name": "Les Échos 원자력", "domain_label": "lesechos.fr",
})
_EENEWS_Q = quote_plus("site:eenews.net (nuclear OR reactor OR uranium) when:3d")
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_EENEWS_Q}&hl=en-US&gl=US&ceid=US:en",
    "name": "E&E News 원자력", "domain_label": "eenews.net",
})

# ---- 사내 참조 사이트 목록 보완 (2026-08-05) --------------------------------
# 부서 「세계원전시장 인사이트」 업무 절차서의 '주요 기사 검색 사이트' 대조.
# 이미 걷고 있던 것: WNN·NucNet·ANS(Nuclear Newswire)·POWER Magazine·IAEA.
#
# 넣지 않은 것과 이유 (재시도 전에 이 목록부터 볼 것, 실측 2026-08-05):
#   UxC              uxc.com RSS 404. 헤드라인 뉴스·UxWeekly 전부 구독 제품이다.
#   BNEF             구독 전용. 공개 피드 없음.
#   IAEA PRIS        발전소 제원·이용률 통계 DB. 뉴스 피드가 아니라 조회 대상이라
#                    수집원이 아니다(기사 작성 시 수치 확인용).
#   Nuclear Asia     nuclearasia.com 직접 429, 구글 인덱싱 0건. 접근 경로 없음.
#   World Nuclear Association  구글 18건 중 절반이 'Contact Us' 류 상시 페이지고
#                    나머지는 뉴스가 아닌 보고서·행사다. 발간물 경로가 맞아
#                    뉴스 수집원으로는 넣지 않는다.
NUCLEAR_TITLE_KEYWORDS = (
    "nuclear", "reactor", "smr", "uranium", "atomic", "enrich",
    "radioactive", "fusion", "nucléaire", "원전", "원자력",
)
# 원자력 전문지 — 실측 8건 전부 원자력(2026-08-05). 직접 RSS(neimagazine.com/feed)는
# 403 이거나 5개월 전 항목을 돌려주는 캐시라 Google News 경로를 쓴다.
_NEI_Q = quote_plus("site:neimagazine.com when:3d")
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_NEI_Q}&hl=en-US&gl=US&ceid=US:en",
    "name": "Nuclear Engineering International", "domain_label": "neimagazine.com",
})
# EU 정책 전문지 — EU 차원 규제·지침·역내 전력망 논의가 다른 출처에 잘 안 잡힌다.
# 직접 RSS 는 Cloudflare 403(브라우저 UA 로도 동일). Google News 는 이 도메인에서
# 괄호 키워드를 무시하므로 require_keywords 로 수집 단계에서 거른다.
_EURACTIV_Q = quote_plus("site:euractiv.com (nuclear OR reactor OR SMR OR uranium) when:3d")
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_EURACTIV_Q}&hl=en-US&gl=US&ceid=US:en",
    "name": "Euractiv 원자력", "domain_label": "euractiv.com",
    "require_keywords": NUCLEAR_TITLE_KEYWORDS,
})

# 국내 언론의 원자력 '업무' 보도 — 보도자료(site:)만으론 국내가 비어 추가.
# 타깃 키워드(기관·정책·사업명)로 좁혀 노이즈 최소화. 일반 '원자력' 단독은 의도적으로
# 제외(원자력병원·원자력시계 등 무관 잡음 방지). 들어온 뒤엔 기존 curation·노이즈 필터로 한 번 더 거름.
# when:1d — Google News 검색 RSS는 '관련도순'이라 몇 주 지난 기사가 대부분
# (실측: 100건 중 95건이 1주+) → LOOKBACK 6h 필터에서 전멸해 국내 0건이 되던 원인.
# 최근 24h 로 한정하면 매시간 크롤이 신선한 기사를 제때 잡는다.
_KR_AFFAIRS_Q = quote_plus(
    "한수원 OR 원자력안전위원회 OR 원전수출 OR i-SMR OR 신한울 OR 새울원전 "
    "OR 사용후핵연료 OR 원전 계속운전 OR 전력수급기본계획 when:1d"
)
RSS_SOURCES.append({
    "url": f"https://news.google.com/rss/search?q={_KR_AFFAIRS_Q}&hl=ko&gl=KR&ceid=KR:ko",
    # resolve_publisher: 이 피드는 여러 매체가 섞이므로 RSS <source> 에서 실제
    # 매체 도메인(전기신문=electimes.com 등)을 복원한다. domain_label 은 복원
    # 실패 시 폴백.
    # 주의: '한국 매체'가 곧 '국내 뉴스'는 아니다 — 국내 언론의 해외 원전 기사는
    # scope=overseas 로 판정돼 해외 브리핑으로 간다 (daily_brief.region 참조).
    "name": "국내 원자력 보도", "domain_label": "news.google.co.kr",
    "resolve_publisher": True,
})

SMR_HINTS = ("smr", "small modular", "i-smr", "advanced reactor")

ANTI_TITLE_PATTERNS = [
    re.compile(r"\[(보도자료|알림|공지|기업\s*소식|새소식|광고|포토|화보|부고|기획|특집|인사|동정)\]"),
]
ANTI_KEYWORDS: list[str] = [
    "원자력병원", "원자력 병원", "원자력 시계",
    "인사 발령", "인사발령", "임원 인사", "신년사", "취임사",
    "채용 공고", "채용공고", "직원 채용", "신입 채용", "신입사원 채용",
    "경력 채용", "경력채용", "임원 채용", "인재 모집", "수시채용",
    "MOU 체결식", "협약 체결식", "기념식",
    "동호회", "체육대회", "야유회",
    "청사 이전", "사옥 이전", "조직 개편 안내",
]
MIN_DESCRIPTION_LEN = 30  # 본문 길이 필터 - 이보다 짧으면 stub으로 보고 드롭

KR_SLD = (".co.kr", ".or.kr", ".go.kr", ".ne.kr", ".re.kr", ".ac.kr")

CURATION_SYSTEM_PROMPT = """당신은 한국수력원자력 전략경영단 정책개발부의 시니어 정책분석관입니다.
의사결정자(본부장·부서장)에게 보고하는 분석관 톤으로 작성합니다. 일반 뉴스 큐레이션 톤 금지.

[3가지 분류 모두 수행]

A. importance (중요도) - 발송 방식 결정
- must_read: 즉시 알아야 함. 하루 평균 0~3건. 매우 엄격.
   · 정부·규제기관 공식 의결·고시·시행령·법안 본회의 통과
   · 주요국 행정명령·정책 발표
   · 신규 원전 부지 결정·인허가 발급, 계속운전 확정, SMR 표준설계인가 발급
   · 사고·중대 안전 이슈 (INES 등급, 정전, 누출)
   · 양자 협력 협정 체결·결정 (한미·미영 등)
   · 글로벌 수주 EPC 계약 체결·확정 (협상 단계는 nice_to_know)
   · 전력수급기본계획 확정·고시
- nice_to_know: 맥락·동향. 정책 함의 있는 기사만.
   · 전력수급·전력시장: 수요 급증 대응, 발전설비 점검·정비 계획, 계통·송전망, 전기요금 제도
     (지역별 차등요금 포함). 원전이 공급 축으로 등장하면 정책 함의가 있는 기사다.
   · 지역사회·수용성 **결정과 요구**: 지자체·지방의회 공식 입장, 주민 대책위 공식 건의,
     주민설명회·공청회 개최·결과, 부지 관련 행정 결정, 원전 주변지역 지원.
     지역지에만 실려도 마찬가지다 — 매체 규모가 아니라 사건의 성격으로 판단한다.
- market: 주식·증권·테마주·시황·증권사 리포트
- noise: **적극 거름. 의심스러우면 noise.**
   · 보도자료 단순 재탕, 외신 단순 번역, 우라까이
   · 기업 PR·ESG·CSR 홍보, 행사 스케치, 시상식·축사
   · **정치 일반**: 대선·총선·지선, 정쟁, 정치인 갈등·비판 성명, 여야 공방
   · **원자력이 본질이 아닌 기사**: 원자력이 부수적으로만 언급되고 본문은 다른 주제 (산업 일반·외교 일반·거시 경제·사회 일반)
   · **단순 발언·견해**: 정책 의사결정자가 아닌 학자·시민단체·일반 칼럼니스트 발언
   · 학회 일반 (춘추계 학술대회 등 정책 함의 없는 단순 행사)
   · 지역 **행사·친목** (축제·체육대회·동호회·봉사·기부·시상, 개인 민원)
     ※ 지자체·의회의 공식 결정, 주민 대책위의 공식 건의, 주민설명회·공청회,
       부지 관련 행정은 여기 해당하지 않는다 — 위 nice_to_know 로 분류할 것.
       원전 소재지의 수용성은 정책 그 자체다.
   · 인사 발령·동정·축하·부고
   · **기관 행정 일반**: 채용 공고·직원 모집·인재 채용·임원 인사, 청사 이전·조직 개편 단순 안내, 회계연도 일반 행정
   · **정부 사이트라 해도 본질이 회의 결과·의결·정책 발표가 아닌 경우** (예: 회의 결과인데 안건이 채용·청사·내부 행정·일반 공지)

** 의결·확정·체결·통과·발급된 사실만 must_read. 칼럼·전망·검토·예정은 절대 must_read 아님. **
** 원자력이 단순 키워드로만 등장하고 본질이 다른 주제면 무조건 noise. **

B. section (주제 영역) - 어느 섹션에 들어갈지
- smr: SMR/소형모듈원자로 관련 모든 뉴스 (행위자 무관). i-SMR, NuScale, TerraPower, X-energy, Holtec, Kairos, Oklo, AP300, eVinci, EU SMR 얼라이언스, 포스코 SMR, 현대건설 SMR 등.
- khnp: 한수원(한국수력원자력)이 주체이거나 핵심 행위자 (SMR 제외). 체코·폴란드 APR1400 수주, 신한울/새울/고리/한빛/한울 운영, 한수원 보도자료 등.
- domestic: 한국 정부·규제기관·국회 (한수원·SMR 제외). 산업부, 원안위(NSSC), KINS, 과기정통부, 국회 입법, 11차 전기본 등.
- international: 그 외 모든 글로벌 동향 (한국·SMR 무관). IAEA·NRC·DOE·EU·OECD/NEA, 외국 정부 정책, 해외 운영사 동향.

** 우선순위: SMR > 한수원 > 국내 > 해외. 같은 기사가 SMR이면서 한수원이면 SMR. **

B-2. scope (기사가 다루는 지역) - 국내/해외 브리핑 분리 발송용. section과 별개로 반드시 판정.
- kr: 한국이 주체이거나 무대인 기사. 한수원·한국 정부·규제기관·국내 기업의 활동, 한국 내 원전·정책·규제, 한국의 해외 수주(체코·폴란드 APR1400 등), 국내 SMR(i-SMR·두산에너빌리티·현대건설).
- overseas: 그 외 전부. 해외 정부·규제기관·기업·국제기구 동향, 해외 SMR 기업(NuScale·TerraPower·X-energy·Oklo·Holtec 등).

** 판단 기준은 '기사를 쓴 매체'가 아니라 '기사가 다루는 대상'. 한국 매체가 한국어로 쓴
기사라도 주제가 해외면 overseas (예: 국내 언론의 '미국 원전 80년 장기운전 승인' 보도 → overseas).
한국이 등장하지만 단순 비교·언급 수준이면 overseas. **

C. category (세부 카테고리) - 4가지 중 하나
- 정책: 정부·국가 단위 의사결정, 외교, 다자기구 정책 결정 (IAEA, OECD/NEA 등)
- 기술: 노형·핵연료주기·안전기술·R&D·표준설계
- 시장: 신규 발주·EPC 계약·인수합병·자본·발전사업자 동향
- 규제: 인허가·안전기준·환경평가·NRC·NSSC 의결

D. 통제 태그 - 웹 트렌드 집계용. **반드시 아래 고정 목록의 값만 사용 (목록 밖 값 금지).**

- topics (0~3개): 기사가 다루는 주제.
  smr(소형모듈원자로) / newbuild(신규 원전 건설) / restart_lto(계속운전·재가동) /
  fuel_cycle(핵연료주기: 우라늄·농축·HALEU) / waste(사용후핵연료·방사성폐기물) /
  finance(원전 금융·투자·자금조달) / regulation(규제·인허가) /
  power_market(전력시장·요금·전력망) / datacenter_ai(데이터센터·AI 전력수요) /
  fusion(핵융합) / security_trade(에너지 안보·통상·수출통제) / fukushima(후쿠시마·처리수)
  ** 해당 주제가 없으면 빈 리스트. 억지로 채우지 말 것. **

- countries (0~2개): 기사의 실제 정책 관할·사업 부지·사건 무대가 되는 국가·지역.
  국가는 ISO 3166-1 alpha-2 코드 사용 (예: KR / US / FR / GB / DE / CA).
  EU는 유럽연합 기관·EU 공동 정책이 직접 주체일 때만 사용한다.
  EUROPE는 3개 이상 유럽 국가에 걸친 범지역 이슈, GLOBAL은 특정 국가가 없는 국제 이슈,
  UNSPECIFIED는 근거만으로 국가·지역을 정할 수 없을 때만 사용한다.
  기업 본사 소재지만으로 국가를 붙이지 말고 EU_ETC / OTHER는 사용하지 않는다.

- article_type (1개): 기사 유형.
  policy(정책·공식발표) / official_doc(공식문서·전문 원문) / corporate(기업 발표·실적) /
  analysis(심층분석·해설) / opinion(칼럼·기고·인터뷰) / report(보고서·통계 소개) / news(그 외 일반 보도)

[필드별 출력 - 모든 텍스트 필드는 한국어로 작성. 원문이 영문이어도 한국어로 옮길 것.]

- title_kr: 한국어 제목 (30~60자). 원문이 영문이면 자연스러운 한국어로 번역. 원문이 한국어면 핵심을 살린 정확한 한국어 제목. 인명·기관명 첫 등장 시 한글(원문) 병기.

- summary: '무슨 일'을 한국어 완결형 서술문 1개로 작성(공백 포함 80자 이내). **모든 항목 작성.** 길면 문자열을 자르지 말고 핵심을 줄여 처음부터 다시 쓸 것. 원문에 있는 수치·일정(GW·MW·금액·기수·시행일·인허가 시한)은 가능한 범위에서 보존할 것.
- summary 사실성 제약: 원문에 없는 전망·평가·인과관계를 추가하지 말 것. 계획·예정·전망·검토를 완료된 사실처럼 바꾸지 말고 원문의 시제를 그대로 보존할 것.

- implication: AI 해석 1문장(90자 이내). nice_to_know·must_read만 작성. 완결형 서술문으로 쓰고 문자열을 자르지 말 것.
  **제목·요약에 없는 사실을 하나는 담아야 한다.** 다음 중 최소 하나를 명시할 것:
  ①이 일이 벌어진 원인·배경(무엇 때문인가) ②이어질 다음 절차·일정 ③걸린 수치·규모
  ④이 결정으로 영향받는 대상.
  담을 사실이 기사에 없으면 **빈 문자열로 둔다.** 억지로 채우지 말 것 — 빈칸이 빈껍데기보다 낫다.
  아래 어미로 끝내는 문장은 금지다(실측 64건 중 31건이 이 꼴이었고 전부 정보량 0이었다):
  "…을 시사한다 / …을 보여준다 / …이 기대된다 / …이 전망된다 / …에 기여할 것이다 /
   …이 중요하다 / …이 필요하다 / …을 주목할 필요가 있다".
    나쁨: "헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다." (제목을 바꿔 말했을 뿐)
    좋음: "다뉴브강 수위가 회복되며 냉각수 취수 제한이 풀린 결과로, 앞서 예고된 전면 정지는 피했다."
    나쁨: "미국 SMR 상용화 가속화에 기여할 것입니다."
    좋음: "INL 부지 사용 협약으로 2028년 착공 목표의 인허가 전 단계가 열렸다."

- why_important: must_read만 작성. **1~2개의 완결형 문장, 150자 이내**. 분석관 톤. 격식체. 핵심 시사점만 압축. 절대 길게 풀어쓰거나 문자열을 자르지 말 것.

- open_question: must_read만 작성. **원문에서 아직 확정되지 않은 것**을 50자 이내 완결형 서술문 1개로. 없으면 null.
  · 질문형이 아니라 선언형으로 쓸 것. (O) "최종 계약 체결 시점은 아직 확정되지 않았다" / (X) "최종 계약은 언제 체결될까?"
  · **원문에 명시적으로 미정·조사 중·검토 중·협의 중·기한 미정으로 남아 있는 것만 쓴다.** 원문에 없는 미확정 사항을 추론해 만들지 말 것.
  · 예상·가능성·전망을 서술하지 말 것. "~할 것으로 보인다"는 미확정 사항이 아니라 예측이다.
  · 근거 문장을 원문에서 지목할 수 없으면 반드시 null.
  · 자주 해당하는 것: 계약 규모는 발표됐으나 금융조달 미정 / 우선협상대상자만 선정되고 최종 계약 시점 미정 / 정책 방향은 나왔으나 시행령·예산 미정 / 조사 진행 중이라 원인 미확정.
- open_question_source: open_question 의 근거가 실제로 있는 위치. title / description / article_text 중 하나. open_question 이 null 이거나 근거를 지목할 수 없으면 unknown.

- event_date: 기사에 명시된 사건 발생·발표·시행·예정일을 YYYY-MM-DD로 작성. 기사 게시일을 사건일로 추정하지 말 것. 일자를 확정할 수 없으면 null.
- event_date_type: announcement(발표) / occurrence(발생) / effective(시행) / deadline(기한) / scheduled(예정) / unknown.
- event_date_precision: day / month / year / unknown. YYYY-MM-DD로 확정한 경우 day.
- event_date_source: title / description / article_text / unknown. 현재 입력에 실제로 존재하는 근거만 선택.

- watch_next: 빈 문자열 (사용 안 함).

- tags: # 으로 시작 3개 이내. 예: #한미협정 #체코수주 #SMR경쟁

- related_reports: 사용자 메시지에 [관련 사내 보고서] 섹션이 있고 실제 분석에 참조한 보고서가 있으면 보고서 제목 리스트(최대 2개). 참조 안 했거나 보고서 섹션이 없으면 빈 리스트.

[관련 사내 보고서 활용]
- 사용자 메시지 끝에 [관련 사내 보고서] 섹션이 있으면 분석에 활용.
- 동일 주제·맥락이면 implication 또는 why_important에 사내 시각과 일관성 있게 작성 (보고서를 명시적으로 인용할 필요는 없으나 톤·관점 통일).
- 보고서가 실제로 의미 있게 참조된 경우만 related_reports 채울 것. 단순 키워드 일치는 제외.

[원칙]
- **모든 텍스트 필드는 한국어**. 영문 원문 입력이 들어와도 한국어로 작성.
- 원문에 없는 정보 추가 금지 (환각 금지).
- 일반 뉴스 요약 톤 금지. KHNP 정책분석관 보고 톤.

[출력 형식] - 반드시 JSON 한 객체만
{
  "importance": "must_read|nice_to_know|market|noise",
  "section": "smr|khnp|domestic|international",
  "scope": "kr|overseas",
  "category": "정책|기술|시장|규제",
  "title_kr": "...",
  "summary": "...",
  "implication": "...",
  "why_important": "...",
  "open_question": "...|null",
  "open_question_source": "title|description|article_text|unknown",
  "watch_next": "...",
  "tags": ["#태그1", "#태그2"],
  "topics": ["smr"],
  "countries": ["US"],
  "article_type": "policy",
  "event_date": "2026-08-01|null",
  "event_date_type": "announcement|occurrence|effective|deadline|scheduled|unknown",
  "event_date_precision": "day|month|year|unknown",
  "event_date_source": "title|description|article_text|unknown",
  "related_reports": ["보고서 제목 1", "..."]
}
"""


def get_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if not host:
        return ""
    if host.endswith(KR_SLD):
        return ".".join(host.split(".")[-3:])
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_score(url: str) -> int:
    d = get_domain(url)
    if d in TIER1_DOMAINS:
        return 10
    return DOMAIN_SCORE.get(d, DEFAULT_SCORE)


def is_tier1(url: str) -> bool:
    return get_domain(url) in TIER1_DOMAINS


def is_tier1_source(art: dict) -> bool:
    """기사가 정부·규제기관·국제기구 등 공식 원발표처인가.

    링크만 보면 안 된다 — 기관 보도자료도 Google News 검색 경유면 링크가
    news.google.com 이다. 수집 시 확정한 출처 도메인을 먼저 본다.
    전문언론은 신뢰도와 무관하게 ``independent``이므로 여기서 제외한다.
    """
    domain = art.get("domain") or get_domain(art.get("link", ""))
    profile = source_profile(domain, art.get("publisher", ""))
    return profile["evidence_role"] == "primary"


def is_promotional(title: str, description: str) -> bool:
    if any(p.search(title) for p in ANTI_TITLE_PATTERNS):
        return True
    text = title + " " + description
    return any(kw in text for kw in ANTI_KEYWORDS)


def is_stub(description: str) -> bool:
    return len(description.strip()) < MIN_DESCRIPTION_LEN


def normalize_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]|\([^)]+\)", "", title)
    title = re.sub(r"[^\w가-힣]", "", title)
    return title.lower()


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def url_hash(url: str) -> str:
    """신규 상태 키는 정규화 URL 해시를 사용한다."""
    return canonical_url_hash(url)


def article_seen(state: dict, url: str) -> bool:
    """정규화 해시 전환 중에도 기존 sent.json을 다시 수집하지 않는다."""
    sent = state.get("sent") or {}
    return url_hash(url) in sent or legacy_url_hash(url) in sent


def source_score(domain: str, publisher: str = "") -> int:
    """출처 모델을 반영한 수집 우선순위 점수."""
    tier = source_profile(domain, publisher)["source_tier"]
    if tier == 1:
        return 10
    if tier == 2:
        return max(8, DOMAIN_SCORE.get(domain, DEFAULT_SCORE))
    return DOMAIN_SCORE.get(domain, DEFAULT_SCORE)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_reports_kb() -> list[dict]:
    if REPORTS_KB_FILE.exists():
        try:
            data = json.loads(REPORTS_KB_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def find_relevant_reports(title: str, description: str, kb: list[dict], top_k: int = 3) -> list[dict]:
    """기사 제목·요약과 가장 관련 있는 사내 보고서 top_k개 반환 (점수 기반).

    매칭은 전부 로컬 — 보고서 내용은 외부 API 로 나가지 않고, 매칭된 제목·요약만
    큐레이션 프롬프트에 첨부된다. trigger_patterns(명시 트리거) > topic_tags >
    entities > 제목 단어 순으로 강하게 가중.
    """
    if not kb:
        return []
    text = (title + " " + description).lower()
    scored: list[tuple[float, dict]] = []
    for report in kb:
        score = 0.0
        for pat in report.get("trigger_patterns") or []:
            if isinstance(pat, str) and pat.lower() in text:
                score += 4.0
        for tag in report.get("topic_tags") or []:
            if isinstance(tag, str) and tag.lower() in text:
                score += 3.0
        for ent in report.get("entities") or []:
            if isinstance(ent, str) and ent.lower() in text:
                score += 2.0
        rtitle = (report.get("title") or "").lower()
        for word in re.findall(r"[가-힣]{2,}|[a-zA-Z]{3,}", rtitle):
            if word in text:
                score += 1.0
        rsum = (report.get("summary") or "").lower()
        for word in re.findall(r"[가-힣]{3,}", rsum)[:30]:
            if word in text:
                score += 0.3
        if score > 0:
            scored.append((score, report))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


def load_embeddings_cache() -> dict:
    return load_embedding_store(EMBEDDINGS_CACHE_FILE)


def save_embeddings_cache(cache: dict) -> None:
    save_embedding_store(cache, EMBEDDINGS_CACHE_FILE)


def cosine_sim(a: list[float], b: list[float]) -> float:
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def get_or_compute_embedding(article: dict, cache_key: str, cache: dict) -> list[float] | None:
    client = get_gemini()
    try:
        vector, _ = pipeline_get_or_compute_embedding(client, article, cache_key, cache)
        return vector
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            print(f"  ! embedding quota exceeded")
        else:
            title = article.get("title_kr") or article.get("title") or ""
            print(f"  ! embedding failed for '{title[:40]}': {type(e).__name__}")
        return None


def semantic_dedup(articles: list[dict], emb_cache: dict, threshold: float = SEMANTIC_DEDUP_THRESHOLD) -> list[dict]:
    """임베딩 cosine similarity로 의미 중복 제거. 점수 높은 것 우선 유지."""
    if len(articles) < 2:
        return articles

    enriched: list[tuple[dict, list[float] | None]] = []
    for art in articles:
        emb = get_or_compute_embedding(art, art["hash"], emb_cache)
        enriched.append((art, emb))
        time.sleep(0.3)

    enriched.sort(key=lambda x: x[0]["score"], reverse=True)

    kept: list[tuple[dict, list[float] | None]] = []
    for art, emb in enriched:
        if emb is None:
            kept.append((art, emb))
            continue
        is_dup = False
        for kept_art, kept_emb in kept:
            if kept_emb is None:
                continue
            if cosine_sim(emb, kept_emb) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append((art, emb))

    return [art for art, _ in kept]


def load_state() -> dict:
    return load_json(STATE_FILE, {"sent": {}})


def save_state(state: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    state["sent"] = {h: ts for h, ts in state["sent"].items() if ts > cutoff}
    save_json(STATE_FILE, state)


def load_curated() -> dict:
    return load_json(CURATED_CACHE_FILE, {})


def save_curated(curated: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_RETENTION_DAYS)).isoformat()
    curated = {k: v for k, v in curated.items() if v.get("cached_at", "") > cutoff}
    save_json(CURATED_CACHE_FILE, curated)


# features 만 없는 항목을 몇 번까지 다시 물어볼 것인가. 상한이 없으면 LLM 이 끝내
# 주지 않는 항목을 매시간(크롤마다) 다시 묻게 되고 무료 티어가 그대로 녹는다.
FEATURES_RETRY_LIMIT = 2


def fallback_curation(article: dict) -> dict | None:
    """batch 큐레이션이 실패한 기사의 최소 레코드. 안전한 문장이 없으면 None.

    원문 스니펫의 **완결문만** 쓴다 — 자르면 문장 중간에서 끊긴다.

    ⚠️ 여기서 등급을 올리지 않는다. 예전에는 1차 출처(`is_tier1_source`)면
    ``must_read`` 로 승격했는데, 이 레코드에는 features 가 없어 ranking 이
    ``_legacy_score()`` 로 빠진다(event_weights·feature 가중치 전부 무시).
    그 결과 ``must_read`` 의 40%(회차 관측치 기준)가 "LLM 이 중요하다고 본
    기사"가 아니라 "큐레이션이 실패한 1차 출처"가 돼 있었다.
    등급은 큐레이션이 판단할 몫이고, 이 항목은 ``needs_recuration()`` 이
    다음 crawl 에서 다시 물어본다.
    근거: docs/AS_IS.md §2, docs/score_distribution.md §4·§7.
    """
    summary = first_complete_sentence(article.get("description"), 80)
    if not summary:
        return None
    return {
        "importance": "nice_to_know",
        "section": default_section(article.get("domain", ""), article.get("title", "")),
        "category": "정책",
        "title_kr": article.get("title", ""),
        "summary": summary,
        "implication": "",
        "why_important": "",
        "watch_next": "",
        "tags": [],
        "related_reports": [],
        "event_date": None,
        "event_date_type": "unknown",
        "event_date_precision": "unknown",
        "event_date_source": "unknown",
    }


def needs_recuration(cached: dict) -> bool:
    """캐시된 큐레이션을 Gemini 에 다시 물어봐야 하는가.

    features 결손을 재큐레이션 대상에 포함시키는 것이 이 함수의 존재 이유다.
    ``curation_errors()`` 만 보면 summary 가 멀쩡한 결손 항목은 완결된 것으로
    취급돼 그대로 캐시된다.

    ⚠️ **이건 2차 방어선이다.** 기사는 큐에 적재되는 순간 ``state["sent"]`` 로
    마킹되고 ``article_seen()`` 이 재수집을 막으므로, 이 판정은 아직 큐에 못 들어간
    항목(품질 격리분)이나 ``sent`` 가 만료(14일)돼 다시 잡힌 항목에만 도달한다.
    **결손을 실제로 막는 곳은 ``curate_batch()`` 의 응답 검증**이다.

    features 만 없는 경우는 재시도 상한을 둔다 — 다른 필드까지 깨진 항목은 상한
    없이 고치되, "LLM 이 이 기사엔 features 를 안 준다"는 상태에 갇히지 않게 한다.
    """
    errors = curation_errors(cached, require_features=True)
    if not errors:
        return False
    if errors == ["features:missing"]:
        return int(cached.get("features_attempts") or 0) < FEATURES_RETRY_LIMIT
    return True


def load_queue() -> list:
    return load_json(DIGEST_QUEUE_FILE, [])


def save_queue(queue: list) -> None:
    save_json(DIGEST_QUEUE_FILE, queue)


def search_naver(query: str, display: int = 30) -> list[dict]:
    """네이버 뉴스 API 검색.

    🔴 negative_terms 를 쿼리에 붙이지 말 것. 네이버 검색 API 는 '-' 를 제외
    연산자로 처리하지 않고 **추가 검색어로 AND 결합**한다. 실측(2026-08-06):

        '계속운전'                                    → total 360,614
        '계속운전 -주가 -채용 … -기념식'(프로덕션 9개) → total 0
        '원자력 정책'                                 → total 299,455, 최신 당일
        '원자력 정책 -인사 -부고'                      → total 82, 최신 5개월 전

    즉 제외하려던 것이 아니라 쿼리 자체가 죽는다. 국내 수집이 네이버가 아니라
    Google News 국내 피드 하나로 연명하던 원인이 이것이다.
    제외는 ``is_rejected_title()`` 이 수집 후에 한다.
    """
    import requests

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": "date"}
    r = requests.get(NAVER_URL, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("items", [])


def send_telegram(text: str) -> bool:
    import requests

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = requests.post(TELEGRAM_URL, data=payload, timeout=10)
    if not r.ok:
        print(f"  ! Telegram error: {r.status_code} {r.text}")
        return False
    time.sleep(1)
    return True


# '원전' 앵커에 걸리지만 원자력이 아닌 동음이의. '기원전 8세기', '호메로스를 원전(原典)
# 으로 한 각색' 류가 실제로 검색에 섞인다(2026-08-05 '원전' 최신순 실측). 앵커 판정 전에
# 지워 버리면 다른 앵커가 없는 한 걸리지 않는다.
_HOMONYM_NOISE = re.compile(r"기원전|원전\s*\(\s*原典\s*\)|原典")


def passes_anchor_filter(title: str, description: str, anchors: list[str]) -> bool:
    if not anchors:
        return True
    haystack = _HOMONYM_NOISE.sub(" ", (title + " " + description)).lower()
    return any(a.lower() in haystack for a in anchors)


# 제목이 이 꼴로 **시작**하면 기사가 아니라 명단이다. 실측(2026-08-05 '원자력 정책'
# 최신순 30건)에서 20건 이상이 「[인사] 경북도」 형태였다 — 원자력산업안전과장이
# 명단에 들어 있어 키워드에는 걸리는데 원자력 뉴스는 아니다.
#
# 왜 접두인가: 본문 포함으로 자르면 "원전 인사 정책", "부고를 계기로 한 안전 논의"
# 같은 정상 기사를 잃는다. 명단 기사는 제목이 예외 없이 이 표지로 시작한다.
#
# 왜 대괄호가 필수인가: 대괄호를 선택으로 두면 "인사 정책 개편으로 원전 인력 확충"
# 처럼 '인사'로 시작하는 정상 기사가 통째로 잘린다. 명단 기사는 「[인사] 경북도」·
# 「[8월 5일 인사종합]」·「[오늘의 인사 및 동정]」처럼 항상 머리 대괄호를 단다.
_TITLE_PREFIX_REJECT = re.compile(
    r"^\s*[\[\【(][^\]\】)]{0,20}(?:인사|부고|동정|人事)[^\]\】)]{0,10}[\]\】)]"
)


def parse_negative_terms(negative_terms: str) -> list[str]:
    """'-주가 -채용' → ['주가', '채용'].

    keywords.json 의 이 필드는 원래 검색 쿼리에 붙었으나 네이버가 '-' 를 제외로
    처리하지 않아 쿼리를 죽이고 있었다(``search_naver`` 주석). 어휘 자체는
    이 도메인에서 실제로 걸러야 할 것들이라 버리지 않고 **제목 제외 목록**으로 쓴다.

    부고·인사·채용·주가처럼 제목에 이 말이 들어가면 그 기사가 정말 그 기사다.
    원자력 브리핑에서 필요 없는 것들이므로 제목 부분일치로 자르는 게 맞다.
    """
    return [t.lstrip("-").strip().lower()
            for t in (negative_terms or "").split() if t.lstrip("-").strip()]


def is_rejected_title(title: str, negative_terms: list[str]) -> bool:
    """수집 후 결정적 제외 — 검색 단계에서 못 하는 일을 여기서 한다.

    ⚠️ 제목만 본다. 본문·요약까지 보면 "원자력 안전 채용 확대에 따른 …" 같은
    맥락 언급으로 정상 기사가 날아간다.
    """
    text = (title or "").strip()
    if not text:
        return False
    if _TITLE_PREFIX_REJECT.match(text):
        return True
    lowered = text.lower()
    return any(term in lowered for term in negative_terms)


_gemini_client = None


def get_gemini():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        return _gemini_client
    except Exception as e:
        print(f"  ! Gemini init failed: {e}")
        return None


VALID_IMPORTANCE = {"must_read", "nice_to_know", "market", "noise"}
VALID_SECTIONS = {"smr", "khnp", "domestic", "international"}
VALID_CATEGORIES = {"정책", "기술", "시장", "규제"}
VALID_SCOPES = {"kr", "overseas"}

# 통제 태그 (웹 트렌드 집계용 — 프롬프트 D 섹션과 반드시 일치)
VALID_TOPICS = {
    "smr", "newbuild", "restart_lto", "fuel_cycle", "waste", "finance",
    "regulation", "power_market", "datacenter_ai", "fusion",
    "security_trade", "fukushima",
}
# 국가는 임의의 화이트리스트가 아니라 ISO 3166-1 alpha-2 전체를 허용한다.
# EU/EUROPE/GLOBAL/UNSPECIFIED는 국가 코드와 섞이지 않도록 의미가 고정된 범위 코드다.
ISO_ALPHA2_COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP
KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY
MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ
VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())
COUNTRY_SCOPE_CODES = {"EU", "EUROPE", "GLOBAL", "UNSPECIFIED"}
VALID_COUNTRIES = ISO_ALPHA2_COUNTRIES | COUNTRY_SCOPE_CODES
COUNTRY_ALIASES = {
    "UK": "GB",             # 관용 코드 → ISO 코드
    "EU_ETC": "UNSPECIFIED",  # 폐기된 묶음 코드
    "OTHER": "UNSPECIFIED",   # 폐기된 모호 코드
}
VALID_ARTICLE_TYPES = {
    "policy", "official_doc", "corporate", "analysis", "opinion", "report", "news",
}

# 한국 출처 도메인 (이외는 해외로 간주)
_KR_DOMAIN_HINTS = (".kr", "khnp", "nssc", "motie", "kaeri", "kins", "korad", "yna", "korea")
_HANGUL_RE = re.compile(r"[가-힣]")


def default_section(domain: str, title: str = "") -> str:
    """LLM이 section을 못 줄 때 도메인·제목으로 추정.

    미국·글로벌 기사가 '국내(domestic)'로 오분류되는 것 방지 — 기본값은 '해외'.
    한국 도메인(khnp.co.kr이면 khnp) 또는 한글 제목이면 domestic.
    (국내 매체 상당수가 .com 이라 도메인만으론 못 걸러진다: electimes.com 등)
    """
    d = (domain or "").lower()
    if any(h in d for h in _KR_DOMAIN_HINTS):
        return "khnp" if "khnp" in d else "domestic"
    if _HANGUL_RE.search(title or ""):
        return "domestic"
    return "international"


# 버린 해석 건수. 한 번의 크롤이 끝날 때 한 줄로 찍는다 — 조용히 지우면
# 프롬프트가 망가진 것을 아무도 모른다.
HOLLOW_IMPLICATION_DROPS: list[str] = []


def drop_hollow_implication(value, title: str = "") -> str:
    """정보량 0인 해석은 빈 문자열로 만든다.

    사용자 지적(2026-08-05): "AI 헝가리 정부의 원전 운영에 대한 긍정적 입장을
    시사한다 >> 이거 보면 내용이 너무 없어." 카드 두 번째 줄이 제목을 바꿔 말하기만
    하면 읽는 사람이 얻는 게 없다. 프롬프트를 고쳐 원인·다음 절차·수치를 요구하되,
    그래도 상투적 문장이 나오면 화면에 안 내보낸다.
    """
    text = clean_text(value)
    if text and implication_is_hollow(text):
        HOLLOW_IMPLICATION_DROPS.append(f"{title[:40]} | {text[:60]}")
        return ""
    return text


def norm_scope(value) -> str:
    """LLM의 scope 값을 정규화. 유효하지 않으면 빈 문자열.

    추정하지 않는다 — 값이 없으면 daily_brief.region() 이 section·도메인·제목
    언어로 판단한다 (같은 추정 로직을 두 곳에 두지 않기 위함).
    """
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in VALID_SCOPES else ""


def norm_topics(value) -> list[str]:
    """통제 태그 topics 정규화 — 목록 밖 값은 버린다 (트렌드 축 오염 방지)."""
    if not isinstance(value, list):
        return []
    out = [t.strip().lower() for t in value if isinstance(t, str)]
    return [t for t in out if t in VALID_TOPICS][:3]


def norm_countries(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for country in value:
        if not isinstance(country, str):
            continue
        code = COUNTRY_ALIASES.get(country.strip().upper(), country.strip().upper())
        if code in VALID_COUNTRIES and code not in out:
            out.append(code)
    return out[:2]


def norm_article_type(value) -> str:
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in VALID_ARTICLE_TYPES else "news"


# ---- open_question 게이트 -----------------------------------------------------
#
# '아직 확정되지 않은 것'은 사실도 해석도 아닌 세 번째 축이다. 정책·수출·사업
# 기사에서 가장 자주 누락되는 정보다(계약 규모는 발표됐으나 금융조달 미정,
# 우선협상대상자만 정해지고 최종 계약 시점 미정 등).
#
# 위험은 불확실성을 보여주는 것이 아니라 **LLM 이 미확정 사항을 추측으로 만들어
# 내는 것**이다. 그래서 프롬프트로 한 번, 여기서 한 번 더 거른다.
OPEN_QUESTION_LIMIT = 60
OPEN_QUESTION_SOURCES = {"title", "description", "article_text"}

# 예측·전망은 미확정 사항이 아니다. "~할 것으로 보인다"는 원문에 없는 추론이다.
_FORECAST_PATTERNS = (
    "것으로 보인다", "것으로 예상", "전망이다", "전망된다", "가능성이 있다",
    "우려된다", "관측된다", "분석된다", "기대된다",
)


# 사고·안전 이슈는 전면 금지가 아니라 강화 게이트다. 사고 원인이 조사 중인지,
# 방출 여부가 확인됐는지, 재가동 시점이 미정인지는 **숨기면 확정된 사건으로
# 오해된다.** 다만 이 영역에서 추측 문장이 나가면 피해가 크므로, 명시적인
# 미확정 표현이 문장 안에 실제로 있을 때만 통과시킨다.
_EXPLICIT_UNCERTAINTY = (
    "조사 중", "조사중", "확인되지 않", "확인 중", "확인중", "결정되지 않",
    "정해지지 않", "밝혀지지 않", "미정", "발표되지 않", "공개되지 않",
)


def open_question_reject_reason(item: dict, importance: str,
                                event_type: str = "") -> str:
    """게이트에서 걸린 사유. 통과하면 빈 문자열.

    ``norm_open_question`` 은 다섯 갈래를 전부 ``("", "unknown")`` 하나로 돌려준다.
    그래서 **아카이브 51건 must_read 가 전건 0인데도 원인을 못 짚었다**(2026-08-03
    실측). LLM 이 애초에 안 쓴 것과 게이트가 먹은 것은 대응이 정반대다 — 전자면
    프롬프트를, 후자면 게이트를 봐야 한다. 그 둘을 가르려고 사유를 따로 뽑았다.

    판정 로직의 단일 출처다. ``norm_open_question`` 이 이 함수를 쓰므로 둘이 어긋날
    수 없다. 조건을 고치면 여기만 고친다.
    """
    if importance != "must_read":
        return "not_must_read"
    text = clean_text(item.get("open_question"))
    if not text:
        # LLM 이 아예 안 썼다(null 또는 빈 문자열). 게이트 문제가 아니다.
        return "llm_null"
    source = (item.get("open_question_source") or "").strip().lower()
    if source not in OPEN_QUESTION_SOURCES:
        # 근거 위치를 지목하지 못했으면 문장 자체를 버린다. 그럴듯한 문장이
        # 근거 없이 남는 것이 정보가 없는 것보다 나쁘다.
        return "no_source"
    if len(text) > OPEN_QUESTION_LIMIT:
        return "too_long"
    if text.rstrip().endswith("?"):
        return "is_question"
    if any(pattern in text for pattern in _FORECAST_PATTERNS):
        return "forecast"
    if event_type == "incident_safety" and not any(
            marker in text for marker in _EXPLICIT_UNCERTAINTY):
        return "incident_no_uncertainty"
    return ""


def norm_open_question(item: dict, importance: str, event_type: str = "") -> tuple[str, str]:
    """(open_question, open_question_source). 근거를 못 대면 빈 값.

    판정은 ``open_question_reject_reason`` 이 한다 — 사유별 계측과 같은 코드를 쓴다.
    """
    if open_question_reject_reason(item, importance, event_type):
        return "", "unknown"
    return (clean_text(item.get("open_question")),
            (item.get("open_question_source") or "").strip().lower())


def normalize_curation_item(item: dict, article: dict) -> dict:
    """LLM 결과를 손실 없이 스키마에 맞춘다. 문장 중간 slicing은 하지 않는다."""
    importance = item.get("importance", "nice_to_know")
    section = item.get("section") or default_section(
        article.get("domain", ""), article.get("title", "")
    )
    category = item.get("category", "정책")
    title_kr = clean_text(item.get("title_kr")) or article.get("title", "")
    grade = importance if importance in VALID_IMPORTANCE else "nice_to_know"
    features = sanitize_features(item.get("features"))
    event_type = (features or {}).get("event_type", "")
    open_question, open_question_source = norm_open_question(item, grade, event_type)
    # 게이트 사유를 레코드에 함께 싣는다. delivery_log 에도 집계를 남기지만
    # **크롤 잡은 delivery_log.jsonl 을 커밋하지 않아 그 기록은 러너와 함께
    # 사라진다**(crawl.yml 의 git add 목록에 없음, 2026-08-04 규명). 아카이브는
    # 커밋되므로 여기 실어야 사후에 원인을 짚을 수 있다.
    # must_read 만 채운다 — 나머지는 애초에 후보가 아니라 'not_must_read' 가
    # 626건에 붙어도 정보가 없다. 빈 값이면 통과(importance 로 구분된다).
    oq_reject = open_question_reject_reason(item, grade, event_type) if grade == "must_read" else ""
    normalized = {
        "features": features,
        "importance": grade,
        "section": section if section in VALID_SECTIONS else default_section(
            article.get("domain", ""), article.get("title", "")
        ),
        "scope": norm_scope(item.get("scope")),
        "category": category if category in VALID_CATEGORIES else "정책",
        "topics": norm_topics(item.get("topics")),
        "countries": norm_countries(item.get("countries")),
        "article_type": norm_article_type(item.get("article_type")),
        "title_kr": title_kr,
        "summary": clean_text(item.get("summary")),
        # 빈껍데기 해석은 화면에 내보내지 않는다. 재생성시키지 않고 그냥 버린다 —
        # 문체 위반으로 기사를 격리하면 영문 제목 폴백으로 떨어져 더 나쁘다.
        "implication": drop_hollow_implication(item.get("implication"),
                                               article.get("title", "")),
        "why_important": clean_text(item.get("why_important")),
        "open_question": open_question,
        "open_question_source": open_question_source,
        "open_question_reject": oq_reject,
        "watch_next": "",
        "tags": [t for t in (item.get("tags") or []) if isinstance(t, str)][:3],
        "related_reports": [
            report for report in (item.get("related_reports") or []) if isinstance(report, str)
        ][:2],
    }
    normalized.update(normalize_event_date_fields(item))
    return normalized


# ---- batch 큐레이션 (기사 N건 → Gemini 1회 호출) -----------------------------
#
# 배경: 건별 호출(기사당 judge 1 + 큐레이션 1 = 2회 + 각 5초 대기)이 무료 티어
# 일일 한도를 소진 → 큐레이션 실패(영문 fallback·오분류·수집 0건인 날)의 근본 원인.
# 해결: CHUNK 건을 한 번에 분류. 호출 수 ~1/20. judge의 노이즈 컷은 큐레이션의
# importance=noise 가 흡수하므로 별도 judge 호출도 제거.

# 1회 호출당 기사 수. 이 값이 곧 하루 큐레이션 호출 수를 정한다 — 크롤이 매시간
# 돌기 때문에 chunk 하나가 호출 하나다.
#
# 10 → 15 (2026-08-06). 근거: 같은 날 크롤에서 6 chunk 전부 429 를 맞았는데
# **chunk 1 부터** 실패했다. 즉 그 실행이 쿼터를 태운 게 아니라 시작 시점에 이미
# 일일 한도가 비어 있었다(gemini-2.5-flash 공용 버킷). 네이버 수리로 유입이 늘면
# 소진이 더 앞당겨지므로 호출 수 자체를 줄인다: 54건 기준 6회 → 4회.
#
# 20 이 아니라 15 인 이유: 잘리면 SPLIT 경로가 오히려 호출을 더 쓴다.
# 첫 주 로그에 분할 재시도가 0 이면 20 으로 올릴 것.
BATCH_CHUNK = 15

# 한 크롤에서 큐레이션에 보낼 새 기사 상한 = 안전 밸브.
#
# 2026-08-06 네이버 쿼리를 수리하자 그동안 0건을 뱉던 키워드 78개가 한꺼번에
# 살아났고, 그 첫 크롤이 평소 3~8분에서 30분+ 로 늘어났다. 상한이 없으면 유입이
# 튀는 날마다 큐레이션이 무제한으로 늘어 무료 티어를 태우고 다음 크롤까지 막는다
# (concurrency: cancel-in-progress: false 라 뒤에 줄을 선다).
#
# 잘라도 유실되지 않는다 — state["sent"] 마킹은 큐레이션 **뒤**에 일어나므로
# 남은 기사는 다음 크롤에서 다시 후보가 된다. final_articles 가 pub 오름차순이라
# 앞에서 자르면 오래된 것부터 빠져 굶는 항목이 생기지 않는다(FIFO).
MAX_CURATION_PER_RUN = int(os.environ.get("MAX_CURATION_PER_RUN", "80"))

# 2.5-flash 는 thinking 토큰이 maxOutputTokens 를 함께 잠식한다 (trend_insights.py:138,
# issue_review.py:43 에도 같은 함정이 박제돼 있다). 실측: curated.json 의 완결 항목
# 하나가 JSON 으로 508자(p50)·633자(p90) → 10건이면 본문만 3~4천 토큰이다. 8192 로는
# thinking 이 4천만 넘겨도 잘렸고, 그게 chunk 통째 유실의 원인이었다.
# 상한을 올려도 과금·지연은 실사용 토큰 기준이라 늘지 않는다 — 천장만 높이는 것.
BATCH_MAX_OUTPUT_TOKENS = 16384

# 잘림·타임아웃으로 chunk 가 통째로 실패하면 절반으로 쪼개 다시 부른다. 무료 티어
# 한도를 지키려고 run 당 추가 호출 수를 묶어둔다 (10건 chunk 를 1건까지 쪼개면
# 최악 15회 — 그건 한도를 태운다).
BATCH_SPLIT_BUDGET = 6

BATCH_SUFFIX = """

[배치 모드 — 출력 형식 오버라이드]
이번에는 기사 여러 건을 한 번에 받습니다. 위의 모든 분류 규칙·필드 정의를 각 기사에
동일하게 적용하되, 출력은 아래 JSON 한 객체만 (다른 텍스트·펜스 금지):

{"items": [{"idx": 0, "importance": "...", "section": "...", "scope": "kr|overseas", "category": "...", "title_kr": "...", "summary": "...", "implication": "...", "why_important": "...", "open_question": "...|null", "open_question_source": "title|description|article_text|unknown", "tags": [], "topics": [], "countries": [], "article_type": "...", "event_date": "2026-08-01|null", "event_date_type": "announcement|occurrence|effective|deadline|scheduled|unknown", "event_date_precision": "day|month|year|unknown", "event_date_source": "title|description|article_text|unknown", "related_reports": [], "features": {"event_type": "...", "korea_relevance": 0, "market_materiality": 0, "policy_materiality": 0, "report_worthiness": 0}}]}

[features — 랭킹용 구조화 지표. 제목·요약에서 확인되는 것만 근거로 매김]
- event_type: 다음 중 하나 (사건의 성격):
  policy_decision(정부·국회 정책 결정·법안 통과) / regulatory_action(인허가·규제 의결) /
  contract_award(계약·수주 체결) / project_milestone(착공·준공·임계·병입 등 사업 이정표) /
  incident_safety(사고·안전 이슈) / corporate_move(기업 전략·투자·조직) /
  market_signal(시장·가격·수급 신호) / research_report(연구·보고서 발간) /
  opinion(칼럼·의견·전망) / other
- 아래 4개는 0~3 정수. 0=무관/없음, 1=약함, 2=유의미, 3=강함:
  korea_relevance(한국·한수원 직접 관련성), market_materiality(시장·투자 영향),
  policy_materiality(정책·규제 영향),
  report_worthiness(부서 보고서로 다룰 가치 — 매우 엄격, 대부분 0)
- novelty·evidence_strength 는 묻지 않는다. 비교 대상 없이 절대 점수를 매기면
  대부분 중간값으로 몰려 변별이 안 되므로 ranking.py 가 아카이브 이력과 표현으로
  직접 판정한다 (2026-08-01).
- 확인 불가능하면 낮은 쪽으로. 지어내지 말 것.

- 모든 idx 가 정확히 한 번씩 등장. 빠지거나 중복 금지.
- 제목 앞에 (OFFICIAL) 표시가 있으면 정부·규제기관·국제기구의 공식 원문입니다:
  본문이 의결·정책 발표·중대 결정·인허가 등 정책 함의가 있는 경우만 must_read,
  채용·일반 행정·공지·축사·시상 등은 noise.
- 각주처럼 붙은 `관련보고서:` 줄이 있으면 해당 기사 분석에 활용 (실제 참조 시만 related_reports).

입력 형식: 각 기사가
[idx] (OFFICIAL)? 제목
요약: ...
출처: 도메인
(선택) 관련보고서: 제목1 / 제목2"""


def classify_request_failure(exc: Exception) -> str:
    """호출 자체가 실패했을 때 '다시 부를 가치가 있는가'로 라벨을 나눈다.

    라벨을 나누는 이유는 대응이 정반대라서다.

      - ``quota``   한도 소진. ``call_json`` 이 이미 429 를 백오프로 3회 재시도한
                    뒤에 올라온 것이므로, 여기서 또 부르면 남은 한도만 태우고
                    같은 실패를 반복한다 → 재시도 금지 (기존 판단 유지).
      - ``timeout`` 응답이 느렸을 뿐. 입력을 줄이면 짧아지므로 분할 재시도 대상.
      - ``other``   원인 불명. 함부로 다시 부르지 않는다 (기존 기본값 유지).

    ``truncated`` 는 예외 타입(``GeminiTruncated``)으로 이미 갈라지므로 여기 없다.
    """
    msg = str(exc)
    if "RESOURCE_EXHAUSTED" in msg or "HTTP 429" in msg:
        return "quota"
    if "TimeoutError" in msg or "timed out" in msg.lower():
        return "timeout"
    return "other"


# 입력을 줄이면 사라지는 실패만 분할 재시도한다. quota·other 는 쪼개도 그대로다.
SPLITTABLE_FAILURES = {"truncated", "timeout"}

# 이번 실행에서 일일 한도 소진을 만났는가. curate_batch 가 세우고 큐 적재 루프가 읽는다.
#
# 왜 필요한가: 큐레이션 실패분은 fallback_curation() 이 받아 **importance=nice_to_know
# + features 없음**으로 큐에 넣고 sent 마킹까지 한다. 그러면 ①비원자력 기사가 noise
# 판정을 못 받고 그대로 들어오고(노이즈 필터가 곧 LLM 이다) ②features 결손이라
# ranking.floor_verdict 의 면제에 걸려 하한을 우회하며 ③sent 마킹 14일 + 아카이브
# hash 스킵 때문에 **영영 제대로 큐레이션되지 않는다.**
#
# 분당 한도면 다음 시각에 풀리므로 fallback 이 합리적이지만, 일일 한도는 리셋까지
# 몇 시간이 남아 그 사이 수집분 전체가 영구 강등된다. 실측 2026-08-06: 한 크롤에서
# 54/54 건이 이 경로로 강등됐고 그중 16건이 사람이 골라내야 하는 잡음이었다.
DAILY_QUOTA_EXHAUSTED = False


def request_failure_reason(failures: dict[str, list[str]], chunk: list[dict]) -> str:
    """chunk 가 '호출 자체 실패'로 전건 날아갔으면 그 사유 라벨, 아니면 빈 문자열.

    호출 실패는 chunk 전건에 같은 사유가 찍히므로(부분 실패가 아니다) 전건 여부로
    판정한다 — 일부만 request 인 상태는 만들어지지 않는다.

    건수가 아니라 **hash 전건 존재**로 판정한다. 같은 hash 가 chunk 안에 두 번 들어와
    건수가 어긋나면, 건수 비교로는 '호출 실패가 아님'으로 새어 나가고 그 chunk 는
    재생성 대상도 유실 기록 대상도 아닌 채 조용히 사라진다 — 고치려던 그 버그다.
    """
    if not chunk:
        return ""
    reasons = set()
    for art in chunk:
        parts = (failures.get(art["hash"]) or [""])[0].split(":")
        if len(parts) < 2 or parts[0] != "request":
            return ""
        reasons.add(parts[1])
    return reasons.pop() if len(reasons) == 1 else "other"


def append_curation_failure(lost: dict[str, str], articles: list[dict],
                            path: Path | None = None,
                            now: datetime | None = None) -> bool:
    """호출 실패로 유실된 기사를 ``delivery_log.jsonl`` 에 한 줄 남긴다.

    콘솔 한 줄(``! batch 큐레이션 실패``)은 워크플로 로그가 만료되면 같이 사라진다.
    유실은 '무슨 기사가 브리핑에 아예 안 올라왔나'라서 사후 감사 대상이고, 그래서
    지속 기록이 필요하다. 기록이 없으면 다음에 같은 일이 나도 또 재현부터 해야 한다.

    ``record_type`` 이 붙은 줄은 기존 리더가 전부 건너뛴다 —
    daily_lead.collect_today · metrics.load_data · build_data 모두 truthy 검사라
    새 타입을 추가해도 기사 집계가 오염되지 않는다.

    품질 게이트 격리(``summary:incomplete`` 등)는 여기 담지 않는다. 그쪽은 기사별로
    제목까지 찍히므로 이미 보이고, 재생성 기회도 한 번 받는다. 조용히 사라지는 건
    호출 실패뿐이다.
    """
    if not lost:
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    by_hash = {art["hash"]: art for art in articles}
    reasons: dict[str, int] = {}
    for detail in lost.values():
        parts = detail.split(":")
        label = parts[1] if len(parts) > 1 else "other"
        reasons[label] = reasons.get(label, 0) + 1
    rec = {
        "record_type": "curation_failure",
        "date": now.astimezone(KST).date().isoformat(),
        "generated_at": now.astimezone(KST).isoformat(),
        "lost": len(lost),
        "candidates": len(articles),
        "reasons": reasons,
        # 사후에 '어떤 기사였나'를 되짚을 수 있어야 한다. 한 run 의 유실은 chunk 몇
        # 개 규모라 통째로 담아도 로그가 부풀지 않는다 (상한만 걸어둔다).
        "items": [
            {"hash": h,
             "title": (by_hash.get(h, {}).get("title") or "")[:120],
             "link": by_hash.get(h, {}).get("link", ""),
             "reason": detail[:200]}
            for h, detail in list(lost.items())[:20]
        ],
    }
    try:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        # 기록 실패로 크롤을 죽이지 않는다 — 유실 기록은 부가 정보다.
        print(f"  ! 큐레이션 유실 기록 실패: {exc}")
        return False
    return True


def append_open_question_stats(verdicts: dict[str, dict],
                               path: Path | None = None,
                               now: datetime | None = None) -> bool:
    """must_read 가 ``open_question`` 게이트의 **어느 조건**에서 걸렸는지 남긴다.

    왜 필요한가: 2026-08-03 기준 아카이브 must_read 51건의 ``open_question`` 이
    전건 비어 있는데, 원인을 짚을 수 없었다. 게이트가 다섯 사유를 하나로 뭉개고
    아무 기록도 남기지 않기 때문이다. 이 값이 0인 한 웹의 이슈 지도(Atlas)에서
    '남은 질문' 노드를 만들 수 없다 — 그래서 원인 규명이 선행 조건이다.

    **생성률을 KPI 로 삼지 말 것.** 게이트를 풀면 AI 가 미확정 사항을 지어낸다.
    이 기록은 *어디서 막히는가* 를 보기 위한 것이지 *몇 건 나왔나* 를 올리기 위한
    것이 아니다. 특히 ``llm_null`` 이 대부분이면 게이트는 무죄고 프롬프트를 봐야 한다.

    ``record_type`` 이 붙은 줄은 기존 리더가 전부 건너뛴다
    (``daily_lead.collect_today`` · ``metrics.load_data`` · ``build_data``).
    """
    if not verdicts:
        return False
    path = path or DELIVERY_LOG_FILE
    now = now or datetime.now(timezone.utc)
    reasons: dict[str, int] = {}
    for row in verdicts.values():
        label = row.get("reason") or "accepted"
        reasons[label] = reasons.get(label, 0) + 1
    rec = {
        "record_type": "open_question_gate",
        "date": now.astimezone(KST).date().isoformat(),
        "generated_at": now.astimezone(KST).isoformat(),
        "must_read": len(verdicts),
        "accepted": reasons.get("accepted", 0),
        "reasons": reasons,
        # 걸린 원문을 몇 건 남긴다. "게이트가 먹었다"까지는 집계로 알 수 있지만
        # **무엇을 먹었는지**는 문장을 봐야 판단이 선다(예: 전부 물음표로 끝나면
        # 프롬프트의 '완결형 서술문' 지시가 안 먹히는 것이다).
        "samples": [
            {"hash": h, "reason": row.get("reason", ""),
             "text": (row.get("text") or "")[:120],
             "source": row.get("source", "")}
            for h, row in list(verdicts.items())
            if row.get("reason") not in ("", "accepted")
        ][:10],
    }
    try:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"  ! open_question 게이트 기록 실패: {exc}")
        return False
    return True


def curate_batch(articles: list[dict], reports_kb: list[dict]) -> dict[str, dict]:
    """새 기사 목록을 chunk 단위 배치 호출로 큐레이션. {hash: cur_dict} 반환.

    문장 완결성·길이 게이트를 통과하지 못한 항목만 한 번 재생성한다. 재생성에도
    실패하면 결과에서 제외하여 잘린 문장이 아카이브나 브리핑으로 넘어가지 않는다.

    호출 자체가 실패하면(잘림·타임아웃) chunk 를 절반으로 쪼개 다시 부른다. 예전엔
    통째로 버려서 그 기사들이 fallback 큐레이션(영문 제목·implication 공란·features
    없음)으로 큐에 들어갔고, 큐에 들어가는 순간 ``sent`` 로 마킹돼 재수집이 막히므로
    영영 복구되지 않았다.
    """
    if not articles:
        return {}
    if not gemini_rest_available():
        print("  ! GEMINI_API_KEY 없음 → batch 큐레이션 건너뜀 (전건 fallback)")
        return {}

    system_prompt = CURATION_SYSTEM_PROMPT + BATCH_SUFFIX

    def run_chunk(chunk: list[dict], error_notes: dict[str, list[str]] | None = None):
        blocks = []
        for i, art in enumerate(chunk):
            official = " (OFFICIAL)" if is_tier1_source(art) else ""
            lines = [f"[{i}]{official} {art['title'][:150]}",
                     f"요약: {(art.get('description') or '')[:200]}",
                     f"출처: {art.get('publisher') or art.get('domain','')}"]
            relevant = find_relevant_reports(art["title"], art.get("description", ""), reports_kb)
            if relevant:
                titles = " / ".join(r.get("title", "")[:40] for r in relevant[:2])
                lines.append(f"관련보고서: {titles}")
            if error_notes and art["hash"] in error_notes:
                lines.append("이전 출력 오류: " + ", ".join(error_notes[art["hash"]]))
            blocks.append("\n".join(lines))

        try:
            result = gemini_call_json(
                system_prompt + (
                    "\n\n[재생성] 이전 출력의 오류가 표시된 항목입니다. 사실·시제를 유지하면서 "
                    "제한 안에서 완결형 문장으로 전부 다시 작성하세요."
                    if error_notes else ""
                ),
                "\n\n---\n\n".join(blocks),
                temperature=0.2, max_output_tokens=BATCH_MAX_OUTPUT_TOKENS, timeout=150.0,
                # 재생성인지 최초 호출인지를 갈라서 센다. 429 가 분당 한도였는데
                # 그 1분에 누가 몇 번 불렀는지 몰라 원인을 두 번 잘못 짚었다.
                label="curation:재생성" if error_notes else "curation",
            )
        except GeminiTruncated as e:
            return {}, {art["hash"]: [f"request:truncated:{e}"] for art in chunk}
        except GeminiError as e:
            return {}, {art["hash"]: [f"request:{classify_request_failure(e)}:{e}"]
                        for art in chunk}

        items = result.get("items")
        if not isinstance(items, list):
            return {}, {art["hash"]: ["response:items_missing"] for art in chunk}

        valid: dict[str, dict] = {}
        failures: dict[str, list[str]] = {}
        seen_indexes: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            idx = item.get("idx")
            if not isinstance(idx, int) or not (0 <= idx < len(chunk)):
                continue
            if idx in seen_indexes:
                failures[chunk[idx]["hash"]] = ["response:duplicate_idx"]
                valid.pop(chunk[idx]["hash"], None)
                continue
            seen_indexes.add(idx)
            art = chunk[idx]
            normalized = normalize_curation_item(item, art)
            # open_question 게이트 계측. hash 로 덮어쓰므로 분할 재시도가 같은 기사를
            # 두 번 세지 않는다(마지막 판정이 남는다). 판정 자체는 바꾸지 않는다.
            if normalized.get("importance") == "must_read":
                oq_verdicts[art["hash"]] = {
                    "reason": open_question_reject_reason(
                        item, "must_read",
                        (normalized.get("features") or {}).get("event_type", "")),
                    "text": clean_text(item.get("open_question")),
                    "source": (item.get("open_question_source") or "").strip().lower(),
                }
            # ★ 결손을 막는 실효 지점. 프롬프트가 features 를 요구하므로 빠진 응답은
            # 재생성 대상이다. 여기서 안 잡으면 결손인 채 캐시·큐에 들어가고, 큐에
            # 들어간 기사는 sent 로 마킹돼 다시 수집되지 않으므로 고칠 기회가 없다.
            # 그 상태로 남으면 ranking 이 _legacy_score() 를 타 event_weights 도
            # feature 가중치도 반영되지 않는다. 근거: docs/AS_IS.md §2.
            errors = curation_errors(normalized, require_features=True)
            if errors:
                failures[art["hash"]] = errors
            else:
                valid[art["hash"]] = normalized

        for idx, art in enumerate(chunk):
            if idx not in seen_indexes:
                failures[art["hash"]] = ["response:idx_missing"]
        return valid, failures

    out: dict[str, dict] = {}
    lost: dict[str, str] = {}          # hash → 최종 실패 사유 (유실 기록용)
    oq_verdicts: dict[str, dict] = {}  # hash → open_question 게이트 판정 (계측용)
    split_budget = BATCH_SPLIT_BUDGET

    def process(chunk: list[dict], label: str) -> None:
        """chunk 하나를 큐레이션해 out/lost 를 채운다."""
        nonlocal split_budget
        valid, failures = run_chunk(chunk)
        out.update(valid)

        reason = request_failure_reason(failures, chunk)
        if reason:
            detail = failures[chunk[0]["hash"]][0]
            # 입력을 줄이면 사라지는 실패는 쪼개서 되살린다. 통째로 버리면 이 기사들은
            # fallback 로 큐에 들어가 sent 마킹되고 다시는 큐레이션되지 않는다.
            if reason in SPLITTABLE_FAILURES and len(chunk) > 1 and split_budget > 0:
                split_budget -= 1
                mid = len(chunk) // 2
                print(f"  ! {label} 호출 실패({reason}) → "
                      f"{len(chunk)}건을 {mid}/{len(chunk) - mid} 로 분할 재시도")
                process(chunk[:mid], f"{label}a")
                time.sleep(1)
                process(chunk[mid:], f"{label}b")
                return
            global DAILY_QUOTA_EXHAUSTED
            if gemini_client._is_daily_quota(str(detail)):
                DAILY_QUOTA_EXHAUSTED = True
            for art in chunk:
                lost[art["hash"]] = detail
            capped = " (분할 예산 소진)" if reason in SPLITTABLE_FAILURES else ""
            # 160자로 자르면 429 의 quotaId·quotaValue 가 잘려 **분당 한도인지
            # 일일 한도인지 판정할 수 없다**(2026-08-06 실측: 6 chunk 전부 429 인데
            # 어느 쪽인지 로그로 못 가렸다). 처방이 다르므로 넉넉히 남긴다 —
            # 분당이면 chunk 간 대기, 일일이면 호출 수 자체를 줄여야 한다.
            print(f"  ! batch 큐레이션 실패 ({label}) — {len(chunk)}건 유실{capped}: "
                  f"{detail[:600]}")
            return

        retryable = [
            art for art in chunk
            if art["hash"] in failures
            and not failures[art["hash"]][0].startswith("request:")
        ]
        if retryable:
            print(f"  ! 품질 게이트 재생성: {len(retryable)}건")
            repaired, remaining = run_chunk(retryable, failures)
            out.update(repaired)
            for art in retryable:
                if art["hash"] in remaining:
                    print(
                        f"  ! 큐레이션 격리 '{art['title'][:35]}': "
                        + ", ".join(remaining[art["hash"]])
                    )

    for start in range(0, len(articles), BATCH_CHUNK):
        process(articles[start:start + BATCH_CHUNK],
                f"chunk {start // BATCH_CHUNK + 1}")

        # 무료 티어 분당 한도 배려 — chunk 사이 짧은 대기
        if start + BATCH_CHUNK < len(articles):
            time.sleep(3)

    if lost:
        print(f"  ! 큐레이션 유실 {len(lost)}/{len(articles)}건 — "
              f"delivery_log.jsonl 에 기록 (fallback 큐레이션으로 넘어감)")
        append_curation_failure(lost, articles)

    # 조용히 지우면 프롬프트가 망가진 것을 아무도 모른다. 실측 기준선: 옛 프롬프트에서
    # implication 의 48%(64건 중 31건)가 여기 걸렸다. 새 프롬프트로 이 비율이
    # 떨어지지 않으면 프롬프트가 안 먹은 것이다.
    if HOLLOW_IMPLICATION_DROPS:
        print(f"  ! 빈껍데기 해석 {len(HOLLOW_IMPLICATION_DROPS)}/{len(articles)}건 폐기 "
              f"(재생성 안 함 — 빈칸이 낫다)")
        for line in HOLLOW_IMPLICATION_DROPS[:5]:
            print(f"      · {line}")
        HOLLOW_IMPLICATION_DROPS.clear()

    if oq_verdicts:
        blocked: dict[str, int] = {}
        for row in oq_verdicts.values():
            blocked[row.get("reason") or "accepted"] = \
                blocked.get(row.get("reason") or "accepted", 0) + 1
        print(f"  · open_question 게이트: must_read {len(oq_verdicts)}건 → "
              + " / ".join(f"{k} {v}" for k, v in sorted(blocked.items())))
        append_open_question_stats(oq_verdicts)

    return out


def resolve_rss_domain(src: dict, item: dict) -> str:
    """RSS 항목의 출처 도메인.

    기관 site: 피드는 domain_label 이 이미 정확하므로 그대로 쓰고,
    매체가 섞이는 키워드 검색 피드(resolve_publisher=True)만 <source> 의
    실제 매체 도메인으로 복원한다 — 전건이 news.google.co.kr 로 뭉개지면
    카드에 매체명이 안 보이고 신뢰도 점수도 매길 수 없다.
    """
    if src.get("resolve_publisher") and item.get("publisher_domain"):
        return item["publisher_domain"]
    return src.get("domain_label") or get_domain(item["link"])


def publisher_of(entry) -> tuple[str, str]:
    """RSS <source> 에서 발행 매체명·도메인 추출. (Google News 검색 피드용)

    Google News 검색 RSS 의 link 는 news.google.com 리다이렉트라 실제 매체를 알 수
    없다. 대신 각 entry 의 <source url="https://www.electimes.com">전기신문</source>
    에 원 매체가 그대로 들어 있다.
    """
    src = entry.get("source") or {}
    try:
        name = (src.get("title") or "").strip()
        href = (src.get("href") or "").strip()
    except AttributeError:      # feedparser 가 dict 아닌 값을 준 경우
        return "", ""
    return name, get_domain(href) if href else ""


def strip_title_suffix(title: str, publisher: str) -> str:
    """제목 끝의 ' - 매체명' 반복 제거 (Google News 표기 습관)."""
    if not publisher:
        return title
    return split_title_publisher(title, publisher)[0]


def fetch_rss(url: str) -> list[dict]:
    import feedparser

    try:
        feed = feedparser.parse(url, agent="nuclear-news-bot/1.0")
        out = []
        for entry in feed.entries:
            link = entry.get("link", "")
            title = entry.get("title", "")
            description = entry.get("summary", "") or entry.get("description", "")
            pub = None
            if entry.get("published_parsed"):
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif entry.get("updated_parsed"):
                pub = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            if not link or not title or not pub:
                continue
            pub_name, pub_domain = publisher_of(entry)
            raw_title = strip_html(title)
            host = (urlparse(link).hostname or "").lower()
            if host.endswith("news.google.com"):
                clean_title, inferred_publisher = split_title_publisher(raw_title, pub_name)
                pub_name = pub_name or inferred_publisher
            else:
                clean_title = strip_title_suffix(raw_title, pub_name)
            if pub_name and not pub_domain:
                pub_domain = source_profile("", pub_name).get("domain", "")
            out.append({
                "link": normalize_url(link),
                "raw_link": link,
                # Google News 는 제목 끝에 " - 매체명" 을 붙인다 (때로 두 번) → 제거.
                # 큐레이션·중복판정에 매체명이 섞여 들어가는 것을 막는다.
                "title": clean_title,
                "description": strip_html(description),
                "pub": pub,
                "publisher": pub_name,
                "publisher_domain": pub_domain,
            })
        return out
    except Exception as e:
        print(f"  ! RSS fetch failed for {url}: {e}")
        return []


def assign_feed_from_title(title: str) -> str:
    t = title.lower()
    return "SMR" if any(h in t for h in SMR_HINTS) else "정책"


def passes_source_keyword_gate(src: dict, item: dict) -> bool:
    """`require_keywords` 를 단 출처만 제목·요약에서 키워드를 확인한다.

    Google News 는 매체에 따라 `site:` 쿼리에 붙인 괄호 키워드를 통째로 무시한다
    (실측 2026-08-05 Euractiv: `site:euractiv.com (nuclear OR reactor OR SMR OR
    uranium) when:2d` 23건 중 원자력 기사 3건. 나머지는 양모·Ozempic·셍겐).
    같은 실패로 Le Figaro·電気新聞이 후보에서 탈락한 전례가 있다. 피드를 버리는
    대신 수집 단계에서 한 번 거른다 — 큐레이션 LLM 에 넣기 전에 잘라야 토큰이
    안 샌다.
    """
    keywords = src.get("require_keywords")
    if not keywords:
        return True
    haystack = f"{item.get('title', '')} {item.get('description', '')}".lower()
    return any(keyword in haystack for keyword in keywords)


def collect_discovery(state: dict, anchors: list[str], negative_terms: str = "") -> list[dict]:
    """discovery 가 세운 쿼리를 네이버로 던지고 성과를 기록한다.

    쿼리 생성은 discovery.py 가 결정적으로 하고(LLM 0회), 여기서는 실행과 상태
    기록만 한다. 쿼리마다 따로 도는 이유는 **성과를 쿼리 단위로 남기기 위해서**다
    — 합쳐 돌리면 어느 조합이 헛도는지 알 수 없어 냉각을 걸 수 없다.
    네트워크 호출 수는 합쳐 돌 때와 같다.
    """
    import discovery

    rows = discovery.load_recent_archive_rows()
    if not rows:
        print("[discovery] 아카이브 비어 있음 → 건너뜀")
        return []
    registry = entity_match.load_entity_registry()
    dstate = discovery.load_state()
    queries, dstate = discovery.plan_queries(rows, registry, dstate)
    if not queries:
        print("[discovery] 쿼리 0건(전부 냉각 중이거나 씨앗 없음)")
        return []

    out: list[dict] = []
    results: list[dict] = []
    for spec in queries:
        found = collect_articles(f"discovery:{spec['entity_id']}", [spec["query"]],
                                 anchors, state, negative_terms=negative_terms)
        for article in found:
            article["discovery_entity"] = spec["entity_id"]
        out.extend(found)
        # collect_articles 는 article_seen 으로 이미 본 URL 을 걸러 내므로
        # 여기 남은 것은 정의상 전부 신규다.
        results.append({**spec, "result_count": len(found), "new_article_count": len(found)})

    dstate = discovery.record_results(dstate, results)
    dstate = discovery.prune_state(dstate)
    discovery.save_state(dstate)
    yielded = sum(1 for r in results if r["new_article_count"])
    print(f"[discovery] 쿼리 {len(queries)}건 → 신규 {len(out)}건 "
          f"(성과 있는 쿼리 {yielded}건, 엔티티 {len({q['entity_id'] for q in queries})}개)")
    return out


def collect_rss_articles(state: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS * 4)
    by_title: dict[str, dict] = {}

    for src in RSS_SOURCES:
        items = fetch_rss(src["url"])
        print(f"[RSS] {src['name']}: {len(items)} entries")
        for item in items:
            if item["pub"] < cutoff:
                continue
            if invalid_url_reason(item["link"]):
                continue
            h = url_hash(item["link"])
            if article_seen(state, item.get("raw_link") or item["link"]):
                continue
            if is_promotional(item["title"], item["description"]):
                continue
            if not passes_source_keyword_gate(src, item):
                continue

            norm = normalize_title(item["title"])
            if not norm or norm in by_title:
                continue

            domain = resolve_rss_domain(src, item)
            by_title[norm] = {
                "hash": h,
                "title": item["title"],
                "description": item["description"],
                "link": item["link"],
                "pub": item["pub"],
                "matched": src["name"],
                # 출처 신뢰도 점수. 기관·전문지(TIER1)만 10, 일반 매체는 도메인 점수.
                # 예전엔 RSS 경로 전건이 10이라 일반 언론 기사까지 '1차 소스'로
                # 취급돼 must_read 로 격상되던 문제가 있었다.
                "score": source_score(domain, item.get("publisher", "")),
                "domain": domain,
                "publisher": item.get("publisher", ""),
                "feed": assign_feed_from_title(item["title"]),
            }

    return list(by_title.values())


def collect_articles(feed_name: str, keywords: list[str], anchors: list[str], state: dict, negative_terms: str = "") -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    by_title: dict[str, dict] = {}
    # 쿼리에 붙이지 않고 수집 후 제목 제외에 쓴다 — search_naver 주석 참고.
    negatives = parse_negative_terms(negative_terms)

    for kw in keywords:
        try:
            items = search_naver(kw)
        except Exception as e:
            print(f"  ! [{feed_name}] '{kw}' search failed: {e}")
            continue

        for item in items:
            raw_link = item.get("originallink") or item.get("link")
            if not raw_link:
                continue
            link = normalize_url(raw_link)
            if invalid_url_reason(link):
                continue

            try:
                pub = parsedate_to_datetime(item["pubDate"])
            except Exception:
                continue
            if pub < cutoff:
                continue

            h = url_hash(link)
            if article_seen(state, raw_link):
                continue

            title = strip_html(item.get("title", ""))
            desc = strip_html(item.get("description", ""))

            if is_rejected_title(title, negatives):
                continue
            if is_promotional(title, desc):
                continue
            if is_stub(desc):
                continue
            if not passes_anchor_filter(title, desc, anchors):
                continue

            domain = get_domain(link)
            profile = source_profile(domain)
            score = source_score(domain, profile.get("publisher", ""))
            if score < MIN_SCORE:
                continue

            norm = normalize_title(title)
            if not norm:
                continue

            existing = by_title.get(norm)
            if existing and existing["score"] >= score:
                continue

            by_title[norm] = {
                "hash": h,
                "title": title,
                "description": desc,
                "link": link,
                "pub": pub,
                "matched": kw,
                "score": score,
                "domain": domain,
                "publisher": profile.get("publisher", ""),
                "feed": feed_name,
            }
        time.sleep(0.1)

    return sorted(by_title.values(), key=lambda x: x["pub"])


def dedup_exact_candidates(articles: list[dict]) -> list[dict]:
    """URL 정규화 1차, 제목 완전일치 2차로 수집 후보를 결정적으로 줄인다."""
    by_url: dict[str, dict] = {}
    for article in articles:
        normalized = normalize_url(article.get("link"))
        if invalid_url_reason(normalized):
            continue
        candidate = dict(article)
        candidate["link"] = normalized
        candidate["hash"] = url_hash(normalized)
        existing = by_url.get(normalized)
        if existing is None or candidate.get("score", 0) > existing.get("score", 0):
            by_url[normalized] = candidate

    by_title: dict[str, dict] = {}
    for article in by_url.values():
        key = title_key(article.get("title"))
        if not key:
            continue
        existing = by_title.get(key)
        if existing is None or article.get("score", 0) > existing.get("score", 0):
            by_title[key] = article
    return list(by_title.values())


SECTION_LABEL = {
    "khnp": "🇰🇷 한수원",
    "domestic": "🏛️ 국내",
    "international": "🌐 해외",
    "smr": "🔋 SMR",
}
CATEGORY_EMOJI = {"정책": "🏛", "기술": "⚙️", "시장": "📈", "규제": "📋"}


def format_must_read(article: dict, curation: dict) -> str:
    section = curation.get("section", "domestic")
    category = curation.get("category", "정책")
    section_lbl = SECTION_LABEL.get(section, section)
    cat_emoji = CATEGORY_EMOJI.get(category, "📌")

    original_title = article["title"]
    title_kr = curation.get("title_kr") or original_title
    show_original = title_kr.strip() != original_title.strip()

    why = html.escape(curation.get("why_important", ""))
    related = curation.get("related_reports") or []

    parts = [f"🔴 <b>[{section_lbl}] {cat_emoji} [{category}]</b> {html.escape(title_kr)}"]
    if show_original:
        parts.append(f"\n<i>{html.escape(original_title)}</i>")
    if why:
        parts.append(f"\n💡 {why}")
    if related:
        report_str = ", ".join(html.escape(r) for r in related)
        parts.append(f"\n📚 관련 사내 보고서: <i>{report_str}</i>")
    parts.append(f"\n🔗 {article['link']}")
    return "".join(parts)


def main() -> None:
    config = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    curated = load_curated()
    queue = load_queue()
    # 안전장치: daily-brief clear 가 실패해 큐가 쌓여도 3일 지난 항목은 제거
    # (이미 발송됐을 것 — 무한 반복 방지)
    _qcut = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _before = len(queue)
    queue = [q for q in queue if (q.get("queued_at") or "9999") >= _qcut]
    if len(queue) < _before:
        print(f"큐 정리: {_before} → {len(queue)} (3일 경과 제거)")
    reports_kb = load_reports_kb()
    print(f"Loaded {len(reports_kb)} reports from KB")

    sent_immediate = 0
    queued = 0
    dropped = 0
    # 큐에 들어간 항목 중 features 없는 건수. 결손은 로그에 아무 흔적을 남기지
    # 않아서, must_read 의 상당수가 랭킹에서 사실상 빠져 있다는 사실이 몇 달간
    # 보이지 않았다. 큐에 들어가면 sent 마킹으로 되돌릴 수 없으므로 이 값이 0 에
    # 수렴하는지가 S1 의 성패다. 근거: docs/score_distribution.md §4.
    features_missing = 0
    skipped_quota = 0
    # 모듈 전역이라 한 프로세스에서 두 번 돌면 이전 실행의 판정이 남는다.
    global DAILY_QUOTA_EXHAUSTED
    DAILY_QUOTA_EXHAUSTED = False
    now_iso = datetime.now(timezone.utc).isoformat()

    all_candidates: list[dict] = []

    for feed_name, feed_cfg in config.items():
        kw_list = feed_cfg["keywords"]
        anchors = feed_cfg.get("anchors", [])
        negative_terms = feed_cfg.get("negative_terms", "")
        print(f"[{feed_name}] {len(kw_list)} keywords (neg: '{negative_terms}')")
        articles = collect_articles(feed_name, kw_list, anchors, state, negative_terms=negative_terms)
        print(f"[{feed_name}] {len(articles)} candidates from Naver")
        all_candidates.extend(articles)

    # 후속 발굴 — 고정 키워드가 못 잡는 '상태가 뒤집힌 사건'을 물으러 간다.
    # 비치명: 실패해도 크롤 본체는 계속 돈다.
    try:
        policy_cfg = config.get("정책") or next(iter(config.values()), {})
        disc = collect_discovery(state,
                                 policy_cfg.get("anchors", []),
                                 policy_cfg.get("negative_terms", ""))
        all_candidates.extend(disc)
    except Exception as e:  # noqa: BLE001
        print(f"[discovery] 실패 → 건너뜀: {type(e).__name__}: {e}")

    rss_articles = collect_rss_articles(state)

    # 이메일 뉴스레터(ANS Nuclear News Daily) 외부 링크 합류 — IMAP 미설정 시 자동 스킵
    try:
        from email_ingest import fetch_newsletter_articles
        rss_articles.extend(fetch_newsletter_articles(state["sent"]))
    except Exception as e:  # noqa: BLE001
        print(f"[email] ingest 모듈 실패 → 건너뜀: {type(e).__name__}")
    print(f"[RSS] {len(rss_articles)} candidates")
    all_candidates.extend(rss_articles)

    exact_kept = dedup_exact_candidates(all_candidates)

    # Fuzzy dedup — 우라까이·받아쓰기 catch
    sorted_by_score = sorted(exact_kept, key=lambda x: x["score"], reverse=True)
    fuzzy_kept: list[dict] = []
    fuzzy_norms: list[str] = []
    for art in sorted_by_score:
        norm = normalize_title(art["title"])
        is_dup = False
        for kept_norm in fuzzy_norms:
            if difflib.SequenceMatcher(None, norm, kept_norm).ratio() >= 0.82:
                is_dup = True
                break
        if not is_dup:
            fuzzy_kept.append(art)
            fuzzy_norms.append(norm)

    print(
        f"After dedup: {len(all_candidates)} candidates → {len(exact_kept)} URL/title unique "
        f"→ {len(fuzzy_kept)} after fuzzy dedup"
    )

    emb_cache = load_embeddings_cache()
    semantically_unique = semantic_dedup(fuzzy_kept, emb_cache)
    save_embeddings_cache(emb_cache)
    print(f"After semantic dedup: {len(semantically_unique)} articles")

    final_articles = sorted(semantically_unique, key=lambda x: x["pub"])

    # ---- batch 큐레이션: 새 기사만 모아 N건 → 1회 호출 (무료 티어 quota 보호) ----
    # 기존: 기사당 judge 1회 + 큐레이션 1회 (+각 5초 대기) → 한도 소진이 실패의 근본 원인.
    # judge 의 노이즈 컷은 큐레이션의 importance=noise 로 흡수 (별도 호출 제거).
    new_articles = [
        article for article in final_articles
        if article["hash"] not in curated or needs_recuration(curated[article["hash"]])
    ]
    deferred = 0
    if len(new_articles) > MAX_CURATION_PER_RUN:
        deferred = len(new_articles) - MAX_CURATION_PER_RUN
        # pub 오름차순이므로 앞에서 자른다 — 오래된 것부터 처리(FIFO).
        new_articles = new_articles[:MAX_CURATION_PER_RUN]
    if new_articles:
        n_calls = (len(new_articles) + BATCH_CHUNK - 1) // BATCH_CHUNK
        print(f"Batch curation: 새 기사 {len(new_articles)}건 → Gemini {n_calls}회 호출")
    if deferred:
        # 조용히 자르면 '수집이 줄었다'로 오독한다. 다음 크롤에서 다시 온다.
        print(f"Batch curation: 상한 {MAX_CURATION_PER_RUN} 초과분 {deferred}건은 "
              f"다음 크롤로 이월 (seen 마킹 전이라 유실 아님)")
    batch_results = curate_batch(new_articles, reports_kb)

    # 후속·반복 보도 판정 재료. 아카이브를 못 읽어도 크롤은 계속한다(빈 목록이면
    # prior_coverage 0 → 전부 신규 취급).
    try:
        prior_titles = news_archive.load_recent_titles()
    except OSError as exc:
        print(f"[rank] 아카이브 제목 로딩 실패 — prior_coverage 생략: {exc}")
        prior_titles = []

    for article in final_articles:
        h = article["hash"]

        if h in curated and not needs_recuration(curated[h]):
            cur = curated[h]
        else:
            previous = curated.get(h) or {}
            cur = batch_results.get(h)
            if cur is None and DAILY_QUOTA_EXHAUSTED:
                # 일일 한도 소진 중에는 fallback 으로 강등해 큐에 넣지 않는다.
                # sent 마킹을 하지 않으므로 한도가 리셋된 뒤 다음 크롤이 다시 수집해
                # 제대로 큐레이션한다. LOOKBACK(6h) 밖으로 밀려난 기사는 놓치지만,
                # 전량을 영구 강등시키는 것보다 낫다 — 강등분은 되돌릴 방법이 없다.
                skipped_quota += 1
                continue
            if cur is None:
                cur = fallback_curation(article)
                if cur is None:
                    print(f"  ! 품질 격리(완결 요약 없음): {article['title'][:60]}")
                    continue
            cur["cached_at"] = now_iso
            cur["title"] = article["title"]
            cur["link"] = article["link"]
            cur["feed"] = article["feed"]
            cur["domain"] = article["domain"]
            cur["matched"] = article["matched"]
            # features 를 끝내 못 받았으면 시도 횟수를 누적한다. needs_recuration()
            # 이 이 값으로 재질의를 멈춘다. 받아냈으면 카운터를 지운다 — 나중에 다른
            # 이유로 결손이 재발했을 때 상한에 이미 걸려 있으면 안 된다.
            if isinstance(cur.get("features"), dict):
                cur.pop("features_attempts", None)
            else:
                cur["features_attempts"] = int(previous.get("features_attempts") or 0) + 1
            curated[h] = cur

        importance = cur.get("importance", "nice_to_know")

        if importance == "noise":
            state["sent"][h] = now_iso
            dropped += 1
            continue

        if not isinstance(cur.get("features"), dict):
            features_missing += 1

        # must_read 포함 모든 비-noise 항목을 큐에 적재 — 즉시 개별 발송 폐지,
        # 일일 브리핑(daily_brief)으로 통합. must_read 는 rank가 높아 브리핑 상단 노출.
        profile = source_profile(article.get("domain", ""), article.get("publisher", ""))
        queue.append({
            "hash": h,
            "title": article["title"],
            "title_kr": cur.get("title_kr") or article["title"],
            "link": article["link"],
            "domain": article["domain"],
            # 카드에 표기할 매체명 (전기신문 등). RSS <source> 에서만 얻어지므로 없을 수 있음
            "publisher": article.get("publisher") or profile["publisher"],
            "source_type": profile["source_type"],
            "evidence_role": profile["evidence_role"],
            "source_tier": profile["source_tier"],
            "feed": article["feed"],
            "matched": article["matched"],
            "importance": importance,
            # 기본값을 domestic 으로 두면 큐레이션 실패 기사가 국내로 섞임 → 도메인·제목 추정
            "section": cur.get("section") or default_section(article["domain"], article["title"]),
            "scope": norm_scope(cur.get("scope")),
            "category": cur.get("category", "정책"),
            "summary": cur.get("summary", ""),
            "implication": cur.get("implication", ""),
            # must_read 의 '왜 중요' — 기존 큐 스키마에 빠져 있어 카드에서 유실되던 필드
            "why_important": cur.get("why_important", ""),
            "open_question": cur.get("open_question", ""),
            "open_question_source": cur.get("open_question_source", "unknown"),
            "watch_next": cur.get("watch_next", ""),
            "tags": cur.get("tags", []),
            "related_reports": cur.get("related_reports") or [],
            "features": cur.get("features"),  # 랭킹용 (batch 실패분은 None)
            # 최근 21일 아카이브에서 같은 사건을 몇 번 다뤘는지. ranking.py 가
            # novelty 와 추적 가점을 여기서 판정한다 (LLM 절대평가 대체).
            "prior_coverage": prior_coverage_count(
                cur.get("title_kr") or article["title"], prior_titles
            ),
            "event_date": cur.get("event_date"),
            "event_date_type": cur.get("event_date_type", "unknown"),
            "event_date_precision": cur.get("event_date_precision", "unknown"),
            "event_date_source": cur.get("event_date_source", "unknown"),
            "queued_at": now_iso,
        })
        state["sent"][h] = now_iso
        queued += 1

    # ---- 영구 아카이브 적재 (웹 확장용 — 실패해도 크롤·발송은 계속) ----------
    # curated.json 은 14일 만료라 트렌드 재료가 안 쌓임 → noise 포함 전부 별도 적재.
    try:
        identities = news_archive.load_recent_identities()
        records = [
            news_archive.make_record(a, curated[a["hash"]], now_iso)
            for a in final_articles
            if a["hash"] in curated
            and not curation_errors(curated[a["hash"]])
            and a["hash"] not in identities["hashes"]
            and normalize_url(a.get("link")) not in identities["urls"]
            and title_key(a.get("title")) not in identities["titles"]
        ]
        n_archived = news_archive.append_records(records)
        if n_archived:
            print(f"Archive: {n_archived}건 적재")
    except Exception as e:
        print(f"[archive] 적재 실패 (크롤은 계속): {type(e).__name__}: {e}")

    save_state(state)
    save_curated(curated)
    save_queue(queue)
    rate = f"{features_missing / queued * 100:.1f}%" if queued else "—"
    print(f"Done. immediate={sent_immediate} queued={queued} dropped={dropped} "
          f"features_missing={features_missing} ({rate})")
    if skipped_quota:
        # 조용히 지나가면 '수집이 줄었다'로 오독한다. sent 마킹을 안 했으므로
        # 한도가 리셋된 뒤 다음 크롤이 다시 가져간다.
        print(f"[queue] 일일 한도 소진으로 {skipped_quota}건 적재 보류 "
              f"(fallback 강등 회피 — 리셋 후 재수집)")
    # 429 는 분당 20회였는데(2026-08-06 실측) 그 1분에 누가 몇 번 불렀는지가
    # 로그에 없어 원인을 두 번 잘못 짚었다. 이제 센다 — **최대 분당**이 20 을
    # 넘는지가 처방을 가른다(재시도를 줄일지, 호출자 사이를 벌릴지).
    try:
        print(gemini_client.format_call_stats())
    except Exception as exc:  # 계측이 본 작업을 죽이면 안 된다
        print(f"[gemini] 호출 통계 실패: {exc}")


if __name__ == "__main__":
    main()
