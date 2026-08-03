# UI 문자열 전수 목록 (Phase 0 부록)

`NUCLENS_SPEC.md` Phase 0 조사항목 4. Phase 2 문자열 외부화의 작업 대상 목록이다.

추출: `scratchpad/extract_strings.py` → `build_string_doc.py`.
따옴표 안 한글 리터럴(`app.js`·`build_data.py`) + 태그 텍스트·속성값(`index.html`).
주석 줄은 제외했다. 동일 파일·라인·문구 중복은 합쳤다.

> **읽는 법**: 아래 823건 중 **37건은 화면에 나가지 않는다** — `build_data.py`의 독스트링
> 27 · 콘솔 로그 9 · 정규식 상수 1. Phase 2의 실제 대상은 **786건**이다.
> 분류(`scratchpad/classify_strings.py`)는 `데이터 문장(빌드 시점)` 버킷 안에 섞여 있다.

| 파일 | 건수 |
|---|---:|
| `web/build_data.py` | 411 |
| `web/public/app.js` | 277 |
| `web/public/index.html` | 135 |
| **합계** | **823** |

| 분류 | 건수 |
|---|---:|
| 빈 화면 | 16 |
| 로딩·진행 | 10 |
| 오류·토스트 | 12 |
| 버튼·액션 | 43 |
| 라벨·본문 | 339 |
| 데이터 문장(빌드 시점) | 403 |

## 빈 화면 (16건)

| 파일:라인 | 문구 |
|---|---|
| `web/build_data.py:1447` | 오늘 새로 연결된 원자력 이슈가 없습니다 |
| `web/build_data.py:1903` | 후보 없음 |
| `web/public/app.js:589` | <p class="empty">오늘 판정할 이슈가 없습니다.</p> |
| `web/public/app.js:634` | 오늘은 브리핑 기준을 넘는 이슈가 없습니다 |
| `web/public/app.js:641` | 오늘 새로 확인된 브리핑 이슈가 없습니다 |
| `web/public/app.js:646` | 오늘은 새로 연결된 이슈가 없습니다 |
| `web/public/app.js:727` | <div class="empty-state"><strong>조건에 맞는 이슈가 없습니다</strong><p>주제나 지역 필터를 해제해 보세요.</p><button type="button" data-clear-briefing>필터 해제</button></div> |
| `web/public/app.js:760` | <p class="empty">이 날짜에 발행된 수집 기사가 없습니다.</p> |
| `web/public/app.js:815` | 검색어 없음 |
| `web/public/app.js:818` | <div class="empty-state"><strong>조건에 맞는 이슈가 없습니다</strong><p>기간을 30일로 넓히거나 주제 필터를 해제해 보세요.</p><button type="button" data-clear-archive>필터 해제</button></div> |
| `web/public/app.js:831` | <div class="empty-state"><strong>저장한 이슈가 없습니다</strong><p>카드의 저장 버튼을 누르면 이 브라우저에서 다시 볼 수 있습니다.</p><button type="button" data-go-view="search">이슈 아카이브 보기</butt… |
| `web/public/app.js:877` | <div class="empty-state"><strong>아직 수집된 발간물이 없습니다</strong><p>매일 새벽 IAEA·OECD NEA·IEA·EIA의 신규 발간물을 확인합니다.</p></div> |
| `web/public/app.js:901` | <div class="empty-state"><strong>이 기관의 발간물이 아직 없습니다</strong><p>다른 기관을 선택해 보세요.</p></div> |
| `web/public/app.js:1052` | <p class="empty">요약이 없습니다.</p> |
| `web/public/app.js:1196` | <p class="empty">조건에 맞는 키워드가 없습니다.</p> |
| `web/public/index.html:175` | 검색어 없음 |

## 로딩·진행 (10건)

| 파일:라인 | 문구 |
|---|---|
| `web/build_data.py:88` | 확인 중 |
| `web/public/app.js:193` | 확인 중 |
| `web/public/app.js:593` | <li class=\"empty\">주간 흐름을 준비하고 있습니다.</li> |
| `web/public/app.js:808` | 단일 출처·확인 중 |
| `web/public/app.js:1117` | <div class="empty-state"><strong>이번 주 흐름을 준비하고 있습니다</strong><p>분류가 완료되면 근거와 함께 표시합니다.</p></div> |
| `web/public/index.html:66` | 브리핑을 불러오고 있습니다 |
| `web/public/index.html:83` | 오늘의 핵심을 정리하고 있습니다 |
| `web/public/index.html:146` | 브리핑을 불러오는 중 |
| `web/public/index.html:185` | 단일 출처·확인 중 |
| `web/public/index.html:318` | 서비스 상태 확인 중 |

## 오류·토스트 (12건)

| 파일:라인 | 문구 |
|---|---|
| `web/build_data.py:254` | 중복·오류 URL·불완전 문장이 있으면 배포 빌드를 중단한다. |
| `web/build_data.py:411` | 브리핑 선정이 실패했습니다 |
| `web/build_data.py:414` | 브리핑 실행 기록을 찾지 못했습니다 |
| `web/build_data.py:1903` | 파이프라인 실패 |
| `web/build_data.py:2363` | 분리 {llm_stats['rejected']} 실패 {llm_stats['failed']} [{llm_stats['status']}] |
| `web/public/app.js:337` | 공유 링크를 만들지 못했습니다 |
| `web/public/app.js:357` | 수집 오류 |
| `web/public/app.js:635` | 검토한 후보 ${below}건은 기준에 미치지 못했습니다. |
| `web/public/app.js:1028` | 보고서용 텍스트를 복사하지 못했습니다 |
| `web/public/app.js:1034` | 자료 팩을 복사하지 못했습니다 |
| `web/public/app.js:1564` | <div class="wrap status-strip-inner"><span class="status-dot"></span><strong>데이터 연결 실패</strong><span>·</span><span>마지막 정상 데이터를 불러오지 못했습니다</span></div> |
| `web/public/app.js:1567` | <div class="error-state"><strong>데이터를 불러오지 못했습니다</strong><p>잠시 후 다시 시도해 주세요. 문제가 계속되면 알려주세요.</p><small>${esc(error.message)}</small><div><button type="button… |

## 버튼·액션 (43건)

