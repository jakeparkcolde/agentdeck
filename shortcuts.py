"""바로가기 페이지: config.json 에 적은 사이트·앱·파일을 열 칸에 열 개씩 (홈 바로가기로 들어가는 하위 페이지).

- 설정 (config.json, 모두 선택. 목록이 비면 홈에 칸이 생기지 않는다):
    "shortcuts": {"title": "포트폴리오",
                  "items": [{"name": "Example", "sub": "짧은 설명", "open": "https://example.com", "color": "#7aa2f7"}]}
  title: 홈 칸 이름과 넓은 칸 제목 (기본 '바로가기'). open: 주소·앱·파일 (present.open_target 으로 연다).
- 0~9번 칸: 한 칸에 하나, 짧게 누르면 연다. 넓은 칸: 누르면 다음 열 개 (끝나면 처음으로).
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont

from config import CFG
from d200 import Button
import present

PER_PAGE = 10
BG = "#16161e"
ACCENT, DIM, YELLOW = "#7dcfff", "#565f89", "#e0af68"
DEFAULT_TITLE = "바로가기"


def _cfg(cfg: dict | None = None) -> dict:
    c = (CFG if cfg is None else cfg).get("shortcuts")
    return c if isinstance(c, dict) else {}


def items(cfg: dict | None = None) -> list[dict]:
    """열 수 있는 항목만 (이름과 open 이 있는 것)."""
    raw = _cfg(cfg).get("items") or []
    return [it for it in raw if isinstance(it, dict) and it.get("open")] if isinstance(raw, list) else []


def title(cfg: dict | None = None) -> str:
    return str(_cfg(cfg).get("title") or DEFAULT_TITLE)


def home_item(page: int, cfg: dict | None = None) -> tuple[int, str, str, str, str] | None:
    """홈 바로가기 칸 (페이지, 이름, 설명, 아이콘, 색). 항목이 없으면 None."""
    its = items(cfg)
    return (page, title(cfg), f"{len(its)}곳", "sparkle", ACCENT) if its else None


def sub_of(item: dict) -> str:
    """아래 줄: 적은 설명, 없으면 주소의 호스트, 앱·파일이면 이름."""
    if item.get("sub"):
        return str(item["sub"])
    target = str(item.get("open", ""))
    host = urlparse(target).hostname if "://" in target else None
    if host:
        return host[4:] if host.startswith("www.") else host
    return Path(target).stem


@dataclass
class State:
    page: int = 0  # 몇 번째 열 개 묶음
    msg: tuple[str, str] | None = None  # 넓은 칸 마지막 메시지 (제목, 내용)

    def pages(self, n: int) -> int:
        return max(1, (n + PER_PAGE - 1) // PER_PAGE)

    def first(self, n: int) -> int:
        return (self.page % self.pages(n)) * PER_PAGE


STATE = State()


# ---------- 칸 그리기: 어두운 카드 + 항목 색 띠·배지 + 이름(크게)·설명(작게) ----------
PALETTE = ("#7aa2f7", "#bb9af7", "#7dcfff", "#9ece6a", "#e0af68", "#ff9e64", "#f7768e", "#73daca")
ICON = 196
CARD, CARD_BOTTOM, NAME_FG, SUB_FG, NUM_FG = "#1f2335", "#16161e", "#eef1ff", "#9aa5ce", "#565f89"
EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
PAD = 18


def color_of(item: dict) -> str:
    """config 의 color, 없으면 이름의 고정 해시로 팔레트에서 고른다 (같은 이름 = 늘 같은 색)."""
    c = str(item.get("color") or "").strip()
    if c:
        return c
    return PALETTE[zlib.crc32(str(item.get("name", "")).encode("utf-8")) % len(PALETTE)]


def _rgb(c: str) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(c)[:3]
    except ValueError:
        return ImageColor.getrgb(ACCENT)[:3]


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _is_latin(s: str) -> bool:
    return all(ord(ch) < 0x0370 for ch in s)


def _name_font(text: str, size: int):
    from clock import _kr, _sf  # noqa: PLC0415

    return _sf(size, b"Bold") if _is_latin(text) else _kr(size, 6)


def _split2(text: str) -> tuple[str, str]:
    """두 줄로 나눈다: 가운데에 가장 가까운 띄어쓰기, 없으면 글자 수 반."""
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if spaces:
        i = min(spaces, key=lambda i: abs(i - len(text) / 2))
        return text[:i], text[i + 1:]
    h = (len(text) + 1) // 2
    return text[:h], text[h:]


def _fit_name(d: ImageDraw.ImageDraw, text: str, max_w: int) -> tuple[list[str], object]:
    """한 줄로 34→26, 안 되면 두 줄로 28→20, 그래도 넘치면 더 줄인다."""
    for size in range(34, 25, -2):
        f = _name_font(text, size)
        if d.textlength(text, font=f) <= max_w:
            return [text], f
    lines = list(_split2(text))
    for size in range(28, 11, -2):
        f = _name_font(text, size)
        if all(d.textlength(ln, font=f) <= max_w for ln in lines):
            return lines, f
    return lines, f


def _ellipsize(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _emoji(ch: str, size: int) -> Image.Image | None:
    """Apple Color Emoji 는 정해진 크기(64 등)로만 그려져서 그려 놓고 줄인다. 실패하면 None."""
    try:
        f = ImageFont.truetype(EMOJI_FONT, 64)
        im = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((48, 48), ch, font=f, embedded_color=True, anchor="mm")
        box = im.getbbox()
        if not box:
            return None
        im = im.crop(box)
        scale = size / max(im.size)
        return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    except Exception:
        return None


def render_key(item: dict, number: int) -> bytes:
    """바로가기 칸 하나를 196x196 PNG 로 그린다 (사용자 데이터라 번역하지 않음)."""
    from clock import _kr, _sf  # noqa: PLC0415

    acc = _rgb(color_of(item))
    card, bottom = _rgb(CARD), _rgb(CARD_BOTTOM)
    s = 2  # 두 배로 그려서 줄이면 둥근 모서리가 매끈하다
    img = Image.new("RGB", (ICON * s, ICON * s), "#000000")
    # 카드: 위는 항목 색이 살짝 섞이고 아래로 갈수록 어두운 세로 그라데이션
    grad = Image.new("RGB", (1, ICON * s))
    top = _mix(card, acc, 0.30)
    for y in range(ICON * s):
        t = y / (ICON * s - 1)
        grad.putpixel((0, y), _mix(top, bottom, min(1.0, t * 1.15)))
    grad = grad.resize((ICON * s, ICON * s))
    mask = Image.new("L", img.size, 0)
    m = 4 * s
    ImageDraw.Draw(mask).rounded_rectangle((m, m, ICON * s - m - 1, ICON * s - m - 1), 26 * s, fill=255)
    img.paste(grad, (0, 0), mask)
    # 위쪽 얇은 색 띠 (카드 모서리 안쪽)
    band = Image.new("L", img.size, 0)
    ImageDraw.Draw(band).rectangle((0, 0, ICON * s, m + 5 * s), fill=255)
    img.paste(acc, (0, 0, *img.size), ImageChops.darker(mask, band))
    img = img.resize((ICON, ICON), Image.LANCZOS)
    d = ImageDraw.Draw(img)

    # 배지: 항목 색 동그라미 + 첫 글자(또는 emoji)
    name = str(item.get("name", "")).strip()
    bx, by, br = PAD, 22, 40
    emoji = str(item.get("emoji") or "").strip()
    pic = _emoji(emoji, 26) if emoji else None
    if pic is not None:
        d.ellipse((bx, by, bx + br, by + br), fill=_mix(card, acc, 0.35))
        img.paste(pic, (bx + (br - pic.width) // 2, by + (br - pic.height) // 2), pic)
    else:
        d.ellipse((bx, by, bx + br, by + br), fill=acc)
        letter = (name[:1] or "?").upper()
        f = _sf(24, b"Heavy") if _is_latin(letter) else _kr(21, 6)
        d.text((bx + br / 2, by + br / 2 + 1), letter, font=f, fill=CARD_BOTTOM, anchor="mm")
    # 칸 번호 (오른쪽 위, 흐리게)
    d.text((ICON - PAD, by + br / 2), str(number), font=_sf(20, b"Semibold"), fill=NUM_FG, anchor="rm")

    # 이름: 왼쪽 정렬, 크게 굵게 (길면 줄이고, 그래도 길면 두 줄)
    max_w = ICON - 2 * PAD
    lines, f = _fit_name(d, name, max_w)
    lines = [_ellipsize(d, ln, f, max_w) for ln in lines]
    lh = f.size * 1.18
    name_bottom = 140
    y0 = name_bottom - lh * (len(lines) - 1)
    for i, ln in enumerate(lines):
        d.text((PAD, y0 + lh * i), ln, font=f, fill=NAME_FG, anchor="ls")

    # 설명: 작게, 가볍게, 넘치면 줄이고 … 로 자른다
    sub = sub_of(item)
    if sub:
        sf = _kr(18, 2)
        for size in (18, 17, 16, 15):
            sf = _kr(size, 2)
            if d.textlength(sub, font=sf) <= max_w:
                break
        d.text((PAD, 172), _ellipsize(d, sub, sf, max_w), font=sf, fill=SUB_FG, anchor="ls")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def page(st: State | None = None, its: list[dict] | None = None) -> dict[int, Button]:
    st = st or STATE
    its = items() if its is None else its
    first = st.first(len(its))
    b: dict[int, Button] = {}
    for k in range(PER_PAGE):
        i = first + k
        if i < len(its):
            it = its[i]
            b[k] = Button(header=str(k + 1), title=str(it.get("name", "")), sub=sub_of(it), bg=BG,
                          accent=color_of(it), raw_header=True, image=render_key(it, k + 1))
        else:
            b[k] = Button(bg="#101118", image=_empty_key())
    return b


def _empty_key() -> bytes:
    """빈 칸: 검은 바탕에 아주 흐린 카드 테두리만 (채운 칸과 모양을 맞춘다)."""
    s = 2
    img = Image.new("RGB", (ICON * s, ICON * s), "#000000")
    ImageDraw.Draw(img).rounded_rectangle((4 * s, 4 * s, ICON * s - 4 * s - 1, ICON * s - 4 * s - 1), 26 * s,
                                          fill="#0b0c11", outline="#1a1b26", width=2 * s)
    buf = io.BytesIO()
    img.resize((ICON, ICON), Image.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


def wide(st: State | None = None, its: list[dict] | None = None, cfg: dict | None = None) -> bytes:
    """넓은 칸: 제목 + 몇 번째 묶음(n/m) + 마지막 메시지."""
    from clock import H, W, _Draw, _kr, _sf  # noqa: PLC0415  (시계 그리기 도구를 같이 씀)

    st = st or STATE
    its = items(cfg) if its is None else its
    img = Image.new("RGB", (W, H), "#0d0e14")
    d = _Draw(img)
    d.text((24, 50), title(cfg), font=_kr(34, 6), fill=ACCENT, anchor="lm")
    n = st.pages(len(its))
    if n > 1:
        d.text((W - 22, 50), f"{st.page % n + 1}/{n}", font=_sf(34), fill="#c0caf5", anchor="rm")
    d.text((24, 100), f"{len(its)}곳", font=_kr(22, 4), fill=DIM, anchor="lm")
    if st.msg:
        d.text((24, 140), st.msg[0], font=_kr(24, 6), fill=YELLOW, anchor="lm")
        d.text((24, 172), st.msg[1][:34], font=_kr(19, 2), fill="#c0caf5", anchor="lm")
    else:
        hint = "칸을 누르면 열려요" + (" · 여기: 다음 묶음" if n > 1 else "")
        d.text((24, 172), hint, font=_kr(19, 2), fill=DIM, anchor="lm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def press(idx: int, st: State | None = None, its: list[dict] | None = None) -> bool:
    """바로가기 페이지 칸 누르기. 화면을 다시 그려야 하면 True."""
    st = st or STATE
    its = items() if its is None else its
    if idx >= 13:  # 넓은 칸: 다음 열 개 (열 개 이하면 아무 일 없음)
        n = st.pages(len(its))
        if n <= 1:
            return False
        st.page, st.msg = (st.page % n + 1) % n, None
        return True
    if idx >= PER_PAGE:
        return False
    i = st.first(len(its)) + idx
    if i >= len(its):
        return False  # 빈 칸
    target = str(its[i].get("open", ""))
    if present.open_target(target):
        st.msg = ("열었어요", str(its[i].get("name", "")) or target)
    else:
        st.msg = ("열지 못했어요", target)
    return True
