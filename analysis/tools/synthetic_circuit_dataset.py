"""Generate and evaluate deterministic synthetic sequence-circuit PDF datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _repo_path  # noqa: F401 - importing it adds the repository root to sys.path

from analysis.synthetic_circuits import DatasetConfig, evaluate_dataset, generate_dataset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("output", type=Path)
    generate.add_argument("--count", type=int, default=100)
    generate.add_argument("--seed", type=int, default=2609)
    generate.add_argument("--min-rungs", type=int, default=3)
    generate.add_argument("--max-rungs", type=int, default=6)
    generate.add_argument("--validation-every", type=int, default=5)
    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("root", type=Path)
    evaluate.add_argument("--model", type=Path)
    evaluate.add_argument("--confidence", type=float, default=0.5)
    evaluate_ocr = evaluate.add_mutually_exclusive_group()
    evaluate_ocr.add_argument("--enable-ocr", action="store_true")
    evaluate_ocr.add_argument("--disable-ocr", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("output", type=Path)
    run.add_argument("--count", type=int, default=100)
    run.add_argument("--seed", type=int, default=2609)
    run.add_argument("--min-rungs", type=int, default=3)
    run.add_argument("--max-rungs", type=int, default=6)
    run.add_argument("--validation-every", type=int, default=5)
    run.add_argument("--model", type=Path)
    run.add_argument("--confidence", type=float, default=0.5)
    run_ocr = run.add_mutually_exclusive_group()
    run_ocr.add_argument("--enable-ocr", action="store_true")
    run_ocr.add_argument("--disable-ocr", action="store_true")
    args = parser.parse_args()

    if args.command == "evaluate":
        disable_ocr = args.disable_ocr or (args.model is None and not args.enable_ocr)
        result = evaluate_dataset(
            args.root, disable_ocr=disable_ocr, model_path=args.model, confidence=args.confidence,
        )
    else:
        config = DatasetConfig(
            count=args.count, seed=args.seed, min_rungs=args.min_rungs, max_rungs=args.max_rungs,
            validation_every=args.validation_every,
        )
        result = generate_dataset(args.output, config)
        if args.command == "run":
            disable_ocr = args.disable_ocr or (args.model is None and not args.enable_ocr)
            result = {
                "generation": result,
                "evaluation": evaluate_dataset(
                    args.output, disable_ocr=disable_ocr, model_path=args.model, confidence=args.confidence,
                ),
            }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
