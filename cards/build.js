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

function parseArgs(argv) {
  // 스토리 카드는 같은 렌더러를 다른 폴더로 뽑는다(같은 out 을 쓰면 나중에 도는
  // 쪽이 앞 앨범 PNG 를 지운다). v1 은 CARDS_OUT 환경변수로, v2 는 --out-dir
  // 플래그로 했다 — 둘 다 받는다. 플래그가 있으면 그것이 이긴다.
  const out = { input: "slides.json", outDir: process.env.CARDS_OUT || "out",
    sample: false, check: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--sample") out.sample = true;
    else if (arg === "--check") out.check = true;
    else if (arg === "--out-dir") {
      if (!argv[i + 1]) throw new Error("--out-dir requires a path");
      out.outDir = argv[++i];
    } else if (arg.startsWith("--")) {
      throw new Error(`Unknown option: ${arg}`);
    } else {
      out.input = arg;
    }
  }
  return out;
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
  // 강조 뒤에 글이 더 있으면 **줄을 차지하지 않게** 한다. 에디토리얼 제목의
  // .em 은 display:block 이라, 문장 가운데 강조가 오면 뒤 말이 셋째 줄로 밀려
  // 히어로가 넘치고 렌더 가드가 카드를 통째로 죽인다(실측 09-20: 26자 제목).
  const html = esc(text);
  return html.replace(/\[\[(.+?)\]\]/g, (hit, inner, at) => {
    const keep = [...inner].length <= 8 ? " keep" : "";
    const mid = at + hit.length < html.length ? " inline" : "";
    return `<span class="${cls}${keep}${mid}">${inner}</span>`;
  });
}

function autoAccent(text) {
  // 모델이 `[[ ]]` 를 빼먹는 날이 있다(09-20: 본문 3장 전부). 그러면 제목이
  // 흰 글씨 한 덩어리로 나가 지니가 본 "제목이 그냥 하얗다" 가 된다. 프롬프트가
  // 요구하는 자리(뒤쪽 짧은 구간)에 코드가 대신 건다 — 색은 규격이지 판단이 아니다.
  const t = String(text || "").trim();
  if (!t || t.includes("[[")) return text;
  const words = t.split(/\s+/);
  if (words.length < 2) return text;
  const span = [words.pop()];
  while (words.length > 1 && span.join(" ").length < 4) span.unshift(words.pop());
  return `${words.join(" ")} [[${span.join(" ")}]]`;
}

function imageData(relativePath) {
  if (!relativePath) return "";
  const file = path.resolve(process.cwd(), relativePath);
  if (!fs.existsSync(file)) throw new Error(`image asset missing: ${file}`);
  const ext = path.extname(file).toLowerCase() === ".jpg" ? "jpeg" : "png";
  return `data:image/${ext};base64,${fs.readFileSync(file).toString("base64")}`;
}

function editorialIcon(kind) {
  const icons = {
    control: `<path d="M18 6h28v36H18zM24 14h16M24 22h16M24 30h10"/><path d="M32 6V2"/>`,
    grid: `<path d="M8 46h48M15 46l7-34h20l7 34M19 25h26M17 35h30M27 12v34M37 12v34"/>`,
    vote: `<path d="M10 21h44v30H10zM17 21l15-13 15 13M19 29v14M29 29v14M39 29v14M49 29v14"/>`,
    atom: `<ellipse cx="32" cy="28" rx="25" ry="9"/><ellipse cx="32" cy="28" rx="25" ry="9" transform="rotate(60 32 28)"/><ellipse cx="32" cy="28" rx="25" ry="9" transform="rotate(120 32 28)"/><circle cx="32" cy="28" r="3"/>`,
    globe: `<circle cx="32" cy="28" r="23"/><path d="M9 28h46M32 5c8 7 12 14 12 23S40 44 32 51M32 5C24 12 20 19 20 28s4 16 12 23"/>`,
    mou: `<path d="M14 46V20l18-10 18 10v26M10 46h44M20 26h4v12h-4M30 26h4v12h-4M40 26h4v12h-4M12 20h40"/>`,
  };
  const paths = icons[kind] || icons.mou;
  return `<svg viewBox="0 0 64 56" aria-hidden="true">${paths}</svg>`;
}

function topicIcon(label) {
  // 아이콘은 **분류**에서 고른다. 문장에서 키워드를 찾으면 틀린 라벨이 붙는다
  // (09-20 "확대" 사고). 분류는 사이트가 정한 값이라 카드가 지어내지 않는다.
  const t = String(label || "");
  if (/전력|송전|수급|요금/.test(t)) return "grid";
  if (/SMR|원전|기술|연료/.test(t)) return "atom";
  if (/규제|인허가|국회|법/.test(t)) return "vote";
  if (/수출|협력|외교|통상/.test(t)) return "mou";
  if (/국제|해외/.test(t)) return "globe";
  return "control";
}

function factPresentation(text, index) {
  // 예전에는 키워드 표(`/SMR|전력망/ → ["핵심 분야","확대"]` 같은)로 라벨과 칩을
  // 붙였다. 프로토타입 문장에 맞춰 손으로 쓴 표라 **오늘 문장에는 거의 틀린다** —
  // 09-20 실물: "석탄발전 퇴출 및 SMR·LNG 전환 로드맵 추진" 에 "확대" 칩이 붙었다.
  // 게다가 라벨+칩이 200px 가까이 먹어 문장이 두 줄로 밀리고 "추진" 한 낱말만
  // 다음 줄에 남았다(지니 09-20: "확대 때문에 추진이 밑으로 내려와 이상하다").
  // 재료에서 나오지 않는 라벨은 붙이지 않는다. 문장이 그 폭을 가져간다.
  void index;
  return { label: "", text: String(text || ""), state: "", icon: "control", tone: "active" };
}

