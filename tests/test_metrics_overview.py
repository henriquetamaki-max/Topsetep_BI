"""Tests para `metrics.compute_overview` — KPIs em USD estilo TopStepX.

Devolve dict de ~24 chaves usado no painel "Visao geral" do Dashboard.
Cobre PnL total, contagem de winning/losing trades, best/worst day/trade,
duracoes medias, direcao (long/short pct), day win %.
"""
from __future__ import annotations

import unittest

import pandas as pd


import metrics


EXPECTED_KEYS = {
    "total_pnl_net", "trade_count", "winning_trades", "losing_trades",
    "total_lots", "avg_winning_trade", "avg_losing_trade",
    "avg_trade_duration_sec", "avg_win_duration_sec", "avg_loss_duration_sec",
    "day_win_pct", "winning_days", "total_days", "best_day_pct_of_total",
    "best_day", "worst_day", "best_trade", "worst_trade",
    "long_pct", "short_pct", "long_count", "short_count",
}


def _trades(rows: list[dict]) -> pd.DataFrame:
    """Builda DataFrame com defaults sensatos para compute_overview.

    Campos esperados em rows: pnl_net (float), duracao_sec (int) ou
    entered_at/exited_at explicitos, type (Long/Short), trade_day (str).
    """
    defaults = {
        "id": 1,
        "contract_name": "MNQ",
        "type": "Long",
        "size": 1,
        "entry_price": 100.0,
        "exit_price": 101.0,
    }
    base = pd.Timestamp("2026-05-20 10:00:00", tz="UTC")
    out = []
    for i, r in enumerate(rows):
        merged = {**defaults, **r}
        merged["id"] = merged.get("id", i + 1)
        # Se duracao_sec foi passada, gera entered_at/exited_at.
        if "entered_at" not in merged:
            merged["entered_at"] = base
            merged["exited_at"] = base + pd.Timedelta(seconds=merged.get("duracao_sec", 60))
            merged.pop("duracao_sec", None)
        out.append(merged)
    df = pd.DataFrame(out)
    # _day_col prefere trade_day_et — usa esse para o path moderno.
    if "trade_day" in df.columns:
        df = df.rename(columns={"trade_day": "trade_day_et"})
    return df


class ComputeOverviewEmptyTests(unittest.TestCase):

    def test_empty_df_returns_complete_zeroed_schema(self):
        out = metrics.compute_overview(pd.DataFrame())
        self.assertEqual(set(out.keys()), EXPECTED_KEYS)
        # Numericos zerados
        self.assertEqual(out["total_pnl_net"], 0.0)
        self.assertEqual(out["trade_count"], 0)
        self.assertEqual(out["day_win_pct"], 0.0)
        self.assertEqual(out["best_day_pct_of_total"], 0.0)
        # Best/worst day/trade vao None.
        self.assertIsNone(out["best_day"])
        self.assertIsNone(out["worst_day"])
        self.assertIsNone(out["best_trade"])
        self.assertIsNone(out["worst_trade"])


