"""Tests para src/action_plan.py — list_items, _normalize_row, upsert_items.

Cliente Supabase mockado via unittest.mock. Foca em:
- list_items: schema previsivel, sort por (done, prio, due_date), coercoes
- _normalize_row: defaults, validacao de enum, sync done<->status
- upsert_items: diff insert/update/delete, ignora linhas em branco, auth check
"""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd


import action_plan


def _make_select_chain(rows: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows)
    leaf.select.return_value = leaf
    return leaf


def _make_crud_chain() -> MagicMock:
    """Mock que aceita insert/update/delete encadeados — todos retornam self."""
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=[{"id": 1}])
    leaf.insert.return_value = leaf
    leaf.update.return_value = leaf
    leaf.delete.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.in_.return_value = leaf
    return leaf


class ListItemsTests(unittest.TestCase):

    @patch("action_plan.auth")
    def test_empty_returns_schema_correct_dataframe(self, mock_auth):
        chain = _make_select_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        df = action_plan.list_items()
        self.assertTrue(df.empty)
        # Mesmo vazio, deve ter todas as colunas (para a UI nao quebrar).
        for col in action_plan.ALL_COLUMNS:
            self.assertIn(col, df.columns)

    @patch("action_plan.auth")
    def test_sort_order_pending_high_due_first(self, mock_auth):
        rows = [
            {"id": 1, "task": "Done easy", "status": "Concluído",
             "priority": "Alta", "due_date": "2026-05-21", "done": True,
             "created_at": None, "updated_at": None},
            {"id": 2, "task": "Pending low far", "status": "Pendente",
             "priority": "Baixa", "due_date": "2026-12-31", "done": False,
             "created_at": None, "updated_at": None},
            {"id": 3, "task": "Pending high near", "status": "Pendente",
             "priority": "Alta", "due_date": "2026-05-22", "done": False,
             "created_at": None, "updated_at": None},
            {"id": 4, "task": "Pending high far", "status": "Pendente",
             "priority": "Alta", "due_date": "2026-06-30", "done": False,
             "created_at": None, "updated_at": None},
        ]
        chain = _make_select_chain(rows)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        df = action_plan.list_items()
        # nao-concluidos antes do concluido
        self.assertEqual(df["done"].tolist(), [False, False, False, True])
        # entre nao-concluidos: Alta proxima, Alta distante, Baixa distante
        tasks = df["task"].tolist()
        self.assertEqual(tasks[0], "Pending high near")
        self.assertEqual(tasks[1], "Pending high far")
        self.assertEqual(tasks[2], "Pending low far")

    @patch("action_plan.auth")
    def test_due_date_coerced_to_python_date(self, mock_auth):
        rows = [{"id": 1, "task": "x", "status": "Pendente",
                 "priority": "Média", "due_date": "2026-05-22", "done": False,
                 "created_at": None, "updated_at": None}]
        chain = _make_select_chain(rows)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        df = action_plan.list_items()
        self.assertEqual(df["due_date"].iloc[0], date(2026, 5, 22))


class NormalizeRowTests(unittest.TestCase):

    def test_basic(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "  Read book  ", "status": "Pendente",
            "priority": "Alta", "due_date": date(2026, 5, 25), "done": False,
        }))
        self.assertEqual(out["task"], "Read book")
        self.assertEqual(out["status"], "Pendente")
        self.assertEqual(out["priority"], "Alta")
        self.assertEqual(out["due_date"], "2026-05-25")
        self.assertFalse(out["done"])

    def test_done_forces_status_concluido(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "x", "status": "Pendente", "priority": "Alta",
            "due_date": None, "done": True,
        }))
        self.assertEqual(out["status"], "Concluído")
        self.assertTrue(out["done"])

    def test_status_concluido_forces_done_true(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "x", "status": "Concluído", "priority": "Alta",
            "due_date": None, "done": False,
        }))
        self.assertTrue(out["done"])

    def test_invalid_enum_falls_back_to_default(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "x", "status": "Bogus", "priority": "Critica",
            "due_date": None, "done": False,
        }))
        self.assertEqual(out["status"], "Pendente")
        self.assertEqual(out["priority"], "Média")

    def test_due_date_nan_becomes_none(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "x", "status": "Pendente", "priority": "Alta",
            "due_date": pd.NaT, "done": False,
        }))
        self.assertIsNone(out["due_date"])

    def test_empty_string_due_date_becomes_none(self):
        out = action_plan._normalize_row(pd.Series({
            "task": "x", "status": "Pendente", "priority": "Alta",
            "due_date": "", "done": False,
        }))
        self.assertIsNone(out["due_date"])

    def test_missing_task_becomes_empty_string(self):
        out = action_plan._normalize_row(pd.Series({
            "status": "Pendente", "priority": "Alta",
            "due_date": None, "done": False,
        }))
        self.assertEqual(out["task"], "")


