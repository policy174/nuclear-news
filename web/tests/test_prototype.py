import json
import sys
import unittest
from itertools import combinations
from pathlib import Path


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


class IssueSimilarityTests(unittest.TestCase):
    def test_paraphrased_12th_plan_articles_are_one_issue(self):
        news = json.loads((DATA_DIR / "news.json").read_text(encoding="utf-8"))
        by_hash = {article["hash"]: article for article in news}
        left = by_hash["9846193b68301679"]
        right = by_hash["d4658d844ee27556"]
        matched, _, diagnostics = build_data.issue_similarity(left, right)
        self.assertTrue(matched)
        self.assertEqual(diagnostics["tag_shared"], 1)

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
        self.assertEqual(self.meta["taxonomy_version"], "prototype-heuristic-v1")
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

    def test_issue_detail_timeline_contains_every_linked_article(self):
        for briefing in self.briefings:
            for issue in briefing["issues"]:
                self.assertEqual(len(issue["related_articles"]), issue["article_count"])

    def test_issue_matching_audit_is_generated(self):
        self.assertEqual(self.meta["issue_matching_version"], "hybrid-guarded-v2")
        self.assertIn("embedding_cache_entries", self.meta)
        self.assertEqual(self.issue_audit["matching_version"], "hybrid-guarded-v2")
        self.assertTrue(self.issue_audit["clusters"])
        self.assertTrue(all(cluster["matches"] for cluster in self.issue_audit["clusters"]))

    def test_generated_issue_clusters_have_no_country_or_facility_conflicts(self):
        by_hash = {article["hash"]: article for article in self.news}
        for cluster in self.issue_audit["clusters"]:
            members = [by_hash[member["hash"]] for member in cluster["members"]]
            for left, right in combinations(members, 2):
                self.assertFalse(build_data._country_conflict(left, right))
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

    def test_public_brand_and_indexing_metadata_are_complete(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn("NUCLENS", html)
        self.assertNotIn('content="noindex"', html)
        self.assertIn('name="description"', html)
        self.assertIn('rel="canonical"', html)
        self.assertIn('property="og:title"', html)
        for name in ("favicon.svg", "robots.txt", "sitemap.xml"):
            self.assertTrue((ROOT / "public" / name).exists(), name)

    def test_ci_persists_embeddings_and_fails_on_web_smoke_errors(self):
        repo_root = ROOT.parent
        crawl = (repo_root / ".github" / "workflows" / "crawl.yml").read_text(encoding="utf-8")
        daily = (repo_root / ".github" / "workflows" / "daily-brief.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache/restore@v4", crawl)
        self.assertIn("actions/cache/save@v4", crawl)
        self.assertIn("Restore embeddings cache", daily)
        self.assertNotIn("- name: Smoke test live site\n        continue-on-error: true", crawl)
        self.assertNotIn("- name: Render smoke (라이브 화면 검증)\n        if: always() && steps.claim.conclusion == 'success'\n        continue-on-error: true", daily)


if __name__ == "__main__":
    unittest.main()
