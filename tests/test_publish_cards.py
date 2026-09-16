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

    def test_card_lines_ride_along_and_expire_with_the_folder(self):
        """홈 카드가 쓰는 한 줄은 index.json 에 실리고, 그 날짜 폴더가 지워지면 같이 빠진다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cards" / "out").mkdir(parents=True)
            (root / "cards" / "out" / "slide-01.png").write_bytes(b"png")
            site = root / "site"
            (site / "2026-08-01").mkdir(parents=True)      # 보관 기간 밖 → 지워진다
            (site / "2026-08-01" / "01.png").write_bytes(b"x")
            (site / "index.json").write_text(json.dumps({
                "latest": "2026-08-01", "dates": {"2026-08-01": ["01.png"]},
                "lines": {"2026-08-01": {"old": "지난 줄"}},
            }), encoding="utf-8")
            album = {"date": "2026-09-15", "files": ["cards/out/slide-01.png"],
                     "lines": {"iss-1": "설계수명 만료 4기 일정에 직결"}}
            index = publish_cards.publish(album, site, root, today=date(2026, 9, 16))
            self.assertEqual(index["lines"], {"2026-09-15": {"iss-1": "설계수명 만료 4기 일정에 직결"}})
            self.assertEqual(json.loads((site / "index.json").read_text(encoding="utf-8")), index)

    def test_index_survives_a_day_without_lines(self):
        """카피가 폴백으로 갔거나 옛 album.json 이면 lines 가 없다 — 빈 칸이지 오류가 아니다."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cards" / "out").mkdir(parents=True)
            (root / "cards" / "out" / "slide-01.png").write_bytes(b"png")
            site = root / "site"
            index = publish_cards.publish({"date": "2026-09-15", "files": ["cards/out/slide-01.png"]},
                                          site, root, today=date(2026, 9, 16))
            self.assertEqual(index["lines"], {})

    def test_missing_png_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                publish_cards.publish({"date": "2026-09-15", "files": ["cards/out/nope.png"]},
                                      Path(tmp) / "site", Path(tmp))


if __name__ == "__main__":
    unittest.main()
