"""Persistence and downloads for automatic structure predictions."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from database import DATA_DIR
from models import AnnotationProject, MlModel, StructurePrediction
from services.annotation_service import image_path


def analyze_project(project: AnnotationProject, model: MlModel, detections: list[dict]) -> dict:
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
    )


def save_structure(db: Session, project_id: int, payload: dict) -> None:
    db.add(StructurePrediction(
        project_id=project_id, payload_json=json.dumps(payload, ensure_ascii=False, allow_nan=False)
    ))


def get_structure(db: Session, project_id: int) -> dict | None:
    row = db.get(StructurePrediction, project_id)
    return json.loads(row.payload_json) if row else None


def document_structure(pages: list[dict]) -> dict:
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
        },
        "pages": pages,
    }
