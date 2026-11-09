"""Collect the text-line images the label OCR reads on gold drawings, each with the text the drawing shows.

The OCR reads every page twice (whole page at 2x, and each connector / contact / coil box at 4x).
This runs the same reads, records every line image the recognizer sees, and gives each line a target:
the recognized text with each token replaced by a label token of the gold symbols around the line.
Only corrections a misread glyph explains are made (see snap_token); a line with a token that has no
such match gets no target and is not trained on. --retarget recomputes targets from saved lines.
The output feeds ocr_finetune.py.

Example:
    python analysis/tools/ocr_finetune_data.py --gold a.zip b.zip --out runs/ocr_lines
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_split import tune_sheets  # noqa: E402
from holdout_cv import gold_items, load_gold  # noqa: E402

from analysis import text_extraction as tx  # noqa: E402


TOKEN = re.compile(r"\([^\s()]*\)?|[^\s()]+")
# Characters the recognizer adds at the edge of a word: the bar of a contact (1 I l |), the terminal
# mark of a connector read as 本 个 小, stray dots and brackets.
EDGE_NOISE = set("本个小.,|Il1[]'")
# Glyphs the drawing font draws alike; the gold labels mix them too, so a read is never "corrected" between them.
_SAME = str.maketrans({"O": "0", "Q": "0", "I": "1"})


def _canon(text: str) -> str:
    return text.upper().translate(_SAME)


def tokens(text: str) -> list[str]:
    return TOKEN.findall(unicodedata.normalize("NFKC", text or ""))


def edit_ops(a: str, b: str) -> list[tuple[str, int, str]]:
    """Levenshtein edits turning a into b, ignoring case and O/0, I/1: ("sub", i, a[i]+b[j]), ("del", i, a[i]), ("ins", i, b[j])."""
    A, B = _canon(a), _canon(b)
    d = [[i + j if i * j == 0 else 0 for j in range(len(B) + 1)] for i in range(len(A) + 1)]
    for i in range(1, len(A) + 1):
        for j in range(1, len(B) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (A[i - 1] != B[j - 1]))
    ops, i, j = [], len(A), len(B)
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (A[i - 1] != B[j - 1]):
            if A[i - 1] != B[j - 1]:
                ops.append(("sub", i - 1, a[i - 1] + b[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", i - 1, a[i - 1]))
            i -= 1
        else:
            ops.append(("ins", i, b[j - 1]))
            j -= 1
    return ops


def snap_token(token: str, gold_tokens: set[str]) -> str | None:
    """The printed word behind an OCR token, or None when no gold token is a misread-glyph away.

    A token equal to a gold token apart from case, O/0 and I/1 stays as read (gold labels do not keep the drawing's case).
    Allowed edits: a letter for a letter or digit (and back), dropping edge noise (EDGE_NOISE at either end), and
    adding a missed alphanumeric inside a word (an end may be cut off by the text box, not misread);
    1 edit for words up to 4 characters, 2 for longer. Words under 3 characters, all-digit words and digit-for-digit
    edits must match exactly: terminal and sheet numbers run in sequence, so the neighbouring number is as close as
    the right one.
    """
    if any(_canon(token) == _canon(g) for g in gold_tokens):
        return token
    if token.isdigit() or len(token) < 3:
        return None
    limit = 1 if len(token) <= 4 else 2
    fits = []
    for g in gold_tokens:
        ops = edit_ops(token, g)
        if not ops or len(ops) > limit:
            continue
        ok = all(
            (kind == "sub" and chars[0].isalnum() and chars[1].isalnum() and not (chars[0].isdigit() and chars[1].isdigit()))
            or (kind == "del" and chars in EDGE_NOISE and pos in (0, len(token) - 1))
            or (kind == "ins" and chars.isalnum() and 0 < pos < len(token))
            for kind, pos, chars in ops
        )
        if ok:
            fits.append((len(ops), g))
    fits.sort()
    if not fits or (len(fits) > 1 and fits[1][0] == fits[0][0]):
        return None
    return fits[0][1]


def target_for(text: str, gold_tokens: set[str]) -> str | None:
    """The line with every token replaced by its printed word, separators kept; None if any token has no match."""
    text = unicodedata.normalize("NFKC", text or "")
    out, last = [], 0
    for m in TOKEN.finditer(text):
        snapped = snap_token(m.group(), gold_tokens)
        if snapped is None:
            return None
        out.append(text[last:m.start()] + snapped)
        last = m.end()
    target = "".join(out) + text[last:]
    return target.strip() or None


def tokens_near(gold: list[dict[str, Any]], box: tuple[float, float, float, float]) -> set[str]:
    """Label tokens of the gold symbols whose box holds the centre of a text line."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return {t for g in gold for t in tokens(g["label"]) if tx._inside((cx, cy), tx._as_box(g["bbox"]), 6)}


class RecordingRecognizer:
    """Wraps RapidOCR's recognizer and keeps every line image with its reading."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.lines = inner, []

    def __call__(self, imgs: Any, *args: Any, **kwargs: Any) -> Any:
        result = self.inner(imgs, *args, **kwargs)
        rec, _elapse = result
        self.lines.extend(zip(list(imgs), [(r[0], float(r[1])) for r in rec], strict=True))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def read_lines(reader: Any, image: np.ndarray, **kwargs: Any) -> list[tuple[np.ndarray, str, float, tuple[float, float, float, float]]]:
    """Every line image of one OCR call with its box, including the ones RapidOCR drops for a low score.

    Dropped lines are mostly the dot of a connector and the arc around it; the fine-tuned model must keep
    dropping them, so they are kept here as lines that read as nothing.
    """
    recorder: RecordingRecognizer = reader.text_rec
    recorder.lines = []
    cut = reader.text_score
    results, _ = reader(image, text_score=0.0, **kwargs)
    reader.text_score = cut
    out = []
    for (img, (text, score)), (points, text2, _s) in zip(recorder.lines, results or [], strict=False):
        if text != text2:
            continue
        xs, ys = [float(x) for x, _ in points], [float(y) for _, y in points]
        out.append((img, text, score, (min(xs), min(ys), max(xs), max(ys))))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--retarget", action="store_true", help="recompute targets of the lines already in --out")
    args = parser.parse_args()

    _, drawings = load_gold(args.gold)
    gold_by_sheet = {d["sheet"]: [g for g in gold_items(d) if g["label"] and g["kind"] == "symbol"] for d in drawings}
    if args.retarget:
        rows = [json.loads(line) for line in (args.out / "lines.jsonl").read_text(encoding="utf-8").splitlines() if line]
        for r in rows:
            if r["target"] == "":
                continue
            near = tokens_near(gold_by_sheet[r["sheet"]], tuple(r["box"]))
            r["target"] = target_for(r["ocr"], near) if near else None
        (args.out / "lines.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(json.dumps({"lines": len(rows), "with_target": sum(r["target"] is not None for r in rows),
                          "corrected": sum(r["target"] not in (None, r["ocr"]) for r in rows)}))
        return

    from rapidocr_onnxruntime import RapidOCR

    reader = RapidOCR()
    reader.text_rec = RecordingRecognizer(reader.text_rec)
    tune = tune_sheets(d["sheet"] for d in drawings)
    (args.out / "images").mkdir(parents=True, exist_ok=True)
    rows = []
    for d in drawings:
        image = Image.open(io.BytesIO(d["image"])).convert("RGB")
        gold = gold_by_sheet[d["sheet"]]
        reads = []
        page = tx.erase_dots(image)
        page = page.resize((page.width * tx.OCR_SCALE, page.height * tx.OCR_SCALE), Image.Resampling.LANCZOS)
        for x0 in tx._tile_starts(page.width):
            for y0 in tx._tile_starts(page.height):
                tile = np.asarray(page.crop((x0, y0, min(x0 + tx.OCR_TILE_PX, page.width), min(y0 + tx.OCR_TILE_PX, page.height))))
                for img, text, score, (a, b, c, e) in read_lines(reader, tile):
                    reads.append(("page", img, text, score, ((a + x0) / tx.OCR_SCALE, (b + y0) / tx.OCR_SCALE, (c + x0) / tx.OCR_SCALE, (e + y0) / tx.OCR_SCALE)))
        for g in gold:
            if g["class_name"] not in tx.CROP_OCR_CLASSES:
                continue
            x0, y0, x1, y1 = tx._as_box(g["bbox"])
            left, top = max(0, int(x0) - tx.CROP_PADDING_PX), max(0, int(y0) - tx.CROP_PADDING_PX)
            right, bottom = min(image.width, int(x1) + tx.CROP_PADDING_PX), min(image.height, int(y1) + tx.CROP_PADDING_PX)
            crop = image.crop((left, top, right, bottom))
            crop = crop.resize((crop.width * tx.CROP_SCALE, crop.height * tx.CROP_SCALE), Image.Resampling.LANCZOS)
            for img, text, score, (a, b, c, e) in read_lines(reader, np.asarray(crop), use_cls=False):
                reads.append(("crop", img, text, score, (left + a / tx.CROP_SCALE, top + b / tx.CROP_SCALE, left + c / tx.CROP_SCALE, top + e / tx.CROP_SCALE)))
        for i, (source, img, text, score, box) in enumerate(reads):
            dropped = score < reader.text_score
            near = tokens_near(gold, box)
            target = "" if dropped else target_for(text, near) if near else None
            name = f"{d['sheet']}_{source}_{i:04d}.png"
            Image.fromarray(np.asarray(img)[:, :, ::-1]).save(args.out / "images" / name)
            rows.append({"image": name, "sheet": d["sheet"], "split": "tune" if d["sheet"] in tune else "report",
                         "source": source, "ocr": "" if dropped else text, "score": round(score, 4), "target": target,
                         "box": [round(v, 1) for v in box]})
        print(json.dumps({"sheet": d["sheet"], "lines": len(reads), "with_target": sum(r["target"] is not None for r in rows if r["sheet"] == d["sheet"]),
                          "corrected": sum(r["target"] not in (None, r["ocr"]) for r in rows if r["sheet"] == d["sheet"])}), flush=True)
    (args.out / "lines.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
