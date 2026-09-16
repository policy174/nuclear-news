/* 푸시 구독 저장소 — POST {subscription} 저장, DELETE {endpoint} 삭제.
 * 저장소는 /admin 과 같은 KV(ADMIN_KV), 키 접두어 push:sub:. 인증 없음: 구독 객체는
 * 브라우저가 만든 것이고, 남의 것을 지우려면 endpoint 전체를 알아야 한다(추측 불가).
 * 목록 조회는 list.js — 그쪽만 토큰을 건다. */
const PREFIX = "push:sub:";

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function keyFor(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return PREFIX + [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function validSubscription(sub) {
  return sub && typeof sub.endpoint === "string" && sub.endpoint.startsWith("https://")
    && sub.endpoint.length < 2048 && sub.keys && typeof sub.keys.p256dh === "string"
    && typeof sub.keys.auth === "string";
}

export async function onRequestPost({ request, env }) {
  if (!env.ADMIN_KV) return json({ error: "kv unavailable" }, 503);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const sub = body && body.subscription;
  if (!validSubscription(sub)) return json({ error: "bad subscription" }, 400);
  const record = {
    endpoint: sub.endpoint,
    keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth },
    expirationTime: sub.expirationTime || null,
    ua: (request.headers.get("User-Agent") || "").slice(0, 200),
    created_at: new Date().toISOString(),
  };
  await env.ADMIN_KV.put(await keyFor(sub.endpoint), JSON.stringify(record));
  return json({ ok: true }, 201);
}

export async function onRequestDelete({ request, env }) {
  if (!env.ADMIN_KV) return json({ error: "kv unavailable" }, 503);
  let body;
  try { body = await request.json(); } catch { return json({ error: "bad json" }, 400); }
  const endpoint = body && body.endpoint;
  if (typeof endpoint !== "string" || !endpoint.startsWith("https://")) return json({ error: "bad endpoint" }, 400);
  await env.ADMIN_KV.delete(await keyFor(endpoint));
  return json({ ok: true });
}
