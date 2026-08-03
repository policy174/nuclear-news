# AS-IS — Nuclens 코드베이스 현황 (Phase 0)

`NUCLENS_SPEC.md` Phase 0 산출물. **코드 변경 없음.**

## 이 문서가 말하는 "현재"는 세 개다 — 섞어 읽지 말 것

| 표시 | 의미 | 이 문서 기준 |
|---|---|---|
| `CODE` | 조사 대상 브랜치에 커밋된 코드 | **`cce4e85`** (`feat/editorial-layer`) |
| `LIVE` | 실제 배포돼 돌아가는 것 | **`6fb3834`** (`origin/main`) — CODE보다 **5커밋 뒤** |
| `DATA` | 실제 저장 데이터에서 관측됨 | `archive/*.jsonl` 568건 · `delivery_log.jsonl` 165줄 |

**`9918e9c`~`cce4e85`(선정 컷오프·편집 override·open_question·주간 판세)는 아직 배포되지
않았다.** 라이브 https://nuclens.pages.dev 는 `6fb3834`로 빌드된 화면이다. 아래에서 이
구간의 기능을 설명할 때는 `CODE only`로 표시한다.

- 워크트리: `my-projects/nuclens-upgrade`
- ⚠️ 이 브랜치는 **다른 세션이 동시에 작업 중**이다. Phase 0 조사 중에도 `393e5e9` → `cce4e85`로
  4커밋 전진했다. 아래 `파일:라인`은 전부 `cce4e85` 기준으로 재검증했고(91건 중 91건 일치),
  이후 커밋에서는 다시 어긋날 수 있다.
- 함께 볼 것: [`score_distribution.md`](score_distribution.md) · [`ui_strings.md`](ui_strings.md) ·
  [`2026-08-03-selection-floor-backtest.md`](2026-08-03-selection-floor-backtest.md)

---

## 0. 명세 ↔ 코드 충돌 (먼저 읽을 것)

명세는 "명세와 실제 코드가 충돌하면 임의 판단하지 말고 보고하라"고 했다. 9건 + 구현 쪽 1건이다.

| # | 명세 | 실제 | 조치 |
|---|---|---|---|
| **C1** | 1-1 `score_threshold_domestic/global` 절대 점수 하한 | 형태는 쓸 수 있으나 **면제 규칙이 빠졌다.** 그대로 걸면 핵심 뉴스가 잘린다 | 명세에 면제 1줄 추가 |
| **C1′** | (명세에 없음) | 현재 구현의 **등급 면제는 표본에서 no-op**이고, 나중에 명세 P1을 무력화한다 | 구현에서 제거 권고 |
| **C2** | 1-1 `display_cap_domestic/global` 신설 | 이미 존재 (`daily_brief.py:59-60`) | 문구만 정정 |
| **C3** | 1-4 `selection_reason` 신설 | 이미 만들었다가 **의도적으로 되돌림**(`a64e252`) | 전제 조건 먼저 |
| **C4** | 2-1 문자열 외부화 | 3개 파일에 786건, 그중 374건은 **빌드 시점 데이터** | 범위 재정의 |
| **C5** | 2-2 `btn.evidence` "근거 데이터 보기" → "선정 근거" | 그 버튼은 **트렌드 차트 원자료 토글**이다 | 매핑 오류 |
| **C6** | 2-3 `state.empty_day` 신설 | `9918e9c` 에 **구현됨**(`app.js:634`) — 단 `CODE only`, 미배포 | 중복 작업 |
| **C7** | 5-2 전일 대비 델타 | 웹 산출물은 gitignore — **어제 데이터가 없다** | 데이터 출처 변경 |
| **C8** | 6 `이슈 아카이브`를 첫 화면에서 제거 | 이미 별도 탭이다. 첫 화면엔 없다 | 문구만 정정 |
| **C9** | 7 `sensitivity_level` | 저장소 전체에 **해당 필드·개념이 0건** | 신규 + 백필 필요 |

### C1. 절대 점수 하한은 쓸 수 있다 — 빠진 것은 **면제 규칙**이다

근거 전문은 `score_distribution.md` §7 · §7-2. 요지만:

- `must_read`의 **40.7%(37/91)** 가 `features` 없이 큐에 들어온다. `ranking.score_item()`이
  `sanitize_features()` None을 받으면 `_legacy_score()`로 빠지는데(`ranking.py:154`), 이
  경로는 `event_weights`도 feature 가중치도 반영하지 않아 **점수가 등급 기본값 10 근처에
  고정**된다.
- 그래서 `must_read` 점수 분포가 이봉(P25 9.7 / 중앙 22.6)이고, `nice_to_know` 중앙값
  13.3보다 낮은 `must_read`가 대량으로 존재한다.
