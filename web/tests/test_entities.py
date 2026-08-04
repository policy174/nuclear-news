"""엔티티 레지스트리 매칭 계약.

원칙: 오탐 > 누락. 여기 테스트가 잠그는 것은 "무엇이 매칭되는가"보다
"무엇이 매칭되면 안 되는가"다 — 잘못 붙은 엔티티는 그 엔티티 페이지 전체의
신뢰를 깎는다. 매칭은 결정적이어야 한다(LLM 0회, 같은 입력 → 같은 출력).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402


def _member(title="", summary="", tags=()):
    return {"title_kr": title, "summary": summary, "canonical_tags": list(tags)}


def _registry(*entities):
    return [
        {
            "id": e.get("id", "x"),
            "name_kr": e.get("name_kr", "엔티티"),
            "name_en": e.get("name_en", ""),
            "type": e.get("type", "company"),
            "countries": e.get("countries", []),
            "aliases": e["aliases"],
            "match_policy": e.get("match_policy", "token"),
        }
        for e in entities
    ]


def _match(members, registry):
    entries = build_data._entity_alias_entries(registry)
    ids, evidence = build_data.entity_ids_for_members(members, entries)
    return ids, evidence


class EntityRegistryLoadTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        missing = Path(tempfile.gettempdir()) / "no-such-entity-registry.json"
        self.assertEqual(build_data.load_entity_registry(missing), [])

    def test_corrupt_file_returns_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("{broken json")
            path = Path(handle.name)
        try:
            self.assertEqual(build_data.load_entity_registry(path), [])
        finally:
            path.unlink()

    def test_unknown_type_and_duplicate_id_are_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump({"entities": [
                {"id": "a", "name_kr": "가", "type": "galaxy", "aliases": ["가나다"]},
                {"id": "b", "name_kr": "나", "type": "company", "aliases": ["나다라"]},
                {"id": "b", "name_kr": "나2", "type": "company", "aliases": ["다라마"]},
                {"id": "c", "name_kr": "다", "type": "company", "aliases": ["라마바"],
                 "match_policy": "regex"},
            ]}, handle, ensure_ascii=False)
            path = Path(handle.name)
        try:
            loaded = build_data.load_entity_registry(path)
        finally:
            path.unlink()
        self.assertEqual([e["id"] for e in loaded], ["b"])

    def test_real_registry_loads_and_is_valid(self):
        """저장소의 실제 사전이 스키마를 지키는지 — 큐레이션 실수를 커밋 전에 잡는다."""
        registry = build_data.load_entity_registry()
        self.assertGreaterEqual(len(registry), 50)
        ids = [e["id"] for e in registry]
        self.assertEqual(len(ids), len(set(ids)), "중복 id")
        for entity in registry:
            self.assertIn(entity["type"], build_data.ENTITY_TYPES)


class EntityMatchTests(unittest.TestCase):
    def test_hangul_particle_tail_is_absorbed(self):
        registry = _registry({"id": "westinghouse", "aliases": ["웨스팅하우스"]})
        ids, evidence = _match([_member(title="웨스팅하우스가 신규 계약을 발표")], registry)
        self.assertEqual(ids, ["westinghouse"])
        self.assertEqual(evidence[0]["source_field"], "title")

    def test_hangul_long_tail_is_rejected(self):
        # 꼬리 4자 이상은 조사가 아니라 다른 낱말일 가능성이 높다 — 매칭하지 않는다.
        registry = _registry({"id": "doosan", "aliases": ["두산"]})
        ids, _ = _match([_member(title="두산에너빌리티가 수주")], registry)
        self.assertEqual(ids, [])

    def test_latin_exact_token_only(self):
        registry = _registry({"id": "edf", "aliases": ["edf"]})
        yes, _ = _match([_member(title="EDF, 신규 원전 투자 확대")], registry)
        no, _ = _match([_member(title="EDFX 프로젝트 발표")], registry)
        self.assertEqual(yes, ["edf"])
        self.assertEqual(no, [])

    def test_latin_normalization_joins_separators(self):
        registry = _registry({"id": "x-energy", "aliases": ["xenergy"]})
        ids, _ = _match([_member(title="X-energy 와 협력")], registry)
        self.assertEqual(ids, ["x-energy"])

    def test_longest_alias_wins_on_the_same_token(self):
        # '한전기술' 토큰은 '한전기술'(정확)이 '한전'(접두+2자)보다 먼저 잡는다.
        registry = _registry(
            {"id": "kepco", "aliases": ["한전"]},
            {"id": "kepco-en", "aliases": ["한전기술"]},
        )
        ids, _ = _match([_member(title="한전기술, 설계 계약 체결")], registry)
        self.assertEqual(ids, ["kepco-en"])

    def test_tag_or_unit_adjacent_ignores_free_text(self):
        registry = _registry({"id": "kori", "name_kr": "고리 원전", "type": "plant",
                              "aliases": ["고리"], "match_policy": "tag_or_unit_adjacent"})
        # 요약 자유문의 '고리'(연결 고리)는 매칭하지 않는다.
        free_text, _ = _match([_member(summary="공급망의 약한 고리가 드러났다")], registry)
        self.assertEqual(free_text, [])
        # 태그 정확 일치('고리' 또는 '고리원전')는 매칭한다.
        tagged, _ = _match([_member(tags=["고리원전"])], registry)
        self.assertEqual(tagged, ["kori"])
        # 제목의 'N호기' 인접도 매칭한다.
        unit, evidence = _match([_member(title="고리 2호기 계속운전 심사")], registry)
        self.assertEqual(unit, ["kori"])
        self.assertEqual(evidence[0]["source_field"], "title_unit")

    def test_title_only_policy_skips_summary_and_tags(self):
        registry = _registry({"id": "arc-clean", "aliases": ["arc"],
                              "match_policy": "title_only"})
        title, _ = _match([_member(title="ARC, INL 과 SMR 배치 협력")], registry)
        summary, _ = _match([_member(summary="arc 방전 현상이 관찰됐다")], registry)
        self.assertEqual(title, ["arc-clean"])
        self.assertEqual(summary, [])

    def test_misspelled_alias_preserves_legacy_facility_names(self):
        # _FACILITY_NAMES 의 오탈자(테믈린)는 별칭으로 남아 과거 데이터도 잡는다.
        registry = _registry({"id": "temelin", "type": "plant",
                              "aliases": ["테멜린", "테믈린"]})
        ids, _ = _match([_member(title="테믈린 원전 증설 계획")], registry)
        self.assertEqual(ids, ["temelin"])

    def test_deterministic_registry_order(self):
        registry = _registry(
            {"id": "first", "aliases": ["가나다라"]},
            {"id": "second", "aliases": ["마바사아"]},
        )
        members = [_member(title="마바사아 그리고 가나다라")]
        ids1, _ = _match(members, registry)
        ids2, _ = _match(members, registry)
        self.assertEqual(ids1, ["first", "second"])  # 등재 순 — 등장 순 아님
        self.assertEqual(ids1, ids2)

    def test_short_latin_alias_is_dropped_at_index_time(self):
        registry = _registry({"id": "ge", "aliases": ["ge"]})
        entries = build_data._entity_alias_entries(registry)
        self.assertEqual(entries, [])


class EntitiesViewTests(unittest.TestCase):
    def test_view_is_always_generated_even_without_registry(self):
        view = build_data.build_entities_view([], [], "2026-08-04T00:00:00+09:00")
        self.assertEqual(view["entities"], [])
        self.assertIn("generated_at", view)

    def test_zero_count_entities_are_included(self):
        registry = _registry({"id": "quiet", "name_kr": "조용한 회사", "aliases": ["조용한회사"]})
        view = build_data.build_entities_view([], registry, "t")
        self.assertEqual(view["entities"][0]["issue_count"], 0)
        # 내부 매칭 정책은 공개 데이터에 싣지 않는다.
        self.assertNotIn("match_policy", view["entities"][0])
        self.assertIn("aliases", view["entities"][0])

    def test_counts_aggregate_from_catalog_rows(self):
        registry = _registry(
            {"id": "a", "aliases": ["가나다라"]},
            {"id": "b", "aliases": ["마바사아"]},
        )
        catalog = [
            {"issue_id": "issue-1", "entity_ids": ["a"], "article_count": 3,
             "last_seen": "2026-08-01"},
            {"issue_id": "issue-2", "entity_ids": ["a", "b"], "article_count": 1,
             "last_seen": "2026-08-03"},
        ]
        view = build_data.build_entities_view(catalog, registry, "t")
        by_id = {e["id"]: e for e in view["entities"]}
        self.assertEqual(by_id["a"]["issue_count"], 2)
        self.assertEqual(by_id["a"]["article_count"], 4)
        self.assertEqual(by_id["a"]["latest_issue_date"], "2026-08-03")
        self.assertEqual(by_id["a"]["issue_ids"], ["issue-1", "issue-2"])
        # 정렬: 이슈 수 내림차순
        self.assertEqual(view["entities"][0]["id"], "a")


if __name__ == "__main__":
    unittest.main()
