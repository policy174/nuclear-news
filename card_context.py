# -*- coding: utf-8 -*-
"""카드가 읽는 재료 — **한 세대의** 사이트 데이터와 그 위의 스토리 신원.

make_cards.py 와 story_cards.py 가 같은 재료를 서로 다른 방법으로 찾아오면
둘이 다른 얘기를 하게 된다. 그래서 "무엇을 읽는가"와 "그것이 어느 스토리인가"는
여기 한 곳에서만 정한다. 카피·검증·렌더는 각자의 몫이다.

여기서 지키는 계약은 셋이다.

**① 한 세대**
  today.json · threads.json · issues.json · manifest.json 은 같은 빌드가 같은
  루프에서 쓴다(`web/build_data.py` 의 outputs 튜플). 그래서 네 파일은 같은
  `generation_id` 를 들고 있어야 하고, 다르면 카드는 배포 중간에 받은 것이다 —
  그때 스토리를 만들면 오늘 순위 위에 어제 사건이 앉는다.

**② 신원은 id 로 푼다**
  제목으로 잇지 않는다. 예전 `story_cards.pick_story` 는 이슈 제목과 thread
  이벤트 제목의 exact match 로 이었는데, 표시 제목은 **움직이는 값**이다 —
  `thread_web._thread_view` 가 카드 제목으로 최신 사건 제목을 걸고(실측
  2026-09-13: 스토리 101개 중 67개가 최초 제목과 달랐다), 이슈 쪽도
  `headline_display` 가 따로 있다. 움직이는 값을 열쇠로 쓰면 어제 되던 연결이
  오늘 안 된다.

**③ 근거는 source_event_id 로 묶는다**
  `event_id` 는 라우트(지금 열리는 주소)라 흡수된 사건이 전부 같은 값으로
  접힌다. 그 값으로 근거를 묶으면 8월 사건의 기사가 9월 사건의 근거가 된다.
  `thread-web-v2` 가 행마다 `source_event_id` 와 `evidence_hashes` 를 싣는
  이유이고, 이 모듈은 그 둘만 쓴다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "web" / "public" / "data"

# 이 신원 계층이 있는 페이로드만 스토리를 만든다. v1 에는 `source_event_id` 가
# 없어 근거를 라우트 id 로 묶어야 하는데, 그건 위 ③ 이 금지하는 일이다.
# 일일 카드는 threads 를 안 보므로 영향이 없다.
REQUIRED_THREAD_CONTRACT = "thread-web-v2"

# 진행을 말하는 관계. `same_matter` 는 **같은 사안이라는 말일 뿐 단계가 넘어갔다는
# 말이 아니다** — 이것만 있는 스레드는 "같은 얘기를 또 했다" 이지 스토리가 아니다.
PROGRESS_RELATIONS = frozenset({"stage_progress", "cause_effect"})


class ContextError(RuntimeError):
    """재료를 신뢰할 수 없다. 호출자가 도메인을 갈라 처리한다."""


@dataclass
class StoryCandidate:
    """상위 N 건 중 스토리가 되는 첫 이슈와 그 스레드."""
    issue: dict
    thread: dict
    rank: int                      # 1-based. 로그에만 쓴다
    events: list[dict]             # 표시 순서(시간순)로 접힌 flow 행
    warnings: list[str] = field(default_factory=list)

    @property
    def thread_id(self) -> str:
        return str(self.thread.get("thread_id") or "")


@dataclass
class SiteData:
    date: str
    issues: list[dict]
    threads: dict                  # thread_web 페이로드 통째
    generation_id: str
    source: str                    # "today" | "briefings"
    warnings: list[str] = field(default_factory=list)

    def thread_index(self) -> dict[str, dict]:
        """thread_id → thread. **고유성을 검증한 뒤** 만든다.

        `threads` 는 객체 map 이 아니라 배열이다. 배열을 그대로 훑으며 찾으면
        중복 id 가 있어도 조용히 첫 것을 쓰게 되는데, 그러면 어느 쪽을 쓰는지가
        파일 순서에 달린다.
        """
        out: dict[str, dict] = {}
        for thread in self.threads.get("threads") or ():
            thread_id = str(thread.get("thread_id") or "")
            if not thread_id:
                continue
            if thread_id in out:
                raise ContextError(f"threads.json 에 thread_id 중복: {thread_id}")
            out[thread_id] = thread
        return out


def _read(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContextError(f"{path.name} 을 읽지 못했다: {exc}") from exc


def load_site_data(date: str, data_dir: Path | None = None) -> SiteData:
    """그날 카드의 재료. 정상 당일은 today.json, 과거 재생성은 briefings.json.

    **왜 today.json 인가**: `thread_id` 가 거기에만 있다. briefings.json 의 이슈
    행은 그날 발간 시점의 사본이라 `stamp_thread_ids` 가 찍는 칸이 없다(라이브
    실측 2026-09-20: briefings 이슈 61일치 전부에 `thread_id` 키 자체가 없다).
    순위는 두 파일이 같다 — today.json 이 그날 briefing 의 이슈를 그대로 싣는다.
    """
    data_dir = data_dir or DATA_DIR
    manifest = _read(data_dir / "manifest.json") if (data_dir / "manifest.json").exists() else {}
    generation_id = str((manifest or {}).get("generation_id") or "")

    threads_path = data_dir / "threads.json"
    threads = _read(threads_path) if threads_path.exists() else {}
    if not isinstance(threads, dict):
        raise ContextError("threads.json 이 객체가 아니다")

    today_path = data_dir / "today.json"
    warnings: list[str] = []
    if today_path.exists():
        today = _read(today_path)
        if isinstance(today, dict) and str(today.get("date") or "") == date:
            return SiteData(date=date, issues=list(today.get("issues") or []),
                            threads=threads, generation_id=generation_id,
                            source="today", warnings=warnings)

    briefings_path = data_dir / "briefings.json"
    if not briefings_path.exists():
        raise ContextError(f"{date} 재료 없음 — today.json 도 briefings.json 도 없다")
    for briefing in _read(briefings_path) or ():
        if str(briefing.get("date") or "") == date:
            # 과거 재생성 경로. 이슈에 `thread_id` 가 없으므로 아래
            # `resolve_thread` 가 **정확한 issue_id ↔ source_event_id 일치**로
            # 떨어진다. 제목으로 잇지 않는다.
            warnings.append(
                "과거 날짜 재생성 — briefings.json 을 쓴다(thread_id 칸 없음, "
                "source_event_id 일치로 연결)")
            return SiteData(date=date, issues=list(briefing.get("issues") or []),
                            threads=threads, generation_id=generation_id,
                            source="briefings", warnings=warnings)
    raise ContextError(f"{date} 브리핑이 today.json 에도 briefings.json 에도 없다")


def check_generation(data_dir: Path | None = None) -> tuple[bool, str]:
    """네 파일이 같은 배포 세대인가.

    manifest 만 세대를 들고 있고 나머지는 안 들고 있으므로, 여기서 재는 것은
    **파일이 다 있고 manifest 가 세대를 말하는가** 까지다. 세대 자체가 갈리는
    경우는 다운로드 전후로 manifest 를 두 번 받아 비교하는 쪽(cards.yml)이
    잡는다 — 그쪽이 경주의 당사자다.
    """
    data_dir = data_dir or DATA_DIR
    missing = [name for name in ("manifest.json", "today.json", "threads.json")
               if not (data_dir / name).exists()]
    if missing:
        return False, f"재료 누락: {', '.join(missing)}"
    generation_id = str((_read(data_dir / "manifest.json") or {}).get("generation_id") or "")
    if not generation_id:
        return False, "manifest.json 에 generation_id 가 없다"
    return True, generation_id


# ── 스토리 신원 ────────────────────────────────────────────────────────────

def story_blocked(threads: dict) -> str:
    """스토리를 만들면 안 되는 이유. 없으면 빈 문자열.

    **`degraded` 는 여기 없다.** 그것은 "이번 판정 회차가 온전하지 않았다"는
    관측값이지 "이 스레드의 관계가 틀렸다"는 말이 아니다 — 원장의 관계 라벨과
    근거는 그대로다. 라이브 실측 2026-09-20 이 `visible=true · degraded=true`
    (build.status=no_api_key)였는데, 이것을 hard gate 로 쓰면 그날 스토리가 0 이
    된다. 게다가 그 상태는 손으로 커밋한 로컬 빌드가 CI 원장을 덮었을 때도
    나므로 고장과 구분되지 않는다. 경고로 싣고 사람이 보게 한다.
    """
    if not threads:
        return "threads.json 없음"
    if not threads.get("visible"):
        return f"threads 숨김 상태({threads.get('status') or 'hidden'})"
    if str(threads.get("version") or "") != REQUIRED_THREAD_CONTRACT:
        return (f"threads 계약이 {threads.get('version') or '없음'} — "
                f"{REQUIRED_THREAD_CONTRACT} 가 필요하다(근거를 source_event_id 로 묶는다)")
    if not (threads.get("threads") or ()):
        return "threads 비어 있음"
    return ""


def thread_warnings(threads: dict) -> list[str]:
    """막지는 않지만 로그에 남겨야 하는 것."""
    out: list[str] = []
    if threads.get("degraded"):
        build = threads.get("build") or {}
        out.append(f"threads degraded — build.status={build.get('status') or '?'} "
                   f"(asked={build.get('asked')}, failed={build.get('failed')})")
    return out


def _display_events(thread: dict) -> list[dict]:
    """표시 기준 사건 목록 = `flow`(시간순, 같은 제목 사본이 접힌 것).

    `events` 가 아니라 `flow` 를 쓴다. `events` 는 최신순이고 사본이 안 접혀
    있어서 "같은 줄이 두 번" 이 그대로 세어진다 — 자격을 사건 수로 판정하는
    자리에서 그건 숫자를 부풀린다.
    """
    return [row for row in (thread.get("flow") or ()) if str(row.get("date") or "")]


def resolve_thread(issue: dict, index: dict[str, dict]) -> dict | None:
    """이 이슈의 스토리. **id 로만 푼다.**

    순서:
      ① `issue.thread_id` — 정상 경로. build_data 가 source_event_id 로 찍는다.
      ② `issue_id` 가 어느 스레드의 `source_event_id` 와 정확히 같은가 —
         `thread_id` 칸이 없는 과거 briefings 행을 위한 제한적 폴백이다.

    제목·`headline_display`·canonical title·fuzzy 는 어느 단계에서도 쓰지 않는다.
    """
    thread_id = str(issue.get("thread_id") or "").strip()
    if thread_id:
        return index.get(thread_id)
    issue_id = str(issue.get("issue_id") or "").strip()
    if not issue_id:
        return None
    for thread in index.values():
        for row in (thread.get("flow") or ()):
            if issue_id in {str(row.get("source_event_id") or ""),
                            *(str(value) for value in row.get("source_event_ids") or ())}:
                return thread
    return None


def eligibility(thread: dict) -> tuple[bool, str]:
    """이 스레드가 스토리 카드가 될 수 있는가.

    **사건 수를 절대 기준으로 쓰지 않는다.** 예전 `MIN_EVENTS = 3` 은 "타임라인이
    안 선다"는 렌더 사정에서 나온 숫자였는데, 스토리 카드가 보여 줘야 하는 것은
    행의 개수가 아니라 **단계가 넘어갔다는 사실**이다. 발표 → 시행, 신청 → 심사,
    사고 → 조사결과는 두 칸으로도 이야기가 되고, 같은 사안을 다섯 번 되풀이한
    다섯 칸은 다섯 칸이어도 이야기가 아니다.

    라이브 실측 2026-09-20(스레드 85개): 옛 기준은 31개(36%)를 통과시켰고 이
    기준은 44개(52%)를 통과시킨다. 늘어난 25개는 전부 2-event 인데, 그중
    통과하는 것은 인접 관계가 `stage_progress`/`cause_effect` 인 것뿐이다.
    """
    events = _display_events(thread)
    if len(events) < 2:
        return False, f"표시 사건 {len(events)}건 — 흐름이 안 선다"

    # 각 행은 **자기 근거**를 들고 있어야 한다. 근거 없는 행을 타임라인에 세우면
    # 그 줄은 출처 없이 날짜와 제목만 주장하는 문장이 된다.
    bare = [row for row in events if not (row.get("evidence_hashes") or ())]
    if bare:
        return False, f"근거 없는 사건 {len(bare)}건 — {str(bare[0].get('title') or '')[:24]}"

    # 인접 관계는 마지막 행을 뺀 나머지가 들고 있다(`relation_to_next`).
    relations = [str(row.get("relation_to_next") or "") for row in events[:-1]]
    progress = [value for value in relations if value in PROGRESS_RELATIONS]
    if not progress:
        named = sorted({value for value in relations if value}) or ["빈칸"]
        return False, f"진행 관계 없음 — 인접 관계가 {'/'.join(named)} 뿐"
    return True, f"사건 {len(events)}건 · 진행 관계 {'/'.join(sorted(set(progress)))}"


def pick_story_candidate(data: SiteData, top: list[dict]) -> StoryCandidate | None:
    """일일 카드 대상 **그 목록 안에서** 순서대로 첫 스토리를 고른다.

    순위를 다시 매기지 않고 LLM 도 부르지 않는다. 일일 카드와 스토리 카드가
    서로 다른 중요도 판단을 만들면 같은 날 두 산출물이 다른 1위를 말한다.
    """
    blocked = story_blocked(data.threads)
    if blocked:
        raise ContextError(blocked)
    index = data.thread_index()
    warnings = thread_warnings(data.threads)
    for rank, issue in enumerate(top, 1):
        thread = resolve_thread(issue, index)
        if thread is None:
            continue
        ok, reason = eligibility(thread)
        if not ok:
            warnings.append(f"#{rank} {str(issue.get('title') or '')[:20]} → "
                            f"{thread.get('thread_id')}: {reason}")
            continue
        return StoryCandidate(issue=issue, thread=thread, rank=rank,
                              events=_display_events(thread), warnings=warnings)
    return None


# ── Evidence Packet ────────────────────────────────────────────────────────
#
# 프롬프트에 원문을 무한히 밀어 넣지 않는다. 아래는 **재료의 상한**이다.
MAX_EVENTS = 8          # 타임라인 후보로 넘기는 사건 상한
MAX_EVIDENCE = 3        # 사건당 근거 해시 상한
DETAIL_MAX = 800        # 사건 본문 요지 상한(글자)


def load_issue_index(data_dir: Path | None = None) -> dict[str, dict]:
    """issue_id → 그 이슈의 구조화 결과. Evidence Packet 의 본문 재료다.

    **왜 issues.json 인가**: 사건 하나하나의 `summary`·`detail`·`why_important`·
    `implication`·`open_question` 이 거기에 있고, 열쇠가 `issue_id` 라
    `source_event_id` 로 바로 찾을 수 있다. 예전에는 지난 브리핑 전체를 훑어
    **제목을 열쇠로** 같은 것을 모았는데, 제목은 움직이는 값이라 그 색인은
    조용히 빗나갔다(card_context 모듈 주석 ②).

    라이브 실측 2026-09-20: 스레드 사건 229건 중 207건(90%)이 여기서 풀린다.
    안 풀리는 것은 흡수되어 현재 카탈로그에 없는 옛 사건이다 — 그 행은 근거
    해시와 제목·날짜만으로 서고, 본문이 비면 자격 검사가 아니라 카피가 얇아지는
    것으로 끝난다.
    """
    path = (data_dir or DATA_DIR) / "issues.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(row.get("issue_id") or ""): row for row in rows if row.get("issue_id")}


def _clip(text: object, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def evidence_packet(candidate: StoryCandidate, date: str, *, topic: str = "") -> dict:
    """Evidence Packet — 사건마다 **자기 근거만** 들고 선다.

    한 행의 재료는 그 행의 `source_event_id` 하나에서만 온다. 다른 사건의
    기사·문장을 끌어와 채우지 않는다 — 그러면 타임라인 한 줄이 다른 날의 근거로
    선다(`card_context` 모듈 주석 ③). 스토리 전체의 '왜 중요한가'만 여러 사건을
    함께 인용할 수 있고, 그 자리는 아래 `narrative`·`watchpoints` 다.
    """
    thread = candidate.thread
    index = load_issue_index()
    events, narrative, watchpoints, seen_w = [], [], [], set()
    for row in candidate.events[-MAX_EVENTS:]:
        source_id = str(row.get("source_event_id") or "")
        detail = index.get(source_id) or {}
        hashes = [str(value) for value in (row.get("evidence_hashes") or ())][:MAX_EVIDENCE]
        events.append({
            "date": row.get("date"),
            # 이 파일의 날짜가 무슨 날짜인지 카피가 추측하지 않게 한다.
            "date_kind": row.get("date_kind") or "first_seen",
            "title": row.get("title"),
            "source_event_id": source_id,
            "relation_to_next": row.get("relation_to_next") or "",
            "evidence_hashes": hashes,
            "summary": _clip(detail.get("summary"), 200),
            "detail": _clip(detail.get("detail"), DETAIL_MAX),
        })
        line = _clip(detail.get("implication") or detail.get("summary"), 160)
        if line and line not in narrative:
            narrative.append(line)
        for question in (detail.get("open_question"), detail.get("why_important")):
            question = _clip(question, 160)
            if question and question not in seen_w:
                seen_w.add(question)
                watchpoints.append(question)

    issue = candidate.issue
    tail = _clip(issue.get("open_question"), 160)
    if tail and tail not in seen_w:
        watchpoints.append(tail)
    rep = issue.get("representative_article") or {}
    return {
        "date": date,
        "thread_id": candidate.thread_id,
        "issue_id": str(issue.get("issue_id") or ""),
        "issue_title": issue.get("title") or thread.get("title"),
        "topic": topic,
        "events": events,
        "narrative": narrative[-5:],
        "phase_now": narrative[-1] if narrative else (issue.get("summary") or ""),
        "watchpoints": watchpoints[:6],
        "summary": rep.get("summary") or issue.get("summary") or "",
        "why_important": issue.get("why_important") or "",
        # 표지 통계 — 지어내지 않고 원장이 센 값을 그대로 쓴다.
        "briefing_count": thread.get("briefing_count") or 0,
        "lifespan_days": thread.get("lifespan_days") or 0,
    }
