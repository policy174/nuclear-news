# -*- coding: utf-8 -*-
"""카드의 편집 계층 — **무엇을 말할지 고르는 일**과 **그것을 적는 일**을 가른다.

왜 갈랐는가
-----------
예전에는 한 번의 호출이 둘을 같이 했다. 기사 여섯 칸을 주고 "제목 18자, 사실
불릿 2~3개, 의미 불릿 2~3개" 를 요구하면, 모델은 **글자 수를 맞추는 일**에
붙들려 무엇을 말할지는 원문 순서대로 고른다. 그 결과가 이런 카드다.

    제목    SAR 시범사업 착수
    사실    진안·금산에서 시범사업 착수
    의미    시범사업이 실제 착수됨

세 칸이 같은 말이다. 규격은 전부 통과한다 — 길이도 개수도 JSON 도 맞다.
`validate` 가 잡을 수 있는 것이 아니었다.

그래서 스토리가 있는 날은 편집 판단을 먼저 받는다.

    Evidence → Narrator(무엇을) → Editorial Brief → Writer(어떻게) → QA

Narrator 는 카피라이터가 아니다. 글자 수를 세지 않고, `core_change`·
`must_know_facts`·`so_what`·`avoid_repeating` 만 고른다. Writer 는
그것을 규격에 맞게 적을 뿐 기사를 다시 해석하지 않는다.

호출 수 계약
------------
**스토리가 없는 날까지 둘로 늘리지 않는다.** 그런 날은 재료가 오늘치 3건뿐이라
한 응답 안에서 판단과 카피를 같이 받아도 맥락이 끊기지 않는다.

    스토리 없는 날   card_daily_writer            논리 1회
    스토리 있는 날   card_editorial_narrator
                     → card_writer                논리 2회
    QA 실패          card_writer_repair           +1 (Narrator 는 다시 안 부른다)

**논리 호출과 HTTP 요청은 다른 수다.** 예전 `make_cards` 는 바깥에
`for attempt in (1, 2)` 루프를 두고 안에서 `call_json(retries=3)` 을 불렀다 —
"1회" 가 최악의 경우 **8번의 HTTP 요청**이었고, 로그 어디에도 그 수가 안 남았다.
바깥 루프를 걷고 전송 재시도를 `CARD_LLM_RETRIES`(기본 1)로 명시한다.
"""
from __future__ import annotations

import json
import os

import gemini_client
import llm_policy

# 전송 계층 재시도 상한. 논리 호출 1회 = 최대 `1 + CARD_LLM_RETRIES` HTTP 요청.
CARD_LLM_RETRIES = max(0, int(os.environ.get("CARD_LLM_RETRIES") or 1))

BRIEF_SCHEMA_VERSION = "editorial-brief-v1"

# ── 스토리 카드 규격 ───────────────────────────────────────────────────────
# 프롬프트가 이 숫자를 문장으로 적어야 해서 **스키마와 같은 곳**에 둔다.
# story_cards.py 가 여기서 가져다 쓴다(반대 방향이면 순환 import 가 된다).
COVER_HEADLINE_MAX = 26
COVER_DECK_MAX = 90
BADGE_VALUE_MAX = 14
BADGE_LABEL_MAX = 20
LEDE_MAX = 30
WHEN_MAX = 16
WHAT_MAX = 34
NOTE_MAX = 40
ISSUE_TITLE_MAX = 14
ISSUE_POINT_MAX = 30
WHY_HEADLINE_MAX = 34
PILLAR_TITLE_MAX = 10
PILLAR_TEXT_MAX = 44
QUOTE_MAX = 48
CHECK_HEADLINE_MAX = 20
CHECK_TEXT_MAX = 34
ASIDE_MAX = 64
TIMELINE_ROWS = 4
ISSUE_COUNT = 3
PILLAR_COUNT = 3
CHECK_COUNT = 5
ISSUE_ICONS = ("coins", "plant", "doc", "market", "shield", "network")
PILLAR_ICONS = ("market", "shield", "network", "coins", "plant", "doc")


class BriefError(RuntimeError):
    """편집 판단을 믿을 수 없다. 호출자가 스토리를 빼고 일일로 떨어진다."""


