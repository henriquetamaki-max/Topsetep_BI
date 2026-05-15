"""
Lista todos os user_id que têm trades, com email (auth.users) e range de
trade_day. Útil pra detectar trades órfãos ou em contas inesperadas.

Uso:
    python scripts/diag_all_trades.py

Apenas leitura.
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
        sys.exit("ERRO: SUPABASE_URL ou SERVICE_ROLE_KEY ausente.")
    return url, key


def all_user_ids_with_counts(client) -> dict[str, dict]:
    """Pagina trades e agrega count + min/max trade_day por user_id."""
    by_user: dict[str, dict] = {}
    offset = 0
    page_size = 1000
    while True:
        r = (
            client.table("trades")
            .select("user_id,trade_day")
            .order("user_id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = r.data or []
        for row in rows:
            uid = row["user_id"]
            day = row["trade_day"]
            entry = by_user.setdefault(uid, {"count": 0, "min": day, "max": day})
            entry["count"] += 1
            if day < entry["min"]:
                entry["min"] = day
            if day > entry["max"]:
                entry["max"] = day
        if len(rows) < page_size:
            break
        offset += page_size
    return by_user


def email_for_user(client, user_id: str) -> str | None:
    try:
        res = client.auth.admin.get_user_by_id(user_id)
        user = getattr(res, "user", None) or res
        return getattr(user, "email", None)
    except Exception as exc:
        return f"<erro: {exc}>"


def main() -> int:
    url, key = load_env()
    client = create_client(url, key)

    print("=== agregando trades por user_id (pode demorar se houver muitos)... ===")
    by_user = all_user_ids_with_counts(client)
    print(f"=== {len(by_user)} user_id distintos com trades ===\n")

    rows = sorted(by_user.items(), key=lambda kv: -kv[1]["count"])
    print(f"{'user_id':<38}  {'email':<45}  {'count':>6}  {'range'}")
    print("-" * 110)
    for uid, stats in rows:
        email = email_for_user(client, uid) or "<sem auth.users>"
        print(
            f"{uid:<38}  {email:<45}  {stats['count']:>6}  "
            f"{stats['min']}..{stats['max']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
