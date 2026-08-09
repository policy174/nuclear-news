"use strict";

const TOPIC_LABELS = {
  smr: "SMR", newbuild: "신규 건설", restart_lto: "계속운전·재가동",
  fuel_cycle: "핵연료주기", waste: "사용후핵연료·방폐", finance: "원전금융·투자",
  regulation: "규제·인허가", power_market: "전력시장·요금", datacenter_ai: "데이터센터·AI 전력",
  fusion: "핵융합", security_trade: "에너지안보·통상", fukushima: "후쿠시마·처리수",
  operations: "원전 운영", safety: "안전·사건", decommissioning: "해체·폐로",
  workforce: "산업 인력", policy_general: "원자력 정책", research: "연구·기술",
  applications: "비발전 활용",
};

const COUNTRY_LABELS = {
  KR: "한국", US: "미국", CA: "캐나다", FR: "프랑스", GB: "영국",
  DE: "독일", ES: "스페인", RS: "세르비아", HU: "헝가리", RO: "루마니아",
  CZ: "체코", PL: "폴란드", SE: "스웨덴", NL: "네덜란드", FI: "핀란드",
  SK: "슬로바키아", BG: "불가리아", UA: "우크라이나", BE: "벨기에",
  IT: "이탈리아", PT: "포르투갈", CH: "스위스", NO: "노르웨이",
  DK: "덴마크", JP: "일본", RU: "러시아", CN: "중국", AR: "아르헨티나",
  IN: "인도", AU: "호주", BR: "브라질", ZA: "남아공", SA: "사우디아라비아",
  AE: "아랍에미리트", TR: "튀르키예", KZ: "카자흐스탄", UZ: "우즈베키스탄",
  EU: "EU(유럽연합)", EUROPE: "유럽", GLOBAL: "글로벌", UNSPECIFIED: "미분류",
};

// 타일 그리드 지도의 칸 좌표 [col, row], 18×9. 실제 투영이 아니라 대륙 덩어리만
// 맞춘 근사 배치다 — 국경 SVG 를 들이지 않고도 '어디서'를 한눈에 주는 게 목적이고,
// 칸이 전부 같은 크기라 작은 나라도 안 사라진다.
// COUNTRY_LABELS 의 EU·EUROPE·GLOBAL·UNSPECIFIED 는 지리 좌표가 없어 여기 없다.
// 여기 없는 코드는 지도에서 빠지고, 빠진 건수는 지도 아래에 적힌다.
const COUNTRY_GRID = {
  CA: [1, 0], US: [1, 2], MX: [1, 4], BR: [3, 6], AR: [2, 8],
  GB: [5, 1], NO: [6, 0], SE: [7, 0], FI: [8, 0], DK: [6, 1],
  BE: [5, 2], NL: [6, 2], DE: [7, 2], CZ: [8, 2], PL: [9, 1],
  FR: [5, 3], CH: [6, 3], AT: [7, 3], SK: [8, 3], HU: [9, 3],
  PT: [4, 4], ES: [5, 4], IT: [7, 4], RS: [8, 4], RO: [10, 4],
  UA: [10, 2], BG: [9, 5], TR: [10, 5], RU: [12, 0], KZ: [12, 2],
  UZ: [12, 3], SA: [11, 6], AE: [12, 6], IN: [13, 5], CN: [14, 3],
  KR: [16, 3], JP: [17, 2], AU: [16, 8], ZA: [7, 8],
};
const COUNTRY_MAP_COLS = 18;
const COUNTRY_MAP_ROWS = 9;
// 대륙 묶음. 지도가 막대와 다른 말을 하는 지점이 여기다 — 막대는 건수순이라
// 유럽 국가들이 4~5건씩 흩어져 캐나다 6건보다 아래에 깔리지만 합치면 3배다.
// 러시아는 묶음 이름에 같이 적는다: 원자력 보도에서 유럽으로 뭉뚱그리면
// 로사톰 몫이 유럽 정책 흐름으로 오독된다.
const COUNTRY_REGION = {
  US: "북미", CA: "북미", MX: "북미",
  BR: "남미", AR: "남미",
  GB: "유럽·러시아", FR: "유럽·러시아", DE: "유럽·러시아", IT: "유럽·러시아",
  ES: "유럽·러시아", PT: "유럽·러시아", NL: "유럽·러시아", BE: "유럽·러시아",
  CH: "유럽·러시아", AT: "유럽·러시아", NO: "유럽·러시아", SE: "유럽·러시아",
  DK: "유럽·러시아", FI: "유럽·러시아", PL: "유럽·러시아", CZ: "유럽·러시아",
  SK: "유럽·러시아", HU: "유럽·러시아", RO: "유럽·러시아", BG: "유럽·러시아",
  RS: "유럽·러시아", UA: "유럽·러시아", RU: "유럽·러시아",
  KR: "아시아", JP: "아시아", CN: "아시아", IN: "아시아",
  SA: "중동", AE: "중동", TR: "중동",
  ZA: "아프리카",
  AU: "오세아니아",
  KZ: "중앙아시아", UZ: "중앙아시아",
};
// 표시 순서 고정. 건수순으로 정렬하면 '이번 달 0건'인 대륙이 목록 끝으로
// 밀리거나 사라져서, 지도가 가진 유일한 특기(부재를 보여주는 것)를 잃는다.
const COUNTRY_REGION_ORDER = ["북미", "아시아", "유럽·러시아", "중동", "남미", "아프리카", "중앙아시아", "오세아니아"];
// 지도 위 대륙 라벨. 등면적 타일은 정확한 대신 한눈에 세계지도로 안 읽힌다 —
// "이게 뭐지"가 먼저 오면 인코딩이 맞아도 소용없어서 덩어리마다 이름을 붙인다.
// 좌표는 COUNTRY_GRID 에 **국가가 배정된 적 없는 칸**만 고른다. 그래야 어느 날
// 브라질이나 사우디가 켜져도 라벨과 타일이 겹칠 수 없다(빈 칸을 골랐다가
// 데이터가 바뀌면 겹치는 게 이런 오버레이의 흔한 실패다).
// 이름은 옆 '대륙별 합계' 목록과 글자까지 같게 둔다 — 눈으로 이어져야 한다.
const COUNTRY_MAP_LABELS = [
  { text: "북미", col: 2.8, row: 3.0 },
  { text: "유럽·러시아", col: 4.0, row: 0.2 },
  { text: "아시아", col: 15.5, row: 4.2 },
];

const OFFICIAL_HINTS = ["go.kr", "khnp", "kaeri", "iaea.org", "energy.gov", "nrc.gov"];
const VIEW_IDS = ["news", "trend", "search", "report"];
const ISSUE_ROUTE = /^\/issue\/([^/]+)\/?$/;
const BRIEF_ROUTE = /^\/brief\/(\d{4}-\d{2}-\d{2})\/?$/;

const ENTITY_TYPE_LABELS = { plant: "원전", company: "기업", org: "기관", project: "프로젝트" };
// 리디자인에서 새로 들어오는 문구는 여기로 모은다 — 화면 하드코딩 786건(S3 부채)을
// 더 키우지 않기 위한 봉쇄선. 기존 문구는 옮기지 않는다(그건 S3 의 일).
const STRINGS = {
  entityUnknown: "등록되지 않은 대상입니다",
  entityClear: "탐색으로 돌아가기",
  recentCapture: "최근 포착",
  hubEmptyEntities: "아직 연결된 대상이 없습니다 — 데이터가 쌓이면 채워집니다.",
};

const state = {
  news: [], briefings: [], issues: [], trend: null, insights: null, meta: null,
  pubs: null, pubsOrg: "전체",
  manifest: null, systemStatus: null, dataBase: "/data",
  briefingDate: "", region: "전체", topic: "전체", view: "news",
  issueSort: "importance", issueView: "card", issueId: "", railIssueId: "",
  archiveQuery: "", archiveRegion: "전체", archiveTopic: "전체",
  archivePeriod: "all", archiveVerification: "전체", archiveSort: "updated", archiveLimit: 20,
  archiveEntity: "", entities: null,
  period: "7", keywordSort: "mentions", savedIds: new Set(), savedMeta: {}, follows: new Set(), followSeen: {},
  offline: !navigator.onLine, pendingGeneration: "",
};

let eventsBound = false;
let appReady = false;
let initLoading = false;
let initRetryTimer = 0;
let initRetryCount = 0;
let generationTimer = 0;
let issueHistoryOwned = false;
let toastTimer = 0;
const briefRouteOwned = BRIEF_ROUTE.test(location.pathname);

function issueIdFromLocation() {
  const match = location.pathname.match(ISSUE_ROUTE);
  if (!match) return "";
  try { return decodeURIComponent(match[1]); } catch { return ""; }
}

function issuePath(issueId) {
  return `/issue/${encodeURIComponent(issueId)}`;
}

function briefDateFromLocation() {
  return location.pathname.match(BRIEF_ROUTE)?.[1] || "";
}

function briefPath(briefingDate) {
  return `/brief/${encodeURIComponent(briefingDate)}`;
}

