"""보고 후보 이슈의 개조식 보고서 초안 생성 — Phase R 생성층.

배경 (2026-09-07 사용자 판정):
    "보고서용 복사는 결국 그냥 기사 정리 정도지 뭐 아무것도 없음."
    실측 확인 — (변화)가 (사실)과 중복, (왜 중요) 결측, (시사점)이 사실
    재진술("~논의가 본격화되었다")로 한수원 함의 0, 서술문 그대로.
    카드 필드를 나열하는 것은 보고서가 아니다. 보고서의 가치는
    골격(개요→주요 내용→시사점)과 개조식 문체, 한수원 관점 함의에 있다.

설계:
    - 대상은 report_pick 이슈만 (하루 0~2건 게이트를 이미 통과한 사안).
      전 이슈에 돌리면 무료 쿼터가 뉴스봇 크론을 굶긴다.
    - build_data 는 결정적(LLM 0회)으로 남긴다. 이 모듈은 빌드 **뒤에** 돌며
      직전 빌드의 issues.json 을 읽어 별도 파일을 낸다. 프런트가 조인한다.
    - 문체는 khnp-report 스킬의 규칙(개조식 체언 종결·□○ 위계·시사점 라벨)을
      프롬프트로 지시하고 같은 regex 게이트로 검증한다. 게이트 실패 1회는
      위반 목록을 되먹여 재시도, 그래도 실패면 초안 없음(비치명 — 프런트가
      기존 템플릿으로 물러난다).
    - 사내 머리말 표·부서 표기는 넣지 않는다. 공개 사이트에 나가는 것은
      개조식 본문뿐이다(스킬 원칙: 사내 서식 완성본은 공개 웹에 올리지 않는다).

가드레일:
    - 타임라인에 나온 사실만. 없는 수치·일정·기관을 지어내지 않는다.
    - 시사점은 요약의 반복 금지 — 한수원·한국 원전 정책에의 함의만.
    - stdlib + gemini_client + llm_cache 만 사용. build_data 를 import 하지
      않는다(순환 방지 — issue_insight 와 같은 계약).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import llm_cache

ROOT = Path(__file__).parent
ISSUES_FILE = ROOT / "web" / "public" / "data" / "issues.json"
CACHE_FILE = ROOT / "report_drafts.json"
PUBLIC_FILE = ROOT / "web" / "public" / "data" / "report_drafts.json"

# 프롬프트를 고치면 올린다 — 옛 초안이 자동 무효.
PROMPT_VERSION = 1

# 한 회차 신규 생성 상한. 첫 실행 시 밀린 후보를 한꺼번에 물으면(실측 26건)
# 무료 쿼터가 뉴스봇 운영 크론을 굶긴다 — 하루 몇 건씩 따라잡는다.
MAX_PER_RUN = 3
# 이보다 오래 조용한 이슈는 보고 시점이 지났다 — 초안을 만들지 않는다.
RECENT_DAYS = 21
MAX_TIMELINE = 12
MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """당신은 한국수력원자력 원자력정책실의 시니어 분석관입니다.
아래 이슈의 사건 타임라인을 재료로 **사내 동향보고 초안**을 개조식으로 작성하세요.

골격 (이 순서, 이 번호 그대로):
1. 개요
 □ (배경) 사안의 출발점 1줄
 □ (경과) 최근 전개 1~2줄
2. 주요 내용
 □ 핵심 사실 2~4개 (필요시 하위에 ○)
3. 시사점
 □ (주제라벨) 한수원·한국 원전 정책에의 함의 1~3개

문체 규칙 (전부 필수):
- 모든 불릿은 명사·명사형으로 종결 — "-했다"·"-이다"·"-습니다" 금지.
  좋은 종결어: 필요, 가능, 검토, 확보, 추진, 전망, 예상, 논의, 지연, 확대, 발의, 개시
- 위계 기호는 □ (1단) → ○ (2단) 순서. 건너뛰기 금지.
- 연도는 '26년 꼴. 국가는 한자 약어 가능(美·中·佛·日). 수치는 반드시 단위 동반.
- 수치·일정·기관명은 타임라인 원문에 있는 것만. 지어내지 않는다.
- 시사점 각 □는 (신규건설)(SMR)(계속운전)(공급망)(규제·인허가)(전력시장) 같은
  주제 라벨로 시작. **요약의 반복 금지** — "논의가 본격화" 같은 재진술이 아니라
  한수원이 검토·대비할 지점을 쓴다. 근거가 부족하면 "확인 필요"로 남긴다.
