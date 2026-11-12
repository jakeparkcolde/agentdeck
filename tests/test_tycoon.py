import importlib.util
import random
import unittest

HAS_DEPS = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(HAS_DEPS, "requires Pillow")
class TycoonRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tycoon
        cls.ty = tycoon

    def game(self, seed=1):
        return self.ty.Game(random.Random(seed))

    def test_board_shape(self):
        cells = self.ty.CELLS
        self.assertEqual(len(cells), 32)
        self.assertEqual(sorted(self.ty.WINDOW_TILES), sorted(self.ty.BOARD_KEYS))  # 판 창 8칸 = 판 버튼
        self.assertEqual(self.ty.window_start(0), 31)  # 말 한 칸 뒤부터
        for i in range(0, 32, 4):
            self.assertNotEqual(cells[i].kind, "company")  # 네 칸마다 특수 칸
        for grp in range(8):
            self.assertEqual(sum(1 for c in cells if c.group == grp), 3)

    def test_buy_rent_monopoly_and_upgrade(self):
        g = self.game()
        for i in (1, 2, 3):  # 업종 0 전부
            self.assertTrue(g.buy(i))
        self.assertTrue(g.monopoly(0, 0))
        self.assertEqual(g.rent_at(1), (100 // 2) * 2)
        self.assertTrue(g.upgrade(1))
        self.assertEqual(g.rent_at(1), 50 * 3 * 2)
        g.turn = 1
        before = g.players[1].cash
        paid = g.pay_rent(1)
        self.assertEqual(g.players[1].cash, before - paid)
        self.assertEqual(g.pay_rent(4), 0)  # 찬스 칸은 사용료 없음

    def test_pass_start_bonus(self):
        g = self.game()
        g.cur.pos = 30
        self.assertTrue(g.move_to(2))
        self.assertEqual(g.cur.cash, self.ty.START_CASH + self.ty.PASS_BONUS)
        self.assertFalse(g.move_to(5))

    def test_debt_sells_then_bankrupts(self):
        g = self.game()
        g.buy(1); g.buy(2)
        g.cur.cash = -40
        sold = g.settle_debt(0)
        self.assertEqual(sold, [1])
        self.assertFalse(g.cur.out)
        g.cur.cash = -10_000
        g.settle_debt(0)
        self.assertTrue(g.cur.out)
        self.assertNotIn(2, g.owner)

    def test_cards(self):
        g = self.game()
        each = next(c for c in self.ty.CARDS if c.kind == "each")
        g.apply_card(each)
        self.assertEqual(g.players[0].cash, self.ty.START_CASH + 100)
        out = next(c for c in self.ty.CARDS if c.kind == "outage")
        g.apply_card(out)
        self.assertTrue(g.cur.skip)
        self.assertEqual(self.ty.CELLS[g.cur.pos].kind, "outage")

    def test_full_games_finish(self):
        for seed in range(20):
            t = self.ty.Tycoon(seed=seed)
            t.fast = True
            t.start()
            t.run_all()
            self.assertEqual(t.phase, "over", seed)
            g = t.g
            self.assertTrue(g.over())
            self.assertLessEqual(g.rounds, self.ty.MAX_ROUNDS)

    def test_renders(self):
        t = self.ty.Tycoon(seed=3)
        tiles = t.tiles()
        self.assertEqual(sorted(tiles), list(range(10)))
        self.assertTrue(t.wide().startswith(b"\x89PNG"))
        img = self.ty.cut_frame("dice", 1.0, {"dice": (3, 4), "who": "x"})
        full, wide = self.ty.split_full(img)
        self.assertEqual(sorted(full), list(range(13)))
        self.assertTrue(t.menu_tile().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