- floor=12를 걸면 잘리는 `must_read` **35건이 전부 features 결손**이다. 같은 `한빛 1·2호기`
  이슈가 하루 뒤 features를 제대로 받았을 땐 25.9점이다. **동일 사건이 11.6 ↔ 25.9로 갈린다.**

> 명세가 쓴 그대로의 절대 하한은 "중요하지 않은 기사"가 아니라 **"큐레이션이 실패한
> 기사"** 를 자른다. 그리고 그 실패는 로그에 아무 흔적을 남기지 않는다.

**그런데 잘리는 것이 전부 결손 항목이라는 사실이 곧 해법이다.** `features is None`이면
하한 판정에서 빼는 한 줄을 넣으면:

| 설계 | 국내 0건 | 해외 0건 | 총 선정 | must_read 탈락 |
|---|---:|---:|---:|---:|
| 절대 14 (명세 그대로) | 4 | 3 | 128 | 7/57 |
| **절대 14 + 결손 면제** | **2** | **2** | **139** | **0/57** |
| 등급 기반 14 (`9918e9c` 구현) | 2 | 2 | 139 | 0/57 |

아래 두 줄은 **선정 결과가 항목 단위로 완전히 같다**(공통 139건, 차집합 0건).

### C1′. 그렇다면 등급 면제는 왜 있는가 — 지금은 no-op, 나중엔 해롭다

`ranking.FLOOR_EXEMPT_GRADE = "must_read"`(`ranking.py:450`, 판정은 `:459`)는 이 표본에서 **한 번도
발동하지 않는다**:

```
features 있는 must_read 중 14점 미만 : 0건   ← 등급 면제가 걸릴 유일한 집단
features 결손 must_read (중복 통과분) : 37건  ← 결손 면제가 이미 전부 통과시킴
```

문제는 `features` 결손이 고쳐진 뒤다. 그때 이 조항은 "`must_read`는 점수와 무관하게 전량
통과"로 남고, **명세 P1(*"채우지 않는다 — 점수의 하한을 신설한다"*)이 `must_read` 전체에
대해 무효**가 된다.

`must_read`가 믿을 만한 판정이면 감수할 만하다. 그런데 §2에서 보듯 **`must_read`의 40%는
LLM 판정이 아니라 batch 실패 시 출처로 자동 부여된 값**이다(`news_bot.py:1464`). 등급을
점수 위에 두는 규칙은 그 신뢰도를 전제하는데, 전제가 성립하지 않는다.

> **권고: `9918e9c`에서 등급 면제를 빼고 결손 면제만 남긴다.** 표본 결과가 동일하고,
> 명세 P1과 충돌하지 않으며, 결손이 고쳐지면 자동으로 의도대로 작동한다.

### C3. 선정 사유는 템플릿 수의 문제가 아니다

`build_data.selection_reasons()`(`web/build_data.py:496`)는 살아 있고 `briefings.json`·
`issues.json`에 계속 실린다. `3fd5038`이 화면에 노출했다가 `a64e252`가 되돌렸다.

되돌린 이유는 문장이 **기사 분류의 재진술**이었기 때문이다. 실측(`score_distribution.md` §5·§6):

- 순위를 실제로 가르는 것은 `event_type`과 `importance` **둘뿐**이다. 기여도 표준편차
  상위 4개 중 3개가 `event_type` 분기이고, LLM feature 3개를 다 합쳐도 `importance`
  하나에 못 미친다.
- `market_materiality`·`policy_materiality`는 사실상 1과 2의 이진 선택(각 83~84%),
  `korea_relevance`는 1이 58.7%.

명세 1-4는 "템플릿 3종 이상 로테이션"을 요구하지만, **입력 factor가 실질 2값이면 템플릿을
늘려도 문장 조합 수는 늘지 않는다.** Phase 1-4의 전제는 템플릿 수가 아니라 features
결손·상수화 해결이다.

### C5. `btn.evidence` 매핑 오류

명세는 `근거 데이터 보기` → `선정 근거`로 바꾸라고 한다. 그런데 그 문구는
`index.html:245·253·260` 세 곳 — 전부 **주간 흐름 탭의 차트 원자료 `<details>` 토글**이다.
이슈 선정 사유와 무관하다. 이름만 바꾸면 "선정 근거"를 눌렀는데 키워드 집계표가 나온다.

이슈 카드의 실제 액션은 `issueActions()`(`app.js:494`)가 만든다. 명세가 의도한 버튼은
여기에 **신설**해야 한다.

### C7. 전일 비교의 재료가 웹 산출물에 없다

`web/public/data/`는 `.gitignore:16`에 있다. CI가 매 빌드 전체 재생성한다.
**git 저장소 기준으로 전일 산출물 이력이 보존되지 않는다.** Cloudflare Pages에 과거 배포
artifact가 남아 있을 수는 있으나 데이터 계약으로 의존할 수 없다(보존 기간·접근 경로 모두
플랫폼 정책이고, 빌드 실패한 날은 애초에 스냅샷이 없다).

