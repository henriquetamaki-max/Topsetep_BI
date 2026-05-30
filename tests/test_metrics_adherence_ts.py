"""Tests para metrics.compute_adherence_timeseries (M16 — score ponderado +
janelas semanal/mensal + streak). Input = by_day no formato de compute_risk_review.
"""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import metrics


def _byday(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        dims = r.get("dims", {})
        has_plan = r.get("has_plan", True)
        blow = r.get("blowout", False)
        viol = any(dims.get(k) == "viol" for k in
                   ("dim_trades", "dim_loss", "dim_size", "dim_stop")) or blow
        out.append({
            "trade_day": r["day"],
            "dim_trades": dims.get("dim_trades"),
            "dim_loss": dims.get("dim_loss"),
            "dim_size": dims.get("dim_size"),
            "dim_stop": dims.get("dim_stop"),
            "blowout": blow,
            "has_plan": has_plan,
            "clean": bool(has_plan and not viol),
        })
    return pd.DataFrame(out)


def _score_for(rows: list[dict]):
    out = metrics.compute_adherence_timeseries(_byday(rows))
    return out["daily"].iloc[0]["score"]


class EmptyTests(unittest.TestCase):
    def test_empty(self):
        out = metrics.compute_adherence_timeseries(pd.DataFrame())
        self.assertTrue(out["daily"].empty)
        self.assertTrue(out["weekly"].empty)
        self.assertEqual(out["streak"]["current_clean"], 0)
        self.assertEqual(out["weights"]["blowout"], 40)


class DayScoreTests(unittest.TestCase):
    def test_clean_is_100(self):
        self.assertEqual(_score_for([{"day": date(2026, 5, 28), "dims": {}}]), 100.0)

    def test_dll_plus_stop_is_55(self):
        sc = _score_for([{"day": date(2026, 5, 28),
                          "dims": {"dim_loss": "viol", "dim_stop": "viol"}}])
        self.assertEqual(sc, 55.0)   # 100 - 30 - 15

    def test_all_violations_floor_zero(self):
        sc = _score_for([{"day": date(2026, 5, 28),
                          "dims": {"dim_trades": "viol", "dim_loss": "viol",
                                   "dim_size": "viol", "dim_stop": "viol"},
                          "blowout": True}])
        self.assertEqual(sc, 0.0)    # 100 - 105 -> 0


class WindowTests(unittest.TestCase):
    def test_weekly_mean_and_counts(self):
        # 2026-05-28 e 2026-05-29 caem na mesma semana ISO. Scores 100 e 55.
        rows = [
            {"day": date(2026, 5, 28), "dims": {}},
            {"day": date(2026, 5, 29), "dims": {"dim_loss": "viol", "dim_stop": "viol"}},
        ]
        out = metrics.compute_adherence_timeseries(_byday(rows))
        wk = out["weekly"]
        self.assertEqual(len(wk), 1)
        self.assertEqual(wk.iloc[0]["score"], 77.5)
        self.assertEqual(wk.iloc[0]["n_days"], 2)
        self.assertEqual(wk.iloc[0]["max_loss"], 1)
        self.assertEqual(wk.iloc[0]["stop"], 1)

    def test_monthly_groups(self):
        rows = [
            {"day": date(2026, 4, 30), "dims": {}},
            {"day": date(2026, 5, 1), "dims": {}},
        ]
        out = metrics.compute_adherence_timeseries(_byday(rows))
        self.assertEqual(len(out["monthly"]), 2)
        self.assertListEqual(list(out["monthly"]["period"]), ["2026-04", "2026-05"])


class StreakTests(unittest.TestCase):
    def test_streak_counts_trailing_clean_and_breaks(self):
        rows = [
            {"day": date(2026, 5, 25), "dims": {}},                       # clean
            {"day": date(2026, 5, 26), "dims": {"dim_loss": "viol"}},     # viol
            {"day": date(2026, 5, 27), "dims": {}},                       # clean
            {"day": date(2026, 5, 28), "dims": {}},                       # clean
        ]
        out = metrics.compute_adherence_timeseries(_byday(rows))
        self.assertEqual(out["streak"]["current_clean"], 2)

    def test_streak_skips_no_plan(self):
        rows = [
            {"day": date(2026, 5, 27), "dims": {}},                       # clean
            {"day": date(2026, 5, 28), "has_plan": False},                # sem plano
        ]
        out = metrics.compute_adherence_timeseries(_byday(rows))
        self.assertEqual(out["streak"]["current_clean"], 1)


class NoPlanTests(unittest.TestCase):
    def test_no_plan_excluded_from_window(self):
        rows = [
            {"day": date(2026, 5, 28), "dims": {}},
            {"day": date(2026, 5, 29), "has_plan": False},
        ]
        out = metrics.compute_adherence_timeseries(_byday(rows))
        self.assertEqual(out["weekly"].iloc[0]["n_days"], 1)   # só o dia com plano


if __name__ == "__main__":
    unittest.main()