# ── 편집 기준 ──────────────────────────────────────────────────────────────
# Narrator 와 Writer 가 **같은 기준**을 본다. 한쪽에만 적으면 두 단계가 다른
# 카드를 상상한다.
RUBRIC = """편집 기준

[제목]
- 오늘 **새롭게 달라진 것**을 앞세운다. 원 기사 제목을 짧게 줄인 문구가 아니다.
- 주체·대상·행동 중 최소 둘이 식별돼야 한다.
- 과장·평가·감탄을 쓰지 않는다. 14~18자에서 정보 밀도를 최대로.
- 「계획 넘어」·「처음」·「본격 전환」 같은 **비교 표현은 과거 단계가 입력에
  실제로 있을 때만** 쓴다. 없으면 지어내지 않는다.

[확인된 사실]
- 검증 가능한 것만. 날짜·기관·대상·지역·결정·수치·시행 여부.
- 전망·평가·의미 해석·수사를 쓰지 않는다.
- **불릿끼리 서로 다른 정보**여야 한다. 같은 사실을 말만 바꿔 두 줄로 쓰지 않는다.
- 가장 중요한 사실을 첫 줄에.

[왜 중요한가]
- 사실의 되풀이가 아니다. "그래서 무엇이 달라지는가" 에 답한다.
- 첫 줄: 그 사실로 실제로 달라지는 것.
- 둘째 줄: 정책·한수원·산업 차원에서 봐야 할 것.
- 셋째 줄(필요할 때): 아직 확정되지 않아 앞으로 확인할 것.
- 한수원과 직접 관련이 없으면 억지로 시사점을 만들지 않는다. 일반 정책·시장
  의미가 낫다.
- 「정책적 의미가 큼」·「귀추 주목」·「관심 필요」·「영향 예상」 같은 문구는
  구체적인 결과 없이 쓰지 않는다. 무엇이 어떻게 달라지는지를 적는다.

[말투]
- 카드 문구는 **개조식 체언 종결**로 쓴다 — "~함"·"~됨"·"~임" 또는 명사로 끝낸다.
  예) "원전 8기 노형 배분 합의 지연" / "연간 200억 달러 한도로 집행함" / "후속 일정은 미정임"
- "~습니다"·"~합니다"·"~했다" 같은 서술형으로 쓰지 않는다. 글자가 늘어 카드에서 잘린다.
- 예외는 스토리의 cover.deck·facts.lede 둘뿐이다(아래 story 규칙).

[반복 금지]
- 같은 사실이 제목·사실·의미 세 군데에 나오면 그 카드는 한 가지만 말한 것이다.

[지어내기 금지]
- 입력에 없는 숫자·날짜·기관·사건을 쓰지 않는다. 불확실한 것은 "미정" 으로 남긴다."""


