#!/usr/bin/env python3
"""Copy a Unicode box table or Markdown table as a native Google Docs table.

Reads the table from a file, a pipe, or the macOS clipboard. Writes several
pasteboard types at once so each app can pick one: HTML for Google Docs,
Word, and PowerPoint; an RTF table for Pages and Word; tab-separated cells
for spreadsheet-style paste. The plain-text flavor stays the original table,
so running the command again still sees the box drawing.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

HORIZONTAL = set("─━┄┅┈┉╌╍═")
VERTICAL = set("│┃║┆┇┊┋")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MD_SEP_CELL = re.compile(r"^:?-+:?$")

TD_BASE = (
    "border-left:solid #000000 1pt;border-right:solid #000000 1pt;"
    "border-bottom:solid #000000 1pt;border-top:solid #000000 1pt;"
    "vertical-align:top;padding:5pt 5pt 5pt 5pt;overflow:hidden;"
    "overflow-wrap:break-word;"
)
SPAN_BASE = (
    "font-size:11pt;font-family:Arial,sans-serif;color:#000000;"
    "background-color:transparent;font-style:normal;font-variant:normal;"
    "text-decoration:none;vertical-align:baseline;white-space:pre-wrap;"
)

PASTE_SCRIPT = r"""
ObjC.import("AppKit");
function readUTF8(path) {
  if (!path) return "";
  const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
  if (!value || value.isNil()) return "";
  return value.js;
}
function run(argv) {
  const html = readUTF8(argv[0]);
  const plain = readUTF8(argv[1]);
  const rtf = readUTF8(argv[2]);
  const tsv = readUTF8(argv[3]);
  if (!html) return "read-html-failed";
  if (!plain) return "read-plain-failed";
  const pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  let ok = pb.setStringForType(html, $.NSPasteboardTypeHTML);
  ok = pb.setStringForType(html, "Apple HTML pasteboard type") && ok;
  if (rtf) ok = pb.setStringForType(rtf, $.NSPasteboardTypeRTF) && ok;
  if (tsv) ok = pb.setStringForType(tsv, "public.utf8-tab-separated-values-text") && ok;
  ok = pb.setStringForType(plain, $.NSPasteboardTypeString) && ok;
  return ok ? "ok" : "write-failed";
}
"""


@dataclass
class Table:
    rows: list[list[str]]
    widths: list[int]


def display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def needs_space(left: str, right: str) -> bool:
    """Restore a space that a column wrap dropped at a script boundary."""
    if unicodedata.category(right).startswith("P"):
        return False
    if unicodedata.category(left).startswith("P") and is_wide(left):
        return False
    if is_wide(left) and is_wide(right):
        return False
    return True


def smart_join(parts: list[str]) -> str:
    merged = parts[0]
    for part in parts[1:]:
        if merged and part and needs_space(merged[-1], part[0]):
            merged += " "
        merged += part
    return merged


def reflow_cell(raw_lines: list[str]) -> str:
    """Join every visual line in a cell into one paragraph.

    Breaks inside a box cell follow the terminal width. They are not paragraph
    breaks, and the padding around them is not part of the text. Blank lines
    are only vertical centering, so they are dropped.
    """
    parts = [raw.strip() for raw in raw_lines if raw.strip()]
    if not parts:
        return ""
    return smart_join(parts)


def _box_char(ch: str) -> bool:
    return 0x2500 <= ord(ch) <= 0x257F


def is_unicode_rule(line: str) -> bool:
    text = line.strip()
    if len(text) < 4:
        return False
    saw_edge = False
    for ch in text:
        if ch == " ":
            continue
        if not _box_char(ch):
            return False
        if ch not in VERTICAL:
            saw_edge = True
    return saw_edge


def is_ascii_rule(line: str) -> bool:
    text = line.strip()
    if "+" not in text or "-" not in text or len(text) < 4:
        return False
    return all(ch in "+-= " for ch in text)


def is_unicode_content(line: str) -> bool:
    text = line.strip()
    return bool(text) and text[0] in VERTICAL and not is_unicode_rule(line)


def is_ascii_content(line: str) -> bool:
    text = line.strip()
    return text.startswith("|") and text.count("|") >= 2 and not is_ascii_rule(line)


def unicode_widths(rule: str) -> list[int]:
    widths: list[int] = []
    run = 0
    started = False
    for ch in rule.strip():
        if ch in HORIZONTAL or ch == " ":
            run += 1
            started = True
            continue
        if started:
            widths.append(run)
        started = True
        run = 0
    return widths


def ascii_widths(rule: str) -> list[int]:
    parts = rule.strip().split("+")
    return [len(part) for part in parts[1:-1]]


def split_bars(line: str, bars: str) -> list[str]:
    text = line.strip()
    parts = re.split(f"[{re.escape(bars)}]", text)
    if text.startswith(tuple(bars)):
        parts = parts[1:]
    if text.endswith(tuple(bars)) and parts:
        parts = parts[:-1]
    return parts


def _table_from_block(block: list[str], ascii_mode: bool) -> Table | None:
    rule_fn = is_ascii_rule if ascii_mode else is_unicode_rule
    content_fn = is_ascii_content if ascii_mode else is_unicode_content
    widths: list[int] | None = None
    raw_rows: list[list[list[str]]] = []
    buffer: list[list[str]] = []
    for line in block:
        if rule_fn(line):
            if widths is None:
                widths = ascii_widths(line) if ascii_mode else unicode_widths(line)
            if buffer:
                raw_rows.append(buffer)
                buffer = []
            continue
        if content_fn(line):
            cells = split_bars(line, "|" if ascii_mode else "".join(sorted(VERTICAL)))
            buffer.append(cells)
    if buffer:
        raw_rows.append(buffer)
    if not widths or not raw_rows:
        return None
    rows: list[list[str]] = []
    for raw in raw_rows:
        if any(len(line) != len(widths) for line in raw):
            found = {len(line) for line in raw}
            raise ValueError(f"表格列数不一致：分隔线是 {len(widths)} 列，内容行是 {sorted(found)}")
        columns = [[line[index] for line in raw] for index in range(len(widths))]
        rows.append([reflow_cell(columns[index]) for index in range(len(widths))])
    return Table(rows=rows, widths=widths)


def parse_box_tables(text: str) -> list[Table]:
    lines = text.splitlines()
    tables: list[Table] = []
    index = 0
    while index < len(lines):
        unicode_rule = is_unicode_rule(lines[index])
        ascii_rule = is_ascii_rule(lines[index])
        if not unicode_rule and not ascii_rule:
            index += 1
            continue
        ascii_mode = ascii_rule and not unicode_rule
        rule_fn = is_ascii_rule if ascii_mode else is_unicode_rule
        content_fn = is_ascii_content if ascii_mode else is_unicode_content
        block = [lines[index]]
        index += 1
        while index < len(lines) and (rule_fn(lines[index]) or content_fn(lines[index])):
            # A second rule with nothing between it and the previous rule is the
            # top border of the next table, not another row of this one.
            if (
                rule_fn(lines[index])
                and rule_fn(block[-1])
                and any(content_fn(line) for line in block)
            ):
                break
            block.append(lines[index])
            index += 1
        if len(block) >= 2 and any(content_fn(line) for line in block):
            table = _table_from_block(block, ascii_mode)
            if table and table.rows:
                tables.append(table)
    return tables


def _split_markdown_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def _is_markdown_separator(line: str) -> bool:
    if "|" not in line or "-" not in line:
        return False
    cells = _split_markdown_row(line)
    return bool(cells) and all(MD_SEP_CELL.match(cell.replace(" ", "")) for cell in cells)


def parse_markdown_tables(text: str) -> list[Table]:
    lines = text.splitlines()
    tables: list[Table] = []
    index = 0
    while index < len(lines) - 1:
        if "|" not in lines[index] or not _is_markdown_separator(lines[index + 1]):
            index += 1
            continue
        header = _split_markdown_row(lines[index])
        body = [header]
        index += 2
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            if _is_markdown_separator(lines[index]):
                break
            row = _split_markdown_row(lines[index])
            if len(row) < len(header):
                row.extend([""] * (len(header) - len(row)))
            body.append(row[: len(header)])
            index += 1
        width = len(header)
        widths = [
            max(display_width(row[col]) for row in body)
            for col in range(width)
        ]
        tables.append(Table(rows=body, widths=widths))
    return tables


def parse_tables(text: str) -> list[Table]:
    cleaned = ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    tables = parse_box_tables(cleaned)
    if tables:
        return tables
    return parse_markdown_tables(cleaned)


def _span(text: str, header: bool) -> str:
    weight = 700 if header else 400
    lines = text.split("\n") if text else [""]
    inner = "<br>".join(html.escape(line) for line in lines)
    return (
        f'<p dir="ltr" style="line-height:1.2;margin-top:0pt;margin-bottom:0pt;">'
        f'<span style="{SPAN_BASE}font-weight:{weight};">{inner}</span></p>'
    )


def table_to_html(table: Table) -> str:
    cols = "".join(f'<col width="{max(48, width * 8)}">' for width in table.widths)
    rows_html: list[str] = []
    for row_index, row in enumerate(table.rows):
        header = row_index == 0
        cells = []
        for text in row:
            style = TD_BASE + ("background-color:#d9d9d9;" if header else "")
            cells.append(f'<td style="{style}">{_span(text, header)}</td>')
        rows_html.append(f'<tr style="height:0pt">{"".join(cells)}</tr>')
    return (
        '<table style="border:none;border-collapse:collapse;">'
        f"<colgroup>{cols}</colgroup><tbody>{''.join(rows_html)}</tbody></table>"
    )


def tables_to_html(tables: list[Table]) -> str:
    body = "<p><br></p>".join(table_to_html(table) for table in tables)
    return (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        f"<!--StartFragment-->{body}<!--EndFragment-->"
        "</body></html>"
    )


def tables_to_rtf(tables: list[Table]) -> str:
    """Turn the same HTML into a Cocoa RTF table.

    Pages reads RTF and skips HTML. Word reads both. textutil is the same
    converter TextEdit uses, so the table survives as ``\\trowd`` rows.
    """
    result = subprocess.run(
        ["textutil", "-stdin", "-format", "html", "-convert", "rtf", "-stdout"],
        input=tables_to_html(tables).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "RTF 转换失败"
        raise RuntimeError(detail)
    rtf = result.stdout.decode("utf-8")
    if "\\trowd" not in rtf:
        raise RuntimeError("RTF 里没有表格")
    return rtf


def tables_to_tsv(tables: list[Table]) -> str:
    blocks: list[str] = []
    for table in tables:
        lines = []
        for row in table.rows:
            cells = [cell.replace("\t", " ").replace("\n", " ") for cell in row]
            lines.append("\t".join(cells))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def copy_html(
    html_text: str,
    plain_text: str,
    rtf_text: str = "",
    tsv_text: str = "",
) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("写入剪贴板目前只支持 macOS")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        html_path = root / "table.html"
        plain_path = root / "table.txt"
        rtf_path = root / "table.rtf"
        tsv_path = root / "table.tsv"
        html_path.write_text(html_text, encoding="utf-8")
        plain_path.write_text(plain_text, encoding="utf-8")
        rtf_path.write_text(rtf_text, encoding="utf-8")
        tsv_path.write_text(tsv_text, encoding="utf-8")
        result = subprocess.run(
            [
                "osascript",
                "-l",
                "JavaScript",
                "-",
                str(html_path),
                str(plain_path),
                str(rtf_path),
                str(tsv_path),
            ],
            input=PASTE_SCRIPT,
            text=True,
            capture_output=True,
            check=False,
        )
    if result.returncode != 0 or result.stdout.strip() != "ok":
        detail = (result.stderr or result.stdout or "osascript 失败").strip()
        raise RuntimeError(detail)


def read_clipboard() -> str:
    result = subprocess.run(["pbpaste"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "pbpaste 失败")
    return result.stdout


class NoTable(Exception):
    """The text has no box, Markdown, or ASCII table."""


def convert_text(source: str, to_stdout: bool) -> str:
    """Put the table on the clipboard, or write HTML when ``to_stdout`` is set.

    The plain-text flavor stays ``source``, so a later ``box2table`` still
    sees the original box drawing. Returns the one-line status.
    """
    tables = parse_tables(source)
    if not tables:
        raise NoTable("没找到表格。支持 Unicode 方框表（┌─┬─┐）和 Markdown 表。")
    document = tables_to_html(tables)
    if to_stdout:
        sys.stdout.write(document)
        if not document.endswith("\n"):
            sys.stdout.write("\n")
        return summary(tables)
    copy_html(document, source, tables_to_rtf(tables), tables_to_tsv(tables))
    return summary(tables)


def read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8")
    # `-` 只读 stdin。这些工具的 shell 不是 tty，空管道仍改读剪贴板。
    if path == "-":
        return sys.stdin.read()
    if sys.stdin.isatty():
        return read_clipboard()
    data = sys.stdin.read()
    if data.strip():
        return data
    return read_clipboard()


def summary(tables: list[Table]) -> str:
    if len(tables) == 1:
        rows = len(tables[0].rows)
        cols = len(tables[0].widths)
        shape = f"{rows} 行 × {cols} 列"
    else:
        shape = f"{len(tables)} 个表格"
    return f"已放入剪贴板：{shape}。可粘贴到 Google Docs、Word、Pages、PowerPoint。"


_TOOL_PATHS = {
    "claude": ".claude/commands/table.md",
    "grok": ".grok/commands/table.md",
    "codex": ".codex/skills/table/SKILL.md",
    "pi": ".pi/agent/extensions/box2table.ts",
    "opencode": ".config/opencode/commands/table.md",
    "gemini": ".gemini/commands/table.toml",
    "qwen": ".qwen/commands/table.md",
    "cursor": ".cursor/commands/table.md",
    "copilot": ".copilot/skills/table/SKILL.md",
    "droid": ".factory/commands/table",
}
_TOOLS = tuple(_TOOL_PATHS)
MANAGED_MARKER = "box2table-managed-command"
_OPEN_NOTE = (
    "已经开着的窗口要新开一次才认。Pi 可以在那个窗口里自己输入 /reload。"
    "Copilot 用 /skills reload。Droid 在 /commands 里按 R。"
)

_COMMAND_MD = """\
---
description: 把剪贴板里刚复制的那一张表转成可粘贴的表格
---

