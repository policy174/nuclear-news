"""og:image(1200×630 PNG)를 stdlib 만으로 생성한다.

왜 스크립트인가: 링크 미리보기 이미지는 한 번 만들고 끝나는 바이너리라
"어떻게 만들었는지 모르는 파일"이 되기 쉽다. 브랜드 색·심벌 기하가 바뀌면
여기 상수만 고쳐 다시 돌린다.

    python web/tools/make_og_image.py

의존성 0 (zlib·struct). 심벌은 배포 중인 public/logo-mark.svg 와 같은 아치
마크다 — 화면과 공유 카드의 심벌이 달라지면 안 된다.

    python web/tools/make_og_image.py           # og-image.png
    python web/tools/make_og_image.py --icons   # + PWA·애플 아이콘 3종
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "og-image.png"

WIDTH, HEIGHT = 1200, 630
# style.css 의 --c-primary 와 같은 값으로 유지한다 — 공유 카드가 사이트와 따로 놀면
# 브랜드가 두 개로 보인다. 팔레트를 바꾸면 이 상수도 함께 바꾸고 재생성할 것.
# 2026-08-17 재도색(#12251e 딥포레스트 → #12294c 딥네이비)이 여기 반영되지
# 않아 공유 카드만 옛 색으로 나가고 있었다. 팔레트를 바꾸면 이 상수도 같이.
BG = (0x12, 0x29, 0x4C)
WHITE = (0xFF, 0xFF, 0xFF)
AMBER = (0xF0, 0xC2, 0x3C)

# 아치 마크 — public/logo-mark.svg 와 같은 기하(48 단위 기준을 비율로 옮긴다).
#   아치 반지름 13/48 · 획 5.6/48 · 다리 끝 42/48 · 노심 점 r3.8 at cy27
MARK_R = 116
MARK_CY = 248
MARK_STROKE = 26

# 워드마크 — 기하학적 스트로크 글리프. 폰트 의존을 없애려고 직접 그린다.
GLYPH_H = 74
GLYPH_W = 52
GLYPH_GAP = 26
GLYPH_STROKE = 9
WORDMARK_Y = 452

SS = 3  # 슈퍼샘플링 배율 — 곡선 계단현상 제거


def _blend(dst: list[int], index: int, color: tuple[int, int, int], alpha: float) -> None:
    if alpha <= 0:
        return
    alpha = min(1.0, alpha)
    for channel in range(3):
        base = dst[index + channel]
        dst[index + channel] = int(round(base + (color[channel] - base) * alpha))


class Canvas:
    """슈퍼샘플링 커버리지 버퍼. 도형마다 0~1 커버리지를 모아 한 번에 합성한다."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.pixels = [0] * (width * height * 3)
        for index in range(0, len(self.pixels), 3):
            self.pixels[index:index + 3] = list(BG)

    def fill(self, coverage: dict[tuple[int, int], float], color: tuple[int, int, int],
             alpha: float = 1.0) -> None:
        for (x, y), value in coverage.items():
            if 0 <= x < self.width and 0 <= y < self.height:
                _blend(self.pixels, (y * self.width + x) * 3, color, value * alpha)

    def write(self, path: Path) -> None:
        raw = bytearray()
        for y in range(self.height):
            raw.append(0)  # filter type 0
            start = y * self.width * 3
            raw.extend(self.pixels[start:start + self.width * 3])

        def chunk(tag: bytes, payload: bytes) -> bytes:
            body = tag + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(
                ">I", zlib.crc32(body) & 0xFFFFFFFF)

        header = struct.pack(">2I5B", self.width, self.height, 8, 2, 0, 0, 0)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def _coverage(test, bounds: tuple[int, int, int, int]) -> dict[tuple[int, int], float]:
    """서브픽셀 SS×SS 샘플로 도형 커버리지를 잰다."""
    left, top, right, bottom = bounds
    result: dict[tuple[int, int], float] = {}
    step = 1.0 / SS
    for y in range(top, bottom):
        for x in range(left, right):
            hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    if test(x + (sx + 0.5) * step, y + (sy + 0.5) * step):
                        hits += 1
            if hits:
                result[(x, y)] = hits / (SS * SS)
    return result


