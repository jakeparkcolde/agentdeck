"""발표 모드: 슬라이드 넘기기 · 발표 타이머 · 화면 가리기 · 강의 녹음 · 중요/질문 표시 · 시연 바로가기.

- 슬라이드: 맨 앞에 떠 있는 앱(키노트, 파워포인트, PDF, 구글 슬라이드 발표 화면 등)에 → / ← / B 키를 보낸다.
  처음 한 번 macOS 가 "손쉬운 사용" 권한을 묻는다 (시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용).
- 녹음: 이 맥의 마이크로 m4a 를 만든다 (ffmpeg). 끝내면 agentnote CLI 가 있으면 자동으로 올려 강의 노트를 만든다.
  중요(★)·질문(Q) 표시는 녹음 시각으로 회의록 메모에 붙는다.
- 설정 (config.json, 모두 선택):
    "presentation": {"mic": "AU05", "save_dir": "~/Movies/agentdeck",
                     "demos": [{"name": "데모 사이트", "open": "https://example.com"}, {"name": "터미널", "open": "/System/Applications/Utilities/Terminal.app"}]}
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import CFG
from d200 import Button

CFG_P: dict = CFG.get("presentation") or {}
TARGETS = [10, 15, 20, 30, 45, 60, 90]  # 목표 시간(분), 칸을 누를 때마다 다음 값
BG = "#16161e"
GREEN, YELLOW, RED, BLUE, DIM = "#9ece6a", "#e0af68", "#f7768e", "#7aa2f7", "#565f89"
WARN_SEC = 5 * 60  # 남은 시간이 이만큼이면 노랑
KEYS = {"next": "key code 124", "prev": "key code 123", "blank": 'keystroke "b"'}


def clock(sec: float) -> str:
    s = int(max(0, sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------- 키 보내기 · 앱 열기 ----------
def send_key(action: str) -> bool:
    """맨 앞 앱에 키 하나. 손쉬운 사용 권한이 없으면 False."""
    try:
        r = subprocess.run(["osascript", "-e", f'tell application "System Events" to {KEYS[action]}'],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, KeyError):
        return False


def front_app() -> str:
    try:
        r = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first application process '
                            'whose frontmost is true'], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def open_target(target: str) -> bool:
    """시연 바로가기: 주소·앱·파일을 연다 (open 명령). 설정 파일에 사용자가 직접 적은 것만 연다."""
    if not target:
        return False
    try:
        return subprocess.run(["open", os.path.expanduser(target)], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------- 녹음 ----------
def _ffmpeg() -> str | None:
    for p in (shutil.which("ffmpeg"), os.path.expanduser("~/.local/bin/ffmpeg"), "/opt/homebrew/bin/ffmpeg",
              "/usr/local/bin/ffmpeg"):
        if p and os.path.exists(p):
            return p
    return None


def parse_audio_devices(listing: str) -> list[tuple[int, str]]:
    """ffmpeg -list_devices 출력에서 오디오 입력 (번호, 이름)."""
    out, audio = [], False
    for line in listing.splitlines():
        if "audio devices" in line.lower():
            audio = True
            continue
        if "video devices" in line.lower():
            audio = False
        m = re.search(r"\[(\d+)\]\s+(.+)$", line)
        if audio and m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def pick_device(devices: list[tuple[int, str]], want: str | None) -> int | None:
    """설정한 이름이 들어간 마이크 > 맥 내장 마이크 > 첫 번째. (아이폰 연속성 마이크는 기본으로 고르지 않음)"""
    if not devices:
        return None
    if want:
        for i, name in devices:
            if want.lower() in name.lower():
                return i
    for i, name in devices:
        if re.search(r"macbook|built-in|내장", name, re.I):
            return i
    for i, name in devices:
        if not re.search(r"iphone|ipad", name, re.I):
            return i
    return devices[0][0]


class Recorder:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.path: Path | None = None
        self.started = 0.0
        self.error = ""

    def start(self, title: str) -> bool:
        ff = _ffmpeg()
        if not ff:
            self.error = "ffmpeg 없음"
            return False
        listing = subprocess.run([ff, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                                 capture_output=True, text=True, timeout=10).stderr
        dev = pick_device(parse_audio_devices(listing), CFG_P.get("mic"))
        if dev is None:
            self.error = "마이크 없음"
            return False
        folder = Path(os.path.expanduser(CFG_P.get("save_dir") or "~/Movies/agentdeck"))
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{title}.m4a"
        self.proc = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", f":{dev}",
                                      "-ac", "1", "-c:a", "aac", "-b:a", "96k", "-y", str(self.path)],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.started = time.time()
        time.sleep(0.5)
        if self.proc.poll() is not None:  # 바로 끝남 = 마이크 권한 없음 등
            self.error = (self.proc.stderr.read() or b"").decode(errors="replace")[-80:] or "녹음 실패"
            self.proc = None
            return False
        return True

    @property
    def on(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def elapsed(self) -> float:
        return time.time() - self.started if self.on else 0.0

    def stop(self) -> Path | None:
        if not self.proc:
            return None
        try:
            self.proc.stdin.write(b"q")  # ffmpeg 를 정상 종료해야 m4a 가 닫힌다
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.proc.terminate()
        self.proc = None
        return self.path if self.path and self.path.exists() and self.path.stat().st_size > 1000 else None


def agentnote_cli() -> str | None:
    for p in (shutil.which("agentnote"), os.path.expanduser("~/.local/bin/agentnote")):
        if p and os.path.exists(p):
            return p
    return None


def upload(path: Path, title: str, notes: str | None) -> tuple[bool, str]:
    """agentnote 로 올린다 (회의록은 서버가 만든다). CLI 가 없으면 파일만 남긴다."""
    cli = agentnote_cli()
    if not cli:
        return False, "agentnote 없음 · 파일만 저장"
    cmd = [cli, "upload", str(path), "--title", title]
    if notes:
        cmd += ["--notes", notes]
    env = dict(os.environ, PATH=f"{os.path.expanduser('~/.local/bin')}:/opt/homebrew/bin:{os.environ.get('PATH', '')}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)[:40]
    if r.returncode:
        return False, (r.stderr.strip().splitlines() or ["올리기 실패"])[-1][:60]
    m = re.search(r"#(\d+)", r.stdout)
    return True, f"#{m.group(1)} 올림 · 강의 노트 만드는 중" if m else "올림"


# ---------- 상태 ----------
@dataclass
class Presenter:
    target_min: int = 30
    state: str = "idle"  # idle | running | paused
    started: float = 0.0  # running 일 때 '경과 0' 이 되는 시각
    paused_at: float = 0.0  # paused 일 때 경과
    slide: int = 1
    blank: bool = False
    marks: list[float] = field(default_factory=list)  # 녹음 기준 초
    questions: list[float] = field(default_factory=list)
    rec: Recorder = field(default_factory=Recorder)
    msg: tuple[str, str, float] | None = None  # (제목, 내용, 사라지는 시각)

    # 타이머
    def elapsed(self, now: float | None = None) -> float:
        now = now or time.time()
        return now - self.started if self.state == "running" else (self.paused_at if self.state == "paused" else 0.0)

    def remaining(self, now: float | None = None) -> float:
        return self.target_min * 60 - self.elapsed(now)

    def color(self, now: float | None = None) -> str:
        if self.state == "idle":
            return DIM
        r = self.remaining(now)
        return RED if r < 0 else (YELLOW if r <= WARN_SEC else GREEN)

    def toggle_timer(self) -> None:
        now = time.time()
        if self.state == "running":
            self.state, self.paused_at = "paused", now - self.started
        else:
            self.state, self.started = "running", now - (self.paused_at if self.state == "paused" else 0.0)

    def reset_timer(self) -> None:
        self.state, self.started, self.paused_at, self.slide = "idle", 0.0, 0.0, 1

    def next_target(self) -> None:
        i = TARGETS.index(self.target_min) if self.target_min in TARGETS else -1
        self.target_min = TARGETS[(i + 1) % len(TARGETS)]

    def active(self) -> bool:
        """발표 중 (화면보호기로 넘어가지 않게)."""
        return self.state != "idle" or self.rec.on

    def say(self, title: str, text: str, sec: float = 4) -> None:
        self.msg = (title, text, time.time() + sec)

    # 표시
    def notes(self) -> str | None:
        parts = []
        if self.marks:
            parts.append("★ " + ", ".join(clock(m) for m in self.marks))
        if self.questions:
            parts.append("Q " + ", ".join(clock(q) for q in self.questions))
        return "\n".join(parts) or None


# ---------- 화면 ----------
def page(p: Presenter, demos: list[dict] | None = None) -> dict[int, Button]:
    demos = demos if demos is not None else (CFG_P.get("demos") or [])
    now = time.time()
    b: dict[int, Button] = {}
    b[0] = Button(header="이전", title="◀", sub=f"슬라이드 {p.slide}", bg=BG, accent=BLUE)
    b[1] = Button(header="다음", title="▶", sub=f"슬라이드 {p.slide}", bg=BG, accent=BLUE)
    b[2] = Button(header="화면", title="가림" if p.blank else "가리기", sub="한 번 더: 되돌리기" if p.blank else "검은 화면 (B)",
                  bg="#000000" if p.blank else BG, accent=YELLOW if p.blank else DIM, border=YELLOW if p.blank else None)
    if p.rec.on:
        b[3] = Button(header="● 녹음 중", title=clock(p.rec.elapsed()), sub="누르면 끝 · 올리기", bg=RED, fg="#16161e",
                      accent="#16161e")
        b[4] = Button(header="중요", title=f"★ {len(p.marks)}", sub="지금 시점 표시", bg=BG, accent=YELLOW)
        b[7] = Button(header="질문", title=f"Q {len(p.questions)}", sub="질문 시점 표시", bg=BG, accent=BLUE)
    else:
        b[3] = Button(header="강의 녹음", title="● 녹음", sub="끝나면 강의 노트로", bg=BG, accent=RED)
        b[4] = Button(header="중요", title="★", sub="녹음 중에 사용", bg=BG, accent=DIM)
        b[7] = Button(header="질문", title="Q", sub="녹음 중에 사용", bg=BG, accent=DIM)
    col = p.color(now)
    title = {"idle": "시작", "running": clock(p.elapsed(now)), "paused": "일시정지"}[p.state]
    sub = {"idle": "발표 타이머", "running": "짧게: 멈춤 · 길게: 초기화", "paused": "짧게: 계속 · 길게: 초기화"}[p.state]
    over = p.state != "idle" and p.remaining(now) < 0
    b[5] = Button(header="타이머", title=title, sub=sub, bg=RED if over else BG, fg="#16161e" if over else "#ffffff",
                  accent="#16161e" if over else col)
    b[6] = Button(header="목표 시간", title=f"{p.target_min}분", sub="누를 때마다 바꾸기", bg=BG, accent=DIM)
    for k, idx in enumerate((8, 9)):
        if k < len(demos):
            b[idx] = Button(header=f"시연 {k + 1}", title=str(demos[k].get("name", ""))[:8],
                            sub="눌러서 열기", bg=BG, accent="#bb9af7")
        else:
            b[idx] = Button(header=f"시연 {k + 1}", title="-", sub="config: presentation.demos", bg=BG, accent=DIM)
    return b


def wide(p: Presenter, app: str = "") -> bytes:
    """넓은 칸: 경과 시간 크게 + 남은 시간·목표 + 녹음 상태."""
    from clock import H, W, _Draw, _kr, _sf  # noqa: PLC0415  (시계 그리기 도구를 같이 씀)

    now = time.time()
    img = Image.new("RGB", (W, H), "#0d0e14")
    d = _Draw(img)
    if p.msg and now < p.msg[2]:
        d.text((24, 60), p.msg[0], font=_kr(34, 6), fill=YELLOW, anchor="lm")
        d.text((24, 120), p.msg[1], font=_kr(24, 2), fill="#c0caf5", anchor="lm")
    else:
        col = p.color(now)
        d.text((24, 92), clock(p.elapsed(now)), font=_sf(96), fill="#e6e9f5" if p.state != "idle" else DIM, anchor="lm")
        rx = W - 22
        r = p.remaining(now)
        d.text((rx, 52), f"목표 {p.target_min}분", font=_kr(24, 4), fill=DIM, anchor="ra")
        if p.state != "idle":
            d.text((rx, 96), ("초과 " if r < 0 else "남음 ") + clock(abs(r)), font=_kr(30, 6), fill=col, anchor="ra")
        line = (f"● 녹음 {clock(p.rec.elapsed())} · ★{len(p.marks)} · Q{len(p.questions)}" if p.rec.on else "녹음 꺼짐")
        d.text((24, 170), line + (f" · {app}" if app else ""), font=_kr(19, 2), fill=RED if p.rec.on else DIM, anchor="lm")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------- 누르기 ----------
def press(p: Presenter, idx: int, held: float, long_press: float, demos: list[dict] | None = None,
          on_uploaded=None) -> None:
    """발표 페이지 칸 누르기. on_uploaded(ok, 문구) 는 녹음 올리기가 끝나면 (다른 스레드에서) 불린다."""
    demos = demos if demos is not None else (CFG_P.get("demos") or [])
    if idx in (0, 1):
        if send_key("prev" if idx == 0 else "next"):
            p.slide = max(1, p.slide + (1 if idx == 1 else -1))
        else:
            p.say("키를 못 보냈어요", "손쉬운 사용 권한을 확인하세요")
    elif idx == 2:
        if send_key("blank"):
            p.blank = not p.blank
    elif idx == 3:
        if p.rec.on:
            stop_and_upload(p, on_uploaded)
        else:
            title = "강의 " + datetime.now().strftime("%Y-%m-%d %H%M")  # i18n: skip (파일·녹음 이름)
            p.marks, p.questions = [], []
            if p.rec.start(title):
                p.say("녹음 시작", "★ 중요 · Q 질문 표시를 쓸 수 있어요")
            else:
                p.say("녹음 못 함", p.rec.error[:30] or "마이크 권한을 확인하세요")
    elif idx == 4 and p.rec.on:
        p.marks.append(p.rec.elapsed())
        p.say("★ 중요 표시", clock(p.rec.elapsed()), 2)
    elif idx == 7 and p.rec.on:
        p.questions.append(p.rec.elapsed())
        p.say("Q 질문 표시", clock(p.rec.elapsed()), 2)
    elif idx == 5 or idx >= 13:
        if held >= long_press:
            p.reset_timer()
        else:
            p.toggle_timer()
    elif idx == 6:
        p.next_target()
    elif idx in (8, 9) and idx - 8 < len(demos):
        if not open_target(str(demos[idx - 8].get("open", ""))):
            p.say("열지 못했어요", str(demos[idx - 8].get("open", ""))[:30])


def stop_and_upload(p: Presenter, on_uploaded=None) -> None:
    path, title, notes = p.rec.stop(), p.rec.path.stem if p.rec.path else "강의", p.notes()  # i18n: skip
    if not path:
        p.say("녹음 파일이 없어요", "마이크 권한을 확인하세요")
        return
    p.say("녹음 끝", "agentnote 로 올리는 중…", 30)

    def run() -> None:
        ok, text = upload(path, title, notes)
        p.say("강의 노트" if ok else "올리기 못 함", text, 8)
        if on_uploaded:
            on_uploaded(ok, text)

    threading.Thread(target=run, daemon=True).start()
