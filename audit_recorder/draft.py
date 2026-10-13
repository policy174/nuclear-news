"""국감 정리본 자동 초안 — 지니 10/7 제출본 양식. 정본 결정은 KICKOFF.md.

    python draft.py <회의폴더>                 초안 docx 만들기 (발송 안 함)
    python draft.py <회의폴더> --send          + 텔레그램 DM
    python draft.py <회의폴더> --until 12:30   그 시각까지 자막만 (정회 시점 누적본)

흐름: 위원장 호명으로 질의 경계 → 질의별 자막 원문 → 에너지 키워드 1개 이상인 질의만 claude -p 로
선별·쟁점 분류·요약(JSON, 질의별 캐시) → 숫자·이름을 그 시간대 자막과 대조, 어긋나면 1회 재요약 →
그래도 어긋나면 본문에서 빼고 [자막 대조 필요]로 → 쟁점 중요도순 docx + 부록(질의별 자막 원문 그대로)
→ verify.py 로 누락 구간(④) 재검사 → 발송.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "docs_pipeline"))
import verify  # noqa: E402  (NUM·NAME·norm·main 재사용)
from khnp_fmt import new_doc, _run, _badge, title, topic, page  # noqa: E402
from docx.shared import Pt, Cm  # noqa: E402

LABEL_CM = 3.2


def say(d, who, text, party=None):
    """지니 제출본(10-07): 배지가 이름 앞 — (국김 소 희). 직함은 그대로 (기후부장관). build_final_gihu.say 와 같음."""
    p = d.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent, pf.first_line_indent = Cm(LABEL_CM), Cm(-LABEL_CM)
    pf.tab_stops.add_tab_stop(Cm(LABEL_CM))
    if party:
        _run(p, "(")
        _badge(p, party)
        _run(p, " ".join(who) + ")\t")
    else:
        _run(p, f"({who})\t")
    _run(p, text)

# .cmd 를 거치면 timeout 이 cmd.exe 만 죽이고 claude.exe 는 남아 무한 대기(10-10 실측) → exe 직접
_EXE = Path.home() / "AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
CLAUDE = str(_EXE if _EXE.exists() else Path.home() / "AppData/Roaming/npm/claude.cmd")
PARTIES = HERE / "parties.json"
DOCS = HERE / "2026-국정감사" / "_문서"

CALL = re.compile(r"([가-힣]{2,4})\s?(?:위원님|간사님)(?!들)[^.?]{0,10}?(?:질의해|질의하|질문해|질문하|발언하|발언해|이십니다)"
                  r"|다음은\s([가-힣]{2,4})\s?(?:위원님|간사님)(?!들)|^\s*([가-힣]{2,4})\s?(?:위원님|간사님)\s*$"   # '위원님들' 은 호명 아님(10-13 '대해서만' 오탐)
                  r"|(?:다음은?\s)?([가-힣]{2,4})\s?위원(?!님|들|장)\s*(?:질의|발언|$)")   # '서천호 위원 질의하시기' — 님 없는 호명(10-13 서천호가 이연희에 묻힘). 명단에 있을 때만 인정
NOT_NAME = {"존경하는", "우리", "그리고", "장관님", "간사님", "위원님", "다음", "그러면", "이어서", "먼저", "그러니까", "그래서", "아니", "대해서만", "대해서", "위원", "간사"}
NOT_NAME_TAIL = "만는은을를에서도과와의께"   # 조사로 끝나는 '이름'은 호명이 아니다 — 명단에 있으면 예외
ENERGY = re.compile(r"원전|원자력|한수원|공론화|전기본|정기본|전력|전기|에너지|SMR|송전|변전|계통|개통|전력망|송전망|요금|데이터\s*센터"
                    r"|재생|태양광|풍력|수소|LNG|가스|석탄|ESS|발전소|발전사|고준위|사용후\s*핵연료|계속\s*운전|한전|용수|탄소"
                    r"|원안위|원자력안전|방사선|방사능|핵연료|KINS|KINAC|통제기술원|안전기술원")   # 10-13 과방위: 피감기관 어휘가 없어 김선민 건강검진 질의 탈락
# 지니 제출본 쟁점 순서(10-07): 공론화 → 데이터센터 수요 → 신규원전·SMR → 전력망 → 요금·보안·기타
CATEGORIES = ["공론화·전기본", "전력수요·데이터센터", "신규원전·SMR", "원전 운영·안전·폐기물",
              "전력망·재생에너지", "전기요금", "보안", "기타 에너지"]
CEO_Q = re.compile(r"한수원\s*사장|수력원자력\s*사장|김회천|김\s*회\s*천")

def is_cand(turn):
    """에너지 단어 2개 이상, 또는 한수원 사장을 직접 부른 질의(10-13 김선민 건강검진 질의가 '한수원' 1회로 탈락)."""
    txt = " ".join(x for _, x in turn["lines"])
    return bool(turn["who"]) and (len(ENERGY.findall(txt)) >= 2 or bool(CEO_Q.search(txt)))

RECHECK = {"num", "name"}

PROMPT = """너는 한국수력원자력 원자력정책실의 국정감사 모니터링 담당자다. 아래는 국회 의사중계 AI 자막(음성인식, 오탈자 있음) 중
위원장이 「{who}」 위원을 호명한 뒤 다음 호명 전까지의 원문이다. [/] 는 화자 전환 표시다.
위원회: {cmte} / 피감기관: {org}

할 일: 이 질의가 **에너지 정책 질의**인지 판단하고, 맞으면 회사 보고용으로 요약한다.
- 포함: 원전·한수원·공론화·전력수급기본계획·전력수요·데이터센터·전력망·송전·재생에너지·ESS·전기요금·용수·에너지 보안 등.
  원전 단어가 없어도 에너지 정책이면 포함한다.
