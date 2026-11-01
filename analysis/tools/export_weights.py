"""配布する学習済み重みから、学習した環境のパスや作業リポジトリの情報を消して保存する。

YOLO の重みには学習時の data.yaml・出力先・元の重みの絶対パスと git の情報が入る。推論には使わないので、
ファイル名だけを残して書き出す。

Example:
    python analysis/tools/export_weights.py runs/final/all/train/weights/last.pt assets/models/symbols.pt
"""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

PATH_ARGS = ("data", "project", "model", "save_dir", "name", "pretrained", "resume")


def _file_name(value: str) -> str:
    return PureWindowsPath(value).name if "\\" in value else PurePosixPath(value).name


def _strip_paths(args: dict[str, Any]) -> dict[str, Any]:
    args = dict(args)
    for key in PATH_ARGS:
        if isinstance(args.get(key), str) and ("/" in args[key] or "\\" in args[key]):
            args[key] = _file_name(args[key])
    return args


def strip_metadata(checkpoint: dict[str, Any]) -> dict[str, Any]:
    # 学習時の設定は train_args と、モデル本体・EMA の args の 3 か所に入っている。
    for key in ("model", "ema"):
        module = checkpoint.get(key)
        if module is not None and isinstance(getattr(module, "args", None), dict):
            module.args = _strip_paths(module.args)
    return {**checkpoint, "train_args": _strip_paths(checkpoint.get("train_args") or {}), "git": {}, "optimizer": None}


def main() -> None:
    import torch

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("target")
    args = parser.parse_args()
    checkpoint = torch.load(args.source, map_location="cpu", weights_only=False)
    torch.save(strip_metadata(checkpoint), args.target)


if __name__ == "__main__":
    main()

