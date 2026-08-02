"""KEEI 인사이트 수집·목차 추출·LLM 매칭 판정 테스트.

목차는 제목 줄만 저장한다(저작권) — 본문 문단이 새어 들어가지 않는지 고정한다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import keei_match
import pubs_fetch


LIST_HTML = """
<table><tbody>
<tr><td><a href="?mid=a10102050000&bid=0002&act=view&list_no=127887">[격주간] 세계 원전시장 인사이트(2026.07.24.)</a></td>
    <td><a href="?...&list_no=127887">바로보기</a></td></tr>
<tr><td><a href="?mid=a10102050000&bid=0002&act=view&list_no=127790">[격주간] 세계 원전시장 인사이트(2026.7.10)</a></td></tr>
<tr><td><a href="?mid=a10102050000&bid=0002&act=view&list_no=127700">[격주간] 세계 원전시장 인사이트(2025. 6. 26.)</a></td></tr>
<tr><td><a href="?mid=a10102050000&bid=0002&act=view&list_no=127999">[격주간] 국제유가 및 시장 동향(2026.07.30.)</a></td></tr>
</tbody></table>
"""

DETAIL_HTML = """
<p>본문으로 바로가기</p>
<p>에너지경제연구원의 새로운소식을 전하고 소통합니다.</p>
<p>□현안이슈</p>
<p>•전 세계 방사성동위원소 산업 현황 (NEA 보고서)</p>
<p>1. 들어가며</p>
<p>2. 방사성동위원소 개요</p>
<p>5. 시사점</p>
<p>□ 주요단신</p>
<p>• 미 NRC, 환경심사 규정 개정안 발표</p>
<p>• 유럽투자은행, 루마니아 Cernavod&#227; 1호기 설비개선 대출 승인</p>
<p>• 기타 단신</p>
"""


class KeeiParseTests(unittest.TestCase):
    def test_date_regex_tolerates_format_drift(self):
        cases = {
            "[격주간] 세계 원전시장 인사이트(2026.07.24.)": "2026-07-24",
            "[격주간] 세계 원전시장 인사이트(2026.7.10)": "2026-07-10",
            "[격주간] 세계 원전시장 인사이트(2025. 6. 26.)": "2025-06-26",
            "제목에 날짜 없음": "",
        }
        for title, expected in cases.items():
            self.assertEqual(pubs_fetch._keei_date(title), expected, title)

    def test_toc_keeps_headings_only_and_drops_body(self):
        toc = pubs_fetch.keei_parse_toc(DETAIL_HTML)
        self.assertEqual(toc["issue_title"], "전 세계 방사성동위원소 산업 현황 (NEA 보고서)")
        self.assertEqual(len(toc["briefs"]), 2)
        self.assertIn("미 NRC, 환경심사 규정 개정안 발표", toc["briefs"])
        # 소절 번호·안내문·'기타 단신'은 목차가 아니다
        blob = json.dumps(toc, ensure_ascii=False)
        for leaked in ("들어가며", "시사점", "기타 단신", "본문으로 바로가기", "소통합니다"):
            self.assertNotIn(leaked, blob, f"본문이 새어 들어감: {leaked}")

    def test_toc_handles_page_without_sections(self):
        self.assertEqual(pubs_fetch.keei_parse_toc("<p>아무 내용</p>"),
                         {"issue_title": "", "briefs": []})


class KeeiFetchTests(unittest.TestCase):
    def setUp(self):
        self._orig = pubs_fetch._http_get
        self.addCleanup(lambda: setattr(pubs_fetch, "_http_get", self._orig))
        pubs_fetch._http_get = self.fake_get

    @staticmethod
    def fake_get(url):
        return DETAIL_HTML if "act=view" in url else LIST_HTML

    def test_bootstrap_takes_recent_issues_and_skips_other_publications(self):
        state = {}
        items = pubs_fetch.fetch_keei(state)
        self.assertEqual(len(items), 3)
        # 같은 게시판의 다른 간행물(국제유가)은 list_no 가 더 커도 제외된다
        self.assertTrue(all("원전시장 인사이트" in item["title"] for item in items))
        # 다만 max 는 게시판 전체 기준이 아니라 이 간행물 기준이어야 재감지된다
        self.assertEqual(state["keei_max_list_no"], 127887)
        first = items[0]
        self.assertEqual(first["date"], "2026-07-24")
        self.assertIn("list_no=127887&seq=1", first["pdf_url"])
        self.assertEqual(first["toc"]["issue_title"],
                         "전 세계 방사성동위원소 산업 현황 (NEA 보고서)")

    def test_incremental_returns_only_new_issues(self):
        state = {"keei_max_list_no": 127790}
        items = pubs_fetch.fetch_keei(state)
        self.assertEqual([item["date"] for item in items], ["2026-07-24"])

    def test_backlog_larger_than_detail_cap_is_never_lost(self):
        """워터마크는 최댓값으로 올라가므로 항목을 자르면 영구 유실된다.

        실측(2026-08-02): 6호가 한꺼번에 올라온 상황에서 상세 상한 4에 맞춰
        항목까지 자르는 바람에 2호가 다음 실행에서 '신규'가 아니게 되어 사라졌다.
        상세(추가 요청)만 제한하고 항목은 전부 내보내야 한다.
        """
        backlog = "\n".join(
            f'<a href="?act=view&list_no={no}">[격주간] 세계 원전시장 인사이트(2026.0{i}.10.)</a>'
            for i, no in enumerate(range(128001, 128007), start=1))
        detail_calls = []

        def fake(url):
            if "act=view" in url:
                detail_calls.append(url)
                return DETAIL_HTML
            return backlog
        pubs_fetch._http_get = fake

        state = {"keei_max_list_no": 128000}
        items = pubs_fetch.fetch_keei(state)
        self.assertEqual(len(items), 6, "상세 상한 때문에 호가 유실되면 안 된다")
        self.assertEqual(len(detail_calls), pubs_fetch.KEEI_MAX_DETAIL,
                         "상세 요청은 상한을 지켜야 한다")
        self.assertEqual(sum(1 for item in items if item.get("toc")),
                         pubs_fetch.KEEI_MAX_DETAIL)
        # 다음 실행에 남는 것이 없어야 정상(전부 이미 내보냈으므로)
        self.assertEqual(pubs_fetch.fetch_keei(state), [])

    def test_toc_failure_does_not_drop_the_item(self):
        def flaky(url):
            if "act=view" in url:
                raise RuntimeError("상세 페이지 500")
            return LIST_HTML
        pubs_fetch._http_get = flaky
        items = pubs_fetch.fetch_keei({})
        self.assertEqual(len(items), 3)
        self.assertNotIn("toc", items[0])


class FakeClient:
    MODEL = "fake"

    def __init__(self, responses=None, available=True, raises=False):
        self.responses = list(responses or [])
        self._available = available
        self.raises = raises
        self.calls = []

    def is_available(self):
        return self._available

    def call_json(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if self.raises:
            raise RuntimeError("429 rate limited")
        return self.responses.pop(0) if self.responses else {"items": []}


def candidate(index, same_hint=""):
    return {"pair_id": f"issue{index}--abc{index}",
            "issue_title": f"이슈 제목 {index}",
            "keei_item": f"KEEI 항목 {index}{same_hint}"}


class KeeiMatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "keei_llm_matches.json"

    def test_no_api_key_attaches_nothing(self):
        client = FakeClient(available=False)
        verdicts, stats = keei_match.match_pairs(
            [candidate(0)], cache_path=self.cache, client=client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(client.calls, [])

    def test_llm_failure_attaches_nothing_and_is_not_cached(self):
        client = FakeClient(raises=True)
        verdicts, stats = keei_match.match_pairs(
            [candidate(0)], cache_path=self.cache, client=client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["failed"], 1)
        self.assertFalse(self.cache.exists(), "실패는 캐시하면 안 된다")

    def test_verdicts_are_cached_and_reused(self):
        client = FakeClient([{"items": [
            {"idx": 0, "same_event": True, "reason": "동일 사안"},
            {"idx": 1, "same_event": False, "reason": "다른 원전"},
        ]}])
        pairs = [candidate(0), candidate(1)]
        verdicts, stats = keei_match.match_pairs(
            pairs, cache_path=self.cache, client=client)
        self.assertEqual(verdicts, {"issue0--abc0": True, "issue1--abc1": False})
        self.assertEqual(stats["approved"], 1)
        self.assertEqual(stats["rejected"], 1)

        again = FakeClient()
        verdicts2, stats2 = keei_match.match_pairs(
            pairs, cache_path=self.cache, client=again)
        self.assertEqual(verdicts2, verdicts)
        self.assertEqual(stats2["from_cache"], 2)
        self.assertEqual(again.calls, [], "캐시가 있으면 다시 묻지 않는다")

    def test_prompt_version_bump_invalidates_cache(self):
        client = FakeClient([{"items": [{"idx": 0, "same_event": True, "reason": "x"}]}])
        keei_match.match_pairs([candidate(0)], cache_path=self.cache, client=client)
        original = keei_match.PROMPT_VERSION
        try:
            keei_match.PROMPT_VERSION = original + 1
            fresh = FakeClient([{"items": [{"idx": 0, "same_event": False, "reason": "y"}]}])
            verdicts, stats = keei_match.match_pairs(
                [candidate(0)], cache_path=self.cache, client=fresh)
            self.assertEqual(stats["from_cache"], 0)
            self.assertEqual(verdicts["issue0--abc0"], False)
        finally:
            keei_match.PROMPT_VERSION = original

    def test_missing_idx_in_response_is_counted_failed_not_attached(self):
        client = FakeClient([{"items": [{"idx": 0, "same_event": True, "reason": "x"}]}])
        verdicts, stats = keei_match.match_pairs(
            [candidate(0), candidate(1)], cache_path=self.cache, client=client)
        self.assertIn("issue0--abc0", verdicts)
        self.assertNotIn("issue1--abc1", verdicts)
        self.assertEqual(stats["failed"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
