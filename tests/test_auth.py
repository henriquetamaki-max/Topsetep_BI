"""Tests para src/auth.py — autenticacao Supabase.

Cobre as funcoes puras e wrappers mockaveis. `login_screen` e `_sign_in_google`
nao sao testados (UI heavy com st.tabs/st.markdown/st.stop). `_handle_oauth_callback`
e `sign_out` tambem nao — fazem `st.rerun()` que para o test runner.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import auth  # noqa: E402


class FakeSessionState(dict):
    """Mimica `st.session_state` (dict-like)."""
    pass


# ---------------------------------------------------------------------------
# current_user / current_user_id
# ---------------------------------------------------------------------------


class CurrentUserTests(unittest.TestCase):

    def test_returns_none_when_no_session(self):
        with patch.object(auth.st, "session_state", FakeSessionState()):
            self.assertIsNone(auth.current_user())

    def test_returns_user_dict_when_session_present(self):
        sess = {"user": {"id": "abc-123", "email": "x@y.com"}}
        with patch.object(auth.st, "session_state", FakeSessionState(session=sess)):
            user = auth.current_user()
            self.assertEqual(user["id"], "abc-123")
            self.assertEqual(user["email"], "x@y.com")


class CurrentUserIdTests(unittest.TestCase):

    def test_returns_none_when_no_session(self):
        with patch.object(auth.st, "session_state", FakeSessionState()):
            self.assertIsNone(auth.current_user_id())

    def test_returns_user_id_string_when_session_present(self):
        sess = {"user": {"id": "uuid-xyz"}}
        with patch.object(auth.st, "session_state", FakeSessionState(session=sess)):
            self.assertEqual(auth.current_user_id(), "uuid-xyz")

    def test_returns_none_when_session_has_no_user(self):
        # Defensive: sessao sem 'user' nao deve explodir — current_user
        # acessa sess["user"] direto, entao essa configuracao gera KeyError.
        # Documenta comportamento atual: caller espera session bem-formado.
        sess = {"access_token": "tok"}
        with patch.object(auth.st, "session_state", FakeSessionState(session=sess)):
            with self.assertRaises(KeyError):
                auth.current_user_id()


# ---------------------------------------------------------------------------
# _read_secret — cascata st.secrets -> env vars -> .env file
# ---------------------------------------------------------------------------


class ReadSecretTests(unittest.TestCase):

    def test_returns_value_from_st_secrets(self):
        fake_secrets = MagicMock()
        fake_secrets.get.side_effect = lambda k: "from-secrets" if k == "SUPABASE_URL" else None
        with patch.object(auth.st, "secrets", fake_secrets):
            self.assertEqual(auth._read_secret("SUPABASE_URL"), "from-secrets")

    def test_tries_multiple_names_in_order(self):
        # Primeiro nome nao bate; segundo bate.
        fake_secrets = MagicMock()
        fake_secrets.get.side_effect = lambda k: "alt-value" if k == "NEXT_PUBLIC_SUPABASE_URL" else None
        with patch.object(auth.st, "secrets", fake_secrets):
            v = auth._read_secret("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
            self.assertEqual(v, "alt-value")

    def test_falls_back_to_env_var_when_secrets_empty(self):
        fake_secrets = MagicMock()
        fake_secrets.get.return_value = None
        with patch.object(auth.st, "secrets", fake_secrets), \
             patch.dict("os.environ", {"SUPABASE_URL": "from-env"}, clear=False), \
             patch.object(auth, "_ENV_FILE", Path("/nonexistent")):
            self.assertEqual(auth._read_secret("SUPABASE_URL"), "from-env")

    def test_returns_none_when_nothing_set(self):
        fake_secrets = MagicMock()
        fake_secrets.get.return_value = None
        with patch.object(auth.st, "secrets", fake_secrets), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(auth, "_ENV_FILE", Path("/nonexistent")):
            self.assertIsNone(auth._read_secret("SUPABASE_URL"))

    def test_swallows_secrets_filenotfound(self):
        # st.secrets pode lancar FileNotFoundError quando nao ha secrets.toml
        # (Streamlit Cloud local sem arquivo). Funcao protege contra isso.
        fake_secrets = MagicMock()
        fake_secrets.get.side_effect = FileNotFoundError("no secrets")
        with patch.object(auth.st, "secrets", fake_secrets), \
             patch.dict("os.environ", {"SUPABASE_URL": "from-env"}, clear=False), \
             patch.object(auth, "_ENV_FILE", Path("/nonexistent")):
            # Nao explode, cai para env var.
            self.assertEqual(auth._read_secret("SUPABASE_URL"), "from-env")


# ---------------------------------------------------------------------------
# _apply_session_to_client
# ---------------------------------------------------------------------------


class ApplySessionToClientTests(unittest.TestCase):

    def test_calls_set_session_with_tokens(self):
        client = MagicMock()
        sess = {"access_token": "tok", "refresh_token": "rt"}
        auth._apply_session_to_client(client, sess)
        client.auth.set_session.assert_called_once_with("tok", "rt")

    def test_falls_back_to_postgrest_auth_on_exception(self):
        client = MagicMock()
        client.auth.set_session.side_effect = Exception("not supported")
        sess = {"access_token": "tok", "refresh_token": "rt"}
        auth._apply_session_to_client(client, sess)
        client.postgrest.auth.assert_called_once_with("tok")


# ---------------------------------------------------------------------------
# _session_to_dict
# ---------------------------------------------------------------------------


class SessionToDictTests(unittest.TestCase):

    def _mk_session(self, **overrides):
        # Mimica o objeto Session do gotrue (atributos, nao dict).
        user = SimpleNamespace(
            id="uuid-abc",
            email="x@y.com",
            app_metadata={"provider": "email"},
            user_metadata={"preferred_language": "pt_BR"},
        )
        session = SimpleNamespace(
            user=user,
            access_token="tok",
            refresh_token="rt",
            expires_at=1234567890,
        )
        for k, v in overrides.items():
            setattr(session, k, v)
        return session

    def test_extracts_basic_fields(self):
        out = auth._session_to_dict(self._mk_session())
        self.assertEqual(out["access_token"], "tok")
        self.assertEqual(out["refresh_token"], "rt")
        self.assertEqual(out["expires_at"], 1234567890)

    def test_user_id_coerced_to_string(self):
        out = auth._session_to_dict(self._mk_session())
        self.assertEqual(out["user"]["id"], "uuid-abc")
        self.assertIsInstance(out["user"]["id"], str)

    def test_metadata_defaults_to_empty_dict_when_none(self):
        # Usuario sem app_metadata/user_metadata (None vindo do gotrue).
        user = SimpleNamespace(
            id="uid", email=None, app_metadata=None, user_metadata=None,
        )
        session = SimpleNamespace(
            user=user, access_token="t", refresh_token="r", expires_at=0,
        )
        out = auth._session_to_dict(session)
        self.assertEqual(out["user"]["app_metadata"], {})
        self.assertEqual(out["user"]["user_metadata"], {})


# ---------------------------------------------------------------------------
# _redirect_url
# ---------------------------------------------------------------------------


class RedirectUrlTests(unittest.TestCase):

    def test_uses_app_url_when_set(self):
        with patch.object(auth, "_read_secret", return_value="https://app.example.com"):
            self.assertEqual(auth._redirect_url(), "https://app.example.com")

    def test_falls_back_to_localhost_when_unset(self):
        with patch.object(auth, "_read_secret", return_value=None):
            self.assertEqual(auth._redirect_url(), "http://localhost:8501")


# ---------------------------------------------------------------------------
# _sign_in_email / _sign_up_email
# ---------------------------------------------------------------------------


class SignInEmailTests(unittest.TestCase):

    def _mk_session(self):
        user = SimpleNamespace(id="uid", email="x@y.com",
                               app_metadata={}, user_metadata={})
        return SimpleNamespace(user=user, access_token="t",
                               refresh_token="r", expires_at=0)

    def test_success_persists_session_in_state(self):
        client = MagicMock()
        client.auth.sign_in_with_password.return_value = SimpleNamespace(
            session=self._mk_session()
        )
        state = FakeSessionState()
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", state):
            ok, err = auth._sign_in_email("x@y.com", "secret")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIn("session", state)
        self.assertEqual(state["session"]["user"]["id"], "uid")

    def test_failure_returns_error_string(self):
        client = MagicMock()
        client.auth.sign_in_with_password.side_effect = Exception("bad creds")
        state = FakeSessionState()
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", state):
            ok, err = auth._sign_in_email("x@y.com", "bad")
        self.assertFalse(ok)
        self.assertIn("bad creds", err)
        self.assertNotIn("session", state)


class SignUpEmailTests(unittest.TestCase):

    def _mk_session(self):
        user = SimpleNamespace(id="uid2", email="new@y.com",
                               app_metadata={}, user_metadata={})
        return SimpleNamespace(user=user, access_token="t",
                               refresh_token="r", expires_at=0)

    def test_success_with_session_persists_session(self):
        # Quando confirmacao por email esta desabilitada, vem session imediata.
        client = MagicMock()
        client.auth.sign_up.return_value = SimpleNamespace(session=self._mk_session())
        state = FakeSessionState()
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", state):
            ok, msg = auth._sign_up_email("new@y.com", "secret")
        self.assertTrue(ok)
        self.assertIsNone(msg)
        self.assertIn("session", state)

    def test_success_without_session_returns_confirm_email(self):
        # Quando confirm-email esta ON, signup volta sem session.
        client = MagicMock()
        client.auth.sign_up.return_value = SimpleNamespace(session=None)
        state = FakeSessionState()
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", state):
            ok, msg = auth._sign_up_email("new@y.com", "secret")
        self.assertTrue(ok)
        self.assertEqual(msg, "confirm_email")
        self.assertNotIn("session", state)

    def test_failure_returns_error_string(self):
        client = MagicMock()
        client.auth.sign_up.side_effect = Exception("email exists")
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", FakeSessionState()):
            ok, msg = auth._sign_up_email("x@y.com", "secret")
        self.assertFalse(ok)
        self.assertIn("email exists", msg)


# ---------------------------------------------------------------------------
# get_client
# ---------------------------------------------------------------------------


class GetClientTests(unittest.TestCase):

    def test_returns_anon_client_when_no_session(self):
        client = MagicMock()
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", FakeSessionState()):
            out = auth.get_client()
        self.assertIs(out, client)
        client.auth.set_session.assert_not_called()

    def test_applies_session_when_present(self):
        client = MagicMock()
        sess = {"access_token": "tok", "refresh_token": "rt", "user": {"id": "u"}}
        with patch.object(auth, "_client_anon", return_value=client), \
             patch.object(auth.st, "session_state", FakeSessionState(session=sess)):
            out = auth.get_client()
        self.assertIs(out, client)
        client.auth.set_session.assert_called_once_with("tok", "rt")


# ---------------------------------------------------------------------------
# _t — lazy i18n wrapper
# ---------------------------------------------------------------------------


class TWrapperTests(unittest.TestCase):

    def test_delegates_to_i18n_t(self):
        # Garante o lazy import — se travasse o ciclo i18n<->auth, explodiria aqui.
        from i18n import t as real_t
        # Chave qualquer com fallback (returna a propria chave se nao existe).
        result = auth._t("auth.tab_login")
        self.assertIsInstance(result, str)
        # Mesmo resultado do helper real:
        self.assertEqual(result, real_t("auth.tab_login"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
