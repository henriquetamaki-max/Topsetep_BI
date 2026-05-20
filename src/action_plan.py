"""
Plano de Ação — CRUD da tabela public.action_items.

Funções puras (sem Streamlit) para facilitar teste. Reusa o cliente
Supabase de coach_ai (mesmo Env/Topstep_bi.env, mesma service_role key).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from coach_ai import _current_user_id, _supabase

TABLE = "action_items"

# Campos que o data_editor expõe ao usuário (na ordem em que aparecem).
EDITABLE_COLUMNS = ["task", "priority", "status", "due_date", "done"]
# Campos completos do select — incluem id/created_at/updated_at para o diff.
ALL_COLUMNS = ["id", "created_at", "updated_at", *EDITABLE_COLUMNS]

VALID_STATUS = ("Pendente", "Em andamento", "Concluído")
VALID_PRIORITY = ("Alta", "Média", "Baixa")


def list_items() -> pd.DataFrame:
    """Lê todos os itens, ordenados: não-concluídos primeiro, por data e
    prioridade. Devolve DataFrame com `ALL_COLUMNS` (vazio se a tabela tiver
    sido criada mas estiver sem dados).
    """
    client = _supabase()
    r = client.table(TABLE).select("*").execute()
    rows = r.data or []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in ALL_COLUMNS})

    # Garante colunas previsíveis mesmo se o retorno vier com ordem diferente.
    for c in ALL_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["done"] = df["done"].fillna(False).astype(bool)
    df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
    # Ordenação: não-concluídos antes, depois prioridade (Alta>Média>Baixa) e
    # data estimada mais próxima. `due_date` NaT vai pro final.
    prio_rank = {"Alta": 0, "Média": 1, "Baixa": 2}
    df["_prio_rank"] = df["priority"].map(prio_rank).fillna(99)
    df["_due_sort"] = df["due_date"].map(lambda d: d if isinstance(d, date) else date.max)
    df = df.sort_values(
        by=["done", "_prio_rank", "_due_sort"], ascending=[True, True, True]
    ).drop(columns=["_prio_rank", "_due_sort"]).reset_index(drop=True)
    return df[ALL_COLUMNS]


def _normalize_row(row: pd.Series) -> dict:
    """Converte uma linha do DataFrame editado para payload Supabase."""
    out: dict = {}
    out["task"] = (str(row.get("task") or "")).strip()
    status = row.get("status") or "Pendente"
    out["status"] = status if status in VALID_STATUS else "Pendente"
    prio = row.get("priority") or "Média"
    out["priority"] = prio if prio in VALID_PRIORITY else "Média"
    out["done"] = bool(row.get("done") or False)
    due = row.get("due_date")
    if pd.isna(due) or due is None or due == "":
        out["due_date"] = None
    else:
        out["due_date"] = (
            due.isoformat() if isinstance(due, date) else str(pd.to_datetime(due).date())
        )
    # Sync done<->status: se done=True força "Concluído"; se done=False e
    # status="Concluído", volta para "Pendente". Não cobre todos os casos, mas
    # evita estado contraditório no banco.
    if out["done"]:
        out["status"] = "Concluído"
    elif out["status"] == "Concluído":
        out["done"] = True
    return out


def upsert_items(original: pd.DataFrame, edited: pd.DataFrame) -> dict:
    """Diff entre `original` (snapshot vindo de list_items) e `edited`
    (DataFrame após o usuário mexer no data_editor). Aplica insert/update/
    delete e devolve contadores.
    """
    try:
        client = _supabase()
    except Exception as e:
        return {"ok": False, "inserted": 0, "updated": 0, "deleted": 0, "error": str(e)}

    orig_ids = {int(i) for i in original["id"].dropna().tolist()} if not original.empty else set()
    edited_ids = {int(i) for i in edited["id"].dropna().tolist()} if not edited.empty else set()

    to_delete = orig_ids - edited_ids
    to_insert: list[dict] = []
    to_update: list[tuple[int, dict]] = []

    orig_by_id = (
        original.set_index("id").to_dict("index") if not original.empty else {}
    )

    for _, row in edited.iterrows():
        payload = _normalize_row(row)
        if not payload["task"]:
            # Linha em branco (clique acidental no "+" do data_editor) — ignora.
            continue
        rid = row.get("id")
        if pd.isna(rid) or rid is None:
            to_insert.append(payload)
            continue
        rid_int = int(rid)
        prev = orig_by_id.get(rid_int) or orig_by_id.get(float(rid_int)) or {}
        # Compara só os campos editáveis (normalizados).
        prev_norm = _normalize_row(pd.Series(prev)) if prev else {}
        if any(payload.get(k) != prev_norm.get(k) for k in EDITABLE_COLUMNS):
            payload["updated_at"] = "now()"
            to_update.append((rid_int, payload))

    inserted = updated = deleted = 0
    try:
        if to_insert:
            user_id = _current_user_id()
            if not user_id:
                return {
                    "ok": False, "inserted": 0, "updated": 0, "deleted": 0,
                    "error": "Usuário não autenticado.",
                }
            for payload in to_insert:
                payload["user_id"] = user_id
            client.table(TABLE).insert(to_insert).execute()
            inserted = len(to_insert)
        for rid, payload in to_update:
            # updated_at: Postgres aceita default explícito via raw expression,
            # mas pelo PostgREST mandamos timestamp atual em ISO.
            payload["updated_at"] = pd.Timestamp.utcnow().isoformat()
            client.table(TABLE).update(payload).eq("id", rid).execute()
            updated += 1
        if to_delete:
            client.table(TABLE).delete().in_("id", list(to_delete)).execute()
            deleted = len(to_delete)
    except Exception as e:
        return {
            "ok": False,
            "inserted": inserted, "updated": updated, "deleted": deleted,
            "error": str(e),
        }

    return {
        "ok": True,
        "inserted": inserted, "updated": updated, "deleted": deleted,
        "error": None,
    }
