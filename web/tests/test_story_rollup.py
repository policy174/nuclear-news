"""스토리 롤업 계약.

issue ≠ story. 카드의 story_* 는 '대표 기사가 속한 스토리 그룹'의 집계값이며,
그룹핑 정의는 _story_groups 한 곳뿐이다(기간 집계도 story_id_map 으로 같은
정의를 소비한다). 여기서 잠그는 것은 병합보다 **과병합 금지**다 — 과거
클러스터 과병합 사고의 재발 방지가 목적.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402


def _article(hash_, title, publisher="", domain="", date="2026-08-18", summary=""):
    return {
        "hash": hash_,
        "title_kr": title,
        "summary": summary,
        "publisher": publisher,
        "domain": domain,
        "article_date": date,
    }


class StoryRollupTests(unittest.TestCase):
    def test_three_outlets_same_event_is_one_story(self):
        members = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="연합뉴스"),
            _article("a2", "한수원 체코 신규 원전 계약 체결 확정", publisher="조선일보"),
            _article("a3", "체코 신규 원전, 한수원과 계약 체결", publisher="한겨레"),
        ]
        count, outlets = build_data.story_rollup(members, members[0])
        self.assertEqual(count, 3)
        self.assertEqual(outlets, 3)

    def test_distinct_events_do_not_merge(self):
        members = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="연합뉴스"),
            _article("a2", "환경단체, 신규 원전 건설 중단 촉구 시위", publisher="한겨레"),
        ]
        groups = build_data._story_groups(members)
        self.assertEqual(len(groups), 2)

    def test_no_transitive_chain_merge(self):
        # A~B 유사, B~C 유사, A~C 비유사 → anchor(A) 기준이라 C 는 별도 그룹.
        # 전이 병합을 허용하면 A+B+C 가 한 스토리로 붙는다(과병합 회귀).
        a = _article("a1", "정부, SMR 지원 예산 확대 발표", publisher="p1")
        b = _article("a2", "정부 SMR 지원 예산 확대 발표에 업계 환영", publisher="p2")
        c = _article("a3", "업계 환영 속 SMR 과제 산적", publisher="p3")
        self.assertTrue(build_data._same_story(a, b))
        self.assertFalse(build_data._same_story(a, c))
        groups = build_data._story_groups([a, b, c])
        anchor_group = next(g for g in groups if any(m["hash"] == "a1" for m in g))
        self.assertNotIn("a3", [m["hash"] for m in anchor_group])

    def test_same_publisher_counts_as_one_outlet(self):
        members = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="연합뉴스"),
            _article("a2", "한수원 체코 신규 원전 계약 체결 공식화", publisher="연합뉴스"),
        ]
        count, outlets = build_data.story_rollup(members, members[0])
        self.assertEqual(count, 2)
        self.assertEqual(outlets, 1)

    def test_google_news_domain_does_not_collapse_outlets(self):
        # 매체명이 비고 구글 뉴스 도메인뿐이면 기사 단위로 남는다(과대 계상 방지
        # 방향과 반대로, 서로 다른 매체를 하나로 뭉개지도 않는다).
        members = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", domain="news.google.com"),
            _article("a2", "한수원 체코 신규 원전 계약 체결 확정", domain="news.google.com"),
        ]
        count, outlets = build_data.story_rollup(members, members[0])
        self.assertEqual(count, 2)
        self.assertEqual(outlets, 2)

    def test_far_apart_dates_do_not_merge(self):
        # 같은 표현이라도 날짜가 멀면 같은 사건이 아니다 — 반복 정책명 과병합 방지.
        members = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="p1", date="2026-08-01"),
            _article("a2", "한수원, 체코 신규 원전 계약 체결", publisher="p2", date="2026-08-18"),
        ]
        groups = build_data._story_groups(members)
        self.assertEqual(len(groups), 2)

    def test_representative_group_only(self):
        # issue 에 사건이 두 개면 카드 칩은 대표 기사가 속한 사건의 값만 센다.
        event_a = [
            _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="p1", date="2026-08-10"),
            _article("a2", "한수원 체코 신규 원전 계약 체결 확정", publisher="p2", date="2026-08-10"),
        ]
        event_b = [
            _article("b1", "환경단체, 원전 건설 중단 촉구 시위", publisher="p3", date="2026-08-18"),
        ]
        count, outlets = build_data.story_rollup(event_a + event_b, event_b[0])
        self.assertEqual((count, outlets), (1, 1))
        count, outlets = build_data.story_rollup(event_a + event_b, event_a[0])
        self.assertEqual((count, outlets), (2, 2))

    def test_story_id_map_matches_card_rollup(self):
        # 기간 집계(WS2)가 소비하는 story_id 와 카드 롤업이 같은 그룹핑을 봐야 한다.
        issue = {
            "issue_id": "issue-x",
            "members": [
                _article("a1", "한수원, 체코 신규 원전 계약 체결", publisher="p1"),
                _article("a2", "한수원 체코 신규 원전 계약 체결 확정", publisher="p2"),
            ],
            "evidence_members": [
                _article("a3", "체코 신규 원전, 한수원과 계약 체결", publisher="p3"),
            ],
        }
        mapping = build_data.story_id_map([issue])
        self.assertEqual(len(set(mapping.values())), 1)
        members = issue["members"] + issue["evidence_members"]
        count, _ = build_data.story_rollup(members, issue["members"][0])
        self.assertEqual(count, len(mapping))


if __name__ == "__main__":
    unittest.main()
