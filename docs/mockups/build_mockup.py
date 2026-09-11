# -*- coding: utf-8 -*-
"""D1 홈·상세 시안 생성기 — 실데이터 9건.

시안이지 구현이 아니다. 빌드·분류 파이프라인을 타지 않고, 라이브
`data/issues.json` 에서 고른 9건을 그대로 박아 정적 HTML 을 낸다.
D2 시각 검증에서 확정된 것만 web/public 에 구현한다.

표본은 예쁜 것이 아니라 나쁜 것으로 골랐다 — 제목 27~52자, 기사 1~33건,
detail 유/무, 시사점 유/무, 국내·해외·다국가, 한 제목에 사건 둘,
끝 단어가 의미를 뒤집는 것(승인 중단).

실행: python docs/mockups/build_mockup.py <picks.json>
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
CSS_REL = "../../web/public/style.css"

# ── 요지 선택 ────────────────────────────────────────────────────────────
# build_data._is_restatement 는 쓰지 않는다. 2026-09-11 실측:
#   · 같은 사실을 다르게 쓴 두 요약   → False (반복을 못 잡음)
#   · "승인을 중단했다"↔"승인을 재개했다" → True  (정반대인데 중복 판정)
# 삭제 필터로 쓰면 의미가 뒤집힌 사실이 사라진다. 그래서 **지우지 않고
# 고른다** — 못 고른 요약은 4단 타임라인에 그대로 남으므로 정보 손실이 없다.
NUM = re.compile(r"\d[\d,.]*\s*(?:%|년|월|일|기|호기|억|조|만|kW|MW|GW|달러|원)?")
WORD = re.compile(r"[가-힣A-Za-z]{2,}")
STOP = set("그리고 그러나 위해 관련 대한 통해 이번 해당 지난 오는 전망 계획 방침 "
           "예정 것으로 등이 등을 밝혔다 있다 했다 된다 한다".split())
# 표본 9건 임계값 스윕(2026-09-11). 3 은 표현 차이를 새 정보로 착각해
# 프랑스/네덜란드 건이 같은 사실을 세 번 반복했다. 14 는 그 반복을 1 개로
# 줄이면서 새울(3)·한미(3)의 서로 다른 사실은 지킨다. 대가는 중국 ESS 건이
# 2→1 로 줄며 '왜 중단했나'(과잉생산·가격폭락)를 잃는 것.
# **표본 9건에 맞춘 값이라 확정이 아니다 — D2 판단 대상.**
MIN_NEW_TOKENS = 14


def _tokens(text: str) -> set[str]:
    out = set(NUM.findall(text or ""))
    out |= {w for w in WORD.findall(text or "") if w not in STOP}
    return out


def pick_gists(members: list[dict], limit: int = 3) -> tuple[list[dict], list[str]]:
    rows = [m for m in members if (m.get("summary") or "").strip()]
    if not rows:
        return [], []
    chosen, seen = [rows[0]], _tokens(rows[0]["summary"])
    trace = [f"1. 대표 — {rows[0].get('publisher', '')}"]
    rest = rows[1:]
    while len(chosen) < limit and rest:
        scored = sorted(
            ((len(_tokens(m["summary"]) - seen), -int(m.get("source_tier") or 9), i, m)
             for i, m in enumerate(rest)),
            key=lambda x: (-x[0], -x[1]),
        )
        count, _, index, best = scored[0]
        if count < MIN_NEW_TOKENS:
            trace.append(f"중단 — 남은 요약의 신규 정보가 {count}개뿐")
            break
        chosen.append(best)
        seen |= _tokens(best["summary"])
        rest.pop(index)
        trace.append(f"{len(chosen)}. {best.get('publisher', '')} — 신규 정보 {count}개")
    return chosen, trace


# ── 표시 헬퍼 ────────────────────────────────────────────────────────────
def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def issue_slug(row: dict) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", row.get("issue_id") or "") or "issue"


def chips(row: dict) -> str:
    """대분류 + 주제태그. 같은 뜻이면 태그를 접는다."""
    domain = row.get("_domain") or ""
    tags = [t for t in (row.get("_domain_tags") or []) if t and t != domain]
    parts = [f'<span class="chip chip--domain" data-domain="{esc(domain)}">{esc(domain)}</span>']
    parts += [f'<span class="chip chip--tag">{esc(t)}</span>' for t in tags]
    return "".join(parts)


def day_label(value: str) -> str:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    return f"{int(match.group(2))}월 {int(match.group(3))}일" if match else ""


def row_meta(row: dict) -> str:
    rep = row.get("representative_article") or {}
    bits = [rep.get("publisher") or ""]
    count = row.get("article_count") or 0
    if count > 1:
        bits.append(f"기사 {count}건")
    bits.append(day_label(row.get("last_seen") or ""))
    return " · ".join(b for b in bits if b)


# ── 홈 ───────────────────────────────────────────────────────────────────
def toc_row(row: dict) -> str:
    return f"""      <a class="toc-row" href="{esc(issue_slug(row))}.html" data-id="{esc(issue_slug(row))}">
        <span class="toc-chips">{chips(row)}</span>
        <span class="toc-title">{esc(row.get('title'))}</span>
        <span class="toc-meta">{esc(row_meta(row))}</span>
      </a>"""


def continuing_row(row: dict) -> str:
    change = (row.get("latest_change") or "").split("→")[-1].strip()
    return f"""      <a class="cont-row" href="{esc(issue_slug(row))}.html">
        <span class="cont-title">{esc(row.get('title'))}</span>
        <span class="cont-change">{esc(change[:90])}</span>
      </a>"""


def build_home(picks: list[dict], continuing: list[dict], stamp: str) -> str:
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>시안 · 오늘의 원전 현안</title>
<link rel="stylesheet" href="{CSS_REL}">
<link rel="stylesheet" href="mockup.css">
<body class="mk">
{banner('home')}
<main class="mk-wrap">
  <header class="mk-head">
    <div class="mk-head-row">
      <h1>오늘의 원전 현안</h1>
      <span class="mk-date">{esc(stamp)}</span>
    </div>
    <a class="mk-search" href="#">현안 검색 · 지난 자료 찾기</a>
  </header>

  <nav class="toc" aria-label="오늘의 현안 목차">
{chr(10).join(toc_row(r) for r in picks)}
  </nav>

  <section class="cont" aria-labelledby="contHead">
    <h2 id="contHead">이어지는 현안</h2>
{chr(10).join(continuing_row(r) for r in continuing)}
  </section>

  <p class="mk-status">수집 176건 · 검증 완료 · 07:25</p>
</main>
<script src="mockup.js"></script>
</body>
"""


