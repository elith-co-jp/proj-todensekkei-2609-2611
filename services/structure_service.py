"""Persistence and downloads for automatic structure predictions."""

from __future__ import annotations

import json
import unicodedata

from sqlalchemy.orm import Session

from analysis.relay_links import link_document_relay_rows
from analysis.text_extraction import same_relay_device
from models import AnnotationProject, MlModel, StructurePrediction
from services.annotation_service import image_path


def analyze_project(
    project: AnnotationProject, model: MlModel, detections: list[dict], regions: list[dict] | None = None
) -> dict:
    from analysis.runtime import analyze_detections

    if len(project.images) != 1:
        raise RuntimeError("構造解析は1図面につき1画像で実行してください。")
    image = project.images[0]
    return analyze_detections(
        image_path(image.sha256), detections,
        source={
            "type": "registered_image", "project_id": project.id,
            "name": project.name, "image_sha256": image.sha256,
            "model_sha256": model.sha256, "model_name": model.name,
        },
        regions=regions,
    )


def save_structure(db: Session, project_id: int, payload: dict) -> None:
    db.add(StructurePrediction(
        project_id=project_id, payload_json=json.dumps(payload, ensure_ascii=False, allow_nan=False)
    ))


def get_structure(db: Session, project_id: int) -> dict | None:
    row = db.get(StructurePrediction, project_id)
    return json.loads(row.payload_json) if row else None


def document_structure(pages: list[dict]) -> dict:
    references = resolve_sheet_references(pages)
    relays, linked_relays = link_cross_sheet_relays(pages)
    rows, linked_rows = link_document_relay_rows(
        [(page_sheet_number(p), (p.get("source") or {}).get("project_id"), p) for p in pages])
    for page in pages:
        symbols = {symbol["id"]: symbol for symbol in page.get("symbols", [])}
        for connection in (page.get("from_to") or {}).get("connections", []):
            for side in ("from", "to"):
                endpoint = connection.get(side) or {}
                symbol = symbols.get(endpoint.get("symbol_id"))
                if symbol is None:
                    continue
                terminal = next((t for t in symbol.get("terminals", []) if t.get("ref") == endpoint.get("terminal_ref")), None)
                if terminal is not None:
                    endpoint["terminal_name"] = terminal.get("name") or ""
                if "class_name" in endpoint:
                    endpoint["class_name"] = symbol["class_name"]
    return {
        "schema_version": "todensekkei.document_structure.v1",
        "status": "partial" if any(p.get("status") == "partial" for p in pages) else "completed",
        "result_type": "prediction",
        "coordinate_unit": "rendered_pixel",
        "summary": {
            "page_count": len(pages),
            "symbol_count": sum(len(p.get("symbols", [])) for p in pages),
            "wire_count": sum(len(p.get("wires", [])) for p in pages),
            "from_to_connection_count": sum(len(p.get("from_to", {}).get("connections", [])) for p in pages),
            "sheet_reference_count": references,
            "resolved_sheet_reference_count": sum(
                1 for p in pages for r in p.get("sheet_references", []) if r["target_project_id"] is not None
            ),
            "cross_sheet_relay_count": relays,
            "linked_cross_sheet_relay_count": linked_relays,
            "cross_sheet_relay_row_count": rows,
            "linked_cross_sheet_relay_row_count": linked_rows,
        },
        "pages": pages,
    }


def _sheet_key(text: str | None) -> str:
    return unicodedata.normalize("NFKC", text or "").upper().replace(" ", "")


def page_sheet_number(page: dict) -> str:
    """人が確定したシート番号。無ければ自動読取の 1 位。"""
    candidates = page.get("sheet_number_candidates") or []
    return _sheet_key(page.get("sheet_no") or (candidates[0]["text"] if candidates else ""))


def resolve_sheet_references(pages: list[dict]) -> int:
    """行先・来先のシート番号を、出力する図面のシート番号と照合し、参照先の図面 ID を書き込む。

    枝番つき（ER21212-1）は枝番なしの図面にも合わせる。見つからない参照先は None のまま残す。
    戻り値は参照の件数。
    """
    index: dict[str, int | None] = {}
    for page in pages:
        sheet = page_sheet_number(page)
        if sheet:
            index.setdefault(sheet, (page.get("source") or {}).get("project_id"))
    count = 0
    for page in pages:
        for ref in page.get("sheet_references") or []:
            target = _sheet_key(ref.get("target_sheet"))
            ref["target_project_id"] = index.get(target, index.get(target.split("-")[0]))
            count += 1
    return count


def link_cross_sheet_relays(pages: list[dict]) -> tuple[int, int]:
    """接点・コイルのラベルにあるシート番号（コイルのあるシート）の図面で、同じ器具番号のコイルとリレー表の行を探す。

    参照先の図面も書き出しているとき、記号に relay_link（参照先の図面 ID、コイルの記号 ID、リレー表の行番号）を付ける。
    戻り値は（別シートを参照する接点・コイルの件数, コイルか行が見つかった件数）。
    """
    by_project = {(page.get("source") or {}).get("project_id"): page for page in pages}
    total = linked = 0
    for page in pages:
        symbols = {symbol.get("id"): symbol for symbol in page.get("symbols", [])}
        for ref in page.get("sheet_references") or []:
            symbol = symbols.get(ref.get("symbol_id")) if ref.get("kind") == "relay" else None
            if symbol is None:
                continue
            total += 1
            target = by_project.get(ref["target_project_id"]) if ref["target_project_id"] is not None else None
            if target is None:
                continue
            label = symbol.get("label") or ""
            coils = [s["id"] for s in target.get("symbols", [])
                     if s.get("class_name") == "relay_coil" and same_relay_device(s.get("label") or "", label)]
            rows = [i for i, row in enumerate((target.get("relay_table") or {}).get("rows", []))
                    if same_relay_device(label, row["device"])]
            symbol["relay_link"] = {"project_id": ref["target_project_id"], "coil_symbol_ids": coils, "relay_table_rows": rows}
            linked += bool(coils or rows)
    return total, linked
