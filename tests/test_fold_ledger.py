"""접힘 장부 계약 (WS7).

수집 단계에서 접은 중복을 생존 기사에 남긴다. 이 기록은 **소급이 안 된다** —
그래서 잠그는 것은 ①접힌 기사가 사라지지 않는가 ②건수가 줄어 말해지지 않는가
③생존 판정(누가 남는가)이 장부 때문에 바뀌지 않는가 셋이다.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot  # noqa: E402


def art(hash_, title, url, score=1, publisher="연합뉴스"):
    return {"hash": hash_, "title": title, "link": url, "score": score,
            "publisher": publisher, "domain": "yna.co.kr", "pub": "2026-08-21T00:00:00+00:00"}


class NoteFoldTests(unittest.TestCase):
    def test_records_dropped_article(self):
        kept, dropped = art("a", "원전 계약", "https://x.kr/1"), art("b", "원전 계약 체결", "https://x.kr/2")
        news_bot.note_fold(kept, dropped, "title_similar", 0.9123)
        self.assertEqual(kept["folded_count"], 1)
        entry = kept["folded"][0]
        self.assertEqual(entry["hash"], "b")
        self.assertEqual(entry["stage"], "title_similar")
        self.assertEqual(entry["similarity"], 0.912)

    def test_absorbs_ledger_of_folded_article(self):
        # A←B, B←C 로 접히면 A 가 C 까지 알아야 건수가 맞는다.
        a, b, c = art("a", "1", "https://x.kr/1"), art("b", "2", "https://x.kr/2"), art("c", "3", "https://x.kr/3")
        news_bot.note_fold(b, c, "url_same")
        news_bot.note_fold(a, b, "embedding", 0.95)
        self.assertEqual(a["folded_count"], 2)
        self.assertEqual({e["hash"] for e in a["folded"]}, {"b", "c"})

    def test_count_survives_list_cap(self):
        # 목록은 상한까지만, 건수는 진짜 건수 — 줄여 말하면 거짓말이 된다.
        kept = art("keep", "대표", "https://x.kr/0")
        for i in range(news_bot.FOLD_LEDGER_CAP + 8):
            news_bot.note_fold(kept, art(f"d{i}", f"제목{i}", f"https://x.kr/{i+1}"), "title_similar")
        self.assertEqual(len(kept["folded"]), news_bot.FOLD_LEDGER_CAP)
        self.assertEqual(kept["folded_count"], news_bot.FOLD_LEDGER_CAP + 8)

    def test_self_fold_is_noop(self):
        a = art("a", "1", "https://x.kr/1")
        news_bot.note_fold(a, a, "url_same")
        self.assertNotIn("folded", a)


class ExactDedupLedgerTests(unittest.TestCase):
    def test_same_url_keeps_higher_score_and_records_loser(self):
        kept = news_bot.dedup_exact_candidates([
            art("a", "원전 계약", "https://x.kr/1", score=5),
            art("b", "원전 계약", "https://x.kr/1", score=9, publisher="조선"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["score"], 9)          # 생존 판정 불변
        self.assertEqual(kept[0]["folded_count"], 1)
        self.assertEqual(kept[0]["folded"][0]["stage"], "url_same")

    def test_exact_title_across_urls_is_recorded(self):
        kept = news_bot.dedup_exact_candidates([
            art("a", "원전 계약 체결", "https://x.kr/1", score=9),
            art("b", "원전 계약 체결", "https://y.kr/2", score=3, publisher="한겨레"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["folded_count"], 1)
        self.assertEqual(kept[0]["folded"][0]["publisher"], "한겨레")
        self.assertEqual(kept[0]["folded"][0]["stage"], "title_exact")

    def test_distinct_articles_carry_no_ledger(self):
        kept = news_bot.dedup_exact_candidates([
            art("a", "원전 계약 체결", "https://x.kr/1"),
            art("b", "방폐장 부지 공모 공고", "https://y.kr/2"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertTrue(all("folded" not in k for k in kept))


class SemanticDedupLedgerTests(unittest.TestCase):
    def test_embedding_fold_records_similarity(self):
        a = art("a", "원전 계약 체결", "https://x.kr/1", score=9)
        b = art("b", "원전, 계약을 체결하다", "https://y.kr/2", score=2)
        cache = {"a": [1.0, 0.0], "b": [1.0, 0.0]}     # 완전 동일 방향 → 1.0
        original = news_bot.get_or_compute_embedding
        news_bot.get_or_compute_embedding = lambda article, h, c: cache.get(h)
        self.addCleanup(setattr, news_bot, "get_or_compute_embedding", original)
        sleep = news_bot.time.sleep
        news_bot.time.sleep = lambda _s: None
        self.addCleanup(setattr, news_bot.time, "sleep", sleep)

        kept = news_bot.semantic_dedup([a, b], {})
        self.assertEqual([k["hash"] for k in kept], ["a"])
        self.assertEqual(kept[0]["folded"][0]["hash"], "b")
        self.assertEqual(kept[0]["folded"][0]["stage"], "embedding")
        self.assertGreaterEqual(kept[0]["folded"][0]["similarity"], 0.99)


class ArchiveCarryTests(unittest.TestCase):
    def test_record_carries_ledger(self):
        import news_archive
        article = art("a", "원전 계약 체결", "https://x.kr/1")
        article["folded"] = [{"hash": "b", "title": "같은 사건", "publisher": "조선", "stage": "title_similar"}]
        article["folded_count"] = 3
        record = news_archive.make_record(article, {"title_kr": "원전 계약 체결"}, "2026-08-21T00:00:00+00:00")
        self.assertEqual(record["folded_count"], 3)
        self.assertEqual(record["folded"][0]["hash"], "b")

    def test_record_without_ledger_is_empty_not_missing(self):
        import news_archive
        record = news_archive.make_record(art("a", "제목", "https://x.kr/1"), {}, "2026-08-21T00:00:00+00:00")
        self.assertEqual(record["folded"], [])
        self.assertEqual(record["folded_count"], 0)


if __name__ == "__main__":
    unittest.main()
