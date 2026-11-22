"""Display numbered test keys and log raw input on a Mirabox/Ajazz deck."""

from __future__ import annotations

import argparse
import io
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hid
from PIL import Image, ImageDraw, ImageFont

from d200 import FONT_PATH
from drivers.base import Button
from drivers.mirabox import MIRABOX_PROFILES, MiraboxDeck


def _marker(slot: int, size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, ("#243a59", "#255043", "#563643")[slot // 5])
    draw = ImageDraw.Draw(image)
    label = ImageFont.truetype(FONT_PATH, height // 5)
    number = ImageFont.truetype(FONT_PATH, height // 2)
    draw.rectangle((2, 2, width - 3, height - 3), outline="#ffffff", width=2)
    draw.text((width // 2, height // 5), "▲ TOP", font=label, fill="white", anchor="mm")
    draw.text((width // 2, height * 3 // 5), str(slot), font=number, fill="white", anchor="mm")
    return image


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _show_test_keys(deck: MiraboxDeck) -> None:
    size = deck.profile.key_size
    buttons = {slot: Button(image=_png(_marker(slot, size))) for slot in range(13)}
    width, height = size
    wide = Image.new("RGB", (2 * width, height))
    wide.paste(_marker(13, size), (0, 0))
    wide.paste(_marker(14, size), (width, 0))
    deck.set_buttons(buttons, small_png=_png(wide))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=tuple(MIRABOX_PROFILES), help="select a model (default: auto-detect)")
    parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), help="override image rotation")
    parser.add_argument("--invert-rows", action="store_true", default=None, help="reverse the order of input rows")
    parser.add_argument("seconds", type=float, default=30, nargs="?", help="input capture duration (default: 30)")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("seconds must be a finite nonnegative number")

    supported = {pair for profile in MIRABOX_PROFILES.values() for pair in profile.vid_pids}
    try:
        devices = hid.enumerate()
    except OSError as exc:
        parser.exit(1, f"Could not enumerate HID devices: {exc}\n")
    print("Mirabox/Ajazz HID interfaces:")
    for info in devices:
        vid, pid = info["vendor_id"], info["product_id"]
        if (vid, pid) in supported:
            usage_page = info.get("usage_page")
            usage = f"0x{usage_page:04x}" if usage_page is not None else "unknown"
            print(f"  {vid:04x}:{pid:04x} interface={info.get('interface_number')} usage_page={usage}")

    try:
        deck = MiraboxDeck({"model": args.model, "rotation": args.rotation, "invert_rows": args.invert_rows})
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    try:
        profile = deck.profile
        deck.debug_raw = True
        print(f"Model={profile.model}, rotation={profile.rotation}, invert_rows={profile.invert_rows}", flush=True)
        _show_test_keys(deck)
        deck.start()
        if deck.disconnected.wait(args.seconds):
            print("Device disconnected or a HID transfer failed.", file=sys.stderr)
            return 1
    except KeyboardInterrupt:
        pass
    finally:
        deck.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
