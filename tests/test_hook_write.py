"""훅 세션 파일 덮어쓰기: Windows 에서 다른 프로세스가 읽는 중이면 거부되므로 다시 시도하고, 남은 임시 파일은 치운다."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import agent_hook  # noqa: E402

import hook_agents  # noqa: E402


class ReplaceRetryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "s.json")
        self.tmp = self.path + ".1"
        Path(self.tmp).write_text('{"a": 1}')

    def test_retries_until_free(self):
        real = os.replace
        calls = []

        def busy_twice(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(13, "busy")
            real(src, dst)

        with patch.object(agent_hook.os, "replace", side_effect=busy_twice), patch.object(agent_hook.time, "sleep"):
            agent_hook._replace(self.tmp, self.path)
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(Path(self.path).read_text()), {"a": 1})
        self.assertFalse(os.path.exists(self.tmp))

    def test_gives_up_and_removes_tmp(self):
        with patch.object(agent_hook.os, "replace", side_effect=PermissionError(13, "busy")) as rep, \
                patch.object(agent_hook.time, "sleep"):
            agent_hook._replace(self.tmp, self.path)
        self.assertEqual(rep.call_count, agent_hook.REPLACE_TRIES)
        self.assertFalse(os.path.exists(self.tmp))

    @unittest.skipUnless(os.name == "nt", "Windows 에서만 열린 파일 덮어쓰기가 거부된다")
    def test_real_reader_on_windows(self):
        Path(self.path).write_text("{}")
        reader = subprocess.Popen([sys.executable, "-c", f"f=open(r'{self.path}'); import time; time.sleep(0.6)"])
        time.sleep(0.3)
        agent_hook._replace(self.tmp, self.path)  # 읽는 쪽이 닫을 때까지 기다렸다가 성공
        reader.wait()
        self.assertEqual(json.loads(Path(self.path).read_text()), {"a": 1})
        self.assertFalse(os.path.exists(self.tmp))


class StatuslineTeeTest(unittest.TestCase):
    """Claude 한도 스냅샷 저장도 같은 문제: 덮어쓰기가 거부되면 다시 시도하고, 끝내 안 되면 임시 파일을 지운다."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "hooks"))
        import statusline_tee

        self.st = statusline_tee
        self.home = tempfile.mkdtemp()
        self.env = patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.dir = Path(self.home, ".cache", "d200")

    def test_saves_after_busy(self):
        real, calls = os.replace, []

        def busy_once(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, "busy")
            real(src, dst)

        with patch.object(self.st.os, "replace", side_effect=busy_once), patch.object(self.st.time, "sleep"):
            self.st.save(b'{"rate_limits": {}}')
        self.assertEqual((self.dir / "claude_status.json").read_bytes(), b'{"rate_limits": {}}')
        self.assertEqual(list(self.dir.glob(".claude_status.*")), [])

    def test_gives_up_without_leftover(self):
        with patch.object(self.st.os, "replace", side_effect=PermissionError(13, "busy")), \
                patch.object(self.st.time, "sleep"):
            self.st.save(b"{}")
        self.assertEqual(list(self.dir.glob(".claude_status.*")), [])


class ScanCleanupTest(unittest.TestCase):
    def test_old_tmp_removed_fresh_kept(self):
        d = tempfile.mkdtemp()
        old, fresh = Path(d, "x.json.111"), Path(d, "x.json.222")
        old.write_text("{}")
        fresh.write_text("{}")
        past = time.time() - 120
        os.utime(old, (past, past))
        script = hook_agents.SCAN.replace(hook_agents.AGENTS_DIR, d.replace("\\", "/"))
        r = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(r.stdout), [])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
