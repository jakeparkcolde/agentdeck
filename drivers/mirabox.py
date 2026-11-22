"""HID driver for Mirabox StreamDock and Ajazz AKP153 15-key decks.

Protocol references: MiraboxSpace/StreamDock-Device-SDK and 4ndv/mirajazz.
"""

from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Callable

import hid
from PIL import Image

from i18n import t

from .base import Button, ButtonCache, InputEvent, render_icon, split_wide_png

RECONNECT_POLL_SEC = 2.0
READ_TIMEOUT_MS = 20


@dataclass(frozen=True)
class MiraboxProfile:
    model: str
    vid_pids: tuple[tuple[int, int], ...]
    packet_size: int
    read_size: int
    key_size: tuple[int, int]
    rotation: int
    invert_rows: bool
    dual_state: bool
    keepalive_sec: float = 10.0


MIRABOX_PROFILES = {
    "293": MiraboxProfile("293", ((0x5500, 0x1001),), 512, 512, (100, 100),
                          rotation=180, invert_rows=True, dual_state=False),
    "293v3": MiraboxProfile("293v3", ((0x6603, 0x1005), (0x6603, 0x1006), (0x6603, 0x1010)),
                            1024, 512, (112, 112), rotation=180, invert_rows=True, dual_state=True),
    "293s": MiraboxProfile("293s", ((0x5548, 0x6670),), 512, 512, (85, 85),
                           rotation=90, invert_rows=False, dual_state=False),
    "293sv3": MiraboxProfile("293sv3", ((0x6603, 0x1014),), 1024, 512, (85, 85),
                             rotation=90, invert_rows=False, dual_state=True),
    "akp153": MiraboxProfile("akp153", ((0x5548, 0x6674), (0x0300, 0x1010), (0x0300, 0x1020),
                                       (0x0300, 0x3010), (0x0300, 0x3011)),
                             512, 512, (85, 85), rotation=90, invert_rows=False, dual_state=False),
}


def _usb_id(value: int | str | None) -> int | None:
    return int(value, 0) if isinstance(value, str) else value


def _matching_models(vid: int | None, pid: int | None) -> dict[tuple[int, int], str]:
    return {
        (vendor, product): model
        for model, profile in MIRABOX_PROFILES.items()
        for vendor, product in profile.vid_pids
        if (vid is None or vendor == vid) and (pid is None or product == pid)
    }


def load_mirabox_profile(cfg: dict | None = None) -> MiraboxProfile:
    """Build a profile from device config. A nonpositive keepalive disables it."""
    cfg = cfg or {}
    model = str(cfg.get("model") or "").lower()
    vid, pid = _usb_id(cfg.get("vid")), _usb_id(cfg.get("pid"))
    if model not in MIRABOX_PROFILES:
        matches = _matching_models(vid, pid) if vid is not None or pid is not None else {}
        model = next(iter(matches.values()), "293s")
    profile = MIRABOX_PROFILES[model]
    overrides = {
        name: convert(cfg[name])
        for name, convert in (
            ("rotation", int), ("invert_rows", bool),
            ("packet_size", int), ("dual_state", bool), ("keepalive_sec", float),
        )
        if cfg.get(name) is not None
    }
    key_size = cfg.get("key_size")
    if isinstance(key_size, int):
        overrides["key_size"] = (key_size, key_size)
    elif key_size is not None:
        overrides["key_size"] = (int(key_size[0]), int(key_size[1]))
    if vid is not None or pid is not None:
        overrides["vid_pids"] = tuple(dict.fromkeys(
            (vendor if vid is None else vid, product if pid is None else pid)
            for vendor, product in profile.vid_pids
        ))
    return replace(profile, **overrides)


def find_mirabox_device(cfg: dict | None = None, devices=None) -> tuple[dict, MiraboxProfile] | None:
    """Select a matching HID path, preferring its vendor-defined collection."""
    cfg = cfg or {}
    model = str(cfg.get("model") or "").lower()
    if model in MIRABOX_PROFILES:
        profile = load_mirabox_profile(cfg)
        models = dict.fromkeys(profile.vid_pids, profile.model)
    else:
        models = _matching_models(_usb_id(cfg.get("vid")), _usb_id(cfg.get("pid")))
        if not models:
            profile = load_mirabox_profile(cfg)
            models = dict.fromkeys(profile.vid_pids, profile.model)

    if devices is None:
        devices = hid.enumerate()
    candidates = {}
    for info in devices:
        pair = (info.get("vendor_id"), info.get("product_id"))
        if pair in models:
            candidates.setdefault(pair, []).append(info)
    if not candidates:
        return None
    pair, interfaces = next(iter(candidates.items()))
    info = max(interfaces, key=lambda d: ((d.get("usage_page") or 0) >= 0xFF00,
                                          d.get("interface_number") == 0))
    return info, load_mirabox_profile({**cfg, "model": models[pair]})


