"""ベクター PDF から線・文字・黒丸を取り出し、電気的な接続の候補にする（DD-0035）。

元 CAD の部品・結線リストが無いとき、次に確かなのはベクター PDF の図形と文字。画像の検出・OCR を通さずに
線の座標と印字の文字列が得られる。ただし PDF の線は「描いた線」で、電気的な接続とは限らないので、次の規則で
候補にする（会議資料 05「位置と意味を分ける」）。

- 図枠・表の罫線: ページの端に沿った長い線、ページ幅（高さ）の 85% 以上の線は図枠として外す。
- 点線・破線（dashes のある線）は盤の枠・注釈線として、配線の候補から外して別に返す。
- 端点どうしが重なる・端点が別の線の途中に着く（T 字）なら接続。
- 線の途中どうしが交わる（十字）は、交点に黒丸があるときだけ接続。黒丸が無ければ単なる交差（非接続）。
- 文字は行ごとに、向き（横・縦）と外接枠を返す。縦書き・回転した文字も向きを持つ。

座標はアプリの画像と同じ（PDF を dpi で描いた画素。登録時に横書きの図面を回したなら、同じだけ回す）。
画像だけの PDF（スキャン）は kind = "scan" で、線・文字は空。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from services.pdf_service import PDF_DPI, _load_pymupdf

FRAME_RATIO = 0.85          # ページの幅・高さに対するこの割合以上の長さの線は図枠
EDGE_MARGIN = 0.04          # ページの端からこの割合以内に沿う線も図枠
SNAP_PX = 2.5               # 端点の一致・線への着きを同じ点とみなす距離（画素）
DOT_MAX_PX = 14.0           # 黒丸とみなす塗りつぶした図形の最大の大きさ（画素）
AXIS_TOL = 0.5              # 水平・垂直とみなす傾き（画素）
SCAN_IMAGE_RATIO = 0.6      # ページのこの割合以上を覆う画像があり、線が少なければスキャン


def _turn_point(x: float, y: float, w: float, h: float, turn: int) -> tuple[float, float]:
    """画像を反時計回りに turn 度回したときの点の行き先（page_orientation と同じ回し方）。"""
    if turn == 90:
        return y, w - x
    if turn == 270:
        return h - y, x
    if turn == 180:
        return w - x, h - y
    return x, y


class _Space:
    """PDF の座標 → 画像の画素（ページの回転・dpi・登録時の回転）。"""

    def __init__(self, page: Any, dpi: float, turn: int) -> None:
        self.matrix = page.rotation_matrix
        self.zoom = dpi / 72.0
        rect = page.rect  # 回転を反映した表示の大きさ
        self.w, self.h = rect.width * self.zoom, rect.height * self.zoom
        self.turn = turn

    def __call__(self, p: Any) -> tuple[float, float]:
        q = p * self.matrix
        return _turn_point(q.x * self.zoom, q.y * self.zoom, self.w, self.h, self.turn)

    @property
    def size(self) -> tuple[float, float]:
        return (self.h, self.w) if self.turn in (90, 270) else (self.w, self.h)


def _seg(a: tuple[float, float], b: tuple[float, float], width: float, dashed: bool) -> dict[str, Any] | None:
    (x1, y1), (x2, y2) = a, b
    if math.hypot(x2 - x1, y2 - y1) < 1.0:
        return None
    if abs(y1 - y2) <= AXIS_TOL:
        orient = "h"
        y1 = y2 = (y1 + y2) / 2
        if x1 > x2:
            x1, x2 = x2, x1
    elif abs(x1 - x2) <= AXIS_TOL:
        orient = "v"
        x1 = x2 = (x1 + x2) / 2
        if y1 > y2:
            y1, y2 = y2, y1
    else:
        orient = "d"
    return {"x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2),
            "orient": orient, "width": round(width, 2), "dashed": dashed}


def _merge_collinear(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ高さ（位置）で重なる・接する水平（垂直）の線を 1 本にする。CAD の PDF は 1 本の線を細かく分けて描くことがある。"""
    out = [s for s in segments if s["orient"] == "d"]
    for orient, fixed, lo, hi in (("h", "y1", "x1", "x2"), ("v", "x1", "y1", "y2")):
        group = sorted((s for s in segments if s["orient"] == orient), key=lambda s: (round(s[fixed]), s["dashed"], s[lo]))
        current: dict[str, Any] | None = None
        for s in group:
            if (current is not None and abs(current[fixed] - s[fixed]) <= 0.6 and current["dashed"] == s["dashed"]
                    and s[lo] <= current[hi] + 0.6):
                current[hi] = max(current[hi], s[hi])
                current["x2" if orient == "h" else "y2"] = current[hi]
                continue
            if current is not None:
                out.append(current)
            current = dict(s)
        if current is not None:
            out.append(current)
    return out


