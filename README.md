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

`GEMINI_MODEL` 은 Repository **Variable** (기본 `gemini-2.5-flash`).

## 로컬 테스트

```bash
python daily_brief.py --dry-run        # 발송 없이 브리핑+점수 내역 출력
python -m unittest discover tests -v   # 테스트 (외부 호출 0)
python metrics.py                      # 품질 지표
```

## 롤백

- 랭킹만 되돌리기: `ranking_config.json` 을 이전 커밋으로.
- 전체 롤백: 이 커밋 이전으로 revert. 옛 큐 JSON 은 새 코드가 그대로 읽고(features
  없으면 기존 점수식), 새 큐 JSON 의 추가 필드는 옛 코드가 무시하므로 양방향 안전.
- outbox 꼬임: `outbox.json` 삭제 후 daily-brief 워크플로 수동 실행 (그날 큐 기준 재계획).
