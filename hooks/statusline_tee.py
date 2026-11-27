#!/usr/bin/env python3
"""Claude Code statusline 래퍼 (statusline-tee.sh 의 Python 판, Windows 처럼 bash 가 없는 곳용).

입력 JSON(rate_limits 포함)을 ~/.cache/d200/claude_status.json 으로 저장한 뒤, 인자로 받은 기존 statusline
명령이 있으면 같은 입력을 넘기고 그 출력을 그대로 내보낸다. 인자가 없으면 저장만 하고 아무것도 출력하지 않는다.
대시보드는 이 파일에서 Claude 5시간 · 주간 한도를 읽는다 (usage.claude_usage).

표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

REPLACE_TRIES = 10  # Windows: 대시보드가 읽는 순간엔 덮어쓰기가 거부된다 → 잠깐씩 기다렸다 다시 (최대 약 1초)


def _freshness(window) -> tuple[float, float]:
    """한도 창이 얼마나 최신인지 (리셋 시각, 사용률). statusline-tee.sh 와 같은 규칙."""
    missing = (float("-inf"), float("-inf"))
    if not isinstance(window, dict):
        return missing
    try:
        used = float(window.get("used_percentage"))
    except (TypeError, ValueError, OverflowError):
        return missing
    reset = window.get("resets_at")
    if isinstance(reset, str):
        try:
            dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            reset = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
        except (OSError, OverflowError, ValueError):
            reset = None
    return (reset if isinstance(reset, (int, float)) else float("-inf"), used)


def merge_fresher(old: bytes, new: bytes) -> bytes:
    """새 입력이 기존 파일보다 오래된 한도 창(5시간 · 주간)을 담고 있으면 기존 창을 남긴다.
    여러 세션이 번갈아 저장할 때, 한동안 쉰 세션의 옛 값이 최신 값을 덮어쓰지 않게 한다."""
    try:
        old_limits = json.loads(old).get("rate_limits") or {}
        current = json.loads(new)
        current_limits = current.get("rate_limits") or {}
        kept = {k: old_limits[k] for k in ("five_hour", "seven_day")
                if _freshness(old_limits.get(k)) > _freshness(current_limits.get(k))}
        if kept:
            current["rate_limits"] = {**current_limits, **kept}
            return json.dumps(current).encode()
    except (AttributeError, TypeError, ValueError):
        pass
    return new


class _Lock:
    """여러 세션이 동시에 저장해도 읽기부터 교체까지 차례로 (macOS · Linux: fcntl, Windows: msvcrt)."""

    def __init__(self, path: str) -> None:
        self.fp = open(path, "a+b")

    def __enter__(self):
        if os.name == "nt":
            import msvcrt

            self.fp.seek(0)
            for _ in range(50):  # 최대 약 1초 기다린다. 끝내 못 잡으면 잠금 없이 저장한다
                try:
                    msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
                    self.locked = True
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl

            fcntl.flock(self.fp, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if os.name == "nt" and getattr(self, "locked", False):
                import msvcrt

                self.fp.seek(0)
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.fp.close()  # macOS · Linux 는 닫으면 풀린다


def save(data: bytes) -> None:
    d = os.path.expanduser("~/.cache/d200")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "claude_status.json")
    tmp = os.path.join(d, f".claude_status.{os.getpid()}")
    with _Lock(path + ".lock"):
        try:
            with open(path, "rb") as fp:
                data = merge_fresher(fp.read(), data)
        except OSError:
            pass
        with open(tmp, "wb") as fp:
            fp.write(data)
        for i in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
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
