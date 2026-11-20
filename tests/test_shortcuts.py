"""바로가기 페이지: 홈 칸, 칸 그리기, 열기, 넓은 칸으로 열 개씩 넘기기 (open 은 가짜로)."""

import unittest
from unittest.mock import patch

import present
import shortcuts
from shortcuts import State


def _cfg(n, **extra):
    return {"shortcuts": {"items": [{"name": f"사이트{i}", "open": f"https://s{i}.example.com"}
                                    for i in range(1, n + 1)], **extra}}


class HomeTest(unittest.TestCase):
    def test_empty_config_has_no_home_item(self):
        for cfg in ({}, {"shortcuts": {}}, {"shortcuts": {"items": []}}, {"shortcuts": None},
                    {"shortcuts": {"items": [{"name": "열 곳 없음"}]}}):
            self.assertEqual(shortcuts.items(cfg), [])
            self.assertIsNone(shortcuts.home_item(19, cfg))

    def test_home_item_title_and_count(self):
        self.assertEqual(shortcuts.home_item(19, _cfg(3)), (19, "바로가기", "3곳", "sparkle", shortcuts.ACCENT))
        self.assertEqual(shortcuts.home_item(19, _cfg(2, title="포트폴리오"))[1:3], ("포트폴리오", "2곳"))


class PageTest(unittest.TestCase):
    def test_three_items_render_and_open(self):
        st, its = State(), shortcuts.items(_cfg(3))
        b = shortcuts.page(st, its)
        self.assertEqual(set(b), set(range(10)))
        self.assertEqual([b[k].title for k in range(3)], ["사이트1", "사이트2", "사이트3"])
        self.assertEqual([b[k].header for k in range(3)], ["1", "2", "3"])
        self.assertEqual((b[3].title, b[9].title), ("", ""))
        with patch.object(present, "open_target", return_value=True) as o:
            self.assertTrue(shortcuts.press(1, st, its))
            o.assert_called_once_with("https://s2.example.com")
            self.assertFalse(shortcuts.press(5, st, its))  # 빈 칸은 아무 일 없음
            self.assertFalse(shortcuts.press(13, st, its))  # 열 개 이하면 넓은 칸도 아무 일 없음
            o.assert_called_once()
        self.assertEqual(st.page, 0)
        self.assertTrue(shortcuts.wide(st, its, _cfg(3)).startswith(b"\x89PNG"))

    def test_twenty_three_items_page_via_wide_tile(self):
        st, its = State(), shortcuts.items(_cfg(23))
        seen = []
        for _ in range(4):
            b = shortcuts.page(st, its)
            seen.append((st.page % st.pages(len(its)) + 1, b[0].title, sum(1 for k in b if b[k].title)))
            self.assertTrue(shortcuts.wide(st, its, _cfg(23)).startswith(b"\x89PNG"))
            self.assertTrue(shortcuts.press(14, st, its))
        self.assertEqual(seen, [(1, "사이트1", 10), (2, "사이트11", 10), (3, "사이트21", 3), (1, "사이트1", 10)])
        st.page = 2
        with patch.object(present, "open_target", return_value=True) as o:
            shortcuts.press(2, st, its)
            self.assertFalse(shortcuts.press(3, st, its))
        o.assert_called_once_with("https://s23.example.com")

    def test_failed_open_shows_message(self):
        st, its = State(), shortcuts.items(_cfg(1))
        with patch.object(present, "open_target", return_value=False):
            self.assertTrue(shortcuts.press(0, st, its))
        self.assertEqual(st.msg, ("열지 못했어요", "https://s1.example.com"))
        self.assertTrue(shortcuts.wide(st, its, _cfg(1)).startswith(b"\x89PNG"))

    def test_sub_defaults_to_host(self):
        its = [{"name": "A", "open": "https://www.example.com/path"}, {"name": "B", "sub": "메모", "open": "https://b.example"},
               {"name": "C", "open": "/Applications/Notes.app"}]
        b = shortcuts.page(State(), its)
        self.assertEqual((b[0].sub, b[1].sub, b[2].sub), ("example.com", "메모", "Notes"))


if __name__ == "__main__":
    unittest.main()
