# 원자력정책실 동향봇

Reddit, X, YouTube 등 영어권 소셜 데이터에서 원자력 관련 핫이슈를 30일 간격으로 모아 텔레그램으로 자동 발송합니다.

[mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) 엔진 위에 구축됐습니다.

## 운영 방식

- **자동 실행:** 매일 평일 오전 7시 KST (GitHub Actions cron)
- **다중 키워드:** `keywords.json` 에 등록된 토픽별로 메시지 1개씩
- **품질 필터:** 신뢰 X 핸들 화이트리스트 + 참여도 하한선 + 노이즈 키워드 제외
- **수동 실행:** GitHub Actions 탭에서 `workflow_dispatch` 클릭

## 파일 구조

```
.
├── keywords.json              # 모니터링 키워드 설정
├── send_research.py           # 메인 파이프라인 (수집→dedup→발송)
├── telegram_send.py           # 텔레그램 API 래퍼
├── dedup.py                   # cross-topic 중복 제거 (URL 정규화 + LLM 의미)
├── gemini_client.py           # Gemini API 얇은 wrapper (stdlib only)
├── requirements.txt           # Python 의존성 (yt-dlp)
├── .github/workflows/
│   └── daily-news.yml         # GitHub Actions 스케줄
└── raw/                       # 검색 결과 raw 저장 (.gitignore 처리됨)
```

## 작동 단계

1. **Phase 1** — `keywords.json`의 토픽마다 last30days 엔진으로 검색·파싱·룰 기반 필터
2. **Phase 2** — 모든 토픽의 cluster를 합쳐 cross-topic dedup
   - URL 정규화 1차 (utm·트래커 제거 후 정확 일치)
   - Gemini가 "같은 사건" 의미 그룹핑 2차 (한 번의 API 호출)
   - 각 그룹의 boosted_score 최고치 cluster만 살아남고 나머지 제거
3. **Phase 3** — 토픽별로 메시지 포맷 후 텔레그램 발송

## 비밀 키 (GitHub Secrets)

저장소 설정에서 다음 값을 등록해야 합니다:

| 이름 | 필수 | 설명 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | ✅ | 메시지 받을 텔레그램 chat ID |
| `GEMINI_API_KEY` | ⭕ | Google AI Studio 발급 키. 없으면 dedup의 의미 단계는 스킵되고 URL 단계만 동작 |
| `X_AUTH_TOKEN` | ⭕ | X 인증 쿠키 (없으면 X 검색 스킵) |
| `X_CT0` | ⭕ | X CSRF 토큰 |

`GEMINI_MODEL` 은 Repository **Variable**(Secret 아님)로 지정 가능, 기본값 `gemini-2.0-flash`.

## 키워드 추가/수정

`keywords.json` 편집 → `git push` → 다음 실행부터 적용. 코드 수정 불필요.

```json
{
  "label": "새 토픽",
  "schedule": "weekly",
  "subqueries": ["짧은 검색어 1", "짧은 검색어 2"],
  "subreddits": "nuclear,energy"
}
```

## 로컬 테스트

```bash
# .env 파일에 토큰 설정 후
python send_research.py --topic "SMR 동향" --dry-run
```

## 라이선스

내부 운영용. last30days-skill 은 MIT.
