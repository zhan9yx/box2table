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


def read_input(path: str | None) -> str:
    if path and path != "-":
        return Path(path).read_text(encoding="utf-8")
    if path == "-" or not sys.stdin.isatty():
        return sys.stdin.read()
    return read_clipboard()


def summary(tables: list[Table]) -> str:
    if len(tables) == 1:
        rows = len(tables[0].rows)
        cols = len(tables[0].widths)
        shape = f"{rows} 行 × {cols} 列"
    else:
        shape = f"{len(tables)} 个表格"
    return f"已放入剪贴板：{shape}。可粘贴到 Google Docs、Word、Pages、PowerPoint。"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="table2docs",
        description="把 Unicode 方框表或 Markdown 表放进剪贴板。Google Docs、Word、Pages、PowerPoint 会各自选一种格式贴成表格。",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="表格文件。省略时，管道读 stdin，否则读剪贴板。用 - 强制读 stdin。",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="把 HTML 打到标准输出，不写剪贴板。",
    )
    args = parser.parse_args(argv)
    try:
        source = read_input(args.file)
        tables = parse_tables(source)
        if not tables:
            print(
                "没找到表格。支持 Unicode 方框表（┌─┬─┐）和 Markdown 表。",
                file=sys.stderr,
            )
            return 1
        document = tables_to_html(tables)
        if args.stdout:
            sys.stdout.write(document)
            if not document.endswith("\n"):
                sys.stdout.write("\n")
            return 0
        copy_html(document, source, tables_to_rtf(tables), tables_to_tsv(tables))
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(summary(tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
