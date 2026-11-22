#!/bin/bash
# Claude Code statusline 래퍼: 입력 JSON(rate_limits 포함)을 대시보드용으로 저장한 뒤,
# 인자로 받은 기존 statusline 명령에 그대로 넘긴다. 인자가 없으면 저장만 하고 아무것도 출력하지 않는다.
#
# ~/.claude/settings.json 예:
#   "statusLine": {"type": "command", "command": "/path/to/statusline-tee.sh <기존 statusline 명령>"}
input=$(cat)
dir="$HOME/.cache/d200"
mkdir -p "$dir"
tmp="$dir/.claude_status.$$"
# 여러 세션이 동시에 저장해도 기존 파일 읽기부터 교체까지 차례로 진행한다.
if ! printf '%s' "$input" | python3 -c '
import fcntl
import json
import os
import sys
from datetime import datetime, timezone


def freshness(window):
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


path, tmp = sys.argv[1:]
output = sys.stdin.read()
with open(path + ".lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with open(path, encoding="utf-8") as fp:
            old_limits = json.load(fp).get("rate_limits") or {}
        current = json.loads(output)
        current_limits = current.get("rate_limits") or {}
        kept = {}
        for key in ("five_hour", "seven_day"):
            previous = old_limits.get(key)
            if freshness(previous) > freshness(current_limits.get(key)):
                kept[key] = previous
        if kept:
            current["rate_limits"] = {**current_limits, **kept}
            output = json.dumps(current)
    except (OSError, AttributeError, TypeError, ValueError):
        pass
    with open(tmp, "w", encoding="utf-8") as fp:
        fp.write(output)
    os.replace(tmp, path)
' "$dir/claude_status.json" "$tmp" 2>/dev/null; then
  printf '%s' "$input" > "$tmp" && mv -f "$tmp" "$dir/claude_status.json"
fi
if [ $# -gt 0 ]; then
  printf '%s' "$input" | "$@"
fi