NARRATOR_SYSTEM = f"""너는 한국수력원자력 원자력정책실 카드뉴스의 **편집 데스크**다.
카피를 쓰지 않는다. 오늘 카드가 **무엇을 말할 것인지**만 정한다.
글자 수를 맞추려 애쓰지 마라 — 그건 다음 단계가 한다.

{RUBRIC}

JSON 만 출력한다. 스키마:
{{
 "schema_version": "{BRIEF_SCHEMA_VERSION}",
 "issues": [{{
   "issue_id": 입력의 issue_id 를 **그대로**,
   "core_change": 오늘 이 이슈에서 새롭게 확인된 변화 한 문장,
   "headline_angle": 제목에서 가장 앞세울 것 한 구절,
   "must_know_facts": [독자가 반드시 알아야 할 **서로 다른** 사실 2~3개],
   "so_what": [{{"type": "policy_effect"|"khnp_implication"|"industry_implication",
               "text": 그 사실 때문에 실제로 달라지는 것}}] 1~2개,
   "watchpoint": 아직 확정되지 않아 앞으로 확인해야 할 것 (없으면 ""),
   "avoid_repeating": [사실과 의미에서 되풀이하면 안 되는 문구 1~3개]
 }}] — 입력 issues 와 **같은 개수·같은 순서**,
 "story": 입력에 story 가 있을 때만. 없으면 null.
 {{
   "thread_id": 입력의 thread_id 를 그대로,
   "story_subject": 이 사안이 무엇에 관한 것인가,
   "story_arc": 어떤 단계들을 거쳐 지금에 왔는가 한 문장,
   "current_stage": 지금 어느 단계인가,
   "turning_point": 흐름이 바뀐 지점 (없으면 ""),
   "key_change": 이 스토리의 핵심 변화,
   "core_issues": [현재 해결·결정되지 않은 것 2~3개],
   "so_what": [정책·시장·사업에서 달라지는 것 2~3개],
   "confirmed_facts": [입력 candidates 로 확인된 사실 2~4개],
   "unknowns": [아직 모르는 것 1~3개],
   "watchpoints": [다음에 확인해야 할 결정·실증·승인 2~5개],
   "timeline_pick": [타임라인에 세울 candidates 의 n 번호] — 아래 규칙
 }}
}}

timeline_pick — 스토리 2장 타임라인에 세울 사건을 고른다. 칸은 {TIMELINE_ROWS}개다.
- 마지막 후보(가장 큰 n)는 오늘 사건이라 **항상 들어간다.** 그것을 **빼고**
  {TIMELINE_ROWS - 1}개를 고른다. 후보가 {TIMELINE_ROWS}개 이하면 [] 로 둔다.
- 이 사안이 **어떻게 여기까지 왔는지** 한눈에 보이게 고른다. 앞세울 것:
  출발점, 흐름이 바뀐 지점(relation_to_next 가 stage_progress·cause_effect),
  한수원 사업·원전과 직접 닿는 사건.
- 같은 얘기를 되풀이한 사건은 고르지 않는다 — relation_to_next 가 same_matter 이거나
  repeats 가 붙은 후보(앞의 같은 제목 후보 번호다).
- 입력에 있는 n 만 쓴다. 번호가 틀리면 코드가 대신 고른다.

story.since_last 가 있으면 이 스토리는 그 날짜에 이미 카드로 나갔다. key_change 는
그 날짜 이후 새로 붙은 사건(new_titles)으로 달라진 것이어야 한다.

story 는 **일일 카드의 확대판이 아니다**. 일일은 "오늘 무엇이 달라졌는가",
story 는 "이 사안이 어떻게 여기까지 왔는가" 를 보여 준다.
story.confirmed_facts 와 unknowns 를 섞지 마라 — 모르는 것을 확인된 것처럼 쓰지 않는다."""


def _writer_card_schema(bullets_min: int, bullets_max: int,
                        headline_max: int, fact_max: int, why_max: int) -> str:
    return f"""
 "daily": {{
   "hook": {{"headline": {headline_max}자 이내 표지 제목. 강조는 `[[대괄호]]`로 한 곳만}},
   "steps": [{{
     "issue_id": 브리프의 issue_id 를 **그대로**,
     "headline": {headline_max}자 이내. 브리프의 headline_angle·core_change 로 쓴다,
     "facts": [{fact_max}자 이내] {bullets_min}~{bullets_max}개. 브리프의 must_know_facts 에서 고른다,
     "why": [{why_max}자 이내] {bullets_min}~{bullets_max}개. 브리프의 so_what·watchpoint 로 쓴다
   }}] — 브리프 issues 와 **같은 개수·같은 순서·같은 issue_id**
 }},"""