Phase 5의 델타는 `archive/YYYY-MM.jsonl`(만료 없음) + `delivery_log.jsonl`에서 **매번
재계산**해야 한다. 다만 둘로 재현되는 범위가 다르다:

| 어제의 무엇 | 재현 가능? | 출처 |
|---|---|---|
| 실제 선정·발송 결과 | ✅ | `delivery_log` (기사 단위) |
| 점수·breakdown | ✅ | `delivery_log.breakdown` |
| 지역 판정 | ✅ (당시 값) | `delivery_log.region` — 아카이브엔 없다 |
| 후보 전체(탈락분 포함) | ❌ | `archive`는 수집 전량이나 '그날 큐에 있었는가'는 없음. `digest_queue.json` 커밋 이력으로만 가능 |
| 이슈 묶음(클러스터) | ❌ | 매 빌드 재계산. 임베딩이 Actions 캐시라 결정적이지 않음 |
| 화면에 표시된 카피 | ❌ | 빌드 산출물이 남지 않음 |

**'신규 기사'는 재현되지만 '신규 이슈'는 재현되지 않는다.** 명세 5-2가 둘을 구분하라고
요구한 그 지점이 곧 저장 구조의 공백이다.

---

## 1. 파이프라인 전체 흐름

```
[매시 정각] crawl.yml
  news_bot.py:1359 main()
    ├ collect_articles()      :1229  네이버 검색 API (feed별 키워드·anchor·negative)
    ├ collect_rss_articles()  :1187  sources.json RSS + Google News site: 우회
    ├ email_ingest.fetch_newsletter_articles()   ANS 뉴스레터 IMAP (미설정 시 스킵)
    ├ dedup_exact_candidates():1300  ① URL 정규화 + ② 제목 완전일치
    ├ (인라인 fuzzy)          :1411  ③ difflib 제목 유사도 ≥ 0.82
    ├ semantic_dedup()        :518   ④ 임베딩 코사인 (gemini-embedding-2, 캐시)
    ├ curate_batch()          :999   Gemini batch — importance/section/summary/tags/features
    │                                 실패 시 폴백(:1464) → features 없이 큐 적재
    ├ prior_coverage_count()  ranking:170  최근 21일 아카이브 제목 대조
    ├ queue.append()          :1499  digest_queue.json (3일 컷, :1366)
    └ news_archive.append()   :1546  archive/YYYY-MM.jsonl (append-only, 만료 없음)

[매일 22:25 UTC = 07:25 KST] daily-brief.yml
  daily_brief.py
    ├ plan_briefs()           :512
    │   ├ noise·market 제외   :543
    │   ├ region() 로 국내/해외 풀 분리  :80
    │   └ ranking.rank_and_select(pool, cap, cfg, now, floor)   ranking:493
    │        score_item():265 → cluster_duplicates():367 → [floor:453] → select_diverse():406
    ├ claim push (발송 전 커밋 — 실패하면 발송 안 함)
    ├ send_outbox()  → telegram_send
    ├ append_delivery_log()   :749   선정분만 delivery_log.jsonl
    └ append_selection_stats():719   회차 통계 1줄 (`9918e9c` 신설)
  trend_insights.py  → trend_insights.json   (Gemini 1회/일, 흐름 해석)
  daily_lead.py      → daily_leads.json      (Gemini 1회/일, 그날 한 문장)

[빌드·배포] crawl.yml(짝수 UTC시만) / daily-brief.yml(하루 1회 확정)
  web/build_data.py build()
    ├ load_archive()          :292   archive/*.jsonl 전량
    ├ load_deliveries()       :311   delivery_log.jsonl
    ├ load_selection_stats()  :433   record_type=selection_stats (`9918e9c` 신설)
    ├ cluster_selected_articles() :1203  이슈 묶기
    │   issue_similarity():943 — 임베딩 코사인 0.92 + 시설·국가 충돌 차단
    │   is_review_candidate():1031 → issue_review.py 배치 LLM 검수 (0.88~0.92 회색지대)
    ├ verification_state()    :1412  4단계 검증 상태
    ├ daily_lead():1525 / daily_headline():1556 / order_issue_rows():1560
    ├ attach_keei_refs()      :1705  KEEI 인사이트 LLM 매칭
    ├ system_status()         :392   (`9918e9c` 신설)
    └ 출력 10종 → web/public/data/
  wrangler pages deploy web/public --project-name=nuclens
```

### 워크플로 3종

| 파일 | 트리거 | 하는 일 |
|---|---|---|
| `crawl.yml` | `cron: 0 * * * *` | 수집 → 아카이브 → 커밋 → **짝수 UTC시에만** 빌드·배포 |
| `daily-brief.yml` | `cron: 25 22 * * *` | 선별 → 텔레그램 발송 → 흐름 해석 → 확정 배포 → 렌더 스모크 |
| `weekly.yml` | 주간 | `weekly_bot.py` → `weekly_reports.json` |

