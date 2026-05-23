"""Tests para `metrics.compute_daily` — daily breakdown.

Agrega trades por `trade_day_et` (sessao NY/ET, M7 da fusao) ou
`trade_day` (legado CT do CSV) usando `_day_col`. Coluna devolvida e'
renomeada para `trade_day` em ambos os casos. Alimenta charts e tabelas
"por dia" do Dashboard.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


EXPECTED_COLUMNS = [
    "trade_day", "net_points", "winning_points", "losing_points",
    "reward_risk", "total_size", "winning_size", "losing_size",
]


def _trades(rows: list[dict], *, et_col: bool = True) -> pd.DataFrame:
    """Builda DataFrame de trades com defaults sensatos.

    et_col=True: usa `trade_day_et` (path moderno).
    et_col=False: usa `trade_day` (legado CT).
    """
    defaults = {"points": 0.0, "size": 1}
    out = []
    for r in rows:
        merged = {**defaults, **r}
        out.append(merged)
    df = pd.DataFrame(out)
    if et_col and "trade_day" in df.columns:
        df = df.rename(columns={"trade_day": "trade_day_et"})
    return df


class ComputeDailyEmptyTests(unittest.TestCase):

    def test_empty_df_returns_empty_with_schema(self):
        out = metrics.compute_daily(pd.DataFrame())
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), EXPECTED_COLUMNS)


class ComputeDailySingleDayTests(unittest.TestCase):

    def test_single_trade_single_day(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 3.0, "size": 2},
        ])
        out = metrics.compute_daily(df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["trade_day"], "2026-05-20")
        self.assertAlmostEqual(row["net_points"], 3.0)
        self.assertAlmostEqual(row["winning_points"], 3.0)
        self.assertAlmostEqual(row["losing_points"], 0.0)
        self.assertAlmostEqual(row["total_size"], 2.0)
        self.assertAlmostEqual(row["winning_size"], 2.0)
        self.assertAlmostEqual(row["losing_size"], 0.0)

    def test_winners_and_losers_in_same_day_partitioned(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 3.0, "size": 1},
            {"trade_day": "2026-05-20", "points": -2.0, "size": 3},
        ])
        out = metrics.compute_daily(df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertAlmostEqual(row["net_points"], 1.0)
        self.assertAlmostEqual(row["winning_points"], 3.0)
        self.assertAlmostEqual(row["losing_points"], -2.0)
        self.assertAlmostEqual(row["winning_size"], 1.0)
        self.assertAlmostEqual(row["losing_size"], 3.0)
        self.assertAlmostEqual(row["total_size"], 4.0)

    def test_flat_trade_excluded_from_win_loss_sizes(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 0.0, "size": 5},  # flat
            {"trade_day": "2026-05-20", "points": 1.0, "size": 2},  # win
        ])
        out = metrics.compute_daily(df)
        row = out.iloc[0]
        self.assertAlmostEqual(row["total_size"], 7.0)
        self.assertAlmostEqual(row["winning_size"], 2.0)
        self.assertAlmostEqual(row["losing_size"], 0.0)


class ComputeDailyMultipleDaysTests(unittest.TestCase):

    def test_two_days_produce_two_rows(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 2.0, "size": 1},
            {"trade_day": "2026-05-21", "points": -1.0, "size": 2},
        ])
        out = metrics.compute_daily(df)
        self.assertEqual(len(out), 2)
        self.assertEqual(
            sorted(out["trade_day"].tolist()),
            ["2026-05-20", "2026-05-21"],
        )

    def test_output_sorted_by_trade_day(self):
        # Input desordenado deve sair ordenado.
        df = _trades([
            {"trade_day": "2026-05-22", "points": 1.0},
            {"trade_day": "2026-05-20", "points": 1.0},
            {"trade_day": "2026-05-21", "points": 1.0},
        ])
        out = metrics.compute_daily(df)
        days = out["trade_day"].tolist()
        self.assertEqual(days, sorted(days))

    def test_groupby_isolates_days(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 5.0, "size": 1},
            {"trade_day": "2026-05-20", "points": -3.0, "size": 2},
            {"trade_day": "2026-05-21", "points": 7.0, "size": 4},
        ])
        out = metrics.compute_daily(df).set_index("trade_day")
        self.assertAlmostEqual(out.loc["2026-05-20", "net_points"], 2.0)
        self.assertAlmostEqual(out.loc["2026-05-20", "total_size"], 3.0)
        self.assertAlmostEqual(out.loc["2026-05-21", "net_points"], 7.0)
        self.assertAlmostEqual(out.loc["2026-05-21", "total_size"], 4.0)


class ComputeDailyRewardRiskTests(unittest.TestCase):

    def test_reward_risk_ratio_of_sums(self):
        # wins=6, |losses|=2 → rr=3.0
        df = _trades([
            {"trade_day": "2026-05-20", "points": 4.0},
            {"trade_day": "2026-05-20", "points": 2.0},
            {"trade_day": "2026-05-20", "points": -2.0},
        ])
        out = metrics.compute_daily(df)
        self.assertAlmostEqual(out.iloc[0]["reward_risk"], 3.0)

    def test_reward_risk_is_zero_when_no_losers(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 4.0},
            {"trade_day": "2026-05-20", "points": 2.0},
        ])
        out = metrics.compute_daily(df)
        self.assertEqual(out.iloc[0]["reward_risk"], 0.0)

    def test_reward_risk_is_zero_when_no_winners(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": -4.0},
        ])
        out = metrics.compute_daily(df)
        self.assertEqual(out.iloc[0]["reward_risk"], 0.0)


class ComputeDailyDayColTests(unittest.TestCase):

    def test_prefers_trade_day_et_when_present(self):
        # Mesmo trade tem trade_day=CT e trade_day_et=ET com datas diferentes.
        # Funcao deve agrupar pelo ET (path moderno).
        df = pd.DataFrame({
            "trade_day": ["2026-05-19", "2026-05-19"],   # CT (legado)
            "trade_day_et": ["2026-05-20", "2026-05-20"],  # ET (preferido)
            "points": [1.0, -1.0],
            "size": [1, 1],
        })
        out = metrics.compute_daily(df)
        self.assertEqual(len(out), 1)
        # A coluna devolvida sempre se chama "trade_day", mas o conteudo
        # veio do trade_day_et (preferido por _day_col).
        self.assertEqual(out.iloc[0]["trade_day"], "2026-05-20")

    def test_falls_back_to_trade_day_when_et_missing(self):
        df = _trades([
            {"trade_day": "2026-05-20", "points": 1.0},
        ], et_col=False)
        # Sanity: confirma o input ficou com trade_day (nao foi renomeado)
        self.assertIn("trade_day", df.columns)
        self.assertNotIn("trade_day_et", df.columns)
        out = metrics.compute_daily(df)
        self.assertEqual(out.iloc[0]["trade_day"], "2026-05-20")

    def test_output_column_is_always_trade_day(self):
        # Mesmo quando o agrupamento internamente usa trade_day_et,
        # downstream (Dashboard) sempre le `trade_day`.
        df = _trades([
            {"trade_day": "2026-05-20", "points": 1.0},
        ], et_col=True)
        out = metrics.compute_daily(df)
        self.assertIn("trade_day", out.columns)
        self.assertNotIn("trade_day_et", out.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
