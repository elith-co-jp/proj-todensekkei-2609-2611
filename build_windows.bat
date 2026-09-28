@echo off
rem =====================================================================
rem  TodenYOLO.exe を Windows 上でビルドするスクリプト。
rem  Python 3.11+ と Node.js 18+ を入れた Windows で実行してください。
rem  （生成した exe は Python 非同梱の Windows PC でそのまま動きます）
rem =====================================================================
setlocal

cd /d "%~dp0"

echo [1/5] Python 仮想環境を用意します...
if not exist ".venv" (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu || goto :error
pip install -r requirements.txt -r requirements-ml.txt "pyinstaller>=6.17,<7" || goto :error

echo [2/5] フロントエンドをビルドします...
pushd frontend
call npm ci || goto :error
call npm run build || goto :error
popd

echo [3/5] exe をビルドします...
python scripts\fetch_base_model.py || goto :error
pyinstaller seq-annotator.spec --noconfirm || goto :error

echo [4/5] exe で短い学習と推論を確認します...
python scripts\smoke_packaged_ml.py dist\TodenYOLO.exe || goto :error

echo [5/5] 完了しました。
echo    生成物: dist\TodenYOLO.exe
echo    ダブルクリックで起動し、既定ブラウザに UI が開きます。
goto :eof

:error
echo.
echo ビルドに失敗しました。上のログを確認してください。
exit /b 1
