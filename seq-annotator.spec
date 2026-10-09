# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 仕様ファイル（1 ファイル exe）。

ビルド:
    pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
    pip install -r requirements.txt -r requirements-ml.txt "pyinstaller>=6.17,<7"
    cd frontend && npm ci && npm run build && cd ..
    pyinstaller seq-annotator.spec --noconfirm

生成物:
    dist/TodenYOLO.exe  （Windows。Python 非同梱の PC でもそのまま動く）

Windows 用 exe は Windows 上でビルドすること（PyInstaller はクロスビルド不可）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

spec_dir = Path(SPECPATH)

# --- 同梱データ（ビルド済みフロントエンド） -----------------------------------
datas = [(str(spec_dir / "frontend" / "dist"), "frontend/dist")]
base_model = spec_dir / ".build-assets" / "yolov8n.pt"
if not base_model.is_file():
    raise FileNotFoundError("Run python scripts/fetch_base_model.py before packaging")
datas.append((str(base_model), "models"))
initial_model = spec_dir / "assets" / "models" / "yolo11n_all_symbols_best.pt"
if not initial_model.is_file():
    raise FileNotFoundError("assets/models/yolo11n_all_symbols_best.pt is missing")
datas.append((str(initial_model), "models"))
binaries = []
hiddenimports = []

# uvicorn / anyio は動的 import が多いのでサブモジュールを明示収集する
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("anyio")
hiddenimports += ["email.mime"]  # 念のため

# PyMuPDF: MuPDF の同梱バイナリまで丸ごと集める
try:
    d, b, h = collect_all("pymupdf")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:  # noqa: BLE001  未導入時は通常の import 解析に任せる
    pass

# 推論・学習は遅延 import。設定 YAML や動的に読むモジュールも同梱する。
for pkg in ("ultralytics", "torchvision", "rapidocr_onnxruntime", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["desktop.py"],
    pathex=[str(spec_dir), str(spec_dir / "analysis" / "tools")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TodenYOLO",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
