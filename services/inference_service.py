"""推論（AI による自動検出）と推論結果の取り込み。

サイクル: 推論 → 結果表示（JSON）→ エディタで修正 → アノテーションとして蓄積 → 学習。

- ultralytics が入っていれば登録済みモデルでサーバ内推論を行う。
- 入っていない環境（exe 配布など）では、外部で `yolo predict ... save_txt=True
  save_conf=True` した出力 ZIP を取り込んで同じ `predictions` テーブルへ載せる。
- 推論結果は常に「プロジェクト単位で最新だけ」を保持する（再推論で置き換え）。
"""

from __future__ import annotations

import io
import zipfile
from importlib.util import find_spec
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from models import AnnotationProject, MlModel, Prediction, SymbolClass
from services.annotation_service import classes_ordered, image_path
from services.export_service import safe_members
from services.geometry import sanitize_box
from services.model_service import model_path

# predictions ZIP でファイル名からプロジェクトを引く規則。
# エクスポート画像名は p<project_id>_<stem>.png なので、同じ stem の labels を対応付ける。
_PREDICTION_PREFIX_SEPARATOR = "_"


def ultralytics_available() -> bool:
    return find_spec("ultralytics") is not None


def _class_by_yolo_index(db: Session) -> dict[int, SymbolClass]:
    return {c.yolo_index: c for c in classes_ordered(db)}


def clear_predictions(db: Session, project_id: int) -> None:
    db.query(Prediction).filter(Prediction.project_id == project_id).delete(
        synchronize_session=False
    )


def _record_predictions(
    db: Session,
    project: AnnotationProject,
    detections: list[dict],
    model: MlModel | None,
    model_label: str,
) -> int:
    """detections: [{class_id, cx, cy, w, h, confidence}]。そのプロジェクトの推論結果を置き換える。"""
    clear_predictions(db, project.id)
    count = 0
    for d in detections:
        cx, cy, w, h = sanitize_box(d["cx"], d["cy"], d["w"], d["h"])
        if w <= 0 or h <= 0:
            continue
        db.add(
            Prediction(
                project_id=project.id,
                model_id=model.id if model else None,
                model_label=model_label,
                class_id=d["class_id"],
                cx=cx,
                cy=cy,
                w=w,
                h=h,
                confidence=d.get("confidence"),
            )
        )
        count += 1
    db.flush()
    return count


def run_inference(
    db: Session,
    model: MlModel,
    projects: list[AnnotationProject],
    conf: float,
) -> list[dict]:
    """登録済みモデルで推論し、結果を predictions テーブルへ保存する。"""
    if not ultralytics_available():
        raise RuntimeError(
            "ultralytics がインストールされていないためサーバ内で推論できません。"
            "外部で `yolo predict` を実行し、結果 ZIP を取り込んでください。"
        )
    path = model_path(model.sha256)
    if not path.exists():
        raise RuntimeError("モデルファイルが見つかりません。モデルを再登録してください。")

    from ultralytics import YOLO  # 重量級のため遅延 import

    detector = YOLO(str(path))
    class_by_index = _class_by_yolo_index(db)
    class_by_key = {c.key: c for c in class_by_index.values()}
    detector_names = getattr(detector, "names", None) or {}
    summary = []
    for project in projects:
        detections: list[dict] = []
        skipped_images = 0
        for image in project.images:
            src = image_path(image.sha256)
            if not src.exists():
                skipped_images += 1
                continue
            results = detector.predict(str(src), conf=conf, verbose=False)
            for result in results:
                boxes = result.boxes
                if boxes is None or boxes.xywhn is None:
                    continue
                xywhn = boxes.xywhn.tolist()
                confs = boxes.conf.tolist() if boxes.conf is not None else [None] * len(xywhn)
                classes = boxes.cls.tolist() if boxes.cls is not None else [None] * len(xywhn)
                for (cx, cy, w, h), score, cls_index in zip(xywhn, confs, classes, strict=False):
                    cls = None
                    if cls_index is not None:
                        # 学習時のクラス順はアプリの順序と一致しないことがあるため、
                        # モデルのクラス名 -> SymbolClass.key で対応付ける。
                        # 名前が一致しない検出を番号で拾うと誤分類になるため捨てる
                        det_name = detector_names.get(int(cls_index))
                        if det_name is not None:
                            cls = class_by_key.get(det_name)
                        else:
                            cls = class_by_index.get(int(cls_index))
                    if cls is None:
                        continue
                    detections.append(
                        {
                            "class_id": cls.id,
                            "cx": cx,
                            "cy": cy,
                            "w": w,
                            "h": h,
                            "confidence": float(score) if score is not None else None,
                        }
                    )
        count = _record_predictions(
            db, project, detections, model, model_label=model.name
        )
        summary.append(
            {
                "project_id": project.id,
                "name": project.name,
                "detections": count,
                "skipped_images": skipped_images,
            }
        )
    db.commit()
    return summary


