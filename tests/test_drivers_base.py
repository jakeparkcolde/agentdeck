import io
import unittest
from unittest import mock

from PIL import Image

import d200
import drivers
from drivers.base import Button, ButtonCache, InputEvent, hw_to_logical, logical_to_hw, render_icon, split_wide_png


class BaseDriverTest(unittest.TestCase):
    def test_reuses_d200_button_event_and_renderer(self):
        self.assertIs(Button, d200.Button)
        self.assertIs(InputEvent, d200.InputEvent)
        self.assertIs(render_icon, d200.render_icon)

    def test_split_wide_png_into_rgb_key_images(self):
        image = Image.new("RGBA", (458, 196), "red")
        image.paste("blue", (229, 0, 458, 196))
        png = io.BytesIO()
        image.save(png, "PNG")

        for size in (72, 96):
            with self.subTest(size=size):
                left, right = split_wide_png(png.getvalue(), (size, size))
                for half, color in ((left, (255, 0, 0)), (right, (0, 0, 255))):
                    self.assertEqual(half.mode, "RGB")
                    self.assertEqual(half.size, (size, size))
                    self.assertEqual(half.getextrema(), tuple((channel, channel) for channel in color))

    def test_button_cache_is_per_slot_and_can_be_cleared(self):
        cache = ButtonCache()
        self.assertTrue(cache.changed(0, b"red"))
        self.assertFalse(cache.changed(0, b"red"))
        self.assertTrue(cache.changed(14, b"red"))
        self.assertTrue(cache.changed(0, b"blue"))
        self.assertFalse(cache.changed(14, b"red"))
        cache.clear()
        self.assertTrue(cache.changed(0, b"blue"))
        self.assertTrue(cache.changed(14, b"red"))

    def test_grid_mapping_round_trips_for_mk2_and_xl(self):
        for cols, rows in ((5, 3), (8, 4)):
            with self.subTest(cols=cols, rows=rows):
                mapped = [row * cols + col for row in range(3) for col in range(5)]
                self.assertEqual([logical_to_hw(i, cols, rows) for i in range(15)], mapped)
                for hw_idx in range(cols * rows):
                    logical = mapped.index(hw_idx) if hw_idx in mapped else None
                    self.assertEqual(hw_to_logical(hw_idx, cols, rows), logical)
                for invalid in (-1, 15):
                    self.assertIsNone(logical_to_hw(invalid, cols, rows))
                for invalid in (-1, cols * rows):
                    self.assertIsNone(hw_to_logical(invalid, cols, rows))

    def test_grid_rejects_devices_smaller_than_five_by_three(self):
        for cols, rows in ((4, 3), (5, 2), (0, 0)):
            with self.subTest(cols=cols, rows=rows):
                self.assertIsNone(logical_to_hw(0, cols, rows))
                self.assertIsNone(hw_to_logical(0, cols, rows))


class OpenDeckTest(unittest.TestCase):
    def setUp(self):
        self.d200 = self.enterContext(mock.patch.object(drivers, "D200"))
        self.elgato = self.enterContext(mock.patch.object(drivers, "ElgatoDeck"))
        self.mirabox = self.enterContext(mock.patch.object(drivers, "MiraboxDeck"))
        self.find_hid = self.enterContext(mock.patch.object(d200, "_find_hid", return_value=None))
        self.probe_elgato = self.enterContext(mock.patch.object(drivers, "probe_elgato", return_value=False))
        self.probe_mirabox = self.enterContext(mock.patch.object(drivers, "probe_mirabox", return_value=False))

    def test_d200_explicit_driver_and_d200h_model(self):
        configs = (
            ({"driver": "D200"}, "d200"),
            ({"driver": "auto", "model": "D200H", "keepalive": "off"}, "d200h"),
        )
        for cfg, model in configs:
            with self.subTest(cfg=cfg):
                self.assertIs(drivers.open_deck(cfg, reconnect=True), self.d200.return_value)
                profile = self.d200.call_args.args[0]
                self.assertEqual(profile, d200.load_profile(cfg))
                self.assertEqual(profile.model, model)
                self.assertEqual(self.d200.call_args.kwargs, {"reconnect": True})
        self.find_hid.assert_not_called()
        self.probe_elgato.assert_not_called()

    def test_elgato_explicit_driver_and_model_aliases(self):
        configs = [{"driver": "ELGATO", "model": "d200", "serial": "fake-selected"}]
        configs.extend({"model": model} for model in ("elgato", "mk2", "xl", "streamdeck"))
        for cfg in configs:
            with self.subTest(cfg=cfg):
                self.assertIs(drivers.open_deck(cfg), self.elgato.return_value)
                self.elgato.assert_called_with(cfg, reconnect=False)
        self.d200.assert_not_called()
        self.find_hid.assert_not_called()
        self.probe_elgato.assert_not_called()

    def test_auto_prefers_d200_hid_without_probing_elgato(self):
        self.find_hid.return_value = {"path": b"fake"}
        self.probe_elgato.return_value = True
        self.assertIs(drivers.open_deck({}), self.d200.return_value)
        self.probe_elgato.assert_not_called()
        self.probe_mirabox.assert_not_called()
        self.elgato.assert_not_called()

    def test_auto_probes_elgato_and_falls_back_to_d200(self):
        cfg = {"driver": "auto"}
        self.probe_elgato.return_value = True
        self.assertIs(drivers.open_deck(cfg, reconnect=True), self.elgato.return_value)
        self.elgato.assert_called_once_with(cfg, reconnect=True)
        self.d200.assert_not_called()
        self.probe_mirabox.assert_not_called()
        self.probe_elgato.return_value = False
        self.assertIs(drivers.open_deck(cfg, reconnect=True), self.d200.return_value)
        self.d200.assert_called_once_with(d200.load_profile(cfg), reconnect=True)

    def test_mirabox_explicit_driver_and_akp153_model(self):
        for cfg in ({"driver": "mirabox"}, {"model": "akp153"}):
            with self.subTest(cfg=cfg):
                self.assertIs(drivers.open_deck(cfg, reconnect=True), self.mirabox.return_value)
                self.mirabox.assert_called_with(cfg, reconnect=True)
        self.d200.assert_not_called()
        self.elgato.assert_not_called()
        self.find_hid.assert_not_called()
        self.probe_elgato.assert_not_called()
        self.probe_mirabox.assert_not_called()

    def test_auto_selects_mirabox_when_d200_and_elgato_are_absent(self):
        cfg = {"driver": "auto"}
        self.probe_mirabox.return_value = True
        self.assertIs(drivers.open_deck(cfg, reconnect=True), self.mirabox.return_value)
        self.find_hid.assert_called_once_with()
        self.probe_elgato.assert_called_once_with()
        self.probe_mirabox.assert_called_once_with()
        self.mirabox.assert_called_once_with(cfg, reconnect=True)
        self.d200.assert_not_called()
        self.elgato.assert_not_called()

    def test_default_config_uses_device_section(self):
        cfg = {"driver": "elgato", "serial": "fake-selected"}
        with mock.patch("config.CFG", {"device": cfg}):
            self.assertIs(drivers.open_deck(reconnect=True), self.elgato.return_value)
        self.elgato.assert_called_once_with(cfg, reconnect=True)

    def test_unknown_driver_raises_before_probing(self):
        with mock.patch("i18n.LANG", "en"):
            with self.assertRaisesRegex(ValueError, "unknown driver: unsupported"):
                drivers.open_deck({"driver": "unsupported", "model": "d200"})
        self.find_hid.assert_not_called()
        self.probe_elgato.assert_not_called()
