import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import embedding_pipeline as ep


class EmbeddingPipelineTests(unittest.TestCase):
    def test_current_cache_requires_model_and_fingerprint(self):
        article = {"title_kr": "동일 사건 후속 보도", "summary": "후속 사실을 확인했습니다."}
        text = ep.embedding_text(article)
        entry = {
            "vec": [1.0, 0.0],
            "model": ep.EMBEDDING_MODEL,
            "text_fingerprint": ep.text_fingerprint(text),
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertTrue(ep.cache_entry_is_current(entry, text))
        self.assertIsNone(ep.cached_vector({**entry, "model": "text-embedding-004"}))
        self.assertFalse(ep.cache_entry_is_current(entry, text + "변경"))

    def test_selected_articles_uses_latest_21_day_delivery_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "archive").mkdir()
            deliveries = [
                {"date": "2026-07-01", "hash": "old"},
                {"date": "2026-07-20", "hash": "kept"},
                {"date": "2026-08-01", "hash": "latest"},
            ]
            (root / "delivery_log.jsonl").write_text(
                "\n".join(json.dumps(row) for row in deliveries), encoding="utf-8"
            )
            archive = [
                {"hash": "old", "title_kr": "과거"},
                {"hash": "kept", "title_kr": "최근"},
                {"hash": "latest", "title_kr": "최신"},
            ]
            (root / "archive" / "2026-08.jsonl").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in archive),
                encoding="utf-8",
            )
            selected = ep.selected_articles(root, window_days=21)
            self.assertEqual({row["hash"] for row in selected}, {"kept", "latest"})

    def test_refresh_writes_model_aware_cache(self):
        articles = [{"hash": "a", "title_kr": "후속 보도", "summary": "사실을 확인했습니다."}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embeddings.json"
            vector = [0.0] * ep.EMBEDDING_DIMENSION
            vector[0] = 1.0
            with patch.object(ep, "embed_one", return_value=vector):
                stats = ep.refresh_embeddings(
                    articles, {}, object(), sleep_seconds=0, cache_path=path
                )
            cache = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stats["current"], 1)
            self.assertEqual(cache["a"]["model"], ep.EMBEDDING_MODEL)
            self.assertEqual(cache["a"]["dimension"], ep.EMBEDDING_DIMENSION)


if __name__ == "__main__":
    unittest.main()
