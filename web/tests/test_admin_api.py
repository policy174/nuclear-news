"""/admin/api 판정 저장소 계약.

node 드라이버로 실행(test_admin_gate.py 패턴) — env.ADMIN_KV 는 Map 스텁.
잠그는 것: KV 부재 503(v2 문구) · version 낙관 잠금 409 · kind/group 화이트
리스트 · 중복 판정 400 · add/delete/toggle 왕복 · 삭제=그 판단만 원복.
"""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT.parent / "functions" / "admin" / "api.js"

DRIVER = """
import { onRequest, applyOp, validateEntry } from API_PATH;

function fakeKV(initial) {
  const store = new Map(initial ? [["judgments", JSON.stringify(initial)]] : []);
  return {
    get: async key => store.get(key) ?? null,
    put: async (key, value) => { store.set(key, value); },
    _dump: () => JSON.parse(store.get("judgments") || "null"),
  };
}

async function call(env, method, body) {
  const request = new Request("https://x/admin/api", {
    method,
    body: body ? JSON.stringify(body) : undefined,
    headers: { "Content-Type": "application/json" },
  });
  const response = await onRequest({ request, env });
  return { status: response.status, body: await response.json() };
}

const results = {};

// ① KV 미바인딩 → 503 + v2 문구
results.no_kv = await call({}, "GET");

// ② 빈 저장소 GET → version 0
const kv = fakeKV(null);
results.empty = await call({ ADMIN_KV: kv }, "GET");

// ③ add 정상 → version 1
const entry = { kind: "keyword_add", group: "정책", value: "이집트 원전", reason: "신규 추적" };
results.add = await call({ ADMIN_KV: kv }, "POST", { op: "add", version: 0, entry });

// ④ 같은 version 재시도 → 409
results.conflict = await call({ ADMIN_KV: kv }, "POST", { op: "add", version: 0, entry: { ...entry, value: "다른 값" } });

// ⑤ 중복 판정 → 400
results.duplicate = await call({ ADMIN_KV: kv }, "POST", { op: "add", version: 1, entry });

// ⑥ kind 화이트리스트
results.bad_kind = applyOp({ version: 0, entries: [] }, { op: "add", version: 0, entry: { kind: "source_add", group: "정책", value: "x" } });
results.bad_group = applyOp({ version: 0, entries: [] }, { op: "add", version: 0, entry: { kind: "keyword_add", group: "없는그룹", value: "x" } });

// ⑦ toggle → disabled true → delete → 원복
const id = kv._dump().entries[0].id;
results.toggle = await call({ ADMIN_KV: kv }, "POST", { op: "toggle", version: 1, id });
results.toggled_flag = kv._dump().entries[0].disabled;
results.del = await call({ ADMIN_KV: kv }, "POST", { op: "delete", version: 2, id });
results.after_delete = kv._dump().entries.length;

console.log(JSON.stringify(results));
"""


class AdminApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        driver = DRIVER.replace("API_PATH", json.dumps(API.resolve().as_uri()))
        # encoding 명시 필수 — node 출력의 한글 문구를 Windows 기본 cp949 로
        # 읽으면 리더 스레드가 죽고 stdout 이 None 이 된다.
        proc = subprocess.run(["node", "--input-type=module", "-e", driver],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"node driver 실패:\n{proc.stderr}")
        cls.r = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_missing_kv_is_503_with_v2_message(self):
        self.assertEqual(self.r["no_kv"]["status"], 503)
        self.assertIn("ADMIN_KV", self.r["no_kv"]["body"]["error"])

    def test_empty_store_starts_at_version_zero(self):
        self.assertEqual(self.r["empty"]["body"], {"version": 0, "entries": []})

    def test_add_bumps_version_and_stamps_fields(self):
        self.assertEqual(self.r["add"]["status"], 200)
        self.assertEqual(self.r["add"]["body"]["version"], 1)
        entry = self.r["add"]["body"]["entries"][0]
        self.assertTrue(entry["id"])
        self.assertTrue(entry["created_at"])
        self.assertFalse(entry["disabled"])

    def test_stale_version_conflicts_409(self):
        self.assertEqual(self.r["conflict"]["status"], 409)
        self.assertIn("먼저 저장", self.r["conflict"]["body"]["error"])

    def test_duplicate_judgment_rejected(self):
        self.assertEqual(self.r["duplicate"]["status"], 400)

    def test_kind_and_group_whitelists(self):
        self.assertEqual(self.r["bad_kind"]["status"], 400)
        self.assertEqual(self.r["bad_group"]["status"], 400)

    def test_toggle_then_delete_roundtrip(self):
        self.assertEqual(self.r["toggle"]["status"], 200)
        self.assertTrue(self.r["toggled_flag"])
        self.assertEqual(self.r["del"]["status"], 200)
        self.assertEqual(self.r["after_delete"], 0)


if __name__ == "__main__":
    unittest.main()
