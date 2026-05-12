"""
BI TopStep — ingestão de CSVs de trades para Supabase (CLI legado).

Hoje a forma recomendada de ingestão é pelo upload via UI (aba "Importar CSVs"
em `app.py`) — autenticada pelo usuário logado.

Este CLI continua existindo para uso local pelo operador. Requer:
- `Env/Topstep_bi.env` com:
    NEXT_PUBLIC_SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...   (bypassa RLS — só local)
    INGEST_USER_ID=<uuid do usuário Supabase Auth dono dos trades>

Lê todos os CSVs em "CSV input/", normaliza (formato TopStepX nativo ou
Dashboard legacy), faz upsert em batch na tabela public.trades (PK:
(user_id, id)) e move cada arquivo processado para "CSV output/" como
YYYYMMDD_N.csv.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

import ingest_core

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "CSV input"
OUTPUT_DIR = ROOT / "CSV output"
ENV_FILE = ROOT / "Env" / "Topstep_bi.env"


def load_env() -> tuple[str, str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"ERRO: arquivo de ambiente não encontrado: {ENV_FILE}")
    load_dotenv(ENV_FILE)
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    user_id = os.getenv("INGEST_USER_ID")
    if not url:
        sys.exit("ERRO: NEXT_PUBLIC_SUPABASE_URL não encontrada no .env")
    if not key:
        sys.exit(
            "ERRO: SUPABASE_SERVICE_ROLE_KEY não encontrada no .env.\n"
            "Pegue em Supabase -> Settings -> API -> service_role secret."
        )
    if not user_id:
        sys.exit(
            "ERRO: INGEST_USER_ID não encontrada no .env.\n"
            "Esse é o UUID do usuário Supabase Auth dono dos trades — "
            "pegue em Authentication -> Users e cole no .env como:\n"
            "INGEST_USER_ID=<uuid>"
        )
    return url, key, user_id


def next_output_name() -> str:
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%d")
    existing = list(OUTPUT_DIR.glob(f"{today}_*.csv"))
    used = set()
    for f in existing:
        m = re.match(rf"^{today}_(\d+)\.csv$", f.name)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"{today}_{n}.csv"


def main() -> int:
    url, key, user_id = load_env()
    client = create_client(url, key)

    OUTPUT_DIR.mkdir(exist_ok=True)
    csvs = sorted(INPUT_DIR.glob("*.csv"))
    if not csvs:
        print(f"Nenhum CSV em {INPUT_DIR}. Nada a fazer.")
        return 0

    files_ok = 0
    rows_total = 0
    errors: list[tuple[Path, str]] = []

    for csv_path in csvs:
        try:
            n, fmt, err = ingest_core.ingest_uploaded_csv(csv_path, client, user_id)
            if err and n == 0:
                # "vazio" ou "sem trades" não é erro — move pra output igual.
                if err in ("CSV vazio", "sem trades após normalização"):
                    print(f"  [skip] {csv_path.name}: {err}")
                    shutil.move(csv_path, OUTPUT_DIR / next_output_name())
                    continue
                raise RuntimeError(err)
            new_name = next_output_name()
            shutil.move(csv_path, OUTPUT_DIR / new_name)
            print(f"  [ok]  {csv_path.name} ({fmt}): {n} linhas upsertadas -> {new_name}")
            files_ok += 1
            rows_total += n
        except Exception as exc:  # noqa: BLE001
            errors.append((csv_path, str(exc)))
            print(f"  [ERR] {csv_path.name}: {exc}")

    print()
    print(f"Resumo: {files_ok}/{len(csvs)} arquivos processados, {rows_total} linhas upsertadas.")
    if errors:
        print(f"{len(errors)} arquivo(s) com erro (mantidos em CSV input/).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
