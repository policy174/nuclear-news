# Signal Desk 개편 인계 (2026-08-03)

시안 `~/Downloads/nuclens_integrated_signal_atlas_v8.html` 이식 작업의 현재 지점.
계획서는 `~/.claude/plans/a-issue-atlas-cozy-lagoon.md` (rev.2), 이 문서가 그 뒤에
실제로 일어난 일이다.

기준선: **`origin/main = 9e2efaa`** · 테스트 봇 223 / 웹 175 · 라이브 반영·자동 배포 확인 완료.

---

## 0. 계획서가 3중이다 — 먼저 이걸 알고 읽을 것

이 문서 첫 판은 맨 안쪽 것만 추적해서 "Phase 1 완료"처럼 읽혔다. 실제 사다리:

| 층 | 문서 | 단계 |
|---|---|---|
| 최상위 | `NUCLENS_SPEC.md` | **명세 Phase 1~5** |
| 실행 | `docs/PHASE_PLAN.md` | **S0~S7** (명세 Phase 를 슬롯으로 재배치) |
| 이번 작업 | `~/.claude/plans/a-issue-atlas-cozy-lagoon.md` | 그 문서 자체의 Phase -1/0/1/2 |

**Atlas 계획서 §2 가 명시한다: "작업 슬롯은 `docs/PHASE_PLAN.md` 의 S4(첫 화면
재구성)".** 즉 오늘 한 일 전부가 **S4 = 명세 Phase 3** 한 칸이다. Atlas 문서 안의
"Phase 1"은 그 문서의 팔레트 단계일 뿐, 명세 Phase 1(랭킹 신뢰도)과 전혀 다르다.

⚠️ `NUCLENS_SPEC.md` 는 **저장소에 없다**(`find` 0건). PHASE_PLAN 이 상위 문서를
계속 참조하는데 그 문서를 볼 수 없다 — 다음 세션이 먼저 찾거나, 없으면 PHASE_PLAN
을 단일 정본으로 승격시켜야 한다.

---

## 1. 진짜 사다리 — S0~S7 현황 (실측)

| S | 명세 | 내용 | 상태 | 근거 |
|---|---|---|---|---|
| **S0** | — | 브랜치 정리 + 명세 개정 | ⚠️ 브랜치는 정리됨 / **명세 개정 5곳 확인 불가** | SPEC 부재 |
| **S1** | §5→승격 | features 결손 해소 | ✅ 코드 완료, **관찰 2주 대기** | `aa3315d` in main |
| **S2** | Phase 1 | 랭킹 신뢰도 | ⚠️ 2-1만 완료 / 2-2~2-4 미착수 | `72ce421` in main |
| **S3** | Phase 2 | 문구 교체 (786건) | ❌ 미착수 — 오늘 지운 설명문 8건은 그 중 일부일 뿐 | `docs/ui_strings.md` |
| **S4** | **Phase 3** | **첫 화면 재구성** | 🟡 **오늘 작업이 여기** | 이 문서 |
| **S5** | Phase 4 | 민감도 제어 | ❌ **개념 자체 없음** (`sensitivity_level` 검색 0건) | 실측 |
| **S6** | — | 유머 문구 | ❌ 미착수 (`copy.json` 없음). **S5 선행 필수** | 실측 |
| **S7** | Phase 5 | 사건 추적 | ❌ 미착수 (이슈 ID 지속성 0건) | 실측 |

**지금 위치: S4 진행 중.** 그런데 S4 의 선행은 `S2·S3` 인데 둘 다 미완이다 —
오늘 작업은 **선행 조건을 건너뛰고 들어간 것**이다. 팔레트가 다르기 때문에 지금까지
충돌은 안 났지만, S3(문구 786건 외부화)를 나중에 하면 오늘 하드코딩한 화면 문구를
다시 들어내게 된다.

### S4 안에서 끝난 것 / 밀린 것

| 항목 | 상태 |
|---|---|
| 딥 포레스트 팔레트 | ✅ `d6621aa` |
| 레이아웃·타이포그래피 (표 4열·대형 헤드라인) | ✅ `26c1d3e` `7ee41d7` `76b3382` |
| 우측 상시 근거 패널 | ✅ `4063caa` (다른 세션) |
| 발간물 정리 | ✅ `32528c4` |
| 배포 자동화 | ✅ `cb5b7bf` `9e2efaa` — 17:56 실행 확인 |
| **이슈 지도 (Atlas)** | ❌ 데이터 부족 — §3-C |
| `자주 찾는 주제` 제거 (명세 6장) | ❌ 미착수 |
| 주간 판세 블록 위치 결정 | ❌ 미결 |
| `open_question` vs `next_checkpoint` 중복 정리 | ❌ 미결 |

---

