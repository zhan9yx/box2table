# table2docs

macOS 上的一条命令。把终端里复制的表格放进剪贴板，贴到 Google Docs 时变成文档自己的表格。

直接粘贴时，看到的还是 `┌─┬─┐`。格子里的换行跟着窗口宽度走，贴进文档还得自己删。

```text
│比如家里人说不吃辣，或者另一顿的            │
│leftover，算进了这桌。不改菜单              │
```

跑过 `table2docs` 再贴，这一格是一句：`比如家里人说不吃辣，或者另一顿的 leftover，算进了这桌。不改菜单`。

## 安装

系统自带 Python 3。不用装别的包，也不用登录 Google。

```bash
git clone git@github.com:zhan9yx/table2docs.git
cd table2docs
chmod +x table2docs.py
ln -sf "$(pwd)/table2docs.py" ~/.local/bin/table2docs
```

`~/.local/bin` 要在 PATH 里。

## 用法

复制整张表，然后：

```bash
table2docs
```

打开 Google Docs，按 ⌘V。

```bash
table2docs 菜单.txt
pbpaste | table2docs
table2docs --stdout 菜单.txt
```

`--stdout` 只打印 HTML，不改剪贴板。

结束时打印几行几列。剪贴板里的纯文本仍是原来的框线，所以可以再跑一次。要原样贴回框线，用 ⌘⇧V。

认 Unicode 方框表、Markdown 表，以及 `+---+---+` 这种 ASCII 表。表外面的字不管。

中文之间不加空格，英文单词之间留一个。用来对齐的空格会去掉。格子里的换行都会拼成一句。不能合并单元格。

## `/table`

在这些工具里可以装一条 `/table`：Claude Code、Grok、Codex、Pi、OpenCode、Gemini CLI、Qwen、Cursor、GitHub Copilot CLI、Factory Droid。

先复制要的那一张表，再输入 `/table`。有多张表就各复制一次。

```bash
table2docs bootstrap
```

↑↓ 移动，空格勾选，回车安装，q 取消。本机已经装了的工具默认勾上。

```bash
table2docs install claude grok
table2docs uninstall claude
```

`table2docs install` 不写名字时只列出可以装哪些，不改配置。

已经开着的窗口要新开一次才认。Pi 输入 `/reload`。Copilot 用 `/skills reload`。Droid 在 `/commands` 里按 R。Gemini 和 Qwen 执行前会先问一声。

只装了 claude 时，Grok 也能看见这条命令。不要再往 `~/.agents/skills`、`~/.claude/skills`、`~/.cursor/skills` 放同名的一份。

## 其他软件

同一次剪贴板里有 HTML、RTF 表格，以及用制表符隔开的单元格。

| 软件 | 读哪种 | 贴过没有 |
| --- | --- | --- |
| Google Docs | HTML | 贴过，是文档里的表格 |
| Word | HTML，也认 RTF | 这台机器没装，没贴过 |
| Pages | RTF | 这台机器没装，没贴过 |
| PowerPoint | HTML | 没有公开的幻灯片表格格式，没贴过 |

只有 macOS。
