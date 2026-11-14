"""타이쿤 추가 규칙: 랜드마크(3단계), 보관 카드(행운 카드), 조합 시너지, 연출 중 말판 표시."""

import io
import random
import unittest

from PIL import Image

import tycoon as ty
from tycoon import CELLS, Game, Tycoon

IDX = {c.art: i for i, c in enumerate(CELLS)}


def own(g: Game, who: int, *arts: str) -> None:
    for a in arts:
        g.owner[IDX[a]] = who
        g.level[IDX[a]] = 0


class LandmarkTest(unittest.TestCase):
    def test_three_levels_and_costs(self):
        g = Game(random.Random(1))
        i = IDX["chatbot"]  # 가격 100
        g.players[0].cash = 10_000
        self.assertTrue(g.buy(i))
        costs = []
        for _ in range(3):
            before = g.players[0].cash
            self.assertTrue(g.upgrade(i))
            costs.append(before - g.players[0].cash)
        self.assertEqual(costs, [50, 50, 100])  # 증설 둘 + 랜드마크는 가격만큼
        self.assertFalse(g.upgrade(i))  # 최대
        self.assertEqual(g.rent_at(i), 50 * 9)
        self.assertEqual(g.assets(0), g.players[0].cash + 100 + 200)


class KeepCardTest(unittest.TestCase):
    def test_keep_card_goes_to_items(self):
        g = Game(random.Random(1))
        shield = next(c for c in ty.CARDS if c.item == "shield")
        g.apply_card(shield)
        self.assertEqual(g.players[0].items, ["shield"])

    def test_shield_waives_one_fee(self):
        g = Game(random.Random(1))
        own(g, 1, "agi")
        g.players[0].items = ["shield"]
        self.assertEqual(g.pay_rent(IDX["agi"]), 0)
        self.assertTrue(g.shielded)
        self.assertGreater(g.pay_rent(IDX["agi"]), 0)  # 두 번째는 냄
        self.assertFalse(g.shielded)

    def test_coupon_halves_next_buy_once(self):
        g = Game(random.Random(1))
        g.players[0].items = ["coupon"]
        cash = g.players[0].cash
        self.assertEqual(g.buy_price(IDX["agi"]), 210)
        self.assertTrue(g.buy(IDX["agi"]))
        self.assertEqual(cash - g.players[0].cash, 210)
        self.assertEqual(g.buy_price(IDX["space"]), 400)

    def test_takeover_pays_owner_and_skips_landmark(self):
        g = Game(random.Random(1))
        own(g, 1, "agi")
        g.players[0].items = ["mna"]
        cost = g.takeover_cost(IDX["agi"])
        self.assertEqual(cost, 630)
        c0, c1 = g.players[0].cash, g.players[1].cash
        self.assertTrue(g.takeover(IDX["agi"]))
        self.assertEqual((g.owner[IDX["agi"]], c0 - g.players[0].cash, g.players[1].cash - c1), (0, cost, cost))
        own(g, 1, "space")
        g.level[IDX["space"]] = ty.MAX_LEVEL
        g.players[0].items = ["mna"]
        self.assertFalse(g.can_takeover(IDX["space"]))  # 랜드마크는 못 가져감


class SynergyTest(unittest.TestCase):
    def test_detection_with_any_of(self):
        g = Game(random.Random(1))
        own(g, 0, "aichip", "drone", "edge")
        self.assertIn("mobility", g.synergies(0))
        own(g, 0, "gpucloud")
        self.assertNotIn("vertical", g.synergies(0))
        own(g, 0, "datacenter")
        self.assertIn("vertical", g.synergies(0))

    def test_rent_boost_only_for_its_groups(self):
        g = Game(random.Random(1))
        own(g, 1, "voice", "imagegen")
        plain = g.rent_at(IDX["imagegen"])
        own(g, 1, "videogen")
        self.assertIn("multimodal", g.synergies(1))
        self.assertEqual(g.rent_at(IDX["imagegen"]), int(plain * 1.5))
        own(g, 1, "agi")
        self.assertEqual(g.rent_at(IDX["agi"]), CELLS[IDX["agi"]].price // 2)  # 다른 업종엔 없음

    def test_vertical_skips_tax_and_synergy_cut_shown(self):
        ty_ = Tycoon(seed=3)
        ty_.fast = True
        ty_.start()
        ty_.tick(float("inf"))
        g = ty_.g
        own(g, 0, "aichip", "gpucloud")
        g.players[0].cash = 2000
        ty_._do_choice("buy", IDX["datacenter"]) if IDX["datacenter"] not in g.owner else None
        self.assertIn("vertical", g.synergies(0))
        cash = g.players[0].cash
        ty_._arrive(next(i for i, c in enumerate(CELLS) if c.kind == "tax"))
        ty_.tick(float("inf"))
        self.assertEqual(g.players[0].cash, cash)


class PivotAndScreenTest(unittest.TestCase):
    def test_pivot_moves_to_tapped_cell(self):
        t_ = Tycoon(seed=5)
        t_.fast = True
        t_.start()
        t_.tick(float("inf"))
        self.assertEqual(t_.phase, "roll")
        t_.g.players[0].items = ["pivot"]
        t_.press(ty.BTN_B, 0)
        self.assertEqual(t_.phase, "pivot")
        t_.press(ty.WINDOW_TILES[3], 0)
        self.assertEqual(t_.g.players[0].pos, 3)
        self.assertEqual(t_.g.players[0].items, [])

    def test_board_wide_stays_during_cut(self):
        t_ = Tycoon(seed=5)
        t_.start()  # 제목 연출이 대기열에
        import time
        t_.tick(time.time() + 0.01)  # 연출이 막 시작된 시점
        self.assertIsNotNone(t_.cut)
        wide = Image.open(io.BytesIO(t_.wide()))
        self.assertEqual(wide.size, ty.WIDE)
        tiles = t_.tiles()
        self.assertIn(10, tiles)  # 연출 글은 아래 줄 왼쪽 칸에
        self.assertEqual(t_.wide(), t_._board_wide())

    def test_full_games_finish_with_new_rules(self):
        for seed in range(8):
            t_ = Tycoon(seed=seed)
            t_.start()
            t_.run_all()
            self.assertEqual(t_.phase, "over", seed)

    def test_new_cutscenes_render(self):
        data = {"who": "나", "color": (229, 72, 77), "cell": CELLS[IDX["agi"]], "synergy": ty.SYN["agi"], "line": "x",
                "landmark": True}
        for kind in ("synergy", "shield", "takeover", "upgrade"):
            out, wide = ty.split_full(ty.cut_frame(kind, 0.8, data))
            self.assertEqual(len(out), 13)


if __name__ == "__main__":
    unittest.main()