- 금지: 줄표(—), 볼드(**), 이탤릭, 이모지, "귀추가 주목", "주목할 만" 류 클리셰.

출력은 정확히 아래 JSON 하나. 다른 텍스트·펜스 금지.
{"lines": ["1. 개요", " □ (배경) ...", " □ (경과) ...", "2. 주요 내용", " □ ...", "3. 시사점", " □ (라벨) ..."]}"""

# ---- 문체 게이트 -------------------------------------------------------------
# khnp-report 스킬 references/check_style.py 의 핵심 검사를 이식한 것.
# 초안에는 붙임·목적 절이 없으므로 그 예외 처리는 뺐다.

_BULLETS = {"□": 1, "○": 2, "–": 3}
_SECTION_HEAD = re.compile(r"^\s*\d+\.\s*")
_CONCLUSION_HEAD = re.compile(r"^\s*\d+\.\s*시사점")
_NARRATIVE_END = re.compile(r"다\s*[.。]?\s*$")
_LABEL = re.compile(r"^\s*[（(\[［]")
_BANNED = re.compile(r"—|\*\*|귀추가 주목|주목할 만|[\U0001F300-\U0001FAFF]")


def gate(lines: list[str]) -> list[str]:
    """위반 목록. 비어 있으면 통과."""
    problems: list[str] = []
    seen_level = 0
    in_conclusion = False
    first_box_pending = False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _BANNED.search(line):
            problems.append(f"{number}행: 금지 표기(줄표·볼드·클리셰·이모지)")
        if _SECTION_HEAD.match(stripped):
            in_conclusion = bool(_CONCLUSION_HEAD.match(stripped))
            first_box_pending = in_conclusion
            seen_level = 0
            continue
        marker = stripped[0]
        if marker not in _BULLETS:
            problems.append(f"{number}행: □○– 위계 기호가 아님")
            continue
        level, body = _BULLETS[marker], stripped[1:].strip()
        if level > seen_level + 1:
            problems.append(f"{number}행: 위계 건너뛰기({marker})")
        seen_level = level
        if _NARRATIVE_END.search(body):
            problems.append(f"{number}행: 서술형 종결 — 개조식 체언 종결로")
        if in_conclusion and level == 1:
            if first_box_pending:
                first_box_pending = False  # 첫 □는 라벨 없는 재진술 허용(검토보고 관습)
            elif not _LABEL.match(body):
                problems.append(f"{number}행: 시사점 □에 (주제) 라벨 없음")
    if not any(_CONCLUSION_HEAD.match(line.strip()) for line in lines):
        problems.append("시사점 절 없음")
    return problems


# ---- 재료 --------------------------------------------------------------------

def _digest(issue: dict) -> str:
    hashes = sorted(
        str(article.get("hash") or "")
        for article in issue.get("related_articles") or []
    )
    seed = "|".join([str(issue.get("issue_id") or ""), *hashes])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _timeline_text(issue: dict) -> str:
    rows = sorted(
        issue.get("related_articles") or [],
        key=lambda a: str(a.get("article_date") or ""),
    )[-MAX_TIMELINE:]
    lines = []
    for article in rows:
        date = str(article.get("article_date") or "")[:10]
        title = str(article.get("title_kr") or "").replace("\n", " ")
        summary = str(article.get("summary") or "").replace("\n", " ")[:200]
        lines.append(f"- {date} {title} :: {summary}")
    return "\n".join(lines)


def _user_message(issue: dict) -> str:
    parts = [
        f"이슈 제목: {issue.get('title', '')}",
        f"보고 추천 사유: {issue.get('report_pick_why', '')}",
    ]
    angles = issue.get("report_pick_angles") or []
    if angles:
        parts.append("추천 각도: " + " / ".join(str(a) for a in angles[:3]))
    parts.append("사건 타임라인 (오래된 순):")
    parts.append(_timeline_text(issue))
    return "\n".join(parts)


def _targets(issues: list[dict], today: datetime) -> list[dict]:
    floor = (today - timedelta(days=RECENT_DAYS)).date().isoformat()
    rows = [
        issue for issue in issues
        if issue.get("report_pick")
        and str(issue.get("last_seen") or "") >= floor
        and (issue.get("related_articles") or [])
    ]
    rows.sort(key=lambda issue: str(issue.get("last_seen") or ""), reverse=True)
    return rows


# ---- 생성 --------------------------------------------------------------------

def _ask(client, issue: dict) -> tuple[str, list[str]]:
    """(초안 텍스트, 위반 목록). 게이트 실패 1회는 위반을 되먹여 재시도."""
    message = _user_message(issue)
    feedback = ""
    problems: list[str] = []
    for _ in range(2):
        payload = client.call_json(
            SYSTEM_PROMPT,
            message + feedback,
            temperature=0.2,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            fallback_model=getattr(client, "FALLBACK_MODEL", None),
            label="report_draft",
        )
        lines = [str(line) for line in (payload.get("lines") or []) if str(line).strip()]
        if not lines:
            return "", ["빈 응답"]
        problems = gate(lines)
        if not problems:
            return "\n".join(lines), []
        feedback = ("\n\n직전 출력의 문체 위반 — 고쳐서 다시 작성:\n"
                    + "\n".join(problems[:8]))
    return "", problems


def run(*, client=None, now: datetime | None = None, publish_only: bool = False) -> dict:
    """publish_only=True 면 LLM 호출 없이 캐시에서 공개 파일만 다시 쓴다 —
    매시간 crawl 배포가 초안 파일을 빠뜨리지 않게 하는 경로(쿼터 소비 0)."""
    now = now or datetime.now(timezone.utc)
    stats = {"targets": 0, "cached": 0, "generated": 0, "failed": 0, "status": "ok"}

    try:
        issues = json.loads(ISSUES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stats["status"] = "no_issues_file"
        return stats
    if not isinstance(issues, list):
        stats["status"] = "bad_issues_file"
        return stats

    targets = _targets(issues, now)
    stats["targets"] = len(targets)
    cache = llm_cache.load(CACHE_FILE, "drafts")

    if client is None and not publish_only:
        try:
            import gemini_client as client  # noqa: PLC0415
        except ImportError:
            client = None
    can_call = (not publish_only) and client is not None and client.is_available()

    calls = 0
    for issue in targets:
        issue_id = str(issue.get("issue_id") or "")
        digest = _digest(issue)
        entry = cache.get(issue_id)
        if (llm_cache.is_current(entry, PROMPT_VERSION)
                and entry.get("digest") == digest):
            stats["cached"] += 1
            continue
        if not can_call or calls >= MAX_PER_RUN:
            continue
        calls += 1
        try:
            draft, problems = _ask(client, issue)
        except Exception as exc:  # noqa: BLE001 — 초안 부재는 비치명
            stats["failed"] += 1
            stats["status"] = f"error: {type(exc).__name__}"
            continue
        # 실패도 캐시한다 — 같은 재료(digest)로 매 회차 재질의하지 않기 위해.
        # 재료가 바뀌거나(digest) 프롬프트가 바뀌면(PROMPT_VERSION) 다시 묻는다.
        cache[issue_id] = {
            "prompt_version": PROMPT_VERSION,
            "digest": digest,
            "draft": draft,
            "title": issue.get("title", ""),
            "problems": problems,
            "generated_at": now.isoformat(),
        }
        if draft:
            stats["generated"] += 1
        else:
            stats["failed"] += 1

    if not publish_only:
        llm_cache.save(cache, CACHE_FILE, key="drafts", prompt_version=PROMPT_VERSION,
                       comment="보고 후보 이슈의 개조식 초안 캐시 — report_draft.py")

    # 공개 파일에는 성공한 초안만, 현재 카탈로그에 있는 이슈만 싣는다.
    live_ids = {str(issue.get("issue_id") or "") for issue in issues}
    drafts = {
        issue_id: {
            "draft": entry["draft"],
            "generated_at": entry.get("generated_at", ""),
        }
        for issue_id, entry in cache.items()
        if entry.get("draft") and issue_id in live_ids
        and llm_cache.is_current(entry, PROMPT_VERSION)
    }
    try:
        PUBLIC_FILE.write_text(
            json.dumps({"generated_at": now.isoformat(), "drafts": drafts},
                       ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
    except OSError:
        stats["status"] = "write_failed"
    stats["published"] = len(drafts)
    return stats


if __name__ == "__main__":
    result = run(publish_only="--publish-only" in sys.argv)
    print(f"[report_draft] {result}")
    # 쿼터·재료 부재는 비치명(초안 없이 배포) — exit 0. 예상 밖 상태만 실패로.
    sys.exit(1 if result["status"] in ("bad_issues_file", "write_failed") else 0)
