"""E2E 모의 실행 — 외부 호출 0 으로 전체 파이프라인 검증.

fixture 큐 → 랭킹 feature 계산 → 국내/해외 top-k → 투자 관점(모의 Gemini) →
보고서 추천 → 카드 렌더링(키보드 포함) → outbox claim → 발송(모의 텔레그램) →
delivery_log → 피드백 수신 → 피드백이 다음날 랭킹에 반영 → weekly 집계.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import _fake_tg  # noqa: E402 — 공용 fake telegram_send 선등록
fake_tg = _fake_tg.installed

import daily_brief as db  # noqa: E402
import feedback_ingest as fi  # noqa: E402
import ranking  # noqa: E402
import weekly_bot  # noqa: E402

NOW = datetime.now(timezone.utc)


def feat(**kw):
    base = {"event_type": "other", "korea_relevance": 0, "market_materiality": 0,
            "policy_materiality": 0, "novelty": 0, "evidence_strength": 0,
            "report_worthiness": 0}
    base.update(kw)
    return base


FIXTURE_QUEUE = [
    # 국내 — 한수원 계약 (must_read, 보고서감)
    {"hash": "d1d1d1d1", "title": "KHNP signs Dukovany contract", "title_kr": "한수원, 두코바니 본계약 체결",
     "link": "https://khnp.co.kr/1", "domain": "khnp.co.kr", "feed": "정책", "matched": "x",
     "importance": "must_read", "section": "khnp", "category": "시장",
     "summary": "한수원이 체코 두코바니 2기 본계약을 체결했습니다.",
     "implication": "유럽 후속 수주 기대", "why_important": "국내 원전 수출 첫 유럽 본계약.",
     "tags": ["#체코수주"], "related_reports": [],
     "features": feat(event_type="contract_award", korea_relevance=3,
                      market_materiality=3, policy_materiality=2, novelty=3,
                      evidence_strength=3, report_worthiness=3),
     "queued_at": NOW.isoformat()},
    # 국내 — 일반
    {"hash": "d2d2d2d2", "title": "원안위 정기회의", "title_kr": "원안위, 정기회의서 계속운전 심사 착수",
     "link": "https://nssc.go.kr/2", "domain": "nssc.go.kr", "feed": "정책", "matched": "x",
     "importance": "nice_to_know", "section": "domestic", "category": "규제",
     "summary": "요약입니다.", "implication": "심사 일정 주시", "why_important": "",
     "tags": [], "related_reports": [],
     "features": feat(event_type="regulatory_action", korea_relevance=3,
                      policy_materiality=2, novelty=2, evidence_strength=2),
     "queued_at": NOW.isoformat()},
    # 해외 — SMR 3건 (같은 theme 과다 노출 후보) + 후속보도 중복 1건
    {"hash": "f1f1f1f1", "title": "NRC certifies NuScale uprated design",
     "title_kr": "NRC, NuScale 상향 설계 인증", "link": "https://world-nuclear-news.org/1",
     "domain": "world-nuclear-news.org", "feed": "SMR", "matched": "x",
     "importance": "must_read", "section": "smr", "category": "규제",
     "summary": "NRC가 NuScale 설계를 인증했습니다.", "implication": "SMR 인허가 가속",
     "why_important": "미 SMR 상업화 관문 통과.", "tags": [], "related_reports": [],
     "features": feat(event_type="regulatory_action", market_materiality=3,
                      policy_materiality=2, novelty=3, evidence_strength=3,
                      report_worthiness=2),
     "queued_at": NOW.isoformat()},
    {"hash": "f2f2f2f2", "title": "NRC certifies the NuScale uprated design today",
     "title_kr": "NRC NuScale 상향 설계를 인증했다", "link": "https://ans.org/2",
     "domain": "ans.org", "feed": "SMR", "matched": "x",
     "importance": "nice_to_know", "section": "smr", "category": "규제",
     "summary": "후속 보도입니다.", "implication": "", "why_important": "",
     "tags": [], "related_reports": [],
     "features": feat(event_type="regulatory_action", novelty=0, evidence_strength=2),
     "queued_at": NOW.isoformat()},
    {"hash": "f3f3f3f3", "title": "X-energy breaks ground in Texas",
     "title_kr": "X-energy, 텍사스 착공", "link": "https://world-nuclear-news.org/3",
     "domain": "world-nuclear-news.org", "feed": "SMR", "matched": "x",
     "importance": "nice_to_know", "section": "smr", "category": "시장",
     "summary": "착공했습니다.", "implication": "", "why_important": "",
     "tags": [], "related_reports": [],
     "features": feat(event_type="project_milestone", market_materiality=2,
                      novelty=2, evidence_strength=3),
     "queued_at": NOW.isoformat()},
    {"hash": "f4f4f4f4", "title": "Kairos gets construction permit",
     "title_kr": "Kairos, 건설허가 취득", "link": "https://ans.org/4",
     "domain": "ans.org", "feed": "SMR", "matched": "x",
     "importance": "nice_to_know", "section": "smr", "category": "규제",
     "summary": "허가 취득.", "implication": "", "why_important": "",
     "tags": [], "related_reports": [],
     "features": feat(event_type="regulatory_action", market_materiality=2,
                      novelty=2, evidence_strength=3),
     "queued_at": NOW.isoformat()},
    # 해외 — 우라늄 (다양성으로 들어와야 함)
    {"hash": "f5f5f5f5", "title": "Kazatomprom cuts 2027 guidance",
     "title_kr": "카자톰프롬, 2027 생산 가이던스 하향", "link": "https://reuters.com/5",
     "domain": "reuters.com", "feed": "정책", "matched": "x",
     "importance": "nice_to_know", "section": "international", "category": "시장",
     "summary": "감산 발표.", "implication": "우라늄 수급 타이트",
     "why_important": "", "tags": [], "related_reports": [],
     "features": feat(event_type="corporate_move", market_materiality=3, novelty=2,
                      evidence_strength=2),
     "queued_at": NOW.isoformat()},
    # 해외 — 의견 기사 (낮게 깔려야 함)
    {"hash": "f6f6f6f6", "title": "Opinion: nuclear renaissance is overhyped",
     "title_kr": "칼럼: 원자력 르네상스는 과장이다", "link": "https://example.com/6",
     "domain": "example.com", "feed": "정책", "matched": "x",
     "importance": "nice_to_know", "section": "international", "category": "정책",
     "summary": "칼럼.", "implication": "", "why_important": "", "tags": [],
     "related_reports": [],
     "features": feat(event_type="opinion", evidence_strength=0),
     "queued_at": NOW.isoformat()},
    # noise/market — 발송 제외 대상
    {"hash": "n1n1n1n1", "title": "채용 공고", "title_kr": "채용 공고",
     "link": "https://x.com/n1", "domain": "x.com", "feed": "정책", "matched": "x",
     "importance": "noise", "section": "domestic", "category": "정책", "summary": "",
     "implication": "", "why_important": "", "tags": [], "related_reports": [],
     "queued_at": NOW.isoformat()},
    # 옛 스키마 (features 없음) — legacy 경로
    {"hash": "e1e1e1e1", "title": "Legacy old-schema article", "title_kr": "옛 스키마 기사",
     "link": "https://world-nuclear-news.org/old", "domain": "world-nuclear-news.org",
     "feed": "정책", "matched": "x", "importance": "nice_to_know",
     "section": "international", "category": "정책", "summary": "요약",
     "implication": "시사점", "watch_next": "", "tags": [], "related_reports": [],
     "queued_at": NOW.isoformat()},
]


def mock_call_json(system_prompt, user_message, **kw):
    """Gemini 모의 — 프롬프트 종류를 보고 스키마에 맞는 응답 반환."""
    if "investments" in system_prompt:
        n = len(user_message.strip().splitlines())
        inv = []
        for i in range(n):
            if i == 0:
                inv.append({"idx": 0, "theme": "export",
                            "mechanism": "본계약 체결로 유럽 수주 파이프라인의 실현 확률이 올라간다",
                            "beneficiary_type": "reactor_vendor", "risk_side": "none",
                            "time_horizon": "mid", "confidence": 2})
            elif i == n - 1:
                inv.append({"idx": i, "theme": "none", "mechanism": "",
                            "confidence": 0})  # 근거 약함 → 생략돼야 함
            else:
                inv.append({"idx": i, "theme": "smr",
                            "mechanism": "인허가 진전이 SMR 밸류체인 자본지출을 앞당긴다",
                            "beneficiary_type": "smr_developer", "risk_side": "none",
                            "time_horizon": "mid", "confidence": 1})
        return {"investments": inv}
    if "보고서 후보" in system_prompt:
        return {"reports": [{"idx": 0, "topic": "두코바니 본계약의 전략적 함의",
                             "why": "유럽 첫 본계약.", "angles": ["후속 입찰", "리스크"]}]}
    raise AssertionError(f"예상 밖 Gemini 호출: {system_prompt[:40]}")


class TestE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        p = Path(self.tmp.name)
        self._orig = (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE,
                      db.DELIVERY_LOG_FILE, ranking.FEEDBACK_DIR,
                      ranking.DELIVERY_LOG_FILE, db.call_json, db.is_available,
                      fi.STATE_FILE, fi.FEEDBACK_DIR)
        db.QUEUE_FILE = p / "digest_queue.json"
        db.OUTBOX_FILE = p / "outbox.json"
        db.OUTBOX_RESULT_FILE = p / "outbox_result.json"
        db.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        ranking.FEEDBACK_DIR = p / "feedback"
        ranking.DELIVERY_LOG_FILE = p / "delivery_log.jsonl"
        fi.STATE_FILE = p / "feedback_state.json"
        fi.FEEDBACK_DIR = p / "feedback"
        db.call_json = mock_call_json
        db.is_available = lambda: True
        fake_tg.sent_messages = []

    def tearDown(self):
        (db.QUEUE_FILE, db.OUTBOX_FILE, db.OUTBOX_RESULT_FILE, db.DELIVERY_LOG_FILE,
         ranking.FEEDBACK_DIR, ranking.DELIVERY_LOG_FILE, db.call_json,
         db.is_available, fi.STATE_FILE, fi.FEEDBACK_DIR) = self._orig
        self.tmp.cleanup()

    def test_full_pipeline(self):
        db.save_queue(json.loads(json.dumps(FIXTURE_QUEUE)))

        # ── ① plan: 랭킹 → top-k → 투자/보고서 → outbox ──
        self.assertEqual(db.cmd_plan(), 0)
        outbox = db.load_outbox()
        self.assertEqual(outbox["status"], "pending")
        names = [b["name"] for b in outbox["briefs"]]
        self.assertEqual(names, ["보고서추천", "국내", "해외"])

        sel = {i["hash"]: i for i in outbox["items"]}
        self.assertIn("d1d1d1d1", sel)          # must_read 계약이 최상위
        self.assertNotIn("f2f2f2f2", sel)       # 후속보도는 대표(f1)에 흡수
        self.assertIn("f5f5f5f5", sel)          # 다양성: 우라늄이 SMR 독식을 뚫음
        self.assertIn("f1f1f1f1", sel)
        # 점수 내역 존재 (설명 가능성)
        self.assertTrue(all(i.get("breakdown") for i in outbox["items"]))

        # 큐: 선별+중복+noise 제거, 미선별(칼럼 등)은 잔류
        left = {a["hash"] for a in db.load_queue()}
        self.assertNotIn("n1n1n1n1", left)
        self.assertNotIn("f2f2f2f2", left)

        # ── ② send: 카드 렌더링 + 키보드 ──
        self.assertEqual(db.cmd_send(), 0)
        self.assertEqual(len(fake_tg.sent_messages), 3)
        dom_msg = next(m for m in fake_tg.sent_messages if "국내 브리핑" in m["text"])
        self.assertIn("두코바니", dom_msg["text"])
        self.assertIn("💰 투자 관점", dom_msg["text"])   # confidence 2 → 표기
        self.assertIn("원자로 공급사 수혜", dom_msg["text"])
        kb = dom_msg["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(kb[0]), 4)  # 👍👎💰📌
        # 근거 약한 항목(마지막 idx=legacy)의 투자 줄은 생략됨
        forn_msg = next(m for m in fake_tg.sent_messages if "해외 브리핑" in m["text"])
        self.assertIn("확신 낮음", forn_msg["text"])  # confidence 1 헤지 표기
        rep_msg = next(m for m in fake_tg.sent_messages if "보고서 검토 추천" in m["text"])
        self.assertIn("두코바니 본계약의 전략적 함의", rep_msg["text"])

        # ── ③ confirm: delivery_log ──
        self.assertEqual(db.cmd_confirm(), 0)
        self.assertEqual(db.load_outbox()["status"], "sent")
        log_lines = db.DELIVERY_LOG_FILE.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log_lines), len(outbox["items"]))

        # 재실행 → 중복 발송 없음
        n = len(fake_tg.sent_messages)
        db.cmd_plan()
        db.cmd_send()
        self.assertEqual(len(fake_tg.sent_messages), n)

        # ── ④ 피드백 수신 (reuters=중요 ×5 → prior 형성) ──
        updates = [{"update_id": 1000 + i,
                    "callback_query": {"id": f"c{i}", "from": {"id": 900 + i},
                                       "data": "fb:f5f5f5f5:important"}}
                   for i in range(5)]
        events, _, _ = fi.extract_events(updates, set(), set(),
                                         datetime.now(timezone.utc).isoformat())
        self.assertEqual(len(events), 5)  # 유저가 달라 5건 모두 기록
        ranking.FEEDBACK_DIR.mkdir(exist_ok=True)
        with (ranking.FEEDBACK_DIR / "2026-07.jsonl").open("a", encoding="utf-8") as fp:
            for ev in events:
                fp.write(json.dumps(ev) + "\n")

        # ── ⑤ 피드백이 랭킹에 반영 (도메인 prior) ──
        priors = ranking.build_feedback_priors(
            ranking.load_feedback_events(), ranking.load_delivery_index(),
            ranking.load_config())
        self.assertGreater(priors["domain"].get("reuters.com", 0), 0)
        test_item = {"hash": "z1", "importance": "nice_to_know", "domain": "reuters.com",
                     "link": "https://reuters.com/z", "title": "t", "title_kr": "t",
                     "features": feat(), "queued_at": NOW.isoformat(),
                     "section": "international", "related_reports": []}
        s_with, b = ranking.score_item(test_item, ranking.load_config(), priors)
        self.assertIn("feedback_prior", b)

        # ── ⑥ weekly 집계 (curated 모의) ──
        curated = {a["hash"]: {**a, "cached_at": NOW.isoformat()}
                   for a in FIXTURE_QUEUE if a["importance"] != "noise"}
        items = weekly_bot.get_week_articles(curated)
        self.assertGreater(len(items), 0)
        agg = weekly_bot.build_aggregates(items)
        self.assertGreaterEqual(agg["report_candidates"].__len__(), 1)


if __name__ == "__main__":
    unittest.main()
