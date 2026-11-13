"""agy(Antigravity CLI)를 쓰는 컴퓨터에서 할당량을 SSH 로 가져온다.

두 가지 방법 (docs/ANTIGRAVITY.md):
1. 수집기(권장): agy 컴퓨터의 로그인 세션(launchd)에서 5분마다 공식 읽기 전용 명령
   `agy -p /usage --output-format json` 을 실행해 ~/.cache/agentdeck/agy_usage.json 에 저장하고,
   덱은 SSH 로 그 파일만 읽는다. 그 컴퓨터의 평소 agy 로그인(키체인)을 같이 쓰므로 따로 로그인할 일이 없다.
   설치: `python remote_agy.py --install` / 제거: `--uninstall`
2. 수집기가 없거나 결과가 오래되면: REMOTE 스크립트를 ssh stdin 으로 넘겨 직접 실행한다.
   agy 는 SSH 로 실행되면 키체인 대신 별도 파일에 로그인을 두는데, 같은 컴퓨터에서 오래 떠 있는 agy 가
   매시간 토큰을 갱신할 때 이 로그인이 풀리는 일이 잦다 (그래서 1 을 권장).
토큰 파일이나 비공개 API 는 쓰지 않는다.
"""

from __future__ import annotations

import json
import math
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from config import AGY_HOST
from i18n import t

HOST = AGY_HOST  # agy 를 쓰는 컴퓨터의 ssh 대상 (config.json agy.host), 없으면 비활성
AGY_PATH = 'PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"'
LOGIN_EXIT = 3  # REMOTE 가 "로그인 필요"를 알리는 종료 코드
login_needed = False  # 마지막 조회가 로그인 문제로 실패했는지 (agy 는 SSH 접속용 로그인을 따로 보관한다)
via_collector = False  # 마지막 값이 수집기 파일에서 왔는지 (로그인 안내가 달라진다)
CACHE = "~/.cache/agentdeck/agy_usage.json"  # 원격 수집기가 쓰는 파일
COLLECTOR = "~/.cache/agentdeck/agy_usage.py"
LABEL = "io.github.jakeparkcolde.agentdeck.agy-usage"
FRESH_SEC = 15 * 60  # 수집기는 5분마다 쓴다. 15분 넘게 안 바뀌면 멈춘 것으로 보고 직접 조회

# /usage 를 지원하는 버전(1.1.11+)인지 먼저 확인한다: 옛 CLI 는 슬래시 명령을 프롬프트로 처리해 한도를 쓸 수 있다.
REMOTE = r'''
import os, re, selectors, subprocess, sys, time

env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
try:
    version = subprocess.run(["agy", "--version"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10, env=env, check=True)
    # "1.5.3", "v1.5.3", "agy version 1.5.3" 모두 허용. "-preview" 같은 시험판은 제외
    match = re.search(r"(?<![\d.])v?(\d+)\.(\d+)\.(\d+)(?![-\w.])", version.stdout)
    if not match or tuple(map(int, match.groups())) < (1, 1, 11):
        sys.exit(1)
    # 로그인이 풀려 있으면 agy 가 로그인 주소를 찍고 기다린다: 그 안내가 보이면 바로 끝내고 3 으로 알린다
    proc = subprocess.Popen(["agy", "-p", "/usage", "--output-format", "json"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    sel, bufs, deadline = selectors.DefaultSelector(), {proc.stdout: b"", proc.stderr: b""}, time.time() + 60
    for f in bufs:
        sel.register(f, selectors.EVENT_READ)
    while sel.get_map() and time.time() < deadline:
        for key, _ in sel.select(1):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if chunk:
                bufs[key.fileobj] += chunk
            else:
                sel.unregister(key.fileobj)
        if b"authentication required" in (bufs[proc.stdout] + bufs[proc.stderr]).lower():
            proc.kill()
            sys.exit(3)
    if sel.get_map():  # 시간 초과
        proc.kill()
        sys.exit(1)
    if proc.wait(timeout=5) != 0:
        sys.exit(1)
    sys.stdout.buffer.write(bufs[proc.stdout])
except (OSError, subprocess.SubprocessError, UnicodeError):
    sys.exit(1)
'''


@dataclass
class Bucket:
    group: str
    window: str  # "5h" | "weekly"
    remaining: float  # 0~1
    reset: datetime | None