- **한수원(사장·임원)에 대한 질의는 주제가 무엇이든 포함한다** — 기관 운영·인사·조직·지역사회·수출 조직 배치 등도 우리 회사 사안이다(10-12 허성무 의원 동경주 질의 누락 사고).
- 제외: 노동·환경 등 비에너지 질의, 자료제출·위원 명단 같은 절차 공방, 정치 공세, 의사진행 발언, 업무보고 낭독.
- 한 질의 안에 주제가 둘 이상이면 블록을 나눈다.

요약 양식(지니 제출본 문체 — 아래 예시가 기준이다):
- 질의→답변을 실제 순서대로 번갈아 쓴다. 한 줄 = 한 사람의 연속 발언을 **한 문장, 60자 안팎(최대 80자)**. 열거는 2개까지, 세미콜론 금지으로. 같은 사람이 이어 말한 것은 한 줄로 합친다. 블록당 2~4줄.
- **풀어 쓰지 않는다.** 배경 설명·해외 사례·수사는 뺀다. 의원은 '무엇을 문제 삼고 무엇을 요구했나', 답변자는 '뭐라고 답했나'만(10-12 지니: "딱 언급한 내용만 핵심으로").
- **단, 의원이 말한 수치는 빼지 않는다.** 수치는 팩트다(10-13 지니: 황정아 2,395만MWh·1.9조·2,530만MWh·하루 51억이 빠짐). 수치가 많으면 한 줄을 120자까지 늘리거나 Q를 둘로 나눈다. 줄이는 것은 수식어·사례·반복이지 숫자가 아니다.
- 의원 줄 = 근거(사실·수치) 한두 마디 + 요구나 질문. 종결: ~함. / ~음. / ~는지? / ~것 아닌지? / ~해 주기 바람. / ~필요가 있음.
- 답변자 줄 = 입장 + 조치. 종결: ~하겠음. / ~임. / ~있음. / ~것으로 생각함. / ~판단함. / ~없음.
- "예·그렇습니다" 같은 짧은 대답은 "그렇다고 답함." / "동의함." / "맞다고 답함."으로 쓴다. 어미만 바꾼 비문("그렇음") 금지.
- 용어는 제출본 표기: 전력수급기본계획(첫 언급, 이후 '12차 계획' 가능), 반도체공장, 데이터센터, 원전 공론화, 한수원. 자막의 구어("뻥튀기", "~거예요")는 보고서 말로 바꾼다.
- 배경 설명·해외 사례 나열·수사는 줄이되, **질의에 담긴 요구·쟁점은 하나도 빼지 않는다**. 줄이는 것은 한 발언 안의 군더더기이지 쟁점이 아니다. 쟁점이 다르면 블록을 나눈다(한 질의에 보통 1~3블록).
- 자주 나오는 오인식: 정기본·전지본→전기본(전력수급기본계획), 3배 메가 프로젝트→3대 메가 프로젝트, 패배·폐배·팹→반도체 팹, 커테일→출력제어, 시즌→CGN, 기재부(에너지 맥락)→기후부, 치료용량→실효용량, 기조전원→기저전원.
- 화자: [/] 가 없어도 "위원님", "~하겠습니다", "~드리겠습니다"로 끝나는 문장은 답변자의 말이다. 의원 마이크가 꺼져 답변만 남은 구간은 답변자 줄로만 쓴다.
- who 는 의원이면 이름만(예: 김소희), 답변자는 직함(예: 기후부장관, 산업부장관, 한수원사장, 기후부차관, 위원장).
- 숫자·고유명사는 **자막에 나온 것만** 쓴다. 지어내거나 계산해 합치지 않는다. 오인식으로 보이면 고치되 확신 없으면 [?]를 붙인다.
- 블록 제목은 쟁점을 드러내는 명사구(예: 원전 공론화에 따른 전력수급기본계획 지연 논란 / 호남·용인 반도체공장 관련 전력 및 용수공급). 따옴표·낫표 없이.
- **팩트만, 평가어 금지.** 자막에 없는 평가·해석 단어(논란·의혹·특혜·부당·강행·전가·전락·불일치·실현 가능성·납득 어려움·우려 등)를 제목이나 본문에 넣지 않는다. 의원이 그 말을 직접 썼을 때만, "~라고 지적함 / ~라고 비판함"으로 의원에게 귀속해 쓴다. 의원이 말하지 않은 동기·인과("~을 빌미로", "~때문에")를 추정하지 않는다.
  제목은 '대상 + 쟁점 사실' 명사구다. 나쁜 예: 「선납 요구의 부당성」「불리한 계약 강행 경위」 → 좋은 예: 「한전의 삼성·SK 전기요금 25조 선납 제안」「엘바다 원전 계약 손실 1조 2천억과 감사 결과」
- **논점이 먼저다.** 제목과 의원 첫 줄은 의원이 주장하는 결론(무엇이 문제이고 무엇을 하라는 것인지)을 쓴다. 의원이 A와 B를 비교하면 "A는 ~인데 B는 ~ → 그래서 ~" 구조를 한 줄 안에 살린다. 비교 대상 중 하나(예: 과거 계약)를 제목으로 뽑아 논점을 가리지 않는다.
  나쁜 예: 「웨스팅하우스 기술사용료 상한 설정 및 체코 두코바니 계약 수익성」 → 좋은 예: 「한미 원전협력 웨스팅하우스 대가 상한 20억 달러의 적정성과 수익성」
  논점 찾는 법: 쓰기 전에 의원이 **최종적으로 요구하거나 주장하는 문장**을 먼저 찾는다. 보통 "~해야 합니다", "~아닙니까", "~하시겠습니까", "~바랍니다"로 끝나며 수치 나열 뒤에 온다. 그 문장이 제목과 첫 줄에 드러나지 않으면 다시 쓴다. 수치·사례는 그 주장을 받치는 재료일 뿐이다.
