#!/usr/bin/env python3
"""Pretendard 를 지면이 실제로 쓰는 글자만 남겨 다시 만든다.

왜: 원본 `PretendardVariable.woff2` 는 2,057,688 바이트로 첫 로드 전송량의 77%다
(2026-08-10 실측 — 나머지 JSON·JS·CSS 를 전부 합쳐도 60만 바이트대). woff2 는 이미
압축돼 있어 엣지 gzip/br 이 더 줄여 주지도 않는다. 한글 음절 11,172자를 전부
싣고 있는 것이 원인이다.

무엇을 남기나: KS X 1001 한글 2,350자 + 호환 자모 + 라틴·기호 전부.
근거: 24일치 지면 텍스트(news.json·issues.json)의 서로 다른 한글 음절은 **896자**
이고 그 **전부**가 KS X 1001 안에 있다. 2,350자는 2.6배 여유다. 빠진 음절이 나오면
그 글자만 시스템 폰트로 떨어진다(치명적이지 않고, 지금도 한자는 그렇게 렌더된다 —
원본 Pretendard 에는 한자 글리프가 아예 없다).

쓰는 법 (Pretendard 를 올릴 때만 다시 돌린다):
    python web/tools/subset_font.py
산출물은 커밋한다 — CI 에 fontTools 를 넣지 않기 위해서다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "public" / "fonts" / "pretendard" / "v1.3.9"
SOURCE = FONT_DIR / "PretendardVariable.woff2"
TARGET = FONT_DIR / "PretendardVariable.subset.woff2"


def ksx1001_syllables() -> set[int]:
    """KS X 1001 완성형 한글 2,350자.

    `"똠".encode("euc_kr")` 로 판정하면 안 된다 — 파이썬 euc_kr 코덱은 완성형에
    없는 음절을 조합형 8바이트로 **성공적으로** 인코딩해서, 11,172자가 전부
    통과한다(실측: 1% 밖에 안 줄어 들통났다). 완성형 영역을 직접 디코드한다.
    """
    out: set[int] = set()
    for lead in range(0xB0, 0xC9):          # 완성형 한글 영역
        for trail in range(0xA1, 0xFF):
            try:
                char = bytes([lead, trail]).decode("euc_kr")
            except UnicodeDecodeError:
                continue
            if 0xAC00 <= ord(char) <= 0xD7A3:
                out.add(ord(char))
    return out


def wanted_codepoints(source: Path) -> set[int]:
    """원본이 가진 글자 중 남길 것. 한글만 KS X 1001 로 줄이고 나머지는 그대로."""
    allowed_hangul = ksx1001_syllables()
    font = TTFont(source)
    keep = {code for code in font.getBestCmap()
            if not (0xAC00 <= code <= 0xD7A3) or code in allowed_hangul}
    font.close()
    return keep


def main() -> int:
    if not SOURCE.exists():
        print(f"원본이 없다: {SOURCE}", file=sys.stderr)
        return 1

    keep = wanted_codepoints(SOURCE)
    options = subset.Options()
    options.flavor = "woff2"
    # 가변 폰트의 weight 축을 살려 둔다 — style.css 가 45~920 을 쓴다.
    options.retain_gids = False
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True

    font = subset.load_font(str(SOURCE), options)
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=keep)
    subsetter.subset(font)
    subset.save_font(font, str(TARGET), options)
    font.close()

    before, after = SOURCE.stat().st_size, TARGET.stat().st_size
    print(f"글자 {len(keep)}자 · {before:,} → {after:,} 바이트 "
          f"({(1 - after / before):.0%} 감소)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
