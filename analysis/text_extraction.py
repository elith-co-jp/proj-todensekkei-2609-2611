"""図面の文字を読み、記号ラベル・リレー表・シート間参照・シート番号・領域の種類を取り出す。

OCR はページ全体に 1 回（read_page_text）と、小さな文字の記号ごとに 1 回（read_crop_text）。
それ以外はすべて OCR 結果の文字枠と検出枠だけを受け取る純粋な関数で、合成データで試験できる。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any, Literal, TypedDict

from PIL import Image


class TextItem(TypedDict):
    text: str
    score: float
    box: tuple[float, float, float, float]


Box = tuple[float, float, float, float]
RegionType = Literal["drawing_area", "title_block", "relay_table", "panel", "loc"]
# 記号の画像と枠を受け取り、読んだ器具番号を返す（読めなければ None）。
LabelReader = Callable[[Image.Image, Any], "str | None"]

SHEET_REF = r"[A-Z]{1,3}\d{5}(?:-\d{1,2})?"
TERMINAL_CLASSES = {"relay_coil", "contact_a", "contact_b", "solenoid", "lamp", "push_button", "diode"}
PAGE_OCR_VERSION = "2x-tiles-dotring"
OCR_SCALE = 2
OCR_TILE_PX = 2000
OCR_TILE_OVERLAP_PX = 480
DOT_KERNEL_PX = 5
DOT_SIZE_PX = (7, 18)
DOT_RING_RADIUS = 1.0


def erase_dots(image: Image.Image) -> Image.Image:
    """黒丸（接続点）を白で消す。黒丸を挟んだ名前と番号（CN56●30）を OCR が 1 語に続けて読まないようにする。

    太字の文字や太線も開放処理で残るため、ほぼ正方形で中が詰まった点の大きさの塊だけを消す。
    端子台の黒丸を囲む円も、黒丸を消すと「(」「)」「1」に読まれるので、黒丸の大きさ 1 つ分の
    半径の内側をまとめて消す。文字はそれより外にある。この画像は OCR にだけ使う。
    """
    import cv2
    import numpy as np

    rgb = np.asarray(image.convert("RGB")).copy()
    ink = (cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) < 128).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (DOT_KERNEL_PX, DOT_KERNEL_PX))
    opened = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    lo, hi = DOT_SIZE_PX
    disks = np.zeros_like(opened)
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if lo <= w <= hi and lo <= h <= hi and abs(int(w) - int(h)) <= 4 and area >= 0.6 * w * h:
            radius = round(DOT_RING_RADIUS * max(w, h))
            cv2.circle(disks, (int(x + w // 2), int(y + h // 2)), radius, 1, thickness=-1)
    rgb[disks > 0] = 255
    return Image.fromarray(rgb)


CROP_OCR_CLASSES = {"connector", "contact_a", "contact_b", "relay_coil"}
# 拡大 OCR の結果を端子番号の読取（terminal_numbers）にも渡す記号。
TERMINAL_NUMBER_CLASSES = {"connector", "contact_a", "contact_b"}
CROP_OCR_VERSION = "crop4x-pad8"
CROP_PADDING_PX = 8
CROP_SCALE = 4


def read_crop_text(image: Image.Image, box: Any, reader: Any) -> list[TextItem]:
    """記号の枠を 4 倍に拡大して OCR し、文字枠をページ座標で返す。

    コネクタ・接点の名前と番号は 10 px 前後の小さな文字で、ページ全体の 2 倍読みでは
    枠の端の 1 文字や B と 3 を取り違える。記号ごとに拡大して読む。
    ページ全体の読みと違って黒丸は消さない。消すと黒丸のすぐ横の細い 1 も消え、端子番号 13 が 3 になる。
    """
    import numpy as np

    x0, y0, x1, y1 = _as_box(box)
    left, top = max(0, int(x0) - CROP_PADDING_PX), max(0, int(y0) - CROP_PADDING_PX)
    right, bottom = min(image.width, int(x1) + CROP_PADDING_PX), min(image.height, int(y1) + CROP_PADDING_PX)
    crop = image.crop((left, top, right, bottom))
    crop = crop.resize((crop.width * CROP_SCALE, crop.height * CROP_SCALE), Image.Resampling.LANCZOS)
    results, _ = reader(np.asarray(crop), use_cls=False)
    items: list[TextItem] = []
    for points, text, score in results or []:
        px = [left + float(x) / CROP_SCALE for x, _ in points]
        py = [top + float(y) / CROP_SCALE for _, y in points]
        items.append({"text": str(text), "score": float(score), "box": (min(px), min(py), max(px), max(py))})
    return items


def _tile_starts(length: int) -> list[int]:
    if length <= OCR_TILE_PX:
        return [0]
    count = -(-(length - OCR_TILE_OVERLAP_PX) // (OCR_TILE_PX - OCR_TILE_OVERLAP_PX))
    return [round(i * (length - OCR_TILE_PX) / (count - 1)) for i in range(count)]


def read_page_text(image: Image.Image, reader: Any) -> list[TextItem]:
    """ページを 2 倍に拡大し、OCR が縮小しない大きさのタイルに分けて読む。

    150 dpi の図面の文字は 10 px 前後で、等倍では a・D・ハイフンを読み落とす。RapidOCR は長辺
    2000 px を超える画像を縮小するため、重ねたタイルで読み、各文字列は中心を持つタイルの分だけ残す。
    """
    import numpy as np

    page = erase_dots(image)
    page = page.resize((page.width * OCR_SCALE, page.height * OCR_SCALE), Image.Resampling.LANCZOS)
    xs, ys = _tile_starts(page.width), _tile_starts(page.height)

    def core(starts: list[int], index: int, length: int) -> tuple[float, float]:
        lo = 0.0 if index == 0 else (starts[index] + starts[index - 1] + OCR_TILE_PX) / 2
        hi = float(length) if index == len(starts) - 1 else (starts[index + 1] + starts[index] + OCR_TILE_PX) / 2
        return lo, hi

    items: list[TextItem] = []
    for xi, x0 in enumerate(xs):
        for yi, y0 in enumerate(ys):
            tile = np.asarray(page.crop((x0, y0, min(x0 + OCR_TILE_PX, page.width), min(y0 + OCR_TILE_PX, page.height))))
            results, _ = reader(tile)
            (cx_lo, cx_hi), (cy_lo, cy_hi) = core(xs, xi, page.width), core(ys, yi, page.height)
            for points, text, score in results or []:
                px = [float(x) + x0 for x, _ in points]
                py = [float(y) + y0 for _, y in points]
                cx, cy = (min(px) + max(px)) / 2, (min(py) + max(py)) / 2
                if cx_lo <= cx < cx_hi and cy_lo <= cy < cy_hi:
                    box = (min(px) / OCR_SCALE, min(py) / OCR_SCALE, max(px) / OCR_SCALE, max(py) / OCR_SCALE)
                    items.append({"text": str(text), "score": float(score), "box": box})
    return items


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").upper()).strip()


def device_token(label: str) -> str:
    """ラベルの器具番号。数字だけの語（電線番号・端子番号）を飛ばした最初の語。"""
    tokens = normalize(label).split()
    return next((t for t in tokens if not t.isdigit()), tokens[0] if tokens else "")


def match_key(text: str) -> str:
    """照合用の文字列。図面の字体では O・Q と 0、I と 1 を見分けられないので同じ文字に寄せる。"""
    return normalize(text).replace(" ", "").replace("O", "0").replace("Q", "0").replace("I", "1")


def _as_box(box: Any) -> Box:
    if isinstance(box, dict):
        return (float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"]))
    return tuple(float(v) for v in box)  # type: ignore[return-value]


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(point: tuple[float, float], box: Box, margin: float = 0.0) -> bool:
    return box[0] - margin <= point[0] <= box[2] + margin and box[1] - margin <= point[1] <= box[3] + margin


def _char_boxes(item: TextItem) -> list[tuple[str, Box]]:
    """OCR の 1 行を文字ごとの枠に割る。図面の字体は等幅なので、全角を 2 文字分として按分する。"""
    text = item["text"]
    x0, y0, x1, y1 = item["box"]
    weights = [2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text]
    total = sum(weights) or 1
    vertical = (y1 - y0) > 1.5 * (x1 - x0) and len(text) > 1
    out, acc = [], 0
    for char, weight in zip(text, weights, strict=True):
        a, b = acc / total, (acc + weight) / total
        acc += weight
        if vertical:
            out.append((char, (x0, y0 + a * (y1 - y0), x1, y0 + b * (y1 - y0))))
        else:
            out.append((char, (x0 + a * (x1 - x0), y0, x0 + b * (x1 - x0), y1)))
    return out


def words_in_box(items: Iterable[TextItem], box: Any) -> list[TextItem]:
    """枠の内側にある文字だけを語に分けて返す。枠をまたぐ OCR 行は枠の内側の部分だけ残す。"""
    box = _as_box(box)
    words: list[TextItem] = []
    for item in items:
        ix0, iy0, ix1, iy1 = item["box"]
        if ix1 < box[0] or ix0 > box[2] or iy1 < box[1] or iy0 > box[3]:
            continue
        run: list[tuple[str, Box]] = []
        for char, char_box in [*_char_boxes(item), (" ", item["box"])]:
            if char.isspace() or not _inside(_center(char_box), box):
                if run:
                    words.append({
                        "text": "".join(c for c, _ in run), "score": item["score"],
                        "box": (run[0][1][0], min(b[1] for _, b in run), run[-1][1][2], max(b[3] for _, b in run)),
                    })
                run = []
            else:
                run.append((char, char_box))
    return words


def _overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    """隣り合う行・列の OCR 枠は数ピクセル重なるので、短い方の半分以上重なるときだけ同じ並びとする。"""
    return min(a1, b1) - max(a0, b0) >= 0.5 * min(a1 - a0, b1 - b0)


def _groups(words: list[TextItem], axis: int) -> list[list[TextItem]]:
    """axis=0 は列（x 方向に重なる語）、axis=1 は行（y 方向に重なる語）にまとめる。"""
    groups: list[list[TextItem]] = []
    for word in sorted(words, key=lambda w: w["box"][axis]):
        lo, hi = word["box"][axis], word["box"][axis + 2]
        for group in groups:
            if any(_overlap(lo, hi, g["box"][axis], g["box"][axis + 2]) for g in group):
                group.append(word)
                break
        else:
            groups.append([word])
    return groups


def reading_order(words: list[TextItem], *, column_major: bool) -> list[TextItem]:
    """コネクタは列ごと（左の列を上から、次に右の列）、それ以外は行ごとに読む。"""
    axis, inner = (0, 1) if column_major else (1, 0)
    ordered = []
    for group in sorted(_groups(words, axis), key=lambda g: min(w["box"][axis] for w in g)):
        ordered.extend(sorted(group, key=lambda w: w["box"][inner]))
    return ordered


# 接点・コイルの枠には端子番号（1〜2 桁）、端子名（C・NO・NC1・COM）、接点の線を読んだ 1 文字が混じる。
_TERMINAL_NOISE = re.compile(r"\d{1,2}|.|NO\d?|NC\d?|COM|L\d")
_EDGE_PUNCT = "().,:;·・°、。-~!?'\"/|[]{}●"


def _clean_token(token: str) -> str:
    """語の両端に付いた記号の線・点の読み違い（CN211:、610°、TB6)）を落とす。括弧の対は残す。"""
    while token and token[-1] in _EDGE_PUNCT and not (token[-1] == ")" and "(" in token):
        token = token[:-1]
    while token and token[0] in _EDGE_PUNCT and not (token[0] == "(" and ")" in token):
        token = token[1:]
    return token


_REF_PATTERN = re.compile(rf"\(({SHEET_REF})\)|({SHEET_REF})(?:へ|より)")
RELAY_REF_CLASSES = {"relay_coil", "contact_a", "contact_b"}


_CONFUSABLE = ("B8", "I1TL!|", "O0DQ", "NHY", "S5", "Z2", "G6")
_CLASS_OF = {c: i for i, group in enumerate(_CONFUSABLE) for c in group}
_WRAP_NOISE = re.compile(r"[0-9I1|!.()]{1,3}")


def page_lexicon(items: list[TextItem]) -> Counter:
    """図面の中で読めた語とその出現回数。同じ器具番号は図面の中で何度も書かれる。"""
    return Counter(t for i in items for t in (_clean_token(w) for w in normalize(i["text"]).split()) if t)


def _confusable(a: str, b: str) -> bool:
    return len(a) == len(b) and all(x == y or _CLASS_OF.get(x, -1) == _CLASS_OF.get(y, -2) for x, y in zip(a, b, strict=True))


def correct_token(token: str, lexicon: Counter) -> str:
    """読み違えた語を、図面の中で 2 倍以上多く読めている見分けにくい語に直す。

    数字だけの 3 桁以下の語（電線番号・端子番号）は直さない。
    """
    if len(token) < 3 or (token.isdigit() and len(token) < 4):
        return token
    own = lexicon.get(token, 0)
    best, best_n = token, max(2, 2 * own) - 1
    for word, n in lexicon.items():
        if n <= best_n or word == token or len(word) < 3 or not any(c.isalpha() for c in word):
            continue
        # 接点ラベルの左には端子番号と接点の線（12|33LA）がくっつく。数字と英字を含む器具番号だけを対象にする。
        wrapped = (token.endswith(word) and _WRAP_NOISE.fullmatch(token[: -len(word)]) is not None
                   and any(c.isdigit() for c in word))
        if _confusable(token, word) or wrapped:
            best, best_n = word, n
    return best


def symbol_label(items: list[TextItem], box: Any, class_name: str, lexicon: Counter | None = None) -> str:
    """記号枠の内側の文字から、その記号のラベルを作る。"""
    words = reading_order(words_in_box(items, box), column_major=class_name == "connector")
    texts = [normalize(w["text"]) for w in words]
    if class_name == "other" and texts:
        ref = _REF_PATTERN.search(texts[0].replace(" ", ""))
        if ref:
            return f"{ref.group(1) or ref.group(2)}へ"
    tokens = [t for t in (_clean_token(t) for t in texts) if t]
    if class_name in TERMINAL_CLASSES:
        tokens = [t for t in tokens if not _TERMINAL_NOISE.fullmatch(t)]
    elif class_name == "other":
        tokens = [t for t in tokens if len(t) > 1]
    if lexicon is not None:
        tokens = [correct_token(t, lexicon) for t in tokens]
    if class_name in RELAY_REF_CLASSES:
        tokens = [repair_device_token(t) for t in tokens]
    return " ".join(tokens)


_FUNCTION_NUMBER_D = re.compile(r"(\d{2})[0O](-\d{1,2})")
_LEADING_ZERO_CODE = re.compile(r"[0O](\d{3})")


def repair_device_token(token: str) -> str:
    """接点・コイルの器具番号で、小さな D を 0 と読んだ語を直す（従来のラベル規則 evaluate_label_word_rules から移した）。

    - 器具番号の番号は 2 桁までなので、830-1 は 83D-1。
    - 電線番号・端子番号は 0 で始まらないので、0010 は D010。
    """
    if match := _FUNCTION_NUMBER_D.fullmatch(token):
        return f"{match.group(1)}D{match.group(2)}"
    if match := _LEADING_ZERO_CODE.fullmatch(token):
        return f"D{match.group(1)}"
    return token


def best_label(crop_label: str, page_label: str, lexicon: Counter) -> str:
    """拡大読みのラベルを基本とし、その器具番号が図面の中で他に読めず、ページ読みの器具番号は
    何度も読めているときだけページ読みを採る。"""
    if lexicon.get(device_token(crop_label), 0) < 2 <= lexicon.get(device_token(page_label), 0):
        return page_label
    return crop_label


# 器具番号の形: 2 桁以下の番号＋英字（33HB、83D-1、20V0。英字の O を 0 と読んだ 430F も含む）か、英字＋数字（D010、HV2323、CN27）。
_DEVICE_SHAPE = re.compile(r"\d{1,2}0?[A-Z][A-Z0-9]*(?:-\d{1,2})?|[A-Z]{1,4}\d{0,4}[A-Z]?(?:-\d{1,2})?")


def doubtful_label(label: str) -> bool:
    """接点・コイルのラベルが器具番号の形をしていない。

    小さな文字の D・O を 0 と読む（830-1、0010）、隣の線を 1 と読む（143E）などの読み違いがここに入る。
    3 桁以上の数字で始まるラベルは、器具番号の英字を数字と読んだものとみなす。
    """
    tokens = normalize(label).split()
    if not tokens or (tokens[0].isdigit() and len(tokens[0]) >= 3):
        return True
    return _DEVICE_SHAPE.fullmatch(device_token(label).split("(")[0]) is None


def reread_label(image: Image.Image, box: Any, class_name: str, label: str, reader: LabelReader | None) -> str | None:
    """接点・コイルの OCR ラベルが器具番号の形でないとき、別の読み手（ローカル LLM）に読み直させる。

    読み直した結果も器具番号の形をしているときだけ、その結果を返す。それ以外は None。
    日本語のラベル（盤異常 など）は器具番号ではないので読み直さない（名称の読み直しは reread_texts）。
    """
    if reader is None or class_name not in RELAY_REF_CLASSES or not doubtful_label(label) or _has_cjk(label):
        return None
    answer = normalize(reader(image, box) or "")
    return answer if answer and not doubtful_label(answer) else None


def reread_texts(image: Image.Image, texts: list[dict[str, Any]], reader: LabelReader | None) -> int:
    """日本語の名称・注記（リレー表・表題欄の外）を別の読み手（ローカル LLM）に読み直させる。

    OCR は日本語を簡体字に寄せて読み違える（盤→盐、記→记）。読み直した結果の最初の日本語の行に置き換え、
    元の読みは ocr_text に残す。戻り値は置き換えた件数。
    """
    if reader is None:
        return 0
    count = 0
    for text in texts:
        if text["kind"] not in ("name", "note") or text["zone"]:
            continue
        lines = [" ".join(line.split()).strip("$*#`") for line in (reader(image, text["box"]) or "").splitlines()]
        answer = next((line for line in lines if _has_cjk(line)), None)
        if answer:
            text["ocr_text"], text["text"], text["source"] = text["text"], answer, "llm"
            count += 1
    return count


def sheet_number_candidates(items: list[TextItem], width: int, height: int) -> list[dict[str, Any]]:
    """シート番号の候補。表題欄が破れて末尾の 1 文字が欠けた読み（ER2107）は、欄外の完全な番号
    （ER21070）に寄せる。改訂欄の図番（ER21029D04）はシート番号の 3 文字先まで続くので対象にしない。"""
    from services.sheet_number_service import pick_sheet_number_candidates

    candidates = pick_sheet_number_candidates(items, width, height)
    merged = []
    for candidate in candidates:
        text = candidate["text"]
        if any(c["text"].startswith(text) and len(c["text"]) == len(text) + 1 for c in candidates):
            continue
        prefixes = [c["score"] for c in candidates if text.startswith(c["text"]) and len(text) == len(c["text"]) + 1]
        merged.append({**candidate, "score": max([candidate["score"], *prefixes])})
    return sorted(merged, key=lambda c: c["score"], reverse=True)


def sheet_references(items: list[TextItem], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """括弧つき・「へ」「より」つきのシート番号を参照として返す。

    接点・コイルの枠の内側にあるものはそのリレーのコイルがあるシート（kind="relay"）、
    それ以外は配線の行先・来先（kind="wire"）とする。relay には枠の記号の symbol_id を付ける。
    """
    relays = [(s.get("id"), _as_box(s["bbox"])) for s in symbols if s.get("class_name") in RELAY_REF_CLASSES]
    refs = []
    for item in items:
        compact = normalize(item["text"]).replace(" ", "")
        for match in _REF_PATTERN.finditer(compact):
            center = _center(item["box"])
            owner = next((relay for relay in relays if _inside(center, relay[1])), None)
            ref = {
                "target_sheet": match.group(1) or match.group(2), "kind": "relay" if owner else "wire",
                "text": item["text"], "box": [round(v, 1) for v in item["box"]],
            }
            if owner:
                ref["symbol_id"] = owner[0]
            refs.append(ref)
    return refs


_TERMINAL_CELL = re.compile(r"^\d{1,2}-\d{1,2}-\d{1,2}$")
_TERMINAL_LOOSE = re.compile(r"^\d{1,2}[-~]?\d{1,2}[-~]?\d{1,2}$")
_POSITION = re.compile(r"^\d{1,2}[A-Z]$")
RELAY_TABLE_TOP = 0.7
RELAY_TABLE_RIGHT = 0.75
RELAY_CELLS_W = 0.06
RELAY_DEVICE_W = 0.035


def _position(value: str) -> str:
    """行列欄（列番号＋行記号）。行記号に O・I は無いので、末尾の 0 は D の読み違いとして直す。"""
    if _POSITION.match(value):
        return value
    if re.fullmatch(r"\d{2,3}", value) and value.endswith("0"):
        return f"{value[:-1]}D"
    return ""


def _terminal_cells(items: list[TextItem], width: int, height: int) -> list[tuple[str, TextItem]]:
    """端子欄の文字（5-9-1 など）。ハイフンが抜けた読み（591）は同じ図面で正しく読めた端子に寄せる。"""
    area = [i for i in items if _center(i["box"])[1] >= RELAY_TABLE_TOP * height and _center(i["box"])[0] <= RELAY_TABLE_RIGHT * width]
    values = [(normalize(i["text"]).replace(" ", "").replace("~", "-"), i) for i in area]
    known = {v.replace("-", ""): v for v, _ in values if _TERMINAL_CELL.match(v)}
    cells = []
    for value, item in values:
        if _TERMINAL_CELL.match(value):
            cells.append((value, item))
        elif _TERMINAL_LOOSE.match(value) and value.replace("-", "") in known:
            cells.append((known[value.replace("-", "")], item))
    return cells


def _terminal_columns(cells: list[tuple[str, TextItem]], width: int) -> list[list[tuple[str, TextItem]]]:
    columns: list[list[tuple[str, TextItem]]] = []
    for cell in sorted(cells, key=lambda c: _center(c[1]["box"])[0]):
        cx = _center(cell[1]["box"])[0]
        if columns and abs(_center(columns[-1][-1][1]["box"])[0] - cx) <= 0.012 * width:
            columns[-1].append(cell)
        else:
            columns.append([cell])
    return columns


def _column_blocks(column: list[tuple[str, TextItem]]) -> list[tuple[list[tuple[str, TextItem]], float, list[list[tuple[str, TextItem]]]]]:
    """端子欄の 1 列を、行の間隔が空いたところ（次のブロックの見出し行）で区切る。"""
    column = sorted(column, key=lambda c: c[1]["box"][1])
    pitch = sorted(b[1]["box"][1] - a[1]["box"][1] for a, b in zip(column, column[1:], strict=False))
    step = pitch[len(pitch) // 2] if pitch else 0.0
    blocks: list[list[tuple[str, TextItem]]] = []
    for cell in column:
        if blocks and cell[1]["box"][1] - blocks[-1][-1][1]["box"][1] <= 1.5 * step:
            blocks[-1].append(cell)
        else:
            blocks.append([cell])
    if not step:
        step = sum(c[1]["box"][3] - c[1]["box"][1] for c in column) / len(column)
    return [(block, step, blocks) for block in blocks]


def parse_relay_table(items: list[TextItem], width: int, height: int) -> list[dict[str, str]]:
    """図面下部のリレー表を行ごとに読む。

    表は「器具番号・端子・接点・シート・行列」のブロックの並びで、各ブロックは端子の行が縦に並ぶ。
    見出しの文字は太字で読み落としやすいので、端子欄の文字を手がかりに列とブロックを決め、
    器具番号はブロックの左、接点・シート・行列は各行の右から書式で読み分ける。
    """
    lexicon = page_lexicon(items)
    rows: list[dict[str, str]] = []
    for column in _terminal_columns(_terminal_cells(items, width, height), width):
        for block, step, blocks in _column_blocks(column):
            rows.extend(_block_rows(items, block, width, step, blocks, lexicon))
    return rows


def _block_rows(
    items: list[TextItem], block: list[tuple[str, TextItem]], width: int, step: float,
    column_blocks: list[list[tuple[str, TextItem]]], lexicon: Counter,
) -> list[dict[str, str]]:
    term_x0 = min(c[1]["box"][0] for c in block)
    term_x1 = max(c[1]["box"][2] for c in block)
    top = block[0][1]["box"][1]
    row_h = sum(c[1]["box"][3] - c[1]["box"][1] for c in block) / len(block)
    # 器具番号の欄は空の端子行の横まで続く。下に次のブロックがあればその見出し行の手前まで。
    below = [b[0][1]["box"][1] for b in column_blocks if b[0][1]["box"][1] > top]
    bottom = min(below) - 1.5 * step if below else top + 3.6 * step
    bottom = max(bottom, block[-1][1]["box"][3])
    device_lines = sorted(
        (i for i in items
         if term_x0 - RELAY_DEVICE_W * width <= _center(i["box"])[0] < term_x0
         and top <= _center(i["box"])[1] <= bottom
         and i["box"][3] - i["box"][1] >= 0.75 * row_h),
        key=lambda i: i["box"][1],
    )
    device = " ".join(correct_token(_clean_token(normalize(i["text"]).replace(" ", "")), lexicon) for i in device_lines[:2])
    rows = []
    for terminal, item in block:
        y0, y1 = item["box"][1], item["box"][3]
        row = {"device": device, "terminal": terminal, "contact": "", "sheet": "", "position": ""}
        for cell in sorted(items, key=lambda i: i["box"][0]):
            cx, cy = _center(cell["box"])
            if not (term_x1 - 2 <= cx <= term_x1 + RELAY_CELLS_W * width and y0 - 3 <= cy <= y1 + 3) or cell is item:
                continue
            value = normalize(cell["text"]).replace(" ", "")
            if value in {"A", "B"}:
                row["contact"] = value.lower()
            elif value == "本" or re.fullmatch(SHEET_REF, value):
                row["sheet"] = value
            elif _position(value):
                row["position"] = _position(value)
        rows.append(row)
    return rows


OtherKind = Literal["destination", "card", "terminal_unit", "equipment", "breaker", "unknown"]
# 「その他」の記号の区分（#42。区分は暫定で、貴社と合意してから確定する）。枠の中の文字の書式で決める。
_CARD = re.compile(r"D[I1L|][O0]-?\d|D[O0]T-?\d|-C[I1L|][RF]-?\d|-C\d[RF]\d")
_TERMINAL_UNIT = re.compile(r"RCVTB|TB\(\d")
_BREAKER = re.compile(r"MCCB|NFB|ELB|ACB|VCB|遮断器|ブレーカ")
_EQUIPMENT = re.compile(r"^\d?R[A-Z]{2,}|\bL[O0]C\b|\bLN[Z2]\b|\bLMZ\b|\dR\s?P/C|盤|装置|ユニット")


def classify_other(items: list[TextItem], box: Any, label: str) -> OtherKind:
    """「その他」の記号を、行先・来先、カード、端子台ユニット、盤・装置、遮断器に分ける。どれにも当たらなければ unknown。"""
    text = normalize(" ".join(w["text"] for w in words_in_box(items, box)))
    compact = text.replace(" ", "")
    if label.endswith("へ"):
        return "destination"
    if _BREAKER.search(compact):
        return "breaker"
    if _CARD.search(compact):
        return "card"
    if _TERMINAL_UNIT.search(compact):
        return "terminal_unit"
    if any(_EQUIPMENT.search(t) for t in text.split()) or _EQUIPMENT.search(compact):
        return "equipment"
    # 盤・カードの枠の中にも行先のシート番号が書かれるので、行先の判定は最後にする。
    if _REF_PATTERN.search(compact):
        return "destination"
    return "unknown"


def classify_region(items: list[TextItem], box: Any, width: int, height: int) -> RegionType:
    """領域枠の種類を、大きさ・位置と枠の内側の文字から決める。"""
    x0, y0, x1, y1 = _as_box(box)
    # 検出された作図領域は図面枠より小さく出ることがある（幅 0.57・高さ 0.49 など）。盤・カードの枠は高さ 0.11 以下の帯。
    if (x1 - x0) >= 0.4 * width and (y1 - y0) >= 0.4 * height:
        return "drawing_area"
    text = "".join(normalize(w["text"]) for w in words_in_box(items, (x0, y0, x1, y1)))
    if x0 >= 0.55 * width and y0 >= 0.7 * height and ("番号" in text or "名称" in text):
        return "title_block"
    if x1 <= 0.55 * width and y0 >= 0.6 * height and ("器具" in text or "行列" in text):
        return "relay_table"
    if "LOC" in text:
        return "loc"
    return "panel"


TITLE_BLOCK_WORDS = ("設備記号", "部分単位", "シート番号", "名称", "改訂")
TITLE_BLOCK_REVISION_H = 0.1


def relay_table_box(items: list[TextItem], width: int, height: int) -> Box | None:
    """リレー表の範囲。端子欄の文字から決めたブロックの、見出し行から器具番号欄・行列欄までを合わせる。"""
    boxes = []
    for column in _terminal_columns(_terminal_cells(items, width, height), width):
        for block, step, _ in _column_blocks(column):
            x0 = min(c[1]["box"][0] for c in block) - RELAY_DEVICE_W * width
            x1 = max(c[1]["box"][2] for c in block) + RELAY_CELLS_W * width
            top = block[0][1]["box"][1]
            boxes.append((x0, top - 1.2 * step, x1, max(block[-1][1]["box"][3], top + 3.6 * step)))
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def title_block_box(items: list[TextItem], width: int, height: int) -> Box | None:
    """表題欄の範囲。右下の「設備記号」「名称」などの見出しから、上の改訂欄を含めて右下の角まで。"""
    anchors = [
        i for i in items
        if any(word in normalize(i["text"]) for word in TITLE_BLOCK_WORDS)
        and _center(i["box"])[0] >= 0.6 * width and _center(i["box"])[1] >= 0.75 * height
    ]
    if not anchors:
        return None
    x0 = min(i["box"][0] for i in anchors) - 0.01 * width
    y0 = min(i["box"][1] for i in anchors) - TITLE_BLOCK_REVISION_H * height
    return (x0, y0, float(width), float(height))


def text_zones(items: list[TextItem], width: int, height: int) -> list[dict[str, Any]]:
    """図面の中で文字だけが並ぶ欄（リレー表・表題欄）。ここに出た記号の検出は文字の誤検出とみなす。"""
    zones = []
    for kind, box in (("relay_table", relay_table_box(items, width, height)), ("title_block", title_block_box(items, width, height))):
        if box is not None:
            zones.append({"type": kind, "bbox": {k: round(v, 1) for k, v in zip(("x0", "y0", "x1", "y1"), box, strict=True)}})
    return zones


def zone_of(box: Any, zones: list[dict[str, Any]]) -> str | None:
    center = _center(_as_box(box))
    return next((z["type"] for z in zones if _inside(center, _as_box(z["bbox"]))), None)



TEXT_NAME_MARGIN_PX = 20


def _has_cjk(text: str) -> bool:
    return any(unicodedata.east_asian_width(c) in "WF" for c in text)


def classify_texts(items: list[TextItem], symbols: list[dict[str, Any]], zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """読めた文字を書式で分ける: 器具番号・番号（端子番号/電線番号）・行先/来先・リレー端子・名称・注記。

    日本語の文字は、記号の枠（少し広げた範囲）に入れば装置名・信号名称としてその記号に結び付け、
    入らなければ注記とする。
    """
    out = []
    for item in items:
        text = normalize(item["text"])
        compact = text.replace(" ", "")
        center = _center(item["box"])
        owner = next((s["id"] for s in symbols if _inside(center, _as_box(s["bbox"]))), None)
        if _REF_PATTERN.search(compact):
            kind = "destination"
        elif _TERMINAL_CELL.match(compact):
            kind = "relay_terminals"
        elif _has_cjk(text):
            owner = owner or next(
                (s["id"] for s in symbols if _inside(center, _as_box(s["bbox"]), TEXT_NAME_MARGIN_PX)), None)
            kind = "name" if owner else "note"
        elif re.fullmatch(r"\d{1,4}", compact):
            kind = "number"
        elif re.search(r"[A-Z]", compact) and re.search(r"\d", compact):
            kind = "device_number"
        else:
            kind = "other"
        out.append({
            "text": item["text"], "kind": kind, "symbol_id": owner, "zone": zone_of(item["box"], zones),
            "score": round(item["score"], 3), "box": [round(v, 1) for v in item["box"]],
        })
    return out


def _relay_key(label: str) -> tuple[str, ...]:
    text = re.sub(rf"\(?{SHEET_REF}\)?", " ", normalize(label))
    return tuple(match_key(t) for t in text.split()[:2])


def same_relay_device(label: str, device: str) -> bool:
    """記号のラベルがリレー表の器具番号（33HB FV2306）と同じ器具か。2 語目はラベルにあるときだけ比べる。"""
    key, other = _relay_key(device), _relay_key(label)
    return bool(key) and other[:1] == key[:1] and (len(other) < 2 or other[1:2] == key[1:2])


def link_relay_table(rows: list[dict[str, str]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """リレー表の器具番号ごとに、同じ図面のリレーコイル・接点を器具番号で照合する。

    コイルが見つからない器具、または「本」（この図面）と書かれた接点が見つからない器具は要確認とする。
    """
    devices: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        devices.setdefault(row["device"], []).append(index)
    links = []
    for device, row_indexes in devices.items():
        matched = [s for s in symbols
                   if s.get("class_name") in RELAY_REF_CLASSES and same_relay_device(s.get("label", ""), device)]
        coils = [s["id"] for s in matched if s["class_name"] == "relay_coil"]
        contacts = [s["id"] for s in matched if s["class_name"] != "relay_coil"]
        on_sheet = sum(1 for i in row_indexes if rows[i]["sheet"] == "本")
        links.append({
            "device": device, "rows": row_indexes, "coil_symbol_ids": coils, "contact_symbol_ids": contacts,
            "status": "linked" if coils and len(contacts) >= on_sheet else "要確認",
        })
    return links


def extract_text_structure(
    image: Image.Image, symbols: list[dict[str, Any]], regions: list[dict[str, Any]], reader: Any,
    label_reader: LabelReader | None = None, text_reader: LabelReader | None = None,
) -> dict[str, Any]:
    """構造 JSON に入れる文字の結果を作り、各記号のラベルを書き込む。

    ページ全体の OCR は 1 回だけ。コネクタ・接点・コイルは枠ごとの拡大 OCR も使う。
    label_reader があれば器具番号の形でない接点・コイルのラベルを、text_reader があれば日本語の名称・注記を読み直す。
    """
    width, height = image.size
    items = read_page_text(image, reader)
    lexicon = page_lexicon(items)
    zones = text_zones(items, width, height)
    for symbol in symbols:
        class_name = symbol.get("class_name") or ""
        label = symbol_label(items, symbol["bbox"], class_name, lexicon)
        if class_name in CROP_OCR_CLASSES:
            crop_items = read_crop_text(image, symbol["bbox"], reader)
            crop = symbol_label(crop_items, symbol["bbox"], class_name, lexicon)
            label = best_label(crop, label, lexicon)
            if class_name in TERMINAL_NUMBER_CLASSES:
                # terminal_numbers は語の位置で器具名（黒丸の左）と番号（右）を分けるので、OCR の行を語に割って渡す。
                x0, y0, x1, y1 = _as_box(symbol["bbox"])
                pad = CROP_PADDING_PX
                symbol["text_items"] = words_in_box(crop_items, (x0 - pad, y0 - pad, x1 + pad, y1 + pad))
        reread = reread_label(image, symbol["bbox"], class_name, label, label_reader)
        if reread is not None:
            symbol["ocr_label"], symbol["label_source"], label = label, "llm", reread
        symbol["label"] = label
        symbol["label_candidate"] = {"text": label, "match_text": label, "tokens": [], "rejected_tokens": []}
        if class_name == "other":
            symbol["other_kind"] = classify_other(items, symbol["bbox"], label)
        zone = zone_of(symbol["bbox"], zones)
        if zone:
            symbol["text_zone"] = zone
    rows = parse_relay_table(items, width, height)
    texts = classify_texts(items, symbols, zones)
    reread_texts(image, texts, text_reader)
    return {
        "texts": texts,
        "relay_table": {"rows": rows, "links": link_relay_table(rows, symbols)},
        "sheet_references": sheet_references(items, symbols),
        "sheet_number_candidates": sheet_number_candidates(items, width, height),
        "regions": [
            *({**r, "region_type": classify_region(items, r["bbox"], width, height), "source": "detection"} for r in regions),
            *({"bbox": z["bbox"], "region_type": z["type"], "source": "text"} for z in zones),
        ],
    }

