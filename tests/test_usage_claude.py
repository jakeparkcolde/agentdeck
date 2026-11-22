"""Claude quota selection and snapshot persistence without device or SSH access."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import usage
from usage import Window, pick_fresher_window


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
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
STATUSLINE = Path(__file__).resolve().parents[1] / "statusline-tee.sh"


class PickFresherWindowTests(unittest.TestCase):
    def test_ignores_expired_windows(self):
        expired = Window(99, NOW - timedelta(seconds=1))
        at_reset = Window(98, NOW)
        active = Window(31, NOW + timedelta(hours=1))

        self.assertEqual(pick_fresher_window(expired, active, NOW), active)
        self.assertEqual(pick_fresher_window(active, at_reset, NOW), active)
        self.assertEqual(pick_fresher_window(expired, at_reset, NOW), Window())

    def test_prefers_later_reset(self):
        earlier = Window(99, NOW + timedelta(hours=1))
        later = Window(31, NOW + timedelta(hours=2))

        self.assertEqual(pick_fresher_window(earlier, later, NOW), later)

    def test_prefers_higher_usage_at_same_reset(self):
        reset = NOW + timedelta(hours=1)
        higher = Window(58, reset)

        self.assertEqual(pick_fresher_window(Window(31, reset), higher, NOW), higher)

    def test_prefers_higher_usage_without_reset_timestamps(self):
        higher = Window(58)

        self.assertEqual(pick_fresher_window(Window(31), higher, NOW), higher)


class ClaudeUsageTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(tempfile.mkdtemp(prefix="agentdeck-claude-usage-"))
        self.local_status = scratch / "claude_status.json"
        self.local_status.write_text(json.dumps(LOCAL_STATUS), encoding="utf-8")
        usage._remote_claude_cache.clear()
        self.addCleanup(usage._remote_claude_cache.clear)

    def collect(self):
        with patch.multiple(
            usage,
            CLAUDE_STATUS=str(self.local_status),
            CLAUDE_GLOB=str(self.local_status.parent / "*.jsonl"),
        ):
            return usage.claude_usage(now=NOW, hosts=[None, "macstudio"])

    def test_merges_newer_remote_seven_day_and_five_hour_windows(self):
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps(NEWER_STATUS))

        with patch.object(usage.subprocess, "run", return_value=response) as run:
            result = self.collect()

        run.assert_called_once()
        self.assertEqual(result.seven_day.used_percent, 58)
        self.assertEqual(result.five_hour.used_percent, 22)

    def test_ssh_failure_keeps_local_window_and_retries_after_25_seconds(self):
        failure = subprocess.CalledProcessError(255, ["ssh", "macstudio"])
        recovery = subprocess.CompletedProcess([], 0, stdout=json.dumps(NEWER_STATUS))

        with (
            patch.object(usage.subprocess, "run", side_effect=[failure, recovery]) as run,
            patch.object(usage.time, "monotonic", return_value=100.0) as monotonic,
        ):
            result = self.collect()
            self.assertEqual(result.seven_day.used_percent, 31)
            self.assertEqual(result.five_hour, Window())
            run.assert_called_once()

            monotonic.return_value = 124.999
            self.assertEqual(self.collect(), result)
            run.assert_called_once()

            monotonic.return_value = 125.0
            recovered = self.collect()
            self.assertEqual(run.call_count, 2)
            self.assertEqual(recovered.seven_day.used_percent, 58)
            self.assertEqual(recovered.five_hour.used_percent, 22)


class StatuslineTeeTests(unittest.TestCase):
    def test_older_idle_snapshot_preserves_newer_windows(self):
        temporary_home = Path(tempfile.mkdtemp(prefix="agentdeck-statusline-"))
        status_path = temporary_home / ".cache/d200/claude_status.json"

        for snapshot in (NEWER_STATUS, LOCAL_STATUS):
            subprocess.run(
                ["bash", str(STATUSLINE)],
                input=json.dumps(snapshot),
                text=True,
                check=True,
                env={**os.environ, "HOME": str(temporary_home)},
            )
            saved = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["rate_limits"], NEWER_STATUS["rate_limits"])
