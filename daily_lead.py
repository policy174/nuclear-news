"""오늘의 한 문장 — 그날 브리핑에 오른 이슈 전체를 한 문장으로 종합.

문제: 웹 히어로가 "오늘, 무엇이 달라졌는가"라고 묻고는 이슈 한 건의 문장을
넣고 있었다. 기사 하나의 문장은 하루 전체를 대표하지 못해 제목이 과장이 된다.

해결:
  - daily_brief 발송 직후, 그날 발송된 항목(delivery_log)과 아카이브 요약을
    묶어 Gemini 1회 호출로 하루 종합 문장을 만든다.
  - daily_leads.json 으로 저장 → 웹 build_data.py 가 브리핑에 붙인다.
  - 하루 1회. trend_insights 와 같은 워크플로에서 돈다 (Gemini 호출 +1/일).

가드레일 (trend_insights 와 동일 원칙):
  - 근거에 없는 내용 금지. 예측·전망·권고·평가 금지.
  - 근거 hash 저장 — 웹에서 원문으로 검증 가능.
  - Gemini 키 없거나 실패 시: 기존 leads 를 보존한 채 파일만 다시 쓴다.

파일은 어떤 경로로 끝나든 반드시 존재해야 한다 — daily-brief.yml 이
daily_leads.json 을 git add 하는데, 파일이 없으면 pathspec 실패로 스텝이
죽고 trend_insights 커밋까지 동반 사망한다 (2026-08-02 실사고).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gemini_client import GeminiError, call_json, is_available

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
OUT_FILE = BASE / "daily_leads.json"
DELIVERY_LOG = BASE / "delivery_log.jsonl"
KST = timezone(timedelta(hours=9))

KEEP_DAYS = 90            # 보관 기간 — 웹이 과거 브리핑도 보여준다
MAX_ITEMS = 12            # 한 문장으로 묶을 최대 이슈 수
LEAD_LIMIT = 90           # 히어로 h1 한 줄 상한 (web/build_data.HEADLINE_LIMIT 여유분)

SYSTEM_PROMPT = """당신은 한국수력원자력 정책부서의 시니어 정책분석관입니다.
오늘 브리핑에 오른 원자력·에너지 이슈 목록을 받고, 그날 하루를 대표하는
한 문장을 씁니다.

[원칙 — 반드시 준수]
- 첨부된 이슈에 없는 내용 추가 금지 (환각 금지). 배경지식으로 살을 붙이지 말 것.
- 미래 예측·전망·권고·투자 판단 금지. "~할 전망", "~해야 한다", "~가 유망" 금지.
- 개별 기사 제목을 그대로 옮기지 말 것. 오늘 무엇이 움직였는지를 묶어서 말할 것.
- 국내와 해외가 함께 있으면 둘을 한 문장에 담을 것.
  예: "국내에서는 월성2호기 계속운전 논의가, 해외에서는 유럽 저수위로 인한 원전
  가동 차질이 함께 진행됐습니다."
- 이슈가 한 건뿐이면 그 사실만 한 문장으로 적을 것. 억지로 묶지 말 것.
- 한국어 자연문 한 문장, 90자 이내, 마침표로 끝낼 것.
- 묶을 만한 공통점이 없으면 lead 를 빈 문자열로 둘 것. 억지로 쓰지 말 것.

