"""발간물 한국어 해석 — 대상 선별·캐시·실패 시 원문 유지."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pubs_translate as T


class FakeClient:
    def __init__(self, responses=None, available=True, raises=False):
        self.responses = list(responses or [])
        self._available = available
        self.raises = raises
        self.calls = []

    def is_available(self):
        return self._available

    def call_json(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if self.raises:
            raise RuntimeError("429")
        if self.responses:
            return self.responses.pop(0)
        rows = [line for line in user_message.split("\n") if line.startswith("[")]
        return {"items": [{"idx": i, "title_kr": f"번역{i}", "gist": "설명"}
                          for i in range(len(rows))]}


def item(title="Nuclear Safety Report 2026", org="IAEA", **extra):
    row = {"title": title, "org": org, "url": "https://x/y"}
    row.update(extra)
    return row


class NeedsTranslationTests(unittest.TestCase):
    def test_english_titles_are_candidates(self):
        self.assertTrue(T.needs_translation(item()))

    def test_korean_titles_are_skipped(self):
        # 에경연 인사이트는 이미 한국어라 번역할 것이 없다
        self.assertFalse(T.needs_translation(
            item("[격주간] 세계 원전시장 인사이트(2026.07.24.)", "KEEI")))

    def test_already_translated_is_skipped(self):
        done = item(title_kr="원자력 안전 보고서 2026",
                    translated_version=T.PROMPT_VERSION)
        self.assertFalse(T.needs_translation(done))

    def test_stale_prompt_version_is_retranslated(self):
        stale = item(title_kr="옛 번역", translated_version=T.PROMPT_VERSION - 1)
        self.assertTrue(T.needs_translation(stale))

    def test_malformed_items_are_ignored(self):
        for bad in (None, 5, {}, {"title": ""}):
            self.assertFalse(T.needs_translation(bad))


class TranslateTests(unittest.TestCase):
    def test_translation_is_written_back_and_marked(self):
        items = [item("Nuclear Safety Report"), item("Uranium Market Update")]
        client = FakeClient()
        stats = T.translate(items, client=client)
        self.assertEqual(stats["translated"], 2)
        self.assertEqual(stats["calls"], 1)
        for row in items:
            self.assertTrue(row["title_kr"])
            self.assertEqual(row["translated_version"], T.PROMPT_VERSION)

    def test_second_run_asks_nothing(self):
        items = [item("Nuclear Safety Report")]
        T.translate(items, client=FakeClient())
        again = FakeClient()
        stats = T.translate(items, client=again)
        self.assertEqual(again.calls, [])
        self.assertEqual(stats["status"], "nothing_to_do")

    def test_no_api_key_leaves_titles_untouched(self):
        items = [item()]
        stats = T.translate(items, client=FakeClient(available=False))
        self.assertEqual(stats["status"], "no_api_key")
        self.assertNotIn("title_kr", items[0])

    def test_failure_leaves_titles_untouched(self):
        items = [item()]
        stats = T.translate(items, client=FakeClient(raises=True))
        self.assertIn("error", stats["status"])
        self.assertNotIn("title_kr", items[0])

    def test_missing_idx_in_response_leaves_that_item_untranslated(self):
        items = [item("A"), item("B")]
        client = FakeClient([{"items": [{"idx": 0, "title_kr": "가", "gist": ""}]}])
        stats = T.translate(items, client=client)
        self.assertEqual(stats["translated"], 1)
        self.assertEqual(items[0]["title_kr"], "가")
        self.assertNotIn("title_kr", items[1])

    def test_empty_gist_is_not_stored(self):
        items = [item()]
        T.translate(items, client=FakeClient(
            [{"items": [{"idx": 0, "title_kr": "제목", "gist": "   "}]}]))
        self.assertNotIn("gist", items[0], "빈 설명을 저장하면 화면에 빈 줄이 생긴다")

    def test_batches_are_split(self):
        items = [item(f"Report {i}") for i in range(35)]
        client = FakeClient()
        T.translate(items, client=client, batch_size=15)
        self.assertEqual(len(client.calls), 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
