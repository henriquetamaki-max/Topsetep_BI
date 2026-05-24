"""Tests para src/account.py — aba Account (plano + Stripe + JWT da extensao).

Cobre helpers puros (_fmt_money_cents, _fmt_date, _status_label) e
_load_subscription_row mockando auth.get_client. Funcoes Streamlit-bound
(_render_plan_card, _trigger_checkout, _trigger_portal, render_account_tab,
handle_checkout_return) ficam fora — exigem contexto Streamlit completo.
"""
from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import account


# ---------------------------------------------------------------------------
# _fmt_money_cents — formato BR ($1.900,00 USD)
# ---------------------------------------------------------------------------


class FmtMoneyCentsTests(unittest.TestCase):

    def test_below_thousand_uses_comma_decimal(self):
        # 1900 cents = 19.00 → "$19,00 USD" (formato BR: vírgula como decimal)
        self.assertEqual(account._fmt_money_cents(1900), "$19,00 USD")

    def test_thousands_use_dot_separator(self):
        # 490000 cents = 4900.00 → "$4.900,00 USD" (ponto como milhar)
        self.assertEqual(account._fmt_money_cents(490000), "$4.900,00 USD")

    def test_zero_cents(self):
        self.assertEqual(account._fmt_money_cents(0), "$0,00 USD")

    def test_custom_currency_label(self):
        self.assertEqual(account._fmt_money_cents(1900, currency="BRL"), "$19,00 BRL")

    def test_million_cents(self):
        # 100000000 cents = 1.000.000,00
        self.assertEqual(account._fmt_money_cents(100_000_000), "$1.000.000,00 USD")


# ---------------------------------------------------------------------------
# _fmt_date — None, ISO strings, datetime aware/naive, fallback
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class FmtDateTests(unittest.TestCase):

    def test_none_returns_em_dash(self):
        self.assertEqual(account._fmt_date(None), "—")

    def test_iso_string_with_z_suffix(self):
        # "Z" e' convertido para "+00:00" antes do fromisoformat
        out = account._fmt_date("2026-05-21T14:30:00Z")
        self.assertRegex(out, _DATE_RE)

    def test_iso_string_with_explicit_offset(self):
        out = account._fmt_date("2026-05-21T14:30:00+00:00")
        self.assertRegex(out, _DATE_RE)

    def test_invalid_string_returned_as_is(self):
        # fromisoformat falha → devolve a string original (nao quebra UI)
        self.assertEqual(account._fmt_date("nao-e-data"), "nao-e-data")

    def test_naive_datetime_assumes_utc(self):
        dt = datetime(2026, 5, 21, 14, 30)
        self.assertRegex(account._fmt_date(dt), _DATE_RE)

    def test_aware_datetime(self):
        dt = datetime(2026, 5, 21, 14, 30, tzinfo=timezone.utc)
        self.assertRegex(account._fmt_date(dt), _DATE_RE)

    def test_unexpected_type_falls_back_to_str(self):
        self.assertEqual(account._fmt_date(12345), "12345")


# ---------------------------------------------------------------------------
# _status_label — mapping i18n com fallback
# ---------------------------------------------------------------------------


class StatusLabelTests(unittest.TestCase):

    @patch("account.t", side_effect=lambda k: f"<{k}>")
    def test_known_statuses_use_i18n_keys(self, _mock_t):
        self.assertEqual(account._status_label("trialing"), "<billing.status.trialing>")
        self.assertEqual(account._status_label("active"), "<billing.status.active>")
        self.assertEqual(account._status_label("past_due"), "<billing.status.past_due>")
        self.assertEqual(account._status_label("canceled"), "<billing.status.canceled>")
        self.assertEqual(account._status_label("free"), "<billing.status.free>")

    @patch("account.t", side_effect=lambda k: f"<{k}>")
    def test_unknown_status_falls_back_to_raw_string(self, _mock_t):
        # Defensivo: status desconhecido nao quebra a UI, mostra o valor cru.
        self.assertEqual(account._status_label("future_state"), "future_state")

    @patch("account.t", side_effect=lambda k: f"<{k}>")
    def test_none_status_falls_back_to_em_dash(self, _mock_t):
        self.assertEqual(account._status_label(None), "—")


# ---------------------------------------------------------------------------
# _load_subscription_row — mock cadeia .table().select().eq().single().execute()
# ---------------------------------------------------------------------------


def _make_subscription_chain(row: dict | None) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=row)
    leaf.select.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.single.return_value = leaf
    return leaf


