# NUCLENS 단일 이슈 5장 카드뉴스

기존 매일 발송(`make_cards.py` → `build.js` → `send_album.py`)과 별개인 **수동 제작 모드**입니다. 자동 발송 경로, 기존 테마, 기존 카드 장수는 바꾸지 않습니다.

## 만들기

```powershell
cd cards
npm run build:story -- story.example.json
```

결과: `cards/story-out/story-01.png` ~ `story-05.png` (각 1080×1080). 특정 장만 확인하려면 `--only 1`, 출력 폴더를 바꾸려면 `--out-dir <경로>`를 붙입니다. 이 명령은 **PNG만 생성**하며 게시·발송하지 않습니다.

## 매번 바꾸는 내용

`story.example.json`을 복사해 이슈별 브리프를 작성합니다. 스타일은 `story_build.js`에 고정되어 있습니다.

| 장 | 입력 필드 | 역할 |
| --- | --- | --- |
| 1 | `category`, `title`, `subtitle`, 선택 `coverImage` | 표지·한 줄 핵심 |
| 2 | `timeline` 2~4개 | 확인된 사실의 흐름 |
| 3 | `issues` 2~3개 | 핵심 쟁점 |
| 4 | `importance.figure`, `headline`, `points` 1~3개 | 왜 중요한가 |
| 5 | `watch` 2~5개, 선택 `closingNote` | 앞으로 확인할 것 |

`date`, `source`, `sourceUrl`도 필수입니다. 수치를 억지로 채우지 말고, 확인된 값이 없으면 `figure`에 짧은 핵심어를 씁니다. 후속 관찰점도 근거 없는 전망을 사실처럼 쓰지 않습니다.

표지 사진은 브리프 JSON과 같은 폴더를 기준으로 한 PNG/JPG/WebP 상대 경로를 넣습니다. 사진이 없으면 타이포 중심 표지를 만듭니다. 이미지를 넣으면 `imageCredit`도 필수입니다. 생성·연출 이미지를 쓸 때는 실제 사건 현장 사진으로 오인되지 않게 `연출 이미지`라고 표기하고, 이슈에 맞는 이미지만 사용합니다. 예제 이미지는 특정 시설의 실제 사진이 아닙니다.

### Pexels 사진 사용

Pexels API 키는 Git에서 제외된 `cards/pexels_api_key.txt`에 한 줄로 붙여넣거나 `PEXELS_API_KEY` 환경변수로 설정합니다. 키를 채팅·브리프 JSON·커밋에 넣지 않습니다. [Pexels API 문서](https://www.pexels.com/api/documentation/)의 `search` 엔드포인트를 사용합니다.

브리프에서 `coverImage`와 `imageCredit`을 지우고 `"pexelsQuery": "research laboratory"`를 넣습니다. 기본적으로 세로 사진 검색 결과의 첫 사진을 씁니다. 마음에 드는 Pexels 사진을 이미 골랐다면 검색어 대신 `"pexelsPhotoId": 12345`로 정확히 지정할 수 있습니다. 사용한 사진 파일은 `story-out/pexels-<id>.jpg`, 작가·사진 링크는 `story-out/pexels-credit.json`에 보존하고 표지 하단에도 작가명과 Pexels를 표시합니다. 검색 결과가 사건 현장이나 해당 시설이라는 보장은 없으므로, 게시 전 사진의 맥락을 반드시 확인하세요.

렌더러는 HTML 특수문자 이스케이프, 입력 구조, 폰트 로딩, 캔버스 크기 및 기본 오버플로를 검사합니다. 최종 게시 전에는 **원문 대조와 5장 육안 검수**가 필요합니다. 특히 제목·날짜·수치·사진 적합성을 확인하세요.
