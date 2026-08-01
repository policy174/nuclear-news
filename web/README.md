# 원자력 뉴스 웹 — 이슈 중심 프로토타입

원본 프로젝트를 수정하지 않고 별도로 만든 이슈 중심 프로토타입입니다.

## 반영한 내용

- 기사 발행일(`article_date`)과 브리핑 발송일(`briefing_date`) 분리
- `delivery_log.jsonl`의 점수 내역을 사람이 읽는 선정 이유로 변환
- 제목·요약·태그 유사도로 같은 브리핑 이슈를 보수적으로 묶음
- 브리핑을 기사 목록 대신 이슈 카드와 관련 기사 타임라인으로 표시
- 요약과 의미를 접지 않고 기본 노출
- 긴 흐름 해석을 `완결형 한줄 흐름 → 구성 사건 3개 → 전체 해석 펼침`으로 압축
- 날짜를 넘겨 연결된 이슈에 추적 횟수와 이번 브리핑·누적 기사 수 표시
- `gemini-embedding-2` 캐시와 로컬 문자 n-gram 후보 벡터를 함께 쓰는 21일 하이브리드 이슈 매칭
- Gemini 캐시는 최근 브리핑 기사를 별도로 백필하고, 없을 때도 로컬 후보 벡터가 선택 기사 전체를 커버
- 자동 병합 아래 임계값은 `issue_audit.json`의 검토 큐로 보내고 `issue_match_overrides.json`의 승인·거절만 반영
- 국가·시설 충돌은 자동·수동 승인 모두 병합 차단
- 추적 이슈에 `이번 브리핑에서 새로 확인된 것` 한 문장 표시
- 빌드 시 `/rss.xml` 생성, 이슈 카드에서 보고서용 개조식 텍스트 복사
- 주간 핵심 흐름은 신호 강도·근거 기사 중복·국내외 커버리지를 함께 계산해 3개 선정
- 흐름마다 `국내`, `해외`, `국내·해외` 범위와 지역별 근거 기사 수 표시
- 이슈 카드의 `이슈 흐름 보기`에서 현재 핵심과 전체 기사 타임라인 제공
- 열린 이슈는 URL의 `issue` 값으로 보존해 같은 화면을 다시 열 수 있음
- `전체 검색` 탭에서 날짜 중복을 제거한 고유 이슈를 기관·시설·주제·지역으로 검색
- 전체 검색 조건은 URL의 `aq`, `ar`, `at` 값으로 보존하고 결과는 20개씩 확장
- 기관·시설·요약 검색과 통제 주제 필터 추가
- 날짜·지역·주제·검색 상태를 URL에 보존
- 기존 제목·태그를 이용한 사본 전용 통제 주제·국가 로컬 백필
- 백필 커버리지를 충족한 경우에만 정량 차트를 노출
- 원본 파일 변경을 60초마다 감지하는 자동 갱신 watcher
- 로컬 사이트 서버와 watcher를 한 번에 실행하는 운영 시작 스크립트
- 세대별 빌드·품질 검증 후 `manifest.json`만 원자적으로 전환
- 빌드 실패 시 마지막 정상 세대 유지 및 화면 상태 안내
- 일시적인 서버 연결 실패 시 화면에서 5초 간격으로 자동 복구

이슈 유사도 판정에 실패하거나 확신이 부족하면 기사를 합치지 않습니다. 따라서
잘못된 병합으로 기사가 사라지는 대신 단독 이슈 카드로 남습니다.

검토 큐는 `public/data/issue_audit.json`의 `review_candidates`에서 확인합니다.
같은 사건으로 확인한 쌍은 `issue_match_overrides.json`의 `approved`, 다른 사건은
`rejected`에 두 해시와 검토 근거를 기록합니다. 다음 빌드부터 결정이 재현됩니다.
RSS 구독 주소는 배포 도메인의 `/rss.xml`입니다.

