"""daily_brief.py 단위 테스트 — 분류·투자 구조화·보고서 게이트·outbox 원자성."""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

# telegram_send 는 토큰 없으면 import 시 sys.exit → 테스트에선 공용 fake 주입
import _fake_tg  # noqa: E402
fake_tg = _fake_tg.installed

import daily_brief as db  # noqa: E402
import ranking  # noqa: E402

NOW = datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)


def qitem(h="h1", importance="nice_to_know", section="international",
          domain="world-nuclear-news.org", title="Title", features=None, **kw):
    d = {"hash": h, "importance": importance, "section": section, "domain": domain,
         "title": title, "title_kr": kw.pop("title_kr", title),
         "link": f"https://{domain}/x/{h}",
         "summary": kw.pop("summary", "요약입니다"),
         "implication": kw.pop("implication", "시사점입니다"),
         "queued_at": kw.pop("queued_at", NOW.isoformat()),
         "related_reports": [], "tags": []}
    if features is not None:
        d["features"] = features
    d.update(kw)
    return d


class TestRegion(unittest.TestCase):
    def test_khnp_section_domestic_even_foreign_source(self):
        self.assertEqual(db.region({"section": "khnp", "domain": "reuters.com"}), "국내")

    def test_us_article_misclassified_domestic_corrected(self):
        self.assertEqual(db.region({"section": "domestic", "domain": "ans.org"}), "해외")

    def test_kr_domain(self):
        self.assertEqual(db.region({"section": "international", "domain": "yna.co.kr"}), "국내")

    def test_international(self):
        self.assertEqual(db.region({"section": "international", "domain": "unknown.io"}), "해외")

    def test_google_kr_feed(self):
        self.assertEqual(db.region({"section": "domestic", "domain": "news.google.co.kr"}), "국내")


class TestInvestment(unittest.TestCase):
    def test_weak_evidence_omitted(self):
        # confidence 0 / theme none / mechanism 없음 → 전부 생략
        self.assertIsNone(db.render_investment(None))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "none", "mechanism": "뭔가", "confidence": 2})))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "smr", "mechanism": "", "confidence": 2})))
        self.assertIsNone(db.render_investment(db._sanitize_invest(
            {"theme": "smr", "mechanism": "근거 약함", "confidence": 0})))

    def test_render_full(self):
        txt = db.render_investment(db._sanitize_invest({
            "theme": "grid_demand", "mechanism": "데이터센터 PPA로 재가동 원전의 장기 판매가가 고정된다",
            "beneficiary_type": "utility", "risk_side": "가스 피크발전",
            "time_horizon": "mid", "confidence": 2}))
        self.assertIn("발전사업자 수혜", txt)
        self.assertIn("가스 피크발전 부담", txt)
        self.assertIn("전력수요", txt)
        self.assertNotIn("확신 낮음", txt)

    def test_low_confidence_hedged(self):
        txt = db.render_investment(db._sanitize_invest({
            "theme": "uranium", "mechanism": "감산이 이어지면 현물가 상방",
            "beneficiary_type": "uranium_miner", "time_horizon": "near",
            "confidence": 1}))
        self.assertIn("확신 낮음", txt)

    def test_sanitize_bad_values(self):
        s = db._sanitize_invest({"theme": "meme_stocks", "mechanism": "x" * 500,
                                 "beneficiary_type": "tesla", "time_horizon": "tomorrow",
                                 "confidence": "high"})
        self.assertEqual(s["theme"], "none")
        self.assertEqual(s["beneficiary_type"], "none")
        self.assertEqual(s["time_horizon"], "mid")
        self.assertEqual(s["confidence"], 0)
        self.assertLessEqual(len(s["mechanism"]), 180)

    def test_enrich_gemini_error_returns_empty(self):
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True

        def boom(*a, **k):
            raise db.GeminiError("429 (모의)")
        db.call_json = boom
        try:
            self.assertEqual(db.enrich_investment([qitem()]), {})
        finally:
            db.call_json, db.is_available = orig_call, orig_avail


