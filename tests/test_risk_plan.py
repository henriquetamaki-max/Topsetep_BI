"""Tests para src/risk_plan.py — CRUD do Risk Planner + ponte para daily_plans.

Foco no push_to_daily_plans (lógica de mapeamento + colisão UNIQUE) e na
injeção de user_id/risk_plan_id nos inserts. Mocka auth.get_client e daily_plan.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

import daily_plan
import risk_plan


def _empty_plan() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in daily_plan.ALL_COLUMNS})


def _existing_mnq() -> pd.DataFrame:
    return pd.DataFrame([{
        "id": 1, "plan_date": date(2026, 5, 30), "created_at": None, "updated_at": None,
        "contract_name": "MNQ", "direction": "Long", "max_size": 2,
        "entry_trigger": None, "stop_points": 5.0, "target_points": None, "notes": None,
    }])


class PushToDailyPlansTests(unittest.TestCase):
    def test_empty_selection_noop(self):
        out = risk_plan.push_to_daily_plans(date(2026, 5, 30), pd.DataFrame())
        self.assertTrue(out["ok"])
        self.assertEqual(out["inserted"], 0)

    def test_maps_assets_to_new_rows(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            captured["default_date"] = default_date
            return {"ok": True, "inserted": len(edited), "updated": 0, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_empty_plan()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([
                {"contract_name": "MNQ", "max_contracts": 25, "max_stop_points": 10.0},
                {"contract_name": "ES", "max_contracts": 1, "max_stop_points": 10.0},
            ])
            out = risk_plan.push_to_daily_plans(date(2026, 5, 30), sel)

        ed = captured["edited"]
        self.assertEqual(len(ed), 2)
        mnq = ed[ed["contract_name"] == "MNQ"].iloc[0]
        self.assertEqual(int(mnq["max_size"]), 25)
        self.assertEqual(mnq["direction"], "Long")
        self.assertEqual(mnq["notes"], "Risk Planner")
        self.assertEqual(captured["default_date"], date(2026, 5, 30))
        self.assertEqual(out["skipped"], 0)

    def test_skips_zero_contracts(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            return {"ok": True, "inserted": 0, "updated": 0, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_empty_plan()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([{"contract_name": "MNQ", "max_contracts": 0, "max_stop_points": 10.0}])
            risk_plan.push_to_daily_plans(date(2026, 5, 30), sel)

        self.assertTrue(captured["edited"].empty)

    def test_collision_skips_without_overwrite(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            return {"ok": True, "inserted": 0, "updated": 0, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_existing_mnq()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([{
                "contract_name": "MNQ", "direction": "Long",
                "max_contracts": 25, "max_stop_points": 10.0,
            }])
            out = risk_plan.push_to_daily_plans(date(2026, 5, 30), sel, overwrite=False)

        self.assertEqual(out["skipped"], 1)
        # linha original intacta (max_size segue 2)
        self.assertEqual(int(captured["edited"].iloc[0]["max_size"]), 2)

    def test_collision_overwrites_with_flag(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            return {"ok": True, "inserted": 0, "updated": 1, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_existing_mnq()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([{
                "contract_name": "MNQ", "direction": "Long",
                "max_contracts": 25, "max_stop_points": 10.0,
            }])
            out = risk_plan.push_to_daily_plans(date(2026, 5, 30), sel, overwrite=True)

        ed = captured["edited"]
        self.assertEqual(int(ed.iloc[0]["max_size"]), 25)
        self.assertEqual(ed.iloc[0]["stop_points"], 10.0)
        self.assertEqual(ed.iloc[0]["notes"], "Risk Planner")
        self.assertEqual(out["skipped"], 0)


    def test_directions_both_writes_two_rows(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            return {"ok": True, "inserted": len(edited), "updated": 0, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_empty_plan()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([{"contract_name": "MNQ", "max_contracts": 5, "max_stop_points": 10.0}])
            risk_plan.push_to_daily_plans(
                date(2026, 5, 30), sel, directions=("Long", "Short"))

        ed = captured["edited"]
        self.assertEqual(len(ed), 2)
        self.assertEqual(set(ed["direction"]), {"Long", "Short"})

    def test_directions_short_only(self):
        captured = {}

        def fake_upsert(original, edited, default_date=None):
            captured["edited"] = edited
            return {"ok": True, "inserted": len(edited), "updated": 0, "deleted": 0, "error": None}

        with patch.object(daily_plan, "list_plans", return_value=_empty_plan()), \
             patch.object(daily_plan, "upsert_plans", side_effect=fake_upsert):
            sel = pd.DataFrame([{"contract_name": "ES", "max_contracts": 1, "max_stop_points": 8.0}])
            risk_plan.push_to_daily_plans(date(2026, 5, 30), sel, directions=("Short",))

        ed = captured["edited"]
        self.assertEqual(len(ed), 1)
        self.assertEqual(ed.iloc[0]["direction"], "Short")


class UpsertPlanTests(unittest.TestCase):
    def test_injects_user_id_and_conflict(self):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.return_value.data = [{"id": 7}]
        with patch.object(risk_plan.auth, "get_client", return_value=client), \
             patch.object(risk_plan.auth, "current_user_id", return_value="u-1"):
            out = risk_plan.upsert_plan({
                "plan_date": "2026-05-30", "balance_usd": 50000,
                "mll_threshold_usd": 48000, "risk_value": 1.0,
            })
        self.assertTrue(out["ok"])
        self.assertEqual(out["id"], 7)
        args, kwargs = client.table.return_value.upsert.call_args
        self.assertEqual(args[0]["user_id"], "u-1")
        self.assertEqual(kwargs.get("on_conflict"), "user_id,plan_date")

    def test_no_user(self):
        with patch.object(risk_plan.auth, "get_client", return_value=MagicMock()), \
             patch.object(risk_plan.auth, "current_user_id", return_value=None):
            out = risk_plan.upsert_plan({"plan_date": "2026-05-30"})
        self.assertFalse(out["ok"])


class SaveReviewTests(unittest.TestCase):
    def test_injects_user_id_and_conflict(self):
        client = MagicMock()
        with patch.object(risk_plan.auth, "get_client", return_value=client), \
             patch.object(risk_plan.auth, "current_user_id", return_value="u-9"):
            out = risk_plan.save_review(date(2026, 5, 1), date(2026, 5, 30), {
                "total_days": 10, "clean_days": 7, "score_pct": 70.0,
                "stop_furado": 2, "risco_excedido": 1, "dll_furado": 0, "blowout": 0,
            })
        self.assertTrue(out["ok"])
        args, kwargs = client.table.return_value.upsert.call_args
        self.assertEqual(args[0]["user_id"], "u-9")
        self.assertEqual(args[0]["score_pct"], 70.0)
        self.assertEqual(args[0]["period_end"], "2026-05-30")
        self.assertEqual(kwargs.get("on_conflict"), "user_id,period_start,period_end")

    def test_no_user(self):
        with patch.object(risk_plan.auth, "get_client", return_value=MagicMock()), \
             patch.object(risk_plan.auth, "current_user_id", return_value=None):
            out = risk_plan.save_review(date(2026, 5, 1), date(2026, 5, 30), {})
        self.assertFalse(out["ok"])


class ListReviewsTests(unittest.TestCase):
    def test_returns_df(self):
        client = MagicMock()
        qb = MagicMock()
        qb.order.return_value = qb
        qb.limit.return_value = qb
        qb.execute.return_value.data = [{"period_end": "2026-05-30", "score_pct": 80.0}]
        client.table.return_value.select.return_value = qb
        with patch.object(risk_plan.auth, "get_client", return_value=client):
            out = risk_plan.list_reviews()
        self.assertEqual(len(out), 1)

    def test_exception_returns_empty(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch.object(risk_plan.auth, "get_client", return_value=client):
            out = risk_plan.list_reviews()
        self.assertTrue(out.empty)


class SaveAssetsTests(unittest.TestCase):
    def test_injects_ids(self):
        client = MagicMock()
        with patch.object(risk_plan.auth, "get_client", return_value=client), \
             patch.object(risk_plan.auth, "current_user_id", return_value="u-1"):
            out = risk_plan.save_assets(7, [{"contract_name": "MNQ", "max_contracts": 5}])
        self.assertTrue(out["ok"])
        # delete por risk_plan_id chamado
        client.table.return_value.delete.return_value.eq.assert_called_with("risk_plan_id", 7)
        # insert injeta risk_plan_id + user_id
        ins_args, _ = client.table.return_value.insert.call_args
        self.assertEqual(ins_args[0][0]["risk_plan_id"], 7)
        self.assertEqual(ins_args[0][0]["user_id"], "u-1")


class ListPlansRangeTests(unittest.TestCase):
    def _client_returning(self, rows):
        client = MagicMock()
        qb = MagicMock()
        qb.gte.return_value = qb
        qb.lte.return_value = qb
        qb.order.return_value = qb
        qb.execute.return_value.data = rows
        client.table.return_value.select.return_value = qb
        return client, qb

    def test_filters_range_and_returns_df(self):
        rows = [{"plan_date": "2026-05-28", "balance_usd": 50000}]
        client, qb = self._client_returning(rows)
        with patch.object(risk_plan.auth, "get_client", return_value=client):
            out = risk_plan.list_plans_range(date(2026, 5, 28), date(2026, 5, 30))
        self.assertEqual(len(out), 1)
        qb.gte.assert_called_with("plan_date", "2026-05-28")
        qb.lte.assert_called_with("plan_date", "2026-05-30")

    def test_empty_data_returns_empty_df(self):
        client, _ = self._client_returning([])
        with patch.object(risk_plan.auth, "get_client", return_value=client):
            out = risk_plan.list_plans_range(date(2026, 5, 1), date(2026, 5, 2))
        self.assertTrue(out.empty)

    def test_exception_returns_empty_df(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        with patch.object(risk_plan.auth, "get_client", return_value=client):
            out = risk_plan.list_plans_range(date(2026, 5, 1), date(2026, 5, 2))
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
