# Nuclens 웹 — 원자력 정책·산업 이슈 트래커

`nuclear-news-bot` 이 모은 아카이브를 읽어 정적 사이트(https://nuclens.pages.dev)를
빌드한다. 백엔드가 없고, 빌드 산출물은 `public/data/*.json` 이다.

## 구조

| 경로 | 역할 |
|---|---|
| `build_data.py` | 아카이브·발송기록 → 화면용 JSON 9종 + 이슈 상세 페이지 + RSS |
| `public/index.html`·`app.js`·`style.css` | 단일 페이지 앱 (의존성 0) |
| `public/data/` | 빌드 산출물 (gitignore — CI 가 매번 생성) |
| `brand/` | 브랜드 개편안·토큰·로고 원본 (배포 대상 아님) |
| `tools/make_og_image.py` | 링크 미리보기 이미지 생성 (stdlib) |
| `tests/` | 단위·데이터 검증 테스트 |

화면은 5개 — 오늘 브리핑 / 이슈 아카이브 / 주간 흐름 / 발간물 / 저장.

## 데이터 계약

- 빌드는 `news.json`·`briefings.json`·`issues.json`·`trend.json`·`meta.json`·
  `insights.json`·`publications.json`·`issue_audit.json`·`manifest.json`·`status.json`
  을 **항상** 쓴다. 수집 결과가 0건이어도 빈 구조로 쓴다 — 앱이 없는 JSON 을
  만나면 화면 전체가 죽는다(2026-08-01 실사고).
- `app.js` 에서 새 JSON 을 불러올 때는 반드시 `.catch()` 로 감싼다.
- `validate_archive_records()` 가 URL 중복·출처 등급·요약 완결성을 검사하고,
  위반이 있으면 **배포 전에 빌드를 실패시킨다**.

## 이슈 병합

임계값 하나로는 "같은 사건"과 "같은 분야"가 갈리지 않는다. 코사인 0.92 이상만
자동 병합하고, 0.88~0.92 회색지대는 `issue_review.py` 가 LLM 배치 1회로 판정한다.
판정 실패·키 부재는 **병합하지 않는다** — 잘못된 병합은 누락보다 해롭고,
검증 배지("복수 출처 확인")까지 위조한다.

사람 검토가 필요한 쌍은 `public/data/issue_audit.json` 의 `review_candidates` 에
남는다. 판단한 쌍은 `issue_match_overrides.json` 의 `approved`/`rejected` 에 두
해시와 근거를 기록하면 다음 빌드부터 재현된다.

KEEI 세계 원전시장 인사이트 목차와 이슈를 잇는 판정도 같은 구조다
(`keei_match.py`) — 파이썬이 후보를 좁히고 LLM 이 판정한다.

## 빌드

```bash
BOT_DIR=/path/to/nuclear-news-bot python web/build_data.py
```

`BOT_DIR` 를 생략하면 저장소 루트를 쓴다. CI 는 `crawl.yml`(짝수 UTC시)과
`daily-brief.yml`(하루 1회)에서 빌드 후 `wrangler pages deploy web/public` 한다.

## 로컬 실행

`fetch()` 로 JSON 을 읽으므로 `index.html` 을 직접 열지 말고 로컬 서버를 쓴다.

```bash
python -m http.server 8765 -d web/public
```

## 테스트

```bash
cd web && python -m unittest discover -s tests
```

`app.js` 를 고쳤다면 **먼저** 구문 검사를 돌린다. 파싱이 깨지면 화면은
"불러오고 있습니다"에서 멈추는데 콘솔에 에러가 안 잡혀 데이터 문제로 오진하기 쉽다.

```bash
node --check web/public/app.js
```

라이브 렌더링 검증(브라우저 실행, `daily-brief.yml` 에서 하루 1회):

```bash
node web/tests/render_smoke.mjs
```

## 링크 미리보기 이미지

`public/og-image.png` 는 손으로 만든 바이너리가 아니라 스크립트 산출물이다.
브랜드 색·심벌이 바뀌면 상수만 고쳐 다시 돌린다.

```bash
python web/tools/make_og_image.py
```
