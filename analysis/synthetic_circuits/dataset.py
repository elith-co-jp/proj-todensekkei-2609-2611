"""Generate reproducible sequence-circuit drawings and score their PDF round trip.

The circuit project is the source of truth.  PNG/PDF, YOLO labels, the importable
bundle and the LTspice/ngspice netlist are adapters derived from that one model.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from services.circuit_model import build_circuit, export_netlist
from services.pdf_service import render_pdf_pages

SCHEMA_VERSION = "todensekkei.synthetic_circuit_dataset.v1"
CLASS_SPECS = (
    ("relay_coil", "リレーコイル", "#0055a4"),
    ("contact_a", "a接点（メーク）", "#00498e"),
    ("contact_b", "b接点（ブレーク）", "#4a88c1"),
    ("terminal", "端子", "#3f9067"),
    ("junction", "分岐点", "#2f7352"),
    ("diode", "ダイオード", "#b45309"),
    ("solenoid", "電磁弁・ソレノイド", "#c2410c"),
    ("connector", "コネクタ", "#7c3aed"),
    ("lamp", "表示灯", "#db2777"),
    ("push_button", "押しボタン", "#0891b2"),
    ("power_bus", "電源母線端", "#475569"),
    ("other", "その他", "#84b0d8"),
)
CLASS_INDEX = {key: index for index, (key, _label, _color) in enumerate(CLASS_SPECS)}
CLASS_LABEL = {key: label for key, label, _color in CLASS_SPECS}


@dataclass(frozen=True)
class DatasetConfig:
    count: int = 100
    seed: int = 2609
    width: int = 1755
    height: int = 1241
    dpi: int = 150
    min_rungs: int = 3
    max_rungs: int = 6
    validation_every: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 2_000:
            raise ValueError("count must be between 1 and 2000")
        if self.width < 800 or self.height < 600:
            raise ValueError("drawing size is too small")
        if not 1 <= self.min_rungs <= self.max_rungs <= 12:
            raise ValueError("rungs must satisfy 1 <= min_rungs <= max_rungs <= 12")
        if not 72 <= self.dpi <= 600:
            raise ValueError("dpi must be between 72 and 600")
        if not 2 <= self.validation_every <= 20:
            raise ValueError("validation_every must be between 2 and 20")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _box(cx: float, cy: float, width: float, height: float) -> dict[str, float]:
    return {"x0": cx - width / 2, "y0": cy - height / 2, "x1": cx + width / 2, "y1": cy + height / 2}


def _symbol(
    ref: str,
    class_key: str,
    label: str,
    box: dict[str, float],
    terminal_points: list[tuple[float, float]],
    config: DatasetConfig,
) -> dict[str, Any]:
    width, height = config.width, config.height
    terminals = [
        {
            "ref": f"{ref}-T{index}",
            "name": str(index),
            "tx": round(x / width, 8),
            "ty": round(y / height, 8),
            "origin": "synthetic_source",
        }
        for index, (x, y) in enumerate(terminal_points, 1)
    ]
    return {
        "ref": ref,
        "class_key": class_key,
        "class_label": CLASS_LABEL[class_key],
        "yolo_index": CLASS_INDEX[class_key],
        "label": label,
        "cx": round((box["x0"] + box["x1"]) / 2 / width, 8),
        "cy": round((box["y0"] + box["y1"]) / 2 / height, 8),
        "w": round((box["x1"] - box["x0"]) / width, 8),
        "h": round((box["y1"] - box["y0"]) / height, 8),
        "note": "generated from deterministic circuit code",
        "attributes": {},
        "terminals": terminals,
    }


def _endpoint(symbol: dict[str, Any], side: str) -> str:
    terminals = symbol["terminals"]
    if len(terminals) == 1:
        return terminals[0]["ref"]
    return terminals[0 if side == "top" else -1]["ref"]


def _connect(a: dict[str, Any], a_side: str, b: dict[str, Any], b_side: str, number: int) -> dict[str, Any]:
    return {
        "from_symbol_ref": a["ref"],
        "from_terminal_ref": _endpoint(a, a_side),
        "to_symbol_ref": b["ref"],
        "to_terminal_ref": _endpoint(b, b_side),
        "wire_no": str(600 + number),
        "net_id": None,
        "kind": "wire",
        "external_ref": None,
        "note": "synthetic source edge",
        "origin": "synthetic_source",
    }


def _make_page(index: int, config: DatasetConfig) -> tuple[dict[str, Any], Image.Image, list[dict[str, Any]], list[list[str]]]:
    page_id = f"SYN{index:06d}"
    page_seed = config.seed + index * 1_000_003
    rng = random.Random(page_seed)
    rung_count = rng.randint(config.min_rungs, config.max_rungs)
    width, height = config.width, config.height
    top_y, bottom_y = 145, height - 145
    load_y = round(height * 0.70)
    left, right = 230, width - 230
    xs = [round(left + position * (right - left) / max(1, rung_count - 1)) for position in range(rung_count)]
    if rung_count == 1:
        xs = [width // 2]

    symbols: list[dict[str, Any]] = []
    top_bus = _symbol("SYM-P", "power_bus", "PER00001", _box(110, top_y, 18, 18), [(110, top_y)], config)
    bottom_bus = _symbol(
        "SYM-N", "power_bus", "NER00001", _box(width - 110, bottom_y, 18, 18), [(width - 110, bottom_y)], config
    )
    symbols.append(top_bus)
    top_dots, device_chains, loads, bottom_dots = [], [], [], []
    has_interlock = page_seed % 3 == 0
    for rung, x in enumerate(xs, 1):
        top = _symbol(f"SYM-JT{rung}", "junction", "", _box(x, top_y, 12, 12), [(x, top_y)], config)
        bottom = _symbol(f"SYM-JB{rung}", "junction", "", _box(x, bottom_y, 12, 12), [(x, bottom_y)], config)
        load_key = rng.choice(("relay_coil", "relay_coil", "lamp", "solenoid"))
        device = f"{30 + rung:02d}H{chr(64 + (index + rung) % 20 + 1)}"
        load_label = device if load_key == "relay_coil" else (f"RL{index:03d}-{rung}" if load_key == "lamp" else f"SOL{index:03d}-{rung}")
        contact_ys = [round(height * 0.39)] if not has_interlock or rung % 2 == 0 else [round(height * 0.31), round(height * 0.48)]
        rung_contacts = []
        for contact_index, contact_y in enumerate(contact_ys, 1):
            contact_key = rng.choice(("contact_a", "contact_a", "contact_b"))
            contact_device = device if contact_index == 1 and load_key == "relay_coil" else f"{70 + rung:02d}X{contact_index}"
            contact_label = contact_device if load_key == "relay_coil" else f"PB{index:03d}-{rung}-{contact_index}"
            rung_contacts.append(_symbol(
                f"SYM-C{rung}-{contact_index}", contact_key, contact_label, _box(x, contact_y, 48, 66),
                [(x, contact_y - 33), (x, contact_y + 33)], config,
            ))
        load = _symbol(
            f"SYM-L{rung}", load_key, load_label, _box(x, load_y, 58, 58),
            [(x, load_y - 29), (x, load_y + 29)], config,
        )
        symbols.extend((top, *rung_contacts, load, bottom))
        top_dots.append(top)
        device_chains.append(rung_contacts)
        loads.append(load)
        bottom_dots.append(bottom)
    symbols.append(bottom_bus)

    edges: list[tuple[dict[str, Any], str, dict[str, Any], str]] = []
    top_chain = [top_bus, *top_dots]
    bottom_chain = [*bottom_dots, bottom_bus]
    edges.extend((a, "bottom", b, "top") for a, b in zip(top_chain, top_chain[1:], strict=False))
    edges.extend((a, "bottom", b, "top") for a, b in zip(bottom_chain, bottom_chain[1:], strict=False))
    for top, contacts, load, bottom in zip(top_dots, device_chains, loads, bottom_dots, strict=True):
        chain = [top, *contacts, load, bottom]
        edges.extend((a, "bottom", b, "top") for a, b in zip(chain, chain[1:], strict=False))
    connections = [_connect(a, a_side, b, b_side, number) for number, (a, a_side, b, b_side) in enumerate(edges, 1)]
    expected_edges = [[a["ref"], b["ref"]] for a, _a_side, b, _b_side in edges]

    project = {
        "id": index,
        "name": page_id,
        "sheet_no": page_id,
        "page_no": str(index),
        "revision": "SYN",
        "source_file": f"{page_id}.png",
        "image_width": width,
        "image_height": height,
        "status": "synthetic",
        "assignee": None,
        "note": f"seed={page_seed}",
        "images": [],
        "symbols": symbols,
        "connections": connections,
    }
    image = _render(project, page_seed)
    detections = [
        {
            "class_key": symbol["class_key"],
            "class_label": symbol["class_label"],
            "cx": symbol["cx"],
            "cy": symbol["cy"],
            "w": symbol["w"],
            "h": symbol["h"],
            "confidence": 1.0,
        }
        for symbol in symbols
    ]
    source = {
        "schema_version": SCHEMA_VERSION,
        "page_id": page_id,
        "page_seed": page_seed,
        "project": project,
        "oracle_detections": detections,
        "expected_edges": expected_edges,
        "generation": {
            "rungs": rung_count,
            "layout": "horizontal_buses_vertical_rungs",
            "circuit_family": "series_interlock" if has_interlock else "single_contact_ladder",
            "render_profile": ("clean", "faint_bus", "hairline_bus", "mixed_scan")[page_seed % 4],
        },
    }
    return source, image, detections, expected_edges


def _pixel_box(symbol: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    return (
        round((symbol["cx"] - symbol["w"] / 2) * width),
        round((symbol["cy"] - symbol["h"] / 2) * height),
        round((symbol["cx"] + symbol["w"] / 2) * width),
        round((symbol["cy"] + symbol["h"] / 2) * height),
    )


def _render(project: dict[str, Any], page_seed: int) -> Image.Image:
    width, height = project["image_width"], project["image_height"]
    rng = random.Random(page_seed)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((45, 45, width - 45, height - 45), outline=(185, 185, 185), width=1)
    draw.text((65, 65), f"SEQUENCE CIRCUIT {project['sheet_no']}  SOURCE SEED {page_seed}", fill=(35, 35, 35), font=font)
    by_ref = {symbol["ref"]: symbol for symbol in project["symbols"]}
    render_profile = ("clean", "faint_bus", "hairline_bus", "mixed_scan")[page_seed % 4]

    for connection_index, connection in enumerate(project["connections"], 1):
        a, b = by_ref[connection["from_symbol_ref"]], by_ref[connection["to_symbol_ref"]]
        ta = next(t for t in a["terminals"] if t["ref"] == connection["from_terminal_ref"])
        tb = next(t for t in b["terminals"] if t["ref"] == connection["to_terminal_ref"])
        x1, y1 = round(ta["tx"] * width), round(ta["ty"] * height)
        x2, y2 = round(tb["tx"] * width), round(tb["ty"] * height)
        is_bus = a["class_key"] in {"junction", "power_bus"} and b["class_key"] in {"junction", "power_bus"} and y1 == y2
        if is_bus and render_profile == "faint_bus":
            shade, line_width = 130, 2
        elif is_bus and render_profile == "hairline_bus":
            shade, line_width = 90, 1
        elif render_profile == "mixed_scan" and (page_seed + connection_index) % 5 == 0:
            shade, line_width = rng.choice((70, 100, 130)), 2
        else:
            shade, line_width = 0, rng.choice((2, 2, 3))
        if x1 != x2 and y1 != y2:
            draw.line((x1, y1, x2, y1, x2, y2), fill=(shade, shade, shade), width=line_width)
        else:
            draw.line((x1, y1, x2, y2), fill=(shade, shade, shade), width=line_width)

    for symbol in project["symbols"]:
        x0, y0, x1, y1 = _pixel_box(symbol, width, height)
        cx, cy = round(symbol["cx"] * width), round(symbol["cy"] * height)
        key = symbol["class_key"]
        if key == "junction":
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill="black")
        elif key == "power_bus":
            draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill="black")
            draw.text((cx + 10, cy - 18), symbol["label"], fill="black", font=font)
        elif key in {"relay_coil", "lamp"}:
            draw.ellipse((x0, y0, x1, y1), outline="black", width=2)
            if key == "lamp":
                draw.line((x0 + 9, y0 + 9, x1 - 9, y1 - 9), fill="black", width=2)
                draw.line((x1 - 9, y0 + 9, x0 + 9, y1 - 9), fill="black", width=2)
            else:
                draw.arc((x0 + 8, y0 + 4, x1 - 8, y1 - 4), 85, 275, fill="black", width=2)
                draw.arc((x0 + 14, y0 + 4, x1 - 2, y1 - 4), 85, 275, fill="black", width=2)
            draw.text((x1 + 9, cy - 6), symbol["label"], fill="black", font=font)
        elif key == "solenoid":
            draw.rectangle((x0, y0, x1, y1), outline="black", width=2)
            draw.line((x0 + 8, y1 - 8, x1 - 8, y0 + 8), fill="black", width=2)
            draw.text((x1 + 9, cy - 6), symbol["label"], fill="black", font=font)
        elif key in {"contact_a", "contact_b"}:
            mid = (y0 + y1) // 2
            draw.line((cx, y0, cx, mid - 8), fill="black", width=2)
            draw.line((cx, mid + 8, cx, y1), fill="black", width=2)
            # The production drawings encode contact kind by the side of the contact stroke:
            # right of the terminal circles is an a-contact, left is a b-contact (DD-0023).
            side = 1 if key == "contact_a" else -1
            draw.line((cx, mid - 8, cx + side * 15, mid + 4), fill="black", width=2)
            if key == "contact_b":
                draw.line((cx, mid + 8, cx - 15, mid - 2), fill="black", width=2)
                draw.line((cx - 10, mid - 16, cx - 10, mid - 2), fill="black", width=2)
                draw.line((cx - 10, mid + 2, cx - 10, mid + 16), fill="black", width=2)
            draw.ellipse((cx - 3, mid - 11, cx + 3, mid - 5), fill="white", outline="black", width=2)
            draw.ellipse((cx - 3, mid + 5, cx + 3, mid + 11), fill="white", outline="black", width=2)
            draw.text((x1 + 8, cy - 6), symbol["label"], fill="black", font=font)
    return image


def _pdf_bytes(images: Iterable[Image.Image], dpi: int) -> bytes:
    """Small deterministic PDF writer: one lossless RGB image XObject per page."""
    objects: list[bytes] = [b"", b""]
    page_refs: list[int] = []
    for image in images:
        rgb = image.convert("RGB")
        width, height = rgb.size
        page_no = len(objects) + 1
        image_no, content_no = page_no + 1, page_no + 2
        page_refs.append(page_no)
        page_width, page_height = width * 72 / dpi, height * 72 / dpi
        page = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width:.6f} {page_height:.6f}] "
            f"/Resources << /XObject << /Im{image_no} {image_no} 0 R >> >> /Contents {content_no} 0 R >>"
        ).encode()
        compressed = zlib.compress(rgb.tobytes(), level=9)
        image_object = (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(compressed)} >>\nstream\n"
        ).encode() + compressed + b"\nendstream"
        command = f"q {page_width:.6f} 0 0 {page_height:.6f} 0 0 cm /Im{image_no} Do Q\n".encode()
        content = f"<< /Length {len(command)} >>\nstream\n".encode() + command + b"endstream"
        objects.extend((page, image_object, content))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Count {len(page_refs)} /Kids [{' '.join(f'{ref} 0 R' for ref in page_refs)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _yolo_labels(project: dict[str, Any]) -> str:
    return "".join(
        f"{symbol['yolo_index']} {symbol['cx']:.8f} {symbol['cy']:.8f} {symbol['w']:.8f} {symbol['h']:.8f}\n"
        for symbol in project["symbols"]
    )


def _classes() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "yolo_index": index, "color": color, "sort_order": index, "is_active": True}
        for index, (key, label, color) in enumerate(CLASS_SPECS)
    ]


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes | str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def generate_dataset(output: Path, config: DatasetConfig) -> dict[str, Any]:
    """Generate all adapters from deterministic circuit projects under ``output``."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    for relative in (
        "dataset/images/train", "dataset/images/val", "dataset/labels/train", "dataset/labels/val",
        "sources", "netlists", "pdf",
    ):
        (output / relative).mkdir(parents=True, exist_ok=True)

    pages: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for index in range(1, config.count + 1):
        source, image, _detections, _edges = _make_page(index, config)
        page_id = source["page_id"]
        split = "val" if index % config.validation_every == 0 else "train"
        image_buffer = io.BytesIO()
        image.save(image_buffer, "PNG", optimize=False, compress_level=9)
        image_bytes = image_buffer.getvalue()
        pdf = _pdf_bytes([image], config.dpi)
        project = source["project"]
        project["images"] = [{
            "filename": f"{page_id}.png", "sha256": _sha256(image_bytes),
            "width": config.width, "height": config.height,
        }]
        circuit = build_circuit([project])
        netlist = export_netlist(circuit)
        source["circuit"] = {
            "elements": len(circuit.elements), "nodes": circuit.nodes, "diagnostics": circuit.diagnostics,
            "netlist_sha256": _sha256(netlist.encode()),
        }
        source_bytes = json.dumps(source, ensure_ascii=False, indent=2, sort_keys=True).encode()
        labels = _yolo_labels(project).encode()
        netlist_bytes = netlist.encode()
        (output / "dataset" / "images" / split / f"{page_id}.png").write_bytes(image_bytes)
        (output / "dataset" / "labels" / split / f"{page_id}.txt").write_bytes(labels)
        (output / "sources" / f"{page_id}.json").write_bytes(source_bytes)
        (output / "netlists" / f"{page_id}.cir").write_bytes(netlist_bytes)
        (output / "pdf" / f"{page_id}.pdf").write_bytes(pdf)
        digest.update(source_bytes)
        digest.update(image_bytes)
        digest.update(pdf)
        digest.update(labels)
        digest.update(netlist_bytes)
        pages.append({
            "id": page_id, "seed": source["page_seed"], "split": split, "rungs": source["generation"]["rungs"],
            "circuit_family": source["generation"]["circuit_family"],
            "render_profile": source["generation"]["render_profile"],
            "symbols": len(project["symbols"]), "connections": len(project["connections"]),
            "image_sha256": _sha256(image_bytes), "source_sha256": _sha256(source_bytes), "pdf_sha256": _sha256(pdf),
            "labels_sha256": _sha256(labels), "netlist_sha256": _sha256(netlist_bytes),
        })
        projects.append(project)

    classes_text = "".join(f"{key}\n" for key, _label, _color in CLASS_SPECS)
    data_yaml = (
        "train: dataset/images/train\nval: dataset/images/val\n"
        f"nc: {len(CLASS_SPECS)}\nnames:\n" +
        "".join(f"  {index}: {key}\n" for index, (key, _label, _color) in enumerate(CLASS_SPECS))
    )
    classes_bytes, data_yaml_bytes = classes_text.encode(), data_yaml.encode()
    (output / "classes.txt").write_bytes(classes_bytes)
    (output / "data.yaml").write_bytes(data_yaml_bytes)
    def generated_images() -> Iterable[Image.Image]:
        for page in pages:
            with Image.open(output / "dataset/images" / page["split"] / f"{page['id']}.png") as opened:
                yield opened.convert("RGB")

    combined_pdf = _pdf_bytes(generated_images(), config.dpi)
    (output / "dataset.pdf").write_bytes(combined_pdf)
    bundle = {
        "schema_version": "1.0", "tool": "seq-annotator-synthetic", "exported_at": "1980-01-01T00:00:00+00:00",
        "classes": _classes(), "projects": projects,
    }
    with zipfile.ZipFile(output / "synthetic_bundle.zip", "w") as archive:
        _zip_write(archive, "bundle.json", json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True))
        _zip_write(archive, "classes.txt", classes_text)
        _zip_write(archive, "data.yaml", data_yaml)
        for page in pages:
            page_id = page["id"]
            split = page["split"]
            _zip_write(
                archive, f"dataset/images/{split}/{page_id}.png",
                (output / "dataset/images" / split / f"{page_id}.png").read_bytes(),
            )
            _zip_write(
                archive, f"dataset/labels/{split}/{page_id}.txt",
                (output / "dataset/labels" / split / f"{page_id}.txt").read_bytes(),
            )

    bundle_bytes = (output / "synthetic_bundle.zip").read_bytes()

    summary = {
        "schema_version": SCHEMA_VERSION,
        "config": asdict(config),
        "page_count": len(pages),
        "dataset_sha256": digest.hexdigest(),
        "combined_pdf_sha256": _sha256(combined_pdf),
        "artifacts": {
            "classes_sha256": _sha256(classes_bytes),
            "data_yaml_sha256": _sha256(data_yaml_bytes),
            "combined_pdf_sha256": _sha256(combined_pdf),
            "synthetic_bundle_sha256": _sha256(bundle_bytes),
        },
        "pages": pages,
    }
    (output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _bbox_iou(a: dict[str, float], b: dict[str, float]) -> float:
    width = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    height = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    intersection = width * height
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - intersection
    return intersection / union if union > 0 else 0.0


def _match_oracle_symbols(source: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Match exact oracle boxes without relying on structure list order or generated IDs."""
    width = source["project"]["image_width"]
    height = source["project"]["image_height"]
    remaining = {item["id"]: item for item in result.get("symbols", [])}
    source_to_result: dict[str, str] = {}
    result_to_source: dict[str, dict[str, Any]] = {}
    for symbol in source["project"]["symbols"]:
        box = dict(zip(("x0", "y0", "x1", "y1"), _pixel_box(symbol, width, height), strict=True))
        candidates = [item for item in remaining.values() if _bbox_iou(box, item["bbox"]) >= 0.95]
        if len(candidates) != 1:
            raise ValueError(f"expected one structure symbol for {symbol['ref']}, got {len(candidates)}")
        matched = candidates[0]
        source_to_result[symbol["ref"]] = matched["id"]
        result_to_source[matched["id"]] = symbol
        remaining.pop(matched["id"])
    return source_to_result, result_to_source


def _match_model_symbols(source: dict[str, Any], result: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """IoU matching for detector output; oracle evaluation uses the stricter identity matcher above."""
    width = source["project"]["image_width"]
    height = source["project"]["image_height"]
    gold = source["project"]["symbols"]
    predicted = result.get("symbols", [])
    pairs = sorted(
        (
            (_bbox_iou(dict(zip(("x0", "y0", "x1", "y1"), _pixel_box(item, width, height), strict=True)), symbol["bbox"]),
             gold_index, pred_index)
            for gold_index, item in enumerate(gold)
            for pred_index, symbol in enumerate(predicted)
        ),
        reverse=True,
    )
    source_to_result: dict[str, str] = {}
    result_to_source: dict[str, dict[str, Any]] = {}
    used_gold, used_pred = set(), set()
    for overlap, gold_index, pred_index in pairs:
        if overlap < 0.5:
            break
        if gold_index in used_gold or pred_index in used_pred:
            continue
        used_gold.add(gold_index)
        used_pred.add(pred_index)
        source_symbol, result_symbol = gold[gold_index], predicted[pred_index]
        source_to_result[source_symbol["ref"]] = result_symbol["id"]
        result_to_source[result_symbol["id"]] = source_symbol
    return source_to_result, result_to_source


def _predict_detections(model: Any, image_path: Path, confidence: float) -> list[dict[str, Any]]:
    from types import SimpleNamespace

    from services.inference_service import _detect

    classes_by_index = {
        index: SimpleNamespace(id=index, key=key, label=label)
        for index, (key, label, _color) in enumerate(CLASS_SPECS)
    }
    return _detect(model, str(image_path), confidence, None, classes_by_index)


def _gold_terminal_edges(project: dict[str, Any]) -> set[frozenset[str]]:
    return {
        frozenset((f"{item['from_symbol_ref']}:{item['from_terminal_ref']}", f"{item['to_symbol_ref']}:{item['to_terminal_ref']}"))
        for item in project["connections"]
    }


def _predicted_edges(
    result: dict[str, Any], result_to_source: dict[str, dict[str, Any]], width: int, height: int,
) -> tuple[set[frozenset[str]], set[frozenset[str]], dict[tuple[str, str], str]]:
    endpoint_map: dict[tuple[str, str], str] = {}
    for result_symbol in result.get("symbols", []):
        source_symbol = result_to_source.get(result_symbol["id"])
        if source_symbol is None:
            continue
        source_terminals = source_symbol.get("terminals") or []
        result_terminals = [item for item in result_symbol.get("terminals") or [] if item.get("point")]
        terminal_gate = max(8.0, 0.25 * max(source_symbol["w"] * width, source_symbol["h"] * height))
        if len(source_terminals) == 1:
            source_terminal = source_terminals[0]
            for result_terminal in result_terminals:
                distance_squared = (
                    (source_terminal["tx"] * width - result_terminal["point"][0]) ** 2
                    + (source_terminal["ty"] * height - result_terminal["point"][1]) ** 2
                )
                if distance_squared > terminal_gate**2:
                    continue
                endpoint_map[(result_symbol["id"], result_terminal["ref"])] = (
                    f"{source_symbol['ref']}:{source_terminal['ref']}"
                )
            continue
        pairs = sorted(
            (
                (
                    (source_terminal["tx"] * width - result_terminal["point"][0]) ** 2
                    + (source_terminal["ty"] * height - result_terminal["point"][1]) ** 2,
                    source_index,
                    result_index,
                )
                for source_index, source_terminal in enumerate(source_terminals)
                for result_index, result_terminal in enumerate(result_terminals)
            ),
        )
        used_source, used_result = set(), set()
        for distance_squared, source_index, result_index in pairs:
            if distance_squared > terminal_gate**2:
                break
            if source_index in used_source or result_index in used_result:
                continue
            used_source.add(source_index)
            used_result.add(result_index)
            source_terminal = source_terminals[source_index]
            result_terminal = result_terminals[result_index]
            endpoint_map[(result_symbol["id"], result_terminal["ref"])] = (
                f"{source_symbol['ref']}:{source_terminal['ref']}"
            )

    symbol_edges: set[frozenset[str]] = set()
    terminal_edges: set[frozenset[str]] = set()
    for connection in (result.get("from_to") or {}).get("connections") or []:
        left, right = connection["from"], connection["to"]
        if left.get("symbol_id") == right.get("symbol_id"):
            continue
        source_left = result_to_source.get(left.get("symbol_id"))
        source_right = result_to_source.get(right.get("symbol_id"))
        if source_left and source_right:
            symbol_edges.add(frozenset((source_left["ref"], source_right["ref"])))
        a = endpoint_map.get((left.get("symbol_id"), left.get("terminal_ref")))
        b = endpoint_map.get((right.get("symbol_id"), right.get("terminal_ref")))
        if a and b and a != b:
            terminal_edges.add(frozenset((a, b)))
    return symbol_edges, terminal_edges, endpoint_map


def _project_from_analysis(
    source_project: dict[str, Any],
    result: dict[str, Any],
    result_to_source: dict[str, dict[str, Any]],
    endpoint_map: dict[tuple[str, str], str],
    edges: set[frozenset[str]],
) -> dict[str, Any]:
    """Build a circuit project from parsed PDF output, using gold only to align identities for scoring."""
    width, height = source_project["image_width"], source_project["image_height"]
    symbols = []
    for result_symbol in result.get("symbols", []):
        source_symbol = result_to_source.get(result_symbol["id"])
        symbol_ref = source_symbol["ref"] if source_symbol is not None else f"ANALYSIS-{result_symbol['id']}"
        box = result_symbol["bbox"]
        terminals = []
        terminal_refs = set()
        for terminal in result_symbol.get("terminals") or []:
            point = terminal.get("point")
            if point is None:
                continue
            canonical = endpoint_map.get((result_symbol["id"], terminal["ref"]))
            if source_symbol is not None and canonical is None:
                # Port inference may expose additional candidates on symbol strokes.  Only the
                # one-to-one terminals selected for this matched symbol participate in its circuit element.
                continue
            terminal_ref = canonical.split(":", 1)[1] if canonical else f"ANALYSIS-{terminal['ref']}"
            if terminal_ref in terminal_refs:
                continue
            terminal_refs.add(terminal_ref)
            terminals.append({
                "ref": terminal_ref,
                "name": terminal.get("name"),
                "tx": round(point[0] / width, 8),
                "ty": round(point[1] / height, 8),
                "origin": "synthetic_analysis",
            })
        class_key = result_symbol["class_name"]
        symbols.append({
            "ref": symbol_ref,
            "class_key": class_key,
            "class_label": result_symbol.get("class_label") or CLASS_LABEL.get(class_key, class_key),
            "yolo_index": CLASS_INDEX.get(class_key, CLASS_INDEX["other"]),
            "label": result_symbol.get("label") or "",
            "cx": round((box["x0"] + box["x1"]) / 2 / width, 8),
            "cy": round((box["y0"] + box["y1"]) / 2 / height, 8),
            "w": round((box["x1"] - box["x0"]) / width, 8),
            "h": round((box["y1"] - box["y0"]) / height, 8),
            "note": "reconstructed from PDF analysis",
            "attributes": {},
            "terminals": terminals,
        })
    project = {**source_project, "symbols": symbols, "connections": []}
    for index, edge in enumerate(sorted(sorted(item) for item in edges), 1):
        left, right = edge
        left_symbol, left_terminal = left.split(":", 1)
        right_symbol, right_terminal = right.split(":", 1)
        project["connections"].append({
            "from_symbol_ref": left_symbol, "from_terminal_ref": left_terminal,
            "to_symbol_ref": right_symbol, "to_terminal_ref": right_terminal,
            "wire_no": str(900_000 + index), "net_id": None, "kind": "wire",
            "external_ref": None, "note": "reconstructed from PDF analysis", "origin": "synthetic_evaluation",
        })
    return project


def _circuit_partition(circuit: Any) -> set[frozenset[str]]:
    by_node: dict[str, set[str]] = {}
    for (_project_id, symbol_ref, terminal_ref), node in circuit.node_of.items():
        by_node.setdefault(node, set()).add(f"{symbol_ref}:{terminal_ref}")
    return {frozenset(endpoints) for endpoints in by_node.values()}


def _semantic_signature(project: dict[str, Any], circuit: Any, *, include_labels: bool) -> tuple[Any, ...]:
    symbols = frozenset(
        (
            symbol["ref"],
            symbol["class_key"],
            (symbol.get("label") or "").strip().upper() if include_labels else None,
            tuple(sorted(terminal["ref"] for terminal in symbol.get("terminals") or [])),
        )
        for symbol in project["symbols"]
    )
    elements = frozenset(
        (
            element.symbol_ref,
            element.kind,
            (element.label or "").strip().upper() if include_labels else None,
        )
        for element in circuit.elements
    )
    return frozenset(_circuit_partition(circuit)), symbols, elements


def _round_trip_metrics(original: Image.Image, rendered: Image.Image) -> tuple[float, float]:
    if rendered.size != original.size:
        rendered = rendered.resize(original.size)
    a = np.asarray(original.convert("L"), dtype=np.int16)
    b = np.asarray(rendered.convert("L"), dtype=np.int16)
    mean_abs = float(np.mean(np.abs(a - b)))
    ink = a < 200
    dark_recall = float(np.count_nonzero(ink & (b < 220)) / max(1, np.count_nonzero(ink)))
    return mean_abs, dark_recall


def evaluate_dataset(
    root: Path, *, disable_ocr: bool | None = None, model_path: Path | None = None, confidence: float = 0.5,
) -> dict[str, Any]:
    """Render PDFs, run production analysis, and compare symbols, terminals and circuit semantics."""
    from analysis import runtime

    detector = None
    if model_path is not None:
        from ultralytics import YOLO

        detector = YOLO(str(model_path))
    if disable_ocr is None:
        disable_ocr = model_path is None

    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    analysis_dir = root / "analysis"
    inputs_dir = analysis_dir / "pdf_pages"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    symbol_totals = {"gold": 0, "predicted": 0, "hit": 0}
    terminal_totals = {"gold": 0, "predicted": 0, "hit": 0}
    error_detail: dict[str, str] = {}
    source_errors = 0
    circuit_mismatches = 0
    detection_totals = {"gold": 0, "predicted": 0, "matched": 0, "correct_class": 0}
    label_totals = {"gold": 0, "exact": 0}
    ocr_statuses: Counter[str] = Counter()
    ocr_engines: Counter[str] = Counter()
    circle_gold: Counter[str] = Counter()
    circle_before: Counter[str] = Counter()
    circle_after: Counter[str] = Counter()
    circle_review_required = 0
    circle_matched = 0
    pixel_differences, dark_recalls = [], []
    rows = []
    original_loader = runtime.load_symbol_ocr_reader
    if disable_ocr:
        runtime.load_symbol_ocr_reader = lambda *_args, **_kwargs: (None, "none", "disabled for structural evaluation")
    try:
        for page in manifest["pages"]:
            page_id = page["id"]
            # A failed page remains in the denominator; otherwise a run can report 100% by dropping hard pages.
            symbol_totals["gold"] += page["connections"]
            terminal_totals["gold"] += page["connections"]
            detection_totals["gold"] += page["symbols"]
            try:
                source = json.loads((root / "sources" / f"{page_id}.json").read_text(encoding="utf-8"))
                circle_gold.update(
                    symbol["class_key"] for symbol in source["project"]["symbols"]
                    if symbol["class_key"] in {"relay_coil", "lamp"}
                )
                rendered_pages = render_pdf_pages((root / "pdf" / f"{page_id}.pdf").read_bytes(), dpi=manifest["config"]["dpi"])
                if len(rendered_pages) != 1:
                    raise ValueError(f"expected one PDF page, got {len(rendered_pages)}")
                input_path = inputs_dir / f"{page_id}.png"
                input_path.write_bytes(rendered_pages[0])
                with Image.open(root / "dataset/images" / page["split"] / f"{page_id}.png") as opened:
                    original = opened.convert("RGB")
                with Image.open(input_path) as opened:
                    rendered = opened.convert("RGB")
                mean_abs, dark_recall = _round_trip_metrics(original, rendered)
                pixel_differences.append(mean_abs)
                dark_recalls.append(dark_recall)
                detections = (
                    _predict_detections(detector, input_path, confidence)
                    if detector is not None else source["oracle_detections"]
                )
                result = runtime.analyze_detections(
                    input_path, detections, source={"synthetic_page": page_id, "seed": source["page_seed"]}
                )
                ocr_statuses.update([
                    "disabled" if disable_ocr
                    else str((result.get("capabilities") or {}).get("label_ocr") or "unknown")
                ])
                ocr_engines.update([
                    str((((result.get("quality") or {}).get("symbol_label_ocr") or {}).get("engine")) or "none")
                ])
                (analysis_dir / f"{page_id}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                source_to_result, result_to_source = (
                    _match_model_symbols(source, result) if detector is not None else _match_oracle_symbols(source, result)
                )
                result_symbols = {item["id"]: item for item in result.get("symbols", [])}
                source_symbols = {item["ref"]: item for item in source["project"]["symbols"]}
                detection_totals["predicted"] += len(result_symbols)
                detection_totals["matched"] += len(source_to_result)
                detection_totals["correct_class"] += sum(
                    source_symbols[source_ref]["class_key"] == result_symbols[result_id]["class_name"]
                    for source_ref, result_id in source_to_result.items()
                )
                if not disable_ocr:
                    labeled_sources = {
                        source_ref: symbol
                        for source_ref, symbol in source_symbols.items()
                        if (symbol.get("label") or "").strip()
                    }
                    label_totals["gold"] += len(labeled_sources)
                    label_totals["exact"] += sum(
                        (source_symbol.get("label") or "").strip().upper()
                        == (result_symbols[source_to_result[source_ref]].get("label") or "").strip().upper()
                        for source_ref, source_symbol in labeled_sources.items()
                        if source_ref in source_to_result
                    )
                for source_ref, result_id in source_to_result.items():
                    gold_class = source_symbols[source_ref]["class_key"]
                    if gold_class not in {"relay_coil", "lamp"}:
                        continue
                    circle_matched += 1
                    found = result_symbols[result_id]
                    detected_class = found.get("detected_class", found["class_name"])
                    final_class = found["class_name"]
                    if detected_class != gold_class:
                        circle_before[f"{gold_class}->{detected_class}"] += 1
                    if final_class != gold_class:
                        circle_after[f"{gold_class}->{final_class}"] += 1
                    circle_review_required += int((found.get("symbol_review") or {}).get("status") == "要確認")
                gold_symbols = {frozenset(edge) for edge in source["expected_edges"]}
                gold_terminals = _gold_terminal_edges(source["project"])
                predicted_symbols, predicted_terminals, endpoint_map = _predicted_edges(
                    result, result_to_source, source["project"]["image_width"], source["project"]["image_height"]
                )
                symbol_hit = gold_symbols & predicted_symbols
                terminal_hit = gold_terminals & predicted_terminals
                source_circuit = build_circuit([source["project"]])
                source_netlist = export_netlist(source_circuit)
                expected_netlist = (root / "netlists" / f"{page_id}.cir").read_text(encoding="utf-8")
                predicted_project = _project_from_analysis(
                    source["project"], result, result_to_source, endpoint_map, predicted_terminals,
                )
                predicted_circuit = build_circuit([predicted_project])
                predicted_netlist = export_netlist(predicted_circuit)
                (analysis_dir / f"{page_id}.cir").write_text(predicted_netlist, encoding="utf-8")
                hard_kinds = {"bus_conflict", "bus_unknown", "no_supply", "terminals_missing", "wire_without_terminal"}
                predicted_hard_diagnostics = {
                    item["kind"] for item in predicted_circuit.diagnostics if item.get("kind") in hard_kinds
                }
                circuit_match = (
                    _semantic_signature(source["project"], source_circuit, include_labels=not disable_ocr)
                    == _semantic_signature(predicted_project, predicted_circuit, include_labels=not disable_ocr)
                    and not predicted_hard_diagnostics
                )
                circuit_mismatches += int(not circuit_match)
                hard_diagnostics = {
                    item["kind"] for item in source_circuit.diagnostics
                    if item.get("kind") in hard_kinds
                }
                consistency = int(source_netlist != expected_netlist or bool(hard_diagnostics))
                source_errors += consistency
                symbol_totals["predicted"] += len(predicted_symbols)
                symbol_totals["hit"] += len(symbol_hit)
                terminal_totals["predicted"] += len(predicted_terminals)
                terminal_totals["hit"] += len(terminal_hit)
                rows.append({
                    "page_id": page_id,
                    "symbol_edges": {"gold": len(gold_symbols), "predicted": len(predicted_symbols), "hit": len(symbol_hit)},
                    "terminal_edges": {"gold": len(gold_terminals), "predicted": len(predicted_terminals), "hit": len(terminal_hit)},
                    "missed": [sorted(edge) for edge in sorted(gold_terminals - predicted_terminals, key=lambda item: sorted(item))],
                    "extra": [sorted(edge) for edge in sorted(predicted_terminals - gold_terminals, key=lambda item: sorted(item))],
                    "pdf_mean_absolute_difference": round(mean_abs, 6), "pdf_dark_pixel_recall": round(dark_recall, 6),
                    "source_consistent": consistency == 0, "circuit_semantic_match": circuit_match,
                    "predicted_hard_diagnostics": sorted(predicted_hard_diagnostics),
                    "predicted_netlist_sha256": _sha256(predicted_netlist.encode()),
                })
            except Exception as exc:  # noqa: BLE001 - one generated page must not hide the rest of the audit
                error_detail[page_id] = f"{type(exc).__name__}: {exc}"
    finally:
        runtime.load_symbol_ocr_reader = original_loader
    def scores(totals: dict[str, int]) -> dict[str, int | float]:
        recall = totals["hit"] / totals["gold"] if totals["gold"] else 0.0
        precision = totals["hit"] / totals["predicted"] if totals["predicted"] else 0.0
        return {**totals, "recall": recall, "precision": precision}

    detection_recall = detection_totals["matched"] / detection_totals["gold"] if detection_totals["gold"] else 0.0
    detection_precision = (
        detection_totals["matched"] / detection_totals["predicted"] if detection_totals["predicted"] else 0.0
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_mode": "model_production_logic_explicit_config" if detector is not None else "oracle_symbols_structural",
        "ocr_requested": not disable_ocr,
        "ocr_runtime": {
            "status_counts": dict(sorted(ocr_statuses.items())),
            "engine_counts": dict(sorted(ocr_engines.items())),
        },
        "model_path": str(model_path) if model_path is not None else None,
        "confidence": confidence if detector is not None else None,
        "page_count": len(manifest["pages"]),
        "errors": len(error_detail),
        "error_detail": error_detail,
        "source_consistency_errors": source_errors,
        "circuit_semantic_mismatches": circuit_mismatches + len(error_detail),
        "circuit_semantics": {
            "gold": len(manifest["pages"]),
            "evaluated": len(manifest["pages"]) - len(error_detail),
            "matched": len(manifest["pages"]) - len(error_detail) - circuit_mismatches,
            "recall": (
                (len(manifest["pages"]) - len(error_detail) - circuit_mismatches) / len(manifest["pages"])
                if manifest["pages"] else 0.0
            ),
        },
        "connections": scores(terminal_totals),
        "symbol_connections": scores(symbol_totals),
        "symbol_detection": {
            **detection_totals,
            "recall": detection_recall,
            "precision": detection_precision,
            "f1": (
                2 * detection_precision * detection_recall / (detection_precision + detection_recall)
                if detection_precision + detection_recall else 0.0
            ),
            "class_accuracy": (
                detection_totals["correct_class"] / detection_totals["matched"] if detection_totals["matched"] else 0.0
            ),
        },
        "label_ocr": {
            **label_totals,
            "accuracy": label_totals["exact"] / label_totals["gold"] if label_totals["gold"] else None,
        },
        "circle_classification": {
            "gold": {class_name: circle_gold[class_name] for class_name in ("relay_coil", "lamp")},
            "matched": circle_matched,
            "before": {"errors": sum(circle_before.values()), "confusion": dict(sorted(circle_before.items()))},
            "after": {"errors": sum(circle_after.values()), "confusion": dict(sorted(circle_after.items()))},
            "review_required": circle_review_required,
            "both_classes_scored": all(circle_gold[class_name] > 0 for class_name in ("relay_coil", "lamp")),
        },
        "pdf_round_trip": {
            "mean_absolute_difference": sum(pixel_differences) / max(1, len(pixel_differences)),
            "dark_pixel_recall": min(dark_recalls, default=0.0),
        },
        "rows": rows,
    }
    (root / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
