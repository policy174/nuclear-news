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

function fontLinks(theme) {
  const seen = new Set();
  const links = [];
  for (const role of ["heading", "body", "mono"]) {
    const f = theme.fonts[role];
    if (!f || !f.family) continue;
    const key = `${f.family}:${f.weights}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const fam = f.family.replace(/\s+/g, "+");
    links.push(
      `<link href="https://fonts.googleapis.com/css2?family=${fam}:wght@${
        f.weights || "400;700"
      }&display=swap" rel="stylesheet">`
    );
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
  return esc(text).replace(/\[\[(.+?)\]\]/g, `<span class="${cls}">$1</span>`);
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
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 1080px; height: 1440px; }
  body {
    font-family: ${theme.fonts.body.css};
    background: ${bg};
    color: ${ink};
    position: relative;
    overflow: hidden;
  }
  /* 3행 그리드 — 꼬리를 바닥에 못박고 본문이 남는 높이를 전부 먹는다.
     원본의 .spacer{flex:1} 방식은 내용을 전부 위로 밀어 아래를 비운다. */
  .card { position: absolute; inset: 0; padding: 72px 64px 64px;
    display: grid; grid-template-rows: auto 1fr auto; }
  .hd { display: flex; justify-content: space-between; align-items: center;
    color: ${inkMute}; font-size: 24px; font-weight: 700; letter-spacing: 3px; }
  .hd .brand { color: ${accent}; }
  /* 카드 한 장에 뱃지·헤드라인·불릿 3개·칩이 들어오면서 본문에 무게가 생겼다.
     가운데 정렬이 맞다 — 위로 붙이면 아래 40%가 다시 빈다(실측). */
  .body { display: flex; flex-direction: column; justify-content: center;
    position: relative; z-index: 1; min-height: 0; }
  .ft { display: flex; justify-content: space-between; align-items: center;
    padding-top: 26px; border-top: 2px solid ${dark ? "rgba(238,241,244,0.18)" : "rgba(18,41,76,0.14)"};
    color: ${inkMute}; font-size: 24px; font-weight: 700; }
  .ft .site { color: ${accent}; }

  /* 꼭지 머리 — 악센트로 꽉 찬 번호 뱃지 + 태그. 카드뉴스의 '몇 번째 무슨 얘기'
     신호를 글자 색이 아니라 덩어리로 준다. */
  .idxrow { display: flex; align-items: center; gap: 28px; }
  .badge { width: 96px; height: 96px; border-radius: 20px; background: ${accent};
    color: #fff; font-family: ${theme.fonts.heading.css}; font-weight: 900;
    font-size: 46px; display: flex; align-items: center; justify-content: center;
    letter-spacing: -1px; }
  .tag { font-size: 32px; font-weight: 700; color: ${accent}; letter-spacing: 1px; }

  .headline { margin-top: 30px; font-family: ${theme.fonts.heading.css};
    font-weight: ${h.weight}; font-size: ${h.size}px; line-height: ${h.lineHeight};
    letter-spacing: ${h.letterSpacing}px; text-transform: ${hCase};
    /* 한글은 어절 단위로 끊는다. 없으면 '결/론' 처럼 낱말이 쪼개진다. */
    word-break: keep-all; overflow-wrap: break-word; }
  .em { color: ${accent}; }
  .subline { font-size: 36px; font-weight: 500; line-height: 1.5; color: ${inkDim};
    word-break: keep-all; overflow-wrap: break-word; }

  /* 사실/의미 불릿 — 카드 한 장이 한 가지만 말한다. 한 문장짜리 요약을 패널에
     넣어 여백을 메우던 방식은 버렸다(글자만 빽빽해진다). */
  .points { margin-top: 48px; display: flex; flex-direction: column; gap: 28px; }
  .points li { list-style: none; display: flex; gap: 24px; font-size: 40px;
    font-weight: 500; line-height: 1.36; color: ${ink};
    word-break: keep-all; overflow-wrap: break-word; }
  .points li::before { content: ""; flex: none; width: 14px; height: 14px;
    border-radius: 4px; background: ${accent}; margin-top: 19px; }

  /* 메타 칩 — 날짜·태그. 출처는 꼬리말이 이미 들고 있다. */
  .meta { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 46px; }
  .chip { padding: 14px 28px; border-radius: 999px; font-size: 28px;
    font-weight: 700; color: ${inkDim};
    border: 2px solid ${dark ? "rgba(238,241,244,0.22)" : "rgba(18,41,76,0.16)"}; }

  /* 커버 — 날짜를 위에, 판단을 아래에. 양 끝을 잡아 가운데가 비어도 구도가 선다. */
  .cover .body { justify-content: space-between; padding: 40px 0 30px; }
  .cover .headline { font-size: ${Math.round(h.size * 1.16)}px; margin-top: 0; }
  .cover .today { font-family: ${theme.fonts.heading.css}; font-weight: 900;
    font-size: 132px; line-height: 0.92; letter-spacing: -5px; color: ${accent}; }
  .cover .label { margin-top: 18px; font-size: 32px; font-weight: 700;
    letter-spacing: 7px; color: ${inkMute}; }
  /* 커버 목차 — 커버 가운데가 통째로 비는 걸 오늘 다룰 꼭지 목록으로 메운다.
     장식이 아니라 '이 앨범에 뭐가 들었나'다. */
  .toc { display: flex; flex-direction: column; gap: 24px; }
  .toc .row { display: flex; gap: 24px; align-items: baseline; }
  .toc .n { font-family: ${theme.fonts.heading.css}; font-weight: 900;
    font-size: 30px; color: ${accent}; letter-spacing: 1px; flex: none; }
  .toc .t { font-size: 36px; font-weight: 500; color: ${inkDim};
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cover .bar { width: 190px; height: 14px; background: ${accent};
    border-radius: 7px; margin-bottom: 44px; }
  .cover .subline { margin-top: 34px; font-size: 34px; }

  /* 마지막장 — 가운데 정렬 + 알약 */
  .end .body { align-items: center; justify-content: center; text-align: center; }
  .end .headline { font-size: ${Math.round(h.size * 1.3)}px; margin-top: 0; }
  .end .kicker { font-size: 28px; font-weight: 700; color: ${accent};
    letter-spacing: 8px; margin-bottom: 30px; }
  .end .subline { margin-top: 30px; }
  .pill { margin-top: 52px; display: inline-block; padding: 24px 62px;
    border-radius: 999px; border: 3px solid ${accent}; color: ${accent};
    font-family: ${theme.fonts.heading.css}; font-weight: 700; font-size: 40px;
    letter-spacing: 4px; }
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
          <div>
            <div class="today">${esc(s.date || "")}</div>
            <div class="label">${esc(s.label || "원자력 정책 브리핑")}</div>
          </div>
          ${
            Array.isArray(s.toc) && s.toc.length
              ? `<div class="toc">${s.toc
                  .map((t, n) => `<div class="row"><span class="n">${String(n + 1).padStart(2, "0")}</span><span class="t">${esc(t)}</span></div>`)
                  .join("")}</div>`
              : ""
          }
          <div>
            <div class="bar"></div>
            <h1 class="headline">${accentize(s.headline, "em")}</h1>
            <p class="subline">${esc(s.subline || "")}</p>
          </div>
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
          <p class="subline">${esc(s.subline || "")}</p>
          ${s.keyword ? `<div class="pill">${esc(s.keyword)}</div>` : ""}
        </div>
        <div class="ft"><span class="site">${site}</span><span>${esc(s.footer || "")}</span></div>
      </div>`,
      theme,
      true
    );
  }

  // 의미 카드는 짙은 판으로 뒤집는다 — 앨범을 넘기면 사실(밝음)/의미(어두움)가
  // 번갈아 와서, 지금 보는 장이 어느 쪽인지 글자를 안 읽어도 안다.
  const dark = s.variant === "why";
  const bullets = Array.isArray(s.points) && s.points.length
    ? `<ul class="points">${s.points.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`
    : "";
  const chips = Array.isArray(s.meta) && s.meta.length
    ? `<div class="meta">${s.meta.map((m) => `<span class="chip">${esc(m)}</span>`).join("")}</div>`
    : "";
  return shell(
    `<div class="card">
      <div class="hd"><span class="brand">NUCLENS</span><span>${num}</span></div>
      <div class="body">
        <div class="idxrow">
          ${s.idx ? `<div class="badge">${esc(s.idx)}</div>` : ""}
          ${s.stepLabel ? `<div class="tag">${esc(s.stepLabel)}</div>` : ""}
        </div>
        <h1 class="headline">${accentize(s.headline, "em")}</h1>
        ${bullets}
        ${chips}
      </div>
      <div class="ft"><span class="site">${site}</span><span>${esc(s.footer || "")}</span></div>
    </div>`,
    theme,
    dark
  );
}

const SAMPLE_SLIDES = [
  {
    type: "hook",
    slideNum: "01 / 04",
    stepLabel: "NUCLENS 브리핑",
    date: "2026.09.17",
    toc: ["원안위, 고리 3호기 운영변경허가 심의"],
    headline: "고리 3호기 [[계속운전]] 심의 연내 결론",
    subline: "오늘 수집 128건 중 1건 추립니다.",
    handle: "nuclens.pages.dev",
  },
  {
    type: "step",
    slideNum: "02 / 04",
    idx: "01",
    stepLabel: "계속운전",
    headline: "원안위, 고리 3호기 [[운영변경허가]] 심의",
    points: ["9월 16일 제2026-15회 회의", "설계수명 만료 4기 대상", "1건 재상정 결정"],
    meta: ["2026.09.16", "#계속운전"],
    handle: "nuclens.pages.dev",
    footer: "원자력안전위원회",
  },
  {
    type: "step",
    variant: "why",
    slideNum: "03 / 04",
    idx: "01",
    stepLabel: "왜 중요한가",
    headline: "재가동 일정의 [[분기점]]",
    points: ["설계수명 만료 원전 4기 일정에 직결",
             "한수원 계속운전 이행 과제와 연동",
             "재상정 안건 결과는 아직 미확정"],
    handle: "nuclens.pages.dev",
    footer: "원자력안전위원회",
  },
  {
    type: "cta",
    slideNum: "04 / 04",
    stepLabel: "NUCLENS",
    headline: "전체 보기",
    subline: "오늘 브리핑 전문과 지난 이슈 흐름",
    keyword: "nuclens.pages.dev",
    handle: "크롤 완료 직후 발송",
    footer: "2026.09.17",
  },
];

(async () => {
  const theme = loadTheme();
  const arg = process.argv[2];

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
  await page.setViewport({ width: 1080, height: 1440, deviceScaleFactor: 1 });

  for (let i = 0; i < slides.length; i++) {
    await page.setContent(renderSlide(slides[i], theme), { waitUntil: "load", timeout: 60000 });
    try {
      await page.evaluate(() => document.fonts.ready);
    } catch (e) {}
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
      if (!faces.length) return false;
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
      const rows = [...card.children].filter((el) => !el.classList.contains("ghost"));
      const bad = [];
      for (const row of rows) {
        const r = row.getBoundingClientRect();
        if (r.height > 0 && (r.bottom > bottom || r.top < top)) bad.push(row.className);
        // 본문 칸은 1fr 이라 칸 자체는 안 넘치고 안쪽 글자만 넘친다.
        for (const el of row.children) {
          const er = el.getBoundingClientRect();
          if (er.height > 0 && (er.bottom > r.bottom + 2 || er.top < r.top - 2)) {
            bad.push(el.className || el.tagName);
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
