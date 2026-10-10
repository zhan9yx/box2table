# table2docs

macOS 命令。把终端里复制的方框表放进剪贴板，贴到 Google Docs 时变成文档自己的表格。

不是从数据库或 Excel 生成文档。输入是你已经复制好的那张表。

## 痛点

终端里的表长这样。一格里的换行多半是窗口太窄折出来的：

```text
│周末菜单可调整  │4             │比如家里人说不吃辣，或者另一顿的            │
│                │              │leftover，算进了这桌。不改菜单              │
```

直接 ⌘V，文档里还是 `┌─┬─┐`，断行还在。窗口一变，断行的位置也变。

## 快速上手

系统自带 Python 3。不用装别的包，也不用登录 Google。

```bash
git clone git@github.com:zhan9yx/table2docs.git
cd table2docs
chmod +x table2docs.py
ln -sf "$(pwd)/table2docs.py" ~/.local/bin/table2docs
```

`~/.local/bin` 要在 PATH 里。

复制整张表，运行：

```bash
table2docs
```

打开 Google Docs，按 ⌘V。

## 示例

仓库里的 `tests/fixtures/menu.txt` 是一张 8 行 × 3 列的方框表。上面那一格，以及另一格被折成两行的菜名，贴进去是：

| 菜名 | 份数 | 怎么做 |
| --- | --- | --- |
| 番茄炒蛋 | 2 | 番茄先炒出汁，鸡蛋后下 |
| 周末菜单可调整 | 4 | 比如家里人说不吃辣，或者另一顿的 leftover，算进了这桌。不改菜单 |
| 红烧肉要提前腌一晚 | 2 | 五花肉先煸出油。冰糖炒到"琥珀色"，酱油和料酒沿锅边淋。菜单里写上全部调料 |
| 器具不够 | 2 道，加上备菜清单里还有 3 道 | 砂锅、蒸笼、烤箱，周末再买 |

中文和中文直接接上，所以「腌一」和「晚」中间没有空格。英文单词前留一个空格，所以「的 leftover」中间有空格。

## 工作流

1. 读入。终端里运行就读剪贴板；后面跟文件名就读文件；管道里有内容就读管道。
2. 只留下表格。认 Unicode 方框表（`┌─┬─┐`）、Markdown 表、`+---+---+` 这种 ASCII 表。表外面的字丢掉。
3. 每个格子拼成一段。对齐用的空格去掉。垂直居中留下的空行去掉。
4. 写回剪贴板。同一次放上 HTML、RTF 表格、用制表符隔开的单元格。纯文本仍是原来的框线，所以可以再跑一次。要贴回框线，用 ⌘⇧V。

Google Docs 读 HTML，这个贴过，结果是文档里的表格。Word 通常也读 HTML，同时认 RTF。Pages 读 RTF。PowerPoint 没有公开的幻灯片表格格式，给它的是 HTML。Word、Pages、PowerPoint 没在这台机器上贴过。

聊天工具里可以改成先复制、再输入 `/table`。`/table` 跑的就是不带参数的 `table2docs`。回复里有多张表，复制哪张就转哪张。

## 配置

没有配置文件。

| 命令 | 做什么 |
| --- | --- |
| `table2docs` | 读剪贴板，写回可粘贴的表格 |
| `table2docs 菜单.txt` | 从文件读 |
| `pbpaste \| table2docs` | 从管道读 |
| `table2docs --stdout 菜单.txt` | 只打印 HTML，不改剪贴板 |
| `table2docs bootstrap` | 看本机装了哪些工具，勾选后写入 `/table` |
| `table2docs install claude grok` | 按名字安装 `/table` |
| `table2docs uninstall claude` | 删掉带标记的 `/table` 文件 |

`table2docs install` 不写名字时只列出名字，不改配置。

`bootstrap` 里 ↑↓ 移动，空格勾选，回车安装，q 取消。已经装好的工具默认勾上。可以装这些：Claude Code、Grok、Codex、Pi、OpenCode、Gemini CLI、Qwen、Cursor、GitHub Copilot CLI、Factory Droid。

已经开着的窗口要新开一次才认。Pi 输入 `/reload`。Copilot 用 `/skills reload`。Droid 在 `/commands` 里按 R。Gemini 和 Qwen 执行前会先问一声。只装了 claude 时，Grok 也能看见这条命令。

## 限制

- 只有 macOS。
- 分不清窗口折行和故意分成的两段，格子里的换行都会拼成一句。
- 不合并单元格。
