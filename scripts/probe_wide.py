"""넓은 칸(3_2) 겹침 진단: 펌웨어 작은 창 모드를 고정하는 방법을 한 번 실행으로 차례로 시험한다.

D200H 는 넓은 칸을 누르면 펌웨어가 작은 창 모드(시계 · CPU …)를 바꿔 우리 이미지 위에 겹친다.
manifest 필드나 작은 창 명령 중 무엇이 "겹침 없음" 모드로 되돌리는지 찾기 위한 도구.

사용:
  1) 기기 USB 를 뽑았다 다시 꽂는다 (모드 초기화)
  2) python scripts/probe_wide.py [단계당 초=5]
  3) 실행 중 버튼을 누르지 말고, 넓은 칸에 뜬 단계 번호마다 겹침이 있는지 적는다
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import string
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont

import d200
from d200 import COLS, FONT_PATH, HEADER, PACKET, Button, render_icon

# (단계 설명, 3_2 manifest 에 넣을 추가 필드, 보낼 작은 창 모드 또는 None)
STEPS = [
    ("manifest default (Action='')", {"Action": ""}, None),
    ("manifest SmallViewMode=0", {"Action": "", "SmallViewMode": 0}, None),
    ("manifest SmallViewMode=1", {"Action": "", "SmallViewMode": 1}, None),
    ("manifest SmallViewMode=2", {"Action": "", "SmallViewMode": 2}, None),
    ("manifest State=1", {"Action": "", "State": 1}, None),
    ("manifest State=2", {"Action": "", "State": 2}, None),
    ("small window cmd mode=0", {"Action": ""}, 0),
    ("small window cmd mode=1", {"Action": ""}, 1),
    ("small window cmd mode=2", {"Action": ""}, 2),
]


def wide_png(step: int, desc: str, size: tuple[int, int]) -> bytes:
    img = Image.new("RGB", size, "#1a1b26")
    d = ImageDraw.Draw(img)
    w, h = size
    d.rectangle((10, 10, w - 11, h - 11), outline="#e0af68", width=3)
    d.text((w // 2, 80), f"STEP {step}", font=ImageFont.truetype(FONT_PATH, 54), fill="#ffffff", anchor="mm")
    d.text((w // 2, 140), desc, font=ImageFont.truetype(FONT_PATH, 20), fill="#a9b1d6", anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build(buttons: dict[int, Button], wide: bytes, extra: dict) -> bytes:
    """d200.build_zip 과 같지만 3_2 항목을 마음대로 만든다."""
    icons: dict[str, bytes] = {}
    manifest: dict[str, dict] = {}
    for idx, btn in buttons.items():
        png = render_icon(btn)
        name = f"Images/{hashlib.sha1(png).hexdigest()[:16]}.png"
        icons[name] = png
        manifest[f"{idx % COLS}_{idx // COLS}"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}]}
    name = f"Images/{hashlib.sha1(wide).hexdigest()[:16]}.png"
    icons[name] = wide
    manifest["3_2"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}], **extra}
    dummy = ""
    for _ in range(200):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            if dummy:
                z.writestr("dummy.txt", dummy)
            z.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            for n, data in icons.items():
                z.writestr(n, data)
        payload = buf.getvalue()
        if d200._payload_safe(payload):
            return payload
        dummy += "".join(random.choices(string.ascii_letters, k=8))
    raise RuntimeError("could not build firmware-safe zip")


def send_zip(deck: d200.D200, payload: bytes) -> None:
    packets = [d200._frame(d200.CMD_SET_BUTTONS, len(payload), payload[: PACKET - HEADER])]
    for off in range(PACKET - HEADER, len(payload), PACKET):
        packets.append(payload[off : off + PACKET].ljust(PACKET, b"\x00"))
    deck._write(packets)


def main() -> None:
    per_step = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    prof = d200.load_profile({"model": "d200h", "keepalive": "off", "wide_press_restore": False})
    deck = d200.D200(prof)
    deck.debug_raw = True
    deck.on_event = lambda ev: print(f"   (input {ev}) - please do not press during the probe", flush=True)
    deck.start()
    deck.set_brightness(80)
    buttons = {i: Button(title=str(i), bg="#1a1b26", accent="#7aa2f7") for i in range(d200.BUTTON_COUNT)}
    print(f"=== {len(STEPS)} steps x {per_step:.0f}s - note which STEP numbers show a clock/CPU overlay ===", flush=True)
    try:
        for i, (desc, extra, cmd_mode) in enumerate(STEPS, 1):
            print(f"{time.strftime('%H:%M:%S')} STEP {i}: {desc}", flush=True)
            send_zip(deck, build(buttons, wide_png(i, desc, prof.wide_size), extra))
            if cmd_mode is not None:
                deck.send(d200.CMD_SMALL_WINDOW, f"{cmd_mode}|0|0|{time.strftime('%H:%M:%S')}|0".encode())
            time.sleep(per_step)
    finally:
        deck.close()
        print("=== done ===", flush=True)


if __name__ == "__main__":
    main()
