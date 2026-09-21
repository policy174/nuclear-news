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

    def test_generic_nuclear_vocabulary_alone_does_not_pass(self):
        """2026-08-10 라이브 실사고. 폴리뉴스 기사 하나가 **해외건설 수주** 본문을
        받아 그대로 이슈 상세의 '기사 내용'이 돼 있었다. 겹친 낱말은 '원전'·'대형'
        둘뿐 — 원자력 기사면 어디에나 있는 낱말이다. 짧은 제목에서 옛 기준
        (round(8*0.30)=2, 하한 2)이 딱 맞물려 통과했다.

        아래 본문은 그날 실제로 받아온 것을 줄인 것이다.
        """
        body = ("국내 건설사들이 올해 해외건설 수주 500억달러 목표 달성을 위해 "
                "대형 프로젝트 확보에 나서고 있다. 상반기 수주액이 지난해보다 크게 "
                "감소한 데다 중동 플랜트 발주에도 변수가 커지면서 원전과 발전·전력 "
                "인프라 등으로 수주 분야를 넓히는 모습이다.")
        self.assertFalse(ab.matches_title(
            body, "한수원, 신규 대형 원전 및 SMR 부지 후보지 선정"))


class SiteNameTests(unittest.TestCase):
    """본문 때문에 이미 받은 페이지에서 매체명은 공짜로 나온다."""

    def test_it_reads_og_site_name(self):
        self.assertEqual(
            ab.extract_site_name('<meta property="og:site_name" content="이데일리">'),
            "이데일리")

    def test_attribute_order_does_not_matter(self):
        self.assertEqual(
            ab.extract_site_name('<meta content="중앙일보" property="og:site_name">'),
            "중앙일보")

    def test_a_portal_prefix_is_removed(self):
        # 포털 미러는 자기 이름을 앞에 붙인다: `Daum | 노컷뉴스`.
        self.assertEqual(
            ab.extract_site_name('<meta property="og:site_name" content="Daum | 노컷뉴스">'),
            "노컷뉴스")

    def test_a_domain_shaped_value_is_no_better_than_what_we_have(self):
        self.assertEqual(
            ab.extract_site_name('<meta property="og:site_name" content="www.example.com">'),
            "")
        self.assertEqual(
            ab.extract_site_name('<meta property="og:site_name" content="kwnews.co.kr">'),
            "")

    def test_no_tag_is_not_an_error(self):
        self.assertEqual(ab.extract_site_name("<html><body>본문</body></html>"), "")

    def test_the_name_survives_a_rejected_body(self):
        """제목과 안 맞아 본문을 버리는 기사도 카드에는 실린다 — 매체명은 살려야 한다."""
        page = ('<meta property="og:site_name" content="이데일리">'
                '<p>전혀 다른 사건에 대한 긴 본문이다. ' + "다른 이야기. " * 30 + '</p>')

        class FakeResponse:
            status_code = 200
            encoding = "utf-8"
            text = page

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

        meta: dict = {}
        body, status = ab.fetch_one("https://example.com/a", FakeSession(),
                                    "헝가리 팍스 원전 다뉴브강 수위 하락 가동 중단", meta)
        self.assertEqual(status, "title_mismatch")
        self.assertEqual(body, "")
        self.assertEqual(meta["site_name"], "이데일리")


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

    def test_bot_blocked_article_is_retried_in_a_browser(self):
        """봇 차단(403)은 헤더로 못 푼다 — 실패한 건만 브라우저로 한 번 더 간다."""
        session = self.FakeSession({})          # 전부 실패시키는 세션
        articles = [{"hash": "h1", "link": "https://www.iaea.org/x",
                     "title": "IAEA 이사회, 신규 이사국 선출"}]
        called = {}

        def fake_browser(urls, **kw):
            called["urls"] = list(urls)
            return {"https://www.iaea.org/x":
                    "Eleven countries have been elected to the 35-member IAEA Board "
                    "of Governors for the 2026-2028 period. " * 3}

        original, ab.browser_bodies = ab.browser_bodies, fake_browser
        try:
            bodies, stats = ab.fetch_bodies(articles, workers=1,
                                            session_factory=lambda: session)
        finally:
            ab.browser_bodies = original
        self.assertEqual(called["urls"], ["https://www.iaea.org/x"])
        self.assertIn("h1", bodies, "브라우저가 받아온 본문이 실려야 한다")
        self.assertEqual(stats["browser_ok"], 1)

    def test_browser_body_of_a_different_article_is_still_dropped(self):
        session = self.FakeSession({})
        articles = [{"hash": "h1", "link": "https://www.iaea.org/x",
                     "title": "IAEA 이사회, 신규 이사국 선출"}]
        original, ab.browser_bodies = ab.browser_bodies, lambda urls, **kw: {
            "https://www.iaea.org/x": "The weather in Vienna is mild and tourists "
                                      "gather in the old town every afternoon. " * 3}
        try:
            bodies, stats = ab.fetch_bodies(articles, workers=1,
                                            session_factory=lambda: session)
        finally:
            ab.browser_bodies = original
        self.assertNotIn("h1", bodies)
        self.assertEqual(stats["reasons"]["browser_title_mismatch"], 1)

    def test_translated_title_matches_its_original_language_body(self):
        """번역된 한글 제목 + 영문 본문 — 라틴 토큰으로 판정한다."""
        body = ("Eleven countries have been elected to serve on the 35-member IAEA "
                "Board of Governors for the 2026-2028 period.")
        self.assertTrue(ab.matches_title(body, "IAEA 이사회, 신규 이사국 선출"))
        self.assertFalse(ab.matches_title(
            "The weather in Vienna is mild this week.", "IAEA 이사회, 신규 이사국 선출"))

    def test_cap_defers_the_rest_instead_of_dropping_silently(self):
        articles = [{"hash": f"h{i}", "link": "https://www.reuters.com/x", "title": "t"}
                    for i in range(5)]
        _bodies, stats = ab.fetch_bodies(
            articles, max_fetch=2, workers=1, session_factory=self.FakeSession)
        self.assertEqual(stats["attempted"], 2)
        self.assertEqual(stats["deferred"], 3)


