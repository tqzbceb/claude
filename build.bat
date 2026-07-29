@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   dcwatch  ->  dist\dcwatch.exe
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 没找到 python。装一个再来： https://www.python.org/downloads/
  echo     安装时务必勾上 "Add python.exe to PATH"
  pause & exit /b 1
)

echo [1/3] 装依赖（aiohttp + pyinstaller）...
python -m pip install --disable-pip-version-check -q aiohttp pyinstaller
if errorlevel 1 (echo [x] 装依赖失败，检查网络或换个源 & pause & exit /b 1)

echo [2/3] 打包（第一次要一两分钟）...
python -m PyInstaller --clean --noconfirm dcwatch.spec
if errorlevel 1 (echo [x] 打包失败，把上面的报错发我 & pause & exit /b 1)

echo [3/3] 收尾...
if not exist "dist\dcwatch.exe" (echo [x] 没生成 exe & pause & exit /b 1)
if exist extension xcopy /e /i /y /q extension "dist\extension\" >nul
copy /y README.md "dist\使用说明.md" >nul 2>nul

echo.
echo 好了： %cd%\dist\dcwatch.exe
echo   - 双击就跑，会自动打开界面 http://127.0.0.1:8777
echo   - 那个黑窗口别关，关了就等于停止监听（可以最小化）
echo   - 配置和消息存在 %%LOCALAPPDATA%%\dcwatch\dcwatch.db
echo   - dist\extension 就是浏览器旁听要装的扩展
echo.
pause
