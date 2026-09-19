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

// 히어로 그래픽 — **사진은 쓰지 않는다.** 뉴스 카드 상단에 실사처럼 보이는 합성
// 이미지를 올리면 없는 장면을 사실처럼 보이게 한다(v2 시안 판정 09-18). 대신
// 분류에서 결정되는 기하 도형을 브랜드 색으로 얇게 깐다. 판단 근거는 파이프라인이
// 이미 정한 분류 라벨 하나뿐이고, 본문 문구를 훑지 않는다 — 훑으면 오늘 기사에만
// 맞는 규칙이 된다.
// 히어로 일러스트 — **사진은 쓰지 않는다.** 실사처럼 보이는 합성 이미지를 뉴스
// 카드 맨 위에 깔면 "이 사건의 사진"으로 읽힌다(v2 시안 판정 09-18). 대신 그림인
// 게 한눈에 보이는 평면 실루엣을 브랜드 색으로 그린다. 고르는 근거는 파이프라인이
// 정한 분류 하나뿐 — 본문 문구를 훑으면 오늘 기사에만 맞는 규칙이 된다.
//
// 좌표계는 720×520. 왼쪽 45%는 제목이 앉으므로 형태를 오른쪽에 모은다.
const SKY = `<circle cx="470" cy="140" r="122" fill="currentColor" opacity=".20"/>`;
const GROUND = `<rect x="0" y="398" width="720" height="330" fill="currentColor" opacity=".32"/>`;

function dome(x, y, w, h) {
  const r = w / 2;
  return `<path d="M${x} ${y} v-${h} a${r} ${r} 0 0 1 ${w} 0 v${h} z" fill="currentColor" opacity=".58"/>`;
}
function pylon(x, y, h) {
  const w = h * 0.42;
  return `<g stroke="currentColor" stroke-width="3.5" fill="none" opacity=".72">
    <path d="M${x - w / 2} ${y} L${x} ${y - h} L${x + w / 2} ${y}"/>
    <path d="M${x - w * 0.36} ${y - h * 0.3}h${w * 0.72}M${x - w * 0.22} ${y - h * 0.58}h${w * 0.44}"/>
    <path d="M${x - w * 0.6} ${y - h * 0.82}h${w * 1.2}M${x - w * 0.46} ${y - h * 0.95}h${w * 0.92}"/>
  </g>`;
}
function tower(x, y, h, w) {
  return `<path d="M${x} ${y} q${w * 0.18} -${h * 0.62} ${w * 0.06} -${h}
    h${w * 0.88} q-${w * 0.12} ${h * 0.38} ${w * 0.06} ${h} z"
    fill="currentColor" opacity=".46"/>`;
}
function blocks(x, y, spec) {
  return spec.map(([w, h], i) =>
    `<rect x="${x + i * (w + 12)}" y="${y - h}" width="${w}" height="${h}" fill="currentColor" opacity=".${38 + i * 6}"/>`
  ).join("");
}

const HERO_ART = {
  // SMR·신규 건설 — 냉각탑과 격납건물이 줄지어 선 부지.
  atom: `${SKY}
    ${tower(80, 398, 190, 108)}${dome(250, 398, 132, 62)}${tower(400, 398, 216, 120)}
    ${dome(560, 398, 150, 70)}${dome(660, 398, 96, 46)}
    <circle cx="596" cy="250" r="30" fill="none" stroke="currentColor" stroke-width="3" opacity=".55"/>
    <ellipse cx="596" cy="250" rx="72" ry="28" fill="none" stroke="currentColor" stroke-width="2.5" opacity=".45"/>
    <ellipse cx="596" cy="250" rx="72" ry="28" fill="none" stroke="currentColor" stroke-width="2.5" opacity=".45" transform="rotate(62 596 250)"/>
    ${GROUND}`,
  // 전력망·수급 — 도시에서 시작해 화면을 가로지르는 송전 계통.
  grid: `${SKY}${blocks(40, 398, [[58, 132], [46, 196], [64, 108], [42, 164]])}
    ${pylon(360, 398, 244)}${pylon(530, 398, 208)}${pylon(672, 398, 168)}
    <path d="M300 258q85 40 170 0M470 288q85 34 170 0" fill="none" stroke="currentColor"
      stroke-width="2.5" opacity=".5"/>
    <path d="M300 288q85 40 170 0M470 314q85 34 170 0" fill="none" stroke="currentColor"
      stroke-width="2.5" opacity=".38"/>
    ${GROUND}`,
  // 해외사업·수출·협력 — 양쪽 부지를 잇는 항로.
  link: `${SKY}${dome(70, 398, 140, 66)}${tower(230, 398, 170, 96)}
    ${dome(520, 398, 140, 66)}${dome(640, 398, 104, 50)}
    <path d="M140 316q230 -150 450 0" fill="none" stroke="currentColor" stroke-width="3.5"
      stroke-dasharray="17 14" opacity=".85"/>
    <circle cx="140" cy="316" r="12" fill="currentColor" opacity=".95"/>
    <circle cx="590" cy="316" r="12" fill="currentColor" opacity=".95"/>
    ${GROUND}`,
  // 규제·정책·법 — 문서철에서 의사당으로.
  doc: `${SKY}
    <rect x="60" y="268" width="128" height="130" fill="currentColor" opacity=".30"/>
    <g stroke="currentColor" stroke-width="3" opacity=".5" fill="none">
      <path d="M82 300h84M82 328h84M82 356h54"/>
    </g>
    <path d="M430 398v-210h190v210z" fill="currentColor" opacity=".34"/>
    <path d="M525 176 L396 248h258z" fill="currentColor" opacity=".46"/>
    <g stroke="currentColor" stroke-width="3.5" opacity=".6" fill="none">
      <path d="M456 270v112M494 270v112M556 270v112M594 270v112"/>
    </g>
    <path d="M250 398v-96h96v96z" fill="currentColor" opacity=".24"/>
    ${GROUND}`,
  // 그 밖 — 지평선과 계측 파형.
  wave: `${SKY}${blocks(60, 398, [[62, 128], [50, 176], [58, 104], [44, 148]])}
    ${dome(520, 398, 128, 60)}${dome(640, 398, 92, 44)}
    <path d="M40 300q70 -76 140 0t140 0t140 0t140 0" fill="none" stroke="currentColor"
      stroke-width="3" opacity=".5"/>
    <path d="M40 342q70 -76 140 0t140 0t140 0t140 0" fill="none" stroke="currentColor"
      stroke-width="3" opacity=".32"/>
    ${GROUND}`,
};

