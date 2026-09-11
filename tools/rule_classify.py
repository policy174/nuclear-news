# -*- coding: utf-8 -*-
"""규칙 기반 분류기 — LLM 없이 domain 을 판정할 수 있는지 재본다.

역태깅 2,610건을 무료 키로 2~3일 쪼개 돌리는 대신 규칙으로 끝낼 수 있으면
쿼터도 시간도 아낀다. 이 저장소엔 선례가 있다 — entity_match.py 가 LLM 0회로
개체를 결정적 매칭한다.

평가: LLM 이 판정한 표본 30건을 정답으로 두고 일치율을 잰다. 정답 자체가
완벽하진 않지만(지니 검수 전), "규칙이 LLM 근처까지 가는가"는 알 수 있다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path.cwd()

# 순서가 곧 우선순위다. 위에서 걸리면 아래는 안 본다 —
# tags.json 의 경계 규칙("구체적인 쪽이 이긴다")을 순서로 구현한 것.
RULES: list[tuple[str, str, str]] = [
    # (대분류, 세부태그, 정규식)
    ("핵연료", "우라늄", r"우라늄|yellowcake|농축|변환시설"),
    ("핵연료", "핵연료제조", r"핵연료|연료봉|연료집합체|MOX|피복관"),
    ("사후처리", "사용후핵연료", r"사용후핵연료|사용후 핵연료|고준위|건식저장|SF저장"),
    ("사후처리", "방폐물", r"방폐물|방사성폐기물|중저준위|처분장"),
    ("사후처리", "해체", r"해체|폐로|부지 재이용"),
    ("SMR", "SMR", r"\bSMR\b|소형모듈|i-SMR|마이크로원자로|소형원자로"),
    ("계속운전", "계속운전", r"계속운전|수명연장|설계수명|장기운전|가동연장"),
    ("계속운전", "탄력운전", r"탄력운전|출력조절"),
    ("해외사업", "원전수출", r"수출|수주|체코|폴란드|두코바니|사우디|UAE|바라카|대미\s?투자|웨스팅하우스 지분"),
    ("재생·수소", "청정수소", r"수소"),
    ("재생·수소", "재생에너지", r"재생에너지|신재생|태양광|풍력|RPS|REC|배출권"),
    ("재생·수소", "수력양수", r"양수발전|수력"),
    ("규제", "원안위", r"원자력안전위원회|원안위|NRC|규제기관|입법예고|행정예고"),
    ("건설", "신규원전", r"신규\s?원전|신규원전|착공|건설 재개|부지 준비|건설허가"),
    ("수용성", "지역지원", r"주변지역|지원사업|주민|수용성|갈등"),
    ("안전성", "사고고장", r"정지|고장|누설|누출|사고|화재|지진|균열"),
    ("경제성", "발전원가", r"발전원가|정산단가|전기요금|원가|보험금|충당금"),
    ("정책", "전원계획", r"전기본|전력수급|전원믹스|전력수요|전력계통"),
    ("정책", "국제협력외교", r"협정|정상회담|고위급|MOU|MoU|협력 확대|123 협정"),
    ("정책", "원자력정책", r"정책|법안|특별법|국회|예산"),
]
COMPILED = [(d, t, re.compile(p)) for d, t, p in RULES]


def classify(text: str) -> tuple[str, str] | tuple[None, None]:
    for dom, tag, pat in COMPILED:
        if pat.search(text):
            return dom, tag
    return None, None


def main() -> int:
    doc = (REPO / "docs/2026-09-11-d6-tag-sample.md").read_text(encoding="utf-8")
    rows = []
    for line in doc.splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            rows.append({"n": int(m.group(1)), "llm": m.group(2).strip(),
                         "title": m.group(5).strip()})
    # 13칸 판본으로 저장된 정답지라 개명분을 맞춰 준다.
    rename = {"신규건설": "건설", "재생에너지": "재생·수소", "수소": "재생·수소"}
    agree = 0
    out = ["| # | LLM 판정 | 규칙 판정 | 일치 | 제목 |", "|---|---|---|---|---|"]
    for r in rows:
        gold = rename.get(r["llm"], r["llm"])
        dom, tag = classify(r["title"])
        ok = (dom == gold)
        agree += ok
        out.append(f"| {r['n']} | {gold} | {dom or '—'} | {'O' if ok else 'X'} | {r['title'][:40]} |")
    print("\n".join(out))
    print(f"\n일치 {agree}/{len(rows)} = {agree/len(rows)*100:.0f}%")
    miss = sum(1 for r in rows if classify(r["title"])[0] is None)
    print(f"규칙이 아무것도 못 고른 것: {miss}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
