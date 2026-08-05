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
# v2: gist 가 제목을 한국어로 되풀이하기만 했다("미래 STEM 리더 멘토링 행사 개최").
#     읽는 사람은 "이걸 우리가 검토해서 보고서로 쓸 만한가"를 여기서 판단해야
#     하는데, 제목 재진술로는 그 판단이 안 선다. 동시에 off_topic 판정을 같은
#     배치에 얹었다 — 추가 호출 0회.
# v3: off_topic 을 통과한 뒤에도 목록이 안 읽혔다. 실측 2026-08-05 라이브 19건 중
#     12건이 전산유체역학 코드 검증·붕괴열 시뮬레이션·흑연 조사 크리프·계측제어
#     요구공학·외부 선량 측정 같은 **연구 실무자용 기술문서**다. off_topic 규칙은
#     행사·농업만 겨냥하므로 이것들을 잡지 못한다. 지우면 안 되는 문서라(원자력과
#     무관한 게 아니다) 버리는 대신 relevance 축을 하나 더 얹어 화면에서 접는다.
PROMPT_VERSION = 3

RELEVANCE_VALUES = ("policy", "market", "technical")
RELEVANCE_DEFAULT = "policy"

SYSTEM_PROMPT = """너는 한국 원자력 정책 담당자에게 국제기구 발간물을 골라 주는
편집자다. 읽는 사람은 한국수력원자력 정책 부서에서 원전 정책·산업 동향을 본다.
목록에서 제목과 네 한 줄만 보고 "이 문서를 열어서 검토할 가치가 있는가"를
판단할 수 있어야 한다.

영문 제목을 받아 세 가지를 만든다.

1) title_kr — 제목의 한국어 번역
   - 원문 제목의 뜻만 옮긴다. 없는 말을 붙이지 않는다.
   - 기관명·고유명사는 통용 표기 + 영문 약자 병기
     (International Atomic Energy Agency → 국제원자력기구(IAEA))
   - 문서 종류가 제목에 있으면 살린다 (보고서·지침·회의·통계 등)

2) gist — 이 문서가 무엇을 다루는지 한국어 한 줄 (45자 이내)
   - 제목을 한국어로 되풀이하지 않는다. 제목이 "무엇"이면 gist 는
     **문서의 성격과 다루는 범위**를 말한다.
       나쁨: "SMR 가속화" (제목 재진술)
       좋음: "SMR 배치를 앞당기기 위한 규제·공급망 과제 정리"
   - 다음이 제목에서 읽히면 반드시 넣는다: 대상 국가·지역, 제도·규제 이름,
     노형, 문서 성격(지침/현황/통계/사례연구/기술보고).
   - 개조식 명사형으로 끝낸다.
   - 제목에 없는 수치·결론·기관을 지어내지 않는다. 평가·전망·권고·투자 판단 금지.
   - 제목만으로 성격을 알 수 없으면 빈 문자열로 둔다. 억지로 채우지 않는다.

3) off_topic — 원전 정책·산업 동향 파악에 쓸 수 없는 문서면 true
   - true 로 둘 것: 교육·행사·워크숍·여름학교·멘토링·시상·인사·기념 소식,
     프로젝트 내부 진행상황 회의, 원자력 기술을 쓰지만 발전과 무관한 분야
     (농업·식품·축산·수자원·의료 응용 뉴스레터).
   - false 로 둘 것: 정책·규제·인허가·시장·공급망·안전기준·기술보고서·통계.
   - 애매하면 false. 놓치는 것보다 지우는 것이 해롭다.
   - true 일 때만 off_topic_reason 에 한국어로 짧은 사유를 적는다.

4) relevance — 원자력 **정책** 담당자 기준으로 이 문서의 쓰임을 하나 고른다
   - "policy"    정책·제도·규제·인허가·국가 전략·국제 협력·안전 기준 체계·
                 사회적 수용성. 정책실이 보고서에 인용하거나 제도 변화를 읽는 자료.
   - "market"    시장·수요 전망·발전 용량·설비 통계·공급망·핵연료·투자·비용·
                 인력 수급. 산업 동향 숫자가 들어 있는 자료.
   - "technical" 공학·해석·실험·재료·계측·해석코드 검증·방사선 측정 방법론처럼
                 **연구·설계 실무자용** 문서. 정책 판단에 바로 쓰이지 않는다.
                 예: 전산유체역학 코드 검증, 붕괴열 시뮬레이션 데이터, 흑연 조사
                 크리프 거동, 계측제어 요구사항 공학, 외부 선량 측정량 보고서.
   - 정책·제도 얘기가 조금이라도 주된 축이면 technical 이 아니라 policy 다.
     예: "SMR 배치 가속화"는 규제·공급망 과제를 다루므로 policy.
   - 판단이 안 서면 "policy". 기술문서는 화면에서 접히므로, 잘못 접는 쪽이 해롭다.

출력은 JSON 하나:
{"items": [{"idx": 0, "title_kr": "...", "gist": "...", "off_topic": false,
            "off_topic_reason": "", "relevance": "policy"}]}
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


def _parse(payload: object, count: int) -> dict[int, dict]:
    out: dict[int, dict] = {}
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
        reason = " ".join(str(row.get("off_topic_reason") or "").split()).strip()
        relevance = str(row.get("relevance") or "").strip().lower()
        if relevance not in RELEVANCE_VALUES:
            # 모르는 값은 접지 않는다 — 잘못 접는 쪽이 해롭다(프롬프트와 같은 방향).
            relevance = RELEVANCE_DEFAULT
        if title_kr:
            out[idx] = {
                "title_kr": title_kr[:160],
                "gist": gist[:80],
                # 문자열 "false" 가 참으로 읽히지 않게 명시적으로 판정한다
                "off_topic": row.get("off_topic") is True,
                "off_topic_reason": reason[:60],
                "relevance": relevance,
            }
    return out


def translate(items: list[dict], *, client=None, batch_size: int = BATCH_SIZE) -> dict:
    """items 를 제자리에서 갱신한다. 반환값은 통계."""
    stats = {"candidates": 0, "translated": 0, "calls": 0, "off_topic": 0, "status": "ok"}
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
        for idx, row in _parse(payload, len(chunk)).items():
            chunk[idx]["title_kr"] = row["title_kr"]
            if row["gist"]:
                chunk[idx]["gist"] = row["gist"]
            # off_topic 은 False 도 눌러 담는다 — 키가 없으면 build_data 가
            # 제목 규칙으로 되돌아가고, LLM 이 "관련 있음"이라 판정한 것을
            # 규칙이 다시 지운다.
            chunk[idx]["off_topic"] = row["off_topic"]
            if row["off_topic"]:
                chunk[idx]["off_topic_reason"] = row["off_topic_reason"]
                stats["off_topic"] += 1
            else:
                chunk[idx].pop("off_topic_reason", None)
            chunk[idx]["relevance"] = row["relevance"]
            stats.setdefault("relevance", {})
            stats["relevance"][row["relevance"]] = \
                stats["relevance"].get(row["relevance"], 0) + 1
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
