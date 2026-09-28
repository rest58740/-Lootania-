@echo off
chcp 65001 >nul
rem 想要自己打包成 exe（給沒有 Python 的朋友用）時才需要執行
echo 正在把裝備截圖打包成 exe……
echo.
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "裝備截圖" "%~dp0裝備截圖.pyw"
echo.
if %errorlevel%==0 (
    echo 完成！exe 在 dist 資料夾裡。
) else (
    echo 打包失敗，請看上面的錯誤訊息。
)
echo.
pause
