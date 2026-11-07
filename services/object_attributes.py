"""記号・配線の属性を、オブジェクト ID と 1 対 1 の JSON で書き出し・取り込む（DD-0010）。

オブジェクト ID は「図面の鍵/参照名」。図面の鍵はシート番号（無ければ P<図面 ID>、書き出す図面の間で重なれば
シート番号#図面 ID）。記号の参照名は SYM-0001、配線は W-0001 で、エディタで保存し直しても変わらない。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from models import AnnotationProject, AnnotationSymbol, Connection
from services.annotation_service import dump_attributes, load_attributes, serialize_project
from services.attribute_definitions import WIRE, all_definitions, symbol_definitions

SCHEMA = "todenyolo.object_attributes.v1"


def _page_keys(projects: list[AnnotationProject]) -> dict[int, str]:
    sheets = Counter((p.sheet_no or "").strip() for p in projects if (p.sheet_no or "").strip())
    keys = {}
    for p in projects:
        sheet = (p.sheet_no or "").strip()
        keys[p.id] = sheet if sheet and sheets[sheet] == 1 else (f"{sheet}#{p.id}" if sheet else f"P{p.id}")
    return keys


def _filled(defs: list, attributes: dict, template: bool) -> dict:
    if not template:
        return attributes
    return {**{d["key"]: None for d in defs}, **attributes}


def export_document(db: Session, project_ids: list[int], template: bool = False) -> dict[str, Any]:
    """全オブジェクトを 1 件ずつ並べた JSON。template なら定義済みの項目を null で並べる（入力用）。"""
    projects = db.query(AnnotationProject).filter(AnnotationProject.id.in_(project_ids)).order_by(AnnotationProject.id).all()
    if not projects:
        raise ValueError("図面を選んでください")
    keys = _page_keys(projects)
    objects = []
    for project in projects:
        page = serialize_project(db, project, with_image_meta=False)
        base = {"project_id": project.id, "sheet_no": project.sheet_no}
        for s in page["symbols"]:
            objects.append({
                "object_id": f"{keys[project.id]}/{s['ref']}", **base, "type": "symbol", "ref": s["ref"],
                "class_key": s["class_key"], "label": s["label"],
                "attributes": _filled(symbol_definitions(s["class_key"]), s["attributes"], template),
            })
        for c in page["connections"]:
            ends = [f"{c[f'{side}_symbol_ref']}" + (f"/{c[f'{side}_terminal_ref']}" if c[f"{side}_terminal_ref"] else "")
                    for side in ("from", "to")]
            objects.append({
                "object_id": f"{keys[project.id]}/{c['ref']}", **base, "type": "wire", "ref": c["ref"],
                "from": ends[0], "to": ends[1], "wire_no": c["wire_no"],
                "attributes": _filled(WIRE, c["attributes"], template),
            })
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "template": template,
        "attribute_definitions": all_definitions(),
        "projects": [{"project_id": p.id, "key": keys[p.id], "sheet_no": p.sheet_no, "name": p.name} for p in projects],
        "objects": objects,
    }


def _coerce(attributes: Any, defs: list) -> tuple[dict, list[str]]:
    if not isinstance(attributes, dict):
        return {}, ["attributes がオブジェクトではありません"]
    numeric = {d["key"] for d in defs if d["type"] == "number"}
    out, problems = {}, []
    for key, value in attributes.items():
        if value is not None and not isinstance(value, str | int | float | bool):
            problems.append(f"{key}: 値は文字・数値・真偽のどれかにしてください")
            continue
        if key in numeric and isinstance(value, str) and value.strip():
            try:
                value = float(value.replace(",", ""))
            except ValueError:
                problems.append(f"{key}: 数値ではありません（{value}）")
                continue
        if key in numeric and isinstance(value, bool):
            problems.append(f"{key}: 数値ではありません（{value}）")
            continue
        out[str(key)] = value
    return out, problems


def import_document(db: Session, document: Any) -> dict[str, Any]:
    """ファイルに載っているオブジェクトの属性を、その内容で置き換える。null の項目は未入力として消す。"""
    if not isinstance(document, dict) or document.get("schema") != SCHEMA or not isinstance(document.get("objects"), list):
        raise ValueError(f"属性の JSON（schema: {SCHEMA}）ではありません")
    by_id = {p.id: p for p in db.query(AnnotationProject).all()}
    by_sheet: dict[str, list[AnnotationProject]] = {}
    for p in by_id.values():
        by_sheet.setdefault((p.sheet_no or "").strip(), []).append(p)

    report: dict[str, Any] = {"updated": 0, "unchanged": 0, "not_found": [], "duplicates": [], "type_mismatch": [], "invalid": []}
    seen: Counter = Counter(o.get("object_id") for o in document["objects"] if isinstance(o, dict))
    report["duplicates"] = sorted(k for k, n in seen.items() if n > 1)
    for item in document["objects"]:
        if not isinstance(item, dict) or item.get("object_id") in report["duplicates"]:
            continue
        oid, kind, ref = item.get("object_id"), item.get("type"), item.get("ref")
        project = by_id.get(item.get("project_id"))
        sheet = (item.get("sheet_no") or "").strip()
        if project is None or (sheet and (project.sheet_no or "").strip() != sheet):
            candidates = by_sheet.get(sheet, []) if sheet else []
            project = candidates[0] if len(candidates) == 1 else None
        if project is None or kind not in ("symbol", "wire") or not ref:
            report["not_found"].append(oid)
            continue
        model = AnnotationSymbol if kind == "symbol" else Connection
        row = db.query(model).filter(model.project_id == project.id, model.ref == ref).one_or_none()
        if row is None:
            other = AnnotationSymbol if kind == "wire" else Connection
            hit = db.query(other).filter(other.project_id == project.id, other.ref == ref).first()
            (report["type_mismatch"] if hit else report["not_found"]).append(oid)
            continue
        defs = symbol_definitions(row.symbol_class.key) if kind == "symbol" else WIRE
        attributes, problems = _coerce(item.get("attributes"), defs)
        report["invalid"] += [f"{oid} {p}" for p in problems]
        raw = dump_attributes(attributes)
        if raw == row.attributes_json or (raw is None and not load_attributes(row.attributes_json)):
            report["unchanged"] += 1
            continue
        row.attributes_json = raw
        report["updated"] += 1
    db.commit()
    return report


def parse_document(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON として読めませんでした") from exc
