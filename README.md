# table2docs

终端里复制一张表格，贴进 Google Docs，出来的经常还是 `┌─┬─┐` 这种框线。格子里的话是按当时窗口宽度折开的，窗口一变，断行就变。贴进文档之后，你得自己删换行、删那些用来对齐的空格，才能读成一句。Word、Pages、PowerPoint 里也是一样，贴出来不是一张表。

这条命令跑在 Mac 上。复制方框表（Markdown 表也可以），运行一次 `table2docs`，再在那四个软件里按 ⌘V。Google Docs 会把它变成文档自己的表格。

## 怎么用

在终端或聊天记录里复制整张表，然后：

```bash
table2docs
```

打开 Google Docs、Word、Pages 或 PowerPoint，按 ⌘V。

也可以从文件或管道读：

```bash
table2docs 某张表.txt
pbpaste | table2docs
```

跑完会打印几行几列。剪贴板里的纯文本还是原来的方框表，所以这条命令可以再跑一次。想把框线原样贴进去，用 ⌘⇧V。

只看生成的 HTML、不改剪贴板：

```bash
table2docs --stdout 某张表.txt
```

## 安装

Mac 自带 Python 3 就够，不用装别的包，也不用登录 Google。

```bash
git clone git@github.com:zhan9yx/table2docs.git
cd table2docs
chmod +x table2docs.py
ln -sf "$(pwd)/table2docs.py" ~/.local/bin/table2docs
```

`~/.local/bin` 需要在 PATH 里。不在的话，把 `table2docs.py` 拷到任何一个已经在 PATH 里的目录，改名为 `table2docs`。

## 折行会拼回去

方框表里一个格子占了好几行，多半是终端太窄折出来的，不是你分段写的。这些行会拼成一句。中文和中文之间不加空格，英文单词之间留一个。格子两头用来对齐的空格会去掉。空行是为了把字垂直摆在格子中间，也会去掉。

表外面的说明文字不管。

也认 Markdown 表，以及 `+---+---+` 这种 ASCII 表。

## 剪贴板里有什么

同一次会放上几种格式，对方软件自己挑：

- HTML。Google Docs 读这个，贴出来是文档里的表格，这个试过。Word 通常也读 HTML。
- RTF 表格。Pages 不认上面那种 HTML，它读 RTF。Word 也能读。
- 用制表符隔开的单元格。给按表格文本来读的软件。

PowerPoint 没有一种公开的“这就是一张幻灯片表格”的剪贴板格式，所以给它的是 HTML。它会不会变成幻灯片里的表格，取决于 PowerPoint 自己挑哪种格式。Word、Pages、PowerPoint 这三款没有在写这个工具的机器上装过，没有贴进去看过。

## 做不到的

- 只有 macOS。写剪贴板和转 RTF 用的是系统自带的能力。
- 分不清窗口折行和你故意分成的两段。格子里的换行都会拼成一句。
- 不合并单元格。