| 파일:라인 | 문구 |
|---|---|
| `web/public/app.js:322` | 저장을 해제했습니다 |
| `web/public/app.js:322` | 이슈를 저장했습니다 |
| `web/public/app.js:334` | 이슈 링크를 복사했습니다 |
| `web/public/app.js:390` | 지금 보기 |
| `web/public/app.js:398` | 지금 보기 |
| `web/public/app.js:500` | 저장됨 |
| `web/public/app.js:500` | 저장 |
| `web/public/app.js:636` | <button type="button" data-go-view="search">이슈 아카이브에서 보기</button> |
| `web/public/app.js:735` | 필터 해제 (${activeFilters.length}) |
| `web/public/app.js:735` | 필터 해제 |
| `web/public/app.js:821` | 더 보기 |
| `web/public/app.js:821` | 더 보기 · ${matches.length - visible.length}개 남음 |
| `web/public/app.js:824` | 필터 해제 (${activeFilters.length}) |
| `web/public/app.js:824` | 필터 해제 |
| `web/public/app.js:1018` | 복사됨 |
| `web/public/app.js:1058` | 저장됨 |
| `web/public/app.js:1058` | 저장 |
| `web/public/app.js:1390` | 라이트 모드 켜기 |
| `web/public/app.js:1390` | 다크 모드 켜기 |
| `web/public/app.js:1538` | “${query}” 검색 |
| `web/public/index.html:33` | 본문으로 바로가기 |
| `web/public/index.html:53` | 통합 검색 |
| `web/public/index.html:58` | 다크 모드 켜기 |
| `web/public/index.html:74` | 브리핑 날짜 선택 |
| `web/public/index.html:86` | 달라진 이슈 보기 |
| `web/public/index.html:125` | 보기 방식 |
| `web/public/index.html:132` | 닫기 |
| `web/public/index.html:139` | 필터 해제 |
| `web/public/index.html:162` | 주간 흐름 전체 보기 → |
| `web/public/index.html:171` | 아카이브 검색 |
| `web/public/index.html:186` | 필터 해제 |
| `web/public/index.html:200` | 더 보기 |
| `web/public/index.html:245` | 근거 데이터 보기 |
| `web/public/index.html:253` | 근거 데이터 보기 |
| `web/public/index.html:260` | 근거 데이터 보기 |
| `web/public/index.html:269` | 저장한 이슈 |
| `web/public/index.html:269` | 이 브라우저에 저장한 이슈입니다. |
| `web/public/index.html:287` | 통합 검색 |
| `web/public/index.html:287` | 검색 닫기 |
| `web/public/index.html:288` | 기관, 호기, 주제로 검색 |
| `web/public/index.html:294` | 상태 닫기 |
| `web/public/index.html:298` | 이슈 상세 닫기 |
| `web/public/index.html:306` | 저장 |

## 라벨·본문 (339건)

