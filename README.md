# PaperCards

中文 | [English](README.en.md)

读论文、听报告时，一键把**好句子、好图表、好数据**收成卡片，**自动带上出处**。

![PaperCards：卡片墙 + 快速录入面板](docs/hero.png)

- 在 Zotero 里选中一句话 → `⌃⌥⌘C` → 弹出小面板，标题、作者、年份、**页码**都已填好，加个标签回车就存
- 用 Snipaste 截一张图 → `⌃⌥⌘V` → 图表卡片，同样自动带来源
- 看直播 / 网课的 PPT → `⌃⌥⌘A` → 每翻一页自动截一张
- 所有卡片在一个本地网页里浏览、筛选、搜索，一键导出带引用和 BibTeX 的 Markdown

数据全部存在本地（SQLite + 图片文件夹），不依赖任何云服务。

<p align="center"><img src="docs/demo.gif" alt="卡片墙的几种视图：瀑布流、录入面板、按来源分组、列表" width="900"></p>

<sub>截图中为示例数据：引文与数值摘自对应论文，图表为据论文数值重绘的示意图。</sub>

## 怎么用

**在 Zotero 里收一句话**：选中 → `⌃⌥⌘C`，弹出的面板里论文标题、作者、年份、页码都已填好，加个标签回车即可。

![在 Zotero 中选中文字，按 ⌃⌥⌘C 弹出卡片](docs/capture-zotero.png)

**在网页上收一张图表**：`⌃⌥⌘S` 调起 Snipaste 框选，截完直接弹出图表卡片，来源从网页自动识别（arXiv / DOI）。

![在 Chrome 中用 Snipaste 框选图表，按 ⌃⌥⌘S 弹出卡片](docs/capture-chrome.png)

<sub>以上为示意图：窗口界面为仿制，卡片面板为 PaperCards 真实界面截图。</sub>

## 功能

**收集**

| 快捷键 | 作用 |
|---|---|
| `⌃⌥⌘C` | 收集选中的文字（Zotero / Chrome / 任意应用） |
| `⌃⌥⌘V` | 有选中文字收文字；否则收剪贴板（配合 Snipaste 截图后使用） |
| `⌃⌥⌘S` | 调起 Snipaste 框选截图，截完直接建卡片 |
| `⌃⌥⌘R` | 按 Snipaste 上一次的区域一键截图 |
| `⌃⌥⌘A` | 开始 / 停止自动截图：监视一块区域，画面换页就截 |
| `⌃⌥⌘P` | 打开卡片墙 |

**自动识别来源**

- **Zotero**：根据阅读器窗口匹配 Zotero 库里的条目，带上完整元数据和当前页码；卡片上点来源可直接在 Zotero 里打开 PDF 并跳到那一页
- **Chrome / Edge / Brave / Arc / Safari**：读取网页 URL 和 `citation_*` 元数据；arXiv 链接自动查 arXiv API，DOI 自动查 Crossref；如果这篇论文也在你的 Zotero 库里会自动关联
- 识别不到时，在面板里粘贴 DOI / arXiv 号 / 链接即可补全

**录入面板**

光标默认在标签框：回车添加标签，空回车跳到备注；备注里回车保存（⇧回车换行）。任何位置 `⌘⏎` 保存、Esc 保存并关闭，`⌘1–4` 切换类型（摘录 / 图表 / 数据 / 想法），`⌘⌫` 丢弃误截的卡片。

**自动截图**

- 区域在开始时锁定（默认取 Snipaste 上次的区域，可在菜单栏 📷 里用 Snipaste 重新框选），之后照常用 Snipaste 截别的不受影响
- 检查间隔可设置（默认 5 秒），换页后可选择弹面板确认或直接保存
- 有屏幕录制权限时静默截图，不闪屏

**卡片墙**（`http://127.0.0.1:8787`）

