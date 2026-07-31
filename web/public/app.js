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
  KR: "한국", US: "미국", FR: "프랑스", EU: "EU", UK: "영국",
  JP: "일본", RU: "러시아", CN: "중국", EU_ETC: "기타 EU", OTHER: "기타",
};

const OFFICIAL_HINTS = ["go.kr", "khnp", "kaeri", "iaea.org", "energy.gov", "nrc.gov"];

const state = {
  news: [], briefings: [], issues: [], trend: null, insights: null, meta: null,
  manifest: null, systemStatus: null, dataBase: "data",
  briefingDate: "", region: "전체", period: "7", query: "", topic: "전체", view: "news",
  issueId: "", archiveQuery: "", archiveRegion: "전체", archiveTopic: "전체", archiveLimit: 20,
};

let eventsBound = false;
let appReady = false;
let initLoading = false;
let initRetryTimer = 0;
let initRetryCount = 0;
let generationTimer = 0;

async function loadJSON(name) {
  const response = await fetch(`${state.dataBase}/${name}?v=${Date.now()}`);
  if (!response.ok) throw new Error(`${name} ${response.status}`);
  // SPA 폴백·오배포로 HTML이 200으로 오는 경우를 파싱 전에 명확한 에러로 변환
  const ctype = response.headers.get("content-type") || "";
  if (!ctype.includes("json")) throw new Error(`${name} 응답이 JSON이 아님 (${ctype.split(";")[0] || "unknown"})`);
  return response.json();
}

