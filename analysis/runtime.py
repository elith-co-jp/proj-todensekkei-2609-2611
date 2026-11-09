"""図面の画像と記号の検出から構造 JSON を作る（アプリの解析の入口）。配線解析の各段は analysis.wiring にある。

Only an image and detector predictions enter this path. Manual annotations and
reference connections are never used as inputs to production inference.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image

from analysis.label_llm import TEXT, reader_from_env
from analysis.name_ocr import default_reader as name_ocr_reader
from analysis.relay_lamp import review_circles
from analysis.relay_links import link_relay_rows
from analysis.text_extraction import extract_text_structure
from analysis.wiring import classes
from analysis.wiring.assemble import structure_from_detection
from analysis.wiring.connectivity import build_direct_from_to
from analysis.wiring.contact_kind import correct_contact_kinds, merge_contact_duplicates
from analysis.wiring.detect import detect_wiring, make_binary_mask
from analysis.wiring.dot_roles import annotate_dots
from analysis.wiring.faint_lines import bridge_faint_wires, bridge_parallel_contacts
from analysis.wiring.filter_wire_decorations import filter_wire_decorations, rapidocr_text_confirmation
from analysis.wiring.linking import enrich_symbols_and_graph
from analysis.wiring.ocr import load_crop_ocr_reader, load_symbol_ocr_reader
from analysis.wiring.params import DEFAULT, WiringParams
from analysis.wiring.ports import DEFAULT_INFERRED_TERMINAL_CLASSES, port_inference_config
from analysis.wiring.revision_clouds import detect_clouds, erase_clouds
from analysis.wiring.terminal_numbers import attach_terminal_numbers

# 端子の位置の推定の設定（analysis.wiring.ports.default_configs の名前）。30 枚の正解データで選んだもの（DD-0007）。
PORT_INFERENCE_CONFIG = "template_confident_relaxed_span_ink02"


def _pixel_box(detection: dict[str, Any], width: int, height: int) -> dict[str, int]:
    cx, cy, w, h = (float(detection[key]) for key in ("cx", "cy", "w", "h"))
    return {
        "x0": round((cx - w / 2) * width), "y0": round((cy - h / 2) * height),
        "x1": round((cx + w / 2) * width), "y1": round((cy + h / 2) * height),
    }


def page_for_detection(image_path: Path) -> Image.Image | str:
    """雲マーク（#41）の線を消した図面。雲マークが記号の検出を邪魔しないようにする。雲マークが無ければファイルのまま。"""
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    clouds = detect_clouds(make_binary_mask(image))
    return erase_clouds(image, clouds, []) if clouds.regions else str(image_path)


