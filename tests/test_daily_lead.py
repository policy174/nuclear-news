"""daily_lead.py 단위 테스트 — 항상-쓰기 계약·재시도 사다리·절 경계 절단.

핵심 계약: generate()가 어떤 경로로 끝나든 daily_leads.json 은 존재해야 한다.
파일 부재는 daily-brief.yml 의 git add 를 죽이고 trend_insights 커밋까지
동반 사망시킨다 (2026-08-02 실사고).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import daily_lead
from gemini_client import GeminiError


LONG_LEAD = (
    "국내에서는 고리 2호기 계속운전 심사가 재개되었으며, 해외에서는 프랑스 EDF의 "
    "신규 원전 건설 계획과 미국 SMR 인허가 진전이 함께 진행되어 정책 환경 전반이 움직였습니다"
)


def delivery_row(idx=0, date="2026-08-02"):
    return {
        "date": date,
        "hash": f"hash{idx}",
        "title_kr": f"이슈 {idx}",
        "score": 10 - idx,
        "region": "국내" if idx % 2 == 0 else "해외",
    }


class DailyLeadTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._orig = (daily_lead.BASE, daily_lead.OUT_FILE, daily_lead.DELIVERY_LOG)
        daily_lead.BASE = base
        daily_lead.OUT_FILE = base / "daily_leads.json"
        daily_lead.DELIVERY_LOG = base / "delivery_log.jsonl"
        self.addCleanup(self._restore)
        self.calls = []
        self.responses = []
        self.available = True
        self.raises = False
        self._orig_gemini = (daily_lead.is_available, daily_lead.call_json)
        daily_lead.is_available = lambda: self.available
        daily_lead.call_json = self._fake_call

    def _restore(self):
        daily_lead.BASE, daily_lead.OUT_FILE, daily_lead.DELIVERY_LOG = self._orig
        daily_lead.is_available, daily_lead.call_json = self._orig_gemini

    def _fake_call(self, system_prompt, user_message, **kwargs):
        self.calls.append(user_message)
        if self.raises:
            raise GeminiError("429")
        return self.responses.pop(0) if self.responses else {"lead": ""}

    def write_log(self, rows):
        daily_lead.DELIVERY_LOG.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )

    def read_leads(self):
        return json.loads(daily_lead.OUT_FILE.read_text(encoding="utf-8"))["leads"]

    def seed_existing(self):
        daily_lead.OUT_FILE.write_text(
            json.dumps({"leads": {"2026-08-01": {"lead": "기존 문장.", "evidence": []}}},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    # ── 항상-쓰기 계약 ──────────────────────────────────────────────

    def test_no_api_key_still_writes_file(self):
        self.available = False
        self.assertFalse(daily_lead.generate())
        self.assertTrue(daily_lead.OUT_FILE.exists())
        self.assertEqual(self.read_leads(), {})

    def test_no_delivery_rows_still_writes_file(self):
        self.assertFalse(daily_lead.generate())
        self.assertTrue(daily_lead.OUT_FILE.exists())

    def test_gemini_error_preserves_existing_leads(self):
        self.seed_existing()
        self.write_log([delivery_row(0)])
        self.raises = True
        self.assertFalse(daily_lead.generate())
        self.assertEqual(self.read_leads()["2026-08-01"]["lead"], "기존 문장.")

    def test_empty_lead_preserves_existing_and_writes(self):
        self.seed_existing()
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": ""}]
        self.assertFalse(daily_lead.generate())
        leads = self.read_leads()
        self.assertIn("2026-08-01", leads)
        self.assertNotIn("2026-08-02", leads)

    # ── 성공 경로 ──────────────────────────────────────────────────

    def test_success_saves_lead_with_evidence(self):
        self.write_log([delivery_row(0), delivery_row(1)])
        self.responses = [{"lead": "국내와 해외 이슈가 함께 움직였습니다", "evidence_idx": [0, 1, 99]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertEqual(entry["lead"], "국내와 해외 이슈가 함께 움직였습니다.")
        self.assertEqual([e["hash"] for e in entry["evidence"]], ["hash0", "hash1"])
        self.assertNotIn("truncated", entry)

    def test_old_leads_pruned_after_keep_days(self):
        daily_lead.OUT_FILE.write_text(
            json.dumps({"leads": {"2020-01-01": {"lead": "옛 문장."}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": "오늘 문장", "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        self.assertNotIn("2020-01-01", self.read_leads())

    # ── 재시도 사다리 ──────────────────────────────────────────────

    def test_overlength_retries_once_and_uses_short(self):
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": LONG_LEAD, "evidence_idx": [0]},
                          {"lead": "짧게 압축된 문장", "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertEqual(entry["lead"], "짧게 압축된 문장.")
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("truncated", entry)

    def test_overlength_twice_falls_back_to_clause_cut(self):
        self.write_log([delivery_row(0)])
        self.responses = [{"lead": LONG_LEAD, "evidence_idx": [0]},
                          {"lead": LONG_LEAD, "evidence_idx": [0]}]
        self.assertTrue(daily_lead.generate())
        entry = self.read_leads()["2026-08-02"]
        self.assertTrue(entry.get("truncated"))
        self.assertLessEqual(len(entry["lead"]), daily_lead.LEAD_LIMIT + 1)
        self.assertTrue(entry["lead"])

    def test_clause_cut_prefers_boundary(self):
        cut = daily_lead._clause_cut(LONG_LEAD)
        self.assertLessEqual(len(cut), daily_lead.LEAD_LIMIT + 1)
        self.assertIn("계속운전 심사가 재개", cut)

    def test_clause_cut_without_boundary_uses_ellipsis(self):
        text = "가" * 120
        cut = daily_lead._clause_cut(text)
        self.assertTrue(cut.endswith("…"))
        self.assertLessEqual(len(cut), daily_lead.LEAD_LIMIT)


if __name__ == "__main__":
    unittest.main(verbosity=1)
