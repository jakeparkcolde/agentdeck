import io
import sys
import threading
import unittest
from unittest import mock

from PIL import Image

from drivers import open_deck
from drivers import elgato
from drivers.base import Button, InputEvent


RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
BLACK = (0, 0, 0)


def png(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def button(color):
    return Button(image=png(Image.new("RGB", (196, 196), color)))


def wide_png():
    image = Image.new("RGB", (458, 196), RED)
    image.paste(BLUE, (229, 0, 458, 196))
    return png(image)


class FakeStreamDeck:
    def __init__(self, rows=3, cols=5, size=72, *, serial="fake-first", visual=True):
        self.rows = rows
        self.cols = cols
        self.size = size
        self.serial = serial
        self.visual = visual
        self.opened = False
        self.plugged = True
        self.callback = None
        self.reset_count = 0
        self.images = {}
        self.writes = []
        self.brightness = []
        self.write_error = None

    def is_visual(self):
        return self.visual

    def key_layout(self):
        return self.rows, self.cols

    def get_serial_number(self):
        if not self.opened:
            raise OSError("serial requires an open device")
        return self.serial

    def open(self):
        self.opened = True

    def reset(self):
        self.reset_count += 1
        self.images.clear()

    def close(self):
        self.opened = False

    def is_open(self):
        return self.opened

    def is_connected(self):
        return self.plugged

    def set_key_callback(self, callback):
        self.callback = callback

    def set_brightness(self, percent):
        if self.write_error is not None:
            raise self.write_error
        self.brightness.append(percent)

    def key_image_format(self):
        return {"size": (self.size, self.size)}

    def set_key_image(self, key, payload):
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(key)
        self.images[key] = payload


class UpstreamStreamDeck(FakeStreamDeck):
    """The installed package API names the physical status check connected()."""

    connected = FakeStreamDeck.is_connected
    is_connected = None


class FakeManager:
    def __init__(self, devices):
        self.devices = devices

    def enumerate(self):
        return list(self.devices)


class FakePILHelper:
    @staticmethod
    def create_scaled_key_image(dev, image):
        return image.resize(dev.key_image_format()["size"])

    @staticmethod
    def to_native_key_format(dev, image):
        return image.tobytes()


class TransportError(Exception):
    pass


class ElgatoDeckTest(unittest.TestCase):
    def make_deck(self, devices, *, cfg=None, reconnect=False):
        deck = elgato.ElgatoDeck(cfg, reconnect=reconnect,
                                manager_factory=lambda: FakeManager(devices), pil_helper=FakePILHelper)
        self.addCleanup(self.close_deck, deck)
        return deck

    def close_deck(self, deck):
        deck.close()
        if deck._monitor is not None:
            deck._monitor.join(2)
            self.assertFalse(deck._monitor.is_alive())

    def test_serial_priority_and_first_compatible_fallback(self):
        nonvisual = FakeStreamDeck(visual=False)
        mini = FakeStreamDeck(2, 3)
        first = FakeStreamDeck()
        selected = FakeStreamDeck(4, 8, 96, serial="fake-selected")
        devices = [nonvisual, mini, first, selected]
        for serial, expected in (("fake-selected", selected), ("fake-missing", first), (None, first)):
            with self.subTest(serial=serial):
                deck = self.make_deck(devices, cfg={"serial": serial})
                self.assertTrue(deck.connected.is_set())
                self.assertEqual([dev for dev in devices if dev.opened], [expected])
                self.assertIsNotNone(expected.callback)
                deck.close()
                self.assertFalse(expected.opened)
                self.assertFalse(deck.connected.is_set())
        self.assertEqual(nonvisual.reset_count, 0)
        self.assertEqual(mini.reset_count, 0)

    def test_writes_all_logical_slots_on_both_layouts_and_skips_duplicates(self):
        for cols, rows, size in ((5, 3, 72), (8, 4, 96)):
            with self.subTest(cols=cols):
                dev = FakeStreamDeck(rows, cols, size)
                deck = self.make_deck([dev])
                keys = [row * cols + col for row in range(3) for col in range(5)]
                buttons = {0: button(GREEN), 12: button(BLUE), 13: button(GREEN), 14: button(RED)}
                wide = wide_png()
                deck.set_buttons(buttons, small_png=wide)
                self.assertEqual(dev.writes, keys)
                for slot, key in enumerate(keys):
                    color = {0: GREEN, 12: BLUE, 13: RED, 14: BLUE}.get(slot, BLACK)
                    self.assertEqual(dev.images[key], bytes(color) * (size * size))

                deck.set_buttons(buttons, small_png=wide)
                self.assertEqual(dev.writes, keys)
                deck.set_buttons({12: button(RED)}, partial=True)
                self.assertEqual(dev.writes, keys + [keys[12]])
                self.assertEqual(dev.images[keys[12]], bytes(RED) * (size * size))
                deck.set_buttons({})
                self.assertEqual(dev.writes, keys + [keys[12], keys[0], keys[12]])
                self.assertEqual(dev.images[keys[12]], bytes(BLACK) * (size * size))

    def test_key_callbacks_map_press_and_release_and_ignore_extra_keys(self):
        for cols, rows in ((5, 3), (8, 4)):
            with self.subTest(cols=cols):
                dev = FakeStreamDeck(rows, cols)
                deck = self.make_deck([dev])
                seen = []
                deck.on_event = seen.append
                for key in (2 * cols + 3, 2 * cols + 4, -1, cols * rows):
                    dev.callback(dev, key, True)
                    dev.callback(dev, key, False)
                if cols == 8:
                    for key in (5, 3 * cols):
                        dev.callback(dev, key, True)
                self.assertEqual(seen, [
                    InputEvent(13, "button", "press", 1), InputEvent(13, "button", "release", 0),
                    InputEvent(14, "button", "press", 1), InputEvent(14, "button", "release", 0),
                ])

    def test_reconnect_restores_brightness_and_combined_updates_before_callback(self):
        self.enterContext(mock.patch.object(elgato, "RECONNECT_POLL_SEC", 0.01))
        first = UpstreamStreamDeck()
        second = UpstreamStreamDeck(4, 8, 96)
        devices = [first]
        deck = self.make_deck(devices, reconnect=True)
        callbacks = []
        dropped = threading.Event()
        restored = threading.Event()

        def on_disconnect():
            callbacks.append("disconnect")
            dropped.set()

        def on_connect():
            callbacks.append(("connect", threading.current_thread(), dict(second.images)))
            restored.set()

        deck.on_connect = on_connect
        deck.on_disconnect = on_disconnect
        deck.set_brightness(130)
        self.assertEqual(first.brightness, [100])
        deck.set_buttons({0: button(RED), 1: button(GREEN)}, small_png=wide_png())
        deck.set_buttons({0: button(BLUE)}, partial=True)
        deck.start()
        devices.clear()
        first.plugged = False
        self.assertTrue(dropped.wait(2))
        self.assertFalse(deck.connected.is_set())
        self.assertFalse(deck.disconnected.is_set())
        self.assertFalse(first.opened)
        deck.set_brightness(-10)
        deck.set_buttons({1: Button(image=b"skipped while disconnected")})
        devices.append(second)
        self.assertTrue(restored.wait(2))
        self.assertTrue(deck.connected.is_set())
        self.assertEqual(second.brightness, [0])
        self.assertEqual(callbacks[0], "disconnect")
        action, callback_thread, seen = callbacks[1]
        self.assertEqual(action, "connect")
        self.assertTrue(callback_thread.daemon)
        self.assertIsNot(callback_thread, deck._monitor)
        for key, color in ((0, BLUE), (1, GREEN), (2, BLACK), (19, RED), (20, BLUE)):
            self.assertEqual(seen[key], bytes(color) * (96 * 96))
        self.assertEqual(len(seen), 15)

    def test_write_error_reconnects_and_retries_interrupted_screen(self):
        self.enterContext(mock.patch.object(elgato, "RECONNECT_POLL_SEC", 0.01))
        first, second = FakeStreamDeck(), FakeStreamDeck()
        devices = [first]
        deck = self.make_deck(devices, reconnect=True)
        deck.on_disconnect = mock.Mock()
        restored = threading.Event()
        deck.on_connect = restored.set
        deck.set_buttons({0: button(RED)})
        first.write_error = TransportError("unplugged during write")
        deck.set_buttons({0: button(BLUE)})
        self.assertFalse(deck.connected.is_set())
        self.assertFalse(first.opened)
        deck.on_disconnect.assert_called_once_with()
        devices[:] = [second]
        deck.start()
        self.assertTrue(restored.wait(2))
        self.assertEqual(second.images[0], bytes(BLUE) * (72 * 72))

    def test_non_reconnecting_deck_stops_after_write_or_poll_failure(self):
        self.enterContext(mock.patch.object(elgato, "RECONNECT_POLL_SEC", 0.01))
        for failure in ("write", "poll"):
            with self.subTest(failure=failure):
                dev = FakeStreamDeck()
                deck = self.make_deck([dev])
                deck.start()
                if failure == "write":
                    dev.write_error = OSError("unplugged during write")
                    deck.set_brightness(20)
                else:
                    dev.opened = False
                self.assertTrue(deck.disconnected.wait(2))
                self.assertFalse(deck.connected.is_set())
                deck._monitor.join(2)
                self.assertFalse(deck._monitor.is_alive())

    def test_probe_checks_layout_without_opening_a_device(self):
        mini = FakeStreamDeck(2, 3)
        xl = FakeStreamDeck(4, 8, 96)
        devices = [mini, FakeStreamDeck(visual=False)]
        with mock.patch.object(elgato, "_device_manager", return_value=lambda: FakeManager(devices)):
            self.assertFalse(elgato.probe_elgato())
            devices.append(xl)
            self.assertTrue(elgato.probe_elgato())
        self.assertFalse(any(dev.opened for dev in devices))

    def test_missing_streamdeck_raises_only_for_explicit_request(self):
        with mock.patch.dict(sys.modules, {"StreamDeck": None}), mock.patch("i18n.LANG", "en"):
            self.assertFalse(elgato.probe_elgato())
            with self.assertRaisesRegex(RuntimeError, "pip install streamdeck"):
                open_deck({"driver": "elgato"}, reconnect=True)