async function loadJSON(name) {
  const response = await fetch(`${state.dataBase}/${name}`, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${name} ${response.status}`);
  const ctype = response.headers.get("content-type") || "";
  if (!ctype.includes("json")) throw new Error(`${name} 응답이 JSON이 아님`);
  return response.json();
}

async function loadRootJSON(name, optional = false) {
  const response = await fetch(`/data/${name}`, { cache: "no-cache" });
  if (!response.ok) {
    if (optional) return null;
    throw new Error(`${name} ${response.status}`);
  }
  try { return await response.json(); } catch (error) {
    if (optional) return null;
    throw error;
  }
}

async function initializeDataBase() {
  if (initRetryCount > 0) {
    state.manifest = null;
    state.dataBase = "/data";
    state.systemStatus = await loadRootJSON("status.json", true);
    return;
  }
  const manifest = await loadRootJSON("manifest.json", true);
  const basePath = String(manifest?.base_path || "");
  if (manifest && /^generations\/[0-9A-Za-z-]+$/.test(basePath)) {
    state.manifest = manifest;
    state.dataBase = `/data/${basePath}`;
  } else {
    state.manifest = manifest?.generation_id ? manifest : null;
    state.dataBase = "/data";
  }
  state.systemStatus = await loadRootJSON("status.json", true);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

// CSS의 prefers-reduced-motion 전역 오버라이드는 JS 주도 스크롤·모션에는
// 적용되지 않는다 — JS 쪽 모션은 전부 이 헬퍼를 거친다.
function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function normalizedSearch(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function dateLabel(value) {
  if (!value) return "-";
  const [, month, day] = value.split("-");
  return `${Number(month)}월 ${Number(day)}일`;
}

function dateWeekdayLabel(value) {
  if (!value) return "-";
  const parsed = new Date(`${value}T00:00:00+09:00`);
  const weekday = parsed.toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul", weekday: "short" });
  return `${dateLabel(value)} (${weekday})`;
}

function dateTimeLabel(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return parsed.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function timeLabel(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).slice(11, 16);
  return parsed.toLocaleTimeString("ko-KR", {
    timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function relativeArticleDate(articleDate, briefingDate) {
  const article = new Date(`${articleDate}T00:00:00+09:00`);
  const briefing = new Date(`${briefingDate}T00:00:00+09:00`);
  const days = Math.round((briefing - article) / 86400000);
  if (days === 0) return "당일 보도";
  if (days === 1) return "전날 보도";
  if (days > 1) return `${days}일 전 보도`;
  return dateLabel(articleDate);
}

function sourceLabel(article) {
  return article.publisher || article.domain || "출처 미상";
}

function isOfficial(article) {
  const domain = String(article.domain || "").toLowerCase();
  return article.evidence_role === "primary"
    || article.source_tier === 1
    || article.article_type === "official_doc"
    || OFFICIAL_HINTS.some(hint => domain.includes(hint));
}

function officialSourceCount(issue) {
  return (issue.related_articles || []).filter(isOfficial).length;
}

function primaryTopicLabel(issue) {
  const topic = (issue.topics || [])[0];
  return topic ? TOPIC_LABELS[topic] || topic : "";
}

function briefingDates() {
  return state.briefings.map(briefing => briefing.date);
}

function currentBriefing() {
  return state.briefings.find(briefing => briefing.date === state.briefingDate) || null;
}

const VERIFICATION_ORDER = ["official", "corroborated", "partial", "unverified"];
const VERIFICATION_VIEW = {
  official: { mark: "✓", label: "공식 확인", detail: "규제기관 또는 사업자 공식 문서로 확인된 내용입니다" },
  corroborated: { mark: "✓", label: "복수 출처 확인", detail: "재인용 관계를 제외한 독립 출처 2곳 이상이 일치합니다" },
  partial: { mark: "·", label: "단일 출처", detail: "독립 출처 1곳이 보도했습니다" },
  unverified: { mark: "○", label: "확인 중", detail: "아직 독립·공식 근거가 확인되지 않았습니다" },
};
// 배지는 예외를 표시할 때만 정보가 된다. 단일 출처는 전체의 대다수라(실측 84%)
// 배지로 달면 신호가 죽고 사이트 전체가 미심쩍어 보인다. 근거 줄의 '독립 출처
// 1곳' 표기가 같은 사실을 이미 전달한다.
const BADGE_STATUSES = new Set(["official", "corroborated", "unverified"]);

// 검증 상태는 빌드가 판정한다. 값이 없는 구버전 데이터에서는 문장을 지어내지 않고
// 공식 출처 유무만으로 보수적으로 폴백한다.
function verificationState(issue) {
  const state = issue.verification;
  if (state && VERIFICATION_VIEW[state.status]) return state;
  const official = officialSourceCount(issue);
  return {
    status: official > 0 ? "official" : "unverified",
    source_count: (issue.related_articles || []).length,
    independent_source_count: 0,
    official_source_count: official,
    checked_at: "",
  };
}

function issueToneClass(issue) {
  const classes = [];
  if (issue.lifecycle === "quiet") classes.push("state-quiet");
  if (verificationState(issue).status === "unverified") classes.push("state-unverified");
  if (issue.importance === "must_read") classes.push("importance-high");
  else if (issue.status === "ongoing" || (issue.tracked_briefings || issue.briefing_count || 1) > 1) classes.push("importance-updated");
  else classes.push("importance-standard");
  return classes.join(" ");
}

function verificationBadge(issue, { always = false } = {}) {
  const state = verificationState(issue);
  if (!always && !BADGE_STATUSES.has(state.status)) return "";
  const view = VERIFICATION_VIEW[state.status] || VERIFICATION_VIEW.unverified;
  return `<span class="verification-badge v-${esc(state.status)}" title="${esc(view.detail)}">${view.mark} ${esc(view.label)}</span>`;
}

// 부서 보고서로 다룰 만하다고 판정된 이슈에 붙는 표식. 판정은 화면이 아니라
// 발송 파이프라인이 한다(daily_brief.build_report_recs) — 여기서는 그 결과만 옮긴다.
//
// 라벨 하나로 끝낸다. 텔레그램은 '왜'와 '추천 각도'까지 펼치지만 그건 개인
// 브리핑의 판단이고, 웹은 동료가 함께 보는 화면이다. 무엇이 후보인지는 공유할
// 수 있어도 어떻게 쓰라는 조언까지 화면이 대신 말할 자리는 아니다.
function reportPickBadge(issue) {
  const topic = (issue.report_pick || "").trim();
  if (!topic) return "";
  return `<span class="report-pick-badge" title="${esc(topic)}">📝 보고서 검토 추천</span>`;
}

function issueEvidenceText(issue) {
  const state = verificationState(issue);
  const articleCount = issue.article_count || (issue.related_articles || []).length;
  const parts = [`근거 ${articleCount}건`];
  if (state.independent_source_count > 0) parts.push(`독립 출처 ${state.independent_source_count}곳`);
  if (state.official_source_count > 0) parts.push(`공식 출처 ${state.official_source_count}건`);
  // 확인 시각은 빌드 시각이라 모든 카드가 같은 값이다. 상단 상태줄이 이미
  // 같은 정보를 보여주므로 여기서는 빼고 출처 구성만 남긴다.
  return parts.join(" · ");
}

// 요약을 그대로 되풀이하는 변화 문장은 빌드가 비운다. 빈 값이면 블록을 그리지
// 않는다 — 요약이 이미 같은 사실을 말하고 있으므로 '없다'는 안내도 붙이지 않는다.
// change_display 는 화살표 문장의 뒤쪽(=현재 요약 재진술)을 걷어낸 표시 전용
// 필드다. 필드 자체가 없으면(구세대 데이터) latest_change 로 물러난다 —
// undefined 와 "" 를 구분해야 "의도적으로 비움"이 폴백으로 되살아나지 않는다.
function issueChangeText(issue) {
  if (issue.change_display !== undefined) return issue.change_display || "";
  return issue.latest_change || "";
}

// 근거 패널과 이슈 다이얼로그가 같은 내용을 보이게 하는 단일 조립 지점.
// 두 화면을 따로 만들면 금방 갈라진다 — 컨테이너만 다르고 데이터는 여기서만 만든다.
//
// 라벨은 값에 따라 바뀐다. 고정 라벨을 쓰면 데이터가 뒷받침하지 못하는 주장을
// 하게 된다: 공식 출처가 없는데 "공식 출처"라 부르거나, 기사 1건짜리에
// "관련 보도"를 켜서 교차 확인된 것처럼 보이게 만든다.
function issueDetailModel(issue, contextDate) {
  const verification = verificationState(issue);
  const articles = [...(issue.related_articles || [])].sort((a, b) => (
    Number(isOfficial(b)) - Number(isOfficial(a)) || String(b.article_date).localeCompare(String(a.article_date))
  ));
  const officialArticles = articles.filter(isOfficial);
  const articleCount = issue.article_count || articles.length;
  const changeText = issueChangeText(issue);
  return {
    issue,
    articles,
    verification,
    evidenceText: issueEvidenceText(issue),
    // 라벨만 바꾸면 안 된다 — 공식이라 부르면 실제 공식 문서를 가리켜야 한다.
    source: officialArticles.length
      ? { label: "공식 출처", official: true, article: officialArticles[0] }
      : { label: "대표 출처", official: false, article: issue.representative_article || articles[0] || null },
    // 1건짜리는 노드를 아예 숨긴다. 켜두면 여러 출처가 교차 확인됐다는 오해를 만든다.
    media: articleCount >= 2 ? { label: `관련 보도 ${articleCount}건`, count: articleCount } : null,
    // implication(시사점)과 why_important(왜 중요한가)는 다른 축이고, 이제 각자
    // 선다. 예전에는 둘 중 하나를 골라 '산업 영향'이라는 제3의 이름으로 내보냈다 —
    // 텔레그램이 같은 문장을 '시사점'이라 부르는데 웹만 다른 이름이었고, 그 라벨이
    // 서비스의 정체성을 지웠다(docs/2026-08-04-gap-review.md).
    // 겹치는 날 한 줄을 비우는 판단은 빌드(split_interpretation)가 이미 했다.
    why: issue.why_important ? { label: "왜 중요한가", text: issue.why_important } : null,
    impact: issue.implication ? { label: "시사점", text: issue.implication } : null,
    // latest_change 는 최신 기사와 과거 기사의 요약을 즉석 비교해 만든다 — 그 변화가
    // '오늘' 생겼다는 보장이 없다. 근거일을 확인할 수 없으면 '최근'으로 둔다.
    change: changeText
      ? { label: contextDate && issue.last_seen === contextDate ? "오늘의 변화" : "최근 변화", text: changeText }
      : null,
    openQuestion: (issue.open_question || "").trim() || null,
  };
}

function setPressed(container, activeButton) {
  if (!container || !activeButton) return;
  container.querySelectorAll("button").forEach(button => {
    const active = button === activeButton;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function markMatch(value, query) {
  const text = String(value || "");
  const needle = String(query || "").trim();
  if (!needle) return esc(text);
  const lower = text.toLowerCase();
  const target = needle.toLowerCase();
  let cursor = 0;
  let output = "";
  let index = lower.indexOf(target);
  while (index >= 0) {
    output += esc(text.slice(cursor, index));
    output += `<mark>${esc(text.slice(index, index + needle.length))}</mark>`;
    cursor = index + needle.length;
    index = lower.indexOf(target, cursor);
  }
  return output + esc(text.slice(cursor));
}

function scrollToPageTop() {
  const root = document.documentElement;
  const previousBehavior = root.style.scrollBehavior;
  root.style.scrollBehavior = "auto";
  window.scrollTo(0, 0);
  root.style.scrollBehavior = previousBehavior;
}

function showToast(message, actionLabel = "", action = null) {
  const toast = document.getElementById("toast");
  window.clearTimeout(toastTimer);
  toast.innerHTML = `<span>${esc(message)}</span>${actionLabel ? `<button type="button">${esc(actionLabel)}</button>` : ""}`;
  toast.hidden = false;
  const button = toast.querySelector("button");
  if (button && action) button.addEventListener("click", () => { action(); toast.hidden = true; }, { once: true });
  toastTimer = window.setTimeout(() => { toast.hidden = true; }, 4000);
  toast.addEventListener("mouseenter", () => window.clearTimeout(toastTimer), { once: true });
}

function loadSaved() {
  try {
    state.savedIds = new Set(JSON.parse(localStorage.getItem("nuclens-saved-issues") || "[]"));
  } catch {
    state.savedIds = new Set();
  }
  // 저장 시점 스냅샷(제목·날짜) — issue_id 는 클러스터 재계산에서 깨질 수 있는
  // 파생 키다(알려진 결함). 스냅샷이 있으면 깨진 저장을 톰스톤으로 보여주고
  // 제목 검색으로 다시 찾게 한다 — 조용한 소실 대신 비파괴 안내.
  try {
    const raw = JSON.parse(localStorage.getItem("nuclens-saved-meta") || "{}");
    state.savedMeta = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  } catch {
    state.savedMeta = {};
  }
  renderSavedCount();
}

function persistSaved() {
  try {
    localStorage.setItem("nuclens-saved-issues", JSON.stringify([...state.savedIds]));
    const meta = {};
    state.savedIds.forEach(id => {
      const issue = state.issues.find(item => item.issue_id === id);
      meta[id] = issue
        ? { title: issue.title || "", last_seen: issue.last_seen || "" }
        : state.savedMeta?.[id] || { title: "", last_seen: "" };
    });
    state.savedMeta = meta;
    localStorage.setItem("nuclens-saved-meta", JSON.stringify(meta));
  } catch { /* 저장 실패가 화면을 죽이면 안 된다 */ }
  renderSavedCount();
}

function renderSavedCount() {
  // 데스크톱 탭·모바일 탭 배지를 함께 갱신한다. 0이면 데스크톱 배지는 숨긴다
  // (숫자 0 배지는 정보가 아니라 소음이다 — 모바일 탭은 자리 유지를 위해 남긴다).
  document.querySelectorAll("[data-saved-count]").forEach(badge => {
    badge.textContent = String(state.savedIds.size);
    if (badge.dataset.savedCount === "desktop") badge.hidden = state.savedIds.size === 0;
  });
}

/* ── 엔티티 팔로우 ──────────────────────────────────────────────────
   이번 범위의 팔로우는 **엔티티 한정**이다(주제·국가는 필터로 충분 — 후속).
   확인 시각은 엔티티별 개별 저장(nuclens-follow-seen) — 단일 last-visit 는
   저장 화면에 들어오기만 해도 모든 배지가 꺼지는 구조라 쓰지 않는다.
   갱신 시점: ①해당 엔티티 페이지를 실제로 열었을 때 ②팔로우 시작 시(보고
   있는 화면이 곧 그 페이지다). 저장 화면 진입은 갱신하지 않는다. */
function loadFollows() {
  try {
    const raw = JSON.parse(localStorage.getItem("nuclens-follows") || "[]");
    state.follows = new Set(Array.isArray(raw) ? raw.filter(id => typeof id === "string") : []);
  } catch {
    state.follows = new Set();
  }
  try {
    const seen = JSON.parse(localStorage.getItem("nuclens-follow-seen") || "{}");
    state.followSeen = seen && typeof seen === "object" && !Array.isArray(seen) ? seen : {};
  } catch {
    state.followSeen = {};
  }
}

function persistFollows() {
  try {
    localStorage.setItem("nuclens-follows", JSON.stringify([...state.follows]));
    localStorage.setItem("nuclens-follow-seen", JSON.stringify(state.followSeen));
  } catch { /* 저장 실패가 화면을 죽이면 안 된다 */ }
}

function markEntitySeen(entityId) {
  const stamp = state.meta?.latest_briefing_date || "";
  if (!entityId || !stamp) return;
  if (state.followSeen[entityId] === stamp) return;
  state.followSeen[entityId] = stamp;
  persistFollows();
}

function toggleFollow(entityId) {
  if (!entityId) return;
  if (state.follows.has(entityId)) {
    state.follows.delete(entityId);
    delete state.followSeen[entityId];
    showToast("팔로우를 해제했습니다");
  } else {
    state.follows.add(entityId);
    // 시작 시점 = 확인 시점 — 지금 보고 있는 것까지가 '본 것'이고,
    // 배지는 이후 도착분만 센다.
    state.followSeen[entityId] = state.meta?.latest_briefing_date || "";
    showToast("대상을 팔로우합니다 — 새 이슈가 저장 탭에 표시됩니다");
  }
  persistFollows();
  renderEntityHeader();
  if (state.view === "saved") renderSaved();
}

function entityNewIssueCount(entityId) {
  const seen = state.followSeen?.[entityId] || "";
  return state.issues.filter(issue =>
    (issue.entity_ids || []).includes(entityId)
    && issue.last_seen && issue.last_seen > seen).length;
}

function renderFollowPanel() {
  const panel = document.getElementById("followPanel");
  if (!panel) return;
  const followed = [...state.follows]
    .map(id => entityById(id))
    .filter(Boolean)
    .sort((a, b) => entityNewIssueCount(b.id) - entityNewIssueCount(a.id)
      || String(b.latest_issue_date).localeCompare(String(a.latest_issue_date)));
  // 머리는 두 상태 모두에서 선다. 빈 상태일 때만 생략하면 이 화면의 구역
  // 번호가 01 없이 02 부터 시작해 빠진 칸처럼 읽힌다(실측).
  const heading = `<div class="section-heading compact"><div class="section-title"><span class="sec-no" aria-hidden="true">01</span><h2>팔로우한 대상</h2></div></div>`;
  if (!followed.length) {
    panel.innerHTML = heading + `<p class="follow-empty">탐색에서 원전·기업·기관을 팔로우하면 새 이슈를 여기서 셉니다.
      <button type="button" data-go-view="search">탐색 열기</button></p>`;
    return;
  }
  panel.innerHTML = heading
    + followed.map(entity => {
      const fresh = entityNewIssueCount(entity.id);
      return `<div class="follow-row">
        <button type="button" class="follow-open" data-follow-open="${esc(entity.id)}">
          <small>${esc(ENTITY_TYPE_LABELS[entity.type] || "")}</small>
          <strong>${esc(entity.name_kr)}</strong>
          ${fresh ? `<span class="follow-fresh">새 이슈 ${fresh}</span>` : `<span class="follow-quiet">새 이슈 없음</span>`}
        </button>
        <button type="button" class="text-action" data-unfollow="${esc(entity.id)}" aria-label="${esc(entity.name_kr)} 팔로우 해제">해제</button>
      </div>`;
    }).join("");
}

/* ── 최근 본 이슈 ──────────────────────────────────────────────────
   상세(다이얼로그·근거 패널)를 연 이슈의 흔적, MRU 8. 저장과 다른 축이다 —
   저장은 의도, 이건 발자취. 그래서 재클러스터로 사라진 id 는 톰스톤 없이
   조용히 떨어진다(발자취는 복구 대상이 아니다). */
function loadRecentIssues() {
  try {
    const raw = JSON.parse(localStorage.getItem("nuclens-recent-issues") || "[]");
    return Array.isArray(raw) ? raw.filter(id => typeof id === "string").slice(0, 8) : [];
  } catch { return []; }
}

function recordRecentIssue(issueId) {
  if (!issueId) return;
  const rest = loadRecentIssues().filter(id => id !== issueId);
  try { localStorage.setItem("nuclens-recent-issues", JSON.stringify([issueId, ...rest].slice(0, 8))); }
  catch { /* 저장 실패가 화면을 죽이면 안 된다 */ }
}

function renderRecentIssues() {
  const panel = document.getElementById("recentPanel");
  if (!panel) return;
  const rows = loadRecentIssues()
    .map(id => state.issues.find(issue => issue.issue_id === id))
    .filter(Boolean);
  panel.hidden = rows.length === 0;
  document.getElementById("recentIssueList").innerHTML = rows.map(issue => `<li>
    <button type="button" class="recent-open" data-issue-id="${esc(issue.issue_id)}">
      <strong>${esc(issue.title)}</strong>
      <small>${esc(dateLabel(issue.last_seen))} · 근거 ${issue.article_count || 0}건</small>
    </button>
  </li>`).join("");
}

/* ── 지난 확인 이후 ────────────────────────────────────────────────
   방문 간격을 잇는 한 줄. 기준점은 '마지막으로 본 브리핑 날짜'(nuclens-last-visit)
   하나다 — 팔로우 배지처럼 엔티티별로 가르지 않는 건, 여기서 말하는 것이
   특정 대상이 아니라 지면 전체이기 때문이다. 렌더 즉시 기준점을 오늘로 옮기므로
   한 방문에 한 번만 뜬다. 숫자는 전부 로컬 데이터에서 센다(추정 0). */
function loadLastVisit() {
  try {
    const raw = JSON.parse(localStorage.getItem("nuclens-last-visit") || "null");
    return raw && typeof raw === "object" && typeof raw.date === "string" ? raw : null;
  } catch { return null; }
}

function renderReturnNote() {
  const box = document.getElementById("returnNote");
  if (!box) return;
  const latest = state.meta?.latest_briefing_date || "";
  const since = loadLastVisit()?.date || "";
  try { localStorage.setItem("nuclens-last-visit", JSON.stringify({ date: latest })); }
  catch { /* 저장 실패가 화면을 죽이면 안 된다 */ }
  if (!latest || !since || since >= latest) { box.hidden = true; return; }
  const missed = state.briefings
    .filter(briefing => briefing.date > since && (briefing.issues || []).length)
    .sort((a, b) => a.date.localeCompare(b.date));
  const fresh = state.issues.filter(issue => (issue.first_seen || "") > since).length;
  const moved = state.issues.filter(issue =>
    (issue.first_seen || "") <= since && (issue.last_seen || "") > since).length;
  if (!missed.length && !fresh && !moved) { box.hidden = true; return; }
  const parts = [];
  if (missed.length) parts.push(`브리핑 <strong>${missed.length}건</strong>`);
  if (fresh) parts.push(`새 이슈 <strong>${fresh}</strong>`);
  if (moved) parts.push(`이어진 이슈 <strong>${moved}</strong>`);
  box.hidden = false;
  box.innerHTML = `<span>지난 확인 ${esc(dateLabel(since))} 이후 — ${parts.join(" · ")}</span>`
    + (missed.length > 1
      ? `<button type="button" data-return-date="${esc(missed[0].date)}">놓친 브리핑부터 보기 <span aria-hidden="true">→</span></button>`
      : "");
}

// 재클러스터로 목록에서 사라진 저장 이슈 — 스냅샷으로 세운 묘비 카드.
function savedTombstone(issueId, meta) {
  const title = meta?.title || "제목을 알 수 없는 이슈";
  const date = meta?.last_seen ? `${dateLabel(meta.last_seen)} 저장 당시` : "";
  return `<article class="issue-card tombstone-card">
    <div class="issue-body">
      <h3>${esc(title)}</h3>
      <p class="tombstone-note">이 이슈는 재구성되어 현재 목록에 없습니다.${date ? ` (${esc(date)})` : ""}</p>
    </div>
    <div class="issue-actions">
      ${meta?.title ? `<button class="text-action" type="button" data-requery="${esc(meta.title)}">제목으로 다시 찾기</button>` : ""}
      <button class="text-action" type="button" data-save-issue="${esc(issueId)}">저장 해제</button>
    </div>
  </article>`;
}

function toggleSaved(issueId) {
  const saved = state.savedIds.has(issueId);
  if (saved) state.savedIds.delete(issueId);
  else state.savedIds.add(issueId);
  persistSaved();
  renderBriefing();
  renderArchiveSearch();
  renderSaved();
  showToast(saved ? "저장을 해제했습니다" : "이슈를 저장했습니다");
}

async function shareIssue(issueId) {
  const issue = state.issues.find(item => item.issue_id === issueId)
    || currentBriefing()?.issues.find(item => item.issue_id === issueId);
  if (!issue) return;
  const url = new URL(issuePath(issueId), location.origin);
  try {
    if (navigator.share) await navigator.share({ title: issue.title, text: issue.title, url: url.href });
    else {
      await navigator.clipboard.writeText(url.href);
      showToast("이슈 링크를 복사했습니다");
    }
  } catch (error) {
    if (error?.name !== "AbortError") showToast("공유 링크를 만들지 못했습니다");
  }
}

function renderSystemStatus() {
  const strip = document.getElementById("systemStatus");
  const header = document.getElementById("headerStatus");
  const footer = document.getElementById("footerStatus");
  const briefing = currentBriefing() || state.briefings[0] || {};
  const refreshedAt = state.systemStatus?.last_success_at || state.manifest?.generated_at || state.meta?.generated_at;
  let status = "ok";
  let lead = "정상";
  // 정상일 때의 문구에서 뺀 둘: '1차 출처 0건' 은 값이 0 인 날이 대부분이라
  // 처음 온 사람에게 '출처 없는 사이트'로 읽혔고, '다음 갱신 2시간 이내' 는
  // 읽는 사람이 할 일이 없는 운영 일정이다. 둘 다 상태 다이얼로그에 남는다.
  let message = `마지막 수집 ${timeLabel(refreshedAt)} · 오늘 기사 ${briefing.article_count || 0}건 · 이슈 ${briefing.issue_count || 0}건`;

  if (state.offline) {
    status = "warning";
    lead = "연결 끊김";
    message = `마지막으로 불러온 ${timeLabel(refreshedAt)} 브리핑을 보고 있습니다`;
  } else if (state.systemStatus?.state === "error") {
    status = "error";
    lead = "수집 오류";
    message = `마지막 정상 수집 ${dateTimeLabel(state.systemStatus.last_success_at)} · ${state.systemStatus.message || "원인을 확인하고 있습니다"}`;
  } else if (state.systemStatus?.state === "refreshing") {
    status = "refreshing";
    lead = "검증 중";
    message = "새 데이터를 검증하고 있습니다 · 완료 전까지 마지막 정상 데이터를 표시합니다";
  } else if (state.systemStatus && !state.systemStatus.watcher_running) {
    status = "warning";
    lead = "수집 지연";
    message = `자동 수집이 중지돼 있습니다 · 마지막 정상 수집 ${dateTimeLabel(state.systemStatus.last_success_at)}`;
  } else if (briefingStaleDays() > 0) {
    // 수집기가 돌고 status.json 이 ok 인데도 새 브리핑이 안 나오는 날이 있다.
    // 그때 '정상'이라고 쓰면 사용자는 오늘 것을 보고 있다고 믿는다 — 가장 나쁜
    // 실패다. 오류가 아니라 **기준 시각**을 말해 준다.
    const days = briefingStaleDays();
    status = "warning";
    lead = "업데이트 지연";
    message = `${days}일 전(${dateLabel(state.meta.latest_briefing_date)}) 브리핑을 보고 있습니다 · 마지막 수집 ${timeLabel(refreshedAt)}`;
  }

  strip.className = `status-strip ${status}`;
  // 한 줄 nowrap 이라 390px 에서 714px 중 절반이 잘렸고 스크롤 힌트도 없었다.
  // 항목마다 span 을 주면 좁은 화면에서 항목 단위로 접힌다 — '오늘 수집 기/사
  // 8건' 처럼 낱말이 갈라지지 않는다. 구분자 '·' 는 CSS ::before 가 그린다.
  const items = String(message).split(" · ").map(part => `<span class="status-item">${esc(part)}</span>`).join("");
  strip.innerHTML = `<div class="wrap status-strip-inner"><span class="status-lead"><span class="status-dot" aria-hidden="true"></span><strong>${lead}</strong></span>${items}</div>`;
  header.className = `header-status ${status}`;
  header.innerHTML = `<i aria-hidden="true"></i><span>${timeLabel(refreshedAt)} · 이슈 ${state.issues.length}</span>`;
  // 좁은 화면에서는 위 span 이 숨겨져 이 버튼에 읽을 이름이 남지 않는다.
  header.setAttribute("aria-label", `데이터 상태 ${lead} · 마지막 수집 ${timeLabel(refreshedAt)}`);
  footer.textContent = `서비스 상태 ${lead} · 마지막 갱신 ${dateTimeLabel(refreshedAt)}`;
  // 정부·기관이 낸 원문은 0 건인 날이 대부분이다. 0 을 띄우면 결함으로 읽히므로
  // 있는 날에만 줄을 만든다 — 이 저장소가 검증 배지에 쓰는 규칙과 같다.
  const primaryCount = briefing.primary_source_count ?? 0;
  document.getElementById("statusDialogContent").innerHTML = `
    <dl class="status-details">
      <div><dt>상태</dt><dd>${esc(lead)}</dd></div>
      <div><dt>마지막 수집</dt><dd>${esc(dateTimeLabel(refreshedAt))}</dd></div>
      <div><dt>보고 있는 브리핑</dt><dd>${esc(dateLabel(state.meta?.latest_briefing_date) || "—")}</dd></div>
      <div><dt>오늘 원문</dt><dd>${briefing.article_count || 0}건</dd></div>
      <div><dt>연결 이슈</dt><dd>${briefing.issue_count || 0}개</dd></div>
      ${primaryCount ? `<div><dt>정부·기관 원문</dt><dd>${primaryCount}건</dd></div>` : ""}
      <div><dt>다음 갱신</dt><dd>2시간 이내</dd></div>
    </dl>${status === "ok" ? "" : `<p>${esc(message)}</p>`}`;
}

async function checkForNewGeneration() {
  try {
    const meta = await loadRootJSON("meta.json", true);
    const current = state.meta?.generated_at || "";
    if (meta?.generated_at && current && meta.generated_at > current && state.pendingGeneration !== meta.generated_at) {
      state.pendingGeneration = meta.generated_at;
      showToast("새 브리핑이 추가됐습니다", "지금 보기", () => location.reload());
      return;
    }
    const manifest = await loadRootJSON("manifest.json", true);
    if (manifest?.generation_id && state.manifest?.generation_id
        && manifest.generation_id !== state.manifest.generation_id
        && state.pendingGeneration !== manifest.generation_id) {
      state.pendingGeneration = manifest.generation_id;
      showToast("새 브리핑이 추가됐습니다", "지금 보기", () => location.reload());
    }
  } catch {
    // 다음 확인 주기에서 다시 시도한다.
  }
}

function syncUrl(mode = "replace") {
  const params = new URLSearchParams();
  if (state.briefingDate) params.set("date", state.briefingDate);
  if (state.region !== "전체") params.set("region", state.region);
  if (state.topic !== "전체") params.set("topic", state.topic);
  if (state.view !== "news") params.set("view", state.view);
  if (state.archiveQuery) params.set("q", state.archiveQuery);
  if (state.archiveEntity) params.set("ent", state.archiveEntity);
  if (state.archiveRegion !== "전체") params.set("ar", state.archiveRegion);
  if (state.archiveTopic !== "전체") params.set("at", state.archiveTopic);
  if (state.archivePeriod !== "all") params.set("ap", state.archivePeriod);
  if (state.archiveVerification !== "전체") params.set("av", state.archiveVerification);
  const query = params.toString();
  const path = state.issueId && state.view !== "trend"
    ? issuePath(state.issueId)
    : (briefRouteOwned && state.view === "news" && state.briefingDate ? briefPath(state.briefingDate) : "/");
  const url = `${path}${query ? `?${query}` : ""}`;
  const historyState = { ...(history.state || {}), nuclensIssue: state.issueId || null };
  if (mode === "push") history.pushState(historyState, "", url);
  else history.replaceState(historyState, "", url);
}

function restoreUrlState() {
  const params = new URLSearchParams(location.search);
  const requestedDate = briefDateFromLocation() || params.get("date");
  if (briefingDates().includes(requestedDate)) state.briefingDate = requestedDate;
  const requestedRegion = params.get("region");
  if (["전체", "국내", "해외"].includes(requestedRegion)) state.region = requestedRegion;
  state.topic = params.get("topic") || "전체";
  const requestedView = ({ saved: "search", pubs: "report" })[params.get("view")] || params.get("view");
  state.view = briefDateFromLocation() ? "news" : (VIEW_IDS.includes(requestedView) ? requestedView : "news");
  state.issueId = issueIdFromLocation() || params.get("issue") || "";
  state.archiveQuery = normalizedSearch(params.get("q") || params.get("aq"));
  // ent 딥링크는 탐색 화면을 전제한다 — view 파라미터가 따로 없으면 그리로 간다.
  state.archiveEntity = params.get("ent") || "";
  if (state.archiveEntity && !params.get("view")) state.view = "search";
  state.archiveRegion = ["전체", "국내", "해외"].includes(params.get("ar")) ? params.get("ar") : "전체";
  state.archiveTopic = params.get("at") || "전체";
  state.archivePeriod = ["7", "30", "all"].includes(params.get("ap")) ? params.get("ap") : "all";
  state.archiveVerification = ["verified", "unverified"].includes(params.get("av")) ? params.get("av") : "전체";
}

function renderTopicSelects() {
  const briefingCounts = new Map();
  state.briefings.forEach(briefing => briefing.issues.forEach(issue => {
    (issue.topics || []).forEach(topic => briefingCounts.set(topic, (briefingCounts.get(topic) || 0) + 1));
  }));
  const archiveCounts = new Map();
  state.issues.forEach(issue => (issue.topics || []).forEach(topic => {
    archiveCounts.set(topic, (archiveCounts.get(topic) || 0) + 1);
  }));
  [
    ["topicSel", briefingCounts, state.topic],
    ["archiveTopic", archiveCounts, state.archiveTopic],
  ].forEach(([id, counts, selected]) => {
    const select = document.getElementById(id);
    const topics = [...counts].sort((a, b) => b[1] - a[1] || String(TOPIC_LABELS[a[0]] || a[0]).localeCompare(String(TOPIC_LABELS[b[0]] || b[0]), "ko"));
    select.innerHTML = '<option value="전체">전체 주제</option>' + topics.map(([topic, count]) => (
      `<option value="${esc(topic)}">${esc(TOPIC_LABELS[topic] || topic)} · ${count}</option>`
    )).join("");
    select.value = counts.has(selected) ? selected : "전체";
  });
}

function renderDateSelect() {
  const select = document.getElementById("dateSel");
  select.innerHTML = state.briefings.map(briefing => (
    `<option value="${esc(briefing.date)}">${esc(dateWeekdayLabel(briefing.date))}</option>`
  )).join("");
  select.value = state.briefingDate;
  const dates = briefingDates();
  const index = dates.indexOf(state.briefingDate);
  document.getElementById("prevDay").disabled = index < 0 || index >= dates.length - 1;
  document.getElementById("nextDay").disabled = index <= 0;
}

function issueMatchesRegion(issue) {
  if (state.region === "전체") return true;
  return (issue.related_articles || []).some(article => article.region === state.region);
}

function issueMatchesFilters(issue) {
  if (!issueMatchesRegion(issue)) return false;
  return state.topic === "전체" || (issue.topics || []).includes(state.topic);
}

function issueStatusText(issue, archive = false) {
  if (archive && issue.lifecycle === "quiet") return `종결 · ${dateLabel(issue.last_seen)}`;
  const tracked = issue.tracked_briefings || issue.briefing_count || 1;
  // 중요도가 추적 이력을 덮으면 '달라진 이슈'인데 무엇이 이어지는지 안 보인다.
  if (issue.importance === "must_read") return tracked > 1 ? `주요 · ${tracked}회 추적` : "주요";
  if (tracked > 1) return `업데이트 · ${tracked}회 추적`;
  // 검증 상태는 배지가 단독으로 책임진다. 여기서 다시 말하면 같은 줄에 두 번 뜬다.
  return "새 이슈";
}

function issueActions(issue) {
  const representativeUrl = safeUrl(issue.representative_article?.url);
  const saved = state.savedIds.has(issue.issue_id);
  return `<div class="issue-actions">
    <button class="issue-detail-button" type="button" data-issue-id="${esc(issue.issue_id)}">타임라인 <span>${issue.article_count || 0}</span></button>
    ${representativeUrl ? `<a class="source-link" href="${esc(representativeUrl)}" target="_blank" rel="noopener noreferrer">원문 <span aria-hidden="true">↗</span></a>` : ""}
    <button class="text-action ${saved ? "saved" : ""}" type="button" data-save-issue="${esc(issue.issue_id)}">${saved ? "저장됨" : "저장"}</button>
    <button class="text-action" type="button" data-share-issue="${esc(issue.issue_id)}">공유</button>
  </div>`;
}

function trackingPeriod(issue) {
  return `<div class="tracking-period" aria-label="${esc(dateLabel(issue.first_seen))}부터 ${esc(dateLabel(issue.last_seen))}까지 ${issue.briefing_count || 1}회 추적">
    <span>${esc(dateLabel(issue.first_seen))}</span><i><b></b></i><span>${esc(dateLabel(issue.last_seen))}</span><strong>${issue.briefing_count || 1}회 브리핑</strong>
  </div>`;
}

// 같은 사건을 KEEI 세계 원전시장 인사이트가 다뤘다면 그 호로 연결한다.
// 이건 예외적으로 붙는 표시라 정보가 된다 — 대다수가 다는 배지는 신호를 죽인다.
function keeiRefLine(issue) {
  const refs = (issue.keei_refs || []).filter(ref => ref && ref.url && ref.title);
  if (!refs.length) return "";
  const links = refs.map(ref => {
    const url = safeUrl(ref.url);
    // 날짜가 있으면 "6월 26일호", 없으면 제목 그대로. 제목에 '호'를 붙이면
    // "…인사이트호" 같은 문장이 나온다.
    const label = ref.date ? `${dateLabel(ref.date)}호` : ref.title;
    return url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
      : esc(label);
  }).join(" · ");
  return `<p class="issue-keei"><strong>에경연 인사이트</strong><span>${links}</span></p>`;
}

// 상세에서는 KEEI 가 이 사건을 어떤 목차 항목으로 다뤘는지까지 보여준다.
// 목차 제목 줄과 원문 링크만 — 본문은 싣지 않는다(저작권).
function keeiDialogSection(issue) {
  const refs = (issue.keei_refs || []).filter(ref => ref && ref.url && ref.title);
  if (!refs.length) return "";
  const rows = refs.map(ref => {
    const url = safeUrl(ref.url);
    const pubLabel = `${ref.org_kr || "에경연"}${ref.date ? ` · ${dateLabel(ref.date)}` : ""}`;
    return `<li>
      ${ref.item ? `<span class="keei-item">${esc(ref.item)}</span>` : ""}
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(ref.title)} <span aria-hidden="true">↗</span></a>` : `<span>${esc(ref.title)}</span>`}
      <small>${esc(pubLabel)}</small>
    </li>`;
  }).join("");
  return `<section class="dialog-keei" aria-labelledby="issueKeeiTitle">
    <div class="dialog-section-head"><h3 id="issueKeeiTitle">에경연 인사이트가 다룬 사건</h3><span>목차와 원문 링크만 제공합니다</span></div>
    <ul>${rows}</ul>
  </section>`;
}

