/* 운영 콘솔. 상태 / 수집 설정(base ∪ 판정) / 내 판정.
 *
 * 데이터 소스 둘:
 *  - /admin/config.json  base 목록 (build_data 가 keywords.json 사본을 산출 —
 *                        배포마다 다시 만들어지므로 스냅샷 배포에도 안전)
 *  - /admin/api          판정 저장소(KV) + crawl 이 PUT 한 적용 장부(applied).
 *                        장부를 정적 파일로 두면 news_bot 이 안 도는 배포가
 *                        폴더 스냅샷으로 지워 버려 배지가 거짓말을 한다.
 */
"use strict";

const state = { store: { version: 0, entries: [] }, config: null, applied: new Set(), appliedLoaded: false };

const KIND_LABELS = {
  keyword_add: "키워드 추가", keyword_remove: "키워드 제거",
  exclusion_add: "제외어 추가", exclusion_remove: "제외어 제거",
  anchor_add: "앵커 추가", anchor_remove: "앵커 제거",
};
const FIELDS = [
  { field: "keyword", label: "검색 키워드", note: "그대로 검색에 나갑니다" },
  { field: "exclusion", label: "제외어", note: "제목에 걸리면 버립니다 — 넓은 말은 조용히 잘못됩니다" },
  { field: "anchor", label: "앵커", note: "결과가 원자력 문맥인지 확인하는 말" },
];

function esc(text) {
  return String(text).replace(/[&<>"']/g, ch => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, 2600);
}

async function fetchJson(url, options) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `${url} → HTTP ${response.status}`);
  return body;
}

// ── 판정 쓰기 ──────────────────────────────────────────────────────────
async function post(op, payload) {
  try {
    state.store = await fetchJson("/admin/api", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op, version: state.store.version, ...payload }),
    });
    render();
  } catch (err) {
    toast(err.message);
    if (String(err.message).includes("먼저 저장")) {
      state.store = await fetchJson("/admin/api").catch(() => state.store);
      render();
    }
  }
}

const addJudgment = (kind, group, value) => post("add", { entry: { kind, group, value } });

// ── 수집 설정 ──────────────────────────────────────────────────────────
function baseListFor(groupName, field) {
  const group = state.config?.[groupName];
  if (!group) return [];
  if (field === "keyword") return group.keywords || [];
  if (field === "anchor") return group.anchors || [];
  return String(group.negative_terms || "").split(/\s+/)
    .map(term => term.replace(/^-/, "")).filter(Boolean);
}

function entriesFor(groupName, field) {
  return state.store.entries.filter(e =>
    !e.disabled && e.kind.startsWith(field) && (e.group === groupName || e.group === "공통"));
}

function statusBadge(entry) {
  if (!state.appliedLoaded || !state.applied.has(entry.id)) {
    return '<span class="badge pending">다음 수집부터</span>';
  }
  return '<span class="badge applied">적용됨</span>';
}

function chip(value, extra, button) {
  return `<span class="chip ${extra}">${esc(value)}${button}</span>`;
}

function renderCollect() {
  const box = document.getElementById("collectGroups");
  const error = document.getElementById("collectError");
  if (!state.config) {
    error.hidden = false;
    error.textContent = "base 목록(/admin/config.json)이 아직 없습니다 — 다음 빌드부터 생깁니다. 판정 추가는 지금도 됩니다.";
  } else {
    error.hidden = true;
  }
  const groups = state.config ? Object.keys(state.config) : ["정책", "SMR"];
  box.innerHTML = groups.concat(["공통"]).map(groupName => {
    const isCommon = groupName === "공통";
    const sections = FIELDS.map(({ field, label, note }) => {
      const additions = state.store.entries.filter(e =>
        e.kind === `${field}_add` && e.group === groupName);
      const removals = new Set(entriesFor(groupName, field)
        .filter(e => e.kind === `${field}_remove`).map(e => e.value));
      const baseChips = isCommon ? [] : baseListFor(groupName, field).map(value => removals.has(value)
        ? chip(value, "removed", ` <button type="button" data-restore="${esc(field)}|${esc(groupName)}|${esc(value)}" title="제거 판정 취소">↩</button>`)
        : chip(value, "", ` <button type="button" data-remove="${esc(field)}|${esc(groupName)}|${esc(value)}" title="제거 판정 만들기">×</button>`));
      const addChips = additions.map(entry => chip(entry.value,
        `added${entry.disabled ? " disabled" : ""}`,
        ` ${statusBadge(entry)} <button type="button" data-delete="${esc(entry.id)}" title="이 판정 지우기">×</button>`));
      return `
        <h2 style="margin-top:14px">${esc(groupName)} · ${label}</h2>
        <p class="note">${note}${isCommon ? " — 공통은 모든 그룹에 적용" : ""}</p>
        <div class="chips">${baseChips.join("")}${addChips.join("") || (isCommon ? '<span class="note">없음</span>' : "")}</div>
        <form class="adder" data-kind="${field}_add" data-group="${esc(groupName)}">
          <input type="text" maxlength="80" placeholder="${label} 추가 — 다음 수집부터" required>
          <button type="submit">추가</button>
        </form>`;
    }).join("");
    return `<section>${sections}</section>`;
  }).join("<hr style='border:0;border-top:1px solid var(--line);margin:20px 0'>");
}

