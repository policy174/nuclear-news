"""LLM 판정 캐시 봉투 — 세 모듈이 복제하던 것을 한 곳으로 모은 뒤의 계약."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import issue_insight
import issue_review
import keei_match
import llm_cache


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "cache.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_is_empty_not_an_error(self):
        """캐시는 없어도 되는 것이다 — 다시 물으면 된다. 예외를 올리면 캐시 손상
        하나가 파이프라인 전체를 세운다."""
        self.assertEqual(llm_cache.load(self.path, "reviews"), {})

    def test_corrupt_json_is_empty(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(llm_cache.load(self.path, "reviews"), {})

    def test_wrong_shape_is_empty(self):
        for raw in ("[1,2,3]", '"문자열"', '{"reviews": [1,2]}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                self.assertEqual(llm_cache.load(self.path, "reviews"), {})

    def test_reads_only_the_requested_key(self):
        self.path.write_text(json.dumps(
            {"reviews": {"a": 1}, "matches": {"b": 2}}), encoding="utf-8")
        self.assertEqual(llm_cache.load(self.path, "matches"), {"b": 2})


class SaveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "cache.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip(self):
        llm_cache.save({"a": {"same_event": True}}, self.path,
                       key="reviews", prompt_version=3, comment="테스트")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["prompt_version"], 3)
        self.assertEqual(raw["_comment"], "테스트")
        self.assertEqual(llm_cache.load(self.path, "reviews"), {"a": {"same_event": True}})

    def test_write_failure_is_swallowed_by_default(self):
        """쓰기 실패로 빌드를 죽이지 않는다 — 캐시가 없으면 다시 물으면 된다."""
        llm_cache.save({}, Path(self._tmp.name) / "없는폴더" / "c.json",
                       key="reviews", prompt_version=1, comment="x")

    def test_write_failure_can_be_raised(self):
        """issue_insight 는 삼키지 않는다. 정리가 동작을 몰래 바꾸면 안 된다."""
        with self.assertRaises(OSError):
            llm_cache.save({}, Path(self._tmp.name) / "없는폴더" / "c.json",
                           key="insights", prompt_version=1, comment="x",
                           swallow_errors=False)

    def test_sort_keys_is_optional(self):
        payload = {"b": {"v": 1}, "a": {"v": 2}}
        llm_cache.save(payload, self.path, key="k", prompt_version=1,
                       comment="x", sort_keys=False)
        unsorted = self.path.read_text(encoding="utf-8")
        llm_cache.save(payload, self.path, key="k", prompt_version=1, comment="x")
        self.assertNotEqual(unsorted, self.path.read_text(encoding="utf-8"))


class IsCurrentTests(unittest.TestCase):
    def test_version_match(self):
        self.assertTrue(llm_cache.is_current({"prompt_version": 2}, 2))
        self.assertFalse(llm_cache.is_current({"prompt_version": 1}, 2))

    def test_broken_entry_is_stale(self):
        for entry in (None, "문자열", [], {}, {"prompt_version": None}):
            with self.subTest(entry=entry):
                self.assertFalse(llm_cache.is_current(entry, 1))


class ModuleContractTests(unittest.TestCase):
    """세 모듈이 봉투를 공유하되 **키·주석·플래그는 각자 유지**한다.

    2026-08-06 추출 당시 셋의 load_cache/save_cache 는 안쪽 키와 주석만 다르고
    나머지가 글자까지 같았다. 합치면서 동작이 바뀌지 않았는지 여기서 잠근다.
    """

    def test_each_module_keeps_its_own_envelope_key(self):
        self.assertEqual(issue_review.CACHE_KEY, "reviews")
        self.assertEqual(keei_match.CACHE_KEY, "matches")
        self.assertEqual(issue_insight.CACHE_KEY, "insights")

    def test_modules_round_trip_through_the_shared_envelope(self):
        with tempfile.TemporaryDirectory() as d:
            for module in (issue_review, keei_match, issue_insight):
                with self.subTest(module=module.__name__):
                    path = Path(d) / f"{module.__name__}.json"
                    module.save_cache({"k": {"prompt_version": module.PROMPT_VERSION}}, path)
                    self.assertEqual(
                        module.load_cache(path),
                        {"k": {"prompt_version": module.PROMPT_VERSION}})


if __name__ == "__main__":
    unittest.main()
