/**
 * Nuclens 카드 렌더러 — carousel-lite(tenfoldmarc) 포크.
 *
 * 원본에서 남긴 것: CLI 구조, theme.json 병합, [[강조]] 파싱, puppeteer 루프.
 * 갈아엎은 것: 레이아웃 전체. 원본은 하단을 `.terminal` 개발자 밈 블록으로
 * 채우는 전제라, 그걸 끄면 화면 60%가 빈 채로 남는다(실측). 3행 그리드
 * (머리·본문·꼬리)로 바꾸고 커버/본문/마지막장을 서로 다른 판형으로 만들었다.
 *
 *   node build.js                 # ./theme.json + ./slides.json -> ./out/*.png
 *   node build.js my-slides.json
 *   node build.js --sample        # 내장 한글 샘플 3장 (테마 미리보기)
 */

const puppeteer = require("puppeteer");
const fs = require("fs");
const path = require("path");

const DEFAULT_THEME = {
  name: "Default",
  canvas: { width: 1080, height: 1080 },
  fonts: {
    heading: { family: "Noto Sans KR", weights: "700;900", css: "'Noto Sans KR', sans-serif" },
    body: { family: "Noto Sans KR", weights: "400;500;700", css: "'Noto Sans KR', sans-serif" },
    mono: { family: "Noto Sans KR", weights: "500;700", css: "'Noto Sans KR', sans-serif" },
  },
  colors: {
    bg: "#EEF1F4",
    bgEdge: "#E2E7EE",
    ink: "#12294C",
    inkDim: "rgba(18,41,76,0.68)",
    inkMute: "rgba(18,41,76,0.40)",
    accent: "#1F5FA8",
    accentBright: "#5AA0E8",
    bgDark: "#12294C",
    bgDarkEdge: "#0B1B33",
    inkOnDark: "#EEF1F4",
    inkOnDarkDim: "rgba(238,241,244,0.70)",
  },
  headline: { case: "none", weight: 700, size: 72, letterSpacing: -1.5, lineHeight: 1.18 },
  radius: 14,
};

function deepMerge(base, over) {
  if (!over) return base;
  const out = Array.isArray(base) ? base.slice() : { ...base };
  for (const k of Object.keys(over)) {
    if (
      over[k] &&
      typeof over[k] === "object" &&
      !Array.isArray(over[k]) &&
      base[k] &&
      typeof base[k] === "object"
    ) {
      out[k] = deepMerge(base[k], over[k]);
    } else {
      out[k] = over[k];
    }
  }
  return out;
}

function loadTheme() {
  const p = path.resolve(process.cwd(), "theme.json");
  if (!fs.existsSync(p)) return DEFAULT_THEME;
  try {
    return deepMerge(DEFAULT_THEME, JSON.parse(fs.readFileSync(p, "utf8")));
  } catch (e) {
    console.warn("theme.json could not be parsed, using default look.", e.message);
    return DEFAULT_THEME;
  }
}

// 저장소에 박제된 폰트 — family → woff2. 외부 CDN 의존 0(지니 09-17: "미리 받아서
// 박제"). Pretendard 는 사이트가 web/public/fonts 에 이미 든 원본을 그대로 쓴다
// (서브셋본은 KS X 1001 2350자라 기사 속 드문 음절이 빠질 수 있다).
// 여기 없는 family 만 Google Fonts 링크로 받고, 로드 여부는 render guard 가 잰다.
const EMBEDDED_FONTS = {
  "SUIT Variable": path.resolve(__dirname, "fonts/SUIT-Variable.woff2"),
  "Wanted Sans Variable": path.resolve(__dirname, "fonts/WantedSansVariable.woff2"),
  "Pretendard Variable": path.resolve(__dirname, "../web/public/fonts/pretendard/v1.3.9/PretendardVariable.woff2"),
};

function fontFaces(theme) {
  const out = [];
  const seen = new Set();
  for (const role of ["heading", "body", "mono"]) {
    const f = theme.fonts[role];
    const file = f && EMBEDDED_FONTS[f.family];
    if (!file || seen.has(f.family) || !fs.existsSync(file)) continue;
    seen.add(f.family);
    const b64 = fs.readFileSync(file).toString("base64");
    out.push(`@font-face { font-family: "${f.family}"; src: url("data:font/woff2;base64,${b64}") format("woff2"); font-style: normal; font-weight: ${f.weights || "400 900"}; }`);
  }
  return out.join("");
}

