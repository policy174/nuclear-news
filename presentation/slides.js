/* =============================================================================
 * slides.js — 발표 내용 데이터 (이 파일만 고치면 발표 내용이 바뀝니다)
 *
 *  · CONFIG : 제목·발표자·날짜 등 상단 설정값
 *  · DECK   : 화면(슬라이드) 배열. { id, nav, prompt, kicker, title, html, notes }
 *  · 화면에는 핵심만, 부가 설명은 접이식 패널("자세히"/"기술적으로는?")과
 *    발표자 노트(N 키)에.
 *
 *  ⚠️ 스크린샷 슬롯 — assets/ 폴더에 아래 파일명으로 캡처를 넣으면 실물 이미지,
 *    없으면 실제 발송 내용을 재구성한 목업이 표시됩니다.
 *      shot-current.png / shot-v1-spam.png / shot-fail-gemini.png
 *      shot-fail-english.png / shot-fail-empty.png / shot-buttons.png
 * ========================================================================== */

const CONFIG = {
  title: '원자력 뉴스봇 소개',
  subtitle: '매일 아침 원자력 뉴스를 골라 보내주는 작은 봇, 석 달의 운영기',
  presenter: '양현진',            // ← 발표자 이름 수정
  affiliation: '기술정책처 원자력정책실',
  date: '2026-07-30',
  audience: '학습동아리',
  lectureUrl: 'https://claudecode-lecture.vercel.app/index.html',
  repoCheckedAt: '2026-07-29',    // 수치 기준일
};

/* 자주 쓰는 조각 ----------------------------------------------------------- */

const term = (word, meaning) =>
  `<span class="gl"><b>${word}</b><span class="gl-def">${meaning}</span></span>`;

const tech = (title, body) => `
  <details class="tech">
    <summary>기술적으로는? <span class="tech-hint">${title}</span></summary>
    <div class="tech-body">${body}</div>
  </details>`;

const more = (title, body) => `
  <details class="tech">
    <summary>자세히 <span class="tech-hint">${title}</span></summary>
    <div class="tech-body">${body}</div>
  </details>`;

/* 스크린샷 슬롯: 이미지 있으면 이미지, 없으면 재구성 목업 */
const shot = (file, alt, fallbackHtml, caption) => `
  <figure class="shot">
    <div class="shot-frame">
      <img src="assets/${file}" alt="${alt}"
           onerror="this.closest('.shot').classList.add('noimg')">
      <div class="shot-fallback">${fallbackHtml}</div>
    </div>
    <figcaption>${caption}</figcaption>
  </figure>`;

/* 텔레그램풍 미니 목업 (실제 발송분 재구성) — 프로필은 assets/bot-avatar.png */
const tg = (inner) => `
  <div class="tg">
    <div class="tg-head"><span class="tg-avatar"><img src="assets/bot-avatar.png" alt=""
      onerror="this.style.display='none'">☢</span> 원자력 뉴스 <span class="tg-sub">구독자 5명</span></div>
    <div class="tg-body">${inner}</div>
  </div>`;

/* ========================================================================== */

