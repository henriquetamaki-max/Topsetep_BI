"""Tests para metrics.compute_day_detail (M16 — comparativo plano×realizado de
um dia). Cobre cada dimensão (planejado/realizado/delta/pct/status), dimensão
sem referência, contexto e dia sem trades."""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import metrics


def _groups(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows, 1):
        gs = r.get("group_start") or pd.Timestamp(f"{r['day']} 15:00", tz="UTC")
        out.append({
            "group_id": i,
            "contract_name": r.get("contract", "MNQ"),
            "type": r.get("type", "Long"),
            "group_start": gs,
            "total_net_pnl": r["net"],
            "total_size": r.get("size", 1),
        })
    return pd.DataFrame(out)


def _plans(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _rp(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _dim(out, key):
    return next(d for d in out["dimensions"] if d["key"] == key)


class EmptyTests(unittest.TestCase):
    def test_no_groups(self):
        out = metrics.compute_day_detail(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                                         day=date(2026, 5, 28))
        self.assertFalse(out["has_plan"])
        self.assertEqual(out["dimensions"], [])

    def test_day_without_trades(self):
        g = _groups([{"day": "2026-05-28", "net": 100.0}])
        out = metrics.compute_day_detail(g, pd.DataFrame(), pd.DataFrame(),
                                         day=date(2026, 5, 29))
        self.assertEqual(out["dimensions"], [])


class TradesDimTests(unittest.TestCase):
    def test_max_trades_delta_and_status(self):
        g = _groups([{"day": "2026-05-28", "net": 20.0} for _ in range(6)])
        rp = _rp([{"plan_date": "2026-05-28", "trades_per_day": 4,
                   "daily_loss_limit_usd": 5000.0, "balance_usd": 50000.0,
                   "mll_threshold_usd": 40000.0}])
        out = metrics.compute_day_detail(g, pd.DataFrame(), rp)
        # day default None -> precisa passar day; usa a data dos trades
        out = metrics.compute_day_detail(g, pd.DataFrame(), rp, day=date(2026, 5, 28))
        dt = _dim(out, "max_trades")
        self.assertEqual(dt["planned"], 4)
        self.assertEqual(dt["realized"], 6)
        self.assertEqual(dt["delta"], 2)
        self.assertEqual(dt["pct"], 1.5)
        self.assertEqual(dt["status"], "viol")


class LossDimTests(unittest.TestCase):
    def test_max_loss_breach(self):
        g = _groups([{"day": "2026-05-28", "net": -1200.0}])
        rp = _rp([{"plan_date": "2026-05-28", "daily_loss_limit_usd": 1000.0,
                   "balance_usd": 50000.0, "mll_threshold_usd": 40000.0,
                   "trades_per_day": 10}])
        out = metrics.compute_day_detail(g, pd.DataFrame(), rp, day=date(2026, 5, 28))
        dl = _dim(out, "max_loss")
        self.assertEqual(dl["planned"], 1000.0)
        self.assertEqual(dl["realized"], 1200.0)
        self.assertEqual(dl["delta"], 200.0)
        self.assertEqual(dl["status"], "viol")


class SizeDimTests(unittest.TestCase):
    def test_max_size_exceeded(self):
        g = _groups([{"day": "2026-05-28", "contract": "MNQ", "type": "Long",
                      "net": 10.0, "size": 5}])
        plans = _plans([{"plan_date": "2026-05-28", "contract_name": "MNQ",
                         "direction": "Long", "max_size": 2, "stop_points": 10.0}])
        out = metrics.compute_day_detail(g, plans, pd.DataFrame(), day=date(2026, 5, 28))
        ds = _dim(out, "max_size")
        self.assertEqual(ds["planned"], 2.0)
        self.assertEqual(ds["realized"], 5)
        self.assertEqual(ds["status"], "viol")


class StopDimTests(unittest.TestCase):
    def test_stop_breach(self):
        # MNQ pv=2, max_size=5, stop=10 -> risco $100. Perda real $150.
        g = _groups([{"day": "2026-05-28", "contract": "MNQ", "type": "Long",
                      "net": -150.0}])
        plans = _plans([{"plan_date": "2026-05-28", "contract_name": "MNQ",
                         "direction": "Long", "max_size": 5, "stop_points": 10.0}])
        out = metrics.compute_day_detail(g, plans, pd.DataFrame(),
                                         point_values={"MNQ": 2.0}, day=date(2026, 5, 28))
        st = _dim(out, "stop")
        self.assertEqual(st["planned"], 100.0)
        self.assertEqual(st["realized"], 150.0)
        self.assertEqual(st["delta"], 50.0)
        self.assertEqual(st["status"], "viol")


class NoReferenceTests(unittest.TestCase):
    def test_dims_without_plan_are_none(self):
        g = _groups([{"day": "2026-05-28", "net": -300.0}])
        out = metrics.compute_day_detail(g, pd.DataFrame(), pd.DataFrame(),
                                         day=date(2026, 5, 28))
        for key in ("max_trades", "max_loss", "max_size", "stop"):
            self.assertIsNone(_dim(out, key)["status"])
            self.assertIsNone(_dim(out, key)["planned"])
        self.assertFalse(out["has_plan"])


class ContextTests(unittest.TestCase):
    def test_context_fields(self):
        g = _groups([
            {"day": "2026-05-28", "contract": "MNQ", "net": 300.0},
            {"day": "2026-05-28", "contract": "ES", "net": -120.0},
        ])
        out = metrics.compute_day_detail(g, pd.DataFrame(), pd.DataFrame(),
                                         day=date(2026, 5, 28))
        ctx = out["context"]
        self.assertEqual(ctx["n_ops"], 2)
        self.assertEqual(ctx["n_winners"], 1)
        self.assertEqual(ctx["n_losers"], 1)
        self.assertEqual(ctx["net_pnl"], 180.0)
        self.assertEqual(ctx["best_op"], 300.0)
        self.assertEqual(ctx["worst_op"], -120.0)
        self.assertEqual(ctx["contracts"], ["ES", "MNQ"])


if __name__ == "__main__":
    unittest.main()