- **文字高亮 / 下划线**（参照 Zotero）：在卡片内容里选中文字，弹出工具条，5 种颜色高亮、下划线、清除；同色再点一次取消。导出为带颜色的 `<mark>` / `<u>`
- **图片标注**（参照 Snipaste）：图片卡片的「标注」里可画矩形、椭圆、箭头、画笔、马克笔、文字、马赛克，6 种颜色 3 种粗细，可撤销 / 重做；原图保留，标注可反复修改或一键清空恢复
- **来源标签**：给来源标注会议、团队、机构等（左栏来源的 ✎ 里编辑，会议名可一键添加）；左栏顶部按来源标签筛选，卡片出处行以描边小标签显示，导出时一并写入
- 按来源、标签、类型筛选，全文搜索。来源和标签默认单选，可打开「多选」（或 ⌘ 点击）；多个标签可选「任一 / 全部」匹配；标签列表只统计所选来源下的卡片
- 分组（来源 / 类型）× 排序（最新 / 最早），瀑布流 / 网格 / 列表 / 四分四种布局，卡片大小可调，左栏宽度可拖动
- **四分布局**：想法、摘录、图表、数据各占一区，各自独立滚动；拖动分隔线调整四区比例（双击恢复），每区可切换「上下滚动 / 左右翻页」，一键重置
- 类型顺序默认想法在前，在顶部类型按钮上拖动即可调整
- **键盘浏览**：`←` `→` 上一张 / 下一张卡片（自动滚到顶端），`⌥←` `⌥→` 跳到上一个 / 下一个类型的第一张；四分布局下作用于鼠标所在的区
- 卡片上直接加 / 删标签（自动补全）；左栏可重命名、合并、删除标签；双击内容、单击备注直接编辑（长内容单击展开全文）
- 页面上 `⌘V` 粘贴图片或文字新建卡片；时间戳精确到秒
- 导出 Markdown（zip，含图片和 BibTeX）：分节和顺序与页面当前视图一致（按类型 / 按来源 / 平铺），图片按「类型-序号」命名（如 `图表-1.png`，同类型内按时间先后编号）
- 地址参数可保存视图为书签，如 `?layout=quad&q=60,50,50&tags=实验结果`（参数：`layout` `group` `sort` `size` `sources` `stags` `tags` `h` `q`）

## 安装

需要 macOS，以及：

- [Hammerspoon](https://www.hammerspoon.org/)（全局快捷键）：`brew install --cask hammerspoon`
- Python 3（系统自带的 `/usr/bin/python3` 即可，只用标准库，无需 pip 安装任何东西）
- 可选：[Snipaste](https://www.snipaste.com/)（截图相关功能）、[Zotero 7+](https://www.zotero.org/)

1. 克隆仓库：

   ```bash
   git clone https://github.com/mia1460/papercards.git ~/papercards
   ```

2. 在 `~/.hammerspoon/init.lua` 末尾加上（路径改成你克隆的位置）：

   ```lua
   package.path = package.path .. ";" .. os.getenv("HOME") .. "/papercards/?.lua"
   papercards = require("papercards")
   ```

3. 在 Hammerspoon 菜单栏图标里点 **Reload Config**。

4. 首次使用时按提示授予 Hammerspoon 权限：
   - **辅助功能**：用于读取选中文字
   - **屏幕录制**：用于自动截图时静默截取（不授权也能用，但会闪屏）

5. Chrome 可选设置：菜单「显示 → 开发者 → 允许 Apple 事件中的 JavaScript」，可以直接读到网页里的论文元数据。

后台服务会由 Hammerspoon 自动启动。也可以手动运行 `python3 server.py`，然后打开 http://127.0.0.1:8787。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `PAPERCARDS_PORT` | `8787` | 服务端口 |
| `PAPERCARDS_DATA` | `./data` | 数据目录（卡片数据库 + 图片） |
| `ZOTERO_DIR` | `~/Zotero` | Zotero 数据目录 |

## 工作原理

```
Hammerspoon (papercards.lua)          server.py (127.0.0.1:8787)
  快捷键 → 取选中文字 / 截图     ──▶   识别来源：Zotero 库（只读）/ arXiv / Crossref
  识别当前窗口、网页 URL               存入 SQLite + 图片文件夹
  弹出录入面板 (static/quick.html) ◀──  卡片墙 (static/index.html)
```

- Zotero 当前阅读的论文和页码：通过窗口标题 + 每个附件目录下的 `.zotero-reader-state` 判断，以只读方式读取 `zotero.sqlite`，不会修改你的 Zotero 库
- 截图通过 Snipaste 命令行（`snip --last -o file`）完成；自动截图从 Snipaste 日志读取区域坐标，再用系统 `screencapture` 静默截取

## License

MIT
