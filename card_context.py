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

import khnp_relevance
from card_editorial import TIMELINE_ROWS

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


# ── 스토리 카드 이력 ──────────────────────────────────────────────────────────
#
# 같은 스토리를 새 전개 없이 또 내지 않는다. 2026-09-18~24 스토리 카드 7장 중
# 세 쌍이 재방송이었다 — 대미 투자(9/18·9/21), 원전 공론화(9/20·9/23), 한·미·일
# SMR(9/22·9/24). 세 경우 모두 그 사이 스토리에 새 사건이 붙지 않았다. 큰 이슈는
# 며칠씩 상위권에 머물고 스토리 후보는 순위 순으로 고르므로, 거르지 않으면 같은
# 스토리가 계속 1순위가 된다.
#
# **무엇이 바뀌었나는 문장이 아니라 사건으로 잰다.** 9/24 의 SMR 이슈는 기사
# 3건이 더 붙어 제목이 '합의' → '이행 계획 발표' 로 바뀌었고 카드 문구도 달랐지만,
# 스토리로 보면 같은 사건이었다. 카드 문구는 LLM 이 매번 새로 쓰므로 문장 비교는
# 늘 '바뀌었다' 가 된다. 스토리 카드가 전하는 것은 "다음 단계로 갔다" 이므로,
# 지난 카드 이후 **새 사건이 합류했고 그 사건이 진행 관계로 이어졌을 때만** 다시 낸다.
#
# 이력은 게시(publish_cards --kind story)가 적는다 — 실제로 사이트에 나간 카드만
# 세야 하기 때문이다. 파일은 카드 PNG 와 같은 폴더라 같은 커밋에 실린다.
STORY_HISTORY_FILE = ROOT / "web" / "public" / "cards" / "story_history.json"
STORY_HISTORY_KEEP_DAYS = 90


def event_ids(thread: dict) -> list[str]:
    """표시 사건의 원장 id 전부. 접힌 사본(`source_event_ids`)까지 센다 —
    접힌 사본이 나중에 따로 서도 '새 사건' 으로 세지 않게."""
    out: list[str] = []
    for row in _display_events(thread):
        for value in (row.get("source_event_id"), *(row.get("source_event_ids") or ())):
            text = str(value or "")
            if text and text not in out:
                out.append(text)
    return out


def load_story_history(path: Path | None = None) -> list[dict]:
    target = path or STORY_HISTORY_FILE
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = raw.get("cards") if isinstance(raw, dict) else None
    return [row for row in rows or () if isinstance(row, dict) and row.get("thread_id")]


def record_story_card(history: list[dict], *, date: str, thread_id: str,
                      issue_id: str = "", ids: list[str] | None = None,
                      keep_days: int = STORY_HISTORY_KEEP_DAYS) -> list[dict]:
    """그날의 스토리 카드를 이력에 적는다. 같은 날 줄은 **바꿔 쓴다** — 그날 다시
    구우면 마지막 것이 사이트에 남은 카드이기 때문이다."""
    from datetime import date as _date, timedelta
    try:
        cutoff = (_date.fromisoformat(date) - timedelta(days=keep_days)).isoformat()
    except ValueError:
        cutoff = ""
    rows = [row for row in history
            if str(row.get("date") or "") != date and str(row.get("date") or "") >= cutoff]
    rows.append({"date": date, "thread_id": thread_id, "issue_id": issue_id,
                 "event_ids": list(ids or [])})
    return sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row.get("thread_id"))))


