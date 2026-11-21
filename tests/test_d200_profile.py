"""기기 프로파일(D200 / D200H)과 HID 경로 선택, 넓은 칸 크기 맞춤을 장치 없이 확인한다."""

import io
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "hid" not in sys.modules:  # hidapi 가 없는 환경에서도 돌게
    try:
        import hid  # noqa: F401
    except ImportError:
        sys.modules["hid"] = types.SimpleNamespace(enumerate=lambda *a: [], device=object)

from PIL import Image

import d200


def _png(size):
    buf = io.BytesIO()
    Image.new("RGB", size, "#123456").save(buf, "PNG")
    return buf.getvalue()


class ProfileTest(unittest.TestCase):
    def test_default_is_d200_and_unchanged(self):
        p = d200.load_profile({})
        self.assertEqual(p, d200.PROFILES["d200"])
        self.assertEqual(p.wide_size, (458, 196))
        self.assertEqual(p.keepalive, "small_window")
        self.assertTrue(p.small_window_cmd)

    def test_d200h(self):
        p = d200.load_profile({"model": "D200H"})
        self.assertEqual(p.wide_size, (392, 196))
        # 실측: 작은 창 mode 2 만 겹침 없음 → 명령은 보내고, 넓은 칸 입력 뒤 되돌린다
        self.assertEqual(p.keepalive, "small_window")
        self.assertTrue(p.small_window_cmd)
        self.assertTrue(p.wide_press_restore)
        self.assertFalse(p.partial_updates)

    def test_overrides(self):
        p = d200.load_profile({"model": "d200h", "wide_width": 458, "keepalive": "off",
                               "keepalive_sec": 7, "small_window_cmd": False})
        self.assertEqual(p.wide_size, (458, 196))
        self.assertEqual(p.keepalive, "off")
        self.assertEqual(p.keepalive_sec, 7.0)
        self.assertFalse(p.small_window_cmd)
        self.assertFalse(p.partial_updates)  # 덮어쓰지 않은 값은 그대로

    def test_bad_values(self):
        with self.assertRaises(ValueError):
            d200.load_profile({"model": "d200x"})
        with self.assertRaises(ValueError):
            d200.load_profile({"keepalive": "sometimes"})


class DevicePathTest(unittest.TestCase):
    def test_prefers_consumer_collection_on_interface_0(self):
        devs = [
            {"interface_number": 1, "usage_page": 0x01, "path": b"kbd"},
            {"interface_number": 0, "usage_page": 0xFF00, "path": b"vendor"},
            {"interface_number": 0, "usage_page": 0x0C, "path": b"consumer"},
        ]
        self.assertEqual(d200.find_device_path(devs), b"consumer")

    def test_falls_back_to_first_interface_0(self):
        devs = [{"interface_number": 0, "usage_page": 0, "path": b"a"},
                {"interface_number": 0, "usage_page": 0, "path": b"b"}]
        self.assertEqual(d200.find_device_path(devs), b"a")

    def test_none(self):
        self.assertIsNone(d200.find_device_path([{"interface_number": 1, "usage_page": 1, "path": b"x"}]))


