"""국제기구 발간물 제목을 한국어로 옮기고 한 줄로 설명한다.

배경: 발간물 탭에 IAEA·NEA·IEA·EIA 원문 제목이 영어 그대로 떠서 "이게 뭔지
모르겠다"는 피드백(2026-08-02). 제목만으로는 보고서인지 회의 소식인지,
우리 업무와 무슨 상관인지 알 수 없다.

설계:
  - 배치 1회 호출로 여러 건을 한 번에 처리. 신규 발간물만 대상이라 하루 몇 건.
  - 결과는 publications.json 에 title_kr·gist 로 눌러 담는다(캐시). 이미 번역된
    항목은 다시 묻지 않는다.
  - **번역 실패는 조용히 통과** — title_kr 이 없으면 화면이 원문 제목만 보인다.
    발간물이 안 뜨는 것보다 영어로라도 뜨는 게 낫다.

가드레일:
  - 제목에 없는 내용을 지어내지 말 것. 특히 수치·기관·결론 추가 금지.
  - gist 는 "무엇에 관한 문서인가" 한 줄. 평가·전망·권고 금지.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent

BATCH_SIZE = 15
PROMPT_VERSION = 1

SYSTEM_PROMPT = """너는 원자력·에너지 분야 국제기구 발간물을 한국 정책 담당자에게
소개하는 편집자다. 영문 제목을 받아 두 가지를 만든다.

1) title_kr — 제목의 한국어 번역
   - 원문 제목의 뜻만 옮긴다. 없는 말을 붙이지 않는다.
   - 기관명·고유명사는 통용 표기를 쓰고 필요하면 영문 병기
     (예: Nuclear Energy Outlook → 원자력 에너지 전망)
   - 문서 종류가 제목에 있으면 살린다 (보고서·지침·회의·통계 등)

2) gist — 이 문서가 무엇에 관한 것인지 한국어 한 줄 (35자 이내)
   - 제목에서 읽어낼 수 있는 범위만 쓴다. 내용을 추측해 요약하지 않는다.
   - 개조식 명사형으로 끝낸다 (…현황, …지침, …전망, …회의 결과)
   - 제목이 이미 자명하면 gist 를 빈 문자열로 둔다. 억지로 채우지 않는다.
   - 평가·전망·권고·투자 판단 금지.

출력은 JSON 하나:
{"items": [{"idx": 0, "title_kr": "...", "gist": "..."}]}
입력에 준 idx 를 모두 포함한다."""


def needs_translation(item: dict) -> bool:
    if not isinstance(item, dict) or not item.get("title"):
        return False
    if item.get("translated_version") == PROMPT_VERSION and item.get("title_kr"):
        return False
    # 이미 한국어인 제목(에경연 인사이트 등)은 번역할 것이 없다
    title = str(item.get("title") or "")
    hangul = sum(1 for ch in title if "가" <= ch <= "힣")
    return hangul < len(title) * 0.2


def build_user_message(items: list[dict]) -> str:
    lines = []
    for index, item in enumerate(items):
        lines.append(f"[{index}] ({item.get('org', '')}) {item.get('title', '')}")
    return "\n".join(lines)


def _parse(payload: object, count: int) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    rows = payload.get("items") if isinstance(payload, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < count:
            continue
        title_kr = " ".join(str(row.get("title_kr") or "").split()).strip()
        gist = " ".join(str(row.get("gist") or "").split()).strip()
        if title_kr:
            out[idx] = (title_kr[:160], gist[:60])
    return out


def translate(items: list[dict], *, client=None, batch_size: int = BATCH_SIZE) -> dict:
    """items 를 제자리에서 갱신한다. 반환값은 통계."""
    stats = {"candidates": 0, "translated": 0, "calls": 0, "status": "ok"}
    todo = [item for item in items if needs_translation(item)]
    stats["candidates"] = len(todo)
    if not todo:
        stats["status"] = "nothing_to_do"
        return stats

    if client is None:
        import gemini_client as client

    if not client.is_available():
        stats["status"] = "no_api_key"
        return stats

    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        try:
            payload = client.call_json(SYSTEM_PROMPT, build_user_message(chunk),
                                       temperature=0.1, max_output_tokens=8192)
            stats["calls"] += 1
        except Exception as exc:  # 번역 실패는 비치명 — 원문 제목으로 뜬다
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        for idx, (title_kr, gist) in _parse(payload, len(chunk)).items():
            chunk[idx]["title_kr"] = title_kr
            if gist:
                chunk[idx]["gist"] = gist
            chunk[idx]["translated_version"] = PROMPT_VERSION
            stats["translated"] += 1
    return stats


if __name__ == "__main__":  # 단독 실행 — 이미 수집된 발간물을 번역만 한다
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    path = ROOT / "publications.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    result = translate(store.get("items") or [])
    if result["translated"]:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    print(f"[pubs-kr] 대상 {result['candidates']}건 / 번역 {result['translated']}건 "
          f"/ 호출 {result['calls']}회 [{result['status']}]")
