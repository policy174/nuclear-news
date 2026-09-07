"""연속일 반복 게이트 — "어제 보낸 이야기를 오늘 또 보내는가"를 발송 **전에** 본다.

왜 필요한가
-----------
2026-08-16 실측. 같은 회차에 두 칸이 같은 이야기였다.

    국내 1위  테라파워-한국 기업 SMR 협력, 한국 원전기업 수출 공급망 확대   29.3
    해외 3위  두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결    20.4

    8/15 해외  스페인, 알마라즈 원전 운영 기한 2030년까지 연장             18.1
    8/16 해외  스페인 정부, 알마라스(Almaraz) 원전 운영 연장 승인           19.5

기존 구조에서 이건 사고가 아니라 **설계대로**였다. 세 가지가 겹친다.

① 반복에 감점이 없다. `feature_weights.novelty = 0.0` 이라 `derive_novelty()` 가
   1점(반복)을 내도 점수에 0을 곱한다. 위 두 기사의 breakdown 에 novelty 항이
   아예 없는 이유다.
② 반복에 **가점**이 있다. `_tracking_bonus` 가 prior_coverage>0 이면
   +1.5(follow_up) / +0.5(repeat) 를 준다. 8/16 두산 건에 `tracking:repeat 0.5`
   가 붙어 있다. 감점 0 + 가점 >0 이면 중요한 이슈일수록 며칠 연속 올라온다.
③ 어제를 보는 눈이 아예 없다. `cluster_duplicates` · `dedup_articles` ·
   `editorial_dedup_articles` 는 전부 **그날 큐 안에서만** 돈다. 어제 발송분과
   대조하는 자리는 파이프라인에 없었다 — 웹(`build_data`)이 이슈로 잇지만
   그건 텔레그램이 나간 **뒤**다.

`prior_coverage` 가 있지 않냐 — 그건 다른 것을 센다. 최근 21일 **아카이브**(수집한
모든 기사)에서 제목이 닮은 것의 개수, 즉 '언론이 얼마나 썼나'다. '우리가 이미
보냈나'가 아니다. 그래서 그 가중치를 올리는 것은 오답이다: 크게 보도된 속보일수록
감점받는다. 여기서 보는 것은 `delivery_log` — **실제로 발송된 것**뿐이다.

무엇을 살리는가 — 단계가 넘어간 후속은 반복이 아니다
---------------------------------------------------
단순 중복 제거였다면 8/17 `공급 계약 체결` 이 8/16 `협력 확대` 에 접혀 사라진다.
그건 하필 가장 중요한 뉴스를 지우는 것이다(`event_stage.py` 가 같은 이유로 존재).
그래서 같은 이슈로 판정된 뒤에 한 번 더 묻는다 — **전일 대비 단계가 실제로
움직였는가.**

    협의 → MOU → 우선협상대상자 → 본계약 → 착공 → 준공          (deal)
    신청 → 심사 → 승인·인가 → 시행·발효                          (permit)
    전망·관측 → 공식 발표·확정                                    (certainty)

어느 척도에서든 칸이 올라갔으면 material — 감점을 거의 면제한다. 척도는 못
넘었지만 새 수치가 붙었으면 minor — 절반만 깎는다. 둘 다 아니면 none — 전액.

어휘가 못 넘는 벽 — 근거 교집합 (2026-08-22)
--------------------------------------------
위 판정은 전부 **글자**를 본다(제목 유사도·척도 어휘표·앵커). 그래서 같은 사건을
다른 매체가 다른 낱말로 쓰면 두 축이 함께 흔들린다.

    8/21 국내 1위  국회, 국가기간 전력망 확충 특별법 등 민생법안 70건 처리
    8/22 국내 3위  국회 본회의, 「국가기간 전력망 확충 특별법」 등 73건 의안 처리

8/20 국회 본회의 **한 건**이다. 70건과 73건은 사건이 진전된 것이 아니라 집계 범위
차이다. `same_issue` 는 정확히 붙였는데(제목 0.776 · 지문 0.723) 그 다음이 어긋났다:
어제 요약이 "통과", 오늘 요약이 "의결"이라 `certainty` 척도가 -1 → 1 로 갈렸고
규칙 ④ 가 걸려 minor(감점 절반)가 됐다. 그리고 hard_drop 은 제목 유사도 0.85 를
요구하는데 0.776 이라 닿지 않았다 — **다른 매체가 다른 표현으로 쓴 동일 사건은
정의상 그 문턱을 못 넘는다.** 진전 판정만 고쳐 none 이 나왔더라도 감점 5.0
(→22.94점)에 그쳐 그날 국내 최하 선정 점수 20.31 위에 그대로 남았다.

어휘를 안 타는 재료가 이미 파이프라인 안에 있었다. 그날 8/22 카드의
`story_members` 14건 **안에 전날 카드 자신이 들어 있었고**(수집 단계 임베딩 dedup 이
접었다) 두 카드의 근거 교집합은 12건이었다. 수집은 같은 사건이라고 판정해 놓았고,
그 판정이 이 게이트까지 오는 배선만 없었다.

그래서 `story_cluster.evidence_overlap` 을 세 번째 재료로 쓴다. 다만 **겹침의 절대
건수로는 못 가른다** — 실측에서 8/17→18 테라파워(공급계약 → SK 공조 합의, 진짜
후속)도 3건을 공유했고 서로의 근거 목록에 상대 카드를 들고 있었다. 가르는 것은
비율이고(0.188 ↔ 0.857), 그 사이가 비어 있다. 확정 강도일 때만 두 곳에서 쓴다:

    · `progression` 의 ④~⑥ 유보를 걷는다 (①~③ 의 실제 단계 이동은 그대로 이긴다)
    · `hard_drop` 의 '사실상 동일' 을 제목 **또는** 근거로 말할 수 있게 한다

읽는 시점이 중요하다 — story 는 `ranking.rank_and_select` **안에서** 조립되므로,
선정 앞에서 한 번만 판정하면 이 재료가 아직 없다. `ranking` 이 story 를 다 접은
뒤 `continuity_recheck` 로 이 모듈을 한 번 더 부른다.

감점은 얼마나 오래 가는가 — 근거의 세기가 창의 폭을 정한다
------------------------------------------------------------
처음에는 창이 하나였다(5일, 하루 20%씩 감쇠). 그러면 사흘 전 반복이 이미 40%로
식어 사실상 통과한다. 2026-08-17 에 발송 이력 365건(34일)을 전수 대조해 보니
그 구간에 진짜 반복이 남아 있었다.

    7/24 · 8/02 · 8/15   IAEA 사무총장, 우크라이나 상황 관련 성명 발표   (제목 동일)
    8/03 · 8/04 · 8/06   중국, 신규 원자로 8기 건설 승인
    7/23 · 7/25          삼성중공업, Sargent & Lundy 부유식 SMR MOU 체결 (제목 동일)
    8/06 · 8/09          미쓰비시 ↔ 일본 정부 차세대 원자로 지원        (주어만 뒤집힘)

그런데 같은 구간의 매칭을 전부 읽어 보면 **경로마다 정밀도가 다르다**. 제목이
닮아서 붙은 것은 대체로 맞았고, 앵커(이름)만으로 붙은 것은 3~14일 구간에서
5쌍 중 3쌍이 오탐이었다 — 뉴클레오(미국 인허가 ↔ 프랑스 MOX), 원자력안전위원회
(한울 4호기 정기검사 ↔ 입법예고), 아이다호 국립연구소(자금 선정 ↔ 협력).
간격이 벌어질수록 '같은 사람이 또 나왔다'와 '같은 사건이 이어진다'가 갈린다.

그래서 창은 하나가 아니라 **근거의 세기별로** 둔다. 셋 다 감쇠는 같고 시작점만
다르다(`penalty_window`).

    제목이 사실상 동일(≥0.85)  → 14일까지 만액, 그리고 후보에서 제외
    같은 이슈(제목·fingerprint) →  7일까지 만액, 이후 감쇠
    단계가 움직였는지 불확실     →  3일까지 (minor — '모르겠다'는 짧게)
    이름만 공유(앵커 단독)       →  1일까지 (예전 폭 그대로 — 정밀도가 낮다)
    단계가 실제로 넘어감         →  0일 (material — 기간과 무관하게 다시 나간다)

세 번을 넘기면 한 번 더 깎는다(`repeat_streak_penalty`). 위 IAEA·중국 사례처럼
같은 이야기가 창 안에서 세 번째로 오는 것은 '이어지는 이슈'가 아니라 재전송이다.

설계 원칙 — 이 저장소의 다른 거부권과 같은 보수성
--------------------------------------------------
* **매칭은 좁게, 면제는 넓게.** 놓치면 예전과 같아질 뿐이고(감점 0), 잘못 걸면
  중요한 후속이 죽는다. 비대칭이 이 파일의 전부다.
* 호기가 어긋나면(`_facility_conflict`) 매칭하지 않는다. 운영 콘솔에서 사람이
  갈라 둔 조합(`admin_overrides.merge_blocked`)도 마찬가지다 — 접는 곳 전부에
  거부권이 서야 한다는 기존 계약을 여기서도 지킨다.
* 감점은 **점수 조정**이지 삭제가 아니다. 삭제(hard drop)는 유사도가 아주 높고
  단계가 전혀 안 움직인 조합에만, 설정으로 켜고 끌 수 있게 둔다.
* 이 모듈은 `ranking` 을 import 하지 않는다. 판정 결과를 기사 dict 의
  `continuity` 키로 **주입**하고, `ranking.score_item` 이 그걸 읽어 쓴다 —
  `prior_coverage` 를 news_bot 이 주입하는 것과 같은 방향이다. 랭킹은
  계속 stdlib 만으로 도는 채로 남는다.

가드레일
--------
* stdlib + event_stage + admin_overrides 만. LLM 호출 없음(전부 결정적).
* delivery_log 를 못 읽어도 죽지 않는다 — 빈 목록이면 판정 자체를 안 한다.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import admin_overrides
import event_stage
import story_cluster
import story_fingerprint
import story_identity

ROOT = Path(__file__).parent
DELIVERY_LOG_FILE = ROOT / "delivery_log.jsonl"

KST = timezone(timedelta(hours=9))

DEFAULT_CONFIG = {
    # 며칠 전까지 발송분을 **읽을** 것인가. 여기서 읽은 것에 감점이 자동으로
    # 붙는 게 아니다 — 실제 폭은 아래 *_window_days 가 근거의 세기별로 정한다.
    # 14일인 이유는 제목이 사실상 동일한 재전송을 그때까지 잡기 때문이다
    # (실측: IAEA 성명이 9일·13일 간격으로 같은 제목으로 다시 왔다).
    "lookback_days": 14,
    # 같은 이슈 판정 문턱. cluster_duplicates 의 0.82 보다 **낮다** — 여기서는
    # 기사를 지우는 게 아니라 감점하는 것이고, 이틀에 걸친 같은 이슈는 매체와
    # 각도가 달라 제목이 그만큼 안 닮는다(테라파워 두 건 실측 0.30).
    "title_similarity": 0.62,
    # fingerprint 는 Gemini 가 만든 보조 증거다. 축 2개 이상이 비교 가능할 때만
    # 쓰고, 단독으로 느슨하게 붙이지 않는다(build_data 와 같은 판단).
    "fingerprint_similarity": 0.55,
    "fingerprint_min_axes": 2,
    # 태그·고유명사 보조 매칭 — 제목 표기가 갈린 경우(알마라즈/알마라스)를 잡는다.
    "anchor_min_shared": 2,
    # 근거 교집합 — 오늘 카드가 어제 카드의 근거 기사를 그대로 다시 쓰고 있는가.
    # 위 세 경로가 전부 어휘를 타는 데 반해 이것은 hash 비교라 표기·번역·매체와
    # 무관하다(`story_cluster.evidence_overlap` 주석에 실측이 있다).
    #
    # 두 조건을 **함께** 요구한다. 건수만 보면 진짜 후속을 죽인다 — 실측
    # 2026-08-17→18 테라파워(공급계약 → SK 공조 합의)가 3건을 공유했다. 비율만
    # 보면 근거가 한둘뿐인 작은 story 가 1.0 으로 튄다. 실측에서 갈린 자리는
    # 0.188(후속) 과 0.857(같은 사건) 사이이고, 그 구간이 비어 있다.
    "evidence_confirm_shared": 3,
    "evidence_confirm_ratio": 0.6,
    # 단계가 안 움직인 반복의 감점. 국내 하한 14.0 · 상위권 20~29점 분포에서
    # 5.0 은 '한 계단 밀린다'에 해당한다(지우지 않는다).
    "repeat_penalty": 5.0,
    # 새 수치만 붙은 후속.
    "minor_penalty_ratio": 0.5,
    # 제목이 이만큼 닮았으면 '요약에 새 숫자가 있다' 정도는 진전으로 안 본다.
    # 척도가 실제로 올라간 경우(협의→계약, 심사→승인)는 이 위에서 판정되므로
    # 영향받지 않는다.
    "restatement_similarity": 0.85,
    # 단계가 넘어간 후속. 0 으로 두지 않는 이유: 같은 이슈를 이틀 연속 1번에
    # 세우는 것 자체는 여전히 비용이라, 동점이면 새 이슈가 앞에 서게 한다.
    "progression_penalty": 0.5,

    # ---- 창(window) — 감점이 만액으로 유지되는 날수. 근거가 셀수록 길다 ----
    #
    # 어느 창을 쓰는지는 `penalty_window()` 가 정하고, 창을 넘어선 뒤에는 셋 다
    # 같은 속도로 식는다. 창을 늘릴 때는 반드시 그 창을 쓰는 **경로의 정밀도**를
    # 먼저 확인할 것 — 감점이 오래 갈수록 오탐 한 건의 값이 비싸진다.
    #
    # 같은 이슈인데 단계가 안 움직인 경우. 사용자 기준 '최근 5~7일 강하게'.
    "repeat_window_days": 7,
    # 진전이 있었는지 확인이 안 되는 경우(minor). 모르는 쪽은 짧게 잡는다 —
    # 실측 3~5일 minor 4쌍 중 2쌍(한수원 부지 확정, 헝가리 팍스 3기 정지)이
    # 실제 진전이었다. 이 칸을 7일로 늘리면 그런 후속이 일주일간 눌린다.
    "minor_window_days": 3,
    # 제목이 사실상 동일한 재전송. hard_drop 이 꺼져 있어도 감점만은 여기까지 간다.
    "restatement_window_days": 14,
    # 이름(앵커)만 공유해서 붙은 매칭. 3~14일 구간 정밀도가 5쌍 중 2쌍이라
    # 예전 폭(하루)에 묶어 둔다. 이 값을 올리려면 named_anchors 를 먼저 좁힐 것.
    "anchor_only_window_days": 1,
    # 창을 넘어간 뒤 하루마다 감점의 이만큼이 준다. 0.25 면 창 + 4일에 0 이 되고,
    # 0 이 된 매칭은 판정 자체를 남기지 않는다.
    "penalty_decay_per_day": 0.25,

    # 창 안에서 **세 번째 이상** 오는 이슈에 붙는 추가 감점(두 번째까지는 0).
    # 반복은 횟수가 쌓일수록 '이어지는 이슈'가 아니라 재전송에 가깝다. 단계가
    # 움직인 회차(material)는 세지 않는다 — 진전마다 세면 정상적인 장기 추적이
    # 벌을 받는다.
    "repeat_streak_penalty": 1.0,
    "repeat_streak_max": 2.0,
    # 반복 판정이 서면 tracking 가점을 되돌린다. 추적 가점은 '이슈가 움직였다'는
    # 신호여야 하는데, 안 움직인 반복에도 붙고 있었다.
    "cancel_tracking_bonus": True,
    # 삭제까지 가는 조합 — 사용자 기준 '제목과 내용이 사실상 동일한 재전송은
    # 7~14일까지 선정 제외'. 문턱을 restatement_similarity 와 같은 0.85 로 두는
    # 이유는 그것이 이 파일에서 '사실상 동일'의 정의이기 때문이다(두 개를 따로
    # 두면 한쪽만 올려 놓고 다른 쪽이 걸린 줄 아는 사고가 난다).
    #
    # 0.85 는 실측에서 온 값이다. 발송 이력 365건에서 이 선 위의 쌍은 13개였고
    # 전부 재전송이었다(삼성중공업 MOU 제목 완전 일치, 중국 8기 승인 3회, 미쓰비시
    # 주어 뒤집기, IAEA 성명 3회). 반대로 **다른 사안인데** 제목이 닮은 최악의
    # 경우는 0.667 이었다(NRC 공청회: 환경영향평가 규정 ↔ ALARA 폐지). 그 사이가
    # 비어 있다는 것이 이 문턱의 근거다.
    #
    # 안전장치는 similarity 가 아니라 **progression == none** 이다. 제목에 낱말
    # 하나(승인·착공)만 붙어도 유사도는 0.9 를 넘지만 그건 단계가 움직인 것이라
    # none 이 아니다. 유사도를 아무리 높여도 그 구분은 못 하므로, 이 게이트에서
    # 정말 중요한 조건은 아래 verdict_for 의 none 판정이다.
    #
    # 뒤쪽 구간(8~14일)은 문턱이 따로다. 오래된 것을 지우려면 더 확실해야 한다는
    # 원칙 이전에, 실측이 그렇게 갈렸다 — 그 구간의 진짜 재전송은 제목이 **글자
    # 그대로** 같았고(IAEA 성명 9일·13일, 둘 다 1.0), 애매한 쪽은 0.906 이었다
    # (웨스팅하우스-아멘텀 `협력 계약 체결` → 12일 뒤 `협력 확대 계약`. 요약에
    # 새 체결일이 있어 확대 계약일 수 있는데, 그걸 확인할 방법이 없다 —
    # event_date 는 발송 로그에 0/365 로 비어 있고 큐의 값도 연도가 틀린다).
    # 그래서 그 구간에서는 낱말 하나만 달라도 지우지 않고 감점만 한다.
    "hard_drop": {"enabled": True, "similarity": 0.85, "max_days": 14,
                  "extended_after_days": 7, "extended_similarity": 0.95},
}

# ---- 단계 척도 -----------------------------------------------------------------
#
# `event_stage.py` 의 단계 집합은 "다른 사건인가"를 가르는 용도라 순서가 없다.
# 여기서 필요한 것은 순서다 — MOU 와 본계약은 event_stage 에서 둘 다 `contract`
# 한 칸이지만, 사용자가 "이건 살려야 한다"고 지목한 진전이 정확히 그 안에 있다.
#
# 한국어 패턴은 **공백을 지운 제목**과 부분일치로 본다(event_stage 와 같은 규칙).
# 영어는 단어 경계. 한 기사가 여러 칸에 걸리면 **가장 높은 칸**을 그 기사의
# 위치로 본다 — '협의 끝에 본계약 체결'은 본계약이다.

PROGRESSION_SCALES: dict[str, tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]] = {
    # 사업·계약 진행도
    "deal": (
        ("탐색", ("협의", "논의", "타진", "모색", "검토", "협상중", "의향", "협력",
                  "추진키로", "협력방안", "건설계획", "사업계획", "추진계획",
                  "도입계획", "확대계획", "투자계획", "계획발표", "계획을발표",
                  "로드맵", "구상", "청사진"),
         ("in talks", "exploring", "negotiating", "considering", "plans to",
          "considers")),
        ("비구속합의", ("양해각서", "mou", "업무협약", "loi", "의향서", "기본합의",
                        "협력각서", "포괄협약"),
         ("memorandum of understanding", "letter of intent", "mou signed",
          "cooperation agreement")),
        ("입찰", ("입찰", "입찰공고", "응찰", "제안서제출", "본입찰", "공개경쟁"),
         ("tender", "bid submitted", "request for proposal", "invites bids")),
        ("우선협상", ("우선협상대상자", "우선협상", "낙찰자선정", "가계약", "예비계약",
                      "본입찰통과", "최종후보", "우선공급자"),
         ("preferred bidder", "shortlisted", "provisional agreement",
          "selected as preferred")),
        ("본계약", ("본계약", "정식계약", "최종계약", "공급계약", "계약체결",
                    "계약을체결", "수주", "발주", "낙찰", "ppa체결", "구매계약",
                    "납품계약", "협정체결"),
         ("signs contract", "contract award", "awarded a contract", "wins order",
          "definitive agreement", "power purchase agreement", "signed a contract")),
        ("착공", ("착공", "기공식", "본공사착수", "첫콘크리트", "최초콘크리트",
                  "굴착개시", "부지정지공사"),
         ("starts construction", "begins construction", "first concrete",
          "groundbreaking", "construction start")),
        ("준공", ("준공", "완공", "상업운전", "상업가동", "가동개시", "운전개시",
                  "인도완료", "납품완료"),
         ("commercial operation", "enters service", "commissioned", "delivered")),
    ),
    # 인허가 진행도
    "permit": (
        ("신청", ("신청서", "신청접수", "인허가신청", "허가신청", "승인신청",
                  "인가신청", "신청서제출", "신청서를제출"),
         ("application filed", "files application", "submits application",
          "applies for")),
        ("심사", ("심사", "심의", "검토", "안전성평가", "적합성평가", "예비타당성",
                  "타당성조사", "공청회", "주민설명회", "검증절차"),
         ("under review", "safety review", "regulatory review", "public hearing",
          "screening")),
        # '재가'(裁可)는 넣지 않는다 — '재가동'에 부분일치해 재가동 기사가 전부
        # 인허가 승인으로 잡힌다. event_stage 의 같은 목록에는 남아 있는데,
        # 그쪽은 집합이 커질수록 거부권이 **덜** 발동하는 구조라 안전한 방향이다.
        # 여기서는 반대로 집합이 커지면 진전으로 오판한다.
        ("승인", ("승인", "인가", "허가취득", "허가발급", "허가획득", "의결", "가결",
                  "적합통보", "면허취득", "인증취득", "통과"),
         ("approved", "approval granted", "licence granted", "license granted",
          "authorised", "authorized", "certified", "green light")),
        ("시행", ("시행", "발효", "공포", "고시", "확정고시", "효력발생"),
         ("takes effect", "enters into force", "promulgated")),
    ),
    # 확정도 — '예상'과 '공식 발표'는 같은 사실의 다른 단계다.
    "certainty": (
        ("관측", ("전망", "예상", "관측", "가능성", "될듯", "할듯", "추진할계획",
                  "검토키로", "것으로보인다", "알려졌다"),
         ("expected to", "likely to", "reportedly", "may ", "could ")),
        ("확정", ("발표", "공식화", "확정", "밝혔다", "공표", "공식발표", "확인됐다",
                  "결정", "의결"),
         ("announced", "officially", "confirmed", "unveiled", "declared")),
    ),
}

# 그 척도에서 '실질적 진전'이라 부를 만한 최소 칸. 어제 표식이 아예 없던
# 경우의 판정에 쓴다 (아래 progression() rule 4 참조).
MATERIAL_TIER = {"deal": 4, "permit": 2, "certainty": 1}

# 같은 칸에 머물러도 규모가 커진 것은 진전이다 (사용자 지목:
# "기존 계약 → 계약 규모 확대 또는 신규 품목 추가"). 수치가 함께 새로 붙을 때만
# 인정한다 — '확대'라는 말만으로는 같은 기사의 다른 제목일 뿐이다.
EXPANSION_MARKERS = (
    "규모확대", "추가수주", "추가계약", "추가공급", "추가발주", "증액", "확대체결",
    "물량확대", "물량증가", "신규품목", "품목추가", "범위확대", "연장계약", "추가물량",
    "expands", "additional order", "scope increase", "follow-on order",
)

# 순서로는 못 세지만 **상태가 뒤집힌** 단계. 어제 없던 이것이 오늘 붙으면
# 그 자체가 사건이다(event_stage 의 단계 id 를 그대로 쓴다).
MATERIAL_STAGE_FLIPS = frozenset({
    "approval", "contract", "construction", "completion",
    "restart", "shutdown", "incident", "cancellation",
})

_SPACE_RE = re.compile(r"\s+")
_NORM_STRIP_RE = re.compile(r"\[[^\]]+\]|\([^)]+\)")
_NORM_KEEP_RE = re.compile(r"[^\w가-힣]")
_TOKEN_RE = re.compile(r"[\w가-힣]+")
# 수치 — '규모 확대·신규 품목'을 잡는 약한 신호. ranking._QUANTITY_RE 와 같은 축.
_QUANTITY_RE = re.compile(
    r"\d[\d,.]*\s*(?:기|호기|GW|MW|㎿|kW|억|조|만|%|퍼센트|달러|유로|원|년|개월|주|일)")
# 고유명사 앵커 — 대문자 약어·한글 3자 이상 명사. 표기가 갈린 이름을 붙이려는
# 것이 아니라, 상투어만으로 두 기사가 붙는 것을 막는 데 쓴다.
_ANCHOR_RE = re.compile(r"[A-Z][A-Za-z0-9\-]{1,}|[가-힣]{3,}")

# 이름처럼 생겼지만 이 바닥에서는 상투어인 약어·단위. 공유해도 근거가 아니다.
# (라틴 문자 3자 이상은 기본적으로 이름으로 치므로 여기서 빼 준다.)
_WEAK_ANCHORS = frozenset({
    "smr", "ai", "ess", "bess", "ppa", "mou", "loi", "epc", "lng", "esg",
    "rps", "re100", "cfe", "ceo", "cfo", "ipo", "gw", "mw", "kw", "twh",
    "mwh", "kwh", "iaea", "nrc", "doe", "eu", "us", "usa", "uk", "kr",
    "khnp", "kepco", "smrs", "the", "and", "for", "with", "new",
})

# 매칭 앵커에서 뺄 상투어. 이것만 공유하면 '원자력 뉴스'라는 뜻밖에 없다.
_STOP_ANCHORS = frozenset({
    "원자력", "원전", "에너지", "전력", "정부", "발표", "추진", "확대", "계획",
    "사업", "산업", "기업", "협력", "체결", "공급", "시장", "글로벌", "국내",
    "해외", "한국", "미국", "지원", "투자", "기술", "관련", "위한", "대한",
    "이번", "가능", "예정", "방침", "결정", "논의", "강화", "구축", "도입",
})


def _compact(text: object) -> str:
    return _SPACE_RE.sub("", str(text or "")).lower()


def _spaced(text: object) -> str:
    return _SPACE_RE.sub(" ", str(text or "")).strip().lower()


def _title_of(row: dict) -> str:
    return str(row.get("title_kr") or row.get("title") or "")


def _norm_title(row: dict) -> str:
    """ranking._norm_title 과 같은 정규화 (같은 문턱을 쓰려면 같은 전처리여야 한다)."""
    return _NORM_KEEP_RE.sub("", _NORM_STRIP_RE.sub("", _title_of(row))).lower()


# 포함비율을 근거로 쓰기 위해 짧은 쪽 제목이 최소한 가져야 할 어절 수.
# 실측 발송 이력 365건의 제목은 8~14 어절이라, 4 는 정상 제목을 거르지 않으면서
# '어절 한둘짜리 제목'만 막는다.
_CONTAINMENT_MIN_TOKENS = 4


def _title_tokens(row: dict) -> set[str]:
    """어절 앞 2글자 집합 — 조사·어미 차이 완화 (ranking 과 동일)."""
    return {w[:2].lower() for w in _TOKEN_RE.findall(_title_of(row)) if len(w) >= 2}


def title_similarity(left: dict, right: dict) -> float:
    """0~1 유사도. 문자열 ratio · 토큰 자카드 · 포함비율 중 가장 큰 값.

    `ranking._same_event` 는 bool 만 낸다. 여기서는 수치가 필요하다 —
    hard drop 문턱과 진단 로그가 '얼마나 닮았나'를 말해야 하기 때문이다.
    """
    na, nb = _norm_title(left), _norm_title(right)
    best = 0.0
    if na and nb:
        best = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = _title_tokens(left), _title_tokens(right)
    if ta and tb:
        inter = ta & tb
        if inter:
            best = max(best, len(inter) / len(ta | tb))
            # 포함비율(짧은 쪽이 긴 쪽 안에 다 들어가 있나)은 어제 제목에 낱말이
            # 몇 개 붙은 재탕을 잡는 축이다. 다만 **짧은 쪽이 너무 짧으면** 이
            # 나눗셈이 무너진다: 어절이 하나뿐인 제목은 그 하나만 겹쳐도 1.0 이
            # 나온다. 실측 2026-08-17 — `옛 기사`(토큰 {기사}) 가 `…관련 기사
            # 발표` 와 1.0 으로 붙어 재전송으로 삭제될 뻔했다. 창이 하루였을 때는
            # 거의 드러나지 않았지만 14일이면 조용히 기사를 지운다.
            if min(len(ta), len(tb)) >= _CONTAINMENT_MIN_TOKENS:
                best = max(best, len(inter) / min(len(ta), len(tb)))
    return round(best, 3)


_HANGUL_RE = re.compile(r"[가-힣]")


def subject_titles(row: dict) -> tuple[str, ...]:
    """이 기사의 **주제**를 말하는 제목들. 단계 판정의 입력이다.

    `event_stage.article_stages` 는 title_kr 과 title 을 늘 함께 읽는다. 그쪽에서는
    옳다 — 집합이 커지면 거부권이 덜 발동해 안전한 방향이고, 번역이 떨어뜨린
    표현(restart·shutdown)을 원문에서 주워야 하기 때문이다.

    여기서는 그 규칙을 그대로 쓸 수 없다. 실측 2026-08-17 큐:

        title   : 캐나다, 우라늄 광산 착공…세계 공급 20% 생산
        title_kr: 스페인 알마라즈 원전 수명 2030년까지 연장

    오타가 아니라 **묶음 기사**다(원문 확인: g-enews 기사 본문이 캐나다 우라늄
    광산으로 시작해 중간에 스페인 알마라즈 연장을 다룬다). 큐레이션이 원자력
    꼭지를 골라 카드 제목으로 삼는 것은 이 서비스에서 대체로 옳은 동작이고
    (`[월드 뉴스 브리프]` 를 카드 제목으로 낼 수는 없다), 실제로 발송분 365건 중
    8건이 이 형태인데 여덟 전부 묶음·칼럼 기사였다.

    문제는 그 뒤다 — 두 제목을 합쳐 읽으면 카드가 말하지 않는 단계가 딸려 온다.
    위 기사는 `착공`(캐나다) 때문에 construction 단계로 잡혔고, 그래서 스페인
    연장 반복 보도가 '상태가 뒤집혔다(material)'로 판정돼 감점을 면제받았다.

    그래서 **둘 다 한국어인데 서로 다른 사건을 말하면 title_kr 만** 본다.
    원문이 외국어면 번역 관계이므로 예전처럼 둘 다 본다.
    """
    kr = str(row.get("title_kr") or "").strip()
    orig = str(row.get("title") or "").strip()
    if not kr:
        return (orig,) if orig else ()
    if not orig or orig == kr:
        return (kr,)
    if not _HANGUL_RE.search(orig):
        return (kr, orig)          # 번역 관계 — 원문에만 있는 표현을 놓치지 않는다
    if title_similarity({"title_kr": kr}, {"title_kr": orig}) < 0.30:
        return (kr,)               # 묶음 기사 — 원문 제목은 다른 꼭지를 가리킨다
    return (kr, orig)


def _stages(row: dict) -> frozenset[str]:
    """카드가 말하는 단계. event_stage 의 어휘를 쓰되 입력을 좁힌다."""
    return event_stage.detect_stages(*subject_titles(row))


def _text_of(row: dict) -> str:
    """척도 판정에 쓸 본문. 제목이 주역이고 요약은 보조다.

    event_stage 는 요약을 안 본다(본문이 과거 단계를 언급해 집합이 뭉개지므로).
    여기서는 요약까지 본다 — 반대 방향의 위험이기 때문이다. 척도를 더 많이
    읽으면 후속 기사가 material 로 판정될 확률이 올라가고, material 은 감점을
    면제하는 쪽이다. 놓치는 쪽이 안전한 방향으로 기운다.
    """
    return " ".join(list(subject_titles(row)) + [str(row.get("summary") or "")])


def scale_tier(row: dict, scale: str) -> int:
    """이 기사가 그 척도에서 어느 칸까지 왔나. 표식이 없으면 -1."""
    spec = PROGRESSION_SCALES.get(scale) or ()
    compact = _compact(_text_of(row))
    spaced = _spaced(_text_of(row))
    tier = -1
    for index, (_label, ko, en) in enumerate(spec):
        hit = any(pat in compact for pat in ko)
        if not hit:
            hit = any(re.search(r"\b" + re.escape(pat).replace(r"\ ", " ") + r"\b", spaced)
                      for pat in en)
        if hit:
            tier = index
    return tier


def scale_tiers(row: dict) -> dict[str, int]:
    return {name: scale_tier(row, name) for name in PROGRESSION_SCALES}


def _quantities(row: dict) -> set[str]:
    return {_SPACE_RE.sub("", m) for m in _QUANTITY_RE.findall(_text_of(row))} | {
        _SPACE_RE.sub("", m.group(0)) for m in _QUANTITY_RE.finditer(_text_of(row))}


def progression(prior: dict, candidate: dict, *,
                restatement_similarity: float = 0.85,
                evidence_confirmed: bool = False) -> dict:
    """전일 기사 대비 오늘 기사가 실제로 나아갔는가.

    판정을 세 등급으로 나누는 이유는 **모르는 경우가 실제로 많기 때문**이다.
    어제 기사가 척도에 아무 표식도 안 남겼으면 '어제가 더 앞이었다'와 '어제는
    표현만 없었다'를 가를 방법이 없다. 그런 경우를 material 로 두면 표현만 바꾼
    재탕이 전부 면제되고(실측 알마라즈 연장 ↔ 연장 승인), none 으로 두면 진짜
    진전이 감점된다. 그래서 절반만 인정하는 칸을 둔다.

    Args:
        evidence_confirmed: 두 story 가 근거 기사를 압도적으로 공유한다
            (`story_cluster.evidence_overlap`). 참이면 아래 ④~⑥ 의 **유보를 쓰지
            않는다** — 그 세 규칙은 전부 "모르겠으니 절반만 인정하자"는 뜻인데,
            같은 기사 뭉치를 근거로 들고 있다는 것은 모르는 상태가 아니다.
            ①~③(척도 이동·규모 확대·상태 전환)은 그대로 위에 남는다: 근거가
            겹쳐도 단계가 실제로 넘어갔으면 그것은 진전이다.

    Returns:
        {"verdict": "material"|"minor"|"none", "kind": str, "detail": str}
    """
    prior_tiers = scale_tiers(prior)
    cand_tiers = scale_tiers(candidate)
    prior_stages = _stages(prior)
    cand_stages = _stages(candidate)
    new_numbers = _quantities(candidate) - _quantities(prior)

    # ① 양쪽 다 표식이 있고 칸이 올라갔다 — 가장 강한 신호.
    #    협의→MOU, MOU→본계약, 입찰→우선협상, 심사→승인, 관측→공식발표.
    moved: list[str] = []
    for name, cand in cand_tiers.items():
        before = prior_tiers[name]
        if cand < 0 or before < 0 or cand <= before:
            continue
        labels = PROGRESSION_SCALES[name]
        moved.append(f"{name}:{labels[before][0]}→{labels[cand][0]}")
    if moved:
        return {"verdict": "material", "kind": "scale_advance",
                "detail": " / ".join(moved)}

    # ② 같은 칸인데 규모가 커졌다 — 계약 확대·신규 품목. 수치가 함께 새로
    #    붙었을 때만 인정한다.
    compact = _compact(_text_of(candidate))
    spaced = _spaced(_text_of(candidate))
    if new_numbers and any(m in compact or m in spaced for m in EXPANSION_MARKERS):
        return {"verdict": "material", "kind": "scope_expansion",
                "detail": ", ".join(sorted(new_numbers)[:4])}

    # ③ 상태가 뒤집혔다 — 가동중단→재가동, 심사중→취소. event_stage 의 판정을
    #    그대로 쓰되, **어제도 단계를 말했을 때만** 본다(stage_conflict 와 같은
    #    보수성). 어제가 침묵했다면 '넘어갔다'가 아니라 '못 읽었다'이다.
    if prior_stages:
        flips = (cand_stages - prior_stages) & MATERIAL_STAGE_FLIPS
        if flips:
            return {"verdict": "material", "kind": "stage_flip",
                    "detail": event_stage.describe(flips)}

    # ── 여기부터는 전부 '모르겠다'에 대한 유보다. 근거가 겹치면 쓰지 않는다. ──
    #
    # 실측 2026-08-22 국회 본회의. 어제 요약은 "…민생법안이 **통과**됨", 오늘
    # 요약은 "…안건을 **의결**했다". `PROGRESSION_SCALES` 에서 `의결` 은
    # `certainty:확정` 칸에 있고 `통과` 는 `permit:승인` 칸에만 있어서, 어제
    # certainty 가 -1 · 오늘이 1 로 갈렸고 아래 ④ 가 걸려 minor(감점 절반)가 됐다.
    # **같은 사실을 두 매체가 다른 동사로 썼다는 것 말고는 아무 일도 없었다.**
    #
    # ④~⑥ 이 약한 것은 결함이 아니라 설계다(위 docstring). 문제는 그 유보를
    # 걷어 줄 재료가 그때까지 없었다는 것이다. 근거 교집합이 그 재료다.
    if evidence_confirmed:
        return {"verdict": "none", "kind": "", "detail": ""}

    # ④ 어제는 척도에 표식이 없었는데 오늘은 실질 단계에 도달했다. 진전일
    #    가능성이 높지만 같은 사실을 다른 낱말로 쓴 것일 수도 있다 — 절반.
    reached = [f"{name}:{PROGRESSION_SCALES[name][tier][0]}"
               for name, tier in cand_tiers.items()
               if prior_tiers[name] < 0 and tier >= MATERIAL_TIER[name]]
    if reached:
        return {"verdict": "minor", "kind": "unmarked_prior",
                "detail": " / ".join(reached)}

    # 제목이 사실상 같으면 아래 약한 신호는 보지 않는다.
    #
    # ⑤·⑥ 은 '요약 어딘가에 없던 숫자가 있다' 정도의 근거다. 제목이 한 낱말만
    # 다른 재탕에서도 그 정도는 늘 나온다 — 실측 2026-08-17:
    #   어제 `두산에너빌리티, 美 테라파워 SMR 핵심 기자재 공급 계약 체결`
    #   오늘 `두산에너빌리티, 美 테라파워 차세대 SMR 핵심 기자재 공급 계약 체결`
    # 포함 유사도 1.0 인데 요약의 숫자 하나로 minor 를 받아 감점이 절반이 됐다.
    # 척도가 실제로 올라간 ①~④ 는 이 게이트 위에 있으므로 영향받지 않는다.
    if title_similarity(candidate, prior) < restatement_similarity:
        # ⑤ 새 수치만 붙었다.
        if new_numbers:
            return {"verdict": "minor", "kind": "new_quantities",
                    "detail": ", ".join(sorted(new_numbers)[:4])}

        # ⑥ 새 단계 표식이 붙었다(실질 단계는 아님).
        if prior_stages and (cand_stages - prior_stages):
            return {"verdict": "minor", "kind": "stage_added",
                    "detail": event_stage.describe(cand_stages - prior_stages)}

    return {"verdict": "none", "kind": "", "detail": ""}


# ---- 같은 이슈 판정 -------------------------------------------------------------

def fingerprint_similarity(left: dict, right: dict) -> tuple[float, int, list[str]]:
    """(유사도, 비교한 축 수, 겹친 축). 축 표는 `story_fingerprint` 하나뿐이다.

    예전에는 이 파일과 `web/build_data` 가 표를 하나씩 들고 "같은 축·같은
    가중치"라고 적어 두었는데, 실제로는 어긋나 있었다(저쪽만 `drivers` 를
    빠뜨렸다). 주석으로 맞추는 것은 안 맞는다 — 표를 하나로 옮겼다.
    """
    comparison = story_fingerprint.compare(
        left.get("story_fingerprint"), right.get("story_fingerprint")
    )
    return round(comparison.similarity, 3), comparison.compared, comparison.shared


def _facilities(row: dict) -> frozenset[str]:
    """제목이 지목한 호기. ranking._title_facilities 와 같은 어휘."""
    plants = "신고리|신월성|신한울|새울|고리|월성|한빛|한울|영광|울진"
    pattern = re.compile(rf"({plants})\s*([0-9][0-9,·~\-\s]*)\s*호기")
    out: set[str] = set()
    for plant, nums in pattern.findall(_title_of(row)):
        out.update(f"{plant}{n}" for n in re.findall(r"[0-9]+", nums))
    return frozenset(out)


def _fingerprint_country_conflict(candidate: dict, prior: dict) -> bool:
    """두 지문의 countries 축이 서로 완전히 다르면 다른 사건으로 본다.

    `countries` 는 story_fingerprint 의 SCOPE 축이라 `compare()` 의 identity 판정에는
    안 들어간다(같은 나라를 공유해도 같은 사건의 근거가 못 된다) — 하지만 그 반대
    방향, **나라가 아예 하나도 안 겹친다**는 것은 반대로 강한 신호다. 호기 충돌
    (`_facilities`)과 같은 자리에 세우는 이유도 같다.

    실측 2026-08-31 사고: 프랑스 볼탈리아의 브라질 데이터센터 승인 건
    (countries=France·Brazil)과 미국 데이터센터 지연 기사가 `데이터센터`·`전력수요`
    라는 업계 공통 태그만 공유해 앵커 경로로 붙었고, 그 매칭이 `story_id` 를
    물려주는 근거가 됐다(뒤 `same_issue`/`annotate` 수정과 별개로, 지문에 나라가
    있었다면 애초에 여기서 막혔을 조합이다).
    """
    left = candidate.get("story_fingerprint")
    right = prior.get("story_fingerprint")
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_countries = story_fingerprint.axis_values(left, "countries")
    right_countries = story_fingerprint.axis_values(right, "countries")
    return bool(left_countries and right_countries
                and left_countries.isdisjoint(right_countries))


def anchors_of(row: dict) -> frozenset[str]:
    """이 기사를 특정하는 말의 후보 — 제목·태그·fingerprint 의 행위자/자산.

    표기 흔들림('알마라즈'/'알마라스')까지 잡으려는 것이 아니다. 제목 유사도가
    닿지 않는 곳에서 **같은 당사자가 같은 사안을 이어 간다**는 사실을 잡는 데
    쓴다 — 실측 테라파워 두 건의 제목 유사도는 0.364 로 어떤 문턱에도 못 미치는데
    양쪽 다 'TerraPower' 와 '두산에너빌리티'를 말하고 있었다.
    """
    text = " ".join(str(row.get(k) or "") for k in ("title_kr", "title"))
    words = {w.lower() for w in _ANCHOR_RE.findall(text)}
    tags = {str(t).lstrip("#").strip().lower()
            for t in (row.get("tags") or []) if str(t).strip()}
    fingerprint = row.get("story_fingerprint")
    if isinstance(fingerprint, dict):
        for key in ("actors", "assets"):
            words |= {str(v).strip().lower()
                      for v in (fingerprint.get(key) or []) if str(v).strip()}
    return frozenset((words | tags) - _STOP_ANCHORS)


def named_anchors(row: dict) -> frozenset[str]:
    """앵커 중 **이름에 가까운** 것. 공유했을 때 같은 이슈의 근거가 되는 쪽.

    실측 2026-08-17 큐(197건)에서 앵커만으로 붙은 조합을 전부 읽어 보고 정했다.
    잘못 붙은 것들의 공통점은 공유한 말이 이름이 아니라 **보통명사나 어절 꼬리**
    였다는 것이다 — `차세대`(빌 게이츠 방한 ↔ 정부 태양광 육성),
    `시장서`(엔비디아 ↔ LS일렉트릭), `본격화`(SK이노베이션 ↔ KINS 심포지엄),
    `공급망`(맥킨지 칼럼 ↔ 테라파워 협력). 반대로 제대로 붙은 것들은
    `테라파워`·`두산에너빌리티`·`다뉴브강`·`KINS`·`SEED` 처럼 이름이었다.

    이름으로 인정하는 것:
      · 큐레이션이 뽑아 준 것 — fingerprint 의 actors/assets, 태그
      · 라틴 문자 3자 이상 (TerraPower·KINS·PJM·BESS) — 단 도메인 상투 약어 제외
      · 한글 4자 이상 (테라파워·다뉴브강·이자비용) — 3자는 대부분 보통명사다
    """
    named: set[str] = set()
    fingerprint = row.get("story_fingerprint")
    if isinstance(fingerprint, dict):
        for key in ("actors", "assets"):
            named |= {str(v).strip().lower()
                      for v in (fingerprint.get(key) or []) if str(v).strip()}
    named |= {str(t).lstrip("#").strip().lower()
              for t in (row.get("tags") or []) if str(t).strip()}
    for anchor in anchors_of(row):
        if anchor in _WEAK_ANCHORS:
            continue
        if anchor.isascii():
            if len(anchor) >= 3:
                named.add(anchor)
        elif len(anchor) >= 4:
            named.add(anchor)
    return frozenset(named - _WEAK_ANCHORS - _STOP_ANCHORS)


# `풀네임(약어)` 표기. 이름 옆의 괄호는 이 바닥에서 거의 예외 없이 별칭이다 —
# 원자력안전위원회(NSSC) · 뉴클레오(Newcleo) · 아이다호 국립연구소(INL).
# 괄호 **앞의 두 어절까지**를 같은 대상으로 본다(기관명이 '수식어 + 본체' 형태다).
# 쉼표를 못 넘으므로 `ARC, 아이다호 국립연구소(INL)` 에서 ARC 는 섞이지 않는다.
_ALIAS_RE = re.compile(
    r"([\w가-힣]+(?:[ \t]+[\w가-힣]+)?)[ \t]*\(\s*([A-Za-z][\w.\-]*(?:[ \t]+[\w.\-]+)?)\s*\)")


def alias_groups(row: dict) -> tuple[frozenset[str], ...]:
    """제목의 `풀네임(약어)` 표기에서 **같은 대상임이 확정된** 말 묶음.

    앵커 경로는 '이름 둘'을 요구하는데, 그 둘이 같은 것의 두 표기면 실제로는
    하나만 공유한 것이다. 실측 2026-08-17 발송 이력에서 3~14일 간격 앵커 매칭
    5쌍 중 3쌍이 정확히 이 형태였다:

        anchors:newcleo,뉴클레오              ← `뉴클레오(Newcleo)` 한 회사
        anchors:nssc,원자력안전위원회          ← `원자력안전위원회(NSSC)` 한 기관
        anchors:국립연구소,아이다호            ← `아이다호 국립연구소(INL)` 한 곳

    셋 다 사안은 완전히 달랐다(미국 인허가 ↔ 프랑스 MOX, 정기검사 ↔ 입법예고,
    자금 선정 ↔ 배치 협력). 창을 넓히기 전에 이걸 먼저 막아야 한다 — 감점이
    오래 갈수록 오탐 한 건의 값이 비싸진다.

    거꾸로 붙어 있던 것을 떼지는 않는다: `두산에너빌리티`+`테라파워` 처럼 서로
    다른 두 회사는 괄호로 묶일 일이 없으므로 그대로 둘로 센다.
    """
    groups: list[frozenset[str]] = []
    for text in (str(row.get("title_kr") or ""), str(row.get("title") or "")):
        for full, alias in _ALIAS_RE.findall(text):
            names = {w.lower() for w in _TOKEN_RE.findall(full) if len(w) >= 2}
            names |= {w.lower() for w in _TOKEN_RE.findall(alias) if len(w) >= 2}
            if len(names) >= 2:
                groups.append(frozenset(names))
    return tuple(groups)


def distinct_names(shared: frozenset[str],
                   groups: tuple[frozenset[str], ...]) -> int:
    """공유한 이름이 **몇 개의 대상**을 가리키는가. 별칭 묶음은 하나로 센다."""
    remaining = set(shared)
    merged = 0
    for group in groups:
        overlap = remaining & group
        if len(overlap) >= 2:      # 한 대상의 여러 표기를 함께 공유한 것
            remaining -= overlap
            merged += 1
    return merged + len(remaining)


def generic_anchors(rows: list[dict], *, ratio: float = 0.15,
                    minimum: int = 6) -> frozenset[str]:
    """비교 풀에서 **압도적으로 흔한** 앵커. 이름처럼 생겼어도 근거가 못 된다.

    `_WEAK_ANCHORS` 가 도메인 상투어의 바닥을 잡고, 이것이 그날의 유행어를 잡는다
    (예: 정부 발표 하나로 같은 사업명이 30건에 들어간 날). 문턱을 높게 두는 이유는
    반대 사고 때문이다 — 한 이슈가 하루를 지배하면 **정작 그 이슈의 이름**이
    흔해져서, 게이트가 가장 필요한 날에 꺼진다. 실측 2026-08-17 에 문턱 10% 로
    두었더니 '테라파워'가 흔한 말로 분류돼 테라파워 반복이 통과했다(그날 후보
    267건 중 25건, 9.4%). 15%는 그 위에 있고, 진짜 유행어(그날 '데이터센터'는
    40건 이상)는 그 아래에 안 남는다.
    """
    if not rows:
        return frozenset()
    counts: dict[str, int] = {}
    for row in rows:
        for anchor in anchors_of(row):
            counts[anchor] = counts.get(anchor, 0) + 1
    threshold = max(minimum, int(len(rows) * ratio))
    return frozenset(a for a, n in counts.items() if n > threshold)


def same_issue(candidate: dict, prior: dict, cfg: dict,
               generic: frozenset[str] = frozenset()) -> dict | None:
    """같은 이슈면 매칭 근거를, 아니면 None.

    거부권이 먼저다 — 호기 충돌과 운영 콘솔의 사람 판정은 유사도보다 위에 선다.
    """
    fac_a, fac_b = _facilities(candidate), _facilities(prior)
    if fac_a and fac_b and not (fac_a & fac_b):
        return None
    if _fingerprint_country_conflict(candidate, prior):
        return None
    if admin_overrides.merge_blocked(candidate, prior):
        return None

    candidate_story_id = str(candidate.get("story_id") or "")
    prior_story_id = str(prior.get("story_id") or "")
    # A shared legacy value is not evidence.  Direct ID confirmation is available only
    # for identities already issued by the v2 authority.
    same_story_id_value = bool(candidate_story_id and prior_story_id
                               and candidate_story_id == prior_story_id)
    direct_story_id = story_identity.trusted_same_id(candidate, prior)

    sim = title_similarity(candidate, prior)
    fp_sim, fp_axes, fp_shared = fingerprint_similarity(candidate, prior)
    named = (named_anchors(candidate) & named_anchors(prior)) - generic
    overlap = story_cluster.evidence_overlap(candidate, prior)
    fp_comparison = story_fingerprint.compare(
        candidate.get("story_fingerprint"), prior.get("story_fingerprint"))
    shared_identity_axes = sorted(
        set(fp_comparison.shared) & set(story_fingerprint.IDENTITY_AXES))
    contested_identity_axes = sorted(
        set(fp_comparison.contested) & set(story_fingerprint.IDENTITY_AXES))
    named_count = distinct_names(named, alias_groups(candidate) + alias_groups(prior))
    # This is the bridge from legacy history to a stable id.  It is deliberately stronger than
    # the ordinary fingerprint penalty path: two concrete identity axes, two distinct named
    # actors, and no contradictory identity axis.  Country/event-family alone can never qualify.
    fingerprint_identity = bool(
        len(shared_identity_axes) >= 2
        and named_count >= int(cfg.get("anchor_min_shared", 2))
        and not contested_identity_axes)
    # 확정 강도일 때만 매칭 근거로 센다. 그 아래는 아예 참여시키지 않는다 —
    # 약하게 걸어 두면 정상 후속(테라파워 3건 공유)이 `anchor_only` 를 잃고
    # 감점 창이 하루에서 이레로 늘어난다. 실측 근거가 있는 구간에서만 움직인다.
    evidence_confirmed = bool(
        overlap.shared >= int(cfg.get("evidence_confirm_shared", 3))
        and overlap.ratio >= float(cfg.get("evidence_confirm_ratio", 0.6)))

    reasons: list[str] = []
    if same_story_id_value:
        reasons.append(f"story_id:{candidate_story_id}")
    if evidence_confirmed:
        reasons.append(f"evidence:{overlap.shared}/{overlap.candidate_total}")
    if sim >= float(cfg.get("title_similarity", 0.62)):
        reasons.append(f"title:{sim}")
    if (fp_axes >= int(cfg.get("fingerprint_min_axes", 2))
            and fp_sim >= float(cfg.get("fingerprint_similarity", 0.55))
            and shared_identity_axes
            and not contested_identity_axes):
        reasons.append(f"fingerprint:{fp_sim}({'+'.join(fp_shared)})")
    # 앵커 경로는 **이름 둘**을 요구한다. 하나로는 못 가른다 — 실측 2026-08-17
    # 큐에서 이름 하나만 요구했을 때 붙은 196쌍을 전부 읽어 보면, 맞은 쌍
    # (`두산에너빌리티`+`테라파워`, `다뉴브강`+`루마니아`)은 둘 이상을 공유했고
    # 틀린 쌍은 예외 없이 하나였다(`데이터센터` 하나로 25쌍, `게이츠와` 하나로 4쌍).
    # 제목 유사도로는 안 갈린다: 맞은 쌍과 틀린 쌍이 0.41~0.58 구간에 섞여 있다.
    # 세는 것은 낱말이 아니라 **대상**이다 — `원자력안전위원회(NSSC)` 는 하나다.
    if named_count >= int(cfg.get("anchor_min_shared", 2)):
        reasons.append(f"anchors:{','.join(sorted(named)[:3])}")
    if not reasons:
        return None
    # 어느 경로로 붙었는지는 감점의 **폭**을 정한다(penalty_window). 앵커 단독은
    # 간격이 벌어지면 정밀도가 떨어지므로 창을 좁게 쓴다.
    anchor_only = all(r.startswith("anchors:") for r in reasons)
    if fac_a and fac_b and (fac_a & fac_b):
        reasons.append(f"facility:{','.join(sorted(fac_a & fac_b))}")

    # `direct_story_id` 는 "후보가 이미 이 story_id 를 들고 있다"는 사실일 뿐,
    # 그 값이 어떻게 붙었는지는 모른다 — 예전 회차의 약한 매칭이었을 수도 있다
    # (2026-08-31 사고: anchors 매칭 하나가 story_id 를 물려주고, 다음 판정에서
    # 그 id 동일성 자체가 "identity_confirmed" 로 자기강화됐다). 그래서 신원
    # 축이 어긋나 있으면(`contested_identity_axes`) 이미 같은 id 를 들고 있어도
    # 확정으로 세지 않는다 — 자기강화 고리를 여기서 끊는다.
    identity_confirmed = bool(
        (direct_story_id and not contested_identity_axes) or fingerprint_identity)
    identity_method = ""
    if direct_story_id and not contested_identity_axes:
        identity_method = "story_id"
    elif fingerprint_identity:
        identity_method = "fingerprint_anchors"
    # story_id **상속**은 identity_confirmed 보다 좁힐 이유가 없다 — evidence_confirmed
    # 도 어휘와 무관한 별도의 강한 근거(같은 근거 기사 뭉치)라 동급으로 본다. 단순
    # 제목 유사도·progression 판정·일반 앵커(anchor_only)만으로는 여기 못 닿는다:
    # 그 경로들은 reasons 에는 들어가도 identity_confirmed 도 evidence_confirmed 도
    # 세우지 못한다.
    story_id_inheritable = bool(identity_confirmed or evidence_confirmed)
    return {"similarity": sim, "fingerprint_similarity": fp_sim,
            "reasons": reasons, "anchor_only": anchor_only,
            "evidence_shared": overlap.shared,
            "evidence_ratio": overlap.ratio,
            "evidence_confirmed": evidence_confirmed,
            "identity_confirmed": identity_confirmed,
            "identity_method": identity_method,
            "story_id": prior_story_id or story_cluster.fallback_story_id(prior),
            "story_id_inheritable": story_id_inheritable,
            "fingerprint_identity_shared": shared_identity_axes,
            "fingerprint_identity_contested": contested_identity_axes}


# ---- 발송 이력 ------------------------------------------------------------------

def load_recent_sent(days: int = 14, *, path: Path | None = None,
                     today: date | None = None) -> list[dict]:
    """최근 발송분(= delivery_log 의 기사 레코드). 못 읽으면 빈 목록.

    아카이브가 아니라 **발송 이력**이다. 그 차이가 이 모듈의 전제다 —
    '언론이 얼마나 썼나'(prior_coverage)와 '우리가 이미 보냈나'는 다른 질문이다.
    """
    path = path or DELIVERY_LOG_FILE
    today = today or datetime.now(KST).date()
    cutoff = (today - timedelta(days=max(0, int(days)))).isoformat()
    rows: list[dict] = []
    registry_rows: list[dict] = []
    registry_cutoff = (today - timedelta(days=max(30, int(days)))).isoformat()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("record_type"):
            continue
        stamp = str(row.get("date") or "")
        if not stamp:
            continue
        story_identity.mark_persistent(row)
        if stamp >= registry_cutoff:
            registry_rows.append(row)
        if stamp >= cutoff:
            rows.append(row)
    registry_audit = story_identity.audit_registry(registry_rows)
    conflicted_ids = {entry["story_id"] for entry in registry_audit["conflicts"]}
    for row in rows:
        if row.get("story_id") in conflicted_ids:
            row["story_registry_conflicted"] = True
    return rows


def as_sent_record(item: dict, briefing_date: str) -> dict:
    """오늘 이미 자리를 잡은 선정 결과를 '발송분'으로 본다.

    국내와 해외는 각자 풀에서 따로 랭킹된다. 그래서 같은 이슈가 국내 1번과 해외
    3번을 동시에 차지하는 일이 실제로 있었다(2026-08-16 테라파워). 어제와 대조하는
    바로 그 장치로 **같은 날 다른 지역**도 대조한다 — 새 규칙을 만들 이유가 없다.
    """
    story_id = story_cluster.ensure_story_id(item)
    return {
        "date": briefing_date,
        "hash": item.get("hash", ""),
        "title_kr": item.get("title_kr") or item.get("title") or "",
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
        "tags": item.get("tags") or [],
        "region": item.get("region") or "",
        "story_fingerprint": item.get("story_fingerprint") or {},
        # 근거 교집합이 같은 날 다른 지역에도 서야 한다. 국내 1번과 해외 3번이
        # 같은 사건이었던 2026-08-16 테라파워가 이 레코드로 잡혔는데, 근거를
        # 안 실으면 그 경로만 눈이 없다.
        "story_members": item.get("story_members") or [],
        "story_article_hashes": item.get("story_article_hashes") or [],
        "story_id": story_id,
        "story_id_source": item.get("story_id_source") or "generated",
        "story_id_trust": item.get("story_id_trust") or "legacy",
        "story_identity_version": int(item.get("story_identity_version") or 0),
    }


# ---- 판정 ----------------------------------------------------------------------

def resolve_config(cfg: dict | None) -> dict:
    """ranking_config 의 continuity 절을 편다. 없거나 깨지면 내장 기본값."""
    merged = dict(DEFAULT_CONFIG)
    raw = (cfg or {}).get("continuity")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key.startswith("_"):
                continue
            if key == "hard_drop" and isinstance(value, dict):
                merged["hard_drop"] = {**DEFAULT_CONFIG["hard_drop"], **value}
            else:
                merged[key] = value
    return merged


def _days_between(later: str, earlier: str) -> int:
    try:
        return max(0, (date.fromisoformat(later[:10]) - date.fromisoformat(earlier[:10])).days)
    except (TypeError, ValueError):
        return 0


def penalty_window(verdict: str, similarity: float, anchor_only: bool,
                   cfg: dict, *, evidence_confirmed: bool = False) -> int:
    """감점이 **만액으로 유지되는** 날수. 근거가 셀수록 길다.

    사다리의 순서가 곧 판단이다. 위에서 걸리는 것이 이긴다.

    ① material — 창 0. 단계가 실제로 넘어갔으면 기간과 무관하게 다시 나간다.
       (감점이 0.5 에서 시작해 나흘이면 사라지므로, 사흘 안에 두 번 올라오는
        경우의 동점 처리만 남는다.)
    ② 앵커 단독 — 창 1일. 이름만 공유해서 붙은 매칭은 간격이 벌어질수록 오탐이
       된다(실측 3~14일 5쌍 중 3쌍). 여기만은 예전 폭 그대로 둔다.
    ③ minor — 창 3일. '진전이 있었는지 모르겠다'는 짧게 잡는다.
    ④ 제목이 사실상 동일 — 창 14일. 재전송은 시간이 지나도 재전송이다.
    ⑤ 나머지(같은 이슈 · 진전 없음) — 창 7일. 사용자 기준의 '최근 5~7일'.

    ②가 ③④보다 위에 있는 이유: 경로의 정밀도는 판정의 세기보다 먼저다. 이름만
    겹친 두 기사는 애초에 같은 이슈인지가 의심스러우므로, 그 위에서 내린 단계
    판정을 근거로 창을 넓히면 오탐을 오래 끌고 간다. ④가 ③보다 아래인 이유는
    반대다 — minor 는 '단계가 올라갔을 수도 있다'는 뜻이고, 제목이 닮았다는
    사실만으로 그 가능성을 덮으면 제목 끝에 낱말 하나 붙은 진짜 진전
    (`…계속운전` → `…계속운전 승인`)이 2주간 눌린다.
    """
    if verdict == "material":
        return 0
    if anchor_only:
        return int(cfg.get("anchor_only_window_days", 1))
    if verdict == "minor":
        return int(cfg.get("minor_window_days", 3))
    # 근거를 압도적으로 공유한 반복은 제목이 안 닮았어도 재전송과 같은 세기다 —
    # 같은 기사 뭉치를 근거로 다시 쓴 것이라 어휘가 달라진 것뿐이다. 그래서 ④와
    # 같은 창을 준다(hard_drop 이 꺼져 있어도 감점만은 여기까지 간다).
    if evidence_confirmed or similarity >= float(cfg.get("restatement_similarity", 0.85)):
        return int(cfg.get("restatement_window_days", 14))
    return int(cfg.get("repeat_window_days", 7))


def verdict_for(candidate: dict, recent: list[dict], cfg: dict,
                today: str, generic: frozenset[str] = frozenset()) -> dict | None:
    """후보 1건의 연속일 판정. 매칭 없으면 None.

    같은 이슈가 여러 건 걸리면 **가장 최근** 발송분과 비교한다. 사용자가 보는
    반복은 '이틀 전에도 있었나'가 아니라 '어제 봤나'이고, 단계 판정도 직전
    회차 대비여야 뜻이 있다(사흘 전 '심사' 대비가 아니라 어제 '승인' 대비).

    나머지 매칭은 버리지 않고 **횟수**로 센다 — 세 번째부터가 재전송이다.

    창이 14일이 되면서 '더 닮은 옛 발송분이 덜 닮은 최근 매칭에 가려진다'가
    이론상 가능해졌다(그러면 삭제 대상이 감점으로 내려앉는다). 발송 이력 34일을
    전수 검사해 0건이라 그대로 둔다 — 가려져도 감점은 최근 매칭 몫으로 그대로
    서므로, 잃는 것은 삭제뿐이고 그 방향이 이 파일의 보수성과 같다.
    """
    best: tuple[str, dict, dict] | None = None
    matches: list[tuple[str, dict, dict]] = []
    for prior in recent:
        if prior.get("hash") and prior.get("hash") == candidate.get("hash"):
            continue
        match = same_issue(candidate, prior, cfg, generic)
        if not match:
            continue
        stamp = str(prior.get("date") or "")
        matches.append((stamp, prior, match))
        if best is None or stamp > str(best[1].get("date") or ""):
            best = (stamp, prior, match)
    if best is None:
        return None

    stamp, prior, match = best
    days_ago = _days_between(today, stamp)
    evidence_confirmed = bool(match.get("evidence_confirmed"))
    prog = progression(prior, candidate,
                       restatement_similarity=float(
                           cfg.get("restatement_similarity", 0.85)),
                       evidence_confirmed=evidence_confirmed)

    ratio = {"material": float(cfg.get("progression_penalty", 0.5))
             / max(1e-9, float(cfg.get("repeat_penalty", 5.0))),
             "minor": float(cfg.get("minor_penalty_ratio", 0.5)),
             "none": 1.0}[prog["verdict"]]
    base = float(cfg.get("repeat_penalty", 5.0))
    window = penalty_window(prog["verdict"], match["similarity"],
                            bool(match.get("anchor_only")), cfg,
                            evidence_confirmed=evidence_confirmed)
    # 창 안은 만액, 창 밖은 하루마다 감쇠. 예전에는 창이 없어 발송 당일부터
    # 식었고, 그래서 3~4일 전 반복이 이미 40% 로 통과했다.
    decay = max(0.0, 1.0 - float(cfg.get("penalty_decay_per_day", 0.25))
                * max(0, days_ago - window))

    # 최근 이 이슈를 몇 번 보냈나. 세는 창은 경로·판정과 무관하게 하나로 둔다
    # ('최근 일주일에 몇 번'이 사람이 세는 방식이고, 경로별 창으로 세면 같은
    # 반복이 매칭 경로에 따라 다른 횟수로 세어진다). 같은 날 국내·해외에 함께
    # 나갈 수 있으므로 날짜가 아니라 발송 건수로 센다.
    count_window = int(cfg.get("repeat_window_days", 7))
    streak = sum(1 for s, _p, _m in matches
                 if _days_between(today, s) <= count_window)
    extra = 0.0
    if prog["verdict"] == "none" and streak >= 2:
        extra = min(float(cfg.get("repeat_streak_max", 2.0)),
                    float(cfg.get("repeat_streak_penalty", 1.0)) * (streak - 1))

    penalty = round((base * ratio + extra) * decay, 3)
    # 감쇠가 0 에 닿았으면 그 매칭은 만료다 — 판정을 남기지 않는다.
    #
    # 남기면 감점은 0 인데 tracking 취소만 살아남아, '5일 전에 비슷한 게 있었다'는
    # 이유로 오늘 기사가 조용히 1.5점을 잃는다. 실측 2026-08-17 큐에서 이 상태가
    # 8건이었다. lookback_days 를 늘리려면 창이나 감쇠를 함께 봐야 한다는 사실도
    # 이 분기가 드러내 준다.
    if penalty <= 0:
        return None

    hard = cfg.get("hard_drop") or {}
    # 오래된 것을 지울수록 '사실상 동일'의 기준이 올라간다 (DEFAULT_CONFIG 주석).
    need = float(hard.get("similarity", 0.85))
    if days_ago > int(hard.get("extended_after_days", 7)):
        need = max(need, float(hard.get("extended_similarity", 0.95)))
    # '사실상 동일'을 말하는 길이 둘이다 — 제목이 같거나, 근거가 같거나.
    #
    # 예전에는 제목뿐이었고 그것이 이 게이트의 구멍이었다: 다른 매체가 다른
    # 표현으로 쓴 동일 사건은 **정의상 0.85 를 못 넘는다.** 실측 2026-08-22
    # 국회 본회의 두 건이 0.776 이라, 진전 판정을 옳게 고쳐 none 이 나왔더라도
    # 감점 5.0(→22.94점)에 그쳐 국내 하한 14.0 위에 그대로 남았다(그날 최하
    # 선정 점수 20.31). 어휘를 안 타는 두 번째 길이 필요한 이유다.
    #
    # `progression == "none"` 은 그대로 **필수**다. 근거가 겹쳐도 단계가 넘어갔으면
    # 위 ①~③ 이 material 을 내고 여기 오지 않는다.
    drop = bool(
        hard.get("enabled")
        and prog["verdict"] == "none"
        and (evidence_confirmed or match.get("identity_confirmed")
             or match["similarity"] >= need)
        and days_ago <= int(hard.get("max_days", 14))
    )

    return {
        "matched": True,
        "prior_hash": str(prior.get("hash") or ""),
        "prior_title": str(prior.get("title_kr") or prior.get("title") or "")[:120],
        "prior_date": stamp,
        "prior_region": str(prior.get("region") or ""),
        "days_ago": days_ago,
        "similarity": match["similarity"],
        "evidence_shared": match.get("evidence_shared", 0),
        "evidence_ratio": match.get("evidence_ratio", 0.0),
        # 겹친 건수와 **문턱을 넘었는가**는 다른 사실이다. 근거가 한 건 겹치는
        # 일은 흔하고(실측 8/22 후보 풀에서 5건 중 4건이 1~2건 겹침) 그 넷은
        # 판정에 아무 영향이 없었다. 이 플래그가 없으면 진단이 둘을 못 가르고,
        # 문턱을 읽으라고 안내한 숫자가 정작 문턱과 무관해진다.
        "evidence_confirmed": bool(match.get("evidence_confirmed")),
        "identity_confirmed": bool(match.get("identity_confirmed")),
        "identity_method": str(match.get("identity_method") or ""),
        "story_id": str(match.get("story_id") or ""),
        "story_id_inheritable": bool(match.get("story_id_inheritable")),
        "fingerprint_identity_contested": match.get(
            "fingerprint_identity_contested") or [],
        "match_reasons": match["reasons"],
        "progression": prog["verdict"],
        "progression_kind": prog["kind"],
        "progression_detail": prog["detail"][:160],
        # 창과 횟수는 진단에 남긴다 — "왜 이만큼 깎였나"를 사후에 재현하려면
        # 감점 숫자만으로는 부족하다(같은 5.0 이 창 안 만액일 수도, 창 밖
        # 감쇠 뒤의 값일 수도 있다).
        "window_days": window,
        "repeat_streak": streak,
        "penalty": penalty,
        "score_delta": round(-penalty, 3),
        # 추적 가점은 '이 이슈가 다시 움직였다'는 신호여야 한다. 안 움직였거나
        # (none) 움직였는지 확인이 안 되는(minor) 후속에 붙어 있으면 감점과
        # 가점이 상쇄돼 아무 일도 일어나지 않는다. material 에는 그대로 둔다 —
        # 거기서는 실제로 움직인 것이 확인됐다.
        "cancel_tracking": bool(cfg.get("cancel_tracking_bonus", True)
                                and prog["verdict"] in ("none", "minor")),
        "drop": drop,
    }


def annotate(items: list[dict], recent: list[dict], cfg: dict | None = None,
             today: str | None = None,
             generic: frozenset[str] | None = None) -> dict:
    """후보들에 `continuity` 판정을 붙인다 (제자리 수정). 요약 진단을 돌려준다.

    `ranking.score_item` 이 이 키를 읽어 점수에 반영하고, `rank_and_select` 가
    `drop` 을 보고 후보에서 뺀다. 이 방향으로 짠 이유: 랭킹 모듈은 stdlib 만
    쓰는 채로 남아야 하고(테스트가 네트워크·파일 없이 돈다), 판정 재료는
    delivery_log 라는 외부 파일이다. news_bot 이 `prior_coverage` 를 주입하는
    것과 같은 구조다.

    Args:
        generic: 흔한 말 집합을 **밖에서** 정해 준다. story 가 조립된 뒤에 이
            함수를 한 번 더 부를 때(ranking 의 `continuity_recheck`) 두 번째
            입력은 이미 걸러진 소수라, 안에서 다시 세면 `generic_anchors` 의
            문턱이 최소값 6 으로 떨어져 **같은 하루 안에서 흔한 말의 정의가
            바뀐다**. 처음 풀에서 한 번 세고 그대로 물려주면 두 번의 판정이
            같은 기준 위에 선다. None 이면 예전처럼 안에서 센다.
    """
    conf = resolve_config(cfg)
    today = today or datetime.now(KST).date().isoformat()
    rows: list[dict] = []
    if not recent:
        for item in items:
            item.pop("continuity", None)
        return {"checked": len(items), "matched": 0, "verdicts": []}

    # 흔한 말은 비교 풀에서 **세어서** 정한다 (generic_anchors 주석 참조).
    if generic is None:
        generic = generic_anchors(list(items) + list(recent))
    matched = 0
    for item in items:
        story_identity.prepare_candidate(item)
        verdict = verdict_for(item, recent, conf, today, generic)
        if verdict is None:
            item.pop("continuity", None)
            continue
        item["continuity"] = verdict
        # story_id 상속은 identity_confirmed(직접 동일 id·재확인됨 또는 지문 신원
        # 축 확정) 또는 evidence_confirmed(근거 기사 압도적 공유)일 때만 한다.
        # 단순 제목 유사도·progression 판정·일반 앵커(anchor_only)만으로 붙은
        # 매칭은 `continuity` 진단·감점에는 반영되지만 story_id 는 물려주지
        # 않는다 — 그 상속이 web/build_data.py 의 issue_id 로 그대로 이어지고,
        # 다음 회차 재판정에서 "이미 같은 id" 라는 사실만으로 identity_confirmed
        # 가 자기강화되는 고리를 만들었다(2026-08-31 사고: 볼탈리아 브라질
        # 데이터센터 건과 무관한 미국 데이터센터 지연 기사가 `데이터센터`·
        # `전력수요` 앵커만으로 story_id 를 물려받아 web 빌드에서 issue_id 가
        # 충돌했다).
        if verdict.get("story_id") and verdict.get("story_id_inheritable"):
            prior = next(
                (row for row in recent
                 if str(row.get("hash") or "") == str(verdict.get("prior_hash") or "")),
                None,
            )
            if prior is not None:
                resolution = story_identity.inherit(
                    item,
                    prior,
                    records=recent,
                    fingerprint_confirmed=(verdict.get("identity_method")
                                           == "fingerprint_anchors"),
                    evidence_confirmed=bool(verdict.get("evidence_confirmed")),
                    contested_axes=verdict.get("fingerprint_identity_contested") or (),
                )
                verdict["story_id_inheritable"] = resolution.inherited
                verdict["identity_resolution"] = resolution.status
                verdict["identity_resolution_reasons"] = list(resolution.reasons)
                verdict["story_id"] = resolution.story_id
        elif verdict.get("story_id"):
            verdict["identity_resolution"] = "uncertain"
        matched += 1
        rows.append({
            "hash": item.get("hash", ""),
            "title": (item.get("title_kr") or item.get("title") or "")[:80],
            **{k: verdict[k] for k in ("prior_title", "prior_date", "days_ago",
                                       "similarity", "evidence_shared",
                                       "evidence_confirmed", "progression",
                                       "progression_kind", "window_days",
                                       "repeat_streak", "penalty", "drop",
                                       "identity_confirmed", "identity_method",
                                       "story_id_inheritable")},
        })
    return {"checked": len(items), "matched": matched, "verdicts": rows}
