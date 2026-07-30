# PyInstaller 配置：一个 exe，把 ui.html 打进去。
# 用法（Windows）：python -m PyInstaller --clean --noconfirm dcwatch.spec
block_cipher = None

a = Analysis(
    ["server.py"],
    pathex=[],
    binaries=[],
    datas=[("ui.html", "."),           # 界面塞进包里，运行时从 sys._MEIPASS 读
           ("sounds", "sounds"),       # 内置提示音
           ("dcwatch.ico", "."),       # 托盘/窗口偶尔要用到的原始图标
           ("extension", "extension")],  # 浏览器扩展，界面上「下载浏览器扩展」要打包它
    hiddenimports=["winsound"],        # 提示音用；PyInstaller 有时扫不到
    hookspath=[],
    runtime_hooks=[],
    # 用不到的大件全砍掉，exe 能小不少
    excludes=["tkinter", "unittest", "pydoc_data", "test", "lib2to3",
              "email.test", "distutils", "setuptools", "pip", "numpy", "PIL"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="dcwatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # 留着黑窗口：能看日志、Ctrl+C 停。改成 False 就完全后台跑
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="dcwatch.ico",     # exe 的图标（任务栏、资源管理器里显示的就是它）
)