- category 는 다음 중 하나: {cats}

예시는 문체 참고용이다. 예시와 같은 내용이 자막에 있어도 건너뛰지 말고 그대로 포함한다.
예시 1 (김소희 의원):
{{"who":"김소희","text":"원전 공론화를 이유로 12차 전력수급기본계획 확정이 계속 늦어지는데, 토론회에서 이미 다룬 원전 역할을 또 공론화해야 하는지?"}}
{{"who":"기후부장관","text":"공론화위원회가 원전 개수를 정하지는 않는다는 취지임."}}
{{"who":"김소희","text":"재생에너지 100GW 상향 때는 안 한 공론화를 원전에만 하는 것은 철회해야 함."}}
{{"who":"기후부장관","text":"공론화에 최소 3개월이 필요해 2~3개월 늦어지더라도 국민 의견을 충분히 반영하겠음."}}
예시 2 (박지혜 의원):
{{"who":"박지혜","text":"데이터센터 수요가 넉 달 사이 4GW에서 20.8GW로 늘었는데 반도체 팹과 같은 성격으로 보는 것이 맞는지?"}}
{{"who":"기후부장관","text":"다른 요소가 있으며 과잉 추계된 측면이 없지 않다고 봄. 다시 살펴보겠음."}}
{{"who":"박지혜","text":"계약용량만 잡고 전력을 안 쓰는 데이터센터 사업자의 책임을 전용요금제에 반영해 주기 바람."}}
{{"who":"기후부장관","text":"전용요금제 설계 시 계약에 반영하겠음."}}
예시 3 (논점 살리기 — 10/12 박성민 의원):
{{"who":"박성민","text":"한미 협상 중인 웨스팅하우스 대가 상한 20억 달러는 체코 때 3.5억 달러의 6배인데 수익이 남겠는지? 요율 협상을 잘해야 함."}}
{{"who":"한전사장","text":"20억 달러는 상한 가이드라인이며, 체코 기술사용료는 언급된 수치보다 적었음."}}
{feedback}
출력은 JSON 하나만(설명·코드블록 금지):
{{"energy": true|false, "reason": "한 줄", "blocks": [{{"category": "...", "title": "...", "lines": [{{"who": "...", "text": "..."}}],
  "requests": [{{"to": "한수원|원안위|…", "items": ["요구한 자료·조치 한 줄", ...]}}]}}]}}
- requests: 의원이 "제출해 달라 / 보고해 달라 / 마련해 달라"고 한 자료·조치만, 기관별로. 자막에 없으면 빈 배열.

