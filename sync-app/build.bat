@echo off
REM 打包为单个 exe（无控制台窗口，自带图标）
cd /d %~dp0
pyinstaller --noconfirm --clean --onefile --noconsole ^
  --name "榜样知识库同步" ^
  --icon assets\icon.ico ^
  --add-data "assets;assets" ^
  launcher.py
echo.
echo 打包完成: dist\榜样知识库同步.exe
pause