STORY_SCHEMA = f"""
 "story": 브리프에 story 가 있을 때만, 없으면 null.
 {{
  "cover":  {{"chip": 분류 한 단어, "topic": 보조 라벨,
             "headline": {COVER_HEADLINE_MAX}자 이내. 강조 `[[ ]]` 한 곳,
             "deck": {COVER_DECK_MAX}자 이내 두 문장,
             "badge": {{"value": 핵심 숫자({BADGE_VALUE_MAX}자 이내),
                       "label": 그 숫자가 무엇인지({BADGE_LABEL_MAX}자 이내)}} 또는 null}},
  "facts":  {{"lede": {LEDE_MAX}자 이내 한 줄,
             "timeline": [{{"when": 날짜, "what": {WHAT_MAX}자 이내}}] 입력 story_events **하나에 한 줄, 같은 순서로**,
             "note": {NOTE_MAX}자 이내 (없으면 "")}},
  "issues": [{{"title": {ISSUE_TITLE_MAX}자 이내, "points": [{ISSUE_POINT_MAX}자 이내] 1~2개,
             "icon": {" | ".join(ISSUE_ICONS)} 중 하나}}] {ISSUE_COUNT}개,
  "why":    {{"headline": {WHY_HEADLINE_MAX}자 이내 한 문장,
             "pillars": [{{"title": {PILLAR_TITLE_MAX}자 이내, "text": {PILLAR_TEXT_MAX}자 이내,
                         "icon": {" | ".join(PILLAR_ICONS)} 중 하나}}] {PILLAR_COUNT}개,
             "quotes": [{QUOTE_MAX}자 이내] 1~2개}},
  "check":  {{"headline": {CHECK_HEADLINE_MAX}자 이내,
             "checks": [{{"text": {CHECK_TEXT_MAX}자 이내, "done": true/false}}] {CHECK_COUNT}개,
             "aside": {ASIDE_MAX}자 이내}}
 }}

story 규칙:
- **story_events 는 타임라인에 세울 사건으로 이미 골라 둔 것이다.** event 하나마다 한 줄을,
  입력과 같은 순서로 쓴다. 고르거나 빼거나 합치지 않는다. 마지막 event 가 오늘 사건이다.
- **timeline[].when 은 그 event 의 날짜만 쓴다.** 없던 날짜를 붙이지 않는다.
  '현재' 같은 행을 지어내지 않는다.
- 각 timeline 행은 **자기 event 의 내용만** 쓴다. 다른 날 사건을 끌어오지 않는다.
- story_background 는 타임라인에 **넣지 않은** 사건이다. 쟁점·의미를 쓸 때 재료로만 쓰고
  timeline 행으로 만들지 않는다.
- badge.value 의 숫자는 입력에 나온 숫자여야 한다. 없으면 badge 를 null 로.
- checks 는 반드시 섞는다: 앞의 2~3개는 이미 일어난 사실(done=true),
  나머지는 앞으로 볼 것(done=false). 전부 같은 값이면 버려진다.
- 브리프 story.since_last 가 있으면 이 스토리는 그 날짜에 이미 카드로 나갔다(후속이다).
  cover.deck 과 facts.lede 는 그 뒤 **새로 붙은 사건(new_titles)** 부터 쓴다.
  지난 카드에서 한 얘기를 처음부터 되풀이하지 않는다.
- 말투: cover.deck·facts.lede 만 "~습니다" 서술형으로 짧게. 나머지(제목·타임라인·
  쟁점·의미·인용·체크리스트·aside)는 위 [말투] 대로 개조식 체언 종결."""


def writer_system(bullets_min: int, bullets_max: int, headline_max: int,
                  fact_max: int, why_max: int, *, with_story: bool) -> str:
    """Writer 프롬프트. **기사를 다시 해석하지 않는다**가 이 프롬프트의 본론이다."""
    return f"""너는 한국수력원자력 원자력정책실 카드뉴스의 **카피라이터**다.
편집 데스크가 이미 무엇을 말할지 정했다. 너는 그것을 화면 규격에 맞게 적는다.

**브리프에 없는 사실을 새로 만들지 않는다.** 기사를 다시 해석하지 않는다.
브리프의 `avoid_repeating` 에 적힌 문구는 제목·사실·의미에서 되풀이하지 않는다.

{RUBRIC}

JSON 만 출력한다. 스키마:
{{{_writer_card_schema(bullets_min, bullets_max, headline_max, fact_max, why_max)}
{STORY_SCHEMA if with_story else ' "story": null'}
}}"""


def daily_writer_system(bullets_min: int, bullets_max: int, headline_max: int,
                        fact_max: int, why_max: int) -> str:
    """스토리 없는 날 — **한 응답 안에서** 먼저 정하고 그다음 적는다.

    호출을 둘로 늘리지 않으면서도 "글자 수 맞추기" 에 먼저 붙들리지 않게 하려고,
    같은 응답에서 판단(`brief`)을 먼저 적게 한다. 순서가 중요하다 — 스키마의
    앞자리에 두면 모델이 그것부터 채운다.
    """
    return f"""너는 한국수력원자력 원자력정책실 카드뉴스의 편집자다.
**먼저 무엇을 말할지 정하고, 그다음 카드 문구로 적는다.** 순서를 바꾸지 마라.

{RUBRIC}

JSON 만 출력한다. 스키마:
{{
 "brief": [{{
   "issue_id": 입력의 issue_id 를 그대로,
   "core_change": 오늘 새롭게 확인된 변화 한 문장,
   "headline_angle": 제목에서 앞세울 것,
   "must_know_facts": [서로 다른 사실 2~3개],
   "so_what": [{{"type": "policy_effect"|"khnp_implication"|"industry_implication",
               "text": 실제로 달라지는 것}}] 1~2개,
   "watchpoint": 앞으로 확인할 것 (없으면 ""),
   "avoid_repeating": [되풀이하면 안 되는 문구 1~3개]
 }}] — 입력 articles 와 같은 개수·순서,
{_writer_card_schema(bullets_min, bullets_max, headline_max, fact_max, why_max)}
 "story": null
}}"""