class TestReportGate(unittest.TestCase):
    def feat(self, **kw):
        base = {"event_type": "other", "korea_relevance": 0, "market_materiality": 0,
                "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
                "report_worthiness": 0}
        base.update(kw)
        return base

    def test_zero_candidates_no_llm(self):
        items = [qitem(h="a", features=self.feat(report_worthiness=1)),
                 qitem(h="b", features=self.feat())]
        called = []
        orig = db.call_json
        db.call_json = lambda *a, **k: called.append(1) or {"reports": []}
        try:
            msg, diag = db.build_report_recs(items)
        finally:
            db.call_json = orig
        self.assertEqual(msg, "")
        self.assertEqual(diag["candidates"], [])
        self.assertEqual(called, [])  # 후보 0건이면 Gemini 호출 자체가 없다

    def test_gate_requires_strong_signal(self):
        weak = qitem(h="a", features=self.feat(report_worthiness=3))  # 이벤트·정책·등급 없음
        strong = qitem(h="b", importance="must_read",
                       features=self.feat(report_worthiness=2))
        out = db.gate_report_candidates([weak, strong], negatives=[])
        self.assertEqual([a["hash"] for a in out], ["b"])

    def test_legacy_item_must_read_passes(self):
        legacy = qitem(h="c", importance="must_read")  # features 없음 (옛 스키마)
        out = db.gate_report_candidates([legacy], negatives=[])
        self.assertEqual(len(out), 1)

    def test_negative_example_blocks(self):
        a = qitem(h="a", importance="must_read",
                  title_kr="체코 언론, 두코바니 일정 지연 가능성 보도")
        negs = ["체코 언론, 두코바니 일정 지연 가능성 보도 — 전망성 반복"]
        self.assertEqual(db.gate_report_candidates([a], negatives=negs), [])

    def test_cap_two_and_invalid_idx(self):
        cands = [qitem(h=f"c{i}", importance="must_read",
                       features=self.feat(report_worthiness=3, policy_materiality=3))
                 for i in range(4)]
        orig_call, orig_avail = db.call_json, db.is_available
        db.is_available = lambda: True
        db.call_json = lambda *a, **k: {"reports": [
            {"idx": 0, "topic": "T0", "why": "w"},
            {"idx": 99, "topic": "무효 idx"},         # 필터돼야 함
            {"idx": 1, "topic": "T1"},
            {"idx": 2, "topic": "T2 — 3건째, 컷"},
        ]}
        try:
            msg, diag = db.build_report_recs(cands)
        finally:
            db.call_json, db.is_available = orig_call, orig_avail
        self.assertEqual(len(diag["recommended"]), 2)  # 하루 최대 2건
        self.assertIn("T0", msg)
        self.assertIn("T1", msg)
        self.assertNotIn("T2", msg)
        self.assertNotIn("무효", msg)


