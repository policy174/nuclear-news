# 슈퍼샘플 조사 — 7개 뉴스 큐레이션 매체 심층 분석

> 2026-08-16. nuclens 콘텐츠 구조 개선을 위한 참조 조사.
> 범위: 콘텐츠 골격·큐레이션 파이프라인·상품 구조만. **디자인은 의도적으로 제외**(별도 트랙).
> 적용 방안은 `2026-08-16-supersample-apply-plan.md` 참조.

---

## 1. Techmeme — "헤드라인이 곧 요약" + 클러스터

- **기본 단위 = 스토리 클러스터**: 대표 기사 1건 + "More:" 2차 출처 5~20개 + 소셜 반응.
  2차 출처 수 자체가 중요도 신호 → 배치 티어링에 사용.
- **Pretitling (2013~)**: 편집자가 **일부**(공식 발표 표현: "some, but not most") 헤드라인을
  재작성 — 전면 재작성이 아니라 **원제목이 요점을 못 담을 때의 편집 개입**. 규칙: 이름·숫자·
  능동 동사, 클릭 없이 요점 파악. 원 매체 제목은 병기. 도입 이유: 매체 헤드라인은 리드를
  묻고 회사명·규모를 뺀다 — 원자력 기사(호기·금액·기관명 누락)와 동일한 문제.
  *(2026-08-16 교정: 초판의 "모든 헤드라인 재작성"은 과장이었음)*
- **대표 기사 선정 규칙(공개)**: 단독=디테일 많은 쪽, 비단독=분석·맥락 우수한 쪽.
  "빠른 것"은 가산점일 뿐. 더 좋은 기사가 나오면 대표 교체(swap).
- **파이프라인**: 알고리즘 발굴·랭킹 → 인간(풀타임 3+파트 ~23명)이 헤드라인·승격·교체.
  "알고리즘은 엣지케이스에서 반드시 실패한다. 거기가 인간 개입 지점." LLM은 헤드라인 보조만.
- **부속 상품**: River(무편집 전체 역시간순 — 선별 검증 장치) / Leaderboard(최근 180일 노출
  기준 매체·기자 랭킹 — 큐레이션 부산물의 상품화) / 아카이브는 화면 스냅샷(`/YYMMDD/hHMM`).
- 출처: techmeme.com/about · news.techmeme.com/130906/headlines ·
  techcrunch.com/2011/10/31/techmeme-opens-the-kimono · crazystupidtech.com (2025-09, 20주년)

## 2. 1440 — "그릇을 절대 안 바꾼다"

- 매일 같은 골격·순서·분량: **Need To Know**(톱 4건, 각 150~200단어) → **In The Know**
  (고정 서브섹션 3개, 불릿 30~50단어) → **In-Depth**(롱폼 2건) → **Etcetera**(잡학+오늘의
  역사+인용구). "5분 완독" 약속을 구조가 보증.
- **제목 공식**: 톱 3건 쉼표 나열("Spending Showdown, AOL, and a Barbecue Feud") = 제목이 목차.
- **중립을 구조로**: 한 사건에 복수 매체 인라인 링크. 470만 구독, VC 없이 흑자.
- 소모성 데일리 → **Topics**(에버그린 딥다이브)로 검색성 자산 전환.
- 출처: join1440.com · join1440.substack.com (2025-09-30 실물 샘플) · amediaoperator.com

## 3. Semafor — 사실/견해 분리 라벨과 그 한계

- **Semaform 정본 5개**: The News(사실만) / Reporter's View(기자 실명 견해) /
  Room for Disagreement(반론) / The View From...(타 지역 시각) / Notable(타 매체 링크).
  실전은 3~5개 가변 — **The News + Notable만 사실상 고정** (2026-07 원자력 기사 2건 실측).
- **Signals**(AI 큐레이션): 내부 도구 MISO가 다국어 요약 후보 반환 → **AI는 탐색, 인간이
  검증·작성**. 철칙: **모든 관점은 특정 매체에 귀속, 무출처 관점 금지**.
- **비판(CJR)**: 사실/견해의 깨끗한 분리는 불가능(무엇을 사실로 고를지가 이미 분석적 선택),
  Room for Disagreement는 기계적 양비론 위험.
- 출처: semafor.com/article/02/05/2024/introducing-semafor-signals · cjr.org · pressgazette.co.uk

## 4. Axios — 고정 소수 라벨 + "라벨 남발 금지"

- **Smart Brevity 4블록**: 제목 6단어 이하 → lede 한 문장 → Why it matters 1~2문장 →
  Go deeper. 전체가 폰 한 화면.
- **Axiom 11종**: What's new / Why it matters / The big picture / By the numbers /
  How it works / Between the lines / Yes, but / What to watch / What's next /
  The bottom line / Go deeper. **모든 문단에 붙이지 않는 게 규칙** — 라벨은 근육기억용.
- **비판(New Republic)**: 라벨 칸을 강제하면 모르는 것도 채운다 → "반증 불가능한 상투구" 양산.
  **nuclens 설계에 가장 중요한 경고 — 채울 수 없는 라벨은 비우는 규칙이 세트다.**