const DECK = [

/* ---------- 1. 표지 ------------------------------------------------------- */
{
  id: 'cover',
  icon: 'terminal', cmd: 'whoami', path: '',
  nav: '표지',
  kind: 'cover',
  html: `
    <div class="cover">
      <div class="cover-eyebrow">학습동아리 공유 · ${CONFIG.date}</div>
      <h1>원자력 뉴스봇 소개</h1>
      <p class="cover-sub">${CONFIG.subtitle}</p>
      <div class="flowchips">
        <span class="fchip">수집</span><span class="farr2">→</span>
        <span class="fchip">선별</span><span class="farr2">→</span>
        <span class="fchip">요약</span><span class="farr2">→</span>
        <span class="fchip">판단</span><span class="farr2">→</span>
        <span class="fchip">발송</span>
      </div>
      <div class="cover-foot">${CONFIG.presenter} · ${CONFIG.affiliation}</div>
    </div>`,
  notes: {
    core: '완성품 자랑이 아니라 실패담 공유라는 걸 첫 30초에 말해둡니다. "석 달간 만들어 쓰고 있는 작은 봇 얘기인데, 잘 된 것보다 실패한 과정 위주로 공유하겠다."',
    analogy: '요리 대회가 아니라 "집밥 석 달 해보니 태운 냄비가 이만큼"이라는 얘기.',
    tech: '(개발자 질문 대비) 스택: Python 3.12 + GitHub Actions + Gemini API + Telegram Bot API. 서버 없음. 5번 화면 참고.',
    bridge: '왜 만들었는지부터.',
  },
},

/* ---------- 2. 왜 만들었는가 ---------------------------------------------- */
{
  id: 'why',
  icon: 'search', cmd: '왜 만들었나요?', path: '~/nuclear-news-bot',
  nav: '왜 만들었나',
  kicker: '문제',
  title: '매일 아침 같은 사이트들을 돌고 있었습니다',
  html: `
    <div class="grid grid-4">
      <div class="card"><h3>흩어짐</h3><p>기관·전문지·언론이 전부 딴 곳에</p></div>
      <div class="card"><h3>중복</h3><p>같은 사건을 여러 매체가 받아쓰기</p></div>
      <div class="card"><h3>고르기</h3><p>모으기보다 고르기가 오래 걸림</p></div>
      <div class="card"><h3>공백</h3><p>바쁜 날은 통째로 건너뜀</p></div>
    </div>

    <div class="srcmap">
      <div class="srcmap-head">실제로 돌던 곳들 — 지금은 봇이 매시간 대신 돕니다</div>
      <div class="srcmap-grid">
        <div class="src-g"><h4>해외 전문지</h4>
          <span>IAEA</span><span>World Nuclear News</span><span>ANS Newswire</span></div>
        <div class="src-g"><h4>국내 기관 보도자료</h4>
          <span>한수원</span><span>원자력안전위원회</span><span>산업통상자원부</span><span>원자력연구원</span></div>
        <div class="src-g"><h4>국내 언론</h4>
          <span>원자력 뉴스 검색</span><span>신한울 · 전기본 · 계속운전 등</span></div>
        <div class="src-g"><h4>네이버 뉴스</h4>
          <span>정책 키워드 46개</span><span>SMR 키워드 32개</span></div>
        <div class="src-g"><h4>구독 뉴스레터</h4>
          <span>ANS Nuclear News Daily</span></div>
      </div>
    </div>

    <div class="callout">
      <b>목표를 좁게 잡았습니다.</b> 이 순회 30분만 없애기.
      판단은 내가 하고, 찾고 거르고 정리하는 <b>앞단만</b> 맡기기.
    </div>`,
  notes: {
    core: '목표를 좁게 잡은 게 완주한 이유. "정보 과잉 해결"이 아니라 "아침 30분 제거"였습니다.',
    analogy: '신문 8종을 매일 사러 다니다가 배달 시키기로 한 것.',
    tech: '(개발자 질문 대비) 수집원: RSS 3곳(IAEA·WNN·ANS) + Google News 기관 검색 4곳 + 국내 키워드 피드 + 네이버 뉴스 API + 이메일 뉴스레터.',
    bridge: '석 달 지난 지금, 아침에 뭐가 오는지부터.',
  },
},

/* ---------- 3. 지금은 이렇게 옵니다 --------------------------------------- */
{
  id: 'now',
  icon: 'chat', cmd: '아침에 뭐가 오나요?', path: '~/nuclear-news-bot',
  nav: '지금의 결과물',
  kicker: '결과 먼저',
  title: '매일 아침 7시 25분에 이렇게 옵니다',
  html: `
    <div class="two-col">
      <div>
        ${shot('shot-current.png', '실제 일일 카드 브리핑 캡처', tg(`
          <div class="tg-msg">
            <div class="tg-title">📰 🇰🇷 원자력 국내 브리핑 (2026-07-27)</div>
            <div class="tg-card">
              <b>📌 1. 원전 안전기준 최신화 추진: 안전등급·가동중검사·품질보증 규정 개정</b>
              <div class="tg-line">• <b>무슨 일:</b> 정부가 원전 안전기준 최신화를 추진한다.</div>
              <div class="tg-line">• <b>왜 중요:</b> 운영사의 규제 준수 의무를 강화하고 안전 관리 체계 변화를 요구.</div>
              <div class="tg-line amber">• 💰 <b>투자 관점:</b> 운영 비용·절차 증가 / utility 부담 (규제·중기)</div>
              <div class="tg-line green">• 🇰🇷 <b>한수원 시사점:</b> 운영·관리 체계 재정비 필요.</div>
              <div class="tg-src">🔗 출처 · news.google.co.kr</div>
            </div>
          </div>`),
        '실제 발송 화면 (2026-07-27 국내 브리핑)')}
      </div>
      <div>
        <ul class="bullets big-gap">
          <li><b>매일 07:25</b> 국내·해외 카드 브리핑</li>
          <li><b>카드 4줄</b>: 무슨 일 / 왜 중요 / 💰 투자 관점 / 🇰🇷 한수원 시사점 + 원문 링크</li>
          <li><b>금요일 17:00</b> 주간 판세 리포트</li>
          <li><b>구독자 5명</b> — 동료들이 같이 보는 중</li>
        </ul>
        <div class="stats">
          <div class="stat"><b>459건</b><span>2주간 수집·분류</span></div>
          <div class="stat"><b>55%</b><span>안 봐도 될 기사로 걸러냄</span></div>
          <div class="stat"><b>9건/일</b><span>사람이 보는 양</span></div>
        </div>
        <p class="micro">※ 저장소 기준 ${CONFIG.repoCheckedAt}, 운영 중이라 매일 달라집니다. 비용은 전부 무료 범위.</p>
        <div class="axiom">
          <div class="no">뉴스를 많이 보내는 봇</div>
          <div class="yes">정책 판단에 쓸 신호를 고르는 시스템</div>
        </div>
      </div>
    </div>`,
  notes: {
    core: '핵심 숫자: 459건 중 사람이 보는 건 9건. 자동화의 본질이 요약이 아니라 버리기라는 복선.',
    analogy: '스크랩 담당자가 "오늘 볼 것 9건"만 추려서 주는 셈.',
    tech: '(개발자 질문 대비) curated.json 459건 중 noise 251건. delivery_log 13일간 107건 발송. 캡: 국내 3 / 해외 6.',
    bridge: '어떻게 돌아가는지 구조를 한 장으로.',
  },
},

/* ---------- 4. 전체 구조 --------------------------------------------------- */
{
  id: 'flow',
  icon: 'stream', cmd: '어떻게 돌아가나요?', path: '~/nuclear-news-bot',
  nav: '전체 구조',
  kicker: '구조',
  title: '전체 구조는 알람 3개입니다',
  html: `
    <p class="lead">${term('크론(스케줄러)', '정해진 시간에 자동으로 일을 시작시키는 알람')} 3개가
    각자 시간에 정해진 절차를 실행합니다. 제 컴퓨터는 꺼져 있어도 됩니다.</p>

    <div class="tline">
      <div class="tl-row">
        <span class="tl-when">매시간</span>
        <span class="tl-step">뉴스 수집</span>
        <span class="tl-desc">앞 화면의 출처 5그룹 순회, 같은 사건 묶기(중복 제거 4겹). 알림 없음</span>
      </div>
      <div class="tl-row">
        <span class="tl-when">수집 직후</span>
        <span class="tl-step">AI 분류·요약</span>
        <span class="tl-desc">${term('LLM', '긴 글을 읽고 핵심을 정리하거나 새 문장을 쓰는 AI')}이 10건씩 묶어 중요도·한국어 요약·순위 재료 생성 — <b>AI가 하는 유일한 칸</b></span>
      </div>
      <div class="tl-row">
        <span class="tl-when">매일 07:25</span>
        <span class="tl-step">아침 브리핑 발송</span>
        <span class="tl-desc">점수·순위(사람이 쓴 계산식) → 카드 조립 → ${term('텔레그램 봇', '프로그램이 사람 대신 메시지를 보내는 계정')} 발송</span>
      </div>
      <div class="tl-row">
        <span class="tl-when">금 17:00</span>
        <span class="tl-step">주간 판세 리포트</span>
        <span class="tl-desc">한 주 수집분을 집계해 별도 발송</span>
      </div>
    </div>

    <div class="callout warn"><b>수집은 매시간 조용히, 알림은 하루 한 번.</b>
    AI 칸은 분류·요약 하나뿐이고, 순위 계산식은 사람이 썼습니다. 결과 확인과 판단도 사람 몫입니다.</div>

    ${tech('각 칸이 실제 코드에서 뭔가', `
      <ul>
        <li><b>매시간 수집</b> — <code>news_bot.py</code> (GitHub Actions, cron <code>0 * * * *</code>).
            해외 전문지 RSS 3곳(IAEA·WNN·ANS), Google News 기관 검색 4곳(한수원·원안위·산업부·원자력연구원),
            국내 키워드 피드, 네이버 뉴스 검색 API, 구독 뉴스레터 이메일.</li>
        <li><b>중복 제거 4겹</b> — 제목 일치 → 문자열 유사도 0.82 → 의미(임베딩) 유사도 0.85 → 발송 직전 한 번 더.</li>
        <li><b>AI 분류</b> — Gemini에 10건씩 묶어 1회 호출. 중요도 4등급·섹션·한국어 요약·랭킹용 feature를 한 번에.</li>
        <li><b>점수·순위</b> — LLM은 feature만 뽑고 <b>계산식은 Python + JSON 가중치</b>. 내역이 로그에 남아 "왜 이 기사가 뽑혔나" 추적 가능.</li>
        <li><b>발송</b> — plan / send / confirm 3단계로 나눠 중복 발송을 막음.</li>
      </ul>`)}`,
  notes: {
    core: '"AI가 다 한다"는 오해를 정리. AI 칸은 하나, 계산식은 사람, 시작 버튼은 알람.',
    analogy: '공장 라인 3개. AI는 라인 위 검수원 한 명이지 공장장이 아닙니다.',
    tech: '(개발자 질문 대비) LLM을 feature 추출로 한정한 이유: 재현성, 점수 근거 로그, 가중치 조정에 코드 수정 불필요.',
    bridge: '방금 나온 부품들을 이름과 함께 정리하면.',
  },
},

/* ---------- 5. 구성 요소와 용어 -------------------------------------------- */
{
  id: 'appendix',
  icon: 'blocks', cmd: '뭘로 만들었나요?', path: '~/nuclear-news-bot',
  nav: '구성 요소와 용어',
  kicker: '구성',
  title: '실제 구성 요소와 용어 정리',
  html: `
    <div class="two-col">
      <div class="card">
        <h3>실행 환경 (코드에서 확인)</h3>
        <table class="tbl">
          <tr><td>언어</td><td>Python 3.12</td></tr>
          <tr><td>실행</td><td>GitHub Actions (워크플로 3개)</td></tr>
          <tr><td>외부 라이브러리</td><td><code>requests</code> · <code>google-genai</code> · <code>feedparser</code></td></tr>
          <tr><td>AI 모델</td><td>Gemini (설정값으로 교체 가능)</td></tr>
          <tr><td>전달</td><td>Telegram Bot API</td></tr>
          <tr><td>상태 저장</td><td>저장소 내 JSON 파일 (별도 DB 없음)</td></tr>
          <tr><td>테스트</td><td>57개 · 외부 호출 없이 통과</td></tr>
          <tr><td>비밀정보</td><td>GitHub Secrets (발표자료 미포함)</td></tr>
        </table>
      </div>
      <div class="card">
        <h3>주요 파일</h3>
        <table class="tbl">
          <tr><td><code>news_bot.py</code></td><td>수집 · 중복 제거 · AI 분류</td></tr>
          <tr><td><code>daily_brief.py</code></td><td>선별 · 카드 조립 · 발송</td></tr>
          <tr><td><code>email_ingest.py</code></td><td>구독 뉴스레터 이메일 수집</td></tr>
          <tr><td><code>ranking.py</code>+<code>ranking_config.json</code></td><td>점수식 — 가중치는 JSON만 수정</td></tr>
          <tr><td><code>weekly_bot.py</code></td><td>주간 판세</td></tr>
          <tr><td><code>keywords.json</code> / <code>sources.json</code></td><td>검색 키워드 / 공신력 출처</td></tr>
          <tr><td><code>delivery_log.jsonl</code></td><td>발송 이력 + 점수 내역</td></tr>
        </table>
      </div>
    </div>

    <div class="card mt">
      <h3>용어 한 줄 정리</h3>
      <div class="glossary">
        <div><b>API</b> 서로 다른 서비스가 정보를 주고받는 통로</div>
        <div><b>RSS</b> 사이트가 새 글 목록을 기계가 읽기 좋게 공개하는 형식</div>
        <div><b>크론 · 스케줄러</b> 정해진 시간에 일을 시작시키는 알람</div>
        <div><b>LLM</b> 긴 글을 읽고 핵심을 정리하거나 새 문장을 쓰는 AI</div>
        <div><b>텔레그램 봇</b> 프로그램이 사람 대신 메시지를 보내는 계정</div>
        <div><b>에이전트</b> 목표와 도구를 받아 여러 단계를 스스로 수행하는 AI 작업자</div>
      </div>
    </div>`,
  notes: {
    core: '봇 설명 직후에 부품 이름을 한 번 정리하고 넘어가는 화면. 표를 다 읽지 말고 "서버 없이 GitHub Actions에서 돌고, 외부 라이브러리 3개, 상태는 JSON 파일"만 짚으면 됩니다. 용어는 뒤에서 나올 때마다 여기로 돌아올 수 있다고 안내.',
    analogy: '-',
    tech: '토큰, 비밀번호, 채팅 ID, 저장소 주소는 이 자료에 없습니다. 전부 GitHub Secrets, 로컬 .env는 저장소 제외.',
    bridge: '그런데 처음부터 이 모양이 아니었습니다. 세 번 갈아엎었습니다.',
  },
},

/* ---------- 6. 석 달의 여정 ------------------------------------------------ */
{
  id: 'journey',
  icon: 'loop', cmd: '한 번에 잘 됐나요?', path: '~/nuclear-news-bot',
  nav: '석 달의 여정',
  kicker: '시행착오',
  title: '석 달 동안 세 번 갈아엎었습니다',
  html: `
    <div class="versions">
      <div class="ver">
        <div class="ver-tag">v1</div>
        <div class="ver-when">5월, "일단 다 보내자"</div>
        <h3>기사마다 개별 알림</h3>
        <p>검색에 걸리는 대로 전부 발송. 하루 수십 건.</p>
        <div class="ver-fate bad">→ 너무 많아서 안 보게 됨</div>
      </div>
      <div class="ver-arrow">→</div>
      <div class="ver">
        <div class="ver-tag">v2</div>
        <div class="ver-when">6월, "묶어서 요약하자"</div>
        <h3>하루 1회 카드 브리핑</h3>
        <p>알림 3~4종을 카드 하나로 통합.</p>
        <div class="ver-fate warn">→ 새 문제: AI 호출 한도, 영어 카드, 국내 0건</div>
      </div>
      <div class="ver-arrow">→</div>
      <div class="ver now">
        <div class="ver-tag">v3</div>
        <div class="ver-when">7월, "덜 보내고 근거를 남기자"</div>
        <h3>선별 + 점수 근거 기록</h3>
        <p>기능을 붙이기도, 빼기도 함.</p>
        <div class="ver-fate good">→ 지금 모습 · 커밋 617개</div>
      </div>
    </div>

    <div class="callout">버전을 올린 계기는 전부 사고였습니다. 대표 사고 3건을 실제 화면으로 보겠습니다.</div>`,
  notes: {
    core: 'v1→v2→v3가 계획된 로드맵이 아니라 사고 수습의 기록이라는 게 포인트.',
    analogy: '자취 요리 1개월차(라면만), 2개월차(재료 사다 태움), 3개월차(반찬 3개 정착).',
    tech: '(개발자 질문 대비) 최초 커밋 2026-05-07. 큰 개편 2회(6/15 카드 통합, 7/14 랭킹·발송 구조)는 git 히스토리에서 확인 가능.',
    bridge: '첫 번째, 알림 폭탄.',
  },
},

/* ---------- 7. 시행착오 ① ------------------------------------------------ */
{
  id: 'trial1',
  icon: 'bell', cmd: '처음엔 어땠나요?', path: '~/nuclear-news-bot',
  nav: '시행착오 ① 알림 폭탄',
  kicker: '시행착오 ①',
  title: '처음엔 걸리는 대로 다 보냈습니다',
  html: `
    <div class="two-col">
      <div>
        ${shot('shot-v1-spam.png', '5월 초기 개별 알림 캡처', tg(`
          <div class="tg-date">5월 7일 — 채널이 만들어졌습니다</div>
          <div class="tg-msg small"><b>[정책]</b> 총성 끝나면 망치소리 커진다…'원전+재건' 불기둥 세운 건설주 <span class="tg-time">오전 6:27</span></div>
          <div class="tg-msg small"><b>[정책]</b> [프로젝트 X파일] 롯데건설 가세한 양수발전 수주경쟁구도 <span class="tg-time">오전 6:27</span></div>
          <div class="tg-msg small"><b>[정책]</b> 두산에너빌리티 주가, 상승세… 이유는 미국? <span class="tg-time">오후 5:01</span></div>
          <div class="tg-msg small"><b>[정책]</b> 미·이란 48시간 내 타결 임박…호르무즈 재개방 <span class="tg-time">오전 6:38</span></div>
          <div class="tg-more">… 같은 날 계속</div>`),
        '실제 초기 발송 (2026-05-07, 채널 첫날)')}
      </div>
      <div class="col-flex">
        <ul class="bullets big-gap">
          <li><b>하루 수십 건</b> — 받는 쪽이 스크롤을 포기</li>
          <li><b>"두산에너빌리티 주가"가 [정책] 라벨로</b> — 키워드에 걸렸다고 필요한 기사인 건 아니었음</li>
          <li><b>미·이란 협상이 원자력 뉴스로</b> — 키워드 매칭의 한계</li>
        </ul>
        <div class="lesson-box">
          <span class="lesson-tag">배운 것 1</span>
          <b>모으기보다 고르기가 어렵다.</b> 수집 코드는 하루, 선별 기준은 석 달째.
        </div>
      </div>
    </div>

    ${tech('이후 노이즈를 어떻게 걸렀나', `
      <ul>
        <li><b>검색어 제외 조건</b> — 키워드마다 <code>-주가 -채용 -배당 -병원 -부고</code> 같은 제외어 (<code>keywords.json</code>).</li>
        <li><b>앵커 필터</b> — 제목·본문에 원자력 핵심 단어가 실제로 있어야 통과.</li>
        <li><b>제목 패턴 컷</b> — [부고]·[인사]·[포토] 말머리, 채용·동호회·기념식은 바로 제외.</li>
        <li><b>LLM 4등급 분류</b> — 남는 것은 Gemini가 must_read / nice / market / noise 판정. 현재 55%가 noise.</li>
        <li>그래도 완벽하지 않아서 "애매하면 noise" 원칙으로 버리는 쪽을 택했습니다.</li>
      </ul>`)}`,
  notes: {
    core: '캡처의 "[정책] 두산에너빌리티 주가"가 핵심 증거. 키워드 매칭의 한계를 여기서 배웠습니다.',
    analogy: '메일 규칙을 "원자력 포함 시 중요"로 만들면 원자력병원 광고까지 중요 표시되는 것.',
    tech: '(개발자 질문 대비) 필터 순서: 제외어 → 앵커 → 제목 패턴 → 본문 길이 → LLM 4등급. 단계별 통과량은 crawl 로그에.',
    bridge: '6월에 카드로 묶었더니 이번엔 다른 데가 터졌습니다.',
  },
},

/* ---------- 8. 시행착오 ② ------------------------------------------------ */
{
  id: 'trial2',
  icon: 'fix', cmd: '뭐가 제일 아팠나요?', path: '~/nuclear-news-bot',
  nav: '시행착오 ② 조용한 실패',
  kicker: '시행착오 ②',
  title: '고장이 나도 에러 화면이 없습니다',
  html: `
    <div class="grid grid-3">
      <div>
        ${shot('shot-fail-gemini.png', 'Gemini 호출 실패 캡처', tg(`
          <div class="tg-msg"><div class="tg-title">📊 주간 인사이트 (2026-06-01 ~ 2026-06-07)</div>
          <div class="tg-hr"></div>
          <div class="tg-line dim">(Gemini 호출 실패)</div>
          <div class="tg-time-r">오후 10:15</div></div>`),
        '6/7 실제 화면')}
        <p class="shot-desc"><b>AI 한도 초과.</b> 본문이 빈 리포트가 그대로 발송됐습니다.</p>
      </div>
      <div>
        ${shot('shot-fail-english.png', '영어 제목 그대로 나간 카드 캡처', tg(`
          <div class="tg-msg"><div class="tg-title">📰 🌐 원자력 해외 브리핑 (2026-06-22)</div>
          <div class="tg-card"><b>📌 1. IAEA Releases First Public Tool to Map the World's Spent Nuclear Fuel</b>
          <div class="tg-line">• <b>무슨 일:</b> IAEA Releases First Public Tool to Map the World's</div>
          <div class="tg-src">🔗 출처 · iaea.org</div></div></div>`),
        '6/22 실제 화면')}
        <p class="shot-desc"><b>요약 실패 시 대체 동작이 문제.</b> 영어 제목이 잘린 채 나갔습니다.</p>
      </div>
      <div>
        ${shot('shot-fail-empty.png', '국내 동향 0건 캡처', tg(`
          <div class="tg-msg"><div class="tg-title">📰 🇰🇷 원자력 국내 브리핑 (2026-06-22)</div>
          <div class="tg-line dim"><i>오늘은 별도로 잡힌 국내 동향이 없습니다.</i></div>
          <div class="tg-time-r">오전 8:54</div></div>`),
        '6월 하순, 며칠 연속')}
        <p class="shot-desc"><b>뉴스 검색이 최신순이 아니라 관련도순.</b> 국내 뉴스가 며칠씩 0건인데 코드는 정상 종료.</p>
      </div>
    </div>

    <div class="lesson-box mt">
      <span class="lesson-tag">배운 것 2</span>
      <b>자동화는 고장 나도 에러 화면을 안 띄웁니다.</b> 이상한 결과가 그냥 발송됩니다.
      그래서 로그·발송 이력·사람의 확인, 즉 <b>깨진 걸 알아채는 장치</b>가 중요했습니다.
    </div>

    ${tech('세 사고를 각각 어떻게 고쳤나', `
      <ul>
        <li><b>AI 한도(429)</b> — 기사당 2회 호출을 <b>10건 묶어 1회</b>로 변경, 호출량 약 1/20. 한도 초과 시 대기 후 재시도.</li>
        <li><b>영어 카드</b> — 대체 동작을 "영문 제목 복사"에서 "실제 한글일 때만 표시"로 교체.</li>
        <li><b>국내 0건</b> — 국내 키워드 검색에 <code>when:1d</code>, 기관 검색에 <code>when:2d</code> 연산자 추가. 제일 크게 당한 함정.</li>
        <li><b>안전망</b> — 발송 이력을 점수 내역과 함께 기록해서 "어제 왜 그 기사가 나갔지?"를 사후 추적.</li>
      </ul>`)}`,
  notes: {
    core: '제일 공들일 슬라이드. "(Gemini 호출 실패)"가 그대로 발송된 캡처가 자동화의 실체를 보여줍니다.',
    analogy: '자동 급식기는 고장 나도 울지 않습니다. 고양이가 말라가는 걸 보고야 압니다.',
    tech: '(개발자 질문 대비) 429는 무료 티어 분당 한도. 관련도순 문제는 실측(100건 중 95건이 1주 이상 경과)으로 확인.',
    bridge: '세 번째는 반대로, 붙인 기능을 뗀 얘기.',
  },
},

/* ---------- 9. 시행착오 ③ ------------------------------------------------ */
{
  id: 'trial3',
  icon: 'trash', cmd: '뺀 기능도 있나요?', path: '~/nuclear-news-bot',
  nav: '시행착오 ③ 뺀 기능들',
  kicker: '시행착오 ③',
  title: '만든 기능을 2주 만에 지웠습니다',
  html: `
    <div class="two-col">
      <div>
        ${shot('shot-buttons.png', '피드백 버튼 24개 캡처', tg(`
          <div class="tg-msg small">🗳️ 피드백: 👍중요 · 👎노이즈 · 💰투자 유용 · 📌보고서감</div>
          <div class="tg-btngrid">
            <span>1 👍</span><span>1 👎</span><span>1 💰</span><span>1 📌</span>
            <span>2 👍</span><span>2 👎</span><span>2 💰</span><span>2 📌</span>
            <span>3 👍</span><span>3 👎</span><span>3 💰</span><span>3 📌</span>
            <span>4 👍</span><span>4 👎</span><span>4 💰</span><span>4 📌</span>
            <span>5 👍</span><span>5 👎</span><span>5 💰</span><span>5 📌</span>
            <span>6 👍</span><span>6 👎</span><span>6 💰</span><span>6 📌</span>
          </div>`),
        '실제 화면 (7월 중순) — 기사 6건 × 버튼 4종')}
      </div>
      <div class="col-flex">
        <ul class="bullets big-gap">
          <li><b>피드백 버튼</b> — 버튼 24개가 채팅 도배, 클릭 0건. <b>2주 만에 삭제</b></li>
          <li><b>소셜 수집(Reddit·X)</b> — 너무 느려서 발송 전체가 멈추는 원인. 자동 실행에서 제외</li>
          <li><b>알림 3~4종 → 2종</b> — 하루 1회 브리핑 + 주 1회 판세로 통합</li>
        </ul>
        <div class="lesson-box">
          <span class="lesson-tag">배운 것 3</span>
          <b>빼는 게 개선일 때가 많다.</b> 안 쓰는 기능은 화면만 어지럽힙니다.
        </div>
      </div>
    </div>`,
  notes: {
    core: '만들었지만 안 쓰여서 지웠다는 걸 담백하게. 팀에서 기능 요청이 쏟아질 때 근거가 되는 슬라이드.',
    analogy: '만능 채칼을 사놓고 한 번도 안 쓰면 서랍만 차지합니다.',
    tech: '(개발자 질문 대비) 피드백 버튼은 수거 스크립트까지 구현했었고 git 히스토리에 남아 있습니다. 소셜 경로는 수동 실행용으로만 존재.',
    bridge: '사고를 수습하면서 지금은 이런 것들이 돌아갑니다.',
  },
},

/* ---------- 10. 지금은 이렇게 합니다 --------------------------------------- */
{
  id: 'today',
  icon: 'folder', cmd: '지금은 어떻게 하나요?', path: '~/nuclear-news-bot',
  nav: '지금의 방식',
  kicker: '현재',
  title: '시행착오를 거쳐, 지금은 이렇게 합니다',
  html: `
    <div class="grid grid-3">
      <div class="card"><h3>구독 뉴스레터도 수집원</h3>
        <p>메일함에서 뉴스레터를 읽어 기사 링크 자동 추출</p></div>
      <div class="card"><h3>선정 이유를 기록</h3>
        <p>"왜 이게 뽑혔지?"를 로그로 추적 가능</p></div>
      <div class="card"><h3>보고서감 추천</h3>
        <p>부서 보고서로 다룰 만한 사안만 하루 최대 2건</p></div>
    </div>

    <div class="callout">
      이 밖에 출처 공신력 배지, 설정 파일화 등 — 전부 처음 설계가 아니라
      <b>쓰면서 필요해서 하나씩 붙인 것들</b>입니다.
    </div>

    ${tech('각 항목의 실제 구현', `
      <ul>
        <li><b>뉴스레터 수집</b> — <code>email_ingest.py</code>. 메일함에 IMAP으로 접속해 ANS 뉴스레터에서
            외부 매체 링크만 추출(이미 크롤하는 출처는 스킵). 원본 메일은 저장하지 않음.</li>
        <li><b>점수 기록</b> — <code>delivery_log.jsonl</code>의 breakdown 필드. 중요도·사건 유형·한국 관련성·출처 보너스·시간 감쇠가 항목별로.</li>
        <li><b>공신력 배지</b> — <code>sources.json</code>의 전문 출처 목록(IAEA·WNN 등) 매칭 시 카드에 ✅.</li>
        <li><b>보고서 추천</b> — 조건 통과 후보가 있을 때만 LLM 호출, 하루 2건 상한.</li>
        <li><b>설정 파일화</b> — 키워드·가중치·출처 목록은 JSON만 고치면 다음 실행부터 반영.</li>
      </ul>`)}`,
  notes: {
    core: '"지금은 어떻게 하나"에 대한 답. 포인트는 전부 나중에 필요해서 붙였다는 것 — 처음부터 설계하지 않았다는 게 메시지.',
    analogy: '주방 도구를 요리하다 부족해서 하나씩 산 것이지, 개업 전에 풀세트를 산 게 아닙니다.',
    tech: '(개발자 질문 대비) 뉴스레터 링크가 전부 트래커 경유라 언랩 처리가 따로 필요했습니다.',
    bridge: '석 달 치 배운 걸 여섯 줄로 정리하면.',
  },
},

/* ---------- 11. 배운 것 정리 ----------------------------------------------- */
{
  id: 'lessons',
  icon: 'doc', cmd: '뭘 배웠나요?', path: '~/nuclear-news-bot',
  nav: '배운 것 6가지',
  kicker: '정리',
  title: '배운 것 여섯 가지',
  html: `
    <div class="grid grid-3">
      <div class="card lesson"><span class="ln">01</span><h3>모으기보다 고르기가 어렵다</h3>
        <p>수집 코드는 하루, 선별 기준은 석 달째.</p></div>
      <div class="card lesson"><span class="ln">02</span><h3>실패해도 티가 안 난다</h3>
        <p>에러 화면 대신 이상한 결과가 발송된다.</p></div>
      <div class="card lesson"><span class="ln">03</span><h3>빼는 게 개선일 때가 많다</h3>
        <p>버튼 24개, 알림 4종, 소셜 수집을 지웠다.</p></div>
      <div class="card lesson"><span class="ln">04</span><h3>데이터 소스는 겉과 다르다</h3>
        <p>검색이 관련도순인 걸 몰라 국내 0건.</p></div>
      <div class="card lesson"><span class="ln">05</span><h3>AI 요약은 원문을 못 대신한다</h3>
        <p>수치·기관명·정책명은 원문 확인. 링크가 요약보다 중요.</p></div>
      <div class="card lesson"><span class="ln">06</span><h3>작게 시작해서 완주했다</h3>
        <p>"원자력 뉴스, 텔레그램, 하루 1회"로 좁혔다.</p></div>
    </div>

    <div class="callout warn">
      <b>아직 안 끝난 것도 있습니다.</b> 해외 기사가 국내 브리핑에 섞이는 분류 오류(미해결),
      AI 오역 가능성. 운영은 계속 고치는 일입니다.
    </div>`,
  notes: {
    core: '새 내용 없이 앞 이야기를 여섯 줄로 압축해 기억에 남기는 슬라이드. 30초 컷.',
    analogy: '-',
    tech: '(개발자 질문 대비) 미해결: 지역 분류가 도메인 기반이라 애매하면 국내로 폴백. 캐나다 기사가 국내 브리핑에 섞인 사례가 실제 발송분에 있습니다.',
    bridge: 'Claude Code가 정확히 뭘 했는지 짚고 넘어가겠습니다.',
  },
},

/* ---------- 12. Claude Code의 역할 ---------------------------------------- */
{
  id: 'claude-code',
  icon: 'bike', cmd: 'Claude Code가 다 해주나요?', path: '~/nuclear-news-bot',
  nav: 'Claude Code의 역할',
  kicker: '오해 방지',
  title: 'Claude Code는 만들 때 쓴 도구, 매일 보내는 건 따로 있습니다',
  html: `
    <div class="two-col">
      <div class="panel">
        <h3 class="sub amber-t">🛠️ 만드는 동안 — Claude Code</h3>
        <ol class="steps">
          <li>하고 싶은 일을 <b>말로 설명</b></li>
          <li>프로젝트 파일을 <b>같이 읽고</b> 수정 제안</li>
          <li><b>코드 작성·수정</b></li>
          <li><b>오류 원인 추적</b> <small>앞의 사고 3건도 이렇게 잡음</small></li>
          <li>내가 확인하고 <b>다시 지시</b> <small>제일 많았던 단계 — 이걸 수십 번 반복</small></li>
        </ol>
      </div>
      <div class="panel">
        <h3 class="sub green-t">⚙️ 매일 도는 동안 — Claude Code 없음</h3>
        <div class="runstack">
          <div class="rs"><b>GitHub Actions</b><span>알람 + 임시 컴퓨터</span></div>
          <div class="rs"><b>Python 코드</b><span>수집·거르기·순위·조립</span></div>
          <div class="rs"><b>Gemini API</b><span>분류·요약 담당 AI</span></div>
          <div class="rs"><b>Telegram Bot</b><span>메시지 전달</span></div>
        </div>
      </div>
    </div>

    <div class="banner-dark">
      드릴로 책장을 짰다고 드릴이 책장을 붙잡고 있는 건 아닙니다.
      <b>운영 중에 Claude Code가 호출되는 지점은 없습니다.</b>
    </div>`,
  notes: {
    core: '비개발자 두 분이 "Claude Code가 매일 보내주는구나"로 이해하면 안 됩니다. 도구(만들 때)와 부품(돌 때)을 나눠 두 번 말하세요. 덧붙일 말: 한 번 말해서 완성된 적은 없고, 막힌 지점의 절반은 내가 뭘 원하는지 몰랐던 것 — 일을 정의하는 연습이었고, 강의를 같이 듣자는 이유로 연결됩니다.',
    analogy: '드릴과 책장 비유가 잘 통합니다.',
    tech: '(개발자 질문 대비) 런타임 의존성 3개(requests/google-genai/feedparser), Anthropic SDK 없음. 운영 경로에 Claude 호출이 물리적으로 없습니다.',
    bridge: 'Hermes 써보신 분을 위해 자동화 단계 얘기를 잠깐.',
  },
},

/* ---------- 13. 자동화 3단계 ---------------------------------------------- */
{
  id: 'levels',
  icon: 'stairs', cmd: '이게 AI 에이전트인가요?', path: '~/nuclear-news-bot',
  nav: '자동화 3단계',
  kicker: '개념',
  title: '자동화에도 단계가 있습니다',
  html: `
    <div class="levels">
      <div class="lv"><div class="lv-n">1</div><h3>사람이 매번 실행</h3>
        <p>내가 열고, 시키고, 확인.</p><span class="lv-ex">챗봇에 붙여넣고 "요약해줘"</span></div>
      <div class="lv"><div class="lv-n">2</div><h3>시스템 실행, 사람 확인</h3>
        <p>버튼 하나로 돌고 결과만 봄.</p><span class="lv-ex">스크립트 수동 실행</span></div>
      <div class="lv current"><div class="lv-n">3</div><h3>시간이 되면 스스로 시작</h3>
        <p>아무도 안 켜도 돌고, 실패하면 재시도.</p>
        <span class="lv-ex"><b>이 봇의 위치</b> — 알람 3개: 매시간 / 07:25 / 금 17:00</span>
        <span class="lv-flag">현재</span></div>
    </div>

    <div class="card mt">
      <h3>요즘 말하는 'AI ${term('에이전트', '목표와 도구를 받아 여러 단계를 스스로 수행하는 AI 작업자')}'와는 뭐가 다른가</h3>
      <p>사람이 안 켜도 도는 건 같지만, 이 봇은 <b>정해진 시간에 정해진 절차</b>만 밟습니다.
      스스로 계획을 세우거나 도구를 고르지 않습니다.</p>
    </div>

    <div class="callout warn">
      <b>스스로 시작해도, 스스로 책임지지는 않습니다.</b>
      실패 알아채기·로그·원문 확인은 여전히 사람과 운영의 몫입니다.
    </div>`,
  notes: {
    core: 'Hermes 써보신 분을 보며 이 화면에서 직접 언급: Hermes가 에이전트 쪽, 이 봇은 3단계 파이프라인. 다음 단계(이벤트 기반·에이전트형) 얘기는 팀 확장 화면에서.',
    analogy: '1은 손빨래, 2는 세탁기 버튼, 3은 예약 세탁. "빨래 쌓인 걸 보고 알아서 돌리는" 단계는 아직 아님.',
    tech: '(개발자 질문 대비) 트리거는 스케줄뿐. 재시도: AI 한도 대기 후 재시도, git push 5회, 발송은 36시간 안에서만.',
    bridge: '이걸 팀으로 키우면 뭐가 달라지는지.',
  },
},

/* ---------- 14. 팀으로 확장한다면 ----------------------------------------- */
{
  id: 'scale',
  icon: 'users', cmd: '팀으로 키우면요?', path: '~/team-briefing',
  nav: '팀으로 확장한다면',
  kicker: '연결',
  title: '팀 플랫폼으로 가면 뭐가 달라지나',
  html: `
    <div class="two-col">
      <div class="panel">
        <h3 class="sub green-t">지금 (사실)</h3>
        <ul class="bullets">
          <li>원자력 1개 분야</li>
          <li>공개 출처 + 구독 뉴스레터</li>
          <li>텔레그램 1채널, 구독자 5명</li>
          <li>무료 티어로 운영</li>
        </ul>
      </div>
      <div class="panel hypo-col">
        <div class="hypo-flag">아직 같이 정해야 할 가설</div>
        <h3 class="sub">팀 플랫폼이라면</h3>
        <ul class="bullets hypo-list">
          <li>정책·에너지·전력시장으로 확대</li>
          <li>산업부·전력거래소·한전·IAEA·IEA·언론 등 출처 확대</li>
          <li>주제·사용자별 브리핑, 검색·저장</li>
          <li>팀 검토, 출처 검증, 모니터링</li>
        </ul>
      </div>
    </div>

    ${more('규모가 커지면 생길 일', `
      <ul>
        <li>출처가 늘면 중복·분류 문제가 같이 커집니다. 제가 겪은 사고들이 더 크게 재현될 가능성이 높습니다.</li>
        <li>사용자가 늘면 "누구에게 무엇이 중요한가"가 새 문제로 등장합니다.</li>
        <li>공개 웹도 이용약관·저작권·기술 제한이 있어 "긁어오면 된다"고 전제할 수 없습니다. 공식 RSS·공개 API·정식 제휴 우선.</li>
        <li>효과를 "몇 % 절감"처럼 숫자로 말할 근거는 아직 없습니다. 측정 기준부터 같이 정할 문제입니다.</li>
      </ul>`)}`,
  notes: {
    core: '왼쪽은 사실, 오른쪽은 점선(가설). 오른쪽을 확정 계획처럼 읽지 않게 말로도 짚어주세요.',
    analogy: '집밥 레시피를 급식으로 옮기는 것. 재료 20배가 아니라 문제 종류가 달라집니다.',
    tech: '(개발자 질문 대비) 먼저 깨질 곳: git 파일을 DB로 쓰는 구조(동시 쓰기), 단일 큐 모델(개인화 불가). 지금 규모에선 일부러 안 바꿨습니다.',
    bridge: '그래서 오늘 제안은 기능 목록이 아니라 질문 목록입니다.',
  },
},

/* ---------- 15. 먼저 정할 질문 -------------------------------------------- */
{
  id: 'questions',
  icon: 'question', cmd: '뭐부터 정할까요?', path: '~/team-briefing',
  nav: '먼저 정할 질문',
  kicker: '논의',
  title: '기능보다 먼저 정할 것들',
  html: `
    <div class="qgrid">
      <div class="qcard"><span class="qn">Q1</span><b>무엇을 줄일까</b><p>가장 먼저 없앨 반복 업무는?</p></div>
      <div class="qcard"><span class="qn">Q2</span><b>누가 읽나</b><p>실제 독자는 누구?</p></div>
      <div class="qcard"><span class="qn">Q3</span><b>얼마나 자주</b><p>매일 / 매주 / 수시?</p></div>
      <div class="qcard"><span class="qn">Q4</span><b>어디까지 해석</b><p>요약까지? 시사점까지?</p></div>
      <div class="qcard"><span class="qn">Q5</span><b>사람의 자리</b><p>자동화하면 안 되는 단계는?</p></div>
      <div class="qcard warnq"><span class="qn">Q6</span><b>금지 자료</b><p>수집·AI 입력이 안 되는 자료는?</p></div>
      <div class="qcard"><span class="qn">Q7</span><b>성공의 척도</b><p>잘 된다는 걸 뭘로 재나?</p></div>
      <div class="qcard hi"><span class="qn">Q8</span><b>첫 실험 범위</b><p>어느 자료·주제 하나로 시작?</p></div>
    </div>

    <div class="callout">
      해보니 <b>Q8이 제일 중요했습니다.</b> 저는 "원자력 뉴스, 텔레그램, 하루 1회"로 좁혀서 완주했습니다.
    </div>`,
  notes: {
    core: '여기서 발표를 멈추고 의견을 받아도 됩니다. 특히 Q1과 Q8. 답을 준비해 가지 않는 게 요점. 2~3분 배정.',
    analogy: '메뉴판보다 먼저 "누가 몇 시에 뭘 먹으러 오는가"를 정하는 것.',
    tech: '(개발자 질문 대비) Q7이 기술적으로 제일 어려움. 정답 라벨 없는 품질 측정은 별도 설계 필요.',
    bridge: '마지막으로 다음 걸음.',
  },
},

/* ---------- 16. 다음 단계 ------------------------------------------------- */
{
  id: 'next',
  icon: 'idea', cmd: '다음은요?', path: '~/team-briefing',
  nav: '다음 단계',
  kicker: '제안',
  title: '작은 것 하나부터 같이 검증해 봅시다',
  html: `
    <div class="two-col">
      <div class="panel">
        <h3 class="sub amber-t">같이 들을 강의에서 얻을 것</h3>
        <ul class="bullets">
          <li><b>AI에게 일을 명확히 설명하는 법</b> <small>— 해보니 이게 8할</small></li>
          <li>아이디어를 실제로 돌아가게 만드는 법</li>
          <li>자동화 결과를 검증하고 고치는 법</li>
          <li>개인 실험을 팀 프로세스로</li>
        </ul>
        <a class="linkbox" href="${CONFIG.lectureUrl}" target="_blank" rel="noopener">
          <span>참고 강의자료</span><code>claudecode-lecture.vercel.app</code>
        </a>
      </div>
      <div class="panel">
        <h3 class="sub green-t">제안하는 다음 걸음 <span class="tag tag-hypo">논의 대상</span></h3>
        <ol class="steps">
          <li><b>업무 하나 고르기</b> <small>반복적이고 실패해도 안전한 것</small></li>
          <li><b>공개 자료로만 시작</b> <small>사내 자료는 규정 검토 후</small></li>
          <li><b>4주 돌려보고 판단</b> <small>버리는 것도 결과</small></li>
          <li><b>배운 걸로 범위 확장</b></li>
        </ol>
      </div>
    </div>

    <div class="banner-dark">
      플랫폼을 한 번에 만들기보다, <b>작은 자동화 하나부터 같이 검증하면서</b>
      우리한테 필요한 정책 브리핑의 모양을 찾아갑시다.
    </div>`,
  notes: {
    core: '마무리 톤은 "같이 정합시다". 4주 실험도 제안이지 확정이 아닙니다.',
    analogy: '집 짓기 전에 모형 만들기. 모형이 별로면 버리는 게 정상.',
    tech: '(개발자 질문 대비) 구성·용어는 5번 화면에 있으니 질문 나오면 T 키 목차로 이동.',
    bridge: '질문 받겠습니다.',
  },
},

];