def analyze_detections(
    image_path: Path,
    detections: list[dict[str, Any]],
    *,
    source: dict[str, Any],
    regions: list[dict[str, Any]] | None = None,
    params: WiringParams = DEFAULT,
) -> dict[str, Any]:
    """アプリに表示している画像そのものから、配線・端子・端子間の接続を解析する。調整値は params（analysis.wiring.params）。

    段の順: 記号 → 雲マーク → 配線の検出 → 文字の読み取り → 円形の記号の確認 → 罫線の除外 → 黒丸の役割
    → 端子と配線の結び付け → 端子番号・リレー表 → 端子間の接続 → 薄い線 → 仕上げ。
    """
    with Image.open(image_path) as opened:
        page_image = opened.convert("RGB")
    width, height = page_image.size
    page_binary = make_binary_mask(page_image)
    # 同じ接点に重なった a 接点と b 接点の枠は、棒の側で 1 つにする（DD-0023）。
    symbols = merge_contact_duplicates(_symbols_from_detections(detections, width, height), page_binary)
    # 重ならない接点も、棒の側がはっきりしていれば a・b をそれに合わせる（DD-0023）。
    contact_kind_fixes = correct_contact_kinds(symbols, page_binary)

    # 雲マーク（#41）: 雲マークの線を消した画像で配線を読み、文字は元の画像で読む。記号の枠の中は消さない。
    clouds = detect_clouds(page_binary)
    image = erase_clouds(page_image, clouds, [s["bbox"] for s in symbols])

    payload = structure_from_detection(detect_wiring(image), source)
    warnings: list[str] = []
    reader, engine, text_result = _read_text(page_image, symbols, regions or [], warnings)
    wired_symbols = [s for s in symbols if not s.get("text_zone")]
    # リレーと表示灯（#39）: 円形の記号を、円の中の×印・近くの文字・リレー表・同じ器具の接点で確かめる。
    circle_review = review_circles(page_binary, symbols, text_result)

    payload = filter_wire_decorations(
        payload, image, wired_symbols,
        confirm_text=rapidocr_text_confirmation(reader) if reader is not None and engine == "rapidocr" else None,
    )
    if payload["quality"]["wire_decoration_filter"]["label_rule_ocr_failed"]:
        warnings.append("罫線の文字確認に失敗したため、判定できない罫線は配線候補として残しています。")
    # 配線の前に: enrich_symbols_and_graph はここで印を付けた重複の黒丸を飛ばす。
    dot_summary = annotate_dots(image, wired_symbols)
    payload = _link_terminals(payload, wired_symbols, image, params)
    terminal_number_counts = attach_terminal_numbers(payload["symbols"], page_items=text_result.get("texts"))
    # リレー表の「本」の行を行列の位置の接点に結び付ける（#40 #44）。接点の種類を表に合わせて直すので from-to より先。
    relay_row_counts = link_relay_rows(text_result, payload["symbols"], width, height) if text_result else None
    payload["from_to"] = build_direct_from_to(payload, source_image=image, params=params.bridge)
    # 細い線は 150 dpi のスキャンで途切れた薄い灰色になり、配線から落ちる。一直線に並んだ未接続の端子の間に
    # 薄い線があれば配線として足す（DD-0007 §4.22）。
    faint_wires = bridge_faint_wires(payload, image) if params.faint_lines else []
    # 並列の接点の迂回の線（L 字の灰色の線）で、片方の接点の端子をもう片方の同じ側の端子に結ぶ（DD-0016）。
    faint_wires += bridge_parallel_contacts(payload, image) if params.faint_lines else []

    for symbol in symbols:
        symbol.pop("text_items", None)
    payload["symbols"] = symbols
    payload.update(text_result)
    payload["regions"] = [*(payload["regions"] if isinstance(payload.get("regions"), list) else []), *clouds.regions]
    if dot_summary is not None:
        payload["dot_roles"] = dot_summary
    if terminal_number_counts is not None:
        payload["quality"]["terminal_numbers"] = terminal_number_counts
    if relay_row_counts is not None:
        payload["quality"]["relay_rows_by_position"] = relay_row_counts
    payload["quality"]["contact_kind_fixes"] = contact_kind_fixes
    _finish(payload, symbols, wired_symbols, text_result, engine, warnings, circle_review, len(faint_wires))
    return payload