function issueCard(issue, index, archive = false, front = false) {
  const topic = primaryTopicLabel(issue);
  const selectionReason = (issue.selection_reasons || []).find(reason => String(reason || "").trim());
  const title = archive ? markMatch(issue.title, state.archiveQuery) : esc(issue.title);
  // '변화' 줄(= 직전 브리핑 문장)은 카드에서 뺐다. 사용자 지적(2026-08-05):
  // "직전 브리핑 내용이 왜 들어가, 그럴거면 그 전꺼를 보겠지 당연히." 맞는 말이다 —
  // 카드가 답해야 하는 것은 '이 뉴스가 무슨 뜻인가'이지 '어제 뭐라고 했나'가
  // 아니다. 상태는 이미 메타 줄('업데이트 · N회 추적')이 말하고, 지난 문장은
  // 상세의 사건 타임라인에 그대로 있다. 카드에서 뺀 것들과 같은 원칙.
  // 카드의 두 번째 줄은 '무엇'이 아니라 '왜'다. summary 는 제목을 어순만 바꿔
  // 다시 쓴 문장이 대부분이라(8/3 브리핑 실측 8건 중 5건) 두 번 읽게 만들 뿐
  // 정보를 더하지 않는다. implication(= 상세의 '시사점')은 이미 만들어 두고도
  // 두 탭 아래에만 두던 문장이다. 그 자리를 바꾼다.
  //
  // **why_important 는 여기 쓰지 않는다.** 2026-08-04 에 두 해석이 갈라졌지만
  // 카드에 맞는 건 짧은 쪽뿐이다 — 실측 중앙값 implication 53자 / why_important
  // 124자인데 이 줄은 2줄(모바일 3줄)에서 잘린다. 잘린 분석문은 완결된 요약보다
  // 나쁘다. 대신 must_read 인데 implication 이 빈 17건은 summary 로 물러난다 —
  // 화면이 아니라 큐레이션 프롬프트에서 채울 구멍이다.
  //
  // 2026-08-08: 카드를 문단 하나에서 라벨 붙은 세 칸으로 바꿨다. 장문 summary 는
  // 카드에서 완전히 내려가 상세로만 간다 — 무엇을 읽을지 고르는 화면에서 문단을
  // 읽게 만들면 스캔이 안 된다. 세 칸의 역할 분리(사실 / 영향 / 앞으로)는
  // build_data.finalize_card_fields 가 확정해 둔다. 여기서 or 폴백을 쌓으면
  // 그 계약이 두 곳으로 흩어진다.
  const changeText = issueChangeText(issue);
  const whyText = String(issue.card_why ?? (issue.implication || issue.why_important || "")).trim();
  const nextText = String(issue.open_question || "").trim();
  const mark = text => (archive ? markMatch(text, state.archiveQuery) : esc(text));
  const cardRow = (label, text, extra = "") => (text
    ? `<p class="issue-line"><span class="issue-line-label">${label}</span>${extra}<span class="issue-line-text">${mark(text)}</span></p>`
    : "");
  // 검색 하이라이트 판정도 화면에 실제로 뜨는 문장을 기준으로 해야 한다.
  const visibleMatch = normalizedSearch(
    `${issue.title || ""} ${changeText} ${whyText} ${nextText}`
  ).includes(state.archiveQuery);
  const matchContext = archive && state.archiveQuery && !visibleMatch
    ? `<p class="search-match">검색 조건 <mark>${esc(state.archiveQuery)}</mark>과 연결된 이슈입니다.</p>`
    : "";
  // 시안의 목록은 표다 — 순서 / 변화 / 이슈 / 근거 네 열. 그래서 '변화'와 '근거'는
  // .issue-body 밖으로 나와 각자 열이 된다. 이 둘을 body 안에 두고 CSS
  // display:contents 로 흩으면 제목과 요약이 서로 다른 그리드 행으로 갈라져,
  // 근거 열 높이(98px)가 그 사이 여백으로 배분된다(실측 42px). 열은 열로 나눈다.
  // data-issue-card: 행 전체를 상세 진입점으로 만드는 위임 표식. hover 가
  // 행 배경을 바꾸면서 클릭은 제목만 받던 어긋남을 접는다. 접근성 경로는
  // 그대로 제목 버튼이다 — 행에 tabindex 를 주면 탭 정지만 두 배가 된다.
  return `<article id="issue-card-${esc(issue.issue_id)}" class="issue-card ${archive ? "archive-card" : ""} ${front ? "front" : ""} ${issueToneClass(issue)}" data-issue-card="${esc(issue.issue_id)}">
    <div class="issue-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</div>
    <div class="issue-meta">
      <span class="issue-state">${esc(issueStatusText(issue, archive))}</span>
      <span>${esc(issue.region)}</span>
      ${topic ? `<span class="issue-topic">${esc(topic)}</span>` : ""}
      ${verificationBadge(issue)}
      ${reportPickBadge(issue)}
    </div>
    <div class="issue-body">
      <h3><button type="button" class="issue-title-button" data-issue-id="${esc(issue.issue_id)}">${title}</button></h3>
      ${cardRow("달라진 것", changeText)}
      ${cardRow("왜 중요해요", whyText, `<span class="ai-badge">AI</span>`)}
      ${cardRow("다음 확인", nextText)}
      ${selectionReason ? `<div class="issue-reason-row"><span class="issue-reason-chip topic-chip">${esc(selectionReason)}</span></div>` : ""}
      ${matchContext}
      ${archive ? trackingPeriod(issue) : ""}
    </div>
    ${issueActions(issue)}
  </article>`;
}

// 상위 1건만 받는 편집 카드. 표의 한 행이 요약 두 줄로 끝나는 데 반해 여기서는
// 무슨 일 / 왜 중요 / 무엇이 달라졌나 / 무엇이 아직 미확정인가를 각각 세운다.
// 라벨은 새로 짓지 않고 상세와 같은 것을 쓴다 — issueDetailModel 이 '오늘의 변화'
// 와 '최근 변화'를 근거일로 갈라 주므로, 여기서 "어제와 달라진 점"이라고 이름
// 붙이면 데이터가 보장하지 않는 것을 말하게 된다.
// 빈 블록은 세우지 않는다. '변화 없음'을 매일 한 줄 차지하게 두면 그 자리가
// 신호가 아니라 배경이 된다(카드에서 뺀 것들과 같은 원칙).
function leadCard(issue, briefing) {
  const model = issueDetailModel(issue, briefing.date);
  const topic = primaryTopicLabel(issue);
  // 국가는 대표 기사에서 온다 — 이슈 행(4열 표)에는 지역(국내/해외)만 있지만
  // 선두 카드는 판단을 펼치는 자리라 어느 나라 이야기인지까지 세운다.
  const countryChips = (issue.representative_article?.countries || [])
    .map(code => COUNTRY_LABELS[code] || code)
    .filter(label => label && label !== issue.region);
  // 제목은 항상 세운다. 원래는 히어로 h1 과 겹치는 날(headline_kind="issue")
  // 접었는데, h1 이 sr 전용 날짜 라벨이 된 뒤로 그 전제가 죽어 선두 이슈의
  // 제목이 화면 어디에도 없었다(실측 8/6 — 첫 화면이 '무슨 일' 본문으로 시작).
  // 이제 이 h3 가 이 문장이 페이지에 서는 유일한 자리다.
  // group 은 축을 가른다: fact = 무슨 일이 있었나, read = 그걸 어떻게 읽나.
  // 근거 패널이 이 이슈를 맡는 폭(≥1200px)에서는 read 를 여기서 되풀이하지
  // 않는다 — 같은 문장이 30cm 떨어져 두 번 서는 대신, 카드는 사실로 짧게 끝나고
  // 해석은 패널이 번호를 붙여 전개한다. 패널이 없는 폭에서는 전부 여기 선다
  // (모바일에서 해석 레이어가 잘리던 2026-08-03 감사의 재발 방지).
  // '무슨 일'(= summary) 은 세우지 않는다. 변화 문장이 그 요약의 첫 문장으로
  // 만들어지므로 두 블록이 구조적으로 같은 말이었다(실측 2026-08-08: 선두 카드
  // '무슨 일' 과 근거 패널 '오늘의 변화' 가 20자 넘게 동일). 사실은 제목과
  // '달라진 것' 이 말하고, 요약 전문은 상세에 그대로 있다.
  const blocks = [
    model.change ? { label: "달라진 것", text: model.change.text, tone: "change", group: "fact" } : null,
    model.why ? { label: model.why.label, text: model.why.text, group: "read" } : null,
    model.impact ? { label: model.impact.label, text: model.impact.text, group: "read" } : null,
    model.openQuestion ? { label: "다음 확인", text: model.openQuestion, tone: "open", group: "read" } : null,
  ].filter(Boolean);
  const shown = railIsActive() ? blocks.filter(block => block.group === "fact") : blocks;
  return `<article id="issue-card-${esc(issue.issue_id)}" class="lead-card ${issueToneClass(issue)}">
    <div class="lead-meta">
      <span class="issue-state">${esc(issueStatusText(issue))}</span>
      <span>${esc(issue.region)}</span>
      ${countryChips.map(label => `<span>${esc(label)}</span>`).join("")}
      ${topic ? `<span>${esc(topic)}</span>` : ""}
      ${verificationBadge(issue)}
      ${reportPickBadge(issue)}
    </div>
    <h3><button type="button" class="issue-title-button" data-issue-id="${esc(issue.issue_id)}">${esc(issue.title)}</button></h3>
    <dl class="lead-blocks">${shown.map(block => `<div class="lead-block${block.tone ? ` tone-${block.tone}` : ""}">
      <dt>${esc(block.label)}</dt><dd>${esc(block.text)}</dd>
    </div>`).join("")}</dl>
    ${keeiRefLine(issue)}
    ${issueActions(issue)}
  </article>`;
}

function renderBriefingSidebar(briefing, leadId = "") {
  // 근거 패널의 기본 선택 = **선두 이슈**.
  //
  // 예전에는 그다음 이슈를 잡았다. 선두 카드가 이미 해석을 펼쳐 놓았으니 같은
  // 문장을 두 번 세우지 않겠다는 뜻이었는데, 결과가 둘 다 나빴다. ①페이지가
  // 머리로 내세운 이슈와 바로 옆 패널이 말하는 이슈가 달라 "이 패널은 왜 다른
  // 이야기를 하나"가 된다 ②중복을 피하려고 선두 카드가 해석 5블록을 통째로
  // 들고 있어야 했고, 그 카드 하나가 1440×900 첫 화면을 다 먹어 목록이 한 행도
  // 안 보였다(실측 0행).
  //
  // 규칙을 뒤집는다: 패널이 선두 이슈의 해석을 맡고, 카드는 사실만 남긴다
  // (leadCard 의 railIsActive 분기). 중복 금지는 그대로 지켜지고, 두 요소가
  // 같은 이슈를 가리키므로 화면이 한 이야기를 한다.
  // 선택이 이번 브리핑에 없는 이슈를 가리키면(날짜 이동 등) 다시 잡는다.
  const inBriefing = briefing.issues.some(issue => issue.issue_id === state.railIssueId);
  if (!inBriefing) state.railIssueId = leadId || briefing.issues[0]?.issue_id || "";
  renderEvidenceRail();
  // 히어로가 이미 지표를 보여준다. 사이드에는 히어로에 없는 검증 분포를 둔다.
  const verified = new Map(VERIFICATION_ORDER.map(status => [status, 0]));
  briefing.issues.forEach(issue => {
    const { status } = verificationState(issue);
    verified.set(status, (verified.get(status) || 0) + 1);
  });
  document.getElementById("sideVerification").innerHTML = VERIFICATION_ORDER
    .filter(status => verified.get(status) > 0)
    .map(status => `<div class="v-row v-${status}">
      <span>${VERIFICATION_VIEW[status].mark} ${esc(VERIFICATION_VIEW[status].label)}</span><strong>${verified.get(status)}</strong>
    </div>`).join("")
    || '<p class="empty">오늘 판정할 이슈가 없습니다.</p>';
}

// 이전 브리핑 이후 상태가 실제로 움직인 이슈 — 히어로 아래 첫 구역의 재료다.
function changedIssues(briefing) {
  return briefing.issues
    .filter(issue => issue.status === "ongoing" || (issue.previous_article_count || 0) > 0)
    .slice(0, 5);
}

// 이슈 0건은 세 가지 서로 다른 상태다. 하나로 뭉뚱그리면 파이프라인 장애가
// '조용한 날'로 위장된다.
//   A 기준 미달  — 파이프라인 정상 + 하한에서 걸린 후보가 있음
//   B 후보 없음  — 파이프라인 정상 + 애초에 후보가 0건
//   C 지연·실패  — 파이프라인이 안 돌았거나 실패
// 판정 근거는 봇이 delivery_log 에 남긴 selection_stats 다. 그게 없는 구간(기능
// 도입 이전 날짜)에서는 단정하지 않고 중립 문구로 내려간다.
function pipelineTrouble() {
  const status = state.systemStatus;
  if (!status) return null;
  if (status.state === "error" || status.watcher_running === false) return status;
  return null;
}

// 오늘(KST). 브라우저 시간대가 무엇이든 서울 기준으로 읽는다 — 이 사이트의
// 하루는 브리핑 생성 시각(07:25 KST)을 기준으로 끊긴다.
function todayKST() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
}

function hourKST() {
  return Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul", hour: "2-digit", hour12: false,
  }).format(new Date()));
}

// 브리핑은 매일 07:25 KST 에 생성된다. 그 전까지 최신 브리핑이 어제 것인 건
// **정상**이므로 지연으로 부르면 매일 아침 거짓 경보가 뜬다. 유예를 09:00 까지
// 둔다(GitHub cron 지연이 실측 50~66분).
const STALE_GRACE_HOUR = 9;

function briefingStaleDays() {
  const latest = state.meta?.latest_briefing_date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(latest || ""))) return 0;
  const today = todayKST();
  if (latest >= today) return 0;
  const days = Math.round(
    (new Date(`${today}T00:00:00+09:00`) - new Date(`${latest}T00:00:00+09:00`)) / 86400000);
  if (days === 1 && hourKST() < STALE_GRACE_HOUR) return 0;
  return days;
}

function emptyBriefingState(briefing) {
  const trouble = pipelineTrouble();
  if (trouble) {
    const stamp = trouble.last_success_at ? dateTimeLabel(trouble.last_success_at) : "";
    return {
      title: "브리핑 데이터가 아직 갱신되지 않았습니다",
      detail: `${esc(trouble.message || "자동 수집 상태를 확인하고 있습니다")}`
        + `${stamp ? ` · 마지막 정상 확인 ${esc(stamp)}` : ""}`,
    };
  }
  const below = briefing && Number(briefing.below_floor_count);
  if (Number.isFinite(below) && below > 0) {
    return {
      title: "오늘은 브리핑 기준을 넘는 이슈가 없습니다",
      detail: `검토한 후보 ${below}건은 기준에 미치지 못했습니다. `
        + `<button type="button" data-go-view="search">탐색에서 보기</button>`,
    };
  }
  if (briefing && briefing.candidate_count === 0) {
    return {
      title: "오늘 새로 확인된 브리핑 이슈가 없습니다",
      detail: '진행 중인 이슈는 <button type="button" data-go-view="search">탐색</button>에서 확인할 수 있습니다.',
    };
  }
  return {
    title: "오늘은 새로 연결된 이슈가 없습니다",
    detail: "가장 최근 브리핑을 확인해 보세요.",
  };
}

function renderEmptyBriefing(briefing, issueList) {
  const view = emptyBriefingState(briefing);
  document.getElementById("changedIssues").hidden = true;
  document.getElementById("todayAgenda").hidden = true;
  // 히어로가 이미 사유를 말했으므로 목록에서 같은 문장을 되풀이하지 않는다.
  // 목록은 '그래서 어디로 가면 되는가'만 담당한다.
  document.getElementById("showChangedIssues").hidden = true;
  // 근거 칩도 함께 지운다 — 안 그러면 직전 브리핑의 근거가 남아 없는 문장을 가리킨다
  const staleEvidence = document.getElementById("headlineEvidence");
  if (staleEvidence) { staleEvidence.hidden = true; staleEvidence.innerHTML = ""; }
  issueList.innerHTML = `<div class="empty-state"><p>${view.detail}</p></div>`;
}

function weekRange(date) {
  const end = new Date(`${date}T00:00:00+09:00`);
  const start = new Date(end.getTime() - 6 * 86400000);
  return { start: start.toISOString().slice(0, 10), end: date };
}

function weeklyChangedIssues(briefing) {
  const { start, end } = weekRange(briefing.date);
  return state.issues
    .filter(issue => issue.latest_change && issue.last_seen >= start && issue.last_seen <= end)
    .sort((a, b) => String(b.last_seen).localeCompare(String(a.last_seen))
      || (b.card_article_count || 0) - (a.card_article_count || 0))
    .slice(0, 5);
}

// 주간 표본이 서로 비교 가능한가 — 모수가 주마다 다르면 방향 판단이 '원자력
// 이슈가 늘었다'가 아니라 '데이터가 늘었다'가 된다.
//
// 원인은 빌드에서 고쳤다(build_topic_weeks: 기사×주제 쌍 → 이슈, 부분 주 제외).
// 실측 2026-08-08 주별 합계 22/59/86/580 → 80/85/102. 이 게이트는 그래도 남긴다 —
// 수집이 다시 기울면 화면이 먼저 입을 다무는 쪽이 낫다.
const TOPIC_WEEK_SAMPLE_RATIO = 2;

function topicWeeksComparable(totals) {
  const low = Math.min(...totals);
  const high = Math.max(...totals);
  return low > 0 && high / low <= TOPIC_WEEK_SAMPLE_RATIO;
}

// 주제 변화. 흐름 탭이 이 표의 주인이다 — 오늘 화면은 '오늘·이번 주'를,
// 흐름은 '몇 주간 어느 방향으로'를 맡는다.
//
// 폭은 고정 4주가 아니라 빌드가 넘겨준 '온전한 주'의 수다. 브리핑 6일 미만인
// 주는 빌드에서 이미 빠졌으므로(build_topic_weeks) 여기 오는 주는 전부 셀 수 있다.
// 3주 미만이면 방향이라 부를 게 없어서 표 자체를 내린다.
const TOPIC_FLOW_MIN_WEEKS = 3;
const TOPIC_FLOW_MAX_WEEKS = 4;

function topicFlowSpan() {
  const weeks = (state.trend?.weeks || []).length;
  return weeks < TOPIC_FLOW_MIN_WEEKS ? 0 : Math.min(TOPIC_FLOW_MAX_WEEKS, weeks);
}

function topicFlowRows() {
  const span = topicFlowSpan();
  if (!span) return [];
  const series = state.trend?.topic_series || {};
  const entries = Object.entries(series).filter(([, values]) => Array.isArray(values) && values.length >= span);
  if (!entries.length) return [];
  const recent = entries.map(([topic, values]) => ({ topic, values: values.slice(-span).map(Number) }));
  const totals = recent[0].values.map((_, index) => recent.reduce((sum, row) => sum + (row.values[index] || 0), 0));
  if (!topicWeeksComparable(totals)) return [];
  return recent.map(row => {
    const shares = row.values.map((value, index) => totals[index] ? value / totals[index] * 100 : 0);
    const delta = shares.at(-1) - shares[0];
    const sample = row.values.reduce((sum, value) => sum + value, 0);
    // 표본이 작으면 방향을 말하지 않는다. 8pp 는 주 단위 잡음을 넘는 폭.
    return { ...row, span, shares, delta, sample, directional: Math.abs(delta) >= 8 && sample >= 8 };
  });
}

function topicFlowRow(row) {
  const direction = !row.directional ? "= 변화 없음" : row.delta > 0 ? "▲ 늘고 있음" : "▼ 줄고 있음";
  const maxShare = Math.max(1, ...row.shares);
  return `<div class="home-topic-row">
    <strong>${esc(TOPIC_LABELS[row.topic] || row.topic)}</strong>
    <span class="topic-spark" aria-label="최근 ${row.span}주 비중 ${row.shares.map(value => `${Math.round(value)}%`).join(", ")}">${row.shares.map(value => `<i style="height:${Math.max(8, Math.round(value / maxShare * 100))}%"></i>`).join("")}</span>
    <span class="topic-direction ${row.directional ? (row.delta > 0 ? "up" : "down") : "flat"}">${direction}</span>
    <small class="topic-figures">
      <!-- 화살표는 span 주 전체의 변화다. 옆 숫자를 '지난주 → 이번 주'로 두면
           ▼ 옆에 오르는 두 수가 붙는다(실측: SMR ▼ / 18% → 19%). 같은 구간을 쓴다. -->
      <span class="topic-compare">${row.span}주 전 ${Math.round(row.shares[0])}% → 이번 주 ${Math.round(row.shares.at(-1))}%</span>
      <span class="topic-delta">${row.span}주 ${row.delta > 0 ? "+" : ""}${Math.round(row.delta)}pp</span>
      <!-- 이슈 단위다(build_topic_weeks). 한 이슈가 주제 둘이면 둘 다에 1건씩
           가므로 합계는 이슈 총수보다 클 수 있다. -->
      <span class="topic-sample">표본 이슈 ${row.sample}건</span>
    </small>
  </div>`;
}

function renderHomeIntelligence(briefing) {
  const { start, end } = weekRange(briefing.date);
  const weeklyIssues = state.issues.filter(issue => issue.last_seen >= start && issue.last_seen <= end);
  const confirmed = weeklyIssues.filter(issue => ["official", "corroborated"].includes(verificationState(issue).status));
  const metrics = document.getElementById("heroMetrics");
  const confirmedRate = weeklyIssues.length ? confirmed.length / weeklyIssues.length : 0;
  metrics.hidden = confirmedRate < 0.5;
  metrics.textContent = metrics.hidden ? "" : `근거 확인 ${confirmed.length}건 · 주간 이슈 ${weeklyIssues.length}건`;


  // 이번 주 해설이 담당하는 것은 '카드에 없는 연결·원인·파급'뿐이다.
  //   · policy_shifts[].what  → 오늘 3분의 '이번 주 결론' 소유 (여기서 다시 안 낸다)
  //   · theme_moves           → 바로 위 '주제 변화' 소유 (4주 방향)
  //   · 남는 것 = weekly_intro(사건 간 연결) + so_what(파급효과)
  // 셋을 다 내던 예전 구성은 한 화면에서 같은 문장을 세 번 보게 만들었다.
  const report = state.trend?.weekly_report;
  const story = document.getElementById("homeWeeklyStory");
  const intro = dropTextsAlreadyOnCards([report?.weekly_intro], briefing);
  const soWhat = dropTextsAlreadyOnCards(
    (report?.policy_shifts || []).map(row => row?.so_what), briefing
  ).slice(0, 3);
  // 새 관점이 하나도 없으면 억지로 채우지 않고 숨긴다.
  story.hidden = intro.length === 0 && soWhat.length === 0;
  if (!story.hidden) {
    document.getElementById("homeWeeklyStoryBody").innerHTML = `
      ${intro.map(text => `<p class="home-weekly-intro">${esc(text)}</p>`).join("")}
      ${soWhat.map(text => `<article><p>${esc(text)}</p></article>`).join("")}`;
  }
}

// 카드가 이미 화면에 낸 문장은 오늘 3분에서 다시 내지 않는다. 정확히 같은 문장만
// 막는다 — 어순·어미만 바꾼 재진술은 문자열로 못 잡고(app.js 아래 주석 참조),
// 잘못 지우면 유일한 결론이 사라진다. 의미 중복은 fixture 와 수동 검토 몫이다.
function dropTextsAlreadyOnCards(lines, briefing) {
  const onScreen = new Set();
  for (const issue of (briefing?.issues || [])) {
    for (const text of [issue.title, issueChangeText(issue), issue.card_why, issue.open_question]) {
      const clean = String(text || "").trim();
      if (clean) onScreen.add(clean);
    }
  }
  const seen = new Set();
  return lines
    .map(line => String(line || "").trim())
    .filter(line => line && !onScreen.has(line) && !seen.has(line) && seen.add(line));
}

function renderTodayAgenda(briefing) {
  const agenda = document.getElementById("todayAgenda");
  const report = state.trend?.weekly_report;
  // 이번 주 결론 = 판이 바뀐 것. theme_moves 는 4주 방향이라 '주제 변화'의 몫이고,
  // 여기서 같이 내면 두 섹션이 같은 답을 한다.
  const conclusions = dropTextsAlreadyOnCards(
    (report?.policy_shifts || []).map(row => row?.what), briefing
  ).slice(0, 3);
  // 다음 확인은 카드의 open_question 이 사실상 비어 있어(실측 19건 중 0건) 주간
  // watchpoints 가 화면에서 이 질문에 답하는 유일한 자리다.
  const watch = dropTextsAlreadyOnCards(report?.watchpoints || [], briefing).slice(0, 3);

  const conclusionBlock = document.getElementById("agendaConclusions");
  conclusionBlock.hidden = conclusions.length === 0;
  document.getElementById("agendaConclusionList").innerHTML =
    conclusions.map(text => `<li>${esc(text)}</li>`).join("");

  const watchBlock = document.getElementById("agendaWatch");
  watchBlock.hidden = watch.length === 0;
  document.getElementById("agendaWatchList").innerHTML =
    watch.map(text => `<li>${esc(text)}</li>`).join("");

  agenda.hidden = conclusions.length === 0 && watch.length === 0;
  document.getElementById("todayAgendaMeta").textContent =
    agenda.hidden ? "" : `결론 ${conclusions.length} · 확인 ${watch.length}`;
}