def disc_shape(cx: float, cy: float, radius: float):
    def inside(x: float, y: float) -> bool:
        return math.hypot(x - cx, y - cy) <= radius
    return inside


def segment_shape(x1: float, y1: float, x2: float, y2: float, width: float):
    """둥근 끝을 가진 선분 — 워드마크 스트로크."""
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    half = width / 2

    def inside(px: float, py: float) -> bool:
        if length_sq == 0:
            return math.hypot(px - x1, py - y1) <= half
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)) <= half
    return inside


def arc_segments(cx: float, cy: float, radius: float, start: float, end: float,
                 width: float, steps: int = 24) -> list:
    """호를 짧은 선분으로 샘플링한다.

    각도 구간을 직접 판정하면 0°/360° 경계에서 어긋나 글자가 깨진다(실측:
    C·S 가 낙서로 렌더됐다). 선분으로 그리면 경계 문제가 사라진다.
    각도는 화면 좌표 기준 — 0°=오른쪽, 90°=아래, 180°=왼쪽, 270°=위.
    """
    points = []
    for index in range(steps + 1):
        angle = math.radians(start + (end - start) * index / steps)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return [segment_shape(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1], width)
            for i in range(len(points) - 1)]


def glyph_shapes(letter: str, x: float, y: float, w: float, h: float) -> list:
    """대문자 기하 글리프. 브랜드 톤(정확·기하학적)에 맞춘 최소 획."""
    top, bottom = y, y + h
    left, right = x, x + w
    mid_y = y + h / 2
    radius = w / 2
    shapes = []
    if letter == "N":
        shapes += [segment_shape(left, bottom, left, top, GLYPH_STROKE),
                   segment_shape(left, top, right, bottom, GLYPH_STROKE),
                   segment_shape(right, bottom, right, top, GLYPH_STROKE)]
    elif letter == "U":
        shapes += [segment_shape(left, top, left, bottom - radius, GLYPH_STROKE),
                   segment_shape(right, top, right, bottom - radius, GLYPH_STROKE)]
        shapes += arc_segments(left + radius, bottom - radius, radius, 0, 180, GLYPH_STROKE)
    elif letter == "C":
        # 세로로 긴 글자라 위·아래 호 + 왼쪽 직선(오브라운드)으로 만든다
        shapes += [segment_shape(left, top + radius, left, bottom - radius, GLYPH_STROKE)]
        shapes += arc_segments(left + radius, top + radius, radius, 180, 325, GLYPH_STROKE)
        shapes += arc_segments(left + radius, bottom - radius, radius, 180, 35, GLYPH_STROKE)
    elif letter == "L":
        shapes += [segment_shape(left, top, left, bottom, GLYPH_STROKE),
                   segment_shape(left, bottom, right, bottom, GLYPH_STROKE)]
    elif letter == "E":
        shapes += [segment_shape(left, top, left, bottom, GLYPH_STROKE),
                   segment_shape(left, top, right, top, GLYPH_STROKE),
                   segment_shape(left, mid_y, right - w * 0.15, mid_y, GLYPH_STROKE),
                   segment_shape(left, bottom, right, bottom, GLYPH_STROKE)]
    elif letter == "S":
        # 위 보울(오른쪽→위→왼쪽) + 가운데 연결 + 아래 보울(오른쪽→아래→왼쪽)
        shapes += arc_segments(left + radius, top + radius, radius, 340, 180, GLYPH_STROKE)
        shapes += [segment_shape(left, top + radius, right, bottom - radius, GLYPH_STROKE)]
        shapes += arc_segments(left + radius, bottom - radius, radius, 0, 160, GLYPH_STROKE)
    return shapes


