"""Tests para src/risk_engine.py — motor puro de risk management pré-trade.

Cobre sizing, blowout, trailing MLL (Combine vs XFA), Monte Carlo determinístico,
regras TopStep e comparativo por ativo. Sem mocks: engine é puro (numpy/pandas).
"""
from __future__ import annotations

import unittest

import risk_engine as re


class PlanSizeKeyTests(unittest.TestCase):
    def test_extracts_size(self):
        self.assertEqual(re.plan_size_key("Express 50K"), "50K")
        self.assertEqual(re.plan_size_key("Express 100K"), "100K")
        self.assertEqual(re.plan_size_key("Express 150K"), "150K")

    def test_custom_and_none(self):
        self.assertIsNone(re.plan_size_key("Custom"))
        self.assertIsNone(re.plan_size_key(None))
        self.assertIsNone(re.plan_size_key("Desconhecido"))


class RiskDollarsTests(unittest.TestCase):
    def test_pct_mode(self):
        self.assertAlmostEqual(re.risk_dollars_per_trade(50000, "pct", 1.0), 500.0)

    def test_usd_mode(self):
        self.assertAlmostEqual(re.risk_dollars_per_trade(50000, "usd", 250.0), 250.0)

    def test_zero_or_negative(self):
        self.assertEqual(re.risk_dollars_per_trade(50000, "pct", 0), 0.0)
        self.assertEqual(re.risk_dollars_per_trade(50000, "usd", -5), 0.0)


class ContractCapTests(unittest.TestCase):
    def test_mini_cap(self):
        self.assertEqual(re.contract_cap("Express 50K", False, None), 5)
        self.assertEqual(re.contract_cap("Express 150K", False, None), 15)

    def test_micro_cap_x10(self):
        self.assertEqual(re.contract_cap("Express 50K", True, None), 50)

    def test_max_position_size_clamps(self):
        # teto TopStep 5, mas risk_settings limita a 3
        self.assertEqual(re.contract_cap("Express 50K", False, 3), 3)
        # risk_settings maior que o teto -> vence o teto TopStep
        self.assertEqual(re.contract_cap("Express 50K", False, 99), 5)

    def test_custom_no_cap(self):
        self.assertIsNone(re.contract_cap("Custom", False, None))
        # custom mas com max_position_size definido -> usa ele
        self.assertEqual(re.contract_cap("Custom", False, 4), 4)


class MaxContractsTests(unittest.TestCase):
    def test_basic_floor(self):
        # risk 500, stop 10pts, ES pv=50 -> 500/(10*50)=1.0 -> 1
        self.assertEqual(re.max_contracts(500, 10, 50, None), 1)
        # MNQ pv=2 -> 500/(10*2)=25 -> 25
        self.assertEqual(re.max_contracts(500, 10, 2, None), 25)

    def test_clamps_to_cap(self):
        self.assertEqual(re.max_contracts(500, 10, 2, 5), 5)

    def test_rounds_down(self):
        # 500/(7*2)=35.7 -> 35
        self.assertEqual(re.max_contracts(500, 7, 2, None), 35)

    def test_div_zero_guards(self):
        self.assertEqual(re.max_contracts(500, 0, 50, None), 0)   # stop 0
        self.assertEqual(re.max_contracts(500, 10, 0, None), 0)   # pv 0
        self.assertEqual(re.max_contracts(0, 10, 50, None), 0)    # risk 0


class MaxStopPointsTests(unittest.TestCase):
    def test_basic(self):
        # risk 500, 1 contrato ES pv=50 -> 10 pts
        self.assertAlmostEqual(re.max_stop_points(500, 1, 50), 10.0)

    def test_zero_contracts_none(self):
        self.assertIsNone(re.max_stop_points(500, 0, 50))
        self.assertIsNone(re.max_stop_points(500, 5, 0))


class TradesToDllTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(re.trades_to_dll(1000, 250), 4)
        self.assertEqual(re.trades_to_dll(1000, 300), 3)  # floor(3.33)

    def test_guards(self):
        self.assertEqual(re.trades_to_dll(None, 250), 0)
        self.assertEqual(re.trades_to_dll(0, 250), 0)
        self.assertEqual(re.trades_to_dll(1000, 0), 0)


class BlowoutTests(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(re.distance_to_blowout(50000, 48000), 2000)
        self.assertEqual(re.distance_to_blowout(47000, 48000), -1000)

    def test_days(self):
        self.assertEqual(re.days_to_blowout(2000, 1000), 2)
        self.assertEqual(re.days_to_blowout(2500, 1000), 2)  # floor
        self.assertIsNone(re.days_to_blowout(2000, None))


class TrailingThresholdTests(unittest.TestCase):
    def test_combine_trails_intraday_peak(self):
        new, locked = re.update_trailing_threshold(
            mode="combine", buffer=2000, current_threshold=48000,
            initial_balance=50000, intraday_peak_equity=51000, eod_equity=49000,
        )
        self.assertEqual(new, 49000)   # 51000 - 2000, usa pico intraday
        self.assertFalse(locked)

    def test_xfa_trails_eod_and_locks(self):
        new, locked = re.update_trailing_threshold(
            mode="xfa", buffer=2000, current_threshold=48000,
            initial_balance=50000, intraday_peak_equity=55000, eod_equity=53000,
        )
        # cand = min(53000-2000=51000, 50000) = 50000 -> trava no inicial
        self.assertEqual(new, 50000)
        self.assertTrue(locked)

    def test_never_decreases(self):
        new, _ = re.update_trailing_threshold(
            mode="combine", buffer=2000, current_threshold=49000,
            initial_balance=50000, intraday_peak_equity=48000, eod_equity=47000,
        )
        self.assertEqual(new, 49000)   # não desce mesmo com pico menor

    def test_buffer_none_noop(self):
        new, locked = re.update_trailing_threshold(
            mode="combine", buffer=None, current_threshold=48000,
            initial_balance=50000, intraday_peak_equity=99999, eod_equity=99999,
        )
        self.assertEqual(new, 48000)
        self.assertFalse(locked)


class MonteCarloTests(unittest.TestCase):
    BASE = dict(
        balance_usd=50000, mll_threshold_usd=48000, daily_loss_limit_usd=1000,
        risk_usd_per_trade=250, avg_r=1.5, trades_per_day=5, horizon_days=5,
        trailing_mode="combine", balance_buffer="50K", n_sims=3000, seed=42,
    )

    def test_deterministic_same_seed(self):
        a = re.monte_carlo(win_rate=0.5, **self.BASE)
        b = re.monte_carlo(win_rate=0.5, **self.BASE)
        self.assertEqual(a["p_blowout"], b["p_blowout"])
        self.assertEqual(a["expected_final_equity"], b["expected_final_equity"])

    def test_all_wins_no_blowout(self):
        r = re.monte_carlo(win_rate=1.0, **self.BASE)
        self.assertEqual(r["p_blowout"], 0.0)
        self.assertGreater(r["p_profit"], 0.99)

    def test_all_losses_high_blowout(self):
        r = re.monte_carlo(win_rate=0.0, **self.BASE)
        self.assertGreater(r["p_blowout"], 0.9)

    def test_curve_length_and_shape(self):
        r = re.monte_carlo(win_rate=0.5, **self.BASE)
        self.assertEqual(len(r["equity_curve_p50"]), self.BASE["horizon_days"])
        for k in ("p5", "p25", "p50", "p75", "p95"):
            self.assertIn(k, r["equity_pctiles"])

    def test_degenerate_risk_zero(self):
        params = {**self.BASE, "risk_usd_per_trade": 0}
        r = re.monte_carlo(win_rate=0.5, **params)
        self.assertTrue(r["degenerate"])
        self.assertIsNone(r["p_blowout"])

    def test_already_below_mll_blows_all(self):
        params = {**self.BASE, "balance_usd": 47000}  # já abaixo do MLL 48000
        r = re.monte_carlo(win_rate=0.9, **params)
        self.assertEqual(r["p_blowout"], 1.0)


class CheckRulesTests(unittest.TestCase):
    def test_contract_cap_exceeded(self):
        rules = re.check_rules(account_type="Express 50K", planned_total_contracts=8)
        cap = next(r for r in rules if r["rule"] == "contract_cap")
        self.assertFalse(cap["ok"])
        self.assertEqual(cap["severity"], "critical")
        self.assertEqual(cap["ctx"]["cap"], 5)

    def test_contract_cap_ok(self):
        rules = re.check_rules(account_type="Express 50K", planned_total_contracts=3)
        cap = next(r for r in rules if r["rule"] == "contract_cap")
        self.assertTrue(cap["ok"])

    def test_custom_cap_info(self):
        rules = re.check_rules(account_type="Custom", planned_total_contracts=99)
        cap = next(r for r in rules if r["rule"] == "contract_cap")
        self.assertTrue(cap["ok"])
        self.assertEqual(cap["severity"], "info")

    def test_min_winning_day(self):
        rules = re.check_rules(
            account_type="Express 50K", planned_total_contracts=1,
            expected_daily_profit_usd=100,
        )
        mwd = next(r for r in rules if r["rule"] == "min_winning_day")
        self.assertFalse(mwd["ok"])

    def test_consistency(self):
        rules = re.check_rules(
            account_type="Express 50K", planned_total_contracts=1,
            best_day_usd=600, cycle_profit_usd=1000,  # 600 > 50% de 1000
        )
        cons = next(r for r in rules if r["rule"] == "consistency_50")
        self.assertFalse(cons["ok"])


class CompareAssetsTests(unittest.TestCase):
    CONTRACTS = [
        {"symbol": "MNQ", "point_value_usd": 2.0, "is_micro": True},
        {"symbol": "ES", "point_value_usd": 50.0, "is_micro": False},
        {"symbol": "MES", "point_value_usd": 5.0, "is_micro": True},
    ]

    def test_micro_allows_more_contracts(self):
        df = re.compare_assets(
            contracts=self.CONTRACTS, balance_usd=50000, mll_threshold_usd=48000,
            daily_loss_limit_usd=1000, risk_mode="usd", risk_value=500,
            default_stop_points=10, account_type="Custom",  # sem teto p/ ver o sizing puro
        )
        row = {r["contract_name"]: r for r in df.to_dict("records")}
        # MNQ (pv 2) permite muito mais que ES (pv 50) com mesmo risco/stop
        self.assertGreater(row["MNQ"]["max_contracts"], row["ES"]["max_contracts"])

    def test_sorted_desc_and_no_mc_by_default(self):
        df = re.compare_assets(
            contracts=self.CONTRACTS, balance_usd=50000, mll_threshold_usd=48000,
            daily_loss_limit_usd=1000, risk_mode="usd", risk_value=500,
            default_stop_points=10, account_type="Custom",
        )
        contracts_order = df["max_contracts"].tolist()
        self.assertEqual(contracts_order, sorted(contracts_order, reverse=True))
        self.assertTrue(df["p_blowout"].isna().all())

    def test_cap_applied_with_account_type(self):
        df = re.compare_assets(
            contracts=self.CONTRACTS, balance_usd=50000, mll_threshold_usd=48000,
            daily_loss_limit_usd=1000, risk_mode="usd", risk_value=500,
            default_stop_points=10, account_type="Express 50K",  # micros cap 50
        )
        row = {r["contract_name"]: r for r in df.to_dict("records")}
        self.assertLessEqual(row["MNQ"]["max_contracts"], 50)


if __name__ == "__main__":
    unittest.main()
