"""
Diagnóstico read-only de contas Supabase Auth e trades associados.

Uso:
    python scripts/diag_user.py [substring_email]

Lê Env/Topstep_bi.env (SUPABASE_URL + SERVICE_ROLE_KEY), lista todos os
auth.users cujo email contenha a substring (default: "henrique.tamaki") e
para cada user_id imprime contagem de trades, range de trade_day e distribuição
nos extremos. Não faz INSERT/UPDATE/DELETE.

Requer service_role pra ler auth.users via admin API e pra bypassar RLS na
contagem de trades.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "Env" / "Topstep_bi.env"


def load_env() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"ERRO: {ENV_FILE} não encontrado.")
    load_dotenv(ENV_FILE)
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("ERRO: NEXT_PUBLIC_SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY ausente.")
    return url, key


def list_users(client, needle: str) -> list[dict]:
    """Pagina auth.admin.list_users e filtra por substring no email."""
    matches: list[dict] = []
    page = 1
    while True:
        res = client.auth.admin.list_users(page=page, per_page=100)
        users = res if isinstance(res, list) else getattr(res, "users", None) or []
        if not users:
            break
        for u in users:
            email = (getattr(u, "email", None) or "").lower()
            if needle in email:
                matches.append(
                    {
                        "id": str(getattr(u, "id", "")),
                        "email": getattr(u, "email", None),
                        "created_at": str(getattr(u, "created_at", "")),
                        "last_sign_in_at": str(getattr(u, "last_sign_in_at", "")),
                        "providers": (
                            (getattr(u, "app_metadata", {}) or {}).get("providers")
                            or [(getattr(u, "app_metadata", {}) or {}).get("provider")]
                        ),
                    }
                )
        if len(users) < 100:
            break
        page += 1
    return matches


def trades_for_user(client, user_id: str) -> dict:
    """Retorna count, min/max trade_day e distribuição. Usa paginação."""
    all_days: list[str] = []
    offset = 0
    page_size = 1000
    while True:
        r = (
            client.table("trades")
            .select("trade_day")
            .eq("user_id", user_id)
            .order("trade_day", desc=False)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = r.data or []
        all_days.extend(row["trade_day"] for row in rows)
        if len(rows) < page_size:
            break
        offset += page_size
    if not all_days:
        return {"count": 0, "min": None, "max": None, "by_day": Counter()}
    return {
        "count": len(all_days),
        "min": all_days[0],
        "max": all_days[-1],
        "by_day": Counter(all_days),
    }


def main() -> int:
    needle = (sys.argv[1] if len(sys.argv) > 1 else "henrique.tamaki").lower()
    url, key = load_env()
    client = create_client(url, key)

    print(f"=== auth.users com '{needle}' no email ===")
    users = list_users(client, needle)
    if not users:
        print("  (nenhum usuário encontrado)")
        return 0
    for u in users:
        provs = ",".join(p for p in (u["providers"] or []) if p) or "?"
        print(
            f"  {u['id']}  {u['email']:<40}  created={u['created_at'][:19]}  "
            f"last={u['last_sign_in_at'][:19]}  providers=[{provs}]"
        )

    print()
    print("=== trades por user_id ===")
    for u in users:
        stats = trades_for_user(client, u["id"])
        print(
            f"  {u['id']}  email={u['email']:<40}  count={stats['count']}  "
            f"range={stats['min']}..{stats['max']}"
        )

    print()
    print("=== distribuição de trade_day por usuário (top 20 mais antigos + top 20 recentes) ===")
    for u in users:
        stats = trades_for_user(client, u["id"])
        if stats["count"] == 0:
            continue
        print(f"\n  -- {u['email']} ({u['id']}) — {stats['count']} trades --")
        days_sorted = sorted(stats["by_day"].items())
        head = days_sorted[:20]
        tail = days_sorted[-20:]
        print("    mais antigos:")
        for day, n in head:
            print(f"      {day}  count={n}")
        if len(days_sorted) > 40:
            print("    ...")
        if len(days_sorted) > 20:
            print("    mais recentes:")
            for day, n in tail:
                print(f"      {day}  count={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
