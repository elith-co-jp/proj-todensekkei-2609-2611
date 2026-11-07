"""Read device numbers of symbol boxes with the local vision LLM alone and compare with gold labels.

This measures the LLM by itself. The product uses it only as a second reader
(analysis.text_extraction.reread_label); evaluate_text_extraction.py --label-llm measures that path.

Example:
    python analysis/tools/llm_label_reader.py --gold a.zip b.zip --url http://127.0.0.1:11434 --limit 120
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from holdout_cv import gold_items, load_gold  # noqa: E402

from analysis import label_llm  # noqa: E402
from analysis import text_extraction as tx  # noqa: E402


def device_key(text: str) -> str:
    return tx.match_key(tx.device_token(text))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--url", required=True, help="Ollama 互換 API の起点 URL")
    parser.add_argument("--model", default="qwen3.6:35b-a3b-mtp-q4_K_M")
    parser.add_argument("--classes", nargs="+", default=["contact_a", "contact_b", "relay_coil"])
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--seed", type=int, default=2609)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    _, drawings = load_gold(args.gold)
    items = []
    for d in drawings:
        image = Image.open(io.BytesIO(d["image"])).convert("RGB")
        items.extend((d["sheet"], image, g) for g in gold_items(d) if g["class_name"] in args.classes and device_key(g["label"]))
    random.Random(args.seed).shuffle(items)
    read = label_llm.make_reader(args.url, args.model, label_llm.DEVICE, timeout=180.0)
    rows, started = [], time.time()
    for sheet, image, g in items[: args.limit]:
        answer = read(image, g["bbox"]) or ""
        rows.append({"sheet": sheet, "ref": g["ref"], "class": g["class_name"], "gold": g["label"], "llm": answer,
                     "ok": device_key(answer) == device_key(g["label"])})
    summary = {"scored": len(rows), "no_answer": sum(1 for r in rows if not r["llm"]),
               "device_accuracy": round(sum(r["ok"] for r in rows) / len(rows), 4) if rows else None,
               "seconds": round(time.time() - started, 1)}
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()

