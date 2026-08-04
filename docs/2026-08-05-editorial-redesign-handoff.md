# 에디토리얼 리디자인 핸드오프 (2026-08-05)

> 계획: `~/.claude/plans/nuclens-ux-ui-pure-kay.md` (rev.3) · 착수 감사: `docs/redesign-audit.md`
> 목표: "브리핑 뷰어" → ①오늘 핵심 빠른 파악 ②원전·기업·국가·기관·주제 검색·추적하는 정보 제품.
> 레퍼런스 반영: Stripe Press(구조·타이포·여백·물성) / Arqé(번호·위계·호버) / Complete Shelf(발간물 한정).
> 팔레트·라운드 0·N 마크 등 기존 결정은 전부 유지 — 색 hex 추가 0.

## 1. 병합 기록 (6병합 · 커밋 12개)

| 병합 | 커밋 | 내용 | 비고 |
|---|---|---|---|
| M1 | C1 `docs` · C2 `style`(토큰 정의) · C3 `style`(이관+preload) | 감사 문서 + 토큰 4계(--sp/--t/--mo/--z) | 시각 변화 = 뷰 h1 +2px 뿐. 12.5px 가드가 토큰 정의값·clamp 최소값까지 검사하도록 확장 |
| M2 | C4 `feat` 오늘 1면 | 마스트헤드 헤어라인·리드 국가 칩·front 정확 2건·표 재조판(번호 28px·첫 행 2px 괘선) | 폴드 ②(다음 이슈 제목)는 리드 정보량 우선 규칙으로 면제 — 감사 문서에 실측 기록 |
| M3 | C5 `feat` 레지스트리 · C6 `feat` 탐색 | entity_registry.json 64건 → entities.json + 발견 허브 + ent 딥링크 | 병합 사이 타 세션의 오디오 브리핑이 main 에 들어와 리베이스로 흡수 |
| M4 | C7 `feat` 검색 · C8 `feat` 흐름 | 즉시 그룹 결과·키보드·최근 검색 + 지난 브리핑 목록 | 검색 실측 중앙값 1.2~2.0ms |
| M5 | C9 `feat` 발간물 서가 | CSS 타이포그래피 표지(스파인=장식), 모바일 1열 | 시각 변화 커서 단독 병합 |
| M6 | C10+C11 `feat` 저장·팔로우 · C12 `polish` · C13 `docs` | 저장 데스크톱 탭·톰스톤·엔티티 팔로우·모션·내성 | C10·C11은 같은 파일에 맞물려 한 커밋(범위는 계획대로) |

배포는 전부 main 병합 → `deploy-web.yml` 자동(1~2분). 수동 wrangler 0회.

## 2. 새 데이터 계약

### entities.json (신규 11번째 산출물 — 상시 생성, 빈 구조 포함)
```json
{"generated_at": "...", "entities": [{"id": "khnp", "name_kr": "한국수력원자력",
  "name_en": "KHNP", "type": "company", "countries": ["KR"], "aliases": ["한수원", "khnp"],
  "issue_count": 3, "article_count": 4, "latest_issue_date": "2026-08-04", "issue_ids": ["issue-..."]}]}
```
- 정렬: issue_count ↓ → latest_issue_date ↓ → id ↑. 0건 엔티티 포함(허브 노출은 프론트가 거름).
- `aliases`는 클라이언트 검색용 공개, **`match_policy`는 레지스트리에만**(비공개).
- issues.json 각 행에 `entity_ids[]` additive. issue_audit.json 에 `entity_matches[]`
  최소 근거({issue_id, entity_id, matched_alias, source_field} — 원문 복제 없음).

### entity_registry.json 큐레이션 가이드 (저장소 루트, 커밋 대상)
- 필드: `id, name_kr, name_en, type(plant|company|org|project), aliases[], countries[], match_policy?`
- **원칙: 오탐 > 누락.** 의심스러우면 별칭을 좁히거나 정책을 조인다.
- 매칭: 한글 = 접두 일치+조사 꼬리 ≤3자(같은 토큰은 긴 별칭이 선점 — 한전기술이 한전을 이김).
  라틴 = 하이픈 보존 런 추출 후 정규화 완전 일치(부분 문자열 금지, 최소 3자).
- `match_policy`: `token`(기본) / `tag_only` / `tag_or_unit_adjacent`(고리·월성 등
  일반명사 충돌 — canonical_tag 정확 일치·`alias+원전` 태그·제목 'N호기' 인접만) /
  `title_only`(ARC 처럼 요약 오탐 위험 단어).
