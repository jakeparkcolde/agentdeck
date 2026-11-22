"""케이블 뽑기 · 꽂기(다른 컴퓨터로 옮겼다 돌아오기)를 가짜 HID 장치로 흉내 내서 재연결을 확인한다."""

import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "hid" not in sys.modules:
    try:
        import hid  # noqa: F401
    except ImportError:
        sys.modules["hid"] = types.SimpleNamespace(enumerate=lambda *a: [], device=object)

import d200


class FakeHub:
    """USB 포트 하나. unplug 하면 그때까지 연 핸들은 다시 꽂아도 계속 실패한다 (실제 장치와 같게)."""

    def __init__(self, plugged: bool = True) -> None:
        self.plugged = plugged
        self.gen = 0
        self.writes: list[bytes] = []
        self.lock = threading.Lock()

    def unplug(self) -> None:
        self.plugged = False
        self.gen += 1

    def plug(self) -> None:
        self.plugged = True

    # hid 모듈 흉내
    def enumerate(self, vid, pid):
        return [{"interface_number": 0, "usage_page": 0x0C, "path": b"fake"}] if self.plugged else []

    def device(self):
        return FakeDev(self)

    def cmds(self) -> list[int]:
        with self.lock:
            return [int.from_bytes(w[3:5], "big") for w in self.writes if w[1:3] == b"\x7c\x7c"]

    def clear(self) -> None:
        with self.lock:
            self.writes.clear()


class FakeDev:
    def __init__(self, hub: FakeHub) -> None:
        self.hub = hub
        self.gen = -1

    def _alive(self) -> bool:
        return self.hub.plugged and self.gen == self.hub.gen

    def open_path(self, path):
        if not self.hub.plugged:
            raise OSError("open failed")
        self.gen = self.hub.gen

    def write(self, data):
        if not self._alive():
            raise OSError("write error")
        with self.hub.lock:
            self.hub.writes.append(bytes(data))

    def read(self, n, timeout):
        if not self._alive():
            raise OSError("read error")
        time.sleep(timeout / 1000)
        return []

    def close(self):
        pass


def wait_until(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class ReconnectTest(unittest.TestCase):
    def setUp(self):
        self.hub = FakeHub()
        for name, value in (("hid", self.hub), ("RECONNECT_POLL_SEC", 0.05), ("RECONNECT_SETTLE_SEC", 0.01)):
            patcher = mock.patch.object(d200, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.profile = d200.load_profile({"model": "d200h", "keepalive": "off"})
        self.screen = {0: d200.Button(title="0")}

    def make(self, **kw) -> d200.D200:
        deck = d200.D200(self.profile, **kw)
        self.addCleanup(deck.close)
        return deck

    def test_no_device_without_reconnect_raises(self):
        self.hub.unplug()
        with self.assertRaises(RuntimeError):
            d200.D200(self.profile)

    def test_starts_without_device_then_connects(self):
        self.hub.unplug()
        deck = self.make(reconnect=True)
        self.assertFalse(deck.connected.is_set())
        connected = threading.Event()
        deck.on_connect = connected.set
        deck.start()
        deck.set_brightness(60)
        deck.set_small_window(d200.SMALL_BACKGROUND)
        deck.set_buttons(self.screen)  # 장치가 없으니 건너뛴다
        self.assertEqual(self.hub.cmds(), [])
        self.hub.plug()
        self.assertTrue(connected.wait(3), "on_connect not called after plug-in")
        cmds = self.hub.cmds()
        self.assertIn(d200.CMD_BRIGHTNESS, cmds)  # 꽂히면 밝기·작은 창 모드 복원
        self.assertIn(d200.CMD_SMALL_WINDOW, cmds)

    def test_unplug_and_replug_restores_last_screen(self):
        deck = self.make(reconnect=True)
        events = {"connect": threading.Event(), "disconnect": threading.Event()}
        deck.on_connect = events["connect"].set
        deck.on_disconnect = events["disconnect"].set
        deck.start()
        deck.set_brightness(80)
        deck.set_small_window(d200.SMALL_BACKGROUND)
        deck.set_buttons(self.screen)
        self.assertIn(d200.CMD_SET_BUTTONS, self.hub.cmds())

        self.hub.unplug()  # 케이블을 다른 컴퓨터로
        self.assertTrue(events["disconnect"].wait(3))
        self.assertFalse(deck.connected.is_set())
        self.assertFalse(deck.disconnected.is_set())  # 재연결 모드에선 멈추지 않는다
        deck.set_buttons(self.screen)  # 끊긴 동안 그리기: 오류 없이 건너뜀
        deck.set_brightness(80)

        self.hub.clear()
        self.hub.plug()  # 돌아옴
        self.assertTrue(events["connect"].wait(3))
        cmds = self.hub.cmds()
        self.assertIn(d200.CMD_BRIGHTNESS, cmds)
        self.assertIn(d200.CMD_SMALL_WINDOW, cmds)
        self.assertIn(d200.CMD_SET_BUTTONS, cmds)  # 마지막 화면을 바로 다시 보낸다
        self.assertTrue(deck.connected.is_set())

    def test_unplug_and_replug_revives_stuck_device_and_restores_screen(self):
        revive = self.enterContext(mock.patch.object(d200, "revive_stuck_device", return_value=True))
        clock = mock.Mock(wraps=time)
        clock.sleep.return_value = None
        self.enterContext(mock.patch.object(d200, "time", clock))
        deck = self.make(reconnect=True)
        hid_info = d200._find_hid()
        connected = threading.Event()
        deck.on_connect = connected.set
        deck.on_disconnect = self.hub.plug
        deck.set_brightness(80)
        deck.set_small_window(d200.SMALL_BACKGROUND)
        deck.set_buttons(self.screen)
        self.hub.clear()

        self.hub.unplug()
        find_hid = self.enterContext(mock.patch.object(d200, "_find_hid", side_effect=[None, hid_info]))
        deck.start()

        self.assertTrue(connected.wait(3), "on_connect not called after reviving stuck device")
        self.assertTrue(deck.connected.is_set())
        revive.assert_called_once_with()
        clock.sleep.assert_called_once_with(0.5)
        self.assertEqual(find_hid.call_count, 2)
        cmds = self.hub.cmds()
        self.assertIn(d200.CMD_BRIGHTNESS, cmds)
        self.assertIn(d200.CMD_SMALL_WINDOW, cmds)
        self.assertIn(d200.CMD_SET_BUTTONS, cmds)

    def test_repeated_switching(self):
        deck = self.make(reconnect=True)
        count = []
        deck.on_connect = lambda: count.append(1)
        deck.start()
        deck.set_buttons(self.screen)
        for i in range(3):
            self.hub.unplug()
            self.assertTrue(wait_until(lambda: not deck.connected.is_set()))
            self.hub.plug()
            self.assertTrue(wait_until(lambda: len(count) == i + 1), f"switch {i + 1} did not reconnect")

    def test_without_reconnect_unplug_stops(self):
        deck = self.make()
        deck.start()
        self.hub.unplug()
        self.assertTrue(deck.disconnected.wait(3))
        self.hub.plug()
        time.sleep(0.2)
        self.assertFalse(deck.connected.is_set())  # 기존 동작: 다시 열지 않는다


if __name__ == "__main__":
    unittest.main()
