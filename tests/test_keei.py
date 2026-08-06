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
        # 목차를 못 채운 호는 다음 실행에서 다시 가져와 채운다. 워터마크는 이미
        # 최댓값이라 여기서 챙기지 않으면 그 호들은 영영 목차를 못 얻고,
        # 목차가 없으면 이슈 매칭 대상에도 들어가지 못한다.
        self.assertEqual(state["keei_pending_toc"], [128002, 128001])
        second = pubs_fetch.fetch_keei(state)
        self.assertEqual(len(second), 2)
        self.assertTrue(all(item.get("toc") for item in second),
                        "재방문에서는 목차가 채워져야 한다")
        self.assertEqual(state["keei_pending_toc"], [])
        # 전부 채운 뒤에는 더 가져올 것이 없다
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

    def test_rejection_reopens_when_the_issue_title_changes(self):
        """거부 판정은 그때의 제목에 대한 것이다 — 제목이 바뀌면 다시 묻는다.

        실측 2026-08-06 (캐시 169건 중 이슈가 살아있는 쌍 147건, 제목 드리프트 25건):
            현재 이슈  "중국 타이핑링 2호기 원자력발전소 상업운전 개시"
            KEEI      "중국 Taipingling 원전 2호기, 최초 계통연결 완료"
        같은 호기인데 판정 당시 제목이 "중국 창장 3호기"라 거부돼 있었다.
        """
        first = FakeClient([{"items": [{"idx": 0, "same_event": False, "reason": "다른 호기"}]}])
        old = dict(candidate(0), issue_title="중국 창장 3호기 원전, 전력망 연결 및 상업 운전 개시")
        keei_match.match_pairs([old], cache_path=self.cache, client=first)

        moved = dict(candidate(0), issue_title="중국 타이핑링 2호기 원자력발전소 상업운전 개시")
        second = FakeClient([{"items": [{"idx": 0, "same_event": True, "reason": "같은 호기"}]}])
        verdicts, stats = keei_match.match_pairs(
            [moved], cache_path=self.cache, client=second)
        self.assertEqual(verdicts["issue0--abc0"], True)
        self.assertEqual((stats["from_cache"], stats["reasked"]), (0, 1))
        stored = json.loads(self.cache.read_text(encoding="utf-8"))["matches"]["issue0--abc0"]
        self.assertIn("타이핑링", stored["issue_title"])

    def test_unchanged_title_still_uses_the_cache(self):
        client = FakeClient([{"items": [{"idx": 0, "same_event": False, "reason": "다름"}]}])
        keei_match.match_pairs([candidate(0)], cache_path=self.cache, client=client)
        again = FakeClient()
        verdicts, stats = keei_match.match_pairs(
            [candidate(0)], cache_path=self.cache, client=again)
        self.assertEqual(verdicts["issue0--abc0"], False)
        self.assertEqual((again.calls, stats["reasked"]), ([], 0))

    def test_approval_is_never_reopened(self):
        """승인은 이미 연결됐다 — 제목이 바뀌어도 되돌릴 것이 없다."""
        client = FakeClient([{"items": [{"idx": 0, "same_event": True, "reason": "같음"}]}])
        keei_match.match_pairs([candidate(0)], cache_path=self.cache, client=client)
        moved = dict(candidate(0), issue_title="완전히 다른 제목으로 바뀜")
        again = FakeClient()
        verdicts, stats = keei_match.match_pairs(
            [moved], cache_path=self.cache, client=again)
        self.assertEqual(verdicts["issue0--abc0"], True)
        self.assertEqual((again.calls, stats["reasked"]), ([], 0))

    def test_reask_is_capped_and_deferred_not_dropped(self):
        """제목이 요동치는 날 재질의가 폭주하면 큐레이션과 같은 버킷을 두고 다툰다."""
        rows = [candidate(i) for i in range(15)]
        seed = FakeClient([{"items": [{"idx": i, "same_event": False, "reason": "x"}
                                      for i in range(15)]}])
        keei_match.match_pairs(rows, cache_path=self.cache, client=seed)
        moved = [dict(row, issue_title=f"바뀐 제목 {i}") for i, row in enumerate(rows)]
        again = FakeClient([{"items": [{"idx": i, "same_event": False, "reason": "y"}
                                       for i in range(10)]}])
        _verdicts, stats = keei_match.match_pairs(
            moved, cache_path=self.cache, client=again)
        self.assertEqual(stats["reasked"], keei_match.MAX_REASK_PER_RUN)
        self.assertEqual(stats["reask_deferred"], 15 - keei_match.MAX_REASK_PER_RUN)

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

            # 재판정 결과가 **디스크에** 반영돼야 한다. 크기 비교로 저장 여부를
            # 정하면 덮어쓰기는 크기가 그대로라 영영 저장되지 않고, 매 빌드
            # 같은 쌍을 다시 묻게 된다(비용 폭주, 로그엔 이상 없음).
            stored = json.loads(self.cache.read_text(encoding="utf-8"))
            self.assertEqual(stored["prompt_version"], original + 1)
            self.assertEqual(stored["matches"]["issue0--abc0"]["same_event"], False)

            again = FakeClient()
            _, stats2 = keei_match.match_pairs(
                [candidate(0)], cache_path=self.cache, client=again)
            self.assertEqual(again.calls, [], "버전 bump 뒤 재실행이 또 물어보면 안 된다")
            self.assertEqual(stats2["from_cache"], 1)
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
