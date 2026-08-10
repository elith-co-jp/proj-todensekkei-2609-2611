@echo off
rem =====================================================================
rem  Annotator.exe を Windows 上でビルドするスクリプト。
rem  Python 3.11+ と Node.js 18+ を入れた Windows で実行してください。
rem  （生成した exe は Python 非同梱の Windows PC でそのまま動きます）
rem =====================================================================
setlocal

cd /d "%~dp0"

echo [1/4] Python 仮想環境を用意します...
if not exist ".venv" (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt pyinstaller || goto :error

echo [2/4] フロントエンドをビルドします...
pushd frontend
call npm ci || goto :error
call npm run build || goto :error
popd

echo [3/4] exe をビルドします...
pyinstaller seq-annotator.spec --noconfirm || goto :error

echo [4/4] 完了しました。
echo    生成物: dist\Annotator.exe
echo    ダブルクリックで起動し、既定ブラウザに UI が開きます。
goto :eof

:error
echo.
echo ビルドに失敗しました。上のログを確認してください。
exit /b 1
