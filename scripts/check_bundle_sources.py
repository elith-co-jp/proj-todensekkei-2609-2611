"""フォルダ形式の EXE（DD-0040）に、リポジトリから図面・DB・文書のファイルが入っていないかを確かめる（CI 用）。

フォルダ形式では同梱物がそのままファイルとして並び、matplotlib の back.pdf や PyInstaller の base_library.zip の
ように、パッケージが持つファイルも名前だけでは見分けられない。そこで PyInstaller の TOC から、元のファイルが
リポジトリの中にあるもの（spec の datas で足したもの）だけを調べる。パッケージのファイルは調べない。

使い方: python scripts/check_bundle_sources.py build/seq-annotator/COLLECT-00.toc
見つかれば終了コード 1。
"""

from __future__ import annotations

import ast
import fnmatch
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# PyInstaller がビルド中に作るもの（base_library.zip など）。リポジトリのファイルではない
GENERATED = {"build", "dist"}
FORBIDDEN = ("*.pdf", "*.tif", "*.tiff", "*.db", "*.sqlite", "*.sqlite3", "*.zip", "*.7z", "*.rar",
             "*.doc", "*.docx", "*.xls", "*.xlsx", "*.ppt", "*.pptx", "*.csv")


def repo_sources(toc_path: Path) -> list[tuple[str, Path]]:
    toc = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
    rows = [row for item in toc if isinstance(item, list) for row in item if isinstance(row, (list, tuple))]
    out = []
    for row in rows:
        if len(row) < 2:
            continue
        source = Path(str(row[1]))
        try:
            rel = source.resolve().relative_to(REPO)
        except (ValueError, OSError):
            continue
        if rel.parts and rel.parts[0] in GENERATED:
            continue
        out.append((str(row[0]), source))
    return out


def main() -> int:
    files = repo_sources(Path(sys.argv[1]))
    bad = [(name, src) for name, src in files if any(fnmatch.fnmatch(src.name.lower(), p) for p in FORBIDDEN)]
    print(f"files from the repository: {len(files)}")
    for name, src in bad:
        print(f"forbidden: {name} <- {src}", file=sys.stderr)
    if not files:
        # 画面や重みは必ずリポジトリから入るので、0 件なら TOC を読み違えている。通さない
        print("no files from the repository were found in the TOC", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
