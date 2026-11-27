"""scripts/install-hooks.py: 임시 홈 폴더에서 돌려 진짜 ~/.claude · ~/.codex 설정은 건드리지 않는다."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install-hooks.py"
sys.path.insert(0, str(ROOT))
import platform_compat as pc  # noqa: E402


class InstallHooksTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.settings = self.home / ".claude" / "settings.json"
        self.codex = self.home / ".codex" / "config.toml"
        self.codex.write_text('model = "x"\nnotify = ["other-notify", "--flag"]\n', encoding="utf-8")

    def run_script(self, *args):
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "PYTHONUTF8": "1"}
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def data(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def ours(self, data):
        return [h for entries in data.get("hooks", {}).values() for e in entries for h in e["hooks"]
                if "agent_hook.py" in " ".join([h.get("command", ""), *h.get("args", [])])]

    def test_install_shape_per_os(self):
        self.run_script()
        hooks = self.ours(self.data())
        self.assertEqual(len(hooks), 9)  # 이벤트마다 하나
        h = hooks[0]
        if pc.IS_WINDOWS:  # 셸을 거치지 않는 프로그램 + 인자 (PowerShell 따옴표 문제를 피한다)
            self.assertEqual(h["args"][-1], "claude")
            self.assertNotIn(" ", h["command"].strip('"')[-12:])
        else:  # macOS · Linux: 원래와 같은 한 줄 명령
            self.assertTrue(h["command"].startswith("python3 "))
            self.assertNotIn("args", h)
        self.assertNotIn("statusLine", self.data())  # 요청하지 않으면 statusline 은 건드리지 않는다

    def test_reinstall_keeps_other_hooks_and_no_duplicates(self):
        self.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}),
                                 encoding="utf-8")
        self.run_script()
        self.run_script()
        stop = self.data()["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(stop[0]["hooks"][0]["command"], "mine")

    def test_uninstall_leaves_statusline_unless_asked(self):
        self.run_script("--statusline")
        self.assertIn("statusLine", self.data())
        self.run_script("--uninstall")
        self.assertEqual(self.ours(self.data()), [])
        self.assertIn("statusLine", self.data())
        self.run_script("--uninstall", "--statusline")
        self.assertNotIn("statusLine", self.data())

    def test_statusline_respects_existing_and_customized(self):
        other = {"type": "command", "command": "my-statusline"}
        self.settings.write_text(json.dumps({"statusLine": other}), encoding="utf-8")
        self.run_script("--statusline-only")
        self.assertEqual(self.data()["statusLine"], other)  # 다른 statusline 은 바꾸지 않는다
        custom = {"type": "command", "command": '"/x/statusline-tee.sh" my-statusline'}  # 가이드대로 직접 이어 붙인 것
        self.settings.write_text(json.dumps({"statusLine": custom}), encoding="utf-8")
        self.run_script("--uninstall", "--statusline-only")
        self.assertEqual(self.data()["statusLine"], custom)

    def test_codex_wrap_and_restore(self):
        self.run_script("--codex")
        text = self.codex.read_text(encoding="utf-8")
        self.assertIn("agent_hook.py", text)
        self.assertIn('"other-notify", "--flag"', text)  # 기존 notify 는 뒤에 이어서 실행
        self.run_script("--uninstall")
        self.assertEqual(self.codex.read_text(encoding="utf-8").rstrip("\n"),  # 마지막 줄바꿈은 원래 코드도 지운다
                         'model = "x"\nnotify = ["other-notify", "--flag"]')


if __name__ == "__main__":
    unittest.main()
