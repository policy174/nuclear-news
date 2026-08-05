# 원자력 뉴스봇 (nuclear-news)

해외 전문지 RSS·한국 기관 보도자료·Google News·ANS 이메일 뉴스레터에서 원자력 뉴스를
매시간 수집하고, 매일 아침 텔레그램으로 **국내/해외 투자 관점 카드 브리핑**을 보낸다.
금요일엔 주간 판세 리포트.

## 파이프라인

```
crawl (매시간)          news_bot.py    RSS·Naver·이메일 수집 → dedup → Gemini batch
                                       큐레이션(+랭킹 feature) → digest_queue.json 적재
daily-brief (07:25 KST) daily_brief.py --plan/--send/--confirm
                                       랭킹(ranking.py) → 카드 브리핑 발송 (outbox 원자성)
weekly (금 17:00 KST)   weekly_bot.py  주간 판세 (정책 변화·테마 강약·watchlist)
```

## 파일 구조

| 파일 | 역할 |
|---|---|
| `news_bot.py` | 수집·dedup·batch 큐레이션 (Gemini 1회/10건, feature 추출 포함) |
| `data_quality.py` | URL·발행처·출처 역할·완결문·사건일 공통 품질 계약 |
| `embedding_pipeline.py` | Gemini 임베딩 모델·35일 캐시·최근 21일 브리핑 백필 계약 |
| `news_archive.py` | v2 아카이브 적재·중복 차단·품질 이관 |
| `archive_repairs.json` | 과거 깨진 레코드의 고정 회귀 수선·제외 근거 |
| `policy_web_sync.py` | 최근 뉴스 JSONL을 정책 웹에 멱등 upsert (`--all` 최초 이관) |
| `policy_monitor.py` | 공식 HTML/PDF 해시 변경 감지 → 검토 후보 등록 |
| `daily_brief.py` | 일일 브리핑: 랭킹→투자 관점(구조화)→보고서 추천→발송 |
| `weekly_bot.py` | 주간 판세 리포트 (Gemini 주 1회 1호출) |
| `ranking.py` + `ranking_config.json` | 설명 가능한 점수식 — **가중치는 JSON 만 편집** |
| `metrics.py` | 오프라인 품질 지표 (`python metrics.py`) — 표본 부족 시 insufficient_data |
| `gemini_client.py` | Gemini REST wrapper (429 백오프) |
| `telegram_send.py` | 텔레그램 발송 (inline keyboard 지원) |
| `sources.py` + `sources.json` | 출처 공신력 tier — JSON 만 편집 |
| `email_ingest.py` | ANS Nuclear News Daily 뉴스레터 외부 링크 추출 (IMAP) |
| `reports_kb.json.example` | 과거 보고서 KB 템플릿 — 채우면 보고서 추천 정밀화 |
| `keywords.json` | Naver 검색 키워드 — JSON 만 편집 |
| `dedup.py` `scorer.py` `synthesize.py` `send_research.py` | 소셜(last30days) 경로 — 수동 실행 전용 |

## 상태 파일 (git 이 DB)

| 파일 | 내용 |
|---|---|
| `sent.json` | 수집 dedup (URL hash, 14일 보존) |
| `curated.json` | 큐레이션 캐시 (14일) — weekly 의 입력 |
| `digest_queue.json` | 발송 대기 큐 (발송분만 hash 단위 제거, 3일 자동 정리) |
| `outbox.json` | 오늘의 발송 계획·상태 (pending/sent/failed) — 중복 발송 방지 핵심 |
| `delivery_log.jsonl` | 발송 이력 + 점수 내역(breakdown) — "왜 이 기사가 올라왔나" |

## 발송 원자성 (outbox 패턴)

`--plan`(선별·outbox 기록·큐 정리) → **claim push** → `--send`(pending 만 발송) →
`--confirm`(결과·delivery_log push). claim push 가 실패하면 발송 자체를 안 한다 →
"발송했는데 상태 저장 실패 → 다음날 중복" 문제 제거. 같은 날 재실행하면 sent 브리핑은
건너뛴다. 36시간 지난 pending 은 재발송하지 않는다(stale_skipped).

