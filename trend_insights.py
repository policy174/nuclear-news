"""
트렌드 흐름 해석 — "이 키워드가 왜 많이 나오는지"를 근거 기사로만 서술.

문제: 웹 트렌드 화면이 키워드 횟수만 보여줌 ("계속운전 8회"). 사용자는 그 숫자가
어떤 사건들로 구성됐고 어느 방향으로 움직이는지를 알고 싶어함.

해결:
  - 최근 7일 아카이브에서 상위·급상승 키워드를 뽑고, 키워드별 근거 기사
    (제목·요약)를 모아 Gemini 1회 배치 호출로 '흐름 서술'을 생성.
  - trend_insights.json 으로 저장 → 웹 build_data.py 가 그대로 노출.
  - daily-brief 워크플로에서 하루 1회 실행 (Gemini 호출 +1/일).

가드레일 (기획문서 'AI 정책 판단 금지' 원칙과의 경계):
  - 전망·예측·권고·투자판단 금지. 근거 기사에 있는 사실의 '구성과 방향'만 서술.
  - 근거 기사 hash 를 함께 저장 — 웹에서 원문 링크로 검증 가능.
  - Gemini 키 없거나 실패 시: 기존 파일 유지, 봇 본연 동작에 영향 0.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gemini_client import GeminiError, call_json, is_available

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = Path(__file__).parent
OUT_FILE = BASE / "trend_insights.json"
KST = timezone(timedelta(hours=9))

MAX_KEYWORDS = 6          # 해석 대상 키워드 수 (상위 + 급상승 합산)
MAX_EVIDENCE = 8          # 키워드당 근거 기사 수
MIN_COUNT = 3             # 해석 대상 최소 언급 횟수

SYSTEM_PROMPT = """당신은 한국수력원자력 정책부서의 시니어 정책분석관입니다.
원자력·에너지 뉴스 키워드별로, 첨부된 근거 기사들만 바탕으로 '이 흐름이 무엇으로
구성되어 있고 어느 방향으로 움직이는지'를 서술합니다.

[원칙 — 반드시 준수]
- 근거 기사에 없는 내용 추가 금지 (환각 금지). 배경지식으로 살을 붙이지 말 것.
- 미래 예측·전망·권고·투자 판단 금지. "~할 전망", "~해야 한다", "~가 유망" 금지.
- 허용되는 서술: 사건의 나열과 묶음, 공통점, 국내/해외 구분, 시간적 전개.
  예: "국내 고리2호기 재가동 심사와 미국의 80년 운전 승인 논의가 같은 주에 겹치며,
  국내외 모두 기존 원전의 수명연장 절차가 흐름의 중심."
- 각 키워드당 2~3문장, 한국어, 분석관 보고 톤 (개조식 아님, 자연문).
- 근거가 빈약하면 억지로 쓰지 말고 direction 을 빈 문자열로.

[출력 — JSON 한 객체만]
{"items": [{"keyword": "...", "direction": "...", "evidence_idx": [0, 2]}]}
- evidence_idx: 서술의 근거가 된 기사 번호 목록 (실제 참조한 것만)
"""


def load_recent_articles(days: int = 7) -> list[dict]:
    cutoff = (datetime.now(KST) - timedelta(days=days + 7)).strftime("%Y-%m")  # 월 경계 대비 2개 파일
    arts = []
    for path in sorted((BASE / "archive").glob("*.jsonl")):
        if path.stem < cutoff[:7]:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            # market(증시성) 제외 — 웹은 투자 시그널을 주지 않는다는 원칙 (목표주가 등 유입 방지)
            if r.get("importance") in ("", "noise", "market"):
                continue
            arts.append(r)
    return arts


def _date_of(r: dict) -> str:
    for k in ("pub", "archived_at"):
        v = r.get(k) or ""
        try:
            return datetime.fromisoformat(v).astimezone(KST).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def pick_keywords(arts: list[dict]) -> list[dict]:
    """최근 7일 상위 + 급상승 키워드 (자유 태그 기준, 트렌드 화면과 동일 축)."""
    now = datetime.now(KST)
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    d14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    cur, prev = Counter(), Counter()
    by_tag: dict[str, list[dict]] = {}
    for r in arts:
        d = _date_of(r)
        tags = [t.lstrip("#") for t in (r.get("tags") or []) if isinstance(t, str) and t.strip("#")]
        if d >= d7:
            cur.update(tags)
            for t in tags:
                by_tag.setdefault(t, []).append(r)
        elif d >= d14:
            prev.update(tags)

    ranked = sorted(cur.items(), key=lambda kv: (kv[1], kv[1] - prev.get(kv[0], 0)), reverse=True)
    out = []
    for tag, n in ranked:
        if n < MIN_COUNT or len(out) >= MAX_KEYWORDS:
            break
        evid = sorted(by_tag[tag], key=_date_of, reverse=True)[:MAX_EVIDENCE]
        out.append({"keyword": tag, "count_now": n, "count_prev": prev.get(tag, 0), "articles": evid})
    return out


def build_user_message(keywords: list[dict]) -> str:
    parts = []
    for kw in keywords:
        lines = [f"## 키워드: {kw['keyword']} (이번 주 {kw['count_now']}회, 전주 {kw['count_prev']}회)"]
        for i, a in enumerate(kw["articles"]):
            lines.append(f"[{i}] ({_date_of(a)}) {a.get('title_kr') or a.get('title', '')}"
                         f" — {a.get('summary', '')}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def generate() -> bool:
    if not is_available():
        print("[insights] GEMINI_API_KEY 없음 — 스킵 (기존 파일 유지)")
        return False
    arts = load_recent_articles()
    keywords = pick_keywords(arts)
    if not keywords:
        print("[insights] 해석 대상 키워드 없음 (7일 데이터 부족)")
        return False

    try:
        # 2.5-flash 는 thinking 토큰이 출력 예산을 잠식 → 2048이면 JSON이 중간에 끊김 (실측)
        result = call_json(SYSTEM_PROMPT, build_user_message(keywords),
                           temperature=0.2, max_output_tokens=8192)
    except GeminiError as e:
        print(f"[insights] Gemini 실패 — 기존 파일 유지: {e}")
        return False

    by_kw = {it.get("keyword"): it for it in result.get("items", []) if isinstance(it, dict)}
    items = []
    for kw in keywords:
        r = by_kw.get(kw["keyword"], {})
        direction = (r.get("direction") or "").strip()
        idxs = [i for i in (r.get("evidence_idx") or []) if isinstance(i, int) and 0 <= i < len(kw["articles"])]
        evidence = [{
            "hash": a.get("hash", ""),
            "title_kr": a.get("title_kr") or a.get("title", ""),
            "url": a.get("url", ""),
            "date": _date_of(a),
        } for a in (kw["articles"][i] for i in idxs)]
        items.append({
            "keyword": kw["keyword"],
            "count_now": kw["count_now"],
            "count_prev": kw["count_prev"],
            "direction": direction,
            "evidence": evidence,
        })

    OUT_FILE.write_text(json.dumps({
        "generated_at": datetime.now(KST).isoformat(),
        "window": "7d",
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    filled = sum(1 for i in items if i["direction"])
    print(f"[insights] 키워드 {len(items)}개 중 {filled}개 해석 생성 → {OUT_FILE.name}")
    return True


if __name__ == "__main__":
    generate()