function fontLinks(theme) {
  const links = [];
  const seen = new Set();
  for (const role of ["heading", "body", "mono"]) {
    const f = theme.fonts[role];
    if (!f || !f.family || EMBEDDED_FONTS[f.family]) continue;
    const key = `${f.family}:${f.weights}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const fam = f.family.replace(/\s+/g, "+");
    links.push(`<link href="https://fonts.googleapis.com/css2?family=${fam}:wght@${f.weights || "400;700"}&display=swap" rel="stylesheet">`);
  }
  return links.join("\n");
}

function esc(value) {
  // 원본은 이름만 esc 이고 String() 변환만 했다. RSS 제목의 &, <, 따옴표가
  // 그대로 주입돼 레이아웃이 깨진다. accentize() 는 esc() 뒤에 [[ ]] 를
  // <span> 으로 바꾸므로 순서는 그대로 둔다.
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function accentize(text, cls) {
  // 8자 이하 강조만 줄바꿈을 막는다. 긴 구절에 nowrap 을 걸면 헤드라인이 캔버스를
  // 넘어가 가로 가드에 걸린다(검토 09-17). 그 이상은 색만 입힌다.
  return esc(text).replace(/\[\[(.+?)\]\]/g, (_, inner) => {
    const keep = [...inner].length <= 8 ? " keep" : "";
    return `<span class="${cls}${keep}">${inner}</span>`;
  });
}

function shell(inner, theme, dark) {
  const c = theme.colors;
  const h = theme.headline;
  const hCase = h.case === "upper" ? "uppercase" : "none";
  const bg = dark
    ? `radial-gradient(130% 100% at 20% 0%, ${c.bgDark} 0%, ${c.bgDarkEdge} 100%)`
    : `radial-gradient(120% 90% at 50% 0%, ${c.bg} 0%, ${c.bgEdge} 100%)`;
  const ink = dark ? c.inkOnDark : c.ink;
  const inkDim = dark ? c.inkOnDarkDim : c.inkDim;
  const inkMute = dark ? "rgba(238,241,244,0.45)" : c.inkMute;
  const accent = dark ? c.accentBright : c.accent;
  return `<!doctype html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
${fontLinks(theme)}
<style>
  ${fontFaces(theme)}
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: ${theme.canvas.width}px; height: ${theme.canvas.height}px; }
  body {
    font-family: ${theme.fonts.body.css};
    background: ${bg};
    color: ${ink};
    position: relative;
    overflow: hidden;
  }
  /* 3행 그리드 — 꼬리를 바닥에 못박고 본문이 남는 높이를 전부 먹는다.
     원본의 .spacer{flex:1} 방식은 내용을 전부 위로 밀어 아래를 비운다. */
  /* 열을 minmax(0,1fr) 로 못박는다 — auto 열은 내용이 넓으면 max-content 로 늘어나
     카드 바깥까지 행을 밀어낸다(09-17 표지 목차 nowrap 에서 1049px 까지 벌어짐). */
  .card { position: absolute; inset: 0; padding: 48px 54px 44px;
    display: grid; grid-template-rows: auto 1fr auto; grid-template-columns: minmax(0, 1fr); }
  .card::after { content: ""; position: absolute; left: 54px; right: 54px; top: 106px;
    height: 1px; background: ${dark ? "rgba(238,241,244,.18)" : "rgba(18,41,76,.14)"}; }
  .hd { display: flex; justify-content: space-between; align-items: center;
    color: ${inkMute}; font-size: 19px; font-weight: 700; letter-spacing: 2px; }
  .hd .brand { color: ${accent}; }
  .classification { margin-top: 32px; display: grid; grid-template-columns: 132px auto;
    width: fit-content; align-items: end; border-bottom: 6px solid ${accent}; padding-bottom: 12px; }
  .classification .class-label { color: ${inkMute}; font-size: 16px; font-weight: 800;
    letter-spacing: 1.5px; padding-bottom: 5px; }
  .classification .class-title { color: ${accent}; font-family: ${theme.fonts.heading.css};
    font-size: 43px; line-height: 1; font-weight: 850; letter-spacing: -1.5px; }
  .section-kicker { margin-top: 20px; color: ${inkMute}; font-size: 20px;
    font-weight: 750; letter-spacing: 3px; }
  /* 카드 한 장에 뱃지·헤드라인·불릿 3개·칩이 들어오면서 본문에 무게가 생겼다.
     가운데 정렬이 맞다 — 위로 붙이면 아래 40%가 다시 빈다(실측). */
  .body { display: flex; flex-direction: column; justify-content: center;
    position: relative; z-index: 1; min-height: 0; }
  .ft { display: flex; justify-content: space-between; align-items: center;
    padding-top: 26px; border-top: 1px solid ${dark ? c.ruleOnDark : c.rule};
    color: ${inkMute}; font-size: 24px; font-weight: 700; }
  /* 매 장 반복되는 주소보다 그 장의 출처가 진해야 한다(검토 09-17). */
  .ft .site { color: ${inkMute}; }
  .ft .src { color: ${inkDim}; font-weight: 800; }

  /* 꼭지 머리 — 악센트로 꽉 찬 번호 뱃지 + 태그. 카드뉴스의 '몇 번째 무슨 얘기'
     신호를 글자 색이 아니라 덩어리로 준다. */
  .idxrow { display: flex; align-items: center; gap: 20px; }
  /* 번호는 표지 목차가 이미 준 정보다 — 뱃지를 잉크색 작은 사각으로 낮추고,
     액센트는 분류 태그와 [[강조]] 두 자리에만 쓴다(디자인 검토 09-17). */
  .badge { width: 64px; height: 64px; border-radius: 0; background: ${ink};
    color: ${dark ? c.bgDark : c.bg}; font-family: ${theme.fonts.heading.css};
    font-weight: 800; font-size: 34px; display: flex; align-items: center;
    justify-content: center; letter-spacing: -1px; }
  .tag { font-size: 46px; font-weight: 800; color: ${accent}; letter-spacing: -0.5px; }

  .headline { margin-top: 26px; font-family: ${theme.fonts.heading.css};
    font-weight: ${h.weight}; font-size: ${h.size}px; line-height: ${h.lineHeight};
    letter-spacing: ${h.letterSpacing}px; text-transform: ${hCase};
    /* 한글은 어절 단위로 끊는다. 없으면 '결/론' 처럼 낱말이 쪼개진다. */
    word-break: keep-all; overflow-wrap: break-word; }
  .em { color: ${accent}; font-weight: 800; }
  /* 짧은 강조만 줄바꿈을 막는다 — 긴 구절에 nowrap 을 걸면 헤드라인이 캔버스를
     넘어간다. 길이 분기는 accentize() 가 한다. */
  .em.keep { white-space: nowrap; }
  .hero-stat { display: grid; grid-template-columns: auto auto 1fr; align-items: end;
    gap: 18px; margin-top: 30px; padding: 22px 0 26px; border-top: 1px solid ${inkMute};
    border-bottom: 1px solid ${inkMute}; color: ${accent}; }
  .hero-stat .value { font-family: ${theme.fonts.heading.css}; font-size: 160px;
    line-height: .76; font-weight: 900; letter-spacing: -10px; }
  .hero-stat .unit { font-family: ${theme.fonts.heading.css}; font-size: 50px;
    line-height: 1; font-weight: 900; padding-bottom: 10px; }
  .hero-stat .caption { margin-left: auto; max-width: 380px; font-size: 27px;
    line-height: 1.35; font-weight: 700; color: ${inkDim}; text-align: right;
    word-break: keep-all; }
  .subline { font-size: 36px; font-weight: 500; line-height: 1.5; color: ${inkDim};
    word-break: keep-all; overflow-wrap: break-word; }

  /* 사실/의미 불릿 — 카드 한 장이 한 가지만 말한다. 한 문장짜리 요약을 패널에
     넣어 여백을 메우던 방식은 버렸다(글자만 빽빽해진다). */
  .points { margin-top: 40px; display: flex; flex-direction: column; gap: 14px; }
  .points li { list-style: none; display: flex; gap: 18px; font-size: 31px;
    font-weight: 500; line-height: 1.36; color: ${ink};
    word-break: keep-all; overflow-wrap: break-word; }
  .points li::before { content: ""; flex: none; width: 12px; height: 12px;
    border-radius: 0; background: ${ink}; margin-top: 14px; }

  .status-list { margin-top: 26px; display: flex; flex-direction: column; }
  .status-row { display: grid; grid-template-columns: 138px 1fr; min-height: 74px;
    align-items: center; border-top: 1px solid ${dark ? "rgba(246,245,240,.20)" : "rgba(18,41,76,.22)"}; }
  .status-row:last-child { border-bottom: 1px solid ${dark ? "rgba(246,245,240,.20)" : "rgba(18,41,76,.22)"}; }
  .status-row .status-label { align-self: stretch; display: flex; align-items: center;
    justify-content: flex-start; font-size: 23px; font-weight: 850; color: ${accent}; }
  .status-row.pending .status-label { color: ${accent}; }
  .status-row.next .status-label { color: ${accent}; }
  .status-row .status-text { padding: 15px 0; font-size: 27px; font-weight: 650;
    line-height: 1.3; color: ${ink}; word-break: keep-all; }

  /* 의미 블록 — 같은 장 안에서 사실과 구분되도록 색을 깐다. 사실 불릿은
     맨몸, 의미는 패널 안. 장을 쪼개지 않고도 두 덩이가 갈린다. */
  /* 의미 패널 — 악센트 단색 위에 밝은 잉크. Codex 원안은 전 카드 다크 전제라
     패널 안 글자를 ink 로 두었는데, 본문을 밝은 판으로 돌리면 라벨이
     악센트-위-악센트로 사라지고 글자는 네이비-위-블루가 된다(실측). */
  /* 진한 파랑 면 위 흰 글자는 "눈에 안 들어온다"(지니 09-17) — 면은 종이와 구분되는
     연한 신호색, 글자는 잉크. 사실과는 크기가 아니라 굵기로 가른다. 위쪽 4px 선은
     꼬리말·헤어라인과 함께 가로선 셋이 경쟁해서 왼쪽 기둥으로 옮겼다(검토 09-17). */
  .why { margin-top: 44px; padding: 22px 26px 24px 26px;
    background: ${dark ? "rgba(255,255,255,.06)" : c.signal};
    border-left: 8px solid ${accent}; color: ${ink}; }
  .why .lbl { font-size: 22px; font-weight: 800; letter-spacing: 1.5px;
    color: ${dark ? accent : c.signalInk}; margin-bottom: 14px; }
  .why .points { margin-top: 0; gap: 10px; }
  .why .points li { font-size: 29px; line-height: 1.34; color: ${ink}; font-weight: 600; }
  .why .points li::before { background: ${dark ? accent : c.signalInk}; }

  .meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 30px; }
  .chip { padding: 9px 16px; border-radius: 0; font-size: 21px;
    font-weight: 700; color: ${inkDim};
    border: 1px solid ${dark ? c.ruleOnDark : c.rule}; }

  /* 커버 — 날짜·라벨을 한 줄 제호로 묶고 그 아래 목차표. 세 덩어리가 따로 놀던
     space-between 을 버렸다(검토 09-17). */
  .cover .body { justify-content: center; padding: 0; }
  .cover .headline { font-size: ${Math.round(h.size * 1.16)}px; margin-top: 0; }
  .datehead { display: flex; align-items: baseline; gap: 20px;
    border-bottom: 2px solid ${accent}; padding-bottom: 20px; }
  .cover .today { font-family: ${theme.fonts.heading.css}; font-weight: 900;
    font-size: 96px; line-height: 0.92; letter-spacing: -4px; color: ${accent}; }
  .cover .label { margin-top: 0; font-size: 26px; font-weight: 700;
    letter-spacing: 5px; color: ${inkMute}; }
  /* 커버 목차 — 커버 가운데가 통째로 비는 걸 오늘 다룰 꼭지 목록으로 메운다.
     장식이 아니라 '이 앨범에 뭐가 들었나'다. */
  /* 표지 목차가 주인공 — 한 줄 판단 헤드라인은 뺐다(목차와 중복, 지니 09-17). 두 줄 허용. */
  .toc { display: flex; flex-direction: column; margin-top: 52px; gap: 0; }
  .toc .row { display: flex; gap: 24px; align-items: baseline; padding: 30px 0;
    border-bottom: 1px solid ${dark ? c.ruleOnDark : c.rule}; }
  .toc .row:last-child { border-bottom: 0; }
  .toc .n { font-family: ${theme.fonts.heading.css}; font-weight: 900; width: 46px;
    font-size: 30px; color: ${accent}; letter-spacing: 1px; flex: none; }
  /* flex:1 + min-width:0 이라야 줄이 칸을 넘지 않는다 — 이게 없으면 nowrap 이
     플렉스 아이템을 캔버스 밖까지 늘리고, 줄이기 루프의 scrollWidth>clientWidth 도
     영원히 거짓이다(실측 09-17: 표지 세 줄이 전부 오른쪽으로 잘림). */
  .toc .t { font-size: 52px; font-weight: 650; line-height: 1.28; color: ${ink};
    flex: 1; min-width: 0; overflow: hidden; word-break: keep-all; }
  .cover .subline { margin-top: 40px; font-size: 30px; }

  /* 마지막장 — 가운데에 큰 글자만 있고 면적 63%가 비어 있었다. 왼쪽 정렬로
     세우고 오늘 3건을 다시 세운다(검토 09-17). */
  .end .body { align-items: stretch; justify-content: center; text-align: left; }
  .end .headline { font-size: 56px; margin-top: 0; margin-bottom: 34px; }
  .end .subline { margin-top: 30px; }
  .end .toc { margin-top: 0; }
  .end .toc .row { padding: 16px 0; }
  .end .toc .t { font-size: 34px; font-weight: 600; color: ${inkDim}; }
  .pill { margin-top: 48px; align-self: flex-start; padding: 20px 44px;
    border-radius: 0; border: 2px solid ${accent}; color: ${accent};
    font-family: ${theme.fonts.heading.css}; font-weight: 700; font-size: 34px;
    letter-spacing: 1px; }