def probe_mirabox() -> bool:
    """Check for compatible hardware without opening it."""
    try:
        return find_mirabox_device() is not None
    except (OSError, ValueError):
        return False


def make_cmd(payload: bytes, packet_size: int) -> bytes:
    report = b"\x00CRT\x00\x00" + payload
    if len(report) > 1 + packet_size:
        raise ValueError("Mirabox command does not fit in one report")
    return report.ljust(1 + packet_size, b"\x00")


def encode_key_image(img: Image.Image, profile: MiraboxProfile) -> bytes:
    image = img.convert("RGB").resize(profile.key_size, Image.Resampling.LANCZOS)
    if profile.rotation:
        image = image.rotate(profile.rotation)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def iter_image_packets(jpeg: bytes, hw_key: int, packet_size: int) -> Iterator[bytes]:
    yield make_cmd(b"BAT\x00\x00" + len(jpeg).to_bytes(2, "big") + bytes([hw_key]), packet_size)
    for offset in range(0, len(jpeg), packet_size):
        yield b"\x00" + jpeg[offset:offset + packet_size].ljust(packet_size, b"\x00")


def parse_mirabox_input(data: bytes, profile: MiraboxProfile) -> list[InputEvent]:
    if len(data) < 11 or data[:3] != b"ACK" or not 1 <= data[9] <= 15:
        return []
    index, state = data[9] - 1, data[10]
    if profile.invert_rows:
        row, col = divmod(index, 5)
        index = (2 - row) * 5 + col
    if not profile.dual_state:
        return [InputEvent(index, "button", "press", 1), InputEvent(index, "button", "release", 0)]
    if state == 1:
        return [InputEvent(index, "button", "press", 1)]
    if state == 0:
        return [InputEvent(index, "button", "release", 0)]
    return []


def _write_report(dev, report: bytes) -> None:
    written = dev.write(report)
    # macOS hidapi can report the payload length without the report ID.
    if written is not None and written < len(report) - 1:
        raise OSError("Incomplete Mirabox HID write")


def _close_device(dev) -> None:
    with suppress(OSError, ValueError):
        dev.close()


