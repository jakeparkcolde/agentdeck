"""agy(Antigravity CLI)를 쓰는 컴퓨터에서 할당량을 SSH 로 가져온다.

원격에는 아무것도 설치하지 않는다: 아래 REMOTE 스크립트를 ssh stdin 으로 넘겨 공식 읽기 전용 명령
`agy -p /usage --output-format json` 을 실행한다. 토큰 파일이나 비공개 API 는 쓰지 않는다.
요구 사항은 docs/ANTIGRAVITY.md 참고.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from config import AGY_HOST

HOST = AGY_HOST  # agy 를 쓰는 컴퓨터의 ssh 대상 (config.json agy.host), 없으면 비활성

# /usage 를 지원하는 버전(1.1.11+)인지 먼저 확인한다: 옛 CLI 는 슬래시 명령을 프롬프트로 처리해 한도를 쓸 수 있다.
REMOTE = r'''
import os, re, subprocess, sys

env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
try:
    version = subprocess.run(["agy", "--version"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10, env=env, check=True)
    # "1.5.3", "v1.5.3", "agy version 1.5.3" 모두 허용. "-preview" 같은 시험판은 제외
    match = re.search(r"(?<![\d.])v?(\d+)\.(\d+)\.(\d+)(?![-\w.])", version.stdout)
    if not match or tuple(map(int, match.groups())) < (1, 1, 11):
        sys.exit(1)
    result = subprocess.run(["agy", "-p", "/usage", "--output-format", "json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, env=env, check=True)
    sys.stdout.write(result.stdout)
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


def agy_quota() -> list[Bucket] | None:
    if not HOST:
        return None
    try:
        result = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             HOST, "python3", "-"],
            input=REMOTE, capture_output=True, text=True, timeout=90,
            check=True,
        )
        return parse_quota(json.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        return None


if __name__ == "__main__":
    for b in agy_quota() or []:
        print(b)
