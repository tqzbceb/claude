@echo off
setlocal
cd /d "%~dp0"
title dcwatch 停止
set PORT=%1
if "%PORT%"=="" set PORT=8777

echo 正在停止 dcwatch，并把端口 %PORT% 释放出来 ...
echo.

python -c "print(1)" >nul 2>nul
if errorlevel 1 goto nopy
if not exist freeport.py goto nopy

python freeport.py stop %PORT%
if errorlevel 1 goto hard
echo.
echo 好了。监听已停止，端口 %PORT% 也空出来了，现在双击 启动.bat 就能正常开。
echo.
pause
exit /b 0

:nopy
echo 用强制结束的方式停 ...
taskkill /f /im dcwatch.exe >nul 2>nul
if not errorlevel 1 goto okexe
goto hard

:okexe
echo.
echo 已经停了（结束了 dcwatch.exe）。
echo.
pause
exit /b 0

:hard
echo.
echo [!] 没能自动停掉。手动来一次：
echo     1) 按 Ctrl+Shift+Esc 打开任务管理器
echo     2) 在「进程」里找 python.exe 或 dcwatch.exe
echo     3) 右键 → 结束任务
echo     如果占端口的是别的程序（上面会写名字），那就把那个程序关掉,
echo     或者给 dcwatch 换个端口：启动.bat 8778
echo.
pause
exit /b 1