=== 자막 원문 ===
{text}
"""


# ---- 자막 ---------------------------------------------------------------------

def sec(ts):
    h, m, s = map(int, ts.split(":"))
    return h * 3600 + m * 60 + s


def load_lines(folder: Path, until: str | None):
    """final 메시지 → [(HH:MM:SS, 원문)]. 버리는 건 3초 안의 같은 문장·그 문장을 통째로 품은 재전송뿐 (extract_verbatim 규칙)."""
    seq = []
    for l in open(folder / "raw_events.jsonl", encoding="utf-8"):
        try:
            ev = json.loads(l)
            m = json.loads(ev["raw"])
        except Exception:
            continue
        ts = ev["captured_at"][11:19]
        if until and ts > until:
            continue
        if m.get("final") not in (1, True, "1", "true"):
            continue
        for x in m.get("transcripts") or []:
            t = str(x[1]).strip()
            if t:
                seq.append((ts, t))
    n = lambda s: re.sub(r"[\s\-·.,?!]", "", s)  # noqa: E731
    keep, ks = [], [n(t) for _, t in seq]
    for i, (ts, t) in enumerate(seq):
        k = ks[i]
        if not k:
            continue
        dup = False
        j = i - 1
        while j >= 0 and sec(ts) - sec(seq[j][0]) <= 3:   # 앞쪽 3초: 같은 문장 재전송
            if ks[j] == k or (k != ks[j] and k in ks[j]):
                dup = True
                break
            j -= 1
        j = i + 1
        while not dup and j < len(seq) and sec(seq[j][0]) - sec(ts) <= 3:   # 뒤쪽 3초: 이 줄을 품은 더 긴 재전송
            if k != ks[j] and k in ks[j]:
                dup = True
            j += 1
        if not dup:
            keep.append((ts, t))
    return keep


def turns(lines):
    """위원장 호명 → [{who, a, b, lines}]. 첫 호명 전(업무보고 등)은 who=None."""
    calls = []
    parties = json.loads(PARTIES.read_text(encoding="utf-8")) if PARTIES.exists() else {}
    from recorder import RECESS   # 정회·산회 선언도 질의 경계 — 안 끊으면 마지막 질의에 정회 중 잡음이 붙는다(10-13 한준호 12:03→14:10)
    for ts, t in lines:
        s = t.lstrip("- "); pos = 0
        if RECESS.search(s):
            if not calls or calls[-1][1] is not None:
                calls.append((ts, None))
            continue
        while (m := CALL.search(s, pos)):   # 10-13 '김현 위원 위원님 김현 위원님 질의' → 첫 매치 '위원'이 이름으로 잡힘 → 한 글자씩 밀며 다음 후보를 본다
            pos = m.start() + 1
            # 호명 오인식(구자영→구자근)을 명단으로 먼저 바로잡아야 같은 의원 질의가 둘로 안 쪼개진다(10-12)
            name = fix_name(m.group(1) or m.group(2) or m.group(3) or m.group(4), parties)
            if m.group(4) and name not in parties:   # 님 없는 형태는 명단 밖이면 무시('본 위원', '현안 위원' 류)
                continue
            if name not in NOT_NAME and (name in parties or name[-1] not in NOT_NAME_TAIL):
                if not calls or calls[-1][1] != name:
                    calls.append((ts, name))
                break
    bounds = [(lines[0][0] if lines else "00:00:00", None)] + calls
    out = []
    for i, (a, who) in enumerate(bounds):
        b = bounds[i + 1][0] if i + 1 < len(bounds) else (lines[-1][0] if lines else a)
        body = [(ts, t) for ts, t in lines if a <= ts < b or (i + 1 == len(bounds) and ts == b)]
        if body:
            out.append({"who": who, "a": a, "b": b, "lines": body})
    return out


NOISE_YEARS = re.compile(r"(?:\b(?:20\d{1,3}|2\d{3})년?\b[\s,]*){4,}")   # '202년' '20년' '20230년' 변형 포함     # 정회 중 잡음을 자막기가 '2023년 2013년 …'로 뱉는다(10-13 지니 "풀 스크립트 2023 있던데")
NOISE_REPEAT = re.compile(r"\b(\S+(?:\s\S+)?)(?:\s+\1\b){3,}")   # '감사합니다 감사합니다 감사합니다 …'


def clean_noise(t: str) -> str:
    """음성인식 잡음 반복만 지운다 — 실제 발언은 건드리지 않는다(연도 4개 이상 연속, 같은 말 4회 이상 반복)."""
    t = NOISE_YEARS.sub("(잡음) ", t)
    return NOISE_REPEAT.sub(r"\1 (반복)", t)


ONLY_YEARS = re.compile(r"^(?:(?:20\d{1,3}|2\d{3})년?[\s,]*)+$")   # 정회 중 한 줄이 통째로 '2013년 2023년' 인 줄은 버린다 (앞의 \s* 를 겹치면 역추적 폭발 — 10-13 실측 멈춤)


def as_text(lines):
    out = []
    for ts, t in lines:
        body = t.replace(" -", " [/]").lstrip("-").strip()
        if ONLY_YEARS.match(body):
            continue
        out.append(f"[{ts}] {clean_noise(body)}")
    return "\n".join(out)


# ---- 요약 ---------------------------------------------------------------------

def claude(prompt: str, timeout: int = 300, model: str | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k != "ANTHROPIC_API_KEY"}
    # 사용자 설정(SessionStart 훅 등)·MCP 를 빼면 기동 57초 → 13초 (2026-10-09 실측)
    cmd = [CLAUDE, "-p", "--setting-sources", "", "--strict-mcp-config", "--output-format", "json", "--model", model or os.environ.get("DRAFT_MODEL", "sonnet")]
    for attempt in (1, 2):   # 10-13: 간헐적으로 600초 넘게 매달림 → 300초에 끊고 1회 재시도가 더 빠르다
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=env, cwd=str(Path(os.environ.get("TEMP", HERE))),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            break
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise
            print(f"claude -p {timeout}초 초과 → 재시도", flush=True)
    try:
        res = json.loads(r.stdout)
    except Exception:
        raise RuntimeError(f"claude -p 실패: {(r.stderr or r.stdout)[:300]}")
    if res.get("is_error"):
        raise RuntimeError(f"claude -p 오류: {str(res.get('result'))[:300]}")
    return res.get("result", "")


def parse_json(s: str) -> dict:
    s = s[s.find("{"): s.rfind("}") + 1]
    return json.loads(s)


def window(lines, a, b, pad=300):
    return [t for ts, t in lines if sec(a) - pad <= sec(ts) <= sec(b) + pad]


def mark_unverified(block):
    """자막에서 확인 안 된 숫자·이름은 그 자리에 [?] (10-13 지니 "수치 모르면 물음표"). 이미 [?]면 그대로."""
    toks = sorted(set(block.get("unverified") or []), key=len, reverse=True)
    if not toks:
        return block
    def mark(s):
        for tok in toks:
            s = re.sub(re.escape(tok) + r"(?!\s*\[\?\])", tok + "[?]", s)
        return s
    block["title"] = mark(block.get("title", ""))
    for l in block.get("lines") or []:
        l["text"] = mark(l["text"])
    return block


def mismatches(block, corpus_lines):
    """verify.py ①② 규칙: 블록의 숫자·이름이 질의 시간대(±5분) 자막에 있는가."""
    corpus = verify.norm(" ".join(corpus_lines))
    text = "\n".join(l["text"] for l in block["lines"])
    bad = []
    for m in verify.NUM.finditer(text):
        tok = m.group(0).strip()
        if verify.norm(tok) in corpus:
            continue
        core = verify.norm(re.sub(r"[^\d.만]", " ", tok).strip())
        if not (core and core in corpus):   # 단위만 다른 건 음성인식 단위 오인식 가능 → 통과
            bad.append(tok)
    for n in set(verify.NAME.findall(text)):
        if n not in verify.SKIP_NAMES and n[-1] not in "는은이가을를의며다고로서에" and n not in corpus:   # '요청에 의원' 오탐(10-10)
            bad.append(n)
    return bad


REVIEW = """너는 국정감사 요약 검수자다. 아래 자막 원문(위원장이 「{who}」 위원을 호명한 질의 구간)과 그 요약(JSON)을 대조하라.
점검 항목 — 하나라도 걸리면 ok=false:
1) 논점: 의원이 마지막에 요구·주장한 문장(~해야 합니다 / ~아닙니까 / ~하시겠습니까 / ~바랍니다)이 제목이나 의원 첫 줄에 드러나는가. 비교 대상(과거 사례·다른 기관)이 제목을 차지하고 있지 않은가.
2) 화자: 답변자의 말("위원님…", "~하겠습니다")이 의원 줄에 들어가거나 그 반대가 아닌가.
3) 누락: 한수원(사장·임원·사업·지역)에 관한 질의·답변이 자막에 있는데 요약에 없지 않은가. energy=false 로 통째로 뺀 경우도 포함.
4) 문장: "그렇음" 같은 비문, 90자를 크게 넘는 줄, 자막에 없는 수치.
5) 팩트: 자막에 없는 평가어(논란·의혹·특혜·부당·강행·전가·전락·불일치·실현 가능성·우려 등)나 의원이 말하지 않은 동기·인과 추정("~을 빌미로")이 제목·본문에 있지 않은가. 의원이 쓴 말이면 "~라고 지적함"으로 귀속됐는가.
출력은 JSON 하나만: {{"ok": true|false, "problems": ["항목번호: 무엇이 어떻게 틀렸고 어떻게 고칠지 한 줄", ...]}}