def save_story_history(history: list[dict], path: Path | None = None) -> None:
    target = path or STORY_HISTORY_FILE
    target.write_text(json.dumps({
        "_comment": "스토리 카드 발행 이력. publish_cards.py --kind story 가 적고 "
                    "card_context.repeat_verdict 가 읽는다. 손으로 고치지 말 것.",
        "cards": history}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def repeat_verdict(thread: dict, history: list[dict], date: str) -> tuple[bool, str]:
    """이 스토리를 오늘 다시 내도 되는가. (가능 여부, 이유).

    오늘 날짜 줄은 보지 않는다 — 같은 날 다시 굽는 것은 재방송이 아니라 교체다.
    """
    thread_id = str(thread.get("thread_id") or "")
    past = [row for row in history
            if row.get("thread_id") == thread_id and str(row.get("date") or "") < date]
    if not past:
        return True, "처음 내는 스토리"
    last = max(past, key=lambda row: str(row.get("date") or ""))
    seen = set(last.get("event_ids") or ())
    events = _display_events(thread)
    fresh = [i for i, row in enumerate(events)
             if not ({str(row.get("source_event_id") or ""),
                      *(str(v) for v in row.get("source_event_ids") or ())} & seen)]
    if not fresh:
        return False, f"{last.get('date')} 에 냈고 그 뒤 새 사건 없음"
    # 새 사건이 앞뒤 어느 쪽으로든 진행 관계로 이어져야 '다음 단계' 다.
    for i in fresh:
        before = str(events[i - 1].get("relation_to_next") or "") if i > 0 else ""
        after = str(events[i].get("relation_to_next") or "") if i < len(events) - 1 else ""
        if before in PROGRESS_RELATIONS or after in PROGRESS_RELATIONS:
            return True, f"{last.get('date')} 이후 새 사건 {len(fresh)}건 · 진행 관계"
    return False, (f"{last.get('date')} 이후 새 사건 {len(fresh)}건이 모두 "
                   "같은 사안 되풀이 — 단계가 넘어가지 않았다")


def since_last(thread: dict, history: list[dict], date: str) -> dict | None:
    """이 스토리를 전에 카드로 낸 적이 있으면 그날과 그 뒤 새로 붙은 사건.

    재방송 판정(`repeat_verdict`)을 통과해 다시 나가는 스토리는 **후속**이다.
    그런데 카드는 매번 처음 보는 사람에게 하듯 처음부터 다시 풀었다 — 지난번
    카드를 본 사람에게는 같은 이야기의 반복이다. 무엇이 새로 붙었는지를 카피와
    표지에 알려 준다. 처음 나가는 스토리는 None.
    """
    thread_id = str(thread.get("thread_id") or "")
    past = [row for row in history
            if row.get("thread_id") == thread_id and str(row.get("date") or "") < date]
    if not past:
        return None
    last = max(past, key=lambda row: str(row.get("date") or ""))
    seen = set(last.get("event_ids") or ())
    fresh = [row for row in _display_events(thread)
             if not ({str(row.get("source_event_id") or ""),
                      *(str(v) for v in row.get("source_event_ids") or ())} & seen)]
    return {"date": str(last.get("date") or ""),
            "new_event_ids": [str(row.get("source_event_id") or "") for row in fresh],
            "new_titles": [str(row.get("title") or "") for row in fresh]}


def pick_story_candidate(data: SiteData, top: list[dict],
                         reasons: list[str] | None = None, *,
                         history: list[dict] | None = None) -> StoryCandidate | None:
    """후보 목록 **순서대로** 첫 스토리를 고른다.

    순위를 다시 매기지 않고 LLM 도 부르지 않는다. 일일 카드와 스토리 카드가
    서로 다른 중요도 판단을 만들면 같은 날 두 산출물이 다른 1위를 말한다.

    `top` 은 사이트 순위 그대로다. 호출부가 일일 카드 3건을 앞에 두고 그 뒤에
    나머지 오늘 이슈를 사이트 순서로 붙여 넘긴다(2026-09-24). 3건 안에 낼 만한
    스토리가 없으면 4위부터 내려간다 — 새 중요도 판단을 만드는 것이 아니라
    **같은 순위표를 더 읽는 것**이다.

    `history` 를 넘기면 새 전개 없는 재방송을 건너뛴다(`repeat_verdict`).

    `reasons` 를 넘기면 **후보가 없을 때도** 순위별로 왜 빠졌는지가 남는다.
    2026-09-21 실측: 상위 3건이 전부 스레드에 안 이어져 None 이 돌아왔는데
    로그에는 "오늘 스토리 카피가 없다" 한 줄뿐이라, 원인(1위가 그날 새 id 로
    조폐되어 원장이 모름)을 되짚는 데 빌드 재현 두 번이 들었다.
    """
    blocked = story_blocked(data.threads)
    if blocked:
        raise ContextError(blocked)
    index = data.thread_index()
    warnings = thread_warnings(data.threads)
    if reasons is not None:
        reasons.extend(warnings)
    for rank, issue in enumerate(top, 1):
        title = str(issue.get("title") or "")[:20]
        thread = resolve_thread(issue, index)
        if thread is None:
            if reasons is not None:
                slot = str(issue.get("thread_id") or "").strip()
                reasons.append(
                    f"#{rank} {title} → 스레드 없음 (issue_id={issue.get('issue_id') or '?'}, "
                    f"thread_id={slot or '빈칸'}"
                    f"{' — 원장에 없는 스레드' if slot else ''})")
            continue
        ok, reason = eligibility(thread)
        if not ok:
            line = f"#{rank} {title} → {thread.get('thread_id')}: {reason}"
            warnings.append(line)
            if reasons is not None:
                reasons.append(line)
            continue
        if history is not None:
            fresh, why = repeat_verdict(thread, history, data.date)
            if not fresh:
                # 재방송은 경고가 아니다 — 거르는 것이 정상 동작이다.
                if reasons is not None:
                    reasons.append(f"#{rank} {title} → {thread.get('thread_id')}: 재방송 — {why}")
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


# ── 타임라인에 세울 사건 고르기 ─────────────────────────────────────────────
#
# 스토리의 사건은 7~8건인데 타임라인 칸은 4개다. 예전에는 8건을 통째로 넘기고
# "최대 4행"만 적어 **고르는 일을 모델에게 맡겼다.** 기준이 없으니 그날그날
# 달랐고, 2026-09-26 실측(대미 전략투자, 7건)에서 모델은
#
#     08-17 막판 조율 · 08-30 MOU 서명 연기 · 09-11 미국 속도 압박 · 09-26 텍사스 확정
#
# 을 골랐다. 빠진 것은 **09-25 APR1400 도입 합의 지연** — 이 스레드에서 원인→결과
# 관계가 판정된 전환점이고, 한수원과 가장 직접 닿는 사건이다. 대신 관계 판정도
# 없는 09-11 이 들어갔다.
#
# 그래서 **오늘 사건(마지막)은 코드가 못 박고**, 나머지 칸은 편집 데스크(Narrator)가
# 기준을 받아 고른다(`choose_timeline`). 모델은 고른 사건마다 한 줄씩 쓴다.
#
# 왜 반반인가 — 지난 스토리 카드를 되짚으면(09-23·09-25·09-26) 기준 없이 모델에게
# 맡겼을 때 난 가장 큰 사고는 **오늘 사건 누락**이었다(3건 중 2건). 그건 코드가
# 막는다. 반면 가운데 칸은 뜻을 봐야 한다 — 코드는 떨어져 있는 같은 얘기(09-23:
# 08-11 과 08-20 이 같은 제목)를 못 알아보고, 관계 라벨이 빈칸이면(09-26: 6 중 3)
# 점수가 서지 않는다. 모델 선택이 형식에 안 맞으면 아래 코드 선택으로 떨어진다.
# 두 선택은 비교할 수 있게 로그에 나란히 남긴다.
#
# 아래는 **코드 선택**(폴백·비교 기준)의 규칙이다.
#
#   필수   오늘 사건(마지막) — 스토리 카드가 나온 이유다.
#          출발점(첫 사건)   — 어디서 시작했는지.
#   점수   진행 관계(stage_progress·cause_effect)에 닿은 사건   +3  흐름이 넘어간 자리
#          한수원 관련 required / expected                   +2 / +1
#          오늘 바로 앞 사건                                  +1  오늘 일의 직접 배경
#          근거 기사 수가 많은 사건                           +1  크게 보도된 것
#          다음 사건과 same_matter                            −2  뒤 사건이 같은 얘기를 한다
#   동점   더 최근 사건
#
# 진행 관계는 스토리 자격(`eligibility`)과 재방송 판정(`repeat_verdict`)이 이미
# 쓰는 기준이다. 타임라인도 같은 자로 잰다 — 셋이 다른 자를 쓰면 "이 스토리는
# 단계가 넘어갔다" 고 판정한 근거가 정작 타임라인에서 빠질 수 있다.
PROGRESS_POINTS = 3
KHNP_POINTS = {"required": 2, "expected": 1}
LEAD_IN_POINTS = 1
COVERAGE_POINTS = 1
COVERAGE_MIN = 3                # 근거 해시가 이만큼 이상이면 '크게 보도됐다'
RESTATED_PENALTY = 2


def _khnp_level(row: dict, detail: dict) -> str:
    return khnp_relevance.relevance({
        "title": row.get("title") or detail.get("title"),
        "summary": detail.get("summary"),
        "topics": detail.get("topics") or [],
    })["level"]


def timeline_score(events: list[dict], i: int, details: list[dict] | None = None) -> tuple[int, list[str]]:
    """가운데 사건 하나의 점수와 그 이유(검사·디버깅용)."""
    row = events[i]
    detail = (details or [{}] * len(events))[i] or {}
    score, why = 0, []
    rel_in = str(events[i - 1].get("relation_to_next") or "") if i > 0 else ""
    rel_out = str(row.get("relation_to_next") or "") if i < len(events) - 1 else ""
    if rel_in in PROGRESS_RELATIONS or rel_out in PROGRESS_RELATIONS:
        score += PROGRESS_POINTS
        why.append("진행")
    level = _khnp_level(row, detail)
    if level in KHNP_POINTS:
        score += KHNP_POINTS[level]
        why.append(f"한수원:{level}")
    if i == len(events) - 2:
        score += LEAD_IN_POINTS
        why.append("직전")
    if len(row.get("evidence_hashes") or ()) >= COVERAGE_MIN:
        score += COVERAGE_POINTS
        why.append("보도")
    if rel_out == "same_matter":
        score -= RESTATED_PENALTY
        why.append("같은사안")
    elif _title_key(row) in {_title_key(earlier) for earlier in events[:i]}:
        # 떨어져 있는 같은 제목(09-23: 08-11 과 08-20). 인접 관계로는 안 잡힌다.
        score -= RESTATED_PENALTY
        why.append("되풀이")
    return score, why


def _title_key(row: dict) -> str:
    return "".join(str(row.get("title") or "").split())


def select_timeline(events: list[dict], limit: int = TIMELINE_ROWS,
                    details: list[dict] | None = None) -> list[int]:
    """타임라인에 세울 사건의 위치(시간순). 사건이 칸보다 적으면 전부."""
    n = len(events)
    if n <= limit:
        return list(range(n))
    if limit < 2:
        return [n - 1]
    middle = sorted(range(1, n - 1),
                    key=lambda i: (timeline_score(events, i, details)[0], i),
                    reverse=True)
    return sorted({0, n - 1, *middle[:limit - 2]})


def evidence_packet(candidate: StoryCandidate, date: str, *, topic: str = "") -> dict:
    """Evidence Packet — 사건마다 **자기 근거만** 들고 선다.

    한 행의 재료는 그 행의 `source_event_id` 하나에서만 온다. 다른 사건의
    기사·문장을 끌어와 채우지 않는다 — 그러면 타임라인 한 줄이 다른 날의 근거로
    선다(`card_context` 모듈 주석 ③). 스토리 전체의 '왜 중요한가'만 여러 사건을
    함께 인용할 수 있고, 그 자리는 아래 `narrative`·`watchpoints` 다.

    `candidates` 는 사건 전부(번호 `n` 이 붙는다) — Narrator 가 여기서 타임라인을
    고른다. `events` 는 **타임라인에 세울 사건만**, `background` 는 나머지다.
    처음에는 코드 선택으로 채우고, Narrator 가 고르면 `choose_timeline` 이
    바꿔 끼운다. Writer 는 `events` 마다 한 줄씩 쓴다 — 고르지 않는다.
    """
    thread = candidate.thread
    index = load_issue_index()
    rows = candidate.events[-MAX_EVENTS:]
    details = [index.get(str(row.get("source_event_id") or "")) or {} for row in rows]
    candidates, narrative, watchpoints, seen_w = [], [], [], set()
    seen_titles: dict[str, int] = {}
    for position, (row, detail) in enumerate(zip(rows, details)):
        item = {
            "n": position + 1,
            "date": row.get("date"),
            # 이 파일의 날짜가 무슨 날짜인지 카피가 추측하지 않게 한다.
            "date_kind": row.get("date_kind") or "first_seen",
            "title": row.get("title"),
            "source_event_id": str(row.get("source_event_id") or ""),
            "relation_to_next": row.get("relation_to_next") or "",
            "evidence_hashes": [str(v) for v in (row.get("evidence_hashes") or ())][:MAX_EVIDENCE],
            "summary": _clip(detail.get("summary"), 200),
            "detail": _clip(detail.get("detail"), DETAIL_MAX),
        }
        # 떨어져 있는 같은 제목은 모델에게도 알려 준다 — 되풀이를 고르지 않게.
        key = _title_key(row)
        if key in seen_titles:
            item["repeats"] = seen_titles[key]
        seen_titles.setdefault(key, position + 1)
        candidates.append(item)
        line = _clip(detail.get("implication") or detail.get("summary"), 160)
        if line and line not in narrative:
            narrative.append(line)
        for question in (detail.get("open_question"), detail.get("why_important")):
            question = _clip(question, 160)
            if question and question not in seen_w:
                seen_w.add(question)
                watchpoints.append(question)
    code_pick = select_timeline(rows, TIMELINE_ROWS, details)

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
        "candidates": candidates,
        # 코드가 고른 타임라인(0 부터 센 위치). Narrator 선택이 틀리면 이것으로 간다.
        "code_pick": code_pick,
        "events": _timeline_events(candidates, code_pick),
        # 타임라인에 안 세운 사건. 쟁점·의미의 재료이고, 타임라인 행이 되면 안 된다.
        "background": _background(candidates, code_pick),
        "narrative": narrative[-5:],
        "phase_now": narrative[-1] if narrative else (issue.get("summary") or ""),
        "watchpoints": watchpoints[:6],
        "summary": rep.get("summary") or issue.get("summary") or "",
        "why_important": issue.get("why_important") or "",
        # 표지 통계 — 지어내지 않고 원장이 센 값을 그대로 쓴다.
        "briefing_count": thread.get("briefing_count") or 0,
        "lifespan_days": thread.get("lifespan_days") or 0,
    }


def _timeline_events(candidates: list[dict], picked: list[int]) -> list[dict]:
    """고른 위치의 사건. 관계는 **타임라인에서도 이웃일 때만** 싣는다 — 사이 사건을
    건너뛰었는데 원래 이웃의 관계를 두면 '원인→결과' 가 엉뚱한 두 칸을 잇는다."""
    out = []
    for k, position in enumerate(picked):
        row = {key: value for key, value in candidates[position].items()
               if key not in ("n", "repeats")}
        nxt = picked[k + 1] if k + 1 < len(picked) else None
        if nxt != position + 1:
            row["relation_to_next"] = ""
        out.append(row)
    return out


def _background(candidates: list[dict], picked: list[int]) -> list[dict]:
    return [{"date": row.get("date"), "title": row.get("title"), "summary": row.get("summary")}
            for position, row in enumerate(candidates) if position not in picked]


def check_model_pick(pick: object, total: int, limit: int = TIMELINE_ROWS) -> tuple[list[int] | None, str]:
    """Narrator 의 `timeline_pick`(1 부터 센 n, 오늘 사건 제외)을 위치 목록으로.

    형식이 틀리면 (None, 이유). 오늘 사건은 여기서 붙인다 — 모델이 뺐든 넣었든
    마지막 칸은 늘 오늘이다.
    """
    if total <= limit:
        return list(range(total)), "후보가 칸보다 적다 — 전부"
    if not isinstance(pick, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in pick):
        return None, f"번호 목록이 아니다: {str(pick)[:40]}"
    chosen = [v for v in pick if v != total]            # 오늘을 넣었으면 걷어 낸다
    if len(set(chosen)) != len(chosen):
        return None, f"번호 중복: {pick}"
    if any(not 1 <= v < total for v in chosen):
        return None, f"없는 번호: {pick} (후보 1~{total})"
    if len(chosen) != limit - 1:
        return None, f"{len(chosen)}개 — 오늘을 빼고 {limit - 1}개여야 한다"
    return sorted(v - 1 for v in chosen) + [total - 1], "모델"


def choose_timeline(packet: dict, model_pick: object, limit: int = TIMELINE_ROWS) -> str:
    """Narrator 선택을 packet 에 반영한다. 틀리면 코드 선택을 둔다. 로그 한 줄을 돌려준다.

    비교를 위해 두 선택을 packet["timeline_pick"] 에 나란히 남긴다.
    """
    candidates = packet.get("candidates") or []
    if not candidates:
        return "[cards] 타임라인 후보 없음 — 선택 건너뜀"
    code = list(packet.get("code_pick") or [])
    picked, why = check_model_pick(model_pick, len(candidates), limit)
    used = "모델" if picked is not None and why == "모델" else ("전부" if picked is not None else "코드")
    final = picked if picked is not None else code
    packet["events"] = _timeline_events(candidates, final)
    packet["background"] = _background(candidates, final)
    packet["timeline_pick"] = {"used": used, "model": model_pick, "code": [p + 1 for p in code],
                               "final": [p + 1 for p in final], "why": why}

    def names(positions):
        return " / ".join(f"{str(candidates[p].get('date'))[5:]} {str(candidates[p].get('title') or '')[:14]}"
                          for p in positions)
    if used == "전부":
        return f"[cards] 타임라인 {len(candidates)}건 전부 — {why}"
    line = f"[cards] 타임라인 선택 — 사용: {used}"
    if used == "코드":
        line += f" (모델 선택 무효: {why})"
    model_view = names(picked) if picked is not None else str(model_pick)
    return f"{line}\n[cards]   모델: {model_view}\n[cards]   코드: {names(code)}"