if __name__ == "__main__":
    unittest.main()


class OgImageTests(unittest.TestCase):
    """전환점 썸네일용 og:image 추출.

    실측 2026-09-20(원문 90건): og:image 자체는 96%가 있는데 그중 상당수가
    기사 사진이 아니라 매체 로고·SNS 기본 이미지·기관 도장이다. 걸러야 79%가
    기사 고유 사진으로 남는다. 아래 세 케이스가 실제로 걸린 것들이다.
    """

    def test_real_article_photo_survives(self):
        html = ('<meta content="https://i3n.news1.kr/system/photos/2026/9/11/high.jpg" '
                'property="og:image">')
        self.assertEqual(ab.extract_og_image(html),
                         "https://i3n.news1.kr/system/photos/2026/9/11/high.jpg")

    def test_property_first_and_content_first_both_parse(self):
        """매체마다 meta 속성 순서가 다르다. 둘 다 안 보면 절반을 놓친다."""
        url = "https://img.hankyung.com/photo/202609/01.4150.jpg"
        self.assertEqual(
            ab.extract_og_image(f'<meta property="og:image" content="{url}">'), url)
        self.assertEqual(
            ab.extract_og_image(f'<meta content="{url}" property="og:image">'), url)

    def test_fixed_house_images_are_dropped(self):
        """로고·도장·SNS 기본 이미지는 그 기사의 사진이 아니다."""
        for junk in (
            "/images/core/logo.png",                       # motir.go.kr, 상대경로 + 로고
            "https://www.ytn.co.kr/img/comm/ytn_sns_default.jpg",
            "https://www.energy.gov/sites/default/files/DOE%20Full%20Seal.png",
            "https://static.mk.co.kr/facebook_mknews.jpg",
        ):
            with self.subTest(junk=junk):
                self.assertEqual(
                    ab.extract_og_image(
                        f'<meta property="og:image" content="{junk}">'), "")

    def test_missing_tag_is_not_an_error(self):
        """사진이 없는 것은 정상이다 — 화면이 '사진 없음' 칸으로 받는다."""
        self.assertEqual(ab.extract_og_image(""), "")
        self.assertEqual(ab.extract_og_image("<html><body>기사</body></html>"), "")


class OgEntityTests(unittest.TestCase):
    """meta content 는 HTML 이라 & 가 &amp; 로 온다. 안 풀면 URL 이 깨진다.

    2026-09-21 실측: g-enews 의 `?idx=5&amp;simg=…` 를 그대로 요청하면 9바이트
    text/html 이 오고(브라우저는 깨진 이미지로 처리), 디코드하면 같은 주소가
    84,792바이트 image/jpeg 다. 저장분 1,948건 중 118건이 이 상태였다.
    """

    def test_og_image_unescapes_entities(self):
        url = "https://n.example.com/a.php?idx=5&simg=b.jpg"
        page = '<meta property="og:image" content="%s">' % url.replace("&", "&amp;")
        self.assertEqual(ab.extract_og_image(page), url)

    def test_site_name_unescapes_entities(self):
        page = '<meta property="og:site_name" content="E&amp;E News">'
        self.assertEqual(ab.extract_site_name(page), "E&E News")