function heroArt(label) {
  const t = String(label || "");
  const key = /SMR|원자로|신규|건설/.test(t) ? "atom"
    : /전력|계통|수급|에너지/.test(t) ? "grid"
    : /해외|수출|협력|통상|외교/.test(t) ? "link"
    : /규제|인허가|안전|정책|법/.test(t) ? "doc"
    : "wave";
  // 카드가 정사각(1080×1080)이므로 뷰박스도 정사각으로 잘라낸다 — 비율이 어긋나면
  // slice 가 형태를 확대해 덩어리로 만든다(실측 09-18).
  return `<svg viewBox="0 -60 720 720" preserveAspectRatio="xMidYMid slice">${HERO_ART[key]}</svg>`;
}

// 사실 카드 두 장. 카피가 signals(라벨·값·상태)를 주면 그걸 쓰고, 없으면(폴백 카피)
// 사실 불릿을 그대로 세운다. 상태 알약은 **카피가 준 값일 때만** 붙는다 — 본문에서
// 유추해 붙이면 원문에 없는 판정을 카드가 만들어내는 꼴이다.
const MUTED_STATUS = ["연기", "보류", "부결", "반려", "중단", "미정", "무산"];

function editorialRows(s) {
  const signals = (Array.isArray(s.signals) ? s.signals : []).filter((x) => x && x.value);
  if (signals.length) {
    return signals.slice(0, 2).map((x) => ({
      label: x.label || "확인된 사실", text: x.value, state: x.status || "",
      muted: MUTED_STATUS.includes(String(x.status || "")),
    }));
  }
  return (Array.isArray(s.points) ? s.points : []).slice(0, 2)
    .map((t) => ({ label: "", text: t, state: "" }));
}

// 스토리 카드뉴스용 사진. 저작권이 확인된 것만 저장소에 박제한다(cards/photos/photos.json
// 에 출처·라이선스·작가). 생성 이미지는 쓰지 않는다 — 없는 장면을 사실처럼 보이게 한다.
const PHOTO_DIR = path.resolve(__dirname, "photos");
let PHOTO_META = {};
try {
  PHOTO_META = JSON.parse(fs.readFileSync(path.join(PHOTO_DIR, "photos.json"), "utf8"));
} catch (e) {}

function photoKey(label) {
  const t = String(label || "");
  return /해외|수출|협력|통상|외교/.test(t) ? "link"
    : /전력|계통|수급|에너지/.test(t) ? "grid"
    : /규제|인허가|정책|법|국회/.test(t) ? "doc"
    : /SMR|원자로|신규|건설/.test(t) ? "atom"
    : "wave";
}

function photoData(key) {
  const file = path.join(PHOTO_DIR, `${key}.jpg`);
  if (!fs.existsSync(file)) return "";
  return `data:image/jpeg;base64,${fs.readFileSync(file).toString("base64")}`;
}

