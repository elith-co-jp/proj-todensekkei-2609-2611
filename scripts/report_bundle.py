"""PyInstaller が EXE に詰めたファイルの大きさを、パッケージごとに集計して表示する（CI のログ用）。

EXE の作成時間と大きさは同梱するファイルの量で決まる。どのパッケージが大きいかをログに残し、
不要なものが入り込んだら気付けるようにする。読めなくてもビルドは止めない。

使い方: python scripts/report_bundle.py build/seq-annotator/PKG-00.toc
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

TOP = 20


def entries(toc_path: Path) -> list[tuple[str, int]]:
    """TOC の (EXE の中の名前, 元のファイル, 種類) から、存在するファイルの (名前, バイト数)。"""
    toc = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
    rows = next(item for item in toc if isinstance(item, list))
    out = []
    for row in rows:
        name, source = str(row[0]), Path(str(row[1]))
        if source.is_file():
            out.append((name.replace("\\", "/"), source.stat().st_size))
    return out


def group_of(name: str) -> str:
    parts = name.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def main() -> int:
    toc_path = Path(sys.argv[1])
    try:
        files = entries(toc_path)
    except (OSError, ValueError, SyntaxError, StopIteration) as caught:
        print(f"bundle report skipped: {caught}")
        return 0
    groups: Counter[str] = Counter()
    for name, size in files:
        groups[group_of(name)] += size
    print(f"bundled files: {len(files)}, total {sum(size for _, size in files) / 1e6:.1f} MB (before compression)")
    for group, size in groups.most_common(TOP):
        print(f"{size / 1e6:9.1f} MB  {group}")
    print("largest files:")
    for name, size in sorted(files, key=lambda item: -item[1])[:TOP]:
        print(f"{size / 1e6:9.1f} MB  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
