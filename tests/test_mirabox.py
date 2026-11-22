import io
import threading
import unittest
from unittest import mock

from PIL import Image

from drivers import mirabox
from drivers.base import Button, InputEvent


RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
BLACK = (0, 0, 0)


def png(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def wide_png():
    image = Image.new("RGB", (458, 196), RED)
    image.paste(BLUE, (229, 0, 458, 196))
    return png(image)


def input_report(key, state):
    return b"ACK" + bytes(6) + bytes([key, state])


def hid_info(path, vendor_id=0x6603, product_id=0x1014):
    return {"path": path, "vendor_id": vendor_id, "product_id": product_id}


class MiraboxProfileTest(unittest.TestCase):
    def test_model_defaults(self):
        profiles = {
            "293": mirabox.MiraboxProfile("293", ((0x5500, 0x1001),), 512, 512, (100, 100), 180, True, False),
            "293v3": mirabox.MiraboxProfile(
                "293v3", ((0x6603, 0x1005), (0x6603, 0x1006), (0x6603, 0x1010)),
                1024, 512, (112, 112), 180, True, True,
            ),
            "293s": mirabox.MiraboxProfile("293s", ((0x5548, 0x6670),), 512, 512, (85, 85), 90, False, False),
            "293sv3": mirabox.MiraboxProfile("293sv3", ((0x6603, 0x1014),), 1024, 512, (85, 85), 90, False, True),
            "akp153": mirabox.MiraboxProfile(
                "akp153", ((0x5548, 0x6674), (0x0300, 0x1010), (0x0300, 0x1020),
                           (0x0300, 0x3010), (0x0300, 0x3011)),
                512, 512, (85, 85), 90, False, False,
            ),
        }
        for model, profile in profiles.items():
            with self.subTest(model=model):
                self.assertEqual(mirabox.load_mirabox_profile({"model": model.upper()}), profile)
                self.assertEqual(profile.keepalive_sec, 10.0)

    def test_usb_ids_accept_strings_and_integers(self):
        for vid, pid in (("0x5548", "0x6674"), (0x5548, 0x6674)):
            with self.subTest(vid=vid, pid=pid):
                profile = mirabox.load_mirabox_profile({"vid": vid, "pid": pid})
                self.assertEqual(profile.model, "akp153")
                self.assertEqual(profile.vid_pids, ((0x5548, 0x6674),))

        profile = mirabox.load_mirabox_profile({"model": "293s", "vid": "0x5548", "pid": 0x1234})
        self.assertEqual(profile.vid_pids, ((0x5548, 0x1234),))

    def test_profile_overrides_and_key_size_normalization(self):
        for value, expected in ((85, (85, 85)), ([80, 72], (80, 72)), (("84", "76"), (84, 76))):
            cfg = {"model": "293", "key_size": value, "rotation": 270, "invert_rows": False,
                   "packet_size": 1024, "dual_state": True}
            with self.subTest(key_size=value):
                profile = mirabox.load_mirabox_profile(cfg)
                self.assertEqual(profile.key_size, expected)
                self.assertEqual(profile.rotation, 270)
                self.assertFalse(profile.invert_rows)
                self.assertEqual(profile.packet_size, 1024)
                self.assertTrue(profile.dual_state)
        self.assertEqual(mirabox.load_mirabox_profile({"model": "293", "key_size": None}).key_size, (100, 100))


class MiraboxProtocolTest(unittest.TestCase):
    def test_make_cmd_prefix_payload_and_padding_at_both_report_sizes(self):
        for report_size in (513, 1025):
            for payload in (b"DIS", b"LIG\x00\x00\x00\x00", b"LIG\x00\x00\x49",
                            b"STP", b"CONNECT", b"CLE\x00\x00\x00\xff"):
                with self.subTest(report_size=report_size, payload=payload):
                    header = b"\x00CRT\x00\x00" + payload
                    report = mirabox.make_cmd(payload, report_size - 1)
                    self.assertEqual(report, header + bytes(report_size - len(header)))

    def test_image_packet_header_size_key_and_chunk_padding(self):
        parts = [b"A" * 512, b"B" * 512, b"C" * 512, b"D" * 512]
        jpeg = b"".join(parts) + b"last"
        for size, chunks in ((512, parts), (1024, [parts[0] + parts[1], parts[2] + parts[3]])):
            with self.subTest(packet_size=size):
                header = b"\x00CRT\x00\x00BAT\x00\x00\x08\x04\x0f"
                expected = [header + bytes(size + 1 - len(header))]
                expected.extend(b"\x00" + chunk for chunk in chunks)
                expected.append(b"\x00last" + bytes(size - 4))
                self.assertEqual(list(mirabox.iter_image_packets(jpeg, 15, size)), expected)

    def test_write_report_accepts_both_report_lengths_and_none(self):
        for size in (512, 1024):
            report = bytes(size + 1)
            for written in (size + 1, size, None):
                with self.subTest(size=size, written=written):
                    dev = mock.Mock()
                    dev.write.return_value = written
                    mirabox._write_report(dev, report)
                    dev.write.assert_called_once_with(report)

    def test_write_report_raises_for_failed_or_short_writes(self):
        for size in (512, 1024):
            for written in (-1, size - 1):
                with self.subTest(size=size, written=written):
                    dev = mock.Mock()
                    dev.write.return_value = written
                    with self.assertRaisesRegex(OSError, "^Incomplete Mirabox HID write$"):
                        mirabox._write_report(dev, bytes(size + 1))

    def test_encode_key_image_jpeg_dimensions_and_rotation(self):
        image = Image.new("RGB", (140, 120), RED)
        image.paste(GREEN, (70, 0, 140, 60))
        image.paste(BLUE, (0, 60, 70, 120))
        image.paste(YELLOW, (70, 60, 140, 120))
        cases = ((0, (RED, GREEN, BLUE, YELLOW)), (90, (GREEN, YELLOW, RED, BLUE)),
                 (180, (YELLOW, BLUE, GREEN, RED)))
        for rotation, colors in cases:
            with self.subTest(rotation=rotation):
                profile = mirabox.load_mirabox_profile({"model": "293s", "rotation": rotation})
                jpeg = mirabox.encode_key_image(image, profile)
                self.assertEqual(jpeg[:2], b"\xff\xd8")
                with Image.open(io.BytesIO(jpeg)) as decoded:
                    self.assertEqual(decoded.format, "JPEG")
                    self.assertEqual(decoded.size, (85, 85))
                    for point, color in zip(((21, 21), (63, 21), (21, 63), (63, 63)), colors, strict=True):
                        self.assertLessEqual(max(abs(a - b) for a, b in zip(decoded.getpixel(point), color)), 4)


class MiraboxInputTest(unittest.TestCase):
    def test_key_mapping_for_all_models(self):
        reversed_rows = [10, 11, 12, 13, 14, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4]
        straight_rows = list(range(15))
        for model, expected in (("293", reversed_rows), ("293v3", reversed_rows),
                                ("293s", straight_rows), ("293sv3", straight_rows), ("akp153", straight_rows)):
            profile = mirabox.load_mirabox_profile({"model": model})
            for key, index in enumerate(expected, 1):
                with self.subTest(model=model, key=key):
                    events = mirabox.parse_mirabox_input(input_report(key, 1), profile)
                    self.assertEqual(events[0], InputEvent(index, "button", "press", 1))

    def test_v1_pulse_produces_press_and_release(self):
        profile = mirabox.load_mirabox_profile({"model": "293"})
        self.assertEqual(mirabox.parse_mirabox_input(input_report(1, 0xFF), profile), [
            InputEvent(10, "button", "press", 1), InputEvent(10, "button", "release", 0),
        ])

    def test_v3_reports_press_and_release_as_separate_events(self):
        for model, index in (("293v3", 10), ("293sv3", 0)):
            with self.subTest(model=model):
                profile = mirabox.load_mirabox_profile({"model": model})
                for state, action in ((1, "press"), (0, "release")):
                    self.assertEqual(mirabox.parse_mirabox_input(input_report(1, state), profile),
                                     [InputEvent(index, "button", action, state)])
                self.assertEqual(mirabox.parse_mirabox_input(input_report(1, 2), profile), [])

    def test_ignores_non_ack_short_and_out_of_range_reports(self):
        profile = mirabox.load_mirabox_profile({"model": "293sv3"})
        for report in (b"DIS" + input_report(1, 1)[3:], input_report(1, 1)[:10],
                       input_report(0, 1), input_report(16, 1), input_report(255, 1)):
            with self.subTest(report=report):
                self.assertEqual(mirabox.parse_mirabox_input(report, profile), [])


class FakeHID:
    def __init__(self):
        self.path = None
        self.reports = []
        self.fail_reads = threading.Event()
        self.keepalive_written = threading.Event()
        self.closed_after = None

    def open_path(self, path):
        self.path = path

    def write(self, report):
        if self.path is None or self.closed_after is not None:
            raise AssertionError("HID write requires an open device")
        self.reports.append(report)
        if report[6:13] == b"CONNECT":
            self.keepalive_written.set()
        return len(report) - 1

    def read(self, size, timeout_ms):
        if self.fail_reads.is_set():
            raise OSError("fake device unplugged")
        return []

    def close(self):
        self.closed_after = len(self.reports)


class MiraboxDeckTest(unittest.TestCase):
    def setUp(self):
        self.enumerate = self.enterContext(mock.patch.object(mirabox.hid, "enumerate"))
        self.hid_device = self.enterContext(mock.patch.object(mirabox.hid, "device"))

    def make_deck(self, dev, *, info=None, cfg=None, reconnect=False):
        self.enumerate.return_value = [info if info is not None else hid_info(b"first")]
        self.hid_device.side_effect = [dev]
        deck = mirabox.MiraboxDeck({"keepalive_sec": 0, **(cfg or {})}, reconnect=reconnect)
        self.addCleanup(self.close_deck, deck)
        return deck

    def close_deck(self, deck):
        deck.close()
        for worker in (deck._reader, deck._keepalive):
            if worker is not None:
                worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_set_buttons_sends_fifteen_keys_with_one_flush_and_caches_identical_frame(self):
        dev = FakeHID()
        deck = self.make_deck(dev)
        self.assertEqual(dev.path, b"first")
        buttons = {
            0: Button(image=png(Image.new("RGB", (196, 196), GREEN))),
            12: Button(image=png(Image.new("RGB", (196, 196), YELLOW))),
        }
        wide = wide_png()
        deck.set_buttons(buttons, small_png=wide)

        reports = dev.reports[2:]
        colors = {0: GREEN, 12: YELLOW, 13: RED, 14: BLUE}
        offset = 0
        for slot in range(15):
            with self.subTest(slot=slot):
                header = reports[offset]
                self.assertEqual(header[:11], b"\x00CRT\x00\x00BAT\x00\x00")
                self.assertEqual(header[13], slot + 1)
                jpeg_size = int.from_bytes(header[11:13], "big")
                chunk_count = (jpeg_size + 1023) // 1024
                chunks = reports[offset + 1:offset + 1 + chunk_count]
                jpeg = b"".join(chunk[1:] for chunk in chunks)[:jpeg_size]
                with Image.open(io.BytesIO(jpeg)) as decoded:
                    self.assertEqual(decoded.size, (85, 85))
                    color = colors.get(slot, BLACK)
                    self.assertLessEqual(max(abs(a - b) for a, b in zip(decoded.getpixel((42, 42)), color)), 4)
                offset += 1 + chunk_count
        self.assertEqual(reports[offset:], [b"\x00CRT\x00\x00STP" + bytes(1016)])

        deck.set_buttons(buttons, small_png=wide)
        self.assertEqual(dev.reports[2:], reports)

    def test_reconnect_restores_brightness_and_screen_before_on_connect(self):
        self.enterContext(mock.patch.object(mirabox, "RECONNECT_POLL_SEC", 0.01))
        first = FakeHID()
        second = FakeHID()
        deck = self.make_deck(first, reconnect=True)
        self.hid_device.side_effect = [second]
        deck.set_brightness(73)
        deck.set_buttons({}, small_png=wide_png())
        original_reports = first.reports.copy()

        callbacks = []
        dropped = threading.Event()
        restored = threading.Event()

        def on_disconnect():
            callbacks.append(("disconnect", deck.connected.is_set(), first.closed_after))
            dropped.set()

        def on_connect():
            callbacks.append(("connect", deck.connected.is_set(), second.reports.copy()))
            restored.set()

        deck.on_disconnect = on_disconnect
        deck.on_connect = on_connect
        self.enumerate.return_value = []
        first.fail_reads.set()
        deck.start()
        self.assertTrue(dropped.wait(2))
        self.assertFalse(deck.disconnected.is_set())
        self.enumerate.return_value = [hid_info(b"second")]
        self.assertTrue(restored.wait(2))
        self.assertEqual(second.path, b"second")
        self.assertEqual(second.reports[:3], [
            b"\x00CRT\x00\x00DIS" + bytes(1016),
            b"\x00CRT\x00\x00LIG\x00\x00\x00\x00" + bytes(1012),
            b"\x00CRT\x00\x00LIG\x00\x00\x49" + bytes(1013),
        ])
        self.assertEqual(callbacks, [
            ("disconnect", False, len(original_reports)),
            ("connect", True, original_reports),
        ])

    def test_keepalive_sends_connect_command(self):
        dev = FakeHID()
        deck = self.make_deck(dev, cfg={"keepalive_sec": 0.01})
        deck.start()
        self.assertTrue(dev.keepalive_written.wait(2))
        self.assertEqual(dev.reports[2], b"\x00CRT\x00\x00CONNECT" + bytes(1012))

    def test_close_clears_screen_and_flushes_1024_byte_devices_before_closing(self):
        for info, size in ((hid_info(b"v1", 0x5548, 0x6670), 512), (hid_info(b"v3"), 1024)):
            with self.subTest(packet_size=size):
                dev = FakeHID()
                deck = self.make_deck(dev, info=info)
                deck.close()
                expected = [b"\x00CRT\x00\x00CLE\x00\x00\x00\xff" + bytes(size - 12)]
                if size == 1024:
                    expected.append(b"\x00CRT\x00\x00STP" + bytes(1016))
                self.assertEqual(dev.reports[2:], expected)
                self.assertEqual(dev.closed_after, len(dev.reports))
                self.assertFalse(deck.connected.is_set())
