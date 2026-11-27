"""hooks/statusline_tee.py (statusline-tee.sh 의 Python 판): 옛 스냅샷이 최신 한도 창을 덮어쓰지 않는다."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))

import statusline_tee  # noqa: E402

# bash 판 시험(tests/test_usage_claude.py)과 같은 입력
LOCAL_STATUS = {
    "rate_limits": {
        "seven_day": {"used_percentage": 31, "resets_at": "2030-01-06T00:00:00Z"},
    },
}
NEWER_STATUS = {
    "rate_limits": {
        "five_hour": {"used_percentage": 22, "resets_at": "2030-01-01T04:00:00Z"},
        "seven_day": {"used_percentage": 58, "resets_at": "2030-01-07T00:00:00Z"},
    },
}


def run_tee(home: Path, snapshot: dict, *forward: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "hooks" / "statusline_tee.py"), *forward],
                          input=json.dumps(snapshot).encode(), capture_output=True, timeout=30,
                          env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)})


class StatuslineTeePyTests(unittest.TestCase):
    def test_older_idle_snapshot_preserves_newer_windows(self):
        """bash 판 test_older_idle_snapshot_preserves_newer_windows 와 같은 시험."""
        home = Path(tempfile.mkdtemp(prefix="agentdeck-statusline-py-"))
        status = home / ".cache" / "d200" / "claude_status.json"
        for snapshot in (NEWER_STATUS, LOCAL_STATUS):
            run_tee(home, snapshot)
            self.assertEqual(json.loads(status.read_text(encoding="utf-8"))["rate_limits"],
                             NEWER_STATUS["rate_limits"])
        self.assertEqual([p.name for p in status.parent.glob(".claude_status.*")], [], "temp files left behind")

    def test_newer_snapshot_replaces_and_other_fields_follow_input(self):
        old = json.dumps({**LOCAL_STATUS, "model": "a"}).encode()
        new = {**NEWER_STATUS, "model": "b"}
        merged = json.loads(statusline_tee.merge_fresher(old, json.dumps(new).encode()))
        self.assertEqual(merged, new)
        # 깨진 기존 파일 · 깨진 입력은 입력을 그대로 쓴다
        self.assertEqual(statusline_tee.merge_fresher(b"{", b'{"x": 1}'), b'{"x": 1}')
        self.assertEqual(statusline_tee.merge_fresher(old, b"not json"), b"not json")

    def test_forwards_original_input_to_wrapped_statusline(self):
        """감싼 statusline 명령에는 합치기 전 원래 입력이 그대로 간다."""
        home = Path(tempfile.mkdtemp(prefix="agentdeck-statusline-py-"))
        run_tee(home, NEWER_STATUS)
        echo = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"]
        r = run_tee(home, LOCAL_STATUS, *echo)
        self.assertEqual(json.loads(r.stdout), LOCAL_STATUS)


if __name__ == "__main__":
    unittest.main()