function renderBriefing() {
  const briefing = currentBriefing();
  const issueList = document.getElementById("issueList");
  issueList.classList.remove("skeleton-list");
  // 모든 반환 경로(브리핑 없음·0건·정상)에서 한 번씩 판정되도록 맨 앞에서 부른다.
  renderAudioBrief(briefing);
  if (!briefing) {
    renderEmptyBriefing(null, issueList);
    return;
  }
  renderTodayAgenda(briefing);
  renderHomeIntelligence(briefing);
  // 필터 때문에 비어 보이는 것과 그날 실제로 이슈가 0건인 것은 다르다.
  if (!briefing.issues.length) {
    renderEmptyBriefing(briefing, issueList);
    document.getElementById("issueCount").textContent = "0개 이슈";
    renderBriefingSidebar(briefing);
    renderNewsFeed();
    return;
  }
  let issues = briefing.issues.filter(issueMatchesFilters);
  // 선두는 편집 판단이라 목록 정렬 토글을 따르지 않는다 — '최신순'으로 바꿨다고
  // 가장 먼저 볼 이슈가 달라지지는 않는다. 필터는 따른다(안 보이는 이슈를 선두로
  // 세울 수는 없다).
  const lead = issues[0] || null;
  const leadId = lead ? lead.issue_id : "";
  document.getElementById("leadIssue").hidden = !lead;
  document.getElementById("leadCard").innerHTML = lead ? leadCard(lead, briefing) : "";
  if (state.issueSort === "latest") {
    issues = [...issues].sort((a, b) => String(b.last_seen).localeCompare(String(a.last_seen)) || b.article_count - a.article_count);
  }
  // HERO 는 생성 문장이 아니라 고정 제품 헤드라인이다. daily_lead 는 아카이브와
  // RSS 용으로 계속 만들지만 이 자리에 다시 연결하지 않는다.
  document.getElementById("briefingTitle").textContent = "이번 주 원자력, 무엇이 달라졌나";
  document.getElementById("briefingKicker").textContent = "주간 원자력 인텔리전스";
  const hero = document.getElementById("briefingHero");
  if (hero) {
    hero.classList.add("lead-issue", "weekly-hero");
    hero.classList.remove("no-lead");
  }
  document.getElementById("briefingDateLabel").textContent = `${dateWeekdayLabel(briefing.date)} · 제${state.briefings.length - state.briefings.indexOf(briefing)}호`;
  // 근거 칩은 히어로가 문장을 낼 때 그 문장이 어디서 왔는지 보이려고 있었다.
  // 낼 문장이 없으니 칩도 없다. 컨테이너는 남긴다 — index.html 이 참조한다.
  const evidenceBox = document.getElementById("headlineEvidence");
  if (evidenceBox) {
    evidenceBox.hidden = true;
    evidenceBox.innerHTML = "";
  }

  const changed = weeklyChangedIssues(briefing);
  const sectionChanges = changed.length >= 3 ? changed : [];
  const changedIds = new Set(sectionChanges.map(issue => issue.issue_id));
  // 선두로 올린 이슈는 아래 두 목록에서 뺀다 — 같은 이슈가 한 화면에 두 번 서면
  // 개수 표시("8개 이슈")도 실제 카드 수와 어긋난다.
  const rest = issues.filter(issue => !changedIds.has(issue.issue_id) && issue.issue_id !== leadId);
  const changedSection = document.getElementById("changedIssues");
  const visibleChanged = sectionChanges.filter(issue => issueMatchesFilters(issue) && issue.issue_id !== leadId);
  changedSection.hidden = visibleChanged.length === 0;
  document.getElementById("changedCount").textContent = `${visibleChanged.length}개 이슈`;
  document.getElementById("changedList").innerHTML =
    visibleChanged.map((issue, index) => issueCard(issue, index)).join("");
  const changedButton = document.getElementById("showChangedIssues");
  changedButton.hidden = visibleChanged.length === 0;
  // 몇 건이 달라졌는지는 버튼이 말한다 — 히어로에 지표 블록을 새로 세우면
  // 헤더 상태 칩·상태 스트립과 같은 숫자를 되풀이하게 된다(중복 표시 금지 원칙).
  if (visibleChanged.length) {
    changedButton.innerHTML = `달라진 이슈 ${visibleChanged.length}건 보기 <span aria-hidden="true">→</span>`;
  }

  document.getElementById("issueCount").textContent = `${rest.length}개 이슈`;
  issueList.classList.toggle("list-view", state.issueView === "list");
  // front 강조는 '기본 화면'에서만 — 최신 브리핑 + 필터·정렬이 기본값일 때.
  // 편집 판단이 아니라 기존 순서의 상위 2건을 조판만 다르게 세우는 것이므로,
  // 조건이 하나라도 어긋나면(과거 날짜·필터·최신순) 강조를 접는다. 개수는
  // 정확히 2건 — "2~3건" 같은 재량 표현이 남으면 화면마다 다르게 구현된다.
  const frontActive = briefing.date === state.briefings?.[0]?.date
    && state.region === "전체" && state.topic === "전체"
    && state.issueSort === "importance" && state.issueView === "card";
  // 위 '지금 달라진 이슈'에 결과가 남아 있는데 아래에서 '없습니다'라고 하면
  // 한 화면이 스스로를 부정한다. 두 구역을 합쳐 0건일 때만 빈 상태를 보인다.
  const elsewhere = visibleChanged.length ? "지금 달라진 이슈" : "가장 먼저 볼 이슈";
  issueList.innerHTML = rest.length
    ? rest.map((issue, index) => issueCard(issue, index, false, frontActive && index < 2)).join("")
    : (visibleChanged.length || lead
      ? `<p class="section-note">필터에 맞는 이슈는 위 <strong>${elsewhere}</strong>에 있습니다.</p>`
      : '<div class="empty-state"><strong>조건에 맞는 이슈가 없습니다</strong><p>주제나 지역 필터를 해제해 보세요.</p><button type="button" data-clear-briefing>필터 해제</button></div>');
  const activeFilters = [];
  if (state.region !== "전체") activeFilters.push(state.region);
  if (state.topic !== "전체") activeFilters.push(TOPIC_LABELS[state.topic] || state.topic);
  document.getElementById("filterSummary").innerHTML = activeFilters.map(item => `<span>${esc(item)}</span>`).join("");
  document.getElementById("filterCount").textContent = activeFilters.length ? `(${activeFilters.length})` : "";
  // 이 숫자는 세 자리(선두 카드 + 이어지는 이슈 + 오늘의 이슈)의 합계다. 바로 아래
  // 섹션이 '7건'이라고 쓰는데 여기가 '8건'이면 한 화면이 스스로와 어긋나 보인다 —
  // 무엇을 더한 값인지 말해 주면 어긋남이 아니라 내역이 된다.
  // 선두 카드는 이 커밋(8551f68) 이후에 생겼다. 문구는 그때 것을 쓰되 셈은
  // 선두 1건을 포함해야 한다 — 안 그러면 화면에 보이는 카드 수보다 하나 적다.
  document.getElementById("filterSheetCount").textContent = `필터 결과 전체 ${visibleChanged.length + rest.length + (lead ? 1 : 0)}건`;
  const clear = document.getElementById("clearFilters");
  clear.hidden = activeFilters.length === 0;
  clear.textContent = activeFilters.length ? `필터 해제 (${activeFilters.length})` : "필터 해제";
  renderBriefingSidebar(briefing, leadId);
  renderNewsFeed();
  renumberSections("view-news");
}

// ── 오디오 브리핑 플레이어 ──────────────────────────────────
// 음원은 1.0x 원본 하나뿐이고 배속은 여기 playbackRate 가 맡는다.
// audio/audio.json 은 부가 데이터 — 없으면 플레이어가 통째로 숨는다.
const AUDIO_RATES = [1, 1.25, 1.5, 2];

function audioRate() {
  const saved = Number(localStorage.getItem("nuclens-audio-rate"));
  return AUDIO_RATES.includes(saved) ? saved : 1;
}

// 선택지 4개를 전부 펼치고 현재 값만 누른 상태로 — 순환 버튼 하나는
// "조절되는 것"이라는 게 안 읽혔다(사용자 피드백 8/5).
function syncAudioRateButtons() {
  const current = audioRate();
  document.querySelectorAll("#audioRates [data-rate]").forEach(button => {
    button.setAttribute("aria-pressed", Number(button.dataset.rate) === current ? "true" : "false");
  });
}

function fmtClock(value) {
  if (!Number.isFinite(value) || value < 0) return "0:00";
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function updateAudioToggle(playing) {
  const button = document.getElementById("audioToggle");
  if (!button) return;
  button.setAttribute("aria-pressed", playing ? "true" : "false");
  button.textContent = playing ? "⏸ 일시정지" : "▶ 브리핑 듣기";
}

function renderAudioBrief(briefing) {
  const box = document.getElementById("audioBrief");
  if (!box) return;
  const audio = document.getElementById("audioEl");
  const meta = state.audio;
  const show = !!(meta && meta.file && briefing && meta.date === briefing.date);
  box.hidden = !show;
  if (!show) {
    // 다른 날짜로 넘어가면 그 날짜에 없는 오디오가 계속 재생되면 안 된다
    if (audio && !audio.paused) audio.pause();
    return;
  }
  // 파일명이 날짜를 품지만(briefing-<date>.mp3) 같은 날 재생성도 있어
  // generated_at 으로 캐시를 가른다 — manifest 구버전 캐시 사고(8/1)의 교훈.
  const src = `/data/audio/${encodeURIComponent(meta.file)}?v=${encodeURIComponent(meta.generated_at || meta.date)}`;
  if (audio.dataset.src !== src) {
    if (!audio.paused) audio.pause();
    audio.dataset.src = src;
    audio.src = src;
    updateAudioToggle(false);
    // 배속 세그먼트는 재생을 시작해야 펼쳐진다(좁은 화면) — 날짜를 옮기면
    // 새 음원이므로 접힌 상태로 되돌린다.
    box.classList.remove("started");
    document.getElementById("audioTime").textContent =
      `0:00 / ${fmtClock(meta.duration_sec)}`;
  }
  syncAudioRateButtons();
}

function articleCard(article) {
  const url = safeUrl(article.url);
  return `<article class="news-item">
    <div class="news-meta"><span>${isOfficial(article) ? "공식기관" : "언론"}</span><span>${esc(sourceLabel(article))}</span><span>${esc(article.region)}</span></div>
    <h3>${esc(article.title_kr)}</h3>
    ${article.summary ? `<p class="news-summary">${esc(article.summary)}</p>` : ""}
    ${url ? `<a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 확인 <span aria-hidden="true">↗</span></a>` : ""}
  </article>`;
}

function renderNewsFeed() {
  const articles = state.news.filter(article => (
    article.article_date === state.briefingDate
    && (state.region === "전체" || article.region === state.region)
    && (state.topic === "전체" || (article.topics || []).includes(state.topic))
  ));
  document.getElementById("feedLabel").textContent = `오늘 수집한 원문 ${articles.length}건`;
  document.getElementById("feedTitle").textContent = `${dateLabel(state.briefingDate)} 발행`;
  document.getElementById("newsList").innerHTML = articles.length
    ? articles.map(articleCard).join("")
    : '<p class="empty">이 날짜에 발행된 수집 기사가 없습니다.</p>';
}

function archiveIssueMatches(issue) {
  // 엔티티 필터가 맨 앞 — 엔티티 페이지는 "이 대상의 이슈"가 전제고,
  // 나머지 필터(주제·기간·검색어)는 그 안에서의 교집합이다.
  if (state.archiveEntity && !(issue.entity_ids || []).includes(state.archiveEntity)) return false;
  if (state.archiveRegion !== "전체" && !(issue.regions || []).includes(state.archiveRegion)) return false;
  if (state.archiveTopic !== "전체" && !(issue.topics || []).includes(state.archiveTopic)) return false;
  const confirmed = ["official", "corroborated"].includes(verificationState(issue).status);
  if (state.archiveVerification === "verified" && !confirmed) return false;
  if (state.archiveVerification === "unverified" && confirmed) return false;
  if (state.archivePeriod !== "all") {
    const latest = new Date(`${state.meta.latest_briefing_date}T00:00:00+09:00`);
    const updated = new Date(`${issue.last_seen}T00:00:00+09:00`);
    if ((latest - updated) / 86400000 >= Number(state.archivePeriod)) return false;
  }
  if (!state.archiveQuery) return true;
  const articleText = (issue.related_articles || []).map(article => (
    `${article.title_kr || ""} ${article.domain || ""} ${article.publisher || ""}`
  )).join(" ");
  const countryText = (issue.related_articles || []).flatMap(article => article.countries || [])
    .map(country => COUNTRY_LABELS[country] || country).join(" ");
  return normalizedSearch([
    issue.title, issue.summary, issue.implication, issue.why_important, issue.region,
    ...(issue.tags || []), ...(issue.topics || []).map(topic => TOPIC_LABELS[topic] || topic),
    articleText, countryText,
  ].join(" ")).includes(state.archiveQuery);
}

function sortArchiveIssues(issues) {
  const rows = [...issues];
  if (state.archiveSort === "tracked") {
    rows.sort((a, b) => (b.briefing_count || 1) - (a.briefing_count || 1) || String(b.last_seen).localeCompare(String(a.last_seen)));
  } else if (state.archiveSort === "sources") {
    rows.sort((a, b) => (b.article_count || 0) - (a.article_count || 0) || String(b.last_seen).localeCompare(String(a.last_seen)));
  } else {
    rows.sort((a, b) => String(b.last_seen).localeCompare(String(a.last_seen)) || (b.article_count || 0) - (a.article_count || 0));
  }
  return rows;
}

function entityById(entityId) {
  return (state.entities?.entities || []).find(entity => entity.id === entityId) || null;
}

// 탐색이 '검색 결과 화면'에서 '발견을 시작하는 화면'이 되도록, 랜딩(검색어·
// 필터·엔티티가 전부 기본값)에서만 시작점을 깐다. 순서는 엔티티 → 주제 →
// 국가 → 출처(접힘) — 이 화면의 새 용도(대상 추적)가 앞에 선다.
function renderExploreHub() {
  const box = document.getElementById("exploreHub");
  if (!box) return;
  const entityChips = (state.entities?.entities || [])
    .filter(entity => entity.issue_count > 0 || state.follows.has(entity.id))
    .slice(0, 12)
    .map(entity => `<button type="button" class="hub-chip" data-hub-ent="${esc(entity.id)}">
      <small>${esc(ENTITY_TYPE_LABELS[entity.type] || entity.type)}</small>${esc(entity.name_kr)}<b>${entity.issue_count}</b>
    </button>`).join("");
  document.getElementById("hubEntities").innerHTML =
    entityChips || `<p class="empty">${esc(STRINGS.hubEmptyEntities)}</p>`;
}

function renderEntityHeader() {
  const box = document.getElementById("entityHeader");
  if (!box) return;
  if (!state.archiveEntity) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  const entity = entityById(state.archiveEntity);
  if (!entity) {
    box.innerHTML = `<p class="entity-unknown">${esc(STRINGS.entityUnknown)}
      <button type="button" data-clear-entity>${esc(STRINGS.entityClear)}</button></p>`;
    return;
  }
  const connected = state.issues.filter(issue => (issue.entity_ids || []).includes(entity.id));
  // '자주 함께 등장한 주제'는 표본이 3건은 되어야 말할 수 있다 — 1건짜리
  // 엔티티에 주제 셋을 붙이면 그 이슈의 주제를 되풀이하는 장식이 된다.
  let topicLine = "";
  if (connected.length >= 3) {
    const together = new Map();
    connected.forEach(issue => (issue.topics || []).forEach(topic => {
      together.set(topic, (together.get(topic) || 0) + 1);
    }));
    const top = [...together].sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([topic]) => `<button type="button" class="hub-chip" data-hub-topic="${esc(topic)}">${esc(TOPIC_LABELS[topic] || topic)}</button>`);
    if (top.length) topicLine = `<div class="entity-topics"><span>자주 함께 등장한 주제</span>${top.join("")}</div>`;
  }
  const countries = (entity.countries || [])
    .map(code => COUNTRY_LABELS[code] || code).join(" · ");
  const latest = entity.latest_issue_date ? ` · ${STRINGS.recentCapture} ${dateLabel(entity.latest_issue_date)}` : "";
  const following = state.follows.has(entity.id);
  box.innerHTML = `
    <p class="entity-kind">${esc(ENTITY_TYPE_LABELS[entity.type] || entity.type)}${countries ? ` · ${esc(countries)}` : ""}</p>
    <h2 class="entity-name">${esc(entity.name_kr)}${entity.name_en ? ` <span lang="en">${esc(entity.name_en)}</span>` : ""}</h2>
    <p class="entity-stats">이슈 ${connected.length}건 · 근거 기사 ${entity.article_count}건${latest}</p>
    ${topicLine}
    <div class="entity-actions">
      <button type="button" class="follow-button ${following ? "following" : ""}" data-follow-toggle="${esc(entity.id)}" aria-pressed="${following}">${following ? "팔로우 중" : "팔로우"}</button>
      <button type="button" class="text-action" data-clear-entity>${esc(STRINGS.entityClear)}</button>
    </div>`;
  // 이 페이지를 실제로 보고 있을 때만 확인 처리한다 — renderArchiveSearch 는
  // 다른 화면 갱신에도 불려서, 화면 조건 없이 찍으면 배지가 몰래 꺼진다.
  if (following && state.view === "search") markEntitySeen(entity.id);
}

function renderArchiveSearch(resetLimit = false) {
  if (resetLimit) state.archiveLimit = 20;
  const matches = sortArchiveIssues(state.issues.filter(archiveIssueMatches));
  const visible = matches.slice(0, state.archiveLimit);
  // 랜딩(모든 조건이 기본값)에서만 발견 허브를 깐다. 조건이 하나라도 서면
  // 이 화면은 결과 화면이고, 허브는 소음이다.
  const isLanding = !state.archiveQuery && !state.archiveEntity
    && state.archiveRegion === "전체" && state.archiveTopic === "전체"
    && state.archivePeriod === "all" && state.archiveVerification === "전체";
  const hub = document.getElementById("exploreHub");
  if (hub) {
    hub.hidden = !isLanding;
    if (isLanding) renderExploreHub();
  }
  renderEntityHeader();
  const entityName = state.archiveEntity ? (entityById(state.archiveEntity)?.name_kr || state.archiveEntity) : "";
  const activeFilters = [
    entityName,
    state.archiveQuery ? `“${state.archiveQuery}”` : "",
    state.archivePeriod !== "all" ? `최근 ${state.archivePeriod}일` : "",
    state.archiveRegion !== "전체" ? state.archiveRegion : "",
    state.archiveTopic !== "전체" ? TOPIC_LABELS[state.archiveTopic] || state.archiveTopic : "",
    state.archiveVerification === "verified" ? "공식·복수 출처 확인" : state.archiveVerification === "unverified" ? "단일 출처·확인 중" : "",
  ].filter(Boolean);
  const matchedArticles = matches.reduce((sum, issue) => sum + (issue.article_count || 0), 0);
  const scale = `${matches.length}개 이슈 · ${matchedArticles}개 원문`;
  document.getElementById("archiveSummary").textContent = activeFilters.length
    ? `${activeFilters.join(" · ")} — ${scale}`
    : scale;
  document.getElementById("archiveQueryDisplay").textContent = state.archiveQuery ? `검색어 · ${state.archiveQuery}` : "검색어 없음";
  document.getElementById("archiveIssueList").innerHTML = visible.length
    ? visible.map((issue, index) => issueCard(issue, index, true)).join("")
    : '<div class="empty-state"><strong>조건에 맞는 이슈가 없습니다</strong><p>기간을 30일로 넓히거나 주제 필터를 해제해 보세요.</p><button type="button" data-clear-archive>필터 해제</button></div>';
  const more = document.getElementById("archiveMore");
  more.hidden = visible.length >= matches.length;
  more.textContent = more.hidden ? "더 보기" : `더 보기 · ${matches.length - visible.length}개 남음`;
  const clear = document.getElementById("archiveClear");
  clear.hidden = activeFilters.length === 0;
  clear.textContent = activeFilters.length ? `필터 해제 (${activeFilters.length})` : "필터 해제";
  document.getElementById("archiveSheetCount").textContent = `${matches.length}개 이슈`;
  // 접힌 서랍 안에 무엇이 걸려 있는지 열지 않고도 알아야 한다.
  const count = document.getElementById("archiveFilterCount");
  if (count) {
    count.textContent = activeFilters.length ? String(activeFilters.length) : "";
    count.hidden = activeFilters.length === 0;
  }
  const summary = document.querySelector("#archiveFilterDrawer > summary");
  if (summary) summary.setAttribute("aria-label", activeFilters.length ? `탐색 필터 ${activeFilters.length}개 적용됨` : "탐색 필터");
}

function renderSaved() {
  renderFollowPanel();
  renderRecentIssues();
  const issues = state.issues.filter(issue => state.savedIds.has(issue.issue_id));
  const liveIds = new Set(issues.map(issue => issue.issue_id));
  // 재클러스터로 사라진 저장 — 스냅샷 묘비로 남긴다(조용한 소실 금지).
  const tombstones = [...state.savedIds]
    .filter(id => !liveIds.has(id))
    .map(id => savedTombstone(id, state.savedMeta?.[id]));
  const cards = issues.map((issue, index) => issueCard(issue, index, true)).concat(tombstones);
  document.getElementById("savedIssueList").innerHTML = cards.length
    ? cards.join("")
    : '<div class="empty-state"><strong>저장한 이슈가 없습니다</strong><p>카드의 저장 버튼을 누르면 이 브라우저에서 다시 볼 수 있습니다.</p><button type="button" data-go-view="search">탐색에서 보기</button></div>';
}

function renderReportCandidates() {
  const box = document.getElementById("reportCandidateList");
  if (!box) return;
  const rows = state.issues.filter(issue => (issue.report_pick || "").trim()).slice(0, 6);
  box.innerHTML = rows.length ? rows.map(issue => {
    const reasons = (issue.report_pick_angles || issue.selection_reasons || []).slice(0, 3);
    const why = issue.report_pick_why || issue.why_important || issue.implication || "";
    return `<article class="report-candidate">
      <p class="report-candidate-topic">${esc(issue.report_pick)}</p>
      <h3><button type="button" data-issue-id="${esc(issue.issue_id)}">${esc(issue.title)}</button></h3>
      ${why ? `<p>${esc(why)}</p>` : ""}
      ${reasons.length ? `<div class="report-angle-row">${reasons.map(reason => `<span class="topic-chip">${esc(reason)}</span>`).join("")}</div>` : ""}
      <button type="button" class="secondary-button" data-pack-issue="${esc(issue.issue_id)}">보고서 자료팩 복사</button>
    </article>`;
  }).join("") : '<div class="empty-state"><strong>이번 주 보고 후보가 없습니다</strong><p>보고 후보로 분류된 이슈가 생기면 근거 자료와 함께 표시합니다.</p></div>';
}

const PUB_KIND_LABELS = {
  publication: "간행물", report: "보고서", analysis: "분석", press: "보도자료",
  news_or_report: "소식·보고서", keei_insight: "정기간행물",
};

// 기관별 표지 스파인 클래스. 색은 잠금 팔레트의 차트 토큰만 재사용한다 —
// **장식이지 의미 체계가 아니다**(범례 없음). 기관을 외워 읽으라는 색이 아니라
// 서가에서 같은 기관 발간물이 한 무리로 보이게 하는 색이다.
const PUB_ORG_CLASS = {
  "IAEA": "org-iaea", "OECD-NEA": "org-nea", "OECD NEA": "org-nea",
  "KEEI": "org-keei", "EIA": "org-eia", "IEA": "org-iea",
};

