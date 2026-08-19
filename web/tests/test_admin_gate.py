"""/admin 엣지 게이트 계약.

미들웨어(functions/admin/_middleware.js)는 node 로 실행해 검증한다 — Pages
런타임과 node 둘 다 WebCrypto·fetch 전역을 제공하므로 같은 코드가 돈다.
잠그는 것: ① env 미설정 → 404 (기본 폐쇄) ② 쿠키 변조 → 401 ③ 만료 경과 →
401 (서버가 서명된 expiry 를 검사 — Max-Age 는 브라우저 편의일 뿐) ④ 정상
로그인 → Set-Cookie + 303, 유효 쿠키 → 통과 + no-store.
"""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIDDLEWARE = ROOT.parent / "functions" / "admin" / "_middleware.js"

DRIVER = """
import { onRequest, makeSessionCookie, verifySessionCookie } from MIDDLEWARE_PATH;

const env = { ADMIN_PASSWORD: "pw-test", ADMIN_SESSION_SECRET: "secret-test" };
const now = Math.floor(Date.now() / 1000);
const results = {};

async function run(request, envArg) {
  const response = await onRequest({
    request,
    env: envArg,
    next: async () => new Response("INNER", { status: 200 }),
  });
  return { status: response.status, cookie: response.headers.get("Set-Cookie") || "",
           cache: response.headers.get("Cache-Control") || "",
           location: response.headers.get("Location") || "" };
}

// ① env 미설정 → 404
results.no_env = await run(new Request("https://x/admin/"), {});

// ② 정상 로그인 → 303 + Set-Cookie
const form = new FormData();
form.set("password", "pw-test");
results.login = await run(new Request("https://x/admin/", { method: "POST", body: form }), env);

// ③ 오답 → 401
const bad = new FormData();
bad.set("password", "nope");
results.wrong = await run(new Request("https://x/admin/", { method: "POST", body: bad }), env);

// ④ 유효 쿠키 → 통과(200) + no-store
const cookie = await makeSessionCookie(env.ADMIN_SESSION_SECRET, now);
results.valid = await run(new Request("https://x/admin/", {
  headers: { Cookie: `nuclens_admin=${cookie}` } }), env);

// ⑤ 변조 쿠키 → 401
const tampered = cookie.slice(0, -2) + (cookie.endsWith("00") ? "11" : "00");
results.tampered = await run(new Request("https://x/admin/", {
  headers: { Cookie: `nuclens_admin=${tampered}` } }), env);

// ⑥ 만료 경과 → verify false (서명은 유효해도 expiry 가 지났다)
const expired = await makeSessionCookie(env.ADMIN_SESSION_SECRET, now - 86401);
results.expired_verify = await verifySessionCookie(env.ADMIN_SESSION_SECRET, expired, now);
results.valid_verify = await verifySessionCookie(env.ADMIN_SESSION_SECRET, cookie, now);

console.log(JSON.stringify(results));
"""


class AdminGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        driver = DRIVER.replace(
            "MIDDLEWARE_PATH", json.dumps(MIDDLEWARE.resolve().as_uri()))
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", driver],
            capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"node driver 실패:\n{proc.stderr}")
        cls.results = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_missing_env_returns_404(self):
        self.assertEqual(self.results["no_env"]["status"], 404)

    def test_login_sets_signed_cookie_and_redirects(self):
        login = self.results["login"]
        self.assertEqual(login["status"], 303)
        self.assertEqual(login["location"], "/admin/")
        self.assertIn("nuclens_admin=v1.", login["cookie"])
        for attr in ("HttpOnly", "Secure", "SameSite=Lax"):
            self.assertIn(attr, login["cookie"])
        self.assertIn("no-store", login["cache"])

    def test_wrong_password_is_401(self):
        self.assertEqual(self.results["wrong"]["status"], 401)

    def test_valid_cookie_passes_with_no_store(self):
        valid = self.results["valid"]
        self.assertEqual(valid["status"], 200)
        self.assertIn("no-store", valid["cache"])
        self.assertTrue(self.results["valid_verify"])

    def test_tampered_cookie_is_401(self):
        self.assertEqual(self.results["tampered"]["status"], 401)

    def test_expired_cookie_fails_server_side(self):
        self.assertFalse(self.results["expired_verify"])


if __name__ == "__main__":
    unittest.main()
