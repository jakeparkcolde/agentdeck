#!/usr/bin/env python3
"""Claude Code statusline 래퍼 (statusline-tee.sh 의 Python 판, Windows 처럼 bash 가 없는 곳용).

입력 JSON(rate_limits 포함)을 ~/.cache/d200/claude_status.json 으로 저장한 뒤, 인자로 받은 기존 statusline
명령이 있으면 같은 입력을 넘기고 그 출력을 그대로 내보낸다. 인자가 없으면 저장만 하고 아무것도 출력하지 않는다.
대시보드는 이 파일에서 Claude 5시간 · 주간 한도를 읽는다 (usage.claude_usage).

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

REPLACE_TRIES = 10  # Windows: 대시보드가 읽는 순간엔 덮어쓰기가 거부된다 → 잠깐씩 기다렸다 다시 (최대 약 1초)


def save(data: bytes) -> None:
    d = os.path.expanduser("~/.cache/d200")
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".claude_status.{os.getpid()}")
    with open(tmp, "wb") as fp:
        fp.write(data)
    for i in range(REPLACE_TRIES):
        try:
            os.replace(tmp, os.path.join(d, "claude_status.json"))
            return
        except PermissionError:
            time.sleep(0.02 * (i + 1))
    os.remove(tmp)


def main() -> None:
    data = sys.stdin.buffer.read()
    try:
        save(data)
    except Exception:
        pass  # 저장이 안 돼도 기존 statusline 은 그대로 보여 준다
    if len(sys.argv) > 1:  # 기존 statusline 명령으로 이어서
        r = subprocess.run(sys.argv[1:], input=data, capture_output=True, timeout=10)
        sys.stdout.buffer.write(r.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # 상태줄 실패가 Claude Code 를 방해하면 안 된다