def _on_segment(px: float, py: float, s: dict[str, Any], tol: float) -> str | None:
    """点が線の端（"end"）か途中（"mid"）にあるか。"""
    for ex, ey in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
        if math.hypot(px - ex, py - ey) <= tol:
            return "end"
    x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return None
    u = ((px - x1) * dx + (py - y1) * dy) / length2
    if not 0 <= u <= 1:
        return None
    if math.hypot(px - (x1 + u * dx), py - (y1 + u * dy)) <= tol:
        return "mid"
    return None


def _intersection(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float] | None:
    if {a["orient"], b["orient"]} != {"h", "v"}:
        return None
    h, v = (a, b) if a["orient"] == "h" else (b, a)
    x, y = v["x1"], h["y1"]
    if h["x1"] - SNAP_PX <= x <= h["x2"] + SNAP_PX and v["y1"] - SNAP_PX <= y <= v["y2"] + SNAP_PX:
        return x, y
    return None


def connectivity(segments: list[dict[str, Any]], dots: list[dict[str, Any]], *, tol: float = SNAP_PX) -> dict[str, Any]:
    """配線の候補の線を、接続の規則でまとめる。nets: 線の番号の集まり、junctions: 接続点、crossings: 単なる交差。"""
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    junctions: list[dict[str, Any]] = []
    crossings: list[dict[str, Any]] = []
    for i, a in enumerate(segments):
        for j in range(i + 1, len(segments)):
            b = segments[j]
            if max(a["x1"], a["x2"]) + tol < min(b["x1"], b["x2"]) or max(b["x1"], b["x2"]) + tol < min(a["x1"], a["x2"]):
                continue
            if max(a["y1"], a["y2"]) + tol < min(b["y1"], b["y2"]) or max(b["y1"], b["y2"]) + tol < min(a["y1"], a["y2"]):
                continue
            how = None
            point = None
            for s, o in ((a, b), (b, a)):
                for px, py in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
                    where = _on_segment(px, py, o, tol)
                    if where:
                        how, point = ("end" if where == "end" else "tee"), (px, py)
                        break
                if how:
                    break
            if how is None:
                point = _intersection(a, b)
                if point is None:
                    continue
                dotted = any(math.hypot(point[0] - d["cx"], point[1] - d["cy"]) <= max(tol, d["r"] + 1) for d in dots)
                if not dotted:
                    crossings.append({"x": round(point[0], 1), "y": round(point[1], 1), "segments": [i, j]})
                    continue
                how = "dot"
            parent[find(i)] = find(j)
            junctions.append({"x": round(point[0], 1), "y": round(point[1], 1), "how": how, "segments": [i, j]})
    nets: dict[int, list[int]] = {}
    for i in range(len(segments)):
        nets.setdefault(find(i), []).append(i)
    return {"nets": sorted(nets.values(), key=lambda n: n[0]), "junctions": junctions, "crossings": crossings}


def _texts(page: Any, space: _Space) -> list[dict[str, Any]]:
    out = []
    point = type(page.rect.tl)  # PyMuPDF の Point
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            corners = [space(point(x, y)) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            dx, dy = line.get("dir", (1, 0))
            angle = round(math.degrees(math.atan2(dy, dx))) % 360
            if space.turn:
                angle = (angle - space.turn) % 360
            sizes = [span.get("size", 0) for span in line.get("spans", [])]
            out.append({"text": text, "box": [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)],
                        "angle": angle, "direction": "horizontal" if angle in (0, 180) else "vertical" if angle in (90, 270)
                        else "rotated", "size": round(max(sizes) * space.zoom, 1) if sizes else None, "source": "pdf_text"})
    return out


