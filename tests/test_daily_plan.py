"""
Tests para src/daily_plan.py — conversao de pontos em USD multi-contrato.

Cobre o fallback offline (sem cliente Supabase) e o helper compute_usd.
"""
from __future__ import annotations

import unittest


import daily_plan


class PointValueUsdTests(unittest.TestCase):

    def setUp(self):
        # Forca o cache para usar o fallback estatico (sem chamar Supabase).
        daily_plan._CONTRACTS_CACHE = dict(daily_plan._POINT_VALUE_FALLBACK)

    def test_known_contract_mnq(self):
        self.assertEqual(daily_plan.point_value_usd("MNQ"), 2.0)

    def test_unknown_contract_returns_none(self):
        self.assertIsNone(daily_plan.point_value_usd("XYZ"))

    def test_empty_or_none(self):
        self.assertIsNone(daily_plan.point_value_usd(None))
        self.assertIsNone(daily_plan.point_value_usd(""))

    def test_case_insensitive_and_trim(self):
        self.assertEqual(daily_plan.point_value_usd("  mnq  "), 2.0)
        self.assertEqual(daily_plan.point_value_usd("Mnq"), 2.0)


class ComputeUsdTests(unittest.TestCase):

    def setUp(self):
        daily_plan._CONTRACTS_CACHE = dict(daily_plan._POINT_VALUE_FALLBACK)

    def test_basic(self):
        # MNQ: 2 USD por ponto. 5 pontos * 2 contratos * 2 USD = 20.
        self.assertEqual(daily_plan.compute_usd(5.0, 2, "MNQ"), 20.0)

    def test_unknown_contract_returns_none(self):
        self.assertIsNone(daily_plan.compute_usd(5.0, 2, "XYZ"))

    def test_missing_inputs(self):
        self.assertIsNone(daily_plan.compute_usd(None, 2, "MNQ"))
        self.assertIsNone(daily_plan.compute_usd(5.0, None, "MNQ"))
        self.assertIsNone(daily_plan.compute_usd(5.0, 2, None))

    def test_negative_points(self):
        # stop_points sao positivos por convencao, mas a funcao nao filtra.
        self.assertEqual(daily_plan.compute_usd(-3.0, 1, "MNQ"), -6.0)

    def test_full_catalog_when_simulated(self):
        # Simula tabela contracts populada (overrida cache).
        daily_plan._CONTRACTS_CACHE = {
            "MNQ": 2.0, "NQ": 20.0, "MES": 5.0, "ES": 50.0,
            "MCL": 1.0, "CL": 10.0, "MGC": 1.0, "GC": 10.0,
        }
        self.assertEqual(daily_plan.compute_usd(4.0, 1, "ES"), 200.0)
        self.assertEqual(daily_plan.compute_usd(2.0, 3, "NQ"), 120.0)
        self.assertEqual(daily_plan.compute_usd(10.0, 1, "GC"), 100.0)
        self.assertIsNone(daily_plan.compute_usd(1.0, 1, "BTC"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