# ── 호출 ───────────────────────────────────────────────────────────────────

def call(task: str, system_prompt: str, payload: dict, *,
         fix_these: list[str] | None = None,
         max_output_tokens: int = 4096,
         log: list[dict] | None = None) -> dict:
    """논리 호출 한 번. **모델은 정책이 정한다.**

    예전 카드 호출은 `model=` 을 안 넘겨 `gemini_client.MODEL` 전역에 기댔고,
    스토리 쪽은 워크플로 env 로 `gemini-3-flash-preview` 를 박았다. 그래서
    "이 카드는 어느 모델이 썼는가" 가 코드가 아니라 환경에 흩어져 있었다.
    """
    profile = llm_policy.profile(task)
    body = dict(payload)
    if fix_these:
        # 재시도에 **구체적인 지적**을 돌려준다. LLM 은 한글 글자 수를 못 세므로
        # "짧게 써라" 를 되풀이하는 것보다 "이 문장이 29자였다" 가 훨씬 잘 듣는다.
        body["fix_these"] = fix_these
    # 실제로 쓴 토큰을 받아 둔다. **응답 본문은 버린다** — 기사 원문이 그 안에
    # 있고, 그것을 로그나 디버그 산출물에 남기지 않는 것이 계약이다.
    usage: dict = {}

    def _usage(event: dict) -> None:
        detail = event.get("detail") or {}
        usage.update({key: detail.get(key) for key in
                      ("prompt_tokens", "candidate_tokens", "thought_tokens",
                       "total_tokens", "finish_reason")})

    result = gemini_client.call_json(
        system_prompt,
        json.dumps(body, ensure_ascii=False, indent=1),
        temperature=0.3,
        max_output_tokens=max_output_tokens,
        retries=CARD_LLM_RETRIES,
        model=profile.model(),
        label=f"cards:{task}",
        trace_sink=_usage,
        **profile.reasoning_kwargs(),
    )
    if log is not None:
        log.append({"task": task, "model": profile.model(),
                    "max_http_attempts": CARD_LLM_RETRIES + 1,
                    "budget": max_output_tokens,
                    "repair": bool(fix_these), **usage})
    return result


# ── 브리프 검증 ────────────────────────────────────────────────────────────

# 모델이 이 칸을 부를 때 실제로 쓴 이름들. **정본은 `so_what` 이다.**
#
# 처음에는 이 칸 이름이 `why_it_matters` 였는데, 2026-09-20 첫 프로덕션 실행에서
# 세 이슈와 스토리 블록 **전부**가 대신 `why_important` 를 냈다. 잘린 것도,
# 사고가 예산을 먹은 것도 아니었다(실측: finish=STOP · output 1441/6144 ·
# thoughts=0). 원인은 이름 충돌이다 — 입력 기사 칸에 `why_important` 가 있고,
# 모델이 출력 스키마 이름보다 **눈앞의 입력 이름**을 따라 적었다.
#
# 그래서 이름을 입력 어디에도 없는 `so_what` 으로 바꿨다(편집 질문 자체이기도
# 하다). 그래도 별칭을 받는 이유는, 동의어로 흔들리는 것이 모델의 정상 동작이고
# **낱말 하나로 그날 스토리를 통째로 버리는 것이 더 나쁘기** 때문이다.
_SO_WHAT_ALIASES = ("so_what", "why_it_matters", "why_important", "impact",
                    "implications", "matters")


def _so_what(row: dict) -> object:
    for name in _SO_WHAT_ALIASES:
        value = row.get(name)
        if value:
            return value
    return None