// 표지 오브젝트 — 이미지 없는 발간물을 타이포그래피 표지로 세운다(CSS-only,
// WebGL·이미지 0). .pub-item 클래스는 렌더 스모크가 세므로 유지한다.
function pubRow(item) {
  const url = safeUrl(item.url);
  const pdfUrl = safeUrl(item.pdf_url || "");
  const kindLabel = PUB_KIND_LABELS[item.kind] || "";
  const tocIssue = item.toc && item.toc.issue_title ? item.toc.issue_title : "";
  // 한국어 제목이 있으면 그것이 표제다. 영문 원제는 아래에 작게 남겨
  // 원문을 찾을 때 대조할 수 있게 한다.
  const heading = item.title_kr || item.title;
  const original = item.title_kr && item.title_kr !== item.title ? item.title : "";
  const orgClass = PUB_ORG_CLASS[item.org] || "org-etc";
  const face = `
    <p class="cover-org">${esc(item.org_kr || item.org)}</p>
    <h3>${esc(heading)}</h3>
    ${item.gist ? `<p class="cover-gist">${esc(item.gist)}</p>` : ""}
    <p class="cover-foot">
      ${kindLabel ? `<span>${esc(kindLabel)}</span>` : ""}
      ${item.date ? `<span>${esc(dateLabel(item.date))}</span>` : ""}
      ${item.is_new ? `<span class="cover-new" aria-label="최근 14일 이내 발간"><i aria-hidden="true"></i>최근 발간</span>` : ""}
    </p>`;
  return `<article class="pub-item pub-cover ${orgClass}">
    ${url
      ? `<a class="cover-face" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${face}</a>`
      : `<div class="cover-face">${face}</div>`}
    ${original ? `<p class="pub-original" lang="en">${esc(original)}</p>` : ""}
    ${tocIssue ? `<p class="pub-toc">현안이슈: ${esc(tocIssue)}</p>` : ""}
    ${pdfUrl ? `<a class="source-link" href="${esc(pdfUrl)}" target="_blank" rel="noopener noreferrer">PDF 원문 <span aria-hidden="true">↗</span></a>` : ""}
  </article>`;
}

function renderPubs() {
  const listBox = document.getElementById("pubsList");
  const filterBox = document.getElementById("pubsFilters");
  if (!listBox || !filterBox) return;
  // 렌더러는 데이터를 신뢰하지 않는다. 배열 안에 null·문자열이 섞이면
  // item.org_kr 에서 TypeError 가 나고 탭이 통째로 멈춘다(실측). 빌드가
  // 걸러 주더라도 여기서 한 번 더 막는다 — 화면이 죽는 사고의 단골 경로다.
  const raw = (state.pubs && Array.isArray(state.pubs.items)) ? state.pubs.items : [];
  const items = raw
    .filter(item => item && typeof item === "object" && item.title && item.url)
    // date 가 숫자면 dateLabel 의 value.split 에서 죽는다 — 타입도 신뢰하지 않는다
    .map(item => (typeof item.date === "string" ? item : { ...item, date: String(item.date ?? "") }));
  if (!items.length) {
    filterBox.innerHTML = "";
    listBox.innerHTML = '<div class="empty-state"><strong>아직 수집된 발간물이 없습니다</strong><p>매일 새벽 IAEA·OECD NEA·IEA·EIA의 신규 발간물을 확인합니다.</p></div>';
    return;
  }
  const orgs = ["전체", ...new Set(items.map(item => item.org_kr || item.org).filter(Boolean))];
  if (!orgs.includes(state.pubsOrg)) state.pubsOrg = "전체";
  // 목록이 그대로면 버튼 DOM 을 다시 만들지 않는다. innerHTML 로 갈아끼우면
  // 방금 누른 버튼이 사라져 포커스가 <body> 로 날아가고, 키보드·스크린리더
  // 사용자는 필터를 고를 때마다 페이지 맨 위로 되돌아간다. 다른 필터 그룹은
  // 모두 setPressed 로 class/aria 만 갱신한다 — 같은 방식으로 맞춘다.
  const rendered = [...filterBox.querySelectorAll("button")].map(button => button.dataset.pubsOrg);
  if (rendered.join(" ") !== orgs.join(" ")) {
    filterBox.innerHTML = orgs.map(org =>
      `<button type="button" data-pubs-org="${esc(org)}">${esc(org)}</button>`
    ).join("");
  }
  // 기관명은 스크레이핑 데이터라 따옴표가 섞일 수 있다 — 셀렉터 문자열 대신 순회로 찾는다
  const activeButton = [...filterBox.querySelectorAll("button")]
    .find(button => button.dataset.pubsOrg === state.pubsOrg);
  setPressed(filterBox, activeButton);
  const visible = state.pubsOrg === "전체"
    ? items
    : items.filter(item => (item.org_kr || item.org) === state.pubsOrg);
  if (!visible.length) {
    listBox.innerHTML = '<div class="empty-state"><strong>이 기관의 발간물이 아직 없습니다</strong><p>다른 기관을 선택해 보세요.</p></div>';
    return;
  }
  // 정책·시장 자료를 먼저 세우고 연구 실무자용 기술문서는 접는다. 실측
  // 2026-08-05: off_topic 을 통과한 19건 중 12건이 전산유체역학 코드 검증·붕괴열
  // 시뮬레이션·흑연 조사 크리프 같은 기술문서였고, 그것이 서가 앞줄을 차지해
  // 정책 자료가 안 보였다. 지우지는 않는다 — 원자력 문서가 맞고, 찾는 사람이 있다.
  const technical = visible.filter(item => item.relevance === "technical");
  const primary = visible.filter(item => item.relevance !== "technical");
  const shelf = primary.length
    ? primary.map(pubRow).join("")
    : '<div class="empty-state"><strong>이 기관의 정책·시장 자료가 아직 없습니다</strong><p>아래 기술문서를 펼쳐 보세요.</p></div>';
  listBox.innerHTML = shelf + (technical.length
    ? `<details class="pub-technical">
         <summary>기술문서 ${technical.length}건 — 연구·설계 실무용</summary>
         <div class="pub-technical-shelf">${technical.map(pubRow).join("")}</div>
       </details>`
    : "");
}

function articleTimelineRow(article, briefingDate, currentStage = "이번 브리핑", shownDetail = "") {
  const url = safeUrl(article.url);
  const stage = article.briefing_date === briefingDate ? currentStage : "이전 흐름";
  // 원문 대신 읽는 기사 내용. 위 '기사 내용' 블록이 이미 보여 준 문장은 건너뛴다 —
  // 같은 문단을 한 화면에 두 번 두면 정보가 아니라 소음이다.
  const detail = String(article.detail || "").trim();
  const body = detail && detail !== shownDetail
    ? `<details class="timeline-detail"><summary>내용 보기</summary><p>${esc(detail)}</p></details>`
    : "";
  return `<li>
    <div class="timeline-date"><span>${esc(dateLabel(article.article_date))}</span><small>${esc(relativeArticleDate(article.article_date, briefingDate))}</small><em>${stage}</em></div>
    <div class="timeline-copy">
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(article.title_kr)}</a>` : `<span>${esc(article.title_kr)}</span>`}
      <small>${esc(sourceLabel(article))}${isOfficial(article) ? " · 1차 출처" : ""}</small>
      ${body}
    </div>
  </li>`;
}

function currentIssueById(issueId) {
  if (state.view === "news") return currentBriefing()?.issues.find(issue => issue.issue_id === issueId) || state.issues.find(issue => issue.issue_id === issueId) || null;
  return state.issues.find(issue => issue.issue_id === issueId) || null;
}

// 같은 주제·태그를 공유하는 다른 이슈. 상세를 막다른 길로 두지 않기 위한 출구다.
function relatedIssues(issue, limit = 3) {
  const topics = new Set(issue.topics || []);
  const tags = new Set(issue.tags || []);
  if (!topics.size && !tags.size) return [];
  return state.issues
    .filter(other => other.issue_id !== issue.issue_id)
    .map(other => {
      const topicHits = (other.topics || []).filter(topic => topics.has(topic)).length;
      const tagHits = (other.tags || []).filter(tag => tags.has(tag)).length;
      return { issue: other, score: topicHits * 2 + tagHits };
    })
    .filter(row => row.score > 0)
    .sort((a, b) => b.score - a.score || String(b.issue.last_seen).localeCompare(String(a.issue.last_seen)))
    .slice(0, limit)
    .map(row => row.issue);
}

function issueReportText(issue) {
  const representative = issue.representative_article || {};
  const source = [sourceLabel(representative), safeUrl(representative.url)].filter(Boolean).join(" · ");
  return [
    `• 이슈: ${issue.title || ""}`,
    issue.summary ? `• 핵심: ${issue.summary}` : "",
    issueChangeText(issue) ? `• 변화: ${issueChangeText(issue)}` : "",
    issue.why_important ? `• 왜 중요(AI 해석): ${issue.why_important}` : "",
    issue.implication ? `• 시사점(AI 해석): ${issue.implication}` : "",
    issue.open_question ? `• 미확정: ${issue.open_question}` : "",
    `• 검증: ${(VERIFICATION_VIEW[verificationState(issue).status] || VERIFICATION_VIEW.unverified).label} — ${issueEvidenceText(issue)}`,
    source ? `• 근거: ${source}` : "",
  ].filter(Boolean).join("\n");
}

// 동향분석 보고서 초안을 쓸 때 필요한 재료를 한 번에 옮긴다. '보고서용 복사'가
// 카드 한 장짜리 요약이라면 이건 타임라인·출처·수치까지 담은 원재료다.
// AI 해석은 넣지 않는다 — 초안은 사람이 쓰고, 근거만 가져간다.
const NUMBER_RE = /\d/;

function issueMaterialPack(issue) {
  const lines = [`# ${issue.title || ""}`, ""];
  const meta = [
    issue.region ? `지역: ${issue.region}` : "",
    issue.first_seen ? `최초 확인: ${dateLabel(issue.first_seen)}` : "",
    issue.last_seen ? `최근 확인: ${dateLabel(issue.last_seen)}` : "",
    `근거 기사: ${issue.article_count || 0}건`,
  ].filter(Boolean);
  lines.push(meta.join(" · "), "");

  if (issue.summary) lines.push("## 한 줄 결론", issue.summary, "");
  if (issueChangeText(issue)) lines.push("## 이번에 달라진 점", issueChangeText(issue), "");

  const state = verificationState(issue);
  lines.push("## 검증 상태",
    `${(VERIFICATION_VIEW[state.status] || VERIFICATION_VIEW.unverified).label} — ${issueEvidenceText(issue)}`, "");

  const articles = [...(issue.related_articles || [])].sort((a, b) =>
    String(a.article_date).localeCompare(String(b.article_date)));
  if (articles.length) {
    lines.push("## 사건 타임라인");
    articles.forEach(article => {
      const marks = [sourceLabel(article), isOfficial(article) ? "1차 출처" : ""].filter(Boolean).join(" · ");
      lines.push(`- ${dateLabel(article.article_date)} · ${article.title_kr || ""} (${marks})`);
      const url = safeUrl(article.url);
      if (url) lines.push(`  ${url}`);
    });
    lines.push("");
  }

  // 수치가 든 문장만 따로 모은다 — 보고서에서 가장 먼저 필요한 재료다
  const figures = [];
  articles.forEach(article => {
    String(article.summary || "").split(/(?<=[.!?])\s+/).forEach(sentence => {
      const text = sentence.trim();
      if (text && NUMBER_RE.test(text) && !figures.includes(text)) figures.push(text);
    });
  });
  if (figures.length) {
    lines.push("## 수치·일정", ...figures.slice(0, 12).map(text => `- ${text}`), "");
  }

  const refs = (issue.keei_refs || []).filter(ref => ref && ref.url);
  if (refs.length) {
    lines.push("## 관련 발간물");
    refs.forEach(ref => {
      lines.push(`- ${ref.org_kr || ""} ${ref.title || ""}${ref.item ? ` — ${ref.item}` : ""}`);
      const url = safeUrl(ref.url);
      if (url) lines.push(`  ${url}`);  // 거부된 URL 이면 공백뿐인 줄이 남는다
    });
    lines.push("");
  }
  lines.push(`출처: Nuclens ${location.origin}${issuePath(issue.issue_id)}`);
  return lines.join("\n");
}

async function copyToClipboard(button, text, failMessage) {
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = "복사됨";
    window.setTimeout(() => { button.textContent = original; }, 1600);
  } catch {
    showToast(failMessage);
  }
}

async function copyIssueReport(button, issueId) {
  const issue = currentIssueById(issueId);
  if (!issue) return;
  await copyToClipboard(button, issueReportText(issue), "보고서용 텍스트를 복사하지 못했습니다");
}

async function copyIssuePack(button, issueId) {
  const issue = currentIssueById(issueId);
  if (!issue) return;
  await copyToClipboard(button, issueMaterialPack(issue), "자료 팩을 복사하지 못했습니다");
}

// '해석과 한계' 문장은 지웠다. 검증 상태를 산문으로 되풀이할 뿐이라 바로 위
// 배지("단일 출처"·"공식 확인")와 같은 말이었고, 뒤에 붙던 "확정된 사실로 읽지
// 마세요" 같은 훈수는 화면이 할 말이 아니다. 배지가 상태를 말하고, 근거 목록이
// 출처를 보여준다 — 그 사이에 설명문이 낄 자리는 없다.
//
// '변화' 블록도 같은 이유로 뺐다(2026-08-08). 카드가 '달라진 것' 을 이미 세우고
// 있어서 같은 문장이 30cm 떨어져 두 번 섰다. 패널이 담당하는 것은 근거·검증·출처다.

function renderEvidenceRail() {
  const rail = document.getElementById("evidenceRail");
  if (!rail) return;
  const issue = state.railIssueId ? currentIssueById(state.railIssueId) : null;
  if (!issue) { rail.hidden = true; rail.innerHTML = ""; return; }
  const model = issueDetailModel(issue, state.briefingDate);
  const sourceArticle = model.source.article;
  const selectionReasons = (issue.selection_reasons || [])
    .filter(reason => String(reason || "").trim())
    .map(reason => `<span class="topic-chip">${esc(reason)}</span>`).join("");
  const sourceUrl = sourceArticle ? safeUrl(sourceArticle.url) : "";
  // 블록이 조건부라 번호를 하드코딩하면 01 다음에 03 이 온다. 남은 것만 세어 붙인다.
  let railNo = 0;
  const no = () => String(++railNo).padStart(2, "0");
  const readingBlock = model.why || model.impact || model.openQuestion
    ? `<section class="rail-block">
        <p class="rail-no">${no()} / 읽을 때</p>
        ${model.why ? `<p class="rail-impact"><strong>${esc(model.why.label)} <span class="ai-badge">AI</span></strong>${esc(model.why.text)}</p>` : ""}
        ${model.impact ? `<p class="rail-impact"><strong>${esc(model.impact.label)} <span class="ai-badge">AI</span></strong>${esc(model.impact.text)}</p>` : ""}
        ${model.openQuestion ? `<p class="rail-open"><strong>아직 확정되지 않은 것</strong>${esc(model.openQuestion)}</p>` : ""}
      </section>`
    : "";
  rail.hidden = false;
  rail.innerHTML = `
    <div class="rail-head">
      <p class="rail-kicker">${esc(model.source.label)}${model.media ? ` · ${esc(model.media.label)}` : ""}</p>
      <h2>${esc(issue.title)}</h2>
      <p class="rail-badges">${verificationBadge(issue, { always: true })}${reportPickBadge(issue)}<span>${esc(model.evidenceText)}</span></p>
      ${selectionReasons ? `<div class="topic-row rail-reasons">${selectionReasons}</div>` : ""}
    </div>
    <div class="rail-body">
      ${readingBlock}
      <section class="rail-block">
        <p class="rail-no">${no()} / 핵심 근거</p>
        <ol class="rail-sources">${model.articles.slice(0, 4).map(article => {
          const url = safeUrl(article.url);
          return `<li>
            ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(article.title_kr)}</a>`
                  : `<span>${esc(article.title_kr)}</span>`}
            <small>${esc(sourceLabel(article))}${isOfficial(article) ? " · 1차 출처" : ""}</small>
          </li>`;
        }).join("")}</ol>
        ${sourceUrl && model.source.official ? `<p class="rail-primary"><a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">공식 문서 열기 ↗</a></p>` : ""}
      </section>
      <div class="rail-actions">
        <button type="button" data-issue-id="${esc(issue.issue_id)}" data-force-dialog="1">전체 상세</button>
        <button type="button" data-save-issue="${esc(issue.issue_id)}">${state.savedIds.has(issue.issue_id) ? "저장됨" : "저장"}</button>
      </div>
    </div>`;
}

function openIssueDialog(issueId, updateUrl = true) {
  const issue = currentIssueById(issueId);
  if (!issue) return;
  recordRecentIssue(issueId);
  const dialog = document.getElementById("issueDialog");
  const topics = (issue.topics || []).map(topic => `<span class="topic-chip">${esc(TOPIC_LABELS[topic] || topic)}</span>`).join("");
  const selectionReasons = (issue.selection_reasons || [])
    .filter(reason => String(reason || "").trim())
    .map(reason => `<span class="topic-chip">${esc(reason)}</span>`).join("");
  const contextDate = state.view === "news" ? state.briefingDate : issue.last_seen;
  const articles = [...(issue.related_articles || [])].sort((a, b) => (
    Number(isOfficial(b)) - Number(isOfficial(a)) || String(b.article_date).localeCompare(String(a.article_date))
  ));
  const related = relatedIssues(issue);
  // 원문(대개 영문)에 들어가지 않고도 읽히도록 만든 기사 요지. 2026-08-07 이전
  // 아카이브에는 없으므로 빈 값이 정상이고, 그때는 이 블록이 통째로 빠진다.
  const issueDetail = String(issue.detail || "").trim();
  document.getElementById("issueDialogContent").innerHTML = `
    <h2 id="issueDialogTitle" tabindex="-1">${esc(issue.title)}</h2>
    <div class="dialog-meta"><span>${esc(issueStatusText(issue, state.view !== "news"))}</span><span>${dateLabel(issue.first_seen)} 시작</span><span>누적 ${issue.article_count}건</span></div>
    <section class="dialog-update" aria-labelledby="issueUpdateTitle">
      <h3 id="issueUpdateTitle">한 줄 결론</h3>
      ${issue.summary ? `<p>${esc(issue.summary)}</p>` : '<p class="empty">요약이 없습니다.</p>'}
      ${issueDetail ? `<div class="dialog-detail"><strong>기사 내용</strong><p>${esc(issueDetail)}</p>${issue.detail_source ? `<small>${esc(issue.detail_source)} 기준</small>` : ""}</div>` : ""}
      ${issueChangeText(issue) ? `<p class="dialog-change"><strong>이번에 달라진 점</strong>${esc(issueChangeText(issue))}</p>` : ""}
      <p class="dialog-verification">${verificationBadge(issue, { always: true })}${reportPickBadge(issue)}<span>${esc(issueEvidenceText(issue))}</span></p>
      ${issue.why_important ? `<p class="dialog-meaning"><strong>왜 중요한가 <span class="ai-badge">AI</span></strong>${esc(issue.why_important)}</p>` : ""}
      ${issue.implication ? `<p class="dialog-meaning"><strong>시사점 <span class="ai-badge">AI</span></strong>${esc(issue.implication)}</p>` : ""}
      ${issue.open_question ? `<p class="dialog-open"><strong>아직 확정되지 않은 것</strong>${esc(issue.open_question)}</p>` : ""}
      ${selectionReasons ? `<div class="topic-row dialog-reasons" aria-label="선정 사유">${selectionReasons}</div>` : ""}
      ${topics ? `<div class="topic-row">${topics}</div>` : ""}
      <div class="dialog-actions"><button type="button" data-copy-issue="${esc(issue.issue_id)}">보고서용 복사</button><button type="button" data-pack-issue="${esc(issue.issue_id)}">자료 팩 복사</button><button type="button" data-save-issue="${esc(issue.issue_id)}">${state.savedIds.has(issue.issue_id) ? "저장됨" : "저장"}</button><button type="button" data-share-issue="${esc(issue.issue_id)}">공유</button></div>
    </section>
    ${keeiDialogSection(issue)}
    <section class="dialog-history" aria-labelledby="issueHistoryTitle">
      <div class="dialog-section-head"><h3 id="issueHistoryTitle">사건 타임라인과 근거 원문</h3></div>
      <ol class="timeline dialog-timeline">${articles.map(article => articleTimelineRow(article, contextDate, state.view === "news" ? "이번 브리핑" : "최근 브리핑", issueDetail)).join("")}</ol>
    </section>
    ${related.length ? `<section class="dialog-related" aria-labelledby="issueRelatedTitle">
      <div class="dialog-section-head"><h3 id="issueRelatedTitle">관련 이슈</h3><span>같은 주제로 연결된 이슈입니다</span></div>
      <ul>${related.map(item => `<li>
        <button type="button" data-issue-id="${esc(item.issue_id)}">${esc(item.title)}</button>
        <small>${esc(dateLabel(item.last_seen))} · 근거 ${item.article_count || 0}건</small>
      </li>`).join("")}</ul>
    </section>` : ""}`;
  state.issueId = issueId;
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => document.getElementById("issueDialogTitle")?.focus());
  if (updateUrl) {
    const currentIssue = issueIdFromLocation() || new URLSearchParams(location.search).get("issue") || "";
    if (currentIssue !== issueId) {
      issueHistoryOwned = true;
      syncUrl("push");
    } else syncUrl();
  }
}

function dismissIssueDialog() {
  const dialog = document.getElementById("issueDialog");
  state.issueId = "";
  if (dialog.open) dialog.close();
}

function closeIssueDialog(useHistory = true) {
  if (useHistory && issueHistoryOwned) {
    issueHistoryOwned = false;
    history.back();
    return;
  }
  issueHistoryOwned = false;
  dismissIssueDialog();
  syncUrl();
}

function restoreIssueFromHistory() {
  const requestedIssue = issueIdFromLocation() || new URLSearchParams(location.search).get("issue") || "";
  if (requestedIssue && state.view !== "trend") {
    issueHistoryOwned = true;
    openIssueDialog(requestedIssue, false);
  } else {
    issueHistoryOwned = false;
    dismissIssueDialog();
  }
}

// 이번 주 움직인 이슈. 예전에는 **키워드마다** 흐름 해석을 한 편씩 만들었는데,
// 한 사건이 키워드를 여럿 달고 있으면 같은 이야기가 그 수만큼 재포장됐다
// (실측: 헝가리 가뭄 원전 중단 하나가 기후변화·원전운영·전력시장·에너지안보
// 네 흐름에 동시 등장). 이슈는 이미 사건 단위라 중복이 생기지 않는다.
// 수명 축의 공유 범위. meta 의 수집 구간을 그대로 쓴다 — 축을 이슈들의 최소·최대로
// 잡으면 날마다 축이 늘었다 줄었다 해서 어제와 오늘의 막대 길이를 못 비겨 본다.
function flowSpanRange() {
  const start = state.meta?.date_min || "";
  const end = state.meta?.date_max || state.meta?.latest_briefing_date || "";
  if (!start || !end || start >= end) return null;
  // 축 계산은 전부 UTC 자정으로 파싱한다. 여기서 쓰는 값은 차이와 비율뿐이라
  // 기준시가 무엇이든 결과가 같고, KST(+09:00)로 파싱해 두면 눈금을 찍을 때
  // getUTC* 가 9시간 앞선 날짜를 돌려줘 라벨이 하루씩 밀린다(실측: 7/17 → 7/16).
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return null;
  return { start, end, startMs, span: endMs - startMs };
}

function flowSpanTrack(item, range) {
  if (!range || !item.first_seen) return "";
  const firstMs = Date.parse(`${item.first_seen}T00:00:00Z`);
  const lastMs = Date.parse(`${item.last_seen || item.first_seen}T00:00:00Z`);
  if (!Number.isFinite(firstMs) || !Number.isFinite(lastMs)) return "";
  const clamp = value => Math.max(0, Math.min(100, value));
  const left = clamp((firstMs - range.startMs) / range.span * 100);
  const right = clamp((Math.max(lastMs, firstMs) - range.startMs) / range.span * 100);
  const days = Math.round((Math.max(lastMs, firstMs) - firstMs) / 86400000) + 1;
  // 하루짜리는 폭이 0 이라 선이 안 보인다 — 점으로 그려 '오늘 처음'을 형태로 구분한다.
  const seed = days <= 1;
  const width = seed ? 0 : right - left;
  return `<div class="flow-span"><span class="flow-span-track"><i class="${seed ? "seed" : ""}"
    style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"></i></span><span class="flow-span-age">${
    seed ? "오늘 시작" : `${days}일째`}</span></div>`;
}

function renderFlowAxis(range) {
  const axis = document.getElementById("flowAxis");
  if (!axis) return;
  axis.hidden = !range;
  if (!range) return;
  const ticks = [0, 0.5, 1].map(fraction => {
    const at = new Date(range.startMs + range.span * fraction);
    const label = `${at.getUTCMonth() + 1}/${at.getUTCDate()}`;
    return `<span style="left:${(fraction * 100).toFixed(2)}%">${esc(label)}</span>`;
  }).join("");
  axis.innerHTML = `<span class="flow-axis-label">추적 구간</span><span class="flow-axis-scale">${ticks}</span>`;
}

function renderInsights() {
  const box = document.getElementById("insightList");
  const movers = (state.trend?.weekly_movers || []).filter(item => item && item.title);
  const range = flowSpanRange();
  renderFlowAxis(movers.length ? range : null);
  if (!movers.length) {
    box.innerHTML = '<div class="empty-state"><strong>이번 주 움직인 이슈를 준비하고 있습니다</strong><p>보도가 쌓이면 근거와 함께 표시합니다.</p></div>';
    return;
  }
  box.innerHTML = movers.map((item, index) => {
    const scale = [
      `원문 ${item.week_article_count}건`,
      item.week_days > 1 ? `${item.week_days}일간 보도` : "하루 보도",
      item.publisher_count > 1 ? `매체 ${item.publisher_count}곳` : "단일 매체",
    ].join(" · ");
    const topics = (item.topics || []).slice(0, 3)
      .map(topic => `<span>${esc(TOPIC_LABELS[topic] || topic)}</span>`).join("");
    const events = (item.events || []).map(event => {
      const url = safeUrl(event.url);
      const title = url
        ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(event.title)}</a>`
        : esc(event.title);
      return `<li><time>${esc(dateLabel(event.date))}</time><span>${title}</span></li>`;
    }).join("");
    return `<article class="flow-item">
      <div class="flow-rank">${String(index + 1).padStart(2, "0")}</div>
      <div class="flow-copy">
        <div class="flow-head">
          <h3><button type="button" class="issue-title-button" data-issue-id="${esc(item.issue_id)}">${esc(item.title)}</button></h3>
          <span>${esc(scale)}</span>
        </div>
        ${flowSpanTrack(item, range)}
        <p class="flow-keyword"><span>${esc(item.region || "지역 미분류")}</span><span>${item.is_continuing ? "이어지는 이슈" : "이번 주 신규"}</span>${topics}</p>
        ${item.summary ? `<p class="flow-summary">${esc(item.summary)}</p>` : ""}
        ${events ? `<div class="event-block"><strong>구성 사건</strong><ul>${events}</ul></div>` : ""}
      </div>
    </article>`;
  }).join("");
}

