"""official_events_fetch 계약 — 파서는 실측 HTML 스니펫으로 오프라인 검증.

스니펫은 2026-09-06 실캡처에서 잘라 왔다. 사이트가 개편되면 파서가 죽는데,
그때 last_checked 의 parsed=0 신호와 여기 스니펫이 함께 진단 재료가 된다.
"""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import official_events_fetch as oef

TODAY = date(2026, 9, 6)

# AMPOS seminarScheduleListInner.do 응답의 행 하나 (실캡처 축약)
AMPOS_ROW = """
<tr>
    <td>
            2026년 09월 29일 (화) 10:00
    </td>
    <td class="text-left">
        <div class="dayForm defaultLi mtb30">
            <div class="right">
                <p style = "white-space: normal; line-height: 1.2;">에너지 산업국가전략 국회 연속토론회: 4차- 해상 SMR</p>
                <ul class="liStyle">
                    <li><span>장소</span>의원회관 제2간담회의실(202호)&nbsp;</li>
                    <li style="white-space: normal;"><span>주최</span>박정 의원실&nbsp;</li>
                    <li><span>문의</span>(02) 0000-0000&nbsp;</li>
                </ul>
            </div>
        </div>
    </td>
    <td></td>
</tr>
<tr>
    <td>2026년 09월 06일 (일) 14:00</td>
    <td class="text-left"><div class="dayForm defaultLi mtb30"><div class="right">
        <p style = "white-space: normal;">경찰개혁의 길을 묻다</p>
        <ul class="liStyle"><li><span>장소</span>의원회관 제2소회의실&nbsp;</li>
        <li><span>주최</span>정춘생 의원실&nbsp;</li></ul>
    </div></div></td>
    <td></td>
</tr>
"""

# KAIF ax.204.php 응답의 행 (실캡처 축약) — 웹사이트 링크는 있을 수도 없을 수도
KAIF_CAL_ROWS = """
<tr>
    <td class="col-num">575</td>
    <td class="">세미나</td>
    <td class="">2026 ANS Winter Conference &amp; Expo</td>
    <td class="no640">2026.11.15 ~ 2026.11.18</td>
    <td class="no768">Phoenix, AZ</td>
    <td class="no640"><a href="https://www.ans.org/meetings/view-wc2026/" target='_blank'><span class="material-icons ">home</span></a></td>
</tr>
<tr>
    <td class="col-num">574</td>
    <td class="">세미나</td>
    <td class="">원자력 CEO 추계포럼</td>
    <td class="no640">2026.11.13 ~ 2026.11.14</td>
    <td class="no768">신라호텔 제주</td>
    <td class="no640"></td>
</tr>
"""

KAIF_NOTICE_HTML = """
<td class="col-tit">
    <a href="?c=193&s=&gp=1&gbn=view&ix=30034">2026년 3차 중소기업 품질시스템 구축 지원사업 시행공고 (~ 9. 8. 15:00)</a>
</td>
<td class="col-tit">
    <a href="?c=193&s=&gp=1&gbn=view&ix=30028">2026 원전해체 비즈니스 포럼 참가자 모집 (~ 9. 10.)</a>
</td>
<td class="col-tit">
    <a href="?c=193&s=&gp=1&gbn=view&ix=30030">마감 표기가 없는 일반 공지</a>
</td>
"""


class AssemblyTests(unittest.TestCase):
    def test_parse_assembly_row_fields(self):
        rows = oef.parse_assembly(AMPOS_ROW)
        self.assertEqual(len(rows), 2)
        row = rows[0]
        self.assertEqual(row["date"], "2026-09-29")
        self.assertEqual(row["time"], "10:00")
        self.assertEqual(row["host"], "박정 의원실")
        self.assertEqual(row["organizer"], "박정 의원실")
        self.assertIn("의원회관", row["place"])
        self.assertEqual(row["origin"], "official")
        self.assertEqual(row["publisher"], "국회")
        self.assertEqual(row["url"], oef.AMPOS_LIST_URL)

    def test_assembly_keyword_gate_drops_unrelated(self):
        # fetch 쪽 게이트 규칙을 파서 결과에 그대로 적용해 검증한다.
        rows = oef.parse_assembly(AMPOS_ROW)
        gated = [r for r in rows
                 if any(k in r["label"] for k in oef.ASSEMBLY_KEYWORDS)]
        self.assertEqual(len(gated), 1)
        self.assertIn("SMR", gated[0]["label"])


