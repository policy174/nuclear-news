"""차단 출처(sources.json `blocked`) 계약.

2026-08-21 사고: mshale.com 이 한국 방송사 유튜브 클립 제목을 그대로 재배포했고
("[🔴속보] 신규 대형원전 부지로 경북 영덕 선정… / 연합뉴스TV(YonhapnewsTV) Mill…"),
본문이 없어 제목만 본 큐레이션이 must_read 를 줬다. 몇 달 지난 사건이 그대로
주간 판세의 1번 정책변화 + 1번 보고서 후보로 올라갔다. URL 은 매번 다르고 제목
끝에 무작위 영단어+videoId 가 붙어 dedup 4중이 전부 비껴갔다 — 같은 사건이
8/13·8/14·8/21 세 번 새 기사로 들어왔다.

여기가 잠그는 것: 차단 판정이 (1) 서브도메인까지 잡고 (2) 정상 매체는 안 잡고
(3) 수집에서 **큐레이션 호출 전에** 걸리고 (4) 웹 빌드가 과거분도 걷어낸다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
from sources import is_blocked_source  # noqa: E402


class BlockedSourceMatch(unittest.TestCase):
    def test_matches_url_bare_domain_and_subdomain(self):
        for value in ("https://mshale.com/8ffa8276/cb66ca7f",
                      "https://www.mshale.com/a",
                      "news.mshale.com",
                      "mshale.com"):
            self.assertTrue(is_blocked_source(value), value)

    def test_normal_sources_pass(self):
        for value in ("yna.co.kr", "https://www.hankyung.com/article/1",
                      "world-nuclear-news.org", "news.google.com", "", None):
            self.assertFalse(is_blocked_source(value), value)

    def test_offending_domain_is_registered(self):
        cfg = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        self.assertIn("mshale.com", cfg.get("blocked", []))


class CollectionDropsBeforeCuration(unittest.TestCase):
    """소스 가드 — 차단이 dedup·큐레이션 뒤로 밀리면 무료 티어 quota 를 그대로 태운다."""

    def test_filter_runs_before_dedup(self):
        src = (ROOT / "news_bot.py").read_text(encoding="utf-8")
        marker = 'is_blocked_source(art.get(k)) for k in ("domain", "link")'
        self.assertIn(marker, src)
        self.assertLess(src.index(marker),
                        src.index("exact_kept = dedup_exact_candidates("))


class ArchiveLoadDropsBlocked(unittest.TestCase):
    """웹은 아카이브를 읽는다 — 파일을 안 고쳐도 화면에서 사라져야 한다."""

    def test_blocked_records_never_reach_the_site(self):
        rows = [
            {"hash": "aaaaaaaa11", "domain": "mshale.com", "title": "재배포"},
            {"hash": "bbbbbbbb22", "domain": "news.google.com",
             "resolved_url": "https://mshale.com/x", "title": "구글뉴스 경유 재배포"},
            {"hash": "cccccccc33", "domain": "hankyung.com", "title": "정상"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive"
            archive.mkdir()
            (archive / "2026-08.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                encoding="utf-8")
            original = build_data.BOT_DIR
            build_data.BOT_DIR = Path(tmp)
            try:
                got = {r["hash"] for r in build_data.load_archive()}
            finally:
                build_data.BOT_DIR = original
        self.assertEqual(got, {"cccccccc33"})


if __name__ == "__main__":
    unittest.main()
