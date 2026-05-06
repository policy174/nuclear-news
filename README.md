# 원자력 뉴스 봇

네이버 뉴스 검색 → 텔레그램 채널 발송. 3시간마다 GitHub Actions로 자동 실행.

## 설정 순서

### 1. GitHub 리포지토리 생성
- github.com 로그인 → 우측 상단 `+` → `New repository`
- 이름: `nuclear-news-bot` (아무거나)
- **Private** 선택 (공개해도 키는 안전하지만 굳이)
- `Create repository`

### 2. 코드 업로드
방법 A — GitHub 웹에서 드래그 앤 드롭:
- 빈 리포지토리 페이지의 `uploading an existing file` 링크 클릭
- 이 폴더의 모든 파일 드래그 (`.github` 폴더 포함)
- `Commit changes`

방법 B — Git 명령어:
```bash
cd C:\Users\USER\nuclear-news-bot
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<본인계정>/nuclear-news-bot.git
git push -u origin main
```

### 3. Secrets 등록
리포지토리 → `Settings` → 좌측 `Secrets and variables` → `Actions` → `New repository secret`

4개 등록:
| Name | Value |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 개발자센터 발급 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 Client Secret |
| `TELEGRAM_BOT_TOKEN` | BotFather 토큰 |
| `TELEGRAM_CHAT_ID` | `-1003955025609` |

### 4. 첫 실행
- `Actions` 탭 → `Nuclear news crawl` → `Run workflow` → `Run workflow` 버튼
- 1~2분 후 채널에 메시지 도착 확인
- 실패 시 Actions 로그에서 에러 확인

이후 3시간마다 자동 실행됨 (UTC 기준 0,3,6,9,12,15,18,21시 = KST 9,12,15,18,21,0,3,6시).

## 키워드 수정
`keywords.json` 편집 후 커밋하면 다음 실행부터 반영.

## 동작 방식
- 각 키워드마다 네이버 뉴스 검색 (최근 30건, 날짜순)
- 최근 6시간 내 기사만 (중복 실행/지연 대비 여유)
- `sent.json`에 기록된 URL은 재발송 안 함 (14일간 보관)
- 텔레그램으로 `[정책]` / `[SMR]` 태그 붙여 발송
