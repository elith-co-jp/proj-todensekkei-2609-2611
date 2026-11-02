"""Fine-tune the label OCR's text recognizer (PP-OCRv4 rec, ONNX) on drawing text lines, and write it back as ONNX.

Input is ocr_finetune_data.py output. Lines from --train-split are trained on; lines from the other split
score the model before and after (exact line match). The ONNX model is converted to PyTorch and trained
with CTC loss; the trained weights are then written back into the original ONNX graph, so its dynamic
shapes and metadata (RapidOCR reads the character list there) stay as they were. Batch-norm statistics
stay frozen.

The app uses the model only for the 4x reads of each connector / contact / coil box
(run_yolo_e2e_pipeline.load_crop_ocr_reader); a version also used for the whole page lowered the
relay-table rows and notes, which these lines do not cover.

Example:
    python analysis/tools/ocr_finetune.py --lines runs/ocr_lines --train-split tune --out runs/ocr_rec_tune.onnx
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

IMG_H = 48


def base_model_path() -> Path:
    import rapidocr_onnxruntime

    return Path(rapidocr_onnxruntime.__file__).parent / "models" / "ch_PP-OCRv4_rec_infer.onnx"


def _constants_as_initializers(model):
    import onnx

    nodes = []
    for node in model.graph.node:
        if node.op_type == "Constant" and len(node.attribute) == 1 and node.attribute[0].name == "value":
            tensor = onnx.helper.get_attribute_value(node.attribute[0])
            tensor.name = node.output[0]
            model.graph.initializer.append(tensor)
        else:
            nodes.append(node)
    del model.graph.node[:]
    model.graph.node.extend(nodes)
    return model


def to_torch(onnx_path: Path):
    """The recognizer as a PyTorch module, and the ONNX constant name behind each trainable parameter."""
    import onnx
    import torch
    from onnx2torch import convert
    from onnx2torch.node_converters import batch_norm

    class AnyRankBatchNorm(torch.nn.BatchNorm1d):
        """Shape inference cannot see the rank of some inputs here; batch_norm itself works for any rank."""

        def _check_input_dim(self, input):  # noqa: A002
            return None

    batch_norm._BN_CLASS_FROM_SPATIAL_RANK[-2] = AnyRankBatchNorm
    model = _constants_as_initializers(onnx.load(str(onnx_path)))
    dims = model.graph.input[0].type.tensor_type.shape.dim
    dims[0].dim_param = "N"
    dims[2].Clear()
    dims[2].dim_value = IMG_H
    dims[3].dim_param = "W"
    net = convert(onnx.shape_inference.infer_shapes(model), attach_onnx_mapping=True)
    nodes_by_name = {n.name: n for n in model.graph.node}
    origin: dict[str, str] = {}
    # Weights used by generic ops (MatMul, Add) arrive as buffers, one per use, in argument order.
    for fx_node in net.graph.nodes:
        if fx_node.op != "call_module":
            continue
        inputs = net.get_submodule(fx_node.target).onnx_mapping.inputs
        for arg, value_name in zip(fx_node.args, inputs, strict=False):
            if getattr(arg, "op", None) == "get_attr":
                origin[arg.target] = value_name
    for module_name, _module in net.named_children():
        node = nodes_by_name.get(module_name.replace("/", "."))
        if node is not None and node.op_type in ("Conv", "BatchNormalization"):
            for index, attr in ((1, "weight"), (2, "bias")):
                if len(node.input) > index and node.input[index]:
                    origin[f"{module_name}.{attr}"] = node.input[index]
    uses: dict[str, int] = {}
    for value_name in origin.values():
        uses[value_name] = uses.get(value_name, 0) + 1
    initializers = net.get_submodule("initializers")
    for name, buf in list(initializers.named_buffers(recurse=False)):
        key = f"initializers.{name}"
        # A constant used in two places gets two copies; training them apart would leave no single value to write back.
        if buf.is_floating_point() and buf.numel() > 1 and uses.get(origin.get(key, ""), 0) == 1:
            delattr(initializers, name)
            initializers.register_parameter(name, torch.nn.Parameter(buf.clone()))
    trainable = {name: origin[name] for name, p in net.named_parameters() if name in origin and uses[origin[name]] == 1}
    for name, p in net.named_parameters():
        p.requires_grad_(name in trainable)
    return net, trainable


def write_back(net, trainable: dict[str, str], base: Path, out: Path) -> int:
    """Copy trained weights into the original ONNX constants; return how many constants changed."""
    import onnx
    from onnx import numpy_helper

    params = dict(net.named_parameters())
    by_constant = {constant: params[name].detach().cpu().numpy() for name, constant in trainable.items()}
    model = onnx.load(str(base))
    changed = 0
    for node in model.graph.node:
        if node.op_type == "Constant" and node.output[0] in by_constant:
            old = onnx.helper.get_attribute_value(node.attribute[0])
            value = by_constant[node.output[0]].astype(numpy_helper.to_array(old).dtype).reshape(numpy_helper.to_array(old).shape)
            node.attribute[0].t.CopyFrom(numpy_helper.from_array(value, node.output[0]))
            changed += 1
    onnx.save(model, str(out))
    return changed


def characters(onnx_path: Path) -> list[str]:
    import onnxruntime as ort

    meta = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"]).get_modelmeta().custom_metadata_map
    return ["blank", *meta["character"].splitlines(), " "]


def batch_tensor(images: list[np.ndarray]):
    """RapidOCR's resize_norm_img: height 48, width from the widest line in the batch (at least 320), zero padding."""
    import torch

    max_ratio = max(320 / IMG_H, *(im.shape[1] / im.shape[0] for im in images))
    width = int(IMG_H * max_ratio)
    out = np.zeros((len(images), 3, IMG_H, width), dtype=np.float32)
    for i, im in enumerate(images):
        w = min(width, int(math.ceil(IMG_H * im.shape[1] / im.shape[0])))
        resized = np.asarray(Image.fromarray(im).resize((w, IMG_H), Image.Resampling.BILINEAR), dtype=np.float32)
        out[i, :, :, :w] = ((resized / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)
    return torch.from_numpy(out)


def decode(probs, chars: list[str]) -> list[str]:
    idx = probs.argmax(-1).cpu().numpy()
    texts = []
    for row in idx:
        prev, s = 0, []
        for k in row:
            if k != prev and k != 0:
                s.append(chars[k])
            prev = k
        texts.append("".join(s))
    return texts


def line_accuracy(net, rows, chars, device, batch=64) -> float:
    import torch

    hits = 0
    with torch.no_grad():
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            pred = decode(net(batch_tensor([r["img"] for r in chunk]).to(device)), chars)
            hits += sum(p.strip() == r["target"] for p, r in zip(pred, chunk, strict=True))
    return hits / max(1, len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lines", type=Path, required=True)
    parser.add_argument("--train-split", choices=("tune", "report", "all"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--seed", type=int, default=2609)
    parser.add_argument("--limit", type=int, help="use only this many training lines (timing checks)")
    parser.add_argument("--keep-lines", type=int, default=2000,
                        help="lines already read right that join every corrected line in training, so the rest of the reading holds")
    parser.add_argument("--drop-lines", type=int, default=1000,
                        help="lines RapidOCR drops (connector dots, arcs) trained to read as nothing, so they stay dropped")
    args = parser.parse_args()

    import torch

    started = time.time()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    base = base_model_path()
    chars = characters(base)
    index = {c: i for i, c in enumerate(chars)}
    rows = [json.loads(line) for line in (args.lines / "lines.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if r["target"] is not None and all(c in index for c in r["target"])]
    for r in rows:
        r["img"] = np.asarray(Image.open(args.lines / "images" / r["image"]).convert("RGB"))
    train = [r for r in rows if args.train_split in ("all", r["split"])]
    corrected = [r for r in train if r["target"] and r["ocr"] != r["target"]]
    kept = [r for r in train if r["target"] and r["ocr"] == r["target"]]
    dropped = [r for r in train if not r["target"]]
    random.shuffle(kept)
    random.shuffle(dropped)
    train = corrected + kept[:args.keep_lines] + dropped[:args.drop_lines]
    if args.limit:
        train = train[:args.limit]
    held_all = [r for r in rows if args.train_split != "all" and r["split"] != args.train_split]
    held = [r for r in held_all if r["target"]]
    held_fixed = [r for r in held if r["ocr"] != r["target"]]
    held_dropped = [r for r in held_all if not r["target"]]

    net, trainable = to_torch(base)
    net = net.to(device).eval()
    report = {"train_lines": len(train), "train_corrected": len(corrected), "train_dropped": min(len(dropped), args.drop_lines),
              "held_lines": len(held), "held_corrected": len(held_fixed), "trainable_constants": len(trainable)}
    if held:
        report["held_before"] = round(line_accuracy(net, held, chars, device), 4)
        report["held_corrected_before"] = round(line_accuracy(net, held_fixed, chars, device), 4)
        report["held_dropped_silent_before"] = round(line_accuracy(net, held_dropped, chars, device), 4)
    opt = torch.optim.AdamW([p for p in net.parameters() if p.requires_grad], lr=args.lr, weight_decay=1e-4)
    ctc = torch.nn.CTCLoss(blank=0, zero_infinity=True)
    for _epoch in range(args.epochs):
        random.shuffle(train)
        total = 0.0
        for i in range(0, len(train), args.batch):
            chunk = train[i:i + args.batch]
            x = batch_tensor([r["img"] for r in chunk]).to(device)
            log_probs = torch.log(net(x).clamp_min(1e-8)).permute(1, 0, 2)
            targets = [torch.tensor([index[c] for c in r["target"]]) for r in chunk]
            loss = ctc(log_probs, torch.cat(targets).to(device), torch.full((len(chunk),), log_probs.shape[0], dtype=torch.long),
                       torch.tensor([len(t) for t in targets]))
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(chunk)
        report.setdefault("loss", []).append(round(total / len(train), 4))
        print(json.dumps({"epoch": _epoch + 1, "loss": report["loss"][-1], "seconds": round(time.time() - started)}), file=sys.stderr, flush=True)
    if held:
        report["held_after"] = round(line_accuracy(net, held, chars, device), 4)
        report["held_corrected_after"] = round(line_accuracy(net, held_fixed, chars, device), 4)
        report["held_dropped_silent_after"] = round(line_accuracy(net, held_dropped, chars, device), 4)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report["written_constants"] = write_back(net, trainable, base, args.out)
    # The written model must read like the trained one, for a batch of lines of different widths.
    import onnxruntime as ort

    sample = batch_tensor([r["img"] for r in (held or train)[:6]])
    with torch.no_grad():
        expected = net(sample.to(device)).cpu().numpy()
    got = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"]).run(None, {"x": sample.numpy()})[0]
    report["onnx_max_diff"] = float(np.abs(expected - got).max())
    report["out"] = str(args.out)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
