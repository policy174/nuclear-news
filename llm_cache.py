"""LLM 판정 캐시의 공통 봉투 — 읽기·쓰기·버전 확인.

배경:
    issue_review · keei_match · issue_insight 이 같은 골격을 세 벌 복제하고 있었다
    (2026-08-06 실측: load_cache/save_cache 가 안쪽 키 이름과 주석만 다르고
    나머지는 글자까지 같았다). 복제는 코드만 늘린 게 아니라 **결함도 복제했다** —
    "거부 판정이 근거 변화를 못 따라간다"를 issue_review 에서 고친 날, 같은 결함이
    keei_match 에 그대로 살아 있었다.

    다만 **무효화 규칙까지 공통화하지는 않는다.** 두 모듈의 처방이 실제로 달랐다:
      · issue_review — 두 이슈 제목의 어휘 겹침 상승(+0.10)
      · keei_match  — 이슈 제목이 바뀌었는가 (KEEI 목차는 로마자, 이슈는 한글이라
                      어휘 겹침이 같은 사건을 못 본다. 같은 문턱으로 재니 0건 걸렸다)
    공통인 것은 **봉투와 버전 확인**이지 판단이 아니다. 판단을 억지로 합치면
    한쪽에서 맞는 규칙이 다른 쪽에서 조용히 틀린다.

가드레일:
    - stdlib 만 사용. 어떤 도메인 모듈도 import 하지 않는다.
    - 기존 동작을 바꾸지 않는다. 모듈마다 다르던 플래그(sort_keys·쓰기 실패 처리)는
      합치지 않고 인자로 받는다 — 정리가 동작 변경을 몰래 태우면 안 된다.
"""

from __future__ import annotations

import json
from pathlib import Path


def load(path: Path, key: str) -> dict:
    """캐시 파일에서 ``key`` 아래의 항목 사전을 읽는다.

    파일이 없거나 깨졌으면 **빈 사전**이다. 여기서 예외를 올리면 캐시 손상 하나가
    파이프라인 전체를 세운다 — 캐시는 없어도 되는 것이고(다시 물으면 된다) 있어야
    하는 것이 아니다.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    entries = raw.get(key)
    return entries if isinstance(entries, dict) else {}


def save(cache: dict, path: Path, *, key: str, prompt_version: int,
         comment: str, sort_keys: bool = True,
         swallow_errors: bool = True) -> None:
    """항목 사전을 봉투에 담아 쓴다.

    ``prompt_version`` 을 파일에도 남기는 이유는 사람이 열었을 때 이 캐시가 어느
    프롬프트의 산물인지 보이게 하려는 것이다. 무효화 판정은 항목별
    ``prompt_version`` 으로 한다(``is_current``).
    """
    payload = {
        "_comment": comment,
        "prompt_version": prompt_version,
        key: cache,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2,
                      sort_keys=sort_keys) + "\n"
    if swallow_errors:
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError:
            pass
    else:
        Path(path).write_text(text, encoding="utf-8")


def is_current(entry: object, prompt_version: int) -> bool:
    """이 항목이 현재 프롬프트의 판정인가.

    프롬프트를 고치면 판정의 근거가 달라지므로 옛 판정은 무효다. 항목이 사전이
    아니거나 버전이 없으면 무효로 본다 — 손상된 항목을 살아 있다고 우기는 것보다
    다시 묻는 쪽이 싸다.
    """
    if not isinstance(entry, dict):
        return False
    return entry.get("prompt_version") == prompt_version
