"""
Autenticação Supabase para o app Streamlit.

Fluxo:
- Email/senha (sign_in / sign_up).
- Google OAuth (PKCE): o botão chama `supabase.auth.sign_in_with_oauth` que
  retorna uma URL pra redirecionar; ao voltar, o callback traz `?code=...` na
  query string e a gente troca pelo session com `exchange_code_for_session`.

Sessão fica em `st.session_state["session"]` como dict
(`access_token`, `refresh_token`, `user`). Todas as queries do app passam por
`get_client()` que injeta o JWT no PostgREST → as RLS policies fazem o filtro
por `user_id` no banco.

Config:
- Credenciais leem-se de `st.secrets` quando hospedado (Streamlit Cloud) ou de
  `Env/Topstep_bi.env` quando local.
- Variáveis esperadas:
    SUPABASE_URL              (ou NEXT_PUBLIC_SUPABASE_URL)
    SUPABASE_ANON_KEY         (ou NEXT_PUBLIC_SUPABASE_ANON_KEY)
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from supabase import Client, create_client

_ENV_FILE = Path(__file__).resolve().parent / "Env" / "Topstep_bi.env"


def _t(key: str, **kwargs) -> str:
    """Resolve uma string i18n. Import tardio para evitar ciclo com i18n.py
    (que importa auth para ler user_metadata)."""
    from i18n import t as _real_t  # noqa: PLC0415
    return _real_t(key, **kwargs)


def _read_secret(*names: str) -> str | None:
    """Lê um segredo de st.secrets, depois env var. Retorna None se nenhum existir."""
    for name in names:
        try:
            v = st.secrets.get(name)  # type: ignore[attr-defined]
        except (FileNotFoundError, KeyError, AttributeError):
            v = None
        if v:
            return str(v)
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
    for name in names:
        v = os.getenv(name)
        if v:
            return v
    return None


@st.cache_resource
def _client_anon() -> Client:
    url = _read_secret("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    key = _read_secret("SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        st.error(_t("auth.err_credentials_missing"))
        st.stop()
    return create_client(url, key)


def _apply_session_to_client(client: Client, session: dict) -> None:
    """Aplica access_token+refresh_token no cliente para autenticar requests."""
    try:
        client.auth.set_session(session["access_token"], session["refresh_token"])
    except Exception:
        # Fallback: alguns SDKs só expõem postgrest.auth().
        client.postgrest.auth(session["access_token"])


def get_client() -> Client:
    """Cliente Supabase com a sessão do usuário logado aplicada (RLS ativo)."""
    c = _client_anon()
    sess = st.session_state.get("session")
    if sess:
        _apply_session_to_client(c, sess)
    return c


def current_user() -> dict | None:
    sess = st.session_state.get("session")
    return sess["user"] if sess else None


def _session_to_dict(session) -> dict:
    """Converte o objeto Session do gotrue em dict serializável."""
    user = session.user
    user_dict = {
        "id": str(user.id),
        "email": getattr(user, "email", None),
        "app_metadata": getattr(user, "app_metadata", {}) or {},
        "user_metadata": getattr(user, "user_metadata", {}) or {},
    }
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": getattr(session, "expires_at", None),
        "user": user_dict,
    }


def _handle_oauth_callback() -> bool:
    """Se há `?code=...` na URL, troca pelo session. Retorna True se logou."""
    code = st.query_params.get("code")
    if not code:
        return False
    client = _client_anon()
    try:
        result = client.auth.exchange_code_for_session({"auth_code": code})
        session = getattr(result, "session", None) or result
        st.session_state["session"] = _session_to_dict(session)
        # Limpa o code da URL pra não tentar trocar de novo no próximo rerun.
        st.query_params.clear()
        return True
    except Exception as e:
        st.error(_t("auth.err_google_callback", err=e))
        st.query_params.clear()
        return False


def _redirect_url() -> str:
    """URL pra qual o Supabase manda o usuário após o OAuth.

    Se definida em secrets/env (`APP_URL`), usa ela; caso contrário, localhost.
    A URL precisa estar listada em Authentication → URL Configuration no Supabase.
    """
    return _read_secret("APP_URL") or "http://localhost:8501"


def _sign_in_email(email: str, password: str) -> tuple[bool, str | None]:
    try:
        r = _client_anon().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
        st.session_state["session"] = _session_to_dict(r.session)
        return True, None
    except Exception as e:
        return False, str(e)


def _sign_up_email(email: str, password: str) -> tuple[bool, str | None]:
    try:
        r = _client_anon().auth.sign_up({"email": email, "password": password})
        # Se confirmação por email estiver desabilitada, vem com session.
        if getattr(r, "session", None):
            st.session_state["session"] = _session_to_dict(r.session)
            return True, None
        return True, "confirm_email"
    except Exception as e:
        return False, str(e)


def _sign_in_google() -> None:
    try:
        r = _client_anon().auth.sign_in_with_oauth(
            {
                "provider": "google",
                "options": {"redirect_to": _redirect_url()},
            }
        )
        url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
        if not url:
            st.error(_t("auth.err_google_init"))
            return
        # Redireciona a aba do usuário para o consent screen do Google.
        st.markdown(
            f'<meta http-equiv="refresh" content="0; url={url}">',
            unsafe_allow_html=True,
        )
        st.link_button(_t("auth.btn_google_continue"), url)
        st.stop()
    except Exception as e:
        st.error(_t("auth.err_google_generic", err=e))


def sign_out() -> None:
    try:
        _client_anon().auth.sign_out()
    except Exception:
        pass
    st.session_state.clear()
    st.cache_data.clear()
    st.rerun()


def login_screen() -> None:
    """Bloqueia o app até o usuário logar. Renderiza tabs de login/cadastro."""
    if "session" in st.session_state:
        return

    # Callback OAuth (Google) — chega com ?code=... na URL.
    if _handle_oauth_callback():
        st.rerun()

    st.title(_t("app.title"))
    st.caption(_t("auth.login_caption"))

    tab_login, tab_signup = st.tabs([_t("auth.tab_login"), _t("auth.tab_signup")])

    with tab_login:
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input(_t("auth.email"), key="login_email")
            password = st.text_input(_t("auth.password"), type="password", key="login_password")
            submitted = st.form_submit_button(
                _t("auth.submit_login"), type="primary", use_container_width=True,
            )
        if submitted:
            ok, err = _sign_in_email(email.strip(), password)
            if ok:
                st.rerun()
            else:
                st.error(_t("auth.err_login", err=err))

        st.divider()
        st.caption(_t("auth.or_google"))
        if st.button(_t("auth.btn_google"), use_container_width=True, key="btn_google"):
            _sign_in_google()

    with tab_signup:
        with st.form("signup_form", clear_on_submit=False):
            new_email = st.text_input(_t("auth.email"), key="signup_email")
            new_password = st.text_input(
                _t("auth.password_min"), type="password", key="signup_password",
            )
            signup_submitted = st.form_submit_button(
                _t("auth.submit_signup"), type="primary", use_container_width=True,
            )
        if signup_submitted:
            if len(new_password) < 6:
                st.error(_t("auth.err_password_min"))
            else:
                ok, msg = _sign_up_email(new_email.strip(), new_password)
                if ok and msg == "confirm_email":
                    st.success(_t("auth.signup_confirm_email"))
                elif ok:
                    st.success(_t("auth.signup_ok"))
                    st.rerun()
                else:
                    st.error(_t("auth.err_signup", err=msg))

    st.stop()
