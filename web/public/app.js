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

const OFFICIAL_HINTS = ["go.kr", "khnp", "kaeri", "iaea.org", "energy.gov", "nrc.gov"];
const VIEW_IDS = ["news", "search", "trend", "saved", "pubs"];
const ISSUE_ROUTE = /^\/issue\/([^/]+)\/?$/;

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

function issueIdFromLocation() {
  const match = location.pathname.match(ISSUE_ROUTE);
  if (!match) return "";
  try { return decodeURIComponent(match[1]); } catch { return ""; }
}

function issuePath(issueId) {
  return `/issue/${encodeURIComponent(issueId)}`;
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
  if (!followed.length) {
    panel.innerHTML = `<p class="follow-empty">탐색에서 원전·기업·기관을 팔로우하면 새 이슈를 여기서 셉니다.
      <button type="button" data-go-view="search">탐색 열기</button></p>`;
    return;
  }
  panel.innerHTML = `<div class="section-heading compact"><div><h2>팔로우한 대상</h2></div></div>`
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
  const path = state.issueId && state.view !== "trend" ? issuePath(state.issueId) : "/";
  const url = `${path}${query ? `?${query}` : ""}`;
  const historyState = { ...(history.state || {}), nuclensIssue: state.issueId || null };
  if (mode === "push") history.pushState(historyState, "", url);
  else history.replaceState(historyState, "", url);
}