> **주의(별건)**: `crawl.yml:134`의 배포 게이트가 `date -u +%H % 2`다. GitHub cron은 상시
> 지연되므로 시각 기반 게이트는 그날 스텝이 통째로 빠질 수 있다. 발간물 수집 스텝에서
> 이미 한 번 겪은 패턴이다.

---

## 2. 랭킹 — `ranking.py` + `ranking_config.json`

### 점수 공식 (`score_item()` `ranking.py:265`)

```
점수 = importance_base[등급]            must_read 10 / nice_to_know 5
     + event_weights[features.event_type]   policy_decision·regulatory_action·
     │                                      contract_award·incident_safety 6
     │                                      project_milestone 4 / corporate_move 3
     │                                      market_signal·research_report 2
     │                                      other 1 / opinion 0
     + Σ (features[k] × feature_weights[k])  korea 1.2 / market 1.0 / policy 1.0
     │                                       evidence 1.0 / novelty 0.0
     + source_bonus[tier]                    tier1 3.0 / tier2 1.5
     + related_reports_bonus                 1.0
     + tracking[follow_up|repeat]            1.5 / 0.5   (prior_coverage 기준)
     − time_decay                            12h당 0.5, 최대 3.0
```

- `evidence_strength`는 LLM이 아니라 `derive_evidence_strength()`(`:194`)가 확정/전망 표현 +
  수치 유무로 판정한다.
- `novelty`는 `derive_novelty()`(`:213`)가 판정하지만 **가중치 0** — 수집 단계 4중 dedup을
  통과한 기사는 정의상 대부분 새 사건이라 판정할 것이 없다는 결론(2026-08-01).

### 실측 기여도 (`score_distribution.md` §6, 433건)

| 항목 | 표준편차 |
|---|---:|
| `event:policy_decision` | 2.22 |
| `importance` | 2.01 |
| `event:regulatory_action` | 1.61 |
| `event:corporate_move` | 1.26 |
| `korea_relevance` | 1.14 |
| `evidence_strength` | 0.93 |
| `policy_materiality` | 0.88 |
| `market_materiality` | 0.84 |
| `source_tier1` | 0.63 |
| `tracking:follow_up` | 0.10 (433건 중 **2건**만 발동) |

**순위를 가르는 것은 `event_type`과 `importance` 둘뿐이다.**

### `_legacy_score()` 폴백 — 이번 개편의 핵심 결함

`ranking.py:154`. `sanitize_features()`가 dict가 아닌 값을 받으면 None을 반환하고
(`ranking.py:104`), `score_item()`이 이 경로로 빠진다.

```
점수 = 10.0(must_read) 또는 5.0 + khnp_section 2.0 + primary_domain 2.0 + related_reports 1.0
```

`event_weights`·feature 가중치가 **전부 무시**된다. 발동 조건은 두 가지다:

1. **구 큐 스키마** — `features` 키 자체가 없던 시기(2026-07-14 이전 적재분). 큐레이션은
   정상이라 summary·tags는 다 있다. 5건.
2. **batch 큐레이션 실패 폴백** — `news_bot.py:1456-1483`. `curate_batch()`가 해당 hash를
   못 돌려주면 원문 스니펫의 완결문만 summary로 쓰고 `features`는 넣지 않는다. 이때
   **`importance`는 판단이 아니라 출처로 결정된다**:
   ```python
   force_t1 = is_tier1_source(article)          # news_bot.py:1464
   "importance": "must_read" if force_t1 else "nice_to_know"
   ```
   11건. 전부 summary가 빈 문자열이고 2026-07-27·07-31에 몰려 있다.

> 즉 **`must_read` 등급의 40%는 LLM의 중요도 판정이 아니다.** 채택된 하한이 이들을
> 면제하는 것은 안전한 선택이지만, 동시에 **등급을 가장 못 믿을 항목이 항상 통과한다**는
> 뜻이기도 하다. 별건으로 잡아야 한다.

### 선정 이후 (`rank_and_select()` `ranking.py:493`)

1. `cluster_duplicates()` `:367` — `duplicate_similarity` 0.82 제목 유사도
2. `floor_verdict()` `:453` — `9918e9c`. `must_read` 면제 + features 결손 면제
3. `select_diverse()` `:406` — 같은 theme 2건 초과 시 −2.5, 상위 k
4. 캡: `DOMESTIC_CAP=3` / `FOREIGN_CAP=6` (`daily_brief.py:59-60`)

**웹은 이 점수로 다시 정렬하지 않는다.** `order_issue_rows()`(`build_data.py:1560`)가
지역별 순위를 맞물려 배열한다. 카드 정렬키는 `(last_seen, importance=="must_read",
sort_score, article_count)` 내림차순 — **1순위가 날짜라 아카이브에서는 `selection_score`가
사실상 무의미**하다.

