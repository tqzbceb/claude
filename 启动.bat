@echo off
setlocal
cd /d "%~dp0"
title dcwatch
set PORT=%1
if "%PORT%"=="" set PORT=8777

python -c "print(1)" >nul 2>nul
if errorlevel 1 goto nopython
python -c "import aiohttp" >nul 2>nul
if errorlevel 1 goto needdep
goto clearport

:needdep
echo 第一次运行，正在装依赖 aiohttp，请稍等...
python -m pip install --disable-pip-version-check -q aiohttp
if errorlevel 1 goto mirror
goto clearport

:mirror
echo 默认源装不上，换国内源再试一次...
python -m pip install --disable-pip-version-check -q aiohttp -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto depfail
goto clearport

:clearport
echo 检查端口 %PORT% ...
python freeport.py ensure %PORT%
if errorlevel 2 goto already
if errorlevel 1 goto taken
goto run

:already
echo.
echo dcwatch 已经在跑了，不再开第二个，直接给你打开界面。
start "" "http://127.0.0.1:%PORT%"
exit /b 0

:taken
echo.
echo [x] 端口 %PORT% 被别的程序占着（上面一行写了是谁），dcwatch 起不来。
echo     两个办法：
echo       1) 把那个程序关掉，再双击本文件
echo       2) 换个端口：按住 Shift 右键点这个文件夹的空白处 → 在终端中打开,
echo          然后输入  启动.bat 8778
echo     换端口的话，浏览器扩展里的端口也要跟着改（点扩展图标就能改）。
echo.
pause
exit /b 1

:run
echo.
echo 正在启动 dcwatch，界面会自动打开 http://127.0.0.1:%PORT%
echo 这个窗口别关，关掉就等于停止监听，可以最小化。想停按 Ctrl+C，或双击 停止.bat。
echo.
python server.py --open --port %PORT%
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