=== 요약 ===
{summary}
=== 자막 원문 ===
{text}
"""


def review(turn, text, res):
    """2차 검수(10-12 지니 "틀린 거 스스로 정정할 수 있니") — 문제가 있으면 재요약 피드백 문자열, 없으면 ''."""
    try:
        r = parse_json(claude(REVIEW.format(who=turn["who"], summary=json.dumps(res, ensure_ascii=False), text=text), timeout=120, model="haiku"))   # 검수는 가벼운 모델로 — 실시간을 막지 않게(10-12 sonnet 검수 300초 초과)
    except Exception as e:
        print(f"검수 실패(건너뜀): {str(e)[:100]}", flush=True)
        return ""
    probs = [p for p in (r.get("problems") or []) if p]
    return "" if r.get("ok", True) or not probs else "\n검수 지적 — 아래를 고쳐서 다시 써라:\n- " + "\n- ".join(probs) + "\n"


def summarize(turn, meta, all_lines, cache_dir: Path, feedback: str = ""):
    """feedback 이 있으면(검수 지적) 캐시를 건너뛰고 다시 요약해 캐시를 덮어쓴다."""
    text = as_text(turn["lines"])
    key = hashlib.sha1((PROMPT + text).encode()).hexdigest()[:16]
    cf = cache_dir / f"{turn['a'].replace(':', '')}_{turn['who']}_{key}.json"
    if cf.exists() and not feedback:
        return json.loads(cf.read_text(encoding="utf-8"))
    args = dict(who=turn["who"], cmte=meta["cmte"], org=meta["org"], cats=", ".join(CATEGORIES), text=text, feedback=feedback)
    res = parse_json(claude(PROMPT.format(**args)))
    corpus = window(all_lines, turn["a"], turn["b"])
    bad = {i: mismatches(b, corpus) for i, b in enumerate(res.get("blocks") or [])}
    if any(bad.values()):   # 1회 재요약 — 어긋난 토큰을 알려 준다
        miss = sorted({x for v in bad.values() for x in v})
        args["feedback"] = f"\n주의: 직전 요약의 다음 숫자·이름은 자막에 없다 → 자막에 있는 표현으로 고치거나 빼라: {', '.join(miss)}\n"
        res = parse_json(claude(PROMPT.format(**args)))
        bad = {i: mismatches(b, corpus) for i, b in enumerate(res.get("blocks") or [])}
    for i, b in enumerate(res.get("blocks") or []):
        b["unverified"] = bad.get(i, [])
    cf.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


EDITOR = """너는 한국수력원자력 원자력정책실의 국정감사 모니터링 보고서 편집자다(독자: 부서장).
아래는 질의별 1차 요약 블록 목록(번호·분류·제목·시각·의원)이다. 제출본의 **구조만** 정하라(문장은 쓰지 않는다).

1) 고르기 — 본문(핵심) = 한수원 사업과 이어지는 것: 원전·한수원·SMR·고준위·계속운전, 원전 공론화·전력수급기본계획(수요 전망·원전 역할),
   전력수요(데이터센터·반도체·AI), 전력망·송전(대규모 수요지 공급), 전기요금(지역별 요금·요금 전가), 반도체 클러스터 등의 전력·용수 공급,
   원전·전력망 보안, 원전 관련 외국 기업 문제.
   참고(제목만) = 개별 재생에너지 사업·태양광 산업·수소·ESS 시장·에너지 복지·효율·기관 유치, 그리고
   자료 제출·위원 명단 공개 같은 절차 공방, 자료 미제출 항의, 정치 공세.
2) 묶기 — 같은 쟁점이면 여러 의원·여러 시각의 블록을 한 주제로 묶는다(예: 「호남 반도체 전력공급 일정과 원전 포함 전력 여력」 = 윤재옥·신정훈).
   단 **한 주제에 의원은 1~2명까지**. 같은 쟁점에 3명 이상이면 논점(예: 절차 정당성 / 수요 재전망 / 부지 지연)으로 나눈다.
3) 제목 — 주제마다 쟁점을 드러내는 명사구 하나.
4) 순서 — 공론화·전기본 → 전력수요·데이터센터 → 신규원전·SMR → 원전 운영 → 전력망 → 전기요금 → 보안 → 기타. 목표 15~25개 주제.

예(10/6 지니 제출본 주제명): 원전 공론화에 따른 전력수급기본계획 지연 논란 / 원전 공론화 필요성 및 데이터센터 수요 재전망 /
신규 원전 부지선정 지연과 반복 공론화 논란 / 호남·용인 반도체공장 관련 전력 및 용수공급 / 월성1호기 재가동 및 원전 전력 활용방안 /
지역별 전기요금 차등과 충남의 부담 반영 / 중국 원전기업(CGN) 계열 국내 발전사업 적정성 / 재생에너지 출력제어와 전력망·ESS 비용

category 는 다음 중 하나: {cats}
모든 번호는 topics 의 sources 나 ref 중 정확히 한 곳에. 출력은 JSON 하나만(설명·코드블록 금지):
{{"topics": [{{"title": "...", "category": "...", "sources": [번호, ...]}}], "ref": [번호, ...]}}