- 출처: axioshq.com/insights/axioms-cut-read-time-and-boost-comprehension ·
  newrepublic.com/article/167857

## 5. Heatmap News — 저널리즘→인텔리전스→데이터 3단 사다리

- **상품 사다리**: News 월 $12.99(기사+뉴스레터, 2026-08 확인 — 조사 초판의 $9.99는 구가) →
  Plus $29(**The Fight**: 주 1회, 고정 코너 3개
  — Spotlight 딥다이브 1건 / Hotspots 지역 라운드업 / Q&A, 단일 필자) → Pro B2B(3,100개
  카운티 반대여론 DB+리스크 모델). **기사가 데이터 상품의 쇼케이스가 되는 순환.**
- **AM Briefing 템플릿**: "Current conditions" 3~4줄 스냅샷 → 번호 스토리 5개(각 100~300단어,
  타 매체는 인라인 귀속) → **The Kicker**(가벼운 마무리 1건). 전체 1,200~1,500단어.
- AM(팩트)과 Daily(Robinson Meyer 관점)를 상품 단위로 분리 — 사실과 견해의 분리를 매체
  차원에서 실행.
- 원자력: 전용 섹션 없음, 데이터센터-전력 수요 스토리에 종속되는 구도.
- 출처: heatmap.news/plus/the-fight · heatmap.news/pro · heatmap.news/am/... (실물)

## 6. Carbon Brief — 타 매체 발췌의 안전 표준형

- **Daily Briefing 발췌 공식**(2026-08-14호 실측): ①원기사 헤드라인 링크 ②이탤릭
  "_기자명, 매체명_" 귀속 ③3~5문장 요약(원문보다 확실히 짧게) ④직접 인용은 따옴표+출처
  ⑤부속 기사는 "MORE ON [토픽]" 한 줄 불릿. **자기 목소리 거의 0, 귀속 철저** —
  저작권·인용 윤리상 가장 방어적, 사내 유통 시비 차단에 최적.
- **DeBriefed(주간) 고정 코너 8개**: This week → Around the world → Latest research →
  **Captured**(차트 1장+해설) → Spotlight → Watch/read/listen → Coming up → 채용.
- **상시 데이터 자산**: State of the Climate(같은 지표 분기 반복), 연례 랭킹, 인터랙티브는
  매년 데이터만 갱신. **모든 집계에 방법론 각주 공개.** 원자력 전용 토픽 페이지 운영.
- 재원: 자선기금(구독 없음, 전부 무료). 직원 28명 중 비주얼·데이터 6명.
- 출처: carbonbrief.org/newsletters · daily-briefing 실물 · debriefed-7-august-2026 실물 ·
  carbonbrief.org/topics/nuclear

## 7. 뉴닉 — 독자의 질문을 구조로 만든다

- **이중 소제목** `[분류 라벨]: [구어체 질문]` — 원전 기사 실측: "상황: 그동안 어떤 상황이었더라?
  / 배경: AI랑 원전이 무슨 상관인데? / 반응: 사람들은 뭐래? / 전망: 앞으로 어떻게 될까?".
  핵심: 질문이 **비전문 독자가 물을 순서**대로(시간순·기관순이 아니라).
- **중립을 레이아웃으로**: "잘한 결정이야 👍 / 잘못된 결정이야 👎" 찬반 소제목 쌍 고정 병기.
  출처 링크는 진영 구분 없이.
- **3단 깊이 피라미드**: 한 호 = 메인 1건(풀 구조) + 핫뉴스 2건(3~4문장) + 1분 뉴스 6건(1문장).
  모든 이슈를 같은 깊이로 쓰지 않는 것이 제작비 통제의 핵심.
- 고슴이의 기능적 본질 = 독자 대리인이 질문을 던진다(캐릭터 없이도 구조만 이식 가능).
  용어는 괄호 병기 + 아웃링크 + 비유 번역.
- 출처: newneek.co/@newneek/article/38453 (2026 원전 기사 실물) · newneek.co/post/LYzMf5/

---

## 교차 관찰 — 공통 패턴 3가지

1. **그릇 고정, 내용 가변** — 1440·Heatmap AM·CB DeBriefed·뉴닉 모두 섹션명·건수·분량 고정.
   LLM 파이프라인에 특히 유리(빈칸 채우기 = 품질 안정). 단, Axios 비판이 경고하듯
   **채울 수 없는 칸은 비우는 규칙**이 반드시 세트.
2. **중립은 태도가 아니라 구조** — 1440(복수 링크), 뉴닉(찬반 병기), Semafor(귀속 강제),
   CB(기자명·매체명 명시). 사내 서비스의 중립성 시비는 논조가 아니라 **귀속·병기 레이아웃**으로 막는다.
3. **3층 케이크** — 매일=팩트 큐레이션(귀속 철저·분석 금지) / 주간=자기 목소리·딥다이브 /
   상시=같은 지표 반복 갱신. nuclens의 TODAY / THIS WEEK·보고서 / 흐름이 1:1 대응.
   **층의 톤을 섞지 않는 것**이 공통 교훈.