# ── 상세 ─────────────────────────────────────────────────────────────────
def build_issue(row: dict) -> str:
    gists, trace = pick_gists(row.get("related_articles") or [])
    why = (row.get("implication") or "").strip() or (row.get("why_important") or "").strip()
    articles = row.get("related_articles") or []
    verification = row.get("verification") or {}

    gist_html = "".join(
        f"""        <li><span class="gist-text">{esc(m.get('summary'))}</span>
          <span class="gist-src">{esc(m.get('publisher'))}</span></li>"""
        for m in gists
    )
    why_html = ""
    if why:
        why_html = f"""    <section class="stage">
      <h2>왜 중요한가</h2>
      <p class="why">{esc(why)}</p>
    </section>"""

    source_html = "".join(
        f"""        <li><a href="{esc(a.get('url'))}" target="_blank" rel="noopener">{esc(a.get('title_kr') or a.get('publisher'))}</a>
          <span class="src-meta">{esc(a.get('publisher'))} · {esc(day_label(a.get('article_date') or ''))}</span></li>"""
        for a in articles[:6]
    )
    timeline_html = "".join(
        f"""        <li><b>{esc(day_label(a.get('article_date') or ''))}</b>
          {esc(a.get('title_kr') or '')}
          <span class="src-meta">{esc(a.get('publisher'))}</span></li>"""
        for a in articles
    )
    detail = (row.get("detail") or "").strip()
    detail_html = f'<p class="detail">{esc(detail)}</p>' if detail else \
        '<p class="detail detail--empty">원문 본문을 수집하지 못한 기사다. 요지와 원문 링크로만 확인할 수 있다.</p>'

    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>시안 · {esc(row.get('title'))}</title>
<link rel="stylesheet" href="{CSS_REL}">
<link rel="stylesheet" href="mockup.css">
<body class="mk">
{banner('issue')}
<main class="mk-wrap mk-wrap--issue">
  <a class="back" href="index.html">← 오늘 현안으로</a>

  <article>
    <div class="mk-chips">{chips(row)}</div>
    <h1 class="issue-title">{esc(row.get('title'))}</h1>

    <section class="stage">
      <h2>무슨 일이 있었나</h2>
      <ul class="gists">
{gist_html}
      </ul>
      <p class="trace">선택 근거(시안 전용) — {esc(' / '.join(trace))}</p>
    </section>

{why_html}

    <section class="stage">
      <h2>근거 확인</h2>
      <p class="verify">{esc(verification.get('label') or '')} · 연결 기사 {len(articles)}건</p>
      <ul class="sources">
{source_html}
      </ul>
    </section>

    <details class="stage more">
      <summary>더 알아보기 — 배경 설명과 전체 경과</summary>
      {detail_html}
      <h3>경과</h3>
      <ol class="timeline">
{timeline_html}
      </ol>
    </details>
  </article>

  <a class="back back--foot" href="index.html">← 오늘 현안으로</a>
</main>
</body>
"""


def banner(kind: str) -> str:
    extra = ""
    if kind == "home":
        extra = """
    <label><input type="checkbox" id="tgTint"> 대분류 색 틴트</label>
    <label><input type="checkbox" id="tgCompact"> 메타 올리기</label>"""
    return f"""<div class="mk-banner">
    <b>D1 시안</b> · 실데이터 9건 · 구현 아님{extra}
</div>"""


def main() -> int:
    picks = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    continuing = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    (OUT / "index.html").write_text(
        build_home(picks, continuing, "2026년 9월 11일"), encoding="utf-8")
    for row in picks + continuing:
        (OUT / f"{issue_slug(row)}.html").write_text(build_issue(row), encoding="utf-8")
    print(f"홈 1 + 상세 {len(picks) + len(continuing)} 생성")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
