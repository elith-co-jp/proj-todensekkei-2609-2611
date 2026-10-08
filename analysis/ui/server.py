from __future__ import annotations

import email
import email.policy
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
JOB_ROOT = ROOT_DIR / "private" / "results" / "analysis_ui" / "jobs"
RUN_SCRIPT = ROOT_DIR / "analysis" / "tools" / "run_yolo_e2e_pipeline.py"
DEFAULT_GPU_PYTHON = ROOT_DIR / "private" / "envs" / "conda_envs" / "yolo-gpu" / "bin" / "python"
WIRE_ANALYSIS_DPI = 150
WIRE_TEXT_MASK = "components"
ALLOWED_PDF_EXTENSIONS = {".pdf"}
ALLOWED_ZIP_EXTENSIONS = {".zip"}
ALLOWED_MODEL_EXTENSIONS = {".pt", ".pth"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_processes: dict[str, subprocess.Popen[str]] = {}
_process_lock = threading.Lock()


@dataclass
class FormField:
    value: str | None = None
    filename: str | None = None
    data: bytes | None = None


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
        raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
    return JOB_ROOT / job_id


def state_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def read_state(job_id: str) -> dict[str, Any]:
    state = read_json(state_path(job_id), None)
    if state is None:
        raise RequestError(HTTPStatus.NOT_FOUND, "job not found")
    return state


def update_state(job_id: str, **changes: Any) -> dict[str, Any]:
    state = read_state(job_id)
    state.update(changes)
    state["updated_at"] = utc_now()
    write_json(state_path(job_id), state)
    return state


def tail_text(path: Path, max_bytes: int = 80_000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def safe_filename(name: str, fallback: str) -> str:
    path = Path(name) if name else Path(fallback)
    suffix = path.suffix.lower() or Path(fallback).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_-")
    if not stem:
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(fallback).stem).strip("_-") or "input"
    return f"{stem}{suffix}"


def ensure_allowed_extension(path: Path, allowed: set[str], label: str) -> None:
    if path.suffix.lower() not in allowed:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{label} must be one of: {', '.join(sorted(allowed))}")


def resolve_local_path(value: str | None, allowed: set[str], label: str, required: bool) -> Path | None:
    if not value or not value.strip():
        if required:
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{label} is required")
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    ensure_allowed_extension(path, allowed, label)
    if not path.exists() or not path.is_file():
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{label} not found: {path}")
    return path


def save_upload(field: FormField | None, destination_dir: Path, allowed: set[str], label: str) -> Path | None:
    if field is None or not field.filename:
        return None
    filename = safe_filename(field.filename, f"input{next(iter(allowed))}")
    destination = destination_dir / filename
    ensure_allowed_extension(destination, allowed, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        output.write(field.data or b"")
    if destination.stat().st_size == 0:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{label} is empty")
    return destination


def resolve_input(
    *,
    form: dict[str, list[FormField]],
    local_name: str,
    upload_name: str,
    destination_dir: Path,
    allowed_extensions: set[str],
    label: str,
    required: bool,
) -> Path | None:
    upload = save_upload(form[upload_name][0], destination_dir, allowed_extensions, label) if upload_name in form else None
    if upload is not None:
        return upload
    return resolve_local_path(form_value(form, local_name), allowed_extensions, label, required)


def split_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in re.split(r"[\s,]+", value.strip()) if part]


def parse_page_sheet_map(value: str | None) -> list[str]:
    items = split_values(value)
    for item in items:
        if "=" not in item:
            raise RequestError(HTTPStatus.BAD_REQUEST, f"page-sheet map must be PAGE=SHEET: {item}")
    return items


def form_value(form: dict[str, list[FormField]], name: str, default: str | None = None) -> str | None:
    if name not in form:
        return default
    value = form[name][0].value
    return value if value is not None else default


def form_bool(form: dict[str, list[FormField]], name: str, default: bool = False) -> bool:
    value = form_value(form, name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "on", "yes"}


def parse_header_params(value: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return "", {}
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        raw_value = raw_value.strip()
        if raw_value.startswith('"') and raw_value.endswith('"'):
            raw_value = raw_value[1:-1].replace('\\"', '"')
        params[key.strip().lower()] = raw_value
    return parts[0].lower(), params


def parse_multipart_form(content_type: str, body: bytes) -> dict[str, list[FormField]]:
    media_type, params = parse_header_params(content_type)
    if media_type != "multipart/form-data" or not params.get("boundary"):
        raise RequestError(HTTPStatus.BAD_REQUEST, "multipart/form-data is required")

    # email パーサに委譲して、コンテンツ中に偶然現れる境界文字列を区切りと誤認しないようにする
    envelope = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    message = email.message_from_bytes(envelope, policy=email.policy.default)

    form: dict[str, list[FormField]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        field = (
            FormField(filename=filename, data=data)
            if filename is not None
            else FormField(value=data.decode("utf-8", errors="replace"))
        )
        form.setdefault(name, []).append(field)
    return form


def discover_models() -> list[dict[str, Any]]:
    search_roots = [
        ROOT_DIR / "private" / "training" / "yolo",
        ROOT_DIR / "analysis" / "weights",
    ]
    candidates = []
    seen: set[Path] = set()
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.pt"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                stat = path.stat()
            except OSError:
                continue
            candidates.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size_mb": round(stat.st_size / 1024 / 1024, 2),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                }
            )
    return sorted(candidates, key=lambda item: item["modified_at"], reverse=True)[:30]


def list_job_states() -> list[dict[str, Any]]:
    if not JOB_ROOT.exists():
        return []
    states = []
    for path in JOB_ROOT.glob("*/job.json"):
        state = read_json(path, None)
        if isinstance(state, dict):
            states.append(state)
    return sorted(states, key=lambda item: item.get("created_at", ""), reverse=True)


def make_file_url(job_id: str, path_value: str | None) -> str | None:
    if not path_value:
        return None
    base = job_dir(job_id).resolve()
    try:
        relative = Path(path_value).resolve().relative_to(base)
    except ValueError:
        return None
    return f"/api/jobs/{job_id}/files/{quote(relative.as_posix())}"


def collect_outputs(job_id: str) -> dict[str, Any]:
    state = read_state(job_id)
    output_dir = Path(state["output_dir"])
    summary_path = output_dir / "e2e_summary.json"
    summary = read_json(summary_path, None)
    if not isinstance(summary, dict):
        return {"summary": None, "pages": [], "archive_url": f"/api/jobs/{job_id}/archive"}

    pages = []
    for page in summary.get("page_summaries", []):
        outputs = page.get("outputs") or {}
        pages.append(
            {
                "page": page.get("page"),
                "quality": page.get("quality") or {},
                "review_url": make_file_url(job_id, outputs.get("review")),
                "wire_overlay_url": make_file_url(job_id, outputs.get("wire_overlay")),
                "final_json_url": make_file_url(job_id, outputs.get("final_json")),
                "from_to_url": make_file_url(job_id, outputs.get("from_to")),
                "external_references_url": make_file_url(job_id, outputs.get("external_references")),
            }
        )
    return {
        "summary": {
            "schema_version": summary.get("schema_version"),
            "status": summary.get("status"),
            "pages": summary.get("pages", []),
            "model": summary.get("model"),
            "annotation_zip": summary.get("annotation_zip"),
        },
        "pages": sorted(pages, key=lambda item: int(item.get("page") or 0)),
        "structure_json_url": make_file_url(job_id, (summary.get("outputs") or {}).get("structure_json")),
        "summary_json_url": make_file_url(job_id, str(summary_path)),
        "archive_url": f"/api/jobs/{job_id}/archive",
    }


def demo_pdf_bytes() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length 111 >>\nstream\nBT /F1 24 Tf 80 520 Td (Analysis UI Demo PDF) Tj ET\n2 w 80 430 m 760 430 l S\n80 350 140 50 re S\n360 350 140 50 re S\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref_at = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    )
    return bytes(body)


def demo_review_svg(page: int) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="900" viewBox="0 0 1400 900">
  <rect width="1400" height="900" fill="#f8fafc"/>
  <text x="36" y="48" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#0f172a">page_{page:03d} demo review</text>
  <g transform="translate(40 80)">
    <rect width="620" height="330" fill="white" stroke="#cbd5e1"/>
    <text x="18" y="30" font-size="16" fill="#334155">Original</text>
    <line x1="90" y1="160" x2="510" y2="160" stroke="#0f172a" stroke-width="4"/>
    <rect x="110" y="125" width="92" height="70" fill="none" stroke="#0f172a" stroke-width="3"/>
    <circle cx="365" cy="160" r="13" fill="#0f172a"/>
    <text x="106" y="112" font-size="18" fill="#0f172a">MC-1</text>
    <text x="342" y="130" font-size="18" fill="#0f172a">CN-2</text>
  </g>
  <g transform="translate(740 80)">
    <rect width="620" height="330" fill="white" stroke="#cbd5e1"/>
    <text x="18" y="30" font-size="16" fill="#334155">YOLO detections</text>
    <line x1="90" y1="160" x2="510" y2="160" stroke="#94a3b8" stroke-width="4"/>
    <rect x="100" y="108" width="118" height="96" fill="none" stroke="#2563eb" stroke-width="5"/>
    <rect x="326" y="116" width="92" height="82" fill="none" stroke="#059669" stroke-width="5"/>
    <text x="100" y="96" font-size="16" fill="#2563eb">contact_a</text>
    <text x="326" y="104" font-size="16" fill="#059669">connector</text>
  </g>
  <g transform="translate(40 500)">
    <rect width="620" height="330" fill="white" stroke="#cbd5e1"/>
    <text x="18" y="30" font-size="16" fill="#334155">Wire / label extraction</text>
    <line x1="90" y1="160" x2="510" y2="160" stroke="#dc2626" stroke-width="7"/>
    <circle cx="110" cy="160" r="8" fill="#2563eb"/>
    <circle cx="365" cy="160" r="8" fill="#2563eb"/>
    <text x="104" y="105" font-size="18" fill="#047857">MC-1</text>
    <text x="342" y="116" font-size="18" fill="#047857">CN-2</text>
  </g>
  <g transform="translate(740 500)">
    <rect width="620" height="330" fill="white" stroke="#cbd5e1"/>
    <text x="18" y="30" font-size="16" fill="#334155">Connection JSON preview</text>
    <circle cx="140" cy="160" r="10" fill="#2563eb"/>
    <circle cx="410" cy="160" r="10" fill="#2563eb"/>
    <line x1="140" y1="160" x2="410" y2="160" stroke="#2563eb" stroke-width="4"/>
    <text x="112" y="135" font-size="18" fill="#0f172a">MC-1</text>
    <text x="382" y="135" font-size="18" fill="#0f172a">CN-2</text>
    <text x="112" y="215" font-size="14" fill="#475569">from-to: contact_a.MC-1 → connector.CN-2</text>
  </g>
</svg>
"""


def create_demo_job() -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    directory = job_dir(job_id)
    input_dir = directory / "inputs"
    output_dir = directory / "outputs"
    page_dir = output_dir / "page_001"
    input_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = input_dir / "demo_sequence.pdf"
    model_path = input_dir / "demo_best.pt"
    pdf_path.write_bytes(demo_pdf_bytes())
    model_path.write_text("demo model placeholder\n", encoding="utf-8")

    final_output = {
        "schema_version": "todensekkei.inferred_graph.v1",
        "status": "demo",
        "source": {"kind": "demo", "page": 1, "pdf": str(pdf_path)},
        "image_size": {"width": 1400, "height": 900},
        "symbols": [
            {
                "id": "symbol_001",
                "class_name": "contact_a",
                "label": "MC-1",
                "bbox": {"x0": 140, "y0": 188, "x1": 258, "y1": 284},
                "confidence": 0.94,
            },
            {
                "id": "symbol_002",
                "class_name": "connector",
                "label": "CN-2",
                "bbox": {"x0": 1066, "y0": 196, "x1": 1158, "y1": 278},
                "confidence": 0.91,
            },
        ],
        "wires": [{"id": "wire_001", "orientation": "h", "axis": 240, "span_start": 130, "span_end": 1160}],
        "nodes": [{"id": "node_001", "type": "junction", "x": 420, "y": 240}],
        "nets": [
            {
                "id": "net_0001",
                "wire_ids": ["wire_001"],
                "symbol_ids": ["symbol_001", "symbol_002"],
                "terminal_members": [
                    {"symbol_id": "symbol_001", "terminal_ref": "P1", "wire_id": "wire_001"},
                    {"symbol_id": "symbol_002", "terminal_ref": "P1", "wire_id": "wire_001"},
                ],
            }
        ],
        "quality": {
            "symbol_count": 2,
            "net_count": 1,
            "symbol_wire_link_count": 2,
            "symbol_terminal_link_count": 2,
            "external_reference_count": 0,
        },
        "outputs": {
            "final_json": str(page_dir / "final_output.json"),
            "from_to": str(page_dir / "from_to.json"),
            "external_references": str(page_dir / "external_references.json"),
            "review": str(page_dir / "review.svg"),
        },
    }
    from_to = {
        "schema_version": "todensekkei.from_to.v1",
        "source": final_output["source"],
        "definition": "Direct terminal-to-terminal connections; connected components are not expanded into all pairs.",
        "directed": False,
        "terminal_count": 2,
        "connected_terminal_count": 2,
        "unconnected_terminal_count": 0,
        "connection_count": 1,
        "connections": [
            {
                "id": "from_to_00001",
                "type": "wire",
                "directed": False,
                "from": {"symbol_id": "symbol_001", "class_name": "contact_a", "label": "MC-1", "terminal_ref": "P1"},
                "to": {"symbol_id": "symbol_002", "class_name": "connector", "label": "CN-2", "terminal_ref": "P1"},
                "basis": "first_terminal_reached_by_wire_graph_traversal",
                "status": "predicted",
            }
        ],
    }
    final_output["from_to"] = from_to
    external_references = {
        "schema_version": "todensekkei.external_references.v1",
        "source": final_output["source"],
        "summary": {"enabled": False, "external_reference_count": 0},
        "external_references": [],
        "external_reference_connections": [],
        "symbol_sheet_references": [],
    }
    summary = {
        "schema_version": "todensekkei.yolo_e2e.summary.v1",
        "status": "demo",
        "pdf": str(pdf_path),
        "pages": [1],
        "model": str(model_path),
        "annotation_zip": None,
        "page_sheet_map": {},
        "page_summaries": [{"page": 1, "quality": final_output["quality"], "outputs": final_output["outputs"]}],
        "outputs": {"structure_json": str(output_dir / "structure.json")},
        "notes": ["This is placeholder demo data for UI review. It does not contain real drawings."],
    }
    structure = {
        "schema_version": "todensekkei.document_structure.v1",
        "status": "completed",
        "result_type": "demo",
        "source": {"pdf_name": pdf_path.name, "page_count": 1, "pages": [1]},
        "summary": {"symbol_count": 2, "wire_count": 1, "terminal_count": 2, "from_to_connection_count": 1},
        "pages": [final_output],
    }
    write_json(page_dir / "final_output.json", final_output)
    write_json(page_dir / "from_to.json", from_to)
    write_json(page_dir / "external_references.json", external_references)
    write_json(output_dir / "structure.json", structure)
    write_json(output_dir / "e2e_summary.json", summary)
    (page_dir / "review.svg").write_text(demo_review_svg(1), encoding="utf-8")

    state = {
        "id": job_id,
        "status": "completed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": utc_now(),
        "ended_at": utc_now(),
        "return_code": 0,
        "input": {
            "pdf": str(pdf_path),
            "annotation_zip": None,
            "model": str(model_path),
            "pages": ["1"],
            "python_executable": str(DEFAULT_GPU_PYTHON if DEFAULT_GPU_PYTHON.exists() else Path(sys.executable)),
            "device": "demo",
            "terminal_source": "annotation-or-inferred",
            "text_suppression": "none",
            "symbol_label_ocr_engine": "none",
            "run_ocr": False,
            "external_reference_source": "none",
            "page_sheet_map": [],
            "gold_symbol_classes": [],
            "gold_unmatched_yolo": "keep",
        },
        "output_dir": str(output_dir),
        "command": ["demo-placeholder-job"],
        "outputs": None,
    }
    write_json(state_path(job_id), state)
    update_state(job_id, outputs=collect_outputs(job_id))
    (directory / "job.log").write_text(
        "$ demo-placeholder-job\n\n"
        "Created a placeholder PDF, review image, structure JSON, connection JSON, and output archive target.\n"
        "No confidential drawing data is included in this demo job.\n",
        encoding="utf-8",
    )
    return {"job": read_state(job_id)}


def command_preview(command: list[str]) -> str:
    return " ".join(command)


def run_job(job_id: str, command: list[str], env_updates: dict[str, str]) -> None:
    log_path = job_dir(job_id) / "job.log"
    env = os.environ.copy()
    env.update(env_updates)
    env["PYTHONUNBUFFERED"] = "1"
    update_state(job_id, status="running", started_at=utc_now(), return_code=None)
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"$ {command_preview(command)}\n\n")
            process = subprocess.Popen(
                command,
                cwd=ROOT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            with _process_lock:
                _processes[job_id] = process
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
            return_code = process.wait()
        with _process_lock:
            _processes.pop(job_id, None)
        status = "completed" if return_code == 0 else "failed"
        update_state(
            job_id,
            status=status,
            ended_at=utc_now(),
            return_code=return_code,
            outputs=collect_outputs(job_id) if return_code == 0 else None,
        )
    except Exception as caught:
        with _process_lock:
            _processes.pop(job_id, None)
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(f"\n[analysis-ui] failed: {caught}\n")
        update_state(job_id, status="failed", ended_at=utc_now(), error=str(caught))


def expand_page_numbers(specs: list[str]) -> list[int]:
    pages: set[int] = set()
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if "-" in part:
                    start, end = (int(item) for item in part.split("-", 1))
                    if end < start:
                        raise ValueError
                    pages.update(range(start, end + 1))
                else:
                    pages.add(int(part))
            except ValueError as caught:
                raise RequestError(HTTPStatus.BAD_REQUEST, f"invalid page spec: {part}") from caught
    return sorted(pages)


_PAGE_COUNT_SNIPPET = (
    "import sys\n"
    "try:\n"
    "    import pymupdf\n"
    "except ImportError:\n"
    "    import fitz as pymupdf\n"
    "document = pymupdf.open(sys.argv[1])\n"
    "print(document.page_count)\n"
    "document.close()"
)


def pdf_page_count(pdf: Path) -> int | None:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except ImportError:
            return None
    try:
        with pymupdf.open(pdf) as document:
            return document.page_count
    except Exception:
        return None


def pdf_page_count_via(pdf: Path, python_path: Path) -> int | None:
    try:
        completed = subprocess.run(
            [str(python_path), "-c", _PAGE_COUNT_SNIPPET, str(pdf)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(ROOT_DIR),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def allowed_interpreters() -> set[Path]:
    allowed: set[Path] = set()
    candidates = [Path(sys.executable), DEFAULT_GPU_PYTHON]
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        try:
            allowed.add(candidate.resolve())
        except OSError:
            continue
    return allowed


def create_job(form: dict[str, list[FormField]]) -> dict[str, Any]:
    terminal_source = form_value(form, "terminal_source", "annotation-or-inferred") or "annotation-or-inferred"
    text_suppression = form_value(form, "text_suppression", "none") or "none"
    symbol_label_ocr_engine = form_value(form, "symbol_label_ocr_engine", "rapidocr") or "rapidocr"
    external_reference_source = form_value(form, "external_reference_source", "annotation-other-label") or "annotation-other-label"
    gold_unmatched_yolo = form_value(form, "gold_unmatched_yolo", "keep") or "keep"

    if terminal_source not in {"annotation", "inferred", "annotation-or-inferred"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid terminal source")
    if text_suppression not in {"none", "light", "medium", "heavy", "erase"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid text suppression")
    if symbol_label_ocr_engine not in {"none", "rapidocr", "easyocr"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid OCR engine")
    if external_reference_source not in {"none", "annotation-other-label"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid external reference source")
    if gold_unmatched_yolo not in {"keep", "drop"}:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid gold unmatched policy")

    job_id = str(uuid.uuid4())
    directory = job_dir(job_id)
    input_dir = directory / "inputs"
    output_dir = directory / "outputs"
    analysis_dir = directory / "wire_analysis"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf = resolve_input(
        form=form,
        local_name="pdf_path",
        upload_name="pdf_file",
        destination_dir=input_dir,
        allowed_extensions=ALLOWED_PDF_EXTENSIONS,
        label="PDF",
        required=True,
    )
    annotation_zip = resolve_input(
        form=form,
        local_name="annotation_zip_path",
        upload_name="annotation_zip_file",
        destination_dir=input_dir,
        allowed_extensions=ALLOWED_ZIP_EXTENSIONS,
        label="annotation ZIP",
        required=False,
    )
    model = resolve_input(
        form=form,
        local_name="model_path",
        upload_name="model_file",
        destination_dir=input_dir,
        allowed_extensions=ALLOWED_MODEL_EXTENSIONS,
        label="YOLO model",
        required=True,
    )

    python_path = Path(form_value(form, "python_executable", sys.executable) or sys.executable).expanduser()
    if not python_path.is_absolute():
        python_path = ROOT_DIR / python_path
    if not python_path.exists() or not python_path.is_file():
        raise RequestError(HTTPStatus.BAD_REQUEST, f"Python executable not found: {python_path}")
    try:
        resolved_python = python_path.resolve()
    except OSError as caught:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"Python executable not found: {python_path}") from caught
    if resolved_python not in allowed_interpreters():
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            "python_executable must be the UI interpreter, the configured GPU env, or a Python on PATH",
        )

    page_args = split_values(form_value(form, "pages", "1-5"))
    if not page_args:
        raise RequestError(HTTPStatus.BAD_REQUEST, "pages are required")
    page_numbers = expand_page_numbers(page_args)
    total_pages = pdf_page_count(pdf)
    if total_pages is None:
        total_pages = pdf_page_count_via(pdf, resolved_python)
    if total_pages is not None:
        page_numbers = [page for page in page_numbers if 1 <= page <= total_pages]
        if not page_numbers:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"PDF は {total_pages} ページです。有効なページを指定してください",
            )
        page_args = [str(page) for page in page_numbers]

    run_ocr = form_bool(form, "run_ocr", True)
    device = form_value(form, "device", "cpu") or "cpu"
    command = [
        str(python_path),
        str(RUN_SCRIPT),
        "--pdf",
        str(pdf),
        "--pages",
        *page_args,
        "--out-dir",
        str(output_dir),
        "--analysis-root",
        str(analysis_dir),
        "--dpi",
        str(WIRE_ANALYSIS_DPI),
        "--wire-text-mask",
        WIRE_TEXT_MASK,
        "--skip-ocr",
        "--model",
        str(model),
        "--device",
        device.strip() or "cpu",
        "--terminal-source",
        terminal_source,
        "--text-suppression",
        text_suppression,
        "--symbol-label-ocr-engine",
        symbol_label_ocr_engine if run_ocr else "none",
        "--external-reference-source",
        external_reference_source,
        "--gold-unmatched-yolo",
        gold_unmatched_yolo,
    ]
    if annotation_zip is not None:
        command.extend(["--annotation-zip", str(annotation_zip)])
    page_sheet_items = parse_page_sheet_map(form_value(form, "page_sheet_map"))
    if page_sheet_items:
        command.extend(["--page-sheet-map", *page_sheet_items])
    gold_classes = split_values(form_value(form, "gold_symbol_classes"))
    if gold_classes:
        command.extend(["--gold-symbol-classes", *gold_classes])

    state = {
        "id": job_id,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "started_at": None,
        "ended_at": None,
        "return_code": None,
        "input": {
            "pdf": str(pdf),
            "annotation_zip": str(annotation_zip) if annotation_zip else None,
            "model": str(model),
            "pages": page_args,
            "python_executable": str(python_path),
            "device": device,
            "terminal_source": terminal_source,
            "text_suppression": text_suppression,
            "symbol_label_ocr_engine": symbol_label_ocr_engine,
            "run_ocr": run_ocr,
            "wire_analysis": {
                "analysis_root": str(analysis_dir),
                "dpi": WIRE_ANALYSIS_DPI,
                "text_mask": WIRE_TEXT_MASK,
                "page_ocr": False,
            },
            "external_reference_source": external_reference_source,
            "page_sheet_map": page_sheet_items,
            "gold_symbol_classes": gold_classes,
            "gold_unmatched_yolo": gold_unmatched_yolo,
        },
        "output_dir": str(output_dir),
        "command": command,
        "outputs": None,
    }
    write_json(state_path(job_id), state)
    env_updates = {}
    cuda_visible_devices = form_value(form, "cuda_visible_devices")
    if cuda_visible_devices and cuda_visible_devices.strip():
        env_updates["CUDA_VISIBLE_DEVICES"] = cuda_visible_devices.strip()
    threading.Thread(target=run_job, args=(job_id, command, env_updates), daemon=True).start()
    return {"job": read_state(job_id)}


def defaults() -> dict[str, Any]:
    models = discover_models()
    python_executable = str(DEFAULT_GPU_PYTHON if DEFAULT_GPU_PYTHON.exists() else Path(sys.executable))
    default_model = next((model["path"] for model in models if model["name"] == "best.pt"), models[0]["path"] if models else "")
    return {
        "root": str(ROOT_DIR),
        "job_root": str(JOB_ROOT),
        "python_executable": python_executable,
        "models": models,
        "default_model": default_model,
        "jobs": list_job_states()[:20],
        "run_script": str(RUN_SCRIPT),
    }


class AnalysisUIHandler(BaseHTTPRequestHandler):
    server_version = "TodensekkeiAnalysisUI/0.1"

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[analysis-ui] {self.address_string()} - {format % args}\n")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        try:
            host = (urlparse(origin).hostname or "").lower()
        except ValueError:
            return False
        return host in LOOPBACK_HOSTS

    def handle_request(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if method == "POST" and not self._origin_allowed():
                raise RequestError(HTTPStatus.FORBIDDEN, "許可されていないオリジンです")
            if method == "GET" and path == "/":
                self.send_file(STATIC_DIR / "index.html")
            elif method == "GET" and path.startswith("/static/"):
                self.send_file((STATIC_DIR / unquote(path.removeprefix("/static/"))).resolve(), base=STATIC_DIR)
            elif method == "GET" and path == "/api/defaults":
                self.send_json(defaults())
            elif method == "GET" and path == "/api/jobs":
                self.send_json({"jobs": list_job_states()[:50]})
            elif method == "POST" and path == "/api/jobs":
                self.send_json(create_job(self.parse_form()))
            elif method == "POST" and path == "/api/demo-job":
                self.send_json(create_demo_job())
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f-]{36})", path):
                self.handle_job(method, match.group(1))
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/stop", path):
                if method != "POST":
                    raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
                self.handle_stop(match.group(1))
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/archive", path):
                if method != "GET":
                    raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
                self.handle_archive(match.group(1))
            elif match := re.fullmatch(r"/api/jobs/([0-9a-f-]{36})/files/(.+)", path):
                if method != "GET":
                    raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
                self.handle_job_file(match.group(1), unquote(match.group(2)))
            else:
                raise RequestError(HTTPStatus.NOT_FOUND, "not found")
        except RequestError as caught:
            self.send_json({"detail": caught.detail}, status=caught.status)
        except Exception as caught:
            self.send_json({"detail": str(caught)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def parse_form(self) -> dict[str, list[FormField]]:
        content_type = self.headers.get("content-type", "")
        length = int(self.headers.get("content-length", "0"))
        if length > 512 * 1024 * 1024:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
        return parse_multipart_form(content_type, self.rfile.read(length))

    def handle_job(self, method: str, job_id: str) -> None:
        if method != "GET":
            raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        state = read_state(job_id)
        if state.get("status") == "completed" and not state.get("outputs"):
            state = update_state(job_id, outputs=collect_outputs(job_id))
        state["log"] = tail_text(job_dir(job_id) / "job.log")
        self.send_json({"job": state})

    def handle_stop(self, job_id: str) -> None:
        read_state(job_id)
        with _process_lock:
            process = _processes.get(job_id)
        if process is None or process.poll() is not None:
            self.send_json({"status": "not_running"})
            return
        process.terminate()
        update_state(job_id, status="stopping")
        self.send_json({"status": "stopping"})

    def handle_job_file(self, job_id: str, relative_path: str) -> None:
        base = job_dir(job_id).resolve()
        target = (base / relative_path).resolve()
        try:
            target.relative_to(base)
        except ValueError as caught:
            raise RequestError(HTTPStatus.NOT_FOUND, "file not found") from caught
        self.send_file(target, base=base)

    def handle_archive(self, job_id: str) -> None:
        state = read_state(job_id)
        output_dir = Path(state["output_dir"])
        if not output_dir.exists():
            raise RequestError(HTTPStatus.NOT_FOUND, "outputs not found")
        archive_path = job_dir(job_id) / "analysis_outputs.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(output_dir))
        self.send_file(archive_path, download_name=f"analysis_outputs_{job_id[:8]}.zip")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, base: Path | None = None, download_name: str | None = None) -> None:
        target = path.resolve()
        if base is not None:
            try:
                target.relative_to(base.resolve())
            except ValueError as caught:
                raise RequestError(HTTPStatus.NOT_FOUND, "file not found") from caught
        if not target.exists() or not target.is_file():
            raise RequestError(HTTPStatus.NOT_FOUND, "file not found")
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with target.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)


def main() -> None:
    host = os.environ.get("TODENSEKKEI_ANALYSIS_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("TODENSEKKEI_ANALYSIS_UI_PORT", "8050"))
    server = ThreadingHTTPServer((host, port), AnalysisUIHandler)
    print(f"Analysis UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
