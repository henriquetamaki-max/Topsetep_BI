"""app_releases.py — catalogo de versoes da extensao Chrome e do app.

Tabela `public.app_releases` (M12). RLS permite SELECT a qualquer usuario
autenticado; INSERT/UPDATE/DELETE so' a admins.

API:
- get_latest(component)            -> dict | None — versao marcada is_latest
- list_releases(component, limit)  -> list[dict] — historico ordenado desc
- compare_versions(installed, latest) -> "up_to_date" | "outdated" | "ahead" | "unknown"

Versoes seguem semver simples (X.Y.Z) — comparacao numerica. Tags pre-release
(0.1.0-rc.1) sao tratadas como "unknown" para nao bloquear distribuicao.
"""

from __future__ import annotations

import re
from typing import Any

import auth

TABLE = "app_releases"

# Components vivem em CHECK constraint no banco — manter sincronizado.
COMPONENTS = ("extension", "app")

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse_semver(v: str | None) -> tuple[int, int, int] | None:
    if not v:
        return None
    m = _SEMVER_RE.match(str(v).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def compare_versions(installed: str | None, latest: str | None) -> str:
    """Compara duas versoes semver.

    - "up_to_date": installed >= latest
    - "outdated": installed < latest
    - "ahead": installed > latest (instancia de dev rodando codigo nao publicado)
    - "unknown": qualquer um eh' None ou nao parseia como semver
    """
    a = _parse_semver(installed)
    b = _parse_semver(latest)
    if a is None or b is None:
        return "unknown"
    if a < b:
        return "outdated"
    if a > b:
        return "ahead"
    return "up_to_date"


def get_latest(component: str = "extension") -> dict[str, Any] | None:
    """Linha onde `is_latest=true` para o componente. None se nao houver."""
    if component not in COMPONENTS:
        return None
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .eq("component", component)
            .eq("is_latest", True)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        # Falha silenciosa — UI nao pode quebrar se tabela ainda nao existir
        # (operador pode ainda nao ter rodado m12_app_releases.sql).
        return None


def list_releases(
    component: str = "extension", limit: int = 10,
) -> list[dict[str, Any]]:
    """Historico de releases do componente, mais recentes primeiro."""
    if component not in COMPONENTS:
        return []
    try:
        r = (
            auth.get_client().table(TABLE)
            .select("*")
            .eq("component", component)
            .order("released_at", desc=True)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception:
        return []
