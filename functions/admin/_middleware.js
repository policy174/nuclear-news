/* /admin/ 엣지 게이트 (Cloudflare Pages Functions).
 *
 * 원칙: 문이 어디 있는지는 보이되 잠겨 있다 — 숨긴 주소로 보호하는 척하지
 * 않는다. 환경변수 둘 다 없으면 404 로 문 자체를 닫는다(기본 폐쇄):
 *   ADMIN_PASSWORD        로그인 검증
 *   ADMIN_SESSION_SECRET  쿠키 서명 — 비밀번호로 서명하지 않는다. 서명 재료가
 *                         비밀번호면 쿠키에서 비밀번호를 향한 오프라인 공격면이
 *                         생기고, 비밀번호 교체와 세션 무효화가 묶인다.
 *
 * 쿠키는 v1.<만료unix초>.<hex(HMAC-SHA256(secret, "v1.<만료>"))>. 만료가 서명
 * 안에 있고 서버가 매 요청 검사한다 — Max-Age 는 브라우저 편의일 뿐, 복사된
 * 쿠키는 브라우저 만료를 따르지 않는다.
 */

const COOKIE_NAME = "nuclens_admin";
const SESSION_SECONDS = 86400;

async function hmacHex(secret, payload) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2, "0")).join("");
}

export async function makeSessionCookie(secret, nowSeconds) {
  const expiry = nowSeconds + SESSION_SECONDS;
  const payload = `v1.${expiry}`;
  return `${payload}.${await hmacHex(secret, payload)}`;
}

export async function verifySessionCookie(secret, value, nowSeconds) {
  const parts = String(value || "").split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return false;
  const expiry = Number(parts[1]);
  if (!Number.isFinite(expiry) || expiry <= nowSeconds) return false;
  const expected = await hmacHex(secret, `v1.${parts[1]}`);
  // 길이 고정(hex 64자) 비교 — 조기 종료 타이밍 차이를 줄인다.
  if (parts[2].length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i += 1) {
    diff |= parts[2].charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

function readCookie(request) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === COOKIE_NAME) return rest.join("=");
  }
  return "";
}

function loginPage(message = "") {
  return `<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>운영 콘솔 로그인 · Nuclens</title>
<style>
  body { margin: 0; display: grid; place-items: center; min-height: 100vh;
         background: #eef1f4; color: #12294c;
         font-family: Pretendard, system-ui, sans-serif; }
  form { display: grid; gap: 12px; width: min(320px, 90vw); padding: 28px;
         background: #fff; border: 1px solid #c9d2de; border-radius: 6px; }
  h1 { margin: 0; font-size: 17px; }
  input { padding: 10px 12px; border: 1px solid #c9d2de; border-radius: 4px;
          font-size: 15px; }
  button { padding: 10px; border: 0; border-radius: 4px; background: #12294c;
           color: #fff; font-size: 15px; font-weight: 700; cursor: pointer; }
  .err { margin: 0; color: #b4232a; font-size: 13px; }
</style></head><body>
<form method="post" action="/admin/">
  <h1>Nuclens 운영 콘솔</h1>
  ${message ? `<p class="err">${message}</p>` : ""}
  <input type="password" name="password" placeholder="비밀번호" autofocus required>
  <button type="submit">로그인</button>
</form>
</body></html>`;
}

function htmlResponse(body, status) {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

export async function onRequest(context) {
  const { request, env } = context;
  const password = env.ADMIN_PASSWORD;
  const secret = env.ADMIN_SESSION_SECRET;
  if (!password || !secret) {
    // 게이트 미설정 = 콘솔 없음. 로그인 화면을 보여 주는 것조차 거짓이 된다.
    return new Response("Not Found", { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  const now = Math.floor(Date.now() / 1000);

  // 인증 확인이 **먼저**다. POST 를 먼저 로그인 시도로 가로채면 /admin/api 같은
  // 하위 라우트의 쓰기 요청이 영원히 로그인 폼(401)을 받는다 — 2026-08-20
  // 로컬 검증에서 판정 추가가 통째로 막혔다.
  if (await verifySessionCookie(secret, readCookie(request), now)) {
    const response = await context.next();
    const guarded = new Response(response.body, response);
    guarded.headers.set("Cache-Control", "no-store");
    return guarded;
  }

  if (request.method === "POST") {
    const form = await request.formData().catch(() => null);
    if (form && form.get("password") === password) {
      const cookie = await makeSessionCookie(secret, now);
      return new Response(null, {
        status: 303,
        headers: {
          Location: "/admin/",
          "Cache-Control": "no-store",
          "Set-Cookie": `${COOKIE_NAME}=${cookie}; Path=/admin; Max-Age=${SESSION_SECONDS}; HttpOnly; Secure; SameSite=Lax`,
        },
      });
    }
    return htmlResponse(loginPage("비밀번호가 일치하지 않습니다."), 401);
  }

  return htmlResponse(loginPage(), 401);
}
