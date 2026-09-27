"""アノテーションの永続化ロジック。

シンボル・端子・配線（from-to）は「一括置換」で保存する（`PUT /api/annotations/{id}`
に送られた内容で全置換するモデル）。差分更新をしないことで実装を単純に保つ代わりに、
同時編集は後勝ちになる点に注意。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from database import IMAGE_DIR
from models import (
    AnnotationProject,
    AnnotationSymbol,
    Connection,
    SymbolClass,
    SymbolTerminal,
)
from services.blob_store import store_blob
from services.geometry import sanitize_box

MAX_IMAGE_BYTES = 64 * 1024 * 1024


# --------------------------------------------------------------------------
# 画像
# --------------------------------------------------------------------------
def store_image_bytes(raw: bytes) -> tuple[str, int, int]:
    """画像を PNG に正規化して data/images/<sha256>.png へ保存し、(sha256, w, h) を返す。"""
    if not raw:
        raise ValueError("画像が空です")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("画像サイズが上限を超えています")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ValueError("画像を読み込めませんでした") from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    digest, _ = store_blob(IMAGE_DIR, buf.getvalue(), ".png")
    return digest, img.width, img.height


def image_path(sha256: str) -> Path:
    return IMAGE_DIR / f"{sha256}.png"


# --------------------------------------------------------------------------
# クラスマスタ
# --------------------------------------------------------------------------
def classes_ordered(db: Session) -> list[SymbolClass]:
    return db.query(SymbolClass).order_by(SymbolClass.yolo_index.asc()).all()


def class_map_by_key(db: Session) -> dict[str, SymbolClass]:
    return {c.key: c for c in classes_ordered(db)}


def ensure_class(db: Session, key: str, label: str | None = None, color: str = "#84b0d8") -> SymbolClass:
    """未知クラスをインポート時に自動登録する（yolo_index は末尾に採番）。"""
    found = db.query(SymbolClass).filter(SymbolClass.key == key).one_or_none()
    if found:
        return found
    next_index = (db.query(SymbolClass).count()) or 0
    while db.query(SymbolClass).filter(SymbolClass.yolo_index == next_index).count():
        next_index += 1
    created = SymbolClass(
        key=key, label=label or key, yolo_index=next_index, color=color, sort_order=next_index
    )
    db.add(created)
    db.flush()
    return created


# --------------------------------------------------------------------------
# アノテーションの一括置換
# --------------------------------------------------------------------------
def replace_annotations(db: Session, project: AnnotationProject, payload) -> dict:
    """シンボル・端子・配線をまとめて置き換える。

    payload.symbols[].ref / payload.connections[].*_ref は「クライアント側の参照名」。
    保存時に DB の実 id へ解決する。ref が未指定なら SYM-0001 形式で採番する。
    """
    cls_by_key = class_map_by_key(db)

    db.query(Connection).filter(Connection.project_id == project.id).delete(synchronize_session=False)
    old_symbols = db.query(AnnotationSymbol).filter(AnnotationSymbol.project_id == project.id).all()
    for s in old_symbols:
        db.delete(s)
    db.flush()

    symbol_by_ref: dict[str, AnnotationSymbol] = {}
    terminal_by_ref: dict[str, SymbolTerminal] = {}
    used_refs: set[str] = set()
    seq = 0

    def next_ref() -> str:
        nonlocal seq
        while True:
            seq += 1
            candidate = f"SYM-{seq:04d}"
            if candidate not in used_refs:
                return candidate

    for item in payload.symbols:
        cls = cls_by_key.get(item.class_key)
        if cls is None:
            cls = ensure_class(db, item.class_key)
            cls_by_key[cls.key] = cls
        ref = (item.ref or "").strip() or next_ref()
        if ref in used_refs:
            ref = next_ref()
        used_refs.add(ref)
        cx, cy, w, h = sanitize_box(item.cx, item.cy, item.w, item.h)
        sym = AnnotationSymbol(
            project_id=project.id,
            class_id=cls.id,
            ref=ref,
            label=(item.label or None),
            cx=cx,
            cy=cy,
            w=w,
            h=h,
            note=(item.note or None),
            origin=(item.origin or "manual"),
            confidence=item.confidence,
        )
        db.add(sym)
        db.flush()
        symbol_by_ref[ref] = sym

        for t_index, t in enumerate(item.terminals or [], start=1):
            t_ref = (t.ref or "").strip() or f"{ref}-T{t_index}"
            term = SymbolTerminal(
                symbol_id=sym.id,
                name=t.name.strip() or str(t_index),
                tx=max(0.0, min(1.0, float(t.tx))),
                ty=max(0.0, min(1.0, float(t.ty))),
            )
            db.add(term)
            db.flush()
            terminal_by_ref[t_ref] = term

    skipped: list[str] = []
    for c in payload.connections:
        src = symbol_by_ref.get(c.from_symbol_ref)
        dst = symbol_by_ref.get(c.to_symbol_ref)
        if src is None or dst is None:
            skipped.append(f"{c.from_symbol_ref} -> {c.to_symbol_ref}")
            continue
        if src.id == dst.id and (c.from_terminal_ref or "") == (c.to_terminal_ref or ""):
            skipped.append(f"{c.from_symbol_ref} -> {c.to_symbol_ref}（自己ループ）")
            continue
        ft = terminal_by_ref.get(c.from_terminal_ref) if c.from_terminal_ref else None
        tt = terminal_by_ref.get(c.to_terminal_ref) if c.to_terminal_ref else None
        db.add(
            Connection(
                project_id=project.id,
                from_symbol_id=src.id,
                from_terminal_id=ft.id if ft else None,
                to_symbol_id=dst.id,
                to_terminal_id=tt.id if tt else None,
                wire_no=(c.wire_no or None),
                net_id=(c.net_id or None),
                kind=(c.kind or "wire"),
                external_ref=(c.external_ref or None),
                note=(c.note or None),
            )
        )

    db.commit()
    return {"skipped": skipped}


# --------------------------------------------------------------------------
# シリアライズ
# --------------------------------------------------------------------------
def terminal_ref(symbol: AnnotationSymbol, terminal: SymbolTerminal) -> str:
    order = sorted(symbol.terminals, key=lambda t: t.id)
    idx = order.index(terminal) + 1 if terminal in order else 1
    return f"{symbol.ref}-T{idx}"


def serialize_project(db: Session, project: AnnotationProject, with_image_meta: bool = True) -> dict:
    cls_by_id = {c.id: c for c in classes_ordered(db)}
    symbols = sorted(project.symbols, key=lambda s: s.ref)
    out_symbols = []
    for s in symbols:
        cls = cls_by_id.get(s.class_id)
        out_symbols.append(
            {
                "ref": s.ref,
                "class_key": cls.key if cls else "other",
                "class_label": cls.label if cls else "その他",
                "yolo_index": cls.yolo_index if cls else 0,
                "label": s.label,
                "cx": s.cx,
                "cy": s.cy,
                "w": s.w,
                "h": s.h,
                "note": s.note,
                "origin": s.origin or "manual",
                "confidence": s.confidence,
                "terminals": [
                    {"ref": terminal_ref(s, t), "name": t.name, "tx": t.tx, "ty": t.ty}
                    for t in sorted(s.terminals, key=lambda t: t.id)
                ],
            }
        )

    sym_by_id = {s.id: s for s in symbols}
    out_connections = []
    for c in sorted(project.connections, key=lambda c: c.id):
        src, dst = sym_by_id.get(c.from_symbol_id), sym_by_id.get(c.to_symbol_id)
        if src is None or dst is None:
            continue
        out_connections.append(
            {
                "from_symbol_ref": src.ref,
                "from_terminal_ref": terminal_ref(src, c.from_terminal) if c.from_terminal else None,
                "to_symbol_ref": dst.ref,
                "to_terminal_ref": terminal_ref(dst, c.to_terminal) if c.to_terminal else None,
                "wire_no": c.wire_no,
                "net_id": c.net_id,
                "kind": c.kind,
                "external_ref": c.external_ref,
                "note": c.note,
            }
        )

    data = {
        "id": project.id,
        "name": project.name,
        "sheet_no": project.sheet_no,
        "page_no": project.page_no,
        "revision": project.revision,
        "source_file": project.source_file,
        "image_width": project.image_width,
        "image_height": project.image_height,
        "status": project.status,
        "assignee": project.assignee,
        "note": project.note,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "symbols": out_symbols,
        "connections": out_connections,
    }
    if with_image_meta:
        data["images"] = [
            {"filename": im.filename, "sha256": im.sha256, "width": im.width, "height": im.height}
            for im in project.images
        ]
    return data
