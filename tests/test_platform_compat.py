"""platform_compat · 훅 상태 수집이 지금 OS(특히 Windows)에서 제대로 도는지 확인한다."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_compat as pc


class FontTest(unittest.TestCase):
    def test_fonts_exist_and_load(self):
        from PIL import ImageFont

        for kind in ("kr", "rounded"):
            for w in pc.WEIGHTS:
                path, index = pc.font_file(kind, w)
                self.assertTrue(os.path.exists(path), f"{kind}/{w}: {path}")
                ImageFont.truetype(path, 20, index=index)
        f = pc.truetype("kr", 24, "bold")
        self.assertGreater(f.getlength("한글"), 0)

    def test_emoji_font_renders_color(self):
        from PIL import Image, ImageDraw, ImageFont

        path, _ = pc.font_file("emoji")
        self.assertTrue(os.path.exists(path), path)
        img = Image.new("RGBA", (220, 220))
        ImageDraw.Draw(img).text((110, 110), "🐟", font=ImageFont.truetype(path, 160), embedded_color=True, anchor="mm")
        self.assertIsNotNone(img.getbbox(), "emoji drew nothing")

    @unittest.skipUnless(pc.IS_MAC, "macOS only")
    def test_mac_fonts_unchanged(self):
        self.assertEqual(pc.font_file("kr", "bold"), ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 6))
        self.assertEqual(pc.font_file("rounded")[0], "/System/Library/Fonts/SFNSRounded.ttf")


class SystemTest(unittest.TestCase):
    def test_ui_language(self):
        code = pc.ui_language()
        self.assertTrue(code is None or (isinstance(code, str) and code))

    def test_python_cmd_runs(self):
        r = subprocess.run(pc.python_cmd() + ["-c", "print(1+1)"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), "2")
        if pc.IS_MAC:
            self.assertEqual(pc.python_cmd(), ["python3"])

    def test_pid_alive(self):
        self.assertTrue(pc.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        time.sleep(0.2)
        self.assertFalse(pc.pid_alive(p.pid))

    def test_local_machine_stats(self):
        d = pc.local_machine_stats()
        if pc.IS_MAC:
            self.assertIsNone(d)  # macOS 는 machines.py 의 원래 bash 스크립트
            return
        self.assertGreaterEqual(d["cpu"], 0)
        self.assertLessEqual(d["cpu"], 100)
        self.assertGreater(d["mem"], 0)
        self.assertGreater(d["disk_total_kb"], d["disk_avail_kb"])
        self.assertLess(d["boot"], time.time())
        import machines

        st = machines._probe("This PC", None)
        self.assertTrue(st.online)
        self.assertGreater(st.mem_gb, 0)


class HookScanTest(unittest.TestCase):
    """대시보드가 세션 상태를 읽을 때 살아 있는 세션 프로세스를 건드리지 않아야 한다.
    (Windows 에서 os.kill(pid, 0) 은 그 프로세스를 강제 종료한다)"""

    def test_scan_keeps_live_session_and_removes_dead(self):
        home = tempfile.mkdtemp()
        agents = Path(home) / ".cache" / "d200" / "agents"
        agents.mkdir(parents=True)
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(live.kill)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        now = time.time()
        for name, pid in (("live", live.pid), ("dead", dead.pid)):
            (agents / f"{name}.json").write_text(json.dumps(
                {"id": name, "pid": pid, "project": "p", "agent": "claude-code", "status": "working",
                 "updated": now, "started": now}))
        env = {"HOME": home, "USERPROFILE": home}
        with mock.patch.dict(os.environ, env):
            import hook_agents

            items = hook_agents._scan(None)
        self.assertEqual([d["id"] for d in items], ["live"])
        self.assertIsNone(live.poll(), "scan killed a live session process")
        self.assertFalse((agents / "dead.json").exists())


@unittest.skipUnless(pc.IS_WINDOWS, "Windows only")
class WindowsHookTest(unittest.TestCase):
    def test_lineage_skips_shells(self):
        """python → cmd → python(훅) 처럼 셸을 거쳐도 셸이 아닌 조상을 에이전트로 고른다."""
        script = Path(tempfile.mkdtemp()) / "lineage.py"
        script.write_text("import sys, json; sys.path.insert(0, r'%s'); import agent_hook; "
                          "print(json.dumps(agent_hook._win_lineage()))" % (ROOT / "hooks"), encoding="utf-8")
        # cmd /s /c ""exe" "file"" : 공백이 든 경로(C:\Users\User Name)도 그대로 넘긴다
        r = subprocess.run(f'cmd /s /c ""{sys.executable}" "{script}""', capture_output=True, text=True, timeout=30)
        pid, name, _app_pid, _app = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertNotIn(name, ("cmd.exe", "python.exe", "conhost.exe"))
        self.assertTrue(pc.pid_alive(pid))

    def test_hook_writes_windows_record(self):
        home = tempfile.mkdtemp()
        env = {**os.environ, "HOME": home, "USERPROFILE": home}
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "t1", "cwd": str(ROOT)})
        subprocess.run(pc.python_cmd() + [str(ROOT / "hooks" / "agent_hook.py"), "claude"], input=payload,
                       text=True, env=env, timeout=30)
        rec = json.loads((Path(home) / ".cache" / "d200" / "agents" / "claude-code-t1.json").read_text())
        self.assertEqual(rec["platform"], "win32")
        self.assertEqual(rec["status"], "working")
        sys.path.insert(0, str(ROOT / "hooks"))
        import agent_hook

        self.assertEqual(rec["project"], agent_hook._project(str(ROOT)))  # git worktree 면 원래 저장소 이름
        self.assertTrue(pc.pid_alive(rec["pid"]), "recorded pid must outlive the hook (not the shell)")

    def test_codex_aux_thread_without_rollout_is_skipped(self):
        """Codex: 기록(rollout)이 없는 내부 보조 대화(제목 생성 등)는 덱에 올리지 않는다."""
        home = Path(tempfile.mkdtemp())
        codex_home = home / ".codex"
        day = codex_home / "sessions" / "2026" / "11" / "21"
        day.mkdir(parents=True)
        (day / "rollout-2026-11-21T23-19-50-th-1.jsonl").write_text("{}")  # th-1 만 기록이 있는 진짜 대화
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(codex_home)}
        agents = home / ".cache" / "d200" / "agents"
        for tid in ("th-1", "th-aux"):
            payload = json.dumps({"type": "agent-turn-complete", "thread-id": tid, "cwd": str(ROOT)})
            subprocess.run(pc.python_cmd() + [str(ROOT / "hooks" / "agent_hook.py"), "codex", payload],
                           env=env, timeout=30)
        rec = json.loads((agents / "codex-th-1.json").read_text())
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["platform"], "win32")
        self.assertFalse((agents / "codex-th-aux.json").exists(), "aux thread without rollout must be skipped")


if __name__ == "__main__":
    unittest.main()
