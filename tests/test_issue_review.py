"""issue_review.py 단위 테스트 — 회색지대 선별·캐시·실패 시 보수 동작."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import issue_review


def candidate(pair_id="a--b", similarity=0.90, left="고리 3·4호기 계속운전 심의 지연",
              right="원전 4기 계속운전 절차 지연", blocked=None,
              left_date="2026-07-20", right_date="2026-07-21"):
    return {
        "candidate_id": pair_id,
        "left_title": left,
        "right_title": right,
        "left_date": left_date,
        "right_date": right_date,
        "diagnostics": {
            "embedding_similarity": similarity,
            "blocked_by": blocked or [],
        },
    }


class FakeClient:
    """gemini_client 대역. 호출 기록과 응답을 통제한다."""

    MODEL = "fake-model"

    def __init__(self, responses=None, available=True, raises=False):
        self.responses = list(responses or [])
        self._available = available
        self.raises = raises
        self.calls = []
        self.kwargs = []

    def is_available(self):
        return self._available

    def call_json(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        self.kwargs.append(kwargs)
        if self.raises:
            raise self.raises if isinstance(self.raises, BaseException) \
                else RuntimeError("429 rate limited")
        return self.responses.pop(0) if self.responses else {"items": []}


def verdict_response(count, same=True):
    return {"items": [{"idx": i, "same_event": same, "reason": "테스트"} for i in range(count)]}


class BandTests(unittest.TestCase):
    def test_band_is_low_inclusive_high_exclusive(self):
        self.assertTrue(issue_review.in_review_band({"embedding_similarity": 0.84}))
        self.assertTrue(issue_review.in_review_band({"embedding_similarity": 0.919}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": 0.92}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": 0.839}))

    def test_real_continuation_at_0_85_is_adjudicated(self):
        """실측 회귀 — 이 쌍이 밴드 밖으로 나가면 헝가리 팍스 후속이 다시 갈라진다.

        "헝가리 총리, 팍스 원전 일요일 가동 중단 발표"(08-02) ↔
        "그리스 산불, 가뭄으로 헝가리 원자력 발전소 가동 중단"(08-03), 코사인 0.8513.
        """
        self.assertTrue(issue_review.in_review_band({"embedding_similarity": 0.8513}))

    def test_blocked_pairs_never_reach_llm(self):
        diag = {"embedding_similarity": 0.90, "blocked_by": ["facility_conflict"]}
        self.assertFalse(issue_review.in_review_band(diag))

    def test_missing_or_bad_similarity_is_excluded(self):
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": None}))
        self.assertFalse(issue_review.in_review_band({"embedding_similarity": "높음"}))
        self.assertFalse(issue_review.in_review_band({}))
        self.assertFalse(issue_review.in_review_band(None))

    def test_select_pairs_filters_and_dedupes(self):
        rows = [
            candidate("in1", 0.90),
            candidate("in1", 0.90),          # 중복
            candidate("out_high", 0.95),
            candidate("out_low", 0.80),
            candidate("blocked", 0.90, blocked=["country_conflict"]),
            {"no_id": True},
        ]
        picked = issue_review.select_pairs(rows)
        self.assertEqual([row["candidate_id"] for row in picked], ["in1"])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmp.name) / "issue_llm_reviews.json"

    def tearDown(self):
        self.tmp.cleanup()

    def review(self, rows, client, **kw):
        return issue_review.review_pairs(rows, cache_path=self.cache_path, client=client, **kw)

    def test_approved_pair_is_returned_and_cached(self):
        client = FakeClient([verdict_response(1, same=True)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual((stats["calls"], stats["approved"], stats["rejected"]), (1, 1, 0))
        self.assertTrue(self.cache_path.exists())
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertTrue(cached["reviews"]["p1"]["same_event"])

    def test_second_run_uses_cache_and_makes_no_call(self):
        self.review([candidate("p1")], FakeClient([verdict_response(1)]))
        client = FakeClient([verdict_response(1)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": True})
        self.assertEqual(client.calls, [])
        self.assertEqual((stats["calls"], stats["from_cache"]), (0, 1))

    def test_prompt_version_change_invalidates_cache(self):
        self.review([candidate("p1")], FakeClient([verdict_response(1)]))
        raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        raw["reviews"]["p1"]["prompt_version"] = issue_review.PROMPT_VERSION - 1
        self.cache_path.write_text(json.dumps(raw), encoding="utf-8")
        client = FakeClient([verdict_response(1)])
        _verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(stats["calls"], 1)

    def test_rejected_pair_is_not_merged(self):
        client = FakeClient([verdict_response(1, same=False)])
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {"p1": False})
        self.assertEqual(stats["rejected"], 1)

    def test_missing_api_key_merges_nothing(self):
        client = FakeClient(available=False)
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "no_api_key")
        self.assertEqual(client.calls, [])

    def test_call_failure_merges_nothing_and_is_not_cached(self):
        client = FakeClient(raises=True)
        verdicts, stats = self.review([candidate("p1")], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "partial_failure")
        self.assertEqual(stats["failed"], 1)
        self.assertFalse(self.cache_path.exists())

    def test_failure_reason_is_recorded_not_erased(self):
        """2026-08-04 02:49 회귀 — calls=0/failed=40 인데 원인을 알 수 없었다.

        한도 소진과 잘림은 대응이 정반대다(전자는 재시도 금지, 후자는 분할).
        사유를 안 남기면 두 시간짜리 왕복을 한 번 더 해야 한다.
        """
        client = FakeClient(raises=RuntimeError("HTTP 429 RESOURCE_EXHAUSTED"))
        _verdicts, stats = self.review([candidate("p1"), candidate("p2")], client)
        self.assertEqual(stats["failure_reasons"], {"quota": 2})
        self.assertIn("RESOURCE_EXHAUSTED", stats["failure_detail"])

    def test_timeout_and_other_are_labelled_apart(self):
        for exc, label in ((RuntimeError("socket timed out"), "timeout"),
                           (RuntimeError("무언가 이상함"), "other")):
            with self.subTest(label=label):
                _v, stats = self.review([candidate("p1")], FakeClient(raises=exc))
                self.assertEqual(stats["failure_reasons"], {label: 1})

    def test_truncation_splits_instead_of_giving_up(self):
        """잘림은 같은 예산으로 다시 불러도 같은 자리에서 잘린다 — 쪼개야 산다."""
        calls = {"n": 0}

        class Splitting(FakeClient):
            def call_json(self, system_prompt, user_message, **kwargs):
                self.calls.append(user_message)
                calls["n"] += 1
                if user_message.count("[") > 2:     # 3쌍 이상이면 잘린다
                    raise issue_review.GeminiTruncated("MAX_TOKENS 출력 예산 소진")
                n = user_message.count("[")
                return {"items": [{"idx": i, "same_event": True, "reason": "ok"}
                                  for i in range(n)]}

        rows = [candidate(f"p{i}") for i in range(4)]
        verdicts, stats = self.review(rows, Splitting(), batch_size=4)
        self.assertEqual(len(verdicts), 4)          # 전건 살아났다
        self.assertGreaterEqual(stats["splits"], 1)
        self.assertEqual(stats["failed"], 0)

    def test_split_budget_stops_runaway_halving(self):
        """20 → 1 까지 쪼개면 한 회차에 호출이 폭증한다. 예산이 소진되면 포기한다."""
        class AlwaysTruncated(FakeClient):
            def call_json(self, system_prompt, user_message, **kwargs):
                self.calls.append(user_message)
                raise issue_review.GeminiTruncated("MAX_TOKENS")

        client = AlwaysTruncated()
        rows = [candidate(f"p{i}") for i in range(16)]
        verdicts, stats = self.review(rows, client, batch_size=16)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["failure_reasons"].get("truncated"), 16)
        self.assertLessEqual(stats["splits"], issue_review.SPLIT_BUDGET)

    def test_output_ceiling_is_raised_for_the_thinking_budget(self):
        """8192 로 되돌리면 밴드 확장 첫 호출이 다시 전건 죽는다."""
        self.assertGreaterEqual(issue_review.MAX_OUTPUT_TOKENS, 16384)
        client = FakeClient([verdict_response(1)])
        self.review([candidate("p1")], client)
        self.assertEqual(client.kwargs[0]["max_output_tokens"],
                         issue_review.MAX_OUTPUT_TOKENS)

    def test_malformed_response_drops_only_the_bad_pair(self):
        client = FakeClient([{"items": [
            {"idx": 0, "same_event": True, "reason": "정상"},
            {"idx": 1, "same_event": "아마도"},          # bool 아님
            {"idx": 99, "same_event": True},             # 범위 밖
        ]}])
        rows = [candidate("p0"), candidate("p1"), candidate("p2")]
        verdicts, stats = self.review(rows, client)
        self.assertEqual(verdicts, {"p0": True})
        self.assertEqual(stats["failed"], 2)

    def test_batches_are_split_by_size(self):
        rows = [candidate(f"p{i}") for i in range(25)]
        client = FakeClient([verdict_response(20), verdict_response(5)])
        _verdicts, stats = self.review(rows, client, batch_size=20)
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["asked"], 25)

    def test_no_candidates_short_circuits(self):
        client = FakeClient()
        verdicts, stats = self.review([candidate("p1", similarity=0.5)], client)
        self.assertEqual(verdicts, {})
        self.assertEqual(stats["status"], "no_candidates")
        self.assertEqual(client.calls, [])

    def test_new_pairs_are_throttled_per_run(self):
        """밀린 후보를 한 빌드에 다 묻지 않는다 — 무료 티어 한도를 태운다."""
        rows = [candidate(f"p{i}") for i in range(30)]
        client = FakeClient([verdict_response(10)])
        _verdicts, stats = self.review(rows, client, batch_size=20, max_new_pairs=10)
        self.assertEqual(stats["asked"], 10)
        self.assertEqual(stats["deferred"], 20)
        self.assertEqual(stats["status"], "throttled")
        self.assertEqual(len(client.calls), 1)

    def test_throttle_asks_newest_and_most_similar_first(self):
        rows = [
            candidate("old_high", 0.91, left="옛기사A", right="옛기사B",
                      left_date="2026-07-01", right_date="2026-07-02"),
            candidate("new_low", 0.85, left="새기사C", right="새기사D",
                      left_date="2026-08-02", right_date="2026-08-03"),
            candidate("new_high", 0.90, left="새기사E", right="새기사F",
                      left_date="2026-08-02", right_date="2026-08-03"),
        ]
        client = FakeClient([verdict_response(2)])
        _verdicts, stats = self.review(rows, client, max_new_pairs=2)
        asked = client.calls[0]
        self.assertEqual(stats["deferred"], 1)
        # 21일 창 밖으로 밀려날 옛 쌍은 유사도가 더 높아도 미룬다.
        self.assertIn("새기사E", asked)
        self.assertIn("새기사C", asked)
        self.assertNotIn("옛기사A", asked)
        # 같은 날짜끼리는 유사도 높은 쪽이 먼저 나온다.
        self.assertLess(asked.index("새기사E"), asked.index("새기사C"))

    def test_deferred_pairs_are_not_merged_but_retried_next_run(self):
        """미룬 쌍은 '다른 사건'이 아니라 '아직 모름'이다 — 캐시에 남기지 않는다."""
        rows = [candidate(f"p{i}") for i in range(5)]
        client = FakeClient([verdict_response(2)])
        verdicts, _stats = self.review(rows, client, max_new_pairs=2)
        self.assertEqual(len(verdicts), 2)
        cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(cached["reviews"]), 2)

    def test_user_message_lists_every_pair_with_index(self):
        rows = [candidate("p0", left="A제목", right="B제목"),
                candidate("p1", left="C제목", right="D제목")]
        message = issue_review.build_user_message(rows)
        self.assertIn("[0]", message)
        self.assertIn("[1]", message)
        self.assertIn("A제목", message)
        self.assertIn("D제목", message)


class TestQuotaBucketSeparation(unittest.TestCase):
    """검수 호출은 크롤·트렌드와 다른 모델 버킷을 써야 한다.

    실측 2026-08-05 라이브: candidates 205 · asked 0 · failed 20 ·
    failure_reasons {"quota": 20}. 판정이 없으면 병합하지 않으므로 밴드 안에 있던
    팍스 원전 후속(코사인 0.8716)이 신규 이슈로 갈라졌다.
    """

    def test_default_model_is_not_the_shared_flash_bucket(self):
        self.assertNotEqual(issue_review.REVIEW_MODEL_DEFAULT, "gemini-2.5-flash")

    def test_call_passes_the_review_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient([verdict_response(1)])
            issue_review.review_pairs(
                [candidate("p0")],
                cache_path=Path(tmp) / "cache.json",
                client=client,
            )
        self.assertTrue(client.kwargs, "호출이 없었다")
        self.assertEqual(client.kwargs[-1].get("model"), issue_review._review_model())

    def test_stats_report_the_model_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            _verdicts, stats = issue_review.review_pairs(
                [candidate("p0")],
                cache_path=Path(tmp) / "cache.json",
                client=FakeClient([verdict_response(1)]),
            )
        self.assertEqual(stats["model"], issue_review._review_model())


class TestFacilityEntityPriority(unittest.TestCase):
    """같은 설비·프로젝트를 다루는 쌍을 먼저 묻는다.

    실측 2026-08-05(판정 완료 185쌍): 설비·프로젝트 엔티티를 공유한 쌍은 3건이
    전부 같은 사건이고 오탐 0. 기관·기업까지 넣으면 40건 중 3건이라 판별력이
    없다. 표본이 작아 자동 병합에는 쓰지 않고 **묻는 순서**에만 쓴다 — 한 회차
    상한 40쌍에 밀린 후보가 519건이라 순서가 곧 추적률이다.
    """

    def test_facility_pair_outranks_a_newer_pair_without_one(self):
        older_with_facility = candidate(
            "paks", similarity=0.8716,
            left="헝가리, 가뭄으로 팍스 원전 가동 중단 위기 직면",
            right="헝가리 총리, 팍스 원전 마지막 터빈 '안전하게 가동 중' 발표",
            left_date="2026-08-04", right_date="2026-08-05")
        older_with_facility["shared_facility_entities"] = ["paks"]
        newer_without = candidate("other", similarity=0.8865,
                                  left_date="2026-08-06", right_date="2026-08-06")
        ranked = sorted([newer_without, older_with_facility],
                        key=issue_review._ask_priority, reverse=True)
        self.assertEqual(ranked[0]["candidate_id"], "paks")

    def test_priority_reads_the_diagnostics_copy_too(self):
        row = candidate("p0")
        row["diagnostics"]["shared_facility_entities"] = ["wolsong"]
        self.assertTrue(issue_review._ask_priority(row)[0])

    def test_absent_field_does_not_crash_or_promote(self):
        self.assertFalse(issue_review._ask_priority(candidate("p0"))[0])

    def test_throttled_run_asks_the_facility_pair_first(self):
        """상한에 걸린 회차에서 설비 쌍이 잘려나가면 안 된다."""
        rows = [candidate(f"p{i}", left_date="2026-08-09", right_date="2026-08-09")
                for i in range(5)]
        rows[4]["candidate_id"] = "facility-pair"
        rows[4]["shared_facility_entities"] = ["paks"]
        rows[4]["left_date"] = rows[4]["right_date"] = "2026-08-01"  # 가장 오래된 쌍
        client = FakeClient([verdict_response(1)])
        with tempfile.TemporaryDirectory() as tmp:
            verdicts, stats = issue_review.review_pairs(
                rows, cache_path=Path(tmp) / "cache.json", client=client, max_new_pairs=1)
        self.assertEqual(stats["deferred"], 4)
        self.assertIn("facility-pair", verdicts)


if __name__ == "__main__":
    unittest.main(verbosity=1)


class TestBandLowerBoundIsDeliberate(unittest.TestCase):
    """하한 0.84 는 두 번 검토해 유지한 값이다 — 낮추기 전에 근거를 다시 볼 것.

    2026-08-06 실측: 미판정 후보 581쌍이 코사인 0.7163~0.8668(중앙값 0.8063)로
    밴드 **바로 아래**에 몰려 있다. 그래서 "하한만 내리면 많이 잡힌다"로 보이지만,
    [0.82,0.84) 상위 18쌍 중 같은 사건은 1쌍뿐이고 나머지는 'NRC Ginna 계속운전 ↔
    일리노이 지역사회 응답'처럼 분야만 같다. 현재 밴드 승인률이 12%(26/219)인데
    이 구간은 1~2% 수준이다.

    분포가 밴드에 가깝다는 것은 병합할 값어치가 있다는 증거가 아니다.
    """

    def test_band_is_unchanged(self):
        self.assertEqual(0.84, issue_review.REVIEW_BAND_LOW)
        self.assertEqual(0.92, issue_review.REVIEW_BAND_HIGH)

    def test_reasoning_is_recorded_in_the_module(self):
        self.assertIn("다시 제안하지 말 것", issue_review.__doc__)
