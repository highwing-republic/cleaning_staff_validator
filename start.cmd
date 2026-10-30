@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo アプリの実行環境が見つかりません。
    echo 管理者に .venv のセットアップを依頼してください。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "start_app.py"
if errorlevel 1 (
    echo.
    echo 起動できませんでした。上の案内を確認してください。
    pause
    exit /b 1
)