function restoreUrlState() {
  const params = new URLSearchParams(location.search);
  const requestedDate = params.get("date");
  if (briefingDates().includes(requestedDate)) state.briefingDate = requestedDate;
  const requestedRegion = params.get("region");
  if (["전체", "국내", "해외"].includes(requestedRegion)) state.region = requestedRegion;
  state.topic = params.get("topic") || "전체";
  state.view = VIEW_IDS.includes(params.get("view")) ? params.get("view") : "news";
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
  const why = (issue.implication || "").trim();
  const leadText = why || issue.summary || "";
  const lead = archive ? markMatch(leadText, state.archiveQuery) : esc(leadText);
  // 검색 하이라이트 판정도 화면에 실제로 뜨는 문장을 기준으로 해야 한다.
  const visibleMatch = normalizedSearch(`${issue.title || ""} ${leadText}`).includes(state.archiveQuery);
  const matchContext = archive && state.archiveQuery && !visibleMatch
    ? `<p class="search-match">검색 조건 <mark>${esc(state.archiveQuery)}</mark>과 연결된 이슈입니다.</p>`
    : "";
  // 시안의 목록은 표다 — 순서 / 변화 / 이슈 / 근거 네 열. 그래서 '변화'와 '근거'는
  // .issue-body 밖으로 나와 각자 열이 된다. 이 둘을 body 안에 두고 CSS
  // display:contents 로 흩으면 제목과 요약이 서로 다른 그리드 행으로 갈라져,
  // 근거 열 높이(98px)가 그 사이 여백으로 배분된다(실측 42px). 열은 열로 나눈다.
  return `<article class="issue-card ${archive ? "archive-card" : ""} ${front ? "front" : ""} ${issueToneClass(issue)}">
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
      ${leadText ? `<p class="issue-summary${why ? " issue-why" : ""}">${why ? `<span class="ai-badge">AI</span>` : ""}${lead}</p>` : ""}
      ${matchContext}
      ${keeiRefLine(issue)}
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
  // 히어로 h1 이 이미 이 이슈 제목이면(headline_kind="issue") 바로 아래에서
  // 되풀이하지 않는다. 종합 문장일 때는 서로 다른 문장이라 제목이 선다.
  const sameAsHeadline = String(briefing.headline || "").trim() === String(issue.title || "").trim();
  const blocks = [
    issue.summary ? { label: "무슨 일", text: issue.summary } : null,
    model.why ? { label: model.why.label, text: model.why.text } : null,
    model.impact ? { label: model.impact.label, text: model.impact.text } : null,
    model.change ? { label: model.change.label, text: model.change.text, tone: "change" } : null,
    model.openQuestion ? { label: "아직 확정되지 않은 것", text: model.openQuestion, tone: "open" } : null,
  ].filter(Boolean);
  return `<article class="lead-card ${issueToneClass(issue)}">
    <div class="lead-meta">
      <span class="issue-state">${esc(issueStatusText(issue))}</span>
      <span>${esc(issue.region)}</span>
      ${countryChips.map(label => `<span>${esc(label)}</span>`).join("")}
      ${topic ? `<span>${esc(topic)}</span>` : ""}
      ${verificationBadge(issue)}
      ${reportPickBadge(issue)}
    </div>
    ${sameAsHeadline ? "" : `<h3><button type="button" class="issue-title-button" data-issue-id="${esc(issue.issue_id)}">${esc(issue.title)}</button></h3>`}
    <dl class="lead-blocks">${blocks.map(block => `<div class="lead-block${block.tone ? ` tone-${block.tone}` : ""}">
      <dt>${esc(block.label)}</dt><dd>${esc(block.text)}</dd>
    </div>`).join("")}</dl>
    ${keeiRefLine(issue)}
    ${issueActions(issue)}
  </article>`;
}

function renderBriefingSidebar(briefing, leadId = "") {
  // 근거 패널의 기본 선택 — 비워두면 사이드 첫 칸이 빈 채로 시작한다.
  // 선택이 이번 브리핑에 없는 이슈를 가리키면(날짜 이동 등) 다시 잡는다.
  // 선두 카드가 그 이슈의 영향·근거를 이미 펼쳐 놓았으므로 기본값은 그다음
  // 이슈로 잡는다 — 안 그러면 한 화면에 같은 문장이 두 번 선다. 사용자가 선두
  // 카드를 직접 누르면 handleIssueAction 이 패널을 그리로 옮긴다.
  const inBriefing = briefing.issues.some(issue => issue.issue_id === state.railIssueId);
  if (!inBriefing || state.railIssueId === leadId) {
    const next = briefing.issues.find(issue => issue.issue_id !== leadId) || briefing.issues[0];
    state.railIssueId = next?.issue_id || "";
  }
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
  const weekly = (state.insights?.featured_items || state.insights?.items || []).slice(0, 3);
  document.getElementById("sideWeekly").innerHTML = weekly.length
    ? weekly.map(item => `<li><strong>${esc(item.keyword)}</strong><small>이번 주 ${item.count_now}회 · ${item.count_now - item.count_prev >= 0 ? "+" : ""}${item.count_now - item.count_prev}</small></li>`).join("")
    : "<li class=\"empty\">주간 흐름을 준비하고 있습니다.</li>";
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
  document.getElementById("briefingTitle").textContent = view.title;
  document.getElementById("briefingKicker").textContent = "오늘의 브리핑";
  // 히어로가 이미 사유를 말했으므로 목록에서 같은 문장을 되풀이하지 않는다.
  // 목록은 '그래서 어디로 가면 되는가'만 담당한다.
  document.getElementById("showChangedIssues").hidden = true;
  // 근거 칩도 함께 지운다 — 안 그러면 직전 브리핑의 근거가 남아 없는 문장을 가리킨다
  const staleEvidence = document.getElementById("headlineEvidence");
  if (staleEvidence) { staleEvidence.hidden = true; staleEvidence.innerHTML = ""; }
  issueList.innerHTML = `<div class="empty-state"><p>${view.detail}</p></div>`;
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
  // 히어로는 문장을 내지 않는다.
  //
  // 여기 있던 h1 은 17일 내내 issues[0].title 이었다 — 같은 페이지에 여섯 번 나오는
  // 문자열을 모바일 첫 화면의 45% 에 45px 로 얹고 있었다. daily_lead 가 나중에
  // 실제 문장을 만들기 시작했지만 두 이슈 제목을 '가운데' 로 이은 것이라 새 정보는
  // 그 한 단어뿐이었다. topics 로 '오늘의 축'을 뽑아 짧은 딱지를 다는 안도 만들어
  // 20일치로 재봤는데, 축이 잡히는 5일 중 1일이 '중국 신규건설 + 그리스 가뭄' 처럼
  // 넓은 태그 하나로 묶인 남남이었다. 20일에 한 번 없는 통찰을 있다고 주장하느니
  // 매일 아무 말도 안 하는 편이 낫다.
  //
  // h1 을 DOM 에서 지우지는 않는다: view-news 가 aria-labelledby 로 이 id 를
  // 가리키고 있어 없애면 섹션이 이름을 잃는다. 날짜 제목으로 바꿔 sr-only 로 둔다.
  document.getElementById("briefingTitle").textContent =
    `${dateWeekdayLabel(briefing.date)} 브리핑`;

  // 두 클래스 모두 항상 붙는다 — 히어로에 문장이 없으니 압축 상태가 곧 유일한
  // 상태다. lead-issue 는 모바일에서 날짜 라벨·액션 줄을 걷어 106px 를 만들고,
  // no-lead 는 킥커·h1·근거 칩을 걷는다. 오디오 브리핑은 둘 다 건드리지 않는다 —
  // 그날 음원이 있으면 그대로 나온다.
  const hero = document.getElementById("briefingHero");
  if (hero) hero.classList.add("lead-issue", "no-lead");
  document.getElementById("briefingDateLabel").textContent = `· ${dateWeekdayLabel(briefing.date)}`;
  // 근거 칩은 히어로가 문장을 낼 때 그 문장이 어디서 왔는지 보이려고 있었다.
  // 낼 문장이 없으니 칩도 없다. 컨테이너는 남긴다 — index.html 이 참조한다.
  const evidenceBox = document.getElementById("headlineEvidence");
  if (evidenceBox) {
    evidenceBox.hidden = true;
    evidenceBox.innerHTML = "";
  }

  const changed = changedIssues(briefing);
  const changedIds = new Set(changed.map(issue => issue.issue_id));
  // 선두로 올린 이슈는 아래 두 목록에서 뺀다 — 같은 이슈가 한 화면에 두 번 서면
  // 개수 표시("8개 이슈")도 실제 카드 수와 어긋난다.
  const rest = issues.filter(issue => !changedIds.has(issue.issue_id) && issue.issue_id !== leadId);
  const changedSection = document.getElementById("changedIssues");
  const visibleChanged = changed.filter(issue => issueMatchesFilters(issue) && issue.issue_id !== leadId);
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
  button.textContent = playing ? "⏸ 일시정지" : "▶ 오디오 브리핑";
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

function articleTimelineRow(article, briefingDate, currentStage = "이번 브리핑") {
  const url = safeUrl(article.url);
  const stage = article.briefing_date === briefingDate ? currentStage : "이전 흐름";
  return `<li>
    <div class="timeline-date"><span>${esc(dateLabel(article.article_date))}</span><small>${esc(relativeArticleDate(article.article_date, briefingDate))}</small><em>${stage}</em></div>
    <div class="timeline-copy">
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(article.title_kr)}</a>` : `<span>${esc(article.title_kr)}</span>`}
      <small>${esc(sourceLabel(article))}${isOfficial(article) ? " · 1차 출처" : ""}</small>
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

function renderEvidenceRail() {
  const rail = document.getElementById("evidenceRail");
  if (!rail) return;
  const issue = state.railIssueId ? currentIssueById(state.railIssueId) : null;
  if (!issue) { rail.hidden = true; rail.innerHTML = ""; return; }
  const model = issueDetailModel(issue, state.briefingDate);
  const sourceArticle = model.source.article;
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
    </div>
    <div class="rail-body">
      ${model.change ? `<section class="rail-block">
        <p class="rail-no">${no()} / ${esc(model.change.label)}</p>
        <p>${esc(model.change.text)}</p>
      </section>` : ""}
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
  const dialog = document.getElementById("issueDialog");
  const topics = (issue.topics || []).map(topic => `<span class="topic-chip">${esc(TOPIC_LABELS[topic] || topic)}</span>`).join("");
  const contextDate = state.view === "news" ? state.briefingDate : issue.last_seen;
  const articles = [...(issue.related_articles || [])].sort((a, b) => (
    Number(isOfficial(b)) - Number(isOfficial(a)) || String(b.article_date).localeCompare(String(a.article_date))
  ));
  const related = relatedIssues(issue);
  document.getElementById("issueDialogContent").innerHTML = `
    <h2 id="issueDialogTitle" tabindex="-1">${esc(issue.title)}</h2>
    <div class="dialog-meta"><span>${esc(issueStatusText(issue, state.view !== "news"))}</span><span>${dateLabel(issue.first_seen)} 시작</span><span>누적 ${issue.article_count}건</span></div>
    <section class="dialog-update" aria-labelledby="issueUpdateTitle">
      <h3 id="issueUpdateTitle">한 줄 결론</h3>
      ${issue.summary ? `<p>${esc(issue.summary)}</p>` : '<p class="empty">요약이 없습니다.</p>'}
      ${issueChangeText(issue) ? `<p class="dialog-change"><strong>이번에 달라진 점</strong>${esc(issueChangeText(issue))}</p>` : ""}
      <p class="dialog-verification">${verificationBadge(issue, { always: true })}${reportPickBadge(issue)}<span>${esc(issueEvidenceText(issue))}</span></p>
      ${issue.why_important ? `<p class="dialog-meaning"><strong>왜 중요한가 <span class="ai-badge">AI</span></strong>${esc(issue.why_important)}</p>` : ""}
      ${issue.implication ? `<p class="dialog-meaning"><strong>시사점 <span class="ai-badge">AI</span></strong>${esc(issue.implication)}</p>` : ""}
      ${issue.open_question ? `<p class="dialog-open"><strong>아직 확정되지 않은 것</strong>${esc(issue.open_question)}</p>` : ""}
      ${topics ? `<div class="topic-row">${topics}</div>` : ""}
      <div class="dialog-actions"><button type="button" data-copy-issue="${esc(issue.issue_id)}">보고서용 복사</button><button type="button" data-pack-issue="${esc(issue.issue_id)}">자료 팩 복사</button><button type="button" data-save-issue="${esc(issue.issue_id)}">${state.savedIds.has(issue.issue_id) ? "저장됨" : "저장"}</button><button type="button" data-share-issue="${esc(issue.issue_id)}">공유</button></div>
    </section>
    ${keeiDialogSection(issue)}
    <section class="dialog-history" aria-labelledby="issueHistoryTitle">
      <div class="dialog-section-head"><h3 id="issueHistoryTitle">사건 타임라인과 근거 원문</h3></div>
      <ol class="timeline dialog-timeline">${articles.map(article => articleTimelineRow(article, contextDate, state.view === "news" ? "이번 브리핑" : "최근 브리핑")).join("")}</ol>
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
function renderInsights() {
  const box = document.getElementById("insightList");
  const movers = (state.trend?.weekly_movers || []).filter(item => item && item.title);
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
  });
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

