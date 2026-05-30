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

_ENV_FILE = Path(__file__).resolve().parent.parent / "Env" / "Topstep_bi.env"


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


def _build_client() -> Client:
    """Constrói um cliente Supabase novo a partir dos secrets."""
    url = _read_secret("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    key = _read_secret("SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        st.error(_t("auth.err_credentials_missing"))
        st.stop()
    return create_client(url, key)


@st.cache_resource
def _client_anon() -> Client:
    """Cliente anônimo COMPARTILHADO entre todas as sessões do processo.

    Uso restrito a operações PRÉ-AUTH (login/signup/oauth/signout): não há
    identidade de usuário aplicada via set_session, então o compartilhamento
    não vaza dados entre tenants. Para qualquer query autenticada use
    `get_client()`, que devolve um cliente isolado por sessão."""
    return _build_client()


def _apply_session_to_client(client: Client, session: dict) -> dict | None:
    """Aplica access_token+refresh_token no cliente para autenticar requests.

    `set_session` refresca o access_token internamente quando ele expirou,
    rotacionando o refresh_token (rotação é o default do Supabase). Devolvemos
    o dict da sessão atualizada para o chamador persistir em st.session_state.
    Sem isso, o refresh_token rotacionado é descartado entre reruns do Streamlit
    e o refresh seguinte falha — o cliente perde a auth e as escritas viram 401
    (upsert do import rejeitado), enquanto as leituras seguem servidas do
    `@st.cache_data`. Retorna None quando nada mudou."""
    try:
        resp = client.auth.set_session(
            session["access_token"], session["refresh_token"]
        )
    except Exception:
        # Fallback: alguns SDKs só expõem postgrest.auth().
        try:
            client.postgrest.auth(session["access_token"])
        except Exception:
            pass
        return None
    new = getattr(resp, "session", None)
    if new and getattr(new, "access_token", None) and (
        new.access_token != session.get("access_token")
        or new.refresh_token != session.get("refresh_token")
    ):
        return _session_to_dict(new)
    return None


def get_client() -> Client:
    """Cliente Supabase autenticado, ISOLADO por sessão Streamlit (RLS ativo).

    NÃO usa o singleton `@st.cache_resource`: ele é compartilhado entre todas as
    browser sessions do mesmo processo, e `set_session()` muta o header de auth
    desse cliente único. Sob reruns concorrentes de 2+ sessões, a leitura de um
    trader poderia sair com o JWT de outro entre o set_session e o `.execute()`
    → RLS devolveria as linhas do tenant errado (vazamento cross-tenant de
    leitura; a escrita falha com 403 pois o user_id do payload diverge). Guardamos
    um cliente por sessão em `st.session_state` (que é per-session), eliminando a
    mutação cruzada. Persiste a sessão refrescada (refresh_token rotacionado) de
    volta — ver `_apply_session_to_client`.

    Sem sessão (pré-login), cai no cliente anônimo compartilhado."""
    sess = st.session_state.get("session")
    if not sess:
        return _client_anon()
    c = st.session_state.get("_authed_client")
    if c is None:
        c = _build_client()
        st.session_state["_authed_client"] = c
    refreshed = _apply_session_to_client(c, sess)
    if refreshed:
        st.session_state["session"] = refreshed
    return c


def current_user() -> dict | None:
    sess = st.session_state.get("session")
    return sess["user"] if sess else None


def current_user_id() -> str | None:
    """UUID do usuario logado, ou None se nao ha sessao. Atalho para
    `current_user()["id"]` — usado por modulos CRUD que precisam injetar
    `user_id` em inserts (RLS exige). RLS confirma server-side, este helper
    so' otimiza o caminho cliente."""
    user = current_user()
    return user["id"] if user else None


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
                _t("auth.submit_login"), type="primary", width="stretch",
            )
        if submitted:
            ok, err = _sign_in_email(email.strip(), password)
            if ok:
                st.rerun()
            else:
                st.error(_t("auth.err_login", err=err))

        st.divider()
        st.caption(_t("auth.or_google"))
        if st.button(_t("auth.btn_google"), width="stretch", key="btn_google"):
            _sign_in_google()

    with tab_signup:
        with st.form("signup_form", clear_on_submit=False):
            new_email = st.text_input(_t("auth.email"), key="signup_email")
            new_password = st.text_input(
                _t("auth.password_min"), type="password", key="signup_password",
            )
            signup_submitted = st.form_submit_button(
                _t("auth.submit_signup"), type="primary", width="stretch",
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
