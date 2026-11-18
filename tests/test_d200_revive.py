"""바로 꽂았을 때 디버그 모양(18d1:d002)으로 멈춘 D200 깨우기 (libusb 는 가짜로)."""

import unittest
from unittest.mock import MagicMock, patch

import d200


def fake_lib(handle=0x1234, init=0):
    lib = MagicMock()
    lib.libusb_init.return_value = init
    lib.libusb_open_device_with_vid_pid.return_value = handle
    lib.libusb_reset_device.return_value = -5  # 다시 인식되면서 NOT_FOUND 가 정상
    return lib


class ReviveTest(unittest.TestCase):
    def test_resets_stuck_device(self):
        lib = fake_lib()
        self.assertTrue(d200.revive_stuck_device(lib))
        args = lib.libusb_open_device_with_vid_pid.call_args.args
        self.assertEqual(args[1:], (0x18D1, 0xD002))
        lib.libusb_reset_device.assert_called_once_with(0x1234)
        lib.libusb_close.assert_called_once_with(0x1234)
        lib.libusb_exit.assert_called_once()

    def test_no_stuck_device_no_reset(self):
        lib = fake_lib(handle=None)
        self.assertFalse(d200.revive_stuck_device(lib))
        lib.libusb_reset_device.assert_not_called()
        lib.libusb_exit.assert_called_once()

    def test_init_failure(self):
        lib = fake_lib(init=-99)
        self.assertFalse(d200.revive_stuck_device(lib))
        lib.libusb_open_device_with_vid_pid.assert_not_called()

    def test_without_libusb(self):
        with patch.object(d200, "_load_libusb", return_value=None):
            self.assertFalse(d200.revive_stuck_device())


class OpenTest(unittest.TestCase):
    INFO = {"interface_number": 0, "path": b"p"}

    def test_opens_after_revive(self):
        with patch.object(d200, "_find_hid", side_effect=[None, None, self.INFO]) as find, \
                patch.object(d200, "revive_stuck_device", return_value=True), \
                patch.object(d200.time, "sleep"), patch.object(d200.hid, "device") as dev:
            d200.D200()
        self.assertEqual(find.call_count, 3)
        dev.return_value.open_path.assert_called_once_with(b"p")

    def test_not_found_when_nothing_to_revive(self):
        with patch.object(d200, "_find_hid", return_value=None) as find, \
                patch.object(d200, "revive_stuck_device", return_value=False), \
                patch.object(d200.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                d200.D200()
        find.assert_called_once()
        sleep.assert_not_called()

    def test_no_revive_when_device_present(self):
        with patch.object(d200, "_find_hid", return_value=self.INFO), \
                patch.object(d200, "revive_stuck_device") as revive, patch.object(d200.hid, "device"):
            d200.D200()
        revive.assert_not_called()

    def test_gives_up_after_wait(self):
        with patch.object(d200, "_find_hid", return_value=None) as find, \
                patch.object(d200, "revive_stuck_device", return_value=True), \
                patch.object(d200.time, "sleep"):
            with self.assertRaises(RuntimeError):
                d200.D200()
        self.assertEqual(find.call_count, 1 + d200.REVIVE_WAIT_S * 2)


if __name__ == "__main__":
    unittest.main()
