"""配線解析で共通に使う型（枠・線分・検出した配線とラベル）と、線の軸・交点の計算。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def padded(self, px: int, image_width: int, image_height: int) -> Box:
        return Box(
            max(0, self.x0 - px),
            max(0, self.y0 - px),
            min(image_width, self.x1 + px),
            min(image_height, self.y1 + px),
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def intersection_area(self, other: Box) -> int:
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        return max(0, x1 - x0) * max(0, y1 - y0)


@dataclass(frozen=True)
class LineSegment:
    orientation: str
    box: Box

    @property
    def length(self) -> int:
        return self.box.width if self.orientation == "h" else self.box.height


@dataclass(frozen=True)
class PageRegions:
    outer_frame: Box | None
    drawing_area: Box | None
    reference_table_area: Box | None
    title_block_area: Box | None


@dataclass(frozen=True)
class OcrResult:
    box: Box
    text: str
    confidence: float
    source: str


@dataclass(frozen=True)
class DetectedLabel:
    id: str
    raw_text: str
    normalized_text: str
    label_type: str
    confidence: float
    source: str
    box: Box
    accepted: bool
    reason: str


@dataclass(frozen=True)
class DetectedWire:
    id: str
    orientation: str
    box: Box
    axis: int
    span_start: int
    span_end: int
    length: int
    confidence: float
    visible_coverage: float
    reason: str
    nearby_label_ids: list[str]


@dataclass(frozen=True)
class TextRegion:
    id: str
    box: Box
    confidence: float
    reason: str


def line_axis(line: LineSegment) -> int:
    x, y = line.box.center()
    return round(y if line.orientation == "h" else x)


def line_intersection(h_line: LineSegment, v_line: LineSegment, tolerance: int = 18) -> tuple[int, int] | None:
    h = h_line.box
    v = v_line.box
    vx = round((v.x0 + v.x1) / 2)
    hy = round((h.y0 + h.y1) / 2)
    if h.x0 - tolerance <= vx <= h.x1 + tolerance and v.y0 - tolerance <= hy <= v.y1 + tolerance:
        return vx, hy
    return None
