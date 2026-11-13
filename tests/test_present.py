"""발표 모드: 타이머 색, 마이크 고르기, 버튼 동작 (키 보내기·녹음은 가짜로)."""

import unittest
from unittest.mock import patch

import present
from present import Presenter, clock, parse_audio_devices, pick_device

LISTING = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] ‘누군가의 iPhone 16 pro’ 마이크
[AVFoundation indev @ 0x1] [1] MacBook Pro 마이크
[AVFoundation indev @ 0x1] [2] Ulanzi AU05
"""


class TimerTest(unittest.TestCase):
    def test_colors_by_remaining(self):
        p = Presenter(target_min=30)
        self.assertEqual(p.color(), present.DIM)
        with patch.object(present.time, "time", return_value=1000.0):
            p.toggle_timer()
        self.assertEqual(p.color(1000 + 60), present.GREEN)
        self.assertEqual(p.color(1000 + 26 * 60), present.YELLOW)  # 4분 남음
        self.assertEqual(p.color(1000 + 31 * 60), present.RED)  # 초과

    def test_pause_resume_keeps_elapsed(self):
        p = Presenter()
        with patch.object(present.time, "time", return_value=100.0):
            p.toggle_timer()
        with patch.object(present.time, "time", return_value=160.0):
            p.toggle_timer()  # 60초 지나 멈춤
        self.assertEqual((p.state, p.elapsed(500.0)), ("paused", 60.0))
        with patch.object(present.time, "time", return_value=1000.0):
            p.toggle_timer()
        self.assertAlmostEqual(p.elapsed(1010.0), 70.0)
        p.reset_timer()
        self.assertEqual((p.state, p.elapsed()), ("idle", 0.0))

    def test_target_cycles(self):
        p = Presenter(target_min=90)
        p.next_target()
        self.assertEqual(p.target_min, present.TARGETS[0])

    def test_clock_and_notes(self):
        self.assertEqual((clock(65), clock(3725)), ("01:05", "1:02:05"))
        p = Presenter(marks=[192.0], questions=[645.0, 700.0])
        self.assertEqual(p.notes(), "★ 03:12\nQ 10:45, 11:40")
        self.assertIsNone(Presenter().notes())


class MicTest(unittest.TestCase):
    def test_parse_and_pick(self):
        devs = parse_audio_devices(LISTING)
        self.assertEqual([i for i, _ in devs], [0, 1, 2])
        self.assertEqual(pick_device(devs, "AU05"), 2)  # 설정한 마이크
        self.assertEqual(pick_device(devs, None), 1)  # 기본: 맥 내장 (아이폰 연속성 마이크 아님)
        self.assertEqual(pick_device(devs, "없는 이름"), 1)
        self.assertIsNone(pick_device([], None))


class PressTest(unittest.TestCase):
    def test_slides_count_only_when_key_sent(self):
        p = Presenter()
        with patch.object(present, "send_key", return_value=True) as k:
            present.press(p, 1, 0, 0.6)
            present.press(p, 1, 0, 0.6)
            present.press(p, 0, 0, 0.6)
        self.assertEqual(p.slide, 2)
        self.assertEqual([c.args[0] for c in k.call_args_list], ["next", "next", "prev"])
        with patch.object(present, "send_key", return_value=False):
            present.press(p, 1, 0, 0.6)
        self.assertEqual(p.slide, 2)
        self.assertIn("권한", p.msg[1])

    def test_marks_need_recording(self):
        p = Presenter()
        present.press(p, 4, 0, 0.6)
        self.assertEqual(p.marks, [])
        with patch.object(present.Recorder, "on", new=True), patch.object(present.Recorder, "elapsed", return_value=42.0):
            present.press(p, 4, 0, 0.6)
            present.press(p, 7, 0, 0.6)
        self.assertEqual((p.marks, p.questions), ([42.0], [42.0]))

    def test_timer_and_long_press_reset(self):
        p = Presenter()
        present.press(p, 5, 0, 0.6)
        self.assertEqual(p.state, "running")
        present.press(p, 5, 1.0, 0.6)
        self.assertEqual(p.state, "idle")

    def test_demo_opens_only_configured(self):
        p = Presenter()
        with patch.object(present, "open_target", return_value=True) as o:
            present.press(p, 8, 0, 0.6, demos=[{"name": "데모", "open": "https://example.com"}])
            present.press(p, 9, 0, 0.6, demos=[{"name": "데모", "open": "https://example.com"}])
        o.assert_called_once_with("https://example.com")

    def test_page_and_wide_render(self):
        p = Presenter()
        b = present.page(p, demos=[{"name": "데모", "open": "x"}])
        self.assertEqual(set(b), set(range(10)))
        self.assertEqual((b[3].title, b[8].title, b[9].title), ("● 녹음", "데모", "-"))
        self.assertTrue(present.wide(p, "Keynote").startswith(b"\x89PNG"))
        p.say("녹음 끝", "올리는 중")
        self.assertTrue(present.wide(p).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
