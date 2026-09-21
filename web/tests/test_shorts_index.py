"""영상 브리핑 발행 계약 — shorts/index.json 과 공유 페이지.

배포 경로(deploy-web.yml → web/tests)에서 돈다. 발행기(nuclens-shorts/publish.py)만
검사하면 웹 파일을 직접 고쳐 푸시하는 경로로 우회된다 — 2026-09-21 에 그 경로로
8번 밀었다. 그날 공유 페이지의 인라인 스크립트가 문법 오류로 나간 것도 여기서
잡혔어야 했다(app.js 만 node --check 했다).
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHORTS = ROOT / "public" / "shorts"

REQUIRED = {"episode_id": str, "no": int, "primary_issue_id": str, "issue_ids": list,
            "title": str, "lead": str, "date": str, "file": str, "youtube": str,
            "poster": str, "page": str, "seconds": int, "summary": list}
EPISODE_ID = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+")
ISSUE_ID = re.compile(r"issue-[0-9a-f]{16}")
SHA = re.compile(r"sha256:[0-9a-f]{64}")


def validate(rows, root=SHORTS):
    """줄 목록 → 문제 문장 목록(비어 있으면 통과). 발행기와 가상 편 시험이 같이 쓴다."""
    out, episodes, numbers = [], {}, {}
    for i, row in enumerate(rows):
        w = "shorts[%d] %s" % (i, row.get("episode_id") or row.get("title") or "")
        bad = False
        for key, kind in REQUIRED.items():
            v = row.get(key)
            if v is None or isinstance(v, bool) or not isinstance(v, kind):
                out.append("%s: %s 는 %s 이어야 함(없거나 형이 다름)" % (w, key, kind.__name__))
                bad = True
        if bad:
            continue
        ep, no = row["episode_id"], row["no"]
        if not EPISODE_ID.fullmatch(ep):
            out.append("%s: episode_id 형식은 YYYY-MM-DD-slug" % w)
        if ep in episodes:
            out.append("%s: episode_id 중복(shorts[%d]) — 재발행은 줄을 갱신해야 한다" % (w, episodes[ep]))
        episodes.setdefault(ep, i)
        if no in numbers:
            out.append("%s: no %d 중복(shorts[%d])" % (w, no, numbers[no]))
        numbers.setdefault(no, i)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date"]):
            out.append("%s: date 는 YYYY-MM-DD" % w)
        elif not ep.startswith(row["date"]):
            out.append("%s: episode_id 는 date 로 시작해야 한다" % w)
        ids = row["issue_ids"]
        if not ids or not all(isinstance(x, str) and ISSUE_ID.fullmatch(x) for x in ids):
            out.append("%s: issue_ids 는 issue-<16hex> 목록(1개 이상)" % w)
        elif row["primary_issue_id"] not in ids:
            out.append("%s: primary_issue_id 가 issue_ids 에 없음" % w)
        for key in ("file", "poster"):
            if row[key] and not (root / row[key]).is_file():
                out.append("%s: %s 파일 없음 — %s" % (w, key, row[key]))
        if row["page"] and not (root / row["page"] / "index.html").is_file():
            out.append("%s: page 폴더에 index.html 없음 — %s" % (w, row["page"]))
        if row["youtube"] and not re.fullmatch(r"[\w-]{6,20}", row["youtube"]):
            out.append("%s: youtube 는 영상 ID 만" % w)
        if row["seconds"] <= 0:
            out.append("%s: seconds 는 양수" % w)
        if len(row["lead"]) > 80:
            out.append("%s: lead 80자 초과(카톡이 자른다)" % w)
        summary = row["summary"]
        if not 1 <= len(summary) <= 4 or not all(isinstance(s, str) and s.strip() for s in summary):
            out.append("%s: summary 는 1~4줄 문자열" % w)
        elif any(s.rstrip().endswith(("다.", "요.", "다")) for s in summary):
            out.append("%s: summary 는 개조식 체언 종결(…다 로 끝내지 않는다)" % w)
        approval = row.get("approval")
        if approval is not None:
            if not isinstance(approval, dict) or not all(
                    isinstance(approval.get(k), str) and SHA.fullmatch(approval[k])
                    for k in ("script", "screens")):
                out.append("%s: approval 은 {script, screens} 각각 sha256:<64hex>" % w)
    return out


def inline_scripts(html):
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)]


class ShortsIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = json.loads((SHORTS / "index.json").read_text(encoding="utf-8"))

    def test_live_index_is_valid(self):
        self.assertEqual(validate(self.index["shorts"]), [])

    def test_virtual_second_episode(self):
        """02편을 넣어도 계약이 서고, 흔한 실수는 문장으로 막힌다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp4").write_bytes(b"x")
            (root / "a.jpg").write_bytes(b"x")
            (root / "p").mkdir()
            (root / "p" / "index.html").write_text("<html></html>", encoding="utf-8")
            one = dict(episode_id="2026-09-21-taiwan", no=1, primary_issue_id="issue-" + "a" * 16,
                       issue_ids=["issue-" + "a" * 16, "issue-" + "b" * 16], title="대만 원전정책 변화",
                       lead="전면 중단 10개월 만에 재가동 추진", date="2026-09-21", file="a.mp4",
                       youtube="", poster="a.jpg", page="p", seconds=69, summary=["정지로 전면 중단"])
            two = dict(one, episode_id="2026-09-28-smr", no=2, date="2026-09-28",
                       primary_issue_id="issue-" + "c" * 16, issue_ids=["issue-" + "c" * 16],
                       title="SMR 특별법", approval={"script": "sha256:" + "0" * 64,
                                                    "screens": "sha256:" + "1" * 64})
            self.assertEqual(validate([one, two], root), [])
            self.assertEqual(validate([two, one], root), [], "줄 순서는 계약과 무관해야 한다")

            def first_problem(rows):
                problems = validate(rows, root)
                self.assertTrue(problems)
                return problems[0]
            self.assertIn("episode_id 중복", first_problem([one, dict(two, episode_id=one["episode_id"])]))
            self.assertIn("no 1 중복", first_problem([one, dict(two, no=1)]))
            self.assertIn("primary_issue_id", first_problem([dict(two, primary_issue_id="issue-" + "d" * 16)]))
            self.assertIn("file 파일 없음", first_problem([dict(two, file="missing.mp4")]))
            self.assertIn("date 로 시작", first_problem([dict(two, episode_id="2026-09-29-smr")]))
            self.assertIn("개조식", first_problem([dict(two, summary=["부결됐다."])]))
            self.assertIn("approval", first_problem([dict(two, approval={"script": "abc"})]))
            self.assertIn("형이 다름", first_problem([dict(two, seconds="69")]))

    def test_app_picks_latest_by_date_and_primary_issue(self):
        """칩은 배열 첫 줄이 아니라 date·no 최신, 「이슈 보기」는 primary_issue_id."""
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        chip = script.split("function renderBriefVideoLink(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("shortsLatest()", chip)
        self.assertNotIn("shortsAll()[0]", chip, "02편을 어디에 끼우느냐에 따라 칩이 바뀐다")
        latest = script.split("function shortsLatest(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("date", latest)
        self.assertIn(".no", latest)
        tile = script.split("function shortsTile(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("row.primary_issue_id", tile, "배열 issue_ids 를 String() 으로 누르면 링크가 죽는다")
        match = script.split("function shortsMatch(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("row.issue_ids", match)

    def test_archive_page_reads_the_same_index(self):
        """index.html 의 칩 기본값 /shorts/ 가 404 가 아니어야 한다."""
        page = (SHORTS / "index.html").read_text(encoding="utf-8")
        self.assertIn('fetch("/shorts/index.json"', page)
        self.assertIn('href="/shorts/"', (ROOT / "public" / "index.html").read_text(encoding="utf-8"))

    def test_inline_scripts_parse(self):
        """공유·아카이브 페이지의 인라인 스크립트는 node --check 를 통과해야 한다.
        2026-09-21 공유 페이지가 문자열 안 개행으로 SyntaxError 인 채 배포됐다 —
        재생 버튼·공유 버튼이 전부 죽었는데 아무 검사에도 안 걸렸다."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node 없음")
        pages = sorted(SHORTS.rglob("*.html"))
        self.assertTrue(pages)
        for page in pages:
            for n, js in enumerate(inline_scripts(page.read_text(encoding="utf-8"))):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                    f.write(js)
                r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
                Path(f.name).unlink()
                self.assertEqual(r.returncode, 0, "%s <script> #%d: %s" % (page.relative_to(ROOT), n, r.stderr.strip()[-400:]))


if __name__ == "__main__":
    unittest.main()
