"""워크트리 정리 요청을 받을 에이전트 고르기 (세션이 닫혀 있어도 동작하도록)."""

import unittest

from worktrees import pick_target

ROOT = "/Users/me/machi-studio"


def term(addr, agent="claude-code", cwd="/Users/me/x", name="", status="done", state="live"):
    return {"address": addr, "agent": agent, "cwd": cwd, "name": name, "status": status, "state": state}


class PickTargetTest(unittest.TestCase):
    def test_prefers_agent_in_same_repo(self):
        terms = [term("t1", name="worktree-lead-claude"), term("t2", cwd=ROOT), term("t3", agent="zsh", cwd=ROOT)]
        self.assertEqual(pick_target(terms, ROOT, "machi-studio", "worktree-lead-claude", "t87")["address"], "t2")

    def test_idle_before_working_in_same_group(self):
        terms = [term("t2", cwd=ROOT, status="working"), term("t4", cwd=ROOT + "/")]
        self.assertEqual(pick_target(terms, ROOT, "machi-studio")["address"], "t4")

    def test_falls_back_to_cleanup_lead_then_fixed_address(self):
        terms = [term("t1", name="worktree-lead-claude"), term("t87")]
        self.assertEqual(pick_target(terms, ROOT, "machi-studio", "worktree-lead-claude", "t87")["address"], "t1")
        self.assertEqual(pick_target([term("t87")], ROOT, "machi-studio", "worktree-lead-claude", "t87")["address"], "t87")

    def test_none_when_every_session_closed(self):
        terms = [term("t87", state="finished"), term("t2", agent="zsh", cwd=ROOT), term("macmini/t1", cwd=ROOT)]
        self.assertIsNone(pick_target(terms, ROOT, "machi-studio", "worktree-lead-claude", "t87"))


if __name__ == "__main__":
    unittest.main()