<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。只运行 box2table，不要加参数。把命令打印的那一行原样回复，不要重画表格。
"""

_SKILL_MD = """\
---
name: table
description: 只在用户输入 /table 时使用。剪贴板里是刚复制的那一张表。
disable-model-invocation: true
user-invocable: true
---

<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。只运行 box2table，不要加参数。把命令打印的那一行原样回复，不要重画表格。
"""

_OPENCODE_MD = """\
---
description: 把剪贴板里刚复制的那一张表转成可粘贴的表格
---

<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。下面命令的输出就是结果。把那一行原样回复，不要重画表格。

!`box2table`
"""

_GEMINI_TOML = '''\
description = "把剪贴板里刚复制的那一张表转成可粘贴的表格"

prompt = """
<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。下面命令的输出就是结果。把那一行原样回复，不要重画表格。

!{box2table}
"""
'''

_QWEN_MD = """\
---
description: 把剪贴板里刚复制的那一张表转成可粘贴的表格
---

<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。下面命令的输出就是结果。把那一行原样回复，不要重画表格。

!{box2table}
"""

_COPILOT_SKILL = """\
---
name: table
description: 只在用户输入 /table 时使用。剪贴板里是刚复制的那一张表。
---

<!-- box2table-managed-command -->

剪贴板里是刚复制的那一张表。只运行 box2table，不要加参数。把命令打印的那一行原样回复，不要重画表格。
"""

_DROID_SH = """\
#!/bin/sh
# box2table-managed-command
# 剪贴板里是刚复制的那一张表。把下面命令打印的那一行原样回复，不要重画表格。
exec box2table
"""

_PI_EXTENSION = """\
// box2table-managed-command
// 剪贴板里是刚复制的那一张表。只运行 box2table。把命令打印的那一行原样回复，不要重画表格。
import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type RunResult = {
  code: number;
  stdout: string;
  stderr: string;
  missing: boolean;
  errorMessage: string;
};