class OutboxBase(unittest.TestCase):
    """tmpdir 로 상태 파일 경로를 돌려서 실제 파일 흐름 검증."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        p = Path(self.tmp.name)
        self._orig = (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE,
                      db.DELIVERY_LOG_FILE, ranking.FEEDBACK_DIR,
                      ranking.DELIVERY_LOG_FILE)
        db.QUEUE_FILE = p / "digest_queue.json"
        db.OUTBOX_FILE = p / "outbox.json"
        db.OUTBOX_RESULT_FILE = p / "outbox_result.json"
        db.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        ranking.FEEDBACK_DIR = p / "feedback"
        ranking.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        # Gemini 차단 (호출 0)
        self._avail = db.is_available
        db.is_available = lambda: False
        fake_tg.sent_messages = []
        fake_tg.fail_next = False

    def tearDown(self):
        (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE,
         db.DELIVERY_LOG_FILE, ranking.FEEDBACK_DIR,
         ranking.DELIVERY_LOG_FILE) = self._orig
        db.is_available = self._avail
        self.tmp.cleanup()

    def seed_queue(self, items):
        db.save_queue(items)


class TestOutboxFlow(OutboxBase):
    def _queue(self):
        return [
            qitem(h="d1", section="khnp", domain="khnp.co.kr", importance="must_read",
                  title="한수원 체코 본계약"),
            qitem(h="f1", section="international", title="NRC approves NuScale design"),
            qitem(h="f2", section="international", title="NRC approves the NuScale design today"),  # f1 후속
            qitem(h="n1", importance="noise", title="채용 공고"),
            qitem(h="m1", importance="market", title="테마주 급등"),
        ]

    def test_plan_prunes_selected_dups_junk_keeps_rest(self):
        many = self._queue() + [
            qitem(h=f"f{i}", section="international", title=f"별개 해외뉴스 {i} 완전다름")
            for i in range(3, 11)]
        self.seed_queue(many)
        self.assertEqual(db.cmd_plan(), 0)
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "pending")
        queue_after = db.load_queue()
        left = {a["hash"] for a in queue_after}
        # noise/market/선별/후속 은 제거, 미선별 해외는 잔류 (다음날 재경쟁)
        self.assertNotIn("n1", left)
        self.assertNotIn("m1", left)
        self.assertNotIn("d1", left)
        self.assertNotIn("f2", left)  # f1(선별)의 중복이므로 함께 제거
        selected = {i["hash"] for i in outbox["items"]}
        self.assertNotIn("f2", selected)
        self.assertEqual(len(left), len(many) - len(outbox["prune_hashes"]))

    def test_empty_queue_plan(self):
        self.seed_queue([])
        db.cmd_plan()
        self.assertEqual(db.load_outbox()["status"], "empty")
        self.assertEqual(db.cmd_send(), 0)  # empty → 발송 스킵, 에러 아님
        self.assertEqual(fake_tg.sent_messages, [])

    def test_send_then_rerun_no_duplicates(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        self.assertEqual(db.cmd_send(), 0)
        n_first = len(fake_tg.sent_messages)
        self.assertGreater(n_first, 0)
        # 같은 날 재실행: plan 재사용 + send 는 sent 스킵 → 재발송 0
        self.assertEqual(db.cmd_plan(), 0)
        self.assertEqual(db.cmd_send(), 0)
        self.assertEqual(len(fake_tg.sent_messages), n_first)

    def test_claim_then_send_failure_marks_failed(self):
        """claim(plan) 후 텔레그램 발송 실패 → failed 기록, 재시도로 회복."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        fake_tg.fail_next = True
        rc = db.cmd_send()
        self.assertEqual(rc, 1)
        outbox = db.load_outbox()
        statuses = [b["status"] for b in outbox["briefs"]]
        self.assertIn("failed", statuses)
        # 재실행 → failed 만 다시 발송
        n = len(fake_tg.sent_messages)
        self.assertEqual(db.cmd_send(), 0)
        self.assertGreater(len(fake_tg.sent_messages), n)
        self.assertEqual(db.load_outbox()["status"], "sent")

    def test_send_success_but_state_lost_recovered_by_result_file(self):
        """발송 성공 후 outbox 저장분 유실(git reset 모의) → outbox_result 로 복구."""
        self.seed_queue(self._queue())
        db.cmd_plan()
        claim_snapshot = db.OUTBOX_FILE.read_text(encoding="utf-8")  # push 된 claim 상태
        db.cmd_send()
        # git reset --hard 모의: outbox 가 claim(pending) 상태로 되돌아감
        db.OUTBOX_FILE.write_text(claim_snapshot, encoding="utf-8")
        self.assertEqual(db.cmd_confirm(), 0)
        self.assertEqual(db.load_outbox()["status"], "sent")  # 멱등 병합으로 복원

    def test_stale_outbox_not_resent(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        outbox = db.load_outbox()
        outbox["created_at"] = (NOW - timedelta(hours=48)).isoformat()
        db.save_outbox(outbox)
        db.send_outbox(outbox, now=NOW)
        self.assertEqual(fake_tg.sent_messages, [])
        self.assertTrue(all(b["status"] == "stale_skipped" for b in outbox["briefs"]))

    def test_yesterday_pending_blocks_new_plan(self):
        """직전 outbox 미발송(<36h) → 새 계획 생략 (재발송 우선)."""
        yesterday = {"schema_version": 1, "date": "2026-07-11",
                     "created_at": (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat(),
                     "status": "pending",
                     "briefs": [{"name": "국내", "text": "x", "keyboard": None,
                                 "status": "pending"}],
                     "items": [], "prune_hashes": []}
        db.save_outbox(yesterday)
        self.seed_queue(self._queue())
        db.cmd_plan()
        self.assertEqual(db.load_outbox()["date"], "2026-07-11")  # 덮어쓰지 않음

    def test_delivery_log_idempotent(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        db.cmd_send()
        self.assertEqual(db.cmd_confirm(), 0)
        n1 = len(db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines())
        self.assertEqual(db.cmd_confirm(), 0)  # 두 번 confirm(재시도 모의)
        n2 = len(db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines())
        self.assertEqual(n1, n2)
        rec = json.loads(db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("breakdown", rec)  # 점수 내역이 남는다

    def test_feedback_keyboard_attached(self):
        self.seed_queue(self._queue())
        db.cmd_plan()
        db.cmd_send()
        with_kb = [m for m in fake_tg.sent_messages if m["reply_markup"]]
        self.assertTrue(with_kb)
        btn = with_kb[0]["reply_markup"]["inline_keyboard"][0][0]
        self.assertTrue(btn["callback_data"].startswith("fb:"))
        self.assertLessEqual(len(btn["callback_data"].encode()), 64)


class TestBackwardCompat(OutboxBase):
    def test_old_queue_schema_loads_and_plans(self):
        """features/why_important 없는 기존 큐 JSON — 그대로 계획·발송 가능해야 함."""
        old_item = {"hash": "old1", "title": "Old article", "title_kr": "옛 기사",
                    "link": "https://world-nuclear-news.org/x", "domain": "world-nuclear-news.org",
                    "feed": "정책", "matched": "kw", "importance": "must_read",
                    "section": "international", "category": "정책", "summary": "요약",
                    "implication": "시사점", "watch_next": "", "tags": [],
                    "related_reports": [], "queued_at": datetime.now(timezone.utc).isoformat()}
        self.seed_queue([old_item])
        self.assertEqual(db.cmd_plan(), 0)
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "pending")
        self.assertEqual(outbox["items"][0]["hash"], "old1")
        self.assertEqual(db.cmd_send(), 0)


if __name__ == "__main__":
    unittest.main()
