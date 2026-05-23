"""Tests para src/risk_settings.py.

Cobre get_settings (com/sem linha), upsert_settings (auth ausente, sucesso,
exception) e apply_account_defaults (4 perfis + fallback). Cliente Supabase
e' mockado via unittest.mock.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import risk_settings


def _make_select_chain(row: dict | None) -> MagicMock:
    """Mock para auth.get_client().table(TABLE).select(...).maybeSingle().execute()."""
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=row)
    leaf.select.return_value = leaf
    leaf.maybeSingle.return_value = leaf
    return leaf


def _make_upsert_chain() -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=[{"id": 1}])
    leaf.upsert.return_value = leaf
    return leaf


class GetSettingsTests(unittest.TestCase):

    @patch("risk_settings.auth")
    def test_returns_row_when_exists(self, mock_auth):
        row = {
            "user_id": "user-abc",
            "account_type": "Express 50K",
            "daily_loss_limit_usd": 1000.0,
            "trailing_drawdown_usd": 2000.0,
            "max_position_size": 5,
            "warning_pct": 80,
        }
        chain = _make_select_chain(row)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = risk_settings.get_settings()
        self.assertEqual(result, row)
        client.table.assert_called_once_with("risk_settings")

    @patch("risk_settings.auth")
    def test_returns_none_when_no_row(self, mock_auth):
        chain = _make_select_chain(None)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        self.assertIsNone(risk_settings.get_settings())

    @patch("risk_settings.auth")
    def test_returns_none_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db unreachable")
        mock_auth.get_client.return_value = client

        # Falha silenciosa — Risk Guard sumir vs. exception nao tratada
        # quebrando a aba Configuracoes inteira. Comportamento intencional.
        self.assertIsNone(risk_settings.get_settings())


class UpsertSettingsTests(unittest.TestCase):

    @patch("risk_settings.auth")
    def test_blocks_when_no_user(self, mock_auth):
        mock_auth.current_user_id.return_value = None
        mock_auth.get_client.return_value = MagicMock()

        result = risk_settings.upsert_settings({"daily_loss_limit_usd": 1000})
        self.assertEqual(result, {"ok": False, "error": "no_user"})

    @patch("risk_settings.auth")
    def test_injects_user_id_and_calls_upsert_with_on_conflict(self, mock_auth):
        chain = _make_upsert_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        payload = {
            "account_type": "Express 100K",
            "daily_loss_limit_usd": 2000.0,
            "trailing_drawdown_usd": 3000.0,
            "max_position_size": 5,
            "warning_pct": 80,
        }
        result = risk_settings.upsert_settings(payload)
        self.assertEqual(result, {"ok": True, "error": None})

        # user_id deve estar injetado na linha enviada
        chain.upsert.assert_called_once()
        call_args, call_kwargs = chain.upsert.call_args
        sent_row = call_args[0]
        self.assertEqual(sent_row["user_id"], "user-abc")
        self.assertEqual(sent_row["account_type"], "Express 100K")
        self.assertEqual(sent_row["daily_loss_limit_usd"], 2000.0)
        # on_conflict deve apontar para o PK (user_id)
        self.assertEqual(call_kwargs.get("on_conflict"), "user_id")

    @patch("risk_settings.auth")
    def test_returns_error_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("permission denied")
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        result = risk_settings.upsert_settings({"daily_loss_limit_usd": 1000})
        self.assertFalse(result["ok"])
        self.assertIn("permission denied", result["error"])

    @patch("risk_settings.auth")
    def test_payload_not_mutated_by_caller(self, mock_auth):
        """O payload original do caller nao deve ser mutado pelo upsert (caso
        re-usem o dict para outro fim — ex.: log)."""
        chain = _make_upsert_chain()
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-xyz"

        payload = {"daily_loss_limit_usd": 1500.0}
        before = dict(payload)
        risk_settings.upsert_settings(payload)
        self.assertEqual(payload, before, "payload do caller foi mutado")


class ApplyAccountDefaultsTests(unittest.TestCase):

    def test_express_50k(self):
        d = risk_settings.apply_account_defaults("Express 50K")
        self.assertEqual(d["daily_loss_limit_usd"], 1000.0)
        self.assertEqual(d["trailing_drawdown_usd"], 2000.0)

    def test_express_100k(self):
        d = risk_settings.apply_account_defaults("Express 100K")
        self.assertEqual(d["daily_loss_limit_usd"], 2000.0)
        self.assertEqual(d["trailing_drawdown_usd"], 3000.0)

    def test_express_150k(self):
        d = risk_settings.apply_account_defaults("Express 150K")
        self.assertEqual(d["daily_loss_limit_usd"], 3000.0)
        self.assertEqual(d["trailing_drawdown_usd"], 4500.0)

    def test_custom_is_zero(self):
        d = risk_settings.apply_account_defaults("Custom")
        self.assertEqual(d["daily_loss_limit_usd"], 0.0)
        self.assertEqual(d["trailing_drawdown_usd"], 0.0)

    def test_unknown_falls_back_to_custom(self):
        # Account type desconhecido nao deve quebrar — UI pode estar
        # passando string nova que ainda nao foi mapeada.
        d = risk_settings.apply_account_defaults("Express 250K")
        self.assertEqual(d["daily_loss_limit_usd"], 0.0)
        self.assertEqual(d["trailing_drawdown_usd"], 0.0)

    def test_returns_fresh_dict_not_shared_reference(self):
        # Importante: caller pode mutar; nao deve afetar defaults para chamadas
        # futuras. Bug em potencial se a funcao retornasse o dict do
        # ACCOUNT_DEFAULTS diretamente.
        d1 = risk_settings.apply_account_defaults("Express 50K")
        d1["daily_loss_limit_usd"] = 9999
        d2 = risk_settings.apply_account_defaults("Express 50K")
        self.assertEqual(d2["daily_loss_limit_usd"], 1000.0)


class AccountTypesConstantTests(unittest.TestCase):

    def test_account_types_match_defaults_keys(self):
        self.assertEqual(
            set(risk_settings.ACCOUNT_TYPES),
            set(risk_settings.ACCOUNT_DEFAULTS.keys()),
        )

    def test_order_preserved(self):
        # UI usa esta ordem no SelectboxColumn — quebrar a ordem confunde
        # o usuario sem necessidade. Trava regressao.
        self.assertEqual(
            risk_settings.ACCOUNT_TYPES,
            ["Express 50K", "Express 100K", "Express 150K", "Custom"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
