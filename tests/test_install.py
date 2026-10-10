import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import table2docs  # noqa: E402

PATHS = {
    "claude": ".claude/commands/table.md",
    "grok": ".grok/commands/table.md",
    "codex": ".codex/skills/table/SKILL.md",
    "pi": ".pi/agent/extensions/table2docs.ts",
    "opencode": ".config/opencode/commands/table.md",
    "gemini": ".gemini/commands/table.toml",
    "qwen": ".qwen/commands/table.md",
    "cursor": ".cursor/commands/table.md",
    "copilot": ".copilot/skills/table/SKILL.md",
    "droid": ".factory/commands/table",
}
FOUR = ("claude", "grok", "codex", "pi")
NEW = ("opencode", "gemini", "qwen", "cursor", "copilot", "droid")


def _block_home() -> Path:
    raise AssertionError("不要读真实家目录")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._orig_home = Path.home
        self._orig_run = subprocess.run
        Path.home = _block_home

        def block_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args", kwargs.get("command"))
            text = " ".join(command) if isinstance(command, (list, tuple)) else str(command)
            if any(name in text for name in ("pbpaste", "pbcopy", "osascript")):
                raise AssertionError("install 测试不应碰剪贴板")
            return self._orig_run(*args, **kwargs)

        subprocess.run = block_run

    def tearDown(self):
        Path.home = self._orig_home
        subprocess.run = self._orig_run

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = table2docs.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_install_without_targets_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out, err = self._run(["--home", str(home), "install"])
            self.assertEqual(code, 0, err)
            self.assertEqual(list(home.rglob("*")), [])
            for name in PATHS:
                self.assertIn(name, out)
            self.assertIn("/table", out)
            self.assertIn("Grok 也能看到这条命令", out)
            self.assertEqual(err, "")

    def test_install_and_uninstall_four_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out, err = self._run(["install", "--home", str(home), *FOUR])
            self.assertEqual(code, 0, err)
            self.assertIn("/reload", out)
            self.assertNotIn("关闭进程", out)
            paths = [home / PATHS[name] for name in FOUR]
            files = sorted(path for path in home.rglob("*") if path.is_file())
            self.assertEqual(files, sorted(paths))
            claude = (home / PATHS["claude"]).read_text(encoding="utf-8")
            grok = (home / PATHS["grok"]).read_text(encoding="utf-8")
            self.assertEqual(claude, grok)
            for text in (claude, grok):
                self.assertNotIn("!{", text)
                self.assertNotIn("!`", text)
            for name in FOUR:
                relative = PATHS[name]
                text = (home / relative).read_text(encoding="utf-8")
                self.assertIn("table2docs-managed-command", text)
                self.assertIn("table2docs", text)
                self.assertIn("剪贴板", text)
                for banned in (
                    "--last",
                    "--source",
                    "--hook",
                    "--from-messages",
                    "--nth",
                    "--session",
                ):
                    self.assertNotIn(banned, text)
                self.assertIn(str(home / relative), out)
            codex = (home / PATHS["codex"]).read_text(encoding="utf-8")
            self.assertIn("name: table", codex)
            self.assertIn("只在用户输入 /table 时使用", codex)
            self.assertIn("disable-model-invocation: true", codex)
            self.assertIn("user-invocable: true", codex)
            pi = (home / PATHS["pi"]).read_text(encoding="utf-8")
            self.assertIn("import type", pi)
            self.assertIn('registerCommand("table"', pi)
            self.assertIn("ctx.ui.notify", pi)
            self.assertNotIn("sessionManager", pi)
            self.assertIn("description:", claude)

            code, out, err = self._run(["uninstall", "--home", str(home), *FOUR])
            self.assertEqual(code, 0, err)
            for path in paths:
                self.assertFalse(path.exists())
                self.assertIn(str(path), out)

    def test_reinstall_updates_marked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out, err = self._run(["install", "--home", str(home), "pi"])
            self.assertEqual(code, 0, err)
            self.assertIn("已写入", out)
            code, out, err = self._run(["install", "--home", str(home), "pi"])
            self.assertEqual(code, 0, err)
            self.assertIn("已更新", out)
            self.assertNotIn("已写入", out)
            self.assertIn("/reload", out)
            text = (home / PATHS["pi"]).read_text(encoding="utf-8")
            self.assertIn("table2docs-managed-command", text)

    def test_uninstall_keeps_unmarked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / PATHS["claude"]
            path.parent.mkdir(parents=True)
            original = "我自己的命令\n"
            path.write_text(original, encoding="utf-8")
            code, out, err = self._run(["uninstall", "--home", str(home), "claude"])
            self.assertEqual(code, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertIn("没有删除", err)
            self.assertNotIn("已删除", out)

    def test_install_does_not_overwrite_unmarked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / PATHS["codex"]
            path.parent.mkdir(parents=True)
            original = "name: table\n我自己的技能\n"
            path.write_text(original, encoding="utf-8")
            code, _out, err = self._run(["install", "--home", str(home), "codex"])
            self.assertEqual(code, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertIn("没有覆盖", err)

    def test_install_six_new_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, _out, err = self._run(["install", "--home", str(home), *NEW])
            self.assertEqual(code, 0, err)
            paths = [home / PATHS[name] for name in NEW]
            files = sorted(path for path in home.rglob("*") if path.is_file())
            self.assertEqual(files, sorted(paths))
            banned = (
                "--last",
                "--source",
                "--hook",
                "--from-messages",
                "--nth",
                "--session",
            )
            for name in NEW:
                text = (home / PATHS[name]).read_text(encoding="utf-8")
                self.assertIn("table2docs-managed-command", text)
                self.assertIn("剪贴板", text)
                self.assertIn("table2docs", text)
                for item in banned:
                    self.assertNotIn(item, text)
            opencode = (home / PATHS["opencode"]).read_text(encoding="utf-8")
            self.assertIn("!`table2docs`", opencode)
            gemini = (home / PATHS["gemini"]).read_text(encoding="utf-8")
            self.assertRegex(gemini, r'(?s)prompt = """.*!\{table2docs\}.*"""')
            qwen = (home / PATHS["qwen"]).read_text(encoding="utf-8")
            self.assertIn("!{table2docs}", qwen)
            self.assertNotIn("!`", qwen)
            self.assertNotRegex(qwen, r"!`[^`]*`")
            cursor = (home / PATHS["cursor"]).read_text(encoding="utf-8")
            self.assertEqual(cursor, table2docs._COMMAND_MD)
            for text in (cursor, table2docs._tool_body("claude"), table2docs._tool_body("grok")):
                self.assertNotIn("!{", text)
                self.assertNotIn("!`", text)
            copilot = (home / PATHS["copilot"]).read_text(encoding="utf-8")
            self.assertNotIn("disable-model-invocation", copilot)
            self.assertNotIn("user-invocable", copilot)
            droid_path = home / PATHS["droid"]
            droid = droid_path.read_text(encoding="utf-8")
            self.assertTrue(droid.startswith("#!/bin/sh\n"))
            self.assertIn("exec table2docs", droid)
            self.assertTrue(droid_path.stat().st_mode & 0o111)
            for relative in (".agents", ".claude/skills", ".cursor/skills", ".grok/skills"):
                self.assertFalse((home / relative).exists(), relative)
            self.assertEqual(list(home.rglob("hooks.json")), [])
            droid_path.chmod(0o644)
            code, out, err = self._run(["install", "--home", str(home), "droid"])
            self.assertEqual(code, 0, err)
            self.assertIn("已更新", out)
            self.assertTrue(droid_path.stat().st_mode & 0o111)
            files = sorted(path for path in home.rglob("*") if path.is_file())
            self.assertEqual(files, sorted(paths))

    def test_unknown_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, _out, err = self._run(["install", "--home", str(home), "nope"])
            self.assertEqual(code, 1)
            self.assertEqual(list(home.rglob("*")), [])
            self.assertIn("nope", err)
            for name in PATHS:
                self.assertIn(name, err)
            code, _out, err = self._run(["--home", str(home), "uninstall", "nope"])
            self.assertEqual(code, 1)
            self.assertIn("nope", err)
            for name in PATHS:
                self.assertIn(name, err)
            self.assertEqual(list(home.rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
