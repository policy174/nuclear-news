# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_cards  # noqa: E402


class PublishTest(unittest.TestCase):
    def test_copies_renames_prunes_and_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cards" / "out").mkdir(parents=True)
            for i in (1, 2):
                (root / "cards" / "out" / f"slide-0{i}.png").write_bytes(b"png")
            site = root / "site"
            (site / "2026-08-01").mkdir(parents=True)      # 오래됨 → 지워진다
            (site / "2026-08-01" / "01.png").write_bytes(b"x")
            (site / "2026-09-10").mkdir()                  # 보관 기간 안
            (site / "2026-09-10" / "01.png").write_bytes(b"x")
            album = {"date": "2026-09-15", "files": ["cards/out/slide-01.png", "cards/out/slide-02.png"]}
            index = publish_cards.publish(album, site, root, today=date(2026, 9, 16))
            self.assertEqual(sorted(p.name for p in (site / "2026-09-15").iterdir()), ["01.png", "02.png"])
            self.assertFalse((site / "2026-08-01").exists())
            self.assertEqual(index["latest"], "2026-09-15")
            self.assertEqual(list(index["dates"]), ["2026-09-10", "2026-09-15"])
            self.assertEqual(json.loads((site / "index.json").read_text(encoding="utf-8")), index)

    def test_missing_png_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                publish_cards.publish({"date": "2026-09-15", "files": ["cards/out/nope.png"]},
                                      Path(tmp) / "site", Path(tmp))


if __name__ == "__main__":
    unittest.main()
