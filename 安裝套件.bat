@echo off
chcp 65001 >nul
echo 正在安裝裝備截圖需要的 Python 套件……
echo.
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
echo.
if %errorlevel%==0 (
    echo 安裝完成！雙擊「裝備截圖.pyw」就能開始使用。
) else (
    echo 安裝失敗：請確認已經安裝 Python，並且安裝時有勾選「Add python.exe to PATH」。
)
echo.
pause
