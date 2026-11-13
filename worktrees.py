"""studio.host 의 git 워크트리 현황과 정리 동작.

수집(SSH 한 번): config 의 studio.worktree_globs 에 맞는 워크트리마다
  브랜치, 커밋 안 한 변경 수, 마지막 커밋 경과, 기준 브랜치(main/master) 대비 앞선 커밋 수,
  그 폴더에서 돌고 있는 Herdr 에이전트(작업 중 여부)

상태 분류
  작업      : 에이전트가 그 워크트리에서 작업 중
  방치      : 커밋 안 한 변경이 있는데 작업 중인 에이전트 없음
  정리 가능 : 변경 없음 + 기준 브랜치에 모두 반영됨(앞선 커밋 0)
  오래됨    : 변경 없음 + 미반영 커밋 있음 + 마지막 커밋 3일 이상
  정상      : 그 외

동작
  open_in_herdr  : 에이전트가 있으면 그 에이전트로, 없으면 리드 스페이스에 그 폴더로 새 탭
  remove_worktree: `git worktree remove` (강제 아님 → 변경 있으면 git 이 거부, 브랜치는 지우지 않음)
  ask_tower      : 리드 pane(studio.lead_pane)에 정리 요청 메시지 전송 (herdr-core send)
"""

from __future__ import annotations

import json
import os
import re
import time
import shlex
import subprocess
from dataclasses import dataclass

from config import LEAD_PANE, STUDIO, STUDIO_ENABLED, STUDIO_HOST, WORKTREE_GLOBS
from i18n import t

HOST = STUDIO_HOST
HERDR_APP = os.path.expanduser("~/.local/bin/herdr-app")
# LEAD_PANE: 정리 요청을 받을 리드 에이전트 pane (config.json studio.lead_pane)
STALE_HOURS = 72

SCAN = r'''
import glob, json, os, subprocess, time
env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
def git(path, *a):
    r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""
cands = [p for g in GLOBS for p in glob.glob(os.path.expanduser(g) + "/")]
try:
    terms = json.loads(subprocess.run(["herdr-core", "ls", "--json"], capture_output=True, text=True, env=env).stdout)["terminals"]
except Exception:
    terms = []
out = []
for d in cands:
    d = d.rstrip("/")
    if not os.path.isfile(d + "/.git"):  # 워크트리는 .git 이 파일
        continue
    base = ""
    for b in ("origin/main", "main", "origin/master", "master"):
        if git(d, "rev-parse", "--verify", "--quiet", b):
            base = b
            break
    ahead = git(d, "rev-list", "--count", base + "..HEAD") if base else ""
    last = git(d, "log", "-1", "--format=%ct")
    agents = [t for t in terms if (t.get("cwd") or "").startswith(d) and t.get("agent") in ("claude-code", "codex", "antigravity")]
    out.append({
        "path": d,
        "repo": os.path.basename(os.path.dirname(d)),
        "name": os.path.basename(d),
        "branch": git(d, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": len([l for l in git(d, "status", "--porcelain").splitlines() if l.strip()]),
        "age_h": (time.time() - int(last)) / 3600 if last else None,
        "base": base,
        "ahead": int(ahead) if ahead.isdigit() else None,
        "agents": [{"address": t["address"], "status": t.get("status")} for t in agents],
    })
print(json.dumps(out))
'''


@dataclass
class Worktree:
    path: str
    repo: str
    name: str
    branch: str
    dirty: int
    age_h: float | None
    base: str
    ahead: int | None
    agents: list[dict]

    @property
    def working(self) -> bool:
        return any(a.get("status") == "working" for a in self.agents)

    @property
    def state(self) -> str:
        if self.working:
            return "작업"
        if self.dirty:
            return "방치"
        if self.ahead == 0:
            return "정리 가능"
        if self.age_h is not None and self.age_h >= STALE_HOURS:
            return "오래됨"
        return "정상"

    @property
    def can_remove(self) -> bool:
        """삭제는 변경 없음 + 기준 브랜치에 모두 반영 + 에이전트 없음 일 때만."""
        return self.state == "정리 가능" and not self.agents

    def age_text(self) -> str:
        if self.age_h is None:
            return "-"
        if self.age_h >= 48:
            return t(f"{self.age_h / 24:.0f}일", f"{self.age_h / 24:.0f}d")
        return t(f"{self.age_h:.0f}시간", f"{self.age_h:.0f}h")


ORDER = {"방치": 0, "정리 가능": 1, "오래됨": 2, "작업": 3, "정상": 4}


def worktrees() -> list[Worktree] | None:
    if not STUDIO_HOST or not WORKTREE_GLOBS:
        return None
    try:
        script = f"GLOBS = {json.dumps(WORKTREE_GLOBS)}\n" + SCAN
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", HOST, "python3", "-"],
                           input=script, capture_output=True, text=True, timeout=60)
        items = [Worktree(**d) for d in json.loads(r.stdout)]
    except Exception:
        return None
    return sorted(items, key=lambda w: (ORDER[w.state], -(w.dirty or 0), w.repo, w.name))


# ---------- 동작 ----------
def _ssh(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", HOST,
                           "export PATH=$HOME/.local/bin:/opt/homebrew/bin:$PATH; " + cmd],
                          capture_output=True, text=True, timeout=timeout)


