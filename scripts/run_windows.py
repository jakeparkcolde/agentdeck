"""Windows 자동 실행용 시작 스크립트: 창 없이(pythonw) 대시보드를 돌리고, 출력은 로그 파일로 보낸다.

scripts/install-task.ps1 이 작업 스케줄러에 이 파일을 등록한다.
로그: ~/.cache/d200/dashboard.log (5MB 를 넘으면 dashboard.log.1 로 넘기고 새로 쓴다)
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.expanduser("~/.cache/d200/dashboard.log")
MAX_LOG = 5 * 1024 * 1024


def hide_child_consoles() -> None:
    """콘솔 없는 pythonw 에서 콘솔 프로그램(python · git · ssh …)을 띄우면 매번 새 콘솔 창이 깜빡인다.

    대시보드는 몇 초마다 이런 프로그램을 실행하므로, 창 옵션을 따로 주지 않은 모든 실행에 CREATE_NO_WINDOW 를
    기본으로 붙인다 (창이 있는 앱 — VS Code 등 — 은 이 옵션의 영향을 받지 않는다).
    """
    import subprocess

    if sys.platform != "win32" or getattr(subprocess.Popen, "_agentdeck_hidden", False):
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init
    subprocess.Popen._agentdeck_hidden = True


def main() -> None:
    hide_child_consoles()
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if os.path.exists(LOG) and os.path.getsize(LOG) > MAX_LOG:
        os.replace(LOG, LOG + ".1")
    log = open(LOG, "a", encoding="utf-8", buffering=1)  # 줄 단위로 바로 기록
    sys.stdout = sys.stderr = log  # pythonw 에는 콘솔이 없어서 print 가 사라진다
    import dashboard

    dashboard.main()


if __name__ == "__main__":
    main()
