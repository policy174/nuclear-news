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
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scrap_seed_ingest import (  # noqa: E402
    SEED_MAX_AGE_DAYS, parse_media_trend, parse_scrap_report, seed_key)

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
    # 스크랩 보고·언론 동향은 형식이 고유해서 방을 특정할 필요가 없다 — 전 메시지에서
    # 패턴으로 찾는다(메모리 덤프의 방 사슬 조각화에도 강건).
    cur.execute("SELECT sendAt, message FROM messages "
                "WHERE message LIKE '%스크랩 보고%' OR message LIKE '%언론 동향%'")
    seeds: dict[str, dict] = {}
    for send_at, message in cur.fetchall():
        ts = datetime.fromtimestamp(send_at if send_at < 1e12 else send_at / 1000)
        for seed in parse_scrap_report(message or "", ts.year) + parse_media_trend(message or "", ts.year):
            key = seed_key(seed)
            # 같은 기사가 보고(링크 없음)와 동향(링크 있음) 양쪽에 오면 링크 쪽 승리
            if key not in seeds or (seed.get("link") and not seeds[key].get("link")):
                seeds[key] = seed
    con.close()
    return list(seeds.values())


_TREND_NEEDLE = "언론 동향".encode("utf-8")
_TREND_HEADER_RE = re.compile(r"\d{1,2}\s*\.\s*\d{1,2}\s*언론\s*동향[^\n]*")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def collect_trend_seeds_from_dump() -> list[dict]:
    """'언론 동향'(장문·링크 다수) 메시지를 sync 가 남긴 원시 덤프에서 직접 추출.

    긴 메시지는 SQLite 오버플로 페이지에 저장돼 store 재구성 파서가 통째로
    놓친다 — 2026-09-05 실측: store 0건 vs 원시 덤프에서 시드 247건. 같은
    메시지가 메모리에 여러 번 나타나므로(23곳) seed_key 로 합집합한다.
    창이 잘려 일부만 파싱되는 조각도 다른 사본이 메운다.
    """
    dump = Path(tempfile.gettempdir()) / "kt_kakao.dmp"
    if not dump.exists():
        return []
    data = dump.read_bytes()  # ~800MB — 실측 무리 없음. 조각내면 경계 유실만 생긴다
    seeds: dict[str, dict] = {}
    pos = -1
    while True:
        pos = data.find(_TREND_NEEDLE, pos + 1)
        if pos < 0:
            break
        text = data[max(0, pos - 256): pos + 24 * 1024].decode("utf-8", errors="ignore")
        m = _TREND_HEADER_RE.search(text)
        if not m:
            continue
        body = _CONTROL_RE.split(text[m.start():])[0]
        for seed in parse_media_trend(body, datetime.now().year):
            seeds[seed_key(seed)] = seed
    return list(seeds.values())


def resolve_redirect(url: str) -> str | None:
    """스크랩 서비스 단축링크(surl.realsn.com 등)를 공개 원문 URL 로 해소.
    사규·저작권상 surl 자체는 레포에 올리지 않는다 — 커밋 전에 반드시 치환.
    실패하면 None (호출자가 링크 없는 제목 시드로 강등)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            final = r.url or ""
    except Exception:  # noqa: BLE001 — 링크 하나가 push 를 못 막는다
        return None
    if not final.startswith("http"):
        return None
    # 단축링크가 또 다른 단축링크로 오면(실측: lrl.kr) 원문이 아니다
    return None if any(h in final for h in ("realsn.com", "lrl.kr")) else final


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
    known = {seed_key(s) for s in fresh}
    trend = [s for s in collect_trend_seeds_from_dump() if seed_key(s) not in known]
    if trend:
        print(f"[trend] 원시 덤프에서 언론 동향 시드 {len(trend)}건")
    fresh += trend
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
        key = seed_key(seed)
        if key in merged and not (seed.get("link") and not merged[key].get("link")):
            continue
        if seed.get("link"):
            resolved = resolve_redirect(seed["link"])
            if resolved:
                seed["link"] = resolved
            else:
                seed.pop("link")  # 해소 실패 — 제목 검색 경로로 강등
                if key in merged:
                    continue
        merged[key] = seed
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
    print(f"커밋 완료 {commit}: 신규 {added}건 / 총 {len(payload)}건")

    # 새 시드가 있으면 크롤을 즉시 디스패치 — 3시간 그리드를 기다리면 조간
    # (07시 언저리 도착)이 당일 08:05+ 브리핑을 놓친다. dispatch 는 cron 과
    # 달리 지연 없이 뜨므로 07시대 시드가 ~07:45 큐에 앉아 당일 아침에 탄다.
    # 비치명: 실패해도 다음 정기 크롤(3시간 내)이 처리한다.
    try:
        gh_api(token, "POST", f"repos/{REPO}/actions/workflows/crawl.yml/dispatches",
               {"ref": "main"})
        print("크롤 디스패치 완료 → ~30분 내 원문 역추적·사이트 반영")
    except SystemExit as e:
        print(f"크롤 디스패치 실패(비치명, 다음 정기 크롤이 처리): {e}")


if __name__ == "__main__":
    main()