def open_in_herdr(w: Worktree, space_id: str | None) -> bool:
    if w.agents:
        target = f"{HOST}/{w.agents[0]['address']}"
        r = subprocess.run([HERDR_APP, "do", "agent.focus", "--terminal", target], capture_output=True, text=True)
    else:
        args = [HERDR_APP, "do", "tab.new", "--name", w.name, "--folder", w.path]
        if space_id:
            args += ["--space", space_id]
        r = subprocess.run(args, capture_output=True, text=True)
    subprocess.run(["open", "-a", "Herdr"])
    return '"ok": true' in r.stdout


def remove_worktree(w: Worktree) -> tuple[bool, str]:
    if not w.can_remove:
        return False, t("정리 가능 상태가 아님", "not ready to remove")
    # main 저장소에서 제거 (강제 아님: 변경이 있으면 git 이 거부)
    r = _ssh(f"git -C {shlex.quote(w.path)} worktree remove {shlex.quote(w.path)}")
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:200]


CLEANUP_LEAD = STUDIO.get("cleanup_lead", "worktree-lead-claude")  # 저장소 담당이 없을 때 받을 에이전트 이름
AGENT_KINDS = ("claude-code", "codex", "antigravity")


def pick_target(terms: list[dict], repo_root: str, repo_name: str, lead_name: str = "", lead_addr: str = "") -> dict | None:
    """정리 요청을 받을 살아 있는 에이전트: 그 저장소 폴더의 에이전트 > 정리 담당(이름) > 설정의 고정 주소.
    같은 순위면 일하는 중이 아닌 쪽. 다른 컴퓨터(주소에 '/')의 터미널은 고르지 않는다 (워크트리는 이 컴퓨터에 있음)."""
    live = [t for t in terms if t.get("state") == "live" and t.get("agent") in AGENT_KINDS and "/" not in t.get("address", "")]

    def same_repo(t: dict) -> bool:
        cwd = (t.get("cwd") or "").rstrip("/")
        return bool(cwd) and (cwd == repo_root.rstrip("/") or cwd.rsplit("/", 1)[-1] == repo_name)

    for group in ([t for t in live if same_repo(t)], [t for t in live if lead_name and t.get("name") == lead_name],
                  [t for t in live if lead_addr and t.get("address") == lead_addr]):
        if group:
            return next((t for t in group if t.get("status") != "working"), group[0])
    return None


def _label(t: dict) -> str:
    where = (t.get("where") or "").split(" / ")[-1]
    return f"{where or t.get('name') or t['address']}({t['address']})"


def ask_tower(w: Worktree) -> tuple[bool, str]:
    ahead = w.ahead if w.ahead is not None else "?"
    msg = t(f"[D200 워크트리 정리 요청] {w.path} (브랜치 {w.branch}, 커밋 안 한 변경 {w.dirty}개, "
            f"기준 {w.base or '없음'} 대비 앞선 커밋 {ahead}개, "
            f"마지막 커밋 {w.age_text()} 전). 상태를 확인하고 필요한 커밋·머지·정리를 진행해줘. "
            f"삭제나 되돌릴 수 없는 작업은 실행 전에 나에게 먼저 확인받아줘.",
            f"[D200 worktree cleanup request] {w.path} (branch {w.branch}, {w.dirty} uncommitted changes, "
            f"{ahead} commits ahead of {w.base or 'none'}, "
            f"last commit {w.age_text()} ago). Check its state and do the needed commit/merge/cleanup. "
            f"Ask me before anything that deletes data or can't be undone.")
    root = _ssh(f"git -C {shlex.quote(w.path)} rev-parse --path-format=absolute --git-common-dir").stdout.strip()
    root = root[:-5] if root.endswith("/.git") else w.path  # 원래 저장소 폴더 (못 찾으면 워크트리 폴더)
    try:
        terms = json.loads(_ssh("herdr-core ls --json").stdout).get("terminals", [])
    except (ValueError, AttributeError):
        terms = []
    target = pick_target(terms, root, w.repo, CLEANUP_LEAD, LEAD_PANE)
    if target:  # 살아 있는 담당 에이전트에게 보낸다
        r = _ssh(f"herdr-core send {shlex.quote(target['address'])} {shlex.quote(msg)}")
        return r.returncode == 0, (t(f"{_label(target)} 에게 보냄", f"sent to {_label(target)}") if r.returncode == 0
                                   else (r.stderr or r.stdout).strip()[:200])
    # 받을 세션이 모두 닫혀 있으면: 그 저장소에서 새 Claude Code 세션을 띄워 같은 요청을 맡긴다
    name = "wt-cleanup-" + re.sub(r"[^a-z0-9_-]", "-", w.name.lower())[:18] + time.strftime("-%H%M")
    r = _ssh(f"herdr-core run --bg --no-parent --json --name {shlex.quote(name)} --cwd {shlex.quote(root)} "
             f"--label purpose=wt-cleanup -- claude {shlex.quote(msg)}")
    try:
        addr = json.loads(r.stdout).get("address", name)
    except ValueError:
        addr = name
    return r.returncode == 0, (t(f"새 세션 {addr} 에서 정리 시작", f"started new session {addr}") if r.returncode == 0
                               else (r.stderr or r.stdout).strip()[:200])


if __name__ == "__main__":
    for w in worktrees() or []:
        print(f"{w.state:6} {w.repo}/{w.name} [{w.branch}] dirty {w.dirty} ahead {w.ahead} ({w.base}) {w.age_text()} agents {len(w.agents)}")
