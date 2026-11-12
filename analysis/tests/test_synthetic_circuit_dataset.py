from __future__ import annotations

import hashlib
import json
import zipfile
from copy import deepcopy

import pytest

from analysis.synthetic_circuits import DatasetConfig, evaluate_dataset, generate_dataset
from analysis.synthetic_circuits.dataset import (
    _match_oracle_symbols,
    _predicted_edges,
    _project_from_analysis,
    _semantic_signature,
)
from services.circuit_model import build_circuit


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generate_dataset_is_deterministic_and_emits_all_representations(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    config = DatasetConfig(count=3, seed=2609, min_rungs=2, max_rungs=3, validation_every=2)

    one = generate_dataset(first, config)
    two = generate_dataset(second, config)

    assert one["page_count"] == two["page_count"] == 3
    assert {page["circuit_family"] for page in one["pages"]} == {"single_contact_ladder", "series_interlock"}
    assert one["dataset_sha256"] == two["dataset_sha256"]
    assert _sha(first / "manifest.json") == _sha(second / "manifest.json")
    assert _sha(first / "dataset.pdf") == _sha(second / "dataset.pdf")
    assert one["artifacts"]["synthetic_bundle_sha256"] == _sha(first / "synthetic_bundle.zip")
    assert all(page["labels_sha256"] and page["netlist_sha256"] for page in one["pages"])
    assert (first / "dataset" / "images" / "train" / "SYN000001.png").is_file()
    assert (first / "dataset" / "images" / "val" / "SYN000002.png").is_file()
    assert (first / "dataset" / "labels" / "train" / "SYN000001.txt").is_file()
    assert (first / "sources" / "SYN000001.json").is_file()
    assert (first / "netlists" / "SYN000001.cir").read_text().endswith(".op\n.end\n")
    assert (first / "pdf" / "SYN000001.pdf").read_bytes().startswith(b"%PDF-")

    with zipfile.ZipFile(first / "synthetic_bundle.zip") as archive:
        names = set(archive.namelist())
        assert {"bundle.json", "classes.txt", "data.yaml"} <= names
        bundle = json.loads(archive.read("bundle.json"))
        assert len(bundle["projects"]) == 3
        assert all(project["symbols"] and project["connections"] for project in bundle["projects"])


def test_generated_pdf_round_trip_is_scored_against_source_graph(tmp_path):
    pytest.importorskip("cv2")
    root = tmp_path / "dataset"
    generate_dataset(root, DatasetConfig(count=2, seed=7, min_rungs=2, max_rungs=2))

    report = evaluate_dataset(root, disable_ocr=True)

    assert report["page_count"] == 2
    assert report["errors"] == 0
    assert report["source_consistency_errors"] == 0
    assert report["circuit_semantic_mismatches"] == 0
    assert report["circuit_semantics"] == {"gold": 2, "evaluated": 2, "matched": 2, "recall": 1.0}
    assert report["ocr_requested"] is False
    assert report["ocr_runtime"] == {
        "status_counts": {"disabled": 2}, "engine_counts": {"none": 2},
    }
    assert report["connections"]["gold"] > 0
    assert report["connections"]["recall"] >= 0.8
    assert report["connections"]["precision"] >= 0.8
    assert report["pdf_round_trip"]["dark_pixel_recall"] >= 0.98
    assert (root / "evaluation.json").is_file()
    assert all((root / "analysis" / f"SYN00000{i}.json").is_file() for i in (1, 2))

    source = json.loads((root / "sources" / "SYN000001.json").read_text())
    result = json.loads((root / "analysis" / "SYN000001.json").read_text())
    _source_to_result, result_to_source = _match_oracle_symbols(source, result)
    _symbol_edges, terminal_edges, endpoint_map = _predicted_edges(
        result, result_to_source, source["project"]["image_width"], source["project"]["image_height"],
    )
    reconstructed = _project_from_analysis(
        source["project"], result, result_to_source, endpoint_map, terminal_edges,
    )
    source_signature = _semantic_signature(
        source["project"], build_circuit([source["project"]]), include_labels=False,
    )
    assert source_signature == _semantic_signature(
        reconstructed, build_circuit([reconstructed]), include_labels=False,
    )

    wrong_result = deepcopy(result)
    contact = next(symbol for symbol in wrong_result["symbols"] if symbol["class_name"] in {"contact_a", "contact_b"})
    contact["class_name"] = "contact_b" if contact["class_name"] == "contact_a" else "contact_a"
    _source_to_result, result_to_source = _match_oracle_symbols(source, wrong_result)
    _symbol_edges, terminal_edges, endpoint_map = _predicted_edges(
        wrong_result, result_to_source, source["project"]["image_width"], source["project"]["image_height"],
    )
    wrong_project = _project_from_analysis(
        source["project"], wrong_result, result_to_source, endpoint_map, terminal_edges,
    )
    assert source_signature != _semantic_signature(
        wrong_project, build_circuit([wrong_project]), include_labels=False,
    )

    wrong_terminals = deepcopy(result)
    contact = next(symbol for symbol in wrong_terminals["symbols"] if symbol["class_name"] in {"contact_a", "contact_b"})
    center = [
        (contact["bbox"]["x0"] + contact["bbox"]["x1"]) / 2,
        (contact["bbox"]["y0"] + contact["bbox"]["y1"]) / 2,
    ]
    for terminal in contact["terminals"]:
        terminal["point"] = center
    _source_to_result, result_to_source = _match_oracle_symbols(source, wrong_terminals)
    _symbol_edges, terminal_edges, endpoint_map = _predicted_edges(
        wrong_terminals, result_to_source, source["project"]["image_width"], source["project"]["image_height"],
    )
    wrong_terminal_project = _project_from_analysis(
        source["project"], wrong_terminals, result_to_source, endpoint_map, terminal_edges,
    )
    assert source_signature != _semantic_signature(
        wrong_terminal_project, build_circuit([wrong_terminal_project]), include_labels=False,
    )


def test_dataset_config_rejects_unbounded_or_invalid_requests():
    with pytest.raises(ValueError, match="count"):
        DatasetConfig(count=0)
    with pytest.raises(ValueError, match="rungs"):
        DatasetConfig(count=1, min_rungs=4, max_rungs=3)
    with pytest.raises(ValueError, match="count"):
        DatasetConfig(count=2001)


def test_evaluation_errors_remain_in_recall_denominator(tmp_path):
    pytest.importorskip("cv2")
    root = tmp_path / "dataset"
    manifest = generate_dataset(root, DatasetConfig(count=2, seed=19, min_rungs=2, max_rungs=2))
    (root / "pdf" / "SYN000002.pdf").unlink()

    report = evaluate_dataset(root)

    assert report["errors"] == 1
    assert report["connections"]["gold"] == sum(page["connections"] for page in manifest["pages"])
    assert report["connections"]["recall"] < 1.0
    assert report["circuit_semantic_mismatches"] == 1
    assert report["circuit_semantics"] == {"gold": 2, "evaluated": 1, "matched": 1, "recall": 0.5}
