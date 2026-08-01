"""news_archive(영구 아카이브) + 통제 태그 정규화 테스트. 외부 호출 0."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import news_archive

# news_bot 은 모듈 로드 시 필수 env 를 요구 — 더미 주입 후 import
for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArchiveDirMixin(unittest.TestCase):
    """ARCHIVE_DIR 을 임시 폴더로 돌려 실제 저장소를 건드리지 않는다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = news_archive.ARCHIVE_DIR
        news_archive.ARCHIVE_DIR = Path(self._tmp.name)

    def tearDown(self):
        news_archive.ARCHIVE_DIR = self._orig_dir
        self._tmp.cleanup()


class TestMakeRecord(unittest.TestCase):
    def test_fields_and_datetime_pub(self):
        article = {
            "hash": "abc123", "link": "https://world-nuclear-news.org/x",
            "title": "NRC approves licence", "domain": "world-nuclear-news.org",
            "feed": "WNN", "pub": datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc),
        }
        cur = {
            "importance": "must_read", "section": "international", "scope": "overseas",
            "category": "규제", "title_kr": "NRC 인허가 승인", "summary": "요약",
            "implication": "", "why_important": "중요", "tags": ["#NRC"],
            "topics": ["regulation"], "countries": ["US"], "article_type": "policy",
            "features": {"novelty": 2},
        }
        r = news_archive.make_record(article, cur, "2026-07-30T04:00:00+00:00")
        self.assertEqual(r["v"], news_archive.RECORD_VERSION)
        self.assertEqual(r["hash"], "abc123")
        self.assertEqual(r["pub"], "2026-07-30T03:00:00+00:00")
        self.assertEqual(r["topics"], ["regulation"])
        self.assertEqual(r["countries"], ["US"])
        self.assertEqual(r["article_type"], "policy")
        self.assertIn(r["source_tier"], (1, 2, 3))
        self.assertEqual(r["publisher"], "World Nuclear News")
        self.assertEqual(r["source_type"], "specialist_media")
        self.assertIsNone(r["event_date"])
        self.assertNotIn("description", r)  # 원문 본문 미저장 (저작권)

    def test_missing_fields_safe(self):
        r = news_archive.make_record({"hash": "h1"}, {}, _now_iso())
        self.assertEqual(r["topics"], [])
        self.assertEqual(r["tags"], [])
        self.assertEqual(r["pub"], "")


class TestAppendDedup(ArchiveDirMixin):
    def test_append_and_hash_load(self):
        now = _now_iso()
        recs = [news_archive.make_record({
                    "hash": f"h{i}", "link": f"https://example.com/{i}", "title": f"기사 {i}"
                }, {}, now)
                for i in range(3)]
        self.assertEqual(news_archive.append_records(recs), 3)
        hashes = news_archive.load_recent_hashes()
        self.assertEqual(hashes, {"h0", "h1", "h2"})
        # 호출부 패턴: 이미 있는 hash 는 거른 뒤 append → 재실행해도 안 불어남
        new = [r for r in recs if r["hash"] not in hashes]
        self.assertEqual(news_archive.append_records(new), 0)

    def test_month_file_routing(self):
        now = datetime.now(timezone.utc)
        rec = news_archive.make_record({
            "hash": "hx", "link": "https://example.com/month", "title": "월 라우팅 기사"
        }, {}, now.isoformat())
        news_archive.append_records([rec])
        expected = Path(self._tmp.name) / f"{now.strftime('%Y-%m')}.jsonl"
        self.assertTrue(expected.exists())
        line = json.loads(expected.read_text(encoding="utf-8").strip())
        self.assertEqual(line["hash"], "hx")

    def test_broken_line_skipped(self):
        now = datetime.now(timezone.utc)
        path = Path(self._tmp.name) / f"{now.strftime('%Y-%m')}.jsonl"
        path.write_text('{"hash": "ok"}\n{broken json\n', encoding="utf-8")
        self.assertEqual(news_archive.load_recent_hashes(), {"ok"})


class TestBackfill(ArchiveDirMixin):
    def test_backfill_skips_existing(self):
        now = _now_iso()
        news_archive.append_records(
            [news_archive.make_record({
                "hash": "old1", "link": "https://example.com/old", "title": "기존 기사"
            }, {}, now)])
        curated = {
            "old1": {"title": "이미 있음", "link": "", "cached_at": now},
            "new1": {"title": "새 항목", "link": "https://ex.com/a", "domain": "ex.com",
                     "importance": "nice_to_know", "cached_at": now},
        }
        cpath = Path(self._tmp.name) / "curated.json"
        cpath.write_text(json.dumps(curated, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(news_archive.backfill_from_curated(cpath), 1)
        self.assertEqual(news_archive.load_recent_hashes(), {"old1", "new1"})


class TestControlledTagNorm(unittest.TestCase):
    def test_topics_whitelist_and_cap(self):
        self.assertEqual(news_bot.norm_topics(["SMR", "waste", "없는태그", "fusion", "finance"]),
                         ["smr", "waste", "fusion"])  # 목록 밖 컷 + 최대 3개
        self.assertEqual(news_bot.norm_topics("smr"), [])  # 리스트 아님 → 빈 값

    def test_countries_whitelist(self):
        self.assertEqual(news_bot.norm_countries(["us", "fr", "jp"]), ["US", "FR"])
        self.assertEqual(news_bot.norm_countries(["KOREA"]), [])

    def test_article_type_fallback(self):
        self.assertEqual(news_bot.norm_article_type("policy"), "policy")
        self.assertEqual(news_bot.norm_article_type("속보"), "news")
        self.assertEqual(news_bot.norm_article_type(None), "news")


if __name__ == "__main__":
    unittest.main()
