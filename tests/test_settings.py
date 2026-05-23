"""Tests para src/settings.py — aba Configuracoes.

settings.py e' quase 100% Streamlit-bound; cobrimos `_persist_user_tz`
(unica funcao puramente testavel via mock de auth.get_client) e a constante
COMMON_TIMEZONES (regression: ordem importa pro UI selectbox).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import streamlit as st  # noqa: E402

import settings  # noqa: E402
import timezones  # noqa: E402


def _reset_session() -> None:
    """Limpa keys que settings._persist_user_tz mexe."""
    st.session_state.pop("user_tz", None)
    st.session_state.pop("session", None)


# ---------------------------------------------------------------------------
# _persist_user_tz — sucesso, propagacao em session, exception
# ---------------------------------------------------------------------------


class PersistUserTzTests(unittest.TestCase):

    def setUp(self):
        _reset_session()

    def tearDown(self):
        _reset_session()

    @patch("settings.auth.get_client")
    def test_calls_update_user_with_preferred_tz_payload(self, mock_get_client):
        client = MagicMock()
        mock_get_client.return_value = client

        ok, err = settings._persist_user_tz("Europe/London")

        self.assertTrue(ok)
        self.assertIsNone(err)
        # Payload exato esperado pelo Supabase Auth: {data: {preferred_tz: tz}}
        client.auth.update_user.assert_called_once_with(
            {"data": {"preferred_tz": "Europe/London"}}
        )

    @patch("settings.auth.get_client")
    def test_updates_session_state_user_tz(self, mock_get_client):
        mock_get_client.return_value = MagicMock()

        settings._persist_user_tz("America/Los_Angeles")

        self.assertEqual(st.session_state.get("user_tz"), "America/Los_Angeles")

    @patch("settings.auth.get_client")
    def test_propagates_into_session_user_metadata(self, mock_get_client):
        # Quando ja existe session em memoria, atualiza tambem `user.user_metadata`
        # — evita refazer login pra refletir a mudanca em outras abas.
        mock_get_client.return_value = MagicMock()
        st.session_state["session"] = {
            "user": {"id": "uid-1", "user_metadata": {"preferred_language": "pt_BR"}},
        }

        settings._persist_user_tz("America/Chicago")

        meta = st.session_state["session"]["user"]["user_metadata"]
        self.assertEqual(meta["preferred_tz"], "America/Chicago")
        # Nao apaga chaves pre-existentes do metadata
        self.assertEqual(meta["preferred_language"], "pt_BR")

    @patch("settings.auth.get_client")
    def test_creates_user_metadata_when_missing(self, mock_get_client):
        # Usuario novo sem user_metadata ainda — setdefault cria dict vazio.
        mock_get_client.return_value = MagicMock()
        st.session_state["session"] = {"user": {"id": "uid-2"}}

        settings._persist_user_tz("UTC")

        meta = st.session_state["session"]["user"]["user_metadata"]
        self.assertEqual(meta, {"preferred_tz": "UTC"})

    @patch("settings.auth.get_client")
    def test_skips_session_meta_when_no_user_dict(self, mock_get_client):
        # session existe mas user veio errado — nao deve quebrar.
        mock_get_client.return_value = MagicMock()
        st.session_state["session"] = {"user": "not-a-dict"}

        ok, err = settings._persist_user_tz("UTC")

        self.assertTrue(ok)
        self.assertIsNone(err)
        # session intacta (codigo defensivo: so' mexe se user e' dict)
        self.assertEqual(st.session_state["session"]["user"], "not-a-dict")

    @patch("settings.auth.get_client")
    def test_returns_error_on_exception(self, mock_get_client):
        # Rede caida, RLS bloqueando etc. — nao propaga; UI mostra erro amigavel.
        client = MagicMock()
        client.auth.update_user.side_effect = RuntimeError("network down")
        mock_get_client.return_value = client

        ok, err = settings._persist_user_tz("UTC")

        self.assertFalse(ok)
        self.assertIn("network down", err)
        # session_state nao deve ser populado em caso de falha
        self.assertNotIn("user_tz", st.session_state)


# ---------------------------------------------------------------------------
# COMMON_TIMEZONES — regression de UI (ordem visivel no selectbox)
# ---------------------------------------------------------------------------


class CommonTimezonesTests(unittest.TestCase):

    def test_is_list_of_strings(self):
        self.assertIsInstance(settings.COMMON_TIMEZONES, list)
        for tz in settings.COMMON_TIMEZONES:
            self.assertIsInstance(tz, str)
            self.assertGreater(len(tz), 0)

    def test_contains_primary_tz(self):
        # Sem isso a UI mostra o ET no topo via dedup mas a opcao "default"
        # ficaria ausente — degrada a experiencia.
        self.assertIn(timezones.PRIMARY_TZ, settings.COMMON_TIMEZONES)

    def test_contains_user_fallback_tz(self):
        # America/Sao_Paulo e' o FALLBACK_USER_TZ — precisa estar disponivel
        # como opcao explicita no selectbox.
        self.assertIn(timezones.FALLBACK_USER_TZ, settings.COMMON_TIMEZONES)

    def test_contains_utc(self):
        # UTC e' opcao escapatoria pra usuarios que querem fuso neutro.
        self.assertIn("UTC", settings.COMMON_TIMEZONES)

    def test_no_duplicates(self):
        # dedup via dict.fromkeys em _render_section_timezone depende disso.
        self.assertEqual(len(settings.COMMON_TIMEZONES), len(set(settings.COMMON_TIMEZONES)))

    def test_sao_paulo_is_first(self):
        # Default visivel pro publico-alvo (traders brasileiros) — ordem importa.
        self.assertEqual(settings.COMMON_TIMEZONES[0], "America/Sao_Paulo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
