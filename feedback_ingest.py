"""
텔레그램 피드백 수거 — 브리핑 카드의 inline keyboard 응답을 JSONL 로 축적.

구조 (서버 없음 — GitHub Actions 매시간 crawl 워크플로에 편승):
    daily_brief 가 카드마다 [n 👍][n 👎][n 💰][n 📌] 버튼을 붙인다
    (callback_data = "fb:<hash8>:<label>", 64B 한도 대비 ~20B).
    이 스크립트가 getUpdates 로 눌린 버튼을 수거해 feedback/YYYY-MM.jsonl 에 append.
    랭킹(ranking.build_feedback_priors)이 이 파일을 읽어 도메인/theme 사전확률로 반영
    — 단 표본 min_samples 미만이면 미적용 (희소 데이터 왜곡 방지).

UX 전제 (문서화):
    버튼을 누르면 최대 1시간 뒤에 수거된다. answerCallbackQuery 는 best-effort —
    누른 직후의 로딩 스피너는 몇 초 뒤 클라이언트가 알아서 지운다(정상).

원자성·중복 방지:
    - offset(마지막 update_id)은 feedback_state.json 에 저장 → 커밋.
    - 같은 update_id 는 두 번 기록 안 함 (offset + 월 파일 내 update_id 검사 이중 방어).
    - 같은 사용자가 같은 기사에 같은 라벨을 또 누르면 기록 안 함 (오터치 방어).
    - state push 실패로 offset 이 과거로 돌아가도 월 파일 update_id 검사가 중복을 막음.

가드레일: stdlib only. 토큰 없거나 API 실패 시 조용히 종료 (crawl 을 막지 않음).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "feedback_state.json"
FEEDBACK_DIR = ROOT / "feedback"
KST = timezone(timedelta(hours=9))

VALID_LABELS = {"important", "noise", "invest", "report"}
_ANSWER_TEXT = {"important": "👍 중요 기록", "noise": "👎 노이즈 기록",
                "invest": "💰 투자 유용 기록", "report": "📌 보고서감 기록"}


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _api(method: str, params: dict, timeout: float = 25.0) -> dict:
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def month_file(now: datetime | None = None) -> Path:
    now = now or datetime.now(KST)
    return FEEDBACK_DIR / f"{now:%Y-%m}.jsonl"


def _load_month_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def parse_callback(data: str) -> tuple[str, str] | None:
    """'fb:<hash8>:<label>' → (hash8, label). 형식 안 맞으면 None."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "fb":
        return None
    h8, label = parts[1], parts[2]
    if not h8 or len(h8) > 16 or label not in VALID_LABELS:
        return None
    return h8, label


def extract_events(updates: list[dict], existing_update_ids: set[int],
                   existing_triples: set[tuple[str, str, int]],
                   now_iso: str) -> tuple[list[dict], list[tuple[str, str]], int]:
    """updates → (신규 이벤트, answerCallbackQuery 대상 (id, label), 최대 update_id).

    순수 함수 (테스트 용이). 중복 update_id·동일 (hash,label,user) 재클릭은 스킵.
    """
    events: list[dict] = []
    answers: list[tuple[str, str]] = []
    max_id = 0
    for up in updates:
        uid = up.get("update_id")
        if not isinstance(uid, int):
            continue
        max_id = max(max_id, uid)
        cq = up.get("callback_query")
        if not isinstance(cq, dict):
            continue
        parsed = parse_callback(cq.get("data", ""))
        if parsed is None:
            continue
        h8, label = parsed
        if cq.get("id"):
            answers.append((str(cq["id"]), label))
        if uid in existing_update_ids:
            continue
        from_id = ((cq.get("from") or {}).get("id")) or 0
        triple = (h8, label, from_id)
        if triple in existing_triples:
            continue  # 같은 사람이 같은 버튼 재클릭 — 오터치 방어
        events.append({
            "ts": now_iso,
            "update_id": uid,
            "hash": h8,
            "label": label,
            "from": from_id,
        })
        existing_update_ids.add(uid)
        existing_triples.add(triple)
    return events, answers, max_id


def main() -> int:
    if not _token():
        print("[feedback] TELEGRAM_BOT_TOKEN 없음 → 스킵")
        return 0

    state = load_state()
    offset = state.get("offset")
    params: dict = {"timeout": 0, "allowed_updates": json.dumps(["callback_query"])}
    if isinstance(offset, int):
        params["offset"] = offset + 1

    try:
        resp = _api("getUpdates", params)
    except urllib.error.HTTPError as e:
        # 409 = 다른 getUpdates 세션과 충돌 — 다음 시간에 재시도하면 됨
        print(f"[feedback] getUpdates HTTP {e.code} → 스킵")
        return 0
    except Exception as e:  # noqa: BLE001 — 피드백 실패가 crawl 을 막지 않게
        print(f"[feedback] getUpdates 실패 → 스킵: {type(e).__name__}")
        return 0

    updates = resp.get("result") or []
    if not updates:
        print("[feedback] 새 update 없음")
        return 0

    path = month_file()
    existing = _load_month_events(path)
    existing_ids = {e.get("update_id") for e in existing if isinstance(e.get("update_id"), int)}
    existing_triples = {(e.get("hash", ""), e.get("label", ""), e.get("from") or 0)
                        for e in existing}

    now_iso = datetime.now(timezone.utc).isoformat()
    events, answers, max_id = extract_events(updates, existing_ids, existing_triples,
                                             now_iso)

    if events:
        FEEDBACK_DIR.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            for ev in events:
                fp.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # 버튼 로딩 스피너 해제 (오래된 콜백은 텔레그램이 거부 — best effort)
    for cq_id, label in answers:
        try:
            _api("answerCallbackQuery",
                 {"callback_query_id": cq_id, "text": _ANSWER_TEXT.get(label, "기록됨")})
        except Exception:  # noqa: BLE001
            pass

    if max_id:
        state["offset"] = max_id
        save_state(state)

    print(f"[feedback] update {len(updates)}건 수신 → 신규 이벤트 {len(events)}건 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main())
