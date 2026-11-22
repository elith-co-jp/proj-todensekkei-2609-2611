"""図面セット（DD-0041）: 行先札でつながる一続きの図面を組にして、回路・波及範囲・追跡をセット単位で回す。

回路の API はもともと図面の ID の並びで動く（複数の図面を選べば行先札でつなぐ。DD-0030）。図面セットは
その並びに名前を付けて残すもので、加えて次を出す。

- 過不足（coverage）: セットの図面の行先札が指すシートのうち、セットに入っていないもの。登録済みの図面に
  そのシートがあれば追加の候補として示す。シート番号の分からない図面も出す（札をつなげないため）。
- 組の候補（suggest）: 登録済みの全図面を、行先札の向き先でつながるまとまりに分けたもの（保存はしない）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from models import AnnotationProject, DrawingSet, DrawingSetMember
from services import sheet_links
from services.annotation_service import load_pages
from services.jst import iso_jst


class NotFound(LookupError):
    pass


def _sheet_of(project: AnnotationProject) -> str | None:
    return sheet_links.page_sheet({"sheet_no": project.sheet_no, "name": project.name})


def _project_ids(db: Session, project_ids: list[int]) -> list[int]:
    ids = sorted(set(project_ids))
    found = {pid for (pid,) in db.query(AnnotationProject.id).filter(AnnotationProject.id.in_(ids)).all()}
    missing = [pid for pid in ids if pid not in found]
    if missing:
        raise NotFound(f"図面が見つかりません: {', '.join(map(str, missing))}")
    return ids


def _out(row: DrawingSet, sheets: dict[int, str | None] | None = None) -> dict[str, Any]:
    ids = [m.project_id for m in row.members]
    out = {"id": row.id, "name": row.name, "note": row.note, "project_ids": ids,
           "created_at": iso_jst(row.created_at), "updated_at": iso_jst(row.updated_at)}
    if sheets is not None:
        out["sheets"] = [sheets.get(pid) for pid in ids]
    return out


def _get(db: Session, set_id: int) -> DrawingSet:
    row = db.get(DrawingSet, set_id)
    if row is None:
        raise NotFound("図面セットが見つかりません")
    return row


def _check_name(db: Session, name: str, exclude: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("図面セットの名前を入れてください")
    q = db.query(DrawingSet.id).filter(DrawingSet.name == name)
    if exclude is not None:
        q = q.filter(DrawingSet.id != exclude)
    if q.first():
        raise ValueError(f"同じ名前の図面セットがあります: {name}")
    return name


def list_sets(db: Session) -> list[dict[str, Any]]:
    sheets = {p.id: _sheet_of(p) for p in db.query(AnnotationProject.id, AnnotationProject.name, AnnotationProject.sheet_no)}
    return [_out(r, sheets) for r in db.query(DrawingSet).order_by(DrawingSet.name).all()]


def get_set(db: Session, set_id: int) -> dict[str, Any]:
    return _out(_get(db, set_id))


def create_set(db: Session, name: str, project_ids: list[int], note: str | None = None) -> dict[str, Any]:
    row = DrawingSet(name=_check_name(db, name), note=note)
    row.members = [DrawingSetMember(project_id=pid) for pid in _project_ids(db, project_ids)]
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row)


def update_set(db: Session, set_id: int, *, name: str | None = None, note: str | None = None,
               project_ids: list[int] | None = None) -> dict[str, Any]:
    row = _get(db, set_id)
    if name is not None:
        row.name = _check_name(db, name, exclude=set_id)
    if note is not None:
        row.note = note or None
    if project_ids is not None:
        wanted = _project_ids(db, project_ids)
        row.members = [m for m in row.members if m.project_id in wanted] + [
            DrawingSetMember(project_id=pid) for pid in wanted if pid not in {m.project_id for m in row.members}]
    db.commit()
    db.refresh(row)
    return _out(row)


def delete_set(db: Session, set_id: int) -> None:
    db.delete(_get(db, set_id))
    db.commit()


def _pages(db: Session, project_ids: list[int] | None = None) -> list[dict[str, Any]]:
    if project_ids is None:
        project_ids = [pid for (pid,) in db.query(AnnotationProject.id)]
    return load_pages(db, project_ids)


def coverage(db: Session, set_id: int) -> dict[str, Any]:
    """セットの図面の行先札が指すシートのうち、セットに無いもの。登録済みの図面にあれば追加の候補を付ける。"""
    row = _get(db, set_id)
    ids = [m.project_id for m in row.members]
    pages = _pages(db, ids)
    own = {sheet_links.base_sheet(s) for p in pages if (s := sheet_links.page_sheet(p))}
    by_sheet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in db.query(AnnotationProject.id, AnnotationProject.name, AnnotationProject.sheet_no).all():
        if p.id not in ids and (s := _sheet_of(p)):
            by_sheet[sheet_links.base_sheet(s)].append({"project_id": p.id, "name": p.name, "sheet": s})

    missing: dict[str, dict[str, Any]] = {}
    for page in pages:
        for ref in sheet_links.page_references(page):
            base = sheet_links.base_sheet(ref["target"])
            if base in own:
                continue
            entry = missing.setdefault(base, {"sheet": base, "referenced_by": [], "candidates": by_sheet.get(base, [])})
            entry["referenced_by"].append({"project_id": page["id"], "name": page["name"], **ref})
    no_sheet = [{"project_id": p["id"], "name": p["name"]} for p in pages if not sheet_links.page_sheet(p)]
    return {"set": _out(row), "missing_sheets": sorted(missing.values(), key=lambda m: m["sheet"]),
            "pages_without_sheet": no_sheet,
            "complete": not missing and not no_sheet}


def suggest(db: Session) -> list[dict[str, Any]]:
    """登録済みの全図面を、行先札の向き先でつながるまとまりに分ける（1 枚だけのまとまりも返す）。"""
    pages = _pages(db)
    sheet_pages: dict[str, list[int]] = defaultdict(list)
    for p in pages:
        if s := sheet_links.page_sheet(p):
            sheet_pages[sheet_links.base_sheet(s)].append(p["id"])
    parent = {p["id"]: p["id"] for p in pages}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    unresolved: dict[int, set[str]] = defaultdict(set)
    for p in pages:
        for ref in sheet_links.page_references(p):
            base = sheet_links.base_sheet(ref["target"])
            targets = sheet_pages.get(base)
            if not targets:
                unresolved[p["id"]].add(base)
            for t in targets or []:
                parent[find(t)] = find(p["id"])
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for p in pages:
        groups[find(p["id"])].append(p)
    out = []
    for members in groups.values():
        sheets = sorted({s for p in members if (s := sheet_links.page_sheet(p))})
        out.append({"name": sheets[0] if sheets else members[0]["name"],
                    "project_ids": [p["id"] for p in members], "sheets": sheets,
                    "missing_sheets": sorted(set().union(*(unresolved[p["id"]] for p in members)))})
    return sorted(out, key=lambda g: (-len(g["project_ids"]), g["name"]))
