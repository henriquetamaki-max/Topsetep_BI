"""Tests para helpers de CRUD/manipulacao em massa em src/daily_plan.py.

Cobre `last_planned_date_before`, `copy_plans` e `delete_plans_for_date`.
Cliente Supabase e' mockado via unittest.mock (sem rede).
"""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import daily_plan


def _make_chainable_select(rows: list[dict]) -> MagicMock:
    """Mock que retorna `rows` no .execute() final da cadeia
    .select(...).lt(...).order(...).limit(...).execute()."""
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows)
    # Cada metodo intermediario retorna self (leaf).
    leaf.select.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.lt.return_value = leaf
    leaf.order.return_value = leaf
    leaf.limit.return_value = leaf
    return leaf


def _make_chainable_delete(rows_deleted: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows_deleted)
    leaf.delete.return_value = leaf
    leaf.eq.return_value = leaf
    return leaf


def _make_chainable_insert(rows_inserted: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows_inserted)
    leaf.insert.return_value = leaf
    return leaf


class LastPlannedDateBeforeTests(unittest.TestCase):

    @patch("daily_plan.auth")
    def test_returns_most_recent_prior_date(self, mock_auth):
        chain = _make_chainable_select([{"plan_date": "2026-05-19"}])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = daily_plan.last_planned_date_before(date(2026, 5, 20))
        self.assertEqual(result, date(2026, 5, 19))
        client.table.assert_called_once_with("daily_plans")
        chain.lt.assert_called_once_with("plan_date", "2026-05-20")
        # ordem desc + limit 1 — comportamento esperado
        chain.order.assert_called_once()
        chain.limit.assert_called_once_with(1)

    @patch("daily_plan.auth")
    def test_returns_none_when_no_history(self, mock_auth):
        chain = _make_chainable_select([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        self.assertIsNone(daily_plan.last_planned_date_before(date(2026, 5, 20)))


class DeletePlansForDateTests(unittest.TestCase):

    @patch("daily_plan.auth")
    def test_deletes_and_counts(self, mock_auth):
        chain = _make_chainable_delete([{"id": 1}, {"id": 2}, {"id": 3}])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = daily_plan.delete_plans_for_date(date(2026, 5, 20))
        self.assertEqual(result, {"ok": True, "deleted": 3, "error": None})
        chain.eq.assert_called_once_with("plan_date", "2026-05-20")

    @patch("daily_plan.auth")
    def test_returns_zero_when_nothing_to_delete(self, mock_auth):
        chain = _make_chainable_delete([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = daily_plan.delete_plans_for_date(date(2026, 5, 20))
        self.assertEqual(result, {"ok": True, "deleted": 0, "error": None})

    @patch("daily_plan.auth")
    def test_returns_error_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("boom")
        mock_auth.get_client.return_value = client

        result = daily_plan.delete_plans_for_date(date(2026, 5, 20))
        self.assertFalse(result["ok"])
        self.assertEqual(result["deleted"], 0)
        self.assertIn("boom", result["error"])


class CopyPlansTests(unittest.TestCase):

    def _fake_list(self, src_rows, dst_rows):
        """Devolve um callable que substitui daily_plan.list_plans."""
        import pandas as pd

        def _impl(plan_date: date):
            if plan_date == date(2026, 5, 19):
                return pd.DataFrame(src_rows)
            if plan_date == date(2026, 5, 20):
                return pd.DataFrame(dst_rows)
            return pd.DataFrame()
        return _impl

    @patch("daily_plan.auth")
    @patch("daily_plan.list_plans")
    def test_copies_all_when_dest_empty(self, mock_list, mock_auth):
        src = [
            {
                "id": 1, "plan_date": date(2026, 5, 19),
                "contract_name": "MNQ", "direction": "Long", "max_size": 2,
                "entry_trigger": "vwap reclaim", "stop_points": 8.0,
                "target_points": 24.0, "notes": "morning plan",
            },
            {
                "id": 2, "plan_date": date(2026, 5, 19),
                "contract_name": "MES", "direction": "Short", "max_size": 1,
                "entry_trigger": None, "stop_points": 4.0,
                "target_points": 12.0, "notes": None,
            },
        ]
        mock_list.side_effect = self._fake_list(src, [])

        insert_chain = _make_chainable_insert([{"id": 10}, {"id": 11}])
        client = MagicMock()
        client.table.return_value = insert_chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        result = daily_plan.copy_plans(date(2026, 5, 19), date(2026, 5, 20))
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["copied"], 2)
        self.assertEqual(result["skipped"], 0)

        # Conferir payload do INSERT (1ª chamada de insert).
        insert_chain.insert.assert_called_once()
        rows = insert_chain.insert.call_args.args[0]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["user_id"], "user-abc")
            self.assertEqual(r["plan_date"], "2026-05-20")
        # checagem por contrato
        by_contract = {r["contract_name"]: r for r in rows}
        self.assertEqual(by_contract["MNQ"]["direction"], "Long")
        self.assertEqual(by_contract["MNQ"]["max_size"], 2)
        self.assertEqual(by_contract["MNQ"]["entry_trigger"], "vwap reclaim")
        self.assertEqual(by_contract["MES"]["entry_trigger"], None)

    @patch("daily_plan.auth")
    @patch("daily_plan.list_plans")
    def test_skips_existing_contract_direction_pairs(self, mock_list, mock_auth):
        src = [
            {
                "id": 1, "contract_name": "MNQ", "direction": "Long",
                "max_size": 2, "stop_points": 8.0, "target_points": 24.0,
                "entry_trigger": None, "notes": None, "plan_date": date(2026, 5, 19),
            },
            {
                "id": 2, "contract_name": "MES", "direction": "Short",
                "max_size": 1, "stop_points": 4.0, "target_points": 12.0,
                "entry_trigger": None, "notes": None, "plan_date": date(2026, 5, 19),
            },
        ]
        dst = [
            # destino ja tem MNQ Long — deve ser pulado
            {
                "id": 99, "contract_name": "MNQ", "direction": "Long",
                "max_size": 1, "stop_points": 5.0, "target_points": 10.0,
                "entry_trigger": None, "notes": None, "plan_date": date(2026, 5, 20),
            },
        ]
        mock_list.side_effect = self._fake_list(src, dst)

        insert_chain = _make_chainable_insert([{"id": 50}])
        client = MagicMock()
        client.table.return_value = insert_chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        result = daily_plan.copy_plans(date(2026, 5, 19), date(2026, 5, 20))
        self.assertEqual(result["copied"], 1)
        self.assertEqual(result["skipped"], 1)
        # apenas MES inserido
        rows = insert_chain.insert.call_args.args[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contract_name"], "MES")

    @patch("daily_plan.auth")
    @patch("daily_plan.list_plans")
    def test_empty_source(self, mock_list, mock_auth):
        import pandas as pd
        mock_list.return_value = pd.DataFrame()
        mock_auth.current_user_id.return_value = "user-abc"
        mock_auth.get_client.return_value = MagicMock()

        result = daily_plan.copy_plans(date(2026, 5, 18), date(2026, 5, 20))
        self.assertEqual(result, {"ok": True, "copied": 0, "skipped": 0, "error": None})
        # INSERT nao deve ter sido chamado.
        client = mock_auth.get_client.return_value
        client.table.assert_not_called()

    @patch("daily_plan.auth")
    @patch("daily_plan.list_plans")
    def test_no_authenticated_user(self, mock_list, mock_auth):
        import pandas as pd
        mock_list.return_value = pd.DataFrame([
            {"contract_name": "MNQ", "direction": "Long", "max_size": 1,
             "stop_points": 5.0, "target_points": 10.0,
             "entry_trigger": None, "notes": None, "plan_date": date(2026, 5, 19)},
        ])
        mock_auth.current_user_id.return_value = None
        # get_client nao deveria ser chamado, mas safeguard:
        mock_auth.get_client.return_value = MagicMock()

        result = daily_plan.copy_plans(date(2026, 5, 19), date(2026, 5, 20))
        self.assertFalse(result["ok"])
        self.assertIn("autenticado", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