class MiraboxDeck:
    """StreamDock and AKP153 driver with optional USB reconnection."""

    def __init__(self, cfg: dict | None = None, reconnect: bool = False) -> None:
        self._cfg = dict(cfg or {})
        self.profile = load_mirabox_profile(self._cfg)
        self.reconnect = reconnect
        self.connected = threading.Event()
        self.disconnected = threading.Event()
        self.on_event: Callable[[InputEvent], None] | None = None
        self.on_connect: Callable[[], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None
        self.on_info: Callable[[str], None] | None = None
        self.debug_raw = False

        self._dev = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._keepalive: threading.Thread | None = None
        self._disconnecting = False
        self._cache = ButtonCache()
        self._brightness: int | None = None
        self._last_buttons: dict[int, Button] | None = None
        self._last_small_png: bytes | None = None
        if not self._open() and not reconnect:
            raise RuntimeError(t("Mirabox/Ajazz 장치를 찾지 못했어요 (USB 연결을 확인하세요)",
                                 "Mirabox/Ajazz deck not found (check the USB connection)"))

    @staticmethod
    def _init_device(dev, profile: MiraboxProfile) -> None:
        _write_report(dev, make_cmd(b"DIS", profile.packet_size))
        _write_report(dev, make_cmd(b"LIG\x00\x00\x00\x00", profile.packet_size))

    def _open(self) -> bool:
        """Initialize and restore a device under the lock before exposing it."""
        dev = None
        try:
            match = find_mirabox_device(self._cfg)
            if match is None:
                return False
            info, profile = match
            dev = hid.device()
            dev.open_path(info["path"])
            self._init_device(dev, profile)
            if self._brightness is not None:
                self._write_brightness(dev, self._brightness, profile.packet_size)
            self._cache.clear()
            if self._last_buttons is not None:
                images = self._key_images(self._last_buttons, False, self._last_small_png, profile)
                self._send_images(dev, images, profile.packet_size)
        except (OSError, ValueError):
            if dev is not None:
                _close_device(dev)
            return False
        if self._stop.is_set():
            _close_device(dev)
            return False
        self.profile, self._dev = profile, dev
        self.connected.set()
        return True

    def _drop(self) -> None:
        """Close a failed device under the lock."""
        dev, self._dev = self._dev, None
        self.connected.clear()
        self._cache.clear()
        _close_device(dev)
        self._disconnecting = self.reconnect
        if not self.reconnect:
            self.disconnected.set()
            self._stop.set()

    def _finish_drop(self) -> None:
        try:
            if self.reconnect and self.on_disconnect and not self._stop.is_set():
                self.on_disconnect()
        finally:
            with self._lock:
                self._disconnecting = False

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                dev = self._dev
                if dev is not None:
                    try:
                        report = bytes(dev.read(self.profile.read_size, READ_TIMEOUT_MS))
                    except (OSError, ValueError):
                        self._drop()
                        report = None
            if dev is None:
                if self._stop.wait(RECONNECT_POLL_SEC):
                    return
                with self._lock:
                    if not self._disconnecting and not self._stop.is_set() and self._open():
                        if self.on_connect:
                            threading.Thread(target=self.on_connect, daemon=True).start()
            elif report is None:
                self._finish_drop()
            elif report and self.connected.is_set() and not self._stop.is_set():
                self._dispatch_input(report)
            # Give writes a chance to acquire the lock between timed reads.
            self._stop.wait(0.005)

    def _dispatch_input(self, report: bytes) -> None:
        events = parse_mirabox_input(report, self.profile)
        if self.debug_raw:
            key = events[0].index if events else "-"
            print(f"RAW {report.hex(' ')} | logical key {key}", flush=True)
        callback = self.on_event
        if callback is not None:
            for event in events:
                callback(event)

    def _keepalive_loop(self) -> None:
        every = self.profile.keepalive_sec
        while every > 0 and not self._stop.wait(every):
            with self._lock:
                dev = self._dev
                if dev is None or self._stop.is_set():
                    continue
                try:
                    _write_report(dev, make_cmd(b"CONNECT", self.profile.packet_size))
                    continue
                except (OSError, ValueError):
                    self._drop()
            self._finish_drop()

    def start(self) -> None:
        with self._lock:
            if self._reader is not None or self._stop.is_set():
                return
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._keepalive = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._reader.start()
            self._keepalive.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            dev, self._dev = self._dev, None
            self.connected.clear()
            self._cache.clear()
            if dev is not None:
                packet_size = self.profile.packet_size
                with suppress(OSError, ValueError):
                    _write_report(dev, make_cmd(b"CLE\x00\x00\x00\xff", packet_size))
                    if packet_size >= 1024:
                        _write_report(dev, make_cmd(b"STP", packet_size))
                _close_device(dev)

    @staticmethod
    def _write_brightness(dev, percent: int, packet_size: int) -> None:
        _write_report(dev, make_cmd(b"LIG\x00\x00" + bytes([percent]), packet_size))

    def set_brightness(self, percent: int) -> None:
        with self._lock:
            self._brightness = max(0, min(100, percent))
            dev = self._dev
            if dev is None:
                return
            try:
                self._write_brightness(dev, self._brightness, self.profile.packet_size)
                return
            except (OSError, ValueError):
                self._drop()
        self._finish_drop()

    def set_small_window(self, mode: int = 1, cpu: int = 0, mem: int = 0, gpu: int = 0) -> None:
        """There is no separate small-window display on these models."""

    @staticmethod
    def _key_images(buttons: dict[int, Button], partial: bool, small_png: bytes | None,
                    profile: MiraboxProfile) -> dict[int, bytes]:
        images = {}
        for slot, button in buttons.items():
            with Image.open(io.BytesIO(render_icon(button))) as source:
                images[slot] = encode_key_image(source, profile)
        if not partial and len(buttons) < 13:
            blank = encode_key_image(Image.new("RGB", profile.key_size, "black"), profile)
            for slot in range(13):
                images.setdefault(slot, blank)
        if small_png is not None:
            left, right = split_wide_png(small_png, profile.key_size)
            images[13] = encode_key_image(left, profile)
            images[14] = encode_key_image(right, profile)
        return images

    def _send_images(self, dev, images: dict[int, bytes], packet_size: int) -> None:
        dirty = False
        for slot in sorted(images):
            jpeg = images[slot]
            if self._cache.changed(slot, jpeg):
                for report in iter_image_packets(jpeg, slot + 1, packet_size):
                    _write_report(dev, report)
                dirty = True
        if dirty:
            _write_report(dev, make_cmd(b"STP", packet_size))

    def set_buttons(self, buttons: dict[int, Button], partial: bool = False,
                    small_png: bytes | None = None) -> None:
        with self._lock:
            dev = self._dev
            if dev is None:
                return
            visible = {slot: buttons[slot] for slot in range(13) if slot in buttons}
            images = self._key_images(visible, partial, small_png, self.profile)
            if partial and self._last_buttons is not None:
                self._last_buttons.update(deepcopy(visible))
            else:
                self._last_buttons = deepcopy(visible)
            if small_png is not None:
                self._last_small_png = small_png
            try:
                self._send_images(dev, images, self.profile.packet_size)
                return
            except (OSError, ValueError):
                self._drop()
        self._finish_drop()
