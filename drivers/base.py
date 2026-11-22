"""Interface and image helpers shared by deck drivers."""

from __future__ import annotations

import hashlib
import io
from threading import Event
from typing import Callable, Protocol

from PIL import Image

from d200 import Button, InputEvent, render_icon

__all__ = ["Button", "ButtonCache", "Deck", "InputEvent", "hw_to_logical",
           "logical_to_hw", "render_icon", "split_wide_png"]


class Deck(Protocol):
    connected: Event
    disconnected: Event
    on_event: Callable[[InputEvent], None] | None
    on_connect: Callable[[], None] | None
    on_disconnect: Callable[[], None] | None
    on_info: Callable[[str], None] | None

    def start(self) -> None: ...
    def close(self) -> None: ...
    def set_brightness(self, percent: int) -> None: ...
    def set_small_window(self, mode: int = 1, cpu: int = 0, mem: int = 0, gpu: int = 0) -> None: ...
    def set_buttons(self, buttons: dict[int, Button], partial: bool = False,
                    small_png: bytes | None = None) -> None: ...


def split_wide_png(small_png: bytes, cell_size: tuple[int, int]) -> tuple[Image.Image, Image.Image]:
    with Image.open(io.BytesIO(small_png)) as source:
        image = source.convert("RGB")
    width, height = image.size
    mid = width // 2
    left = image.crop((0, 0, mid, height)).resize(cell_size, Image.Resampling.LANCZOS)
    right = image.crop((mid, 0, width, height)).resize(cell_size, Image.Resampling.LANCZOS)
    return left, right


class ButtonCache:
    """Remember the last image payload sent to each logical slot."""

    def __init__(self) -> None:
        self._hashes: dict[int, bytes] = {}

    def changed(self, slot: int, payload: bytes) -> bool:
        digest = hashlib.sha1(payload).digest()
        if self._hashes.get(slot) == digest:
            return False
        self._hashes[slot] = digest
        return True

    def clear(self) -> None:
        self._hashes.clear()


def logical_to_hw(idx: int, cols: int, rows: int) -> int | None:
    if cols < 5 or rows < 3 or not 0 <= idx < 15:
        return None
    row, col = divmod(idx, 5)
    return row * cols + col


def hw_to_logical(hw_idx: int, cols: int, rows: int) -> int | None:
    if cols < 5 or rows < 3 or not 0 <= hw_idx < cols * rows:
        return None
    row, col = divmod(hw_idx, cols)
    if row < 3 and col < 5:
        return row * 5 + col
    return None
