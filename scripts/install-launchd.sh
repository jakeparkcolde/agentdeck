#!/bin/bash
# 로그인할 때 대시보드를 자동 실행하도록 launchd 에 등록한다. (다시 실행하면 갱신)
# 제거: launchctl bootout gui/$(id -u)/com.agentdeck.dashboard && rm ~/Library/LaunchAgents/com.agentdeck.dashboard.plist
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.agentdeck.dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"
# 안내 문구 언어: 맥의 첫 번째 선호 언어가 한국어면 한국어, 아니면 영어
if defaults read -g AppleLanguages 2>/dev/null | sed -n 2p | grep -qi '"\?ko'; then KO=1; else KO=0; fi
say() { if [ "$KO" = 1 ]; then echo "$1"; else echo "$2"; fi; }
[ -x "$PY" ] || { say "먼저 가상환경을 만드세요: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" \
          "Create the virtualenv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$PY</string><string>-u</string><string>$ROOT/dashboard.py</string></array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key><string>$HOME</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>15</integer>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>$ROOT/dashboard.log</string>
    <key>StandardErrorPath</key><string>$ROOT/dashboard.log</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
say "등록됨: $LABEL (로그: $ROOT/dashboard.log)" "Registered: $LABEL (log: $ROOT/dashboard.log)"