class WideTest(unittest.TestCase):
    def test_fit_wide_resizes_only_when_needed(self):
        src = _png((458, 196))
        self.assertIs(d200.fit_wide(src, (458, 196)), src)
        out = d200.fit_wide(src, (392, 196))
        self.assertEqual(Image.open(io.BytesIO(out)).size, (392, 196))

    def test_build_zip_uses_profile_wide_size(self):
        import json
        import zipfile

        payload = d200.build_zip({0: d200.Button(title="0")}, _png((458, 196)), (392, 196))
        z = zipfile.ZipFile(io.BytesIO(payload))
        manifest = json.loads(z.read("manifest.json"))
        wide = z.read(manifest["3_2"]["ViewParam"][0]["Icon"])
        self.assertEqual(Image.open(io.BytesIO(wide)).size, (392, 196))
        self.assertTrue(d200._payload_safe(payload))
        self.assertNotIn("Action", manifest["3_2"])  # wide_action 을 안 주면 기존 manifest 그대로

    def test_wide_action(self):
        import json
        import zipfile

        self.assertIsNone(d200.load_profile({}).wide_action)
        self.assertEqual(d200.load_profile({"model": "d200h"}).wide_action, "")
        self.assertIsNone(d200.load_profile({"model": "d200h", "wide_action": None}).wide_action)
        payload = d200.build_zip({0: d200.Button(title="0")}, _png((458, 196)), (392, 196), "")
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("manifest.json"))
        self.assertEqual(manifest["3_2"]["Action"], "")
        self.assertNotIn("Action", manifest["0_0"])

    def test_d200h_wide_manifest_matches_puritysb_shape(self):
        """puritysb 의 D200H 시계 억제 manifest: State·Action=""·ViewParam 만, SmallViewMode 없음."""
        import json
        import zipfile

        h = d200.load_profile({"model": "d200h"})
        payload = d200.build_zip({0: d200.Button(title="0")}, _png((458, 196)),
                                 h.wide_size, h.wide_action, h.wide_view_mode)
        entry = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("manifest.json"))["3_2"]
        self.assertEqual(set(entry), {"State", "ViewParam", "Action"})
        # D200 기본은 기존 그대로 SmallViewMode 2
        payload = d200.build_zip({0: d200.Button(title="0")}, _png((458, 196)))
        entry = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("manifest.json"))["3_2"]
        self.assertEqual(entry["SmallViewMode"], d200.SMALL_BACKGROUND)
        self.assertEqual(d200.parse_overrides(["wide_view_mode=none"]), {"wide_view_mode": None})

    def test_restore_sends_last_small_window_mode(self):
        """넓은 칸 입력 뒤 되돌리기 = 마지막으로 정한 작은 창 모드 (기본 2) 를 보낸다."""
        sent = []
        deck = d200.D200.__new__(d200.D200)  # 장치 없이
        deck.profile = d200.load_profile({"model": "d200h"})
        deck._stop = d200.threading.Event()
        deck.debug_raw = False
        deck._small = "1|0|0|00:00:00|0"
        deck._restore_mode = d200.SMALL_BACKGROUND
        deck.send = lambda cmd, data: sent.append((cmd, data))
        deck._restore_now()
        self.assertEqual(sent[-1][0], d200.CMD_SMALL_WINDOW)
        self.assertTrue(sent[-1][1].startswith(b"2|"))
        deck.set_small_window(200)  # 사용자가 디지털 시계를 고르면 그걸로 되돌린다
        deck._restore_now()
        self.assertTrue(sent[-1][1].startswith(b"200|"))

    def test_wide_press_restore_flag(self):
        self.assertTrue(d200.load_profile({"model": "d200h"}).wide_press_restore)
        self.assertFalse(d200.load_profile({}).wide_press_restore)
        self.assertFalse(d200.load_profile({"model": "d200h", "wide_press_restore": False}).wide_press_restore)

    def test_parse_button(self):
        """D200H 실측 바이트 (Windows, 2026-11-21)."""
        h = d200.load_profile({"model": "d200h"})
        old = d200.load_profile({})
        ev = d200.parse_button(bytes([0x01, 0x0D, 0x01, 0x01]), h)
        self.assertEqual((ev.index, ev.kind, ev.action, ev.state), (13, "button", "press", 1))
        self.assertEqual(d200.parse_button(bytes([0x01, 0x0D, 0x00, 0x01]), h).action, "release")
        self.assertEqual(d200.parse_button(bytes([0x00, 0x0D, 0x00, 0x01]), h).action, "release")
        # 일반 칸은 기존 해석(data[3]) 그대로
        self.assertEqual(d200.parse_button(bytes([0x01, 0x09, 0x01, 0x01]), h).action, "press")
        self.assertEqual(d200.parse_button(bytes([0x01, 0x09, 0x01, 0x00]), h).action, "release")
        # D200 프로파일은 넓은 칸도 기존 해석
        self.assertEqual(d200.parse_button(bytes([0x01, 0x0D, 0x00, 0x01]), old).action, "press")
        self.assertEqual(d200.parse_button(bytes([0x00, 0x03, 0x02, 0x02]), old).kind, "encoder")

    def test_parse_overrides(self):
        self.assertEqual(
            d200.parse_overrides(["model=d200h", "wide_width=458", "keepalive_sec=7.5",
                                  "small_window_cmd=true", "wide_action=none"]),
            {"model": "d200h", "wide_width": 458, "keepalive_sec": 7.5,
             "small_window_cmd": True, "wide_action": None})
        self.assertEqual(d200.parse_overrides(["wide_action=empty"]), {"wide_action": ""})


if __name__ == "__main__":
    unittest.main()