/* =============================================================================
 * ICONS — 레퍼런스와 동일한 라인아트 스타일 (100x100, ink/accent/svg-mute)
 * ========================================================================== */

const ICONS = {
  terminal:`<svg viewBox="0 0 100 100"><rect x="14" y="24" width="72" height="54" rx="8" fill="none" stroke="var(--ink)" stroke-width="3"/><line x1="14" y1="38" x2="86" y2="38" stroke="var(--ink)" stroke-width="3"/><circle cx="23" cy="31" r="2.3" fill="var(--accent)"/><circle cx="31" cy="31" r="2.3" fill="var(--svg-mute)"/><circle cx="39" cy="31" r="2.3" fill="var(--svg-mute)"/><path d="M25 52 l8 7 -8 7" fill="none" stroke="var(--accent)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/><rect class="tcur" x="41" y="59" width="17" height="4.5" rx="2" fill="var(--accent)"/></svg>`,

  search:`<svg viewBox="0 0 100 100"><g class="bob"><circle cx="46" cy="44" r="22" fill="none" stroke="var(--ink)" stroke-width="3"/><line x1="62" y1="60" x2="80" y2="78" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/><path d="M38 44 h16 M46 36 v16" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g></svg>`,

  chat:`<svg viewBox="0 0 100 100"><g class="pulse"><rect x="16" y="26" width="46" height="30" rx="9" fill="none" stroke="var(--ink)" stroke-width="3"/><path d="M26 56 l0 9 9 -9" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><line x1="26" y1="37" x2="52" y2="37" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round"/><line x1="26" y1="45" x2="45" y2="45" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round"/></g><g class="pulse2"><rect x="46" y="50" width="40" height="27" rx="9" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/><path d="M76 77 l0 8 -9 -8" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linejoin="round"/><line x1="55" y1="60" x2="77" y2="60" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="55" y1="68" x2="70" y2="68" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g></svg>`,

  stream:`<svg viewBox="0 0 100 100"><rect x="16" y="22" width="68" height="56" rx="9" fill="none" stroke="var(--ink)" stroke-width="3"/><clipPath id="cpst"><rect x="20" y="30" width="60" height="44"/></clipPath><g clip-path="url(#cpst)"><g class="stream"><path d="M26 34 h20 M26 42 h34 M26 50 h16 M26 58 h30 M26 66 h22 M26 74 h34 M26 82 h18" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round"/><path d="M26 42 h34" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><path d="M26 66 h22" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g></g></svg>`,

  blocks:`<svg viewBox="0 0 100 100"><rect class="pop d1" x="34" y="62" width="32" height="20" rx="4" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/><rect class="pop d2" x="40" y="40" width="32" height="20" rx="4" fill="none" stroke="var(--ink)" stroke-width="3"/><rect class="pop d3" x="30" y="18" width="32" height="20" rx="4" fill="none" stroke="var(--svg-mute)" stroke-width="3"/></svg>`,

  loop:`<svg viewBox="0 0 100 100"><g class="spin-fast"><path d="M50 22 a28 28 0 1 1 -26 17" fill="none" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/><path d="M50 12 l0 18 -14 -6 Z" fill="var(--accent)"/></g><circle cx="50" cy="50" r="6" fill="var(--ink)"/></svg>`,

  bell:`<svg viewBox="0 0 100 100"><g class="shake"><path d="M34 56 v-12 a16 16 0 0 1 32 0 v12 l7 10 H27 Z" fill="var(--accent-soft)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><path d="M44 72 a6 6 0 0 0 12 0" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></g><g class="glow"><path d="M22 32 a30 30 0 0 1 9 -11 M78 32 a30 30 0 0 0 -9 -11" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g></svg>`,

  fix:`<svg viewBox="0 0 100 100"><g class="shake"><path d="M50 22 L80 74 H20 Z" fill="rgba(214,92,70,.12)" stroke="var(--coral)" stroke-width="3" stroke-linejoin="round"/><line x1="50" y1="40" x2="50" y2="56" stroke="var(--coral)" stroke-width="4" stroke-linecap="round"/><circle cx="50" cy="65" r="2.6" fill="var(--coral)"/></g><g class="pop d3"><circle cx="72" cy="70" r="15" fill="var(--card)" stroke="var(--accent)" stroke-width="3"/><path d="M65 70 l5 6 10 -12" fill="none" stroke="var(--accent)" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></g></svg>`,

  trash:`<svg viewBox="0 0 100 100"><g class="bob"><rect x="37" y="18" width="26" height="7" rx="3" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/><line x1="26" y1="29" x2="74" y2="29" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g><path d="M32 37 l3 41 h30 l3 -41" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><line class="draw d1" x1="43" y1="45" x2="44" y2="70" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round"/><line class="draw d2" x1="57" y1="45" x2="56" y2="70" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round"/></svg>`,

  folder:`<svg viewBox="0 0 100 100"><path d="M16 44 h20 l6 -8 h20 a4 4 0 0 1 4 4 v4" fill="none" stroke="var(--svg-mute)" stroke-width="3" stroke-linejoin="round"/><rect class="lid" x="16" y="44" width="68" height="36" rx="6" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/></svg>`,

  doc:`<svg viewBox="0 0 100 100"><path d="M28 18 h30 l16 16 v48 a4 4 0 0 1 -4 4 H28 a4 4 0 0 1 -4 -4 V22 a4 4 0 0 1 4 -4 Z" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><path d="M58 18 v16 h16" fill="none" stroke="var(--svg-mute)" stroke-width="3" stroke-linejoin="round"/><path class="draw d1" d="M34 44 h32" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" fill="none"/><path class="draw d2" d="M34 54 h32" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round" fill="none"/><path class="draw d3" d="M34 64 h24" stroke="var(--svg-mute)" stroke-width="3" stroke-linecap="round" fill="none"/></svg>`,

  bike:`<svg viewBox="0 0 100 100"><g class="spin"><circle cx="26" cy="66" r="16" fill="none" stroke="var(--ink)" stroke-width="3"/><line x1="26" y1="52" x2="26" y2="80" stroke="var(--svg-mute)" stroke-width="2.5"/><line x1="12" y1="66" x2="40" y2="66" stroke="var(--svg-mute)" stroke-width="2.5"/></g><g class="spin"><circle cx="74" cy="66" r="16" fill="none" stroke="var(--accent)" stroke-width="3"/><line x1="74" y1="52" x2="74" y2="80" stroke="rgba(206,138,44,.5)" stroke-width="2.5"/><line x1="60" y1="66" x2="88" y2="66" stroke="rgba(206,138,44,.5)" stroke-width="2.5"/></g><path d="M26 66 L46 66 L58 40 M42 66 L60 40 L74 66" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="M55 40 h10" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></svg>`,

  stairs:`<svg viewBox="0 0 100 100"><rect class="pop d1" x="16" y="62" width="20" height="18" rx="3" fill="none" stroke="var(--svg-mute)" stroke-width="3"/><rect class="pop d2" x="40" y="46" width="20" height="34" rx="3" fill="none" stroke="var(--ink)" stroke-width="3"/><rect class="pop d3" x="64" y="26" width="20" height="54" rx="3" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/></svg>`,

  users:`<svg viewBox="0 0 100 100"><g class="pulse"><circle cx="38" cy="40" r="13" fill="none" stroke="var(--ink)" stroke-width="3"/><path d="M18 78 a20 20 0 0 1 40 0" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></g><g class="pulse2"><circle cx="70" cy="44" r="10" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="3"/><path d="M56 78 a15 15 0 0 1 29 0" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g></svg>`,

  question:`<svg viewBox="0 0 100 100"><rect x="20" y="22" width="60" height="44" rx="10" fill="none" stroke="var(--ink)" stroke-width="3"/><path d="M34 66 l0 10 10 -10" fill="none" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><path class="glow" d="M43 39 a8 8 0 1 1 10 7 c-2 .8 -3 2 -3 4" fill="none" stroke="var(--accent)" stroke-width="3.5" stroke-linecap="round"/><circle cx="50" cy="57" r="2.4" fill="var(--accent)"/></svg>`,

  idea:`<svg viewBox="0 0 100 100"><g class="ray"><line x1="50" y1="10" x2="50" y2="20" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="24" y1="20" x2="31" y2="27" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="76" y1="20" x2="69" y2="27" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="16" y1="44" x2="26" y2="44" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="84" y1="44" x2="74" y2="44" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/></g><path d="M50 26 a20 20 0 0 1 12 36 c-2 2 -3 4 -3 7 H41 c0 -3 -1 -5 -3 -7 a20 20 0 0 1 12 -36 Z" fill="var(--accent-soft)" stroke="var(--ink)" stroke-width="3" stroke-linejoin="round"/><path class="glow" d="M45 50 a7 8 0 0 1 10 0" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round"/><line x1="43" y1="74" x2="57" y2="74" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/><line x1="45" y1="80" x2="55" y2="80" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/></svg>`,
};
