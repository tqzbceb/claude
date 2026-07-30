@echo off
setlocal
cd /d "%~dp0"
title dcwatch build

echo ============================================
echo   dcwatch  =^>  dist\dcwatch.exe
echo ============================================
echo.

python -c "print(1)" >nul 2>nul
if errorlevel 1 goto nopython

echo [1/3] 装依赖 aiohttp 和 pyinstaller ...
python -m pip install --disable-pip-version-check -q aiohttp pyinstaller
if errorlevel 1 goto pipfail

echo [2/3] 打包，第一次要一两分钟 ...
python -m PyInstaller --clean --noconfirm dcwatch.spec
if errorlevel 1 goto buildfail

echo [3/3] 收尾 ...
if not exist "dist\dcwatch.exe" goto nofile
if exist extension xcopy /e /i /y /q extension "dist\extension\" >nul
copy /y README.md "dist\使用说明.md" >nul 2>nul

echo.
echo 打好了: %cd%\dist\dcwatch.exe
echo   - 双击就跑，会自动打开界面 http://127.0.0.1:8777
echo   - 那个黑窗口别关，关了就等于停止监听，可以最小化
echo   - 配置和消息存在 %%LOCALAPPDATA%%\dcwatch\dcwatch.db
echo   - dist\extension 就是浏览器旁听要装的扩展
echo.
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

:pipfail
echo.
echo [x] 装依赖失败，检查网络，或者换国内源手动装一次:
echo     python -m pip install aiohttp pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1

:buildfail
echo.
echo [x] 打包失败，把上面的报错发我。
echo.
pause
exit /b 1

:nofile
echo.
echo [x] 没生成 exe，把上面的输出发我。
echo.
pause
exit /b 1