def _symbols_from_detections(detections: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    """YOLO の検出（中心・幅・高さの比率）を、画素の枠を持つ記号にする。"""
    symbols = []
    for index, detection in enumerate(detections, 1):
        cx, cy = float(detection["cx"]), float(detection["cy"])
        symbols.append({
            "id": f"symbol_{index:03d}",
            "type": detection["class_key"],
            "class_name": detection["class_key"],
            "class_label": detection["class_label"],
            "bbox": _pixel_box(detection, width, height),
            "center": [cx * width, cy * height],
            "confidence": detection.get("confidence"),
            "status": "detected_by_yolo",
        })
    return symbols


def _read_text(
    page_image: Image.Image, symbols: list[dict[str, Any]], regions: list[dict[str, Any]], warnings: list[str],
) -> tuple[Any, str, dict[str, Any]]:
    """文字の読み取り（#38 #41 #43 #44 #45）: ページ OCR 1 回で全記号のラベル・リレー表・シート間参照・
    シート番号候補・領域の種類を作る。リレー表・表題欄の中の記号は文字として扱い、配線に使わない（text_zone）。

    OCR に失敗しても、検出できた配線は捨てない。戻り値は (ページの OCR, エンジン名, 文字の結果)。
    """
    width, height = page_image.size
    region_boxes = [
        {"id": f"region_{index:03d}", "bbox": _pixel_box(r, width, height), "confidence": r.get("confidence")}
        for index, r in enumerate(regions, 1)
    ]
    reader, engine, text_result = None, "none", {}
    try:
        reader, engine, warning = load_symbol_ocr_reader("rapidocr", "en")
        if warning:
            warnings.append("ラベル読み取りを実行できませんでした。OCRの実行環境を確認してください。")
        elif reader is not None:
            text_llm = reader_from_env(TEXT)
            text_result = extract_text_structure(
                # 日本語の名称・注記は、ローカル LLM が設定されていればそれで、なければ同梱の日本語対応 OCR で読み直す。
                page_image, symbols, region_boxes, reader, reader_from_env(), text_llm or name_ocr_reader(),
                text_source="llm" if text_llm else "name_ocr",
                crop_reader=load_crop_ocr_reader(reader),
            )
    except Exception:  # noqa: BLE001  OCR の失敗で、検出できた配線を捨てない。
        logging.getLogger(__name__).exception("Symbol label OCR failed")
        engine, text_result = "none", {}
        warnings.append("ラベル読み取りに失敗しました。シンボル・配線の結果は確認できます。")
    for symbol in symbols:
        if symbol.get("text_zone"):
            symbol["status"] = "suppressed_text_zone"
    return reader, engine, text_result


def _link_terminals(
    payload: dict[str, Any], wired_symbols: list[dict[str, Any]], image: Image.Image, params: WiringParams,
) -> dict[str, Any]:
    """記号の端子を決め、配線・ノードに結ぶ（analysis.wiring.ports・linking）。"""
    return enrich_symbols_and_graph(
        payload, wired_symbols, terminal_wire_threshold=params.link.terminal_wire_px,
        terminal_node_threshold=params.link.terminal_node_px,
        terminal_source="inferred", inferred_terminal_classes=DEFAULT_INFERRED_TERMINAL_CLASSES,
        inferred_terminal_config=port_inference_config(PORT_INFERENCE_CONFIG),
        symbol_terminal_bridge_classes=set(classes.TWO_TERMINAL_DEVICES) | {classes.JUNCTION},
        junction_node_policy="all",
        junction_node_classes={classes.JUNCTION}, junction_node_threshold=params.link.junction_node_px, source_image=image,
        port_params=params.ports,
    )



def _finish(
    payload: dict[str, Any], symbols: list[dict[str, Any]], wired_symbols: list[dict[str, Any]],
    text_result: dict[str, Any], engine: str, warnings: list[str], circle_review: Any, faint_wire_count: int,
) -> None:
    """構造 JSON の版・状態・能力・注記・品質の集計を書く。"""
    payload.update({
        "schema_version": "todensekkei.sequence_extraction.v1",
        "status": "partial" if warnings else "completed",
        "warnings": warnings,
        "external_references": [],
        "capabilities": {
            "symbol_detection": "completed", "wire_detection": "completed",
            "from_to": "predicted", "label_ocr": "unavailable" if warnings else "completed",
            "external_references": "not_resolved",
            "sheet_references": "extracted" if text_result else "unavailable",
            "relay_table": "extracted" if text_result else "unavailable",
        },
        "notes": [
            "Predictions only; no manual symbols, terminals or reference connections were used.",
            "Wire geometry and terminal-to-terminal connectivity are distinct prediction outputs.",
            "Cross-sheet references are read as target sheet numbers; they are not resolved to other pages.",
            "Symbols inside the relay table or title block are treated as text and are not wired.",
        ],
    })
    payload["quality"].update({
        "symbol_count": len(symbols),
        "from_to_connection_count": payload["from_to"]["connection_count"],
        "symbol_label_ocr": {
            "engine": engine, "count": sum(1 for s in symbols if s.get("label")) if text_result else 0,
            "llm_reread_count": sum(1 for s in symbols if s.get("label_source") == "llm"),
            "llm_text_reread_count": sum(1 for t in text_result.get("texts", []) if t.get("source") == "llm"),
            "name_ocr_reread_count": sum(1 for t in text_result.get("texts", []) if t.get("source") == "name_ocr"),
        },
        "text_zone_suppressed_count": len(symbols) - len(wired_symbols),
        "circle_review": circle_review,
        "faint_wire_count": faint_wire_count,
        "terminal_source": "inferred", "annotation_assistance": False,
    })