function renderWeeklyReport() {
  const panel = document.getElementById("weeklyReport");
  const report = state.trend?.weekly_report;
  const questions = state.trend?.open_questions || [];
  // 리포트가 없으면 통째로 숨긴다 — 빈 탭이 되면 안 되므로 아래 정량 트렌드가
  // 그대로 남는다.
  //
  // 원래는 `!report && !questions.length` 였다. 그런데 weekly_reports.json 이
  // 3개월째 한 번도 생성된 적이 없어(weekly.yml 미가동) 실제로는 '아직 결론
  // 나지 않은 것' 한 코너만 '주간 판세' 라는 제목을 달고 떠 있었다. 5칸 중
  // 4칸이 빈 채로 제목이 판세를 약속하는 셈이다.
  // 게다가 그 한 코너의 문장은 근거 이슈 제목의 서술문 전환에 가깝고(실측
  // 2건: 유사도 0.32·0.48 — 어순·어미만 바꾸면 문자열 유사도로는 못 거른다),
  // 같은 open_question 은 이미 선두 카드와 상세 모달에 '아직 확정되지 않은
  // 것'으로 나온다. 세 번째 노출을 위해 제목을 빌려 쓰지 않는다.
  // 진짜 주간 리포트가 생기면 5칸이 함께 돌아온다.
  if (!report) { panel.hidden = true; return; }
  panel.hidden = false;

  document.getElementById("weeklyReportMeta").textContent = report
    ? `${dateLabel(report.week_start)}–${dateLabel(report.week_end)} · 이슈 ${report.source_issue_count ?? 0}건`
    : "";

  const shifts = (report?.policy_shifts || []).filter(row => row && row.what);
  const themes = (report?.theme_moves || []).filter(row => row && row.theme);
  const watch = report?.watchpoints || [];
  const arrow = { "강화": "▲", "약화": "▼", "유지": "―" };

  document.getElementById("weeklyReportBody").innerHTML = [
    weeklySection("이번 주 판을 바꾼 것", "",
      [report?.weekly_intro ? `<p class="weekly-intro">${esc(report.weekly_intro)}</p>` : "",
       shifts.map(row =>
         `<div class="weekly-item"><p><strong>${esc(row.what)}</strong></p>`
         + (row.so_what ? `<p>${esc(row.so_what)}</p>` : "")
         + evidenceChips(row.evidence) + `</div>`).join("")].join("")),
    weeklySection("조용하지만 놓치면 안 되는 것", "투자 테마 강약",
      themes.map(row =>
        `<div class="weekly-item"><p><strong>${esc(arrow[row.direction] || "―")} ${esc(row.theme)}</strong>`
        + (row.why ? ` — ${esc(row.why)}` : "") + `</p>`
        + evidenceChips(row.evidence) + `</div>`).join("")),
    weeklySection("한수원에 직접 닿는 변화", "",
      report?.khnp_direct ? `<p>${esc(report.khnp_direct)}</p>` : ""),
    weeklySection("다음 주 하나만 본다면", "",
      watch.length ? `<ul class="weekly-list">${watch.map(row => `<li>${esc(row)}</li>`).join("")}</ul>` : ""),
    weeklySection("아직 결론 나지 않은 것", "이슈당 한 번만 · 최신순",
      questions.length ? questions.map(row =>
        `<div class="weekly-item"><p>${esc(row.text)}</p>${evidenceChips(row.evidence)}</div>`
      ).join("") : ""),
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

function renderTrend() {
  renderWeeklyReport();
  renderInsights();
  renderTrendReadiness();
  // 지난 브리핑은 트렌드 집계 준비 여부와 무관하다 — 이른 return 앞에서 그린다.
  renderBriefingTimeline();
  if (!state.meta?.trend_ready) return;
  renderKeywordTable();
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
  document.querySelectorAll("[data-view]").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (view === "search") renderArchiveSearch();
  if (view === "trend") renderTrend();
  if (view === "saved") renderSaved();
  if (view === "pubs") renderPubs();
  if (updateUrl) syncUrl();
  scrollToPageTop();
}

/* 필터 서랍 — 좁은 화면에서는 바텀시트, 넓은 화면에서는 기존 드롭다운·사이드바.
   <details> 는 ESC·바깥 탭·포커스 복귀를 스스로 해 주지 않는다. 바텀시트 모양을
   하고 있으면 사용자는 그 셋을 기대하므로 여기서 직접 붙인다. */
const narrowScreen = matchMedia("(max-width: 767px)");

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
    box.innerHTML = recent.length
      ? `<div class="search-group"><h3>최근 검색<button type="button" class="search-clear-recent" data-recent-clear>전체 삭제</button></h3>`
        + recent.map((item, index) => searchOptionRow(`sr-${index}`,
          `<span>${esc(item)}</span><button type="button" class="search-remove" data-recent-remove="${esc(item)}" aria-label="‘${esc(item)}’ 삭제">×</button>`,
          { "search-query": item })).join("")
        + "</div>"
      : "";
    input.setAttribute("aria-expanded", String(recent.length > 0));
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
      state.railIssueId = detail.dataset.issueId;
      renderEvidenceRail();
      document.getElementById("evidenceRail")?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
      return true;
    }
    openIssueDialog(detail.dataset.issueId);
    return true;
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
  ["issueList", "changedList", "leadCard", "archiveIssueList", "savedIssueList", "issueDialog",
   "headlineEvidence", "weeklyReportBody", "insightList", "evidenceRail", "briefingTitle"].forEach(id => {
    document.getElementById(id).addEventListener("click", handleIssueAction);
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

function renderLoadError(error) {
  const strip = document.getElementById("systemStatus");
  strip.className = "status-strip error";
  strip.innerHTML = '<div class="wrap status-strip-inner"><span class="status-dot"></span><strong>데이터 연결 실패</strong><span>·</span><span>마지막 정상 데이터를 불러오지 못했습니다</span></div>';
  const delay = [5000, 20000, 40000, 60000, 90000][Math.max(0, initRetryCount - 1)] || 90000;
  document.getElementById("issueList").classList.remove("skeleton-list");
  document.getElementById("issueList").innerHTML = `<div class="error-state"><strong>데이터를 불러오지 못했습니다</strong><p>잠시 후 다시 시도해 주세요. 문제가 계속되면 알려주세요.</p><small>${esc(error.message)}</small><div><button type="button" id="retryInit">다시 시도</button><a href="mailto:policy174@naver.com">문의</a></div></div>`;
  document.getElementById("retryInit")?.addEventListener("click", () => { initRetryCount = 0; init(); });
  if (initRetryCount <= 5) initRetryTimer = window.setTimeout(init, delay);
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
  switchView(state.view, false);
  if (state.issueId && state.view !== "trend") openIssueDialog(state.issueId, false);
  syncUrl();
  appReady = true;
  initLoading = false;
  if (!generationTimer) generationTimer = window.setInterval(checkForNewGeneration, 60000);
}

initializeTheme();
init();
