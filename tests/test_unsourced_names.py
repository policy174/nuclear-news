"""큐레이션이 원문과 다른 사람을 지목하는 사고 방지.

실측 재현(2026-08-10, namdonews 919437): 원문 제목은 `李 대통령 "해남 청정에너지,
반도체 클러스터 움직이는 힘"` 인데 큐레이션이 '윤석열 대통령'으로 풀어 썼다.
같은 착공식을 다룬 뉴시스·서울경제는 전부 '이재명'이라 사이트에서 한 이슈가
두 대통령을 말했다.

규칙은 '원문에 없는 이름'이 아니라 '원문과 성이 어긋나는 이름'이다. 전자로 짰다가
실측 889건 중 58건을 잘못 깎았다(신용시장→시장, 헝가리 총리→총리). 아래 오탐
테스트들이 그 58건에서 뽑은 것이다 — 규칙을 넓히려면 여기부터 통과시켜야 한다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

for _k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.setdefault(_k, "test-dummy")
import news_bot  # noqa: E402

strip = news_bot.strip_unsourced_person_names


# ---- 잡아야 하는 것 -------------------------------------------------------

def test_한자성과_어긋나는_실명은_직함만_남긴다():
    """실제로 났던 사고 그대로. 원문 李, 출력 윤석열."""
    src = '李 대통령 "해남 청정에너지, 반도체 클러스터 움직이는 힘"'
    got = strip("윤석열 대통령이 해남 태양광 착공식에서 밝혔다.", src)
    assert "윤석열" not in got, got
    assert got.startswith("대통령이"), got
    assert "해남" in got


def test_호환한자_이형태도_같은_글자로_본다():
    """실측의 진짜 함정. namdonews 제목의 '李' 는 U+674E 가 아니라 U+F9E1
    (CJK 호환 한자)였다. NFC 정규화를 안 붙이면 재현하려던 그 기사 하나만
    규칙을 조용히 통과한다."""
    src = '李 대통령 "해남 청정에너지, 반도체 클러스터 움직이는 힘"'
    assert src[0] != "李", "이 테스트는 호환 한자 이형태를 써야 의미가 있다"
    got = strip("윤석열 대통령이 착공식에서 밝혔다.", src)
    assert "윤석열" not in got, got


def test_붙여쓴_표기도_잡는다():
    """실측: `[이슈] 李대통령, '호남반도체' 직접 챙긴다`"""
    src = "[이슈] 李대통령, '호남반도체' 직접 챙긴다…반도체특별법 의결"
    got = strip("윤석열 대통령이 반도체특별법을 의결했다.", src)
    assert "윤석열" not in got, got


def test_출력이_성만_줄여_써도_어긋나면_잡는다():
    """같은 사고 기사의 title_kr 이 '윤 대통령, …' 이었다. 카드에서 제일 크게
    보이는 줄이라 요약만 고치면 반쪽이다."""
    src = '李 대통령 "해남 청정에너지, 반도체 클러스터 움직이는 힘"'
    got = strip("윤 대통령, 해남 태양광 착공식서 청정에너지와 첨단산업 연계 강조", src)
    assert "윤 대통령" not in got, got
    assert got.startswith("대통령,"), got


def test_출력_성이_원문과_같으면_그대로():
    src = '李 대통령 "해남 청정에너지"'
    before = "이 대통령, 해남 태양광 착공식 참석"
    assert strip(before, src) == before


def test_전_대통령은_성이_아니라_관형사():
    """'전 대통령'(전직)을 성 표식으로 보면 멀쩡한 이름이 깎인다."""
    src = "전 대통령 예우 논란…연금 지급 기준 재검토"
    before = "이재명 대통령이 관련 제도 개선을 지시했다."
    assert strip(before, src) == before


def test_한글성과_어긋나도_잡는다():
    src = "이 대통령, 해남 태양광 착공식 참석"
    got = strip("윤석열 대통령이 착공식에 참석했다.", src)
    assert "윤석열" not in got, got


def test_대통령_외_직함도_같은_규칙():
    src = "尹 장관, 전력수급기본계획 발표"
    got = strip("김정관 장관이 계획을 발표했다.", src)
    assert "김정관" not in got, got
    assert "장관" in got


def test_필드_전체가_같은_문을_지난다():
    """제목만 고치고 요지에 틀린 이름이 남으면 고친 게 아니다."""
    article = {"title": '李 대통령 "해남 청정에너지"', "description": "", "domain": "namdonews.com"}
    item = {
        "title_kr": "윤석열 대통령, 해남 태양광 착공식 참석",
        "summary": "윤석열 대통령이 착공식에서 청정에너지를 강조했다.",
        "detail": "윤석열 대통령은 전남 해남 착공식에서 재생에너지 구상을 발표했다.",
    }
    out = news_bot.normalize_curation_item(item, article)
    for field in ("title_kr", "summary", "detail"):
        assert "윤석열" not in out[field], f"{field}: {out[field]}"


# ---- 건드리면 안 되는 것 (전부 실측 오탐에서 온 것) ------------------------

def test_성이_같으면_풀어_쓴_것이므로_통과():
    """李 → 이재명은 확장이 맞다. 깎으면 멀쩡한 정보를 잃는다."""
    src = '李 대통령 "해남 청정에너지, 반도체 클러스터 움직이는 힘"'
    got = strip("이재명 대통령이 착공식에서 밝혔다.", src)
    assert "이재명 대통령" in got, got


def test_원문에_줄인_성이_없으면_대상이_아니다():
    """'헝가리 총리'의 '헝가리'를 이름으로 보면 국가명이 사라진다."""
    src = "Hungary PM says last turbine of Paks nuclear plant 'operating safely'"
    got = strip("헝가리 총리가 팍스 원전 터빈이 안전하게 가동 중이라고 발표했다.", src)
    assert got.startswith("헝가리 총리가"), got


def test_보통명사는_건드리지_않는다():
    src = "AI 투자, 채권·SPV로 확산…한국도 리스크 관리 시급"
    for before in (
        "빅테크가 자금 조달을 신용시장 중심으로 다변화했다.",
        "네이버가 내년 AI 인프라 시장에 진출한다.",
        "울진 이장연합회장이 지원 확대를 요구했다.",
    ):
        assert strip(before, src) == before, before


def test_인사기사처럼_같은_직함에_여러_성이면_포기한다():
    """누구와 대조해야 할지 모르는 상태에서 깎는 것은 또 다른 추측이다."""
    src = "尹 장관 사퇴…후임에 金 장관 유력"
    got = strip("이호현 장관이 후임으로 거론된다.", src)
    assert "이호현 장관" in got, got


def test_대통령실_같은_합성어에_걸리지_않는다():
    src = "靑은 '원전 반영' 기후부는 '아직 미정'"
    before = "원전 반영을 두고 대통령실과 환경부 간 의견이 갈렸다."
    assert strip(before, src) == before


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            news_bot.UNSOURCED_NAME_DROPS.clear()
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{'실패 ' + str(failed) + '건' if failed else '전부 통과'}")
    sys.exit(1 if failed else 0)
