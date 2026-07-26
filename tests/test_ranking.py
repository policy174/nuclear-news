"""ranking.py 단위 테스트 — 점수식·감쇠·중복·다양성·피드백 사전확률."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import ranking

NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)
CFG = ranking.load_config()


def item(h="h1", importance="nice_to_know", section="international",
         domain="example.com", title="Some title", queued_hours_ago=1,
         features=None, related_reports=None, **kw):
    d = {
        "hash": h, "importance": importance, "section": section,
        "domain": domain, "title": title, "title_kr": title,
        "link": f"https://{domain}/a/{h}",
        "queued_at": (NOW - timedelta(hours=queued_hours_ago)).isoformat(),
        "related_reports": related_reports or [],
    }
    if features is not None:
        d["features"] = features
    d.update(kw)
    return d


def feat(**kw):
    base = {"event_type": "other", "korea_relevance": 0, "market_materiality": 0,
            "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
            "report_worthiness": 0}
    base.update(kw)
    return base


class TestSanitize(unittest.TestCase):
    def test_none_and_non_dict(self):
        self.assertIsNone(ranking.sanitize_features(None))
        self.assertIsNone(ranking.sanitize_features("policy"))
        self.assertIsNone(ranking.sanitize_features([1, 2]))

    def test_missing_fields_default_zero(self):
        f = ranking.sanitize_features({"event_type": "contract_award"})
        self.assertEqual(f["event_type"], "contract_award")
        self.assertEqual(f["korea_relevance"], 0)
        self.assertEqual(f["report_worthiness"], 0)

    def test_out_of_range_clamped(self):
        f = ranking.sanitize_features({"korea_relevance": 99, "novelty": -5,
                                       "market_materiality": "3"})
        self.assertEqual(f["korea_relevance"], 3)
        self.assertEqual(f["novelty"], 0)
        self.assertEqual(f["market_materiality"], 3)

    def test_bad_event_type(self):
        f = ranking.sanitize_features({"event_type": "invented_type"})
        self.assertEqual(f["event_type"], "other")

    def test_non_int_scores(self):
        f = ranking.sanitize_features({"evidence_strength": "strong"})
        self.assertEqual(f["evidence_strength"], 0)


class TestLegacyScore(unittest.TestCase):
    """features 없는 옛 큐 항목 — 기존 rank_item 공식 그대로여야 함."""

    def test_must_read_khnp_primary_reports(self):
        a = item(importance="must_read", section="khnp", domain="khnp.co.kr",
                 related_reports=["r1"], queued_hours_ago=0)
        s, b = ranking.score_item(a, CFG, now=NOW)
        # 10(must) + 2(khnp) + 2(primary) + 1(reports) = 15
        self.assertEqual(s, 15.0)
        self.assertTrue(b.get("legacy"))

    def test_nice_plain(self):
        a = item(importance="nice_to_know", queued_hours_ago=0)
        s, _ = ranking.score_item(a, CFG, now=NOW)
        self.assertEqual(s, 5.0)


class TestNewScore(unittest.TestCase):
    def test_breakdown_explainable(self):
        a = item(features=feat(event_type="contract_award", korea_relevance=3,
                               market_materiality=2, evidence_strength=3),
                 importance="must_read", queued_hours_ago=0)
        s, b = ranking.score_item(a, CFG, now=NOW)
        self.assertIn("importance", b)
        self.assertIn("event:contract_award", b)
        self.assertIn("korea_relevance", b)
        self.assertAlmostEqual(s, sum(v for k, v in b.items() if k != "legacy"),
                               places=2)

    def test_tier1_source_bonus(self):
        a = item(domain="iaea.org", features=feat(), queued_hours_ago=0)
        a["link"] = "https://www.iaea.org/newscenter/x"
        _, b = ranking.score_item(a, CFG, now=NOW)
        self.assertIn("source_tier1", b)

    def test_time_decay_old_article_lower(self):
        fresh = item(h="a", features=feat(novelty=2), queued_hours_ago=0)
        old = item(h="b", features=feat(novelty=2), queued_hours_ago=36)
        s1, _ = ranking.score_item(fresh, CFG, now=NOW)
        s2, b2 = ranking.score_item(old, CFG, now=NOW)
        self.assertLess(s2, s1)
        self.assertIn("time_decay", b2)

    def test_decay_capped(self):
        ancient = item(features=feat(), queued_hours_ago=1000)
        _, b = ranking.score_item(ancient, CFG, now=NOW)
        self.assertGreaterEqual(b["time_decay"], -CFG["time_decay"]["max"])


class TestDuplicates(unittest.TestCase):
    def test_same_and_followup_titles_clustered(self):
        a = item(h="a", title="한수원, 체코 두코바니 원전 본계약 체결")
        b = item(h="b", title="한수원 체코 두코바니 원전 본계약을 체결했다")  # 후속·우라까이
        c = item(h="c", title="미국 NRC, NuScale SMR 설계 인증")
        scores = {"a": 10.0, "b": 5.0, "c": 7.0}
        kept, dropped = ranking.cluster_duplicates([a, b, c], scores)
        self.assertEqual({x["hash"] for x in kept}, {"a", "c"})
        self.assertEqual(dropped[0]["hash"], "b")
        self.assertEqual(dropped[0]["dup_of"], "a")  # 점수 높은 쪽이 대표

    def test_distinct_titles_kept(self):
        a = item(h="a", title="폴란드 신규 원전 부지 확정")
        b = item(h="b", title="우라늄 현물가 급등, 카자흐 감산")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 5, "b": 5})
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_paraphrased_same_event_clustered(self):
        """2026-07-13 실전 사례: 같은 사건의 패러프레이즈 (문자열 ratio 0.52)."""
        a = item(h="a", title="반도체 특구 전력 수요, 원전 18기 필요성 제기…전력 수급 불확실성 증대")
        b = item(h="b", title="'반도체 특구'에 원전 18기 필요하다는데…커지는 전력 물음표")
        kept, dropped = ranking.cluster_duplicates([a, b], {"a": 6, "b": 5})
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["dup_of"], "a")

    def test_same_entity_different_events_not_clustered(self):
        a = item(h="a", title="원안위, 고리2호기 계속운전 심사 착수")
        b = item(h="b", title="원안위, 한빛1호기 계속운전 심사 결과 발표")
        kept, _ = ranking.cluster_duplicates([a, b], {"a": 5, "b": 5})
        self.assertEqual(len(kept), 2)


class TestDiversity(unittest.TestCase):
    def test_topic_overexposure_penalized(self):
        # smr 이 이미 2건(cap) 선정된 뒤엔 3번째 smr 에 penalty(2.5) →
        # 점수차가 penalty 안이면 다른 주제가 비집고 들어온다 (소프트 페널티 설계)
        items = [item(h=f"s{i}", section="smr", title=f"SMR {i}") for i in range(3)]
        items.append(item(h="x", section="international", title="기타"))
        scores = {"s0": 10, "s1": 9, "s2": 8, "x": 6.5}
        sel = ranking.select_diverse(items, scores, 3, CFG)
        self.assertEqual([a["hash"] for a in sel], ["s0", "s1", "x"])

    def test_topic_penalty_not_absolute(self):
        # 점수차가 penalty 보다 크면 같은 주제라도 그대로 선정 (강한 뉴스 존중)
        items = [item(h=f"s{i}", section="smr", title=f"SMR {i}") for i in range(3)]
        items.append(item(h="x", section="international", title="기타"))
        scores = {"s0": 10, "s1": 9, "s2": 8, "x": 3}
        sel = ranking.select_diverse(items, scores, 3, CFG)
        self.assertEqual([a["hash"] for a in sel], ["s0", "s1", "s2"])

    def test_tie_deterministic(self):
        a = item(h="aaa", title="t1", queued_hours_ago=1)
        b = item(h="bbb", title="t2", queued_hours_ago=1)
        scores = {"aaa": 5.0, "bbb": 5.0}
        sel1 = ranking.select_diverse([a, b], scores, 1, CFG)
        sel2 = ranking.select_diverse([b, a], scores, 1, CFG)
        self.assertEqual(sel1[0]["hash"], sel2[0]["hash"])  # 입력 순서 무관


class TestConfig(unittest.TestCase):
    def test_missing_config_falls_back(self):
        cfg = ranking.load_config(Path("no_such_file.json"))
        self.assertIn("importance_base", cfg)

    def test_repo_config_loads(self):
        cfg = ranking.load_config()
        self.assertEqual(cfg["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
