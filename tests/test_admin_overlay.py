"""운영 콘솔 판정 오버레이 계약.

덧칠: keywords.json 은 안 바뀌고 메모리의 config 만 합쳐진다.
되돌리기 한 줄: 판정이 빠지면 다음 수집이 원래 동작으로 돌아간다(= 이 함수에
판정을 안 주면 base 그대로).
장부: 실제로 설정을 바꾼 판정만 '적용됨'으로 센다 — 콘솔의 배지가 거짓말하지
않게.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# news_bot 은 import 시점에 자격증명을 요구한다(모듈 상단 가드) — test_collect 와
# 같은 방식으로 더미를 심는다. 이 테스트는 외부 호출 0.
for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot  # noqa: E402


def base_config():
    return {
        "정책": {"keywords": ["원자력 정책"], "anchors": ["원자력"], "negative_terms": "-주가 -채용"},
        "SMR": {"keywords": ["SMR"], "anchors": ["소형모듈원자로"], "negative_terms": "-주가"},
    }


def entry(kind, group, value, **extra):
    return {"id": f"id-{kind}-{value}", "kind": kind, "group": group, "value": value, **extra}


class AdminOverlayTests(unittest.TestCase):
    def test_keyword_add_and_remove(self):
        config = base_config()
        applied = news_bot.apply_admin_overlay(config, [
            entry("keyword_add", "정책", "이집트 원전"),
            entry("keyword_remove", "정책", "원자력 정책"),
        ])
        self.assertEqual(config["정책"]["keywords"], ["이집트 원전"])
        self.assertEqual(len(applied), 2)
        self.assertEqual(config["SMR"]["keywords"], ["SMR"])   # 다른 그룹 불변

    def test_common_group_spreads_to_every_feed(self):
        config = base_config()
        news_bot.apply_admin_overlay(config, [entry("anchor_add", "공통", "방폐")])
        self.assertIn("방폐", config["정책"]["anchors"])
        self.assertIn("방폐", config["SMR"]["anchors"])

    def test_exclusion_joins_negative_terms_string(self):
        config = base_config()
        news_bot.apply_admin_overlay(config, [
            entry("exclusion_add", "정책", "동호회"),
            entry("exclusion_remove", "정책", "채용"),
        ])
        terms = config["정책"]["negative_terms"].split()
        self.assertIn("-동호회", terms)
        self.assertNotIn("-채용", terms)

    def test_protected_domain_word_survives_via_existing_guard(self):
        # 콘솔에서 '원전'을 제외어로 넣어도 수집 시점 가드가 걷어낸다.
        # 가드를 오버레이에 복제하지 않는다는 계약의 회귀 테스트.
        config = base_config()
        news_bot.apply_admin_overlay(config, [entry("exclusion_add", "정책", "원전")])
        self.assertIn("-원전", config["정책"]["negative_terms"])
        kept = news_bot.parse_negative_terms(config["정책"]["negative_terms"])
        self.assertNotIn("원전", kept)
        self.assertIn("주가", kept)

    def test_disabled_entry_ignored(self):
        config = base_config()
        applied = news_bot.apply_admin_overlay(
            config, [entry("keyword_add", "정책", "이집트 원전", disabled=True)])
        self.assertEqual(config["정책"]["keywords"], ["원자력 정책"])
        self.assertEqual(applied, [])

    def test_noop_judgment_not_counted_as_applied(self):
        config = base_config()
        applied = news_bot.apply_admin_overlay(config, [
            entry("keyword_add", "정책", "원자력 정책"),      # 이미 있음
            entry("keyword_remove", "정책", "없는 키워드"),   # 없음
            entry("keyword_add", "없는그룹", "무엇"),         # 대상 없음
        ])
        self.assertEqual(applied, [])

    def test_unknown_kind_ignored(self):
        config = base_config()
        applied = news_bot.apply_admin_overlay(config, [entry("source_add", "정책", "example.com")])
        self.assertEqual(applied, [])
        self.assertEqual(config, base_config())

    def test_missing_overlay_file_keeps_base(self):
        entries, state = news_bot.load_admin_overlay(Path("no-such-overlay.json"))
        self.assertEqual(entries, [])
        self.assertEqual(state, "unavailable")

    def test_broken_overlay_file_is_unavailable_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay.json"
            path.write_text("{not json", encoding="utf-8")
            entries, state = news_bot.load_admin_overlay(path)
        self.assertEqual((entries, state), ([], "unavailable"))

    def test_loads_entries_and_records_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.json"
            overlay.write_text(json.dumps({"version": 3, "entries": [
                entry("keyword_add", "정책", "이집트 원전")]}), encoding="utf-8")
            entries, state = news_bot.load_admin_overlay(overlay)
            self.assertEqual(state, "ok")
            config = base_config()
            applied = news_bot.apply_admin_overlay(config, entries)
            ledger = Path(tmp) / "admin" / "applied.json"
            news_bot.record_admin_applied(applied, state, ledger)
            written = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(written["applied_ids"], applied)
        self.assertEqual(written["overlay"], "ok")
        self.assertTrue(written["collected_at"])


if __name__ == "__main__":
    unittest.main()