def normalize_brief(brief: dict) -> dict:
    """별칭을 정본 이름으로 모은다. 검증 **전에** 돈다."""
    if not isinstance(brief, dict):
        return brief
    for row in brief.get("issues") or ():
        if isinstance(row, dict):
            row["so_what"] = _so_what(row)
    story = brief.get("story")
    if isinstance(story, dict):
        story["why_it_matters"] = _so_what(story) or story.get("why_it_matters")
    return brief


def _so_what_texts(value: object) -> list[str]:
    """`[{type, text}]` 도 `["..."]` 도 받는다. 모양까지 틀렸다고 버리지 않는다."""
    out: list[str] = []
    for item in value or ():
        if isinstance(item, dict):
            out.append(str(item.get("text") or "").strip())
        else:
            out.append(str(item or "").strip())
    return [text for text in out if text]


def validate_brief(brief: dict, items: list[dict],
                   thread_id: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """편집 판단을 믿어도 되는가. **판정을 세 갈래로 돌려준다.**

        daily_fatal   이 브리프로는 일일 카드를 쓸 수 없다 → 단독 호출로 물러난다
        story_fatal   스토리만 못 쓴다 → 스토리만 빼고 일일은 이 브리프로 간다
        warnings      쓸 수는 있다. 로그에 남기고 QA·repair 가 받는다

    처음에는 한 목록이었다. 그래서 2026-09-20 실행에서 **일일 쪽 칸 하나가
    비었다는 이유로 멀쩡한 스토리 브리프까지 통째로 버렸고**, 호출도 계약(2회)
    보다 한 번 더 나갔다. 다른 모든 자리에서 "일일과 스토리의 실패를 가른다"고
    해 놓고 여기서만 안 갈랐던 것이 문제였다.
    """
    daily_fatal: list[str] = []
    story_fatal: list[str] = []
    warnings: list[str] = []
    if not isinstance(brief, dict):
        return ["브리프가 객체가 아님"], [], []
    version = str(brief.get("schema_version") or "")
    if version and version != BRIEF_SCHEMA_VERSION:
        warnings.append(f"schema_version={version} — {BRIEF_SCHEMA_VERSION} 를 기대했다")

    issues = brief.get("issues")
    if not isinstance(issues, list):
        return ["brief.issues 가 배열이 아님"], [], warnings
    want = [str(item.get("issue_id") or "") for item in items]
    got = [str((row or {}).get("issue_id") or "") for row in issues]
    if got != want:
        # 누락·추가·재정렬은 **치명적이다.** 순서가 틀리면 Writer 가 A 이슈의
        # 판단으로 B 카드를 쓴다.
        daily_fatal.append(f"brief.issues 의 issue_id 가 입력과 다르다: {got} ≠ {want}")

    for index, row in enumerate(issues, start=1):
        tag = f"brief.issues[{index}]"
        if not isinstance(row, dict):
            daily_fatal.append(f"{tag}: 객체가 아님")
            continue
        # **사실이 없으면 카드를 쓸 수 없다.** Writer 에게는 브리프밖에 없다.
        facts = [str(text or "").strip() for text in (row.get("must_know_facts") or ())]
        if not [text for text in facts if text]:
            daily_fatal.append(f"{tag}.must_know_facts: 비어 있음")
        elif len(facts) > 4:
            warnings.append(f"{tag}.must_know_facts: {len(facts)}개 — 앞의 넷만 쓴다")
        # 아래 둘은 **경고다.** 얇으면 카드가 얇아지지만 QA 와 repair 가 받는다.
        # 여기서 죽이면 그 대가로 스토리까지 같이 빠진다.
        if not str(row.get("core_change") or "").strip():
            warnings.append(f"{tag}.core_change: 비어 있음 — 제목 각이 없다")
        if not _so_what_texts(row.get("so_what")):
            warnings.append(f"{tag}.so_what: 비어 있음 — 의미 불릿이 얇아진다")

    story = brief.get("story")
    if thread_id:
        if not isinstance(story, dict):
            story_fatal.append("brief.story: 스토리 후보가 있는데 story 가 없다")
        elif str(story.get("thread_id") or "") != thread_id:
            story_fatal.append(
                f"brief.story.thread_id={story.get('thread_id')} ≠ {thread_id}")
    elif story not in (None, {}):
        story_fatal.append("brief.story: 후보가 없는데 story 를 만들었다")
    return daily_fatal, story_fatal, warnings
