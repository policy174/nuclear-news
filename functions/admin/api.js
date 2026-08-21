/* /admin/api — 판정 저장소 (Cloudflare KV).
 *
 * 인증은 _middleware.js 가 이미 끝냈다 — 여기는 로직만.
 *
 * 계약 (v2 콘솔의 4대 원칙):
 *  - 덧칠: 기본 파일(keywords.json)은 불변, 판정 항목만 쌓인다.
 *  - 다음 수집부터: 여기 저장된 판정은 crawl 이 KV 를 읽어 갈 때 적용된다.
 *  - 되돌리기 한 줄: 항목 삭제 = 그 판단만 원복.
 *  - 단일 키 + version 낙관 잠금. 관리자가 한 명이라 이걸로 충분하다.
 *    ponytail: 다중 관리자가 생기면 엔트리별 키로 승급.
 */

const STORE_KEY = "judgments";
// crawl 이 매 수집 끝에 PUT 하는 적용 장부 {applied_ids, overlay, collected_at}.
// 콘솔의 '적용됨 / 다음 수집부터' 배지가 이걸 본다.
const APPLIED_KEY = "applied";

export const KINDS = new Set([
  "keyword_add", "keyword_remove",
  "exclusion_add", "exclusion_remove",
  "anchor_add", "anchor_remove",
  // 병합 진단의 쌍 판정. value 는 정렬된 pair id "<a>--<b>" 이고,
  // build_data 의 load_admin_pair_judgments 가 같은 계약으로 읽는다.
  "pair_join", "pair_split",
]);
export const GROUPS = new Set(["정책", "SMR", "공통", "병합"]);

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function loadStore(kv) {
  const raw = await kv.get(STORE_KEY);
  if (!raw) return { version: 0, entries: [] };
  try {
    const parsed = JSON.parse(raw);
    return {
      version: Number(parsed.version) || 0,
      entries: Array.isArray(parsed.entries) ? parsed.entries : [],
    };
  } catch {
    // 깨진 저장소는 빈 것으로 취급하되 version 은 유지 못 한다 — 첫 쓰기가 새로 시작.
    return { version: 0, entries: [] };
  }
}

export function validateEntry(entry) {
  if (!entry || typeof entry !== "object") return "entry 없음";
  if (!KINDS.has(entry.kind)) return `kind 가 올바르지 않습니다: ${entry.kind}`;
  if (!GROUPS.has(entry.group)) return `group 이 올바르지 않습니다: ${entry.group}`;
  const value = String(entry.value || "").trim();
  if (!value) return "value 가 비어 있습니다";
  if (value.length > 80) return "value 가 너무 깁니다 (80자 상한)";
  return "";
}

export function applyOp(store, body) {
  // 반환: {status, payload} — KV 쓰기는 호출자 몫 (테스트가 순수 함수로 돌게).
  if (Number(body.version) !== store.version) {
    return { status: 409, payload: { error: "다른 곳에서 먼저 저장했습니다 — 새로고침 뒤 다시 누르세요.", version: store.version } };
  }
  const entries = [...store.entries];
  if (body.op === "add") {
    const problem = validateEntry(body.entry);
    if (problem) return { status: 400, payload: { error: problem } };
    const value = String(body.entry.value).trim();
    const duplicate = entries.find(e =>
      e.kind === body.entry.kind && e.group === body.entry.group && e.value === value);
    if (duplicate) {
      return { status: 400, payload: { error: "같은 판정이 이미 있습니다 — 내 판정에서 확인하세요.", id: duplicate.id } };
    }
    entries.push({
      id: crypto.randomUUID(),
      kind: body.entry.kind,
      group: body.entry.group,
      value,
      reason: String(body.entry.reason || "").slice(0, 200),
      created_at: new Date().toISOString(),
      disabled: false,
    });
  } else if (body.op === "delete") {
    const index = entries.findIndex(e => e.id === body.id);
    if (index < 0) return { status: 404, payload: { error: "해당 판정이 없습니다" } };
    entries.splice(index, 1);
  } else if (body.op === "toggle") {
    const entry = entries.find(e => e.id === body.id);
    if (!entry) return { status: 404, payload: { error: "해당 판정이 없습니다" } };
    entry.disabled = !entry.disabled;
  } else {
    return { status: 400, payload: { error: `모르는 op: ${body.op}` } };
  }
  const next = { version: store.version + 1, entries };
  return { status: 200, payload: next, write: next };
}

export async function onRequest(context) {
  const kv = context.env.ADMIN_KV;
  if (!kv) {
    return json({ error: "판정 저장은 지금 쓸 수 없습니다 — 배포의 ADMIN_KV 바인딩을 확인하세요." }, 503);
  }
  const store = await loadStore(kv);
  if (context.request.method === "GET") {
    const applied = await kv.get(APPLIED_KEY, "json").catch(() => null);
    return json({ ...store, applied });
  }
  if (context.request.method !== "POST") return json({ error: "GET/POST 만" }, 405);

  const body = await context.request.json().catch(() => null);
  if (!body) return json({ error: "JSON 본문이 필요합니다" }, 400);
  const result = applyOp(store, body);
  if (result.write) await kv.put(STORE_KEY, JSON.stringify(result.write));
  return json(result.payload, result.status);
}
