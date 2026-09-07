"""관리자 판정 오버레이 — V2 admin_overrides.py 의 **최소 이식** (5차, 2026-09-07).

V2 원본(679줄)은 분리 판정 쌍·학습 규칙까지 담지만, V1 운영 콘솔(A단계)은 아직
'두 기사를 다른 사건으로 분리' 판정을 만들지 않는다. issue_continuity 의 거부권
자리만 유지한다 — 콘솔이 분리 판정을 내기 시작하면(B단계 이후) V2 원본으로
대체하고, 그때까지 이 함수는 admin_overrides.json 의 `split_pairs` 만 읽는다
(형식: [[left_hash, right_hash], ...] — V2 blocked_pairs 와 같은 뜻).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
OVERLAY_FILE = ROOT / "admin_overrides.json"


def _pairs(path: Path | None = None) -> set[frozenset[str]]:
    try:
        data = json.loads((path or OVERLAY_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    out: set[frozenset[str]] = set()
    for pair in (data.get("split_pairs") or []) if isinstance(data, dict) else []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            left, right = str(pair[0] or ""), str(pair[1] or "")
            if left and right:
                out.add(frozenset((left, right)))
    return out


def merge_blocked(left: dict, right: dict, path: Path | None = None) -> dict | None:
    """두 기사를 접으면 안 되는가. 막을 이유가 있으면 진단 레코드, 없으면 None."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_hash = str(left.get("hash") or "")
    right_hash = str(right.get("hash") or "")
    if not (left_hash and right_hash):
        return None
    if frozenset((left_hash, right_hash)) in _pairs(path):
        return {"kind": "admin_split", "rule_label": "관리자 분리",
                "left_hash": left_hash, "right_hash": right_hash,
                "explanation": "관리자가 다른 사건으로 판정한 조합입니다"}
    return None