---

## 3. 프런트엔드 렌더 구조

`web/public/`: `index.html`(323줄) · `app.js`(1,630줄) · `style.css`(1,243줄). 빌드 도구 없음,
의존성 0. 데이터는 `/data/*.json` fetch.

### 데이터 로드 (`app.js`)

| 시점 | 파일 | 함수 |
|---|---|---|
| 부트 | `status.json`(선택) → `manifest.json`(선택) | `initializeDataBase()` :80 |
| 초기화 | `news.json` `briefings.json` `issues.json` `trend.json` `meta.json` `insights.json` `publications.json`(선택) | `init()` :1572 |
| 폴링 | `meta.json` `manifest.json` | `checkForNewGeneration()` :384 |

`manifest.json`이 없으면 flat `data/`로 폴백한다. `loadRootJSON()`은 상시 캐시버스터를 붙인다
(2026-08-01 엣지 캐시 사고 대응).

### 탭 5개 — `VIEW_IDS = ["news","search","trend","saved","pubs"]` (`app.js:26`)

| 뷰 | DOM | 렌더 함수 | 소비 데이터 |
|---|---|---|---|
| 오늘 브리핑 | `#view-news` | `renderBriefing()` :665 | `briefings.json` · `issues.json` |
| 이슈 아카이브 | `#view-search` | `renderArchiveSearch()` :799 | `issues.json` |
| 주간 흐름 | `#view-trend` | `renderTrend()` :1321 | `trend.json` · `insights.json` · `weekly_reports` |
| 저장 | `#view-saved` | `renderSaved()` :827 | localStorage + `issues.json` |
| 발간물 | `#view-pubs` | `renderPubs()` :863 | `publications.json` |

### 첫 화면 섹션 ↔ 명세 매핑

| DOM | 현재 라벨 | 렌더 | 명세 키 |
|---|---|---|---|
| `#systemStatus` (`index.html:65`) | 상태 스트립 | `renderSystemStatus()` :341 | — (`status.json`) |
| `.briefing-hero` `#briefingKicker` (`:82`) | 오늘의 핵심 | `renderBriefing()` | `section.headline` |
| `#changedIssues` (`:99`) | 지금 달라진 이슈 | `changedIssues()` :601 | `section.delta` |
| `#todayIssues` (`:110`) | 오늘 확인된 이슈 | `renderBriefing()` | `section.issues` |
| `.feed-drawer` `#feedLabel` (`:154`) | 오늘 수집한 원문 | `renderNewsFeed()` :750 | `section.sources` |
| 사이드 `#sideVerification` (`:161`) | 오늘의 검증 상태 | `renderBriefingSidebar()` :577 | `section.verification` |
| 사이드 `#sideWeekly` (`:162`) | 이번 주 이어지는 흐름 | `renderBriefingSidebar()` | `section.ongoing` |
| 사이드 `#quickTopics` (`:163`) | 자주 찾는 주제 | `renderTopicSelects()` :440 | 명세 6장 "제거" |
| 탭 (`:46`) | 이슈 아카이브 | — | `section.archive` (**이미 별도 탭**) |

이슈 카드 액션은 `issueActions()`(`app.js:494`), 상세 모달은
`openIssueDialog()`(`app.js:1037`)가 만든다.

### 빈 화면·상태 문구는 이미 절반 있다

`9918e9c` 가 `emptyBriefingState()`(`app.js:621`)·`pipelineTrouble()`(`:614`)을 넣었다.

- `app.js:634` `"오늘은 브리핑 기준을 넘는 이슈가 없습니다"` + `"검토한 후보 N건은 기준에
  미치지 못했습니다."` → 명세 `state.empty_day`와 사실상 동일
- `app.js:641` `"오늘 새로 확인된 브리핑 이슈가 없습니다"` → 수집 자체가 0인 경우와 구분
- `app.js:646` `"오늘은 새로 연결된 이슈가 없습니다"`

명세 `state.quiet` / `no_change` / `surge` / `loading`(유머 결)만 없다.

---

## 4. 하드코딩 UI 문자열

전수 목록은 **[`ui_strings.md`](ui_strings.md)** (추출 823건 · UI 대상 786건, `파일:라인` 포함).
여기엔 요약만.

> **단위**: 823은 **발생 위치 수**(파일·라인·문구 3중 중복만 제거)이지 고유 문구 수가 아니다.
> 같은 문구가 여러 곳에 있으면 각각 센다 — Phase 2의 작업량은 이 단위가 맞다.