## 1-1. Atlas 계획서 자체의 단계 (참고용)

이건 S4 안에서만 의미가 있는 하위 단계다. 바깥 사다리와 헷갈리지 말 것.

| 단계 | 항목 | 상태 |
|---|---|---|
| **Phase -1** | 브랜치 스택 병합 | ✅ (8/3 오전 patch-id 로 이미 반영 확인) |
| | origin/main 기준 워크트리 | ✅ |
| | 테스트 기준선 기록 | ✅ 봇 223 / 웹 174 |
| | 라이브 지표 스냅샷 | ⚠️ 문서화 안 함 — 세션 대화에만 있음 |
| **Phase 0** | 0-A `open_question` 실행 | ❌ **0/110.** 템플릿은 고쳤으나(`c82a09f`) 검증 못 함 |
| | 0-B 병합 판정기 | ⚠️ 임베딩·LLM 검수는 정상, **추적률 0.125 (목표 0.20)** — §2-2 |
| | 0-C 게이트 지표 표 채우기 | ❌ 미착수 |
| **Phase 1** | 토큰 값 교체 | ✅ `d6621aa` |
| | 색 용도 제한 (표면/텍스트 분리) | ✅ CSS 주석으로 고정 |
| | 12.5px 하한 + `font:` 축약형 구멍 | ✅ 테스트 확장, 예외 0 |
| | 다크 파생 | ✅ `data-theme="dark"` 6블록 |
| | og-image 재생성 | ✅ `d6621aa` |
| | "하지 않을 것" 준수 | ✅ tokens.css 미채택 / 렌즈 미재론 / SUIT·IBM Plex 미추가 |
| **Phase 2** | 우측 상시 근거 패널 | ✅ `4063caa` (main 에 있음) |
| | 이슈 지도 (Atlas) | ❌ **미착수 — 데이터 부족** |
| **계획 밖** | 레이아웃·타이포그래피 | ✅ `26c1d3e` `7ee41d7` `76b3382` |
| | 발간물 정리 | ✅ `32528c4` |
| | 배포 자동화 | ✅ `cb5b7bf` + `9e2efaa` — 17:56 실행 확인 |

정리하면 Atlas 문서 기준 **Phase 1 완료 / Phase 0 미완 / Phase 2 는 근거 패널만.**
사용자 체감으로 크게 바뀐 표 레이아웃·타이포는 **Atlas 문서에도 없던 항목**이다 —
계획 밖 작업이었고, 그 경위는 §5 마지막 항목에 있다.

**다시 강조: 위 표는 S4 내부의 하위 단계다.** 바깥 사다리(S0~S7 / 명세 Phase 1~5)
기준으로는 §1 을 볼 것.

### 레이아웃에서 실제로 바뀐 것

- 이슈 목록이 카드 → **표 4열**(`56px 124px 1fr auto`). 상자·라운드를 걷고 행 사이 선 하나.
- `--r-1/2/3` = `0`. 시안 197줄에 `border-radius` 는 장식 원 1건뿐이다.
- h1 `clamp(32px, 4vw, 58px)` / `-.05em` / 800. 시안은 82px 이지만 그건 짧은 문장 기준이고
  여기 h1 은 `daily_lead` 종합 문장(최대 70자)이라 82px 면 다섯 줄이 된다.
- 히어로가 어두운 박스 → 캔버스 위 대형 타이포. `status-strip` 이 헤더 위 모노 바로.
- 오버라인·번호는 `--ff-mono`(시스템 스택). **IBM Plex Mono 는 추가하지 않는다.**
  12.5px 하한도 낮추지 않았다 — 모노·자간·색으로 구분.

---

## 2. 지금 깨져 있는 것 — 먼저 볼 것

### 2-1. [해소됨] 병합 판정기가 죽었던 것

로컬 `wrangler pages deploy` 로 올리면서 **`embeddings.json` 없이 빌드한
산출물이 CI 정상 빌드를 덮었다**(빌드 로그에 `exists: false`). 지표가
332→0, 이슈 108→119 로 잘게 쪼개졌다.

17:56 자동 배포가 캐시를 복원해 **자가 치유됐다**:
`embedding_cache_entries 339` · `remote_embedding_selected 132` ·
`llm_review ok` · 이슈 110.

**교훈: 로컬 수동 배포는 화면만 올리는 게 아니라 데이터까지 덮는다.**
미리보기는 로컬 서버를 쓸 것(§4).

### 2-2. 추적률 12.5% — 유일하게 남은 진짜 문제

`deploy-web.yml` 첫 실행이 웹 테스트 174개 중 하나로 죽었다:

```
test_issue_matching_audit_is_generated
AssertionError: 0.125 not greater than or equal to 0.2
```

