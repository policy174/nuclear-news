"""배포 후 산출물 검사 — "exit 0 인데 산출물이 없다"를 잡는다.

왜 있는가 (2026-09-12): daily-brief 는 거의 모든 스텝이 `continue-on-error`
이고, 파이썬 쪽도 쿼터 실패를 비치명으로 삼켜 `exit 0` 한다. 그래서 9/08~9/12
닷새간 dedup·오디오·보고서 초안이 전부 죽었는데 워크플로는 초록이었고,
아무도 몰랐다. **조건식(`failure() || cancelled()`)만 고쳐서는 이 병을 못 잡는다**
— GitHub 는 exit 0 을 성공으로 보기 때문이다. 그래서 "스텝이 돌았는가"가 아니라
**"산출물이 실제로 생겼는가"**를 여기서 직접 본다. 이 스크립트만은
`continue-on-error` 없이 붙이고, 실패하면 exit 1 로 잡을 빨갛게 만든다.

판정 셋 (데모 경로만 — 전 시스템 검사가 목적이 아니다):
  1. 오늘자 브리핑이 briefings.json 에 있는가
  2. 초안이 아예 없는 report_pick 대상이 있는데 이번 실행 신규 초안이 0건인가
  3. 프런트가 첫 화면에 쓰는 데이터 파일이 실제로 있고 비어 있지 않은가

오탐 방지가 설계의 절반이다 — 매일 아침 헛울리는 경보는 꺼진 경보보다 나쁘다:
  - 대상이 0건인 날은 PASS (조용한 날은 정상이다)
  - 기준선 파일이 없으면 2번은 SKIP (첫 도입일·수동 실행)
  - 개수가 아니라 **issue_id 집합**을 비교한다. 캐시 만료로 줄었다 늘어난 것을
    성공으로 오판하지 않기 위해서다.

실행: python tools/artifact_guard.py --baseline <실행 전 report_drafts.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "public" / "data"
DRAFTS_FILE = ROOT / "report_drafts.json"

KST = timezone(timedelta(hours=9))
RECENT_DAYS = 21  # report_draft._targets 와 같은 창 — 바뀌면 같이 바꿀 것
CORE_FILES = ("briefings.json", "issues.json", "news.json", "meta.json")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def draft_ids(payload) -> set[str]:
    """report_drafts.json → 초안이 있는 issue_id 집합."""
    drafts = payload.get("drafts") if isinstance(payload, dict) else None
    return set(drafts) if isinstance(drafts, dict) else set()


def pending_targets(issues, have: set[str], today) -> list[str]:
    """초안이 **아예 없는** report_pick 대상.

    report_draft._targets 의 조건(report_pick · last_seen 21일 이내 · 기사 있음)을
    따르되, 재생성(digest 변경) 판정은 흉내내지 않는다 — '아직 한 번도 초안이
    없는 것'만 세면 오탐 없이 "할 일이 있었는가"를 답할 수 있다.
    """
    floor = (today - timedelta(days=RECENT_DAYS)).date().isoformat()
    return [
        str(issue.get("issue_id") or "")
        for issue in (issues or [])
        if issue.get("report_pick")
        and str(issue.get("last_seen") or "") >= floor
        and (issue.get("related_articles") or [])
        and str(issue.get("issue_id") or "") not in have
    ]


def check(baseline: Path | None, now: datetime | None = None) -> list[str]:
    """위반 목록. 빈 리스트면 통과."""
    now = now or datetime.now(KST)
    today = now.date().isoformat()
    failures: list[str] = []

    # 1. 오늘자 브리핑
    briefings = _load(DATA / "briefings.json", [])
    dates = {str(b.get("date") or "") for b in briefings if isinstance(b, dict)}
    if today not in dates:
        newest = max(dates) if dates else "(없음)"
        failures.append(f"오늘({today}) 브리핑이 briefings.json 에 없음 — 최신은 {newest}")

    # 2. 보고서 초안이 생겼는가
    after = draft_ids(_load(DRAFTS_FILE, {}))
    pending = pending_targets(_load(DATA / "issues.json", []), after, now)
    if baseline is None or not baseline.exists():
        print("[guard] 기준선 없음 — 초안 증가 검사 SKIP")
    elif not pending:
        print("[guard] 초안 미보유 report_pick 대상 0건 — 초안 검사 PASS(할 일 없음)")
    else:
        before = draft_ids(_load(baseline, {}))
        fresh = after - before
        if not fresh:
            failures.append(
                f"초안 없는 report_pick 대상 {len(pending)}건이 남았는데 신규 초안 0건 "
                f"(보유 {len(after)}건 그대로) — 쿼터 고갈 의심")
        else:
            print(f"[guard] 신규 초안 {len(fresh)}건 — 남은 대상 {len(pending)}건")

    # 3. 첫 화면 데이터
    for name in CORE_FILES:
        payload = _load(DATA / name, None)
        if payload is None:
            failures.append(f"{name} 가 없거나 JSON 이 아님")
        elif isinstance(payload, (list, dict)) and not payload:
            failures.append(f"{name} 가 비어 있음")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 후 산출물 검사")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="실행 전 report_drafts.json 스냅샷")
    args = parser.parse_args()

    failures = check(args.baseline)
    if failures:
        print("산출물 검사 실패:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("산출물 검사 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
