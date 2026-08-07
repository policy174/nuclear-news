"""issue_insight.py 단위 테스트 — 재료 판정·재진술 차단·캐시. 외부 호출 0.

사용자 지적(2026-08-05)에서 나온 두 요구가 그대로 계약이다.
  ① "AI 헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다 → 내용이 너무 없어"
  ② "직전 브리핑 내용이 왜 들어가, 그럴거면 그 전꺼를 보겠지"
①의 답은 지우는 것이 아니라 **타임라인에서 끌어와 채우는 것**이고, ②는 그 자리를
직전 브리핑 문장으로 때우지 말라는 뜻이다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import issue_insight


PAKS_TIMELINE = [
    {"hash": "a1", "article_date": "2026-07-31",
     "title_kr": "다뉴브강 수위 최저치 기록, 헝가리·루마니아 원전 가동 중단",
     "summary": "다뉴브강 수위가 최저치를 기록하며 냉각수 취수가 제한됐다."},
    {"hash": "a2", "article_date": "2026-08-02",
     "title_kr": "헝가리 총리, 팍스 원전 일요일 가동 중단 발표", "summary": ""},
    {"hash": "a3", "article_date": "2026-08-05",
     "title_kr": "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표",
     "summary": "마지막 터빈이 안전하게 가동 중이라고 발표했다."},
]


def row(**overrides):
    base = {
        "issue_id": "issue-paks",
        "title": "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표",
        "summary": "헝가리 총리가 마지막 터빈이 안전하게 가동 중이라고 발표했다.",
        "implication": "",
        "last_seen": "2026-08-05",
        "related_articles": PAKS_TIMELINE,
    }
    base.update(overrides)
    return base


class FakeClient:
    def __init__(self, responses=None, raises=None):
        self.responses = list(responses or [])
        self.raises = raises
        self.kwargs = []
        self.messages = []

    def is_available(self):
        return True

    def call_json(self, system_prompt, user_message, **kwargs):
        self.messages.append(user_message)
        self.kwargs.append(kwargs)
        if self.raises:
            raise self.raises
        return self.responses.pop(0) if self.responses else {"items": []}


class TestNeedsInsight(unittest.TestCase):
    def test_single_article_issue_is_skipped(self):
        """기사 1건짜리는 끌어올 맥락이 없다 — 쓰게 하면 빈껍데기가 돌아온다."""
        self.assertFalse(issue_insight.needs_insight(
            row(related_articles=PAKS_TIMELINE[:1])))

    def test_empty_interpretation_with_timeline_is_a_candidate(self):
        self.assertTrue(issue_insight.needs_insight(row()))

    def test_hollow_interpretation_is_a_candidate(self):
        """사용자가 지적한 그 문장이 후보로 잡혀야 한다."""
        self.assertTrue(issue_insight.needs_insight(
            row(implication="헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다.")))

    def test_good_interpretation_is_left_alone(self):
        self.assertFalse(issue_insight.needs_insight(
            row(implication="다뉴브강 수위 저하로 예고됐던 전면 정지를 피했다.")))


class TestRestatementGuard(unittest.TestCase):
    """프롬프트로 금지해도 모델은 제목을 바꿔 쓴다 — 실측 첫 실행 7건 중 4건.

    유사도만으로는 못 가른다(실측): 겹침 0.63 인 포천양수 문장은 제목에 없는
    '1조 7,508억 원'을 담은 좋은 문장이었고, 겹침 0.42 인 IAEA 문장이 재진술이었다.
    갈라주는 것은 **제목에 없는 수치**다.
    """

    def test_paraphrase_without_new_facts_is_rejected(self):
        self.assertTrue(issue_insight._restates_title(
            "원자력 라이프사이클 혁신 캠퍼스 유치를 위한 후보지로 5개 주를 선정했다.",
            "미국 에너지부, 원자력 혁신 캠퍼스 유치 후보지 5개 주 선정 발표"))

    def test_new_quantity_rescues_a_similar_sentence(self):
        self.assertFalse(issue_insight._restates_title(
            "한국수력원자력이 1조 7,508억 원 규모의 포천양수발전소 본공사에 착수했다.",
            "한수원, 포천양수발전소 본공사 착수…2033년 준공 목표"))

    def test_a_cause_from_the_timeline_is_kept(self):
        self.assertFalse(issue_insight._restates_title(
            "다뉴브강 수위 저하로 냉각수 취수가 막혀 예고됐던 전면 정지를 피한 것이다.",
            "헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표"))

    def test_quantity_already_in_the_title_does_not_rescue(self):
        """'5개 주'가 제목에 있으면 그것을 되풀이해도 새 정보가 아니다."""
        self.assertTrue(issue_insight._restates_title(
            "원자력 혁신 캠퍼스 후보지 5개 주를 선정해 발표한 것이다.",
            "미국 에너지부, 원자력 혁신 캠퍼스 유치 후보지 5개 주 선정 발표"))


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmp.name) / "insights.json"

    def tearDown(self):
        self._tmp.cleanup()

    def run_one(self, insight, rows=None):
        client = FakeClient([{"items": [{"idx": 0, "insight": insight}]}])
        return issue_insight.generate(rows or [row()], client=client,
                                      cache_path=self.cache_path), client

    def test_timeline_is_in_the_prompt(self):
        """경과가 안 들어가면 이 모듈이 존재할 이유가 없다."""
        (_insights, _stats), client = self.run_one("")
        self.assertIn("다뉴브강 수위 최저치 기록", client.messages[0])
        self.assertIn("경과", client.messages[0])

    def test_timeline_is_newest_first_and_marked(self):
        """어느 기사가 현재 상태인지 표시하지 않으면 옛 상태가 해석으로 올라온다.

        2026-08-07 사용자 지적: 제목이 '3기 가동 중단'인데 해석은 '가동 중단을
        피했다'였다. 프롬프트 안에 '지금은 어느 쪽인가'를 가릴 근거가 없었다.
        """
        (_insights, _stats), client = self.run_one("")
        message = client.messages[0]
        newest = message.index("마지막 터빈")   # 2026-08-05
        oldest = message.index("다뉴브강 수위 최저치")  # 2026-07-31
        self.assertLess(newest, oldest, "경과가 최신순이 아니다")
        self.assertIn("← 최신", message)

    def test_article_detail_is_material_for_the_insight(self):
        """본문 요지가 있으면 프롬프트에 실려야 한다 — 제목 한 줄보다 좋은 재료다."""
        timeline = [dict(member) for member in PAKS_TIMELINE]
        timeline[-1]["detail"] = "다뉴브강 수위가 취수 기준선 아래로 내려갔다. 4기 중 3기가 멈췄다."
        (_insights, _stats), client = self.run_one("", rows=[row(related_articles=timeline)])
        self.assertIn("본문 요지: 다뉴브강 수위가 취수 기준선", client.messages[0])

    def test_copying_one_timeline_member_is_discarded(self):
        """경과 한 건을 그대로 옮긴 문장은 버린다.

        그 기사가 최신이 아니면 카드가 제목과 정면으로 모순되고(2026-08-07 실사고),
        최신이더라도 같은 문장이 바로 아래 타임라인에 이미 있다.
        """
        (insights, stats), _client = self.run_one(
            "다뉴브강 수위가 최저치를 기록하며 냉각수 취수가 제한된 것이다.")
        self.assertEqual(insights, {})
        self.assertEqual(stats["rejected"].get("copies_member"), 1)

    def test_good_insight_is_returned(self):
        (insights, stats), _client = self.run_one(
            "다뉴브강 수위 저하로 냉각수 취수가 막혀 예고됐던 전면 정지를 피한 것이다.")
        self.assertIn("issue-paks", insights)
        self.assertEqual(stats["asked"], 1)

    def test_hollow_output_is_discarded(self):
        (insights, stats), _client = self.run_one(
            "헝가리 정부의 원전 운영에 대한 긍정적 입장을 시사한다.")
        self.assertEqual(insights, {})
        self.assertEqual(stats["rejected"].get("hollow"), 1)

    def test_restating_output_is_discarded(self):
        (insights, stats), _client = self.run_one(
            "헝가리 총리가 팍스 원전의 마지막 터빈이 안전하게 가동 중이라고 발표한 것이다.")
        self.assertEqual(insights, {})
        self.assertEqual(stats["rejected"].get("restates_title"), 1)

    def test_overlong_output_is_not_truncated(self):
        """잘린 분석문은 완결된 요약보다 나쁘다."""
        (insights, stats), _client = self.run_one("가" * (issue_insight.MAX_LENGTH + 1))
        self.assertEqual(insights, {})
        self.assertEqual(stats["rejected"].get("too_long"), 1)

    def test_empty_result_is_cached_so_we_do_not_reask(self):
        """재료 없는 이슈를 매 빌드(하루 12회+)마다 다시 물으면 안 된다."""
        self.run_one("")
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertIn("issue-paks", cached["insights"])
        client = FakeClient([{"items": [{"idx": 0, "insight": "새 문장"}]}])
        _insights, stats = issue_insight.generate([row()], client=client,
                                                  cache_path=self.cache_path)
        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["from_cache"], 1)

    def test_new_article_invalidates_the_cache(self):
        """클러스터에 기사가 붙으면 다시 물어야 한다 — 그게 후속 보도다."""
        self.run_one("다뉴브강 수위 저하로 예고됐던 전면 정지를 피한 것이다.")
        grown = row(related_articles=PAKS_TIMELINE + [
            {"hash": "a4", "article_date": "2026-08-06", "title_kr": "재가동 확대", "summary": ""}])
        client = FakeClient([{"items": [{"idx": 0, "insight": ""}]}])
        _insights, stats = issue_insight.generate([grown], client=client,
                                                  cache_path=self.cache_path)
        self.assertEqual(stats["calls"], 1)

    def test_failure_is_not_fatal(self):
        client = FakeClient(raises=RuntimeError("HTTP 429"))
        insights, stats = issue_insight.generate([row()], client=client,
                                                 cache_path=self.cache_path)
        self.assertEqual(insights, {})
        self.assertTrue(stats["status"].startswith("error"))

    def test_uses_a_separate_model_bucket(self):
        """공용 2.5-flash 버킷에 얹으면 저녁마다 429 로 죽는다(issue_review 전례)."""
        (_result, _stats), client = self.run_one("")
        self.assertEqual(client.kwargs[0]["model"], issue_insight._resolve_model())
        self.assertNotEqual(issue_insight.INSIGHT_MODEL_DEFAULT, "gemini-2.5-flash")


class TestApply(unittest.TestCase):
    """생성은 카탈로그 행에서 한 번, 배포는 issue_id 로."""

    def test_briefing_rows_get_the_same_sentence_without_reasking(self):
        rows = [row(implication=""), row(implication="")]
        applied = issue_insight.apply(rows, {"issue-paks": "다뉴브강 수위 저하가 원인이다."})
        self.assertEqual(applied, 2)
        self.assertEqual(rows[0]["implication_source"], "issue_timeline")

    def test_a_good_existing_sentence_is_not_overwritten(self):
        rows = [row(implication="EIB 대출 승인으로 자금 조달이 확정됐다.")]
        self.assertEqual(issue_insight.apply(rows, {"issue-paks": "새 문장"}), 0)

    def test_a_hollow_existing_sentence_is_replaced(self):
        rows = [row(implication="긍정적 입장을 시사한다.")]
        self.assertEqual(issue_insight.apply(rows, {"issue-paks": "새 문장"}), 1)
        self.assertEqual(rows[0]["implication"], "새 문장")


if __name__ == "__main__":
    unittest.main()
