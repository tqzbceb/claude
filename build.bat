@echo off
setlocal
cd /d "%~dp0"
title dcwatch build
set LOCK=%TEMP%\dcwatch_build.lock

echo ============================================
echo   dcwatch  =^> dist\dcwatch.exe
echo ============================================
echo.
echo 先说清楚，免得你以为死机了：
echo   - 打包很吃内存和硬盘，慢的电脑要十几分钟
echo   - 中间长时间没有任何输出是正常的
echo   - 千万别再双击第二次。两个打包一起跑会把内存吃光,
echo     整台电脑会卡到动不了
echo   - 也别关这个窗口
echo.

python -c "print(1)" >nul 2>nul
if errorlevel 1 goto nopython

python -c "import os,sys,time,operator;p=os.environ['LOCK'];sys.exit(1 if os.path.exists(p) and operator.lt(time.time()-os.path.getmtime(p),1800) else 0)"
if errorlevel 1 goto busy
python -c "import os;open(os.environ['LOCK'],'w').write('building')"

echo [1/3] 装依赖 aiohttp 和 pyinstaller ...
python -m pip install --disable-pip-version-check -q aiohttp pyinstaller
if errorlevel 1 goto pipfail

echo [2/3] 正在打包，这一步最慢，别动它 ...
python -m PyInstaller --clean --noconfirm dcwatch.spec
if errorlevel 1 goto buildfail

echo [3/3] 收尾 ...
if not exist "dist\dcwatch.exe" goto nofile
if exist extension xcopy /e /i /y /q extension "dist\extension\" >nul
copy /y README.md "dist\使用说明.md" >nul 2>nul
copy /y 停止.bat "dist\停止.bat" >nul 2>nul
copy /y freeport.py "dist\freeport.py" >nul 2>nul
del "%LOCK%" >nul 2>nul

echo.
echo 打好了: %cd%\dist\dcwatch.exe
echo   - 双击就跑，会自动打开界面 http://127.0.0.1:8777
echo   - 那个黑窗口别关，关了就等于停止监听，可以最小化
echo   - 想停也可以双击 停止.bat
echo   - 配置和消息存在 %%LOCALAPPDATA%%\dcwatch\dcwatch.db
echo   - dist\extension 就是浏览器旁听要装的扩展
echo.
pause
exit /b 0

:busy
echo.
echo [!] 已经有一个打包在跑了，这次不开第二个。
echo     两个一起跑会把内存吃光,整台电脑会卡死,所以这里挡住了。
echo.
echo     如果你确定没有别的窗口在打包（比如上次是强制关机中断的）,
echo     把这个文件删掉再来一次:
echo     %LOCK%
echo.
pause
exit /b 1

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
del "%LOCK%" >nul 2>nul
echo.
echo [x] 装 aiohttp / pyinstaller 失败，一般是网络问题。把上面几行报错发我。
echo.
pause
exit /b 1

:buildfail
del "%LOCK%" >nul 2>nul
echo.
echo [x] 打包失败了。把上面的报错整段发我。
echo.
pause
exit /b 1

:nofile
del "%LOCK%" >nul 2>nul
echo.
echo [x] 打包跑完了但没找到 dist\dcwatch.exe，把上面的输出发我。
echo.
pause
exit /b 1