def extract_page(page: Any, *, dpi: float = PDF_DPI, turn: int = 0) -> dict[str, Any]:
    """1 ページの線・黒丸・文字と、接続の候補。"""
    space = _Space(page, dpi, turn)
    width, height = space.size
    segments: list[dict[str, Any]] = []
    dots: list[dict[str, Any]] = []
    for path in page.get_drawings():
        dashes = str(path.get("dashes") or "").strip()
        dashed = bool(dashes) and not dashes.startswith("[] ")
        stroke = float(path.get("width") or 0) * space.zoom
        filled = path.get("fill") is not None
        rect = path.get("rect")
        if filled and rect is not None:
            a, b = space(rect.tl), space(rect.br)
            w, h = abs(a[0] - b[0]), abs(a[1] - b[1])
            if w <= DOT_MAX_PX and h <= DOT_MAX_PX and w > 0 and h > 0 and 0.6 <= w / h <= 1.6:
                dots.append({"cx": round((a[0] + b[0]) / 2, 1), "cy": round((a[1] + b[1]) / 2, 1), "r": round(max(w, h) / 2, 1)})
                continue
        for item in path.get("items", []):
            op = item[0]
            if op == "l":
                points = [(item[1], item[2])]
            elif op == "re":
                r = item[1]
                points = [(r.tl, r.tr), (r.tr, r.br), (r.br, r.bl), (r.bl, r.tl)]
            elif op == "qu":
                q = item[1]
                points = [(q.ul, q.ur), (q.ur, q.lr), (q.lr, q.ll), (q.ll, q.ul)]
            else:
                continue  # 曲線（"c"）は配線にしない（記号の円弧など）
            for p, q in points:
                s = _seg(space(p), space(q), stroke, dashed)
                if s is not None:
                    segments.append(s)
    segments = _merge_collinear(segments)
    frame, dashed_lines, wires = [], [], []
    for s in segments:
        length = math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"])
        near_edge = (s["orient"] == "h" and min(s["y1"], height - s["y1"]) <= EDGE_MARGIN * height) or (
            s["orient"] == "v" and min(s["x1"], width - s["x1"]) <= EDGE_MARGIN * width)
        if (s["orient"] == "h" and length >= FRAME_RATIO * width) or (s["orient"] == "v" and length >= FRAME_RATIO * height) \
                or (near_edge and length >= 0.25 * (width if s["orient"] == "h" else height)):
            frame.append(s)
        elif s["dashed"]:
            dashed_lines.append(s)
        else:
            wires.append(s)
    texts = _texts(page, space)
    images = page.get_images(full=True)
    image_cover = 0.0
    for img in images:
        for r in page.get_image_rects(img[0]):
            image_cover = max(image_cover, (r.width * r.height) / max(page.rect.width * page.rect.height, 1))
    if image_cover >= SCAN_IMAGE_RATIO and len(wires) < 20 and len(texts) < 5:
        kind = "scan"
    elif image_cover >= SCAN_IMAGE_RATIO:
        kind = "mixed"
    else:
        kind = "vector" if wires or texts else "empty"
    graph = connectivity(wires, dots)
    return {"kind": kind, "width": round(width), "height": round(height), "dpi": dpi, "turn": turn,
            "wires": wires, "frame": frame, "dashed": dashed_lines, "dots": dots, "texts": texts, **graph,
            "stats": {"wires": len(wires), "frame": len(frame), "dashed": len(dashed_lines), "dots": len(dots),
                      "texts": len(texts), "nets": len(graph["nets"]), "junctions": len(graph["junctions"]),
                      "crossings": len(graph["crossings"]), "image_cover": round(image_cover, 3)}}


def extract(raw: bytes, *, dpi: float = PDF_DPI, turns: list[int] | None = None) -> list[dict[str, Any]]:
    """PDF の全ページ。turns: 登録時に各ページを回した角度（page_orientation）。"""
    fitz = _load_pymupdf()
    if fitz is None:
        raise RuntimeError("PyMuPDF が必要です")
    out = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            out.append(extract_page(page, dpi=dpi, turn=(turns or [])[i] if turns and i < len(turns) else 0))
    return out


def structure_texts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """構造解析の texts と同じ形（text・box）。母線の PER/NER の判定などに OCR の代わりに使える。"""
    return [{"text": t["text"], "box": t["box"], "source": "pdf_text", "angle": t["angle"]} for t in result.get("texts", [])]


# ------------------------------------------------------------------------------------------- 保存


def store_dir() -> Path:
    from database import DATA_DIR

    path = DATA_DIR / "vector_pdf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(project_id: int, result: dict[str, Any]) -> None:
    (store_dir() / f"{int(project_id)}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def load(project_id: int) -> dict[str, Any] | None:
    path = store_dir() / f"{int(project_id)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def remove(project_id: int) -> None:
    """図面を消したら一緒に消す（ID を使い回したとき別の図面の線を返さないため）。"""
    (store_dir() / f"{int(project_id)}.json").unlink(missing_ok=True)