| 파일 | 추출 | 제외 | **UI 대상** | 성격 |
|---|---:|---:|---:|---|
| `web/build_data.py` | 411 | 37 | **374** | **빌드 시점에 JSON에 구워지는 문장** (오버라인·검증 라벨·변화 문장·해석문) |
| `web/public/app.js` | 277 | 0 | **277** | 동적 라벨·빈 화면·오류·토스트 |
| `web/public/index.html` | 135 | 0 | **135** | 정적 섹션명·탭·버튼·푸터 |
| **합계** | **823** | **37** | **786** | |

제외 37건은 전부 `build_data.py`의 독스트링 27 · 콘솔 로그 9 · 정규식 상수 1이다 —
화면에 나가지 않는다. (`scratchpad/classify_strings.py`)

분류별:

| 분류 | 건수 | 대표 |
|---|---:|---|
| 빈 화면 | 16 | `app.js:634` 오늘은 브리핑 기준을 넘는 이슈가 없습니다 |
| 로딩·진행 | 10 | `index.html:66` 브리핑을 불러오고 있습니다 / `index.html:83` 오늘의 핵심을 정리하고 있습니다 |
| 오류·토스트 | 12 | `app.js:1567` 데이터를 불러오지 못했습니다 / `app.js:337` 공유 링크를 만들지 못했습니다 |
| 버튼·액션 | 43 | `index.html:245` 근거 데이터 보기 / `index.html:171` 아카이브 검색 |
| 라벨·본문 | 339 | 섹션 제목·설명문·필터 옵션·푸터 |
| 데이터 문장(빌드 시점) | 403 → **374** | `build_data.py` (독스트링·로그 29건 제외 후) |

### Phase 2에 대한 함의

명세 2-1은 "하드코딩된 UI 문자열을 `copy.json`으로 분리"라고만 쓴다. 실제로는 **두 층**이다.

