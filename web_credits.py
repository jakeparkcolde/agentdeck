"""웹으로만 확인할 수 있는 AI 서비스 크레딧(Google Flow, Higgsfield)을 내 맥에서 읽어 온다 (한도 페이지에 표시).

    .venv/bin/python web_credits.py login flow         # 덱 전용 Chrome 창이 열림 → 직접 로그인 (처음 한 번)
    .venv/bin/python web_credits.py login higgsfield
    .venv/bin/python web_credits.py [flow|higgsfield]  # 화면 없이 크레딧 읽기

동작
- 택배 가져오기와 같은 덱 전용 Chrome 프로필(~/.cache/d200/ali-browser)을 쓴다. 로그인 정보는 이 맥의 그 폴더에만 남는다.
- 이 맥 안(127.0.0.1)에서만 브라우저에 접속한다. 비밀번호는 저장하지도 입력하지도 않는다 (로그인은 사람이 직접).
- Flow 는 계정 단추만 눌러 크레딧 칸을 읽고, Higgsfield 는 구독 설정 화면 글자를 읽기만 한다.
  다른 단추(로그아웃·업그레이드·구매 등)는 누르지 않는다.
- 읽은 값(크레딧 수, 월 한도, 초기화 시각, 요금제 이름)만 ~/.cache/d200/<서비스>.json 에 저장한다.
  이메일·이름 같은 계정 정보는 저장하지 않는다.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import parcels_import as browser
from i18n import t

CACHE_DIR = Path.home() / ".cache" / "d200"
POLL_SEC = 30 * 60

# Flow: 계정 단추를 눌러 크레딧 칸이 뜨면 그 글자만 돌려준다
FLOW_JS = r'''(async () => {
  const btn = document.querySelector('.header-user-button');
  if (!btn) return {signedOut: true};
  const tier = btn.textContent.trim();
  let info = document.querySelector('.credits-info');
  if (!info) {
    btn.click();
    for (let i = 0; i < 20 && !(info = document.querySelector('.credits-info')); i++)
      await new Promise(r => setTimeout(r, 250));
  }
  const text = info ? info.innerText : '';
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  return {tier, text};
})()'''

# Higgsfield: 구독 화면 글자만 읽는다 (누르는 것 없음)
HIGGS_JS = r'''(() => {
  const t = document.body.innerText;
  if (!/credits left/i.test(t)) return {signedOut: /sign in|log in|로그인/i.test(t)};
  return {text: t.slice(0, 6000)};
})()'''

SERVICES = {
    "flow": {"url": "https://flow.google.com/", "js": FLOW_JS},
    "higgsfield": {"url": "https://higgsfield.ai/me/settings/subscription", "js": HIGGS_JS},
}


def _num(s: str) -> float:
    """'1 200' / '1,200' / '771.28' → 숫자 (자리 구분 공백·쉼표 무시)."""
    return float(re.sub(r"[^\d.]", "", s))


def _parse_reset(text: str, now: datetime) -> datetime | None:
    """'11월 19일 오후 12:00에 초기화' / 'Resets Nov 19, 12:00 PM' → datetime (가장 가까운 미래)."""
    month = day = None
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
    if m:
        month, day = int(m[1]), int(m[2])
    else:
        m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d{1,2})", text)
        if m:
            month, day = browser.MONTHS.index(m[1]) + 1, int(m[2])
    if not month:
        return None
    hour = minute = 0
    t = re.search(r"(오전|오후)?\s*(\d{1,2}):(\d{2})\s*(AM|PM)?", text, re.I)
    if t:
        hour, minute = int(t[2]) % 12, int(t[3])
        if t[1] == "오후" or (t[4] or "").upper() == "PM":
            hour += 12
        elif not t[1] and not t[4]:
            hour = int(t[2])
    when = datetime(now.year, month, day, hour, minute)
    if when < now.replace(tzinfo=None):
        when = when.replace(year=now.year + 1)
    return when.astimezone()


def parse_flow(raw: dict, now: datetime) -> dict:
    text = raw.get("text") or ""
    m = re.search(r"크레딧\s*([\d,]+)|([\d,]+)\s*(?:AI\s*)?credits", text, re.I)
    reset = _parse_reset(text, now)
    return {"credits": int(_num(m[1] or m[2])) if m else None, "total": None,
            "reset": reset.isoformat() if reset else None, "tier": (raw.get("tier") or "")[:12]}


def parse_higgsfield(raw: dict, now: datetime) -> dict:
    text = raw.get("text") or ""
    m = re.search(r"Monthly credits left\s*([\d.,\s  ]+?)\s*/\s*([\d,\s  ]+\d)", text, re.I)
    left = total = None
    if m:
        left, total = _num(m[1]), _num(m[2])
    else:
        m = re.search(r"([\d.,]+)\s*credits left", text, re.I)
        left = _num(m[1]) if m else None
    plan = re.search(r"\b(\w+) Plan\b", text)
    reset = _higgs_refill(text, now)
    return {"credits": None if left is None else int(left), "total": None if total is None else int(total),
            "reset": reset.isoformat() if reset else None, "tier": plan[1][:12] if plan else ""}


def _higgs_refill(text: str, now: datetime) -> datetime | None:
    """'Your plan renews June 30, 2027' → 다음 월 크레딧 충전 시각 (추정).

    화면에는 갱신일만 나온다. 월간 크레딧은 갱신일과 같은 날짜에 매달 채워진다고 보고,
    지금 이후 가장 가까운 그 날짜(달에 그 날이 없으면 말일)를 돌려준다. 연간 요금제도 같은 방식.
    """
    m = re.search(r"renews\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d{1,2}),?\s+(\d{4})", text, re.I)
    if not m:
        return None
    renew = datetime(int(m[3]), browser.MONTHS.index(m[1].title()) + 1, int(m[2]))
    now = now.replace(tzinfo=None)
    y, mo = now.year, now.month
    for _ in range(14):
        last = calendar.monthrange(y, mo)[1]
        when = datetime(y, mo, min(renew.day, last))
        if when > now:
            return min(when, renew).astimezone()
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return None


PARSERS = {"flow": parse_flow, "higgsfield": parse_higgsfield}


async def _read(name: str) -> dict:
    import websockets

    svc = SERVICES[name]
    async with websockets.connect(browser._alive(), max_size=50_000_000) as ws:
        c = browser._CDP(ws)
        tid = (await c.call("Target.createTarget", {"url": svc["url"], "background": True}))["result"]["targetId"]
        sid = (await c.call("Target.attachToTarget", {"targetId": tid, "flatten": True}))["result"]["sessionId"]
        try:
            raw = {}
            for _ in range(6):  # 화면이 다 뜰 때까지 조금씩 기다림
                await asyncio.sleep(3)
                r = await c.call("Runtime.evaluate", {"expression": svc["js"], "returnByValue": True,
                                                      "awaitPromise": True}, sid, 30)
                raw = r.get("result", {}).get("result", {}).get("value") or {}
                if raw.get("text"):
                    break
            return raw
        finally:
            await c.call("Target.closeTarget", {"targetId": tid})


def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def run(name: str) -> dict:
    """크레딧을 읽어 캐시에 저장하고 돌려준다. 로그인이 필요하면 SystemExit."""
    with browser.BROWSER_LOCK:
        proc = browser.launch(headless=True)
        try:
            raw = asyncio.run(_read(name))
        finally:
            if proc:
                proc.terminate()
    if not raw.get("text"):
        raise SystemExit(t(f"{name} 에 로그인이 필요해요", f"Please log in to {name}") + f": .venv/bin/python web_credits.py login {name}")
    now = datetime.now()
    got = PARSERS[name](raw, now)
    if got["credits"] is None:
        raise SystemExit(t(f"{name} 크레딧을 못 읽었어요", f"Could not read {name} credits"))
    got["checked"] = now.astimezone().isoformat(timespec="seconds")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(name).write_text(json.dumps(got, ensure_ascii=False))
    return got


def load(name: str) -> dict | None:
    try:
        return json.loads(cache_path(name).read_text())
    except Exception:
        return None


def login(name: str) -> None:
    url = SERVICES[name]["url"]
    if browser._alive():
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{browser.PORT}/json/new?{url}", method="PUT"), timeout=5).read()
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True)
    else:
        browser.launch(headless=False, url=url)
    print(t("덱 전용 Chrome 창에서 직접 로그인한 뒤 그 창을 닫아 주세요.",
            "Log in yourself in the dedicated Chrome window, then close that window."))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["login"]:
        login(args[1] if len(args) > 1 else "flow")
    else:
        for n in args or list(SERVICES):
            t0 = time.time()
            print(n, run(n), f"({time.time() - t0:.0f}s)")
