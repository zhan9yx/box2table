import io
import os
import subprocess
import sys
import tempfile
import termios
import tty
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import table2docs  # noqa: E402

IDS = (
    "claude",
    "grok",
    "codex",
    "pi",
    "opencode",
    "gemini",
    "qwen",
    "cursor",
    "copilot",
    "droid",
)


def _block_home() -> Path:
    raise AssertionError("不要读真实家目录")


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self._orig_home = Path.home
        self._orig_run = subprocess.run
        Path.home = _block_home

        def block_run(*args, **kwargs):
            command = args[0] if args else kwargs.get("args", kwargs.get("command"))
            text = " ".join(command) if isinstance(command, (list, tuple)) else str(command)
            if any(name in text for name in ("pbpaste", "pbcopy", "osascript")):
                raise AssertionError("bootstrap 测试不应碰剪贴板")
            if isinstance(command, (list, tuple)) and command:
                if Path(str(command[0])).name == "which":
                    raise AssertionError("不要用 shell 跑 which")
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

    def test_detect_command_config_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".gemini").mkdir()

            def which(name: str):
                return "/usr/local/bin/claude" if name == "claude" else None

            found = dict(table2docs._detect_tools(home, which=which, app_dirs=[]))
            self.assertEqual(set(found), set(IDS))
            self.assertEqual(found["claude"], "已安装")
            self.assertEqual(found["gemini"], "只看到配置目录")
            for name in IDS:
                if name not in ("claude", "gemini"):
                    self.assertEqual(found[name], "没检测到", name)

    def test_opencode_config_dir_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".opencode").mkdir()
            found = dict(table2docs._detect_tools(home, which=lambda _name: None, app_dirs=[]))
            self.assertEqual(found["opencode"], "只看到配置目录")
            (home / ".opencode").rmdir()
            (home / ".config" / "opencode").mkdir(parents=True)
            found = dict(table2docs._detect_tools(home, which=lambda _name: None, app_dirs=[]))
            self.assertEqual(found["opencode"], "只看到配置目录")

    def test_app_bundles_not_chatgpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            apps = root / "apps"
            home.mkdir()
            apps.mkdir()
            (apps / "Cursor.app").mkdir()
            (apps / "Claude.app").mkdir()
            (apps / "ChatGPT.app").mkdir()

            def which(name: str):
                self.assertNotEqual(name, "agent")
                return None

            found = dict(table2docs._detect_tools(home, which=which, app_dirs=[apps]))
            self.assertEqual(found["cursor"], "已安装")
            self.assertEqual(found["claude"], "已安装")
            self.assertEqual(found["codex"], "没检测到")

    def test_agent_command_is_not_grok_or_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            def which(name: str):
                if name == "agent":
                    return "/usr/local/bin/agent"
                return None

            found = dict(table2docs._detect_tools(home, which=which, app_dirs=[]))
            self.assertEqual(found["grok"], "没检测到")
            self.assertEqual(found["cursor"], "没检测到")

    def test_default_which_is_shutil(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(table2docs.shutil, "which", return_value=None) as which:
                found = dict(table2docs._detect_tools(home, app_dirs=[]))
            self.assertEqual(found["claude"], "没检测到")
            self.assertEqual([call.args[0] for call in which.call_args_list], list(IDS))
            self.assertEqual(
                table2docs._app_dirs(home),
                (Path("/Applications"), home / "Applications"),
            )

    def test_choice_loop(self):
        items = table2docs._items_from_status([
            ("claude", "已安装"),
            ("gemini", "没检测到"),
        ])
        self.assertTrue(items[0][2])
        self.assertFalse(items[1][2])
        self.assertEqual(
            table2docs._choose_tools(items, ["\x1b[B", " ", "\r"]),
            ["claude", "gemini"],
        )
        self.assertIsNone(table2docs._choose_tools(items, ["q"]))
        self.assertIsNone(table2docs._choose_tools(items, ["Q"]))
        self.assertIsNone(table2docs._choose_tools(items, []))
        self.assertEqual(table2docs._choose_tools(items, [" ", "\r"]), [])

    def test_parse_keys(self):
        parse = table2docs._parse_bootstrap_key
        self.assertEqual(parse("\x1b[A"), "up")
        self.assertEqual(parse("\x1b[B"), "down")
        self.assertEqual(parse("\x1bOA"), "up")
        self.assertEqual(parse("\x1bOB"), "down")
        self.assertEqual(parse(" "), "toggle")
        self.assertEqual(parse("\r"), "confirm")
        self.assertEqual(parse("q"), "cancel")

    def test_screen_shows_notes_before_confirm(self):
        items = table2docs._items_from_status([
            ("claude", "已安装"),
            ("pi", "没检测到"),
        ])
        frames: list[str] = []

        def draw(rows, checked, index):
            frames.append(table2docs._bootstrap_screen(rows, checked, index))

        self.assertIsNone(table2docs._choose_tools(items, ["q"], draw=draw))
        self.assertTrue(frames)
        screen = frames[0]
        self.assertIn("新开一次", screen)
        self.assertIn("Grok 也能看到这条命令", screen)
        self.assertIn("Gemini 和 Qwen 执行前会先问", screen)
        self.assertIn("Pi 在本地直接跑", screen)
        self.assertIn("脚本文字会进对话", screen)
        self.assertIn("↑↓ 移动，空格勾选，回车安装，q 取消。", screen)
        self.assertIn("[x]", screen)
        self.assertIn("[ ]", screen)

    def test_main_not_a_tty_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            def fail_tty(*_args, **_kwargs):
                raise AssertionError("非 tty 不应打开勾选")

            with (
                mock.patch.object(table2docs.sys.stdin, "isatty", return_value=False),
                mock.patch.object(table2docs, "_choose_on_tty", side_effect=fail_tty),
            ):
                code, out, err = self._run(["bootstrap", "--home", str(home)])
            self.assertEqual(code, 0, err)
            self.assertEqual(list(home.rglob("*")), [])
            self.assertTrue(any(word in out for word in ("已安装", "只看到配置目录", "没检测到")))
            self.assertIn("不是交互终端", out)
            self.assertIn("新开一次", out)
            self.assertIn("table2docs install 名字", out)
            for name in IDS:
                self.assertIn(name, out)
            self.assertEqual(err, "")

    def test_bootstrap_rejects_tool_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, _out, err = self._run(["bootstrap", "--home", str(home), "claude"])
            self.assertEqual(code, 1)
            self.assertEqual(
                err,
                "bootstrap 不用写工具名字，运行后在列表里勾选。也可以用 table2docs install 名字。\n",
            )
            self.assertEqual(list(home.rglob("*")), [])

    def test_empty_home_errors(self):
        code, _out, err = self._run(["bootstrap", "--home", ""])
        self.assertEqual(code, 1)
        self.assertIn("要给 --home 一个目录。", err)

    def test_injected_tty_confirm_cancel_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                mock.patch.object(table2docs.sys.stdin, "isatty", return_value=True),
                mock.patch.object(table2docs, "_choose_on_tty", return_value=["pi"]),
            ):
                code, out, err = self._run(["bootstrap", "--home", str(home)])
            self.assertEqual(code, 0, err)
            files = sorted(path for path in home.rglob("*") if path.is_file())
            self.assertEqual(files, [home / ".pi/agent/extensions/table2docs.ts"])
            self.assertIn("table2docs-managed-command", files[0].read_text(encoding="utf-8"))
            self.assertIn("新开一次", out)
            self.assertNotIn("不是交互终端", out)

        for chosen, text in (
            (None, "已取消，没有写入。"),
            ([], "没有选择，没有写入。"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                with (
                    mock.patch.object(table2docs.sys.stdin, "isatty", return_value=True),
                    mock.patch.object(table2docs, "_choose_on_tty", return_value=chosen),
                ):
                    code, out, err = self._run(["bootstrap", "--home", str(home)])
                self.assertEqual(code, 0, err)
                self.assertIn(text, out)
                self.assertEqual(list(home.rglob("*")), [])

    def test_tty_cancel_restores_terminal_before_message(self):
        saved = ["attrs"]
        seen: dict[str, object] = {}

        def tcgetattr(fd):
            seen["get"] = fd
            return saved

        def tcsetattr(fd, when, attr):
            seen["restored"] = (fd, when, attr)

        def setcbreak(fd):
            seen["cbreak"] = fd

        def keys(_fd):
            yield "q"

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                mock.patch.object(table2docs.sys.stdin, "isatty", return_value=True),
                mock.patch.object(table2docs.sys.stdin, "fileno", return_value=7),
                mock.patch.object(termios, "tcgetattr", tcgetattr),
                mock.patch.object(termios, "tcsetattr", tcsetattr),
                mock.patch.object(tty, "setcbreak", setcbreak),
                mock.patch.object(table2docs, "_tty_keys", keys),
            ):
                code, out, err = self._run(["bootstrap", "--home", str(home)])
            self.assertEqual(code, 0, err)
            self.assertEqual(list(home.rglob("*")), [])
            self.assertEqual(seen["cbreak"], 7)
            self.assertEqual(seen["restored"], (7, termios.TCSADRAIN, saved))
            self.assertIn("\033[?1049h", out)
            self.assertIn("\033[?1049l", out)
            self.assertLess(out.index("新开一次"), out.index("\033[?1049l"))
            self.assertLess(out.index("\033[?1049l"), out.index("已取消，没有写入。"))
            self.assertIn("↑↓ 移动，空格勾选，回车安装，q 取消。", out)

    def test_ctrl_c_restores_terminal(self):
        saved = ["attrs"]
        restored = []

        def tcgetattr(_fd):
            return saved

        def tcsetattr(fd, when, attr):
            restored.append((fd, when, attr))

        def keys(_fd):
            raise KeyboardInterrupt
            yield "q"

        stdout = io.StringIO()
        with (
            redirect_stdout(stdout),
            mock.patch.object(table2docs.sys.stdin, "fileno", return_value=7),
            mock.patch.object(termios, "tcgetattr", tcgetattr),
            mock.patch.object(termios, "tcsetattr", tcsetattr),
            mock.patch.object(tty, "setcbreak", lambda _fd: None),
            mock.patch.object(table2docs, "_tty_keys", keys),
        ):
            chosen = table2docs._choose_on_tty([("pi", "没检测到", False)])
        self.assertIsNone(chosen)
        self.assertEqual(restored, [(7, termios.TCSADRAIN, saved)])
        text = stdout.getvalue()
        self.assertIn("\033[?1049h", text)
        self.assertIn("\033[?1049l", text)

    def test_stdout_bootstrap_is_a_filename(self):
        self.assertFalse(table2docs._is_setup_argv(["--stdout", "bootstrap"]))
        self.assertTrue(table2docs._is_setup_argv(["bootstrap"]))
        self.assertTrue(table2docs._is_setup_argv(["--home", "/tmp", "bootstrap"]))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "bootstrap").write_text("没有表格\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(home)
                code, _out, err = self._run(["--stdout", "bootstrap"])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 1)
            self.assertIn("没找到表格", err)
            self.assertNotIn("不是交互终端", err)
            files = sorted(path for path in home.rglob("*") if path.is_file())
            self.assertEqual(files, [home / "bootstrap"])

    def test_install_without_names_mentions_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out, err = self._run(["--home", str(home), "install"])
            self.assertEqual(code, 0, err)
            self.assertIn("要勾选安装，运行 table2docs bootstrap。", out)
            self.assertEqual(list(home.rglob("*")), [])

    def test_help_mentions_bootstrap_and_install(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                table2docs.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = stdout.getvalue()
        self.assertIn("table2docs bootstrap", text)
        self.assertIn("table2docs install", text)

    def test_termios_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                mock.patch.object(table2docs.sys.stdin, "isatty", return_value=True),
                mock.patch.object(
                    table2docs,
                    "_choose_on_tty",
                    side_effect=termios.error("no tty"),
                ),
            ):
                code, _out, err = self._run(["bootstrap", "--home", str(home)])
            self.assertEqual(code, 1)
            self.assertIn("没能打开勾选", err)
            self.assertIn("table2docs install", err)
            self.assertEqual(list(home.rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
