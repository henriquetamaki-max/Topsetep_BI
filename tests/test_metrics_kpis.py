"""Tests para `metrics.compute_kpis` — 13 KPIs em pontos.

KPIs alimentam o card de "Visão geral" do Dashboard e os comparativos
internos do app. Ports diretos das formulas do TradePontos (`processor.py`
linhas 252-271). Asseguramos shape do dict, divisao por zero protegida
(mean_loss=0 quando sem losers, etc.) e diferenca entre contadores trade-a-trade
vs. agregacao por grupo (`win_rate_grouped`).
"""
from __future__ import annotations

import unittest

import pandas as pd


import metrics


def _trades(points_list: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"points": points_list})


def _groups(statuses: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"points_status": statuses})


EXPECTED_KEYS = {
    "total_net_points",
    "total_winning_points",
    "total_losing_points",
    "avg_points_per_trade",
    "avg_winning_trade_points",
    "avg_losing_trade_points",
    "trade_count",
    "winning_trade_count",
    "losing_trade_count",
    "rr_average",
    "rr_aggregate",
    "total_grouped_operations",
    "win_rate_grouped",
}


class ComputeKpisEmptyTests(unittest.TestCase):

    def test_empty_df_returns_zeroed_dict(self):
        out = metrics.compute_kpis(pd.DataFrame(), pd.DataFrame())
        # Schema completo, todos os valores em 0
        self.assertEqual(set(out.keys()), EXPECTED_KEYS)
        for k, v in out.items():
            self.assertEqual(v, 0 if isinstance(v, int) else 0.0, f"key={k}")

    def test_empty_df_with_nonempty_groups_still_zeroed(self):
        # df vazio dispara early return ignorando o groups passado
        out = metrics.compute_kpis(pd.DataFrame(), _groups(["Winner", "Loser"]))
        self.assertEqual(out["total_grouped_operations"], 0)
        self.assertEqual(out["win_rate_grouped"], 0.0)


class ComputeKpisCountsTests(unittest.TestCase):

    def test_trade_count_is_len_df(self):
        out = metrics.compute_kpis(_trades([1.0, -2.0, 3.0]), _groups([]))
        self.assertEqual(out["trade_count"], 3)

    def test_winners_losers_partitioned_by_sign(self):
        out = metrics.compute_kpis(_trades([1.0, -2.0, 3.0]), _groups([]))
        self.assertEqual(out["winning_trade_count"], 2)
        self.assertEqual(out["losing_trade_count"], 1)

    def test_flat_trades_excluded_from_winners_and_losers(self):
        # points=0.0 nao conta como winner nem loser.
        out = metrics.compute_kpis(_trades([1.0, 0.0, -2.0, 0.0]), _groups([]))
        self.assertEqual(out["trade_count"], 4)
        self.assertEqual(out["winning_trade_count"], 1)
        self.assertEqual(out["losing_trade_count"], 1)


class ComputeKpisSumsTests(unittest.TestCase):

    def test_total_net_points_sum_signed(self):
        out = metrics.compute_kpis(_trades([2.0, -1.0, 3.0]), _groups([]))
        self.assertAlmostEqual(out["total_net_points"], 4.0)

    def test_winning_and_losing_sums_separated(self):
        out = metrics.compute_kpis(_trades([2.0, -1.0, 3.0, -4.0]), _groups([]))
        self.assertAlmostEqual(out["total_winning_points"], 5.0)
        self.assertAlmostEqual(out["total_losing_points"], -5.0)

    def test_avg_points_per_trade_uses_full_df_including_flats(self):
        # Mean considera todos os trades, inclusive flats — alinhado com Pandas .mean()
        out = metrics.compute_kpis(_trades([2.0, 0.0, -2.0]), _groups([]))
        self.assertAlmostEqual(out["avg_points_per_trade"], 0.0)


class ComputeKpisAveragesTests(unittest.TestCase):

    def test_avg_winning_trade_points_only_over_winners(self):
        out = metrics.compute_kpis(_trades([2.0, 4.0, -1.0]), _groups([]))
        self.assertAlmostEqual(out["avg_winning_trade_points"], 3.0)

    def test_avg_losing_trade_points_returned_as_absolute(self):
        # Documenta semantica: abs(mean(losers)). Util para RR ratios.
        out = metrics.compute_kpis(_trades([2.0, -1.0, -3.0]), _groups([]))
        self.assertAlmostEqual(out["avg_losing_trade_points"], 2.0)

    def test_avg_winning_is_zero_when_no_winners(self):
        out = metrics.compute_kpis(_trades([-1.0, -2.0]), _groups([]))
        self.assertEqual(out["avg_winning_trade_points"], 0.0)

    def test_avg_losing_is_zero_when_no_losers(self):
        out = metrics.compute_kpis(_trades([1.0, 2.0]), _groups([]))
        self.assertEqual(out["avg_losing_trade_points"], 0.0)


