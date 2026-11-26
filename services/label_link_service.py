"""編集画面の「ラベルを近くの文字から付ける」（DD-0049）。

記号の枠は印だけを囲み、器具番号の文字は枠の外に書かれている。図面の文字（構造解析が読んだ文字、ベクター PDF の
文字、どちらも無ければその場で OCR）から、記号ごとに近くの文字のまとまりを選び、ラベルと文字の位置の候補を返す。
保存はしない。採るかどうかは画面で人が決める。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from PIL import Image
from sqlalchemy.orm import Session

from analysis import label_link
from analysis import text_extraction as tx
from models import AnnotationProject
from services import vector_pdf
from services.annotation_service import image_path
from services.geometry import denormalize_box, normalize_box
from services.structure_service import get_structure_texts

TextSource = Literal["analysis", "pdf_text", "ocr"]
_logger = logging.getLogger(__name__)


def _cache_path(project_id: int):
    from database import DATA_DIR

    path = DATA_DIR / "label_text"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{int(project_id)}.json"


def _ocr_items(project: AnnotationProject, image: Image.Image, sha256: str) -> list[tx.TextItem]:
    """図面全体を OCR した文字。同じ画像なら 2 回目からは保存した結果を使う。"""
    path = _cache_path(project.id)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("image_sha256") == sha256:
            return [{**i, "box": tuple(i["box"])} for i in cached["items"]]
    from analysis.wiring.ocr import load_symbol_ocr_reader

    reader, _engine, warning = load_symbol_ocr_reader("rapidocr", "en")
    if reader is None:
        raise RuntimeError("文字認識（OCR）を使えない環境です。先に AI 解析を実行するか、ラベルを手で入力してください。"
                           + (f"（{warning}）" if warning else ""))
    items = tx.read_page_text(image.convert("RGB"), reader)
    path.write_text(json.dumps({"image_sha256": sha256, "items": items}, ensure_ascii=False), encoding="utf-8")
    return items


def page_text_items(db: Session, project: AnnotationProject, image: Image.Image) -> tuple[list[tx.TextItem], TextSource]:
    """図面の文字。構造解析が読んだ文字 → ベクター PDF の文字 → その場の OCR の順に使う。"""
    sha256 = project.images[0].sha256
    texts = get_structure_texts(db, [project.id]).get(project.id)
    if texts:
        return [{"text": t["text"], "score": float(t.get("score") or 1.0), "box": tuple(t["box"])} for t in texts], "analysis"
    vector = vector_pdf.load(project.id, sha256)
    if vector and vector.get("texts"):
        return [{"text": t["text"], "score": 1.0, "box": tuple(t["box"])}
                for t in vector_pdf.structure_texts(vector) if t.get("angle", 0) in (0, 180)], "pdf_text"
    return _ocr_items(project, image, sha256), "ocr"


def suggest_label_links(db: Session, project: AnnotationProject, symbols: list[Any]) -> dict[str, Any]:
    """記号（保存形式の SymbolPayload。編集中で未保存でもよい）ごとに、近くの文字から作ったラベルの候補を返す。

    返り値: {"source", "links": [{"ref", "label", "label_box", "status", "direction", "candidates"}]}。
    座標は正規化（cx, cy, w, h）。器具番号を書かない種類（分岐点・母線端・その他）は返さない。
    """
    if not project.images:
        raise RuntimeError("図面画像が登録されていないため、文字を読めません")
    path = image_path(project.images[0].sha256)
    if not path.exists():
        raise RuntimeError("図面画像が見つかりません")
    with Image.open(path) as opened:
        image = opened.copy()
    width, height = image.size
    items, source = page_text_items(db, project, image)
    pixel_symbols = [
        {"id": s.ref, "class_name": s.class_key, "bbox": denormalize_box(s.cx, s.cy, s.w, s.h, width, height)}
        for s in symbols if s.ref
    ]
    lexicon = tx.page_lexicon(items)
    zones = tx.text_zones(items, width, height)
    links = label_link.link_labels(items, pixel_symbols, lexicon=lexicon, zones=zones)

    def norm(box: Any) -> dict[str, float]:
        cx, cy, w, h = normalize_box(*box, width, height)
        return {"cx": cx, "cy": cy, "w": w, "h": h}

    out = []
    for ref, link in links.items():
        out.append({
            "ref": ref, "label": link["label"], "status": link["status"], "direction": link["direction"],
            "label_box": norm(link["label_box"]) if link["label_box"] else None,
            "candidates": [{"text": c["text"], "box": norm(c["box"])} for c in link["candidates"]],
        })
    return {"source": source, "links": out}
