"""본문 없이 쓰인 해석은 화면에 내보내지 않는다.

실측 재현(2026-08-11, polinews 739662): 본문 수집에 실패한 기사인데
`해외건설 500억 달러 시대 겨냥…K건설, 중동 플랜트서 원전·전력 선회` 가
"한수원이 신규 원전 2기 후보지로 경북 영덕군, SMR 후보지로 부산 기장군을
선정했다"로 둔갑했고, why_important 까지 붙어 must_read 로 올라갔다.
'기장군'은 수집 코퍼스 전체에서 이 요약에만 나온다.

배경 수치: 큐레이션 900건 중 597건(66.3%)이 본문 없이 작성됐고, 그중
implication 408건 · why_important 63건이 채워져 있었다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot  # noqa: E402

ARTICLE = {
    "title": "해외건설 500억 달러 시대 겨냥…K건설, 중동 플랜트서 원전·전력 선회",
    "description": "",
    "domain": "polinews.co.kr",
}
ITEM = {
    "importance": "must_read",
    "summary": "한국수력원자력이 신규 대형 원전 2기 후보지로 경북 영덕군을 선정했다고 밝혔다.",
    "implication": "신규 원전 및 SMR 부지 후보지 선정은 향후 국내 원전 건설 사업의 중요한 초기 단계이다.",
    "why_important": "신규 원전 및 SMR 부지 후보지 선정은 국가 전력 수급 계획의 핵심적인 진전이다.",
}


def test_본문_없으면_해석_필드를_비운다():
    out = news_bot.normalize_curation_item(dict(ITEM), ARTICLE)   # body 미전달 = 수집 실패
    assert out["implication"] == "", out["implication"]
    assert out["why_important"] == "", out["why_important"]


def test_본문이_있으면_해석을_그대로_둔다():
    body = "한국수력원자력은 신규 대형 원전 2기 후보지를 검토 중이라고 밝혔다."
    out = news_bot.normalize_curation_item(dict(ITEM), ARTICLE, body)
    assert out["implication"], "본문이 있으면 해석은 살아 있어야 한다"
    assert out["why_important"]


def test_등급은_건드리지_않는다():
    """`Oklo 임계 달성` 처럼 제목 자체가 사실인 must_read 가 있다. 본문이 없다고
    일괄 강등하면 진짜 신호까지 죽는다 — 지우는 것은 덧붙인 해석뿐이다."""
    out = news_bot.normalize_curation_item(dict(ITEM), ARTICLE)
    assert out["importance"] == "must_read"


def test_지운_것을_조용히_넘기지_않는다():
    news_bot.NO_BODY_INTERPRETATION_DROPS.clear()
    news_bot.normalize_curation_item(dict(ITEM), ARTICLE)
    assert len(news_bot.NO_BODY_INTERPRETATION_DROPS) == 2, news_bot.NO_BODY_INTERPRETATION_DROPS


def test_공백뿐인_본문은_본문이_아니다():
    out = news_bot.normalize_curation_item(dict(ITEM), ARTICLE, "   \n  ")
    assert out["implication"] == ""


def test_해석이_원래_비어있으면_기록하지_않는다():
    news_bot.NO_BODY_INTERPRETATION_DROPS.clear()
    item = dict(ITEM, implication="", why_important="")
    news_bot.normalize_curation_item(item, ARTICLE)
    assert news_bot.NO_BODY_INTERPRETATION_DROPS == []


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            news_bot.NO_BODY_INTERPRETATION_DROPS.clear()
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'실패 ' + str(failed) + '건' if failed else '전부 통과'}")
    sys.exit(1 if failed else 0)