=== 블록 목록 ===
{items}
"""

CONDENSE = """아래는 국정감사 한 쟁점에 대한 질의·답변 요지다. 회사 보고용으로 **{n}줄 이내**로 줄여라. 의원마다 핵심 요구와 그 답변은 남긴다.
- 질의→답변 실제 순서 유지. 한 줄 = 한 사람 발언 요지 80자 안팎. 의원 ~함./~는지?/~바람., 답변 ~하겠음./~임.
- **아래에 없는 숫자·이름·사실을 새로 쓰지 않는다.** 줄이기만 한다. who 는 그대로 둔다.
출력은 JSON 하나만: {{"lines": [{{"who": "...", "text": "..."}}]}}

쟁점: {title}
{lines}
"""
MAX_LINES = 8


def edit(blocks, cache: Path, all_lines, workers: int = 5):
    """구조 편집 1회(고르기·묶기·제목·순서, 출력 작음) + 8줄 넘는 주제만 병렬 압축.
    실패 시 1차 블록 그대로(Recall > Precision). 10-10: 문장까지 한 번에 쓰게 하면 25분 넘게 안 끝났음."""
    items = "\n".join(f"{i}. [{b.get('category')}] {b['title']} ({b['a'][:5]}, {b['lines'][0]['who']})" for i, b in enumerate(blocks))
    prompt = EDITOR.format(cats=", ".join(CATEGORIES), items=items)
    cf = cache / f"edit_{hashlib.sha1(prompt.encode()).hexdigest()[:16]}.json"
    try:
        if cf.exists():
            res = json.loads(cf.read_text(encoding="utf-8"))
        else:
            res = parse_json(claude(prompt, timeout=480))
            cf.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print("편집 실패 — 1차 블록 그대로", repr(e)[:150])
        for b in blocks:
            b.update(core=True, turn_objs=[b["turn_obj"]])
        return blocks
    used, topics = set(), []
    for t in res.get("topics") or []:
        src = [i for i in t.get("sources") or [] if isinstance(i, int) and 0 <= i < len(blocks) and i not in used]
        if not src:
            continue
        used.update(src)
        src.sort(key=lambda i: blocks[i]["a"])
        turn_objs = []
        for i in src:
            if all(x is not blocks[i]["turn_obj"] for x in turn_objs):
                turn_objs.append(blocks[i]["turn_obj"])
        topics.append({"title": t.get("title", blocks[src[0]]["title"]).strip("「」 "), "category": t.get("category"),
                       "lines": [l for i in src for l in blocks[i]["lines"]], "core": True,
                       "turn_objs": turn_objs, "a": turn_objs[0]["a"], "b": turn_objs[-1]["b"],
                       "unverified": sorted({x for i in src for x in blocks[i].get("unverified", [])})})

    def condense(tp):
        n = max(MAX_LINES, 4 * len(tp["turn_objs"]))   # 묶인 질의가 많으면 줄도 비례 — 답변이 잘리지 않게
        if len(tp["lines"]) <= n:
            return
        body = "\n".join(f"{l['who']}: {l['text']}" for l in tp["lines"])
        ck = cache / f"cond_{hashlib.sha1((CONDENSE + str(n) + tp['title'] + body).encode()).hexdigest()[:16]}.json"
        try:
            if ck.exists():
                lines = json.loads(ck.read_text(encoding="utf-8"))["lines"]
            else:
                lines = parse_json(claude(CONDENSE.format(title=tp["title"], lines=body, n=n), timeout=300))["lines"]
                ck.write_text(json.dumps({"lines": lines}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:   # 압축 실패면 길어도 1차 요약 그대로
            print("압축 실패", tp["title"][:30], repr(e)[:100])
            return
        corpus = [x for to in tp["turn_objs"] for x in window(all_lines, to["a"], to["b"])]
        cand = dict(tp, lines=lines)
        bad = mismatches(cand, corpus)
        if not bad:   # 압축이 자막에 없는 숫자·이름을 만들면 버리고 1차 요약 유지
            tp["lines"] = lines

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(condense, topics))
    ref = [dict(b, core=False, turn_objs=[b["turn_obj"]]) for i, b in enumerate(blocks) if i not in used]
    return topics + ref


def fix_name(name, parties):
    """호명 음성인식 오류(소왕진→서왕진): 명단에 없고, 명단 중 같은 길이·한 글자 차이가 하나뿐이면 교정."""
    if name in parties or not re.fullmatch(r"[가-힣]{2,4}", name):
        return name
    near = [k for k in parties if len(k) == len(name) and sum(a != b for a, b in zip(k, name)) == 1]
    return near[0] if len(near) == 1 else name


# ---- 문서 ---------------------------------------------------------------------

def load_meta(folder: Path):
    m = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    item = m.get("item") or {}
    subj = item.get("xsubj") or m.get("title") or ""
    org = re.search(r"\(([^)]*)\)", subj)
    full = (m.get("play") or {}).get("xfull") or subj.split(" 국정감사")[0]
    return {"date": m.get("date", folder.name[:10]), "cmte": full, "short": folder.name.split("_", 1)[1],
            "org": org.group(1) if org else "피감기관 미확인"}


def key_of(short):
    return next((v for k, v in verify.CMTE.items() if k in short), None) or short[:2]


def write_docx(path: Path, meta, blocks, parties, check=False, failed=()):
    """check=True: 블록마다 'HH:MM~HH:MM 위원회' 줄을 넣은 검증용 사본(verify.py 가 질의 시간대를 읽는다)."""
    d = new_doc()
    title(d, f"2026년도 {meta['cmte']} 국정감사 모니터링(피감기관: {meta['org']})",
          datetime.strptime(meta["date"], "%Y-%m-%d").strftime("%Y. %m. %d.") + f" ({meta['until'] or '종료'} 기준 · 자동 초안)")
    ref = [b for b in blocks if not b.get("core", True)]
    blocks = [b for b in blocks if b.get("core", True)]
    ok = [b for b in blocks if not b["unverified"]]
    flagged = [b for b in blocks if b["unverified"]]
    for b in ok:
        topic(d, b["title"])
        if check:   # 묶인 질의마다 시간대 줄 — verify ④ 누락 검사가 덮인 구간으로 친다
            for to in b["turn_objs"]:
                d.add_paragraph(f"{to['a'][:5]}~{to['b'][:5]} {meta['short']}")
        for l in b["lines"]:
            who = l["who"].strip()
            say(d, who, l["text"].strip(), parties.get(who))
    if flagged:
        page(d)
        p = d.add_paragraph(); _run(p, "[자막 대조 필요] — 아래 숫자·이름이 그 시간대 자막에 없음. 영상 확인 전 인용 금지", bold=True)
        for b in flagged:
            topic(d, b["title"])
            if check:
                for to in b["turn_objs"]:
                    d.add_paragraph(f"{to['a'][:5]}~{to['b'][:5]} {meta['short']}")
            p = d.add_paragraph(); _run(p, "※ 자막에 없음: " + ", ".join(b["unverified"]), size=10, color="C00000")
            b = mark_unverified(dict(b, lines=[dict(l) for l in b["lines"]]))   # 숫자 옆에 [?] (10-13)
            for l in b["lines"]:
                say(d, l["who"].strip(), l["text"].strip(), parties.get(l["who"].strip()))
    if ref and not check:
        p = d.add_paragraph(); p.paragraph_format.space_before = Pt(18)
        _run(p, "[참고] 그 밖의 에너지 질의 (제목만 — 원문은 미가공 자막 파일)", bold=True)
        for b in ref:
            p = d.add_paragraph(); _run(p, f"· {b['title'].strip('「」')} ({b['lines'][0]['who']}, {b['a'][:5]})", size=10.5)
    if failed and not check:
        page(d)
        p = d.add_paragraph(); _run(p, "[요약 실패] — 자동 요약을 못 한 질의(사용량 한도·오류). 부록 자막 원문으로 확인", bold=True)
        for t in failed:
            p = d.add_paragraph(); _run(p, f"({t['a'][:5]}~{t['b'][:5]}) {t['who']} 위원 질의")
    if not check:   # 부록 — 질의별 자막 원문 그대로 (지니 10-09: 빼거나 더하지 않음)
        page(d)
        p = d.add_paragraph(); p.alignment = 1
        _run(p, "[부록] 질의별 자막 원문", bold=True, size=13)
        p = d.add_paragraph()
        _run(p, "※ 국회 의사중계 AI 자막(음성인식) 원문 그대로 — 3초 안 재전송 중복만 제거. 오인식 포함, [/]는 화자 전환. 인용 전 영상 확인 필요", size=9.5)
        seen = set()
        for t in [to for b in ok + flagged for to in b["turn_objs"]] + list(failed):
            if id(t) in seen:
                continue
            seen.add(id(t))
            p = d.add_paragraph(); p.paragraph_format.space_before = Pt(10)
            _run(p, f"({t['a'][:5]}~{t['b'][:5]}) {t['who']} 위원 질의", bold=True, size=10.5)
            for ts, line in t["lines"]:
                body = line.replace(" -", " [/]").lstrip("-").strip()
                if ONLY_YEARS.match(body):   # 정회 중 잡음 줄(10-13 지니 "요약본도?")
                    continue
                p = d.add_paragraph()
                pf = p.paragraph_format
                pf.left_indent, pf.first_line_indent, pf.line_spacing, pf.space_after = Cm(1.6), Cm(-1.6), 1.2, Pt(1)
                _run(p, f"[{ts}] ", size=9, color="777777")
                _run(p, clean_noise(body), size=10)
    d.save(path)


def ascii_name(meta):
    k = {"기후": "gihu", "산자": "sanja", "과방": "gwabang"}.get(key_of(meta["short"]), "cmte")
    stamp = (meta["until"] or "final").replace(":", "")
    return f"kukgam_{meta['date'].replace('-', '')}_{k}_draft_{stamp}.docx"


def build(folder: Path, until: str | None = None, workers: int = 5):
    meta = load_meta(folder)
    meta["until"] = until
    lines = load_lines(folder, until)
    ts_all = turns(lines)
    # 키워드 2회 이상 — 1회는 인사·업무보고 언급 수준이 대부분(10-06 실측 80개 중 76개가 1회 기준 통과)
    cands = [t for t in ts_all if is_cand(t)]
    cache = folder / "draft_cache"
    cache.mkdir(exist_ok=True)
    limit_hit = []

    def safe(t):   # 질의 하나 실패가 초안 전체를 죽이면 안 된다(10-09 세션 한도로 전체 중단 실측)
        if limit_hit:
            return {"energy": None, "error": "사용량 한도 — 이후 질의 건너뜀"}
        try:
            return summarize(t, meta, lines, cache)
        except Exception as e:
            if "limit" in str(e).lower():
                limit_hit.append(1)
            return {"energy": None, "error": str(e)[:200]}

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(safe, cands))
    failed = [t for t, r in zip(cands, results) if r.get("error")]
    blocks = []
    for i, (t, r) in enumerate(zip(cands, results)):
        if not r.get("energy"):
            continue
        for b in r.get("blocks") or []:
            if not b.get("lines"):
                continue
            b.update(turn=i, turn_obj=t, a=t["a"], b=t["b"])
            b["unverified"] = mismatches(b, window(lines, t["a"], t["b"]))   # 캐시 무관 매번 재대조(LLM 호출 없음)
            blocks.append(b)
    parties = json.loads(PARTIES.read_text(encoding="utf-8")) if PARTIES.exists() else {}
    for b in blocks:
        for l in b["lines"]:
            l["who"] = fix_name(l["who"].strip(), parties)
        b["title"] = b["title"].strip("「」 ")
    blocks = edit(blocks, cache, lines)
    rank = {c: i for i, c in enumerate(CATEGORIES)}
    blocks.sort(key=lambda b: (rank.get(b.get("category"), len(CATEGORIES)), b["a"]))
    DOCS.mkdir(exist_ok=True)
    out = DOCS / ascii_name(meta)
    write_docx(out, meta, blocks, parties, failed=failed)
    chk = out.with_name(out.stem + "_check.docx")
    write_docx(chk, meta, blocks, parties, check=True)
    verify.main(str(chk), [str(folder)])   # → <chk>_검증.md, ④ 누락 구간까지
    report = chk.with_name(chk.stem + "_검증.md")
    missed = [l for l in report.read_text(encoding="utf-8").splitlines() if l.startswith("- [") and "대 (" in l]
    members = {l["who"] for b in blocks if b["core"] for l in b["lines"]}
    no_party = sorted(w for w in members if re.fullmatch(r"[가-힣]{2,4}", w) and not re.search(r"장관|차관|사장|위원장|원장|청장", w) and w not in parties)
    summary = {"docx": str(out), "turns": len(ts_all), "candidates": len(cands), "blocks": sum(b["core"] for b in blocks), "ref": sum(not b["core"] for b in blocks),
               "flagged": sum(1 for b in blocks if b["unverified"] and b["core"]), "missed": missed, "no_party": no_party,
               "energy_turns": [f"{t['a'][:5]} {t['who']}" for t, r in zip(cands, results) if r.get("energy")],
               "failed": [f"{t['a'][:5]} {t['who']}" for t in failed],
               "error": next((r["error"] for r in results if r.get("error")), "")}
    (folder / "draft_last.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def caption(s, meta_short):
    c = [f"{meta_short} 정리 초안 — 에너지 질의 {len(s['energy_turns'])}건 · 핵심 쟁점 {s['blocks']}개 + 참고 {s['ref']}개 (자동, 다듬어서 제출)"]
    if s["flagged"]:
        c.append(f"⚠ 자막 대조 필요 {s['flagged']}개 → 문서 뒤쪽 [자막 대조 필요]")
    if s["missed"]:
        c.append(f"⚠ 어느 쟁점에도 안 들어간 원자력 언급 {len(s['missed'])}구간 — 확인 필요")
    if s["failed"]:
        c.append(f"⚠ 요약 못 한 질의 {len(s['failed'])}건({s['error'][:40]}) — 문서 [요약 실패]·부록 원문 확인: " + ", ".join(s["failed"][:12]))
    if s["no_party"]:
        c.append("정당 미확인(배지 없음): " + ", ".join(s["no_party"]))
    return "\n".join(c)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--until", help="HH:MM — 이 시각까지 자막만")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--mail", action="store_true", help="산회 최종본: 정리본 docx + 전체 자막 txt 를 사내 메일로(10-12 지니)")
    a = ap.parse_args()
    until = (a.until + ":59") if a.until and len(a.until) == 5 else a.until
    s = build(Path(a.folder), until)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    if a.mail:
        import smtplib
        from email.message import EmailMessage
        from recorder import load_env
        load_env()
        user, pw, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "MAIL_TO"))
        if not (user and pw and to):
            print("메일 생략: GMAIL_USER/GMAIL_APP_PASSWORD/MAIL_TO 없음")
        else:
            meta = load_meta(Path(a.folder)); k = {"기후": "gihu", "산자": "sanja", "과방": "gwabang"}.get(key_of(meta["short"]), "cmte")
            d8 = meta["date"].replace("-", "")
            script = f"{meta['date']} {meta['cmte']} 전체 자막 원문 (~{a.until or '산회'}) — 국회 AI 자막, 미가공\n\n" + as_text(load_lines(Path(a.folder), until))
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = user, to, f"[PC] {meta['date'][5:].replace('-', '/')} {meta['short']} 국감 최종 정리본 + 전체 스크립트"
            m.set_content(f"{meta['date']} {meta['cmte']} 국정감사({meta['org']}) 산회 기준 자동 정리본과 전체 자막입니다.\n"
                          f"쟁점 {s['blocks']}개, 참고 {s['ref']}개, 에너지 질의 {len(s['energy_turns'])}건. 숫자·이름은 질의 시간대 자막과 대조했습니다. 자동 초안이므로 제출 전 다듬어 주세요.")
            m.add_attachment(Path(s["docx"]).read_bytes(), maintype="application", subtype="vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"kukgam_{d8}_{k}_final.docx")
            m.add_attachment(script.encode("utf-8"), maintype="text", subtype="plain", filename=f"kukgam_{d8}_{k}_full_script.txt")
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as sm:
                sm.login(user, pw.replace(" ", "")); sm.send_message(m)
            print("메일 발송 OK")
    if a.send:   # recorder.send_telegram 은 토큰 없거나 HTTP 실패여도 조용함(10-10 실측: 아무것도 안 감) → 직접 보내고 응답 확인
        import requests
        from recorder import load_env, SOURCE
        load_env()
        # docx 초안은 개인 대화. 원자력 뉴스 채널에는 live_post.py 의 질의별 요약 글만(지니 10-12)
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_OPS_CHAT_ID")
        if not (tok and chat):
            sys.exit("발송 실패: TELEGRAM_BOT_TOKEN / TELEGRAM_SUMMARY_CHAT_ID·TELEGRAM_OPS_CHAT_ID 없음")
        cap = f"[{SOURCE}] " + caption(s, Path(a.folder).name.split("_", 1)[1])
        with open(s["docx"], "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendDocument",
                              data={"chat_id": chat, "caption": cap[:1024]}, files={"document": fh}, timeout=60)
        if not r.ok or not r.json().get("ok"):
            sys.exit(f"발송 실패: HTTP {r.status_code} {r.text[:200]}")
        print("발송 완료", r.json()["result"]["message_id"])