이건 코드 결함이 아니다. 0.20 은 한때 실제로 달성했던 수치고(0.2857),
0.125 는 계획서 §3-C 가 "현행 12.5% 대비 개선"을 Phase 0 목표로 적어둔
그 숫자다. **이 테스트가 빨간 것 = Phase 0-B 가 미완이라는 뜻.**

임계값은 내리지 않았다(내리면 신호가 사라진다). 대신 **게이트를 켜는
자리**를 나눴다 — 추적률은 "오늘 뉴스가 어떻게 묶였나"의 결과지 화면
코드의 성질이 아니다:

| 어디서 | 추적률 게이트 |
|---|---|
| `crawl.yml` · 로컬 | **켠다** (데이터 품질을 보는 자리) |
| `deploy-web.yml` | **끈다** (`NUCLENS_SKIP_DATA_GATES=1`) |

건너뛴 것은 `skipped=1` 로 출력에 남아 조용히 사라지지 않는다.

**다음 세션이 할 일: 임계값 손대지 말고 추적률을 올려라.** 상류는 §3-C 의
Atlas 데이터 부족과 같은 뿌리다(병합 판정기가 후보를 못 받고 있다).

---

## 3. 다음에 할 것 (우선순위)

### A. `feat/atlas-p0-data` — 남은 건 커밋 하나뿐

**patch-id 로 확인한 결과**(ancestry 로 보면 4커밋 미병합으로 보이지만 내용 기준):

| 커밋 | main 에 |
|---|---|
| `243f84e` 레이아웃 | ✔ 있음 (`26c1d3e` 로 cherry-pick) |
| `85c8aea` 들여쓰기 | ✔ 있음 (`7ee41d7`) |
| `709d662` 설명문·서체 | ✔ 있음 (`76b3382`) |
| `7eb952e` `curate_with_llm` 잔해 제거 | ✘ **없음 — 유일한 미병합** |

근거 패널(`4063caa`)은 다른 세션이 이미 main 에 올려놨다. 남은 `7eb952e` 는
`news_bot.py` 를 건드리는데, 같은 워크트리(`my-projects/nuclens-upgrade`)에
**그 세션의 미커밋 작업**(batch 잘림 수리 — `BATCH_MAX_OUTPUT_TOKENS 16384`,
분할 재시도)이 얹혀 있다. 그 작업의 주인이 끝낸 뒤 함께 병합할 것.

### B. 발간물 요약 v2 확인 — 다음 pubs CI 실행 직후

`pubs_translate.PROMPT_VERSION` 을 2로 올렸지만 **로컬에 Gemini 키가 없어 한 번도
실행되지 않았다.** 20 UTC 발간물 실행 뒤 확인할 것:

```bash
curl -s "https://nuclens.pages.dev/data/publications.json?cb=$(date +%s)" \
  | python -c "import json,sys;[print(i.get('gist','—'),'|',i.get('title_kr','')[:40]) for i in json.load(sys.stdin)['items']]"
```

- gist 가 제목 재진술이 아니라 **문서 성격·범위**를 말하는지 (10건이 재진술이라 지금은 숨겨져 있다)
- `off_topic` 판정이 붙는지. 붙으면 `publication_drop_reason` 이 제목 규칙 대신 그것을 쓴다
- 남은 오탐 1건: "Mixed Contaminants and Residues in Foods" — 규칙에 안 걸린다.
  `contaminant`/`residue` 를 규칙에 넣으면 원전 오염 문서를 잡을 위험이 커서 LLM 에 맡겼다

### C. 이슈 지도 (Atlas) — 데이터가 오면

시안의 5단계 경로 중 **2칸이 비어 있어 착수하지 않았다.** 정상 빌드 실측(108건):

| 노드 | 필드 | 보유율 |
|---|---|---|
| 오늘의 변화 | `latest_change` | 7 (6%) |
| 남은 질문 | `open_question` | **0 (0%)** |
| 산업 영향 | `implication` | 68 (62%) |
| 관련 보도 N건 | `article_count >= 2` | 18 (17%) |
| 공식 출처 | `official_source_count > 0` | 7 (6%) |

**5칸이 전부 채워지는 이슈 0건.** 지금 그리면 100% 빈칸 그래프다.

착수 판단은 **`open_question` 과 `article_count>=2` 두 값만 보면 된다.** 앞의 것이
0 인 한 '남은 질문' 노드는 만들 수 없고, 뒤의 것이 20% 아래인 한 '관련 보도' 노드가
대부분 숨는다. 둘 다 §2-2 추적률과 같은 뿌리다(병합 판정기가 후보를 못 받는다).

`open_question` 은 `c82a09f`(배치 템플릿 수리) 이후 크롤이 여러 번 돌았지만
**신규 기사 중 must_read 가 0건**이라 게이트를 탈 기사가 안 들어왔다 — 수리가
먹혔는지 아직 판정 불가. 판정 방법:

