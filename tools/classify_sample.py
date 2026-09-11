# -*- coding: utf-8 -*-
"""D6 표본 검증 — 경계가 애매한 실기사 30건을 새 분류 체계로 판정한다.

이건 게이트지 구현이 아니다. 여기서 지니가 확인한 뒤에야 큐레이션 프롬프트에
넣고 역태깅을 돌린다(계획 D6/D7). 호출은 2회(15건씩)라 쿼터 부담 없음.

실행: python scratchpad/classify_sample.py   (repo 루트에서)
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[0]
REPO = Path.cwd()
sys.path.insert(0, str(REPO))

import gemini_client  # noqa: E402

TAGS = json.loads((REPO / "tags.json").read_text(encoding="utf-8"))
DOMAINS = TAGS["domains"]
RULES = TAGS["boundary_rules"]

CATALOG = "\n".join(
    f"- {d['id']}: {' / '.join(d['tags'])}" for d in DOMAINS
)
RULE_TEXT = "\n".join(f"- {r}" for r in RULES)

SYSTEM = f"""너는 한국수력원자력 원자력정책실의 기사 분류 담당자다.
아래 기사들을 회사 현안 체계로 분류한다.

대분류(domain)는 아래 아홉 중 **정확히 하나**. 세부 태그(tags)는 그 대분류에
속한 목록에서만 **1~2개**.

{CATALOG}

경계 규칙:
{RULE_TEXT}

목록에 마땅한 것이 없으면 tags 를 비우고 suggested 에 제안 낱말 하나를 적는다.
대분류는 반드시 아홉 중 하나를 고른다.

출력은 정확히 아래 JSON 하나. 다른 텍스트·펜스 금지.
{{"items": [{{"idx": 0, "domain": "...", "tags": ["..."], "suggested": null, "why": "판정 근거 15자 이내"}}]}}"""


def pick_samples(limit: int = 30) -> list[dict]:
    """경계가 애매한 것 위주로 고른다 — 쉬운 것만 넣으면 게이트가 무의미하다."""
    rows = json.loads((REPO / "web/public/data/issues.json").read_text(encoding="utf-8"))
    hard, rest = [], []
    KEY = ("수출", "체코", "폴란드", "대미", "해외", "합작", "지분", "협력", "정상",
           "협정", "외교", "SMR", "신규", "건설", "계속운전", "수명", "정책", "전기본")
    for r in rows:
        title = r.get("title") or ""
        (hard if any(k in title for k in KEY) else rest).append(r)
    random.seed(20260911)
    chosen = random.sample(hard, min(limit - 6, len(hard)))
    chosen += random.sample(rest, min(6, len(rest)))
    return chosen


def main() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY 없음")
    samples = pick_samples()
    out = []
    for start in range(0, len(samples), 10):
        chunk = samples[start:start + 10]
        payload = [
            {"idx": i, "title": r.get("title", ""), "summary": (r.get("summary") or "")[:160]}
            for i, r in enumerate(chunk)
        ]
        # 하루 1회짜리 검증 호출을 크롤 큐레이션과 같은 버킷에 두지 않는다.
        result = gemini_client.call_json(
            SYSTEM,
            json.dumps({"items": payload}, ensure_ascii=False),
            # thinking 이 출력 예산을 먹어 MAX_TOKENS 로 잘렸다(실측 thoughts=3125).
            # 3.x 계열은 thinking_budget=0 을 400 으로 거부하므로 0 이 아닌 값으로 묶는다.
            max_output_tokens=16384,
            thinking_budget=1024,
            label="tag-sample",
            model=os.environ.get("GEMINI_REVIEW_MODEL", "gemini-3.5-flash"),
        )
        for item in result.get("items", []):
            row = chunk[item["idx"]]
            out.append({**item, "title": row.get("title", ""),
                        "old_topics": row.get("topics", [])})
        print(f"  {start + len(chunk)}/{len(samples)} 판정")

    valid = {d["id"]: set(d["tags"]) for d in DOMAINS}
    lines = ["# D6 표본 검증 — 실기사 30건\n",
             "판정이 이상한 줄에 표시해 주면 그 줄 기준으로 프롬프트·경계 규칙을 고칩니다.\n",
             "| # | 대분류 | 세부 태그 | 근거 | 제목 | (참고) 기존 topics |",
             "|---|---|---|---|---|---|"]
    bad = 0
    for n, item in enumerate(out, 1):
        dom, tags = item.get("domain", ""), item.get("tags") or []
        flag = ""
        if dom not in valid:
            flag, bad = " ⚠️목록밖", bad + 1
        elif any(t not in valid[dom] for t in tags):
            flag, bad = " ⚠️태그불일치", bad + 1
        sug = f" (제안: {item['suggested']})" if item.get("suggested") else ""
        lines.append(
            f"| {n} | **{dom}**{flag} | {' · '.join(tags) or '—'}{sug} | {item.get('why','')} "
            f"| {item['title'][:46]} | {', '.join(item['old_topics'][:2])} |"
        )
    lines.append(f"\n스키마 위반 {bad}/{len(out)}건")
    from collections import Counter
    dist = Counter(i.get("domain", "") for i in out)
    lines.append("\n## 분포\n")
    for k, v in dist.most_common():
        lines.append(f"- {k}: {v}건")
    (REPO / "docs/2026-09-11-d6-tag-sample.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n저장: docs/2026-09-11-d6-tag-sample.md (스키마 위반 {bad}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
