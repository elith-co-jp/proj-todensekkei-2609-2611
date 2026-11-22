"""小さな部品の、ふだんの使い方では通らない場合（凍結した exe・別の DB・空の入力・外の LLM の応答など）。"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

import database
import runtime
from analysis import label_llm, parallel
from analysis.relay_lamp import _circle
from analysis.wiring.assemble import label_text
from schemas import ATTRIBUTE_MAX_KEYS, ATTRIBUTE_TEXT_MAX, check_attributes
from services import circuit_model
from services.circuit_interop import lcapy_netlist
from services.circuit_model import build_circuit
from services.circuit_timing import run_timeline, state_at
from services.rotation_copies import rotate_label_text
from symbol_class_policy import canonical_symbol_class_key
from tests.test_circuit_model import _series_page, _sym
from tests.test_circuit_timing import lamp, timer_page


def test_horizontal_names_of_aliased_classes():
    alias = next(iter(__import__("symbol_class_policy").CLASS_KEY_ALIASES.items()))
    assert canonical_symbol_class_key(f"{alias[0]}_horizontal", []) == alias[1]
    assert canonical_symbol_class_key(f"horizontal_{alias[0]}", []) == alias[1]


def test_attribute_limits():
    with pytest.raises(ValueError, match="個まで"):
        check_attributes({f"k{i}": 1 for i in range(ATTRIBUTE_MAX_KEYS + 1)})
    with pytest.raises(ValueError, match="文字まで"):
        check_attributes({"note": "x" * (ATTRIBUTE_TEXT_MAX + 1)})


def test_frozen_exe_paths(monkeypatch, tmp_path):
    exe = tmp_path / "TodenYOLO.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert runtime.resource_base() == tmp_path.resolve() == runtime.data_base()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert runtime.resource_base() == Path(tmp_path / "bundle")


def test_sqlite_pragmas_are_skipped_on_other_databases(monkeypatch):
    monkeypatch.setattr(database.engine.dialect, "name", "postgresql")
    database._sqlite_pragmas(object(), None)  # 接続に触らない（object には cursor が無い）


def test_short_yolo_lines_are_dropped_when_rotating():
    assert rotate_label_text("0 0.5\n", 90) == ""
    assert rotate_label_text("0 0.2 0.3 0.1 0.05\n", 90) == "0 0.300000 0.800000 0.050000 0.100000\n"


def test_blocking_diode_is_open_in_the_lcapy_netlist():
    circuit = build_circuit([_series_page(_sym("D", "diode", "D1"))])
    diode = next(e for e in circuit.elements if e.kind == "d")
    assert any(line.startswith("O ") for line in lcapy_netlist(circuit, {diode.name: False}).splitlines()[1:]
               if not line.startswith("#") and "V" not in line[:1])


@pytest.mark.parametrize("mode", ["on_delay", "off_delay"])
def test_timer_without_delay_switches_at_once(monkeypatch, mode):
    monkeypatch.setattr(circuit_model, "ngspice_path", lambda: None)
    circuit = build_circuit([timer_page(mode=mode, delay=0)])
    tl = run_timeline(circuit, [{"at": 1, "inputs": {"PB1": True}}, {"at": 2, "inputs": {"PB1": False}}], until=4)["timeline"]
    assert lamp(tl, 1.0) is True
    assert lamp(tl, 2.0) is False
    assert state_at(tl, 3)["timers"]


def test_label_text_falls_back_to_the_id():
    assert label_text({"id": "L1"}) == "L1"
    assert label_text({"id": "L1", "raw_text": "R"}) == "R"


def test_relay_lamp_circle_without_a_drawn_circle_uses_the_box():
    blank = np.zeros((40, 40), dtype=np.uint8)
    assert _circle(blank, {"x0": 0, "y0": 0, "x1": 20, "y1": 20}) == (10.0, 10.0, 0.36 * 20)


def test_ocr_workers_from_the_environment(monkeypatch):
    monkeypatch.setenv("SEQANNO_OCR_WORKERS", "0")
    assert parallel.workers() == 1
    monkeypatch.setenv("SEQANNO_OCR_WORKERS", "3")
    assert parallel.workers() == 3


def test_local_llm_reader_talks_to_an_ollama_compatible_server():
    """手元の LLM（Ollama 互換の /api/chat）に本物の HTTP で問い合わせる。外の API は使わない。"""
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps({"message": {"content": "  X R100 \n"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        assert label_llm.ask(url, "qwen", "読んで", b"png", timeout=5) == "X R100"
    finally:
        server.shutdown()
    assert seen["path"] == "/api/chat" and seen["body"]["model"] == "qwen"
    assert seen["body"]["messages"][0]["images"]