## 랭킹 조정 (비개발자용)

1. `ranking_config.json` 열기 — 모든 가중치에 한국어 설명 주석이 있다.
2. 숫자 수정 → commit → 다음 브리핑부터 적용. 코드 수정 불필요.
3. "왜 이 기사가 뽑혔지?" → `delivery_log.jsonl` 의 `breakdown` 확인.
4. 지표는 `python metrics.py`. (피드백 버튼 기능은 2026-07-16 완전 삭제 — git 히스토리 참조.)

## Secrets (GitHub Actions)

| 이름 | 필수 | 용도 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | ✅ | 발송·피드백 수거 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | ✅ | 국내 뉴스 검색 |
| `GEMINI_API_KEY` | ⭕ | 없으면 큐레이션·투자관점 생략(fallback 발송) |
| `IMAP_USER` / `IMAP_PASSWORD` | ⭕ | ANS 뉴스레터 수집 (Gmail 앱 비밀번호, 공백 제거) |
| `POLICY_INGEST_TOKEN` | ⭕ | 정책 웹 연계 Bearer token. 미설정 시 뉴스 동기화만 스킵 |

`GEMINI_MODEL`(기본 `gemini-2.5-flash`)과 `POLICY_WEB_URL`은 Repository **Variable**입니다.

## 정책 웹 연계

- 시간별 crawl 뒤 최근 7일을 재전송한다. 정책 웹 장애는 `continue-on-error`라 기존 뉴스·텔레그램 운영에 전파되지 않는다.
- `.github/workflows/policy-monitor.yml`은 매일 뉴스 신호 대상, 매주 전체 활성 소스를 확인한다.
- 공식 본문 해시가 달라진 경우에만 Gemini를 호출한다. 결과는 항상 검토 후보이며 승인 정책값을 직접 수정하지 않는다.
- 최초 이관: `python policy_web_sync.py --all`

## 로컬 테스트

```bash
python daily_brief.py --dry-run        # 발송 없이 브리핑+점수 내역 출력
python embedding_pipeline.py --window-days 21 --max-new 150  # 이슈 매칭 캐시 백필
python -m unittest discover tests -v   # 테스트 (외부 호출 0)
python metrics.py                      # 품질 지표
```

## 데이터 품질 게이트

- Google News RSS의 `source`를 우선하고, 없으면 제목의 `- 매체명` 꼬리에서 발행처를 복원한다.
- URL은 추적 파라미터와 이중 슬래시만 정규화하며 기사 식별용 일반 쿼리는 유지한다.
- `/Error/` 경로, 정규화 URL 중복, 제목 완전일치 중복은 저장 전에 차단한다.
- 요약은 80자 이내 완결문이어야 하며 실패 항목만 한 번 재생성한다. 재실패 항목은 격리한다.
- `source_type`과 `evidence_role`을 분리해 전문언론을 공식 원문으로 표시하지 않는다.
- 이슈 임베딩은 `gemini-embedding-2`로 생성하고 모델·차원·입력 지문이 다른 구형 캐시는 폐기한다.
- 신규 큐레이션은 `event_date`와 날짜 의미·정밀도·근거 필드를 함께 기록한다.

과거 아카이브 이관 미리보기와 적용:

```bash
python news_archive.py --migrate-quality
python news_archive.py --migrate-quality --apply
```

웹 빌드는 위 조건을 다시 검사하고 위반이 있으면 배포 전에 실패한다.

## 롤백

- 랭킹만 되돌리기: `ranking_config.json` 을 이전 커밋으로.
- 전체 롤백: 이 커밋 이전으로 revert. 옛 큐 JSON 은 새 코드가 그대로 읽고(features
  없으면 기존 점수식), 새 큐 JSON 의 추가 필드는 옛 코드가 무시하므로 양방향 안전.
- outbox 꼬임: `outbox.json` 삭제 후 daily-brief 워크플로 수동 실행 (그날 큐 기준 재계획).
