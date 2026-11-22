"""Select the configured hardware driver."""

from __future__ import annotations

import d200
from d200 import D200, load_profile
from i18n import t

from .base import Deck
from .elgato import ElgatoDeck, probe_elgato
from .mirabox import MIRABOX_PROFILES, MiraboxDeck, probe_mirabox


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
    if driver == "mirabox" or (driver == "auto" and (model == "mirabox" or model in MIRABOX_PROFILES)):
        return MiraboxDeck(cfg, reconnect=reconnect)
    if driver == "auto":
        if d200._find_hid() is not None:
            return D200(load_profile(cfg), reconnect=reconnect)
        if probe_elgato():
            return ElgatoDeck(cfg, reconnect=reconnect)
        if probe_mirabox():
            return MiraboxDeck(cfg, reconnect=reconnect)
        return D200(load_profile(cfg), reconnect=reconnect)
    raise ValueError(t(f"알 수 없는 드라이버: {driver} (auto, d200, elgato, mirabox)",
                       f"unknown driver: {driver} (use auto, d200, elgato, or mirabox)"))