통제 태그 백필은 디자인과 정보구조를 검증하기 위한 `prototype-heuristic-v1`입니다.
원본 아카이브에는 쓰지 않으며, 운영 분류기로 교체할 때는 고정 회귀 fixture와 자동
품질 게이트로 기존 분류가 되돌아가지 않는지 확인합니다.

## 운영 자동화

현재 자동화는 원본 뉴스봇을 읽기만 합니다. 새 데이터는
`public/data/generations/<generation_id>`에 먼저 생성되고, 검증을 통과한 세대만
`public/data/manifest.json`을 통해 공개됩니다. 실패 시 기존 manifest는 바뀌지 않습니다.

사이트 서버와 자동 갱신 시작:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\start_automation.ps1
```

즉시 한 번 갱신:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\refresh_now.ps1 -Force
```

자동 갱신 중지:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\stop_automation.ps1
```

운영 파일:

- `public/data/manifest.json`: 현재 공개 중인 정상 세대
- `public/data/status.json`: 마지막 시도·성공 시각과 watcher 상태
- `runtime/watcher.log`: 정상 확인·갱신 로그
- `runtime/watcher-error.log`: 오류 로그
- `runtime/watcher.pid`: 실행 중인 watcher PID
- `runtime/server.log`: 로컬 사이트 요청 로그
- `runtime/server-error.log`: 로컬 사이트 서버 오류 로그
- `runtime/server.pid`: 관리형 로컬 사이트 서버 PID

watcher는 현재 Windows 세션에서 계속 실행됩니다. 이 프로토타입 경로가 최종 배포
위치로 확정되기 전에는 재부팅 시 자동 시작하는 예약 작업은 등록하지 않습니다.
`runtime/cloudflared.exe`가 설치된 현재 사본에서는 같은 시작 명령이 임시 공개 터널도
함께 확인하고 실행합니다.

## 임시 온라인 공개

Cloudflare 계정 없이 현재 로컬 서버를 임시 공개하려면 다음 명령을 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\start_public_tunnel.ps1
```

공개 주소는 `runtime/tunnel-url.txt`에 기록됩니다. PC·로컬 서버·터널 프로세스가
실행 중인 동안만 유지되며 재시작하면 주소가 달라질 수 있습니다. 중지 명령:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\stop_public_tunnel.ps1
```

영구 운영 주소에는 Cloudflare Pages나 Git 연동 배포를 사용하세요.

## 개발용 직접 빌드

PowerShell:

```powershell
$env:BOT_DIR='C:\Users\USER\.claude\my-projects\nuclear-news-bot'
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\build_data.py
```

직접 빌드는 `public/data`의 호환용 파일을 만들 뿐 현재 manifest를 전환하지 않습니다.
운영 갱신에는 `refresh_now.ps1`을 사용하세요.

## 로컬 실행

`fetch()`로 JSON을 읽으므로 `index.html`을 직접 열지 말고 로컬 서버를 사용합니다.
운영 자동화를 시작했다면 서버도 함께 실행되므로 별도 명령은 필요하지 않습니다.
서버만 임시로 직접 띄울 때는 아래 명령을 사용할 수 있습니다.

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m http.server 8765 -d .\public
```

브라우저에서 `http://127.0.0.1:8765/`를 엽니다.

## 테스트

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -v
```

테스트는 다음을 확인합니다.

- 표현이 다른 12차 전기본 기사가 같은 이슈로 묶이는지
- 서로 다른 안전 사건은 합쳐지지 않는지
- 전날 기사가 최신 브리핑에서 누락되지 않는지
- 모든 발송 기사가 정확히 한 번 이슈 카드에 포함되는지
- 선정 이유가 최대 두 개인지
- 불완전한 트렌드 차트가 노출되지 않는지
- 실패한 빌드가 이전 정상 manifest를 바꾸지 않는지
- manifest가 가리키는 세대의 모든 JSON이 서로 일관적인지