</style></head><body>${inner}</body></html>`;
}

function renderSlide(s, theme) {
  const type = s.type || "step";
  const site = esc(s.handle || "");
  const num = esc(s.slideNum || "");

  if (type === "hook") {
    return shell(
      `<div class="card cover">
        <div class="hd"><span class="brand">${esc(s.stepLabel || "NUCLENS")}</span><span>${num}</span></div>
        <div class="body">
          <div class="datehead">
            <div class="today">${esc(s.date || "")}</div>
            <div class="label">${esc(s.label || "원자력 정책 브리핑")}</div>
          </div>
          ${s.headline ? `<h1 class="headline">${accentize(s.headline, "em")}</h1>` : ""}
          ${
            Array.isArray(s.toc) && s.toc.length
              ? `<div class="toc">${s.toc
                  .map((t, n) => `<div class="row"><span class="n">${String(n + 1).padStart(2, "0")}</span><span class="t">${esc(t)}</span></div>`)
                  .join("")}</div>`
              : ""
          }
          <p class="subline">${esc(s.subline || "")}</p>
        </div>
        <div class="ft"><span class="site">${site}</span><span>SWIPE →</span></div>
      </div>`,
      theme,
      true
    );
  }

  if (type === "cta") {
    return shell(
      `<div class="card end">
        <div class="hd"><span class="brand">${esc(s.stepLabel || "NUCLENS")}</span><span>${num}</span></div>
        <div class="body">
          <h1 class="headline">${accentize(s.headline, "em")}</h1>
          ${Array.isArray(s.toc) && s.toc.length ? `<div class="toc">${s.toc
                  .map((t, n) => `<div class="row"><span class="n">${String(n + 1).padStart(2, "0")}</span><span class="t">${esc(t)}</span></div>`)
                  .join("")}</div>` : `<p class="subline">${esc(s.subline || "")}</p>`}
          ${s.keyword ? `<div class="pill">${esc(s.keyword)}</div>` : ""}
        </div>
        <div class="ft"><span class="site">${site}</span><span>${esc(s.footer || "")}</span></div>
      </div>`,
      theme,
      true
    );
  }

  const bullets = (list, cls) =>
    Array.isArray(list) && list.length
      ? `<ul class="points${cls || ""}">${list.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`
      : "";
  const why = Array.isArray(s.why) && s.why.length
    ? `<div class="why"><div class="lbl">${esc(s.whyLabel || "왜 중요한가")}</div>${bullets(s.why)}</div>`
    : "";
  const chips = Array.isArray(s.meta) && s.meta.length
    ? `<div class="meta">${s.meta.map((m) => `<span class="chip">${esc(m)}</span>`).join("")}</div>`
    : "";
  const heroStat = s.heroStat
    ? `<div class="hero-stat"><span class="value">${esc(s.heroStat)}</span><span class="unit">${esc(s.heroUnit || "")}</span><span class="caption">${esc(s.heroCaption || "")}</span></div>`
    : "";
  const statusRows = Array.isArray(s.statusRows) && s.statusRows.length
    ? `<div class="status-list">${s.statusRows.map((row) =>
        `<div class="status-row ${esc(row.tone || "")}"><div class="status-label">${esc(row.label)}</div><div class="status-text">${esc(row.text)}</div></div>`
      ).join("")}</div>`
    : "";
  return shell(
    `<div class="card">
      <div class="hd"><span class="brand">NUCLENS</span><span>${num}</span></div>
      <div class="body">
        ${s.mainTitle ? `<div class="classification"><span class="class-label">MAIN TITLE</span><span class="class-title">${esc(s.mainTitle)}</span></div>` : ""}
        ${s.sectionLabel ? `<div class="section-kicker">${esc(s.sectionLabel)}</div>` : ""}
        <div class="idxrow">
          ${s.idx && !s.mainTitle ? `<div class="badge">${esc(s.idx)}</div>` : ""}
          ${s.stepLabel ? `<div class="tag">${esc(s.stepLabel)}</div>` : ""}
        </div>
        <h1 class="headline">${accentize(s.headline, "em")}</h1>
        ${heroStat}
        ${statusRows}
        ${bullets(s.points)}
        ${why}
        ${chips}
      </div>
      <div class="ft"><span class="site">${site}</span><span class="src">${esc(s.footer || "")}</span></div>
    </div>`,
    theme,
    false
  );
}

const SAMPLE_SLIDES = [
  {
    type: "hook",
    slideNum: "01 / 03",
    stepLabel: "NUCLENS 브리핑",
    date: "2026.09.17",
    toc: ["원안위, 고리 3호기 운영변경허가 심의"],
    headline: "고리 3호기 [[계속운전]] 심의 연내 결론",
    subline: "오늘 수집 128건 중 1건",
    handle: "nuclens.pages.dev",
  },
  {
    type: "step",
    slideNum: "02 / 03",
    idx: "01",
    stepLabel: "계속운전",
    headline: "원안위, 고리 3호기 [[운영변경허가]] 심의",
    points: ["9월 16일 제2026-15회 회의", "설계수명 만료 4기 대상", "1건 재상정 결정"],
    whyLabel: "왜 중요한가",
    why: ["설계수명 만료 원전 4기 일정에 직결", "재상정 안건 결과는 아직 미확정"],
    meta: ["2026.09.16", "#계속운전"],
    handle: "nuclens.pages.dev",
    footer: "원자력안전위원회",
  },
  {
    type: "cta",
    slideNum: "03 / 03",
    stepLabel: "NUCLENS",
    headline: "전체 보기",
    subline: "오늘 브리핑 전문과 지난 이슈 흐름",
    keyword: "nuclens.pages.dev",
    handle: "크롤 완료 직후 발송",
    footer: "2026.09.17",
  },
];

// runnable check — `node build.js --check`. 브라우저 없이 되는 분기만 본다.
function selfCheck() {
  const assert = require("assert");
  assert.strictEqual(accentize("[[원안위]] 심의", "em"), '<span class="em keep">원안위</span> 심의');
  assert.strictEqual(accentize("[[아홉자가넘는강조구절]] 확인", "em"),
    '<span class="em">아홉자가넘는강조구절</span> 확인');   // 9자 이상은 nowrap 안 건다
  assert.strictEqual(accentize("강조 없음", "em"), "강조 없음");
  assert.ok(accentize("<b>&", "em").includes("&lt;b&gt;&amp;"), "이스케이프 유지");
  const t = loadTheme();
  assert.ok(EMBEDDED_FONTS[t.fonts.heading.family], "제목 서체가 박제 목록에 없다");
  assert.ok(fs.existsSync(EMBEDDED_FONTS[t.fonts.heading.family]), "제목 서체 파일이 없다");
  assert.ok(t.fonts.heading.css.includes("Pretendard"),
    "SUIT 는 ㎾·㎿·㎸ 가 없다 — Pretendard 를 폴백으로 세워야 한다");
  assert.strictEqual(fontLinks(t), "", "박제 서체만 쓸 때 외부 링크가 없어야 한다");
  console.log("build.js self-check OK");
}

(async () => {
  const theme = loadTheme();
  const arg = process.argv[2];
  if (arg === "--check") return selfCheck();

  let slides;
  if (arg === "--sample") {
    slides = SAMPLE_SLIDES;
  } else {
    const input = arg || "slides.json";
    const inputPath = path.resolve(process.cwd(), input);
    if (!fs.existsSync(inputPath)) {
      console.error(`No input file at ${inputPath}. Create slides.json first, or run: node build.js --sample`);
      process.exit(1);
    }
    slides = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  }

  if (!Array.isArray(slides) || !slides.length) {
    console.error("slides must be a non-empty array.");
    process.exit(1);
  }

  console.log(`Theme: ${theme.name} | ${theme.fonts.heading.family}`);

  const outDir = path.resolve(process.cwd(), "out");
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  await page.setViewport({ width: theme.canvas.width, height: theme.canvas.height, deviceScaleFactor: 1 });

  for (let i = 0; i < slides.length; i++) {
    await page.setContent(renderSlide(slides[i], theme), { waitUntil: "load", timeout: 60000 });
    try {
      await page.evaluate(() => document.fonts.ready);
    } catch (e) {}
    // 표지 목차는 한 줄에 한 꼭지(지니 09-17). 넘치는 줄만 줄인 뒤, 세 줄을 그중
    // 최솟값으로 맞춘다 — 행마다 크기가 다르면 줄 끝이 너덜거린다(검토 09-17).
    await page.evaluate(() => {
      const rows = [...document.querySelectorAll(".toc .t")];
      if (!rows.length) return;
      let smallest = Infinity;
      for (const el of rows) {
        el.style.whiteSpace = "nowrap";
        let size = parseFloat(getComputedStyle(el).fontSize);
        while (el.scrollWidth > el.clientWidth && size > 28) {
          size -= 1;
          el.style.fontSize = size + "px";
        }
        smallest = Math.min(smallest, size);
      }
      for (const el of rows) el.style.fontSize = smallest + "px";
    });

    // 본문이 칸을 넘으면 불릿 글자를 함께 줄인다. 사실 3 + 의미 2 가 각각 40자로
    // 꽉 차면 993px 가 필요한데 가용 높이는 903px 다(검토 09-17 계산) — 가드가
    // throw 하기 전에 여기서 맞춘다. scrollHeight 로 재면 한글 잉크박스 때문에
    // 멀쩡한 카드도 걸리므로 자식 rect 의 최하단으로 잰다.
    await page.evaluate(() => {
      const body = document.querySelector(".card > .body");
      if (!body) return;
      const facts = [...body.querySelectorAll(".points > li:not(.why .points > li)")];
      const over = () => {
        const br = body.getBoundingClientRect();
        return [...body.children].some((el) => el.getBoundingClientRect().bottom > br.bottom + 1);
      };
      const shrink = (nodes, floor) => {
        for (let guard = 0; guard < 40 && over(); guard++) {
          let moved = false;
          for (const el of nodes) {
            const size = parseFloat(getComputedStyle(el).fontSize);
            if (size > floor) { el.style.fontSize = size - 1 + "px"; moved = true; }
          }
          if (!moved) break;
        }
      };
      shrink(body.querySelectorAll(".why .points > li"), 25);
      shrink(facts.filter((el) => !el.closest(".why")), 27);
    });
    await new Promise((r) => setTimeout(r, 400));

    // 조용한 실패 두 가지를 여기서 잡는다. CDN 이 막히면 폰트 없이 "성공" 하고,
    // 한글이 넘치면 잘린 채 "성공" 한다. 둘 다 PNG 는 멀쩡해 보인다.
    const headFamily = theme.fonts.heading.family;
    const fontOk = await page.evaluate((fam) => {
      // check() 단독은 가드가 못 된다 — 선언된 @font-face 가 하나도 없는
      // family 는 시스템 폰트로 폴백하며 true 를 준다(실측: 존재하지 않는
      // family 로도 통과). CDN 이 막히면 정확히 이 상태다. 스타일시트가
      // 실제로 왔는지를 먼저 묻고, 한글 텍스트로 subset 까지 확인한다.
      const faces = [...document.fonts].filter(
        (f) => f.family.replace(/['"]/g, "") === fam
      );
      // 선언만으로는 모자란다 — local() 소스가 실패한 face 도 "선언됨"이고 Chrome 은
      // 그걸 check() 실패로 안 친다(실측 2026-09-15: 로컬 폰트 없음+CDN 차단에서
      // 통과). 실제로 로드된 face 가 하나라도 있어야 한다.
      if (!faces.some((f) => f.status === "loaded")) return false;
      return document.fonts.check(`700 68px '${fam}'`, "계속운전 원자력");
    }, headFamily);

    const overflow = await page.evaluate(() => {
      const card = document.querySelector(".card");
      if (!card) return "no-card";
      // scrollHeight 로 재면 안 된다 — 한글 폰트는 글자 잉크박스가 line-height
      // 보다 커서(실측: headline 155 < 166) 멀쩡한 카드도 매번 걸린다.
      // 각 칸의 실제 사각형이 카드 안쪽 여백을 벗어났는지만 본다.
      const cr = card.getBoundingClientRect();
      const cs = getComputedStyle(card);
      const top = cr.top + parseFloat(cs.paddingTop) - 2;
      const bottom = cr.bottom - parseFloat(cs.paddingBottom) + 2;
      // 가로도 잰다 — 세로만 보던 탓에 표지 목차가 캔버스 밖으로 잘려 나갔는데도
      // 통과했다(09-17). 글자는 안쪽 여백 안에 있어야 한다.
      const left = cr.left + parseFloat(cs.paddingLeft) - 2;
      const right = cr.right - parseFloat(cs.paddingRight) + 2;
      const rows = [...card.children].filter((el) => !el.classList.contains("ghost"));
      const bad = [];
      for (const row of rows) {
        const r = row.getBoundingClientRect();
        if (r.height > 0 && (r.bottom > bottom || r.top < top)) bad.push(row.className);
        if (r.width > 0 && (r.right > right || r.left < left)) bad.push(row.className + ":가로");
        // 본문 칸은 1fr 이라 칸 자체는 안 넘치고 안쪽 글자만 넘친다.
        for (const el of row.children) {
          const er = el.getBoundingClientRect();
          if (er.height > 0 && (er.bottom > r.bottom + 2 || er.top < r.top - 2)) {
            bad.push(el.className || el.tagName);
          }
          if (er.width > 0 && (er.right > right || er.left < left)) {
            bad.push((el.className || el.tagName) + ":가로");
          }
        }
      }
      return [...new Set(bad)].join(",");
    });

    if (!fontOk || overflow) {
      await browser.close();
      throw new Error(
        `render guard failed (slide ${i + 1}): font=${fontOk} overflow=${overflow}`
      );
    }

    const n = String(i + 1).padStart(2, "0");
    const out = path.join(outDir, `slide-${n}.png`);
    await page.screenshot({ path: out, type: "png" });
    console.log("rendered", out);
  }

  await browser.close();
  console.log(`\nDone. ${slides.length} slides in ./out`);
})();
