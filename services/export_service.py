"""エクスポート／インポート。

出力する ZIP は 1 つで 2 つの役割を兼ねる。

    <root>/
      bundle.json                       完全復元用（このツールの正本）
      classes.txt                       クラス名（yolo_index 順）
      data.yaml                         ultralytics 用の設定
      dataset/images/train/*.png        YOLO 学習データ（全件 train。val 分割はしない）
      dataset/labels/train/*.txt
      connections/connections.csv       from-to（人が読める形式）
      connections/netlist.json          from-to（機械可読・ネット単位）
      README.txt

インポートで復元できるのは、このツール自身が出力した `bundle.json` 付きの ZIP のみ。
`bundle.json` には端子・配線・メタデータまで含まれるので、別環境でも完全に復元できる。
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import yaml
from sqlalchemy.orm import Session

from models import (
    AnnotationImage,
    AnnotationProject,
    AnnotationSymbol,
    Connection,
    SymbolClass,
    SymbolTerminal,
)
from services.annotation_service import (
    class_map_by_key,
    classes_ordered,
    ensure_class,
    image_path,
    serialize_project,
    store_image_bytes,
)
from services.geometry import sanitize_box

BUNDLE_FILENAME = "bundle.json"
CLASSES_FILENAME = "classes.txt"
DATA_YAML_FILENAME = "data.yaml"
DATASET_DIRNAME = "dataset"
IMAGES_DIRNAME = "images"
LABELS_DIRNAME = "labels"
CONNECTIONS_DIRNAME = "connections"
SCHEMA_VERSION = "1.0"
TOOL_NAME = "seq-annotator"

# インポート時のセーフガード（ZIP 爆弾・パストラバーサル対策）
MAX_IMPORT_FILES = 20000
MAX_IMPORT_UNCOMPRESSED_BYTES = 2 * 1024**3
MAX_IMPORT_COMPRESSION_RATIO = 25
_UNSAFE_NAMES = {".DS_Store", "Thumbs.db"}


# --------------------------------------------------------------------------
# 共通
# --------------------------------------------------------------------------
def project_image_stem(project: AnnotationProject, image: AnnotationImage) -> str:
    """エクスポート画像名のステム ``p<project_id>_<stem>`` を返す。

    外部の yolo predict 出力を取り込むときは、このステム規則で図面へ対応付ける。
    """
    stem = Path(image.filename).stem or f"image_{image.id}"
    stem = re.sub(r"[^\w.\-]+", "_", stem)
    return f"p{project.id}_{stem}"


def build_label_content(db: Session, project: AnnotationProject) -> str:
    """YOLO のラベルテキスト（`class_id cx cy w h`、小数 6 桁）。"""
    cls_by_id = {c.id: c for c in classes_ordered(db)}
    lines = []
    for s in sorted(project.symbols, key=lambda s: s.ref):
        cls = cls_by_id.get(s.class_id)
        if cls is None:
            continue
        lines.append(f"{cls.yolo_index} {s.cx:.6f} {s.cy:.6f} {s.w:.6f} {s.h:.6f}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_classes_content(db: Session) -> str:
    return "\n".join(c.key for c in classes_ordered(db)) + "\n"


def build_data_yaml(db: Session) -> str:
    names = {c.yolo_index: c.key for c in classes_ordered(db)}
    # 全件を train に出力する。ultralytics は val キーが必須のため train と同じ場所を指す。
    doc = {
        "path": f"./{DATASET_DIRNAME}",
        "train": f"{IMAGES_DIRNAME}/train",
        "val": f"{IMAGES_DIRNAME}/train",
        "nc": len(names),
        "names": {int(k): v for k, v in sorted(names.items())},
    }
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def build_connections_csv(db: Session, projects: list[AnnotationProject]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "project_id",
            "sheet_no",
            "from_symbol_ref",
            "from_class",
            "from_label",
            "from_terminal",
            "to_symbol_ref",
            "to_class",
            "to_label",
            "to_terminal",
            "wire_no",
            "net_id",
            "kind",
            "external_ref",
            "note",
        ]
    )
    for p in projects:
        data = serialize_project(db, p, with_image_meta=False)
        by_ref = {s["ref"]: s for s in data["symbols"]}
        for c in data["connections"]:
            src = by_ref.get(c["from_symbol_ref"], {})
            dst = by_ref.get(c["to_symbol_ref"], {})
            writer.writerow(
                [
                    p.id,
                    p.sheet_no or "",
                    c["from_symbol_ref"],
                    src.get("class_key", ""),
                    src.get("label") or "",
                    c["from_terminal_ref"] or "",
                    c["to_symbol_ref"],
                    dst.get("class_key", ""),
                    dst.get("label") or "",
                    c["to_terminal_ref"] or "",
                    c["wire_no"] or "",
                    c["net_id"] or "",
                    c["kind"],
                    c["external_ref"] or "",
                    (c["note"] or "").replace("\n", " "),
                ]
            )
    return "\ufeff" + buf.getvalue()  # BOM: Excel 開封時の文字化け防止


# Union-Find（ネット分割用）
def _find(parent: dict[str, str], a: str) -> str:
    parent.setdefault(a, a)
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def _union(parent: dict[str, str], a: str, b: str) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def _node_key(sym: str, term: str | None) -> str:
    return f"{sym}:{term}" if term else sym


def build_netlist(db: Session, projects: list[AnnotationProject]) -> dict:
    """from-to を連結して、同一ネットに属する端子集合を求める（Union-Find）。"""
    out = []
    for p in projects:
        data = serialize_project(db, p, with_image_meta=False)
        parent: dict[str, str] = {}
        for c in data["connections"]:
            _union(
                parent,
                _node_key(c["from_symbol_ref"], c["from_terminal_ref"]),
                _node_key(c["to_symbol_ref"], c["to_terminal_ref"]),
            )
        groups: dict[str, list[str]] = {}
        for n in parent:
            groups.setdefault(_find(parent, n), []).append(n)

        wire_by_root: dict[str, str] = {}
        for c in data["connections"]:
            if c["wire_no"]:
                wire_by_root.setdefault(
                    _find(parent, _node_key(c["from_symbol_ref"], c["from_terminal_ref"])),
                    c["wire_no"],
                )

        nets = []
        for i, (root, members) in enumerate(sorted(groups.items()), start=1):
            nets.append(
                {
                    "id": f"NET-{i:04d}",
                    "wire_no": wire_by_root.get(root),
                    "members": sorted(members),
                }
            )
        out.append(
            {
                "project_id": p.id,
                "sheet_no": p.sheet_no,
                "connection_count": len(data["connections"]),
                "nets": nets,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "projects": out}


def build_bundle(db: Session, projects: list[AnnotationProject]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "classes": [
            {
                "key": c.key,
                "label": c.label,
                "yolo_index": c.yolo_index,
                "color": c.color,
                "sort_order": c.sort_order,
                "is_active": bool(c.is_active),
            }
            for c in classes_ordered(db)
        ],
        "projects": [serialize_project(db, p) for p in projects],
    }


README_TEXT = """\
シーケンス図アノテーション エクスポート
========================================