def _bucket(name: str, bucket: object) -> Bucket | None:
    """버킷 하나 해석. 쓸 수 없으면 None (호출 측에서 건너뜀)."""
    if not isinstance(bucket, dict) or bucket.get("disabled") is True or bucket.get("enabled") is False:
        return None
    remaining = bucket.get("remaining_fraction")
    if (isinstance(remaining, bool) or not isinstance(remaining, (int, float))
            or not math.isfinite(remaining) or not 0 <= remaining <= 1):
        return None
    window = bucket.get("window")
    if window not in ("5h", "weekly"):  # 대시보드가 그리는 창만
        return None
    reset = bucket.get("reset_time")
    if reset is not None:
        if not isinstance(reset, str):
            return None
        try:
            reset = datetime.fromisoformat(reset.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return None
        if reset.tzinfo is None:
            return None
        reset = reset.astimezone(timezone.utc)
    return Bucket(name, window, float(remaining), reset)


def parse_quota(payload: object) -> list[Bucket] | None:
    """CLI 응답에서 한도만 뽑는다.

    - 응답 전체 형식(status SUCCESS, command.data.groups)이 다르면 None.
    - 그룹·버킷 하나가 이상하거나 모르는 시간 창이면 그것만 건너뛴다 (나머지 한도는 계속 표시).
    - 값이 없거나 꺼진 버킷은 0 으로 만들지 않고 건너뛴다. 토큰 사용량 `usage` 는 한도가 아니다.
    """
    if (not isinstance(payload, dict) or payload.get("status") != "SUCCESS"
            or "error" in payload):
        return None
    command = payload.get("command")
    if not isinstance(command, dict):
        return None
    data = command.get("data")
    if not isinstance(data, dict):
        return None
    groups = data.get("groups")
    if not isinstance(groups, list):
        return None
    out = []
    for group in groups:
        if not isinstance(group, dict) or group.get("disabled") is True or group.get("enabled") is False:
            continue
        name, buckets = group.get("name"), group.get("buckets")
        if not isinstance(name, str) or not name.strip() or not isinstance(buckets, list):
            continue
        for bucket in buckets:
            b = _bucket(name, bucket)
            if b:
                out.append(b)
    return out or None


def read_cache(text: str, now: float) -> tuple[str, list[Bucket] | None]:
    """수집기 파일 해석 → ("ok"|"login"|"error"|"stale", 한도). 형식이 이상하면 "stale" (직접 조회로 넘어감)."""
    try:
        d = json.loads(text)
        checked, code = float(d["checked"]), int(d["code"])
    except (ValueError, TypeError, KeyError):
        return "stale", None
    if not 0 <= now - checked <= FRESH_SEC:
        return "stale", None
    if code == LOGIN_EXIT:
        return "login", None
    if code != 0:
        return "error", None
    return "ok", parse_quota(d.get("payload"))


def _from_collector() -> tuple[str, list[Bucket] | None]:
    try:
        r = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", HOST, f"cat {CACHE}"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "stale", None
    if r.returncode != 0:
        return "stale", None
    import time
    return read_cache(r.stdout, time.time())


def agy_quota() -> list[Bucket] | None:
    global login_needed, via_collector
    if not HOST:
        return None
    state, buckets = _from_collector()
    if state != "stale":
        via_collector, login_needed = True, state == "login"
        return buckets
    via_collector = False
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             HOST, "python3", "-"],
            input=REMOTE, capture_output=True, text=True, timeout=90,
            check=True,
        )
        login_needed = False
        return parse_quota(json.loads(result.stdout))
    except subprocess.CalledProcessError as e:
        login_needed = e.returncode == LOGIN_EXIT
        return None
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        return None


def collector_script() -> str:
    """agy 컴퓨터에 둘 수집기: REMOTE 를 실행해 결과를 CACHE 에 한 번에(원자적으로) 쓴다."""
    return f"""# agentdeck agy 한도 수집기 (remote_agy.py --install 이 설치). 지우려면 --uninstall
import json, os, subprocess, sys, time
REMOTE = {REMOTE!r}
out = {{"checked": time.time(), "code": 1, "payload": None}}
try:
    r = subprocess.run([sys.executable, "-c", REMOTE], capture_output=True, text=True, timeout=150)
    out["code"] = r.returncode
    if r.returncode == 0:
        out["payload"] = json.loads(r.stdout)
except (OSError, subprocess.SubprocessError, ValueError):
    out["code"] = 1
path = os.path.expanduser({CACHE!r})
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path + ".tmp", "w") as f:
    json.dump(out, f)
os.replace(path + ".tmp", path)
"""


