"""Tests para src/billing.py — gate de features e ciclo de vida de trial/sub.

Cobre as funcoes puras (has_feature, feature_value, _parse_ts, days_left_in_trial,
is_trial_expired) + is_admin/ensure_subscription com mock. Funcoes que dependem
de auth._read_secret + modulo stripe (create_checkout_session, etc.) nao sao
testadas aqui — seriam testes de integracao.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import billing


# ---------------------------------------------------------------------------
# has_feature
# ---------------------------------------------------------------------------


class HasFeatureTests(unittest.TestCase):

    def test_true_for_truthy_value(self):
        plan = {"features": {"live_monitor": True}}
        self.assertTrue(billing.has_feature(plan, "live_monitor"))

    def test_false_for_missing_key(self):
        plan = {"features": {"live_monitor": True}}
        self.assertFalse(billing.has_feature(plan, "ai_coach"))

    def test_false_for_falsy_value(self):
        plan = {"features": {"live_monitor": False}}
        self.assertFalse(billing.has_feature(plan, "live_monitor"))

    def test_false_when_plan_is_none(self):
        self.assertFalse(billing.has_feature(None, "live_monitor"))

    def test_false_when_features_is_missing(self):
        self.assertFalse(billing.has_feature({}, "live_monitor"))

    def test_false_when_features_is_explicit_none(self):
        # Guarda contra null vindo do banco em vez de dict vazio.
        self.assertFalse(billing.has_feature({"features": None}, "live_monitor"))

    def test_truthy_string_value(self):
        # Plano basic pode ter features["dashboard"]="limited_30d" — non-empty
        # string e' truthy. Use feature_value() para ler o valor real.
        plan = {"features": {"dashboard": "limited_30d"}}
        self.assertTrue(billing.has_feature(plan, "dashboard"))


# ---------------------------------------------------------------------------
# feature_value
# ---------------------------------------------------------------------------


class FeatureValueTests(unittest.TestCase):

    def test_returns_value_when_set(self):
        plan = {"features": {"dashboard": "limited_30d"}}
        self.assertEqual(billing.feature_value(plan, "dashboard"), "limited_30d")

    def test_returns_default_when_missing(self):
        plan = {"features": {}}
        self.assertEqual(
            billing.feature_value(plan, "dashboard", default="full"),
            "full",
        )

    def test_returns_default_when_plan_is_none(self):
        self.assertEqual(
            billing.feature_value(None, "dashboard", default="basic"),
            "basic",
        )

    def test_default_is_none_when_not_provided(self):
        self.assertIsNone(billing.feature_value({}, "anything"))


# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


class ParseTsTests(unittest.TestCase):

    def test_none_returns_none(self):
        self.assertIsNone(billing._parse_ts(None))

    def test_datetime_with_tz_passthrough(self):
        ts = datetime(2026, 5, 21, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(billing._parse_ts(ts), ts)

    def test_datetime_naive_gets_utc_tz(self):
        ts = datetime(2026, 5, 21, 14, 30)
        result = billing._parse_ts(ts)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_iso_string(self):
        result = billing._parse_ts("2026-05-21T14:30:00+00:00")
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.year, 2026)

    def test_iso_string_with_z_suffix(self):
        # Supabase Postgres serializa timestamptz com Z; Python <3.11 nao
        # aceita Z em fromisoformat. Funcao deve converter Z em +00:00.
        result = billing._parse_ts("2026-05-21T14:30:00Z")
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.year, 2026)

    def test_invalid_string_returns_none(self):
        self.assertIsNone(billing._parse_ts("not a date"))

    def test_invalid_type_returns_none(self):
        # int passa por str() — vira algo como "1234" que nao parseia.
        self.assertIsNone(billing._parse_ts(1234567890))


# ---------------------------------------------------------------------------
# days_left_in_trial
# ---------------------------------------------------------------------------


class DaysLeftInTrialTests(unittest.TestCase):

    def test_none_plan_returns_none(self):
        self.assertIsNone(billing.days_left_in_trial(None))

    def test_not_trialing_returns_none(self):
        plan = {"status": "active", "trial_ends_at": "2026-05-30T00:00:00Z"}
        self.assertIsNone(billing.days_left_in_trial(plan))

    def test_trialing_without_ends_at_returns_none(self):
        plan = {"status": "trialing", "trial_ends_at": None}
        self.assertIsNone(billing.days_left_in_trial(plan))

    def test_trialing_with_future_ends_at(self):
        # 5 dias no futuro
        future = (datetime.now(timezone.utc) + timedelta(days=5, hours=2)).isoformat()
        plan = {"status": "trialing", "trial_ends_at": future}
        self.assertEqual(billing.days_left_in_trial(plan), 5)

    def test_trialing_with_past_ends_at_returns_zero(self):
        # Trial expirou mas status ainda "trialing" (intervalo entre cron e UI)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        plan = {"status": "trialing", "trial_ends_at": past}
        self.assertEqual(billing.days_left_in_trial(plan), 0)


# ---------------------------------------------------------------------------
# is_trial_expired
# ---------------------------------------------------------------------------


class IsTrialExpiredTests(unittest.TestCase):

    def test_free_status_free_plan_is_expired(self):
        plan = {"status": "free", "plan_slug": "free"}
        self.assertTrue(billing.is_trial_expired(plan))

    def test_free_status_pro_plan_is_not_expired(self):
        # Caso estranho mas defensivo — plan_slug deve casar com status.
        plan = {"status": "free", "plan_slug": "pro"}
        self.assertFalse(billing.is_trial_expired(plan))

    def test_trialing_is_not_expired(self):
        plan = {"status": "trialing", "plan_slug": "trial"}
        self.assertFalse(billing.is_trial_expired(plan))

    def test_active_is_not_expired(self):
        plan = {"status": "active", "plan_slug": "pro"}
        self.assertFalse(billing.is_trial_expired(plan))

    def test_none_plan_is_not_expired(self):
        self.assertFalse(billing.is_trial_expired(None))


# ---------------------------------------------------------------------------
# is_admin
# ---------------------------------------------------------------------------


class IsAdminTests(unittest.TestCase):
    # billing.is_admin faz `import auth` tardio dentro da funcao. Patchear
    # `billing.auth` nao adianta — `import auth` reolhe `sys.modules['auth']`.
    # Patcheamos `auth.get_client` diretamente.

    @patch("auth.get_client")
    def test_returns_true_when_user_in_admin_users(self, mock_get_client):
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[{"user_id": "user-abc"}])
        chain.select.return_value = chain
        chain.eq.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        mock_get_client.return_value = client

        self.assertTrue(billing.is_admin("user-abc"))
        chain.eq.assert_called_once_with("user_id", "user-abc")

    @patch("auth.get_client")
    def test_returns_false_when_user_not_in_admin_users(self, mock_get_client):
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[])
        chain.select.return_value = chain
        chain.eq.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        mock_get_client.return_value = client

        self.assertFalse(billing.is_admin("user-abc"))

    @patch("auth.get_client")
    def test_returns_false_on_exception(self, mock_get_client):
        # Falha de rede / RLS / etc → admin e' negado por seguranca.
        client = MagicMock()
        client.table.side_effect = RuntimeError("denied")
        mock_get_client.return_value = client

        self.assertFalse(billing.is_admin("user-abc"))


# ---------------------------------------------------------------------------
# ensure_subscription
# ---------------------------------------------------------------------------


class EnsureSubscriptionTests(unittest.TestCase):

    def test_calls_rpc(self):
        client = MagicMock()
        billing.ensure_subscription(client)
        client.rpc.assert_called_once_with("ensure_subscription")

    def test_silently_swallows_exception(self):
        # Best-effort: se a RPC nao existir ou bombar, nao quebra o login.
        client = MagicMock()
        client.rpc.side_effect = RuntimeError("rpc missing")
        # Nao deve levantar
        billing.ensure_subscription(client)


if __name__ == "__main__":
    unittest.main(verbosity=2)
