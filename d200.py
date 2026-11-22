"""Ulanzi D200 / D200H 직접 제어 드라이버 (공식 앱 불필요).

프로토콜 출처: redphx/strmdck (MIT), jcalado/companion-surface-d200 (USBPcap 분석).
패킷: [7c 7c][cmd:2 BE][length:4 LE][data → 1024 바이트 패딩]

D200H 도 같은 VID/PID·같은 프로토콜을 쓴다(DEVICE_INFO 도 "D200" 으로 응답하므로 자동 구분 불가).
다만 펌웨어 동작이 조금 달라서 config.json 의 "device" 로 기기 프로파일을 고른다 (DeviceProfile 참고).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import random
import string
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Callable

import hid
from PIL import Image, ImageDraw, ImageFont

from i18n import t

VID, PID = 0x2207, 0x0019
STUCK_VID, STUCK_PID = 0x18D1, 0xD002  # 바로 꽂았을 때 가끔 이것만 잡힌다 (revive_stuck_device)
REVIVE_WAIT_S = 6
PACKET = 1024
HEADER = 8
COLS, ROWS = 5, 3
BUTTON_COUNT = 13  # 5 + 5 + 3, 마지막 줄 3_2~4_2 는 작은 창(458x196)
ICON = 196

CMD_SET_BUTTONS = 0x0001
CMD_SMALL_WINDOW = 0x0006
CMD_BRIGHTNESS = 0x000A
CMD_LABEL_STYLE = 0x000B
CMD_PARTIAL_UPDATE = 0x000D
SMALL_BACKGROUND = 2  # 작은 창 모드: 배경 이미지
IN_BUTTON = 0x0101
IN_DEVICE_INFO = 0x0303
USAGE_PAGE_CONSUMER = 0x0C  # 인터페이스 0 의 Consumer Control 컬렉션 = 화면·명령 채널


def _pick_font() -> str:
    """한글이 되는 시스템 폰트. macOS 가 먼저, 그다음 Windows · Linux."""
    candidates = (
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
        r"C:\Windows\Fonts\malgun.ttf",  # Windows (맑은 고딕)
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",  # Linux
    )
    return next((p for p in candidates if os.path.exists(p)), candidates[0])


FONT_PATH = _pick_font()  # 한글 지원


@dataclass(frozen=True)
class DeviceProfile:
    """기기마다 다른 펌웨어 동작.

    wide_size: 오른쪽 아래 넓은 칸(3_2) 이미지 크기. 다른 크기로 그린 이미지는 보내기 전에 맞춰 줄인다.
    keepalive: 앱 연결이 끊긴 걸로 판단하지 않게 주기적으로 보내는 것
               "small_window" = 작은 창 명령, "buttons" = 마지막 화면 전체 재전송, "off" = 안 보냄
    keepalive_sec: keepalive 간격(초). "buttons" 는 그 사이에 화면을 보냈으면 건너뛴다
    small_window_cmd: 작은 창 명령(CMD_SMALL_WINDOW)을 보낼지
    partial_updates: 부분 갱신(CMD_PARTIAL_UPDATE) 허용. D200H 는 엉뚱한 아이콘이 남는 문제가 보고되어 전체 갱신만 쓴다
    wide_action: 넓은 칸 manifest 의 "Action" 값. None 이면 넣지 않는다(D200 기존 동작)

    D200H 넓은 칸 실측 (Windows, 2026-11-21, scripts/probe_wide.py):
      - 펌웨어가 작은 창 모드를 따로 기억하고, 넓은 칸을 누를 때마다 스스로 바꾼다 (입력의 state 가 그 모드)
      - 작은 창 명령 mode 0 = CPU 겹침, 1 = 시계 겹침, 2 = 겹침 없음(우리 이미지만)
      - manifest 의 SmallViewMode · State · Action 은 겹침에 영향 없음
      → 넓은 칸 입력 직후 mode 2 를 다시 보내 되돌린다 (wide_press_restore)
    """

    model: str
    wide_size: tuple[int, int]
    keepalive: str
    keepalive_sec: float
    small_window_cmd: bool
    partial_updates: bool
    wide_action: str | None = None
    # 넓은 칸(13)의 누름/뗌이 data[3] 이 아니라 data[2] 에 오는 기기면 True.
    # D200H 실측(Windows): 누름 = [state, 0d, 01, 01], 뗌 = [state, 0d, 00, 01] — data[3] 은 항상 1
    wide_press_byte2: bool = False
    # 넓은 칸 manifest 의 "SmallViewMode" 값. None 이면 넣지 않는다.
    # D200 은 2(배경 이미지)를 넣어 왔다. D200H 는 이 값이 겹침에 영향 없어서 puritysb 의 manifest 모양대로 뺀다
    wide_view_mode: int | None = SMALL_BACKGROUND
    # True 면 넓은 칸 입력 직후 작은 창 명령을 다시 보내, 펌웨어가 누름으로 바꾼 모드(시계 · CPU)를 되돌린다
    wide_press_restore: bool = False


WIDE_INDEX = 13  # 넓은 칸(3_2) 버튼 번호
WIDE_RESTORE_DELAY = 0.08  # 넓은 칸 입력 뒤 되돌리기까지 (펌웨어가 모드 전환을 끝낼 틈)


# D200H: 392x196 넓은 칸 · 부분 갱신 금지 · Action="" 는 puritysb/AgentDeck 기록,
# 넓은 칸 입력 바이트 · 작은 창 모드 되돌리기는 실측. 다르면 config.json 의 device 항목으로 하나씩 덮어쓴다.
PROFILES: dict[str, DeviceProfile] = {
    "d200": DeviceProfile("d200", (458, 196), "small_window", 5, True, True),
    "d200h": DeviceProfile("d200h", (392, 196), "small_window", 5, True, False, wide_action="",
                           wide_press_byte2=True, wide_view_mode=None, wide_press_restore=True),
}
KEEPALIVE_MODES = ("small_window", "buttons", "off")


def load_profile(cfg: dict | None = None) -> DeviceProfile:
    """config.json 의 "device" 항목으로 프로파일을 만든다.

    예: {"model": "d200h"} / {"model": "d200h", "wide_width": 458, "keepalive": "off"}
    """
    from dataclasses import replace

    if cfg is None:
        from config import CFG

        cfg = CFG.get("device") or {}
    model = str(cfg.get("model", "d200")).lower()
    if model not in PROFILES:
        raise ValueError(t(f"알 수 없는 기기 모델: {model} (d200 또는 d200h)",
                           f"unknown device model: {model} (use d200 or d200h)"))
    prof = PROFILES[model]
    over: dict = {}
    if cfg.get("wide_width"):
        over["wide_size"] = (int(cfg["wide_width"]), prof.wide_size[1])
    if cfg.get("keepalive") is not None:
        if cfg["keepalive"] not in KEEPALIVE_MODES:
            raise ValueError(t(f"keepalive 값은 {KEEPALIVE_MODES} 중 하나여야 해요",
                               f"keepalive must be one of {KEEPALIVE_MODES}"))
        over["keepalive"] = cfg["keepalive"]
    if cfg.get("keepalive_sec"):
        over["keepalive_sec"] = float(cfg["keepalive_sec"])
    for key in ("small_window_cmd", "partial_updates", "wide_press_byte2", "wide_press_restore"):
        if cfg.get(key) is not None:
            over[key] = bool(cfg[key])
    if "wide_action" in cfg:  # null = 넣지 않음, "" = 기본 위젯 끄기
        over["wide_action"] = cfg["wide_action"]
    if "wide_view_mode" in cfg:  # null = 넣지 않음
        over["wide_view_mode"] = None if cfg["wide_view_mode"] is None else int(cfg["wide_view_mode"])
    return replace(prof, **over)


def parse_overrides(args: list[str]) -> dict:
    """테스트용 key=value 인자를 device 설정 dict 로. 예: model=d200h wide_width=458 wide_action=none"""
    out: dict = {}
    for arg in args:
        key, _, val = arg.partition("=")
        low = val.lower()
        if low in ("none", "null"):
            out[key] = None
        elif low in ("true", "false"):
            out[key] = low == "true"
        elif key == "wide_action":
            out[key] = "" if low in ("empty", '""', "''") else val
        elif val.replace(".", "", 1).isdigit():
            out[key] = float(val) if "." in val else int(val)
        else:
            out[key] = val
    return out


@dataclass
class InputEvent:
    index: int
    kind: str  # "button" | "encoder"
    action: str  # "press" | "release" | "left" | "right"
    state: int


@dataclass
class Button:
    """버튼 하나의 표시 내용."""

    title: str = ""
    sub: str = ""
    header: str = ""  # 상단 작은 라벨
    bg: str = "#1e1e2e"
    fg: str = "#ffffff"
    accent: str | None = None  # 하단 게이지 색
    ratio: float | None = None  # 0~1 게이지
    # 미니 게이지 여러 개: (라벨, 0~1, 색). 지정하면 sub/ratio 대신 하단에 줄 단위로 그린다
    bars: list[tuple] | None = None
    border: str | None = None  # 강조 테두리 색 (경고 등)
    header_fg: str | None = None  # 헤더 글자색 (없으면 accent)
    raw_header: bool = False
    image: bytes | None = None  # 미리 그린 196x196 PNG (어항 등). 있으면 그대로 보낸다  # 헤더가 사용자 데이터(종목·프로젝트·pane 이름)면 번역하지 않음
    fill: float | None = None  # 0~1: 아래에서부터 칸을 채우는 비율 (타이머)
    fill_color: str = "#f7768e"


def _fit(d: ImageDraw.ImageDraw, text: str, size: int, max_w: int) -> ImageFont.FreeTypeFont:
    """텍스트가 max_w 안에 들어갈 때까지 글자 크기를 줄인다."""
    while size > 12:
        f = ImageFont.truetype(FONT_PATH, size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return ImageFont.truetype(FONT_PATH, size)


def _render_bars(d: ImageDraw.ImageDraw, btn: Button) -> None:
    """헤더 + (큰 숫자) + 라벨 붙은 미니 게이지 여러 줄.

    bars 항목: (라벨, 0~1, 색) 또는 (라벨, 0~1, 색, 오른쪽 값 텍스트)
    title 이 비어 있으면 큰 숫자 없이 게이지를 위로 올려 3줄까지 여유 있게 그린다.
    """
    tiny = ImageFont.truetype(FONT_PATH, 22)
    lab = ImageFont.truetype(FONT_PATH, 19)
    if btn.header:
        d.text((ICON / 2, 24), btn.header, font=_fit(d, btn.header, 22, ICON - 20), fill=btn.accent or btn.fg, anchor="mm")
    if btn.title:
        big = ImageFont.truetype(FONT_PATH, 46 if len(btn.title) <= 4 else 34)
        d.text((ICON / 2, 70), btn.title, font=big, fill=btn.fg, anchor="mm")
        y, step = 112, 28
    else:
        y, step = 54, 36
    label_w = max((d.textlength(b[0], font=lab) for b in btn.bars or []), default=0)
    for bar in btn.bars or []:
        label, ratio, color = bar[:3]
        value = bar[3] if len(bar) > 3 else None
        d.text((12, y + 7), label, font=lab, fill="#a9b1d6", anchor="lm")
        x0, x1 = int(12 + label_w + 8), ICON - (52 if value else 14)
        d.rounded_rectangle((x0, y, x1, y + 14), 7, fill="#3a3a4a")
        if ratio is not None and ratio > 0:
            d.rounded_rectangle((x0, y, x0 + (x1 - x0) * min(1.0, ratio), y + 14), 7, fill=color)
        if value:
            d.text((ICON - 10, y + 7), value, font=lab, fill="#c0caf5", anchor="rm")
        y += step
    if btn.sub:
        d.text((ICON / 2, min(y + 10, ICON - 16)), btn.sub, font=lab, fill="#a9b1d6", anchor="mm")


def render_icon(btn: Button) -> bytes:
    """버튼 정보를 196x196 PNG 로 그린다. 문구는 화면 언어(i18n)에 맞춰 바꾼다."""
    from dataclasses import replace

    from i18n import tr

    if btn.image is not None:
        return btn.image
    btn = replace(btn, header=btn.header if btn.raw_header else tr(btn.header), title=tr(btn.title), sub=tr(btn.sub),
                  bars=[(tr(b[0]), *b[1:3], *(tr(x) for x in b[3:])) for b in btn.bars] if btn.bars else None)
    img = Image.new("RGB", (ICON, ICON), btn.bg)
    d = ImageDraw.Draw(img)
    if btn.fill is not None and btn.fill > 0:
        top = int(ICON * (1 - min(1.0, btn.fill)))
        d.rectangle((0, top, ICON, ICON), fill=btn.fill_color)
    if btn.border:
        d.rounded_rectangle((2, 2, ICON - 3, ICON - 3), 18, outline=btn.border, width=5)
    if btn.bars:
        _render_bars(d, btn)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    big = ImageFont.truetype(FONT_PATH, 52 if len(btn.title) <= 5 else 34)
    small = ImageFont.truetype(FONT_PATH, 24)
    tiny = ImageFont.truetype(FONT_PATH, 22)
    if btn.header:
        d.text((ICON / 2, 26), btn.header, font=_fit(d, btn.header, 22, ICON - 20), fill=btn.header_fg or btn.accent or btn.fg, anchor="mm")
    d.text((ICON / 2, 84), btn.title, font=_fit(d, btn.title, big.size, ICON - 16), fill=btn.fg, anchor="mm")
    if btn.sub:
        d.text((ICON / 2, 134), btn.sub, font=_fit(d, btn.sub, small.size, ICON - 14), fill="#a9b1d6" if btn.fg == "#ffffff" else btn.fg, anchor="mm")
    if btn.ratio is not None:
        r = max(0.0, min(1.0, btn.ratio))
        d.rounded_rectangle((16, 160, ICON - 16, 176), 8, fill="#3a3a4a")
        if r > 0:
            d.rounded_rectangle((16, 160, 16 + (ICON - 32) * r, 176), 8, fill=btn.accent or "#7aa2f7")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _frame(cmd: int, length: int, data: bytes) -> bytes:
    if len(data) > PACKET - HEADER:
        raise ValueError("data too large for one frame")
    head = b"\x7c\x7c" + cmd.to_bytes(2, "big") + length.to_bytes(4, "little")
    return (head + data).ljust(PACKET, b"\x00")


def _payload_safe(payload: bytes) -> bool:
    # 펌웨어 버그: 1016 + 1024*N 위치 바이트가 0x00/0x7c 이면 안 됨
    return all(payload[i] not in (0x00, 0x7C) for i in range(PACKET - HEADER, len(payload), PACKET))


def fit_wide(png: bytes, size: tuple[int, int]) -> bytes:
    """넓은 칸 이미지를 기기 크기에 맞춘다. 페이지들은 458x196 으로 그리므로 D200H(392x196)에서만 줄어든다."""
    img = Image.open(io.BytesIO(png))
    if img.size == size:
        return png
    buf = io.BytesIO()
    img.convert("RGB").resize(size, Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


def build_zip(buttons: dict[int, Button], small_png: bytes | None = None,
              wide_size: tuple[int, int] | None = None, wide_action: str | None = None,
              wide_view_mode: int | None = SMALL_BACKGROUND) -> bytes:
    """manifest.json + 아이콘 PNG 를 ZIP 으로 묶는다 (펌웨어 버그 회피 포함).

    small_png: 오른쪽 아래 넓은 칸(3_2) 배경 이미지. 주면 SmallViewMode 2 로 표시된다.
    wide_size: 주면 small_png 를 이 크기로 맞춘다 (기기 프로파일의 wide_size).
    wide_action: None 이 아니면 넓은 칸 manifest 에 "Action" 으로 넣는다 (DeviceProfile.wide_action).
    wide_view_mode: None 이 아니면 넓은 칸 manifest 에 "SmallViewMode" 로 넣는다 (DeviceProfile.wide_view_mode).
    """
    if small_png and wide_size:
        small_png = fit_wide(small_png, wide_size)
    icons: dict[str, bytes] = {}
    manifest: dict[str, dict] = {}
    for idx, btn in buttons.items():
        col, row = idx % COLS, idx // COLS
        png = render_icon(btn)
        # 장치가 파일명으로 아이콘을 캐시하므로 내용 해시를 이름에 넣어 바뀐 아이콘만 새로 그리게 한다
        name = f"Images/{hashlib.sha1(png).hexdigest()[:16]}.png"
        icons[name] = png
        manifest[f"{col}_{row}"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}]}
    if small_png:
        name = f"Images/{hashlib.sha1(small_png).hexdigest()[:16]}.png"
        icons[name] = small_png
        manifest["3_2"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}]}
        if wide_view_mode is not None:
            manifest["3_2"]["SmallViewMode"] = wide_view_mode
        if wide_action is not None:
            manifest["3_2"]["Action"] = wide_action

    dummy = ""
    for _ in range(200):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            if dummy:
                z.writestr("dummy.txt", dummy)
            z.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            for name, data in icons.items():
                z.writestr(name, data)
        payload = buf.getvalue()
        if _payload_safe(payload):
            return payload
        dummy += "".join(random.choices(string.ascii_letters, k=8))
    raise RuntimeError("could not build firmware-safe zip")


def _load_libusb():
    """libusb 를 찾으면 돌려준다 (없으면 None — 그때는 깨우기를 건너뛴다)."""
    import ctypes
    import ctypes.util

    for name in (ctypes.util.find_library("usb-1.0"), "/opt/homebrew/lib/libusb-1.0.dylib",
                 "/usr/local/lib/libusb-1.0.dylib", "libusb-1.0.so.0"):
        if not name:
            continue
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


# @MX:NOTE: 컴퓨터에 바로 꽂으면 D200 이 가끔 18d1:d002(응답 없는 디버그 모양 장치)로만 잡히고
#           진짜 HID(2207:0019)가 안 나온다. USB 리셋을 한 번 보내면 다시 인식되면서 HID 로 올라온다
#           (2026-11-18 맥북 직결에서 확인. 리셋 호출 자체는 장치가 사라져 NOT_FOUND 를 돌려준다).
def revive_stuck_device(lib=None) -> bool:
    """디버그 모양으로 멈춘 D200 에 USB 리셋을 보낸다. 리셋을 보냈으면 True."""
    import ctypes

    lib = lib or _load_libusb()
    if lib is None:
        return False
    lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
    lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
    lib.libusb_reset_device.argtypes = [ctypes.c_void_p]
    lib.libusb_close.argtypes = [ctypes.c_void_p]
    lib.libusb_exit.argtypes = [ctypes.c_void_p]
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)) != 0:
        return False
    try:
        handle = lib.libusb_open_device_with_vid_pid(ctx, STUCK_VID, STUCK_PID)
        if not handle:
            return False
        lib.libusb_reset_device(handle)
        lib.libusb_close(handle)
        return True
    finally:
        lib.libusb_exit(ctx)


def parse_button(data: bytes, profile: DeviceProfile) -> InputEvent:
    """버튼 입력 데이터 [state, index, kind/pressed, action] 를 이벤트로."""
    state, index = data[0], data[1]
    if profile.wide_press_byte2 and index >= WIDE_INDEX and data[2] in (0x00, 0x01):
        return InputEvent(index, "button", "press" if data[2] == 0x01 else "release", state)
    kind = "encoder" if data[2] == 0x02 else "button"
    action = {0x01: "press", 0x02: "left", 0x03: "right"}.get(data[3], "release")
    return InputEvent(index, kind, action, state)


def _find_hid(devices: list[dict] | None = None) -> dict | None:
    """화면·명령용 HID 항목을 고른다.

    인터페이스 0 이 화면·명령 채널이다. Windows hidapi 는 같은 인터페이스를 컬렉션(usage page)마다
    따로 보여주므로, 여러 개면 Consumer Control(0x0C) 컬렉션을 우선한다.
    """
    if devices is None:
        devices = hid.enumerate(VID, PID)
    cands = [d for d in devices if d.get("interface_number") == 0]
    if not cands:
        return None
    return next((d for d in cands if d.get("usage_page") == USAGE_PAGE_CONSUMER), cands[0])


def find_device_path(devices: list[dict] | None = None) -> bytes | None:
    """화면·명령용 HID 경로를 돌려준다."""
    info = _find_hid(devices)
    return info["path"] if info is not None else None


RECONNECT_POLL_SEC = 2.0  # 장치가 없을 때 다시 찾는 간격
RECONNECT_SETTLE_SEC = 1.0  # 다시 연 뒤 펌웨어가 준비될 때까지 기다렸다가 화면을 보낸다


def _log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class D200:
    """D200 / D200H 장치. profile 을 안 주면 config.json 의 "device" 항목으로 고른다.

    reconnect=False (기본): 장치가 없으면 RuntimeError, 끊기면 disconnected 를 켜고 멈춘다 (기존 동작).
    reconnect=True: 장치가 없어도 시작하고, 끊기면 기다렸다가 다시 연다. 다시 열면 밝기 · 작은 창 모드 ·
                    마지막 화면을 복원하고 on_connect 를 부른다. 케이블을 다른 컴퓨터로 옮겼다 돌아와도 이어서 쓴다.
                    끊겨 있는 동안의 화면 그리기는 건너뛴다.
    """

    def __init__(self, profile: DeviceProfile | None = None, reconnect: bool = False) -> None:
        self.profile = profile or load_profile()
        self.reconnect = reconnect
        self.dev = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_revive_at = 0.0
        self.disconnected = threading.Event()  # reconnect=False 에서만 켜진다 (멈춤 신호)
        self.connected = threading.Event()  # 지금 장치가 열려 있는지
        self._brightness: int | None = None  # 다시 연결할 때 복원
        self.on_connect: Callable[[], None] | None = None  # 읽기 스레드가 장치를 (다시) 열었을 때. 생성 때 바로 연 경우는 제외
        self.on_disconnect: Callable[[], None] | None = None
        if not self._open():
            if not reconnect:
                raise RuntimeError(t("D200 을 찾지 못했어요 (USB 연결과 Ulanzi Studio 종료를 확인하세요)",
                                     "D200 not found (check the USB cable and quit Ulanzi Studio)"))
            _log(t("장치를 기다리는 중이에요 (USB 연결과 Ulanzi Studio 종료를 확인하세요)",
                   "waiting for the device (check the USB cable and quit Ulanzi Studio)"))
        self._small = "1|0|0|00:00:00|0"
        self._last_buttons: list[bytes] | None = None  # keepalive "buttons" 용 마지막 화면 패킷
        self._last_write = 0.0
        self.debug_raw = False  # True 면 받은 입력 리포트를 16진수로 찍는다 (기기 분석용)
        self._restore_timer: threading.Timer | None = None
        self._restore_mode = SMALL_BACKGROUND  # 넓은 칸 입력 뒤 되돌릴 작은 창 모드 (set_small_window 가 갱신)
        self.on_event: Callable[[InputEvent], None] | None = None
        self.on_info: Callable[[str], None] | None = None

    # ---- 연결 ----
    def _open(self) -> bool:
        """장치를 찾아 연다. 없거나 열 수 없으면 False."""
        info = _find_hid()
        if info is None:
            can_revive = self._last_revive_at == 0.0 or time.monotonic() - self._last_revive_at >= 10.0
            if can_revive and revive_stuck_device():
                self._last_revive_at = time.monotonic()
                for _ in range(REVIVE_WAIT_S * 2):  # 리셋 뒤 HID 가 올라올 때까지 기다린다
                    if self._stop.is_set():
                        return False
                    time.sleep(0.5)
                    info = _find_hid()
                    if info is not None:
                        break
        if info is None or self._stop.is_set():
            return False
        dev = hid.device()
        try:
            dev.open_path(info["path"])
        except (OSError, ValueError):  # Ulanzi Studio 가 잡고 있거나 아직 준비 안 됨
            return False
        with self._lock:
            self.dev = dev
        self.connected.set()
        return True

    def _drop(self) -> None:
        """끊긴 장치를 닫는다 (읽기 스레드에서만 부른다)."""
        with self._lock:
            dev, self.dev = self.dev, None
        self.connected.clear()
        if dev is not None:
            try:
                dev.close()
            except (OSError, ValueError):
                pass

    def _restore_after_reconnect(self) -> None:
        """다시 연 장치에 밝기 · 작은 창 모드 · 마지막 화면을 다시 보낸다."""
        if self._stop.wait(RECONNECT_SETTLE_SEC):
            return
        if self._brightness is not None:
            self.set_brightness(self._brightness)
        if self.profile.small_window_cmd:
            self.send(CMD_SMALL_WINDOW, self._small.encode())
        self._resend_buttons()
        if self.on_connect:
            threading.Thread(target=self.on_connect, daemon=True).start()  # 읽기를 막지 않게

    # ---- 송신 ----
    def _write(self, packets: list[bytes], remember: bool = False) -> None:
        with self._lock:
            if remember:
                self._last_buttons = packets
            if self.dev is None:  # 끊겨 있음: 다시 연결되면 마지막 화면을 보낸다
                return
            try:
                for p in packets:
                    self.dev.write(b"\x00" + p)  # report ID 0 (번호 없는 리포트)
            except (OSError, ValueError):
                if not self.reconnect:
                    raise
                return  # 끊김은 _read_loop 가 알아채고 다시 연다
            self._last_write = time.monotonic()

    def send(self, cmd: int, data: bytes) -> None:
        self._write([_frame(cmd, len(data), data)])

    def set_brightness(self, percent: int) -> None:
        self._brightness = max(0, min(100, percent))
        self.send(CMD_BRIGHTNESS, str(self._brightness).encode())

    def set_small_window(self, mode: int = 1, cpu: int = 0, mem: int = 0, gpu: int = 0) -> None:
        """mode: 0=CPU/RAM/GPU 통계, 1=아날로그 시계, 200~203=디지털 시계.

        D200H 는 2 만 겹침 없이 우리 이미지를 보여 준다(0·1 은 CPU·시계를 겹쳐 그림).
        프로파일이 작은 창 명령을 막으면 모드만 기억하고 보내지 않는다.
        """
        self._small = f"{mode}|{cpu}|{mem}|{time.strftime('%H:%M:%S')}|{gpu}"
        self._restore_mode = mode
        if self.profile.small_window_cmd:
            self.send(CMD_SMALL_WINDOW, self._small.encode())

    def set_buttons(self, buttons: dict[int, Button], partial: bool = False, small_png: bytes | None = None) -> None:
        if self.dev is None and self.reconnect:
            return  # 끊겨 있는 동안은 그리지 않는다 (애니메이션이 헛도는 CPU 를 아낌). 다시 연결되면 on_connect 에서 새로 그림
        p = self.profile
        payload = build_zip(buttons, small_png, p.wide_size, p.wide_action, p.wide_view_mode)
        partial = partial and self.profile.partial_updates
        cmd = CMD_PARTIAL_UPDATE if partial else CMD_SET_BUTTONS
        first = payload[: PACKET - HEADER]
        packets = [_frame(cmd, len(payload), first)]
        for off in range(PACKET - HEADER, len(payload), PACKET):
            packets.append(payload[off : off + PACKET].ljust(PACKET, b"\x00"))
        # 부분 갱신은 화면 일부라서 재전송용으로 기억하지 않는다
        self._write(packets, remember=not partial)

    def _resend_buttons(self) -> None:
        # 기억한 패킷을 꺼내는 것과 보내는 것을 한 잠금 안에서 해서, 그 사이 새 화면이 나가도 옛 화면으로 덮지 않는다
        with self._lock:
            if not self._last_buttons or self.dev is None:
                return
            try:
                for p in self._last_buttons:
                    self.dev.write(b"\x00" + p)
            except (OSError, ValueError):
                if not self.reconnect:
                    raise
                return
            self._last_write = time.monotonic()

    # ---- 수신 ----
    def _parse(self, r: bytes) -> None:
        if len(r) < HEADER or r[0] != 0x7C or r[1] != 0x7C:
            return
        cmd = int.from_bytes(r[2:4], "big")
        length = int.from_bytes(r[4:8], "little")
        data = r[HEADER : HEADER + length]
        if self.debug_raw:
            print("RAW:", r[: HEADER + max(length, 0)][:32].hex(" "), flush=True)
        if cmd == IN_BUTTON and len(data) >= 4:
            ev = parse_button(data, self.profile)
            if self.profile.wide_press_restore and ev.kind == "button" and ev.index >= WIDE_INDEX:
                self._schedule_restore()
            if self.on_event:
                self.on_event(ev)
        elif cmd == IN_DEVICE_INFO and self.on_info:
            self.on_info(data.split(b"\x00")[0].decode("ascii", "replace"))

    def _schedule_restore(self) -> None:
        """넓은 칸 입력 직후 작은 창 모드를 되돌린다.

        펌웨어는 누르는 순간 시계·CPU 를 먼저 그리고 입력을 보내므로 잠깐 비치는 건 막을 수 없다.
        비치는 시간을 줄이려고 받자마자 한 번 보내고, 펌웨어 전환이 늦게 끝날 때를 대비해 조금 뒤 한 번 더 보낸다.
        """
        self._restore_now()
        if self._restore_timer:
            self._restore_timer.cancel()
        self._restore_timer = threading.Timer(WIDE_RESTORE_DELAY, self._restore_now)
        self._restore_timer.daemon = True
        self._restore_timer.start()

    def _restore_now(self) -> None:
        if self._stop.is_set():
            return
        try:
            # 마지막으로 정한 모드로 (dashboard 는 시작할 때 SMALL_BACKGROUND 로 정한다)
            self.set_small_window(self._restore_mode)
            if self.debug_raw:
                print(f"RESTORE: small window mode {self._restore_mode}", flush=True)
        except (OSError, ValueError):
            pass  # 끊김은 _read_loop 가 처리한다

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            dev = self.dev
            if dev is None:  # reconnect=True 에서만: 장치가 돌아올 때까지 찾는다
                if self._stop.wait(RECONNECT_POLL_SEC):
                    return
                if self._open():
                    _log(t("장치가 다시 연결됐어요", "device reconnected"))
                    self._restore_after_reconnect()
                continue
            try:
                r = dev.read(PACKET, 100)
            except (OSError, ValueError):
                self._drop()
                if not self.reconnect:
                    # 끊김 표시 후 종료 (상위에서 프로세스를 끝내면 launchd 가 재시작)
                    self.disconnected.set()
                    self._stop.set()
                    return
                _log(t("장치 연결이 끊겼어요. 다시 꽂히면 이어서 그려요",
                       "device disconnected; will resume when it is plugged back in"))
                if self.on_disconnect:
                    self.on_disconnect()
                continue
            if r:
                self._parse(bytes(r))

    def _keepalive_loop(self) -> None:
        # 앱 연결이 끊긴 걸로 판단하지 않도록 주기적으로 무언가를 보낸다 (방식은 프로파일에 따라)
        kind, every = self.profile.keepalive, self.profile.keepalive_sec
        if kind == "off":
            return
        while not self._stop.wait(every):
            try:
                if kind == "small_window":
                    self.set_small_window(int(self._small.split("|")[0]))
                elif time.monotonic() - self._last_write >= every:  # "buttons": 그 사이 화면을 안 보냈을 때만
                    self._resend_buttons()
            except (OSError, ValueError):
                return  # 끊김은 _read_loop 가 처리한다

    def start(self) -> None:
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        if self._restore_timer:
            self._restore_timer.cancel()
        time.sleep(0.2)
        self._drop()


if __name__ == "__main__":
    import sys

    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    # 두 번째 인자부터 모델·설정을 바로 고를 수 있다:
    #   python d200.py 60 d200h
    #   python d200.py 60 d200h wide_width=458 wide_action=none small_window_cmd=true
    #   python d200.py 120 d200h reconnect      ← 실행 중 케이블을 뽑았다 꽂아 재연결 확인
    extra = [a for a in sys.argv[2:] if a != "reconnect"]
    reconnect = "reconnect" in sys.argv[2:]
    over = parse_overrides([a if "=" in a else f"model={a}" for a in extra])
    deck = D200(load_profile(over) if extra else None, reconnect=reconnect)
    print("PROFILE:", deck.profile, "reconnect:", reconnect, flush=True)
    print("FONT:", FONT_PATH, flush=True)
    # 넓은 칸 확인용: 페이지들처럼 458x196 으로 그려 보내면 프로파일 크기로 맞춰진다.
    # 테두리 + 안쪽 눈금(가장자리에서 10·20px)으로 잘림 정도를 본다
    _wide = Image.new("RGB", (458, 196), "#24283b")
    _wd = ImageDraw.Draw(_wide)
    _wd.rectangle((0, 0, 457, 195), outline="#e0af68", width=4)
    _wd.rectangle((10, 10, 447, 185), outline="#f7768e", width=2)
    _wd.rectangle((20, 20, 437, 175), outline="#7dcfff", width=2)
    _act = "none" if deck.profile.wide_action is None else repr(deck.profile.wide_action)
    _wd.text((229, 80), f"3_2 → {deck.profile.wide_size[0]}x{deck.profile.wide_size[1]}",
             font=ImageFont.truetype(FONT_PATH, 32), fill="#ffffff", anchor="mm")
    _wd.text((229, 125), f"Action={_act} view={deck.profile.wide_view_mode} cmd={deck.profile.small_window_cmd}",
             font=ImageFont.truetype(FONT_PATH, 22), fill="#a9b1d6", anchor="mm")
    _wbuf = io.BytesIO()
    _wide.save(_wbuf, "PNG")
    wide_png = _wbuf.getvalue()
    palette = ["#f7768e", "#ff9e64", "#e0af68", "#9ece6a", "#7dcfff", "#7aa2f7", "#bb9af7"]
    buttons = {i: Button(title=f"{i}", sub="눌러봐", bg="#1a1b26", accent=palette[i % 7], ratio=i / 12) for i in range(BUTTON_COUNT)}
    counts = {i: 0 for i in range(BUTTON_COUNT)}

    def handle(ev: InputEvent) -> None:
        print(f"{time.strftime('%H:%M:%S')} {ev}", flush=True)
        if ev.kind == "button" and ev.action == "press" and ev.index in buttons:
            counts[ev.index] += 1
            buttons[ev.index] = Button(title=f"{ev.index}", sub=f"{counts[ev.index]}회", bg=palette[ev.index % 7], fg="#1a1b26")
            deck.set_buttons(buttons, small_png=wide_png)

    deck.on_event = handle
    deck.on_info = lambda s: print("DEVICE INFO:", s, flush=True)
    deck.on_connect = lambda: deck.set_buttons(buttons, small_png=wide_png)  # 장치 없이 시작했을 때를 위해 새로 그림
    deck.debug_raw = True
    deck.start()
    deck.set_brightness(80)
    deck.set_buttons(buttons, small_png=wide_png)
    deck.set_small_window(mode=SMALL_BACKGROUND)  # dashboard 와 같은 모드. D200H 프로파일도 모드 2 를 보낸다
    print(f"=== listening for button events for {duration:.0f}s ===", flush=True)
    try:
        time.sleep(duration)
    finally:
        deck.close()
        print("=== done ===", flush=True)
