"""Tests para `metrics.compute_duration_buckets` — 11 buckets de duracao.

Replica o painel "Trade Duration Analysis" do TopStepX. Cada bucket conta
trades cujo `exited_at - entered_at` cai no intervalo `[lo, hi)` em segundos.
Win = `pnl_net > 0`; `win_rate = wins / trades` (0.0 se trades=0).

Buckets (segundos):
  Under 15 sec        [0,        15)
  15-45 sec           [15,       45)
  45 sec - 1 min      [45,       60)
  1 min - 2 min       [60,      120)
  2 min - 5 min       [120,     300)
  5 min - 10 min      [300,     600)
  10 min - 30 min    [600,    1800)
  30 min - 1 hour    [1800,   3600)
  1 hour - 2 hours   [3600,   7200)
  2 hours - 4 hours  [7200,  14400)
  4 hours and up    [14400,    inf)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


EXPECTED_COLUMNS = ["bucket", "trades", "wins", "win_rate"]
EXPECTED_BUCKETS = [label for label, _, _ in metrics.DURATION_BUCKETS]


def _trades(rows: list[dict]) -> pd.DataFrame:
    """Builda DataFrame com entered_at/exited_at e pnl_net.

    Cada `row` precisa de `duration_sec` (int/float) e `pnl_net`. Helper
    constroi entered_at fixo e calcula exited_at via Timedelta.
    """
    base = pd.Timestamp("2026-05-20 10:00:00")
    out = []
    for r in rows:
        dur = r["duration_sec"]
        out.append({
            "entered_at": base,
            "exited_at": base + pd.Timedelta(seconds=dur),
            "pnl_net": r["pnl_net"],
        })
    return pd.DataFrame(out)


class ComputeDurationBucketsEmptyTests(unittest.TestCase):

    def test_empty_df_returns_empty_with_schema(self):
        out = metrics.compute_duration_buckets(pd.DataFrame())
        self.assertTrue(out.empty)
        self.assertEqual(list(out.columns), EXPECTED_COLUMNS)


class ComputeDurationBucketsShapeTests(unittest.TestCase):

    def test_output_always_has_11_buckets_when_df_nonempty(self):
        df = _trades([{"duration_sec": 60, "pnl_net": 10.0}])
        out = metrics.compute_duration_buckets(df)
        self.assertEqual(len(out), 11)
        self.assertEqual(out["bucket"].tolist(), EXPECTED_BUCKETS)

    def test_buckets_with_no_trades_have_zero_counts_and_zero_win_rate(self):
        # Trade so' em "1 min - 2 min" → 10 buckets ficam zerados.
        df = _trades([{"duration_sec": 60, "pnl_net": 10.0}])
        out = metrics.compute_duration_buckets(df)
        empty_buckets = out[out["bucket"] != "1 min - 2 min"]
        self.assertTrue((empty_buckets["trades"] == 0).all())
        self.assertTrue((empty_buckets["wins"] == 0).all())
        self.assertTrue((empty_buckets["win_rate"] == 0.0).all())


class ComputeDurationBucketsClassificationTests(unittest.TestCase):

    def _bucket_for(self, df, label):
        out = metrics.compute_duration_buckets(df)
        return out[out["bucket"] == label].iloc[0]

    def test_under_15_sec(self):
        df = _trades([
            {"duration_sec": 5, "pnl_net": 10.0},
            {"duration_sec": 14, "pnl_net": -1.0},
        ])
        row = self._bucket_for(df, "Under 15 sec")
        self.assertEqual(row["trades"], 2)
        self.assertEqual(row["wins"], 1)
        self.assertAlmostEqual(row["win_rate"], 0.5)

    def test_lower_boundary_inclusive(self):
        # duration_sec == lo → entra no bucket (>= lo).
        df = _trades([{"duration_sec": 15, "pnl_net": 10.0}])
        row = self._bucket_for(df, "15-45 sec")
        self.assertEqual(row["trades"], 1)
        # E nao deveria ter contado no bucket anterior:
        prev = self._bucket_for(df, "Under 15 sec")
        self.assertEqual(prev["trades"], 0)

    def test_upper_boundary_exclusive(self):
        # duration_sec == hi → NAO entra no bucket atual (< hi),
        # cai no bucket seguinte.
        df = _trades([{"duration_sec": 60, "pnl_net": 10.0}])
        row = self._bucket_for(df, "1 min - 2 min")  # [60, 120)
        self.assertEqual(row["trades"], 1)
        prev = self._bucket_for(df, "45 sec - 1 min")  # [45, 60)
        self.assertEqual(prev["trades"], 0)

    def test_four_hours_and_up_open_ended(self):
        # 5h, 10h, 24h, ... todos caem no ultimo bucket.
        df = _trades([
            {"duration_sec": 5 * 3600, "pnl_net": 10.0},
            {"duration_sec": 10 * 3600, "pnl_net": -5.0},
            {"duration_sec": 24 * 3600, "pnl_net": 3.0},
        ])
        row = self._bucket_for(df, "4 hours and up")
        self.assertEqual(row["trades"], 3)
        self.assertEqual(row["wins"], 2)

    def test_classification_spans_all_buckets(self):
        # Um trade em cada bucket — todos com 1 trade.
        midpoints = [7, 30, 50, 90, 200, 450, 1200, 2700, 5400, 10800, 20000]
        df = _trades([
            {"duration_sec": d, "pnl_net": 1.0} for d in midpoints
        ])
        out = metrics.compute_duration_buckets(df)
        self.assertTrue((out["trades"] == 1).all())
        self.assertEqual(out["trades"].sum(), 11)


class ComputeDurationBucketsWinRateTests(unittest.TestCase):

    def test_pnl_net_zero_is_not_a_win(self):
        # win exige pnl_net > 0; flat (== 0) entra em trades mas nao em wins.
        df = _trades([
            {"duration_sec": 60, "pnl_net": 0.0},
            {"duration_sec": 70, "pnl_net": 1.0},
        ])
        out = metrics.compute_duration_buckets(df)
        row = out[out["bucket"] == "1 min - 2 min"].iloc[0]
        self.assertEqual(row["trades"], 2)
        self.assertEqual(row["wins"], 1)
        self.assertAlmostEqual(row["win_rate"], 0.5)

    def test_win_rate_full_wins(self):
        df = _trades([
            {"duration_sec": 60, "pnl_net": 1.0},
            {"duration_sec": 70, "pnl_net": 2.0},
        ])
        out = metrics.compute_duration_buckets(df)
        row = out[out["bucket"] == "1 min - 2 min"].iloc[0]
        self.assertAlmostEqual(row["win_rate"], 1.0)

    def test_win_rate_no_wins(self):
        df = _trades([
            {"duration_sec": 60, "pnl_net": -1.0},
            {"duration_sec": 70, "pnl_net": -2.0},
        ])
        out = metrics.compute_duration_buckets(df)
        row = out[out["bucket"] == "1 min - 2 min"].iloc[0]
        self.assertEqual(row["wins"], 0)
        self.assertEqual(row["win_rate"], 0.0)

    def test_win_rate_no_zero_division_when_trades_is_zero(self):
        # Buckets sem trades nao podem explodir — verifica todos os 11.
        df = _trades([{"duration_sec": 60, "pnl_net": 1.0}])
        out = metrics.compute_duration_buckets(df)
        # Os 10 buckets vazios continuam com win_rate=0.0
        empty = out[out["bucket"] != "1 min - 2 min"]
        self.assertTrue((empty["win_rate"] == 0.0).all())


class ComputeDurationBucketsTypesTests(unittest.TestCase):

    def test_trades_and_wins_are_int_win_rate_is_float(self):
        df = _trades([{"duration_sec": 60, "pnl_net": 1.0}])
        out = metrics.compute_duration_buckets(df)
        for _, row in out.iterrows():
            self.assertIsInstance(row["trades"], int)
            self.assertIsInstance(row["wins"], int)
            self.assertIsInstance(row["win_rate"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