class UpsertItemsTests(unittest.TestCase):

    def _setup_client(self, mock_auth, chain=None, user_id="user-abc"):
        chain = chain or _make_crud_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = user_id
        return chain

    @patch("action_plan.auth")
    def test_insert_new_row(self, mock_auth):
        chain = self._setup_client(mock_auth)
        original = pd.DataFrame(columns=action_plan.ALL_COLUMNS)
        edited = pd.DataFrame([
            {"id": None, "task": "New task", "status": "Pendente",
             "priority": "Alta", "due_date": date(2026, 5, 22), "done": False},
        ])
        result = action_plan.upsert_items(original, edited)
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)

        # user_id deve ser injetado no payload
        chain.insert.assert_called_once()
        rows = chain.insert.call_args.args[0]
        self.assertEqual(rows[0]["user_id"], "user-abc")
        self.assertEqual(rows[0]["task"], "New task")

    @patch("action_plan.auth")
    def test_update_row_when_changed(self, mock_auth):
        chain = self._setup_client(mock_auth)
        original = pd.DataFrame([
            {"id": 7, "created_at": None, "updated_at": None,
             "task": "Old task", "status": "Pendente",
             "priority": "Alta", "due_date": date(2026, 5, 22), "done": False},
        ])
        edited = pd.DataFrame([
            {"id": 7, "task": "Updated task", "status": "Em andamento",
             "priority": "Alta", "due_date": date(2026, 5, 22), "done": False},
        ])
        result = action_plan.upsert_items(original, edited)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["inserted"], 0)
        chain.update.assert_called_once()
        chain.eq.assert_called_once_with("id", 7)

    @patch("action_plan.auth")
    def test_delete_rows_removed_from_edited(self, mock_auth):
        chain = self._setup_client(mock_auth)
        original = pd.DataFrame([
            {"id": 7, "created_at": None, "updated_at": None,
             "task": "Will delete", "status": "Pendente",
             "priority": "Alta", "due_date": None, "done": False},
            {"id": 8, "created_at": None, "updated_at": None,
             "task": "Survives", "status": "Pendente",
             "priority": "Média", "due_date": None, "done": False},
        ])
        edited = pd.DataFrame([
            {"id": 8, "task": "Survives", "status": "Pendente",
             "priority": "Média", "due_date": None, "done": False},
        ])
        result = action_plan.upsert_items(original, edited)
        self.assertEqual(result["deleted"], 1)
        chain.delete.assert_called_once()
        chain.in_.assert_called_once_with("id", [7])

    @patch("action_plan.auth")
    def test_blank_row_ignored(self, mock_auth):
        chain = self._setup_client(mock_auth)
        original = pd.DataFrame(columns=action_plan.ALL_COLUMNS)
        edited = pd.DataFrame([
            # Clique no "+" do data_editor — task em branco
            {"id": None, "task": "", "status": "Pendente",
             "priority": "Alta", "due_date": None, "done": False},
        ])
        result = action_plan.upsert_items(original, edited)
        self.assertEqual(result["inserted"], 0)
        chain.insert.assert_not_called()

    @patch("action_plan.auth")
    def test_no_diff_no_update(self, mock_auth):
        chain = self._setup_client(mock_auth)
        original = pd.DataFrame([
            {"id": 7, "created_at": None, "updated_at": None,
             "task": "Same", "status": "Pendente",
             "priority": "Alta", "due_date": date(2026, 5, 22), "done": False},
        ])
        edited = original.drop(columns=["created_at", "updated_at"]).copy()
        result = action_plan.upsert_items(original, edited)
        self.assertEqual(result["updated"], 0)
        chain.update.assert_not_called()

    @patch("action_plan.auth")
    def test_insert_blocked_when_no_user(self, mock_auth):
        chain = self._setup_client(mock_auth, user_id=None)
        original = pd.DataFrame(columns=action_plan.ALL_COLUMNS)
        edited = pd.DataFrame([
            {"id": None, "task": "Task", "status": "Pendente",
             "priority": "Alta", "due_date": None, "done": False},
        ])
        result = action_plan.upsert_items(original, edited)
        self.assertFalse(result["ok"])
        self.assertIn("autenticado", result["error"])
        chain.insert.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
