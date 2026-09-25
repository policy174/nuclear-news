"""markdown → 자료실 정적 문서(HTML) 변환기.

용도: docs/precedents/*.md(손큐레이션 심층 분석)를 web/public/docs/*.html 로.
외부 markdown 라이브러리 없이 이 저장소가 쓰는 부분집합만 다룬다 —
제목(#~####)·문단·목록(-·1.)·표(|)·인용(>)·구분선·**굵게**·`코드`·[링크](url).
그 밖의 문법은 문단으로 그대로 나간다(잘못된 통과보다 눈에 띄는 실패).

사용: python tools/md2doc.py docs/precedents/shingori-2017.md web/public/docs/shingori-2017.html
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

INLINE = [
    (re.compile(r"`([^`]+)`"), lambda m: f"<code>{m.group(1)}</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), lambda m: f"<strong>{m.group(1)}</strong>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)"),
     lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'),
    # 같은 폴더의 자료실 문서끼리 잇는 상대 링크만 허용
    (re.compile(r"\[([^\]]+)\]\(([\w-]+\.html(?:#[\w가-힣-]*)?)\)"),
     lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>'),
]


def inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pat, fn in INLINE:
        out = pat.sub(fn, out)
    return out


def slug(text: str, seen: set) -> str:
    base = re.sub(r"[^\w가-힣]+", "-", text).strip("-").lower() or "s"
    s, n = base, 2
    while s in seen:
        s, n = f"{base}-{n}", n + 1
    seen.add(s)
    return s


def convert(md: str) -> tuple[str, str, list[tuple[int, str, str]]]:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    seen: set = set()
    title = ""
    i = 0
    para: list[str] = []

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush_para(); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_para()
            lvl, text = len(m.group(1)), m.group(2).strip()
            if lvl == 1 and not title:
                title = text
            sid = slug(text, seen)
            if lvl in (2, 3):
                toc.append((lvl, text, sid))
            out.append(f'<h{lvl} id="{sid}">{inline(text)}</h{lvl}>')
            i += 1; continue
        if s.startswith("|"):
            flush_para()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip()); i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            body = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "--") for x in c)]
            if not body:
                continue
            head, rest = body[0], body[1:]
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rest)
            out.append(f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            flush_para()
            ordered = m.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append(mm.group(3)); i += 1
                # 들여쓴 연속 줄은 같은 항목에 붙인다
                while i < len(lines) and lines[i].startswith("  ") and not re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
                    items[-1] += " " + lines[i].strip(); i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if s.startswith(">"):
            flush_para()
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip()); i += 1
            out.append(f"<blockquote><p>{inline(' '.join(q))}</p></blockquote>")
            continue
        if re.fullmatch(r"-{3,}|\*{3,}", s):
            flush_para(); out.append("<hr>"); i += 1; continue
        para.append(s); i += 1
    flush_para()
    return title, "\n".join(out), toc


TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} — Nuclens 자료실</title>
<style>
@font-face {{ font-family: "Pretendard Variable"; src: url("../fonts/pretendard/v1.3.9/PretendardVariable.subset.woff2") format("woff2-variations"); font-weight: 45 920; font-display: swap; }}
:root {{ --c-primary:#12294c; --c-secondary:#204a8f; --c-bg:#eef1f4; --c-surface:#fff; --c-border:#d7dde4; --c-text:#1a1c1f; --c-text-2:#4a5058; --c-signal:#e6edf7; --c-signal-ink:#1a3a6b; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--c-bg); color:var(--c-text); font-family:"Pretendard Variable",Pretendard,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; font-size:16px; line-height:1.65; word-break:keep-all; }}
header {{ background:var(--c-primary); color:#fff; padding:14px 16px; }}
header a {{ color:#fff; text-decoration:none; font-weight:600; }}
header small {{ display:block; opacity:.8; margin-top:2px; }}
main {{ max-width: 920px; margin: 0 auto; padding: 16px; }}
article {{ background:var(--c-surface); border:1px solid var(--c-border); padding: 20px 20px 32px; }}
h1 {{ font-size:1.6rem; line-height:1.3; margin:0 0 6px; color:var(--c-primary); }}
h2 {{ font-size:1.25rem; margin:36px 0 10px; padding-top:14px; border-top:2px solid var(--c-primary); color:var(--c-primary); }}
h3 {{ font-size:1.05rem; margin:24px 0 8px; color:var(--c-signal-ink); }}
h4 {{ font-size:1rem; margin:18px 0 6px; }}
p {{ margin:0 0 10px; }}
li {{ margin:2px 0; }}
blockquote {{ margin:10px 0; padding:8px 14px; background:var(--c-signal); color:var(--c-signal-ink); border-left:3px solid var(--c-secondary); }}
blockquote p {{ margin:0; }}
.tbl {{ overflow-x:auto; margin:8px 0 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:.92rem; }}
th, td {{ border:1px solid var(--c-border); padding:6px 8px; vertical-align:top; text-align:left; }}
th {{ background:var(--c-signal); color:var(--c-signal-ink); white-space:nowrap; }}
code {{ font-family:ui-monospace,Consolas,monospace; font-size:.9em; background:var(--c-bg); padding:0 4px; }}
a {{ color:var(--c-secondary); }}
nav.toc {{ background:var(--c-bg); border:1px solid var(--c-border); padding:12px 16px; margin:14px 0 24px; font-size:.95rem; }}
nav.toc summary {{ cursor:pointer; font-weight:600; }}
nav.toc ul {{ margin:8px 0 0; padding-left:18px; }}
article {{ overflow-wrap:anywhere; }}
.meta {{ color:var(--c-text-2); font-size:.92rem; margin-bottom:12px; }}
@media (max-width:600px) {{ article {{ padding:14px 12px 24px; }} h1 {{ font-size:1.35rem; }} }}
</style>
</head>
<body>
<header><a href="../?view=report">← Nuclens 보고서 · 대응 자료실</a><small>{title}</small></header>
<main><article>
{body}
</article></main>
</body>
</html>
"""


