"""훅 방식 에이전트 현황: hooks/agent_hook.py 가 남긴 상태 파일을 모아 프로젝트별로 묶는다.

Herdr 없이도 VS Code·Cursor·iTerm·Ghostty·터미널 등 어디서 도는 Claude Code / Codex 세션을 볼 수 있다.
  - 이 맥: ~/.cache/d200/agents/*.json
  - 원격 맥(config hooks.hosts): SSH 로 같은 폴더를 읽는다
  - 프로세스가 끝난 세션(pid 없음)과 하루 넘게 갱신 없는 세션은 정리한다
눌렀을 때 이동: 그 세션이 도는 앱(iTerm·Terminal 은 해당 탭, VS Code·Cursor 는 프로젝트 창, 그 외는 앱 활성화)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess

import platform_compat
from remote_herdr import StudioState, Workspace

AGENTS_DIR = "~/.cache/d200/agents"
STALE_SEC = 24 * 3600

# 원격(또는 로컬)에서 실행: 살아 있는 세션만 JSON 목록으로 출력, 죽은 세션 파일은 삭제
# (원격에서도 돌아서 platform_compat 를 못 쓴다. Windows 에서 os.kill(pid, 0) 은 그 프로세스를 강제 종료하므로
#  Windows 는 OpenProcess 로 확인한다 — platform_compat.pid_alive 와 같은 방법)
SCAN = r'''
import glob, json, os, time
def alive(pid):
    if os.name == "nt":
        if pid <= 0:
            return False
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False
out = []
for p in glob.glob(os.path.expanduser("%s/*.json.*")):  # 훅이 덮어쓰다 남긴 임시 파일 (1분 지나면 버려진 것)
    try:
        if time.time() - os.path.getmtime(p) > 60:
            os.remove(p)
    except Exception:
        pass
for p in glob.glob(os.path.expanduser("%s/*.json")):
    try:
        with open(p) as f:  # 바로 닫는다: 열려 있는 동안엔 Windows 에서 훅이 덮어쓰지 못한다
            d = json.load(f)
        if not alive(int(d.get("pid") or 0)) or time.time() - d.get("updated", 0) > %d:
            os.remove(p)
            continue
        out.append(d)
    except Exception:
        pass
print(json.dumps(out))
''' % (AGENTS_DIR, AGENTS_DIR, STALE_SEC)

# 세션 정보 (term id → 기록) : 눌렀을 때 이동에 쓴다
_sessions: dict[str, dict] = {}
LOCAL_HOST = socket.gethostname().split(".")[0]


def _scan(host: str | None) -> list[dict]:
    cmd = platform_compat.python_cmd() + ["-"] if host is None else \
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "python3", "-"]
    try:
        r = subprocess.run(cmd, input=SCAN, capture_output=True, text=True, timeout=15)
        items = json.loads(r.stdout)
    except Exception:
        return []
    for d in items:
        d["_ssh"] = host
    return items


def state(hosts: list[str | None]) -> StudioState:
    items = [d for h in hosts for d in _scan(h)]
    groups: dict[str, Workspace] = {}
    _sessions.clear()
    for d in sorted(items, key=lambda x: x.get("started", 0)):
        local = d.get("_ssh") is None
        name = d.get("project") or "?"
        if not local:
            name = f"{name} · {d.get('host', d['_ssh'])}"
        ws = groups.setdefault(name, Workspace(name, space_id=None))
        term = f"{d.get('_ssh') or 'local'}:{d['id']}"
        _sessions[term] = d
        ws.add(term, d.get("agent"), d.get("status"), bool(d.get("top", True)))
    # 알림·작업 중인 프로젝트를 앞쪽에 (같은 상태면 이름순)
    ordered = sorted(groups.values(), key=lambda w: (-w.attention, -w.done, -w.working, w.name))
    return StudioState(True, ordered, "hooks")


# ---------- 눌렀을 때 이동 ----------
def _osa(script: str) -> None:
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def focus_session(term: str) -> bool:
    d = _sessions.get(term)
    if not d:
        return False
    if d.get("_ssh") is not None:
        return False  # 다른 맥에서 도는 세션: 이 맥에서 창을 띄울 수 없음
    if d.get("platform") == "win32":
        return _focus_windows(d)
    bundle, tty, cwd = d.get("bundle", ""), d.get("tty", ""), d.get("cwd", "")
    dev = f"/dev/{tty}" if tty and tty not in ("??", "-") else ""
    if bundle == "com.googlecode.iterm2" and dev:
        _osa(f'''tell application "iTerm2"
  activate
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "{dev}" then
          select t
          select s
          set index of w to 1
          return
        end if
      end repeat
    end repeat
  end repeat
end tell''')
        return True
    if bundle == "com.apple.Terminal" and dev:
        _osa(f'''tell application "Terminal"
  activate
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is "{dev}" then
        set selected of t to true
        set index of w to 1
        return
      end if
    end repeat
  end repeat
end tell''')
        return True
    if bundle and cwd and ("VSCode" in bundle or "vscodium" in bundle.lower() or "cursor" in bundle.lower()
                           or bundle.startswith("com.todesktop.")):
        subprocess.run(["open", "-b", bundle, cwd], timeout=10)  # 그 프로젝트 창으로
        return True
    if bundle:
        subprocess.run(["open", "-b", bundle], timeout=10)  # 앱만 앞으로 (Ghostty, Herdr 등)
        return True
    return False


_hook_mod = None


def _window_app(pid: int) -> tuple[int, str]:
    """Windows: 에이전트 pid 에서 위로 올라가 창을 가진 앱 (앱 pid, 종류). 훅과 같은 규칙 (hooks/agent_hook.py)."""
    global _hook_mod
    try:
        if _hook_mod is None:
            import importlib.util

            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks", "agent_hook.py")
            spec = importlib.util.spec_from_file_location("_agentdeck_hook", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _hook_mod = mod
        _, _, app_pid, app = _hook_mod._win_lineage(start=pid)
        return app_pid, app
    except Exception:
        return 0, ""


def _focus_windows(d: dict) -> bool:
    """Windows: VS Code · Cursor 는 그 프로젝트 창을 열고, 그 밖(Windows Terminal 등)은 세션이 도는 앱 창을 앞으로."""
    app, cwd, app_pid = d.get("app", ""), d.get("cwd", ""), int(d.get("app_pid") or 0)
    if not app and not app_pid and d.get("pid"):
        # 기록에 앱이 없다 (예전 훅이 쓴 기록, 또는 그때 창이 없었음): 지금 다시 찾는다
        app_pid, app = _window_app(int(d["pid"]))
    if app in ("code", "cursor") and cwd and platform_compat.run_cli(app, cwd):
        return True
    return platform_compat.activate_pid(app_pid)


def focus(ws: Workspace, step: int = 0) -> bool:
    """처음: 결정 필요 > 완료 > 작업 중 세션으로 / 반복 누름: 그 프로젝트의 세션을 차례로."""
    order = ws.attention_terms + ws.done_terms + ws.working_terms
    order += [m.term for m in ws.members if m.term not in order]
    return focus_session(order[step % len(order)]) if order else False


if __name__ == "__main__":
    s = state([None])
    for w in s.workspaces:
        print(w.name, "working", w.working, "attention", w.attention, "done", w.done, w.agents)
