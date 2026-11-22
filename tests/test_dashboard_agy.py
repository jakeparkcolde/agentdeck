"""Device-free integration checks; install requirements.txt to enable these."""
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import remote_agy

HAS_DEPS = all(importlib.util.find_spec(name) is not None
               for name in ("hid", "PIL", "certifi", "websockets"))


@unittest.skipUnless(HAS_DEPS, "dashboard checks require requirements.txt")
class DashboardQuotaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import dashboard
        import pages
        from usage import ClaudeUsage, CodexUsage
        cls.dashboard = dashboard
        cls.pages = pages
        cls.claude_type = ClaudeUsage
        cls.codex_type = CodexUsage

    def collect(self, buckets):
        # 설정과 상관없이 Claude·Codex·AGY 세 열이 다 나오게 (AGY 호스트 지정, ~/.codex 있음)
        with patch.object(self.dashboard, "AGY_HOST", "test-host"), \
             patch.object(self.dashboard.Path, "exists", return_value=True), \
             patch.object(self.dashboard, "agy_quota", return_value=buckets), \
             patch.object(self.dashboard, "claude_usage", return_value=self.claude_type()), \
             patch.object(self.dashboard, "codex_live", return_value=self.codex_type()), \
             ThreadPoolExecutor(max_workers=2) as pool:
            return self.dashboard.collect_llms(pool)

    def test_fixture_reaches_dashboard_showing_tightest_group(self):
        payload = json.loads((Path(__file__).parent / "fixtures/agy_quota.json").read_text())
        llms = self.collect(remote_agy.parse_quota(payload))
        agy = llms[2]
        self.assertEqual(agy.name, "AGY")
        # 모델 그룹이 여럿이면 창마다 가장 빠듯한 그룹(여기서는 A 의 5h = 0)을 보여준다
        self.assertEqual(agy.five.remaining, 0.0)
        self.assertEqual(agy.week.remaining, .75)
        self.assertEqual(agy.week.reset.isoformat(), "2030-01-07T12:00:00+00:00")
        grid = self.pages.page_grid(llms, datetime(2030, 1, 1, tzinfo=timezone.utc))
        self.assertEqual((grid[2].title, grid[7].title), ("0%", "75%"))

    def test_exhausted_quota_remains_zero_in_summary_and_grid(self):
        payload = {"status": "SUCCESS", "command": {"data": {"groups": [
            {"name": "Synthetic", "buckets": [
                {"window": "5h", "remaining_fraction": 0},
                {"window": "weekly", "remaining_fraction": .75}]}]}}}
        llms = self.collect(remote_agy.parse_quota(payload))
        grid = self.pages.page_grid(llms, datetime.now(timezone.utc))
        self.assertEqual(grid[2].title, "0%")
        self.assertEqual(grid[2].ratio, 0)
        self.assertEqual(self.pages.llm_tile(llms[2]).title, "0%")

    def test_unavailable_quota_displays_placeholder_instead_of_zero(self):
        llms = self.collect(None)
        self.assertIsNone(llms[2].five)
        self.assertIsNone(llms[2].week)
        grid = self.pages.page_grid(llms, datetime.now(timezone.utc))
        for index in (2, 7):
            self.assertEqual(grid[index].title, "-")
            self.assertIsNone(grid[index].ratio)
        self.assertEqual(self.pages.llm_tile(llms[2]).title, "-")

    def test_login_needed_shows_login_tiles(self):
        with patch.object(self.dashboard.remote_agy, "login_needed", True):
            llms = self.collect(None)
        self.assertTrue(llms[2].login)
        now = datetime(2029, 12, 31, tzinfo=timezone.utc)
        self.assertEqual(self.pages.llm_tile(llms[2]).title, "로그인")
        grid = self.pages.page_grid(llms, now)
        self.assertEqual((grid[2].title, grid[7].sub), ("로그인", "길게: 로그인"))
        with patch.object(self.dashboard.remote_agy, "login_needed", False):
            self.assertFalse(self.collect(None)[2].login)

    def test_two_weekly_groups_show_separate_bars_and_recommend_primary(self):
        from io import BytesIO

        from PIL import Image

        from d200 import ICON, render_icon
        from remote_herdr import StudioState

        payload = {"status": "SUCCESS", "command": {"data": {"groups": [
            {"name": "Gemini Models", "buckets": [
                {"window": "5h", "remaining_fraction": .86},
                {"window": "weekly", "remaining_fraction": .83}]},
            {"name": "Claude and GPT models", "buckets": [
                {"window": "5h", "remaining_fraction": 0, "disabled": True},
                {"window": "weekly", "remaining_fraction": 0}]},
        ]}}}
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        llms = self.collect(remote_agy.parse_quota(payload))
        agy = llms[2]
        self.assertEqual((agy.week.remaining, agy.five.remaining), (.83, .86))
        self.assertEqual(agy.bars, [
            ("5H", .86, self.pages.GREEN, "86%"),
            ("GEM", .83, self.pages.GREEN, "83%"),
            ("3P", 0, self.pages.RED, "0%"),
        ])
        tile = self.pages.llm_tile(agy, now)
        self.assertEqual((tile.title, tile.bars), ("", agy.bars))
        with Image.open(BytesIO(render_icon(tile))) as icon:
            self.assertEqual((icon.format, icon.size), ("PNG", (ICON, ICON)))
            icon.verify()

        week = self.pages.page_grid(llms, now)[7]
        self.assertEqual((week.header, week.title), ("AGY 주간", "83%"))
        self.assertIn("3P 0%", week.sub)
        recommendation = self.pages.page_summary(llms, StudioState(False, []), now)[3]
        self.assertEqual((recommendation.header, recommendation.title, recommendation.sub),
                         ("지금 쓰기 좋은", "AGY", "여유 83%"))

    def test_gemini_wins_tie_and_uses_tightest_fallbacks(self):
        buckets = [
            remote_agy.Bucket("Other Models", "weekly", .83, None),
            remote_agy.Bucket("Other Models", "5h", .4, None),
            remote_agy.Bucket("Gemini Models", "weekly", .83, None),
            remote_agy.Bucket("GPT Models", "weekly", .2, None),
            remote_agy.Bucket("GPT Models", "5h", .3, None),
        ]
        agy = self.collect(buckets)[2]
        self.assertEqual((agy.week.remaining, agy.five.remaining), (.83, .3))
        self.assertEqual([bar[0] for bar in agy.bars], ["5H", "GEM", "3P"])
        self.assertEqual(agy.week_note, "3P 20%")
