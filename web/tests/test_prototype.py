import json
import sys
import unittest
from itertools import combinations
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT = ROOT / "public" / "data"
try:
    _manifest = json.loads((DATA_ROOT / "manifest.json").read_text(encoding="utf-8"))
    DATA_DIR = DATA_ROOT / _manifest["base_path"]
except (OSError, KeyError, json.JSONDecodeError):
    DATA_DIR = DATA_ROOT

import build_data  # noqa: E402


class SelectionReasonTests(unittest.TestCase):
    def test_breakdown_becomes_two_human_reasons(self):
        delivery = {
            "score": 22.5,
            "breakdown": {
                "importance": 10,
                "event:policy_decision": 6,
                "korea_relevance": 3.6,
                "policy_materiality": 3,
            },
        }
        self.assertEqual(
            build_data.selection_reasons(delivery),
            ["정책 결정", "국내 관련성 높음"],
        )

    def test_time_decay_is_never_exposed(self):
        reasons = build_data.selection_reasons({"score": 5, "breakdown": {"time_decay": -1}})
        self.assertEqual(reasons, ["브리핑 우선순위"])

    def test_official_and_specialist_labels_are_distinct(self):
        delivery = {"score": 5, "breakdown": {"source_tier1": 4}}
        self.assertEqual(
            build_data.selection_reasons(delivery, {"evidence_role": "primary"}),
            ["공식 원문"],
        )
        self.assertEqual(
            build_data.selection_reasons(
                delivery, {"evidence_role": "independent", "source_type": "specialist_media"}
            ),
            ["전문 매체"],
        )


class DataQualityGateTests(unittest.TestCase):
    @staticmethod
    def _record(hash_value="h1", url="https://example.com/a", title="원전 계획 발표"):
        return {
            "hash": hash_value,
            "url": url,
            "title": title,
            "publisher": "테스트 매체",
            "source_tier": 3,
            "importance": "nice_to_know",
            "summary": "정부가 신규 원전 계획을 발표했다.",
            "implication": "",
            "why_important": "",
        }

    def test_duplicate_url_fails_build_gate(self):
        with self.assertRaisesRegex(ValueError, "duplicate_url"):
            build_data.validate_archive_records([
                self._record(), self._record("h2", title="다른 제목")
            ])

    def test_incomplete_summary_fails_build_gate(self):
        record = self._record()
        record["summary"] = "정부가 신규 원전 계획을 발표"
        with self.assertRaisesRegex(ValueError, "summary:incomplete"):
            build_data.validate_archive_records([record])


