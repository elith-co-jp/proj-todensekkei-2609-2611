"""Persistence and downloads for automatic structure predictions."""

from __future__ import annotations

import json
import unicodedata

from sqlalchemy.orm import Session

from database import DATA_DIR
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
        work_dir=DATA_DIR / "analysis_tmp",
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
