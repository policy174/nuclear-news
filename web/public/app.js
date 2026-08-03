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

const state = {
  news: [], briefings: [], issues: [], trend: null, insights: null, meta: null,
  pubs: null, pubsOrg: "전체",
  manifest: null, systemStatus: null, dataBase: "/data",
  briefingDate: "", region: "전체", topic: "전체", view: "news",
  issueSort: "importance", issueView: "card", issueId: "",
  archiveQuery: "", archiveRegion: "전체", archiveTopic: "전체",
  archivePeriod: "all", archiveVerification: "전체", archiveSort: "updated", archiveLimit: 20,
  period: "7", keywordSort: "mentions", savedIds: new Set(),
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
function issueChangeText(issue) {
  return issue.latest_change || "";
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
  renderSavedCount();
}

function persistSaved() {
  localStorage.setItem("nuclens-saved-issues", JSON.stringify([...state.savedIds]));
  renderSavedCount();
}

function renderSavedCount() {
  const count = document.getElementById("savedCount");
  if (count) count.textContent = String(state.savedIds.size);
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
  let message = `마지막 수집 ${timeLabel(refreshedAt)} · 오늘 수집 기사 ${briefing.article_count || 0}건 · 연결된 이슈 ${briefing.issue_count || 0}개 · 1차 출처 ${briefing.primary_source_count ?? 0}건 · 다음 갱신 2시간 이내`;

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
  strip.innerHTML = `<div class="wrap status-strip-inner"><span class="status-dot" aria-hidden="true"></span><strong>${lead}</strong><span>·</span><span>${esc(message)}</span></div>`;
  header.className = `header-status ${status}`;
  header.innerHTML = `<i aria-hidden="true"></i><span>${timeLabel(refreshedAt)} · 이슈 ${state.issues.length}</span>`;
  footer.textContent = `서비스 상태 ${lead} · 마지막 갱신 ${dateTimeLabel(refreshedAt)}`;
  document.getElementById("statusDialogContent").innerHTML = `
    <dl class="status-details">
      <div><dt>상태</dt><dd>${esc(lead)}</dd></div>
      <div><dt>마지막 수집</dt><dd>${esc(dateTimeLabel(refreshedAt))}</dd></div>
      <div><dt>오늘 원문</dt><dd>${briefing.article_count || 0}건</dd></div>
      <div><dt>연결 이슈</dt><dd>${briefing.issue_count || 0}개</dd></div>
      <div><dt>1차 출처</dt><dd>${briefing.primary_source_count ?? 0}건</dd></div>
    </dl><p>${esc(message)}</p>`;
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

function issueCard(issue, index, archive = false) {
  const topic = primaryTopicLabel(issue);
  const change = issueChangeText(issue);
  const title = archive ? markMatch(issue.title, state.archiveQuery) : esc(issue.title);
  const summary = archive ? markMatch(issue.summary, state.archiveQuery) : esc(issue.summary);
  const visibleMatch = normalizedSearch(`${issue.title || ""} ${issue.summary || ""}`).includes(state.archiveQuery);
  const matchContext = archive && state.archiveQuery && !visibleMatch
    ? `<p class="search-match">검색 조건 <mark>${esc(state.archiveQuery)}</mark>과 연결된 이슈입니다.</p>`
    : "";
  return `<article class="issue-card ${archive ? "archive-card" : ""} ${issueToneClass(issue)}">
    <div class="issue-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</div>
    <div class="issue-body">
      <div class="issue-meta">
        <span class="issue-state">${esc(issueStatusText(issue, archive))}</span>
        <span>${esc(issue.region)}</span>
        ${topic ? `<span class="issue-topic">${esc(topic)}</span>` : ""}
        ${verificationBadge(issue)}
      </div>
      <h3><button type="button" class="issue-title-button" data-issue-id="${esc(issue.issue_id)}">${title}</button></h3>
      ${issue.summary ? `<p class="issue-summary">${summary}</p>` : ""}
      ${matchContext}
      ${change ? `<p class="issue-change"><strong>변화</strong><span>${esc(change)}</span></p>` : ""}
      ${keeiRefLine(issue)}
      ${archive ? trackingPeriod(issue) : ""}
      ${issueActions(issue)}
    </div>
  </article>`;
}

function renderBriefingSidebar(briefing) {
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
  const counts = new Map();
  state.issues.forEach(issue => (issue.topics || []).forEach(topic => counts.set(topic, (counts.get(topic) || 0) + 1)));
  document.getElementById("quickTopics").innerHTML = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8)
    .map(([topic]) => `<button type="button" data-quick-topic="${esc(topic)}">${esc(TOPIC_LABELS[topic] || topic)}</button>`).join("");
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
        + `<button type="button" data-go-view="search">이슈 아카이브에서 보기</button>`,
    };
  }
  if (briefing && briefing.candidate_count === 0) {
    return {
      title: "오늘 새로 확인된 브리핑 이슈가 없습니다",
      detail: '진행 중인 이슈는 <button type="button" data-go-view="search">이슈 아카이브</button>에서 확인할 수 있습니다.',
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
  if (state.issueSort === "latest") {
    issues = [...issues].sort((a, b) => String(b.last_seen).localeCompare(String(a.last_seen)) || b.article_count - a.article_count);
  }
  document.getElementById("briefingTitle").textContent = briefing.headline || briefing.issues[0]?.title || "오늘의 핵심";
  // 오버라인은 h1이 실제로 무엇인지 따라간다. 변화 문장이 아닌데 '달라졌는가'라고
  // 쓰면 제목이 거짓말이 된다.
  document.getElementById("briefingKicker").textContent =
    ["synthesis", "change"].includes(briefing.headline_kind)
      ? "오늘, 무엇이 달라졌는가"
      : "오늘의 핵심 이슈";
  document.getElementById("briefingDateLabel").textContent = `· ${dateWeekdayLabel(briefing.date)}`;
  // 종합 문장(synthesis)일 때만 근거 이슈 칩을 보인다 — 문장이 어디서 왔는지
  // 클릭 한 번으로 검증할 수 있게. 필드가 없으면 조용히 숨긴다.
  const evidenceBox = document.getElementById("headlineEvidence");
  if (evidenceBox) {
    const evidence = briefing.headline_kind === "synthesis"
      ? (briefing.headline_evidence || []).filter(item => item && item.issue_id && item.title)
      : [];
    evidenceBox.hidden = evidence.length === 0;
    evidenceBox.innerHTML = evidence.length
      ? `<span class="hero-evidence-label">근거 이슈</span>` + evidence.map(item =>
        `<button type="button" class="hero-evidence-chip" data-issue-id="${esc(item.issue_id)}">${esc(item.title)}</button>`
      ).join("")
      : "";
  }

  const changed = changedIssues(briefing);
  const changedIds = new Set(changed.map(issue => issue.issue_id));
  const rest = issues.filter(issue => !changedIds.has(issue.issue_id));
  const changedSection = document.getElementById("changedIssues");
  const visibleChanged = changed.filter(issueMatchesFilters);
  changedSection.hidden = visibleChanged.length === 0;
  document.getElementById("changedCount").textContent = `${visibleChanged.length}개 이슈`;
  document.getElementById("changedList").innerHTML =
    visibleChanged.map((issue, index) => issueCard(issue, index)).join("");
  document.getElementById("showChangedIssues").hidden = visibleChanged.length === 0;

  document.getElementById("issueCount").textContent = `${rest.length}개 이슈`;
  issueList.classList.toggle("list-view", state.issueView === "list");
  // 위 '지금 달라진 이슈'에 결과가 남아 있는데 아래에서 '없습니다'라고 하면
  // 한 화면이 스스로를 부정한다. 두 구역을 합쳐 0건일 때만 빈 상태를 보인다.
  issueList.innerHTML = rest.length
    ? rest.map((issue, index) => issueCard(issue, index)).join("")
    : (visibleChanged.length
      ? '<p class="section-note">필터에 맞는 이슈는 위 <strong>지금 달라진 이슈</strong>에 있습니다.</p>'
      : '<div class="empty-state"><strong>조건에 맞는 이슈가 없습니다</strong><p>주제나 지역 필터를 해제해 보세요.</p><button type="button" data-clear-briefing>필터 해제</button></div>');
  const activeFilters = [];
  if (state.region !== "전체") activeFilters.push(state.region);
  if (state.topic !== "전체") activeFilters.push(TOPIC_LABELS[state.topic] || state.topic);
  document.getElementById("filterSummary").innerHTML = activeFilters.map(item => `<span>${esc(item)}</span>`).join("");
  document.getElementById("filterCount").textContent = activeFilters.length ? `(${activeFilters.length})` : "";
  const clear = document.getElementById("clearFilters");
  clear.hidden = activeFilters.length === 0;
  clear.textContent = activeFilters.length ? `필터 해제 (${activeFilters.length})` : "필터 해제";
  renderBriefingSidebar(briefing);
  renderNewsFeed();
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
    issue.title, issue.summary, issue.implication, issue.region,
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

function renderArchiveSearch(resetLimit = false) {
  if (resetLimit) state.archiveLimit = 20;
  const matches = sortArchiveIssues(state.issues.filter(archiveIssueMatches));
  const visible = matches.slice(0, state.archiveLimit);
  const activeFilters = [
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
}

function renderSaved() {
  const issues = state.issues.filter(issue => state.savedIds.has(issue.issue_id));
  document.getElementById("savedIssueList").innerHTML = issues.length
    ? issues.map((issue, index) => issueCard(issue, index, true)).join("")
    : '<div class="empty-state"><strong>저장한 이슈가 없습니다</strong><p>카드의 저장 버튼을 누르면 이 브라우저에서 다시 볼 수 있습니다.</p><button type="button" data-go-view="search">이슈 아카이브 보기</button></div>';
}

const PUB_KIND_LABELS = {
  publication: "간행물", report: "보고서", analysis: "분석", press: "보도자료",
  news_or_report: "소식·보고서", keei_insight: "정기간행물",
};

function pubRow(item) {
  const url = safeUrl(item.url);
  const pdfUrl = safeUrl(item.pdf_url || "");
  const kindLabel = PUB_KIND_LABELS[item.kind] || "";
  const tocIssue = item.toc && item.toc.issue_title ? item.toc.issue_title : "";
  // 한국어 제목이 있으면 그것이 표제다. 영문 원제는 아래에 작게 남겨
  // 원문을 찾을 때 대조할 수 있게 한다.
  const heading = item.title_kr || item.title;
  const original = item.title_kr && item.title_kr !== item.title ? item.title : "";
  return `<article class="pub-item">
    <div class="pub-meta">
      <span class="pub-org">${esc(item.org_kr || item.org)}</span>
      ${kindLabel ? `<span>${esc(kindLabel)}</span>` : ""}
      ${item.date ? `<span>${esc(dateLabel(item.date))}</span>` : ""}
      ${item.is_new ? '<span class="pub-new">NEW</span>' : ""}
    </div>
    <h3>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(heading)}</a>` : esc(heading)}</h3>
    ${item.gist ? `<p class="pub-gist">${esc(item.gist)}</p>` : ""}
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
  listBox.innerHTML = visible.length
    ? visible.map(pubRow).join("")
    : '<div class="empty-state"><strong>이 기관의 발간물이 아직 없습니다</strong><p>다른 기관을 선택해 보세요.</p></div>';
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
    issue.implication ? `• 의미(AI 해석): ${issue.implication}` : "",
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
      <p class="dialog-verification">${verificationBadge(issue, { always: true })}<span>${esc(issueEvidenceText(issue))}</span></p>
      ${issue.implication ? `<p class="dialog-meaning"><strong>Nuclens 해석 <span class="ai-badge">AI</span></strong>${esc(issue.implication)}</p>` : ""}
      ${issue.open_question ? `<p class="dialog-open"><strong>아직 확정되지 않은 것</strong>${esc(issue.open_question)}</p>` : ""}
      ${topics ? `<div class="topic-row">${topics}</div>` : ""}
      <div class="dialog-actions"><button type="button" data-copy-issue="${esc(issue.issue_id)}">보고서용 복사</button><button type="button" data-pack-issue="${esc(issue.issue_id)}">자료 팩 복사</button><button type="button" data-save-issue="${esc(issue.issue_id)}">${state.savedIds.has(issue.issue_id) ? "저장됨" : "저장"}</button><button type="button" data-share-issue="${esc(issue.issue_id)}">공유</button></div>
    </section>
    ${keeiDialogSection(issue)}
    <section class="dialog-history" aria-labelledby="issueHistoryTitle">
      <div class="dialog-section-head"><h3 id="issueHistoryTitle">사건 타임라인과 근거 원문</h3><span>공식 출처를 먼저 표시합니다</span></div>
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

function renderInsights() {
  const box = document.getElementById("insightList");
  const featured = state.insights?.featured_items || [];
  const items = (featured.length ? featured : state.insights?.items || []).filter(item => item.direction).slice(0, 3);
  if (!items.length) {
    box.innerHTML = '<div class="empty-state"><strong>이번 주 흐름을 준비하고 있습니다</strong><p>분류가 완료되면 근거와 함께 표시합니다.</p></div>';
    return;
  }
  box.innerHTML = items.map((item, index) => {
    const delta = item.count_now - item.count_prev;
    const uniqueEvidence = [];
    const seen = new Set();
    (item.evidence || []).forEach(article => {
      const key = normalizedSearch(article.title_kr).replace(/[^0-9a-z가-힣]/g, "");
      if (!key || seen.has(key)) return;
      seen.add(key);
      uniqueEvidence.push(article);
    });
    const evidence = uniqueEvidence.map(article => {
      const url = safeUrl(article.url);
      return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(article.title_kr)}</a>` : esc(article.title_kr)}<small>${esc(dateLabel(article.date))}</small></li>`;
    }).join("");
    const fullDirection = String(item.direction || "").trim();
    const takeaway = String(item.takeaway || fullDirection.split(/(?<=[.!?])\s+/)[0] || fullDirection).trim();
    const eventBullets = uniqueEvidence.slice(0, 3).map(article => `<li><time>${esc(dateLabel(article.date))}</time><span>${esc(article.title_kr)}</span></li>`).join("");
    return `<article class="flow-item">
      <div class="flow-rank">${String(index + 1).padStart(2, "0")}</div>
      <div class="flow-copy">
        <div class="flow-head"><h3>${esc(takeaway)}</h3><span>이번 주 ${item.count_now}회 · 전주 대비 ${delta >= 0 ? "+" : "−"}${Math.abs(delta)}</span></div>
        <p class="flow-keyword"><span>${esc(item.keyword)}</span></p>
        ${eventBullets ? `<div class="event-block"><strong>구성 사건</strong><ul>${eventBullets}</ul></div>` : ""}
        <details class="evidence"><summary>전체 해석과 근거 보기</summary><p>${esc(fullDirection)}</p>${evidence ? `<ul class="evidence-links">${evidence}</ul>` : ""}</details>
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
  // 리포트가 없는 주(금요일 이전)에는 통째로 숨긴다 — 빈 탭이 되면 안 되므로
  // 아래 정량 트렌드가 그대로 남는다.
  if (!report && !questions.length) { panel.hidden = true; return; }
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

function renderTrend() {
  renderWeeklyReport();
  renderInsights();
  renderTrendReadiness();
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

function switchView(view, updateUrl = true) {
  if (!VIEW_IDS.includes(view)) return;
  if (view !== state.view && state.issueId) closeIssueDialog(false);
  state.view = view;
  VIEW_IDS.forEach(id => { document.getElementById(`view-${id}`).hidden = id !== view; });
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

function openGlobalSearch() {
  const dialog = document.getElementById("globalSearchDialog");
  const input = document.getElementById("globalSearch");
  input.value = state.archiveQuery;
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => { input.focus(); input.select(); });
}

function applyTheme(theme, persist = false) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0c1519" : "#0e2a3c";
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
  if (detail) { openIssueDialog(detail.dataset.issueId); return true; }
  return false;
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
    const pubsOrg = event.target.closest("[data-pubs-org]");
    if (pubsOrg) { state.pubsOrg = pubsOrg.dataset.pubsOrg; renderPubs(); }
    const quick = event.target.closest("[data-quick-topic]");
    if (quick) {
      state.archiveTopic = quick.dataset.quickTopic;
      document.getElementById("archiveTopic").value = state.archiveTopic;
      switchView("search");
    }
    const keyword = event.target.closest("[data-keyword]");
    if (keyword) {
      state.archiveQuery = normalizedSearch(keyword.dataset.keyword);
      document.getElementById("globalSearch").value = state.archiveQuery;
      switchView("search");
    }
    if (event.target.closest("[data-clear-briefing]")) clearBriefingFilters();
    if (event.target.closest("[data-clear-archive]")) clearArchiveFilters();
  });
  ["issueList", "changedList", "archiveIssueList", "savedIssueList", "issueDialog",
   "headlineEvidence", "weeklyReportBody"].forEach(id => {
    document.getElementById(id).addEventListener("click", handleIssueAction);
  });

  document.getElementById("showChangedIssues").addEventListener("click", () => {
    const section = document.getElementById("changedIssues");
    // CSS의 prefers-reduced-motion은 JS scrollIntoView에 적용되지 않는다.
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    (section.hidden ? document.getElementById("todayIssues") : section)
      .scrollIntoView({ behavior: reduced ? "auto" : "smooth" });
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
  document.getElementById("closeFilters").addEventListener("click", () => { document.getElementById("briefingFilters").open = false; });
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
    state.archiveQuery = normalizedSearch(document.getElementById("globalSearch").value);
    document.getElementById("globalSearchDialog").close();
    switchView("search");
    renderArchiveSearch(true);
  });
  document.getElementById("globalSearch").addEventListener("input", event => {
    const query = normalizedSearch(event.target.value);
    document.getElementById("globalSearchHint").textContent = query ? `“${query}” 검색` : "검색어를 입력하세요.";
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
    [state.news, state.briefings, state.issues, state.trend, state.meta, state.insights, state.pubs] = await Promise.all([
      loadJSON("news.json"), loadJSON("briefings.json"), loadJSON("issues.json"),
      loadJSON("trend.json"), loadJSON("meta.json"), loadJSON("insights.json"),
      // 발간물은 부가 데이터 — 없어도 사이트 전체가 죽으면 안 된다 (8/1 빈 화면 사고 계약)
      loadJSON("publications.json").catch(() => null),
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