class RegionClassificationTests(unittest.TestCase):
    def test_google_korea_domain_does_not_turn_us_story_domestic(self):
        record = {
            "title_kr": "미국 원전의 80년 장기운전 및 민간 금융 동향",
            "summary": "미국 원전 정책을 분석한다.",
            "domain": "news.google.co.kr",
            "section": "international",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertIn("US", countries)
        self.assertEqual(build_data.region_of(record, countries), "해외")

    def test_korean_project_with_foreign_counterpart_stays_domestic(self):
        record = {
            "title_kr": "한국 SMR 선박, 미국선급협회 기본승인 획득",
            "domain": "world-nuclear-news.org",
            "section": "smr",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertIn("KR", countries)
        self.assertEqual(build_data.region_of(record, countries), "국내")

    def test_legacy_eu_bucket_is_refined_to_actual_country(self):
        record = {
            "title_kr": "독일, 국가 핵융합 허브와 연구개발 지원 계획 발표",
            "summary": "독일 정부가 핵융합 연구개발 지원 계획을 발표했다.",
            "countries": ["EU_ETC"],
            "section": "international",
        }
        countries, source = build_data.infer_countries(record)
        self.assertEqual(countries, ["DE"])
        self.assertEqual(source, "legacy-refined-v2")

    def test_serbia_is_a_country_not_eu(self):
        record = {
            "title_kr": "세르비아 정부, 신규 원자력 프로그램 검토",
            "section": "international",
        }
        countries, _ = build_data.infer_countries(record)
        self.assertEqual(countries, ["RS"])

    def test_us_agency_token_uses_word_boundary(self):
        countries, _ = build_data.infer_countries({
            "title_kr": "NRC, 오이스터크릭 인허가 종료계획 승인",
            "section": "international",
        })
        self.assertEqual(countries, ["US"])

    def test_eu_and_geographic_europe_are_distinct(self):
        eu, _ = build_data.infer_countries({
            "title_kr": "EU 집행위원회, 원자력 공동투자 기준 발표",
            "countries": ["EU"],
        })
        europe, source = build_data.infer_countries({
            "title_kr": "유럽 강 수위 저하로 전력 생산 차질",
            "countries": ["EU"],
        })
        self.assertEqual(eu, ["EU"])
        self.assertEqual(europe, ["EUROPE"])
        self.assertEqual(source, "eu-refined-v2")

    def test_country_trend_counts_distinct_issues_not_articles(self):
        issues = [
            {"members": [
                {"article_date": "2026-07-20", "countries": ["DE"]},
                {"article_date": "2026-07-21", "countries": ["DE"]},
            ]},
            {"members": [
                {"article_date": "2026-07-22", "countries": ["DE", "FR"]},
            ]},
            {"members": [
                {"article_date": "2026-06-01", "countries": ["FR"]},
            ]},
        ]
        counts = build_data.count_country_issues(issues, "2026-07-01")
        self.assertEqual(counts, {"DE": 2, "FR": 1})


class IssueSimilarityTests(unittest.TestCase):
    def test_paraphrased_12th_plan_articles_are_one_issue(self):
        # 생성 데이터의 특정 해시에 결합하지 않고, 같은 사건의 두 표현을 고정
        # 회귀 표본으로 둔다. 품질 마이그레이션으로 중복 기사가 삭제돼도 유효하다.
        left = {
            "title_kr": "12차 전기본, 원전 반영 여부 두고 정부 부처 간 정책 혼선",
            "summary": "12차 전력수급기본계획의 원전 반영 여부를 두고 정부 내 입장이 엇갈렸습니다.",
            "tags": ["#12차전기본", "#원전정책", "#정부정책"],
            "countries": ["KR"],
        }
        right = {
            "title_kr": "12차 전력수급기본계획, 원전 반영 여부 두고 정부 부처 간 혼선",
            "summary": "12차 전력수급기본계획 수립 과정에서 원전 반영을 두고 부처 간 이견이 보도됐습니다.",
            "tags": ["#12차전기본", "#에너지정책", "#정부이견"],
            "countries": ["KR"],
        }
        matched, _, diagnostics = build_data.issue_similarity(left, right)
        self.assertTrue(matched)
        self.assertEqual(diagnostics["tag_shared"], 1)

    def test_prepare_insights_drops_evidence_missing_from_public_news(self):
        insights = {
            "items": [{
                "keyword": "원전 정책",
                "direction": "원전 정책 논의가 이어졌습니다.",
                "evidence": [{"hash": "kept"}, {"hash": "removed"}],
            }],
        }
        news = [{
            "hash": "kept", "region": "국내", "countries": ["KR"],
            "topics": ["정책"], "publisher": "산업통상자원부", "domain": "motie.go.kr",
        }]
        prepared = build_data.prepare_insights(insights, news)
        self.assertEqual([row["hash"] for row in prepared["items"][0]["evidence"]], ["kept"])
        self.assertEqual(prepared["items"][0]["region_scope"], "국내")

    def test_unrelated_safety_events_stay_separate(self):
        left = {
            "title_kr": "다뉴브강 저수위로 헝가리 원전 가동 중단",
            "summary": "강 수위 저하로 냉각수 확보에 차질이 발생했다.",
            "tags": ["#원전안전", "#기후변화"],
        }
        right = {
            "title_kr": "미국 핵연구시설 글러브박스 화재 발생",
            "summary": "플루토늄 취급 시설에서 화재가 발생했다.",
            "tags": ["#원전안전", "#화재"],
        }
        matched, _, _ = build_data.issue_similarity(left, right)
        self.assertFalse(matched)

    def test_same_regulator_does_not_mean_same_issue(self):
        left = {
            "title_kr": "원자력안전위원회, 입법 및 행정예고 진행",
            "summary": "원안위가 입법예고 사항을 공지했다.",
            "tags": ["#원안위", "#입법예고"],
        }
        right = {
            "title_kr": "한울 4호기, 원자력안전위원회 정기검사 중 임계 허용",
            "summary": "원안위가 한울 4호기의 임계를 허용했다.",
            "tags": ["#원안위", "#한울4호기"],
        }
        matched, _, _ = build_data.issue_similarity(left, right)
        self.assertFalse(matched)

    def test_cached_embeddings_connect_supported_followup(self):
        left = {
            "hash": "left",
            "title_kr": "월성 계속운전 지원 체계 검토 착수",
            "summary": "지역 지원 제도를 검토한다.",
            "tags": ["#계속운전"],
            "topics": ["restart_lto"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "지역 상생 재원 논의 본격화",
            "summary": "장기운전과 연계한 재원을 논의한다.",
            "tags": ["#계속운전"],
            "topics": ["restart_lto"],
            "countries": ["KR"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [0.99, 0.01]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertTrue(matched)
        self.assertEqual(diagnostics["method"], "embedding")

    def test_country_conflict_blocks_embedding_merge(self):
        left = {
            "hash": "left",
            "title_kr": "한국 신규 원전 정책 발표",
            "tags": ["#원전정책"],
            "topics": ["policy_general"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "미국 신규 원전 지원책 공개",
            "tags": ["#원전정책"],
            "topics": ["policy_general"],
            "countries": ["US"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [1.0, 0.0]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertFalse(matched)
        self.assertIn("country_conflict", diagnostics["blocked_by"])

    def test_facility_conflict_blocks_embedding_merge(self):
        left = {
            "hash": "left",
            "title_kr": "월성 2호기 정기검사 진행",
            "tags": ["#정기검사"],
            "topics": ["regulation"],
            "countries": ["KR"],
        }
        right = {
            "hash": "right",
            "title_kr": "한울 4호기 정기검사 진행",
            "tags": ["#정기검사"],
            "topics": ["regulation"],
            "countries": ["KR"],
        }
        embeddings = {"left": [1.0, 0.0], "right": [1.0, 0.0]}
        matched, _, diagnostics = build_data.issue_similarity(left, right, embeddings)
        self.assertFalse(matched)
        self.assertIn("facility_conflict", diagnostics["blocked_by"])

    def test_recent_member_bridge_keeps_evolving_issue_together(self):
        articles = [
            {
                "hash": "a", "briefing_date": "2026-07-01", "article_date": "2026-07-01",
                "title_kr": "월성2호기 계속운전 지역지원 체계 검토",
                "tags": ["#월성2호기", "#지역지원"], "countries": ["KR"],
            },
            {
                "hash": "b", "briefing_date": "2026-07-02", "article_date": "2026-07-02",
                "title_kr": "월성2호기 지역지원 제도와 주민수용성 논의",
                "tags": ["#월성2호기", "#지역지원", "#주민수용성"], "countries": ["KR"],
            },
            {
                "hash": "c", "briefing_date": "2026-07-03", "article_date": "2026-07-03",
                "title_kr": "월성2호기 주민수용성 확보 위한 상생기금 협의",
                "tags": ["#월성2호기", "#주민수용성"], "countries": ["KR"],
            },
        ]
        matched_ac, _, _ = build_data.issue_similarity(articles[0], articles[2])
        self.assertFalse(matched_ac)
        issues = build_data.cluster_selected_articles(articles)
        self.assertEqual(len(issues), 1)
        self.assertEqual([member["hash"] for member in issues[0]["members"]], ["a", "b", "c"])


class GeneratedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data_dir = DATA_DIR
        cls.news = json.loads((data_dir / "news.json").read_text(encoding="utf-8"))
        cls.briefings = json.loads((data_dir / "briefings.json").read_text(encoding="utf-8"))
        cls.meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        cls.issue_audit = json.loads((data_dir / "issue_audit.json").read_text(encoding="utf-8"))
        cls.insights = json.loads((data_dir / "insights.json").read_text(encoding="utf-8"))
        cls.issue_catalog = json.loads((data_dir / "issues.json").read_text(encoding="utf-8"))

    def test_every_delivered_article_is_represented_once_in_its_briefing(self):
        for briefing in self.briefings:
            expected = sum(1 for article in self.news if article.get("briefing_date") == briefing["date"])
            current = sum(issue["current_article_count"] for issue in briefing["issues"])
            self.assertEqual(briefing["article_count"], expected)
            self.assertEqual(current, expected)

    def test_global_issue_catalog_contains_each_delivered_article_once(self):
        delivered_hashes = [article["hash"] for article in self.news if article.get("briefing_date")]
        catalog_hashes = [
            article["hash"]
            for issue in self.issue_catalog
            for article in issue["related_articles"]
        ]
        self.assertEqual(len(self.issue_catalog), self.meta["issue_catalog_total"])
        self.assertEqual(len({issue["issue_id"] for issue in self.issue_catalog}), len(self.issue_catalog))
        self.assertCountEqual(catalog_hashes, delivered_hashes)
        self.assertEqual(len(catalog_hashes), len(set(catalog_hashes)))

    def test_latest_briefing_keeps_previous_day_articles(self):
        latest = self.briefings[0]
        articles = [
            article
            for issue in latest["issues"]
            for article in issue["related_articles"]
            if article.get("briefing_date") == latest["date"]
        ]
        self.assertTrue(any(article["article_date"] < latest["date"] for article in articles))

    def test_selection_reasons_are_short(self):
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertLessEqual(len(issue["selection_reasons"]), 2)

    def test_local_taxonomy_enables_prototype_trend(self):
        self.assertEqual(self.meta["taxonomy_version"], "topic-v1-country-scope-v2")
        self.assertGreaterEqual(self.meta["topic_coverage"], 0.9)
        self.assertGreaterEqual(self.meta["country_coverage"], 0.9)
        self.assertTrue(self.meta["trend_ready"])

    def test_issue_rows_expose_topics_for_filtering(self):
        classified = [
            issue
            for briefing in self.briefings
            for issue in briefing["issues"]
            if issue.get("topics")
        ]
        self.assertGreater(len(classified), 0)

    def test_compact_flow_and_search_controls_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="issueSearch"', html)
        self.assertIn('id="topicSel"', html)
        self.assertIn('class="flow-takeaway"', script)
        self.assertIn('class="event-block"', script)

    def test_flow_takeaways_are_complete_sentences(self):
        takeaways = [item.get("takeaway", "") for item in self.insights.get("items", [])]
        self.assertTrue(takeaways)
        self.assertTrue(all(text.endswith((".", "!", "?")) for text in takeaways))
        self.assertTrue(all(not text.endswith("…") for text in takeaways))

    def test_featured_flows_cover_domestic_and_overseas_without_blind_top_three(self):
        featured = self.insights.get("featured_items", [])
        self.assertEqual(self.insights["selection_method"], "signal-region-evidence-diversity-v1")
        self.assertEqual(len(featured), 3)
        regions = {region for item in featured for region in item.get("evidence_regions", [])}
        self.assertIn("국내", regions)
        self.assertIn("해외", regions)
        self.assertTrue(all(item.get("region_scope") for item in featured))
        self.assertTrue(all(
            all(evidence.get("region") in {"국내", "해외"} for evidence in item.get("evidence", []))
            for item in featured
        ))

    def test_explanatory_copy_is_removed(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        combined = html + script
        self.assertNotIn("뉴스를 기사보다 이슈 단위로 읽습니다", combined)
        self.assertNotIn("발행일이 아니라 이 브리핑에서 다룬 사안을 기준으로 묶었습니다", combined)

    def test_search_scope_and_balanced_region_stats_are_explicit(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("이 브리핑에서 기관·시설·주제 검색", html)
        self.assertIn("<small>국내</small>", script)
        self.assertIn("<small>해외</small>", script)

    def test_issue_detail_dialog_and_url_state_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="issueDialog"', html)
        self.assertIn('id="issueDialogContent"', html)
        self.assertIn("function openIssueDialog", script)
        self.assertIn('params.set("issue", state.issueId)', script)
        self.assertIn('class="issue-detail-button"', script)
        self.assertIn('class="flow-region ${scopeClass}"', script)

    def test_global_issue_search_view_and_url_filters_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        for element_id in ("view-search", "archiveSearch", "archiveRegion", "archiveTopic", "archiveIssueList", "archiveMore"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('data-view="search"', html)
        self.assertIn("function renderArchiveSearch", script)
        self.assertIn('params.set("aq", state.archiveQuery)', script)
        self.assertIn('loadJSON("issues.json")', script)

    def test_manifest_loading_and_operation_status_ui_exist(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="systemStatus"', html)
        self.assertIn("async function initializeDataBase", script)
        self.assertIn('loadRootJSON("manifest.json", true)', script)
        manifest = json.loads((DATA_ROOT / "manifest.json").read_text(encoding="utf-8"))
        status = json.loads((DATA_ROOT / "status.json").read_text(encoding="utf-8"))
        meta = json.loads((DATA_ROOT / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["generation_id"])
        self.assertEqual(manifest["generation_id"], status["generation_id"])
        self.assertEqual(manifest["generation_id"], meta["generation_id"])

    def test_initial_data_connection_recovers_without_manual_reload(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        self.assertIn("initRetryCount <= 5", script)
        self.assertIn("window.setTimeout(init, delay)", script)
        self.assertIn('id="retryInit"', script)
        self.assertIn('window.addEventListener("online"', script)
        self.assertIn("if (appReady || initLoading) return", script)
        self.assertIn("function renderSystemStatus", script)
        self.assertIn("window.setInterval(checkForNewGeneration, 60000)", script)

    def test_ongoing_issues_expose_tracking_metadata(self):
        briefings = json.loads((DATA_DIR / "briefings.json").read_text(encoding="utf-8"))
        ongoing = [issue for briefing in briefings for issue in briefing["issues"] if issue["status"] == "ongoing"]
        self.assertTrue(ongoing)
        self.assertTrue(all(issue["tracked_briefings"] >= 2 for issue in ongoing))
        self.assertTrue(all(issue["previous_article_count"] >= 1 for issue in ongoing))
        self.assertTrue(all(issue["latest_change"] for issue in ongoing))
        self.assertTrue(all(issue["latest_change"].endswith((".", "!", "?")) for issue in ongoing))

    def test_issue_detail_timeline_contains_every_linked_article(self):
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertEqual(len(issue["related_articles"]), issue["article_count"])

    def test_issue_matching_audit_is_generated(self):
        self.assertEqual(self.meta["issue_matching_version"], "hybrid-review-v3")
        self.assertIn("embedding_cache_entries", self.meta)
        self.assertGreater(self.meta["embedding_selected_count"], 0)
        self.assertGreaterEqual(self.meta["embedding_selected_coverage"], 0.95)
        self.assertIn("remote_embedding_selected_count", self.meta)
        self.assertEqual(self.issue_audit["matching_version"], "hybrid-review-v3")
        self.assertTrue(self.issue_audit["review_candidates"])
        self.assertTrue(all(row["review_state"] == "pending" for row in self.issue_audit["review_candidates"]))
        self.assertGreaterEqual(self.meta["latest_briefing_tracking_rate"], 0.20)
        self.assertTrue(self.issue_audit["clusters"])
        self.assertTrue(all(cluster["matches"] for cluster in self.issue_audit["clusters"]))

    def test_manual_merge_overrides_are_auditable(self):
        approved = set(self.issue_audit["overrides"]["approved"])
        rejected = set(self.issue_audit["overrides"]["rejected"])
        pending = {row["candidate_id"] for row in self.issue_audit["review_candidates"]}
        self.assertTrue(approved)
        self.assertTrue(rejected)
        self.assertTrue(approved.isdisjoint(rejected | pending))
        methods = {
            match["method"]
            for cluster in self.issue_audit["clusters"]
            for match in cluster["matches"]
        }
        self.assertIn("manual_approved", methods)

    def test_generated_issue_clusters_have_no_country_or_facility_conflicts(self):
        by_hash = {article["hash"]: article for article in self.news}
        non_country_scopes = {"OTHER", "UNSPECIFIED", "GLOBAL", "EUROPE", "EU"}
        for cluster in self.issue_audit["clusters"]:
            members = [by_hash[member["hash"]] for member in cluster["members"]]
            for left, right in combinations(members, 2):
                left_countries = set(left.get("countries") or []) - non_country_scopes
                right_countries = set(right.get("countries") or []) - non_country_scopes
                if left_countries and right_countries and left_countries.isdisjoint(right_countries):
                    # 국경을 넘는 하나의 사건은 양국을 함께 명시한 중간 기사로 연결될
                    # 수 있다. 과거 EU_ETC 묶음 없이도 그 연결 근거가 있어야 한다.
                    has_cross_border_bridge = any(
                        left_countries & (set(member.get("countries") or []) - non_country_scopes)
                        and right_countries & (set(member.get("countries") or []) - non_country_scopes)
                        for member in members
                    )
                    self.assertTrue(has_cross_border_bridge)
                self.assertFalse(build_data._facility_conflict(left, right))

    def test_region_matches_confident_country_tags(self):
        self.assertEqual(self.meta["region_classification_version"], "country-first-v1")
        self.assertEqual(self.meta["region_country_mismatch_count"], 0)
        for article in self.news:
            countries = set(article.get("countries") or []) - {"OTHER"}
            if not countries:
                continue
            expected = "국내" if "KR" in countries else "해외"
            self.assertEqual(article["region"], expected, article["title_kr"])

    def test_brand_is_kept_private_until_access_control_decision(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("NUCLENS", html)
        self.assertIn('content="noindex,nofollow"', html)
        self.assertIn('name="color-scheme" content="light"', html)
        self.assertIn('name="description"', html)
        self.assertNotIn('rel="canonical"', html)
        self.assertNotIn('property="og:title"', html)
        for name in ("favicon.svg", "robots.txt"):
            self.assertTrue((ROOT / "public" / name).exists(), name)
        self.assertFalse((ROOT / "public" / "sitemap.xml").exists())
        self.assertIn("Disallow: /", (ROOT / "public" / "robots.txt").read_text(encoding="utf-8"))

    def test_stage_two_navigation_and_ai_disclosure(self):
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn('data/${name}?t=', script)
        self.assertIn("window.scrollTo(0, 0)", script)
        self.assertIn('state.view === "search" && state.archiveQuery', script)
        self.assertIn('syncUrl("push")', script)
        self.assertIn('window.addEventListener("popstate"', script)
        self.assertGreaterEqual(script.count('class="ai-badge"'), 4)
        self.assertIn(".ai-badge", style)

    def test_rss_and_report_copy_are_generated(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
        rss_path = ROOT / "public" / "rss.xml"
        self.assertIn('type="application/rss+xml"', html)
        self.assertTrue(rss_path.exists())
        channel = ET.parse(rss_path).getroot().find("channel")
        self.assertIsNotNone(channel)
        self.assertTrue(channel.findall("item"))
        self.assertIn("function issueReportText", script)
        self.assertIn("• 이번 브리핑에서 새로 확인된 것:", script)
        self.assertIn('data-copy-issue="${esc(issue.issue_id)}"', script)

    def test_ci_persists_embeddings_and_fails_on_web_smoke_errors(self):
        repo_root = ROOT.parent
        crawl = (repo_root / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        daily = (repo_root / ".github" / "workflows" / "daily-brief.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache/restore@v4", crawl)
        self.assertIn("actions/cache/save@v4", crawl)
        self.assertIn("Restore embeddings cache", daily)
        self.assertIn("gemini-embedding-2", crawl)
        self.assertIn("--window-days 21", crawl)
        self.assertIn("--require-nonzero", daily)
        self.assertNotIn("- name: Smoke test live site\n        continue-on-error: true", crawl)
        self.assertNotIn("- name: Render smoke (라이브 화면 검증)\n        if: always() && steps.claim.conclusion == 'success'\n        continue-on-error: true", daily)


if __name__ == "__main__":
    unittest.main()