def draw_wordmark(canvas: Canvas, text: str) -> None:
    total = len(text) * GLYPH_W + (len(text) - 1) * GLYPH_GAP
    cursor = (WIDTH - total) / 2
    for letter in text:
        for shape in glyph_shapes(letter, cursor, WORDMARK_Y, GLYPH_W, GLYPH_H):
            bounds = (int(cursor - GLYPH_STROKE), int(WORDMARK_Y - GLYPH_STROKE),
                      int(cursor + GLYPH_W + GLYPH_STROKE),
                      int(WORDMARK_Y + GLYPH_H + GLYPH_STROKE))
            canvas.fill(_coverage(shape, bounds), WHITE)
        cursor += GLYPH_W + GLYPH_GAP


def arch_mark(canvas: Canvas, cx: float, cy: float, unit: float,
              ink: tuple[int, int, int] = WHITE, core: tuple[int, int, int] = AMBER) -> None:
    """아치 + 노심. unit 은 SVG 48 단위 1칸의 픽셀 길이다.

    SVG 와 같은 수를 쓴다 — 두 그림이 어긋나는 사고를 막으려면 기하를 두 번
    적지 말고 한 번 적고 배율만 바꿔야 한다.
    """
    r = 13 * unit
    stroke = 5.6 * unit
    top_y = cy - 2 * unit          # 아치 중심 (SVG 의 y=25, 박스 중심 24 기준)
    foot_y = cy + 18 * unit        # 다리 끝 (SVG 의 y=42)
    pad = stroke + 2
    bounds = (int(cx - r - pad), int(top_y - r - pad), int(cx + r + pad), int(foot_y + pad))
    shapes = [segment_shape(cx - r, top_y, cx - r, foot_y, stroke),
              segment_shape(cx + r, top_y, cx + r, foot_y, stroke)]
    shapes += arc_segments(cx, top_y, r, 180, 360, stroke, steps=40)
    for shape in shapes:
        canvas.fill(_coverage(shape, bounds), ink)
    canvas.fill(_coverage(disc_shape(cx, cy, 3.8 * unit), bounds), core)


def draw_mark(canvas: Canvas) -> None:
    arch_mark(canvas, WIDTH / 2, MARK_CY, MARK_R / 13)


def rounded_tile(size: int, radius: float):
    def inside(x: float, y: float) -> bool:
        cx = min(max(x, radius), size - radius)
        cy = min(max(y, radius), size - radius)
        return math.hypot(x - cx, y - cy) <= radius
    return inside


def write_icon(path: Path, size: int) -> None:
    """PWA·애플 아이콘. 마스크 대비 안전 영역(88%) 안에 마크를 앉힌다."""
    canvas = Canvas(size, size)
    unit = size / 64          # favicon.svg 와 같은 64 단위 기준
    # 애플 아이콘은 스스로 모서리를 깎으므로 판은 꽉 채운다. PWA maskable 도 같다.
    canvas.fill(_coverage(rounded_tile(size, 0.001), (0, 0, size, size)), BG)
    arch_mark(canvas, size / 2, size * 0.545, unit * 64 / 48 * 0.79)
    canvas.write(path)
    print(f"[icon] {path.name} ({path.stat().st_size:,} bytes, {size}x{size})")


def main() -> None:
    canvas = Canvas(WIDTH, HEIGHT)
    draw_mark(canvas)
    draw_wordmark(canvas, "NUCLENS")
    canvas.write(OUT)
    print(f"[og] {OUT.name} 생성 ({OUT.stat().st_size:,} bytes, {WIDTH}x{HEIGHT})")
    import sys
    if "--icons" in sys.argv:
        public = OUT.parent
        write_icon(public / "icon-192.png", 192)
        write_icon(public / "icon-512.png", 512)
        write_icon(public / "apple-touch-icon.png", 180)


if __name__ == "__main__":
    main()