def _parse_label_lines(
    raw: bytes, class_by_index: dict[int, SymbolClass], names_from_zip: list[str] | None
) -> list[dict]:
    """`cls cx cy w h [conf]` 形式のラベルテキストを解析する。"""
    key_by_index: dict[int, str] = {}
    if names_from_zip:
        key_by_index = {i: name for i, name in enumerate(names_from_zip)}
    index_to_class: dict[int, SymbolClass] = {}
    if key_by_index:
        key_to_class = {c.key: c for c in class_by_index.values()}
        for i, key in key_by_index.items():
            cls = key_to_class.get(key)
            if cls is not None:
                index_to_class[i] = cls
    else:
        index_to_class = dict(class_by_index)

    detections: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls_index = int(float(parts[0]))
            cx, cy, w, h = (float(v) for v in parts[1:5])
            conf = float(parts[5]) if len(parts) >= 6 else None
        except ValueError:
            continue
        cls = index_to_class.get(cls_index)
        if cls is None:
            continue
        detections.append(
            {"class_id": cls.id, "cx": cx, "cy": cy, "w": w, "h": h, "confidence": conf}
        )
    return detections


def _project_id_from_label_name(stem: str) -> int | None:
    """`p<digits>_...` 形式のステムから project_id を取り出す。"""
    if not stem.startswith("p"):
        return None
    head = stem[1:].split(_PREDICTION_PREFIX_SEPARATOR, 1)[0]
    return int(head) if head.isdigit() else None


def import_predictions_zip(db: Session, blob: bytes, source_label: str) -> dict:
    """`yolo predict --save-txt --save-conf` の出力 ZIP を取り込む。

    ラベルファイル名が `p<project_id>_*.txt`（このツールのエクスポート画像名と同じ
    ステム）なら、そのプロジェクトの推論結果として登録する。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP ファイルとして読み込めませんでした") from exc

    with zf:
        infos = safe_members(zf)
        files: dict[str, bytes] = {}
        for info in infos:
            files[info.filename.replace("\\", "/")] = zf.read(info)

    names_from_zip: list[str] | None = None
    for name, raw in files.items():
        if PurePosixPath(name).name == "classes.txt":
            names_from_zip = [
                line.strip() for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()
            ]
            break

    class_by_index = _class_by_yolo_index(db)
    by_project: dict[int, list[dict]] = {}
    unmatched: list[str] = []
    for name, raw in files.items():
        path = PurePosixPath(name)
        if path.suffix.lower() != ".txt" or path.name == "classes.txt":
            continue
        project_id = _project_id_from_label_name(path.stem)
        if project_id is None:
            unmatched.append(path.name)
            continue
        detections = _parse_label_lines(raw, class_by_index, names_from_zip)
        by_project.setdefault(project_id, []).extend(detections)

    if not by_project:
        raise ValueError(
            "取り込める推論結果がありません。`p<図面ID>_*.txt` 形式のラベルファイルを含む ZIP を指定してください"
        )

    project_ids = sorted(by_project)
    projects = {
        p.id: p
        for p in db.query(AnnotationProject).filter(AnnotationProject.id.in_(project_ids)).all()
    }
    results = []
    missing_ids = []
    for project_id in project_ids:
        project = projects.get(project_id)
        if project is None:
            missing_ids.append(project_id)
            continue
        count = _record_predictions(
            db, project, by_project[project_id], model=None, model_label=source_label
        )
        results.append({"project_id": project_id, "name": project.name, "detections": count})
    db.commit()
    return {
        "results": results,
        "unmatched_files": unmatched,
        "missing_project_ids": missing_ids,
        "count": len(results),
    }


def predictions_payload(db: Session, project: AnnotationProject) -> dict:
    """結果表示用 JSON。モデル情報＋検出一覧（クラス名・信頼度つき）。"""
    cls_by_id = {c.id: c for c in classes_ordered(db)}
    rows = (
        db.query(Prediction)
        .filter(Prediction.project_id == project.id)
        .order_by(Prediction.id.asc())
        .all()
    )
    model_labels = {r.model_label for r in rows if r.model_label}
    model_ids = {r.model_id for r in rows if r.model_id is not None}
    return {
        "project_id": project.id,
        "name": project.name,
        # 複数ソースが混ざる場合は「出どころが一意でない」として None を返す
        "model_label": next(iter(model_labels)) if len(model_labels) == 1 else None,
        "model_id": next(iter(model_ids)) if len(model_ids) == 1 else None,
        "count": len(rows),
        "detections": [
            {
                "class_key": (cls_by_id.get(r.class_id).key if cls_by_id.get(r.class_id) else "other"),
                "class_label": (
                    cls_by_id.get(r.class_id).label if cls_by_id.get(r.class_id) else "その他"
                ),
                "yolo_index": (
                    cls_by_id.get(r.class_id).yolo_index if cls_by_id.get(r.class_id) else 0
                ),
                "cx": r.cx,
                "cy": r.cy,
                "w": r.w,
                "h": r.h,
                "confidence": r.confidence,
            }
            for r in rows
        ],
    }


def predictions_summary(db: Session) -> list[dict]:
    """プロジェクトごとの推論結果件数（一覧表示用）。"""
    rows = (
        db.query(Prediction.project_id, Prediction.model_label, Prediction.id)
        .order_by(Prediction.id.asc())
        .all()
    )
    by_project: dict[int, dict] = {}
    for project_id, model_label, _ in rows:
        entry = by_project.setdefault(
            project_id, {"project_id": project_id, "count": 0, "model_label": model_label}
        )
        entry["count"] += 1
        if model_label:
            entry["model_label"] = model_label
    return sorted(by_project.values(), key=lambda e: e["project_id"])
