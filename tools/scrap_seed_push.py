"""조간 신문스크랩 카톡방 → scrap_seeds.json 커밋 (PC 전용, stdlib만).

카톡은 이 PC의 프로세스 메모리에서만 읽을 수 있어 이 단계만 로컬이다.
kakao-read 스킬의 sync 를 돌리고, 누적 저장소(store.db)에서 "스크랩 보고"
메시지를 찾아 매체명·제목만 파싱해 GitHub API 로 scrap_seeds.json 을
갱신한다 — 로컬 git 작업트리를 건드리지 않는다(오래된 체크아웃·자격증명
꼬임 회피). 원문 URL 역추적·주입은 다음 크롤(Actions)이 한다.

사내 스크랩 PDF·지면 스캔·surl 링크는 절대 올리지 않는다 — 매체명·제목뿐.

실행:  python tools/scrap_seed_push.py          (레포 안 어디서든)
예약:  작업 스케줄러에 위 명령 등록 (조건: 카톡 로그인 상태.
       스크랩 방을 한 번 열어둔 뒤부터는 새 메시지가 메모리에 잡힌다)
"""
from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scrap_seed_ingest import SEED_MAX_AGE_DAYS, parse_scrap_report, seed_key  # noqa: E402

KAKAO_WIN = Path.home() / ".claude/skills/kakao-read/scripts/win/kakao_win.py"
STORE_DB = Path.home() / ".config/kakao-read/store.db"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
REPO = "policy174/nuclear-news"
SEEDS_PATH = "scrap_seeds.json"
KEEP_DAYS = 14  # 커밋 파일에 남길 시드 수명 (ingest 쪽 재시도 창보다 넉넉히)


def sync_kakao() -> None:
    r = subprocess.run([sys.executable, str(KAKAO_WIN), "sync"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()
    print("[sync]", tail[-2] if len(tail) >= 2 else r.stdout.strip() or r.stderr.strip()[:200])
    if r.returncode != 0:
        raise SystemExit(f"kakao sync 실패 (카톡 실행 중인지 확인): {r.stderr.strip()[:200]}")


def collect_seeds() -> list[dict]:
    con = sqlite3.connect(STORE_DB)
    cur = con.cursor()
    # 스크랩 보고는 형식이 고유해서 방을 특정할 필요가 없다 — 전 메시지에서
    # 패턴으로 찾는다(메모리 덤프의 방 사슬 조각화에도 강건).
    cur.execute("SELECT sendAt, message FROM messages WHERE message LIKE '%스크랩 보고%'")
    seeds: dict[str, dict] = {}
    for send_at, message in cur.fetchall():
        ts = datetime.fromtimestamp(send_at if send_at < 1e12 else send_at / 1000)
        for seed in parse_scrap_report(message or "", ts.year):
            seeds[seed_key(seed)] = seed
    con.close()
    return list(seeds.values())


def gh_token() -> str:
    r = subprocess.run([GH, "auth", "token", "-u", REPO.split("/")[0]],
                       capture_output=True, text=True)
    token = (r.stdout or "").strip()
    if not token.startswith("gh"):
        raise SystemExit(f"gh 토큰 조회 실패: {r.stderr.strip()[:200]}")
    return token


def gh_api(token: str, method: str, path: str, body: dict | None = None) -> dict:
    cmd = [GH, "api", "-X", method, path, "-H", f"Authorization: token {token}"]
    if body:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(body) if body else None,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 and "404" not in (r.stderr or ""):
        raise SystemExit(f"gh api {path} 실패: {(r.stderr or '')[:300]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def main() -> None:
    sync_kakao()
    fresh = collect_seeds()
    if not fresh:
        print("스크랩 보고 메시지 없음 — 카톡에서 방을 한 번 열고 다시 실행")
        return

    token = gh_token()
    current = gh_api(token, "GET", f"repos/{REPO}/contents/{SEEDS_PATH}")
    existing, sha = [], current.get("sha")
    if current.get("content"):
        try:
            existing = json.loads(base64.b64decode(current["content"]))
        except (ValueError, json.JSONDecodeError):
            existing = []

    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).date().isoformat()
    merged = {seed_key(s): s for s in existing if (s.get("date") or "") >= cutoff}
    added = 0
    for seed in fresh:
        if seed_key(seed) not in merged:
            merged[seed_key(seed)] = seed
            added += 1
    if not added and sha:
        print(f"신규 시드 0건 (기존 {len(merged)}건 유지) — 커밋 생략")
        return

    payload = sorted(merged.values(), key=lambda s: (s["date"], s["publisher"], s["title"]))
    body = {
        "message": f"chore: scrap seeds +{added} (신문스크랩 카톡방, {datetime.now():%m-%d})",
        "content": base64.b64encode(
            (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")).decode(),
    }
    if sha:
        body["sha"] = sha
    result = gh_api(token, "PUT", f"repos/{REPO}/contents/{SEEDS_PATH}", body)
    commit = (result.get("commit") or {}).get("sha", "")[:7]
    print(f"커밋 완료 {commit}: 신규 {added}건 / 총 {len(payload)}건 → 다음 크롤이 원문 역추적")


if __name__ == "__main__":
    main()