bundle.json
    このツールの正本。シンボル矩形・端子・配線(from-to)・メタデータを完全に含む。
    「インポート」でこの ZIP を読み込むと、別環境でも同じ状態に復元できる。

classes.txt / data.yaml / dataset/
    YOLO 学習用。全画像を dataset/images/train に出力する（val 分割はしない）。
    ultralytics でそのまま学習できる。
        yolo detect train data=data.yaml model=yolov8n.pt
    class id は classes.txt の行番号（0 始まり）と一致する。

connections/connections.csv
    配線(from-to)の一覧。Excel で確認・レビューする用途。
connections/netlist.json
    from-to を連結して求めたネット（導通する端子集合）。

インポート（復元）は、この ZIP（bundle.json を含む）だけを受け付ける。
"""


def write_export_zip(
    db: Session,
    projects: list[AnnotationProject],
) -> bytes:
    buf = io.BytesIO()
    classes_text = build_classes_content(db)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(BUNDLE_FILENAME, json.dumps(build_bundle(db, projects), ensure_ascii=False, indent=2))
        zf.writestr(CLASSES_FILENAME, classes_text)
        zf.writestr(DATA_YAML_FILENAME, build_data_yaml(db))
        zf.writestr("README.txt", README_TEXT)
        zf.writestr(f"{CONNECTIONS_DIRNAME}/connections.csv", build_connections_csv(db, projects))
        zf.writestr(
            f"{CONNECTIONS_DIRNAME}/netlist.json",
            json.dumps(build_netlist(db, projects), ensure_ascii=False, indent=2),
        )

        for p in projects:
            label_content = build_label_content(db, p)
            # val 分割はしない。全画像を train に出力する。
            split = "train"
            for im in p.images:
                src = image_path(im.sha256)
                if not src.exists():
                    continue
                data = src.read_bytes()
                base = project_image_stem(p, im)
                zf.writestr(f"{DATASET_DIRNAME}/{IMAGES_DIRNAME}/{split}/{base}.png", data)
                zf.writestr(f"{DATASET_DIRNAME}/{LABELS_DIRNAME}/{split}/{base}.txt", label_content)
    return buf.getvalue()


# --------------------------------------------------------------------------
# インポート
# --------------------------------------------------------------------------
def safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """展開してよいエントリだけを返す（パストラバーサル・ZIP 爆弾・OS ゴミを除外）。"""
    infos: list[zipfile.ZipInfo] = []
    total, compressed = 0, 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("ZIP 内に不正なパスが含まれています")
        if "__MACOSX" in path.parts or path.name in _UNSAFE_NAMES or path.name.startswith("._"):
            continue
        total += info.file_size
        compressed += max(info.compress_size, 1)
        infos.append(info)
    if len(infos) > MAX_IMPORT_FILES:
        raise ValueError("ZIP 内のファイル数が上限を超えています")
    if total > MAX_IMPORT_UNCOMPRESSED_BYTES:
        raise ValueError("展開後のサイズが上限を超えています")
    if total > 10 * 1024**2 and total / compressed > MAX_IMPORT_COMPRESSION_RATIO:
        raise ValueError("圧縮率が異常です（ZIP 爆弾の可能性）")
    return infos


def _restore_classes(db: Session, classes: list[dict]) -> None:
    for c in classes:
        key = str(c.get("key") or "").strip()
        if not key:
            continue
        found = db.query(SymbolClass).filter(SymbolClass.key == key).one_or_none()
        if found:
            continue
        ensure_class(db, key, c.get("label"), c.get("color") or "#84b0d8")
    db.flush()


def _restore_project_from_bundle(db: Session, proj: dict, images: dict[str, bytes]) -> AnnotationProject:
    p = AnnotationProject(
        name=proj.get("name") or "インポート",
        sheet_no=proj.get("sheet_no"),
        page_no=proj.get("page_no"),
        revision=proj.get("revision"),
        source_file=proj.get("source_file"),
        image_width=int(proj.get("image_width") or 0),
        image_height=int(proj.get("image_height") or 0),
        status=proj.get("status") or "draft",
        assignee=proj.get("assignee"),
        note=proj.get("note"),
    )
    db.add(p)
    db.flush()

    for im in proj.get("images", []):
        raw = images.get(str(im.get("sha256"))) or images.get(str(im.get("filename")))
        if raw is None:
            continue
        digest, w, h = store_image_bytes(raw)
        db.add(
            AnnotationImage(
                project_id=p.id,
                filename=im.get("filename") or f"{digest}.png",
                sha256=digest,
                width=w,
                height=h,
            )
        )
        if not p.image_width:
            p.image_width, p.image_height = w, h

    cls_by_key = class_map_by_key(db)
    sym_by_ref: dict[str, AnnotationSymbol] = {}
    term_by_ref = {}
    for s in proj.get("symbols", []):
        key = s.get("class_key") or "other"
        cls = cls_by_key.get(key) or ensure_class(db, key)
        cls_by_key[cls.key] = cls
        cx, cy, w, h = sanitize_box(s.get("cx", 0), s.get("cy", 0), s.get("w", 0), s.get("h", 0))
        sym = AnnotationSymbol(
            project_id=p.id,
            class_id=cls.id,
            ref=s.get("ref") or f"SYM-{len(sym_by_ref) + 1:04d}",
            label=s.get("label"),
            cx=cx,
            cy=cy,
            w=w,
            h=h,
            note=s.get("note"),
            origin=s.get("origin") or "manual",
            confidence=s.get("confidence"),
        )
        db.add(sym)
        db.flush()
        sym_by_ref[sym.ref] = sym
        for t in s.get("terminals", []):
            term = SymbolTerminal(
                symbol_id=sym.id,
                name=str(t.get("name") or ""),
                tx=float(t.get("tx") or 0.0),
                ty=float(t.get("ty") or 0.0),
            )
            db.add(term)
            db.flush()
            term_by_ref[t.get("ref") or f"{sym.ref}-T{len(term_by_ref) + 1}"] = term

    for c in proj.get("connections", []):
        src = sym_by_ref.get(c.get("from_symbol_ref"))
        dst = sym_by_ref.get(c.get("to_symbol_ref"))
        if src is None or dst is None:
            continue
        ft = term_by_ref.get(c.get("from_terminal_ref")) if c.get("from_terminal_ref") else None
        tt = term_by_ref.get(c.get("to_terminal_ref")) if c.get("to_terminal_ref") else None
        db.add(
            Connection(
                project_id=p.id,
                from_symbol_id=src.id,
                from_terminal_id=ft.id if ft else None,
                to_symbol_id=dst.id,
                to_terminal_id=tt.id if tt else None,
                wire_no=c.get("wire_no"),
                net_id=c.get("net_id"),
                kind=c.get("kind") or "wire",
                external_ref=c.get("external_ref"),
                note=c.get("note"),
            )
        )
    return p


def import_zip(db: Session, blob: bytes) -> dict:
    """このツールが出力した ZIP（bundle.json 付き）を取り込む。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP ファイルとして読み込めませんでした") from exc

    with zf:
        infos = safe_members(zf)
        files: dict[str, bytes] = {}
        for info in infos:
            files[info.filename.replace("\\", "/")] = zf.read(info)

    bundle_raw = None
    for name, raw in files.items():
        if PurePosixPath(name).name == BUNDLE_FILENAME:
            bundle_raw = raw
            break

    if bundle_raw is None:
        raise ValueError("bundle.json を含む ZIP ではありません（このツールで出力した ZIP を取り込んでください）")

    try:
        bundle = json.loads(bundle_raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("bundle.json を解析できませんでした") from exc

    _restore_classes(db, bundle.get("classes", []))
    # 画像は sha256 と filename の両方で引けるようにしておく
    image_blobs: dict[str, bytes] = {}
    for name, raw in files.items():
        if PurePosixPath(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            image_blobs[PurePosixPath(name).name] = raw
            image_blobs[PurePosixPath(name).stem] = raw

    created: list[AnnotationProject] = []
    for proj in bundle.get("projects", []):
        resolved = {}
        for im in proj.get("images", []):
            fname = im.get("filename") or ""
            sha = str(im.get("sha256") or "")
            for cand in (
                fname,
                Path(fname).stem,
                f"{sha}.png",
                sha,
            ):
                if cand and cand in image_blobs:
                    resolved[sha] = image_blobs[cand]
                    resolved[fname] = image_blobs[cand]
                    break
            else:
                # ファイル名が p<id>_<stem>.png に変換されている場合の後方一致
                stem = Path(fname).stem
                for k, v in image_blobs.items():
                    if stem and k.endswith(stem):
                        resolved[sha] = v
                        resolved[fname] = v
                        break
        created.append(_restore_project_from_bundle(db, proj, resolved))

    if not created:
        raise ValueError("取り込めるデータが見つかりませんでした")
    db.commit()
    return {
        "mode": "bundle",
        "project_ids": [p.id for p in created],
        "count": len(created),
    }
