@echo off
setlocal
cd /d "%~dp0"
title dcwatch

python -c "print(1)" >nul 2>nul
if errorlevel 1 goto nopython
python -c "import aiohttp" >nul 2>nul
if errorlevel 1 goto needdep
goto run

:needdep
echo 第一次运行，正在装依赖 aiohttp，请稍等...
python -m pip install --disable-pip-version-check -q aiohttp
if errorlevel 1 goto mirror
goto run

:mirror
echo 默认源装不上，换国内源再试一次...
python -m pip install --disable-pip-version-check -q aiohttp -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto depfail
goto run

:run
echo.
echo 正在启动 dcwatch，界面会自动打开 http://127.0.0.1:8777
echo 这个窗口别关，关掉就等于停止监听，可以最小化。想停按 Ctrl+C。
echo.
python server.py --open
echo.
echo dcwatch 已经退出了。
pause
exit /b 0

:nopython
echo.
echo [x] 这台电脑上没有可用的 Python。
echo     去 https://www.python.org/downloads/ 下载安装，
echo     安装第一屏务必勾上 Add python.exe to PATH 这一项，
echo     装完关掉本窗口，再双击一次本文件。
echo.
pause
exit /b 1

:depfail
echo.
echo [x] 依赖 aiohttp 装不上，一般是网络问题。把上面几行报错发我。
echo.
pause
exit /b 1
