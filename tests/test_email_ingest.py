"""뉴스레터(ANS Nuclear News Daily) 수집 형태 회귀 테스트. 외부 호출 0.

회귀 방지 (2026-08-05): 뉴스레터 경유 기사의 제목이 ``text[:120]`` 이라 **단어
중간에서 끊긴 파편**이었다. 그 파편이 큐레이션 프롬프트의 제목 자리에 들어가
판단 재료를 통째로 나쁘게 만들었고, 2026-07~08 아카이브에서 뉴스레터 경유분의
결손이 다른 경로보다 뚜렷했다 — implication 65% vs 38%, features 22% vs 2%.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")

import email_ingest as ei  # noqa: E402

# 실제 뉴스레터 블록 형태 — 편집진이 쓴 요약 문장 + 뒤따르는 부연.
PARAGRAPH = (
    "South Africa's National Nuclear Regulator says that no radioactive material "
    "leaked into the environment during three recent incidents at Koeberg. "
    "The regulator added that it continues to monitor the site."
)
LONG_SENTENCE = (
    "Researchers at the University of Maine are developing acoustic sensors designed "
    "to operate inside fusion machines that will endure extreme heat and radiation "
    "over long durations, a project funded by the Department of Energy and expected "
    "to run for several years beyond the initial phase."
)


class HeadlineTests(unittest.TestCase):
    def test_first_sentence_becomes_the_title(self):
        self.assertEqual(
            ei._headline(PARAGRAPH),
            "South Africa's National Nuclear Regulator says that no radioactive material "
            "leaked into the environment during three recent incidents at Koeberg.",
        )

    def test_title_never_ends_mid_word(self):
        """고치려던 그 버그다 — '...that w' 같은 파편이 제목 자리에 오면 안 된다."""
        for text in (PARAGRAPH, LONG_SENTENCE):
            with self.subTest(text=text[:30]):
                title = ei._headline(text)
                self.assertTrue(title)
                self.assertLessEqual(len(title), ei.HEADLINE_LIMIT)
                # 마지막 토큰이 원문에 온전한 단어로 존재하는가 (잘린 조각이 아니라)
                def words(s):
                    return {w.strip(".…!?,;:") for w in s.split()}

                last = title.rstrip(".…!?").split()[-1].strip(".…!?,;:")
                self.assertIn(last, words(text),
                              f"제목이 단어 중간에서 잘렸다: ...{title[-30:]!r}")

    def test_overlong_sentence_is_shortened_at_a_word_boundary(self):
        title = ei._headline(LONG_SENTENCE)
        self.assertLessEqual(len(title), ei.HEADLINE_LIMIT)
        self.assertTrue(title.endswith("…"))

    def test_blank_text_yields_no_title(self):
        # 제목을 못 만들면 기사로 만들지 않는다 — 빈 제목은 큐레이션이 못 읽는다.
        self.assertEqual(ei._headline("   "), "")
        self.assertEqual(ei._headline(None), "")

    def test_whitespace_is_collapsed(self):
        self.assertEqual(ei._headline("Reactor  restart\n approved  today."),
                         "Reactor restart approved today.")


class CandidateExtractionTests(unittest.TestCase):
    """추출 단계의 계약은 그대로여야 한다 — 제목 형태만 바뀐 수리다."""

    HTML = (
        "<p>" + PARAGRAPH + " <a href='https://example.com/story'>Reuters</a></p>"
        "<p>Short nav</p>"
        "<p>Please <a href='https://example.com/unsubscribe'>unsubscribe</a> here "
        "if you no longer wish to receive these daily newsletter messages.</p>"
    )

    def test_junk_and_short_blocks_are_dropped(self):
        cands = ei._extract_candidates(self.HTML)
        self.assertEqual([href for _text, href in cands], ["https://example.com/story"])

    def test_extracted_text_keeps_more_than_the_headline(self):
        # description 이 제목보다 길어야 큐레이션이 볼 재료가 남는다.
        (text, _href), = ei._extract_candidates(self.HTML)
        self.assertGreater(len(text), len(ei._headline(text)))


if __name__ == "__main__":
    unittest.main()