class ComputeOverviewCountsTests(unittest.TestCase):

    def test_trade_count_equals_len(self):
        df = _trades([
            {"pnl_net": 1.0, "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "trade_day": "2026-05-20"},
            {"pnl_net": 0.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["trade_count"], 3)

    def test_winners_losers_partitioned_by_pnl_net_sign(self):
        df = _trades([
            {"pnl_net": 5.0, "trade_day": "2026-05-20"},
            {"pnl_net": -3.0, "trade_day": "2026-05-20"},
            {"pnl_net": 0.0, "trade_day": "2026-05-20"},  # flat — fora dos dois
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["winning_trades"], 1)
        self.assertEqual(out["losing_trades"], 1)

    def test_total_lots_sums_size_column(self):
        df = _trades([
            {"pnl_net": 1.0, "size": 2, "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "size": 3, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["total_lots"], 5)

    def test_total_lots_robust_to_nan_size(self):
        # pd.to_numeric(errors="coerce").fillna(0) — NaN nao explode.
        df = _trades([
            {"pnl_net": 1.0, "size": 2, "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "size": float("nan"), "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["total_lots"], 2)


class ComputeOverviewPnlTests(unittest.TestCase):

    def test_total_pnl_net_sums_signed(self):
        df = _trades([
            {"pnl_net": 5.0, "trade_day": "2026-05-20"},
            {"pnl_net": -3.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["total_pnl_net"], 2.0)

    def test_avg_winning_trade_only_over_winners(self):
        df = _trades([
            {"pnl_net": 4.0, "trade_day": "2026-05-20"},
            {"pnl_net": 6.0, "trade_day": "2026-05-20"},
            {"pnl_net": -10.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["avg_winning_trade"], 5.0)

    def test_avg_losing_trade_preserves_negative_sign(self):
        # Diferente do compute_kpis que usa abs(), compute_overview mantem o sinal.
        df = _trades([
            {"pnl_net": -4.0, "trade_day": "2026-05-20"},
            {"pnl_net": -6.0, "trade_day": "2026-05-20"},
            {"pnl_net": 10.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["avg_losing_trade"], -5.0)

    def test_avg_winning_zero_when_no_winners(self):
        df = _trades([
            {"pnl_net": -1.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["avg_winning_trade"], 0.0)

    def test_avg_losing_zero_when_no_losers(self):
        df = _trades([
            {"pnl_net": 1.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["avg_losing_trade"], 0.0)


class ComputeOverviewDurationTests(unittest.TestCase):

    def test_avg_trade_duration_mean_of_all(self):
        df = _trades([
            {"pnl_net": 1.0, "duracao_sec": 60, "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "duracao_sec": 180, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["avg_trade_duration_sec"], 120.0)

    def test_avg_win_and_loss_duration_partitioned(self):
        df = _trades([
            {"pnl_net": 1.0, "duracao_sec": 60, "trade_day": "2026-05-20"},
            {"pnl_net": 2.0, "duracao_sec": 100, "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "duracao_sec": 300, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["avg_win_duration_sec"], 80.0)
        self.assertAlmostEqual(out["avg_loss_duration_sec"], 300.0)

    def test_avg_win_loss_duration_zero_when_subset_empty(self):
        df = _trades([
            {"pnl_net": 1.0, "duracao_sec": 60, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["avg_loss_duration_sec"], 0.0)


class ComputeOverviewDailyTests(unittest.TestCase):

    def test_day_win_pct_counts_positive_days(self):
        # Dia 1: +5 (win), Dia 2: -3 (loss), Dia 3: +2 (win) → 2/3
        df = _trades([
            {"pnl_net": 5.0, "trade_day": "2026-05-20"},
            {"pnl_net": -3.0, "trade_day": "2026-05-21"},
            {"pnl_net": 2.0, "trade_day": "2026-05-22"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["winning_days"], 2)
        self.assertEqual(out["total_days"], 3)
        self.assertAlmostEqual(out["day_win_pct"], 2 / 3)

    def test_day_aggregates_multiple_trades_per_day(self):
        # Mesmo dia, 2 trades cuja soma e' negativa → dia perdedor.
        df = _trades([
            {"pnl_net": 5.0, "trade_day": "2026-05-20"},
            {"pnl_net": -10.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["total_days"], 1)
        self.assertEqual(out["winning_days"], 0)

    def test_best_day_and_worst_day_tuples(self):
        df = _trades([
            {"pnl_net": 10.0, "trade_day": "2026-05-20"},
            {"pnl_net": -5.0, "trade_day": "2026-05-21"},
            {"pnl_net": 3.0, "trade_day": "2026-05-22"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["best_day"], ("2026-05-20", 10.0))
        self.assertEqual(out["worst_day"], ("2026-05-21", -5.0))

    def test_best_day_pct_of_total_uses_grand_total(self):
        # Soma dos dias = +8. Best = +10. Ratio = 10/8 = 1.25.
        df = _trades([
            {"pnl_net": 10.0, "trade_day": "2026-05-20"},
            {"pnl_net": -5.0, "trade_day": "2026-05-21"},
            {"pnl_net": 3.0, "trade_day": "2026-05-22"},
        ])
        out = metrics.compute_overview(df)
        self.assertAlmostEqual(out["best_day_pct_of_total"], 10.0 / 8.0)

    def test_best_day_pct_zero_when_total_non_positive(self):
        # Total = -2 (negativo). Ratio protegido → 0.0.
        df = _trades([
            {"pnl_net": 1.0, "trade_day": "2026-05-20"},
            {"pnl_net": -3.0, "trade_day": "2026-05-21"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["best_day_pct_of_total"], 0.0)


class ComputeOverviewDirectionTests(unittest.TestCase):

    def test_long_short_counts_and_pcts(self):
        df = _trades([
            {"pnl_net": 1.0, "type": "Long", "trade_day": "2026-05-20"},
            {"pnl_net": 2.0, "type": "Long", "trade_day": "2026-05-20"},
            {"pnl_net": -1.0, "type": "Short", "trade_day": "2026-05-20"},
            {"pnl_net": -2.0, "type": "Short", "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["long_count"], 2)
        self.assertEqual(out["short_count"], 2)
        self.assertAlmostEqual(out["long_pct"], 0.5)
        self.assertAlmostEqual(out["short_pct"], 0.5)

    def test_only_longs_short_pct_zero(self):
        df = _trades([
            {"pnl_net": 1.0, "type": "Long", "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        self.assertEqual(out["short_count"], 0)
        self.assertEqual(out["short_pct"], 0.0)
        self.assertAlmostEqual(out["long_pct"], 1.0)


class ComputeOverviewBestWorstTradeTests(unittest.TestCase):

    def test_best_and_worst_trade_dict_shape(self):
        df = _trades([
            {"id": 1, "pnl_net": 5.0, "trade_day": "2026-05-20",
             "contract_name": "MNQ", "type": "Long", "size": 2,
             "entry_price": 100.0, "exit_price": 105.0},
            {"id": 2, "pnl_net": -3.0, "trade_day": "2026-05-20",
             "contract_name": "MES", "type": "Short", "size": 1,
             "entry_price": 200.0, "exit_price": 203.0},
        ])
        out = metrics.compute_overview(df)
        best = out["best_trade"]
        worst = out["worst_trade"]
        self.assertEqual(best["id"], 1)
        self.assertEqual(best["contract_name"], "MNQ")
        self.assertEqual(best["pnl_net"], 5.0)
        self.assertEqual(worst["id"], 2)
        self.assertEqual(worst["contract_name"], "MES")
        self.assertEqual(worst["pnl_net"], -3.0)

    def test_best_and_worst_trade_share_keys(self):
        df = _trades([
            {"pnl_net": 1.0, "trade_day": "2026-05-20"},
        ])
        out = metrics.compute_overview(df)
        expected = {"id", "contract_name", "type", "size", "entry_price",
                    "exit_price", "pnl_net", "entered_at", "exited_at"}
        self.assertEqual(set(out["best_trade"].keys()), expected)
        self.assertEqual(set(out["worst_trade"].keys()), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
