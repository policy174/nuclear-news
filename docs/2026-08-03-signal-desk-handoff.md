# Signal Desk 개편 인계 (2026-08-03)

시안 `~/Downloads/nuclens_integrated_signal_atlas_v8.html` 이식 작업의 현재 지점.
계획서는 `~/.claude/plans/a-issue-atlas-cozy-lagoon.md` (rev.2), 이 문서가 그 뒤에
실제로 일어난 일이다.

기준선: **`origin/main = f9a1eb5`** · 테스트 봇 223 / 웹 174 · 라이브 반영 완료.

---

## 1. 계획서 단계별 현황

계획서의 단계는 Phase -1 / 0 / 1 / 2 다. **계획서에 레이아웃·타이포그래피 단계가
없다** — 오늘 한 작업의 절반이 계획 밖이었다(§5 마지막 항목 참조).

| 단계 | 항목 | 상태 |
|---|---|---|
| **Phase -1** | 브랜치 스택 병합 | ✅ (8/3 오전 patch-id 로 이미 반영 확인) |
| | origin/main 기준 워크트리 | ✅ |
| | 테스트 기준선 기록 | ✅ 봇 223 / 웹 174 |
| | 라이브 지표 스냅샷 | ⚠️ 문서화 안 함 — 세션 대화에만 있음 |
| **Phase 0** | 0-A `open_question` 실행 | ❌ **0/119.** 템플릿은 고쳤으나(`c82a09f`) 검증 못 함 |
| | 0-B 병합 판정기 복구 | ⚠️ **한 번 살았다가 되돌아감** (아래 §2) |
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
| | 배포 자동화 | ⚠️ `cb5b7bf` — **작동 미검증** (아래 §2) |

정리하면 **Phase 1 은 완료, Phase 0 은 미완, Phase 2 는 근거 패널만.**
사용자 체감으로 크게 바뀐 부분(표 레이아웃·타이포)은 계획서에 없던 항목이다.

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

### 2-1. 병합 판정기가 다시 죽었다 (내가 만든 회귀)

라이브 `issue_audit.json` 실측 (17:18 KST):

```
remote_embedding_selected_count : 0     ← 오늘 낮에는 132 였다
embedding_cache_entries         : 0     ← 오늘 낮에는 332 였다
llm_review.status               : no_candidates (calls 0)
review_candidates               : 40건 전부 pending / similarity=null
이슈 수                          : 108 → 119 (클러스터링이 나빠져 잘게 쪼개짐)
article_count>=2                : 18 → 8
```

**원인은 데이터가 아니라 내 배포 방식이다.** `embeddings.json` 은 git 이 아니라
Actions 캐시에 사는데, 로컬에서 `wrangler pages deploy` 로 올리면서 캐시 없이
빌드한 산출물이 CI 가 만든 정상 빌드를 덮었다(빌드 로그에 `exists: false`).

**자가 치유된다** — 다음 CI 빌드(crawl 짝수 UTC시, 또는 `deploy-web.yml`)가
캐시를 복원해 다시 만든다. 확인:

```bash
curl -s "https://nuclens.pages.dev/data/issue_audit.json?cb=$(date +%s)" \
  | python -c "import json,sys;d=json.load(sys.stdin);print(d['embedding_cache_entries'], d['llm_review'])"
```

`embedding_cache_entries` 가 300 대로 돌아오면 회복. **교훈: 로컬 수동 배포는
데이터까지 같이 덮는다. 화면만 보고 싶으면 로컬 서버를 쓸 것(§4).**

### 2-2. `deploy-web.yml` 이 실제로 돌았다는 증거가 없다

`cb5b7bf`(17:09:28)가 `paths` 에 걸리는 파일을 건드렸으므로 배포가 돌았어야
하는데, 라이브 빌드는 **16:55:40**(내 수동 배포) 그대로다. 9분 넘게 변화 없음.
같은 시간대 `08:00 UTC` crawl 도 흔적이 없다(`chore: update bot state` 커밋 없음).

이 세션에서는 Actions 를 볼 수 없었다(repo 가 private, `gh` 미설치). 다음 세션이
**가장 먼저 확인할 것**:

```
https://github.com/policy174/nuclear-news/actions
```

- 실행 자체가 없으면 → Actions 가 멈춘 것. 메모리에 같은 관찰 있음
  ("매시간 cron 인데 3시간 가까이 실행 0회")
- 실행됐는데 실패면 → 로그에서 어느 스텝인지 확인. 의심 순서:
  `pip install` → `build_data.py`(embeddings 없이도 돌아야 함) → 웹 테스트 174개
  → `wrangler`(시크릿)
- Actions 탭에서 `Deploy web` → `Run workflow` 로 수동 실행해 보면 즉시 갈린다

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

시안의 5단계 경로 중 **2칸이 비어 있어 착수하지 않았다.** 두 시점 실측:

| 노드 | 필드 | 정상 빌드(108건) | 현재 회귀 상태(119건) |
|---|---|---|---|
| 오늘의 변화 | `latest_change` | 7 (6%) | 4 (3%) |
| 남은 질문 | `open_question` | **0 (0%)** | **0 (0%)** |
| 산업 영향 | `implication` | 68 (62%) | 72 (61%) |
| 관련 보도 N건 | `article_count >= 2` | 18 (17%) | 8 (7%) |
| 공식 출처 | `official_source_count > 0` | 7 (6%) | 7 (6%) |

**5칸이 전부 채워지는 이슈 0건.** 지금 그리면 100% 빈칸 그래프다.
오른쪽 열은 §2-1 의 embeddings 회귀 때문에 더 나쁘다 — **판단은 왼쪽 열로 하고,
회복 후 다시 재보고 착수 여부를 정할 것.**

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

## 4. 배포 — 자동화했으나 미검증

```
화면 코드(web/**) 를 main 에 병합  →  deploy-web.yml 즉시 배포 (1~2분)
데이터 갱신                        →  crawl.yml 짝수 UTC시 배포
```

`deploy-web.yml` 은 수집·LLM 호출이 없고 상태를 커밋하지 않는다. embeddings 캐시는
**복원만** 한다(주인은 `crawl.yml`). 배포 전에 `node --check app.js` + 웹 테스트 174개.

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
