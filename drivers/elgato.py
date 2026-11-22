"""Elgato Stream Deck driver for devices with at least a 5×3 key layout."""

from __future__ import annotations

import io
import threading
from contextlib import suppress
from copy import deepcopy
from typing import Callable

from PIL import Image

from i18n import t

from .base import Button, ButtonCache, InputEvent, hw_to_logical, logical_to_hw, render_icon, split_wide_png

RECONNECT_POLL_SEC = 2.0


def _device_manager():
    from StreamDeck.DeviceManager import DeviceManager

    return DeviceManager


def _compatible_devices(manager_factory):
    for dev in manager_factory().enumerate():
        if dev.is_visual():
            rows, cols = dev.key_layout()
            if cols >= 5 and rows >= 3:
                yield dev


def probe_elgato() -> bool:
    """Check for a compatible Stream Deck without opening a device."""
    try:
        return next(_compatible_devices(_device_manager()), None) is not None
    except Exception:
        return False  # Optional package or HID backend may be unavailable.


def _close_device(dev) -> None:
    with suppress(Exception):
        dev.close()


class ElgatoDeck:
    def __init__(self, cfg: dict | None = None, reconnect: bool = False, *,
                 manager_factory=None, pil_helper=None) -> None:
        try:
            if manager_factory is None:
                manager_factory = _device_manager()
            if pil_helper is None:
                from StreamDeck.ImageHelpers import PILHelper

                pil_helper = PILHelper
        except ImportError as exc:
            raise RuntimeError(t("Elgato Stream Deck 을 쓰려면 streamdeck 패키지를 설치하세요: pip install streamdeck",
                                 "Install the streamdeck package to use an Elgato Stream Deck: pip install streamdeck")) from exc

        self.reconnect = reconnect
        self.connected = threading.Event()
        self.disconnected = threading.Event()
        self.on_event: Callable[[InputEvent], None] | None = None
        self.on_connect: Callable[[], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None
        self.on_info: Callable[[str], None] | None = None

        self._manager_factory = manager_factory
        self._pil_helper = pil_helper
        self._serial = (cfg or {}).get("serial")
        self._dev = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._disconnecting = False
        self._cache = ButtonCache()
        self._brightness: int | None = None
        self._last_buttons: dict[int, Button] | None = None
        self._last_small_png: bytes | None = None
        if not self._open() and not reconnect:
            raise RuntimeError(t("Elgato Stream Deck 을 찾지 못했어요 (USB 연결을 확인하세요)",
                                 "Elgato Stream Deck not found (check the USB connection)"))

    def _open_device(self):
        devices = list(_compatible_devices(self._manager_factory))
        first = None
        for dev in devices:
            try:
                dev.open()
            except Exception:
                _close_device(dev)
                continue
            if not self._serial:
                return dev
            # The serial number is read from a HID feature report after open().
            with suppress(Exception):
                if dev.get_serial_number() == self._serial:
                    if first is not None:
                        _close_device(first)
                    return dev
            if first is None:
                first = dev
            else:
                _close_device(dev)
        return first

    def _open(self) -> bool:
        dev = None
        try:
            dev = self._open_device()
            if dev is None:
                return False
            dev.reset()
            self._cache.clear()
            if self._brightness is not None:
                dev.set_brightness(self._brightness)
            if self._last_buttons is not None:
                images = self._key_images(dev, self._last_buttons, False, self._last_small_png)
                self._send_images(dev, images)
            dev.set_key_callback(self._on_key)
        except Exception:
            if dev is not None:
                _close_device(dev)
            return False
        if self._stop.is_set():
            _close_device(dev)
            return False
        self._dev = dev
        self.connected.set()
        return True

    @staticmethod
    def _is_connected(dev) -> bool:
        try:
            # python-elgato-streamdeck calls this method connected().
            check = getattr(dev, "is_connected", None) or dev.connected
            return dev.is_open() and check()
        except Exception:
            return False

    def _drop(self) -> None:
        """Detach a failed device under the lock. The caller finishes closing it."""
        self._dev = None
        self.connected.clear()
        self._cache.clear()
        if self.reconnect:
            self._disconnecting = True
        else:
            self.disconnected.set()
            self._stop.set()

    def _finish_drop(self, dev) -> None:
        _close_device(dev)
        try:
            callback = self.on_disconnect
            if self.reconnect and callback and not self._stop.is_set():
                callback()
        finally:
            with self._lock:
                self._disconnecting = False

    def _watch(self) -> None:
        while not self._stop.wait(RECONNECT_POLL_SEC):
            dropped = None
            on_connect = None
            with self._lock:
                if self._stop.is_set():
                    return
                dev = self._dev
                if dev is None:
                    if self.reconnect and not self._disconnecting and self._open():
                        on_connect = self.on_connect
                elif not self._is_connected(dev):
                    self._drop()
                    dropped = dev
            if dropped is not None:
                self._finish_drop(dropped)
            if on_connect and not self._stop.is_set():
                threading.Thread(target=on_connect, daemon=True).start()

    def start(self) -> None:
        with self._lock:
            if self._monitor is None and not self._stop.is_set():
                self._monitor = threading.Thread(target=self._watch, daemon=True)
                self._monitor.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            dev, self._dev = self._dev, None
            self.connected.clear()
            self._cache.clear()
        if dev is not None:
            with suppress(Exception):
                dev.reset()
            _close_device(dev)

    def set_brightness(self, percent: int) -> None:
        with self._lock:
            self._brightness = max(0, min(100, percent))
            dev = self._dev
            if dev is None:
                return
            try:
                dev.set_brightness(self._brightness)
                return
            except Exception:
                self._drop()
        self._finish_drop(dev)

    def set_small_window(self, mode: int = 1, cpu: int = 0, mem: int = 0, gpu: int = 0) -> None:
        """Stream Deck has no small-window display mode."""

    def _key_images(self, dev, buttons: dict[int, Button], partial: bool,
                    small_png: bytes | None) -> dict[int, bytes]:
        key_size = dev.key_image_format()["size"]
        blank = Image.new("RGB", key_size, "black")
        images = {}
        for slot in range(13):
            if slot in buttons:
                with Image.open(io.BytesIO(render_icon(buttons[slot]))) as source:
                    images[slot] = source.convert("RGB")
            elif not partial:
                images[slot] = blank
        if small_png is not None:
            images[13], images[14] = split_wide_png(small_png, key_size)
        return {
            slot: self._pil_helper.to_native_key_format(dev, self._pil_helper.create_scaled_key_image(dev, image))
            for slot, image in images.items()
        }

    def _send_images(self, dev, images: dict[int, bytes]) -> None:
        rows, cols = dev.key_layout()
        for slot, payload in images.items():
            hw_key = logical_to_hw(slot, cols, rows)
            if hw_key is not None and self._cache.changed(slot, payload):
                dev.set_key_image(hw_key, payload)

    def set_buttons(self, buttons: dict[int, Button], partial: bool = False,
                    small_png: bytes | None = None) -> None:
        if self.reconnect and not self.connected.is_set():
            return
        with self._lock:
            dev = self._dev
            if dev is None:
                return
            visible = {slot: buttons[slot] for slot in range(13) if slot in buttons}
            images = self._key_images(dev, visible, partial, small_png)
            if partial and self._last_buttons is not None:
                self._last_buttons.update(deepcopy(visible))
            else:
                self._last_buttons = deepcopy(visible)
            if small_png is not None:
                self._last_small_png = small_png
            try:
                self._send_images(dev, images)
                return
            except Exception:
                self._drop()
        self._finish_drop(dev)

    def _on_key(self, dev, hw_key: int, state: bool) -> None:
        callback = self.on_event
        if dev is not self._dev or not self.connected.is_set() or callback is None:
            return
        rows, cols = dev.key_layout()
        logical = hw_to_logical(hw_key, cols, rows)
        if logical is not None:
            callback(InputEvent(logical, "button", "press" if state else "release", 1 if state else 0))