| 파일:라인 | 문구 |
|---|---|
| `web/public/app.js:4` | 신규 건설 |
| `web/public/app.js:4` | 계속운전·재가동 |
| `web/public/app.js:5` | 핵연료주기 |
| `web/public/app.js:5` | 사용후핵연료·방폐 |
| `web/public/app.js:5` | 원전금융·투자 |
| `web/public/app.js:6` | 규제·인허가 |
| `web/public/app.js:6` | 전력시장·요금 |
| `web/public/app.js:6` | 데이터센터·AI 전력 |
| `web/public/app.js:7` | 핵융합 |
| `web/public/app.js:7` | 에너지안보·통상 |
| `web/public/app.js:7` | 후쿠시마·처리수 |
| `web/public/app.js:8` | 원전 운영 |
| `web/public/app.js:8` | 안전·사건 |
| `web/public/app.js:8` | 해체·폐로 |
| `web/public/app.js:9` | 산업 인력 |
| `web/public/app.js:9` | 원자력 정책 |
| `web/public/app.js:9` | 연구·기술 |
| `web/public/app.js:10` | 비발전 활용 |
| `web/public/app.js:14` | 한국 |
| `web/public/app.js:14` | 미국 |
| `web/public/app.js:14` | 캐나다 |
| `web/public/app.js:14` | 프랑스 |
| `web/public/app.js:14` | 영국 |
| `web/public/app.js:15` | 독일 |
| `web/public/app.js:15` | 스페인 |
| `web/public/app.js:15` | 세르비아 |
| `web/public/app.js:15` | 헝가리 |
| `web/public/app.js:15` | 루마니아 |
| `web/public/app.js:16` | 체코 |
| `web/public/app.js:16` | 폴란드 |
| `web/public/app.js:16` | 스웨덴 |
| `web/public/app.js:16` | 네덜란드 |
| `web/public/app.js:16` | 핀란드 |
| `web/public/app.js:17` | 슬로바키아 |
| `web/public/app.js:17` | 불가리아 |
| `web/public/app.js:17` | 우크라이나 |
| `web/public/app.js:17` | 벨기에 |
| `web/public/app.js:18` | 이탈리아 |
| `web/public/app.js:18` | 포르투갈 |
| `web/public/app.js:18` | 스위스 |
| `web/public/app.js:18` | 노르웨이 |
| `web/public/app.js:19` | 덴마크 |
| `web/public/app.js:19` | 일본 |
| `web/public/app.js:19` | 러시아 |
| `web/public/app.js:19` | 중국 |
| `web/public/app.js:19` | 아르헨티나 |
| `web/public/app.js:20` | 인도 |
| `web/public/app.js:20` | 호주 |
| `web/public/app.js:20` | 브라질 |
| `web/public/app.js:20` | 남아공 |
| `web/public/app.js:20` | 사우디아라비아 |
| `web/public/app.js:21` | 아랍에미리트 |
| `web/public/app.js:21` | 튀르키예 |
| `web/public/app.js:21` | 카자흐스탄 |
| `web/public/app.js:21` | 우즈베키스탄 |
| `web/public/app.js:22` | EU(유럽연합) |
| `web/public/app.js:22` | 유럽 |
| `web/public/app.js:22` | 글로벌 |
| `web/public/app.js:22` | 미분류 |
| `web/public/app.js:31` | 전체 |
| `web/public/app.js:33` | 전체 |
| `web/public/app.js:35` | 전체 |
| `web/public/app.js:36` | 전체 |
| `web/public/app.js:64` | ${name} 응답이 JSON이 아님 |
| `web/public/app.js:121` | ${Number(month)}월 ${Number(day)}일 |
| `web/public/app.js:153` | 당일 보도 |
| `web/public/app.js:154` | 전날 보도 |
| `web/public/app.js:155` | ${days}일 전 보도 |
| `web/public/app.js:160` | 출처 미상 |
| `web/public/app.js:190` | 공식 확인 |
| `web/public/app.js:190` | 규제기관 또는 사업자 공식 문서로 확인된 내용입니다 |
| `web/public/app.js:191` | 복수 출처 확인 |
| `web/public/app.js:191` | 재인용 관계를 제외한 독립 출처 2곳 이상이 일치합니다 |
| `web/public/app.js:192` | 단일 출처 |
| `web/public/app.js:192` | 독립 출처 1곳이 보도했습니다 |
| `web/public/app.js:193` | 아직 독립·공식 근거가 확인되지 않았습니다 |
| `web/public/app.js:235` | 근거 ${articleCount}건 |
| `web/public/app.js:236` | 독립 출처 ${state.independent_source_count}곳 |
| `web/public/app.js:237` | 공식 출처 ${state.official_source_count}건 |
| `web/public/app.js:348` | 정상 |
| `web/public/app.js:349` | 마지막 수집 ${timeLabel(refreshedAt)} · 오늘 수집 기사 ${briefing.article_count \|\| 0}건 · 연결된 이슈 ${briefing.issue_count \|\| 0}개 · 1차 출처 ${briefing.primary_source_coun… |
| `web/public/app.js:353` | 연결 끊김 |
| `web/public/app.js:354` | 마지막으로 불러온 ${timeLabel(refreshedAt)} 브리핑을 보고 있습니다 |
| `web/public/app.js:358` | 마지막 정상 수집 ${dateTimeLabel(state.systemStatus.last_success_at)} · ${state.systemStatus.message \|\| "원인을 확인하고 있습니다"} |
| `web/public/app.js:361` | 검증 중 |
| `web/public/app.js:362` | 새 데이터를 검증하고 있습니다 · 완료 전까지 마지막 정상 데이터를 표시합니다 |
| `web/public/app.js:365` | 수집 지연 |
| `web/public/app.js:366` | 자동 수집이 중지돼 있습니다 · 마지막 정상 수집 ${dateTimeLabel(state.systemStatus.last_success_at)} |
| `web/public/app.js:372` | <i aria-hidden="true"></i><span>${timeLabel(refreshedAt)} · 이슈 ${state.issues.length}</span> |
| `web/public/app.js:373` | 서비스 상태 ${lead} · 마지막 갱신 ${dateTimeLabel(refreshedAt)} |
| `web/public/app.js:390` | 새 브리핑이 추가됐습니다 |
| `web/public/app.js:398` | 새 브리핑이 추가됐습니다 |
| `web/public/app.js:408` | 전체 |
| `web/public/app.js:409` | 전체 |
| `web/public/app.js:412` | 전체 |
| `web/public/app.js:413` | 전체 |
| `web/public/app.js:415` | 전체 |
| `web/public/app.js:429` | 전체 |
| `web/public/app.js:429` | 국내 |
| `web/public/app.js:429` | 해외 |
| `web/public/app.js:430` | 전체 |
| `web/public/app.js:434` | 전체 |
| `web/public/app.js:434` | 국내 |
| `web/public/app.js:434` | 해외 |
| `web/public/app.js:435` | 전체 |
| `web/public/app.js:437` | 전체 |
| `web/public/app.js:455` | <option value="전체">전체 주제</option> |
| `web/public/app.js:458` | 전체 |
| `web/public/app.js:475` | 전체 |
| `web/public/app.js:481` | 전체 |
| `web/public/app.js:485` | 종결 · ${dateLabel(issue.last_seen)} |
| `web/public/app.js:488` | 주요 · ${tracked}회 추적 |
| `web/public/app.js:488` | 주요 |
| `web/public/app.js:489` | 업데이트 · ${tracked}회 추적 |
| `web/public/app.js:491` | 새 이슈 |
| `web/public/app.js:499` | <a class="source-link" href="${esc(representativeUrl)}" target="_blank" rel="noopener noreferrer">원문 <span aria-hidden="true">↗</span></a> |
| `web/public/app.js:506` | ${esc(dateLabel(issue.first_seen))}부터 ${esc(dateLabel(issue.last_seen))}까지 ${issue.briefing_count \|\| 1}회 추적 |
| `web/public/app.js:520` | ${dateLabel(ref.date)}호 |
| `web/public/app.js:525` | <p class="issue-keei"><strong>에경연 인사이트</strong><span>${links}</span></p> |
| `web/public/app.js:535` | ${ref.org_kr \|\| "에경연"}${ref.date ? |
| `web/public/app.js:555` | <p class="search-match">검색 조건 <mark>${esc(state.archiveQuery)}</mark>과 연결된 이슈입니다.</p> |
| `web/public/app.js:569` | <p class="issue-change"><strong>변화</strong><span>${esc(change)}</span></p> |
| `web/public/app.js:592` | <li><strong>${esc(item.keyword)}</strong><small>이번 주 ${item.count_now}회 · ${item.count_now - item.count_prev >= 0 ? "+" : ""}${item.count_now - item.count_pr… |
| `web/public/app.js:626` | 브리핑 데이터가 아직 갱신되지 않았습니다 |
| `web/public/app.js:627` | ${esc(trouble.message \|\| "자동 수집 상태를 확인하고 있습니다")} |
| `web/public/app.js:642` | 진행 중인 이슈는 <button type="button" data-go-view="search">이슈 아카이브</button>에서 확인할 수 있습니다. |
| `web/public/app.js:647` | 가장 최근 브리핑을 확인해 보세요. |
| `web/public/app.js:655` | 오늘의 브리핑 |
| `web/public/app.js:676` | 0개 이슈 |
| `web/public/app.js:685` | 오늘의 핵심 |
| `web/public/app.js:690` | 오늘, 무엇이 달라졌는가 |
| `web/public/app.js:691` | 오늘의 핵심 이슈 |
| `web/public/app.js:702` | <span class="hero-evidence-label">근거 이슈</span> |
| `web/public/app.js:714` | ${visibleChanged.length}개 이슈 |
| `web/public/app.js:719` | ${rest.length}개 이슈 |
| `web/public/app.js:726` | <p class="section-note">필터에 맞는 이슈는 위 <strong>지금 달라진 이슈</strong>에 있습니다.</p> |
| `web/public/app.js:729` | 전체 |
| `web/public/app.js:730` | 전체 |
| `web/public/app.js:743` | 공식기관 |
| `web/public/app.js:743` | 언론 |
| `web/public/app.js:746` | <a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 확인 <span aria-hidden="true">↗</span></a> |
| `web/public/app.js:753` | 전체 |
| `web/public/app.js:754` | 전체 |
| `web/public/app.js:756` | 오늘 수집한 원문 ${articles.length}건 |
| `web/public/app.js:757` | ${dateLabel(state.briefingDate)} 발행 |
| `web/public/app.js:764` | 전체 |
| `web/public/app.js:765` | 전체 |
| `web/public/app.js:805` | 최근 ${state.archivePeriod}일 |
| `web/public/app.js:806` | 전체 |
| `web/public/app.js:807` | 전체 |
| `web/public/app.js:808` | 공식·복수 출처 확인 |
| `web/public/app.js:811` | ${matches.length}개 이슈 · ${matchedArticles}개 원문 |
| `web/public/app.js:815` | 검색어 · ${state.archiveQuery} |
| `web/public/app.js:835` | 간행물 |
| `web/public/app.js:835` | 보고서 |
| `web/public/app.js:835` | 분석 |
| `web/public/app.js:835` | 보도자료 |
| `web/public/app.js:836` | 소식·보고서 |
| `web/public/app.js:836` | 정기간행물 |
| `web/public/app.js:858` | <p class="pub-toc">현안이슈: ${esc(tocIssue)}</p> |
| `web/public/app.js:859` | <a class="source-link" href="${esc(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF 원문 <span aria-hidden="true">↗</span></a> |
| `web/public/app.js:880` | 전체 |
| `web/public/app.js:881` | 전체 |
| `web/public/app.js:896` | 전체 |
| `web/public/app.js:904` | 이번 브리핑 |
| `web/public/app.js:906` | 이전 흐름 |
| `web/public/app.js:911` | · 1차 출처 |
| `web/public/app.js:943` | • 이슈: ${issue.title \|\| ""} |
| `web/public/app.js:944` | • 핵심: ${issue.summary} |
| `web/public/app.js:945` | • 변화: ${issueChangeText(issue)} |
| `web/public/app.js:946` | • 의미(AI 해석): ${issue.implication} |
| `web/public/app.js:947` | • 미확정: ${issue.open_question} |
| `web/public/app.js:948` | • 검증: ${(VERIFICATION_VIEW[verificationState(issue).status] \|\| VERIFICATION_VIEW.unverified).label} — ${issueEvidenceText(issue)} |
| `web/public/app.js:949` | • 근거: ${source} |
| `web/public/app.js:961` | 지역: ${issue.region} |
| `web/public/app.js:962` | 최초 확인: ${dateLabel(issue.first_seen)} |
| `web/public/app.js:963` | 최근 확인: ${dateLabel(issue.last_seen)} |
| `web/public/app.js:964` | 근거 기사: ${issue.article_count \|\| 0}건 |
| `web/public/app.js:968` | ## 한 줄 결론 |
| `web/public/app.js:969` | ## 이번에 달라진 점 |
| `web/public/app.js:972` | ## 검증 상태 |
| `web/public/app.js:978` | ## 사건 타임라인 |
| `web/public/app.js:980` | 1차 출처 |
| `web/public/app.js:997` | ## 수치·일정 |
| `web/public/app.js:1002` | ## 관련 발간물 |
| `web/public/app.js:1010` | 출처: Nuclens ${location.origin}${issuePath(issue.issue_id)} |
| `web/public/app.js:1053` | <p class="dialog-change"><strong>이번에 달라진 점</strong>${esc(issueChangeText(issue))}</p> |
| `web/public/app.js:1055` | <p class="dialog-meaning"><strong>Nuclens 해석 <span class="ai-badge">AI</span></strong>${esc(issue.implication)}</p> |
| `web/public/app.js:1056` | <p class="dialog-open"><strong>아직 확정되지 않은 것</strong>${esc(issue.open_question)}</p> |
| `web/public/app.js:1063` | 이번 브리핑 |
| `web/public/app.js:1063` | 최근 브리핑 |
| `web/public/app.js:1142` | <div class="event-block"><strong>구성 사건</strong><ul>${eventBullets}</ul></div> |
| `web/public/app.js:1169` | <div><strong>분석 기간 ${dateLabel(start)}–${dateLabel(end)}</strong><p>중복 제거 적용 · 원본 ${articleCount}건 → 연결 이슈 ${issueCount}개</p></div><div class="coverage"><spa… |
| `web/public/app.js:1170` | <div><strong>분류 기준을 확인하고 있습니다</strong><p>분류가 완료되면 분석 기간과 근거 데이터를 함께 표시합니다.</p></div><div class="coverage"><span>주제 분류 <strong>${topicCoverage}%</strong></spa… |
| `web/public/app.js:1195` | <div class="keyword-row"><strong>${esc(row.tag)}</strong><span>${row.now}</span><span>${row.prev}</span><span class="${row.delta > 0 ? "positive" : row.delta… |
| `web/public/app.js:1199` | ${strongest.tag}이(가) 전주보다 ${Math.abs(strongest.delta)}건 늘어 이번 주 변화가 가장 컸습니다. |
| `web/public/app.js:1200` | 비교할 키워드가 아직 충분하지 않습니다. |
| `web/public/app.js:1201` | <p><strong>${esc(row.tag)}</strong> · 이번 주 ${row.now}건 · 전주 ${row.prev}건</p> |
| `web/public/app.js:1206` | <p class="empty">아직 데이터가 충분하지 않습니다.</p> |
| `web/public/app.js:1220` | <p class="empty">주간 데이터가 더 필요합니다.</p> |
| `web/public/app.js:1243` | 기타 |
| `web/public/app.js:1244` | <g class="slope-series"><line x1="${left}" y1="${y(row.prev)}" x2="${right}" y2="${y(row.now)}" style="stroke:${color}"/><circle cx="${left}" cy="${y(row.pre… |
| `web/public/app.js:1248` | 기타 |
| `web/public/app.js:1251` | 전주와 이번 주의 주제별 이슈 수 비교 |
| `web/public/app.js:1254` | 기타 주제 |
| `web/public/app.js:1256` | ${label} 이슈가 전주 ${strongest.prev}건에서 이번 주 ${strongest.now}건으로 ${delta >= 0 ? |
| `web/public/app.js:1258` | 기타 |
| `web/public/app.js:1259` | <p><strong>${esc(name)}</strong> · 전주 ${row.prev}건 → 이번 주 ${row.now}건</p> |
| `web/public/app.js:1290` | ${dateLabel(report.week_start)}–${dateLabel(report.week_end)} · 이슈 ${report.source_issue_count ?? 0}건 |
| `web/public/app.js:1296` | 강화 |
| `web/public/app.js:1296` | 약화 |
| `web/public/app.js:1296` | 유지 |
| `web/public/app.js:1299` | 이번 주 판을 바꾼 것 |
| `web/public/app.js:1305` | 조용하지만 놓치면 안 되는 것 |
| `web/public/app.js:1305` | 투자 테마 강약 |
| `web/public/app.js:1310` | 한수원에 직접 닿는 변화 |
| `web/public/app.js:1312` | 다음 주 하나만 본다면 |
| `web/public/app.js:1314` | 아직 결론 나지 않은 것 |
| `web/public/app.js:1314` | 이슈당 한 번만 · 최신순 |
| `web/public/app.js:1330` | 최근 30일에는 ${COUNTRY_LABELS[topCountry.country] \|\| topCountry.country} 관련 이슈가 ${topCountry.count}개로 가장 많았습니다. |
| `web/public/app.js:1331` | 국가별로 비교할 이슈가 아직 충분하지 않습니다. |
| `web/public/app.js:1336` | 전체 |
| `web/public/app.js:1337` | 전체 |
| `web/public/app.js:1338` | 전체 |
| `web/public/app.js:1339` | #regionTabs [data-region="전체"] |
| `web/public/app.js:1346` | 전체 |
| `web/public/app.js:1347` | 전체 |
| `web/public/app.js:1349` | 전체 |
| `web/public/app.js:1351` | 전체 |
| `web/public/app.js:1352` | 전체 |
| `web/public/app.js:1353` | 전체 |
| `web/public/app.js:1538` | 검색어를 입력하세요. |
| `web/public/app.js:1614` | ${state.issues.length}개 이슈 · ${catalogArticles}개 원문 · ${dateLabel(firstIssueDate)}–${dateLabel(state.meta.latest_briefing_date)} |
| `web/public/index.html:7` | Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다. |
| `web/public/index.html:13` | Nuclens · 원자력 정책·산업 이슈 트래커 |
| `web/public/index.html:14` | 원자력 이슈를 연결하고, 변화를 추적합니다. |
| `web/public/index.html:21` | Nuclens 로고 — N 마크와 워드마크 |
| `web/public/index.html:23` | Nuclens · 원자력 정책·산업 이슈 트래커 |
| `web/public/index.html:24` | 원자력 이슈를 연결하고, 변화를 추적합니다. |
| `web/public/index.html:27` | Nuclens 원자력 정책·산업 이슈 트래커 |
| `web/public/index.html:28` | Nuclens · 원자력 정책·산업 이슈 트래커 |
| `web/public/index.html:37` | Nuclens 오늘 브리핑 |
| `web/public/index.html:41` | 원자력 정책·산업 이슈 트래커 |
| `web/public/index.html:44` | 주요 화면 |
| `web/public/index.html:45` | 오늘 브리핑 |
| `web/public/index.html:46` | 이슈 아카이브 |
| `web/public/index.html:47` | 주간 흐름 |
| `web/public/index.html:48` | 발간물 |
| `web/public/index.html:56` | 상태 확인 |
| `web/public/index.html:75` | 이전 브리핑 |
| `web/public/index.html:76` | 브리핑 날짜 |
| `web/public/index.html:77` | 다음 브리핑 |
| `web/public/index.html:82` | 오늘의 핵심 |
| `web/public/index.html:84` | 이 문장의 근거 이슈 |
| `web/public/index.html:87` | 주간 흐름 |
| `web/public/index.html:102` | 지금 달라진 이슈 |
| `web/public/index.html:103` | 이전 브리핑 이후 상태가 움직인 이슈입니다. |
| `web/public/index.html:114` | 오늘 확인된 이슈 |
| `web/public/index.html:115` | 기사가 아니라 연결된 이슈 단위입니다. |
| `web/public/index.html:121` | 중요도순 |
| `web/public/index.html:122` | 최신순 |
| `web/public/index.html:126` | 카드 |
| `web/public/index.html:127` | 목록 |
| `web/public/index.html:130` | 필터 |
| `web/public/index.html:132` | 오늘 이슈 필터 |
| `web/public/index.html:133` | 지역 필터 |
| `web/public/index.html:134` | 전체 |
| `web/public/index.html:135` | 국내 |
| `web/public/index.html:136` | 해외 |
| `web/public/index.html:138` | 주제 |
| `web/public/index.html:138` | 전체 주제 |
| `web/public/index.html:138` | 전체 |
| `web/public/index.html:154` | 오늘 수집한 원문 |
| `web/public/index.html:157` | 요약은 읽는 시간을 줄이기 위한 것입니다. 날짜·수치·기관명은 원문에서 확인하세요. |
| `web/public/index.html:160` | 오늘 브리핑 보조 정보 |
| `web/public/index.html:161` | 오늘의 검증 상태 |
| `web/public/index.html:162` | 이번 주 이어지는 흐름 |
| `web/public/index.html:163` | 자주 찾는 주제 |
| `web/public/index.html:170` | 이슈 아카이브 |
| `web/public/index.html:174` | 아카이브 필터 |
| `web/public/index.html:176` | 기간 |
| `web/public/index.html:178` | 전체 |
| `web/public/index.html:179` | 30일 |
| `web/public/index.html:180` | 7일 |
| `web/public/index.html:183` | 지역 |
| `web/public/index.html:183` | 전체 지역 |
| `web/public/index.html:183` | 국내 |
| `web/public/index.html:183` | 해외 |
| `web/public/index.html:183` | 전체 |
| `web/public/index.html:184` | 주제 |
| `web/public/index.html:184` | 전체 주제 |
| `web/public/index.html:184` | 전체 |
| `web/public/index.html:185` | 검증 상태 |
| `web/public/index.html:185` | 전체 상태 |
| `web/public/index.html:185` | 공식·복수 출처 확인 |
| `web/public/index.html:185` | 전체 |
| `web/public/index.html:190` | 검색 결과 |
| `web/public/index.html:193` | 최근 갱신순 |
| `web/public/index.html:194` | 추적 횟수순 |
| `web/public/index.html:195` | 출처 수순 |
| `web/public/index.html:201` | 검색 결과는 개별 기사가 아니라 연결된 이슈를 기준으로 표시합니다. |
| `web/public/index.html:209` | 주간 흐름 |
| `web/public/index.html:210` | 언급량 자체보다 어떤 사건이 같은 방향으로 이어지는지 확인합니다. |
| `web/public/index.html:219` | 주간 판세 |
| `web/public/index.html:223` | 이번 주 이어지는 흐름 |
| `web/public/index.html:229` | 집계 기간 |
| `web/public/index.html:230` | 최근 7일 |
| `web/public/index.html:231` | 최근 30일 |
| `web/public/index.html:236` | 키워드 비교 |
| `web/public/index.html:236` | 이번 주와 전주를 같은 기준으로 비교합니다. |
| `web/public/index.html:238` | 언급순 |
| `web/public/index.html:239` | 변화순 |
| `web/public/index.html:240` | 신규만 |
| `web/public/index.html:249` | 국가·지역별 이슈 수 |
| `web/public/index.html:250` | 최근 30일 연결 이슈 기준 · 복수 국가는 각각 1건 |
| `web/public/index.html:253` | EU는 유럽연합 기관·공동정책, 유럽은 범지역 이슈입니다. 개별 국가 이슈는 프랑스처럼 국가명으로 표시합니다. |
| `web/public/index.html:256` | 주제별 주간 변화 |
| `web/public/index.html:257` | 상위 4개 주제와 기타 합계 · 연결 이슈 기준 |
| `web/public/index.html:265` | 언급량은 중요도가 아닙니다. 흐름은 근거 기사와 함께 보세요. |
| `web/public/index.html:276` | 국제기구 발간물 |
| `web/public/index.html:277` | IAEA·OECD NEA·IEA·EIA 등 공식 기관의 보고서·발간물 발행을 추적합니다. 제목과 원문 링크만 제공합니다. |
| `web/public/index.html:280` | 기관 필터 |
| `web/public/index.html:288` | 검색어 |
| `web/public/index.html:289` | 검색 결과는 이슈 아카이브에서 확인합니다. |
| `web/public/index.html:289` | 검색어를 입력하세요. |
| `web/public/index.html:294` | 데이터 상태 |
| `web/public/index.html:301` | 모바일 주요 화면 |
| `web/public/index.html:302` | 오늘 |
| `web/public/index.html:303` | 아카이브 |
| `web/public/index.html:304` | 주간 |
| `web/public/index.html:305` | 발간물 |
| `web/public/index.html:313` | 원자력 이슈를 연결하고, 변화를 추적합니다. |
| `web/public/index.html:314` | 데이터 기준 |
| `web/public/index.html:314` | 중복 제거 후 연결된 이슈를 기준으로 표시합니다. |
| `web/public/index.html:315` | AI 요약 정책 |
| `web/public/index.html:315` | 날짜·수치·기관명은 원문에서 확인하세요. |
| `web/public/index.html:316` | 출처·저작권 |
| `web/public/index.html:316` | 원문 저작권은 각 언론사와 기관에 있습니다. |
| `web/public/index.html:318` | Nuclens는 제목·요약·출처 링크만 제공합니다. |

## 데이터 문장(빌드 시점) (403건)

| 파일:라인 | 문구 |
|---|---|
| `web/build_data.py:85` | 공식 확인 |
| `web/build_data.py:86` | 복수 출처 확인 |
| `web/build_data.py:87` | 단일 출처 |
| `web/build_data.py:92` | [0-9A-Za-z가-힣]+ |
| `web/build_data.py:93` | [^0-9a-z가-힣] |
| `web/build_data.py:96` | 신월성 |
| `web/build_data.py:96` | 신한울 |
| `web/build_data.py:96` | 신고리 |
| `web/build_data.py:96` | 후쿠시마 |
| `web/build_data.py:96` | 자포리자 |
| `web/build_data.py:96` | 체르노빌 |
| `web/build_data.py:97` | 올킬루오토 |
| `web/build_data.py:97` | 플라망빌 |
| `web/build_data.py:97` | 힝클리 |
| `web/build_data.py:97` | 사이즈웰 |
| `web/build_data.py:97` | 두코바니 |
| `web/build_data.py:97` | 테믈린 |
| `web/build_data.py:98` | 세르나보다 |
| `web/build_data.py:98` | 알마라즈 |
| `web/build_data.py:98` | 바라카 |
| `web/build_data.py:98` | 아투차 |
| `web/build_data.py:98` | 엠발세 |
| `web/build_data.py:98` | 보글틀 |
| `web/build_data.py:99` | 새울 |
| `web/build_data.py:99` | 고리 |
| `web/build_data.py:99` | 월성 |
| `web/build_data.py:99` | 한빛 |
| `web/build_data.py:99` | 한울 |
| `web/build_data.py:99` | 타이산 |
| `web/build_data.py:99` | 파크스 |
| `web/build_data.py:103` | ({_FACILITY_PATTERN})\s*(\d+)\s*호기 |
| `web/build_data.py:106` | 원전 |
| `web/build_data.py:106` | 원자력 |
| `web/build_data.py:106` | 에너지 |
| `web/build_data.py:106` | 정책 |
| `web/build_data.py:106` | 에너지정책 |
| `web/build_data.py:106` | 원전정책 |
| `web/build_data.py:106` | 해외원전 |
| `web/build_data.py:107` | 국내원전 |
| `web/build_data.py:107` | 산업동향 |
| `web/build_data.py:107` | 시장동향 |
| `web/build_data.py:107` | 기술개발 |
| `web/build_data.py:107` | 국제협력 |
| `web/build_data.py:107` | 안전 |
| `web/build_data.py:110` | 원안위 |
| `web/build_data.py:110` | 한수원 |
| `web/build_data.py:110` | 미국nrc |
| `web/build_data.py:110` | 미국doe |
| `web/build_data.py:110` | 정부 |
| `web/build_data.py:113` | 미국doe |
| `web/build_data.py:114` | 미에너지부 |
| `web/build_data.py:114` | 미국doe |
| `web/build_data.py:115` | 미국에너지부 |
| `web/build_data.py:115` | 미국doe |
| `web/build_data.py:116` | 미국nrc |
| `web/build_data.py:117` | 미원자력규제위원회 |
| `web/build_data.py:117` | 미국nrc |
| `web/build_data.py:118` | 전기본 |
| `web/build_data.py:118` | 전력수급기본계획 |
| `web/build_data.py:119` | 12차전기본 |
| `web/build_data.py:119` | 12차전력수급기본계획 |
| `web/build_data.py:123` | 정책 결정 |
| `web/build_data.py:124` | 규제 조치 |
| `web/build_data.py:125` | 계약 체결 |
| `web/build_data.py:126` | 사업 진전 |
| `web/build_data.py:127` | 안전 사건 |
| `web/build_data.py:128` | 기업 동향 |
| `web/build_data.py:129` | 연구·보고서 |
| `web/build_data.py:130` | 시장 신호 |
| `web/build_data.py:137` | 후쿠시마 |
| `web/build_data.py:137` | 처리수 |
| `web/build_data.py:137` | 오염수 |
| `web/build_data.py:138` | 핵융합 |
| `web/build_data.py:138` | 토카막 |
| `web/build_data.py:139` | 소형모듈 |
| `web/build_data.py:139` | 소형 모듈 |
| `web/build_data.py:139` | 마이크로원자로 |
| `web/build_data.py:140` | 계속운전 |
| `web/build_data.py:140` | 계속 운전 |
| `web/build_data.py:140` | 수명연장 |
| `web/build_data.py:140` | 수명 연장 |
| `web/build_data.py:140` | 재가동 |
| `web/build_data.py:141` | 신규원전 |
| `web/build_data.py:141` | 신규 원전 |
| `web/build_data.py:141` | 원전건설 |
| `web/build_data.py:141` | 원전 건설 |
| `web/build_data.py:142` | 핵연료 |
| `web/build_data.py:142` | 우라늄 |
| `web/build_data.py:142` | 농축 |
| `web/build_data.py:142` | 연료주기 |
| `web/build_data.py:143` | 사용후핵연료 |
| `web/build_data.py:143` | 방사성폐기물 |
| `web/build_data.py:143` | 방폐 |
| `web/build_data.py:143` | 고준위 |
| `web/build_data.py:143` | 폐기물 처분 |
| `web/build_data.py:144` | 규제 |
| `web/build_data.py:144` | 인허가 |
| `web/build_data.py:144` | 허가 연장 |
| `web/build_data.py:144` | 원안위 |
| `web/build_data.py:144` | 행정예고 |
| `web/build_data.py:144` | 입법예고 |
| `web/build_data.py:144` | 안전심사 |
| `web/build_data.py:145` | 데이터센터 |
| `web/build_data.py:145` | 데이터 센터 |
| `web/build_data.py:145` | ai 전력 |
| `web/build_data.py:145` | 인공지능 전력 |
| `web/build_data.py:145` | 빅테크 |
| `web/build_data.py:146` | 전력수급 |
| `web/build_data.py:146` | 전기본 |
| `web/build_data.py:146` | 전력시장 |
| `web/build_data.py:146` | 전력망 |
| `web/build_data.py:146` | 전기요금 |
| `web/build_data.py:146` | 전력 수요 |
| `web/build_data.py:146` | 전력공급 |
| `web/build_data.py:148` | 원전금융 |
| `web/build_data.py:148` | 프로젝트 금융 |
| `web/build_data.py:148` | 자금조달 |
| `web/build_data.py:148` | 투자계약 |
| `web/build_data.py:148` | 글로벌원전투자 |
| `web/build_data.py:148` | 민간금융 |
| `web/build_data.py:149` | 투자 유치 |
| `web/build_data.py:149` | 대출 |
| `web/build_data.py:149` | 전력구매계약 |
| `web/build_data.py:152` | 원전수출 |
| `web/build_data.py:152` | 수출 계약 |
| `web/build_data.py:152` | 원자력협력 |
| `web/build_data.py:152` | 핵협력 |
| `web/build_data.py:152` | 협력 협정 |
| `web/build_data.py:152` | 에너지안보 |
| `web/build_data.py:153` | 공급망 |
| `web/build_data.py:153` | 통상 |
| `web/build_data.py:153` | 제재 |
| `web/build_data.py:153` | 양자협정 |
| `web/build_data.py:153` | 안전조치 협정 |
| `web/build_data.py:155` | 원전운영 |
| `web/build_data.py:155` | 설비이용률 |
| `web/build_data.py:155` | 운영효율 |
| `web/build_data.py:155` | 가동중단 |
| `web/build_data.py:155` | 장기운전 |
| `web/build_data.py:155` | 리튜빙 |
| `web/build_data.py:155` | 설비개선 |
| `web/build_data.py:155` | 개보수 |
| `web/build_data.py:156` | 원전안전 |
| `web/build_data.py:156` | 핵안전 |
| `web/build_data.py:156` | 안전사고 |
| `web/build_data.py:156` | 화재 |
| `web/build_data.py:156` | 비상대비 |
| `web/build_data.py:156` | 방사선안전 |
| `web/build_data.py:157` | 원전해체 |
| `web/build_data.py:157` | 원전 해체 |
| `web/build_data.py:157` | 해체 작업 |
| `web/build_data.py:157` | 폐로 |
| `web/build_data.py:158` | 원전인력 |
| `web/build_data.py:158` | 원전 인력 |
| `web/build_data.py:158` | 인력증가 |
| `web/build_data.py:158` | 인력동향 |
| `web/build_data.py:158` | 전문인력 |
| `web/build_data.py:159` | 원자력정책 |
| `web/build_data.py:159` | 미국원자력정책 |
| `web/build_data.py:159` | 미국정책 |
| `web/build_data.py:159` | 원자력확대 |
| `web/build_data.py:159` | 에너지전환 |
| `web/build_data.py:159` | 에너지로드맵 |
| `web/build_data.py:159` | 원자력혁신 |
| `web/build_data.py:160` | 원자력연구 |
| `web/build_data.py:160` | 연구개발 |
| `web/build_data.py:160` | 센서기술 |
| `web/build_data.py:160` | 핵과학 |
| `web/build_data.py:160` | 시험 시설 |
| `web/build_data.py:160` | 기술실증 |
| `web/build_data.py:161` | 원자력수소 |
| `web/build_data.py:161` | 원자력 기반 수소 |
| `web/build_data.py:161` | 동위원소 |
| `web/build_data.py:161` | 방사선 활용 |
| `web/build_data.py:161` | 핵 과학 활용 |
| `web/build_data.py:167` | 한국 |
| `web/build_data.py:167` | 대한민국 |
| `web/build_data.py:167` | 한수원 |
| `web/build_data.py:167` | 원안위 |
| `web/build_data.py:167` | 고리 |
| `web/build_data.py:167` | 월성 |
| `web/build_data.py:167` | 한울 |
| `web/build_data.py:167` | 신한울 |
| `web/build_data.py:167` | 새울 |
| `web/build_data.py:167` | 영덕 |
| `web/build_data.py:167` | 경주 |
| `web/build_data.py:169` | 미국 |
| `web/build_data.py:169` | 미 에너지부 |
| `web/build_data.py:169` | 미 원자력규제위원회 |
| `web/build_data.py:169` | 백악관 |
| `web/build_data.py:170` | 로스앨러모스 |
| `web/build_data.py:170` | 패듀카 |
| `web/build_data.py:170` | 사바나강 |
| `web/build_data.py:170` | 오이스터크릭 |
| `web/build_data.py:170` | 화이트메사 |
| `web/build_data.py:170` | 샌디아 |
| `web/build_data.py:171` | 텍사스 |
| `web/build_data.py:171` | 버지니아 |
| `web/build_data.py:171` | 아이다호 |
| `web/build_data.py:173` | 캐나다 |
| `web/build_data.py:173` | 온타리오 |
| `web/build_data.py:173` | 서스캐처원 |
| `web/build_data.py:173` | 브루스 파워 |
| `web/build_data.py:173` | 달링턴 |
| `web/build_data.py:174` | 프랑스 |
| `web/build_data.py:174` | 플라망빌 |
| `web/build_data.py:174` | 팔리 |
| `web/build_data.py:174` | 마르쿨 |
| `web/build_data.py:174` | 카다라슈 |
| `web/build_data.py:175` | 영국 |
| `web/build_data.py:175` | 잉글랜드 |
| `web/build_data.py:175` | 스코틀랜드 |
| `web/build_data.py:175` | 웨일스 |
| `web/build_data.py:175` | 사이즈웰 |
| `web/build_data.py:175` | 힝클리 |
| `web/build_data.py:175` | 헤이샴 |
| `web/build_data.py:175` | 하틀풀 |
| `web/build_data.py:176` | 독일 |
| `web/build_data.py:176` | 도이칠란트 |
| `web/build_data.py:176` | 막스 플랑크 |
| `web/build_data.py:176` | 벤델슈타인 |
| `web/build_data.py:177` | 스페인 |
| `web/build_data.py:178` | 세르비아 |
| `web/build_data.py:179` | 헝가리 |
| `web/build_data.py:179` | 팍스 원전 |
| `web/build_data.py:180` | 루마니아 |
| `web/build_data.py:180` | 체르나보다 |
| `web/build_data.py:181` | 체코 |
| `web/build_data.py:181` | 두코바니 |
| `web/build_data.py:181` | 테멜린 |
| `web/build_data.py:182` | 폴란드 |
| `web/build_data.py:183` | 스웨덴 |
| `web/build_data.py:184` | 네덜란드 |
| `web/build_data.py:184` | 보르셀레 |
| `web/build_data.py:185` | 핀란드 |
| `web/build_data.py:185` | 올킬루오토 |
| `web/build_data.py:186` | 슬로바키아 |
| `web/build_data.py:186` | 모호프체 |
| `web/build_data.py:187` | 불가리아 |
| `web/build_data.py:187` | 코즐로두이 |
| `web/build_data.py:188` | 우크라이나 |
| `web/build_data.py:188` | 자포리자 |
| `web/build_data.py:189` | 벨기에 |
| `web/build_data.py:190` | 이탈리아 |
| `web/build_data.py:191` | 포르투갈 |
| `web/build_data.py:192` | 스위스 |
| `web/build_data.py:193` | 노르웨이 |
| `web/build_data.py:194` | 덴마크 |
| `web/build_data.py:195` | 일본 |
| `web/build_data.py:195` | 후쿠시마 |
| `web/build_data.py:195` | 도쿄전력 |
| `web/build_data.py:196` | 러시아 |
| `web/build_data.py:197` | 중국 |
| `web/build_data.py:198` | 아르헨티나 |
| `web/build_data.py:198` | 아투차 |
| `web/build_data.py:199` | 인도 |
| `web/build_data.py:200` | 호주 |
| `web/build_data.py:201` | 브라질 |
| `web/build_data.py:202` | 남아공 |
| `web/build_data.py:202` | 남아프리카공화국 |
| `web/build_data.py:203` | 사우디 |
| `web/build_data.py:204` | 아랍에미리트 |
| `web/build_data.py:204` | 바라카 |
| `web/build_data.py:205` | 튀르키예 |
| `web/build_data.py:205` | 터키 |
| `web/build_data.py:205` | 아쿠유 |
| `web/build_data.py:206` | 카자흐스탄 |
| `web/build_data.py:207` | 우즈베키스탄 |
| `web/build_data.py:210` | 유럽연합 |
| `web/build_data.py:210` | eu 집행위 |
| `web/build_data.py:210` | eu 집행위원회 |
| `web/build_data.py:210` | 유럽위원회 |
| `web/build_data.py:211` | 유럽의회 |
| `web/build_data.py:213` | 유럽 |
| `web/build_data.py:213` | 범유럽 |
| `web/build_data.py:215` | 글로벌 |
| `web/build_data.py:215` | 전 세계 |
| `web/build_data.py:215` | 세계 원자력 |
| `web/build_data.py:215` | 세계원자력 |
| `web/build_data.py:215` | 국제원자력기구 |
| `web/build_data.py:216` | 세계은행 |
| `web/build_data.py:233` | 구버전 레코드를 웹 빌드의 현재 출처·사건일 계약으로 읽는다. |
| `web/build_data.py:344` | 날짜 → 그날의 대표 selection_stats 레코드. |
| `web/build_data.py:393` | status.json 본문. app.js renderSystemStatus 가 이 계약을 이미 렌더한다. |
| `web/build_data.py:408` | 수집이 {collector_age:.0f}시간째 멈춰 있습니다 |
| `web/build_data.py:417` | 브리핑이 {briefing_age / 24:.0f}일째 갱신되지 않았습니다 |
| `web/build_data.py:419` | 브리핑 일부가 발송되지 않았습니다 |
| `web/build_data.py:457` | 국내 |
| `web/build_data.py:459` | 해외 |
| `web/build_data.py:467` | 국내 |
| `web/build_data.py:467` | 해외 |
| `web/build_data.py:471` | 국내 |
| `web/build_data.py:473` | 해외 |
| `web/build_data.py:477` | 국내 |
| `web/build_data.py:477` | 해외 |
| `web/build_data.py:497` | 내부 점수 내역을 카드용 설명 배지 최대 2개로 바꾼다. |
| `web/build_data.py:515` | 공식 원문 |
| `web/build_data.py:519` | 전문 매체 |
| `web/build_data.py:521` | 국내 관련성 높음 |
| `web/build_data.py:523` | 정책 영향 큼 |
| `web/build_data.py:525` | 근거 강도 높음 |
| `web/build_data.py:528` | 브리핑 우선순위 |
| `web/build_data.py:572` | 텍스트에서 국가와 명시적 지역 범위를 서로 다른 축으로 판정한다. |
| `web/build_data.py:635` | 국내 |
| `web/build_data.py:688` | 이전 → 현재 |
| `web/build_data.py:702` | 현행 Gemini 모델의 임베딩 캐시만 읽기 전용으로 정규화한다. |
| `web/build_data.py:717` | API 장애 때도 후보 탐색을 계속할 수 있는 언어 독립 특징 벡터. |
| `web/build_data.py:842` | [overrides] 무시 {skipped}건 (hash8/date 누락) / |
| `web/build_data.py:843` | promote·demote 충돌 {len(conflicts)}건 → demote 우선 |
| `web/build_data.py:848` | 1단계 — promote 대상을 그날 브리핑 후보로 끌어올린다(클러스터링 전). |
| `web/build_data.py:881` | [overrides] {briefing_date} 한 이슈에 promote·demote 공존 → demote 적용 |
| `web/build_data.py:887` | 없는 hash 는 조용히 무시하되 흔적은 남긴다 — 오타를 영영 모르면 안 된다. |
| `web/build_data.py:892` | [overrides] 해당 날짜 데이터에 없는 항목 {len(missing)}건 무시: {preview} |
| `web/build_data.py:1032` | 자동 병합 아래 구간을 사람 확인 큐로 보낸다. |
| `web/build_data.py:1101` | 고 있으며$ |
| `web/build_data.py:1101` | 고 있습니다. |
| `web/build_data.py:1102` | 해 왔으며$ |
| `web/build_data.py:1102` | 해 왔습니다. |
| `web/build_data.py:1103` | 했으며$ |
| `web/build_data.py:1103` | 했습니다. |
| `web/build_data.py:1104` | 됐으며$ |
| `web/build_data.py:1104` | 됐습니다. |
| `web/build_data.py:1105` | 였으며$ |
| `web/build_data.py:1105` | 였습니다. |
| `web/build_data.py:1106` | 이며$ |
| `web/build_data.py:1106` | 입니다. |
| `web/build_data.py:1107` | 하고$ |
| `web/build_data.py:1107` | 했습니다. |
| `web/build_data.py:1108` | 되고$ |
| `web/build_data.py:1108` | 됐습니다. |
| `web/build_data.py:1109` | 되어$ |
| `web/build_data.py:1109` | 됐습니다. |
| `web/build_data.py:1134` | 강도·근거 중복·국내외 커버리지를 함께 보는 주간 대표 흐름 선택. |
| `web/build_data.py:1144` | 해외 |
| `web/build_data.py:1158` | 흐름 근거에 지역 메타를 붙이고 다양화된 대표 3개를 만든다. |
| `web/build_data.py:1183` | 국내 |
| `web/build_data.py:1183` | 해외 |
| `web/build_data.py:1185` | 국내 |
| `web/build_data.py:1186` | 국내 |
| `web/build_data.py:1187` | 해외 |
| `web/build_data.py:1189` | 국내·해외 |
| `web/build_data.py:1189` | 국내 |
| `web/build_data.py:1189` | 해외 |
| `web/build_data.py:1190` | 범위 미분류 |
| `web/build_data.py:1319` | 추적 이슈의 이번 브리핑 신규 사실을 완결된 한 문장으로 만든다. |
| `web/build_data.py:1352` | 변화 |
| `web/build_data.py:1367` | 같은 매체가 쓴 여러 기사를 하나의 출처로 묶는 키. |
| `web/build_data.py:1380` | 규제기관·사업자의 공식 문서인지. |
| `web/build_data.py:1385` | 독립 취재 보도인지. 보도자료 전재(distributed_claim)는 재인용으로 제외한다. |
| `web/build_data.py:1390` | 아직 확정되지 않은 것 |
| `web/build_data.py:1451` | 후보 문장 중 히어로 한 줄에 들어가는 첫 문장을 고른다. |
| `web/build_data.py:1468` | 며, |
| `web/build_data.py:1468` | 고, |
| `web/build_data.py:1468` | 지만 |
| `web/build_data.py:1468` | 으나 |
| `web/build_data.py:1492` | 종합 문장의 근거 기사 hash를 그날 이슈 카드로 연결한다 (최대 3개). |
| `web/build_data.py:1518` | 어제 히어로가 말한 것과 같은 사건인지. |
| `web/build_data.py:1532` | …발표했습니다 |
| `web/build_data.py:1532` | …경고했다 |
| `web/build_data.py:1536` | 무엇이 달라졌는가 |
| `web/build_data.py:1576` | 국내 |
| `web/build_data.py:1578` | 국내 |
| `web/build_data.py:1598` | 에너지경제연구원 |
| `web/build_data.py:1598` | 에경연 |
| `web/build_data.py:1619` | 매칭 판정에 쓸 의미 토큰 — 일반어는 버린다. |
| `web/build_data.py:1626` | 영덕군과 |
| `web/build_data.py:1626` | 영덕군 |
| `web/build_data.py:1627` | 로 |
| `web/build_data.py:1645` | 발간물에서 KEEI 목차 항목을 펼친다. 제목 줄만 — 본문은 저장하지 않는다. |
| `web/build_data.py:1800` | 봇이 하루 1회 생성한 '오늘의 한 문장'. 없으면 빈 dict (히어로가 폴백). |
| `web/build_data.py:1875` | 아직 확정되지 않은 것 |
| `web/build_data.py:1903` | 기준 미달 |
| `web/build_data.py:1905` | 후보가 없었다 |
| `web/build_data.py:1926` | 발송된 기사 |
| `web/build_data.py:1928` | 기준 미달 |
| `web/build_data.py:2012` | 국내·해외 |
| `web/build_data.py:2054` | 국내 |
| `web/build_data.py:2055` | 해외 |
| `web/build_data.py:2131` | 국내·해외 |
| `web/build_data.py:2157` | 원자력 정책·산업 이슈의 변화와 근거를 추적합니다. |
| `web/build_data.py:2163` | 이슈별 OG 메타데이터를 가진 정적 진입 페이지를 생성한다. |
| `web/build_data.py:2178` | Nuclens 이슈 |
| `web/build_data.py:2183` | <meta name="description" content="Nuclens는 원자력 정책·산업 뉴스를 이슈 단위로 연결하고 중요한 변화를 근거와 함께 추적합니다."> |
| `web/build_data.py:2186` | <meta property="og:title" content="Nuclens · 원자력 정책·산업 이슈 트래커"> |
| `web/build_data.py:2188` | <meta property="og:description" content="원자력 이슈를 연결하고, 변화를 추적합니다."> |
| `web/build_data.py:2194` | <title>Nuclens · 원자력 정책·산업 이슈 트래커</title> |
| `web/build_data.py:2221` | 최신 이슈 카드를 보고서형 RSS 2.0으로 직렬화한다. |
| `web/build_data.py:2225` | nuclens 원자력 정책 브리핑 |
| `web/build_data.py:2227` | 이슈 단위로 추적하는 원자력 정책 브리핑 |
| `web/build_data.py:2252` | 핵심: {issue['summary']} |
| `web/build_data.py:2254` | 새로 확인: {issue['latest_change']} |
| `web/build_data.py:2256` | 의미(AI 해석): {issue['implication']} |
| `web/build_data.py:2328` | [overrides] 편집 승격 {promoted}건 |
| `web/build_data.py:2360` | [build_data] 이슈 병합 LLM 검수: 후보 {llm_stats['candidates']}쌍 |
| `web/build_data.py:2361` | (캐시 {llm_stats['from_cache']} / 신규 {llm_stats['asked']} / |
| `web/build_data.py:2362` | 호출 {llm_stats['calls']}회) → 병합 {llm_stats['approved']} |
| `web/build_data.py:2381` | [build_data] KEEI 매칭: 후보 {keei_stats.get('candidates', 0)}쌍 |
| `web/build_data.py:2382` | (캐시 {keei_stats.get('from_cache', 0)} / 질의 {keei_stats.get('asked', 0)} / |
| `web/build_data.py:2383` | 호출 {keei_stats.get('calls', 0)}회) → 연결 {keei_stats.get('attached', 0)}건 |
| `web/build_data.py:2484` | 국내 |
| `web/build_data.py:2485` | 해외 |
| `web/build_data.py:2640` | [build] 아카이브 {len(records)}건 → 표시 {len(news_items)}건 → |
| `web/build_data.py:2641` | 브리핑 기사 {selected_count}건 / 이슈 카드 {issue_count}개 / 상세 페이지 {issue_page_count}개 → {OUT_DIR} |
