import html
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import table2docs  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "menu.txt").read_text(encoding="utf-8")

EXPECTED = [
    ["菜名", "份数", "怎么做"],
    ["番茄炒蛋", "2", "番茄先炒出汁，鸡蛋后下"],
    ["青椒土豆丝", "1", "土豆切细"],
    ["清炒时蔬", "3", "蒜末后放"],
    ["周末菜单可调整", "4", "比如家里人说不吃辣，或者另一顿的 leftover，算进了这桌。不改菜单"],
    [
        "红烧肉要提前腌一晚",
        "2",
        '五花肉先煸出油。冰糖炒到"琥珀色"，酱油和料酒沿锅边淋。菜单里写上全部调料',
    ],
    ["火候", "2", "一是糖色炒过了发苦，二是盐和糖的先后不同。笔记里补了一句：糖色停在琥珀色"],
    ["器具不够", "2 道，加上备菜清单里还有 3 道", "砂锅、蒸笼、烤箱，周末再买"],
]


class ParseTests(unittest.TestCase):
    def test_sample_cells(self):
        tables = table2docs.parse_tables(FIXTURE)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].widths, [16, 14, 44])
        self.assertEqual(tables[0].rows, EXPECTED)

    def test_sample_ignores_surrounding_prose(self):
        tables = table2docs.parse_tables("前面的说明\n\n" + FIXTURE + "\n后面的说明\n")
        self.assertEqual(tables[0].rows, EXPECTED)

    def test_soft_wrap_latin_and_cjk(self):
        text = """\
┌────────┐
│ head   │
├────────┤
│ hello  │
│ world  │
└────────┘
┌────────┐
│ 标题   │
├────────┤
│ 中文字 │
│ 测试   │
└────────┘
"""
        tables = table2docs.parse_tables(text)
        self.assertEqual([table.rows for table in tables], [
            [["head"], ["hello world"]],
            [["标题"], ["中文字测试"]],
        ])

    def test_window_wraps_become_one_line(self):
        text = """\
┌──────────────┬──────────────┐
│ 标题         │ 说明         │
├──────────────┼──────────────┤
│ 第一行       │ alpha        │
│ 第二行       │ beta         │
└──────────────┴──────────────┘
"""
        tables = table2docs.parse_tables(text)
        self.assertEqual(tables[0].rows[1], ["第一行第二行", "alpha beta"])
        document = table2docs.tables_to_html(tables)
        self.assertNotIn("<br>", document)
        self.assertIn("第一行第二行", document)
        self.assertIn("alpha beta", document)

    def test_ascii_grid(self):
        text = """\
+------+------+
| name | n    |
+------+------+
| sql  | 10   |
+------+------+
"""
        tables = table2docs.parse_tables(text)
        self.assertEqual(tables[0].rows, [["name", "n"], ["sql", "10"]])

    def test_markdown(self):
        text = """\
| 菜名 | 份数 |
| --- | --- |
| 番茄炒蛋 | 2 |
| 青椒土豆丝 | 1 |
| 1<2 & 3 | ok |
"""
        tables = table2docs.parse_tables(text)
        self.assertEqual(
            tables[0].rows,
            [["菜名", "份数"], ["番茄炒蛋", "2"], ["青椒土豆丝", "1"], ["1<2 & 3", "ok"]],
        )
        self.assertIn("1&lt;2 &amp; 3", table2docs.tables_to_html(tables))

    def test_html_uses_docs_table_markup(self):
        document = table2docs.tables_to_html(table2docs.parse_tables(FIXTURE))
        self.assertIn("<table", document)
        self.assertIn("border-collapse:collapse", document)
        self.assertIn("font-weight:700", document)
        self.assertIn("font-weight:400", document)
        self.assertNotIn("<br>", document)
        rtf = table2docs.tables_to_rtf(table2docs.parse_tables(FIXTURE))
        self.assertEqual(rtf.count("\\trowd"), 8)
        plain = subprocess.run(
            ["textutil", "-stdin", "-format", "rtf", "-convert", "txt", "-stdout"],
            input=rtf.encode(),
            capture_output=True,
            check=True,
        ).stdout.decode()
        self.assertIn("红烧肉要提前腌一晚", plain)
        self.assertIn("2 道，加上备菜清单里还有 3 道", plain)
        tsv = table2docs.tables_to_tsv(table2docs.parse_tables(FIXTURE))
        self.assertEqual(len(tsv.splitlines()), 8)
        self.assertEqual(tsv.splitlines()[0].split("\t"), EXPECTED[0])
        for row in EXPECTED:
            for cell in row:
                self.assertIn(html.escape(cell), document)

    def test_missing_table(self):
        self.assertEqual(table2docs.parse_tables("只有一段话"), [])


def _paste(prefer: str | None = None) -> str:
    command = ["pbpaste"] if prefer is None else ["pbpaste", "-Prefer", prefer]
    return subprocess.run(command, text=True, capture_output=True, check=True).stdout


class ClipboardTests(unittest.TestCase):
    def test_roundtrip_public_html(self):
        saved_plain = _paste()
        saved_html = _paste("public.html")
        fixture = str(ROOT / "tests" / "fixtures" / "menu.txt")
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "table2docs.py"), fixture],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("8 行 × 3 列", result.stdout)
            html_back = _paste("public.html")
            plain_back = _paste()
            self.assertIn("<table", html_back)
            self.assertIn("border-collapse:collapse", html_back)
            self.assertIn("器具不够", html_back)
            self.assertIn("2 道，加上备菜清单里还有 3 道", html_back)
            self.assertIn("琥珀色", html_back)
            self.assertTrue(plain_back.startswith("┌"))
            flavors = subprocess.run(
                ["osascript", "-l", "JavaScript", "-"],
                input=(
                    'ObjC.import("AppKit");\n'
                    "const pb = $.NSPasteboard.generalPasteboard;\n"
                    "const types = pb.types;\n"
                    "const names = [];\n"
                    "for (let i = 0; i < types.count; i++) names.push(ObjC.unwrap(types.objectAtIndex(i)));\n"
                    "const rtf = pb.stringForType($.NSPasteboardTypeRTF);\n"
                    'const tsv = pb.stringForType("public.utf8-tab-separated-values-text");\n'
                    "JSON.stringify({types: names, rtf: rtf ? String(rtf.js) : '', tsv: tsv ? String(tsv.js) : ''});\n"
                ),
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            payload = json.loads(flavors)
            self.assertIn("public.html", payload["types"])
            self.assertIn("public.rtf", payload["types"])
            self.assertIn("public.utf8-tab-separated-values-text", payload["types"])
            self.assertIn("\\trowd", payload["rtf"])
            self.assertIn("菜名\t份数\t怎么做", payload["tsv"])
            stdout = subprocess.run(
                [sys.executable, str(ROOT / "table2docs.py"), "--stdout", "-"],
                input="没有表格",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(stdout.returncode, 1)
            self.assertIn("没找到表格", stdout.stderr)
        finally:
            plain = saved_plain or saved_html
            html_text = saved_html or saved_plain
            if plain and html_text:
                table2docs.copy_html(html_text, plain)
            else:
                subprocess.run(["pbcopy"], input="", text=True, check=False)


if __name__ == "__main__":
    unittest.main()
