"""og:image(1200×630 PNG)를 stdlib 만으로 생성한다.

왜 스크립트인가: 링크 미리보기 이미지는 한 번 만들고 끝나는 바이너리라
"어떻게 만들었는지 모르는 파일"이 되기 쉽다. 브랜드 색·심벌 기하가 바뀌면
여기 상수만 고쳐 다시 돌린다.

    python web/tools/make_og_image.py

의존성 0 (zlib·struct). 심벌은 배포 중인 public/logo-mark.svg 와 같은 N 마크다.
브랜드 개편안의 Overlap Lens 는 2026-08-01 에 N 마크로 되돌린 결정(7bc99b2)이
있으므로 여기서도 쓰지 않는다 — 화면과 공유 카드의 심벌이 달라지면 안 된다.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "public" / "og-image.png"

WIDTH, HEIGHT = 1200, 630
BG = (0x0E, 0x2A, 0x3C)
WHITE = (0xFF, 0xFF, 0xFF)

# N 마크 — public/logo-mark.svg 와 같은 구성(원판 + 흰 N).
MARK_R = 116
MARK_CY = 248
MARK_STROKE = 26  # N 획 두께 (원 지름 대비 비율을 SVG 와 맞춤)

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


def draw_mark(canvas: Canvas) -> None:
    """원판 + 흰 N — public/logo-mark.svg 와 같은 심벌."""
    cx = WIDTH / 2
    bounds = (int(cx - MARK_R - 2), int(MARK_CY - MARK_R - 2),
              int(cx + MARK_R + 2), int(MARK_CY + MARK_R + 2))
    # 원판은 배경과 같은 Primary 라 보이지 않으므로 흰 테두리 대신 밝은 원판을
    # 쓰지 않고, SVG 처럼 원판 위에 N 만 얹는다(배경이 곧 원판 역할).
    canvas.fill(_coverage(disc_shape(cx, MARK_CY, MARK_R), bounds), WHITE, 0.08)

    half_w, half_h = MARK_R * 0.46, MARK_R * 0.52
    left, right = cx - half_w, cx + half_w
    top, bottom = MARK_CY - half_h, MARK_CY + half_h
    strokes = [
        segment_shape(left, bottom, left, top, MARK_STROKE),
        segment_shape(left, top, right, bottom, MARK_STROKE),
        segment_shape(right, bottom, right, top, MARK_STROKE),
    ]
    for stroke in strokes:
        canvas.fill(_coverage(stroke, bounds), WHITE)


def main() -> None:
    canvas = Canvas(WIDTH, HEIGHT)
    draw_mark(canvas)
    draw_wordmark(canvas, "NUCLENS")
    canvas.write(OUT)
    print(f"[og] {OUT.name} 생성 ({OUT.stat().st_size:,} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
