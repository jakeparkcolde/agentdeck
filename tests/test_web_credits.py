import unittest
from datetime import datetime

import web_credits

PAGE = "Plus Plan\nYour plan renews June 30, 2027\nMonthly credits left\n771/ 1 200\n"


class HiggsfieldTest(unittest.TestCase):
    def test_parses_credits_and_next_monthly_refill(self):
        got = web_credits.parse_higgsfield({"text": PAGE}, datetime(2026, 11, 9, 15))
        self.assertEqual((got["credits"], got["total"], got["tier"]), (771, 1200, "Plus"))
        self.assertEqual(got["reset"][:10], "2026-11-30")

    def test_refill_day_clamps_to_month_end_and_renewal(self):
        r = web_credits._higgs_refill
        self.assertEqual(r(PAGE, datetime(2027, 2, 1)).date().isoformat(), "2027-02-28")
        self.assertEqual(r(PAGE, datetime(2026, 11, 30, 12)).date().isoformat(), "2026-12-30")
        self.assertIsNone(r("no renewal text", datetime(2026, 11, 9)))


if __name__ == "__main__":
    unittest.main()