function trendRange() {
  const days = Number(state.period) || 7;
  const end = state.meta?.date_max || state.meta?.latest_briefing_date || "";
  const parsedEnd = new Date(`${end}T00:00:00+09:00`);
  const requestedStart = new Date(parsedEnd.getTime() - (days - 1) * 86400000).toISOString().slice(0, 10);
  const start = state.meta?.date_min && state.meta.date_min > requestedStart ? state.meta.date_min : requestedStart;
  return { start, end };
}

function renderTrendReadiness() {
  const ready = Boolean(state.meta?.trend_ready);
  const topicCoverage = Math.round((state.meta?.topic_coverage || 0) * 100);
  const countryCoverage = Math.round((state.meta?.country_coverage || 0) * 100);
  const { start, end } = trendRange();
  const articleCount = state.news.filter(article => article.article_date >= start && article.article_date <= end).length;
  const issueCount = state.issues.filter(issue => (issue.related_articles || []).some(article => article.article_date >= start && article.article_date <= end)).length;
  const panel = document.getElementById("trendReadiness");
  document.getElementById("trendData").hidden = !ready;
  panel.classList.toggle("ready", ready);
  panel.innerHTML = ready
    ? `<div><strong>분석 기간 ${dateLabel(start)}–${dateLabel(end)}</strong><p>중복 제거 적용 · 원본 ${articleCount}건 → 연결 이슈 ${issueCount}개</p></div><div class="coverage"><span>주제 분류 <strong>${topicCoverage}%</strong></span><span>국가 분류 <strong>${countryCoverage}%</strong></span></div>`
    : `<div><strong>분류 기준을 확인하고 있습니다</strong><p>분류가 완료되면 분석 기간과 근거 데이터를 함께 표시합니다.</p></div><div class="coverage"><span>주제 분류 <strong>${topicCoverage}%</strong></span><span>국가 분류 <strong>${countryCoverage}%</strong></span></div>`;
}

function keywordRows() {
  const top = new Map((state.trend?.top_tags_7d || []).map(row => [row.tag, row.count]));
  const rising = new Map((state.trend?.rising || []).map(row => [row.tag, row]));
  const newTags = new Set((state.trend?.new_tags || []).map(row => row.tag));
  const tags = new Set([...top.keys(), ...rising.keys(), ...newTags]);
  return [...tags].map(tag => {
    const rise = rising.get(tag);
    const now = top.get(tag) ?? rise?.now ?? 0;
    const prev = rise?.prev ?? (newTags.has(tag) ? 0 : now);
    return { tag, now, prev, delta: now - prev, isNew: newTags.has(tag) || (now > 0 && prev === 0) };
    // 0·0 행은 표에 앉을 자격이 없다 — 실측: 수집 키워드가 new_tags 에 올라오고
    // 언급이 0이면 '한수원 0 0 0 신규' 같은 행이 근거 0건 버튼까지 달고 섰다.
  }).filter(row => row.now > 0 || row.prev > 0);
}

function renderKeywordTable() {
  let rows = keywordRows();
  if (state.keywordSort === "new") rows = rows.filter(row => row.isNew);
  rows.sort((a, b) => state.keywordSort === "change"
    ? b.delta - a.delta || b.now - a.now
    : b.now - a.now || b.delta - a.delta);
  rows = rows.slice(0, 12);
  document.getElementById("keywordTable").innerHTML = rows.length ? `
    <div class="keyword-row keyword-head" aria-hidden="true"><span>키워드</span><span>이번 주</span><span>전주</span><span>변화</span><span>상태</span><span></span></div>
    ${rows.map(row => `<div class="keyword-row"><strong>${esc(row.tag)}</strong><span>${row.now}</span><span>${row.prev}</span><span class="${row.delta > 0 ? "positive" : row.delta < 0 ? "negative" : ""}">${row.delta > 0 ? "+" : row.delta < 0 ? "−" : ""}${Math.abs(row.delta)}</span><span>${row.isNew ? "신규" : row.delta >= 3 ? "늘어남" : "이어짐"}</span><button type="button" data-keyword="${esc(row.tag)}">근거 ${row.now}건 →</button></div>`).join("")}`
    : '<p class="empty">조건에 맞는 키워드가 없습니다.</p>';
  const strongest = [...keywordRows()].sort((a, b) => b.delta - a.delta)[0];
  document.getElementById("keywordInterpretation").textContent = strongest
    ? `${strongest.tag}이(가) 전주보다 ${Math.abs(strongest.delta)}건 늘어 이번 주 변화가 가장 컸습니다.`
    : "비교할 키워드가 아직 충분하지 않습니다.";
  document.getElementById("keywordEvidence").innerHTML = rows.map(row => `<p><strong>${esc(row.tag)}</strong> · 이번 주 ${row.now}건 · 전주 ${row.prev}건</p>`).join("");
}

function bars(element, rows, labelFn) {
  if (!rows?.length) {
    element.innerHTML = '<p class="empty">아직 데이터가 충분하지 않습니다.</p>';
    return;
  }
  const max = Math.max(...rows.map(row => row.count));
  element.innerHTML = rows.map(row => `<div class="bar-row"><span class="bar-name">${esc(labelFn(row))}</span><div class="bar-track"><span style="width:${Math.max(3, Math.round(row.count / max * 100))}%"></span></div><span class="bar-value">${row.count}</span></div>`).join("");
}

// 국가 타일 지도. 막대와 같은 countries_30d 를 쓰되 지리로 배치한다.
// 농도 4단은 절대 건수가 아니라 최댓값 대비 비율로 끊는다 — 하루 2건인 날과
// 53건인 날에 같은 임계값을 쓰면 조용한 주에는 지도가 통째로 비어 보인다.
function renderCountryMap() {
  const box = document.getElementById("countryMap");
  const note = document.getElementById("countryMapNote");
  if (!box) return;
  const rows = state.trend?.countries_30d || [];
  const counts = new Map(rows.map(row => [row.country, row.count]));
  const max = Math.max(1, ...rows.filter(row => COUNTRY_GRID[row.country]).map(row => row.count));
  box.style.setProperty("--map-cols", COUNTRY_MAP_COLS);
  box.style.setProperty("--map-rows", COUNTRY_MAP_ROWS);
  box.innerHTML = Object.entries(COUNTRY_GRID).map(([code, [col, row]]) => {
    const count = counts.get(code) || 0;
    const ratio = count / max;
    const level = count === 0 ? 0 : (ratio <= 0.08 ? 1 : (ratio <= 0.3 ? 2 : (ratio <= 0.7 ? 3 : 4)));
    // 라벨은 자료가 있는 칸에만. 39칸에 코드를 다 적으면 켜진 9칸이 묻히고,
    // 빈 칸에는 애초에 할 말이 없다. 카드를 전체 폭으로 올려 칸이 ~52px 이
    // 되면서 12.5px 하한 안에서 코드와 건수가 둘 다 들어간다.
    const label = count ? `<span>${esc(code)}</span><b>${count}</b>` : "";
    return `<div class="country-tile level-${level}" style="grid-column:${col + 1};grid-row:${row + 1}"
      title="${esc(COUNTRY_LABELS[code] || code)} ${count}건">${label}</div>`;
  }).join("") + COUNTRY_MAP_LABELS.map(item => `<span class="country-map-label"
    style="left:${((item.col + 0.5) / COUNTRY_MAP_COLS * 100).toFixed(2)}%;top:${((item.row + 0.5) / COUNTRY_MAP_ROWS * 100).toFixed(2)}%"
    >${esc(item.text)}</span>`).join("");
  renderCountryMapLegend(max);
  renderCountryRegions(rows);
  // 지도에 못 올린 몫을 밝힌다. 조용한 누락은 '전부 담았다'로 읽힌다.
  const offMap = rows.filter(row => !COUNTRY_GRID[row.country]);
  const offCount = offMap.reduce((sum, row) => sum + row.count, 0);
  if (note) {
    note.hidden = !offCount;
    if (offCount) {
      const names = offMap.slice(0, 4).map(row => COUNTRY_LABELS[row.country] || row.country).join(" · ");
      note.textContent = `지도 밖 ${offCount}건 — ${names}${offMap.length > 4 ? " 외" : ""}. 아래 막대에는 포함됩니다.`;
    }
  }
}

// 범례. 농도가 최댓값 대비 비율이라 눈금도 그날의 최댓값으로 적는다 — 고정
// 숫자를 적어두면 조용한 달에 거짓말이 된다.
function renderCountryMapLegend(max) {
  const box = document.getElementById("countryMapLegend");
  if (!box) return;
  const swatches = [0, 1, 2, 3, 4].map(level => `<i class="level-${level}"></i>`).join("");
  box.innerHTML = `<span>0</span>${swatches}<span>${max}건</span>`;
}

// 대륙 합계. 지도의 존재 이유를 숫자로 확인시키는 자리다 — 0건인 대륙도
// 순서대로 남겨 '이번 달 중동 0건'이라는 사실이 보이게 한다.
function renderCountryRegions(rows) {
  const box = document.getElementById("countryRegions");
  const line = document.getElementById("countryMapInterpretation");
  if (!box) return;
  const totals = new Map(COUNTRY_REGION_ORDER.map(name => [name, 0]));
  let mapped = 0;
  for (const row of rows) {
    const region = COUNTRY_REGION[row.country];
    if (!region) continue;
    totals.set(region, (totals.get(region) || 0) + row.count);
    mapped += row.count;
  }
  box.innerHTML = COUNTRY_REGION_ORDER.map(name => {
    const count = totals.get(name) || 0;
    const share = mapped ? Math.round(count / mapped * 100) : 0;
    return `<li class="${count ? "" : "is-zero"}"><span>${esc(name)}</span>
      <span class="country-region-bar"><i style="width:${share}%"></i></span>
      <b>${count}</b></li>`;
  }).join("");
  if (!line) return;
  if (!mapped) {
    line.textContent = "국가로 분류된 이슈가 아직 없습니다.";
    return;
  }
  // 해석은 막대가 못 하는 두 가지만 말한다: 흩어진 것을 합치면 얼마인지, 그리고 없는 곳.
  const ranked = COUNTRY_REGION_ORDER
    .map(name => ({ name, count: totals.get(name) || 0 }))
    .filter(item => item.count)
    .sort((a, b) => b.count - a.count);
  const lead = ranked[0];
  const empty = COUNTRY_REGION_ORDER.filter(name => !totals.get(name));
  const spread = ranked.find(item => item.name === "유럽·러시아");
  const spreadCount = spread
    ? rows.filter(row => COUNTRY_REGION[row.country] === "유럽·러시아").length
    : 0;
  const parts = [`최근 30일은 ${lead.name}가 ${lead.count}건으로 가장 많았습니다`];
  if (spread && spreadCount > 1) {
    parts.push(`유럽·러시아는 ${spreadCount}개국에 ${spread.count}건이 흩어져 있어 나라별로는 작아 보입니다`);
  }
  if (empty.length) parts.push(`${empty.join(" · ")}는 이슈가 없었습니다`);
  line.textContent = `${parts.join(". ")}.`;
}

