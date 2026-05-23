"""
Tests para metrics.compute_plan_adherence — 5 categorias de violacao.

Roda com:
    .venv/Scripts/python.exe -m unittest tests.test_metrics_adherence -v
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


def _make_group(group_id: int, contract: str, side: str, size: int,
                start: str) -> dict:
    return {
        "group_id": group_id,
        "contract_name": contract,
        "type": side,
        "total_size": size,
        "group_start": pd.Timestamp(start, tz="UTC"),
    }


def _make_plan(date_iso: str, contract: str, direction: str,
               max_size: int) -> dict:
    return {
        "plan_date": dt.date.fromisoformat(date_iso),
        "contract_name": contract,
        "direction": direction,
        "max_size": max_size,
    }


class ComputePlanAdherenceTests(unittest.TestCase):

    def test_empty_groups_returns_zeros(self):
        r = metrics.compute_plan_adherence(pd.DataFrame(), pd.DataFrame())
        self.assertEqual(r["total_groups"], 0)
        self.assertEqual(r["compliant"], 0)
        self.assertEqual(r["score_pct"], 0.0)
        self.assertTrue(r["violations"].empty)
        for k in ("unplanned", "size_exceeded", "against_plan", "size_creep_day"):
            self.assertEqual(r[k], 0, msg=f"chave {k}")

    def test_no_plans_all_unplanned(self):
        groups = pd.DataFrame([
            _make_group(1, "MNQ", "Long", 2, "2026-05-20 14:30"),
            _make_group(2, "ES", "Short", 1, "2026-05-20 15:00"),
        ])
        r = metrics.compute_plan_adherence(groups, pd.DataFrame())
        self.assertEqual(r["total_groups"], 2)
        self.assertEqual(r["unplanned"], 2)
        self.assertEqual(r["compliant"], 0)
        self.assertEqual(r["score_pct"], 0.0)

    def test_compliant_single_group(self):
        # Plano: MNQ Long max_size=4. Operacao: 3 contratos. Deve ser compliant.
        groups = pd.DataFrame([_make_group(1, "MNQ", "Long", 3, "2026-05-20 14:30")])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["compliant"], 1)
        self.assertEqual(r["unplanned"], 0)
        self.assertEqual(r["size_exceeded"], 0)
        self.assertEqual(r["score_pct"], 100.0)

    def test_size_exceeded(self):
        # Operacao com 5 > plano de 4 num grupo so.
        groups = pd.DataFrame([_make_group(1, "MNQ", "Long", 5, "2026-05-20 14:30")])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["size_exceeded"], 1)
        self.assertEqual(r["compliant"], 0)
        self.assertEqual(r["score_pct"], 0.0)

    def test_against_plan_direction(self):
        # Plano Long; operacao Short no mesmo contrato e dia.
        groups = pd.DataFrame([_make_group(1, "MNQ", "Short", 2, "2026-05-20 14:30")])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["against_plan"], 1)
        self.assertEqual(r["unplanned"], 0,
                         "against_plan toma precedencia sobre unplanned")

    def test_unplanned_when_no_matching_contract(self):
        # Plano: MNQ Long. Operacao: ES Long (contrato diferente).
        groups = pd.DataFrame([_make_group(1, "ES", "Long", 1, "2026-05-20 14:30")])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["unplanned"], 1)
        self.assertEqual(r["against_plan"], 0)

    def test_size_creep_day_across_groups(self):
        # Plano MNQ Long max_size=4.
        # G1: 3 contratos (compliant). G2: 2 contratos (cabe sozinho mas
        # acumulado vira 5 > 4 = size_creep_day).
        groups = pd.DataFrame([
            _make_group(1, "MNQ", "Long", 3, "2026-05-20 14:30"),
            _make_group(2, "MNQ", "Long", 2, "2026-05-20 15:00"),
        ])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["compliant"], 1, "G1 (3) ainda dentro do limite")
        self.assertEqual(r["size_creep_day"], 1, "G2 (2) acumulou 5 > 4")
        self.assertEqual(r["size_exceeded"], 0,
                         "size_exceeded eh apenas grupo isolado acima")

    def test_creep_does_not_trigger_for_first_group_alone_over(self):
        # G1 com 6 num plano de 4 deve ser size_exceeded, NAO size_creep_day
        # (size_creep eh acumulo distribuido, nao single-group oversized).
        groups = pd.DataFrame([_make_group(1, "MNQ", "Long", 6, "2026-05-20 14:30")])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["size_exceeded"], 1)
        self.assertEqual(r["size_creep_day"], 0)

    def test_mixed_scenario_all_5_categories(self):
        # Cenario sintetico mostrando que as 5 categorias coexistem.
        groups = pd.DataFrame([
            _make_group(1, "MNQ", "Long",  3, "2026-05-20 14:30"),  # compliant
            _make_group(2, "MNQ", "Long",  5, "2026-05-20 15:00"),  # size_exceeded
            _make_group(3, "MNQ", "Long",  2, "2026-05-20 15:30"),  # size_creep_day (G1+G3=5)
            _make_group(4, "MNQ", "Short", 2, "2026-05-20 16:00"),  # against_plan
            _make_group(5, "ES",  "Long",  1, "2026-05-20 16:30"),  # unplanned
        ])
        plans = pd.DataFrame([_make_plan("2026-05-20", "MNQ", "Long", 4)])
        r = metrics.compute_plan_adherence(groups, plans)
        self.assertEqual(r["compliant"], 1)
        self.assertEqual(r["size_exceeded"], 1)
        self.assertEqual(r["size_creep_day"], 1)
        self.assertEqual(r["against_plan"], 1)
        self.assertEqual(r["unplanned"], 1)
        self.assertEqual(r["total_groups"], 5)
        self.assertAlmostEqual(r["score_pct"], 20.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
