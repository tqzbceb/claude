# release —— 直接能用的成品包

这里放**最新一个**打好的交付包，用途只有一个：让用户在没有 AI 帮忙、也不碰命令行的情况下，
从 GitHub 网页上点两下就拿到能跑的程序。

## 用户怎么下（发给他的原话）

> 打开 https://github.com/tqzbceb/claude/tree/main/release ，点里面那个 `dcwatch-v1.11.1.zip`，
> 页面上点 **Download raw file**（右上角那个下载图标）。
> 下载完解压出 `dcwatch` 文件夹，双击里面的 **启动.bat**。
> 黑窗口第一行应该写 `dcwatch v1.11.1`。**这轮只动程序，扩展不用重装**（还是 v1.11.0）；
> 如果你是从 v1.11.0 之前的版本升上来，扩展要重装到 v1.11.0，三步：覆盖 extension 文件夹 →
> `chrome://extensions` 里 dcwatch 卡片点 ⟳ → 回 Discord 页面按 F5
> （程序包里自带 extension 文件夹；另有 `dcwatch-extension-v1.11.0.zip` 给只换扩展的人）。
> 覆盖时**别删旧的 `dcwatch.db`**——你的规则和密钥都在里面。

## 给接手的 AI

- 这个 zip 就是 `START_HERE.md` 第 4 节那条命令打出来的东西，**27 个文件**（v1.10.0 起含 presets/），
  不含 `tests/`、`release/`、`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`、`START_HERE.md`。
- 出了新版本就**替换掉旧的，只留最新一个**（仓库里躺三个版本的 zip，用户必然拿错 ——
  这事已经发生过三次，见 `AGENTS.md` 硬规矩 3）。
- 版本号只有一个来源：`server.py` 的 `VERSION`。改完记得连这个 zip 一起重打，
  否则仓库里的成品包和代码对不上，比没有更害人。