function renderSlopeGraph() {
  const box = document.getElementById("topicChart");
  const series = state.trend?.topic_series || {};
  const topics = Object.entries(series).filter(([, values]) => values.length >= 2).map(([topic, values]) => ({
    topic, prev: values.at(-2), now: values.at(-1),
  }));
  if (!topics.length) {
    box.innerHTML = '<p class="empty">주간 데이터가 더 필요합니다.</p>';
    return;
  }
  // 위 주제 변화 표와 같은 재료(이슈 단위)를 쓴다. 게이트도 같다 — 수집이 다시
  // 기울면 두 화면이 같이 입을 다물어야 한다.
  const weekTotals = [
    topics.reduce((sum, row) => sum + row.prev, 0),
    topics.reduce((sum, row) => sum + row.now, 0),
  ];
  if (!topicWeeksComparable(weekTotals)) {
    box.innerHTML = '<p class="empty">두 주의 수집량 차이가 커서 아직 비교할 수 없습니다.</p>';
    document.getElementById("topicInterpretation").textContent = "";
    document.getElementById("topicEvidence").innerHTML = "";
    return;
  }
  topics.sort((a, b) => Math.max(b.prev, b.now) - Math.max(a.prev, a.now));
  const top = topics.slice(0, 4);
  if (topics.length > 4) {
    top.push({ topic: "other", prev: topics.slice(4).reduce((sum, row) => sum + row.prev, 0), now: topics.slice(4).reduce((sum, row) => sum + row.now, 0) });
  }
  const rootStyle = getComputedStyle(document.documentElement);
  const palette = [1, 2, 3, 4].map(index => rootStyle.getPropertyValue(`--c-chart-${index}`).trim());
  const mutedColor = rootStyle.getPropertyValue("--c-text-muted").trim();
  // '기타'는 나머지 주제의 합계다. 색을 돌려쓰면 상위 주제와 같은 색이 나와
  // 선이 구분되지 않으므로 회색으로 따로 뗀다.
  const colorFor = (row, index) => (row.topic === "other" ? mutedColor : palette[index % palette.length]);
  // 주제명을 선 오른쪽에 붙이면 가장 긴 라벨이 그래프 최소 폭을 정해버려서 좁은
  // 화면이 가로 스크롤된다. 이름은 아래 범례로 빼고 그래프는 값만 그린다.
  const width = 560, height = 260, left = 74, right = 486, topPad = 26, bottom = 40;
  const maxValue = Math.max(1, ...top.flatMap(row => [row.prev, row.now]));
  const y = value => height - bottom - (value / maxValue) * (height - topPad - bottom);
  const ticks = [...new Set([0, Math.ceil(maxValue / 2), maxValue])].sort((a, b) => a - b);
  const grid = ticks.map(value => `<g><line x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left - 16}" y="${y(value) + 4}" text-anchor="end">${value}</text></g>`).join("");
  const lines = top.map((row, index) => {
    const color = colorFor(row, index);
    const label = row.topic === "other" ? "기타" : TOPIC_LABELS[row.topic] || row.topic;
    return `<g class="slope-series"><line x1="${left}" y1="${y(row.prev)}" x2="${right}" y2="${y(row.now)}" style="stroke:${color}"/><circle cx="${left}" cy="${y(row.prev)}" r="5" style="fill:${color}"/><circle cx="${right}" cy="${y(row.now)}" r="5" style="fill:${color}"/><text x="${left - 10}" y="${y(row.prev) - 9}" text-anchor="end">${row.prev}</text><text x="${right + 10}" y="${y(row.now) + 4}" style="fill:${color}">${row.now}</text><title>${esc(label)} · 전주 ${row.prev}건 → 이번 주 ${row.now}건</title></g>`;
  }).join("");
  const legend = top.map((row, index) => {
    const color = colorFor(row, index);
    const label = row.topic === "other" ? "기타" : TOPIC_LABELS[row.topic] || row.topic;
    return `<li><i style="background:${color}"></i><span>${esc(label)}</span><small>${row.prev} → ${row.now}</small></li>`;
  }).join("");
  box.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="전주와 이번 주의 주제별 이슈 수 비교"><g class="slope-grid">${grid}</g>${lines}<text class="axis-label" x="${left}" y="${height - 10}" text-anchor="middle">전주</text><text class="axis-label" x="${right}" y="${height - 10}" text-anchor="middle">이번 주</text></svg>
    <ul class="slope-legend">${legend}</ul>`;
  const strongest = [...top].sort((a, b) => Math.abs(b.now - b.prev) - Math.abs(a.now - a.prev))[0];
  const label = strongest.topic === "other" ? "기타 주제" : TOPIC_LABELS[strongest.topic] || strongest.topic;
  const delta = strongest.now - strongest.prev;
  document.getElementById("topicInterpretation").textContent = `${label} 이슈가 전주 ${strongest.prev}건에서 이번 주 ${strongest.now}건으로 ${delta >= 0 ? `${delta}건 늘었습니다` : `${Math.abs(delta)}건 줄었습니다`}.`;
  document.getElementById("topicEvidence").innerHTML = top.map(row => {
    const name = row.topic === "other" ? "기타" : TOPIC_LABELS[row.topic] || row.topic;
    return `<p><strong>${esc(name)}</strong> · 전주 ${row.prev}건 → 이번 주 ${row.now}건</p>`;
  }).join("");
}

// 주간 판세 — 고정 코너 5개. 매주 같은 자리에서 같은 질문에 답하는 편집 형식이
// 서비스의 목소리를 만든다. 근거 칩은 문장별 evidence 로만 붙인다(전역 key_events
// 를 모든 문장에 붙이면 같은 칩이 반복돼 의미가 사라진다).
function evidenceChips(evidence) {
  const rows = (evidence || []).filter(item => item && item.issue_id && item.title);
  if (!rows.length) return "";
  return `<div class="weekly-evidence">` + rows.map(item =>
    `<button type="button" class="hero-evidence-chip" data-issue-id="${esc(item.issue_id)}">${esc(item.title)}</button>`
  ).join("") + `</div>`;
}

function weeklySection(title, note, body) {
  if (!body) return "";
  return `<section class="weekly-block"><h3>${esc(title)}</h3>`
    + (note ? `<p class="data-note">${esc(note)}</p>` : "") + body + `</section>`;
}

// 흐름 탭의 첫 블록 — 설명문보다 방향과 크기가 먼저 온다.
//
// theme_moves 의 해설을 각 행에 접어 붙이려다 말았다. 테마는 LLM 자유 서술이고
// 주제는 고정 분류라 라벨 공간이 다르다(실측 2026-08-08: 4건 중 SMR·핵융합
// 2건만 일치). 붙이면 맞는 두 줄만 접히고 그 문장은 바로 아래 '조용하지만
// 놓치면 안 되는 것'에도 그대로 있어 새 중복이 된다. 해설은 그 코너가 맡는다.
function renderTrendTopicFlow() {
  const section = document.getElementById("trendTopicFlow");
  if (!section) return;
  const rows = topicFlowRows();
  section.hidden = rows.length === 0;
  if (section.hidden) return;
  // 제목이 기간을 말한다. 온전한 주가 3개뿐인데 '4주'라고 써 두면 표가 못 채운
  // 한 주를 독자가 채워 읽는다.
  document.getElementById("trendTopicFlowTitle").textContent =
    `최근 ${rows[0].span}주 동안 어디로 움직였나`;
  document.getElementById("trendTopicFlowRows").innerHTML = rows.map(topicFlowRow).join("");
}

function renderWeeklyReport() {
  const panel = document.getElementById("weeklyReport");
  const report = state.trend?.weekly_report;
  // 리포트가 없으면 통째로 숨긴다 — 빈 탭이 되면 안 되므로 아래 정량 트렌드가
  // 그대로 남는다. 원래 가드는 `!report && !questions.length` 였는데,
  // weekly_reports.json 이 3개월째 생성된 적이 없어(weekly.yml 미가동) 실제로는
  // open_questions 한 코너만 '주간 판세' 제목을 달고 떠 있었다.
  if (!report) { panel.hidden = true; return; }
  panel.hidden = false;

  document.getElementById("weeklyReportMeta").textContent = report
    ? `${dateLabel(report.week_start)}–${dateLabel(report.week_end)} · 이슈 ${report.source_issue_count ?? 0}건`
    : "";

  const themes = (report?.theme_moves || []).filter(row => row && row.theme);
  const arrow = { "강화": "▲", "약화": "▼", "유지": "―" };
  // 방향 색은 주제 변화 표(.topic-direction)와 같은 어휘를 쓴다.
  const DIRECTION_TONE = { "강화": "up", "약화": "down", "유지": "flat" };

  // '이번 주 판을 바꾼 것'(weekly_intro + policy_shifts)과 '다음 주 하나만
  // 본다면'(watchpoints)은 뺐다. 오늘 화면의 '이번 주 결론'·'다음 확인'·'이번 주
  // 해설'이 같은 문장을 이미 낸다 — 실측 2026-08-08: 흐름 첫 화면 산문 여섯
  // 문단 중 일곱 문장이 오늘 탭과 글자 그대로 동일. 탭을 옮겼는데 같은 글이
  // 다시 나오면 그건 깊이가 아니라 반복이다.
  document.getElementById("weeklyReportBody").innerHTML = [
    // 테마명을 라벨 열로 뗀다. 화살표·테마·설명이 한 문장으로 이어져 있으면
    // 훑는 눈이 걸릴 데가 없다 — 카드의 세 칸과 같은 원칙이다.
    weeklySection("조용하지만 놓치면 안 되는 것", "투자 테마 강약",
      themes.map(row =>
        `<div class="weekly-item theme-move">`
        + `<p class="theme-name ${DIRECTION_TONE[row.direction] || "flat"}">`
        + `${esc(arrow[row.direction] || "―")} ${esc(row.theme)}</p>`
        + `<div class="theme-body">${row.why ? `<p>${esc(row.why)}</p>` : ""}`
        + evidenceChips(row.evidence) + `</div></div>`).join("")),
    weeklySection("한수원에 직접 닿는 변화", "",
      report?.khnp_direct ? `<p>${esc(report.khnp_direct)}</p>` : ""),
    // '아직 결론 나지 않은 것'(open_questions)도 뺐다. 같은 문장이 선두 카드의
    // '다음 확인' 칸과 상세 모달에 이미 나온다 — 세 번째 노출이다. 게다가 채움률
    // 6/168 이라 코너 자체가 대개 비어 있었다.
  ].join("");
}

// 지난 브리핑 — 흐름 탭의 시간 축. briefings.json 은 이미 클라이언트에 있다
// (빌드 변경 0). 빈 날의 사유는 데이터가 말할 때만 쓴다 — 추정 금지.
function renderBriefingTimeline() {
  const list = document.getElementById("briefingTimelineList");
  if (!list) return;
  list.innerHTML = state.briefings.map(briefing => {
    const issueCount = Number(briefing.issue_count || 0);
    const changed = Number(briefing.changed_issue_count || 0);
    let counts;
    if (issueCount) {
      counts = `이슈 ${issueCount}${changed ? ` · 변화 ${changed}` : ""}`;
    } else if (briefing.pipeline_status && briefing.pipeline_status !== "ok") {
      counts = "지연·확인 중";
    } else if (Number(briefing.below_floor_count || 0) > 0) {
      counts = `기준 미달 ${briefing.below_floor_count}건`;
    } else {
      counts = "생성된 브리핑이 없습니다";
    }
    return `<li class="${issueCount ? "" : "bt-quiet"}">
      <button type="button" data-go-date="${esc(briefing.date)}">
        <span class="bt-date">${esc(dateLabel(briefing.date))}</span>
        <span class="bt-headline">${esc(briefing.headline || "")}</span>
        <span class="bt-counts">${esc(counts)}</span>
      </button>
    </li>`;
  }).join("");
}

// 구역 번호는 화면에 실제로 선 구역을 센다. 조건부 구역이 늘면서 01 이 숨은 날
// 흐름 탭이 02 부터 시작했다 — 번호가 있는데 앞이 비면 뭔가 빠졌다고 읽힌다.
// HTML 의 숫자는 편집 순서를 적어 두는 용도로 남기고, 표시값만 여기서 고친다.
function renumberSections(viewId) {
  const view = document.getElementById(viewId);
  if (!view) return;
  let index = 0;
  for (const marker of view.querySelectorAll(".sec-no")) {
    const host = marker.closest("section");
    if (host && host.hidden) continue;
    marker.textContent = String(++index).padStart(2, "0");
  }
}

function renderTrend() {
  renderTrendTopicFlow();
  renderWeeklyReport();
  renderInsights();
  renderTrendReadiness();
  // 지난 브리핑은 트렌드 집계 준비 여부와 무관하다 — 이른 return 앞에서 그린다.
  renderBriefingTimeline();
  // 번호가 붙은 구역은 전부 위에서 결정됐다 — 아래 정량 블록에는 sec-no 가 없으므로
  // trend_ready 조기 return 앞에서 매긴다.
  renumberSections("view-trend");
  if (!state.meta?.trend_ready) return;
  renderKeywordTable();
  renderCountryMap();
  bars(document.getElementById("countryBars"), state.trend.countries_30d, row => COUNTRY_LABELS[row.country] || row.country);
  const topCountry = state.trend.countries_30d?.[0];
  document.getElementById("countryInterpretation").textContent = topCountry
    ? `최근 30일에는 ${COUNTRY_LABELS[topCountry.country] || topCountry.country} 관련 이슈가 ${topCountry.count}개로 가장 많았습니다.`
    : "국가별로 비교할 이슈가 아직 충분하지 않습니다.";
  renderSlopeGraph();
}

function clearBriefingFilters() {
  state.region = "전체";
  state.topic = "전체";
  document.getElementById("topicSel").value = "전체";
  setPressed(document.getElementById("regionTabs"), document.querySelector('#regionTabs [data-region="전체"]'));
  renderBriefing();
  syncUrl();
}

function clearArchiveFilters() {
  state.archiveQuery = "";
  state.archiveEntity = "";
  state.archiveRegion = "전체";
  state.archiveTopic = "전체";
  state.archivePeriod = "all";
  state.archiveVerification = "전체";
  document.getElementById("globalSearch").value = "";
  document.getElementById("archiveRegion").value = "전체";
  document.getElementById("archiveTopic").value = "전체";
  document.getElementById("archiveVerification").value = "전체";
  setPressed(document.getElementById("archivePeriod"), document.querySelector('#archivePeriod [data-period="all"]'));
  renderArchiveSearch(true);
  syncUrl();
}

// 허브·엔티티 헤더의 클릭은 필터 조작이지 이슈 액션이 아니다 — handleIssueAction
// 위임 목록에 넣지 않고 따로 받는다. 규칙: 허브에서 무엇을 고르면 **그 필터
// 하나만 선 깨끗한 결과**에서 시작한다(교집합은 그 뒤 사용자가 쌓는 것).
function handleHubAction(event) {
  // 팔로우 토글은 필터 조작이 아니다 — 리셋 없이 처리하고 끝낸다.
  const followToggle = event.target.closest("[data-follow-toggle]");
  if (followToggle) { toggleFollow(followToggle.dataset.followToggle); return; }
  const entityChip = event.target.closest("[data-hub-ent]");
  // 주제 칩은 허브에서 뺐지만 엔티티 헤더의 '자주 함께 등장한 주제'가 계속 쓴다.
  const topicChip = event.target.closest("[data-hub-topic]");
  const clearEntity = event.target.closest("[data-clear-entity]");
  if (!entityChip && !topicChip && !clearEntity) return;
  state.archiveQuery = "";
  state.archiveEntity = "";
  state.archiveRegion = "전체";
  state.archiveTopic = "전체";
  state.archivePeriod = "all";
  state.archiveVerification = "전체";
  document.getElementById("globalSearch").value = "";
  document.getElementById("archiveRegion").value = "전체";
  document.getElementById("archiveTopic").value = "전체";
  document.getElementById("archiveVerification").value = "전체";
  setPressed(document.getElementById("archivePeriod"), document.querySelector('#archivePeriod [data-period="all"]'));
  if (entityChip) state.archiveEntity = entityChip.dataset.hubEnt;
  if (topicChip) {
    state.archiveTopic = topicChip.dataset.hubTopic;
    document.getElementById("archiveTopic").value = state.archiveTopic;
  }
  renderArchiveSearch(true);
  syncUrl("push");
  scrollToPageTop();
}

function switchView(view, updateUrl = true) {
  if (!VIEW_IDS.includes(view)) return;
  if (view !== state.view && state.issueId) closeIssueDialog(false);
  state.view = view;
  VIEW_IDS.forEach(id => {
    const section = document.getElementById(`view-${id}`);
    const entering = id === view && section.hidden;
    section.hidden = id !== view;
    // 진입 모션 — 토큰(--mo-2) 경유라 reduced-motion 전역 오버라이드가 함께 끈다.
    if (entering && !prefersReducedMotion()) {
      section.classList.remove("view-in");
      void section.offsetWidth;
      section.classList.add("view-in");
    }
  });
  const savedPanel = document.getElementById("search-saved");
  if (savedPanel) savedPanel.hidden = view !== "search";
  document.querySelectorAll("[data-view]").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (view === "search") renderArchiveSearch();
  if (view === "trend") renderTrend();
  if (view === "search") renderSaved();
  if (view === "report") { renderReportCandidates(); renderPubs(); }
  if (updateUrl) syncUrl();
  scrollToPageTop();
}

/* 필터 서랍 — 좁은 화면에서는 바텀시트, 넓은 화면에서는 기존 드롭다운·사이드바.
   <details> 는 ESC·바깥 탭·포커스 복귀를 스스로 해 주지 않는다. 바텀시트 모양을
   하고 있으면 사용자는 그 셋을 기대하므로 여기서 직접 붙인다. */
const narrowScreen = matchMedia("(max-width: 767px)");
/* 근거 패널이 실제로 보이는 폭(style.css 의 .briefing-sidebar 미디어쿼리와 같은
   값). 이 경계를 넘나들면 선두 카드가 해석을 들고 있어야 하는지가 바뀌므로 —
   패널이 사라졌는데 카드가 사실만 들고 있으면 해석이 화면에서 통째로 증발한다 —
   경계에서 한 번 다시 그린다. */
const railScreen = matchMedia("(min-width: 1200px)");

function filterDrawers() {
  return [document.getElementById("briefingFilters"), document.getElementById("archiveFilterDrawer")].filter(Boolean);
}

function syncSheetLock() {
  const locked = narrowScreen.matches && filterDrawers().some(drawer => drawer.open);
  document.documentElement.classList.toggle("sheet-open", locked);
}

function closeFilterDrawer(drawer, returnFocus = true) {
  if (!drawer || !drawer.open) return;
  drawer.open = false;
  syncSheetLock();
  if (returnFocus) drawer.querySelector("summary")?.focus();
}

// 넓은 화면의 아카이브 필터는 접히지 않는 사이드바다. summary 를 숨기는 것만으로는
// 내용이 사라지므로 open 을 켜 둔다.
function syncArchiveDrawer() {
  const drawer = document.getElementById("archiveFilterDrawer");
  if (!drawer) return;
  if (!narrowScreen.matches) drawer.open = true;
  else if (drawer.dataset.userOpened !== "1") drawer.open = false;
  syncSheetLock();
}

function initFilterDrawers() {
  filterDrawers().forEach(drawer => {
    drawer.addEventListener("toggle", () => {
      if (drawer.id === "archiveFilterDrawer") drawer.dataset.userOpened = drawer.open && narrowScreen.matches ? "1" : "";
      syncSheetLock();
    });
  });
  // 스크림은 details 의 ::before 라 클릭 target 이 details 자신으로 잡힌다.
  document.addEventListener("click", event => {
    filterDrawers().forEach(drawer => {
      if (!drawer.open) return;
      if (drawer.id === "archiveFilterDrawer" && !narrowScreen.matches) return;
      if (event.target === drawer || !drawer.contains(event.target)) closeFilterDrawer(drawer, false);
    });
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const open = filterDrawers().find(drawer => drawer.open && (drawer.id !== "archiveFilterDrawer" || narrowScreen.matches));
    if (!open) return;
    event.preventDefault();
    closeFilterDrawer(open);
  });
  narrowScreen.addEventListener("change", syncArchiveDrawer);
  railScreen.addEventListener("change", () => { if (appReady) renderBriefing(); });
  syncArchiveDrawer();
}

/* ── 통합 검색: 입력 즉시 그룹 결과 ─────────────────────────────────
   전부 초기 로드된 JSON 위에서 도는 클라이언트 검색이다 — 다이얼로그를 다시
   열어도 네트워크 요청 0. 점수는 상수로 박아 재량을 없앤다. 결과 그룹 순서는
   이슈 → 대상 → 주제 → 국가 → 발간물. */
const SEARCH_SCORE = {
  issueTitleExact: 100, issueTitleStart: 70, issueTitleHas: 50, issueTagHas: 35, issueSummaryHas: 15,
  entityNameExact: 100, entityEnExact: 90, entityAliasExact: 85, entityPrefix: 60, entityHas: 30,
  pubTitleHas: 60, pubOrgHas: 40, pubGistHas: 20, pubBriefHas: 10,
};
// 검색어·대상 텍스트 공통 정규화 — 소문자화 + 하이픈·중점·슬래시·점 제거.
// 'X-energy'와 'xenergy', '1 호기'와 '1호기'가 같은 것으로 읽히게 한다.
function searchNormalize(value) {
  return String(value || "").toLowerCase().replace(/[\s\-–—·./]+/g, "");
}
// 도메인 동의어 — 어느 쪽으로 검색해도 짝을 함께 찾는다. 엔티티 동의어는
// 레지스트리 aliases 가 담당하므로 여기는 주제어만 둔다.
const SEARCH_SYNONYMS = [["smr", "소형모듈원자로"], ["사용후핵연료", "방사성폐기물"]];
function searchVariants(query) {
  const norm = searchNormalize(query);
  const variants = new Set([norm]);
  // '고리 1호기'처럼 호기까지 쓴 질의 — 데이터에 그 호기가 아직 없어도
  // 발전소 이름으로는 찾아져야 한다. 원형('고리1호기')이 먼저 매칭되므로
  // 호기 데이터가 생기면 자연히 그쪽이 이긴다.
  const unit = norm.match(/^(.+?)\d+호기$/);
  if (unit && unit[1].length >= 2) variants.add(unit[1]);
  SEARCH_SYNONYMS.forEach(pair => {
    pair.forEach((word, index) => {
      if (norm.includes(word)) variants.add(norm.replace(word, pair[1 - index]));
    });
  });
  return [...variants].filter(Boolean);
}
function searchHit(text, variants) {
  const norm = searchNormalize(text);
  return variants.some(variant => norm.includes(variant));
}

function searchIssuesQuick(variants, limit) {
  const scored = [];
  state.issues.forEach(issue => {
    const title = searchNormalize(issue.title);
    let score = 0;
    if (variants.some(v => title === v)) score = SEARCH_SCORE.issueTitleExact;
    else if (variants.some(v => title.startsWith(v))) score = SEARCH_SCORE.issueTitleStart;
    else if (variants.some(v => title.includes(v))) score = SEARCH_SCORE.issueTitleHas;
    else if ((issue.tags || []).some(tag => searchHit(tag, variants))) score = SEARCH_SCORE.issueTagHas;
    else if (searchHit(issue.summary, variants) || searchHit(issue.implication, variants)) score = SEARCH_SCORE.issueSummaryHas;
    if (score) scored.push({ score, issue });
  });
  scored.sort((a, b) => b.score - a.score || String(b.issue.last_seen).localeCompare(String(a.issue.last_seen)));
  return scored.slice(0, limit);
}

function searchEntitiesQuick(variants, limit) {
  const scored = [];
  (state.entities?.entities || []).forEach(entity => {
    const kr = searchNormalize(entity.name_kr);
    const en = searchNormalize(entity.name_en);
    const aliases = (entity.aliases || []).map(searchNormalize);
    let score = 0;
    if (variants.some(v => kr === v)) score = SEARCH_SCORE.entityNameExact;
    else if (en && variants.some(v => en === v)) score = SEARCH_SCORE.entityEnExact;
    else if (variants.some(v => aliases.includes(v))) score = SEARCH_SCORE.entityAliasExact;
    else if (variants.some(v => kr.startsWith(v) || aliases.some(alias => alias.startsWith(v)))) score = SEARCH_SCORE.entityPrefix;
    else if (variants.some(v => kr.includes(v) || en.includes(v))) score = SEARCH_SCORE.entityHas;
    if (!score) return;
    // 0건 대상: 정확 명칭 일치는 상단 그대로(찾은 게 맞으니), 포함 일치는
    // 이슈 보정 없이 하위로 — '관련 이슈 없음'을 함께 말한다.
    const bonus = Math.min(entity.issue_count || 0, 10);
    scored.push({ score: score + bonus, entity });
  });
  scored.sort((a, b) => b.score - a.score || (b.entity.issue_count || 0) - (a.entity.issue_count || 0));
  return scored.slice(0, limit);
}

function searchPubsQuick(variants, limit) {
  const scored = [];
  (state.pubs?.items || []).forEach(item => {
    if (!item || typeof item !== "object" || !item.url) return;
    let score = 0;
    let brief = "";
    if (searchHit(item.title_kr, variants) || searchHit(item.title, variants)) score = SEARCH_SCORE.pubTitleHas;
    else if (searchHit(item.org_kr, variants) || searchHit(item.org, variants)) score = SEARCH_SCORE.pubOrgHas;
    else if (searchHit(item.gist, variants)) score = SEARCH_SCORE.pubGistHas;
    else {
      // toc.briefs 는 데이터셋에서 가장 밀도 높은 문장들 — 단, 스니펫은 일치한
      // 한 문장만 보여준다(다 펼치면 검색 결과가 목차 사본이 된다).
      brief = (item.toc?.briefs || []).find(line => searchHit(line, variants)) || "";
      if (brief) score = SEARCH_SCORE.pubBriefHas;
    }
    if (score) scored.push({ score, item, brief });
  });
  scored.sort((a, b) => b.score - a.score || String(b.item.date || "").localeCompare(String(a.item.date || "")));
  return scored.slice(0, limit);
}

function searchLabelChips(variants, labels, limit) {
  return Object.entries(labels)
    .filter(([, label]) => searchHit(label, variants))
    .slice(0, limit);
}

function loadRecentSearches() {
  try {
    const raw = JSON.parse(localStorage.getItem("nuclens-recent-searches") || "[]");
    return Array.isArray(raw) ? raw.filter(item => typeof item === "string").slice(0, 8) : [];
  } catch { return []; }
}
function saveRecentSearch(query) {
  const value = normalizedSearch(query);
  if (value.length < 2) return;   // 1글자·공백은 저장하지 않는다
  const rest = loadRecentSearches().filter(item => item !== value);
  try { localStorage.setItem("nuclens-recent-searches", JSON.stringify([value, ...rest].slice(0, 8))); }
  catch { /* 저장 실패는 검색을 막지 않는다 */ }
}
function removeRecentSearch(query) {
  try {
    localStorage.setItem("nuclens-recent-searches",
      JSON.stringify(loadRecentSearches().filter(item => item !== query)));
  } catch { /* 동일 */ }
}

let searchActiveIndex = -1;
function searchOptionRow(id, body, dataset) {
  const attrs = Object.entries(dataset).map(([key, value]) => `data-${key}="${esc(value)}"`).join(" ");
  return `<div class="search-option" role="option" id="${id}" aria-selected="false" ${attrs}>${body}</div>`;
}

function renderSearchResults() {
  const box = document.getElementById("globalSearchResults");
  const input = document.getElementById("globalSearch");
  if (!box || !input) return;
  searchActiveIndex = -1;
  input.setAttribute("aria-activedescendant", "");
  const query = normalizedSearch(input.value);
  if (!query) {
    const recent = loadRecentSearches();
    const groups = [];
    if (recent.length) {
      groups.push(`<div class="search-group"><h3>최근 검색<button type="button" class="search-clear-recent" data-recent-clear>전체 삭제</button></h3>`
        + recent.map((item, index) => searchOptionRow(`sr-${index}`,
          `<span>${esc(item)}</span><button type="button" class="search-remove" data-recent-remove="${esc(item)}" aria-label="‘${esc(item)}’ 삭제">×</button>`,
          { "search-query": item })).join("")
        + "</div>");
    }
    // 검색어를 아직 안 친 화면이 빈 상자면 안 된다 — 탐색 허브와 같은 대상
    // 목록을 시작점으로 깐다(데이터도 경로도 재사용, data-search-entity 는
    // applySearchResult 가 이미 처리한다). 결과가 아니라 시작점이므로 최근
    // 검색 아래에 선다.
    const starters = (state.entities?.entities || [])
      .filter(entity => entity.issue_count > 0)
      .slice(0, 6);
    if (starters.length) {
      groups.push(`<div class="search-group"><h3>지금 많이 등장하는 대상</h3>`
        + starters.map(entity => searchOptionRow(`sr-e${entity.id}`,
          `<span><small>${esc(ENTITY_TYPE_LABELS[entity.type] || "")}</small> ${esc(entity.name_kr)}</span><small>이슈 ${entity.issue_count}건</small>`,
          { "search-entity": entity.id })).join("")
        + "</div>");
    }
    box.innerHTML = groups.join("");
    input.setAttribute("aria-expanded", String(groups.length > 0));
    return;
  }
  const variants = searchVariants(query);
  const perGroup = narrowScreen.matches ? 3 : 5;
  let optionIndex = 0;
  const groups = [];
  const issues = searchIssuesQuick(variants, perGroup);
  if (issues.length) {
    groups.push(`<div class="search-group"><h3>이슈</h3>${issues.map(({ issue }) => searchOptionRow(
      `sr-${optionIndex++}`,
      `<span>${esc(issue.title)}</span><small>${esc(dateLabel(issue.last_seen))} · ${esc(issue.region || "")}</small>`,
      { "search-issue": issue.issue_id })).join("")}</div>`);
  }
  const entities = searchEntitiesQuick(variants, narrowScreen.matches ? 3 : 4);
  if (entities.length) {
    groups.push(`<div class="search-group"><h3>대상</h3>${entities.map(({ entity }) => searchOptionRow(
      `sr-${optionIndex++}`,
      `<span><small>${esc(ENTITY_TYPE_LABELS[entity.type] || "")}</small> ${esc(entity.name_kr)}</span>`
      + `<small>${entity.issue_count ? `이슈 ${entity.issue_count}건` : "관련 이슈 없음"}</small>`,
      { "search-entity": entity.id })).join("")}</div>`);
  }
  const topics = searchLabelChips(variants, TOPIC_LABELS, narrowScreen.matches ? 3 : 4);
  if (topics.length) {
    groups.push(`<div class="search-group"><h3>주제</h3>${topics.map(([key, label]) => searchOptionRow(
      `sr-${optionIndex++}`, `<span>${esc(label)}</span>`, { "search-topic": key })).join("")}</div>`);
  }
  const countries = searchLabelChips(variants, COUNTRY_LABELS, narrowScreen.matches ? 3 : 4);
  if (countries.length) {
    groups.push(`<div class="search-group"><h3>국가</h3>${countries.map(([, label]) => searchOptionRow(
      `sr-${optionIndex++}`, `<span>${esc(label)}</span>`, { "search-country": label })).join("")}</div>`);
  }
  const pubs = searchPubsQuick(variants, narrowScreen.matches ? 3 : 4);
  if (pubs.length) {
    groups.push(`<div class="search-group"><h3>발간물</h3>${pubs.map(({ item, brief }) => searchOptionRow(
      `sr-${optionIndex++}`,
      `<span>${esc(item.title_kr || item.title)}</span>`
      + `<small>${esc(item.org_kr || item.org || "")}${item.date ? ` · ${esc(dateLabel(item.date))}` : ""}</small>`
      + (brief ? `<em class="search-brief">${esc(brief)}</em>` : ""),
      { "search-pub": item.url })).join("")}</div>`);
  }
  box.innerHTML = groups.length ? groups.join("") : `<div class="search-empty">
    <p>조건에 맞는 결과가 없습니다 — 주제나 국가명으로 시작해 보세요.</p>
    <div class="hub-chips">${["SMR", "계속운전", "미국"].map(word =>
    `<button type="button" class="hub-chip" data-search-starter="${esc(word)}">${esc(word)}</button>`).join("")}</div>
  </div>`;
  input.setAttribute("aria-expanded", String(groups.length > 0));
}

function searchOptions() {
  return [...document.querySelectorAll("#globalSearchResults [role=\"option\"]")];
}
function moveSearchActive(delta) {
  const options = searchOptions();
  if (!options.length) return;
  searchActiveIndex = (searchActiveIndex + delta + options.length) % options.length;
  options.forEach((option, index) => {
    option.classList.toggle("active", index === searchActiveIndex);
    option.setAttribute("aria-selected", String(index === searchActiveIndex));
  });
  const active = options[searchActiveIndex];
  document.getElementById("globalSearch").setAttribute("aria-activedescendant", active.id);
  active.scrollIntoView({ block: "nearest" });
}

// 결과 선택 — 종류마다 목적지가 다르다. 공통 규칙: 필터는 깨끗한 상태에서
// 그 하나만 세운다(허브와 같은 계약).
function applySearchResult(option) {
  const dialog = document.getElementById("globalSearchDialog");
  const data = option.dataset;
  if (data.recentClear !== undefined) return;   // 별도 처리
  if (data.searchQuery) {
    document.getElementById("globalSearch").value = data.searchQuery;
    renderSearchResults();
    return;
  }
  if (data.searchStarter) {
    document.getElementById("globalSearch").value = data.searchStarter;
    renderSearchResults();
    return;
  }
  saveRecentSearch(document.getElementById("globalSearch").value);
  if (data.searchIssue) {
    dialog.close();
    openIssueDialog(data.searchIssue);
    return;
  }
  if (data.searchPub) {
    const url = safeUrl(data.searchPub);
    if (url) window.open(url, "_blank", "noopener");
    return;
  }
  const reset = () => {
    state.archiveQuery = "";
    state.archiveEntity = "";
    state.archiveRegion = "전체";
    state.archiveTopic = "전체";
    state.archivePeriod = "all";
    state.archiveVerification = "전체";
  };
  if (data.searchEntity) { reset(); state.archiveEntity = data.searchEntity; }
  else if (data.searchTopic) { reset(); state.archiveTopic = data.searchTopic; }
  else if (data.searchCountry) { reset(); state.archiveQuery = normalizedSearch(data.searchCountry); }
  else return;
  dialog.close();
  document.getElementById("globalSearch").value = "";
  switchView("search");
  renderArchiveSearch(true);
  syncUrl("push");
}

function openGlobalSearch() {
  const dialog = document.getElementById("globalSearchDialog");
  const input = document.getElementById("globalSearch");
  input.value = state.archiveQuery;
  if (!dialog.open) dialog.showModal();
  renderSearchResults();
  requestAnimationFrame(() => { input.focus(); input.select(); });
}

function applyTheme(theme, persist = false) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0d1613" : "#12251e";
  const button = document.getElementById("themeToggle");
  button.setAttribute("aria-label", theme === "dark" ? "라이트 모드 켜기" : "다크 모드 켜기");
  if (persist) localStorage.setItem("nuclens-theme", theme);
}

function initializeTheme() {
  const saved = localStorage.getItem("nuclens-theme");
  applyTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
}

function stepBriefing(direction) {
  const dates = briefingDates();
  const nextIndex = dates.indexOf(state.briefingDate) + direction;
  if (nextIndex < 0 || nextIndex >= dates.length) return;
  state.briefingDate = dates[nextIndex];
  renderDateSelect();
  renderBriefing();
  renderSystemStatus();
  syncUrl();
}

function handleIssueAction(event) {
  const agenda = event.target.closest("[data-agenda-issue]");
  if (agenda) {
    const target = document.getElementById(`issue-card-${agenda.dataset.agendaIssue}`);
    if (target) {
      target.scrollIntoView({ block: "start", behavior: prefersReducedMotion() ? "auto" : "smooth" });
      target.classList.add("agenda-target");
      window.setTimeout(() => target.classList.remove("agenda-target"), 1200);
    } else {
      openIssueDialog(agenda.dataset.agendaIssue);
    }
    return true;
  }
  const copy = event.target.closest("[data-copy-issue]");
  if (copy) { copyIssueReport(copy, copy.dataset.copyIssue); return true; }
  const pack = event.target.closest("[data-pack-issue]");
  if (pack) { copyIssuePack(pack, pack.dataset.packIssue); return true; }
  const save = event.target.closest("[data-save-issue]");
  if (save) { toggleSaved(save.dataset.saveIssue); return true; }
  const share = event.target.closest("[data-share-issue]");
  if (share) { shareIssue(share.dataset.shareIssue); return true; }
  const detail = event.target.closest("[data-issue-id]");
  if (detail) {
    // 데스크톱 오늘 브리핑에서는 모달 대신 우측 근거 패널을 갈아끼운다.
    // 모바일·딥링크·아카이브는 그대로 다이얼로그 — /issue/<id>/ 정적 페이지
    // 113개가 부팅 시 openIssueDialog 를 부르므로 그 경로는 살아 있어야 한다.
    // 패널 안의 '전체 상세'(data-force-dialog)는 언제나 다이얼로그를 연다.
    if (!detail.dataset.forceDialog && state.view === "news" && railIsActive()) {
      recordRecentIssue(detail.dataset.issueId);
      state.railIssueId = detail.dataset.issueId;
      renderEvidenceRail();
      document.getElementById("evidenceRail")?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
      return true;
    }
    openIssueDialog(detail.dataset.issueId);
    return true;
  }
  // 행 전체 클릭 — hover 가 행을 켜는데 제목만 눌리던 어긋남을 접는다.
  // 버튼·링크는 위에서 이미 걸렀고, 텍스트를 긁는 중(드래그 선택)이면 열지
  // 않는다. 동작은 제목 클릭과 완전히 같아야 하므로 새 경로를 만들지 않고
  // 제목 버튼에 위임한다.
  const row = event.target.closest("[data-issue-card]");
  if (row && !event.target.closest("a, button") && window.getSelection().isCollapsed) {
    const title = row.querySelector(".issue-title-button");
    if (title) { title.click(); return true; }
  }
  return false;
}

// 패널은 사이드바가 실제로 보이는 폭에서만 쓴다. style.css 의
// `.briefing-sidebar { display: none }` 가 좁은 화면에서 사이드바를 숨기므로,
// 폭을 숫자로 다시 적지 않고 렌더 결과를 직접 본다 — 값이 두 곳에 있으면 갈라진다.
function railIsActive() {
  const sidebar = document.querySelector(".briefing-sidebar");
  return !!sidebar && getComputedStyle(sidebar).display !== "none";
}

function bind() {
  const viewHandler = event => {
    const button = event.target.closest("button[data-view]");
    if (button) switchView(button.dataset.view);
  };
  document.getElementById("mainTabs").addEventListener("click", viewHandler);
  document.getElementById("mobileTabs").addEventListener("click", viewHandler);
  document.body.addEventListener("click", event => {
    const go = event.target.closest("[data-go-view]");
    if (go) switchView(go.dataset.goView);
    const saved = event.target.closest("[data-go-saved]");
    if (saved) {
      switchView("search");
      document.getElementById("search-saved")?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
    // 톰스톤의 '제목으로 다시 찾기' — 저장 당시 제목을 검색어로 탐색에 넘긴다.
    const requery = event.target.closest("[data-requery]");
    if (requery) {
      state.archiveQuery = normalizedSearch(requery.dataset.requery);
      state.archiveEntity = "";
      switchView("search");
      renderArchiveSearch(true);
      syncUrl("push");
    }
    const pubsOrg = event.target.closest("[data-pubs-org]");
    if (pubsOrg) { state.pubsOrg = pubsOrg.dataset.pubsOrg; renderPubs(); }
    const keyword = event.target.closest("[data-keyword]");
    if (keyword) {
      state.archiveQuery = normalizedSearch(keyword.dataset.keyword);
      document.getElementById("globalSearch").value = state.archiveQuery;
      switchView("search");
    }
    if (event.target.closest("[data-clear-briefing]")) clearBriefingFilters();
    if (event.target.closest("[data-clear-archive]")) clearArchiveFilters();
  });
  // briefingTitle: 기사 제목을 얹은 날의 h1 은 안에 상세 진입 버튼을 품는다.
  // leadCard: 선두 카드 안의 버튼(타임라인·저장·공유)도 같은 위임을 탄다.
  ["todayAgenda", "issueList", "changedList", "leadCard", "archiveIssueList", "savedIssueList", "reportCandidateList", "issueDialog",
   "headlineEvidence", "weeklyReportBody", "insightList", "evidenceRail", "briefingTitle",
   "recentIssueList"].forEach(id => {
    document.getElementById(id).addEventListener("click", handleIssueAction);
  });
  document.getElementById("clearRecentIssues")?.addEventListener("click", () => {
    try { localStorage.removeItem("nuclens-recent-issues"); } catch { /* 무해 */ }
    renderRecentIssues();
  });
  // '놓친 브리핑부터 보기' — 지난 브리핑 행(data-go-date)과 같은 경로.
  document.getElementById("returnNote")?.addEventListener("click", event => {
    const jump = event.target.closest("[data-return-date]");
    if (!jump || !briefingDates().includes(jump.dataset.returnDate)) return;
    state.briefingDate = jump.dataset.returnDate;
    renderDateSelect();
    renderBriefing();
    renderSystemStatus();
    syncUrl();
    document.getElementById("leadIssue")?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
  });
  // 발견 허브·엔티티 헤더는 필터 조작 전용 — 이슈 액션 위임과 분리해 받는다.
  ["exploreHub", "entityHeader"].forEach(id => {
    document.getElementById(id).addEventListener("click", handleHubAction);
  });
  // 팔로우 패널 — 대상 열기(그 시점에 확인 처리)·해제. 저장 화면 진입만으로는
  // 확인 처리하지 않는다(주석 계약은 index.html 의 followPanel 에).
  document.getElementById("followPanel").addEventListener("click", event => {
    const unfollow = event.target.closest("[data-unfollow]");
    if (unfollow) { toggleFollow(unfollow.dataset.unfollow); return; }
    const open = event.target.closest("[data-follow-open]");
    if (!open) return;
    markEntitySeen(open.dataset.followOpen);
    state.archiveQuery = "";
    state.archiveEntity = open.dataset.followOpen;
    state.archiveRegion = "전체";
    state.archiveTopic = "전체";
    state.archivePeriod = "all";
    state.archiveVerification = "전체";
    switchView("search");
    renderArchiveSearch(true);
    syncUrl("push");
  });
  // 지난 브리핑 행 — 그 날짜의 오늘 화면으로 점프(dateSel 변경과 같은 경로).
  document.getElementById("briefingTimelineList").addEventListener("click", event => {
    const row = event.target.closest("[data-go-date]");
    if (!row || !briefingDates().includes(row.dataset.goDate)) return;
    state.briefingDate = row.dataset.goDate;
    renderDateSelect();
    renderBriefing();
    renderSystemStatus();
    switchView("news");
  });

  document.getElementById("showChangedIssues").addEventListener("click", () => {
    const section = document.getElementById("changedIssues");
    (section.hidden ? document.getElementById("todayIssues") : section)
      .scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
  });
  const briefAudio = document.getElementById("audioEl");
  document.getElementById("audioToggle").addEventListener("click", () => {
    if (briefAudio.paused) briefAudio.play().catch(() => {});
    else briefAudio.pause();
  });
  document.getElementById("audioRates").addEventListener("click", event => {
    const button = event.target.closest("[data-rate]");
    if (!button) return;
    const rate = Number(button.dataset.rate);
    if (!AUDIO_RATES.includes(rate)) return;
    localStorage.setItem("nuclens-audio-rate", String(rate));
    briefAudio.playbackRate = rate;
    syncAudioRateButtons();
  });
  // playbackRate 는 src 교체 때 1.0 으로 돌아온다 — 재생 시작마다 다시 얹는다.
  briefAudio.addEventListener("play", () => {
    briefAudio.playbackRate = audioRate();
    updateAudioToggle(true);
    // 좁은 화면에서 접혀 있던 배속 세그먼트를 이때 펼친다 — 듣기 시작한
    // 사용자에게만 의미 있는 조절이라 그 전에는 첫 화면 공간을 안 쓴다.
    document.getElementById("audioBrief").classList.add("started");
  });
  briefAudio.addEventListener("pause", () => updateAudioToggle(false));
  briefAudio.addEventListener("ended", () => updateAudioToggle(false));
  briefAudio.addEventListener("timeupdate", () => {
    const total = Number.isFinite(briefAudio.duration) && briefAudio.duration > 0
      ? briefAudio.duration : state.audio?.duration_sec;
    document.getElementById("audioTime").textContent =
      `${fmtClock(briefAudio.currentTime)} / ${fmtClock(total)}`;
  });
  // 캐시 유실 등으로 mp3 가 404 면 죽은 버튼을 남기지 않는다
  briefAudio.addEventListener("error", () => {
    document.getElementById("audioBrief").hidden = true;
  });

  document.getElementById("regionTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-region]");
    if (!button) return;
    state.region = button.dataset.region;
    setPressed(event.currentTarget, button);
    renderBriefing();
    syncUrl();
  });
  document.getElementById("topicSel").addEventListener("change", event => {
    state.topic = event.target.value;
    renderBriefing();
    syncUrl();
  });
  document.getElementById("clearFilters").addEventListener("click", clearBriefingFilters);
  document.getElementById("closeFilters").addEventListener("click", () => closeFilterDrawer(document.getElementById("briefingFilters")));
  document.getElementById("closeArchiveFilters").addEventListener("click", () => closeFilterDrawer(document.getElementById("archiveFilterDrawer")));
  initFilterDrawers();
  document.getElementById("issueSort").addEventListener("change", event => { state.issueSort = event.target.value; renderBriefing(); });
  document.getElementById("issueViewToggle").addEventListener("click", event => {
    const button = event.target.closest("[data-issue-view]");
    if (!button) return;
    state.issueView = button.dataset.issueView;
    setPressed(event.currentTarget, button);
    renderBriefing();
  });
  document.getElementById("dateSel").addEventListener("change", event => {
    state.briefingDate = event.target.value;
    renderDateSelect();
    renderBriefing();
    renderSystemStatus();
    syncUrl();
  });
  document.getElementById("prevDay").addEventListener("click", () => stepBriefing(1));
  document.getElementById("nextDay").addEventListener("click", () => stepBriefing(-1));

  document.getElementById("archiveRegion").addEventListener("change", event => { state.archiveRegion = event.target.value; renderArchiveSearch(true); syncUrl(); });
  document.getElementById("archiveTopic").addEventListener("change", event => { state.archiveTopic = event.target.value; renderArchiveSearch(true); syncUrl(); });
  document.getElementById("archiveVerification").addEventListener("change", event => { state.archiveVerification = event.target.value; renderArchiveSearch(true); syncUrl(); });
  document.getElementById("archiveSort").addEventListener("change", event => { state.archiveSort = event.target.value; renderArchiveSearch(); });
  document.getElementById("archivePeriod").addEventListener("click", event => {
    const button = event.target.closest("[data-period]");
    if (!button) return;
    state.archivePeriod = button.dataset.period;
    setPressed(event.currentTarget, button);
    renderArchiveSearch(true);
    syncUrl();
  });
  document.getElementById("archiveClear").addEventListener("click", clearArchiveFilters);
  document.getElementById("archiveMore").addEventListener("click", () => { state.archiveLimit += 20; renderArchiveSearch(); });

  document.getElementById("periodTabs").addEventListener("click", event => {
    const button = event.target.closest("[data-period]");
    if (!button) return;
    state.period = button.dataset.period;
    setPressed(event.currentTarget, button);
    renderTrend();
  });
  document.getElementById("keywordSort").addEventListener("click", event => {
    const button = event.target.closest("[data-sort]");
    if (!button) return;
    state.keywordSort = button.dataset.sort;
    setPressed(event.currentTarget, button);
    renderKeywordTable();
  });

  document.getElementById("globalSearchOpen").addEventListener("click", openGlobalSearch);
  document.getElementById("archiveSearchOpen").addEventListener("click", openGlobalSearch);
  document.getElementById("globalSearchClose").addEventListener("click", () => document.getElementById("globalSearchDialog").close());
  document.getElementById("globalSearchForm").addEventListener("submit", event => {
    event.preventDefault();
    // 화살표로 고른 결과가 있으면 Enter 는 그 결과를 연다. 없으면 기존 경로 —
    // 검색어를 들고 탐색 화면으로 간다(이 경로의 동작·문구는 잠금).
    const active = searchOptions()[searchActiveIndex];
    if (active) { applySearchResult(active); return; }
    saveRecentSearch(document.getElementById("globalSearch").value);
    state.archiveQuery = normalizedSearch(document.getElementById("globalSearch").value);
    document.getElementById("globalSearchDialog").close();
    switchView("search");
    renderArchiveSearch(true);
  });
  let searchDebounce = 0;
  document.getElementById("globalSearch").addEventListener("input", event => {
    const query = normalizedSearch(event.target.value);
    document.getElementById("globalSearchHint").textContent = query ? `“${query}” 검색` : "검색어를 입력하세요.";
    window.clearTimeout(searchDebounce);
    searchDebounce = window.setTimeout(renderSearchResults, 120);
  });
  document.getElementById("globalSearch").addEventListener("keydown", event => {
    if (event.key === "ArrowDown") { event.preventDefault(); moveSearchActive(1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); moveSearchActive(-1); }
  });
  document.getElementById("globalSearchResults").addEventListener("click", event => {
    const removeButton = event.target.closest("[data-recent-remove]");
    if (removeButton) {
      removeRecentSearch(removeButton.dataset.recentRemove);
      renderSearchResults();
      return;
    }
    if (event.target.closest("[data-recent-clear]")) {
      try { localStorage.removeItem("nuclens-recent-searches"); } catch { /* 무해 */ }
      renderSearchResults();
      return;
    }
    const starter = event.target.closest("[data-search-starter]");
    if (starter) { applySearchResult(starter); return; }
    const option = event.target.closest('[role="option"]');
    if (option) applySearchResult(option);
  });
  document.addEventListener("keydown", event => {
    const tag = event.target.tagName;
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag) || event.target.isContentEditable;
    if ((event.key === "/" && !typing) || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k")) {
      event.preventDefault();
      openGlobalSearch();
    }
  });

  document.getElementById("themeToggle").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
    if (state.view === "trend") renderSlopeGraph();
  });
  document.getElementById("headerStatus").addEventListener("click", () => document.getElementById("statusDialog").showModal());
  document.getElementById("statusDialogClose").addEventListener("click", () => document.getElementById("statusDialog").close());
  document.getElementById("issueDialogClose").addEventListener("click", () => closeIssueDialog());
  document.getElementById("issueDialog").addEventListener("cancel", event => { event.preventDefault(); closeIssueDialog(); });
  document.getElementById("issueDialog").addEventListener("click", event => { if (event.target === event.currentTarget) closeIssueDialog(); });
  document.getElementById("issueDialog").addEventListener("close", () => { state.issueId = ""; syncUrl(); });
}

// 화면이 여러 곳에서 서로 다른 말을 하면 사용자는 무엇을 믿을지 정해야 한다.
// 실패했을 때 히어로만 "오늘의 핵심을 정리하고 있습니다"로 남아 있으면, 아래
// 오류 카드와 정면으로 모순된다 — 미완성인지 고장인지 알 수 없는 상태가 된다.
// 여기서 히어로·푸터·헤더·상태띠를 한 문장으로 맞춘다.
function renderFailureCopy(lead, headline) {
  const kicker = document.getElementById("briefingKicker");
  const title = document.getElementById("briefingTitle");
  const header = document.getElementById("headerStatus");
  const footer = document.getElementById("footerStatus");
  if (kicker) kicker.textContent = lead;
  if (title) title.textContent = headline;
  if (header) {
    header.className = "header-status error";
    header.innerHTML = `<i aria-hidden="true"></i><span>${esc(lead)}</span>`;
    header.setAttribute("aria-label", `데이터 상태 ${lead}`);
  }
  // "서비스 상태 확인 중" 은 index.html 의 초기값이다. 실패해도 그대로 남아
  // 있으면 영원히 확인만 하는 것처럼 읽힌다.
  if (footer) footer.textContent = `서비스 상태 ${lead}`;
  // 데이터가 없으면 눌러서 갈 곳도 없다. 남겨두면 죽은 컨트롤이 된다 —
  // renderEmptyBriefing 이 이슈 0건에 하는 처리와 같은 계약.
  for (const id of ["audioBrief", "headlineEvidence", "changedIssues", "showChangedIssues"]) {
    document.getElementById(id)?.setAttribute("hidden", "");
  }
  // 날짜 선택기는 목록이 비어 있어 빈 상자로 남는다. 이전·다음도 갈 데가 없다.
  for (const id of ["dateSel", "prevDay", "nextDay"]) {
    document.getElementById(id)?.setAttribute("disabled", "");
  }
}

function renderLoadError(error) {
  const strip = document.getElementById("systemStatus");
  const willRetry = initRetryCount <= 5;
  const delay = [5000, 20000, 40000, 60000, 90000][Math.max(0, initRetryCount - 1)] || 90000;
  const lead = willRetry ? "연결 실패" : "연결 실패 — 자동 재시도 중단";
  strip.className = "status-strip error";
  strip.innerHTML = `<div class="wrap status-strip-inner"><span class="status-lead"><span class="status-dot" aria-hidden="true"></span><strong>${esc(lead)}</strong></span><span class="status-item">${
    willRetry ? `${Math.round(delay / 1000)}초 뒤 다시 시도합니다` : "'다시 시도'를 눌러 주세요"
  }</span></div>`;
  renderFailureCopy(lead, "브리핑을 불러오지 못했습니다");
  document.getElementById("issueList").classList.remove("skeleton-list");
  // 재시도가 끝났는데도 "잠시 후 다시 시도해 주세요"라고 하면 거짓말이 된다 —
  // 그 시점부터는 아무도 다시 시도하지 않는다.
  const guidance = willRetry
    ? `${Math.round(delay / 1000)}초 뒤 자동으로 다시 시도합니다.`
    : "자동 재시도를 5회 모두 실패했습니다. 아래 버튼으로 다시 시도해 주세요.";
  document.getElementById("issueList").innerHTML = `<div class="error-state"><strong>데이터를 불러오지 못했습니다</strong><p>${guidance}</p><small>${esc(error.message)}</small><div><button type="button" id="retryInit">다시 시도</button><a href="mailto:policy174@naver.com">문의</a></div></div>`;
  document.getElementById("retryInit")?.addEventListener("click", () => { initRetryCount = 0; init(); });
  if (willRetry) initRetryTimer = window.setTimeout(init, delay);
}

async function init() {
  if (!eventsBound) {
    bind();
    eventsBound = true;
    window.addEventListener("online", () => { state.offline = false; if (!appReady) init(); else renderSystemStatus(); });
    window.addEventListener("offline", () => { state.offline = true; renderSystemStatus(); });
    window.addEventListener("popstate", () => { if (appReady) restoreIssueFromHistory(); });
  }
  if (appReady || initLoading) return;
  initLoading = true;
  try {
    await initializeDataBase();
    [state.news, state.briefings, state.issues, state.trend, state.meta, state.insights, state.pubs, state.audio, state.entities] = await Promise.all([
      loadJSON("news.json"), loadJSON("briefings.json"), loadJSON("issues.json"),
      loadJSON("trend.json"), loadJSON("meta.json"), loadJSON("insights.json"),
      // 발간물은 부가 데이터 — 없어도 사이트 전체가 죽으면 안 된다 (8/1 빈 화면 사고 계약)
      loadJSON("publications.json").catch(() => null),
      // 오디오는 세대 폴더가 아니라 data/ 루트에 산다(daily-brief 가 하루 1회 생성).
      // 없거나 깨져도 플레이어만 숨는다 — 같은 비치명 계약.
      loadRootJSON("audio/audio.json", true).catch(() => null),
      // 엔티티 사전도 부가 데이터 — 없으면 허브의 대상 그룹만 비고 나머지는 산다.
      loadJSON("entities.json").catch(() => null),
    ]);
  } catch (error) {
    initLoading = false;
    initRetryCount += 1;
    window.clearTimeout(initRetryTimer);
    renderLoadError(error);
    return;
  }
  window.clearTimeout(initRetryTimer);
  initRetryCount = 0;
  // 재시도로 살아났을 때 실패 화면이 잠가둔 것을 되돌린다. 안 풀면 데이터가
  // 정상인데도 날짜 이동이 죽은 채로 남는다.
  for (const id of ["dateSel", "prevDay", "nextDay"]) {
    document.getElementById(id)?.removeAttribute("disabled");
  }
  loadSaved();
  loadFollows();
  state.briefingDate = state.meta.latest_briefing_date || state.briefings[0]?.date || "";
  restoreUrlState();
  renderTopicSelects();
  document.getElementById("topicSel").value = state.topic;
  document.getElementById("archiveRegion").value = state.archiveRegion;
  document.getElementById("archiveTopic").value = state.archiveTopic;
  document.getElementById("archiveVerification").value = state.archiveVerification;
  document.getElementById("globalSearch").value = state.archiveQuery;
  setPressed(document.getElementById("regionTabs"), document.querySelector(`#regionTabs [data-region="${state.region}"]`));
  setPressed(document.getElementById("archivePeriod"), document.querySelector(`#archivePeriod [data-period="${state.archivePeriod}"]`));
  const firstIssueDate = state.issues.reduce((oldest, issue) => !oldest || issue.first_seen < oldest ? issue.first_seen : oldest, "");
  // 이슈 수와 원문 수는 다른 단위다. 한 숫자로 뭉치면 규모를 오해한다.
  const catalogArticles = state.issues.reduce((sum, issue) => sum + (issue.article_count || 0), 0);
  document.getElementById("archiveCatalogMeta").textContent =
    `${state.issues.length}개 이슈 · ${catalogArticles}개 원문 · ${dateLabel(firstIssueDate)}–${dateLabel(state.meta.latest_briefing_date)}`;
  renderDateSelect();
  renderBriefing();
  renderArchiveSearch();
  renderTrend();
  renderSaved();
  renderSystemStatus();
  renderReturnNote();
  switchView(state.view, false);
  if (state.issueId && state.view !== "trend") openIssueDialog(state.issueId, false);
  syncUrl();
  appReady = true;
  initLoading = false;
  if (!generationTimer) generationTimer = window.setInterval(checkForNewGeneration, 60000);
}

initializeTheme();
init();