1. **표시 문구** (`index.html` + `app.js`, **412건**) — `copy.json` 이관 가능. 명세가 상정한 것.
2. **데이터 문장** (`build_data.py`, **374건**) — 빌드 시점에 JSON 필드로 구워져 배포된다.
   문구를 고쳐도 **재빌드 전까지 화면에 반영되지 않는다.** 명세 2-1의 목적("문구 수정에
   배포가 필요 없게")을 이 층에서는 달성할 수 없다.

> Phase 2 착수 전에 어느 층까지를 범위로 볼지 결정해야 한다. 1층만 하면 반나절이 맞고,
> 2층까지 하면 데이터 스키마 변경이 따라온다.

명세 5장 문구 교체표 10개 항목은 전부 실제 DOM과 1:1 대응한다(위 §3 표). 다만 `btn.evidence`
매핑은 틀렸다(C5).

---

## 5. 데이터 스키마

### 아카이브 레코드 — `archive/YYYY-MM.jsonl` `DATA` (실측 **29필드**)

`news_archive.py`가 append-only로 쓴다. 원문 본문은 저장하지 않는다(저작권 — 제목·요약·링크만).

**568건 전부가 정확히 29키**다(키 합집합 29 = 모든 레코드 공통, 선택 필드 없음).

| 필드 | 내용 | 신뢰도 |
|---|---|---|
| `hash` | URL 정규화 후 해시 | 키 |
| `url` `domain` `publisher` | 원문 위치·매체 | `publisher`는 `8d0f5c6` 이후 신규분만 정상 |
| `title` `title_kr` | 원제·한국어 제목 | |
| `summary` `implication` `why_important` | LLM 요약 3종 | batch 폴백분은 `summary`가 스니펫 or 빈 값 |
| `importance` | `must_read` / `nice_to_know` / `market` / `noise` | `must_read` 중 features 결손 — **고유 기사 23.5% / 관측치 40.7%**(§2) |
| `features` | `{event_type, korea_relevance, market_materiality, policy_materiality, novelty, evidence_strength, report_worthiness}` | **결손 — 고유 기사 11.5% / 회차 관측치 14.7%** |
| `section` | `khnp` / `domestic` / `international` / `smr` … | 지역 판정의 실질 기준 |
| `scope` | `kr` / `overseas` | 🔴 **신뢰 불가** — `delivery_log` 165줄 중 값이 있는 것 **12줄(7%)**. 국내/해외는 `daily_brief.region()`(`:80`)을 쓸 것 |
| `category` `tags` `topics` `countries` `article_type` | 분류 | `topics`는 통제 어휘 12개 |
| `source_type` `source_tier` `evidence_role` | 출처 프로파일 (`data_quality.source_profile()`) | 2026-08-01 이후 전량 |
| `event_date` `event_date_type` `event_date_precision` `event_date_source` | 사건 시점 4필드 | 스키마만 있고 신규 수집분부터 채움 |
| `feed` `pub` `archived_at` `v` | 수집 메타 | |

건수: `2026-07.jsonl` 399 · `2026-08.jsonl` 169 = 568.

> 🔴 **`region`이 없다.** 지역은 저장되지 않고 `daily_brief.region()`(`:80`)이 매번 재계산한다.
> 발송 시점의 판정은 `delivery_log.jsonl`의 `region`에만 남는다(165/165줄). 함수가 바뀌면
> 아카이브에서 재계산한 과거 지역이 당시 실제 판정과 달라진다 — Phase 5 재현의 제약이다.
> `sensitivity_level`도 없다(C9).

### 큐 레코드 — `digest_queue.json` (`news_bot.py:1499`)

아카이브 필드 + `matched` · `related_reports` · **`prior_coverage`** · `queued_at`.
`prior_coverage`는 2026-08-01(`4da745d`) 도입이라 그 이전 데이터에는 없다.

### `delivery_log.jsonl` — 두 종류의 줄이 섞여 있다

| 종류 | 판별 | 필드 |
|---|---|---|
| 기사 (기존) | `record_type` 없음 | `hash` `date` `title_kr` `domain` `region` `scope` `score` `section` `theme` `breakdown` |
| 회차 통계 | `record_type == "selection_stats"` | `date` `generated_at` `pipeline_status` `domestic{candidate_count, selected_count, below_floor_count}` `overseas{…}` |

`breakdown`은 `score_item()`이 남긴 점수 내역이다 — "왜 이 기사가 올라왔지?"의 유일한 근거.

> ⚠️ **`selection_stats`는 `CODE only`다.** 생산 코드는 `9918e9c`에 있으나 **실제 로그에는
> 0줄**이다(165줄 전부 기사 레코드). 아직 배포되지 않았고 `daily-brief.yml`이 한 번도
> 이 코드로 돌지 않았다. `build_data.system_status()`가 이걸 소비하므로, 배포 첫날까지는
> 상태 스트립이 `selection_stats` 없는 경로(수집 시각 폴백)로 동작한다.

> 읽는 쪽이 `record_type`을 걸러야 한다. `daily_lead.collect_today()`(`daily_lead.py:110`)에
> 이미 방어가 들어갔다.

### `sensitivity_level` — 존재하지 않는다

저장소 전체 검색 결과 0건. Phase 4는 필드 신설 + 큐레이션 프롬프트 변경 +
기존 568건 백필이 전제다. 명세는 이 비용을 반영하지 않았다.

---

## 6. 일별 산출물 저장 위치·보존 기간

| 산출물 | 위치 | 보존 | 근거 |
|---|---|---|---|
| `archive/YYYY-MM.jsonl` | git 커밋 | **만료 없음** | `news_archive.py:9` |
| `delivery_log.jsonl` | git 커밋 | 만료 없음 (165행 / 20일) | append-only |
| `trend_insights.json` `daily_leads.json` `publications.json` `issue_llm_reviews.json` `keei_llm_matches.json` | git 커밋 | 만료 없음 (LLM 캐시) | |
| `curated.json` | git 커밋 | **14일** | `news_bot.py:616` `DEDUP_RETENTION_DAYS=14`(`:44`) |
| `sent.json` (dedup 상태) | git 커밋 | **14일** | `news_bot.py:606` |
| `digest_queue.json` | git 커밋 | **3일** | `news_bot.py:1366` |
| `outbox.json` | git 커밋 | 당일 1건 (덮어씀) | 36h 보호 창 `daily_brief.py:793` |
| `embeddings.json` | **Actions 캐시** | git 아님. 캐시 없이는 **동일 상태 복원 불가** — 원문·API 키가 있으면 재생성은 되나 모델 버전 차이로 같은 값 보장 안 됨 | `.gitignore:13` |
| `web/public/data/*.json` | **gitignore** | **이력 없음** — 빌드마다 재생성 | `.gitignore:16` |
| Cloudflare Pages 배포본 | 엣지 | 배포 시점 스냅샷 | |

### Phase 5에 대한 결론

**전일 비교는 `web/public/data`에서 할 수 없다. 어제가 없다.**

가능한 재료는 `archive`(만료 없음) + `delivery_log.jsonl`(만료 없음) 둘뿐이고, 이슈 묶음은
`build_data.cluster_selected_articles()`가 매 빌드 새로 만든다. 따라서 델타는
**빌드 시점에 어제 이슈를 재구성해 비교**하거나, 이슈 클러스터 결과를 **커밋되는 파일로
새로 저장**해야 한다. 후자가 명세 5-1 "사건 ID 지속성"과 같은 요구다.

전일 데이터가 없는 날(파이프라인 실패·서비스 시작일)의 처리 규칙은 명세도 요구했다.
`selection_stats`의 `pipeline_status`(`ok`/`partial`)가 이미 그 구분을 만들고 있고,
`system_status()`(`build_data.py:392`)가 소비한다.

### 진단 요령

`https://nuclens.pages.dev/data/issue_audit.json`이 공개돼 있어 모든 병합 쌍의 코사인·제목
유사도·태그 공유·method가 그대로 들어 있다. `embeddings.json`이 git에 없어 로컬 재빌드가
불가능하므로, 임계값 실험은 이 파일을 받아 `issue_similarity()`에 코사인을 주입하는 방식으로
한다(실측 64/64 재현).

---

## 7. 테스트 현황

| 위치 | 파일 | 비고 |
|---|---|---|
| `tests/` | 13개 (`test_ranking` `test_daily_brief` `test_archive` `test_e2e` `test_issue_review` `test_keei` `test_pubs_*` …) | 봇 파이프라인 |
| `web/tests/` | `test_prototype.py` · `render_smoke.mjs` | 웹 빌드 + 라이브 렌더 |

> 렌더 스모크는 **렌더러 출력 노드**를 봐야 한다. `#view-pubs`처럼 정적 HTML이 든 컨테이너를
> 검사하면 렌더러가 죽어도 `textContent`가 안 비어 공허하게 통과한다(2026-08-02 독립 리뷰).

> `app.js` 수정 시 **`node --check web/public/app.js`를 먼저 돌릴 것.** 파싱 실패하면
> 브라우저 console에 에러가 안 잡히고 네트워크는 전부 200이라 데이터 문제로 오진한다.

---

## 8. 조사 중 병렬 세션이 추가한 것 (`34f2b81` · `f5042cb` · `cce4e85`)

Phase 0 조사 도중 같은 브랜치에 3커밋이 더 들어왔다. **명세 범위와 겹친다.**

### `f5042cb` open_question — 명세 P3·Phase 5-3와 직접 겹침

`must_read`에만 붙는 "아직 확정되지 않은 것" 한 문장. `news_bot.norm_open_question()`
(`news_bot.py:827`)이 게이트를 걸고, `build_data.pick_open_question()`(`web/build_data.py:1389`)이
이슈 대표 문장을 고른다. `collect_open_questions()`(`:1874`)가 화면용으로 모은다.

게이트: 50자·선언형·`open_question_source`로 근거 위치를 못 대면 폐기·예측 표현 거부·
초과 길이는 자르지 않고 버림.

> 명세 P3("모르는 건 모른다고 쓴다")와 Phase 5-3의 `uncertainty_note`가 **부분적으로 이미
> 구현됐다.** 다만 명세 5-3이 요구한 `status`(confirmed/announced/reported/disputed/pending)와
> `confirmation_basis`는 여전히 없다. 현재 있는 것은 `verification_state()`의 4단계
> (공식/복수 출처/단일 출처/확인 중)이고, 이건 **출처 개수 기반**이지 사실성 분류가 아니다.

### `34f2b81` 편집 override — 명세 1-2 에디터 픽과 인접

`selection_overrides.json`으로 사람이 이슈 클러스터를 강제 포함(`promote`)·제외(`demote`)한다.
**웹 레이어에만 적용된다** — 텔레그램 브리핑은 07:25 무인 발송이라 개입 창이 없다.

> 명세 1-2는 에디터 픽을 **자동 0~2건**으로 요구한다. 이건 **수동** 장치다. 둘은 배타적이지
> 않지만, "고정 슬롯을 만들지 않는다"는 명세 요구와 `promote`가 충돌할 수 있다
> (`date` 필수로 완화돼 있긴 하다).

### `cce4e85` 주간 판세 — 주간 흐름 탭 상단에 고정 코너 5개

`weekly_bot`의 `batch_synthesize` 결과를 웹에 재사용(`weekly_reports.json`, 최근 26주).
Gemini 호출 증가 없음. `renderWeeklyReport()`(`web/public/app.js`), `#weeklyReport`
(`web/public/index.html:215`).

> 명세 6장의 첫 화면 정보 구조에는 없는 섹션이다. Phase 3 착수 시 이 블록의 위치를
> 다시 정해야 한다.

---

## 9. Phase 0 수용 기준

- [x] `docs/AS_IS.md` 작성 완료 (명세 3장 6개 항목 전부)
- [x] 4번 문자열 목록이 `파일:라인` 형태로 전수 기록 → `docs/ui_strings.md` 823건(UI 대상 786)
- [x] 점수 분포 통계 산출 완료 → `docs/score_distribution.md`
- [x] 명세↔코드 충돌 C1~C9 + C1′ 정리
- [x] 자기 재검토 1회 — C1 결론 정정(절대 하한 기각 → 면제 규칙 추가), 문자열 수 정정
- [x] 병렬 세션 추가분(`34f2b81`·`f5042cb`·`cce4e85`) 반영
- [x] 외부 리뷰 반영 — 표본 단위(관측치 463 / 고유 269) 명시, 29필드 정정,
      `CODE`/`LIVE`/`DATA` 상태 분리, breakdown legacy·정상 분리
- [ ] **여기서 멈추고 보고한다** ← 현재 위치
