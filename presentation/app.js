/* =============================================================================
 * app.js — 슬라이드 엔진 (레퍼런스 스타일: 타이핑 명령줄 + 도트 내비)
 *
 *  키보드   → / Space / PageDown : 다음      ← / PageUp : 이전
 *            Home / End : 처음·끝            N : 발표자 노트
 *            T : 목차     F : 전체화면       Ctrl+P : 인쇄·PDF   Esc : 닫기
 *  URL      #/3 형태로 현재 화면 유지 (새로고침·공유 시 복원)
 * ========================================================================== */

(function () {
  'use strict';

  const stage    = document.getElementById('stage');
  const progress = document.getElementById('progress');
  const counter  = document.getElementById('counter');
  const winTitle = document.getElementById('win-title');
  const notesEl  = document.getElementById('notes');
  const tocEl    = document.getElementById('toc');
  const tocList  = document.getElementById('toc-list');
  const dotsNav  = document.getElementById('dots-nav');
  const prevBtn  = document.getElementById('btn-prev');
  const nextBtn  = document.getElementById('btn-next');

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pad = (n) => String(n).padStart(2, '0');

  let index = 0;
  let notesOpen = false;
  let typeTimer = null;

  const NOTE_FIELDS = [
    ['core',    '이 화면의 핵심'],
    ['analogy', '쉬운 비유'],
    ['tech',    '개발자 질문 대비'],
    ['bridge',  '다음 화면 연결'],
  ];

  /* ---- 렌더 -------------------------------------------------------------- */

  function noteBlock(notes, cls, heading) {
    if (!notes) return '';
    const rows = NOTE_FIELDS
      .filter(([k]) => notes[k] && notes[k] !== '-')
      .map(([k, l]) => `<div class="note-row"><span>${l}</span><div>${notes[k]}</div></div>`)
      .join('');
    return `<div class="${cls}"><h4>${heading}</h4>${rows}</div>`;
  }

  function build() {
    stage.innerHTML = DECK.map((s, i) => {
      const icon = (typeof ICONS !== 'undefined' && s.icon && ICONS[s.icon]) ? ICONS[s.icon] : '';
      // 표지: 캐릭터 이미지(assets/bot-avatar.png) 우선, 없으면 아이콘 폴백
      const iconCell = s.kind === 'cover'
        ? `<div class="cover-art">
             <img src="assets/bot-avatar.png" alt="원자력 뉴스봇 캐릭터"
                  onerror="this.closest('.cover-art').classList.add('noart')">
             <div class="fallback-icon icon">${icon}</div>
           </div>`
        : `<div class="icon">${icon}</div>`;

      const path = s.path ? `<span class="path">${s.path}</span> ` : '';
      const cmdline = `<div class="cmdline"><span class="dollar">$</span> ${path}` +
        `<span class="cmd" data-text="${(s.cmd || '').replace(/"/g, '&quot;')}">${s.cmd || ''}</span>` +
        `<span class="tcur"></span></div>`;

      const pill = s.kicker ? `<span class="step-pill">${s.kicker}</span>` : '';
      const title = s.kind === 'cover' ? '' : `<h2 class="slide-title">${s.title}</h2>`;

      return `<section class="slide" id="slide-${i}" role="group"
                       aria-roledescription="slide" aria-label="${i + 1}/${DECK.length} ${s.nav}"
                       ${i === 0 ? '' : 'aria-hidden="true"'}>
                ${iconCell}
                <div class="content">
                  ${cmdline}
                  ${pill}${title}${s.html}
                  ${noteBlock(s.notes, 'print-notes', '발표자 노트')}
                </div>
              </section>`;
    }).join('');

    // 도트 내비게이션
    dotsNav.innerHTML = '';
    DECK.forEach((s, i) => {
      const d = document.createElement('button');
      d.type = 'button';
      d.setAttribute('aria-label', `${i + 1}. ${s.nav}`);
      d.addEventListener('click', () => go(i));
      dotsNav.appendChild(d);
    });

    // 목차
    tocList.innerHTML = DECK.map((s, i) =>
      `<button class="toc-item" data-go="${i}" type="button">
         <span class="tn">${pad(i + 1)}</span><span>${s.nav}</span>
       </button>`).join('');
    tocList.querySelectorAll('[data-go]').forEach((b) =>
      b.addEventListener('click', () => { go(+b.dataset.go); closeToc(); }));

    document.title = `${CONFIG.title} — ${CONFIG.presenter}`;
  }

  /* ---- 명령줄 타이핑 ------------------------------------------------------ */

  function typeCmd(el) {
    clearTimeout(typeTimer);
    const t = el.getAttribute('data-text') || '';
    if (reduce) { el.textContent = t; return; }
    el.textContent = '';
    let k = 0;
    (function tick() {
      el.textContent = t.slice(0, k);
      if (k++ < t.length) typeTimer = setTimeout(tick, 30);
    })();
  }

  /* ---- 이동 -------------------------------------------------------------- */

  function go(n, push) {
    index = Math.max(0, Math.min(DECK.length - 1, n));

    DECK.forEach((_, i) => {
      const el = document.getElementById('slide-' + i);
      const on = i === index;
      el.classList.toggle('active', on);
      el.setAttribute('aria-hidden', on ? 'false' : 'true');
    });

    progress.style.width = ((index + 1) / DECK.length * 100) + '%';
    counter.textContent = `${pad(index + 1)} / ${pad(DECK.length)}`;
    winTitle.textContent = `nuclear-news-bot — ${DECK[index].id}`;
    prevBtn.disabled = index === 0;
    nextBtn.disabled = index === DECK.length - 1;

    [...dotsNav.children].forEach((d, i) => d.classList.toggle('on', i === index));
    tocList.querySelectorAll('.toc-item').forEach((b, i) =>
      b.classList.toggle('current', i === index));

    const cmd = document.querySelector('#slide-' + index + ' .cmd');
    if (cmd) typeCmd(cmd);

    renderNotes();

    const hash = '#/' + (index + 1);
    if (push !== false && location.hash !== hash) history.replaceState(null, '', hash);
    try { sessionStorage.setItem('nnb-slide', String(index)); } catch (e) { /* 무시 */ }
  }

  const next = () => go(index + 1);
  const prev = () => go(index - 1);

  /* ---- 발표자 노트 -------------------------------------------------------- */

  function renderNotes() {
    if (!notesOpen) return;
    const n = DECK[index].notes;
    notesEl.innerHTML = n
      ? noteBlock(n, 'notes-inner', `발표자 노트 — ${index + 1}. ${DECK[index].nav}`)
      : '<h4>발표자 노트 없음</h4>';
  }

  function toggleNotes(force) {
    notesOpen = (force === undefined) ? !notesOpen : force;
    notesEl.classList.toggle('open', notesOpen);
    renderNotes();
  }

  /* ---- 목차 -------------------------------------------------------------- */

  let tocReturnFocus = null;

  function openToc() {
    tocReturnFocus = document.activeElement;
    tocEl.classList.add('open');
    const cur = tocList.querySelector('.toc-item.current') || tocList.firstElementChild;
    if (cur) cur.focus();
  }
  function closeToc() {
    tocEl.classList.remove('open');
    if (tocReturnFocus && document.contains(tocReturnFocus)) tocReturnFocus.focus();
    tocReturnFocus = null;
  }
  const toggleToc = () => (tocEl.classList.contains('open') ? closeToc() : openToc());

  // 목차 모달 focus trap
  tocEl.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab' || !tocEl.classList.contains('open')) return;
    const f = tocEl.querySelectorAll('button');
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  });
  tocEl.addEventListener('click', (e) => { if (e.target === tocEl) closeToc(); });
  document.getElementById('toc-close').addEventListener('click', closeToc);

  /* ---- 전체화면 ----------------------------------------------------------- */

  function toggleFullscreen() {
    const el = document.documentElement;
    if (!document.fullscreenElement) {
      (el.requestFullscreen || el.webkitRequestFullscreen || function () {}).call(el);
    } else {
      (document.exitFullscreen || document.webkitExitFullscreen || function () {}).call(document);
    }
  }

  /* ---- 키보드 ------------------------------------------------------------- */

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    const inSummary = tag === 'summary';

    switch (e.key) {
      case 'ArrowRight': case 'PageDown': next(); e.preventDefault(); break;
      case ' ': case 'Spacebar':
        if (inSummary) break;
        next(); e.preventDefault(); break;
      case 'ArrowLeft': case 'PageUp': prev(); e.preventDefault(); break;
      case 'Home': go(0); e.preventDefault(); break;
      case 'End': go(DECK.length - 1); e.preventDefault(); break;
      case 'Escape':
        if (tocEl.classList.contains('open')) { closeToc(); e.preventDefault(); }
        else if (notesOpen) { toggleNotes(false); e.preventDefault(); }
        break;
      default: {
        const k = e.key.toLowerCase();
        if (k === 'n') { toggleNotes(); e.preventDefault(); }
        else if (k === 't' || k === 'o') { toggleToc(); e.preventDefault(); }
        else if (k === 'f') { toggleFullscreen(); e.preventDefault(); }
      }
    }
  });

  prevBtn.addEventListener('click', prev);
  nextBtn.addEventListener('click', next);

  /* ---- 터치 스와이프 ------------------------------------------------------ */

  let tx = 0, ty = 0;
  stage.addEventListener('touchstart', (e) => {
    tx = e.changedTouches[0].clientX; ty = e.changedTouches[0].clientY;
  }, { passive: true });
  stage.addEventListener('touchend', (e) => {
    const dx = e.changedTouches[0].clientX - tx;
    const dy = e.changedTouches[0].clientY - ty;
    if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.5) (dx < 0 ? next : prev)();
  }, { passive: true });

  /* ---- 시작 위치 복원 ----------------------------------------------------- */

  function initialIndex() {
    const m = /^#\/(\d+)/.exec(location.hash);
    if (m) return +m[1] - 1;
    const q = new URLSearchParams(location.search).get('s');
    if (q && /^\d+$/.test(q)) return +q - 1;
    try {
      const saved = sessionStorage.getItem('nnb-slide');
      if (saved !== null) return +saved;
    } catch (e) { /* 무시 */ }
    return 0;
  }

  window.addEventListener('hashchange', () => {
    const m = /^#\/(\d+)/.exec(location.hash);
    if (m && +m[1] - 1 !== index) go(+m[1] - 1, false);
  });

  build();
  go(initialIndex());
})();
