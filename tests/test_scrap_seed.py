import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scrap_seed_ingest as ssi

REPORT = """8월 31일 석간스크랩 보고

■ 에너지
1.[헤럴드경제 027면] 전기에도 색깔을 입혀보자 (민병권 국가과학기술연구회 연구전략 본부장)
2.[전기신문] 12차 전기본 초안 공개, 원전 비중 유지
3.[한국일보 A16면] 전력망, 민간이 건설해 한전에 판매 길 열려

■ 기타
쓸데없는 줄은 무시된다
"""

# 실물 형식 재현 — 섹션 헤더 + "제목 (매체명)" + 단축링크 줄 (코드는 가짜)
TREND = """9.3 언론 동향 17:00 기준

[종합일간지]
정부 주도 ‘보안 특화 AI’ 구축 (동아일보)
https://surl.example.com/aaa000

사이버 위협 막는 '보안 AI' 만든다 (한국일보)
https://surl.example.com/bbb111
"""


class ParseTests(unittest.TestCase):
    def test_parses_publisher_page_and_title(self):
        seeds = ssi.parse_scrap_report(REPORT, 2026)
        self.assertEqual(len(seeds), 3)
        self.assertEqual(seeds[0]["date"], "2026-08-31")
        self.assertEqual(seeds[0]["publisher"], "헤럴드경제")  # "027면" 은 제거
        # 저자·직함 꼬리는 제거 — 네이버 쿼리를 죽이고 토큰 포함률을 희석한다
        self.assertEqual(seeds[0]["title"], "전기에도 색깔을 입혀보자")
        self.assertEqual(seeds[1]["publisher"], "전기신문")  # 면 번호 없는 꼴
        self.assertEqual(seeds[2]["publisher"], "한국일보")  # "A16면" 꼴도 제거

    def test_non_report_text_returns_empty(self):
        self.assertEqual(ssi.parse_scrap_report("오늘 점심 뭐 먹지", 2026), [])

    def test_edition_captured_from_header(self):
        # 헤더의 조간|석간이 시드마다 실린다 — 웹 탭에서 '석간 누락'을 보이게
        # 하는 라벨. 라벨 없는 헤더는 빈 문자열(구형 보고 호환).
        seeds = ssi.parse_scrap_report(REPORT, 2026)
        self.assertTrue(seeds)
        self.assertTrue(all(s["edition"] == "석간" for s in seeds))
        plain = ssi.parse_scrap_report("9월 1일 스크랩 보고\n1.[전기신문] 제목", 2026)
        self.assertEqual(plain[0]["edition"], "")
        # seed_key 는 edition 과 무관 — 키 안정성 유지.
        with_ed = dict(plain[0], edition="조간")
        self.assertEqual(ssi.seed_key(plain[0]), ssi.seed_key(with_ed))

    def test_parses_media_trend_with_links(self):
        seeds = ssi.parse_media_trend(TREND, 2026)
        self.assertEqual(len(seeds), 2)
        self.assertEqual(seeds[0]["date"], "2026-09-03")
        self.assertEqual(seeds[0]["publisher"], "동아일보")
        self.assertEqual(seeds[0]["link"], "https://surl.example.com/aaa000")
        self.assertIn("보안 특화 AI", seeds[0]["title"])
        # 섹션 헤더([종합일간지])는 시드가 되지 않는다
        self.assertTrue(all("종합일간지" not in s["title"] for s in seeds))

    def test_media_trend_ignores_other_text(self):
        self.assertEqual(ssi.parse_media_trend("오늘 점심 뭐 먹지", 2026), [])

    def test_seed_key_is_stable(self):
        seed = {"date": "2026-08-31", "publisher": "전기신문", "title": "제목"}
        self.assertEqual(ssi.seed_key(seed), ssi.seed_key(dict(seed)))


