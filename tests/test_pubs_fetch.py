"""pubs_fetch.py 단위 테스트 — 파서 fixture·신규 판별·소스 격리·보관 정책."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pubs_fetch


NEA_HTML = """
<div class="news">
 <a href="jcms/pl_120785/milestone-in-mentoring"><img src="x.jpg"></a>
 <a href="jcms/pl_120785/milestone-in-mentoring">Milestone in mentoring future STEM leaders</a>
 <a href="jcms/pl_120785/milestone-in-mentoring">READ MORE</a>
 <a href="jcms/pl_120580/summer-school">Inaugural Summer School held\r\n   Inaugural Summer School held</a>
 <a href="jcms/pl_120329/workshop">PREVIEW</a>
 <a href="/jcms/pl_120100/older-news">Older news item</a>
 <a href="jcms/pl_72332/nuclear-safety-research-joint-projects">Joint projects</a>
 <a href="jcms/pl_120900/accessibility-page">Accessibility</a>
</div>
"""

IEA_HTML = """
<a href="/reports/electric-car-markets">Electric Car Markets</a>
<a href="/reports/nuclear-power-outlook-2026"><span>Nuclear Power Outlook 2026</span></a>
<a href="/reports/nuclear-power-outlook-2026">Nuclear Power Outlook 2026</a>
"""


class FakeEntry(dict):
    pass


class ParserTests(unittest.TestCase):
    def setUp(self):
        self._orig_get = pubs_fetch._http_get

        def restore():
            pubs_fetch._http_get = self._orig_get
        self.addCleanup(restore)

    def test_dedouble_fixes_repeated_anchor_text(self):
        self.assertEqual(pubs_fetch._dedouble("제목 하나 제목 하나"), "제목 하나")
        self.assertEqual(pubs_fetch._dedouble("정상 제목"), "정상 제목")

    def test_nea_bootstrap_takes_recent_ids_and_skips_buttons(self):
        pubs_fetch._http_get = lambda url: NEA_HTML
        state = {}
        items = pubs_fetch.fetch_nea(state)
        titles = [item["title"] for item in items]
        # 버튼 텍스트가 아니라 실제 제목이 뽑힌다
        self.assertIn("Milestone in mentoring future STEM leaders", titles)
        # 중복 앵커 텍스트는 한 번으로 접힌다
        self.assertIn("Inaugural Summer School held", titles)
        # 텍스트 없는(버튼뿐) 항목은 슬러그 폴백
        self.assertTrue(any(t.startswith("Workshop") for t in titles))
        # 상시 페이지(Accessibility)는 높은 ID여도 제외
        self.assertNotIn("Accessibility", titles)
        self.assertEqual(state["nea_max_id"], 120900)

    def test_nea_incremental_only_returns_new_ids(self):
        pubs_fetch._http_get = lambda url: NEA_HTML
        state = {"nea_max_id": 120580}
        items = pubs_fetch.fetch_nea(state)
        titles = [item["title"] for item in items]
        self.assertIn("Milestone in mentoring future STEM leaders", titles)
        self.assertNotIn("Inaugural Summer School held", titles)
        self.assertNotIn("Older news item", titles)

    def test_iea_applies_keyword_gate_and_dedups_paths(self):
        pubs_fetch._http_get = lambda url: IEA_HTML
        items = pubs_fetch.fetch_iea()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Nuclear Power Outlook 2026")

    def test_eia_nonstandard_pubdate_falls_back_to_regex(self):
        entry = FakeEntry(published="Fri, 31 Jul 2026  09:00:00 EST")
        self.assertEqual(pubs_fetch._entry_date(entry), "2026-07-31")
        self.assertEqual(pubs_fetch._entry_date(FakeEntry(published="깨진 값")), "")

    def test_keyword_gate(self):
        self.assertTrue(pubs_fetch._passes_keyword_gate("China's nuclear capacity doubled"))
        self.assertTrue(pubs_fetch._passes_keyword_gate("Uranium market update"))
        self.assertFalse(pubs_fetch._passes_keyword_gate("Oil Market Report - July 2026"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_out = pubs_fetch.OUT_FILE
        pubs_fetch.OUT_FILE = Path(self._tmp.name) / "publications.json"
        self.addCleanup(lambda: setattr(pubs_fetch, "OUT_FILE", self._orig_out))

    @staticmethod
    def item(url, title="어떤 보고서", date=""):
        return pubs_fetch._make_item("IAEA", "국제원자력기구", "publication",
                                     title, url, date)

    def test_one_source_failure_does_not_kill_the_rest(self):
        def boom(state):
            raise RuntimeError("HTML 구조 변경")
        sources = [
            {"id": "broken", "fetch": boom},
            {"id": "healthy",
             "fetch": lambda state: [self.item("https://iaea.org/pub/1", date="2026-08-01")]},
        ]
        self.assertTrue(pubs_fetch.run(sources))
        store = json.loads(pubs_fetch.OUT_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(store["items"]), 1)
        self.assertFalse(store["last_checked"]["broken"]["ok"])
        self.assertIn("HTML 구조 변경", store["last_checked"]["broken"]["error"])
        self.assertTrue(store["last_checked"]["healthy"]["ok"])

    def test_rerun_dedups_by_normalized_url(self):
        sources = [{"id": "s",
                    "fetch": lambda state: [self.item("https://iaea.org/pub/1?utm_source=x",
                                                      date="2026-08-01")]}]
        pubs_fetch.run(sources)
        sources2 = [{"id": "s",
                     "fetch": lambda state: [self.item("https://iaea.org/pub/1",
                                                       date="2026-08-01")]}]
        pubs_fetch.run(sources2)
        store = json.loads(pubs_fetch.OUT_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(store["items"]), 1)
        self.assertEqual(store["last_checked"]["s"]["new"], 0)

    def test_prune_drops_items_older_than_keep_days(self):
        old_date = (datetime.now(pubs_fetch.KST)
                    - timedelta(days=pubs_fetch.KEEP_DAYS + 10)).strftime("%Y-%m-%d")
        sources = [{"id": "s", "fetch": lambda state: [
            self.item("https://iaea.org/pub/old", "옛 보고서", old_date),
            self.item("https://iaea.org/pub/new", "새 보고서", "2099-01-01"),
        ]}]
        pubs_fetch.run(sources)
        store = json.loads(pubs_fetch.OUT_FILE.read_text(encoding="utf-8"))
        titles = [item["title"] for item in store["items"]]
        self.assertEqual(titles, ["새 보고서"])

    def test_file_always_written_even_when_all_sources_fail(self):
        def boom(state):
            raise RuntimeError("죽음")
        pubs_fetch.run([{"id": "only", "fetch": boom}])
        self.assertTrue(pubs_fetch.OUT_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