class LoadSubscriptionRowTests(unittest.TestCase):

    @patch("account.auth")
    def test_returns_row_when_exists(self, mock_auth):
        row = {
            "plan_slug": "pro",
            "status": "active",
            "stripe_customer_id": "cus_abc",
            "stripe_subscription_id": "sub_xyz",
            "current_period_start": "2026-05-01T00:00:00+00:00",
            "current_period_end": "2026-06-01T00:00:00+00:00",
            "trial_ends_at": None,
            "cancel_at_period_end": False,
        }
        chain = _make_subscription_chain(row)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = account._load_subscription_row("uid-123")
        self.assertEqual(result, row)
        client.table.assert_called_once_with("subscriptions")
        chain.eq.assert_called_once_with("user_id", "uid-123")
        chain.single.assert_called_once_with()

    @patch("account.auth")
    def test_returns_none_when_no_data(self, mock_auth):
        chain = _make_subscription_chain(None)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        self.assertIsNone(account._load_subscription_row("uid-vazio"))

    @patch("account.auth")
    def test_returns_none_on_exception(self, mock_auth):
        # RLS bloqueando ou tabela ainda nao criada — nao deve propagar.
        client = MagicMock()
        client.table.side_effect = RuntimeError("rls denied")
        mock_auth.get_client.return_value = client

        self.assertIsNone(account._load_subscription_row("uid-x"))

    @patch("account.auth")
    def test_select_columns_include_customer_id(self, mock_auth):
        # Acessar customer_id e' o motivo de existir esta funcao — billing.get_effective_plan
        # nao retorna esse campo. Quebrar o select quebra abertura do Customer Portal.
        chain = _make_subscription_chain(None)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        account._load_subscription_row("uid-y")
        select_arg = chain.select.call_args.args[0]
        self.assertIn("stripe_customer_id", select_arg)


# ---------------------------------------------------------------------------
# _PLAN_CATALOG — consistencia com seed do saas_schema.sql
# ---------------------------------------------------------------------------


class PlanCatalogTests(unittest.TestCase):

    def test_has_basic_and_pro_only(self):
        # Trial e free nao tem cartao de upgrade (sao gerenciados pelo banco).
        # Admin idem (gestor da plataforma).
        slugs = [p["slug"] for p in account._PLAN_CATALOG]
        self.assertEqual(slugs, ["basic", "pro"])

    def test_basic_price_matches_seed(self):
        # public.plans seed em PRD/saas_schema.sql: basic = 1900 cents.
        basic = next(p for p in account._PLAN_CATALOG if p["slug"] == "basic")
        self.assertEqual(basic["price_cents"], 1900)
        self.assertEqual(basic["trade_limit"], 100)

    def test_pro_is_unlimited_imports(self):
        # trade_limit=None significa ilimitado (regra do dashboard).
        pro = next(p for p in account._PLAN_CATALOG if p["slug"] == "pro")
        self.assertEqual(pro["price_cents"], 4900)
        self.assertIsNone(pro["trade_limit"])

    def test_all_entries_have_i18n_keys(self):
        for p in account._PLAN_CATALOG:
            self.assertTrue(p["name_key"].startswith("billing.plan."))
            self.assertTrue(p["tagline_key"].startswith("billing.plan."))

    def test_all_entries_have_positive_price(self):
        for p in account._PLAN_CATALOG:
            self.assertGreater(p["price_cents"], 0)


# ---------------------------------------------------------------------------
# _build_jwt_bundle — serializa session p/ refresh automatico na extensao v0.2.0+
# ---------------------------------------------------------------------------


import json  # noqa: E402  (local to this section)


class BuildJwtBundleTests(unittest.TestCase):

    def test_none_session_returns_none(self):
        self.assertIsNone(account._build_jwt_bundle(None))

    def test_empty_session_returns_none(self):
        self.assertIsNone(account._build_jwt_bundle({}))

    def test_session_without_access_token_returns_none(self):
        self.assertIsNone(account._build_jwt_bundle({"refresh_token": "r"}))

    def test_full_session_returns_compact_json(self):
        sess = {
            "access_token": "eyJhbG.access.xyz",
            "refresh_token": "v2.refresh.abc",
            "expires_at": 1729123456,
        }
        out = account._build_jwt_bundle(sess)
        self.assertIsInstance(out, str)
        parsed = json.loads(out)
        self.assertEqual(parsed["access_token"], "eyJhbG.access.xyz")
        self.assertEqual(parsed["refresh_token"], "v2.refresh.abc")
        self.assertEqual(parsed["expires_at"], 1729123456)

    def test_missing_refresh_token_defaults_to_empty_string(self):
        # Trader que loga via OAuth + provider sem refresh_token: nao quebra,
        # bundle tem refresh_token="" e extensao trata como modo legado.
        sess = {"access_token": "x", "expires_at": 1729000000}
        out = account._build_jwt_bundle(sess)
        parsed = json.loads(out)
        self.assertEqual(parsed["refresh_token"], "")

    def test_missing_expires_at_preserved_as_none(self):
        # Algumas sessions antigas nao tem expires_at — extensao decodifica
        # o JWT diretamente p/ extrair exp claim como fallback.
        sess = {"access_token": "x", "refresh_token": "r"}
        out = account._build_jwt_bundle(sess)
        parsed = json.loads(out)
        self.assertIsNone(parsed["expires_at"])

    def test_compact_format_no_spaces(self):
        # JSON compacto (sem espacos apos virgulas/dois-pontos) facilita o
        # paste integro num campo de texto da extensao sem quebra de linha.
        sess = {"access_token": "a", "refresh_token": "r", "expires_at": 1}
        out = account._build_jwt_bundle(sess)
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_null_refresh_token_normalized_to_empty(self):
        # session.refresh_token pode chegar como None (auth.py setattr fallback)
        # — normalizamos p/ "" para o JSON nao ficar com null literal.
        sess = {"access_token": "a", "refresh_token": None, "expires_at": 1}
        out = account._build_jwt_bundle(sess)
        parsed = json.loads(out)
        self.assertEqual(parsed["refresh_token"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
