"""
i18n — internacionalização do dashboard.

Idiomas suportados: en (default), pt_BR, es. Os textos vivem em
`locales/<code>.json`. Use `t("chave")` para buscar uma string traduzida no
idioma corrente.

Persistência: a preferência fica em `user_metadata.preferred_language` do
usuário logado (Supabase Auth). Cache local em `st.session_state["lang"]`
para evitar round-trip a cada rerun. Sem usuário logado, só session_state.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

DEFAULT_LANG = "en"
LANGS: dict[str, str] = {
    "en": "English",
    "pt_BR": "Português (Brasil)",
    "es": "Español",
}

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# Mapeamento entre valores canônicos do banco (action_items.status / .priority,
# em PT por CHECK constraint) e as chaves i18n de exibição.
STATUS_DB_TO_KEY: dict[str, str] = {
    "Pendente": "action.status.pending",
    "Em andamento": "action.status.in_progress",
    "Concluído": "action.status.done",
}
PRIORITY_DB_TO_KEY: dict[str, str] = {
    "Alta": "action.priority.high",
    "Média": "action.priority.medium",
    "Baixa": "action.priority.low",
}

# Nome do idioma no próprio idioma — usado no prompt do Coach.
LANG_NATIVE_NAME: dict[str, str] = {
    "en": "English",
    "pt_BR": "português",
    "es": "español",
}


@st.cache_data(show_spinner=False)
def _load_locale(code: str) -> dict:
    path = _LOCALES_DIR / f"{code}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def init() -> None:
    """Garante `session_state["lang"]` definido. Lê de `user_metadata` na primeira
    chamada se o usuário já estiver logado.
    """
    if "lang" in st.session_state:
        return
    lang = _read_lang_from_user_metadata()
    st.session_state["lang"] = lang if lang in LANGS else DEFAULT_LANG


def _read_lang_from_user_metadata() -> str | None:
    """Lê preferred_language do user_metadata do usuário logado.

    Retorna None se não houver usuário logado ou se a chave não existir.
    Importação tardia para evitar dependência circular com auth.py.
    """
    try:
        import auth  # noqa: PLC0415
        user = auth.current_user()
    except Exception:
        return None
    if not user:
        return None
    meta = user.get("user_metadata") or {}
    code = meta.get("preferred_language")
    return code if code in LANGS else None


def current_lang() -> str:
    return st.session_state.get("lang", DEFAULT_LANG)


def set_user_language(code: str) -> None:
    """Aplica o idioma na sessão e persiste em user_metadata (best-effort).

    Falhas de persistência (sem internet, sem usuário logado) não bloqueiam
    a mudança em session_state — o app continua usando o idioma escolhido
    pelo menos durante a sessão atual.
    """
    if code not in LANGS:
        return
    st.session_state["lang"] = code
    try:
        import auth  # noqa: PLC0415
        if auth.current_user() is None:
            return
        client = auth.get_client()
        client.auth.update_user({"data": {"preferred_language": code}})
        # Atualiza o snapshot em session_state["session"] para refletir o novo
        # metadata sem precisar refazer login.
        sess = st.session_state.get("session")
        if sess and isinstance(sess.get("user"), dict):
            meta = sess["user"].setdefault("user_metadata", {})
            meta["preferred_language"] = code
    except Exception:
        # Persistência é best-effort; session_state já foi atualizado acima.
        pass


def t(key: str, **kwargs) -> str:
    """Busca `key` no idioma corrente; fallback para o default; senão devolve
    a própria chave (útil para detectar strings faltantes durante review).
    Aplica `.format(**kwargs)` se houver placeholders.
    """
    lang = current_lang()
    msg = _load_locale(lang).get(key)
    if msg is None and lang != DEFAULT_LANG:
        msg = _load_locale(DEFAULT_LANG).get(key)
    if msg is None:
        msg = key
    if kwargs:
        try:
            return msg.format(**kwargs)
        except (KeyError, IndexError):
            return msg
    return msg


def language_selector(*, key: str = "lang_selector") -> None:
    """Renderiza o seletor de idioma. Deve ser chamado no topo da sidebar,
    antes dos demais widgets, para que a troca afete o mesmo rerun.
    """
    codes = list(LANGS.keys())
    try:
        idx = codes.index(current_lang())
    except ValueError:
        idx = codes.index(DEFAULT_LANG)
    selected = st.selectbox(
        t("sidebar.language"),
        options=codes,
        index=idx,
        format_func=lambda c: LANGS[c],
        key=key,
    )
    if selected != current_lang():
        set_user_language(selected)
        st.rerun()


# ---------------------------------------------------------------------------
# Helpers de tradução de valores canônicos


def status_label(db_value: str) -> str:
    """'Pendente' → t('action.status.pending'). Fallback: devolve o valor cru."""
    key = STATUS_DB_TO_KEY.get(db_value)
    return t(key) if key else db_value


def priority_label(db_value: str) -> str:
    key = PRIORITY_DB_TO_KEY.get(db_value)
    return t(key) if key else db_value


def status_options() -> list[str]:
    """Lista de status traduzidos na ordem canônica (Pendente, Em andamento,
    Concluído). Usado em SelectboxColumn."""
    return [t(STATUS_DB_TO_KEY[v]) for v in ("Pendente", "Em andamento", "Concluído")]


def priority_options() -> list[str]:
    return [t(PRIORITY_DB_TO_KEY[v]) for v in ("Alta", "Média", "Baixa")]


def status_from_label(label: str) -> str:
    """Inverso de status_label: converte rótulo traduzido para valor canônico
    do banco. Se não casar, devolve o próprio label (e o INSERT falha com erro
    explícito do CHECK constraint, o que é desejado)."""
    for db_value, key in STATUS_DB_TO_KEY.items():
        if t(key) == label:
            return db_value
    return label


def priority_from_label(label: str) -> str:
    for db_value, key in PRIORITY_DB_TO_KEY.items():
        if t(key) == label:
            return db_value
    return label


# ---------------------------------------------------------------------------
# Constantes internas para keys de radio/select que precisam ser estáveis
# (independem do idioma) — usadas em comparações no app.py.

SHORTCUT_KEYS: list[str] = [
    "custom", "today", "last_7", "current_week", "last_30", "current_month", "all"
]
RESULT_KEYS: list[str] = ["all", "winners", "losers"]
WEEKDAY_PANDAS: list[str] = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


def shortcut_label(key: str) -> str:
    return t(f"sidebar.shortcut.{key}")


def result_label(key: str) -> str:
    return t(f"sidebar.result.{key}")


def weekday_label(pandas_name: str) -> str:
    return t(f"weekday.{pandas_name.lower()}")
