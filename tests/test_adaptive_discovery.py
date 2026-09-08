"""신규 이슈 탐색 — 결정적, LLM 0회, 네트워크 0회.

존재 이유: discovery 는 **등재된 대상**의 상태 변화만 묻는다. 그래서 사전에도
고정 키워드에도 없는 이름(처음 등장한 SMR 기업·해외 원전·법안)은 구조적으로
아무도 안 묻는다. 그 이름이 고정 키워드가 되려면 사람이 기사를 읽고 파일을
고쳐야 하는데 그건 이미 늦은 뒤다.

이 파일이 제일 신경 쓰는 것은 **재현율이 아니라 폭증**이다. 자동 생성 검색어는
조용히 늘어나고, 늘어난 유입은 전부 LLM 큐레이션을 타므로 그 사고는 며칠 뒤
쿼터 소진으로만 드러난다. 그래서 상한 하나하나에 테스트가 붙어 있다.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import adaptive_discovery as ad  # noqa: E402
import entity_match  # noqa: E402

NOW = datetime(2026, 8, 17, 7, 0, tzinfo=timezone.utc)


def rows(*specs) -> list[dict]:
    """아카이브 레코드 흉내. 기본은 씨앗 창(48h) 안이다."""
    out = []
    for index, spec in enumerate(specs):
        at = spec.get("at") or (NOW - timedelta(hours=6)).isoformat()
        out.append({
            "archived_at": at,
            "title_kr": spec["title"],
            "summary": spec.get("summary", ""),
            "importance": spec.get("importance", "must_read"),
            "domain": spec.get("domain", f"outlet{index}.co.kr"),
            "hash": spec.get("hash", f"h{index}"),
        })
    return out


def old_rows(*specs) -> list[dict]:
    """씨앗 창 **밖**의 기사 — 후보가 되지 않고 신규성 판정에만 쓰인다."""
    at = (NOW - timedelta(days=10)).isoformat()
    return rows(*[{**spec, "at": at} for spec in specs])


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = entity_match.load_entity_registry()

    def terms(self, archive, **kwargs):
        found = ad.extract_candidates(archive, self.registry, now=NOW, **kwargs)
        return {entry["term"]: entry for entry in found.values()}

    def eligible(self, archive, **kwargs):
        found = ad.extract_candidates(archive, self.registry, now=NOW, **kwargs)
        return {e["term"] for e in found.values() if ad._eligible(e)}

    def test_a_plant_name_that_is_not_in_the_registry_becomes_a_candidate(self):
        """이 모듈이 존재하는 이유 그 자체."""
        archive = rows(
            {"title": "핀란드 로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 1호기 정비 착수", "domain": "b.co.kr"},
        )
        self.assertIn("로비사", self.eligible(archive))

    def test_registered_entities_never_become_candidates(self):
        """등재된 대상은 discovery 의 몫이다. 여기서 또 만들면 같은 검색이 두 번."""
        archive = rows(
            {"title": "팍스 원전 가동 중단", "domain": "a.co.kr"},
            {"title": "팍스 원전 재가동 검토", "domain": "b.co.kr"},
            {"title": "신한울 원전 3호기 착공", "domain": "c.co.kr"},
        )
        terms = self.terms(archive)
        self.assertNotIn("팍스", terms)
        self.assertNotIn("신한울", terms)

    def test_a_long_name_that_contains_a_registered_alias_is_not_new(self):
        """'고리원자력본부'는 새 이름이 아니라 아는 대상의 다른 표기다.

        `entity_match` 의 접두 판정은 꼬리 3자까지만 봐서 이 형태를 놓친다.
        """
        archive = rows(
            {"title": "고리원자력본부는 계속운전 설명회를 열었다", "domain": "a.co.kr"},
            {"title": "고리원자력본부는 정비 일정을 공개했다", "domain": "b.co.kr"},
        )
        self.assertNotIn("고리원자력본부", self.terms(archive))

    def test_a_name_that_was_already_in_the_archive_is_not_new(self):
        """**이 모듈 정밀도의 대부분이 여기서 나온다.**

        모양만 보면 '산업통상자원부'·'소형모듈'처럼 몇 주째 매일 나오는 말이
        매번 최상위 후보가 된다 — 이름인 것은 맞지만 새롭지 않아 물을 값이 없다.
        """
        fresh = rows(
            {"title": "그라블린 원전 가동 중단", "domain": "a.co.kr"},
            {"title": "그라블린 원전 재가동 일정", "domain": "b.co.kr"},
        )
        self.assertIn("그라블린", self.eligible(fresh))

        history = old_rows(
            {"title": "그라블린 원전 정기 점검"},
            {"title": "그라블린 원전 출력 감발"},
            {"title": "그라블린 원전 안전 점검 결과"},
        )
        self.assertNotIn("그라블린", self.eligible(fresh + history))

    def test_generic_modifiers_never_become_search_terms(self):
        """'신규 원전'·'체코 원전'은 넓기만 하고 새롭지 않다."""
        archive = rows(
            {"title": "정부, 신규 원전 부지 검토", "domain": "a.co.kr"},
            {"title": "체코 원전 수주 협상 진행", "domain": "b.co.kr"},
            {"title": "차세대 원전 예산 편성", "domain": "c.co.kr"},
        )
        terms = self.terms(archive)
        for junk in ("신규", "체코", "차세대", "정부"):
            self.assertNotIn(junk, terms)

    def test_sentence_fragments_never_become_search_terms(self):
        """'X 원전' 은 **원전 바로 앞 낱말**을 잡으므로 문장 토막이 딸려 온다."""
        archive = rows(
            {"title": "가뭄으로 원전 출력이 줄었다", "domain": "a.co.kr"},
            {"title": "이를 위해 원전 건설을 서두른다", "domain": "b.co.kr"},
            {"title": "정부가 소형모듈 원전 지원을 늘린다", "domain": "c.co.kr"},
        )
        terms = self.terms(archive)
        for junk in ("가뭄으로", "위해", "정부가 소형모듈", "소형모듈"):
            self.assertNotIn(junk, terms)

    def test_a_two_word_name_is_not_cut_in_half(self):
        """실측: '아보이티즈 파워와 원전' 에서 한 낱말만 보면 '파워와'가 된다."""
        archive = rows(
            {"title": "한수원, 필리핀 아보이티즈 파워와 원전 협력 MOU", "domain": "a.co.kr"},
            {"title": "아보이티즈 파워와 원전 기술 교류 확대", "domain": "b.co.kr"},
        )
        eligible = self.eligible(archive)
        self.assertIn("아보이티즈 파워", eligible)
        self.assertNotIn("파워와", eligible)

    def test_a_name_that_ends_like_a_particle_survives(self):
        """'오나가와'의 '와', '홋카이도'의 '도'는 조사가 아니라 이름의 일부다.

        띄어쓴 이름에서만 조사를 뗀다(`_canonical_term`). 한 낱말짜리는 자르지
        않고 점수만 깎는다 — 그래서 반복해서 나오면 결국 올라온다.
        """
        archive = rows(
            {"title": "오나가와 원전 2호기 재가동", "domain": "a.co.kr"},
            {"title": "일본 오나가와 원전 출력 상승", "domain": "b.co.kr"},
            {"title": "오나가와 원전 안전성 평가", "domain": "c.co.kr"},
            {"title": "오나가와 원전 지역 설명회", "domain": "d.co.kr"},
        )
        terms = self.terms(archive)
        self.assertIn("오나가와", terms)
        self.assertNotIn("오나가", terms)

    def test_all_caps_acronyms_do_not_come_in_as_company_names(self):
        """실측: SEED·ESS·AIDC·LNG·ETF·GPU 가 전부 '기업'으로 들어왔다."""
        archive = rows(
            {"title": "ESS 연계 원전 운영 확대", "domain": "a.co.kr"},
            {"title": "LNG 발전과 원전 비중 조정", "domain": "b.co.kr"},
        )
        terms = self.terms(archive)
        self.assertNotIn("ESS", terms)
        self.assertNotIn("LNG", terms)

    def test_a_foreign_name_introduced_in_parentheses_is_a_strong_signal(self):
        """한국 언론이 **낯선 이름을 처음 소개할 때** 쓰는 모양이다."""
        archive = rows({"title": "미국 카이로스파워(Kairos Power)가 원자로 건설 허가를 받았다"})
        self.assertIn("Kairos Power", self.terms(archive))

    def test_noise_grade_articles_alone_cannot_mint_a_term(self):
        """중요도는 큐레이션 LLM 이 이미 매긴 값이다 — 새로 묻지 않고 재사용한다."""
        archive = rows(*[{"title": f"로비사 원전 소식 {i}", "importance": "noise",
                          "domain": f"n{i}.co.kr"} for i in range(4)])
        self.assertNotIn("로비사", self.eligible(archive))

    def test_the_query_gets_a_nuclear_qualifier_when_the_name_lacks_one(self):
        """이름만 던지면 동명이의 기사가 그대로 들어오고, 그때는 이미 검색을 태웠다."""
        self.assertEqual(ad.build_query("아보이티즈 파워", "plant"), "아보이티즈 파워 원전")
        self.assertEqual(ad.build_query("테라파워", "company"), "테라파워 원자력")
        # 이미 원자력 문맥을 담은 말에는 붙이지 않는다.
        self.assertEqual(ad.build_query("고준위 방폐물 특별법", "project"),
                         "고준위 방폐물 특별법")


class BudgetTests(unittest.TestCase):
    """상한 넷 — 이 모듈이 제일 조심하는 자리."""

    @classmethod
    def setUpClass(cls):
        cls.registry = entity_match.load_entity_registry()

    def _archive(self, count: int = 30):
        """서로 다른 신규 이름을 넉넉히 만든다. 이름은 등재 엔티티와 겹치지 않는다."""
        specs = []
        for index in range(count):
            name = f"제네바{index:02d}"
            specs.append({"title": f"{name} 원전 가동 중단", "domain": f"a{index}.co.kr"})
            specs.append({"title": f"{name} 원전 재가동 검토", "domain": f"b{index}.co.kr"})
        return rows(*specs)

    def _plan(self, archive, state=None, **kwargs):
        return ad.plan_queries(archive, self.registry, state or ad._empty_state(),
                               now=kwargs.pop("now", NOW), **kwargs)

    def test_a_single_run_cannot_eat_the_whole_day(self):
        queries, state = self._plan(self._archive(), per_run_cap=3, budget=12)
        self.assertEqual(len(queries), 3)
        self.assertEqual(state["spent"], {"date": "2026-08-17", "count": 3})

    def test_the_daily_budget_is_a_total_not_a_per_run_cap(self):
        """discovery 가 2026-08-15 에 걸렸던 함정이다 — 회차마다 새로 세면
        하루 상한이 이름만 하루 상한이고 실제로는 24배가 나간다.

        회차 간격을 재실행 창(`MIN_RERUN_HOURS`)보다 넓게 두고, 같은 KST 날짜
        안에 머무르게 잡는다. 둘 중 하나라도 어기면 예산이 아니라 회전 규칙이나
        날짜 경계를 재게 되어 이 테스트가 무엇을 지키는지 흐려진다.
        """
        archive = self._archive()
        base = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)   # KST 10:00
        state = ad._empty_state()
        counts = []
        for hours in (0, 7, 13):
            queries, state = self._plan(archive, state, per_run_cap=3, budget=8,
                                        now=base + timedelta(hours=hours))
            counts.append(len(queries))
        self.assertEqual(counts, [3, 3, 2])
        self.assertEqual(state["spent"]["count"], 8)

    def test_the_budget_resets_on_the_kst_day_boundary(self):
        """이 저장소의 '오늘'은 전부 KST 다(브리핑·아카이브·discovery)."""
        archive = self._archive()
        exhausted = ad._empty_state()
        exhausted["spent"] = {"date": "2026-08-17", "count": 12}
        same_day, _ = self._plan(archive, dict(exhausted), budget=12, per_run_cap=3)
        self.assertEqual(same_day, [])

        next_day = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)   # KST 로 8/18 01:00
        queries, state = self._plan(archive, dict(exhausted), budget=12,
                                    per_run_cap=3, now=next_day)
        self.assertEqual(len(queries), 3)
        self.assertEqual(state["spent"], {"date": "2026-08-18", "count": 3})

    def test_only_so_many_new_terms_are_minted_per_day(self):
        """검색어가 폭증하는 경로는 질의 수가 아니라 **말의 수**다."""
        _queries, state = self._plan(self._archive(), per_run_cap=3, budget=12)
        self.assertLessEqual(len(state["terms"]), ad.MAX_NEW_TERMS_PER_DAY)
        self.assertEqual(state["minted"]["count"], len(state["terms"]))

    def test_no_terms_are_minted_when_the_budget_is_gone(self):
        """TTL 은 만든 순간부터 흐른다 — 못 물어볼 날에 만들면 한 번도 못 물어보고
        만료되는 검색어가 생긴다. 씨앗 창이 48시간이라 내일 다시 올라온다."""
        state = ad._empty_state()
        state["spent"] = {"date": "2026-08-17", "count": 12}
        queries, state = self._plan(self._archive(), state, per_run_cap=3, budget=12)
        self.assertEqual(queries, [])
        self.assertEqual(state["terms"], {})

    def test_the_active_pool_has_a_hard_ceiling(self):
        archive = self._archive()
        state = ad._empty_state()
        now = NOW
        for day in range(10):                      # 열흘치 — 매일 상한까지 만든다
            now = NOW + timedelta(days=day)
            _queries, state = self._plan(archive, state, per_run_cap=3,
                                         budget=12, now=now)
        self.assertLessEqual(len(state["terms"]), ad.MAX_ACTIVE_TERMS)

    def test_a_term_is_never_asked_twice_within_the_rerun_window(self):
        archive = self._archive()
        first, state = self._plan(archive, per_run_cap=3, budget=12)
        soon = NOW + timedelta(hours=1)
        second, _state = self._plan(archive, state, per_run_cap=3, budget=12, now=soon)
        self.assertFalse({q["query"] for q in first} & {q["query"] for q in second})

    def test_a_query_that_a_fixed_keyword_already_covers_is_never_created(self):
        """같은 검색을 두 번 던지는 낭비는 로그에 '유입 0건'으로만 보인다."""
        archive = rows(
            {"title": "로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 정비 착수", "domain": "b.co.kr"},
        )
        queries, state = self._plan(archive, fixed_queries=["로비사 원전"])
        self.assertEqual(queries, [])
        self.assertEqual(state["terms"], {})

    def test_a_query_discovery_already_asks_is_never_created(self):
        archive = rows(
            {"title": "로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 정비 착수", "domain": "b.co.kr"},
        )
        queries, _state = self._plan(archive, discovery_queries=["로비사 원전"])
        self.assertEqual(queries, [])

    def test_discovery_budget_is_never_touched(self):
        """예산이 갈려 있다는 것이 이 기능의 전제다 — 상태 파일부터 다르다."""
        import discovery
        self.assertNotEqual(ad.STATE_FILE, discovery.STATE_FILE)
        self.assertLess(ad.DAILY_QUERY_BUDGET, discovery.DAILY_QUERY_BUDGET)


class LifecycleTests(unittest.TestCase):
    """24~72시간 → 성과 있으면 연장 → 승격 후보, 없으면 폐기."""

    @classmethod
    def setUpClass(cls):
        cls.registry = entity_match.load_entity_registry()

    def _tracking(self):
        archive = rows(
            {"title": "로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 정비 착수", "domain": "b.co.kr"},
        )
        queries, state = ad.plan_queries(archive, self.registry, ad._empty_state(), now=NOW)
        self.assertTrue(queries)
        return queries, state

    def test_ttl_is_between_one_and_three_days(self):
        _queries, state = self._tracking()
        for entry in state["terms"].values():
            span = ad._parse_dt(entry["expires_at"]) - ad._parse_dt(entry["created_at"])
            self.assertGreaterEqual(span, timedelta(hours=ad.TTL_HOURS_LOW))
            self.assertLessEqual(span, timedelta(hours=ad.TTL_HOURS_HIGH))

    def test_a_yield_extends_the_tracking_window(self):
        queries, state = self._tracking()
        key = queries[0]["term_id"]
        before = ad._parse_dt(state["terms"][key]["expires_at"])
        # 만료가 가까워진 시점의 유입이라야 연장이 눈에 보인다. 이른 유입은
        # 이미 잡힌 만료보다 앞이라 아무것도 바꾸지 않는다 — **줄이지 않는다**는
        # 것도 계약이다(연장이 만료를 앞당기면 성과가 벌이 된다).
        early = ad.record_results(dict(state), [{**queries[0], "result_count": 4,
                                                 "new_article_count": 2}],
                                  now=NOW + timedelta(hours=2))
        self.assertEqual(ad._parse_dt(early["terms"][key]["expires_at"]), before)

        state = ad.record_results(state, [{**queries[0], "result_count": 4,
                                           "new_article_count": 2}],
                                  now=before - timedelta(hours=2))
        after = ad._parse_dt(state["terms"][key]["expires_at"])
        self.assertGreater(after, before)
        self.assertEqual(state["terms"][key]["status"], "extended")

    def test_extensions_cannot_outrun_the_lifetime_ceiling(self):
        """연장이 무한히 쌓이면 '임시'라는 말이 거짓이 된다."""
        queries, state = self._tracking()
        key = queries[0]["term_id"]
        now = NOW
        for _ in range(30):
            now += timedelta(hours=12)
            state = ad.record_results(state, [{**queries[0], "result_count": 3,
                                               "new_article_count": 1}], now=now)
            state = ad.sweep(state, now)
            if key not in state["terms"]:
                break
        self.assertNotIn(key, state["terms"])
        self.assertEqual(state["retired"][key]["reason"], "추적 기간 상한")

    def test_three_empty_runs_retire_a_term(self):
        queries, state = self._tracking()
        key = queries[0]["term_id"]
        for _ in range(ad.ZERO_YIELD_LIMIT):
            state = ad.record_results(state, [{**queries[0], "result_count": 0,
                                               "new_article_count": 0}], now=NOW)
        state = ad.sweep(state, NOW)
        self.assertNotIn(key, state["terms"])
        self.assertEqual(state["retired"][key]["reason"], "성과 없음")

    def test_expiry_retires_a_term_that_never_yielded(self):
        queries, state = self._tracking()
        key = queries[0]["term_id"]
        state = ad.sweep(state, NOW + timedelta(hours=ad.TTL_HOURS_HIGH + 1))
        self.assertNotIn(key, state["terms"])
        self.assertEqual(state["retired"][key]["reason"], "기간 만료")

    def test_a_retired_term_is_not_immediately_re_created(self):
        """안 그러면 같은 헛방을 매일 새로 '발견'한다."""
        archive = rows(
            {"title": "로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 정비 착수", "domain": "b.co.kr"},
        )
        queries, state = ad.plan_queries(archive, self.registry, ad._empty_state(), now=NOW)
        key = queries[0]["term_id"]
        state = ad.sweep(state, NOW + timedelta(hours=ad.TTL_HOURS_HIGH + 1))
        later = NOW + timedelta(days=2)
        _again, state = ad.plan_queries(archive, self.registry, state, now=later)
        self.assertNotIn(key, state["terms"])

    def test_repeated_yields_across_days_make_a_promotion_candidate(self):
        queries, state = self._tracking()
        key = queries[0]["term_id"]
        for day in range(ad.PROMOTE_MIN_YIELDS):
            when = NOW + timedelta(days=day, hours=1)
            state = ad.record_results(state, [{**queries[0], "result_count": 5,
                                               "new_article_count": 2}], now=when)
        entry = state["terms"][key]
        self.assertEqual(entry["status"], "promote_candidate")
        self.assertGreaterEqual(len(entry["yield_days"]), ad.PROMOTE_MIN_DAYS)
        view = ad.console_view(state, NOW)
        self.assertTrue(any(row["registry_draft"] for row in view))


class ConsoleTests(unittest.TestCase):
    """콘솔 판정이 실제로 닿는가. 안 닿으면 화면만 바뀌고 검색은 그대로 돈다."""

    @classmethod
    def setUpClass(cls):
        cls.registry = entity_match.load_entity_registry()

    def _archive(self):
        return rows(
            {"title": "로비사 원전 계속운전 승인", "domain": "a.co.kr"},
            {"title": "로비사 원전 정비 착수", "domain": "b.co.kr"},
        )

    def test_a_term_the_admin_removed_is_dropped_and_never_re_created(self):
        """자동 폐기(냉각)와 다르다 — 사람이 뺀 말은 판정을 지우기 전엔 안 만든다."""
        archive = self._archive()
        _queries, state = ad.plan_queries(archive, self.registry, ad._empty_state(), now=NOW)
        self.assertTrue(state["terms"])
        console = {"added": [], "blocked": {"로비사"}, "pinned": set()}
        later = NOW + timedelta(days=30)   # 냉각이 풀리고도 남을 만큼 뒤
        _again, state = ad.plan_queries(archive, self.registry, state,
                                        console=console, now=later)
        self.assertEqual(state["terms"], {})

    def test_an_admin_added_term_skips_the_score_threshold(self):
        console = {"added": [{"term": "웨스팅하우스 SMR"}], "blocked": set(), "pinned": set()}
        queries, state = ad.plan_queries([], self.registry, ad._empty_state(),
                                         console=console, now=NOW)
        self.assertEqual([q["term"] for q in queries], ["웨스팅하우스 SMR"])
        self.assertEqual(state["terms"][ad._compact("웨스팅하우스 SMR")]["origin"], "console")

    def test_an_admin_added_term_never_expires_on_its_own(self):
        """자동 폐기하면 판정 항목은 남아 다음 회차에 되살아난다 — 그 왕복이 영원하다."""
        console = {"added": [{"term": "웨스팅하우스 SMR"}], "blocked": set(), "pinned": set()}
        _queries, state = ad.plan_queries([], self.registry, ad._empty_state(),
                                          console=console, now=NOW)
        state = ad.sweep(state, NOW + timedelta(days=90))
        self.assertIn(ad._compact("웨스팅하우스 SMR"), state["terms"])

    def test_a_pinned_term_survives_expiry_and_empty_runs(self):
        archive = self._archive()
        queries, state = ad.plan_queries(archive, self.registry, ad._empty_state(), now=NOW)
        key = queries[0]["term_id"]
        for _ in range(ad.ZERO_YIELD_LIMIT + 2):
            state = ad.record_results(state, [{**queries[0], "result_count": 0,
                                               "new_article_count": 0}], now=NOW)
        console = {"added": [], "blocked": set(), "pinned": {"로비사"}}
        _again, state = ad.plan_queries(archive, self.registry, state, console=console,
                                        now=NOW + timedelta(days=30))
        self.assertIn(key, state["terms"])

    def test_the_console_view_says_why_a_term_exists_and_when_it_dies(self):
        """근거 없는 목록은 판단할 수 없고, 판단할 수 없는 목록은 아무도 안 본다."""
        archive = self._archive()
        _queries, state = ad.plan_queries(archive, self.registry, ad._empty_state(), now=NOW)
        row = ad.console_view(state, NOW)[0]
        self.assertEqual(row["term"], "로비사")
        self.assertTrue(row["query"])
        self.assertTrue(row["evidence"])
        self.assertTrue(row["evidence"][0]["title"])
        self.assertGreater(row["expires_in_hours"], 0)

    @unittest.skip("V1 admin_overrides 는 최소 이식이라 KINDS·learned_term 게이트가 "
                   "없다 — collect_adaptive 가 console={} 로 돌므로 계약 자체가 비활성. "
                   "콘솔에서 임시 검색어를 차단/고정하는 기능을 이식할 때 해제할 것.")
    def test_the_overlay_and_the_write_gate_agree_on_the_new_kinds(self):
        """둘이 갈라지면 콘솔은 저장 성공이라 말하고 파이프라인은 조용히 무시한다."""
        import admin_overrides
        api = (ROOT / "functions" / "admin" / "api" / "overrides.js").read_text(encoding="utf-8")
        for kind in ("learned_term_add", "learned_term_remove", "learned_term_keep"):
            self.assertIn(kind, admin_overrides.KINDS)
            self.assertIn(kind, api)


class ResilienceTests(unittest.TestCase):
    """비치명 경로다 — 여기서 예외가 나면 크롤 전체가 위험해진다."""

    @classmethod
    def setUpClass(cls):
        cls.registry = entity_match.load_entity_registry()

    def test_a_broken_or_missing_state_file_is_not_fatal(self):
        import json
        import tempfile
        directory = Path(tempfile.mkdtemp())
        self.assertEqual(ad.load_state(directory / "없는파일.json")["terms"], {})
        broken = directory / "broken.json"
        broken.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
        self.assertEqual(ad.load_state(broken)["terms"], {})
        wrong_shape = directory / "shape.json"
        wrong_shape.write_text(json.dumps({"terms": "쓰레기", "spent": 7}), encoding="utf-8")
        self.assertEqual(ad.load_state(wrong_shape)["terms"], {})

    def test_hand_edited_or_stale_counters_do_not_crash(self):
        """상태 파일은 커밋되므로 손으로도 고쳐지고, 옛 버전에는 칸이 아예 없다."""
        for spent in (None, {}, {"date": "2026-08-17"}, {"date": None, "count": None},
                      "쓰레기", {"date": "2026-08-17", "count": "3"}):
            with self.subTest(spent=spent):
                state = ad._empty_state()
                if spent is not None:
                    state["spent"] = spent
                queries, _state = ad.plan_queries([], self.registry, state, now=NOW)
                self.assertEqual(queries, [])

    def test_an_empty_archive_produces_nothing(self):
        queries, state = ad.plan_queries([], self.registry, ad._empty_state(), now=NOW)
        self.assertEqual(queries, [])
        self.assertEqual(state["terms"], {})


if __name__ == "__main__":
    unittest.main()