INSTALL = r'''
import os, subprocess, sys, time
script, label = sys.stdin.read(), sys.argv[1]
home = os.path.expanduser("~")
cdir = os.path.join(home, ".cache", "agentdeck")
os.makedirs(cdir, exist_ok=True)
py = os.path.join(cdir, "agy_usage.py")
plist = os.path.join(home, "Library", "LaunchAgents", label + ".plist")
uid = str(os.getuid())
def bootout():
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    for _ in range(30):  # bootout 은 바로 끝나지 않는다
        if subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True).returncode:
            break
        time.sleep(1)
if script == "--uninstall":
    bootout()
    for f in (plist, py, os.path.join(cdir, "agy_usage.json")):
        if os.path.exists(f):
            os.remove(f)
    print("removed")
    sys.exit(0)
with open(py, "w") as f:
    f.write(script)
path = f"{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
with open(plist, "w") as f:
    f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>/usr/bin/env</string><string>python3</string><string>{py}</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
<key>StartInterval</key><integer>300</integer>
<key>RunAtLoad</key><true/>
<key>ProcessType</key><string>Background</string>
</dict></plist>
""")
bootout()
r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist], capture_output=True, text=True)
if r.returncode:
    print(r.stderr.strip(), file=sys.stderr)
    sys.exit(2)
print("installed")
'''


def manage_collector(install: bool) -> bool:
    """agy 컴퓨터에 수집기를 설치/제거한다 (그 컴퓨터 사용자의 로그인 세션에 launchd 작업 하나)."""
    if not HOST:
        print(t("config.json 에 agy.host 가 없어요.", "Set agy.host in config.json first."))
        return False
    payload = collector_script() if install else "--uninstall"
    cmd = "python3 -c " + shlex.quote(INSTALL) + " " + shlex.quote(LABEL)
    r = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", HOST, cmd], input=payload, capture_output=True,
                       text=True, timeout=60)
    if r.returncode:
        print(t(f"실패: {r.stderr.strip()} (그 컴퓨터에 로그인한 화면 세션이 있어야 해요)",
                f"Failed: {r.stderr.strip()} (that computer needs a logged-in desktop session)"))
        return False
    if not install:
        print(t("수집기를 지웠어요.", "Collector removed."))
        return True
    import time
    for _ in range(40):  # 첫 실행 결과 기다리기
        time.sleep(3)
        state, buckets = _from_collector()
        if state != "stale":
            msg = {"ok": t(f"설치했어요. 한도 {len(buckets or [])}개를 읽었어요.", f"Installed. Read {len(buckets or [])} limits."),
                   "login": t("설치했어요. 그 컴퓨터에서 agy 에 로그인해 주세요.", "Installed. Please sign in to agy on that computer."),
                   "error": t("설치했지만 agy 를 실행하지 못했어요 (버전·경로 확인).", "Installed, but agy failed to run (check version/path).")}
            print(msg[state])
            return state == "ok"
    print(t("설치했지만 아직 결과가 없어요. 잠시 뒤 덱에서 확인하세요.", "Installed; no result yet. Check the deck shortly."))
    return True


def login_command() -> str:
    """터미널에 넣을 명령: 안내 한 줄을 찍고 원격 agy 를 대화형으로 연다."""
    hint = t("agy 가 로그인 주소를 보여주면 브라우저로 로그인하고, 끝나면 agy 를 종료하세요. 덱은 곧 다시 읽어요.",
             "When agy shows a login URL, sign in with your browser, then quit agy. The deck will retry shortly.")
    return f"echo {shlex.quote(hint)}; ssh -t {shlex.quote(HOST)} {shlex.quote(AGY_PATH + ' agy')}"


def open_login() -> bool:
    """맥의 터미널 앱을 열어 사용자가 직접 로그인하게 한다. 비밀번호나 토큰은 이 프로그램이 다루지 않는다."""
    if not HOST:
        return False
    cmd = login_command().replace("\\", "\\\\").replace('"', '\\"')
    try:
        r = subprocess.run(["osascript", "-e", 'tell application "Terminal" to activate',
                            "-e", f'tell application "Terminal" to do script "{cmd}"'],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


if __name__ == "__main__":
    import sys
    if "--install" in sys.argv[1:] or "--uninstall" in sys.argv[1:]:
        sys.exit(0 if manage_collector("--install" in sys.argv[1:]) else 1)
    for b in agy_quota() or []:
        print(b)
    print(t("출처: 수집기 파일", "source: collector") if via_collector else t("출처: 직접 조회", "source: direct"))
