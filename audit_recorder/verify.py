"""정리본(docx)을 자막 원본(raw_events.jsonl)과 기계적으로 대조한다 — Claude 정리를 믿기 전에 돌리는 검산.

  python verify.py <정리본.docx> <회의폴더> [<회의폴더> ...]

정리본을 '질의 단위'로 나눈다: 'HH:MM~HH:MM'(90분 이하) 이 든 줄이 새 질의의 시작이고, 그 줄에
'기노위'·'산자위' 가 있으면 그 위원회 폴더만 본다. 각 질의의 내용은 그 시간대 ±5분 자막하고만 대조한다.

검사 4가지 (결과는 정리본 옆 <이름>_검증.md):
  ① 숫자 — 질의 안의 수치가 그 시간대 자막에 있는가 (있지만 단위만 다르면 '단위 오인식'으로 따로)
  ② 이름 — 'OOO 의원/장관/차관/간사' 이름이 그 시간대 자막에 나오는가
  ③ 인용 — 따옴표 안 문장이 그 시간대 자막과 3글자 조각 기준 70% 이상 겹치는가
  ④ 누락 — 원자력 키워드가 2번 이상 나온 10분 구간 중 어느 질의에도 안 걸린 구간
통과 = '자막과 어긋나지 않는다'. 자막 자체의 오인식은 영상·회의록으로만 잡힌다.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from docx import Document

NUC = re.compile(r"원전|원자력|한수원|공론화|전기본|정기본|SMR|고리|한빛|한울|월성|새울|사용후\s*핵연료|고준위|계속\s*운전|핵발전")
NUM = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?(?:만\s?\d(?:[\d,]*\d)?)?\s?(?:GW|TWh|GWh|MW|kV|%|조|억|만|t|명|기|건|곳|개월|배)")
NAME = re.compile(r"(?:^|(?<=[\s(·,↔\-]))([가-힣]{2,3})\s(?:의원|위원장|장관|차관|간사)")
QUOTE = re.compile(r"[\"“‘]([^\"”’\n]{8,160})[\"”’]|(?<![\d\w])'([^'\n]{8,160})'")
TIME = re.compile(r"(\d{2}):(\d{2})~(\d{2}):(\d{2})")
STAMP = re.compile(r"^\((\d{2}):(\d{2})\)$")   # 회사 양식 시각 줄 '(HH:MM)'
SKIP_NAMES = {"주요", "기노", "산자", "여당", "야당", "여야", "답변", "질의", "해당", "담당", "관계", "전문", "자문", "국민", "상임", "보충", "마지막"}
CMTE = {"기노위": "기후", "기후노동위": "기후", "기후에너지환경노동위": "기후", "산자위": "산자", "산중위": "산자",
        "산자중기위": "산자", "산업통상자원중소벤처기업위원회": "산자", "과방위": "과방", "과학기술정보방송통신위원회": "과방"}
PAD = 5


def norm(s):
    return re.sub(r"[\s,'·\-–]", "", s)


def mins(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def load_raw(dirs):
    lines = []   # (분, 위원회폴더명, 문장)
    for d in dirs:
        for l in open(Path(d) / "raw_events.jsonl", encoding="utf-8"):
            try:
                ev = json.loads(l)
                m = json.loads(ev["raw"])
            except Exception:
                continue
            for x in m.get("transcripts") or [[None, m.get("transcript", "")]]:
                t = str(x[1]).lstrip("- ").strip()
                if t:
                    lines.append((mins(ev["captured_at"][11:16]), Path(d).name, t))
    return lines


def shingles(s, n=3):
    s = norm(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def sections(paras, doc_key=None):
    """→ [(라벨, 시작분, 끝분, 위원회키, [문단...])]. 시간 없는 앞부분은 하루 전체."""
    out = [("(시간 미지정 앞부분)", 0, 24 * 60, None, [])]
    stamps = [i for i, p in enumerate(paras) if STAMP.match(p)]
    for i, p in enumerate(paras):
        m = STAMP.match(p)
        if m:   # 회사 양식 '(HH:MM)' 한 줄 = 시작 시각만 → 다음 시각까지(70분 넘으면 +20분)
            # ponytail: 끝 시각 추정치. 질의 사이에 다른 질의가 끼면 창이 넓어짐, 끝 시각을 양식에 넣으면 해결
            a = mins(f"{m[1]}:{m[2]}")
            nxt = [mins(f"{STAMP.match(paras[j])[1]}:{STAMP.match(paras[j])[2]}") for j in stamps if j > i]
            b = nxt[0] if nxt and 0 < nxt[0] - a <= 70 else a + 20
            out.append((p, a, b, doc_key, [p]))
            continue
        m = TIME.search(p)
        if m:
            a, b = mins(f"{m[1]}:{m[2]}"), mins(f"{m[3]}:{m[4]}")
            if 0 <= b - a <= 90:
                key = next((v for k, v in CMTE.items() if k in p), None) or doc_key
                title = []
                prev = out[-1][4]
                if prev and not prev[-1].startswith(("□", "ㅇ", "Q", "A", "→", "(", "[", "※")) and len(prev[-1]) < 90:
                    title = [prev.pop()]   # 시간 줄 바로 앞의 제목 줄은 새 질의 소속
                out.append((p[:50], a, b, key, title + [p]))
                continue
        out[-1][4].append(p)
    return out


def main(docx_path, dirs):
    lines = load_raw(dirs)
    paras = [p.text for p in Document(docx_path).paragraphs if p.text.strip()]
    findings = defaultdict(list)   # 검사 → [(질의라벨, 내용)]
    covered = defaultdict(set)     # 위원회키 → 분
    keys = {v for k, v in CMTE.items() if any(k in p for p in paras[:3])}
    doc_key = keys.pop() if len(keys) == 1 else None   # 표지에 위원회가 하나뿐이면 문서 전체 기본값
    for label, a, b, key, ps in sections(paras, doc_key):
        win = [t for m, d, t in lines if a - PAD <= m <= b + PAD and (key is None or key in d)]
        if not win:
            continue
        corpus, corpus_sh = norm(" ".join(win)), shingles(" ".join(win))
        if key:
            covered[key].update(range(a - PAD, b + PAD + 1))
        text = "\n".join(ps)
        for m in NUM.finditer(text):
            tok = m.group(0).strip()
            if norm(tok) in corpus:
                continue
            core = norm(re.sub(r"[^\d.만]", " ", tok).strip())
            findings["num_unit" if core and core in corpus else "num"].append((label, tok))
        for n in set(NAME.findall(text)):
            if n not in SKIP_NAMES and n[-1] not in "는은이가을를의며다고로서" and n not in corpus:
                findings["name"].append((label, n))
        for q in (x or y for x, y in QUOTE.findall(text)):
            sh = shingles(q)
            if sh and len(sh & corpus_sh) / len(sh) < 0.7:
                findings["quote"].append((label, f"{round(len(sh & corpus_sh) / len(sh) * 100)}% · {q[:60]}"))

    span = [m for m in covered.values() for m in m]
    lo, hi = (min(span), max(span)) if span else (0, 24 * 60)
    buckets = defaultdict(list)
    for m, d, t in lines:
        if NUC.search(t) and lo <= m <= hi:
            buckets[(d, m // 10 * 10)].append(t)
    for (d, k), v in sorted(buckets.items()):
        key = next((v_ for v_ in CMTE.values() if v_ in d), None)
        if len(set(v)) >= 2 and not any(x in covered.get(key, ()) for x in range(k, k + 10)):
            findings["missed"].append((d, f"{k // 60:02d}:{k % 60:02d}대 ({len(v)}회) {v[0][:70]}"))

    titles = {"num": "① 그 시간대 자막에 없는 숫자 (기사 등 자막 밖 출처면 정상, 아니면 오류)",
              "num_unit": "①-2 숫자는 있는데 단위가 다른 것 (음성인식 단위 오인식이면 정상)",
              "name": "② 그 시간대 자막에 없는 이름 (음성인식 변형이면 영상·기사로 확인)",
              "quote": "③ 그 시간대 자막과 덜 겹치는 인용 (요약·교정한 인용이면 정상)",
              "missed": "④ 어느 질의에도 안 걸린 원자력 언급 구간"}
    out = [f"# 검증: {Path(docx_path).name}", "", f"자막 {len(lines)}줄 · 질의별 ±{PAD}분 대조 · 통과 = 자막과 어긋나지 않음(사실 보증 아님)", ""]
    for k, title in titles.items():
        out += [f"## {title}: {len(findings[k])}건", ""] + [f"- [{lab}] {x}" for lab, x in findings[k]] + [""]
    report = Path(docx_path).with_name(Path(docx_path).stem + "_검증.md")
    report.write_text("\n".join(out), encoding="utf-8")
    print(" · ".join(f"{k} {len(findings[k])}" for k in titles), "→", report)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main(sys.argv[1], sys.argv[2:])
