"""Tests para `metrics.compute_segments` — 4 buckets por adicoes.

Recebe `groups` (saida de compute_groups) e particiona em:
- `no_additions`: trade_count==1 (sem adicoes)
- `with_additions`: trade_count>1
- `with_additions_winners`: with_additions & status==Winner
- `with_additions_losers`: with_additions & status==Loser

`with_additions_winners + with_additions_losers` pode ser MENOR que
`with_additions` quando ha grupos Flat (zero net) — esses ficam em
with_additions mas fora dos sub-buckets de win/loss.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


def _groups(rows: list[dict]) -> pd.DataFrame:
    """Helper: builda DataFrame de grupos com defaults sensatos.

    Campos esperados em `rows`: `has_addition`, `points_status`. Resto
    tem default razoavel (total_points, total_pnl, additions_count,
    total_size).
    """
    defaults = {
        "total_points": 0.0,
        "total_pnl": 0.0,
        "additions_count": 0,
        "total_size": 1,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


EMPTY_SEGMENT_KEYS = {
    "count", "total_points", "total_pnl", "avg_points", "avg_pnl",
    "win_rate_by_group", "avg_additions", "total_size",
}


class ComputeSegmentsEmptyTests(unittest.TestCase):

    def test_empty_groups_returns_4_zeroed_buckets(self):
        out = metrics.compute_segments(pd.DataFrame())
        self.assertEqual(
            set(out.keys()),
            {"no_additions", "with_additions",
             "with_additions_winners", "with_additions_losers"},
        )
        for bucket_name, bucket in out.items():
            self.assertEqual(set(bucket.keys()), EMPTY_SEGMENT_KEYS, bucket_name)
            self.assertEqual(bucket["count"], 0, bucket_name)
            self.assertEqual(bucket["win_rate_by_group"], 0.0, bucket_name)


class ComputeSegmentsPartitioningTests(unittest.TestCase):

    def test_only_no_additions_populates_first_bucket(self):
        g = _groups([
            {"has_addition": False, "points_status": "Winner"},
            {"has_addition": False, "points_status": "Loser"},
        ])
        out = metrics.compute_segments(g)
        self.assertEqual(out["no_additions"]["count"], 2)
        self.assertEqual(out["with_additions"]["count"], 0)
        self.assertEqual(out["with_additions_winners"]["count"], 0)
        self.assertEqual(out["with_additions_losers"]["count"], 0)

    def test_only_with_additions_leaves_no_additions_empty(self):
        g = _groups([
            {"has_addition": True, "points_status": "Winner", "additions_count": 1},
            {"has_addition": True, "points_status": "Loser", "additions_count": 2},
        ])
        out = metrics.compute_segments(g)
        self.assertEqual(out["no_additions"]["count"], 0)
        self.assertEqual(out["with_additions"]["count"], 2)
        self.assertEqual(out["with_additions_winners"]["count"], 1)
        self.assertEqual(out["with_additions_losers"]["count"], 1)

    def test_flat_grouped_with_additions_excluded_from_win_loss_subbuckets(self):
        # Documenta semantica: Flat conta em with_additions mas nao em
        # with_additions_winners nem _losers. So' Winner/Loser sao binarios.
        g = _groups([
            {"has_addition": True, "points_status": "Winner"},
            {"has_addition": True, "points_status": "Flat"},
            {"has_addition": True, "points_status": "Loser"},
        ])
        out = metrics.compute_segments(g)
        self.assertEqual(out["with_additions"]["count"], 3)
        self.assertEqual(out["with_additions_winners"]["count"], 1)
        self.assertEqual(out["with_additions_losers"]["count"], 1)
        # 1+1 < 3 — o Flat fica de fora dos sub-buckets.

    def test_mixed_population_partitions_correctly(self):
        g = _groups([
            {"has_addition": False, "points_status": "Winner"},   # no_add
            {"has_addition": False, "points_status": "Loser"},    # no_add
            {"has_addition": True,  "points_status": "Winner"},   # with_add + winners
            {"has_addition": True,  "points_status": "Loser"},    # with_add + losers
            {"has_addition": True,  "points_status": "Winner"},   # with_add + winners
        ])
        out = metrics.compute_segments(g)
        self.assertEqual(out["no_additions"]["count"], 2)
        self.assertEqual(out["with_additions"]["count"], 3)
        self.assertEqual(out["with_additions_winners"]["count"], 2)
        self.assertEqual(out["with_additions_losers"]["count"], 1)


class ComputeSegmentsAggregationTests(unittest.TestCase):

    def test_total_points_and_pnl_sum_within_bucket(self):
        g = _groups([
            {"has_addition": False, "points_status": "Winner",
             "total_points": 3.0, "total_pnl": 6.0, "total_size": 2},
            {"has_addition": False, "points_status": "Loser",
             "total_points": -1.0, "total_pnl": -2.0, "total_size": 1},
        ])
        out = metrics.compute_segments(g)
        seg = out["no_additions"]
        self.assertAlmostEqual(seg["total_points"], 2.0)
        self.assertAlmostEqual(seg["total_pnl"], 4.0)
        self.assertAlmostEqual(seg["total_size"], 3.0)

    def test_avg_points_and_pnl_use_bucket_mean(self):
        g = _groups([
            {"has_addition": False, "points_status": "Winner",
             "total_points": 3.0, "total_pnl": 6.0},
            {"has_addition": False, "points_status": "Loser",
             "total_points": -1.0, "total_pnl": -2.0},
        ])
        out = metrics.compute_segments(g)
        seg = out["no_additions"]
        self.assertAlmostEqual(seg["avg_points"], 1.0)
        self.assertAlmostEqual(seg["avg_pnl"], 2.0)

    def test_avg_additions_uses_subset_mean(self):
        g = _groups([
            {"has_addition": True, "points_status": "Winner", "additions_count": 1},
            {"has_addition": True, "points_status": "Loser", "additions_count": 3},
            {"has_addition": False, "points_status": "Winner", "additions_count": 0},
        ])
        out = metrics.compute_segments(g)
        # mean de [1, 3] dentro de with_additions
        self.assertAlmostEqual(out["with_additions"]["avg_additions"], 2.0)
        # mean de [0] dentro de no_additions
        self.assertAlmostEqual(out["no_additions"]["avg_additions"], 0.0)


class ComputeSegmentsWinRateTests(unittest.TestCase):

    def test_win_rate_by_group_is_segment_relative(self):
        # 2 winners / 4 grupos no_additions = 0.5 nesse bucket.
        # Bucket with_additions tem 1/2 = 0.5 mas isolado.
        g = _groups([
            {"has_addition": False, "points_status": "Winner"},
            {"has_addition": False, "points_status": "Loser"},
            {"has_addition": False, "points_status": "Winner"},
            {"has_addition": False, "points_status": "Flat"},
            {"has_addition": True, "points_status": "Winner"},
            {"has_addition": True, "points_status": "Loser"},
        ])
        out = metrics.compute_segments(g)
        self.assertAlmostEqual(out["no_additions"]["win_rate_by_group"], 0.5)
        self.assertAlmostEqual(out["with_additions"]["win_rate_by_group"], 0.5)

    def test_win_rate_in_subbucket_is_always_one_or_zero(self):
        # Por definicao, with_additions_winners so' contem Winners, entao
        # win_rate_by_group dentro desse bucket sempre == 1.0 (se nao vazio).
        g = _groups([
            {"has_addition": True, "points_status": "Winner"},
            {"has_addition": True, "points_status": "Winner"},
            {"has_addition": True, "points_status": "Loser"},
        ])
        out = metrics.compute_segments(g)
        self.assertAlmostEqual(out["with_additions_winners"]["win_rate_by_group"], 1.0)
        self.assertAlmostEqual(out["with_additions_losers"]["win_rate_by_group"], 0.0)

    def test_win_rate_with_only_flats_is_zero(self):
        g = _groups([
            {"has_addition": False, "points_status": "Flat"},
            {"has_addition": False, "points_status": "Flat"},
        ])
        out = metrics.compute_segments(g)
        self.assertEqual(out["no_additions"]["win_rate_by_group"], 0.0)


class ComputeSegmentsSchemaTests(unittest.TestCase):

    def test_all_4_buckets_share_same_keys(self):
        g = _groups([
            {"has_addition": True, "points_status": "Winner"},
            {"has_addition": False, "points_status": "Loser"},
        ])
        out = metrics.compute_segments(g)
        for name, bucket in out.items():
            self.assertEqual(set(bucket.keys()), EMPTY_SEGMENT_KEYS, name)

    def test_types_int_for_count_float_rest(self):
        g = _groups([
            {"has_addition": True, "points_status": "Winner",
             "total_points": 1.0, "total_pnl": 2.0, "additions_count": 1},
        ])
        seg = metrics.compute_segments(g)["with_additions"]
        self.assertIsInstance(seg["count"], int)
        for k in EMPTY_SEGMENT_KEYS - {"count"}:
            self.assertIsInstance(seg[k], float, k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
