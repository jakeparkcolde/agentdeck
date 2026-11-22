"""Render page previews without hardware. Usage: preview.py [--device DEVICE] OUTPUT"""

from __future__ import annotations

import argparse
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import clock
from d200 import FONT_PATH, ICON, Button, render_icon
from dashboard import collect_llms
from drivers.base import split_wide_png
from drivers.mirabox import MIRABOX_PROFILES
from pages import nav, page_grid, page_studio, page_summary
from remote_herdr import TOWER_SPACE, studio_state

KEY_SIZES = {"d200": ICON, "elgato": 72, "mirabox": MIRABOX_PROFILES["293s"].key_size[0]}


def sheet(buttons: dict[int, Button], title: str, out: str | Path, device: str = "d200") -> None:
    key_size = KEY_SIZES[device]
    scale = 2 if device == "elgato" else 1
    pad, cell = 10, key_size * scale
    width = 5 * (cell + pad) + pad
    if device == "d200":
        width = max(width, 3 * (cell + pad) + clock.W + 2 * pad)
    img = Image.new("RGB", (width, 3 * (cell + pad) + pad + 40), "black")
    d = ImageDraw.Draw(img)
    d.text((pad, 8), title, font=ImageFont.truetype(FONT_PATH, 24), fill="#c0caf5")

    def paste(slot: int, image: Image.Image) -> None:
        row, col = divmod(slot, 5)
        if scale != 1:
            image = image.resize((cell, cell), Image.Resampling.NEAREST)
        img.paste(image, (pad + col * (cell + pad), 40 + pad + row * (cell + pad)))

    for slot, button in buttons.items():
        if 0 <= slot < 13:
            with Image.open(io.BytesIO(render_icon(button))) as source:
                paste(slot, source.convert("RGB").resize((key_size, key_size), Image.Resampling.LANCZOS))

    wide = clock.render_clock()
    if device == "d200":
        with Image.open(io.BytesIO(wide)) as source:
            paste(13, source)
    else:
        left, right = split_wide_png(wide, (key_size, key_size))
        paste(13, left)
        paste(14, right)
    img.save(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=tuple(KEY_SIZES), default="d200")
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(4) as pool:
        llms = collect_llms(pool)
    studio = studio_state()
    row = [workspace for workspace in studio.workspaces if workspace.name != TOWER_SPACE]
    pages = (
        ("1 · 요약", page_summary(llms, studio, now)),
        ("2 · 한도 그리드", page_grid(llms, now)),
        ("3 · 스튜디오 (Herdr 실데이터)", page_studio(studio, False, row)),
    )
    for page, (title, buttons) in enumerate(pages, start=1):
        sheet({**buttons, **nav(page, 3, 80)}, title, args.output / f"p{page}.png", args.device)


if __name__ == "__main__":
    main()