function photoCredit(key) {
  const m = PHOTO_META[key];
  if (!m) return "";
  const who = (m.author || "").replace(/\s+/g, " ").trim();
  return ["사진", who, m.license, "via Wikimedia Commons"].filter(Boolean).join(" · ");
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

  /* 편집형 본문 장 — v2 코덱스 시안(47d30b8d)의 판형을 옮긴다. 옮기면서 바꾼 둘:
     사진 히어로는 코드로 그린 실루엣으로(없는 장면을 만들지 않는다), 라벨·상태는
     정규식이 아니라 카피가 준 값으로(오늘 기사에만 맞는 규칙을 코드에 박지 않는다).
     장면은 카드 한 장 전체를 덮고 글자는 그 위에 올라간다(지니 09-18). */
  .card.ed { padding: 0; display: flex; flex-direction: column;
    background: ${c.bg}; color: ${c.ink}; }
  .card.ed::after { display: none; }
  .ed-art { position: absolute; inset: 0; color: ${c.accent}; opacity: .62; }
  /* 종이 위에 그림을 깔고, 글자 자리는 종이색으로 다시 덮는다. 어두운 판에서는
     같은 글자가 안 읽혔다(지니 09-18) — 카드는 훑는 물건이라 종이가 기본이다. */
  .ed-art::after { content: ""; position: absolute; inset: 0; background:
    linear-gradient(180deg, rgba(238,241,244,.34) 0%, rgba(238,241,244,.56) 34%,
      rgba(238,241,244,.80) 54%, rgba(238,241,244,.88) 100%),
    linear-gradient(90deg, rgba(238,241,244,.74) 0%, rgba(238,241,244,.22) 60%,
      rgba(238,241,244,0) 100%); }
  .ed-art svg { width: 100%; height: 100%; fill: none; stroke: currentColor;
    stroke-width: 2.5; }
  .ed-hero, .ed-body, .ed-ft { position: relative; z-index: 1; }
  .ed-hero { padding: 44px 54px 0; display: flex; flex-direction: column; flex: 0 0 auto; }
  .ed-hero .hd { color: ${inkMute}; }
  .ed-hero .hd .brand { color: ${accent}; }
  .ed-copy { margin-top: 84px; width: 92%; }
  .ed-kicker { display: flex; align-items: center; gap: 14px; font-size: 22px;
    font-weight: 750; color: ${inkMute}; }
  .ed-kicker strong { color: ${accent}; font-weight: 850; }
  .ed-title { margin-top: 18px; font-family: ${theme.fonts.heading.css};
    font-size: 68px; line-height: 1.08; letter-spacing: -2.8px; font-weight: 850;
    color: ${c.ink}; word-break: keep-all; }
  /* 강조는 제목의 마지막 구절 — 색만 바꾼다. 줄을 떨어뜨리면 제목이 두 덩이로
     갈려서 오히려 안 읽힌다(지니 09-18). */
  .ed-title .em { color: ${accent}; }
  .ed-deck { margin-top: 16px; max-width: 860px; color: ${inkDim};
    font-size: 27px; line-height: 1.4; font-weight: 550; word-break: keep-all; }
  .ed-body { padding: 0 54px; flex: 1; display: flex; flex-direction: column;
    justify-content: center; }
  .ed-head { color: ${accent}; font-size: 24px; font-weight: 850; letter-spacing: 1px; }
  .ed-grid { margin-top: 10px; display: flex; flex-direction: column; }
  /* 박스를 걷는다 — 면이 둘이면 내용이 아니라 칸이 먼저 읽힌다(지니 09-18).
     라벨·상태는 한 줄, 값은 그 아래 본문 크기로. 행은 가는 선으로만 가른다. */
  .ed-fact { display: block; padding: 20px 0; border-top: 1px solid rgba(18,41,76,.16); }
  .ed-fact:last-child { border-bottom: 1px solid rgba(18,41,76,.16); }
  .ed-fact .lbl { display: flex; align-items: center; gap: 12px; }
  .ed-fact .lbl strong { color: ${accent}; font-size: 26px; font-weight: 850;
    letter-spacing: .5px; }
  /* 알약 배경이 신호색(#E0E9F5)이면 종이와 구분이 안 된다 — 한 단 더 진하게. */
  .ed-fact .state { padding: 3px 11px; font-size: 18px; font-weight: 800;
    background: rgba(31,95,168,.16); color: ${accent}; }
  .ed-fact .state.muted { background: none; color: ${inkMute};
    border: 1px solid rgba(18,41,76,.24); }
  .ed-fact .val { margin-top: 9px; color: ${c.ink}; font-size: 33px;
    line-height: 1.32; font-weight: 700; word-break: keep-all; }
  .ed-fact .val.solo { margin-top: 0; }
  /* 취재 규모 + 관련 보도 — 이슈가 이미 들고 있는 재료다. 본문 수집이 막힌 날에도
     카드가 비지 않게 한다(지니 09-19 "내용이 너무 없다"). */
  .ed-more { margin-top: 26px; padding-top: 20px; border-top: 1px solid rgba(18,41,76,.16); }
  .ed-scale { display: flex; align-items: center; gap: 14px; font-size: 24px;
    font-weight: 750; color: ${inkDim}; }
  .ed-scale .ed-badge { padding: 4px 12px; background: rgba(31,95,168,.14); color: ${accent};
    font-size: 20px; font-weight: 800; }
  .ed-rel { margin-top: 14px; display: flex; flex-direction: column; gap: 9px; }
  .ed-rel li { list-style: none; display: grid; grid-template-columns: 1fr auto; gap: 16px;
    align-items: baseline; font-size: 24px; line-height: 1.34; font-weight: 600;
    color: ${inkDim}; word-break: keep-all; }
  .ed-rel li .s { font-size: 20px; font-weight: 700; color: ${inkMute}; white-space: nowrap; }
  .ed-why { margin-top: 30px; }
  .ed-lead { margin-top: 14px; color: ${c.ink}; font-size: 42px; line-height: 1.24;
    font-weight: 800; letter-spacing: -1.4px; word-break: keep-all; }
  .ed-checks { margin-top: 20px; display: flex; flex-direction: column; gap: 12px; }
  .ed-check { display: grid; grid-template-columns: 24px 1fr; gap: 16px;
    color: ${inkDim}; font-size: 26px; line-height: 1.36; font-weight: 650;
    word-break: keep-all; }
  .ed-check::before { content: ""; width: 11px; height: 11px; margin-top: 11px;
    background: ${accent}; }
  .ed-ft { padding: 26px 54px 40px; display: flex; justify-content: space-between;
    align-items: center; color: ${inkMute}; font-size: 23px; font-weight: 700; }
  .ed-ft .src { color: ${c.ink}; font-weight: 800; }

  /* ── 스토리 카드뉴스(5장) ────────────────────────────────────────────────
     이슈 하나를 표지·사실·쟁점·의미·체크리스트로 푸는 판형. 일일 카드와 달리
     장마다 역할이 다르고, 재료는 chronicle(이벤트·서사·관전포인트)에서 온다.
     밝은 아이보리 바탕 + 네이비 잉크 + 블루 포인트(연두는 폐기 팔레트라 안 쓴다). */
  .card.st { padding: 0; display: flex; flex-direction: column;
    background: #F6F2E9; color: #12294C; }
  .card.st::after { display: none; }
  /* 장마다 바탕을 달리해 넘길 때 리듬을 준다(시안). 쟁점 장만 옅은 블루 판. */
  .card.st.st-blue { background: #E6EEFA; }
  .st-hd { padding: 36px 44px 0; display: flex; justify-content: space-between;
    align-items: flex-start; flex: 0 0 auto; }
  .st-hd .brand { font-family: ${theme.fonts.heading.css}; font-size: 29px;
    font-weight: 900; letter-spacing: 3px; }
  .st-hd .tagline { display: block; margin-top: 7px; font-size: 16px; font-weight: 750;
    letter-spacing: 2.4px; color: #8A93A1; }
  .st-hd .num { font-size: 21px; font-weight: 800; letter-spacing: 2px; color: #8A93A1; }
  .st-body { flex: 1; padding: 24px 44px 34px; display: flex; flex-direction: column;
    min-height: 0; }
  .st-chip { align-self: flex-start; padding: 13px 26px; border-radius: 999px;
    background: #DDE8F6; color: #1F5FA8; font-size: 30px; font-weight: 850; }
  .st-q { margin-top: 20px; font-family: ${theme.fonts.heading.css}; font-size: 46px;
    font-weight: 850; line-height: 1.26; letter-spacing: -1.8px; word-break: keep-all; }
  .st-q .em { color: #1F5FA8; }
  .st-row { display: flex; align-items: center; gap: 20px; }
  .st-row .st-q { margin-top: 0; font-size: 50px; }

  /* 표지 */
  .st-photo { position: relative; height: 470px; flex: 0 0 auto; background-size: cover;
    background-position: center; clip-path: polygon(0 0, 100% 0, 100% 100%, 0 86%); }
  .st-photo::after { content: ""; position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(18,41,76,.46) 0%, rgba(18,41,76,.12) 40%,
      rgba(18,41,76,.06) 100%); }
  .st-photo .st-hd { position: relative; z-index: 1; color: #F7F5EF; }
  .st-photo .st-hd .tagline, .st-photo .st-hd .num { color: rgba(247,245,239,.78); }
  .st-cover .st-body { padding-top: 34px; }
  .st-title { margin-top: 20px; font-family: ${theme.fonts.heading.css}; font-size: 78px;
    font-weight: 850; line-height: 1.16; letter-spacing: -2.6px; word-break: keep-all; }
  .st-title .em { color: #1F5FA8; }
  .st-desc { margin-top: 24px; font-size: 33px; line-height: 1.48; font-weight: 600;
    color: #41506B; word-break: keep-all; }
  /* 표지 배지 — 그 이슈의 핵심 숫자. 원문에 있는 값이 있을 때만 붙는다. */
  .st-badge { margin: auto 0; align-self: stretch; display: flex; align-items: baseline;
    gap: 22px; background: #E6EEFA; border-left: 10px solid #1F5FA8; padding: 30px 34px; }
  .st-badge .v { font-family: ${theme.fonts.heading.css}; font-size: 72px; font-weight: 900;
    letter-spacing: -2px; color: #12294C; }
  .st-badge .l { font-size: 29px; font-weight: 700; color: #41506B; word-break: keep-all; }
  .st-credit { margin-top: auto; font-size: 16px; font-weight: 600; color: #9AA3B0; }
  .st-slogan { margin-top: 14px; display: flex; justify-content: space-between;
    align-items: baseline; }
  .st-slogan strong { font-family: ${theme.fonts.heading.css}; font-size: 24px;
    font-weight: 900; letter-spacing: 2px; }
  .st-slogan span { font-size: 19px; font-weight: 650; color: #8A93A1; }

  .st-lede { margin-top: 26px; font-family: ${theme.fonts.heading.css}; font-size: 54px;
    font-weight: 850; line-height: 1.3; letter-spacing: -1.6px; word-break: keep-all; }
  .st-lede .em { color: #1F5FA8; }

  /* 사실 정리 — 세로 타임라인 */
  .st-tl { flex: 1; margin: 26px 0 0; display: flex; flex-direction: column;
    justify-content: space-evenly; }
  .st-tl .tl-row { position: relative; display: grid; grid-template-columns: 34px 236px 1fr;
    gap: 24px; align-items: center; padding-bottom: 26px; }
  .st-tl .tl-row:last-child { padding-bottom: 0; }
  /* 연결선은 행마다 긋지 않고 한 줄로 관통시킨다 — 행 간격이 가변이라 토막난다. */
  .st-tl { position: relative; }
  .st-tl::before { content: ""; position: absolute; left: 15px; top: 26px; bottom: 26px;
    width: 2px; background: #D7DEE8; }
  .st-tl .dot { position: relative; z-index: 1; width: 24px; height: 24px; margin-left: 4px;
    border-radius: 50%; border: 6px solid #8FB8E4; background: #F6F2E9; box-sizing: border-box; }
  .st-tl .tl-row.now .dot { border-color: #12294C; background: #12294C; }
  .st-tl .when { font-size: 32px; font-weight: 800; color: #1F5FA8; word-break: keep-all; }
  .st-tl .tl-row.now .when { color: #12294C; }
  .st-tl .what { background: #E6EEFA; padding: 26px 30px; font-size: 33px;
    line-height: 1.34; font-weight: 700; color: #12294C; word-break: keep-all; }
  .st-tl .tl-row.now .what { background: #DCE8F8; }
  .st-note { margin-top: 20px; display: grid; grid-template-columns: 36px 1fr; gap: 18px;
    align-items: center; background: #EFEADC; padding: 28px 30px; font-size: 29px;
    line-height: 1.4; font-weight: 650; color: #1F3D6B; word-break: keep-all; }
  .st-note .ic { width: 32px; height: 32px; color: #1F5FA8; display: block; }
  .st-note .ic svg { width: 100%; height: 100%; fill: none; stroke: currentColor;
    stroke-width: 3; stroke-linejoin: round; }

  /* 핵심 쟁점 — 번호 카드 */
  .st-cards { flex: 1; margin: 26px 0 0; display: flex; flex-direction: column;
    justify-content: space-evenly; gap: 20px; }
  .st-icard { background: #FFFFFF; border: 1px solid #D3E0F2; padding: 30px 32px;
    display: grid; grid-template-columns: 104px 1fr; gap: 28px; align-items: center; }
  .st-icard .lead { display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .st-icard .n { font-family: ${theme.fonts.heading.css}; font-size: 26px; font-weight: 900;
    color: #1F5FA8; letter-spacing: 1px; }
  .st-icard .ic { width: 84px; height: 84px; border-radius: 50%; background: #E7EEF8;
    color: #1F5FA8; display: flex; align-items: center; justify-content: center; }
  .st-icard .ic svg { width: 46px; height: 46px; fill: none; stroke: currentColor;
    stroke-width: 3; stroke-linejoin: round; }
  .st-icard h3 { font-size: 37px; font-weight: 850; word-break: keep-all; }
  .st-icard ul { margin-top: 10px; display: flex; flex-direction: column; gap: 7px; }
  .st-icard li { list-style: none; display: grid; grid-template-columns: 14px 1fr; gap: 12px;
    font-size: 28px; line-height: 1.4; font-weight: 650; color: #35455F;
    word-break: keep-all; }
  .st-icard li::before { content: ""; width: 9px; height: 9px; margin-top: 12px;
    border-radius: 50%; background: #1F5FA8; }

  /* 왜 중요한가 */
  .st-msg { margin-top: 24px; font-family: ${theme.fonts.heading.css}; font-size: 56px;
    font-weight: 850; line-height: 1.3; letter-spacing: -1.8px; word-break: keep-all; }
  .st-msg .em { color: #1F5FA8; }
  .st-three { flex: 1; margin: 34px 0; display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 20px; align-content: center; }
  .st-three .cell { background: #FFFDF7; border: 1px solid #E6E0D2; padding: 34px 22px;
    text-align: center; }
  .st-three .ic { width: 96px; height: 96px; margin: 0 auto 20px; border-radius: 50%;
    background: #E7EEF8; color: #1F5FA8; display: flex; align-items: center;
    justify-content: center; }
  .st-three .ic svg { width: 52px; height: 52px; fill: none; stroke: currentColor;
    stroke-width: 3.5; }
  .st-three h4 { font-size: 32px; font-weight: 850; word-break: keep-all; }
  .st-three p { margin-top: 14px; font-size: 26px; line-height: 1.44; font-weight: 620;
    color: #41506B; word-break: keep-all; }
  .st-quotes { margin-top: auto; display: flex; flex-direction: column; gap: 14px; }
  .st-quote { background: #FFFDF7; border: 1px solid #E6E0D2; padding: 20px 24px;
    display: grid; grid-template-columns: 40px 1fr; gap: 18px; font-size: 29px;
    line-height: 1.42; font-weight: 650; color: #35455F; word-break: keep-all; }
  .st-quote::before { content: "C"; font-family: ${theme.fonts.heading.css};
    font-size: 46px; font-weight: 900; color: #A9C4E6; line-height: .9; }

  /* 앞으로 볼 것 */
  /* 인용을 옆 칸에 세우면 세로로 긴 빈 면에 작은 글씨가 갇힌다(지니 09-19).
     체크리스트가 폭을 다 쓰고, 인용은 그 아래 가로 띠로 깐다. */
  .st-check { flex: 1; margin-top: 26px; display: flex; flex-direction: column;
    justify-content: space-evenly; gap: 16px; }
  .st-check .item { display: grid; grid-template-columns: 42px 1fr; gap: 18px;
    align-items: center; background: #FFFDF7; border: 1px solid #E6E0D2;
    padding: 26px 24px; font-size: 30px; font-weight: 700; color: #35455F;
    word-break: keep-all; }
  .st-check .item.on { background: #E6EEFA; border-color: #CFDFF4; }
  .st-check .box { width: 38px; height: 38px; border: 2px solid #C3CDDB; color: #FFFDF7;
    display: flex; align-items: center; justify-content: center; font-size: 19px;
    font-weight: 900; }
  .st-check .item.on .box { background: #1F5FA8; border-color: #1F5FA8; }
  .st-check .item.on { color: #12294C; }
  .st-aside { margin-top: 20px; background: #E6EEFA; padding: 26px 30px; font-size: 31px;
    line-height: 1.4; font-weight: 700; color: #1F3D6B; word-break: keep-all;
    display: grid; grid-template-columns: 70px 1fr; gap: 24px; align-items: center; }
  .st-aside .ic { display: block; width: 64px; height: 64px; color: #1F5FA8; }
  .st-aside .ic svg { width: 100%; height: 100%; fill: none; stroke: currentColor;
    stroke-width: 3; stroke-linejoin: round; }
  .st-cta { margin-top: 26px; align-self: flex-start; background: #12294C; color: #F6F2E9;
    padding: 22px 40px; border-radius: 999px; font-size: 30px; font-weight: 800; }

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

function storyHead(s, tagline) {
  return `<div class="st-hd"><div><span class="brand">NUCLENS</span>
      <span class="tagline">${esc(tagline || "")}</span></div>
    <span class="num">${esc(s.slideNum || "")}</span></div>`;
}

const STORY_ICONS = {
  market: `<path d="M8 38h10v10H8zM19 26h10v22H19zM30 14h10v34H30z"/><path d="M6 8h36"/>`,
  shield: `<path d="M24 5l17 7v13c0 11-7 18-17 22C14 43 7 36 7 25V12z"/><path d="M16 24l6 6 11-12"/>`,
  network: `<circle cx="24" cy="11" r="6"/><circle cx="10" cy="37" r="6"/><circle cx="38" cy="37" r="6"/><path d="M20 16L13 31M28 16l7 15M16 37h16"/>`,
  clip: `<path d="M20 6h14l8 8v28H20z"/><path d="M26 20h12M26 28h12M26 36h8"/>`,
  coins: `<ellipse cx="24" cy="13" rx="15" ry="6"/><path d="M9 13v8c0 3.3 6.7 6 15 6s15-2.7 15-6v-8"/><path d="M9 21v8c0 3.3 6.7 6 15 6s15-2.7 15-6v-8"/><path d="M9 29v8c0 3.3 6.7 6 15 6s15-2.7 15-6v-8"/>`,
  plant: `<path d="M6 42V22l12-7v7l12-7v27z"/><path d="M30 42V14h12v28"/><path d="M12 28v6M20 28v6M34 22v6"/>`,
  doc: `<path d="M13 6h16l8 8v28H13z"/><path d="M29 6v9h8"/><path d="M19 22h12M19 29h12M19 36h8"/>`,
  scope: `<path d="M6 30l24-13 5 9-24 13z"/><path d="M30 17l9-5 5 9-9 5"/><path d="M15 36l4 8M19 44h-8"/><circle cx="38" cy="30" r="3"/>`,
};

function storyIcon(kind) {
  return `<svg viewBox="0 0 48 48">${STORY_ICONS[kind] || STORY_ICONS.market}</svg>`;
}

function renderStory(s, theme, type) {
  const num = esc(s.slideNum || "");
  const chip = s.chip ? `<div class="st-chip">${esc(s.chip)}</div>` : "";

  if (type === "story-cover") {
    const key = s.photo || photoKey(s.topic);
    const data = photoData(key);
    return shell(
      `<div class="card st st-cover">
        <div class="st-photo" style="background-image:url('${data}')">
          ${storyHead(s, s.tagline || "NEWS FOR A BRIGHTER TOMORROW")}
        </div>
        <div class="st-body">
          <div class="st-row">${chip}${s.topic ? `<span class="st-desc" style="margin:0;font-size:24px;font-weight:700;color:#12294C">${esc(s.topic)}</span>` : ""}</div>
          <h1 class="st-title">${accentize(s.headline, "em")}</h1>
          <p class="st-desc">${esc(s.deck || "")}</p>
          ${s.badge ? `<div class="st-badge"><span class="v">${esc(s.badge.value)}</span>
            <span class="l">${esc(s.badge.label)}</span></div>` : ""}
          <div class="st-credit">${esc(photoCredit(key))}</div>
          <div class="st-slogan"><strong>NUCLENS</strong><span>${esc(s.slogan || "원전을 넘어, 더 나은 내일로")}</span></div>
        </div>
      </div>`, theme, false);
  }

  if (type === "story-facts") {
    const rows = (s.timeline || []).slice(0, 5).map((r, i, arr) =>
      `<div class="tl-row${i === arr.length - 1 ? " now" : ""}"><div class="dot"></div>
        <div class="when">${esc(r.when)}</div><div class="what">${esc(r.what)}</div></div>`).join("");
    return shell(
      `<div class="card st">
        ${storyHead(s, s.tagline || "GLOBAL NUCLEAR INSIGHT")}
        <div class="st-body">
          <div class="st-row">${chip}<h2 class="st-q">${accentize(s.headline, "em")}</h2></div>
          ${s.lede ? `<p class="st-lede">${accentize(s.lede, "em")}</p>` : ""}
          <div class="st-tl">${rows}</div>
          ${s.note ? `<div class="st-note"><span class="ic">${storyIcon("clip")}</span><span>${esc(s.note)}</span></div>` : ""}
        </div>
      </div>`, theme, false);
  }

  if (type === "story-issues") {
    const cards = (s.issues || []).slice(0, 3).map((it, i) =>
      `<div class="st-icard"><div class="lead"><span class="n">${String(i + 1).padStart(2, "0")}</span>
          <span class="ic">${storyIcon(it.icon || ["coins", "plant", "doc"][i] || "doc")}</span></div>
        <div><h3>${esc(it.title)}</h3>
          <ul>${(it.points || []).slice(0, 2).map((t) => `<li><span>${esc(t)}</span></li>`).join("")}</ul>
        </div></div>`).join("");
    return shell(
      `<div class="card st st-blue">
        ${storyHead(s, s.tagline || "FOCUS ON WHAT MATTERS")}
        <div class="st-body">
          <div class="st-row">${chip}<h2 class="st-q">${accentize(s.headline, "em")}</h2></div>
          <div class="st-cards">${cards}</div>
        </div>
      </div>`, theme, false);
  }

  if (type === "story-why") {
    const cells = (s.pillars || []).slice(0, 3).map((c) =>
      `<div class="cell"><div class="ic">${storyIcon(c.icon)}</div><h4>${esc(c.title)}</h4><p>${esc(c.text)}</p></div>`).join("");
    return shell(
      `<div class="card st">
        ${storyHead(s, s.tagline || "BIGGER PICTURE, CLEARER INSIGHTS")}
        <div class="st-body">
          ${chip}
          <h2 class="st-msg">${accentize(s.headline, "em")}</h2>
          <div class="st-three">${cells}</div>
          <div class="st-quotes">${(s.quotes || []).slice(0, 2).map((q) => `<p class="st-quote">${esc(q)}</p>`).join("")}</div>
        </div>
      </div>`, theme, false);
  }

  // story-check
  const items = (s.checks || []).slice(0, 5).map((c) =>
    `<div class="item${c.done ? " on" : ""}"><span class="box">${c.done ? "✓" : ""}</span><span>${esc(c.text)}</span></div>`).join("");
  return shell(
    `<div class="card st">
      ${storyHead(s, s.tagline || "NEXT STEP FOR A SUSTAINABLE TOMORROW")}
      <div class="st-body">
        <div class="st-row">${chip}<h2 class="st-q">${accentize(s.headline, "em")}</h2></div>
        <div class="st-check">${items}</div>
        ${s.aside ? `<div class="st-aside"><span class="ic">${storyIcon("scope")}</span><span>${esc(s.aside)}</span></div>` : ""}
        <div class="st-cta">${esc(s.cta || "지금 이슈를 계속 업데이트합니다")} →</div>
      </div>
    </div>`, theme, false);
}

function renderSlide(s, theme) {
  const type = s.type || "step";
  if (type.startsWith("story-")) return renderStory(s, theme, type);
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

  const rows = editorialRows(s);
  const checks = (Array.isArray(s.why) ? s.why : []).slice(1, 3);
  const lead = (Array.isArray(s.why) ? s.why : [])[0] || "";
  // 리드 문장이 사실 카드에 다시 서지 않도록, 덱은 signals 가 있을 때만 쓴다.
  const deck = (Array.isArray(s.signals) && s.signals.length)
    ? (Array.isArray(s.points) ? s.points[0] || "" : "") : "";
  const context = Array.isArray(s.meta) ? s.meta.join(" · ").replaceAll("#", "") : "";
  return shell(
    `<div class="card ed">
      <div class="ed-art ghost">${heroArt(s.stepLabel)}</div>
      <section class="ed-hero">
        <div class="hd"><span class="brand">NUCLENS</span><span>${num}</span></div>
        <div class="ed-copy">
          <div class="ed-kicker"><strong>${esc(s.stepLabel || "")}</strong>${context ? `<span>${esc(context)}</span>` : ""}</div>
          <h1 class="ed-title">${accentize(s.headline, "em")}</h1>
          ${deck ? `<p class="ed-deck">${esc(deck)}</p>` : ""}
        </div>
      </section>
      <section class="ed-body">
        <div class="ed-head">확인된 사실</div>
        <div class="ed-grid">${rows.map((row, i) =>
          `<div class="ed-fact">
             ${row.label ? `<div class="lbl"><strong>${esc(row.label)}</strong>${row.state ? `<span class="state${row.muted ? " muted" : ""}">${esc(row.state)}</span>` : ""}</div>` : ""}
             <div class="val${row.label ? "" : " solo"}">${esc(row.text)}</div>
           </div>`
        ).join("")}</div>
        ${rows.length && (s.coverage || (s.related || []).length) ? `<div class="ed-more">
          ${s.coverage ? `<div class="ed-scale"><span>${esc(s.coverage)}</span>${s.verified ? `<span class="ed-badge">${esc(s.verified)}</span>` : ""}</div>` : ""}
          ${(s.related || []).length ? `<ul class="ed-rel">${s.related.map((r) =>
            `<li><span class="t">${esc(r.title)}</span>${r.source ? `<span class="s">${esc(r.source)}</span>` : ""}</li>`).join("")}</ul>` : ""}
        </div>` : ""}
        ${lead ? `<div class="ed-why">
          <div class="ed-head">왜 중요한가</div>
          <p class="ed-lead">${esc(lead)}</p>
          ${checks.length ? `<div class="ed-checks">${checks.map((t) => `<div class="ed-check"><span>${esc(t)}</span></div>`).join("")}</div>` : ""}
        </div>` : ""}
      </section>
      <footer class="ed-ft"><span>${site}</span><span class="src">${esc(s.footer || "")}</span></footer>
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

  // 스토리 카드뉴스는 같은 렌더러를 쓰되 다른 폴더로 뽑는다(CARDS_OUT=out-story).
  // 한 폴더를 나눠 쓰면 나중에 도는 쪽이 앞 앨범의 PNG 를 지운다.
  const outDir = path.resolve(process.cwd(), process.env.CARDS_OUT || "out");
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
          if (el.classList.contains("ghost")) continue;   // 장식 그래픽은 일부러 흘러넘친다
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
