"""以前の取り決め（配線がリレーコイルの中を通り抜ける）で登録した図面を、コイルに端子を付ける取り決めに移す（DD-0009 §6）。

エディタの書き出し zip（bundle.json）を読み、コイルを通り抜ける配線をコイルの枠の出入りの点で 2 本に分けて端子を付けた
zip を書く。bundle.json 以外のファイルはそのまま写す。分けられなかったコイル（通り抜ける配線が 2 本以上、または無い）は
図面ごとに表示するので、エディタで直す。

Example:
    python analysis/tools/coil_wiring_gold.py data/gold_a_fixed.zip --out data/gold_a_coil.zip
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.coil_wiring import coil_issues, split_through_wires  # noqa: E402


def convert(src: Path, dst: Path) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "bundle.json":
                bundle = json.loads(data)
                for project in bundle["projects"]:
                    w, h = int(project["image_width"]), int(project["image_height"])
                    symbols, connections, counts = split_through_wires(project["symbols"], project["connections"], w, h)
                    project["symbols"], project["connections"] = symbols, connections
                    left = Counter(i["kind"] for i in coil_issues(symbols, connections, w, h))
                    rows[project.get("sheet_no") or project["name"]] = {**counts, **{f"left_{k}": v for k, v in left.items()}}
                data = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
            zout.writestr(info, data)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = convert(args.src, args.out)
    total: Counter = Counter()
    for sheet, counts in sorted(rows.items()):
        total.update(counts)
        print(sheet, json.dumps(counts, ensure_ascii=False))
    print("total", json.dumps(dict(total), ensure_ascii=False))


if __name__ == "__main__":
    main()

