# Signal Desk 개편 인계 (2026-08-03)

시안 `~/Downloads/nuclens_integrated_signal_atlas_v8.html` 이식 작업의 현재 지점.
계획서는 `~/.claude/plans/a-issue-atlas-cozy-lagoon.md` (rev.2), 이 문서가 그 뒤에
실제로 일어난 일이다.

기준선: **`origin/main = cb5b7bf`** · 테스트 봇 223 / 웹 174 · 라이브 반영 완료.

---

## 1. 끝난 것

| 시안 요소 | 상태 | 커밋 |
|---|---|---|
| 딥 포레스트 팔레트 | 배포됨 | `d6621aa` |
| 레이아웃·타이포그래피 | 배포됨 | `26c1d3e` |
| 본문 들여쓰기 제거 | 배포됨 | `7ee41d7` |
| 설명문 제거 · 서체 | 배포됨 | `76b3382` |
| 발간물 제외·기관명·요약 | 배포됨 | `32528c4` |
| 화면 변경 시 즉시 배포 | 배포됨 | `cb5b7bf` |
| 우측 상시 근거 패널 | `feat/atlas-p0-data` 에 있음, **미병합** | `4063caa` |
| 이슈 지도 (Atlas) | **미착수 — 데이터 부족** | — |

### 레이아웃에서 실제로 바뀐 것

- 이슈 목록이 카드 → **표 4열**(`56px 124px 1fr auto`). 상자·라운드를 걷고 행 사이 선 하나.
- `--r-1/2/3` = `0`. 시안 197줄에 `border-radius` 는 장식 원 1건뿐이다.
- h1 `clamp(32px, 4vw, 58px)` / `-.05em` / 800. 시안은 82px 이지만 그건 짧은 문장 기준이고
  여기 h1 은 `daily_lead` 종합 문장(최대 70자)이라 82px 면 다섯 줄이 된다.
- 히어로가 어두운 박스 → 캔버스 위 대형 타이포. `status-strip` 이 헤더 위 모노 바로.
- 오버라인·번호는 `--ff-mono`(시스템 스택). **IBM Plex Mono 는 추가하지 않는다.**
  12.5px 하한도 낮추지 않았다 — 모노·자간·색으로 구분.

---

## 2. 다음에 할 것 (우선순위)

### A. `feat/atlas-p0-data` 정리 — 병합 여부 판단

`4063caa`(근거 패널)와 `7eb952e`(큐레이션 잔해 제거)가 미푸시 브랜치에 남아 있고,
같은 워크트리(`my-projects/nuclens-upgrade`)에 **다른 세션의 미커밋 작업**
(`news_bot.py`·`gemini_client.py` batch 잘림 수리)이 얹혀 있다.

근거 패널은 이미 라이브에서 보인다 — `26c1d3e` 가 `feat/atlas-p0-data` 위에서
cherry-pick 된 것이라 패널 CSS·마크업이 같이 넘어왔기 때문이다. **브랜치의 남은
차이는 `news_bot.py` 쪽 뿐**이므로, 그 작업의 주인 세션이 끝낸 뒤 별도로 병합한다.

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

시안의 5단계 경로 중 **2칸이 비어 있어 착수하지 않았다.** 실측(108건 기준):

| 노드 | 필드 | 보유율 |
|---|---|---|
| 오늘의 변화 | `latest_change` | 7/108 (6%) |
| 남은 질문 | `open_question` | **0/108 (0%)** |
| 산업 영향 | `implication` | 68/108 (62%) |
| 관련 보도 N건 | `article_count >= 2` | 18/108 (17%) |
| 공식 출처 | `official_source_count > 0` | 7/108 (6%) |

**5칸이 전부 채워지는 이슈 0건.** 지금 그리면 100% 빈칸 그래프다.

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

## 3. 배포 — 이제 자동이다

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

## 4. 이번에 데인 것

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
