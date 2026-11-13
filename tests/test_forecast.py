"""한도 경고: 너무 빨리 씀(리셋 전 바닥) / 다 못 씀(리셋 때 많이 남음)."""

import unittest
from datetime import datetime, timedelta, timezone

import pages
from pages import LLM, Limit, forecast, forecast_text

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
WEEK, H5 = timedelta(days=7), timedelta(hours=5)


def lim(remaining, days_left=None, hours_left=None, window=WEEK):
    left = timedelta(days=days_left) if days_left is not None else timedelta(hours=hours_left)
    return Limit(remaining, NOW + left, window)


class ForecastTest(unittest.TestCase):
    def test_too_fast(self):
        # 이틀 지났는데 60% 씀 → 하루 30% 속도, 40% 남음 → 약 1.3일 뒤 바닥 (리셋은 5일 뒤)
        f = forecast(lim(0.4, days_left=5), NOW)
        self.assertEqual(f.kind, "fast")
        self.assertAlmostEqual(f.hours, 32, delta=1)
        self.assertEqual(forecast_text(f), "32시간 뒤 바닥")

    def test_on_track_is_quiet(self):
        self.assertIsNone(forecast(lim(0.5, days_left=3.5), NOW, waste=True))  # 절반 지나 절반 남음

    def test_ends_just_before_reset_is_fine(self):
        # 리셋 직전에 딱 바닥나는 정도(여유 10% 안쪽)는 경고하지 않는다
        self.assertIsNone(forecast(lim(0.27, days_left=2), NOW))

    def test_waste_near_reset(self):
        # 남은 기간 20%인데 60% 남음 → 지금 속도면 리셋 때 ~50% 남음
        f = forecast(lim(0.6, days_left=1.4), NOW, waste=True)
        self.assertEqual(f.kind, "waste")
        self.assertEqual(forecast_text(f), "리셋 때 ~50% 남음")

    def test_waste_only_when_asked_and_near_reset(self):
        self.assertIsNone(forecast(lim(0.6, days_left=1.4), NOW))  # 5H 칸처럼 waste=False
        self.assertIsNone(forecast(lim(0.9, days_left=4), NOW, waste=True))  # 아직 기간이 많이 남음

    def test_untouched_window_near_reset_is_waste(self):
        f = forecast(lim(1.0, days_left=1), NOW, waste=True)
        self.assertEqual((f.kind, round(f.leftover, 2)), ("waste", 1.0))

    def test_ignores_early_noise_and_bad_data(self):
        self.assertIsNone(forecast(lim(0.8, hours_left=4.8, window=H5), NOW))  # 시작 직후 (4% 경과)
        self.assertIsNone(forecast(lim(None, days_left=3), NOW))
        self.assertIsNone(forecast(Limit(0.5, None, WEEK), NOW))
        self.assertIsNone(forecast(lim(0.5, days_left=-1), NOW))  # 이미 리셋 시각 지남

    def test_days_text_for_long_horizon(self):
        f = pages.Forecast("fast", hours=72)
        self.assertEqual(forecast_text(f), "3일 뒤 바닥")

    def test_tiles_show_warning(self):
        fast = LLM("CODEX", "#10a37f", week=lim(0.4, days_left=5))
        waste = LLM("AGY", "#7aa2f7", week=lim(0.6, days_left=1.4))
        calm = LLM("CLAUDE", "#d97757", week=lim(0.5, days_left=3.5))
        p = pages.page_summary([calm, fast, waste], pages.StudioState(False, []), NOW)
        self.assertEqual((p[5].title, p[6].title, p[7].title)[1:], ("바닥 예상", "남음 예상"))
        self.assertEqual((p[6].border, p[7].border), (pages.RED, pages.YELLOW))
        self.assertEqual((p[1].border, p[2].border, p[0].border), (pages.RED, pages.YELLOW, None))
        g = pages.page_grid([calm, fast, waste], NOW)
        self.assertEqual(g[6].sub, "32시간 뒤 바닥")
        self.assertEqual(g[7].sub, "리셋 때 ~50% 남음")
        self.assertIsNone(g[5].border)

    def test_english(self):
        import i18n
        old = i18n.LANG
        i18n.LANG = "en"
        try:
            self.assertEqual(i18n.tr("32시간 뒤 바닥"), "empty in 32h")
            self.assertEqual(i18n.tr("리셋 때 ~50% 남음"), "~50% left at reset")
            self.assertEqual(i18n.tr("바닥 예상"), "Runs out")
        finally:
            i18n.LANG = old


if __name__ == "__main__":
    unittest.main()
