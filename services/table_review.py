"""表の欄の読み取りを、元の表と同じ並びで見せて人が確かめ・直し・確定する（DD-0051）。

構造解析の結果（StructurePrediction の payload）には、OCR が読んだ文字（texts）と、文字だけが並ぶ欄の範囲
（regions の relay_table・title_block）がある。ここでは欄ごとに文字を行 × 列の格子に並べ（analysis/table_grid.py）、
人が直した文字と確認の状態を TableReview に残す。

確定したリレー表は、人の読みからリレー表の行を読み直して payload の relay_table に入れる（整合性チェックや
シート間のリレーの照合がそのまま人の読みを使う）。AI の読みは relay_table.ocr_rows に残し、確定を解除すると戻す。
解析し直したときも、確定したリレー表があれば保存の前に同じように入れ直す（save_structure）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.table_grid import build_grid, cell_box, expand_to_rules, items_in, ruling_lines
from models import AnnotationImage, StructurePrediction, TableReview, utc_now_naive
from services import lifecycles
from services.jst import iso_jst

# 確かめる表の種類（regions の region_type）と画面に出す名前。並びは画面のタブの順。
TABLE_KINDS: dict[str, str] = {"relay_table": "リレー表", "title_block": "表題欄"}
# これ未満の確からしさの読みは「要確認」として目立たせる
LOW_SCORE = 0.7


class NotFound(LookupError):
    pass


def _region_box(region: dict[str, Any]) -> tuple[float, float, float, float]:
    box = region["bbox"]
    if isinstance(box, dict):
        return (float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"]))
    return tuple(float(v) for v in box)  # type: ignore[return-value]


def table_areas(payload: dict[str, Any]) -> dict[str, tuple[float, float, float, float]]:
    """表の種類ごとの範囲。文字から決めた範囲（source=text）を優先し、無ければ検出した領域枠を使う。"""
    areas: dict[str, tuple[float, float, float, float]] = {}
    regions = payload.get("regions") or []
    for source in ("text", "detection"):
        for region in regions:
            kind = region.get("region_type")
            if kind in TABLE_KINDS and kind not in areas and region.get("source", "text") == source:
                areas[kind] = _region_box(region)
    return {kind: areas[kind] for kind in TABLE_KINDS if kind in areas}


def ocr_grids(payload: dict[str, Any], image: Any = None) -> dict[str, dict[str, Any]]:
    """表ごとの OCR の格子。解析で欄ごとに読み直した格子（payload の tables）があればそれを使う。

    無ければ（DD-0051 §6 より前の解析）ページの OCR の文字を並べる。図面の画像があれば罫線も使う。
    """
    saved = payload.get("tables") or {}
    texts = payload.get("texts") or []
    grids = {}
    for kind, area in table_areas(payload).items():
        if kind in saved:
            grids[kind] = saved[kind]
            continue
        inside = items_in(texts, area)
        heights = sorted(float(t["box"][3]) - float(t["box"][1]) for t in inside)
        rules = None
        if image is not None and heights:
            area = expand_to_rules(image, area, heights[len(heights) // 2])
            rules = ruling_lines(image, area, heights[len(heights) // 2])
        grids[kind] = build_grid(texts, area, rules)
    return grids


def _page_image(db: Session, project_id: int) -> Any:
    """図面の画像（PIL）。見つからなければ None（罫線を使わずに並べる）。"""
    from PIL import Image

    from services.annotation_service import image_path

    sha = db.execute(select(AnnotationImage.sha256).where(AnnotationImage.project_id == project_id)).scalars().first()
    path = image_path(sha) if sha else None
    if path is None or not path.is_file():
        return None
    with Image.open(path) as image:
        return image.convert("L")


def _values(raw: str | None) -> dict[tuple[int, int], str]:
    return {(int(v["row"]), int(v["col"])): str(v["text"]) for v in json.loads(raw or "[]")}


def _ocr_values(grid: dict[str, Any]) -> dict[tuple[int, int], str]:
    return {(c["row"], c["col"]): c["text"] for c in grid["cells"]}


def _same_grid(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("rows", "cols")
    return all(a.get(k) == b.get(k) for k in keys) and _ocr_values(a) == _ocr_values(b)


def _reviews(db: Session, project_id: int) -> dict[str, TableReview]:
    rows = db.execute(select(TableReview).where(TableReview.project_id == project_id)).scalars()
    return {row.table_key: row for row in rows}


def _payload(db: Session, project_id: int) -> dict[str, Any] | None:
    row = db.get(StructurePrediction, project_id)
    return json.loads(row.payload_json) if row is not None else None


def _table_view(kind: str, grid: dict[str, Any], review: TableReview | None, latest: dict[str, Any] | None) -> dict[str, Any]:
    ocr = _ocr_values(grid)
    values = _values(review.values_json) if review is not None else dict(ocr)
    changed = sum(1 for key in set(ocr) | set(values) if values.get(key, "") != ocr.get(key, ""))
    return {
        "key": kind,
        "label": TABLE_KINDS[kind],
        "status": review.status if review is not None else "unreviewed",
        "updated_at": iso_jst(review.updated_at) if review is not None else None,
        "confirmed_at": iso_jst(review.confirmed_at) if review is not None and review.confirmed_at else None,
        "grid": grid,
        "values": [{"row": r, "col": c, "text": t} for (r, c), t in sorted(values.items()) if t != ""],
        "cell_count": len(grid["cells"]),
        "low_score_count": sum(1 for c in grid["cells"] if c["score"] < LOW_SCORE),
        "changed_count": changed,
        # 確かめ始めた後に解析し直して、AI の読みが変わった（人が直した欄はそのまま残している）
        "ocr_changed": review is not None and latest is not None and not _same_grid(grid, latest),
    }


def list_tables(db: Session, project_id: int) -> dict[str, Any]:
    payload = _payload(db, project_id)
    if payload is None:
        return {"project_id": project_id, "analyzed": False, "image_size": None, "tables": [], "relay_rows": []}
    grids = ocr_grids(payload, _page_image(db, project_id))
    reviews = _reviews(db, project_id)
    tables = []
    for kind in TABLE_KINDS:
        review = reviews.get(kind)
        latest = grids.get(kind)
        grid = json.loads(review.grid_json) if review is not None else latest
        if grid is None:
            continue
        tables.append(_table_view(kind, grid, review, latest))
    relay = payload.get("relay_table") or {}
    return {
        "project_id": project_id,
        "analyzed": True,
        "image_size": payload.get("image_size"),
        "tables": tables,
        "relay_rows": relay.get("rows") or [],
        "relay_source": "confirmed" if relay.get("confirmed") else "ocr",
    }


def _clean_values(grid: dict[str, Any], values: list[dict[str, Any]]) -> dict[tuple[int, int], str]:
    rows, cols = len(grid["rows"]), len(grid["cols"])
    out: dict[tuple[int, int], str] = {}
    for value in values:
        row, col = int(value["row"]), int(value["col"])
        if not (0 <= row < rows and 0 <= col < cols):
            raise ValueError(f"表の外の欄です（行 {row + 1}・列 {col + 1}）")
        text = " ".join(str(value.get("text") or "").split())
        if text:
            out[(row, col)] = text
    return out


def save_table(db: Session, project_id: int, kind: str, values: list[dict[str, Any]], confirm: bool) -> dict[str, Any]:
    """人が直した欄の文字を保存する。confirm なら確定し、確定でなければ確認中にする（確定済みなら解除）。"""
    if kind not in TABLE_KINDS:
        raise NotFound(f"表の種類が違います: {kind}")
    payload = _payload(db, project_id)
    if payload is None:
        raise NotFound("この図面はまだ解析していません")
    review = _reviews(db, project_id).get(kind)
    if review is None:
        grid = ocr_grids(payload, _page_image(db, project_id)).get(kind)
        if grid is None:
            raise NotFound(f"この図面に{TABLE_KINDS[kind]}が見つかりません")
        review = TableReview(project_id=project_id, table_key=kind, status="draft", grid_json=json.dumps(grid, ensure_ascii=False))
        db.add(review)
    grid = json.loads(review.grid_json)
    cleaned = _clean_values(grid, values)
    review.values_json = json.dumps([{"row": r, "col": c, "text": t} for (r, c), t in sorted(cleaned.items())],
                                    ensure_ascii=False)
    machine = lifecycles.table_review(review)
    if machine.confirmed.is_active:
        machine.reopen()
        review.confirmed_at = None
    if confirm:
        machine.confirm()
        review.confirmed_at = utc_now_naive()
    db.flush()
    _store(db, project_id, apply_confirmed(payload, _reviews(db, project_id)))
    db.commit()
    return list_tables(db, project_id)


def reset_table(db: Session, project_id: int, kind: str) -> dict[str, Any]:
    """確かめた結果を消して、今の AI の読みから始め直す。"""
    review = _reviews(db, project_id).get(kind)
    if review is not None:
        db.delete(review)
        db.flush()
        payload = _payload(db, project_id)
        if payload is not None:
            _store(db, project_id, apply_confirmed(payload, _reviews(db, project_id)))
        db.commit()
    return list_tables(db, project_id)


def _store(db: Session, project_id: int, payload: dict[str, Any]) -> None:
    row = db.get(StructurePrediction, project_id)
    if row is not None:
        row.payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)


def confirmed_items(review: TableReview) -> list[dict[str, Any]]:
    """確定した欄の文字を、OCR の文字と同じ形（text・score・box）にする。欄の位置は元の文字の枠か、行・列の範囲。"""
    grid = json.loads(review.grid_json)
    boxes = {(c["row"], c["col"]): (c["box"], c.get("colspan", 1)) for c in grid["cells"]}
    items = []
    for (row, col), text in sorted(_values(review.values_json).items()):
        known = boxes.get((row, col))
        box = tuple(known[0]) if known else cell_box(grid, row, col)
        items.append({"text": text, "score": 1.0, "box": box})
    return items


def apply_confirmed(payload: dict[str, Any], reviews: dict[str, TableReview]) -> dict[str, Any]:
    """確定したリレー表を構造解析の結果に入れる。確定が無ければ AI の読み（ocr_rows）に戻す。"""
    relay = dict(payload.get("relay_table") or {"rows": [], "links": []})
    if "ocr_rows" in relay:
        relay["rows"], relay["links"] = relay.pop("ocr_rows"), relay.pop("ocr_links", [])
    relay.pop("confirmed", None)
    review = reviews.get("relay_table")
    if review is not None and review.status == "confirmed":
        from analysis.text_extraction import link_relay_table, parse_relay_table

        size = payload.get("image_size") or {}
        width, height = int(size.get("width") or 0), int(size.get("height") or 0)
        area = json.loads(review.grid_json)["bbox"]
        inside = {id(t) for t in items_in(payload.get("texts") or [], area)}
        items = [t for t in payload.get("texts") or [] if id(t) not in inside] + confirmed_items(review)
        rows = parse_relay_table(items, width, height) if width and height else []
        relay = {
            **relay,
            "ocr_rows": relay.get("rows", []),
            "ocr_links": relay.get("links", []),
            "rows": rows,
            "links": link_relay_table(rows, payload.get("symbols") or []),
            "confirmed": True,
        }
    return {**payload, "relay_table": relay}


def apply_saved_reviews(db: Session, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """解析し直した結果を保存する前に、確定したリレー表を入れ直す（save_structure から呼ぶ）。"""
    reviews = _reviews(db, project_id)
    return apply_confirmed(payload, reviews) if reviews else payload