// **렌더러는 의미를 만들지 않는다.** 그림과 도장만 고른다.
//
// 예전에는 세 분기가 `headline` 까지 시연용 고정 문구로 덮었다 — 법안 가결,
// 협력 채널 신설, MOU 서명 연기 세 가지였다(문구를 글자 그대로 옮겨 적지
// 않는다. 되살리기 쉬워진다). 걸리는 조건이
// `/대미|웨스팅하우스|MOU|한미/` 처럼 **매우 넓다.** 한미 협력 기사는 거의
// 매주 나오므로, 그 주제의 카드는 그날 무슨 일이 있었든 "MOU 서명 연기" 라고
// 적힌 채 나갈 수 있었다. 검증·QA 는 전부 이 앞 단계에 있어서 아무도 못 잡는다
// — 검증한 문장과 인쇄된 문장이 다른 것이 문제의 본질이다.
//
// 이제 이 함수는 headline 을 받지도 돌려주지도 않는다. 되살리려면 시그니처를
// 바꿔야 하고, 그 순간 `test_renderer_never_rewrites_meaning` 가 걸린다.
function pickEditorialArt(haystack) {
  let image = "assets/energy-cooperation-editorial.png";
  let stamp = "ENERGY INFRASTRUCTURE";
  if (/데이터센터|100MW|417표|GRID Savings/.test(haystack)) {
    image = "assets/data-center-grid-editorial.png";
    stamp = "DATA CENTER · POWER GRID";
  } else if (/휴스턴|474GW|협력 정례 채널|협력 대화/.test(haystack)) {
    image = "assets/energy-cooperation-editorial.png";
    stamp = "GRID · SMR COOPERATION";
  } else if (/대미|웨스팅하우스|MOU|한미/.test(haystack)) {
    image = "assets/korea-us-flags-editorial.png";
    stamp = "KOREA · U.S.\nNUCLEAR COOPERATION";
  } else if (/국정감사|상임위/.test(haystack)) {
    image = "assets/reactor-operations-editorial.png";
    stamp = "POLICY OVERSIGHT · OPERATIONS";
  } else if (/사용후핵연료|방폐|방사성폐기물|핵연료주기|해체|폐로/.test(haystack)) {
    image = "assets/fuel-cycle-waste-editorial.png";
    stamp = "FUEL CYCLE · WASTE";
  } else if (/SMR|나트륨 원전|핵융합|마이크로원자로|동위원소|비발전 활용|연구|실증|첨단 원자로/.test(haystack)) {
    image = "assets/research-technology-editorial.png";
    stamp = "NUCLEAR RESEARCH · TECHNOLOGY";
  } else if (/계속운전|재가동|원전 운영|정비|안전|사고|규제|인허가|신규 건설|신규 원전/.test(haystack)) {
    image = "assets/reactor-operations-editorial.png";
    stamp = "NUCLEAR OPERATIONS · SAFETY";
  }
  return { image, stamp };
}

