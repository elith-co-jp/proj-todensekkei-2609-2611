"""I/O 表（他盤との信号の一覧）から、接点の外部入力と信号名を入れる（DD-0050）。

図面だけでは、他盤（DDC・SAM）から来る信号かどうかと、その名前が決まらない（資料 03 章）。I/O 表を 1 つ登録すると、
回路を組むたびに、器具番号（I/O の点番号「D001 B405」）が一致する接点へ次を入れる。人が入れた属性は変えない。

- 入力の行: 属性 external_io = yes（図面の範囲外から来る入力。スイッチに出る）と signal_name（論理図の名前）
- 出力の行: signal_name だけ

表は CSV（UTF-8 / Shift_JIS、カンマ・タブ区切り）か Excel（.xlsx の最初のシート）。見出しの行から列を決める。
器具番号の列: 器具番号・I/O・点番号・信号番号・アドレス。名前の列: 信号名・名称・内容。向きの列（任意）: 入出力・方向・種別。
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from typing import Any

from analysis import text_extraction as tx

_DEVICE_HEADS = ("器具番号", "I/O", "IO", "点番号", "信号番号", "アドレス", "DEVICE", "TAG")
_NAME_HEADS = ("信号名", "名称", "内容", "NAME")
_DIRECTION_HEADS = ("入出力", "方向", "種別", "DIRECTION")
_OUTPUT = re.compile(r"出力|^DO$|^OUT|OUTPUT", re.IGNORECASE)
CONTACT_CLASSES = ("contact_a", "contact_b", "push_button")
MAX_ROWS = 20_000


def _path():
    from database import DATA_DIR

    return DATA_DIR / "io_table.json"


def _cell(row: list[str], index: int | None) -> str:
    return row[index] if index is not None and index < len(row) else ""


def _rows_from_cells(cells: Iterable[list[Any]]) -> list[dict[str, str]]:
    table = [[str(c if c is not None else "").strip() for c in row] for row in cells]
    table = [row for row in table if any(row)]
    for head_index, head in enumerate(table[:10]):
        normalized = [tx.normalize(h).replace(" ", "") for h in head]

        def column(names: tuple[str, ...], normalized: list[str] = normalized) -> int | None:
            return next((i for i, h in enumerate(normalized) if h and any(n in h for n in names)), None)

        device, name = column(_DEVICE_HEADS), column(_NAME_HEADS)
        if device is None or name is None or device == name:
            continue
        direction = column(_DIRECTION_HEADS)
        rows = []
        for row in table[head_index + 1:]:
            if not _cell(row, device):
                continue
            rows.append({"device": _cell(row, device), "name": _cell(row, name),
                         "direction": "output" if _OUTPUT.search(_cell(row, direction)) else "input"})
            if len(rows) > MAX_ROWS:
                raise ValueError(f"I/O 表の行が多すぎます（{MAX_ROWS} 行まで）")
        return rows
    raise ValueError("I/O 表の見出しが見つかりません（「器具番号」と「信号名」の列が要ります）")


def parse(data: bytes, file_name: str = "") -> list[dict[str, str]]:
    """CSV / Excel の I/O 表を行（device, name, direction）にする。"""
    if file_name.lower().endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        from openpyxl import load_workbook

        sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        return _rows_from_cells(list(r) for r in sheet.iter_rows(values_only=True))
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("I/O 表の文字コードを読めません（UTF-8 か Shift_JIS の CSV にしてください）")
    delimiter = "\t" if text.count("\t") > text.count(",") else ","
    return _rows_from_cells(csv.reader(io.StringIO(text), delimiter=delimiter))


def save(rows: list[dict[str, str]], file_name: str) -> dict[str, Any]:
    from services.jst import now_iso_jst

    doc = {"file_name": file_name, "updated_at": now_iso_jst(), "rows": rows}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return summary(doc)


def load() -> dict[str, Any] | None:
    path = _path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clear() -> None:
    _path().unlink(missing_ok=True)


def summary(doc: dict[str, Any] | None) -> dict[str, Any]:
    if doc is None:
        return {"loaded": False, "file_name": None, "updated_at": None, "rows": 0, "inputs": 0, "preview": []}
    rows = doc.get("rows") or []
    return {"loaded": True, "file_name": doc.get("file_name"), "updated_at": doc.get("updated_at"), "rows": len(rows),
            "inputs": sum(1 for r in rows if r.get("direction") != "output"), "preview": rows[:5]}


def _key(text: str) -> tuple[str, ...]:
    words = tx.normalize(re.sub(rf"\(?{tx.SHEET_REF}\)?", " ", tx.normalize(text or ""))).split()
    return tuple(tx.match_key(w) for w in words[:2])


def _matches(row_key: tuple[str, ...], label_key: tuple[str, ...]) -> bool:
    return bool(row_key) and bool(label_key) and row_key[0] == label_key[0] and (
        len(row_key) < 2 or len(label_key) < 2 or row_key[1] == label_key[1])


def apply(pages: list[dict[str, Any]], rows: list[dict[str, str]] | None = None) -> int:
    """図面（load_pages の形）の接点に I/O 表の属性を入れる。入れた記号の数を返す。

    人が登録した図面では、入っている属性を変えない。AI 解析だけの図面（source = ai）では、図面の文字から推定した
    名前より I/O 表を優先する。
    """
    if rows is None:
        doc = load()
        rows = (doc or {}).get("rows") or []
    if not rows:
        return 0
    keyed = [(_key(r["device"]), r) for r in rows]
    changed = 0
    for page in pages:
        overwrite = page.get("source") == "ai"
        for symbol in page.get("symbols") or []:
            if symbol.get("class_key") not in CONTACT_CLASSES:
                continue
            label_key = _key(symbol.get("label") or "")
            row = next((r for k, r in keyed if _matches(k, label_key)), None)
            if row is None:
                continue
            attributes = dict(symbol.get("attributes") or {})
            before = dict(attributes)
            put = attributes.__setitem__ if overwrite else attributes.setdefault
            if row.get("direction") != "output":
                put("external_io", "yes")
            if row.get("name"):
                put("signal_name", row["name"])
            if attributes != before:
                symbol["attributes"] = attributes
                changed += 1
    return changed