// ── 내 판정 ────────────────────────────────────────────────────────────
function renderJudgments() {
  const rows = document.getElementById("judgmentRows");
  const empty = document.getElementById("judgmentEmpty");
  empty.hidden = state.store.entries.length > 0;
  rows.innerHTML = state.store.entries.map(entry => `
    <tr class="${entry.disabled ? "chip disabled" : ""}">
      <td>${esc(KIND_LABELS[entry.kind] || entry.kind)}</td>
      <td>${esc(entry.group)}</td>
      <td>${esc(entry.value)}</td>
      <td>${esc(String(entry.created_at || "").slice(0, 10))}</td>
      <td>${entry.disabled ? '<span class="badge">잠시 꺼짐</span>' : statusBadge(entry)}</td>
      <td><div class="row-actions">
        <button type="button" data-toggle="${esc(entry.id)}">${entry.disabled ? "켜기" : "잠시 끄기"}</button>
        <button type="button" data-delete="${esc(entry.id)}">지우기</button>
      </div></td>
    </tr>`).join("");
}

function render() {
  renderCollect();
  renderJudgments();
}

// ── 상태 탭 (기존) ─────────────────────────────────────────────────────
async function renderStatus() {
  const list = document.getElementById("statusList");
  const row = (dt, dd, cls = "") => `<dt>${dt}</dt><dd${cls ? ` class="${cls}"` : ""}>${dd}</dd>`;
  try {
    const [status, meta] = await Promise.all([
      fetchJson("/data/status.json"), fetchJson("/data/meta.json"),
    ]);
    const ok = status.state === "ok";
    list.innerHTML = [
      row("상태", ok ? "정상" : esc(status.message || status.state), ok ? "state-ok" : "state-bad"),
      row("브리핑 날짜", esc(status.briefing_date || "—")),
      row("마지막 성공", esc(status.last_success_at || "—")),
      row("수집기 스탬프", esc(status.collector_stamp || "—")),
      row("generation_id", esc(status.generation_id || "—")),
      row("데이터 생성", esc(meta.generated_at || "—")),
    ].join("");
  } catch (err) {
    list.innerHTML = row("상태", `status.json 을 불러오지 못했습니다: ${esc(err.message)}`, "state-bad");
  }
}

// ── 배선 ───────────────────────────────────────────────────────────────
document.getElementById("tabs").addEventListener("click", event => {
  const button = event.target.closest("[data-panel]");
  if (!button) return;
  document.querySelectorAll("#tabs button").forEach(b =>
    b.setAttribute("aria-pressed", b === button ? "true" : "false"));
  ["status", "collect", "judgments"].forEach(name => {
    document.getElementById(`panel-${name}`).hidden = name !== button.dataset.panel;
  });
});

document.body.addEventListener("submit", event => {
  const form = event.target.closest("form.adder");
  if (!form) return;
  event.preventDefault();
  const input = form.querySelector("input");
  const value = input.value.trim();
  if (!value) return;
  addJudgment(form.dataset.kind, form.dataset.group, value).then(() => { input.value = ""; });
});

document.body.addEventListener("click", event => {
  const del = event.target.closest("[data-delete]");
  if (del) { post("delete", { id: del.dataset.delete }); return; }
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) { post("toggle", { id: toggle.dataset.toggle }); return; }
  const remove = event.target.closest("[data-remove]");
  if (remove) {
    const [field, group, value] = remove.dataset.remove.split("|");
    addJudgment(`${field}_remove`, group, value);
    return;
  }
  const restore = event.target.closest("[data-restore]");
  if (restore) {
    const [field, group, value] = restore.dataset.restore.split("|");
    const entry = state.store.entries.find(e =>
      e.kind === `${field}_remove` && e.value === value && (e.group === group || e.group === "공통"));
    if (entry) post("delete", { id: entry.id });
  }
});

(async () => {
  renderStatus();
  const [store, config] = await Promise.allSettled([
    fetchJson("/admin/api"),
    fetchJson("/admin/config.json"),
  ]);
  if (store.status === "fulfilled") {
    state.store = store.value;
    // 장부가 아직 없으면(첫 수집 전) 모든 판정은 '다음 수집부터'가 맞다.
    if (store.value.applied) {
      state.applied = new Set(store.value.applied.applied_ids || []);
      state.appliedLoaded = true;
    }
  } else {
    toast(store.reason.message);
  }
  if (config.status === "fulfilled") state.config = config.value;
  render();
})();