function editorialFromStep(slide) {
  // 심층 카드는 make_cards 가 editorial 형태로 완성해 보낸다 — 손대지 않는다.
  if (slide.type === "editorial") {
    if (slide.image) return slide;
    const hs = [slide.stepLabel, slide.headline,
      ...(slide.statusRows || []).map((r) => r.text)].join(" ");
    return { ...slide, image: pickEditorialArt(hs).image };
  }
  const points = Array.isArray(slide.points) ? slide.points : [];
  const why = Array.isArray(slide.why) ? slide.why : [];
  const haystack = [slide.stepLabel, slide.headline, ...points].join(" ");
  const { image, stamp } = pickEditorialArt(haystack);
  return {
    ...slide,
    type: "editorial",
    // headline 은 들어온 그대로다. 위 주석을 볼 것.
    image,
    stamp,
    // meta 가 stepLabel 과 같은 말이면 키커에 두 번 찍힌다("SMR | smr").
    // 대소문자·공백만 다른 경우까지 같은 것으로 본다.
    context: (() => {
      const meta = Array.isArray(slide.meta) ? slide.meta.join(" · ").replaceAll("#", "") : "";
      const norm = (t) => String(t || "").replace(/\s+/g, "").toLowerCase();
      return norm(meta) === norm(slide.stepLabel) ? "" : meta;
    })(),
    // 덱이 points[0] 이면 바로 아래 첫 사실 줄과 글자까지 같다(3장 전수 확인).
    // 히어로의 60px 짜리 자리를 중복에 쓰지 않는다 — 세 번째 사실을 올린다.
    // 아래 statusRows 가 앞의 둘만 그리므로, 그러지 않으면 셋째 사실은 카드
    // 어디에도 안 나온다.
    //
    // **`why[0]` 폴백은 걷었다 (2026-09-20).** 사실이 둘뿐인 날 그 줄이 덱과
    // whyLead 두 자리에 같은 글자로 찍혔다 — 한 카드 안의 같은 문장이다.
    // 셋째 사실이 없으면 덱은 비우고, 마크업이 그 줄을 아예 안 그린다.
    // 히어로가 조금 성긴 것이 같은 말을 두 번 하는 것보다 낫다.
    deck: points[2] || "",
    statusRows: points.slice(0, 2).map(factPresentation),
    whyLead: why[0] || "",
    whyChecks: why.slice(1, 3),
  };
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

  /* 상태형 카드. 불릿을 한 줄로 쌓는 대신 사건 판단 → 규모 → 협상 상태 → 의미의
     순서로 읽힌다. 장식 그래픽이 아니라 원고 안에 실제로 있는 구조만 면으로 만든다. */
  .status-card { padding: 0; display: grid; grid-template-rows: 414px 578px 88px; }
  .status-card::after { display: none; }
  .status-hero { padding: 48px 54px 40px; background:
    radial-gradient(90% 140% at 100% 0%, rgba(90,160,232,.34) 0%, transparent 55%),
    ${c.bgDark}; color: ${c.inkOnDark}; }
  .status-hero .hd { color: rgba(238,241,244,.55); }
  .status-hero .hd .brand { color: ${c.accentBright}; }
  .status-eyebrow { margin-top: 68px; display: flex; align-items: center; gap: 18px;
    color: ${c.accentBright}; font-size: 24px; font-weight: 800; letter-spacing: -.3px; }
  .status-eyebrow .serial { min-width: 58px; padding: 9px 12px 8px;
    border: 1px solid rgba(90,160,232,.55); text-align: center; font-size: 21px;
    letter-spacing: 1px; }
  .status-hero h1 { margin-top: 25px; max-width: 900px; font-family: ${theme.fonts.heading.css};
    font-size: 74px; line-height: 1.12; letter-spacing: -3px; font-weight: 820;
    word-break: keep-all; }
  .status-hero h1 .em { color: ${c.accentBright}; }
  .status-content { padding: 34px 54px 28px; display: grid;
    grid-template-rows: 246px 1fr; gap: 28px; min-height: 0; }
  .status-overview { display: grid; grid-template-columns: 344px 1fr; gap: 38px; }
  .amount-block { border-right: 1px solid ${c.rule}; padding-right: 34px; }
  .micro-label { color: ${inkMute}; font-size: 18px; font-weight: 800;
    letter-spacing: 2.4px; }
  .amount-line { margin-top: 20px; color: ${accent}; display: flex; align-items: baseline;
    gap: 10px; font-family: ${theme.fonts.heading.css}; white-space: nowrap; }
  .amount-line .value { font-size: 86px; line-height: .9; font-weight: 900;
    letter-spacing: -5px; }
  .amount-line .unit { font-size: 35px; font-weight: 850; letter-spacing: -1px; }
  .amount-caption { margin-top: 24px; color: ${inkDim}; font-size: 25px;
    line-height: 1.3; font-weight: 650; }
  .deal-status { display: grid; grid-template-rows: repeat(2, 1fr); }
  .deal-row { display: grid; grid-template-columns: 116px 1fr; align-items: center;
    border-top: 1px solid rgba(18,41,76,.2); }
  .deal-row:last-child { border-bottom: 1px solid rgba(18,41,76,.2); }
  .deal-row .key { color: ${accent}; font-size: 20px; font-weight: 850;
    letter-spacing: 1px; }
  .deal-row .copy { color: ${ink}; font-size: 27px; line-height: 1.32;
    font-weight: 650; word-break: keep-all; }
  .implications { border-top: 5px solid ${ink}; padding-top: 20px; }
  .implication-head { display: flex; justify-content: space-between; align-items: baseline; }
  .implication-head strong { color: ${ink}; font-size: 23px; font-weight: 850; }
  .implication-head span { color: ${inkMute}; font-size: 17px; font-weight: 700;
    letter-spacing: 1.5px; }
  .implication-grid { margin-top: 18px; display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 24px; }
  .implication { display: grid; grid-template-columns: 32px 1fr; gap: 12px;
    color: ${ink}; font-size: 24px; line-height: 1.42; font-weight: 620;
    word-break: keep-all; }
  .implication .n { color: ${accent}; font-family: ${theme.fonts.heading.css};
    font-size: 19px; font-weight: 900; padding-top: 4px; }
  .status-footer { margin: 0 54px; display: flex; justify-content: space-between;
    align-items: center; border-top: 1px solid ${c.rule}; color: ${inkMute};
    font-size: 22px; font-weight: 700; }
  .status-footer .src { color: ${inkDim}; font-weight: 850; }

  /* 편집형 카드. 사용자가 고른 레퍼런스의 핵심인 사진 히어로, 상태 카드 둘,
     흰 해설면, 다크 출처 푸터를 코드로 재구성한다. */
  /* B안 — 사진이 카드 전체의 바탕이다. 글 있는 칸까지 사진 위에 얹되,
     스크림을 아래로 갈수록 짙게 깔아 본문 구간의 배경 알파를 .93 이상으로
     잠근다(레퍼런스 실측: 사진 위 텍스트는 배경 휘도 L≤30 = 대비 12:1 이상). */
  .editorial-card { padding: 0; display: grid; grid-template-rows: 430px 562px 88px;
    background: ${c.bgDark}; isolation: isolate; }
  /* 피사체가 하단에 있으면 본문 스크림에 묻힌다 — 위쪽으로 당겨 히어로 칸에 세운다. */
  .card-photo { position: absolute; inset: 0; z-index: -2;
    background-size: cover; background-position: center 26%; }
  .card-scrim { position: absolute; inset: 0; z-index: -1;
    background:
      linear-gradient(180deg, rgba(9,22,40,.22) 0%, rgba(9,22,40,.30) 26%,
        rgba(9,22,40,.58) 41%, rgba(9,22,40,.80) 50%, rgba(9,22,40,.86) 72%,
        rgba(9,22,40,.88) 100%),
      linear-gradient(90deg, rgba(9,22,40,.94) 0%, rgba(9,22,40,.88) 30%,
        rgba(9,22,40,.58) 52%, rgba(9,22,40,.16) 74%, rgba(9,22,40,0) 100%); }
  /* 밝은 사진 가드가 붙으면 아래 절반을 한 단 더 누른다(렌더 스크립트가 실측). */
  /* 표지·마지막 장은 격자 없이 배경 레이어만 빌린다. */
  .card.has-photo { isolation: isolate; }
  .bright-photo > .card-scrim {
    background:
      linear-gradient(180deg, rgba(9,22,40,.34) 0%, rgba(9,22,40,.46) 26%,
        rgba(9,22,40,.76) 41%, rgba(9,22,40,.91) 50%, rgba(9,22,40,.94) 72%,
        rgba(9,22,40,.95) 100%),
      linear-gradient(90deg, rgba(9,22,40,.96) 0%, rgba(9,22,40,.92) 30%,
        rgba(9,22,40,.70) 52%, rgba(9,22,40,.28) 74%, rgba(9,22,40,.10) 100%); }
  .editorial-card::after { display: none; }
  .editorial-hero { position: relative; overflow: hidden; padding: 40px 76px 38px;
    color: ${c.inkOnDark}; background: transparent; }
  .editorial-photo { display: none; }
  .editorial-hero .hd, .editorial-copy { position: relative; z-index: 1; }
  .editorial-hero .hd { color: rgba(238,241,244,.72); }
  /* 쪽번호는 오버레이가 옅은 우측(α.20)에 선다 — 사진이 밝으면 사라진다.
     불투명도를 올리고 어두운 그림자로 받친다. */
  .editorial-hero .hd span:not(.brand) { color: rgba(238,241,244,.95);
    text-shadow: 0 1px 6px rgba(9,22,40,.85); }
  .editorial-hero .hd .brand { color: ${c.inkOnDark}; letter-spacing: 2.5px; }
  .editorial-copy { margin-top: 44px; width: 66%; }
  .editorial-kicker { color: rgba(238,241,244,.82); font-size: 20px; font-weight: 700;
    letter-spacing: .2px; }
  .editorial-kicker strong { color: ${c.inkOnDark}; font-weight: 850; }
  .editorial-title { margin-top: 22px; font-family: ${theme.fonts.heading.css};
    font-size: 72px; line-height: 1.08; letter-spacing: -3.2px; font-weight: 850;
    word-break: keep-all; }
  .editorial-title .em { display: block; color: ${c.accentBright}; font-size: 1.13em; }
  .editorial-title .em.inline { display: inline; font-size: 1em; }
  .editorial-deck { margin-top: 24px; max-width: 520px; color: rgba(238,241,244,.88);
    font-size: 25px; line-height: 1.42; font-weight: 520; word-break: keep-all; }
  /* 사진 우하단 영문 스탬프는 14px(폰 5.1px) 라 읽히지 않고, 오버레이가 옅은
     구간이라 사진마다 대비가 흔들렸다. 장식이므로 뺀다. */
  .editorial-stamp { display: none; }
  /* 글을 박스에 가두지 않는다. 카드 안의 카드(테두리·아이콘 원·상태칩)가
     텍스트 폭을 273px 로 졸라매 본문이 18px 에 갇혀 있었다(폰에서 6.5px).
     박스를 풀어 폭을 928px 로 열고, 그 폭을 글자 크기로 환산한다.
     기준: 폰(390px)에서 14px 이상 = 1080 기준 39px 이상. */
  /* 09-18 판형으로 되돌리되(밝은 패널·박스·아이콘) **글자를 키운다**.
     예전 실패는 박스 자체가 아니라 **2열**이었다 — 한 칸이 273px 로 졸아들어
     문장이 18px(폰 6.5px)에 갇혔다. 1열로 펴면 같은 박스 안에서 폭이 850px 라
     36px(폰 13px)까지 올라간다. 대신 한 장에 담는 줄 수가 줄어든다. */
  .editorial-body { padding: 28px 56px 22px; background: #F4F7FA; color: #12294C; }
  .editorial-section-head { color: ${accent}; font-size: 30px; font-weight: 900;
    letter-spacing: -.4px; }
  .editorial-section-head span { display: none; }
  .event-grid { margin-top: 16px; display: grid; grid-template-columns: 1fr; gap: 13px; }
  .event-card { border: 1px solid rgba(18,41,76,.16); border-radius: 14px;
    background: #FFFFFF; display: grid; grid-template-columns: 62px 1fr;
    align-items: center; gap: 18px; padding: 17px 20px; }
  .event-icon { width: 58px; height: 58px; border-radius: 50%; background: #DCE9F7;
    display: flex; align-items: center; justify-content: center; color: ${c.signalInk}; }
  .event-icon svg { width: 34px; height: 34px; fill: none; stroke: currentColor;
    stroke-width: 3.5; stroke-linecap: square; stroke-linejoin: miter; }
  .event-copy strong { display: block; color: ${ink}; font-size: 24px; font-weight: 820; }
  .event-copy span { display: block; color: #12294C; font-size: 38px; line-height: 1.32;
    font-weight: 700; letter-spacing: -.8px; word-break: keep-all; }
  .event-state { display: none; }
  .why-editorial { margin-top: 20px; padding-top: 16px;
    border-top: 2px solid rgba(18,41,76,.30); }
  /* 의미 칸도 1열이다. 예전에는 1.25fr/.85fr 로 갈라 확인점이 18px 였다. */
  .why-layout { margin-top: 10px; display: block; }
  .why-lead { color: #12294C; font-size: 40px; line-height: 1.28; font-weight: 820;
    letter-spacing: -1.2px; word-break: keep-all; }
  .why-checks { margin-top: 12px; display: flex; flex-direction: column; gap: 9px; }
  .why-check { display: grid; grid-template-columns: 30px 1fr; gap: 12px;
    align-items: start; color: #2A4368; font-size: 33px; line-height: 1.3;
    font-weight: 660; word-break: keep-all; }
  .why-check::before { content: "✓"; width: 28px; height: 28px; margin-top: 6px;
    border-radius: 50%; background: ${accent}; color: white; display: flex;
    align-items: center; justify-content: center; font-size: 18px; font-weight: 900; }
  .editorial-footer { padding: 0 76px; display: flex; justify-content: space-between;
    align-items: center; background: transparent; color: rgba(238,241,244,.72);
    border-top: 1px solid rgba(238,241,244,.18); }
  .editorial-cta { display: grid; grid-template-columns: 46px auto; column-gap: 14px;
    align-items: center; font-size: 26px; line-height: 1.35; }
  .editorial-arrow { grid-row: span 2; width: 42px; height: 42px; border: 1px solid rgba(238,241,244,.28);
    border-radius: 8px; display: flex; align-items: center; justify-content: center;
    color: ${c.inkOnDark}; font-size: 27px; }
  .editorial-site { color: rgba(238,241,244,.52); }
  .editorial-source { text-align: right; font-size: 26px; line-height: 1.45; }
  .editorial-source strong { color: ${c.inkOnDark}; }

  /* ── 스토리 카드(v1 이식) ─────────────────────────────────────────
     일일 카드와 다른 트랙이다. 밝은 크림 판형에 이슈 하나를 5장으로 푼다 —
     같은 앨범이 아니라 별도 앨범으로 나가므로 판형이 달라도 된다. */
  .card.st::after { display: none; }

  /* ── 스토리 카드뉴스(5장) ────────────────────────────────────────────────
     이슈 하나를 표지·사실·쟁점·의미·체크리스트로 푸는 판형. 일일 카드와 달리
     장마다 역할이 다르고, 재료는 chronicle(이벤트·서사·관전포인트)에서 온다.
     밝은 아이보리 바탕 + 네이비 잉크 + 블루 포인트(연두는 폐기 팔레트라 안 쓴다). */
  .card.st { padding: 0; display: flex; flex-direction: column;
    background: #F6F2E9; color: #12294C; }
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
  /* 세 칸이 남는 높이를 **채운다.** 예전에는 align-content:center 라 칸이 가운데
     떠서 제목 아래와 인용 위에 큰 빈 면이 남았다(09-26 4장). 칸 안에서 가운데 맞춘다. */
  .st-three { flex: 1; margin: 30px 0 24px; display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 20px; align-content: stretch; }
  .st-three .cell { background: #FFFDF7; border: 1px solid #E6E0D2; padding: 34px 22px;
    text-align: center; display: flex; flex-direction: column; justify-content: center; }
  .st-three .ic { width: 96px; height: 96px; margin: 0 auto 20px; border-radius: 50%;
    background: #E7EEF8; color: #1F5FA8; display: flex; align-items: center;
    justify-content: center; }
  .st-three .ic svg { width: 52px; height: 52px; fill: none; stroke: currentColor;
    stroke-width: 3.5; }
  .st-three h4 { font-size: 36px; font-weight: 850; word-break: keep-all; }
  .st-three p { margin-top: 16px; font-size: 30px; line-height: 1.44; font-weight: 620;
    color: #41506B; word-break: keep-all; }
  .st-quotes { margin-top: auto; display: flex; flex-direction: column; gap: 14px; }
  /* 인용 표지는 글자가 아니라 왼쪽 띠다. 예전에는 ::before 에 깨진 따옴표(U+0081
     제어문자 + "C")를 46px 로 찍었는데, 40px 칸보다 넓어 본문 첫 글자를 덮었다
     (09-26 4장, 폰트에 따라 "C" 나 빈 네모로 보였다). */
  .st-quote { background: #FFFDF7; border: 1px solid #E6E0D2; border-left: 8px solid #A9C4E6;
    padding: 20px 28px; font-size: 29px;
    line-height: 1.42; font-weight: 650; color: #35455F; word-break: keep-all; }

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
  .st-foot { margin-top: 26px; display: flex; align-items: center; gap: 24px; }
  .st-foot .st-cta { margin-top: 0; flex: 0 0 auto; }
  .st-url { min-width: 0; font-size: 22px; font-weight: 700; color: #5B6B84;
    overflow-wrap: anywhere; }
  /* 같은 스토리를 전에 카드로 낸 적이 있으면 표지에 '후속' 을 단다(card_context.since_last).
     칩·주제·후속 셋이 한 줄에 서므로 주제가 긴 날은 줄을 바꾼다 — 가로로 넘치면
     렌더 가드가 스토리를 통째로 죽인다. */
  .st-cover .st-row { flex-wrap: wrap; row-gap: 12px; }
  .st-follow { padding: 8px 18px; border-radius: 999px; border: 2px solid #1F5FA8;
    color: #1F5FA8; font-size: 22px; font-weight: 800; white-space: nowrap; }
</style></head><body>${inner}</body></html>`;
}



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
          <div class="st-row">${chip}${s.topic ? `<span class="st-desc" style="margin:0;font-size:24px;font-weight:700;color:#12294C">${esc(s.topic)}</span>` : ""}${s.followUp ? `<span class="st-follow">${esc(s.followUp)}</span>` : ""}</div>
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
        <div class="st-foot"><div class="st-cta">${esc(s.cta || "지금 이슈를 계속 업데이트합니다")} →</div>
          ${s.ctaUrl ? `<span class="st-url">${esc(s.ctaUrl)}</span>` : ""}</div>
      </div>
    </div>`, theme, false);
}

function renderSlide(s, theme) {
  let type = s.type || "step";
  if (type.startsWith("story-")) return renderStory(s, theme, type);
  if (type === "step" || type === "editorial") {
    // step 은 여기서 editorial 로 바뀌고, 이미 editorial 인 심층 카드는
    // 사진만 물려받는다(editorialFromStep 이 둘 다 처리한다).
    s = editorialFromStep(s);
    type = "editorial";
  }
  const site = esc(s.handle || "");
  const num = esc(s.slideNum || "");

  if (type === "hook") {
    return shell(
      `<div class="card cover has-photo">${s.image ? `<div class="card-photo ghost" style="background-image:url('${imageData(s.image)}')"></div><div class="card-scrim ghost"></div>` : ""}
        <div class="hd"><span class="brand">${esc(s.stepLabel || "NUCLENS")}</span><span>${num}</span></div>
        <div class="body">
          <div class="datehead">
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
      `<div class="card end has-photo">${s.image ? `<div class="card-photo ghost" style="background-image:url('${imageData(s.image)}')"></div><div class="card-scrim ghost"></div>` : ""}
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

  if (type === "status") {
    const rows = Array.isArray(s.statusRows) ? s.statusRows.slice(0, 3) : [];   // 심층 타임라인은 3줄
    const implications = Array.isArray(s.why) ? s.why.slice(0, 3) : [];
    return shell(
      `<div class="card status-card">
        <section class="status-hero">
          <div class="hd"><span class="brand">NUCLENS</span><span>${num}</span></div>
          <div class="status-eyebrow"><span class="serial">${esc(s.idx || "01")}</span><span>${esc(s.stepLabel || "")}</span></div>
          <h1>${accentize(s.headline, "em")}</h1>
        </section>
        <section class="status-content">
          <div class="status-overview">
            <div class="amount-block">
              <div class="micro-label">${esc(s.heroLabel || "핵심 수치")}</div>
              <div class="amount-line"><span class="value">${esc(s.heroStat || "")}</span><span class="unit">${esc(s.heroUnit || "")}</span></div>
              <div class="amount-caption">${esc(s.heroCaption || "")}</div>
            </div>
            <div class="deal-status">${rows.map((row) =>
              `<div class="deal-row"><div class="key">${esc(row.label)}</div><div class="copy">${esc(row.text)}</div></div>`
            ).join("")}</div>
          </div>
          <div class="implications">
            <div class="implication-head"><strong>${esc(s.whyLabel || "왜 중요한가")}</strong><span>WHAT TO WATCH</span></div>
            <div class="implication-grid">${implications.map((text, i) =>
              `<div class="implication"><span class="n">0${i + 1}</span><span>${esc(text)}</span></div>`
            ).join("")}</div>
          </div>
        </section>
        <footer class="status-footer"><span>${site}</span><span class="src">${esc(s.footer || "")}</span></footer>
      </div>`,
      theme,
      false
    );
  }

  if (type === "editorial") {
    const rows = Array.isArray(s.statusRows) ? s.statusRows.slice(0, 3) : [];   // 심층 타임라인은 3줄
    const checks = Array.isArray(s.whyChecks) ? s.whyChecks.slice(0, 2) : [];
    const photo = imageData(s.image);
    return shell(
      `<div class="card editorial-card">
        <div class="card-photo ghost" style="background-image:url('${photo}')"></div>
        <div class="card-scrim ghost"></div>
        <section class="editorial-hero">
          <div class="hd"><span class="brand">NUCLENS</span><span>${num}</span></div>
          <div class="editorial-copy">
            <div class="editorial-kicker"><strong>${esc(s.stepLabel || "")}</strong>${s.context ? ` &nbsp;|&nbsp; ${esc(s.context)}` : ""}</div>
            <h1 class="editorial-title">${accentize(autoAccent(s.headline), "em")}</h1>
            ${s.deck ? `<p class="editorial-deck">${esc(s.deck)}</p>` : ""}
          </div>
          <div class="editorial-stamp">${esc(s.stamp || "EDITORIAL BRIEF").replaceAll("\n", "<br>")}</div>
        </section>
        <section class="editorial-body">
          <div class="editorial-section-head">${esc(s.factsLabel || "확인된 사실")}</div>
          <div class="event-grid">${rows.map((row) =>
            `<div class="event-card"><div class="event-icon">${editorialIcon(topicIcon(s.stepLabel))}</div><div class="event-copy">${row.label ? `<strong>${esc(row.label)}</strong>` : ""}<span>${esc(row.text)}</span></div></div>`
          ).join("")}</div>
          ${(s.whyLead || checks.length) ? `<div class="why-editorial">
            <div class="editorial-section-head">${esc(s.whyLabel || "왜 중요한가")}</div>
            <div class="why-layout"><div class="why-lead">${esc(s.whyLead || "")}</div><div class="why-checks">${checks.map((text) => `<div class="why-check">${esc(text)}</div>`).join("")}</div></div>
          </div>` : ""}
        </section>
        <footer class="editorial-footer">
          <div class="editorial-cta"><span class="editorial-arrow">↗</span><span>더 자세한 원문 보기</span><span class="editorial-site">${site}</span></div>
          <div class="editorial-source">출처&nbsp; <strong>${esc(s.footer || "")}</strong><br>${esc(s.date || "")}</div>
        </footer>
      </div>`,
      theme,
      false
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
  assert.deepStrictEqual(parseArgs(["preview.json", "--out-dir", "previews/new"]), {
    input: "preview.json", outDir: "previews/new", sample: false, check: false,
  });
  assert.ok(editorialFromStep({ stepLabel: "사용후핵연료·방폐", headline: "저장시설 확충" }).image.includes("fuel-cycle-waste"));
  assert.ok(editorialFromStep({ stepLabel: "계속운전·재가동", headline: "심사 착수" }).image.includes("reactor-operations"));
  assert.ok(editorialFromStep({ stepLabel: "핵융합", headline: "실증 연구" }).image.includes("research-technology"));
  console.log("build.js self-check OK");
}

(async () => {
  const theme = loadTheme();
  const args = parseArgs(process.argv.slice(2));
  if (args.check) return selfCheck();

  let slides;
  if (args.sample) {
    slides = SAMPLE_SLIDES;
  } else {
    const inputPath = path.resolve(process.cwd(), args.input);
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

  const outDir = path.resolve(process.cwd(), args.outDir);
  fs.mkdirSync(outDir, { recursive: true });

  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  await page.setViewport({ width: theme.canvas.width, height: theme.canvas.height, deviceScaleFactor: 1 });

  // 표지·마지막 장은 그날 첫 카드의 사진을 물려받는다 — 앨범이 한 벌로 보인다.
  const heroImage = (slides.map(editorialFromStep).find((x) => x.image) || {}).image;
  for (let i = 0; i < slides.length; i++) {
    if (heroImage && (slides[i].type === "hook" || slides[i].type === "cta") && !slides[i].image) {
      slides[i] = { ...slides[i], image: heroImage };
    }
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

    // ── 밝은 사진 가드 ──────────────────────────────────────────────
    // B안(사진 전면)은 스크림 농도가 사진 밝기에 물려 있다. 지금 자산 6장은
    // 전부 야간·실내라 어둡지만, 밝은 사진(설경·흰 외벽·주간 하늘)이 들어오면
    // 본문 뒤가 밝아져 흰 글자가 깨진다. 고정값으로는 못 막으므로 **글자가 앉는
    // 아래 55% 구간의 평균 휘도를 실제로 재서** 임계를 넘으면 스크림을 올린다.
    // 임계 0.28: 현행 자산 실측 상단값이 0.19, 12:1 을 지키는 한계가 0.30 이다.
    await page.evaluate(async () => {
      const card = document.querySelector(".card");
      const photo = card && card.querySelector(".card-photo");
      if (!photo) return;
      const url = (photo.style.backgroundImage || "").slice(5, -2);
      if (!url) return;
      const img = new Image();
      await new Promise((ok) => { img.onload = ok; img.onerror = ok; img.src = url; });
      if (!img.naturalWidth) return;
      const cv = document.createElement("canvas");
      cv.width = 64; cv.height = 64;
      const ctx = cv.getContext("2d");
      // background-position: center 26% 와 같은 자리를 본다 — 위 26% 를 버리고 그린다.
      ctx.drawImage(img, 0, img.naturalHeight * 0.26, img.naturalWidth,
        img.naturalHeight * 0.74, 0, 0, 64, 64);
      const px = ctx.getImageData(0, 35, 64, 29).data;   // 아래 55% = 글자 구간
      let sum = 0;
      for (let p = 0; p < px.length; p += 4) {
        const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
        sum += 0.2126 * f(px[p]) + 0.7152 * f(px[p + 1]) + 0.0722 * f(px[p + 2]);
      }
      const mean = sum / (px.length / 4);
      card.dataset.photoLum = mean.toFixed(3);
      if (mean > 0.28) card.classList.add("bright-photo");
    });

    // ── 스토리 표지 넘침 ────────────────────────────────────────────
    // 덱이 긴 날이면 배지·출처·슬로건이 본문 칸 밖으로 밀려 렌더 가드가 카드를
    // 통째로 죽인다(09-19 재생성 실측: overflow=st-badge,st-credit,st-slogan).
    // 상한 안에 든 카피인데도 그렇다 — 길이는 글자 수로 재고 넘침은 픽셀로
    // 나기 때문이다. 일일 카드에 있는 안전망과 같은 것을 여기에도 둔다.
    await page.evaluate(() => {
      const body = document.querySelector(".st-cover .st-body");
      if (!body) return;
      const over = () => {
        const br = body.getBoundingClientRect();
        return [...body.children].some((el) => el.getBoundingClientRect().bottom > br.bottom - 1);
      };
      const shrink = (sel, floor) => {
        const el = body.querySelector(sel);
        for (let guard = 0; el && guard < 24 && over(); guard++) {
          const size = parseFloat(getComputedStyle(el).fontSize);
          if (size <= floor) break;
          el.style.fontSize = size - 1 + "px";
        }
      };
      shrink(".st-desc", 26);    // 폰 9.4px 까지. 덱은 부연이라 먼저 양보한다
      shrink(".st-title", 58);   // 표지 제목은 마지막에, 조금만
    });

    // ── 제목 2줄 ────────────────────────────────────────────────────
    // 3줄 제목은 히어로를 다 먹고도 정보가 안 늘어난다("테라파워·메타, 나트륨
    // 원전 8기 협력" — 지니 09-20). 글자를 줄여 2줄에 앉힌다. 강조 구간(.em)은
    // 자기 줄을 갖게 돼 있으므로 앞부분이 1줄이면 전체가 2줄이다.
    await page.evaluate(() => {
      const title = document.querySelector(".editorial-title");
      if (!title) return;
      const lead = title.firstChild && title.firstChild.nodeType === 3 ? title : null;
      const em = title.querySelector(".em");
      const lineCount = () => {
        const fs = parseFloat(getComputedStyle(title).fontSize);
        const lh = fs * 1.08;
        // .em 은 1.13em 이라 한 줄이 더 높다. 2줄 = 앞 1줄 + em 1줄.
        const budget = lh + (em ? fs * 1.13 * 1.08 : lh);
        return title.getBoundingClientRect().height > budget + 6 ? 3 : 2;
      };
      // 강조를 제 줄에 세우는 배치(블록)와 문장 안에 두는 배치(인라인)를 **둘 다
      // 재보고 크게 앉는 쪽**을 쓴다.
      //
      // 예전에는 블록으로 먼저 줄여 보고 그게 **실패할 때만** 인라인으로 내렸다.
      // 그러면 블록이 작은 크기에서 우연히 두 줄에 들어맞을 때 거기서 멈춘다 —
      // 더 큰 인라인 배치는 시도조차 안 된다. 칸이 넓어질수록 제목이 작아지는
      // 구간이 그래서 생겼다(실측 09-20 `미 에너지부 … 긴급명령`):
      //
      //     칸 612px   블록 52px(3줄, 실패) → 인라인 70px
      //     칸 649px   블록 52px(2줄, 성공) → **거기서 멈춰 52px**
      //     칸 649px   인라인이라면 72px 이었다
      //
      // 먼저 맞는 배치가 아니라 크게 앉는 배치를 고른다.
      const fit = (inline) => {
        if (em) em.classList.toggle("inline", inline);
        title.style.fontSize = "";
        let value = parseFloat(getComputedStyle(title).fontSize);
        for (let guard = 0; guard < 24 && lineCount() > 2 && value > 52; guard++) {
          value -= 2;
          title.style.fontSize = value + "px";
        }
        return { fs: value, lines: lineCount(),
                 h: title.getBoundingClientRect().height };
      };
      const asBlock = fit(false);
      const asInline = em ? fit(true) : asBlock;
      // 같은 크기면 블록이 이긴다 — 강조를 제 줄에 세우는 것이 원래 구성이고,
      // 크기가 같다면 양보할 이유가 없다. 인라인은 **더 크게 앉을 때**, 그리고
      // 크기가 같은데 **블록이 줄 수를 못 지킬 때**만 이긴다. 뒤쪽이 예전 폴백이
      // 보던 경우다(52px 3줄보다 52px 2줄이 읽힌다). 둘 다 못 지키면 그중
      // 낮은 쪽을 쓴다 — 하한까지 줄인 제목은 더 넘칠수록 본문을 밀어낸다.
      const useInline = Boolean(em) && (
        asInline.fs > asBlock.fs
        || (asInline.fs === asBlock.fs && asBlock.lines > 2
            && (asInline.lines <= 2 || asInline.h < asBlock.h)));
      const chosen = useInline ? asInline : asBlock;
      if (em) em.classList.toggle("inline", useInline);
      title.style.fontSize = chosen.fs + "px";
      const fs = chosen.fs;
      void lead;
      title.dataset.finalFs = String(fs);   // 진단용 — 몇 px 로 앉았는지
    });
    if (process.env.CARD_DEBUG) {
      const fsInfo = await page.evaluate(() => {
        const t = document.querySelector(".editorial-title");
        const c = document.querySelector(".card");
        return t ? { fs: t.dataset.finalFs, h: Math.round(t.getBoundingClientRect().height), lum: c && c.dataset.photoLum, bright: c && c.classList.contains("bright-photo") } : null;
      });
      if (fsInfo) console.log("[title]", JSON.stringify(fsInfo));
    }

    // 에디토리얼 카드의 넘침 안전망. 아래 구 카드용 축소기는 `.card > .body` 를
    // 찾는데 이쪽 구조는 `.editorial-body` 라 **한 번도 걸린 적이 없었다**(가드가
    // 그냥 throw 했다). 재료가 긴 날에도 카드를 살리려면 여기서 줄여야 한다.
    // 하한은 폰 실효 12px(34px)·11px(30px) — 그 아래로는 줄이느니 안 만든다.
    await page.evaluate(() => {
      const body = document.querySelector(".editorial-body");
      if (!body) return;
      const over = () => {
        const br = body.getBoundingClientRect();
        return [...body.children].some((el) => el.getBoundingClientRect().bottom > br.bottom - 2);
      };
      const shrink = (sel, floor) => {
        const nodes = [...body.querySelectorAll(sel)];
        for (let guard = 0; guard < 30 && over(); guard++) {
          let moved = false;
          for (const el of nodes) {
            const size = parseFloat(getComputedStyle(el).fontSize);
            if (size > floor) { el.style.fontSize = size - 1 + "px"; moved = true; }
          }
          if (!moved) break;
        }
      };
      shrink(".why-check", 30);
      shrink(".event-copy span", 34);
      shrink(".why-lead", 36);
      // 하한까지 줄였는데도 넘치면 **줄을 버린다**. 폰에서 못 읽을 크기로
      // 밀어 넣느니 확인점 한 줄을 접는 쪽이 낫다(같은 판단을 마지막 장에서도 썼다).
      const checks = [...body.querySelectorAll(".why-check")];
      while (over() && checks.length > 1) checks.pop().remove();
      // 그래도 넘치면 사실 줄을 하나 접는다 — 여기까지 오는 날은 재료가 비정상이다.
      const rows = [...body.querySelectorAll(".event-card")];
      while (over() && rows.length > 1) rows.pop().remove();

      // 꼬리 한 낱말만 다음 줄에 남는 것을 막는다. 줄이 넘치지 않아도 보기
      // 나쁘다 — "…전환 로드맵 / 추진" 처럼 한 낱말이 한 줄을 차지한다.
      // inline 요소의 getClientRects() 가 줄마다 사각형을 준다는 점을 쓴다.
      // 하한은 34px(폰 12.3px) — 그 아래로 줄이느니 두 줄로 둔다. 본문 기본값이
      // 38px 이라 하한을 같은 값으로 두면 가드가 한 번도 못 움직인다.
      for (const span of body.querySelectorAll(".event-copy span")) {
        const width = span.parentElement.getBoundingClientRect().width;
        for (let guard = 0; guard < 6; guard++) {
          const rects = [...span.getClientRects()];
          const tail = rects[rects.length - 1];
          if (rects.length < 2 || !tail || tail.width > width * 0.28) break;
          const size = parseFloat(getComputedStyle(span).fontSize);
          if (size <= 34) break;
          span.style.fontSize = size - 2 + "px";
        }
      }
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

    // 말줄임은 **죽이지 않고 알린다.** 카피가 잘려 "…" 로 끝났거나 칸이 글자를
    // 가로로 삼켰으면 Actions 에 경고로 남긴다 — 09-26 에는 거의 모든 장이 "…" 로
    // 나갔는데 로그 어디에도 흔적이 없어 사이트를 열어 보고서야 알았다.
    const clipped = await page.evaluate(() => {
      const card = document.querySelector(".card");
      if (!card) return [];
      const found = [];
      for (const el of card.querySelectorAll("*")) {
        const own = [...el.childNodes].filter((n) => n.nodeType === 3)
          .map((n) => n.textContent).join("").trim();
        if (!own) continue;
        if (/…$/.test(own)) found.push(`말줄임 "${own.slice(-24)}"`);
        else if (getComputedStyle(el).overflow === "hidden" && el.scrollWidth > el.clientWidth + 2) {
          found.push(`가로 잘림 "${own.slice(0, 24)}"`);
        }
      }
      return found;
    });
    for (const line of clipped) {
      console.log(`::warning title=카드 말줄임::slide ${i + 1} (${slides[i].type || "step"}) ${line}`);
    }

    const n = String(i + 1).padStart(2, "0");
    const out = path.join(outDir, `slide-${n}.png`);
    await page.screenshot({ path: out, type: "png" });
    console.log("rendered", out);
  }

  await browser.close();
  console.log(`\nDone. ${slides.length} slides in ${outDir}`);
})();
