# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 仕様ファイル（1 ファイル exe）。

ビルド:
    pip install -r requirements.txt pyinstaller
    cd frontend && npm ci && npm run build && cd ..
    pyinstaller seq-annotator.spec --noconfirm

生成物:
    dist/SeqAnnotator.exe  （Windows。Python 非同梱の PC でもそのまま動く）

Windows 用 exe は Windows 上でビルドすること（PyInstaller はクロスビルド不可）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

spec_dir = Path(SPECPATH)

# --- 同梱データ（ビルド済みフロントエンド） -----------------------------------
datas = [(str(spec_dir / "frontend" / "dist"), "frontend/dist")]
binaries = []
hiddenimports = []

# uvicorn / anyio は動的 import が多いのでサブモジュールを明示収集する
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("anyio")
hiddenimports += ["passlib.handlers", "email.mime"]  # 念のため

# PyMuPDF（fitz）: MuPDF の同梱バイナリまで丸ごと集める
for pkg in ("fitz", "pymupdf"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:  # noqa: BLE001  未導入パッケージ名は無視
        pass

a = Analysis(
    ["desktop.py"],
    pathex=[str(spec_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SeqAnnotator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
