"""브리핑 헤더 날짜 = KST 회귀 테스트.

배경 (2026-08-04 실사고):
    format_cards_message 가 tz 없는 date.today() 를 썼다. 브리핑은 GitHub
    Actions(UTC)에서 08:30 KST 안팎 = 전날 23:30 UTC 에 나가므로 헤더 날짜가
    매일 하루 전으로 찍혔다. 로컬(KST)에서 돌리면 정상으로 보여 오래 안 잡혔다.
    → 날짜 계산은 반드시 tz-aware KST 로.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import synthesize  # noqa: E402

UTC = timezone.utc
# 실제 사고 시각: 2026-08-03 23:31 UTC = 2026-08-04 08:31 KST
INCIDENT_UTC = datetime(2026, 8, 3, 23, 31, tzinfo=UTC)

CARD = {"headline": "테스트 헤드라인", "what": "무슨 일", "cluster": {}}


class FrozenDatetime(datetime):
    """datetime.now(tz) 만 고정. tz 를 무시하지 않고 정상 변환한다."""
    frozen = INCIDENT_UTC

    @classmethod
    def now(cls, tz=None):
        return cls.frozen if tz is None else cls.frozen.astimezone(tz)


class HeaderDateKstTest(unittest.TestCase):

    def _header_date(self) -> str:
        msg = synthesize.format_cards_message([CARD], header="원자력 국내 브리핑")
        m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", msg)
        self.assertIsNotNone(m, f"헤더에 날짜가 없다: {msg[:120]}")
        return m.group(1)

    def test_utc_runner_gives_kst_date(self):
        """UTC 23:31 에 발송해도 헤더는 KST 기준 다음 날짜여야 한다."""
        with patch.object(synthesize, "datetime", FrozenDatetime):
            self.assertEqual(self._header_date(), "2026-08-04")

    def test_kst_offset_is_plus_nine(self):
        self.assertEqual(synthesize.KST.utcoffset(None), timedelta(hours=9))

    def test_section_header_has_no_date(self):
        """show_header=False 는 섹션용 — 날짜를 붙이지 않는다 (기존 계약 유지)."""
        with patch.object(synthesize, "datetime", FrozenDatetime):
            msg = synthesize.format_cards_message(
                [CARD], header="━━ 소셜 ━━", show_header=False)
        self.assertIsNone(re.search(r"\(\d{4}-\d{2}-\d{2}\)", msg))


class NoNaiveDateTest(unittest.TestCase):
    """tz 없는 date.today() 가 다시 기어들어오는 것을 막는 가드."""

    TARGETS = ("synthesize.py", "send_research.py", "daily_brief.py")

    def test_no_naive_date_today(self):
        root = Path(__file__).parent.parent
        for name in self.TARGETS:
            src = (root / name).read_text(encoding="utf-8")
            hits = [ln for ln in src.splitlines()
                    if "date.today()" in ln and not ln.lstrip().startswith("#")]
            self.assertEqual(
                hits, [],
                f"{name}: tz 없는 date.today() 사용 — datetime.now(KST).date() 로 바꿀 것")


if __name__ == "__main__":
    unittest.main()