def main(src: str, dst: str) -> None:
    title, body, toc = convert(Path(src).read_text(encoding="utf-8"))
    # 목차는 장(h2)만, 접힌 상태로 — 첫 화면은 제목과 요약이 차지해야 한다
    items = "".join(f'<li><a href="#{sid}">{html.escape(text)}</a></li>' for lvl, text, sid in toc if lvl == 2)
    nav = f'<nav class="toc" aria-label="목차"><details><summary>목차 ({sum(1 for t in toc if t[0] == 2)}개 장)</summary><ul>{items}</ul></details></nav>'
    # 첫 h1 뒤 머리말 인용(있으면)까지 지나서 목차를 꽂는다
    cut = body.find("</blockquote>")
    cut = cut + len("</blockquote>") if cut != -1 and cut < body.find("<h2") else body.find("</h1>") + len("</h1>")
    body = body[:cut] + nav + body[cut:]
    Path(dst).write_text(TEMPLATE.format(title=html.escape(title or Path(src).stem), body=body),
                         encoding="utf-8")
    print(f"{dst}: {len(body)} chars, toc {len(toc)}")


if __name__ == "__main__":
    if len(sys.argv) == 1:  # 자체 점검
        t, b, toc = convert("# 제목\n\n## 절\n\n- 가 **나** [링크](https://x.y)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        assert t == "제목" and "<strong>나</strong>" in b and "<table>" in b and toc == [(2, "절", "절")], b
        _, b2, _ = convert("[노트](a-b.html) [x](javascript:alert(1))")
        assert '<a href="a-b.html">노트</a>' in b2 and 'href="javascript' not in b2, b2
        print("md2doc self-check ok")
    else:
        main(sys.argv[1], sys.argv[2])
