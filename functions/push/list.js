/* 구독 목록 — 발송기(push_notify.py, GitHub Actions)만 읽는다.
 * Authorization: Bearer <PUSH_ADMIN_TOKEN>. 토큰은 Pages 시크릿(wrangler pages secret put)과
 * GitHub Secrets 에 같은 값. 저장소가 공개라 wrangler.toml [vars] 에는 두지 않는다. */
const PREFIX = "push:sub:";

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export async function onRequestGet({ request, env }) {
  if (!env.PUSH_ADMIN_TOKEN) return json({ error: "PUSH_ADMIN_TOKEN 미설정" }, 503);
  const auth = request.headers.get("Authorization") || "";
  if (auth !== `Bearer ${env.PUSH_ADMIN_TOKEN}`) return json({ error: "unauthorized" }, 401);
  if (!env.ADMIN_KV) return json({ error: "kv unavailable" }, 503);
  const subs = [];
  let cursor;
  do {
    const page = await env.ADMIN_KV.list({ prefix: PREFIX, cursor });
    for (const key of page.keys) {
      const raw = await env.ADMIN_KV.get(key.name);
      if (!raw) continue;
      try { subs.push(JSON.parse(raw)); } catch { /* 깨진 항목은 건너뛴다 */ }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return json({ count: subs.length, subscriptions: subs });
}