```bash
git show origin/main:archive/2026-08.jsonl | python -c "
import json,sys
rows=[json.loads(l) for l in sys.stdin if l.strip()]
mr=[r for r in rows if r.get('importance')=='must_read' and (r.get('archived_at') or '')>='2026-08-03T04:30']
print('수리 이후 must_read:',len(mr),'| open_question 보유:',sum(1 for r in mr if r.get('open_question')))"
```

must_read 가 몇 건 쌓였는데도 `open_question` 이 0이면 템플릿이 아니라 게이트
(`news_bot.py:829-843` — 60자·서술문·전망어구 금지·must_read 한정)를 봐야 한다.
**단 생성률을 KPI 로 삼지 말 것** — 게이트를 풀면 AI 가 전망을 지어낸다(계획서 §3-C).

---

## 4. 배포 — 자동화 완료(검증됨)

```
화면 코드(web/**) 를 main 에 병합  →  deploy-web.yml 즉시 배포 (1~2분)
데이터 갱신                        →  crawl.yml 짝수 UTC시 배포
```

`deploy-web.yml` 은 수집·LLM 호출이 없고 상태를 커밋하지 않는다. embeddings 캐시는
**복원만** 한다(주인은 `crawl.yml`). 배포 전에 `node --check app.js` + 웹 테스트 175개
(데이터 지표 게이트는 `NUCLENS_SKIP_DATA_GATES=1` 로 제외 — §2-2).

실측 확인(2026-08-03): `9e2efaa` 푸시 → 17:56:12 빌드 배포, 콘솔 오류 0,
표 4열 · radius 0 · h1 57.6px 라이브 렌더 확인.

**수동 wrangler 배포는 임시다.** 미병합 브랜치를 올리면 즉시 보이지만 다음 크롤이
`origin/main` 기준으로 덮어쓴다. 미리보기는 로컬 서버를 쓸 것:

```bash
python -m http.server 8790 --bind 127.0.0.1 --directory <worktree>/web/public
```

---

## 5. 이번에 데인 것

- **`display: contents` 로 4열 표를 만들면 제목과 요약이 벌어진다.** 자식들이 서로 다른
  그리드 행으로 갈라져 옆 열(근거, 98px) 높이가 그 사이로 배분된다(실측 42px).
  `align-self: start` 로 안 막힌다 — 행의 자동 크기는 여전히 그 항목을 센다.
  열은 CSS 로 흩지 말고 **마크업에서 형제로** 낼 것.
- **규칙과 LLM 판정을 섞을 때 `off_topic: False` 를 명시적으로 저장해야 한다.** 키가
  없으면 폴백 규칙이 돌아서 LLM 이 "관련 있음"으로 본 것을 다시 지운다.
  `is True` / `is False` / 부재 3분기.
- **"제목에서 읽어낼 수 있는 범위만" 프롬프트는 제목 재진술을 낳는다.** 지어내기
  가드레일이 과하면 모델은 안전하게 원문을 되풀이한다. 역할을 갈라 주고
  (제목=무엇 / gist=성격·범위) 나쁨·좋음 예시를 박아야 한다.
- **Windows 로컬 서버**: 백그라운드로 띄우면 `cd` 가 안 먹으니 `--directory` 를 쓸 것.
  Git Bash `pkill` 이 Windows python 을 못 죽여 **여러 프로세스가 같은 포트에 LISTENING**
  되고 오래된 것이 응답한다(수정이 반영 안 되는 것처럼 보임).
  → PowerShell `Get-NetTCPConnection -LocalPort N | Stop-Process`
- **계획서가 "이미 배포돼 있다"를 기능 단위로 판정하면 비주얼 개편이 통째로 빠진다.**
  rev.2 §1 표가 그랬고, 그래서 팔레트만 바뀐 채 "색깔만 바꿨네" 소리를 들었다.
  시안 대조는 기능 목록이 아니라 **실측**(폰트 크기·radius 개수·그리드 열)으로 할 것.
- **작업 문서의 "Phase N"을 상위 명세의 Phase N 으로 착각했다.** 이 인계 문서 첫 판이
  Atlas 계획서의 팔레트 단계를 "Phase 1 완료"로 적어, 명세 Phase 1(랭킹 신뢰도)이 끝난
  것처럼 읽히게 만들었다. 실제로는 S0~S7 중 **S4 한 칸**에 있었다.
  **인계 문서를 쓸 때는 그 작업이 어느 상위 슬롯에 들어가는지부터 적을 것** —
  Atlas 계획서 §2 에 "작업 슬롯은 PHASE_PLAN 의 S4"라고 이미 적혀 있었는데 놓쳤다.
