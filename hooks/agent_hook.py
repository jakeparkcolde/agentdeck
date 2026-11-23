#!/usr/bin/env python3
"""에이전트 상태 훅: Claude Code·Codex 가 어디서 돌든(VS Code, Cursor, iTerm, Ghostty, 터미널, Herdr…)
세션 상태를 ~/.cache/d200/agents/<id>.json 으로 남긴다. 대시보드가 이 파일들을 읽는다.

표준 라이브러리만 쓴다 (시스템 python3 로 실행).

Claude Code (~/.claude/settings.json 의 hooks, stdin 으로 JSON 이 들어온다)
  python3 agent_hook.py claude
    SessionStart                 → idle
    UserPromptSubmit / Pre·PostToolUse → working
    PermissionRequest            → blocked (결정 필요)
    Notification(권한 관련)       → blocked
    Stop                         → done (완료, 아직 안 봄)
    StopFailure                  → error
    SessionEnd                   → 파일 삭제

Codex (~/.codex/config.toml 의 notify, 마지막 인자로 JSON 이 들어온다)
  notify = ["python3", "/path/agent_hook.py", "codex", "--", <기존 notify 명령...>]
    agent-turn-complete          → done
  `--` 뒤에 기존 notify 명령이 있으면 같은 인자로 이어서 실행한다 (기존 연동 유지).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

DIR = os.path.expanduser("~/.cache/d200/agents")

CLAUDE_STATUS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "PostToolUseFailure": "working",
    "PermissionRequest": "blocked",
    "Stop": "done",
    "StopFailure": "error",
}


IS_WINDOWS = sys.platform == "win32"
# Windows: 훅은 Git Bash · cmd 같은 셸을 거쳐 실행되므로 부모 프로세스가 곧 사라지는 셸이다.
# 셸을 건너뛰며 위로 올라가 진짜 에이전트(claude · node · codex) 와 그 창을 가진 앱을 찾는다
_WIN_PASS = {"bash.exe", "sh.exe", "dash.exe", "env.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
             "python.exe", "pythonw.exe", "py.exe", "uv.exe", "uvx.exe"}
_WIN_APPS = {"windowsterminal.exe": "terminal", "code.exe": "code", "code - insiders.exe": "code",
             "cursor.exe": "cursor", "windsurf.exe": "windsurf", "wezterm-gui.exe": "wezterm",
             "alacritty.exe": "alacritty", "idea64.exe": "idea", "pycharm64.exe": "pycharm"}
# 목록에 없는 앱은 '보이는 창을 가진 첫 조상' 으로 찾는다. 이 시스템 프로세스에 닿으면 멈춘다
# (탐색기도 창이 있지만 거기서 띄운 앱의 창이 아니다)
_WIN_STOP = {"explorer.exe", "sihost.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe",
             "userinit.exe", "taskhostw.exe", "runtimebroker.exe", "dllhost.exe"}


def _win_procs() -> dict[int, tuple[int, str]]:
    """Windows: {pid: (부모 pid, 실행 파일 이름 소문자)}."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    out: dict[int, tuple[int, str]] = {}
    if snap in (None, wintypes.HANDLE(-1).value):
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out[pe.th32ProcessID] = (pe.th32ParentProcessID, pe.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return out


def _win_window_pids() -> set[int]:
    """Windows: 화면에 보이는 맨 위 창(제목 있음, 다른 창에 딸리지 않음)을 가진 프로세스들."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pids: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and user32.GetWindowTextLengthW(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(each, 0)
    return pids


def _win_lineage(window_pids=_win_window_pids) -> tuple[int, str, int, str]:
    """(에이전트 pid, 이름, 창을 가진 앱 pid, 앱 종류). 못 찾으면 부모 pid 와 빈 값.

    앱 종류: 아는 앱(_WIN_APPS)이면 그 이름(terminal · code …). 모르는 앱이면 에이전트 위쪽에서
    보이는 창을 가진 첫 프로세스의 실행 파일 이름(예: Codex 앱 → chatgpt). 누르면 그 창을 앞으로 가져온다.
    """
    procs = _win_procs()
    pid, seen = os.getppid(), set()
    agent_pid, agent_name, app_pid, app = 0, "", 0, ""
    above: list[tuple[int, str]] = []  # 에이전트부터 위로 (모르는 앱 찾기용)
    while pid and pid in procs and pid not in seen and len(seen) < 40:
        seen.add(pid)
        parent, name = procs[pid]
        if not agent_pid and name not in _WIN_PASS:
            agent_pid, agent_name = pid, name
        if agent_pid and name in _WIN_APPS:
            # VS Code 같은 앱은 자식 프로세스(확장 호스트 등)도 같은 이름이다. 창은 맨 위 프로세스가 가진다
            while parent in procs and procs[parent][1] == name and parent not in seen:
                seen.add(parent)
                pid, parent = parent, procs[parent][0]
            return agent_pid, agent_name, pid, _WIN_APPS[name]
        if agent_pid:
            if name in _WIN_STOP:
                break
            above.append((pid, name))
        pid = parent
    if above:
        wins = window_pids()
        for p, name in above:  # 창 없는 보조 프로세스(같은 이름)는 건너뛰고 창이 있는 첫 조상
            if p in wins:
                app_pid, app = p, name[:-4] if name.endswith(".exe") else name
                break
    return agent_pid or os.getppid(), agent_name, app_pid, app


def _ps(pid: int, field: str) -> str:
    if IS_WINDOWS:
        return ""  # Windows 에는 ps 가 없다 (Git Bash 의 ps 는 옵션이 다름)
    try:
        return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=2).stdout.strip()
    except Exception:
        return ""


def _project(cwd: str) -> str:
    """git 저장소 최상위 폴더 이름 (없으면 현재 폴더 이름)."""
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=2).stdout.strip()
        if top:
            # 워크트리면 원래 저장소 이름을 쓴다
            common = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                    capture_output=True, text=True, timeout=2).stdout.strip()
            if common.endswith("/.git"):
                return os.path.basename(os.path.dirname(common))
            return os.path.basename(top)
    except Exception:
        pass
    return os.path.basename(cwd.rstrip("/\\")) or cwd


def _write(rec: dict) -> None:
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, rec["id"].replace("/", "_") + ".json")
    old = {}
    try:
        with open(path) as fp:  # 바로 닫는다: 열려 있는 동안엔 Windows 에서 다른 훅이 덮어쓰지 못한다
            old = json.load(fp)
    except Exception:
        pass
    rec = {**old, **rec, "started": old.get("started", rec["updated"])}
    tmp = path + f".{os.getpid()}"
    with open(tmp, "w") as fp:
        json.dump(rec, fp)
    _replace(tmp, path)


REPLACE_TRIES = 20  # 0.02 → 0.2초씩 늘려 가며 최대 약 2초 (훅 제한 시간 5초 안)


def _replace(tmp: str, path: str) -> None:
    """tmp 로 path 를 덮어쓴다.

    Windows 는 다른 프로세스(대시보드 · 같은 세션의 다른 훅)가 path 를 읽는 순간엔 덮어쓰기를 거부한다
    (PermissionError). macOS · Linux 는 늘 한 번에 성공한다. 잠깐 기다렸다 다시 하고, 끝내 안 되면 임시 파일을 지운다.
    """
    for i in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                break
            time.sleep(min(0.2, 0.02 * (i + 1)))
    try:
        os.remove(tmp)
    except OSError:
        pass


def _base(agent: str, session: str, cwd: str) -> dict:
    if IS_WINDOWS:
        return _base_windows(agent, session, cwd)
    ppid = os.getppid()  # 훅을 띄운 에이전트 프로세스
    args = _ps(ppid, "args")
    return {
        "id": f"{agent}-{session}",
        "agent": agent,
        "session": session,
        "cwd": cwd,
        "project": _project(cwd),
        "host": socket.gethostname().split(".")[0],
        "pid": ppid,
        "tty": _ps(ppid, "tty"),
        "term_program": os.environ.get("TERM_PROGRAM", ""),
        "bundle": os.environ.get("__CFBundleIdentifier", ""),  # 실행 앱 (VS Code, Cursor, iTerm…)
        # 다른 프로그램이 띄운 비대화형 실행(-p/--print, exec)은 하위 작업으로 본다
        "top": not any(f in f" {args} " for f in (" -p ", " --print ", " exec ")),
        "updated": time.time(),
    }


def _base_windows(agent: str, session: str, cwd: str) -> dict:
    try:
        pid, name, app_pid, app = _win_lineage()
    except Exception:
        pid, name, app_pid, app = os.getppid(), "", 0, ""
    if not app and os.environ.get("TERM_PROGRAM") == "vscode":  # VS Code 계열 내장 터미널
        app = "cursor" if "cursor" in os.environ.get("VSCODE_GIT_ASKPASS_NODE", "").lower() else "code"
    return {
        "id": f"{agent}-{session}",
        "agent": agent,
        "session": session,
        "cwd": cwd,
        "project": _project(cwd),
        "host": socket.gethostname().split(".")[0],
        "pid": pid,
        "tty": "",
        "term_program": os.environ.get("TERM_PROGRAM", ""),
        "bundle": "",
        "platform": "win32",
        "agent_exe": name,
        "app": app,  # terminal · code · cursor … (눌렀을 때 이동에 쓴다)
        "app_pid": app_pid,
        "top": True,  # Windows 에선 명령줄을 읽지 않으므로 비대화형 실행을 가려내지 않는다
        "updated": time.time(),
    }


def _codex_has_rollout(thread_id: str) -> bool:
    """Codex 가 대화 기록(rollout)을 남긴 대화인지. 내부 보조 대화(제목 생성 등)는 기록이 없고 탭도 없다."""
    import glob

    root = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
    return bool(glob.glob(os.path.join(root, "*", "*", "*", f"rollout-*{thread_id}.jsonl")))


def claude() -> None:
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    ev = d.get("hook_event_name", "")
    session = d.get("session_id") or "unknown"
    if ev == "SessionEnd":
        try:
            os.remove(os.path.join(DIR, f"claude-code-{session}.json"))
        except FileNotFoundError:
            pass
        return
    status = CLAUDE_STATUS.get(ev)
    if ev == "Notification" and "permission" in (d.get("message") or "").lower():
        status = "blocked"
    if not status:
        return
    rec = _base("claude-code", session, d.get("cwd") or os.getcwd())
    rec.update(status=status, event=ev)
    _write(rec)


def codex(argv: list[str]) -> None:
    # 기존 notify 명령: "--" 뒤 ~ 마지막 인자(JSON) 앞
    forward: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        forward, argv = argv[i + 1:], argv[:i]
    payload = forward[-1] if forward else (argv[-1] if argv else "")
    try:
        d = json.loads(payload)
    except Exception:
        d = {}
    if IS_WINDOWS and d.get("thread-id") and not _codex_has_rollout(d["thread-id"]):
        d = {}  # Windows: 기록이 없는 내부 보조 대화는 덱에 올리지 않는다 (기존 notify 연결은 아래에서 그대로)
    if d.get("type") == "agent-turn-complete":
        session = d.get("thread-id") or d.get("turn-id") or "unknown"
        rec = _base("codex", session, d.get("cwd") or os.getcwd())
        rec.update(status="done", event="agent-turn-complete")
        _write(rec)
    if forward[:-1]:  # 기존 연동(예: Computer Use) 그대로 실행
        try:
            subprocess.Popen(forward, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "claude":
            claude()
        elif len(sys.argv) > 1 and sys.argv[1] == "codex":
            codex(sys.argv[2:])
    except Exception:
        pass  # 훅 실패가 에이전트를 막으면 안 된다
    sys.exit(0)
