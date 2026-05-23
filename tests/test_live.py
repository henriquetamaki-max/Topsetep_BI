"""Tests para src/live.py — funcoes puras de fetch (cache + Supabase chain).

Foco em logica testavel sem renderizar Streamlit: `_fetch_last_snapshot` e
`_fetch_recent_alerts`. As funcoes `_section_*` chamam diretamente st.metric/
st.markdown/etc e nao sao cobertas aqui (test harness seria mock hell sem
ganho real — visual review supre).

Cliente Supabase e' mockado via unittest.mock. `st.cache_data` precisa ser
limpo entre testes para evitar resposta carregada do teste anterior.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import live  # noqa: E402


def _make_snapshot_chain(rows: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows)
    leaf.select.return_value = leaf
    leaf.order.return_value = leaf
    leaf.limit.return_value = leaf
    return leaf


def _setup_client(rows: list[dict]) -> MagicMock:
    chain = _make_snapshot_chain(rows)
    client = MagicMock()
    client.table.return_value = chain
    return client


class FetchLastSnapshotTests(unittest.TestCase):
    """`_fetch_last_snapshot(user_id)` -> dict | None.

    Query: client.table("live_snapshots").select("*").order("snapshot_at",
    desc=True).limit(1).execute(). Retorna primeiro row ou None.
    """

    def setUp(self) -> None:
        live._fetch_last_snapshot.clear()

    @patch("live.auth")
    def test_returns_none_when_no_rows(self, mock_auth):
        mock_auth.get_client.return_value = _setup_client([])
        self.assertIsNone(live._fetch_last_snapshot("user-1"))

    @patch("live.auth")
    def test_returns_first_row_when_present(self, mock_auth):
        row = {
            "id": 42,
            "snapshot_at": "2026-05-23T12:00:00+00:00",
            "account_id": "TEST-ACC-001",
            "position_contract": "MES",
            "position_side": "Long",
            "position_size": 1,
            "day_pnl": 0,
        }
        mock_auth.get_client.return_value = _setup_client([row])
        result = live._fetch_last_snapshot("user-1")
        self.assertEqual(result, row)

    @patch("live.auth")
    def test_returns_only_first_when_multiple_rows(self, mock_auth):
        rows = [
            {"id": 99, "snapshot_at": "2026-05-23T13:00:00Z"},
            {"id": 98, "snapshot_at": "2026-05-23T12:00:00Z"},
        ]
        mock_auth.get_client.return_value = _setup_client(rows)
        result = live._fetch_last_snapshot("user-1")
        self.assertEqual(result["id"], 99)

    @patch("live.auth")
    def test_returns_none_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("Supabase down")
        mock_auth.get_client.return_value = client
        # Exception interna nao deve propagar — caller pinta "no snapshot".
        self.assertIsNone(live._fetch_last_snapshot("user-1"))

    @patch("live.auth")
    def test_order_is_snapshot_at_desc(self, mock_auth):
        chain = _make_snapshot_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        live._fetch_last_snapshot("user-1")

        chain.order.assert_called_once_with("snapshot_at", desc=True)

    @patch("live.auth")
    def test_limit_is_one(self, mock_auth):
        chain = _make_snapshot_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        live._fetch_last_snapshot("user-1")

        chain.limit.assert_called_once_with(1)

    @patch("live.auth")
    def test_queries_live_snapshots_table(self, mock_auth):
        client = _setup_client([])
        mock_auth.get_client.return_value = client

        live._fetch_last_snapshot("user-1")

        client.table.assert_called_once_with("live_snapshots")

    @patch("live.auth")
    def test_cache_is_isolated_by_user_id(self, mock_auth):
        # user-1 retorna row A; depois user-2 retorna row B sem mistura.
        # Validar isolamento e' importante: bug aqui = vazamento entre traders.
        def make_client(rows):
            return _setup_client(rows)

        # Primeiro: user-1
        mock_auth.get_client.return_value = make_client(
            [{"id": 1, "account_id": "U1"}]
        )
        r1 = live._fetch_last_snapshot("user-1")
        self.assertEqual(r1["account_id"], "U1")

        # Segundo: user-2 (key diferente, deve re-query)
        mock_auth.get_client.return_value = make_client(
            [{"id": 2, "account_id": "U2"}]
        )
        r2 = live._fetch_last_snapshot("user-2")
        self.assertEqual(r2["account_id"], "U2")


class FetchRecentAlertsTests(unittest.TestCase):
    """`_fetch_recent_alerts(user_id, limit=20)` delega para alerts.list_recent.

    Wrapper fino com cache TTL=2s. Cobre delegacao + propagacao de limit.
    """

    def setUp(self) -> None:
        live._fetch_recent_alerts.clear()

    @patch("live.alerts_mod")
    def test_delegates_to_alerts_list_recent(self, mock_alerts):
        expected = pd.DataFrame({"id": [1, 2], "severity": ["warn", "info"]})
        mock_alerts.list_recent.return_value = expected

        df = live._fetch_recent_alerts("user-1")

        pd.testing.assert_frame_equal(df, expected)
        mock_alerts.list_recent.assert_called_once_with(limit=20)

    @patch("live.alerts_mod")
    def test_default_limit_is_20(self, mock_alerts):
        mock_alerts.list_recent.return_value = pd.DataFrame()
        live._fetch_recent_alerts("user-1")
        mock_alerts.list_recent.assert_called_once_with(limit=20)

    @patch("live.alerts_mod")
    def test_custom_limit_passes_through(self, mock_alerts):
        mock_alerts.list_recent.return_value = pd.DataFrame()
        live._fetch_recent_alerts("user-1", limit=50)
        mock_alerts.list_recent.assert_called_once_with(limit=50)

    @patch("live.alerts_mod")
    def test_cache_isolated_by_user_id(self, mock_alerts):
        # Cache key inclui user_id — dois users nunca compartilham resultado.
        mock_alerts.list_recent.side_effect = [
            pd.DataFrame({"id": [1]}),
            pd.DataFrame({"id": [99]}),
        ]
        r1 = live._fetch_recent_alerts("user-1")
        r2 = live._fetch_recent_alerts("user-2")
        self.assertEqual(r1["id"].iloc[0], 1)
        self.assertEqual(r2["id"].iloc[0], 99)
        self.assertEqual(mock_alerts.list_recent.call_count, 2)


if __name__ == "__main__":
    unittest.main()
