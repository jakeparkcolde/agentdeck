"""Select the configured hardware driver."""

from __future__ import annotations

import d200
from d200 import D200, load_profile
from i18n import t

from .base import Deck
from .elgato import ElgatoDeck, probe_elgato


def open_deck(cfg: dict | None = None, reconnect: bool = False) -> Deck:
    if cfg is None:
        from config import CFG

        cfg = CFG.get("device") or {}
    driver = str(cfg.get("driver") or "auto").lower()
    model = str(cfg.get("model") or "").lower()

    if driver == "d200" or (driver == "auto" and model in ("d200", "d200h")):
        return D200(load_profile(cfg), reconnect=reconnect)
    if driver == "elgato" or (driver == "auto" and model in ("elgato", "mk2", "xl", "streamdeck")):
        return ElgatoDeck(cfg, reconnect=reconnect)
    if driver == "auto":
        if d200._find_hid() is not None or not probe_elgato():
            return D200(load_profile(cfg), reconnect=reconnect)
        return ElgatoDeck(cfg, reconnect=reconnect)
    raise ValueError(t(f"알 수 없는 드라이버: {driver} (auto, d200, elgato)",
                       f"unknown driver: {driver} (use auto, d200, or elgato)"))
