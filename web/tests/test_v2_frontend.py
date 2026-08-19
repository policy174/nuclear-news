"""v2 프런트엔드 포팅 계약 — 소스 텍스트 대조.

app.js 는 모듈이 아니라 브라우저 스크립트라 import 로 잠글 수 없다.
그래서 '있어야 하는 문자열'과 '지워지면 안 되는 문자열(keep-ours)'을
소스 그대로 대조한다 — 리팩터링으로 문자열이 바뀌면 여기가 먼저 깨진다.
"""
import unittest
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
APP = (PUBLIC / "app.js").read_text(encoding="utf-8")
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")


class TestV2Ported(unittest.TestCase):
    def test_weekly_pending_message_present(self):
        # 그 주 리포트가 없으면 직전 주로 채우지 않고 집계 중이라고 말한다.
        self.assertIn("집계 중입니다", APP)

    def test_story_chip_templates_present(self):
        self.assertIn("동일 사건 보도", APP)
        self.assertIn("보도 매체", APP)

    def test_timeline_head_is_five(self):
        self.assertIn("TIMELINE_HEAD", APP)
        self.assertIn("const TIMELINE_HEAD = 5;", APP)

    def test_audio_mode_buttons_in_index(self):
        self.assertIn("data-audio-mode", INDEX)

    def test_long_periods_in_index(self):
        self.assertIn('data-period="365"', INDEX)
        self.assertIn('data-period="90"', INDEX)
        self.assertIn('data-period="180"', INDEX)

    def test_weekly_reports_consumed(self):
        self.assertIn("weekly_reports", APP)

    def test_briefing_week_uses_utc_day_math(self):
        # 토~금 주차: UTC 요일 산술이어야 KST 오프셋 왕복 off-by-one 이 없다.
        self.assertIn("(5 - parsed.getUTCDay() + 7) % 7", APP)


class TestKeepOurs(unittest.TestCase):
    """v2 diff 가 갈아치우려던 카드 본문 계약 — 우리 쪽을 유지한다."""

    def test_card_source_line_kept(self):
        self.assertIn("issueSourceText", APP)

    def test_card_prior_line_kept(self):
        self.assertIn("card_prior", APP)


if __name__ == "__main__":
    unittest.main()
