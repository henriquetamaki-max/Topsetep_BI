"""Tests para src/alerts.py — list_recent, mark_read, mark_dismissed, mark_all_read.

Cliente Supabase e' mockado via unittest.mock. RLS nao e' testada aqui (a
funcao confia no banco); cobrimos so' que as queries certas sao construidas.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import alerts  # noqa: E402


def _make_select_chain(rows: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows)
    leaf.select.return_value = leaf
    leaf.order.return_value = leaf
    leaf.limit.return_value = leaf
    return leaf


def _make_update_chain() -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=[{"id": 1}])
    leaf.update.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.is_.return_value = leaf
    return leaf


class ListRecentTests(unittest.TestCase):

    @patch("alerts.auth")
    def test_returns_empty_dataframe_when_no_rows(self, mock_auth):
        chain = _make_select_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        df = alerts.list_recent()
        self.assertTrue(df.empty)

    @patch("alerts.auth")
    def test_returns_dataframe_with_datetime_coercion(self, mock_auth):
        rows = [
            {
                "id": 1, "alert_type": "daily_loss_limit",
                "severity": "warn", "source": "trigger",
                "created_at": "2026-05-21T14:30:00+00:00",
                "read_at": None, "dismissed_at": None,
                "message": "DLL approaching",
            },
            {
                "id": 2, "alert_type": "max_size",
                "severity": "critical", "source": "trigger",
                "created_at": "2026-05-21T13:30:00+00:00",
                "read_at": "2026-05-21T13:35:00+00:00",
                "dismissed_at": None,
                "message": "size 6 > max 5",
            },
        ]
        chain = _make_select_chain(rows)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        df = alerts.list_recent(limit=10)
        self.assertEqual(len(df), 2)
        # created_at deve estar tz-aware (UTC)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["created_at"]))
        self.assertIsNotNone(df["created_at"].iloc[0].tzinfo)
        # read_at do alerta 1 e' null → NaT
        self.assertTrue(pd.isna(df["read_at"].iloc[0]))
        # read_at do alerta 2 e' valido
        self.assertFalse(pd.isna(df["read_at"].iloc[1]))

    @patch("alerts.auth")
    def test_default_limit_is_50(self, mock_auth):
        chain = _make_select_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        alerts.list_recent()
        chain.limit.assert_called_once_with(50)

    @patch("alerts.auth")
    def test_custom_limit_passes_through(self, mock_auth):
        chain = _make_select_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        alerts.list_recent(limit=5)
        chain.limit.assert_called_once_with(5)

    @patch("alerts.auth")
    def test_order_is_desc_by_created_at(self, mock_auth):
        chain = _make_select_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        alerts.list_recent()
        chain.order.assert_called_once_with("created_at", desc=True)

    @patch("alerts.auth")
    def test_returns_empty_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("network")
        mock_auth.get_client.return_value = client

        # Falha silenciosa — aba Live nao pode quebrar por falha de leitura
        # de alertas. Comportamento intencional.
        df = alerts.list_recent()
        self.assertTrue(df.empty)


class MarkReadTests(unittest.TestCase):

    @patch("alerts.auth")
    def test_patches_read_at_with_iso_now(self, mock_auth):
        chain = _make_update_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = alerts.mark_read(42)
        self.assertEqual(result, {"ok": True, "error": None})

        chain.update.assert_called_once()
        payload = chain.update.call_args.args[0]
        self.assertIn("read_at", payload)
        # read_at deve ser ISO timestamp UTC
        ts = datetime.fromisoformat(payload["read_at"])
        self.assertIsNotNone(ts.tzinfo)
        self.assertEqual(ts.tzinfo.utcoffset(ts), timezone.utc.utcoffset(ts))
        # filtro deve usar id correto
        chain.eq.assert_called_once_with("id", 42)

    @patch("alerts.auth")
    def test_returns_error_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("rls denied")
        mock_auth.get_client.return_value = client

        result = alerts.mark_read(42)
        self.assertFalse(result["ok"])
        self.assertIn("rls denied", result["error"])

    @patch("alerts.auth")
    def test_coerces_string_id_to_int(self, mock_auth):
        chain = _make_update_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        alerts.mark_read("42")  # tipo: ignore — UI pode mandar str do widget
        chain.eq.assert_called_once_with("id", 42)


class MarkDismissedTests(unittest.TestCase):

    @patch("alerts.auth")
    def test_patches_dismissed_at(self, mock_auth):
        chain = _make_update_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = alerts.mark_dismissed(99)
        self.assertEqual(result, {"ok": True, "error": None})
        payload = chain.update.call_args.args[0]
        self.assertIn("dismissed_at", payload)
        chain.eq.assert_called_once_with("id", 99)


class MarkAllReadTests(unittest.TestCase):

    @patch("alerts.auth")
    def test_updates_only_unread_via_is_null_filter(self, mock_auth):
        chain = _make_update_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = alerts.mark_all_read()
        self.assertEqual(result, {"ok": True, "error": None})

        # update + is_(read_at, null) — sem isso, marcaria *tudo* (incluindo
        # alertas ja lidos) e sobrescreveria timestamps validos.
        chain.update.assert_called_once()
        chain.is_.assert_called_once_with("read_at", "null")

    @patch("alerts.auth")
    def test_returns_error_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("timeout")
        mock_auth.get_client.return_value = client

        result = alerts.mark_all_read()
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"])


class NowIsoTests(unittest.TestCase):

    def test_returns_utc_iso(self):
        ts_str = alerts._now_iso()
        ts = datetime.fromisoformat(ts_str)
        self.assertIsNotNone(ts.tzinfo)
        # comparacao com agora — janela folgada (1min) p/ tolerar lentidao
        delta = abs((datetime.now(timezone.utc) - ts).total_seconds())
        self.assertLess(delta, 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
