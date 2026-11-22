"""장치가 끊겼을 때 인트로와 화면 그리기를 건너뛰는지 확인한다."""

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import dashboard


class DashboardReconnectTest(unittest.TestCase):
    def setUp(self):
        self.dash = dashboard.Dashboard.__new__(dashboard.Dashboard)
        self.dash.deck = SimpleNamespace(connected=threading.Event(), set_buttons=mock.Mock())

    def test_play_intro_skips_sound_and_mark_seen_while_disconnected(self):
        self.dash.page = 1
        with (mock.patch.object(dashboard, "play") as play,
              mock.patch.object(dashboard, "intro") as intro):
            self.dash.play_intro()

        self.assertEqual(self.dash.page, dashboard.HOME_PAGE)
        play.assert_not_called()
        intro.intro_frame.assert_not_called()
        intro.mark_seen.assert_not_called()

    def test_render_and_render_home_skip_writes_while_disconnected(self):
        for render in (self.dash.render, self.dash.render_home):
            with self.subTest(method=render.__name__):
                self.assertIsNone(render())
                self.dash.deck.set_buttons.assert_not_called()