function lastLine(text: string): string {
  const lines = text.split(/\\r?\\n/).map((line) => line.trim()).filter(Boolean);
  return lines.length > 0 ? lines[lines.length - 1] : "";
}

function run(bin: string): RunResult {
  // 继承终端，box2table 才会读剪贴板，而不是去读一条空管道。
  const result = spawnSync(bin, [], { encoding: "utf8", stdio: ["inherit", "pipe", "pipe"] });
  const error = result.error as { code?: string; message?: string } | undefined;
  return {
    code: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    missing: error?.code === "ENOENT",
    errorMessage: error && error.code !== "ENOENT" ? error.message ?? "" : "",
  };
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand("table", {
    description: "把剪贴板里刚复制的那一张表转成可粘贴的表格",
    handler: async (_args, ctx) => {
      let result = run("box2table");
      if (result.missing) {
        result = run(`${homedir()}/.local/bin/box2table`);
      }
      if (result.missing) {
        ctx.ui.notify("找不到 box2table。", "error");
        return;
      }
      if (result.errorMessage) {
        ctx.ui.notify(result.errorMessage, "error");
        return;
      }
      const line =
        lastLine(result.code === 0 ? result.stdout : result.stderr) ||
        lastLine(result.stdout) ||
        lastLine(result.stderr) ||
        "转换失败";
      ctx.ui.notify(line, result.code === 0 ? "info" : "error");
    },
  });
}
"""


def _tool_path(home: Path, name: str) -> Path:
    return home / _TOOL_PATHS[name]


def _tool_body(name: str) -> str:
    if name in ("claude", "grok", "cursor"):
        return _COMMAND_MD
    if name == "codex":
        return _SKILL_MD
    if name == "pi":
        return _PI_EXTENSION
    if name == "opencode":
        return _OPENCODE_MD
    if name == "gemini":
        return _GEMINI_TOML
    if name == "qwen":
        return _QWEN_MD
    if name == "copilot":
        return _COPILOT_SKILL
    if name == "droid":
        return _DROID_SH
    raise ValueError(name)


def _known_tools() -> str:
    return "、".join(_TOOLS)


def _unique(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _print_install_help() -> None:
    print(f"可安装：{_known_tools()}。")
    print("先复制要的那一张表，再输入 /table；只装了 claude 时，Grok 也能看到这条命令。")
    print("要勾选安装，运行 box2table bootstrap。")


def _reject_unknown(names: list[str]) -> bool:
    unknown = [name for name in names if name not in _TOOLS]
    if not unknown and names:
        return False
    if unknown:
        print(f"不认识：{'、'.join(unknown)}。已知：{_known_tools()}。", file=sys.stderr)
    else:
        print(f"要写上工具名字。已知：{_known_tools()}。", file=sys.stderr)
    return True


def _install_tools(home: Path, names: list[str]) -> int:
    failed = False
    wrote = False
    for name in names:
        path = _tool_path(home, name)
        updated = False
        if path.exists():
            try:
                current = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                print(f"{path} 读不了，没有覆盖：{exc}", file=sys.stderr)
                failed = True
                continue
            if MANAGED_MARKER not in current:
                print(f"{path} 没有 {MANAGED_MARKER} 标记，没有覆盖。", file=sys.stderr)
                failed = True
                continue
            updated = True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_tool_body(name), encoding="utf-8")
            if name == "droid":
                path.chmod(0o755)
        except OSError as exc:
            print(f"{path} 没有写成：{exc}", file=sys.stderr)
            failed = True
            continue
        print(f"{'已更新' if updated else '已写入'} {path}")
        wrote = True
    if wrote:
        print(_OPEN_NOTE)
    return 1 if failed else 0


def _uninstall_tools(home: Path, names: list[str]) -> int:
    failed = False
    for name in names:
        path = _tool_path(home, name)
        if not path.exists():
            print(f"没有安装 {name}：{path}")
            continue
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            print(f"{path} 读不了，没有删除：{exc}", file=sys.stderr)
            failed = True
            continue
        if MANAGED_MARKER not in current:
            print(f"{path} 没有 {MANAGED_MARKER} 标记，没有删除。", file=sys.stderr)
            failed = True
            continue
        try:
            path.unlink()
        except OSError as exc:
            print(f"{path} 没有删掉：{exc}", file=sys.stderr)
            failed = True
            continue
        print(f"已删除 {path}")
    return 1 if failed else 0


# 只认这些目录。opencode 两个位置有一个即可。ChatGPT.app 不是 codex。
_TOOL_CONFIG_DIRS = {
    "claude": (".claude",),
    "grok": (".grok",),
    "codex": (".codex",),
    "pi": (".pi",),
    "opencode": (".config/opencode", ".opencode"),
    "gemini": (".gemini",),
    "qwen": (".qwen",),
    "cursor": (".cursor",),
    "copilot": (".copilot",),
    "droid": (".factory",),
}
_TOOL_APPS = {
    "claude": "Claude.app",
    "cursor": "Cursor.app",
}
_STATUS_INSTALLED = "已安装"
_STATUS_CONFIG = "只看到配置目录"
_STATUS_MISSING = "没检测到"
_BOOTSTRAP_NOTES = (
    _OPEN_NOTE,
    "只装了 claude 时，Grok 也能看到这条命令。",
    "Gemini 和 Qwen 执行前会先问。",
    "Pi 在本地直接跑。Droid 也在本地跑，脚本文字会进对话。",
    "↑↓ 移动，空格勾选，回车安装，q 取消。",
)


def _app_dirs(home: Path) -> tuple[Path, ...]:
    return (Path("/Applications"), home / "Applications")


def _detect_status(name: str, home: Path, which, app_dirs) -> str:
    # 只问和 id 同名的命令。名为 agent 的程序不是 grok，也不是 cursor。
    if which(name):
        return _STATUS_INSTALLED
    bundle = _TOOL_APPS.get(name)
    if bundle is not None and any((Path(directory) / bundle).exists() for directory in app_dirs):
        return _STATUS_INSTALLED
    if any((home / relative).is_dir() for relative in _TOOL_CONFIG_DIRS[name]):
        return _STATUS_CONFIG
    return _STATUS_MISSING


def _detect_tools(home: Path, *, which=None, app_dirs=None) -> list[tuple[str, str]]:
    if which is None:
        which = shutil.which
    if app_dirs is None:
        app_dirs = _app_dirs(home)
    return [
        (name, _detect_status(name, home, which, app_dirs))
        for name in _TOOLS
    ]


def _items_from_status(found: list[tuple[str, str]]) -> list[tuple[str, str, bool]]:
    return [(name, status, status == _STATUS_INSTALLED) for name, status in found]


def _parse_bootstrap_key(key: str) -> str:
    if key in ("\x1b[A", "\x1bOA"):
        return "up"
    if key in ("\x1b[B", "\x1bOB"):
        return "down"
    if key == " ":
        return "toggle"
    if key in ("\r", "\n"):
        return "confirm"
    if key in ("q", "Q", "\x04"):
        return "cancel"
    return ""


def _choose_tools(items, keys, draw=None) -> list[str] | None:
    # keys 里每一项是一次按键。方向键是完整的 ESC 序列，不是拆开的字符。
    checked = [bool(item[2]) for item in items]
    index = 0
    count = len(items)
    if draw is not None:
        draw(items, checked, index)
    for key in keys:
        action = _parse_bootstrap_key(key)
        if action == "up":
            if index > 0:
                index -= 1
        elif action == "down":
            if index + 1 < count:
                index += 1
        elif action == "toggle":
            if count:
                checked[index] = not checked[index]
        elif action == "confirm":
            return [item[0] for item, on in zip(items, checked) if on]
        elif action == "cancel":
            return None
        else:
            continue
        if draw is not None:
            draw(items, checked, index)
    return None


def _bootstrap_screen(items, checked, index: int) -> str:
    lines = ["勾选要安装的 /table。已安装的默认勾上。", ""]
    for i, (name, status, _default) in enumerate(items):
        cursor = ">" if i == index else " "
        mark = "x" if checked[i] else " "
        lines.append(f"{cursor} [{mark}] {name}  {status}")
    lines.append("")
    lines.extend(_BOOTSTRAP_NOTES)
    return "\n".join(lines) + "\n"


def _draw_bootstrap(items, checked, index: int) -> None:
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.write(_bootstrap_screen(items, checked, index).replace("\n", "\r\n"))
    sys.stdout.flush()


def _read_tty_key(fd: int) -> str | None:
    import os
    import select

    raw = os.read(fd, 1)
    if not raw:
        return None
    if raw != b"\x1b":
        return raw.decode("utf-8", "replace")
    parts = [raw]
    for _ in range(2):
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            break
        nxt = os.read(fd, 1)
        if not nxt:
            break
        parts.append(nxt)
    return b"".join(parts).decode("utf-8", "replace")


def _tty_keys(fd: int):
    while True:
        key = _read_tty_key(fd)
        if key is None:
            return
        yield key


def _choose_on_tty(items) -> list[str] | None:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    entered = False
    try:
        # setcbreak 仍把 Ctrl-C 交给进程。退出时恢复终端属性和备用屏幕。
        tty.setcbreak(fd)
        sys.stdout.write("\033[?1049h")
        sys.stdout.flush()
        entered = True
        try:
            return _choose_tools(items, _tty_keys(fd), draw=_draw_bootstrap)
        except KeyboardInterrupt:
            return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        finally:
            if entered:
                sys.stdout.write("\033[?1049l")
                sys.stdout.flush()


def _print_bootstrap_plain(items) -> None:
    print("不是交互终端，没有打开勾选，也没有写入。")
    for name, status, _checked in items:
        print(f"{name}  {status}")
    print("要安装，运行 box2table install 名字。")
    for line in _BOOTSTRAP_NOTES:
        print(line)


def _apply_bootstrap_choice(home: Path, chosen: list[str] | None) -> int:
    if chosen is None:
        print("已取消，没有写入。")
        return 0
    if not chosen:
        print("没有选择，没有写入。")
        return 0
    return _install_tools(home, list(chosen))


def _bootstrap(home: Path) -> int:
    import termios

    items = _items_from_status(_detect_tools(home))
    if not sys.stdin.isatty():
        _print_bootstrap_plain(items)
        return 0
    try:
        chosen = _choose_on_tty(items)
    except (termios.error, OSError) as exc:
        print(f"没能打开勾选：{exc}。改用 box2table install 名字。", file=sys.stderr)
        return 1
    return _apply_bootstrap_choice(home, chosen)


def _is_setup_argv(argv: list[str]) -> bool:
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return False
        if token == "--home":
            index += 2
            continue
        if token.startswith("--home="):
            index += 1
            continue
        if token.startswith("-"):
            return False
        return token in ("install", "uninstall", "bootstrap")
    return False


def _setup_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="box2table",
        description="安装或卸掉 /table。",
    )
    parser.add_argument("--home", help="家目录。省略时用当前用户的家目录。")
    parser.add_argument("action", choices=("install", "uninstall", "bootstrap"))
    parser.add_argument("tools", nargs="*")
    args = parser.parse_args(argv)
    if args.home is None:
        home = Path.home()
    elif not str(args.home).strip():
        print("要给 --home 一个目录。", file=sys.stderr)
        return 1
    else:
        home = Path(args.home).expanduser()
    names = _unique(args.tools)
    if args.action == "bootstrap":
        if names:
            print(
                "bootstrap 不用写工具名字，运行后在列表里勾选。"
                "也可以用 box2table install 名字。",
                file=sys.stderr,
            )
            return 1
        return _bootstrap(home)
    if args.action == "install" and not names:
        _print_install_help()
        return 0
    if _reject_unknown(names):
        return 1
    if args.action == "install":
        return _install_tools(home, names)
    return _uninstall_tools(home, names)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _is_setup_argv(argv):
        return _setup_main(argv)
    parser = argparse.ArgumentParser(
        prog="box2table",
        description="把 Unicode 方框表或 Markdown 表放进剪贴板。Google Docs、Word、Pages、PowerPoint 会各自选一种格式贴成表格。",
        epilog="勾选安装：box2table bootstrap。按名字安装：box2table install。",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="表格文件。省略时，管道有内容就读 stdin，管道里没有内容时读剪贴板；在终端里也读剪贴板。用 - 强制读 stdin。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="把 HTML 打到标准输出，不写剪贴板。",
    )
    args = parser.parse_args(argv)
    try:
        line = convert_text(read_input(args.file), args.stdout)
    except NoTable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.stdout:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
