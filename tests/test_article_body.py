"""article_body.py 단위 테스트 — 추출·본인확인·실패 통계. 외부 호출 0.

계약의 근거는 사용자 지적(2026-08-07)이다.
  "지금 ai가 대충 제목만 보고 요약하는 것 같아서 내용이 제대로 안 담겨있는 경우가 많음."
그 진단은 실측으로 맞았다 — 모델이 받던 것은 제목 150자 + RSS 요약 200자뿐이고,
Google News 경유 기사(전체의 51%)는 그 요약마저 제목의 재탕이다.

이 모듈이 지켜야 하는 두 가지:
  ① **본문을 저장하지 않는다.** 아카이브·큐·웹 산출물 어디에도 넣지 않는다.
  ② **못 가져오면 조용히 물러난다.** 본문 없이 도는 경로가 이미 있고 그쪽이 안전하다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import article_body as ab


PAGE = """<!doctype html><html><head>
<meta property="og:description" content="다뉴브강 수위 하락으로 헝가리 팍스 원전 4기 가운데 3기가 가동을 멈췄다는 소식이다.">
</head><body>
<nav><p>메뉴 링크가 잔뜩 들어 있는 내비게이션 영역이라 본문이 아니다</p></nav>
<article>
<p>헝가리 팍스 원자력발전소 4기 가운데 3기가 8월 6일 가동을 멈췄다고 현지 언론이 보도했다.</p>
<p>다뉴브강 수위가 취수 기준선 아래로 내려가면서 냉각수 확보가 불가능해진 것이 원인이다.</p>
<p>나머지 1기도 출력을 절반으로 낮춰 운전 중이며 헝가리 정부는 전력 수급 대책을 검토하고 있다.</p>
<p>팍스 원전은 헝가리 전력 생산의 약 40%를 담당해 왔으며 이번 정지로 수입 전력 의존도가 높아질 전망이다.</p>
<p>루마니아 체르나보다 원전도 같은 이유로 출력을 낮춘 상태이며 양국은 공동 대응을 협의하고 있다.</p>
<p>짧음</p>
<p>무단 전재 및 재배포 금지. 저작권자 © 예시신문</p>
</article>
<script>var x = "본문처럼 보이는 스크립트 문자열이 들어 있어도 걸러져야 한다";</script>
</body></html>"""


class ExtractTests(unittest.TestCase):
    def test_paragraphs_are_kept_and_boilerplate_is_dropped(self):
        body = ab.extract_text(PAGE)
        self.assertIn("팍스 원자력발전소 4기 가운데 3기", body)
        self.assertIn("다뉴브강 수위가 취수 기준선", body)
        self.assertNotIn("내비게이션", body)
        self.assertNotIn("무단 전재", body)
        self.assertNotIn("스크립트 문자열", body)
        self.assertNotIn("짧음", body)

    def test_pages_without_p_tags_fall_back_to_block_split(self):
        """<br> 로만 줄을 나누는 국내 매체가 많다 — 실측 thin 16건의 대부분이었다."""
        page = ("<body><div id='content'>"
                "원자력안전위원회가 고리 3·4호기 계속운전 심의를 하반기에 착수한다고 밝혔다.<br>"
                "심의는 운영변경허가 절차에 따라 진행되며 결과는 내년 상반기에 나온다.<br>"
                "한국수력원자력은 앞서 주기적 안전성 평가 보고서를 제출했으며 추가 자료를 준비 중이다.<br>"
                "지역 주민 의견 수렴 절차도 함께 진행되며 공청회 일정은 아직 확정되지 않았다고 밝혔다.<br>"
                "원안위는 심의 과정에서 설비 건전성과 방사선 환경영향평가를 함께 검토할 방침이다.<br>"
                "고리 3호기와 4호기의 설계수명은 각각 2024년과 2025년에 만료돼 현재는 정지 상태로 관리되고 있다.<br>"
                "한수원은 계속운전이 승인되면 최대 10년간 추가 운전이 가능하다고 설명했다고 전해졌다.<br>"
                "</div></body>")
        body = ab.extract_text(page)
        self.assertIn("고리 3·4호기 계속운전 심의", body)
        self.assertIn("주기적 안전성 평가", body)

    def test_meta_description_is_the_last_resort(self):
        page = "<html><head><meta name='description' content='%s'></head><body></body></html>" % (
            "다뉴브강 수위 하락으로 헝가리 팍스 원전 3기가 가동을 멈췄고 나머지 1기도 출력을 낮췄다는 내용의 기사다.")
        self.assertIn("팍스 원전 3기", ab.extract_text(page))

    def test_nothing_usable_returns_empty_not_garbage(self):
        self.assertEqual(ab.extract_text("<html><body><p>짧다</p></body></html>"), "")
        self.assertEqual(ab.extract_text(""), "")

    def test_limit_cuts_at_a_sentence_boundary(self):
        page = "<body>" + "".join(
            f"<p>{'가' * 60}{i}번째 문장이며 여기서 문장이 끝난다고 표시한다.</p>" for i in range(10)
        ) + "</body>"
        body = ab.extract_text(page, limit=200)
        self.assertLessEqual(len(body), 200)
        # 자른 자리가 문장 중간이면 모델이 잘린 절을 사실로 읽는다.
        self.assertTrue(body.endswith("다.") or body.endswith("다"), body[-20:])


class TitleMatchTests(unittest.TestCase):
    """엉뚱한 페이지를 긁어오면 그 오류가 그대로 요약이 된다.

    프롬프트가 "제목과 본문이 어긋나면 본문이 우선"이라고 지시하기 때문에 이
    확인이 없으면 잘못된 본문이 제목을 이긴다. 판정할 수 없으면 본문을 버린다.
    """

    def test_matching_body_passes(self):
        self.assertTrue(ab.matches_title(
            "헝가리 팍스 원전 4기 중 3기가 다뉴브강 수위 하락으로 멈췄다.",
            "헝가리 팍스 원전, 다뉴브강 수위 하락으로 3기 가동 중단"))

    def test_josa_does_not_break_the_match(self):
        # keei_match 선행 사례: '영덕군과' ≠ '영덕군' 때문에 진짜 매칭이 탈락할 뻔했다.
        self.assertTrue(ab.matches_title(
            "한국수력원자력은 영덕군과 부지 협약을 맺었다고 밝혔다. 협약은 이달 발효된다.",
            "한수원, 영덕 부지 협약 체결"))

    def test_unrelated_body_is_rejected(self):
        self.assertFalse(ab.matches_title(
            "삼성전자 선물이 상승한 가운데 SK하이닉스 선물은 하락했다. 배터리주도 올랐다.",
            "헝가리 팍스 원전, 다뉴브강 수위 하락으로 3기 가동 중단"))

    def test_no_title_is_not_a_rejection(self):
        self.assertTrue(ab.matches_title("아무 본문", ""))


class FetchTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, text="", status=200):
            self.text = text
            self.status_code = status
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"

    class FakeSession:
        def __init__(self, pages=None, status=200):
            self.pages = pages or {}
            self.status = status
            self.requested = []

        def get(self, url, **kwargs):
            self.requested.append(url)
            return FetchTests.FakeResponse(self.pages.get(url, ""), self.status)

    def test_blocked_domain_is_not_fetched(self):
        session = self.FakeSession()
        body, status = ab.fetch_one("https://www.reuters.com/x", session)
        self.assertEqual(body, "")
        self.assertEqual(status, "blocked_domain")
        self.assertEqual(session.requested, [], "차단 도메인은 요청조차 하지 않는다")

    def test_http_error_is_reported_not_raised(self):
        body, status = ab.fetch_one("https://example.com/a", self.FakeSession(status=403))
        self.assertEqual(body, "")
        self.assertEqual(status, "http_403")

    def test_title_mismatch_is_its_own_status(self):
        session = self.FakeSession({"https://example.com/a": PAGE})
        body, status = ab.fetch_one("https://example.com/a", session,
                                    "삼성전자 선물 상승, SK하이닉스 하락")
        self.assertEqual(body, "")
        self.assertEqual(status, "title_mismatch")

    def test_fetch_bodies_keys_by_hash_and_counts_reasons(self):
        session = self.FakeSession({"https://example.com/a": PAGE})
        articles = [
            {"hash": "h1", "link": "https://example.com/a", "title": "팍스 원전 가동 중단"},
            {"hash": "h2", "link": "https://www.reuters.com/x", "title": "무엇"},
        ]
        bodies, stats = ab.fetch_bodies(
            articles, workers=1, session_factory=lambda: session)
        self.assertIn("h1", bodies)
        self.assertNotIn("h2", bodies, "실패한 기사는 키가 없어야 한다")
        self.assertEqual(stats["ok"], 1)
        self.assertEqual(stats["attempted"], 2)
        self.assertEqual(stats["reasons"]["blocked_domain"], 1)

    def test_cap_defers_the_rest_instead_of_dropping_silently(self):
        articles = [{"hash": f"h{i}", "link": "https://www.reuters.com/x", "title": "t"}
                    for i in range(5)]
        _bodies, stats = ab.fetch_bodies(
            articles, max_fetch=2, workers=1, session_factory=self.FakeSession)
        self.assertEqual(stats["attempted"], 2)
        self.assertEqual(stats["deferred"], 3)


if __name__ == "__main__":
    unittest.main()