class KaifTests(unittest.TestCase):
    def test_calendar_range_and_external_url(self):
        rows = oef.parse_kaif_calendar(KAIF_CAL_ROWS)
        self.assertEqual(len(rows), 2)
        ans = rows[0]
        self.assertEqual(ans["kind"], "range")
        self.assertEqual(ans["date"], "2026-11-15")
        self.assertEqual(ans["end_date"], "2026-11-18")
        # 외부 행사 홈페이지가 있으면 그것이 url — 협회 목록 페이지가 아니라.
        self.assertEqual(ans["url"], "https://www.ans.org/meetings/view-wc2026/")
        self.assertEqual(ans["publisher"], "한국원자력산업협회")
        ceo = rows[1]
        self.assertEqual(ceo["url"], oef.KAIF_CAL_PAGE)
        self.assertEqual(ceo["place"], "신라호텔 제주")

    def test_notice_deadline_from_title(self):
        rows = oef.parse_kaif_notice(KAIF_NOTICE_HTML, TODAY)
        # 마감 표기 없는 일반 공지는 일정이 아니다.
        self.assertEqual(len(rows), 2)
        forum = next(r for r in rows if "포럼" in r["label"])
        self.assertEqual(forum["kind"], "deadline")
        self.assertEqual(forum["date"], "2026-09-10")
        self.assertEqual(forum["date"], forum["end_date"])
        self.assertNotIn("(~", forum["label"])
        self.assertIn("ix=30028", forum["url"])
        # 시각 붙은 마감("~ 9. 8. 15:00")도 날짜만 읽는다.
        quality = next(r for r in rows if "품질시스템" in r["label"])
        self.assertEqual(quality["date"], "2026-09-08")


class KnsTests(unittest.TestCase):
    def test_title_full_meta(self):
        row = oef.parse_kns_notice(
            "AI 시대 국가경쟁력을 위한 전원믹스와 시장제도 심포지움 개최(9.9(수) 14:00, 대한상공회의소)",
            "103327", TODAY)
        self.assertEqual(row["date"], "2026-09-09")
        self.assertEqual(row["time"], "14:00")
        self.assertEqual(row["place"], "대한상공회의소")
        self.assertNotIn("(9.9", row["label"])
        self.assertIn("심포지움 개최", row["label"])
        self.assertIn("9.9", row["notice_title"])
        self.assertEqual(row["url"], "https://www.kns.org/boards/view/notice/103327")

    def test_title_partial_meta(self):
        # 시간 없음
        row = oef.parse_kns_notice("워크숍 개최(10.2(금))", "1", TODAY)
        self.assertEqual(row["date"], "2026-10-02")
        self.assertEqual(row["time"], "")
        self.assertEqual(row["place"], "")
        # 날짜 괄호가 아예 없으면 일정 아님
        self.assertIsNone(oef.parse_kns_notice("수석부회장 선출 결과", "2", TODAY))

    def test_year_rollover_near_december(self):
        row = oef.parse_kns_notice("신년 하례회 개최(1.10(월) 10:00)", "3",
                                   date(2026, 12, 20))
        self.assertEqual(row["date"], "2027-01-10")


class StoreTests(unittest.TestCase):
    def test_hash_prefix_and_stability(self):
        a = oef._hash("kns_notice", "103327")
        b = oef._hash("kns_notice", "103327")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^of-[0-9a-f]{12}$")

    def test_prune_grace_window(self):
        items = [
            {"hash": "of-1", "date": "2026-08-20", "end_date": "2026-08-20"},
            {"hash": "of-2", "date": "2026-08-31", "end_date": "2026-08-31"},
            {"hash": "of-3", "date": "2026-09-10", "end_date": "2026-09-10"},
        ]
        kept = oef.prune(items, today=TODAY)
        self.assertEqual([i["hash"] for i in kept], ["of-2", "of-3"])

    def test_source_failure_keeps_previous_items(self):
        store = {"items": [{"hash": "of-x", "date": "2026-09-10",
                            "end_date": "2026-09-10"}],
                 "state": {}, "last_checked": {}}
        def boom(state):
            raise RuntimeError("사이트 개편")
        with mock.patch.object(oef, "load_store", return_value=store), \
             mock.patch.object(oef, "save_store") as saved:
            oef.run(sources=[{"id": "kns_notice", "fetch": boom}])
        result = saved.call_args[0][0]
        self.assertEqual(len(result["items"]), 1)
        self.assertFalse(result["last_checked"]["kns_notice"]["ok"])
        self.assertIn("RuntimeError", result["last_checked"]["kns_notice"]["error"])

    def test_once_per_day_skips(self):
        today = oef.datetime.now(oef.KST).strftime("%Y-%m-%d")
        store = {"items": [], "state": {},
                 "last_checked": {"kns_notice": {"at": f"{today}T07:00:00+09:00",
                                                 "ok": True}}}
        with mock.patch.object(oef, "load_store", return_value=store), \
             mock.patch.object(oef, "save_store") as saved:
            ran = oef.run(sources=[], once_per_day=True)
        self.assertFalse(ran)
        saved.assert_not_called()

    def test_enrichment_backfills_missing_facts(self):
        row = oef.parse_kns_notice("포럼 개최(9.20(일) 10:00, 코엑스)", "9", TODAY)
        bare = dict(row, time="", place="")
        store = {"items": [bare], "state": {}, "last_checked": {}}
        with mock.patch.object(oef, "load_store", return_value=store), \
             mock.patch.object(oef, "save_store") as saved:
            oef.run(sources=[{"id": "kns_notice", "fetch": lambda s: [row]}])
        item = saved.call_args[0][0]["items"][0]
        self.assertEqual(item["time"], "10:00")
        self.assertEqual(item["place"], "코엑스")


if __name__ == "__main__":
    unittest.main()