async function loadRootJSON(name, optional = false) {
  const response = await fetch(`data/${name}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    if (optional) return null;
    throw new Error(`${name} ${response.status}`);
  }
  if (optional) {
    // Pages SPA 폴백이 없는 파일에 200+HTML 을 돌려줄 수 있음 — optional 파일은
    // 파싱 실패를 "없음"으로 취급 (manifest 없는 flat 배포에서 전체 초기화가 죽던 버그)
    try { return await response.json(); } catch { return null; }
  }
  return response.json();
}

async function initializeDataBase() {
  const manifest = await loadRootJSON("manifest.json", true);
  const basePath = String(manifest?.base_path || "");
  if (manifest && /^generations\/[0-9A-Za-z-]+$/.test(basePath)) {
    state.manifest = manifest;
    state.dataBase = `data/${basePath}`;
  } else {
    state.manifest = null;
    state.dataBase = "data";
  }
  state.systemStatus = await loadRootJSON("status.json", true);
}

function dateTimeLabel(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).replace("T", " ").slice(0, 16);
  return parsed.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function renderSystemStatus() {
  const panel = document.getElementById("systemStatus");
  const status = state.systemStatus;
  panel.className = "system-status";
  panel.hidden = true;
  if (!state.manifest) {
    // flat 배포(CI — manifest 없음)는 정상 상태: 데이터 자체가 배포 시점 최신이다.
    // 경고를 띄우면 "오래된 데이터"로 오독됨. 갱신 시각은 헤더 meta 라인이 이미 표시.
    return;
  }
  if (status?.state === "error") {
    panel.textContent = `자동 갱신 오류 · 마지막 정상 데이터 ${dateTimeLabel(status.last_success_at)} · ${status.message || "원인을 확인하세요"}`;
    panel.classList.add("error");
    panel.hidden = false;
    return;
  }
  if (status?.state === "refreshing") {
    panel.textContent = "새 데이터를 검증하고 있습니다. 완료 전까지 마지막 정상 데이터를 표시합니다.";
    panel.classList.add("refreshing");
    panel.hidden = false;
    return;
  }
  if (status && !status.watcher_running) {
    panel.textContent = `자동 감시가 중지돼 있습니다. 마지막 정상 갱신 ${dateTimeLabel(status.last_success_at)}`;
    panel.classList.add("warning");
    panel.hidden = false;
  }
}

async function checkForNewGeneration() {
  try {
    const [manifest, status] = await Promise.all([
      loadRootJSON("manifest.json", true), loadRootJSON("status.json", true),
    ]);
    state.systemStatus = status;
    renderSystemStatus();
    if (manifest?.generation_id && manifest.generation_id !== state.manifest?.generation_id) {
      location.reload();
    }
  } catch {
    // 일시적인 로컬 파일 교체나 연결 오류는 다음 확인 주기에서 다시 시도한다.
  }
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

function sourceLabel(article) {
  return article.publisher || article.domain || "출처 미상";
}

function isOfficial(article) {
  const domain = String(article.domain || "").toLowerCase();
  return article.article_type === "official_doc" || OFFICIAL_HINTS.some(hint => domain.includes(hint));
}

function dateLabel(value) {
  if (!value) return "-";
  const [year, month, day] = value.split("-");
  return `${Number(month)}월 ${Number(day)}일`;
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

function briefingDates() {
  return state.briefings.map(briefing => briefing.date);
}

function currentBriefing() {
  return state.briefings.find(briefing => briefing.date === state.briefingDate) || null;
}

function issueMatchesRegion(issue) {
  if (state.region === "전체") return true;
  return (issue.related_articles || []).some(article => article.region === state.region);
}

function normalizedSearch(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function issueMatchesFilters(issue) {
  if (!issueMatchesRegion(issue)) return false;
  if (state.topic !== "전체" && !(issue.topics || []).includes(state.topic)) return false;
  if (!state.query) return true;
  const articleText = (issue.related_articles || []).map(article => (
    `${article.title_kr || ""} ${article.domain || ""} ${article.publisher || ""}`
  )).join(" ");
  const haystack = normalizedSearch([
    issue.title, issue.summary, issue.implication, ...(issue.tags || []),
    ...(issue.topics || []).map(topic => TOPIC_LABELS[topic] || topic), articleText,
  ].join(" "));
  return haystack.includes(state.query);
}

function renderTopicSelect() {
  const select = document.getElementById("topicSel");
  const counts = new Map();
  state.briefings.forEach(briefing => briefing.issues.forEach(issue => {
    (issue.topics || []).forEach(topic => counts.set(topic, (counts.get(topic) || 0) + 1));
  }));
  const topics = [...counts.keys()].sort((a, b) => (
    counts.get(b) - counts.get(a) || String(TOPIC_LABELS[a] || a).localeCompare(String(TOPIC_LABELS[b] || b), "ko")
  ));
  select.innerHTML = '<option value="전체">전체 주제</option>' + topics.map(topic => (
    `<option value="${esc(topic)}">${esc(TOPIC_LABELS[topic] || topic)} · ${counts.get(topic)}</option>`
  )).join("");
  if (topics.includes(state.topic)) select.value = state.topic;
  else state.topic = "전체";
}

function renderArchiveTopicSelect() {
  const select = document.getElementById("archiveTopic");
  const counts = new Map();
  state.issues.forEach(issue => (issue.topics || []).forEach(topic => (
    counts.set(topic, (counts.get(topic) || 0) + 1)
  )));
  const topics = [...counts.keys()].sort((a, b) => (
    counts.get(b) - counts.get(a) || String(TOPIC_LABELS[a] || a).localeCompare(String(TOPIC_LABELS[b] || b), "ko")
  ));
  select.innerHTML = '<option value="전체">전체 주제</option>' + topics.map(topic => (
    `<option value="${esc(topic)}">${esc(TOPIC_LABELS[topic] || topic)} · ${counts.get(topic)}</option>`
  )).join("");
  if (topics.includes(state.archiveTopic)) select.value = state.archiveTopic;
  else state.archiveTopic = "전체";
}

function archiveIssueMatches(issue) {
  if (state.archiveRegion !== "전체" && !(issue.regions || []).includes(state.archiveRegion)) return false;
  if (state.archiveTopic !== "전체" && !(issue.topics || []).includes(state.archiveTopic)) return false;
  if (!state.archiveQuery) return true;
  const articleText = (issue.related_articles || []).map(article => (
    `${article.title_kr || ""} ${article.domain || ""} ${article.publisher || ""}`
  )).join(" ");
  const countryText = (issue.related_articles || []).flatMap(article => article.countries || []).map(country => (
    COUNTRY_LABELS[country] || country
  )).join(" ");
  return normalizedSearch([
    issue.title, issue.summary, issue.implication, issue.region,
    ...(issue.tags || []), ...(issue.topics || []).map(topic => TOPIC_LABELS[topic] || topic),
    articleText, countryText,
  ].join(" ")).includes(state.archiveQuery);
}

function archiveIssueCard(issue, index) {
  const lifecycleText = issue.lifecycle === "active" ? "최근 갱신" : "기록 이슈";
  const lifecycleClass = issue.lifecycle === "active" ? "ongoing" : "quiet";
  const topicBadges = (issue.topics || []).slice(0, 2).map(topic => (
    `<span class="topic-chip">${esc(TOPIC_LABELS[topic] || topic)}</span>`
  )).join("");
  const reasons = (issue.selection_reasons || []).map(reason => (
    `<span class="reason ${reasonClass(reason)}">${esc(reason)}</span>`
  )).join("");
  const representativeUrl = safeUrl(issue.representative_article?.url);
  return `<article class="issue-card archive-card ${issue.importance === "must_read" ? "must" : ""}">
    <div class="issue-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</div>
    <div class="issue-body">
      <div class="issue-meta">
        <span class="issue-status ${lifecycleClass}">${lifecycleText}</span>
        <span>${esc(issue.region)}</span>
        <span>${esc(dateLabel(issue.last_seen))} 갱신</span>
        <span>${issue.briefing_count}회 브리핑 · ${issue.article_count}건</span>
      </div>
      <h3>${esc(issue.title)}</h3>
      ${issue.summary ? `<p class="issue-summary">${esc(issue.summary)}</p>` : ""}
      ${issue.implication ? `<p class="issue-meaning"><strong>의미</strong>${esc(issue.implication)}</p>` : ""}
      ${topicBadges ? `<div class="topic-row">${topicBadges}</div>` : ""}
      ${reasons ? `<div class="reason-row">${reasons}</div>` : ""}
      <div class="issue-actions">
        <button class="issue-detail-button" type="button" data-issue-id="${esc(issue.issue_id)}">이슈 흐름 보기 <span>${issue.article_count}건</span></button>
        ${representativeUrl ? `<a class="source-link" href="${esc(representativeUrl)}" target="_blank" rel="noopener noreferrer">최근 원문 <span aria-hidden="true">↗</span></a>` : ""}
      </div>
    </div>
  </article>`;
}

function renderArchiveSearch(resetLimit = false) {
  if (resetLimit) state.archiveLimit = 20;
  const matches = state.issues.filter(archiveIssueMatches);
  const visible = matches.slice(0, state.archiveLimit);
  const activeFilters = [
    state.archiveQuery ? `“${state.archiveQuery}”` : "",
    state.archiveRegion !== "전체" ? state.archiveRegion : "",
    state.archiveTopic !== "전체" ? TOPIC_LABELS[state.archiveTopic] || state.archiveTopic : "",
  ].filter(Boolean);
  document.getElementById("archiveSummary").textContent = activeFilters.length
    ? `${activeFilters.join(" · ")} — ${matches.length}개 이슈`
    : `최근순 ${matches.length}개 이슈`;
  document.getElementById("archiveIssueList").innerHTML = visible.length
    ? visible.map(archiveIssueCard).join("")
    : '<p class="empty large">조건에 맞는 이슈가 없습니다.</p>';
  const more = document.getElementById("archiveMore");
  more.hidden = visible.length >= matches.length;
  more.textContent = more.hidden ? "더 보기" : `더 보기 · ${matches.length - visible.length}개 남음`;
  document.getElementById("archiveClear").hidden = activeFilters.length === 0;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.briefingDate) params.set("date", state.briefingDate);
  if (state.region !== "전체") params.set("region", state.region);
  if (state.topic !== "전체") params.set("topic", state.topic);
  if (state.query) params.set("q", state.query);
  if (state.view !== "news") params.set("view", state.view);
  if (state.archiveQuery) params.set("aq", state.archiveQuery);
  if (state.archiveRegion !== "전체") params.set("ar", state.archiveRegion);
  if (state.archiveTopic !== "전체") params.set("at", state.archiveTopic);
  if (state.issueId && state.view !== "trend") params.set("issue", state.issueId);
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
}

function restoreUrlState() {
  const params = new URLSearchParams(location.search);
  const requestedDate = params.get("date");
  if (briefingDates().includes(requestedDate)) state.briefingDate = requestedDate;
  const requestedRegion = params.get("region");
  if (["전체", "국내", "해외"].includes(requestedRegion)) state.region = requestedRegion;
  state.query = normalizedSearch(params.get("q"));
  state.topic = params.get("topic") || "전체";
  state.view = ["news", "search", "trend"].includes(params.get("view")) ? params.get("view") : "news";
  state.issueId = params.get("issue") || "";
  state.archiveQuery = normalizedSearch(params.get("aq"));
  state.archiveRegion = ["전체", "국내", "해외"].includes(params.get("ar")) ? params.get("ar") : "전체";
  state.archiveTopic = params.get("at") || "전체";
}

function renderDateSelect() {
  const select = document.getElementById("dateSel");
  select.innerHTML = state.briefings.map(briefing => (
    `<option value="${esc(briefing.date)}">${esc(dateLabel(briefing.date))} 브리핑</option>`
  )).join("");
  select.value = state.briefingDate;

  const dates = briefingDates();
  const index = dates.indexOf(state.briefingDate);
  document.getElementById("prevDay").disabled = index < 0 || index >= dates.length - 1;
  document.getElementById("nextDay").disabled = index <= 0;
}

function reasonClass(reason) {
  if (/안전/.test(reason)) return "risk";
  if (/정책|규제/.test(reason)) return "policy";
  if (/1차/.test(reason)) return "source";
  return "neutral";
}

function articleTimelineRow(article, briefingDate, currentStage = "이번 브리핑") {
  const url = safeUrl(article.url);
  const title = esc(article.title_kr);
  const source = esc(sourceLabel(article));
  const timing = esc(relativeArticleDate(article.article_date, briefingDate));
  const stage = article.briefing_date === briefingDate ? currentStage : "이전 흐름";
  return `<li>
    <div class="timeline-date"><span>${esc(dateLabel(article.article_date))}</span><small>${timing}</small><em>${stage}</em></div>
    <div class="timeline-copy">
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<span>${title}</span>`}
      <small>${source}</small>
    </div>
  </li>`;
}

function issueCard(issue, index, briefingDate) {
  const reasons = (issue.selection_reasons || []).map(reason => (
    `<span class="reason ${reasonClass(reason)}">${esc(reason)}</span>`
  )).join("");
  const statusText = issue.status === "ongoing"
    ? `이어지는 이슈 · ${issue.tracked_briefings || 2}회 추적`
    : "새 이슈";
  const statusClass = issue.status === "ongoing" ? "ongoing" : "new";
  const articles = issue.related_articles || [];
  const topicBadges = (issue.topics || []).slice(0, 2).map(topic => (
    `<span class="topic-chip">${esc(TOPIC_LABELS[topic] || topic)}</span>`
  )).join("");
  const representativeUrl = safeUrl(issue.representative_article?.url);
  const articleCountText = issue.status === "ongoing"
    ? `이번 브리핑 ${issue.current_article_count}건 · 누적 ${issue.article_count}건`
    : issue.article_count > 1
      ? `관련 기사 ${issue.article_count}건`
    : `${relativeArticleDate(issue.representative_article?.article_date, briefingDate)} · ${sourceLabel(issue.representative_article || {})}`;

  const sourceArea = `<div class="issue-actions">
    <button class="issue-detail-button" type="button" data-issue-id="${esc(issue.issue_id)}">
      이슈 흐름 보기 <span>${articles.length}건</span>
    </button>
    ${representativeUrl
      ? `<a class="source-link" href="${esc(representativeUrl)}" target="_blank" rel="noopener noreferrer">대표 원문 <span aria-hidden="true">↗</span></a>`
      : ""}
  </div>`;

  return `<article class="issue-card ${issue.importance === "must_read" ? "must" : ""}">
    <div class="issue-index" aria-hidden="true">${String(index + 1).padStart(2, "0")}</div>
    <div class="issue-body">
      <div class="issue-meta">
        <span class="issue-status ${statusClass}">${statusText}</span>
        <span>${esc(issue.region)}</span>
        <span>${esc(articleCountText)}</span>
      </div>
      <h3>${esc(issue.title)}</h3>
      ${issue.summary ? `<p class="issue-summary">${esc(issue.summary)}</p>` : ""}
      ${issue.implication ? `<p class="issue-meaning"><strong>의미</strong>${esc(issue.implication)}</p>` : ""}
      ${topicBadges ? `<div class="topic-row">${topicBadges}</div>` : ""}
      ${reasons ? `<div class="reason-row">${reasons}</div>` : ""}
      ${sourceArea}
    </div>
  </article>`;
}

function currentIssueById(issueId) {
  if (state.view === "search") {
    return state.issues.find(issue => issue.issue_id === issueId) || null;
  }
  return currentBriefing()?.issues.find(issue => issue.issue_id === issueId) || null;
}

function openIssueDialog(issueId, updateUrl = true) {
  const issue = currentIssueById(issueId);
  if (!issue) {
    state.issueId = "";
    if (updateUrl) syncUrl();
    return;
  }
  const dialog = document.getElementById("issueDialog");
  const topics = (issue.topics || []).map(topic => (
    `<span class="topic-chip">${esc(TOPIC_LABELS[topic] || topic)}</span>`
  )).join("");
  const status = (issue.tracked_briefings || 1) > 1
    ? `${issue.tracked_briefings || 2}회 브리핑에서 추적`
    : "이번 브리핑에서 처음 포착";
  const articles = issue.related_articles || [];
  const archiveView = state.view === "search";
  const contextDate = archiveView ? issue.last_seen : state.briefingDate;
  const updateTitle = archiveView ? "최근 업데이트의 핵심" : "현재 브리핑의 핵심";
  const currentLabel = archiveView ? "최근 브리핑" : "이번 브리핑";
  document.getElementById("issueDialogContent").innerHTML = `
    <p class="dialog-kicker">ISSUE TIMELINE</p>
    <h2 id="issueDialogTitle">${esc(issue.title)}</h2>
    <div class="dialog-meta">
      <span>${esc(status)}</span>
      <span>${esc(dateLabel(issue.first_seen))} 시작</span>
      <span>누적 ${issue.article_count}건</span>
    </div>
    <section class="dialog-update" aria-labelledby="issueUpdateTitle">
      <h3 id="issueUpdateTitle">${updateTitle}</h3>
      ${issue.summary ? `<p>${esc(issue.summary)}</p>` : '<p class="empty">요약이 없습니다.</p>'}
      ${issue.implication ? `<p class="dialog-meaning"><strong>의미</strong>${esc(issue.implication)}</p>` : ""}
      ${topics ? `<div class="topic-row">${topics}</div>` : ""}
    </section>
    <section class="dialog-history" aria-labelledby="issueHistoryTitle">
      <div class="dialog-section-head">
        <h3 id="issueHistoryTitle">기사 타임라인</h3>
        <span>${currentLabel} ${issue.current_article_count}건 · 이전 ${issue.previous_article_count || 0}건</span>
      </div>
      <ol class="timeline dialog-timeline">${articles.map(article => articleTimelineRow(article, contextDate, currentLabel)).join("")}</ol>
    </section>`;
  state.issueId = issueId;
  if (!dialog.open) {
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  if (updateUrl) syncUrl();
}

function closeIssueDialog() {
  const dialog = document.getElementById("issueDialog");
  if (dialog.open && typeof dialog.close === "function") dialog.close();
  else {
    dialog.removeAttribute("open");
    state.issueId = "";
    syncUrl();
  }
}

function renderBriefing() {
  const briefing = currentBriefing();
  const issueList = document.getElementById("issueList");
  if (!briefing) {
    document.getElementById("briefingTitle").textContent = "브리핑 데이터가 없습니다";
    issueList.innerHTML = '<p class="empty">표시할 브리핑이 없습니다.</p>';
    return;
  }

  const issues = briefing.issues.filter(issueMatchesFilters);
  document.getElementById("briefingTitle").textContent = `${dateLabel(briefing.date)} 브리핑`;
  document.getElementById("briefingStats").innerHTML = `
    <span><strong>${briefing.issue_count}</strong><small>이슈</small></span>
    <span><strong>${briefing.article_count}</strong><small>기사</small></span>
    <span><strong>${briefing.domestic_count}</strong><small>국내</small></span>
    <span><strong>${briefing.overseas_count}</strong><small>해외</small></span>`;

  const highlights = issues.slice(0, 3);
  document.getElementById("briefingHighlights").innerHTML = highlights.length
    ? highlights.map(issue => `<li>${esc(issue.title)}</li>`).join("")
    : "<li>선택한 지역에 해당하는 이슈가 없습니다.</li>";

  document.getElementById("issueCount").textContent =
    state.region === "전체" ? `${issues.length}개 이슈` : `${state.region} ${issues.length}개 이슈`;
  issueList.innerHTML = issues.length
    ? issues.map((issue, index) => issueCard(issue, index, briefing.date)).join("")
    : '<p class="empty large">선택한 지역에 해당하는 브리핑 이슈가 없습니다.</p>';

  const activeFilters = [];
  if (state.region !== "전체") activeFilters.push(state.region);
  if (state.topic !== "전체") activeFilters.push(TOPIC_LABELS[state.topic] || state.topic);
  if (state.query) activeFilters.push(`“${state.query}”`);
  document.getElementById("filterSummary").textContent = activeFilters.length
    ? `${activeFilters.join(" · ")} — ${issues.length}개 이슈`
    : "";
  document.getElementById("clearFilters").hidden = activeFilters.length === 0;

  renderNewsFeed();
}

function articleCard(article) {
  const badges = [];
  if (article.importance === "must_read") badges.push('<span class="mini-badge important">핵심</span>');
  badges.push(isOfficial(article)
    ? '<span class="mini-badge official">공식기관</span>'
    : '<span class="mini-badge press">언론</span>');
  if (article.briefing_date) badges.push(`<span class="mini-badge selected">${esc(dateLabel(article.briefing_date))} 브리핑</span>`);
  const topics = (article.topics || []).map(topic => TOPIC_LABELS[topic] || topic);
  const tags = [...topics, ...(article.tags || []).map(tag => String(tag).replace(/^#/, ""))];
  const url = safeUrl(article.url);

  return `<article class="news-item">
    <div class="news-meta">${badges.join("")}<span>${esc(sourceLabel(article))}</span><span>${esc(article.region)}</span></div>
    <h3>${esc(article.title_kr)}</h3>
    ${article.summary ? `<p class="news-summary">${esc(article.summary)}</p>` : ""}
    <details class="article-details">
      <summary>상세 정보</summary>
      ${article.implication ? `<p><strong>의미</strong>${esc(article.implication)}</p>` : ""}
      ${article.why_important ? `<p><strong>왜 중요한가</strong>${esc(article.why_important)}</p>` : ""}
      ${tags.length ? `<div class="tag-row">${tags.map(tag => `<span>${esc(tag)}</span>`).join("")}</div>` : ""}
      ${article.title && article.title !== article.title_kr ? `<p class="original-title"><strong>원문 제목</strong>${esc(article.title)}</p>` : ""}
      ${url ? `<a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 확인 <span aria-hidden="true">↗</span></a>` : ""}
    </details>
  </article>`;
}

function renderNewsFeed() {
  const articles = state.news.filter(article => (
    article.article_date === state.briefingDate
    && (state.region === "전체" || article.region === state.region)
    && (state.topic === "전체" || (article.topics || []).includes(state.topic))
    && (!state.query || normalizedSearch([
      article.title_kr, article.title, article.summary, article.implication,
      sourceLabel(article), ...(article.canonical_tags || []),
    ].join(" ")).includes(state.query))
  ));
  document.getElementById("feedTitle").textContent =
    `${dateLabel(state.briefingDate)} 발행 · ${articles.length}건`;
  document.getElementById("newsList").innerHTML = articles.length
    ? articles.map(articleCard).join("")
    : '<p class="empty">이 날짜에 발행된 수집 기사가 없습니다.</p>';
}

function bars(element, rows, labelFn) {
  if (!rows?.length) {
    element.innerHTML = '<p class="empty">아직 데이터가 충분하지 않습니다.</p>';
    return;
  }
  const max = Math.max(...rows.map(row => row.count));
  element.innerHTML = rows.map(row => `<div class="bar-row">
    <span class="bar-name" title="${esc(labelFn(row))}">${esc(labelFn(row))}</span>
    <div class="bar-track"><span style="width:${Math.max(3, Math.round(row.count / max * 100))}%"></span></div>
    <span class="bar-value">${row.count}</span>
  </div>`).join("");
}

function renderInsights() {
  const box = document.getElementById("insightList");
  const featured = state.insights?.featured_items || [];
  const items = (featured.length ? featured : state.insights?.items || [])
    .filter(item => item.direction)
    .slice(0, 3);
  if (!items.length) {
    box.innerHTML = '<p class="empty large">이번 주 흐름 해석이 아직 생성되지 않았습니다.</p>';
    return;
  }
  box.innerHTML = items.map((item, index) => {
    const delta = item.count_now - item.count_prev;
    const uniqueEvidence = [];
    const seenTitles = new Set();
    (item.evidence || []).forEach(article => {
      const key = normalizedSearch(article.title_kr).replace(/[^0-9a-z가-힣]/g, "");
      if (!key || seenTitles.has(key)) return;
      seenTitles.add(key);
      uniqueEvidence.push(article);
    });
    const evidence = uniqueEvidence.map(article => {
      const url = safeUrl(article.url);
      return `<li>${url
        ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(article.title_kr)}</a>`
        : esc(article.title_kr)}<small>${esc(dateLabel(article.date))}</small></li>`;
    }).join("");
    const fullDirection = String(item.direction || "").trim();
    const takeaway = String(item.takeaway || fullDirection.split(/(?<=[.!?])\s+/)[0] || fullDirection).trim();
    const eventBullets = uniqueEvidence.slice(0, 3).map(article => `<li>${esc(article.title_kr)}</li>`).join("");
    const scope = item.region_scope || "범위 미분류";
    const scopeClass = scope === "국내" ? "domestic" : scope === "해외" ? "overseas" : "mixed";
    const regionCounts = [
      item.domestic_evidence_count ? `국내 ${item.domestic_evidence_count}건` : "",
      item.overseas_evidence_count ? `해외 ${item.overseas_evidence_count}건` : "",
    ].filter(Boolean).join(" · ");
    return `<article class="flow-item">
      <div class="flow-rank">${String(index + 1).padStart(2, "0")}</div>
      <div class="flow-copy">
        <div class="flow-head">
          <h3>${esc(item.keyword)}</h3>
          <span>이번 주 ${item.count_now}회 · 전주 대비 ${delta >= 0 ? "+" : ""}${delta}</span>
        </div>
        <div class="flow-range">
          <span class="flow-region ${scopeClass}">${esc(scope)}</span>
          ${regionCounts ? `<small>근거 ${esc(regionCounts)}</small>` : ""}
        </div>
        <span class="flow-label">한줄 흐름</span>
        <p class="flow-takeaway">${esc(takeaway)}</p>
        ${eventBullets ? `<div class="event-block"><strong>구성 사건</strong><ul>${eventBullets}</ul></div>` : ""}
        <details class="evidence">
          <summary>전체 해석과 근거 보기</summary>
          <p class="full-direction">${esc(fullDirection)}</p>
          ${evidence ? `<ul class="evidence-links">${evidence}</ul>` : ""}
        </details>
      </div>
    </article>`;
  }).join("");
}

function renderTrendReadiness() {
  const ready = Boolean(state.meta?.trend_ready);
  const topicCoverage = Math.round((state.meta?.topic_coverage || 0) * 100);
  const countryCoverage = Math.round((state.meta?.country_coverage || 0) * 100);
  const panel = document.getElementById("trendReadiness");
  document.getElementById("trendData").hidden = !ready;
  if (ready) {
    panel.innerHTML = `<div><strong>프로토타입 통계 사용 가능</strong><p>원본을 바꾸지 않고 기존 제목·태그에서 통제 주제를 로컬 백필했습니다.</p></div>
      <div class="coverage"><span>주제 분류 <strong>${topicCoverage}%</strong></span><span>국가 분류 <strong>${countryCoverage}%</strong></span></div>`;
    panel.classList.add("ready");
    return;
  }
  panel.classList.remove("ready");
  panel.innerHTML = `<div>
      <strong>정량 차트는 아직 준비 중입니다</strong>
      <p>불완전한 집계를 사실처럼 보이지 않도록 통제 태그 백필 전까지 차트를 숨겼습니다.</p>
    </div>
    <div class="coverage">
      <span>주제 분류 <strong>${topicCoverage}%</strong></span>
      <span>국가 분류 <strong>${countryCoverage}%</strong></span>
    </div>`;
}

function renderTrend() {
  renderInsights();
  renderTrendReadiness();
  if (!state.meta?.trend_ready) return;

  const trend = state.trend;
  bars(document.getElementById("topTags"),
    state.period === "7" ? trend.top_tags_7d : trend.top_tags_30d,
    row => row.tag);
  bars(document.getElementById("countryBars"), trend.countries_30d,
    row => COUNTRY_LABELS[row.country] || row.country);

  document.getElementById("risingTags").innerHTML = trend.rising?.length
    ? trend.rising.map(row => `<div class="rise-row"><span>${esc(row.tag)}</span><strong>+${row.now - row.prev}</strong><small>${row.prev}→${row.now}</small></div>`).join("")
    : '<p class="empty">급상승 키워드가 없습니다.</p>';
  document.getElementById("newTags").innerHTML = trend.new_tags?.length
    ? trend.new_tags.map(row => `<div class="rise-row"><span>${esc(row.tag)}</span><strong>${row.count}</strong><small>회</small></div>`).join("")
    : '<p class="empty">새 키워드가 없습니다.</p>';
  renderTopicChart(trend);
}

function renderTopicChart(trend) {
  const box = document.getElementById("topicChart");
  const legend = document.getElementById("topicLegend");
  const topics = Object.keys(trend.topic_series || {});
  if (!trend.weeks?.length || trend.weeks.length < 2 || !topics.length) {
    box.innerHTML = '<p class="empty">주간 데이터가 더 필요합니다.</p>';
    legend.innerHTML = "";
    return;
  }
  const colors = ["#135d8f", "#b7503b", "#37806b", "#9b7328", "#76528f", "#bb6c2c"];
  const width = 800, height = 220, padding = 32;
  const maxValue = Math.max(1, ...topics.flatMap(topic => trend.topic_series[topic]));
  const x = index => padding + index * (width - 2 * padding) / (trend.weeks.length - 1);
  const y = value => height - padding - value * (height - 2 * padding) / maxValue;
  const lines = topics.map((topic, topicIndex) => {
    const points = trend.topic_series[topic].map((value, index) => `${x(index)},${y(value)}`).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${colors[topicIndex % colors.length]}" stroke-width="3"/>`;
  }).join("");
  const labels = trend.weeks.map((week, index) => (
    `<text x="${x(index)}" y="${height - 8}" font-size="11" fill="currentColor" opacity=".65" text-anchor="middle">${esc(week.slice(5))}</text>`
  )).join("");
  box.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="주제별 주간 기사 수 추이">${lines}${labels}</svg>`;
  legend.innerHTML = topics.map((topic, index) => (
    `<span><i style="background:${colors[index % colors.length]}"></i>${esc(TOPIC_LABELS[topic] || topic)}</span>`
  )).join("");
}

function setPressed(container, activeButton) {
  container.querySelectorAll("button").forEach(button => {
    const active = button === activeButton;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function stepBriefing(direction) {
  const dates = briefingDates();
  const nextIndex = dates.indexOf(state.briefingDate) + direction;
  if (nextIndex < 0 || nextIndex >= dates.length) return;
  if (state.issueId) closeIssueDialog();
  state.briefingDate = dates[nextIndex];
  renderDateSelect();
  renderBriefing();
  syncUrl();
}

function bind() {
  document.getElementById("mainTabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-view]");
    if (!button) return;
    if (button.dataset.view !== state.view && state.issueId) closeIssueDialog();
    setPressed(event.currentTarget, button);
    state.view = button.dataset.view;
    document.getElementById("view-news").hidden = button.dataset.view !== "news";
    document.getElementById("view-search").hidden = button.dataset.view !== "search";
    document.getElementById("view-trend").hidden = button.dataset.view !== "trend";
    if (state.view === "search") renderArchiveSearch();
    syncUrl();
  });

  document.getElementById("regionTabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-region]");
    if (!button) return;
    state.region = button.dataset.region;
    setPressed(event.currentTarget, button);
    renderBriefing();
    syncUrl();
  });

  document.getElementById("periodTabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-period]");
    if (!button) return;
    state.period = button.dataset.period;
    setPressed(event.currentTarget, button);
    renderTrend();
  });

  document.getElementById("dateSel").addEventListener("change", event => {
    if (state.issueId) closeIssueDialog();
    state.briefingDate = event.target.value;
    renderDateSelect();
    renderBriefing();
    syncUrl();
  });
  document.getElementById("prevDay").addEventListener("click", () => stepBriefing(1));
  document.getElementById("nextDay").addEventListener("click", () => stepBriefing(-1));

  document.getElementById("issueSearch").addEventListener("input", event => {
    state.query = normalizedSearch(event.target.value);
    renderBriefing();
    syncUrl();
  });
  document.getElementById("topicSel").addEventListener("change", event => {
    state.topic = event.target.value;
    renderBriefing();
    syncUrl();
  });
  document.getElementById("clearFilters").addEventListener("click", () => {
    state.region = "전체";
    state.topic = "전체";
    state.query = "";
    document.getElementById("issueSearch").value = "";
    document.getElementById("topicSel").value = "전체";
    setPressed(document.getElementById("regionTabs"), document.querySelector('#regionTabs [data-region="전체"]'));
    renderBriefing();
    syncUrl();
  });

  document.getElementById("issueList").addEventListener("click", event => {
    const button = event.target.closest("button[data-issue-id]");
    if (!button) return;
    openIssueDialog(button.dataset.issueId);
  });
  document.getElementById("archiveIssueList").addEventListener("click", event => {
    const button = event.target.closest("button[data-issue-id]");
    if (!button) return;
    openIssueDialog(button.dataset.issueId);
  });
  document.getElementById("archiveSearch").addEventListener("input", event => {
    state.archiveQuery = normalizedSearch(event.target.value);
    renderArchiveSearch(true);
    syncUrl();
  });
  document.getElementById("archiveRegion").addEventListener("change", event => {
    state.archiveRegion = event.target.value;
    renderArchiveSearch(true);
    syncUrl();
  });
  document.getElementById("archiveTopic").addEventListener("change", event => {
    state.archiveTopic = event.target.value;
    renderArchiveSearch(true);
    syncUrl();
  });
  document.getElementById("archiveClear").addEventListener("click", () => {
    state.archiveQuery = "";
    state.archiveRegion = "전체";
    state.archiveTopic = "전체";
    document.getElementById("archiveSearch").value = "";
    document.getElementById("archiveRegion").value = "전체";
    document.getElementById("archiveTopic").value = "전체";
    renderArchiveSearch(true);
    syncUrl();
  });
  document.getElementById("archiveMore").addEventListener("click", () => {
    state.archiveLimit += 20;
    renderArchiveSearch();
  });
  document.getElementById("issueDialogClose").addEventListener("click", closeIssueDialog);
  document.getElementById("issueDialog").addEventListener("cancel", event => {
    event.preventDefault();
    closeIssueDialog();
  });
  document.getElementById("issueDialog").addEventListener("close", () => {
    state.issueId = "";
    syncUrl();
  });
  document.getElementById("issueDialog").addEventListener("click", event => {
    if (event.target === event.currentTarget) closeIssueDialog();
  });
}

async function init() {
  if (!eventsBound) {
    bind();
    eventsBound = true;
    window.addEventListener("online", () => {
      if (!appReady) init();
    });
  }
  if (appReady || initLoading) return;
  initLoading = true;
  try {
    await initializeDataBase();
    [state.news, state.briefings, state.issues, state.trend, state.meta, state.insights] = await Promise.all([
      loadJSON("news.json"), loadJSON("briefings.json"), loadJSON("issues.json"),
      loadJSON("trend.json"), loadJSON("meta.json"), loadJSON("insights.json"),
    ]);
  } catch (error) {
    initLoading = false;
    initRetryCount += 1;
    window.clearTimeout(initRetryTimer);
    if (initRetryCount <= 3) {
      // 일시 장애 대비 3회까지만 자동 재시도 — 무한 5초 폴링은 실패를 가리고 트래픽만 만든다
      document.getElementById("metaLine").textContent =
        `데이터 연결 실패 · 5초 후 자동 재시도 (${initRetryCount}/3) — ${error.message}`;
      document.getElementById("issueList").innerHTML =
        '<p class="empty large">데이터 연결을 복구하는 중입니다. 잠시만 기다려주세요.</p>';
      initRetryTimer = window.setTimeout(init, 5000);
    } else {
      document.getElementById("metaLine").textContent = `데이터 연결 실패 — ${error.message}`;
      document.getElementById("issueList").innerHTML =
        '<p class="empty large">데이터를 불러오지 못했습니다. ' +
        '<button type="button" id="retryInit" class="retry-btn">다시 시도</button></p>';
      document.getElementById("retryInit")?.addEventListener("click", () => {
        initRetryCount = 0;
        init();
      });
    }
    return;
  }

  window.clearTimeout(initRetryTimer);
  initRetryCount = 0;

  state.briefingDate = state.meta.latest_briefing_date || state.briefings[0]?.date || "";
  restoreUrlState();
  renderTopicSelect();
  renderArchiveTopicSelect();
  document.getElementById("issueSearch").value = state.query;
  document.getElementById("topicSel").value = state.topic;
  document.getElementById("archiveSearch").value = state.archiveQuery;
  document.getElementById("archiveRegion").value = state.archiveRegion;
  document.getElementById("archiveTopic").value = state.archiveTopic;
  const regionButton = document.querySelector(`#regionTabs [data-region="${state.region}"]`);
  if (regionButton) setPressed(document.getElementById("regionTabs"), regionButton);
  const viewButton = document.querySelector(`#mainTabs [data-view="${state.view}"]`);
  if (viewButton) setPressed(document.getElementById("mainTabs"), viewButton);
  document.getElementById("view-news").hidden = state.view !== "news";
  document.getElementById("view-search").hidden = state.view !== "search";
  document.getElementById("view-trend").hidden = state.view !== "trend";
  const refreshedAt = state.systemStatus?.last_success_at || state.manifest?.generated_at || state.meta.generated_at;
  document.getElementById("metaLine").textContent =
    `갱신 ${dateTimeLabel(refreshedAt)} · 이슈 ${state.issues.length}개`;
  renderSystemStatus();
  const firstIssueDate = state.issues.reduce((oldest, issue) => (
    !oldest || issue.first_seen < oldest ? issue.first_seen : oldest
  ), "");
  document.getElementById("archiveCatalogMeta").textContent =
    `${dateLabel(firstIssueDate)}–${dateLabel(state.meta.latest_briefing_date)} · ${state.issues.length}개 이슈`;
  renderDateSelect();
  renderBriefing();
  renderArchiveSearch();
  renderTrend();
  if (state.issueId && state.view !== "trend") openIssueDialog(state.issueId, false);
  syncUrl();
  appReady = true;
  initLoading = false;
  if (!generationTimer) generationTimer = window.setInterval(checkForNewGeneration, 60000);
}

init();