class ComputeKpisRrRatioTests(unittest.TestCase):

    def test_rr_average_ratio_of_means(self):
        # winners mean=3.0, losers abs mean=2.0 → 1.5
        out = metrics.compute_kpis(_trades([2.0, 4.0, -1.0, -3.0]), _groups([]))
        self.assertAlmostEqual(out["rr_average"], 1.5)

    def test_rr_aggregate_ratio_of_sums(self):
        # winners sum=6.0, |losers sum|=4.0 → 1.5
        out = metrics.compute_kpis(_trades([2.0, 4.0, -1.0, -3.0]), _groups([]))
        self.assertAlmostEqual(out["rr_aggregate"], 1.5)

    def test_rr_average_is_zero_when_no_losers(self):
        # Divide-by-zero protegido — sem losers, mean_loss=0 → rr_avg=0.
        out = metrics.compute_kpis(_trades([1.0, 2.0]), _groups([]))
        self.assertEqual(out["rr_average"], 0.0)
        self.assertEqual(out["rr_aggregate"], 0.0)

    def test_rr_aggregate_is_zero_when_no_winners(self):
        # Sem winners, total_win=0 e divisao acima da linha vira 0 — protegida.
        out = metrics.compute_kpis(_trades([-1.0, -2.0]), _groups([]))
        self.assertEqual(out["rr_average"], 0.0)
        self.assertEqual(out["rr_aggregate"], 0.0)


class ComputeKpisGroupedTests(unittest.TestCase):

    def test_win_rate_grouped_counts_winner_status_only(self):
        # 2 winners de 4 grupos = 0.5
        out = metrics.compute_kpis(
            _trades([1.0, -1.0]),
            _groups(["Winner", "Loser", "Winner", "Flat"]),
        )
        self.assertEqual(out["total_grouped_operations"], 4)
        self.assertAlmostEqual(out["win_rate_grouped"], 0.5)

    def test_win_rate_grouped_is_zero_when_groups_empty(self):
        # df nao vazio mas groups sim — win_rate vai a 0 sem dividir por zero.
        out = metrics.compute_kpis(_trades([1.0]), pd.DataFrame())
        self.assertEqual(out["total_grouped_operations"], 0)
        self.assertEqual(out["win_rate_grouped"], 0.0)

    def test_win_rate_grouped_full_win(self):
        out = metrics.compute_kpis(
            _trades([1.0]),
            _groups(["Winner", "Winner", "Winner"]),
        )
        self.assertAlmostEqual(out["win_rate_grouped"], 1.0)

    def test_win_rate_grouped_no_wins(self):
        out = metrics.compute_kpis(
            _trades([-1.0]),
            _groups(["Loser", "Loser", "Flat"]),
        )
        self.assertEqual(out["win_rate_grouped"], 0.0)


class ComputeKpisSchemaTests(unittest.TestCase):

    def test_output_schema_stable_across_empty_and_nonempty(self):
        # Garante que o caller (UI) nunca enxerga KeyError independente do input.
        empty = metrics.compute_kpis(pd.DataFrame(), pd.DataFrame())
        nonempty = metrics.compute_kpis(_trades([1.0, -1.0]), _groups(["Winner"]))
        self.assertEqual(set(empty.keys()), set(nonempty.keys()))
        self.assertEqual(set(empty.keys()), EXPECTED_KEYS)

    def test_counts_are_int_metrics_are_float(self):
        out = metrics.compute_kpis(_trades([1.0, -1.0]), _groups(["Winner"]))
        int_keys = {
            "trade_count", "winning_trade_count", "losing_trade_count",
            "total_grouped_operations",
        }
        for k in int_keys:
            self.assertIsInstance(out[k], int, f"key={k}")
        for k in EXPECTED_KEYS - int_keys:
            self.assertIsInstance(out[k], float, f"key={k}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