- 오탈자 별칭(테믈린·세르나보다·보글틀)은 과거 데이터 호환용 — 지우지 말 것.
- 수정 즉시 배포된다(deploy-web paths 에 등록됨).
- 빌드 스탯 경고 기준: 이슈당 평균 >4(범용어 오탐) / 연결 비율 <30%(별칭 부족) /
  단일 엔티티 40%+ 점유(범용어 오탐). 현재: 등록 64 → 활성 31 · 연결 49% · 평균 0.60.

## 3. localStorage 키 (전부 try/catch + 형태 검증)

| 키 | 내용 | 도입 |
|---|---|---|
| `nuclens-saved-issues` | 저장 이슈 id 배열 | 기존 |
| `nuclens-saved-meta` | id→{title, last_seen} 저장 시점 스냅샷 (재클러스터 톰스톤용) | C10 |
| `nuclens-follows` | 팔로우 엔티티 id 배열 | C11 |
| `nuclens-follow-seen` | 엔티티별 확인일 {id: "YYYY-MM-DD"} — **저장 화면 진입으로 일괄 갱신 금지**(테스트 잠금). 갱신은 ①엔티티 페이지 열람 ②팔로우 시작 ③패널에서 그 대상 열기 | C11 |
| `nuclens-recent-searches` | 최근 검색 MRU 8 (1글자 미저장, 항목·전체 삭제 UI) | C7 |
| `nuclens-theme` | light\|dark (문자열, 파싱 없음) | 기존 |
| `nuclens-audio-rate` | 재생 배속 (타 세션 오디오 기능) | 8/4 타 세션 |

## 4. 토큰 시스템 (style.css :root)

- 간격 `--sp-1..6,8,10,12,14,16,20`(4px 배수, 중간 단계 없음. 44px 터치 타깃은 WCAG 상수라 리터럴).
- 타입 `--t-min(12.5px)·caption·body·body-lg·card·title·lead·hero` — **--t-hero 는 3ff0907
  히어로 축소 결정의 박제(clamp 23~30px). 58px 복원 금지(테스트 잠금).**
- 모션 `--mo-1(120ms)/2(200ms)/3(320ms) + --mo-ease(-out)` — JS 모션은 `prefersReducedMotion()` 헬퍼 필수.
- z `--z-pop..--z-skip`(기존 서열 이관).
- 12.5px 가드는 리터럴 + `--t-*` 정의값 + clamp 최소값을 함께 검사한다 — 토큰 우회 불가.

## 5. 신규 테스트 클러스터 (웹 197 → 257)

`TokenSystemTests`(4) · `ExploreHubTests`(10) · `SearchDialogTests`(7) ·
`BriefingTimelineTests`(5) · `PubShelfTests`(7) · `SavedFollowTests`(6) ·
`MotionTests`(3) + `web/tests/test_entities.py`(17). 기존 잠금(오버라인 2종·N 마크·
팔레트·브레이크포인트·상세 순서·빈 상태 4종 등) 전부 보존 — assertion 약화 0.

## 6. 이연 항목 (이번 범위 밖)

- 주제·국가 팔로우 (팔로우는 엔티티 한정으로 출시 — 실사용 후)
- 엔티티 30일 등장 횟수·`모두 확인` 버튼
- 발간물 신규 도트의 방문 기준 전환(현재 is_new 14일 + '최근 발간' 텍스트)
- 레지스트리 오탐 1주 관찰 → match_policy 보강 (`issue_audit.json → entity_matches` 로 디버깅)
- briefings.json 이 issues.json 을 통째 중복(~370KB) — 데이터 계약 개편 시
- S3 문자열 786건 외부화 — 신규 문구는 `STRINGS` 상수에 봉쇄해 부채 증가 0
- 폰트 preload 저속 회선 waterfall 실측 — 회귀 시 index.html 한 줄 제거

## 7. 운영 메모

- 로컬 미리보기: `python -m http.server 8917 --bind 127.0.0.1 --directory <worktree>/web/public`
  (8791 등 다른 세션 서버와 포트 충돌 주의 — 오래된 서버가 응답하면 curl 로 t-hero 존재 확인).
- gstack browse 는 app.js·style.css 를 힙하게 캐시한다 —
  `fetch('/app.js',{cache:'reload'})` 후 reload 로 강제 갱신.
- `/data/audio/audio.json` 404 는 정상(선택 파일, daily-brief TTS 가 만들 때만 존재).
