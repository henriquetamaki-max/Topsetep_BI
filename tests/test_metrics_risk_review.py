"""Tests para metrics.compute_risk_review (M15 — Avaliação de Risco).

Cobre as 4 dimensões (stop_furado, risco_excedido, dll_furado, blowout),
agrupamento por dia em fuso ET, dia sem plano fora do denominador e o vazio.
"""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import metrics


def _groups(rows: list[dict]) -> pd.DataFrame:
    """Constrói um `groups` mínimo. Cada row: {day|group_start, contract, type,
    net}. group_start default = `day` 15:00 UTC (≈11h ET, longe da meia-noite)."""
    out = []
    for i, r in enumerate(rows, 1):
        gs = r.get("group_start")
        if gs is None:
            gs = pd.Timestamp(f"{r['day']} 15:00", tz="UTC")
        out.append({
            "group_id": i,
            "contract_name": r.get("contract", "MNQ"),
            "type": r.get("type", "Long"),
            "group_start": gs,
            "total_net_pnl": r["net"],
            "total_size": r.get("size", 1),
        })
    return pd.DataFrame(out)


def _plans(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _risk_plans(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


class EmptyTests(unittest.TestCase):
    def test_empty_groups_schema(self):
        out = metrics.compute_risk_review(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        for k in ("total_days", "clean_days", "stop_furado", "risco_excedido",
                  "dll_furado", "blowout"):
            self.assertEqual(out[k], 0)
        self.assertEqual(out["score_pct"], 0.0)
        self.assertTrue(out["by_day"].empty)
        self.assertTrue(out["op_violations"].empty)


class CleanDayTests(unittest.TestCase):
    def test_positive_day_with_plan_is_clean(self):
        g = _groups([{"day": "2026-05-28", "net": 300.0}])
        rp = _risk_plans([{
            "plan_date": "2026-05-28", "daily_loss_limit_usd": 1000.0,
            "balance_usd": 50000.0, "mll_threshold_usd": 48000.0,
            "risk_mode": "usd", "risk_value": 500.0,
        }])
        out = metrics.compute_risk_review(g, pd.DataFrame(), rp)
        self.assertEqual(out["total_days"], 1)
        self.assertEqual(out["clean_days"], 1)
        self.assertEqual(out["score_pct"], 100.0)
        self.assertEqual(out["dll_furado"], 0)


class StopTests(unittest.TestCase):
    def test_stop_furado_when_loss_exceeds_planned_risk(self):
        # MNQ pv=2, max_size=5, stop=10 -> risco planejado $100. Perda real $150.
        g = _groups([{"day": "2026-05-28", "contract": "MNQ", "type": "Long", "net": -150.0}])
        plans = _plans([{
            "plan_date": "2026-05-28", "contract_name": "MNQ", "direction": "Long",
            "max_size": 5, "stop_points": 10.0,
        }])
        rp = _risk_plans([{
            "plan_date": "2026-05-28", "daily_loss_limit_usd": 5000.0,
            "balance_usd": 50000.0, "mll_threshold_usd": 40000.0,
            "risk_mode": "usd", "risk_value": 500.0,
        }])
        out = metrics.compute_risk_review(g, plans, rp, point_values={"MNQ": 2.0})
        self.assertEqual(out["stop_furado"], 1)
        self.assertEqual(len(out["op_violations"]), 1)
        self.assertEqual(out["op_violations"].iloc[0]["excess"], 50.0)
        self.assertEqual(out["clean_days"], 0)

    def test_no_stop_check_without_point_value(self):
        g = _groups([{"day": "2026-05-28", "contract": "MNQ", "net": -150.0}])
        plans = _plans([{
            "plan_date": "2026-05-28", "contract_name": "MNQ", "direction": "Long",
            "max_size": 5, "stop_points": 10.0,
        }])
        out = metrics.compute_risk_review(g, plans, pd.DataFrame(), point_values={})
        self.assertEqual(out["stop_furado"], 0)
        self.assertTrue(out["op_violations"].empty)


class DllTests(unittest.TestCase):
    def test_dll_furado_when_day_loss_breaches_limit(self):
        g = _groups([{"day": "2026-05-28", "net": -1200.0}])
        rp = _risk_plans([{
            "plan_date": "2026-05-28", "daily_loss_limit_usd": 1000.0,
            "balance_usd": 50000.0, "mll_threshold_usd": 40000.0,
            "risk_mode": "usd", "risk_value": 2000.0,
        }])
        out = metrics.compute_risk_review(g, pd.DataFrame(), rp)
        self.assertEqual(out["dll_furado"], 1)
        self.assertEqual(out["risco_excedido"], 0)
        row = out["by_day"].iloc[0]
        self.assertTrue(row["dll_furado"])
        self.assertIn("dll_furado", row["violations"])


class RiskPerTradeTests(unittest.TestCase):
    def test_risco_excedido_when_avg_loss_above_target(self):
        # risco alvo = 1% de 50000 = $500; perda média = $700.
        g = _groups([
            {"day": "2026-05-28", "net": -700.0},
            {"day": "2026-05-28", "net": -700.0},
        ])
        rp = _risk_plans([{
            "plan_date": "2026-05-28", "daily_loss_limit_usd": 5000.0,
            "balance_usd": 50000.0, "mll_threshold_usd": 40000.0,
            "risk_mode": "pct", "risk_value": 1.0,
        }])
        out = metrics.compute_risk_review(g, pd.DataFrame(), rp)
        self.assertEqual(out["risco_excedido"], 1)
        self.assertEqual(out["dll_furado"], 0)
        self.assertEqual(out["blowout"], 0)


class BlowoutTests(unittest.TestCase):
    def test_blowout_when_cum_drawdown_reaches_distance(self):
        # distância = 50000 - 48000 = 2000. Dois dias -1500 -> drawdown 3000.
        rp = _risk_plans([
            {"plan_date": "2026-05-28", "daily_loss_limit_usd": 5000.0,
             "balance_usd": 50000.0, "mll_threshold_usd": 48000.0,
             "risk_mode": "usd", "risk_value": 5000.0},
            {"plan_date": "2026-05-29", "daily_loss_limit_usd": 5000.0,
             "balance_usd": 50000.0, "mll_threshold_usd": 48000.0,
             "risk_mode": "usd", "risk_value": 5000.0},
        ])
        g = _groups([
            {"day": "2026-05-28", "net": -1500.0},
            {"day": "2026-05-29", "net": -1500.0},
        ])
        out = metrics.compute_risk_review(g, pd.DataFrame(), rp)
        self.assertEqual(out["blowout"], 1)  # só o 2º dia cruza a distância
        self.assertEqual(out["total_days"], 2)
        self.assertEqual(out["clean_days"], 1)
        self.assertEqual(out["score_pct"], 50.0)


class NoPlanTests(unittest.TestCase):
    def test_unplanned_day_excluded_from_score(self):
        g = _groups([
            {"day": "2026-05-28", "net": 200.0},     # planejado, limpo
            {"day": "2026-05-29", "net": -3000.0},   # sem plano
        ])
        rp = _risk_plans([{
            "plan_date": "2026-05-28", "daily_loss_limit_usd": 1000.0,
            "balance_usd": 50000.0, "mll_threshold_usd": 48000.0,
            "risk_mode": "usd", "risk_value": 500.0,
        }])
        out = metrics.compute_risk_review(g, pd.DataFrame(), rp)
        self.assertEqual(out["total_days"], 1)   # só o dia 28 tem plano
        self.assertEqual(out["clean_days"], 1)
        self.assertEqual(out["score_pct"], 100.0)
        self.assertEqual(len(out["by_day"]), 2)  # ambos aparecem na tabela
        d29 = out["by_day"][out["by_day"]["trade_day"] == date(2026, 5, 29)].iloc[0]
        self.assertFalse(bool(d29["has_plan"]))


class TimezoneTests(unittest.TestCase):
    def test_et_day_bucketing(self):
        # 02:00 UTC de 28/05 = 22:00 ET de 27/05 (EDT, UTC-4) -> dia ET = 27.
        g = _groups([{
            "group_start": pd.Timestamp("2026-05-28 02:00", tz="UTC"),
            "contract": "MNQ", "type": "Long", "net": 100.0,
        }])
        out = metrics.compute_risk_review(g, pd.DataFrame(), pd.DataFrame())
        self.assertEqual(out["by_day"].iloc[0]["trade_day"], date(2026, 5, 27))


if __name__ == "__main__":
    unittest.main()
