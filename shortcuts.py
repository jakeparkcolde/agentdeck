"""바로가기 페이지: config.json 에 적은 사이트·앱·파일을 열 칸에 열 개씩 (홈 바로가기로 들어가는 하위 페이지).

- 설정 (config.json, 모두 선택. 목록이 비면 홈에 칸이 생기지 않는다):
    "shortcuts": {"title": "포트폴리오",
                  "items": [{"name": "Example", "sub": "짧은 설명", "open": "https://example.com", "color": "#7aa2f7"}]}
  title: 홈 칸 이름과 넓은 칸 제목 (기본 '바로가기'). open: 주소·앱·파일 (present.open_target 으로 연다).
- 0~9번 칸: 한 칸에 하나, 짧게 누르면 연다. 넓은 칸: 누르면 다음 열 개 (끝나면 처음으로).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

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
                          accent=str(it.get("color") or ACCENT), raw_header=True)
        else:
            b[k] = Button(bg="#101118")
    return b


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