class MatchTests(unittest.TestCase):
    def test_online_title_with_subtitle_still_matches(self):
        # 지면 제목이 온라인 제목 안에 들어 있으면(부제가 붙어도) 잡힌다
        self.assertGreaterEqual(
            ssi.title_overlap("12차 전기본 초안 공개, 원전 비중 유지",
                              "12차 전기본 초안 공개…원전 비중 유지·재생에너지 확대"),
            ssi.MATCH_THRESHOLD)

    def test_unrelated_title_does_not_match(self):
        self.assertLess(
            ssi.title_overlap("전기에도 색깔을 입혀보자",
                              "한수원, 체코 신규 원전 계약 체결"),
            ssi.MATCH_THRESHOLD)


class LedgerTests(unittest.TestCase):
    def _run(self, state, seeds, naver_items, already_sent=False, webkr_items=None,
             gnews_items=None):
        search = mock.MagicMock(return_value=naver_items)
        webkr = mock.MagicMock(return_value=webkr_items or [])
        gnews = mock.MagicMock(return_value=gnews_items or [])
        with mock.patch.object(ssi, "load_seeds", return_value=seeds), \
             mock.patch.dict(sys.modules, {"news_bot": mock.MagicMock(
                 search_naver=search,
                 search_naver_webkr=webkr,
                 fetch_rss=gnews,
                 article_seen=mock.MagicMock(return_value=already_sent),
                 get_domain=lambda url: "electimes.com",
                 url_hash=lambda url: "h" + url[-4:],
                 strip_html=lambda t: t,
                 source_profile=lambda d: {"publisher": "전기신문"},
             )}):
            result = ssi.fetch_scrap_seed_articles(state)
        self._search_calls = search.call_count
        self._webkr_calls = webkr.call_count
        return result

    def test_resolves_and_marks_ledger(self):
        from datetime import datetime
        seeds = [{"date": datetime.now(ssi.KST).date().isoformat(),
                  "publisher": "전기신문", "title": "12차 전기본 초안 공개, 원전 비중 유지"}]
        state = {"sent": {}}
        items = [{"originallink": "https://electimes.com/a123",
                  "title": "12차 전기본 초안 공개…원전 비중 유지"}]
        articles = self._run(state, seeds, items)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["matched"], "사내스크랩")
        row = state["scrap_seeds"][ssi.seed_key(seeds[0])]
        self.assertEqual(row["status"], "resolved")
        # 아직 sent 안 됐으면(429 유실 등) 재검색 없이 재주입
        reinjected = self._run(state, seeds, items)
        self.assertEqual(len(reinjected), 1)
        self.assertEqual(self._search_calls, 0)
        # sent 되면 재주입도 끝
        self.assertEqual(self._run(state, seeds, items, already_sent=True), [])
        self.assertEqual(self._search_calls, 0)

    def test_backoff_and_give_up(self):
        from datetime import datetime
        seeds = [{"date": datetime.now(ssi.KST).date().isoformat(),
                  "publisher": "전기신문", "title": "아무도 안 쓴 제목"}]
        state = {"sent": {}}
        self.assertEqual(self._run(state, seeds, []), [])
        row = state["scrap_seeds"][ssi.seed_key(seeds[0])]
        self.assertEqual(row["tries"], 1)
        # 백오프 창 안이라 재시도 안 함
        self.assertEqual(self._run(state, seeds, []), [])
        self.assertEqual(row["tries"], 1)
        # 상한 도달 시 gave_up
        row["tries"] = ssi.SEED_MAX_TRIES
        row.pop("last_tried")
        self._run(state, seeds, [])
        self.assertEqual(row["status"], "gave_up")

    def test_stale_seed_skipped_and_ledger_pruned(self):
        seeds = [{"date": "2026-01-01", "publisher": "전기신문", "title": "옛날 기사"}]
        state = {"sent": {}, "scrap_seeds": {"deadbeef0000": {"seed_date": "2026-01-01"}}}
        self.assertEqual(self._run(state, seeds, []), [])
        self.assertEqual(state["scrap_seeds"], {})

    def test_webkr_fallback_rescues_non_affiliated_press(self):
        """뉴스 검색 0건이어도 웹문서 검색이 지역지 자사 기사를 잡으면 resolve."""
        from datetime import datetime
        seeds = [{"date": datetime.now(ssi.KST).date().isoformat(),
                  "publisher": "경상투데이", "title": "울진군, 대규모 청정수소 생산 가능성 강조"}]
        state = {"sent": {}}
        webkr = [
            {"title": "TARGET_noun.xls", "link": "https://example.com/file.xls"},
            {"title": "울진군, 대규모 청정수소 생산 가능성 강조",
             "link": "https://www.gyeongsangtoday.com/news/view.php?idx=1234"},
        ]
        articles = self._run(state, seeds, [], webkr_items=webkr)
        self.assertEqual(len(articles), 1)
        self.assertIn("gyeongsangtoday", articles[0]["link"])
        row = state["scrap_seeds"][ssi.seed_key(seeds[0])]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(self._webkr_calls, 1)

    def test_gnews_fallback_after_naver_and_webkr(self):
        """네이버 뉴스·웹문서 모두 실패해도 구글뉴스 RSS 가 잡으면 resolve."""
        from datetime import datetime
        seeds = [{"date": datetime.now(ssi.KST).date().isoformat(),
                  "publisher": "경상매일신문",
                  "title": "사드 10년 버틴 성주 소성리 햇빛 소득 하반기 착공 눈앞"}]
        state = {"sent": {}}
        gnews = [{"title": "사드 10년 버틴 성주 소성리 햇빛 소득 하반기 착공 눈앞",
                  "link": "https://news.google.com/rss/articles/abc123"}]
        articles = self._run(state, seeds, [], gnews_items=gnews)
        self.assertEqual(len(articles), 1)
        self.assertIn("news.google.com", articles[0]["link"])
        self.assertEqual(self._webkr_calls, 1)  # 웹문서를 먼저 시도한 뒤

    def test_link_seed_resolves_without_search(self):
        from datetime import datetime
        seeds = [{"date": datetime.now(ssi.KST).date().isoformat(),
                  "publisher": "동아일보", "title": "링크 딸린 기사",
                  "link": "https://donga.com/a999"}]
        state = {"sent": {}}
        articles = self._run(state, seeds, [])
        self.assertEqual(self._search_calls, 0)  # 검색 없이
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["link"], "https://donga.com/a999")
        row = state["scrap_seeds"][ssi.seed_key(seeds[0])]
        self.assertEqual(row["status"], "resolved")

    def test_history_persists_after_ledger_prune(self):
        from datetime import datetime
        today = datetime.now(ssi.KST).date().isoformat()
        seeds = [{"date": today, "publisher": "전기신문",
                  "title": "12차 전기본 초안 공개, 원전 비중 유지"}]
        state = {"sent": {}}
        items = [{"originallink": "https://electimes.com/a123",
                  "title": "12차 전기본 초안 공개…원전 비중 유지"}]
        self._run(state, seeds, items)
        key = ssi.seed_key(seeds[0])
        self.assertIn(key, state["scrap_history"])
        self.assertEqual(state["scrap_history"][key]["link"], "https://electimes.com/a123")
        self.assertEqual(state["scrap_history"][key]["date"], today)
        # ledger 가 5일 롤링으로 지워져도 이력은 90일 창이라 남는다
        # (시드 0건이면 조기 반환이라 청소가 안 돈다 — 더미 시드로 청소를 트리거)
        state["scrap_seeds"][key]["seed_date"] = "2026-01-01"
        dummy = [{"date": today, "publisher": "전기신문", "title": "전혀 다른 기사"}]
        self._run(state, dummy, [])
        self.assertNotIn(key, state["scrap_seeds"])
        self.assertIn(key, state["scrap_history"])


if __name__ == "__main__":
    unittest.main()