[출력 — JSON 한 객체만]
{"lead": "...", "evidence_idx": [0, 3]}
- evidence_idx: 문장의 근거가 된 이슈 번호 목록 (실제 참조한 것만)
"""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    except OSError:
        return []
    return rows


def _archive_summaries() -> dict[str, dict]:
    """hash → 아카이브 레코드. delivery_log 에는 요약이 없어 붙여준다."""
    summaries: dict[str, dict] = {}
    archive_dir = BASE / "archive"
    if not archive_dir.exists():
        return summaries
    for path in sorted(archive_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("hash"):
                summaries[record["hash"]] = record
    return summaries


def collect_today(delivery_rows: list[dict]) -> tuple[str, list[dict]]:
    """가장 최근 발송일과 그날 항목을 점수 내림차순으로 돌려준다."""
    # record_type 이 붙은 줄은 기사가 아니라 부가 레코드(selection_stats 등).
    # 걸러내지 않으면 제목·요약이 빈 항목이 프롬프트에 섞여 들어간다.
    delivery_rows = [row for row in delivery_rows if not row.get("record_type")]
    dates = [row.get("date") for row in delivery_rows if row.get("date")]
    if not dates:
        return "", []
    latest = max(dates)
    today = [row for row in delivery_rows if row.get("date") == latest]
    today.sort(key=lambda row: -(row.get("score") or 0))
    return latest, today[:MAX_ITEMS]


def build_user_message(items: list[dict], summaries: dict[str, dict]) -> str:
    lines = []
    for index, row in enumerate(items):
        record = summaries.get(row.get("hash") or "") or {}
        # delivery_log 의 scope 는 큐레이션 LLM 이 명시한 경우에만 채워진다(국내
        # 항목은 대개 빈 문자열). 확정 판정은 daily_brief.region 이 써 넣은 region.
        region = row.get("region") or ("국내" if row.get("scope") == "kr" else "해외")
        title = row.get("title_kr") or record.get("title_kr") or record.get("title") or ""
        summary = record.get("summary") or ""
        lines.append(f"[{index}] ({region}) {title} — {summary}")
    return "\n".join(lines)


CLAUSE_BOUNDARIES = ("며, ", "고, ", "지만 ", "으나 ", ", ")


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _finish(text: str) -> str:
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


def _clause_cut(text: str) -> str:
    """LEAD_LIMIT 초과 문장을 절 경계에서 자른다. 경계가 없으면 말줄임."""
    window = text[:LEAD_LIMIT]
    best = -1
    for sep in CLAUSE_BOUNDARIES:
        pos = window.rfind(sep)
        if pos > best:
            best = pos + len(sep.rstrip())
    if best > 20:  # 너무 앞에서 잘리면 문장이 앙상해진다
        return _finish(window[:best].rstrip().rstrip(","))
    return window[: LEAD_LIMIT - 1].rstrip() + "…"


def _load_leads() -> dict:
    """기존 leads 를 읽는다. 손상된 파일은 지우기 전에 옆에 남긴다.

    이 함수의 결과를 finally 에서 그대로 다시 쓰기 때문에, 손상 시 {} 를
    돌려주면 90일치 leads 가 빈 값으로 덮이고 워크플로가 곧바로 커밋한다.
    git 이력으로 되찾을 수는 있지만 아무 신호도 남지 않는다.
    """
    if not OUT_FILE.exists():
        return {}
    try:
        stored = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        backup = OUT_FILE.with_suffix(".json.corrupt")
        try:
            backup.write_bytes(OUT_FILE.read_bytes())
            print(f"[lead] 기존 파일 손상({type(exc).__name__}) — {backup.name} 로 보존")
        except OSError:
            print(f"[lead] 기존 파일 손상({type(exc).__name__}) — 백업도 실패")
        return {}
    leads = stored.get("leads") if isinstance(stored, dict) else None
    return leads if isinstance(leads, dict) else {}


def _save_leads(leads: dict) -> None:
    """원자적 기록. 실패 경로에서도 호출된다 — 파일은 항상 존재해야 한다."""
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"leads": leads}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tmp.replace(OUT_FILE)


def _call_lead(items: list[dict], summaries: dict[str, dict]) -> dict:
    """1차 호출 + 길이 초과 시 압축 재호출 1회 + 최후 절 경계 절단."""
    user_message = build_user_message(items, summaries)
    result = call_json(SYSTEM_PROMPT, user_message,
                       temperature=0.2, max_output_tokens=8192)
    lead = _normalize(result.get("lead"))
    if not lead or len(lead) <= LEAD_LIMIT:
        return {"lead": _finish(lead), "result": result, "truncated": False}

    # 재시도 사다리 2단: 이전 출력을 보여주고 더 짧게 압축시킨다 (+1 호출, 초과일에만)
    retry_message = (
        f"{user_message}\n\n"
        f"[재요청] 방금 작성한 문장이 {len(lead)}자로 상한을 넘었습니다:\n"
        f"{lead}\n"
        "같은 내용을 70자 이내 한 문장으로 다시 압축하세요. 근거 밖 내용 추가 금지."
    )
    try:
        retry = call_json(SYSTEM_PROMPT, retry_message,
                          temperature=0.2, max_output_tokens=8192)
        short = _normalize(retry.get("lead"))
        if short and len(short) <= LEAD_LIMIT:
            return {"lead": _finish(short), "result": retry, "truncated": False}
    except GeminiError:
        pass  # 재시도 실패는 절단 폴백으로

    # 사다리 3단: 절 경계 절단 — silent drop 금지 (예전엔 여기서 "" 를 돌려
    # 파일이 안 쓰였고, 그 부재가 워크플로 git add 를 죽였다)
    return {"lead": _clause_cut(lead), "result": result, "truncated": True}


def generate() -> bool:
    leads = _load_leads()
    try:
        if not is_available():
            print("[lead] GEMINI_API_KEY 없음 — 기존 leads 유지")
            return False
        rows = _load_jsonl(DELIVERY_LOG)
        date, items = collect_today(rows)
        if not items:
            print("[lead] 발송 기록 없음 — 기존 leads 유지")
            return False

        summaries = _archive_summaries()
        try:
            outcome = _call_lead(items, summaries)
        except GeminiError as exc:
            print(f"[lead] Gemini 실패 — 기존 leads 유지: {exc}")
            return False

        lead = outcome["lead"]
        if not lead:
            print(f"[lead] {date} 종합 문장 없음 (근거 부족) — 기존 leads 유지")
            return False

        result = outcome["result"]
        idxs = [i for i in (result.get("evidence_idx") or [])
                if isinstance(i, int) and 0 <= i < len(items)]
        evidence = [{
            "hash": items[i].get("hash", ""),
            "title_kr": items[i].get("title_kr", ""),
        } for i in idxs]

        entry = {
            "lead": lead,
            "evidence": evidence,
            "issue_count": len(items),
            "generated_at": datetime.now(KST).isoformat(),
        }
        if outcome["truncated"]:
            entry["truncated"] = True
        leads[date] = entry
        cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        leads = {day: value for day, value in leads.items() if day >= cutoff}
        print(f"[lead] {date} 종합 문장 저장 (근거 {len(evidence)}건"
              f"{', 절단' if outcome['truncated'] else ''}) → {OUT_FILE.name}")
        return True
    finally:
        _save_leads(leads)


if __name__ == "__main__":
    generate()
