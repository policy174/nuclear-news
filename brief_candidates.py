#!/usr/bin/env python3
"""금요일 영상 브리핑 후보 — 주간 리포트(weekly_reports.json)에서 다음 주 영상 씨앗을 뽑아 DM 으로.

  python brief_candidates.py            최신 주차 후보를 텔레그램 DM(--ops)으로
  python brief_candidates.py --dry-run  보내지 않고 본문만 출력

씨앗은 둘 다다(지니 2026-09-21): 보고서 후보(report_candidates)가 있으면 그것, 없으면 그 주
정책 변화(policy_shifts). 억지로 만들지 않는다 — 둘 다 비면 "후보 없음" 한 줄만 보낸다.
weekly.yml 이 weekly_bot.py 뒤에 부른다. 실패는 비치명(주간 리포트 커밋을 막지 않는다).
"""
import json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent
SITE = "https://nuclens.pages.dev"


def latest(reports):
    if not reports:
        return None, None
    k = sorted(reports)[-1]
    return k, reports[k]


def build(week_id, w):
    lines = ["📹 다음 주 영상 브리핑 후보 — %s (%s~%s)" % (week_id, w.get("week_start", ""), w.get("week_end", ""))]
    # evidence_hashes 는 기사 hash8 이다(이슈 id 가 아니다) — key_events 로 제목을 되찾아 적는다
    head = {e.get("hash"): e.get("headline", "") for e in (w.get("key_events") or []) if e.get("hash")}
    n = 0
    for c in w.get("report_candidates") or []:
        n += 1
        lines.append("%d. [보고서] %s" % (n, c.get("topic", "").strip()))
        if c.get("basis"):
            lines.append("   근거: %s" % c["basis"].strip())
    for p in w.get("policy_shifts") or []:
        n += 1
        lines.append("%d. [정책 변화] %s" % (n, p.get("what", "").strip()))
        if p.get("so_what"):
            lines.append("   함의: %s" % p["so_what"].strip())
        titles = [head[h] for h in (p.get("evidence_hashes") or []) if head.get(h)]
        if titles:
            lines.append("   기사: " + " / ".join(titles[:2]))
    if n == 0:
        lines.append("후보 없음 — 이번 주는 억지로 만들지 않는다.")
    else:
        lines.append("")
        lines.append("시작: 클로드에 「/brief %s 번호」 (대본 승인 → 화면 승인 → 발행)" % week_id)
    return "\n".join(lines)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = ROOT / "weekly_reports.json"
    if not p.exists():
        print("weekly_reports.json 없음 — 후보 스킵")
        return 0
    week_id, w = latest(json.loads(p.read_text(encoding="utf-8")).get("reports") or {})
    if not w:
        print("주간 리포트 비어 있음 — 후보 스킵")
        return 0
    text = build(week_id, w)
    if "--dry-run" in argv:
        print(text)
        return 0
    r = subprocess.run([sys.executable, str(ROOT / "telegram_send.py"), "--ops", "--plain", text])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
