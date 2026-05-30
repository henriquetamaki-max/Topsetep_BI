"""E2E mínimo do caminho golden de ingestão (sem Streamlit, sem Supabase real).

Exercita a cadeia completa do produto que transforma o CSV exportado do
TopStepX em registros gravados no banco:

    CSV bruto → detect_format → normalize_topstepx → records_for_supabase
              → upsert_batches (cliente fake)

Princípio #4 dos quality gates: pelo menos 1 smoke do caminho golden, não só
unidades isoladas. Os testes de `test_ingest_core.py` cobrem cada função em
separado; este garante que elas se encaixam ponta-a-ponta com um payload real.
"""
from __future__ import annotations

import io
import unittest

import pandas as pd

import ingest_core

# CSV mínimo no formato TopStepX (2 trades: 1 long vencedor, 1 short perdedor).
# Fração .NET de 7 casas no TradeDuration força o truncamento p/ microssegundos.
_TOPSTEPX_CSV = (
    "Id,ContractName,EnteredAt,ExitedAt,EntryPrice,ExitPrice,Fees,PnL,Size,Type,TradeDay,TradeDuration,Commissions\n"
    "1001,MNQM5,05/29/2026 09:40:00 -0500,05/29/2026 09:45:30 -0500,18500.25,18510.75,1.24,21.00,2,Long,05/29/2026 00:00:00 -0500,00:05:30.1234567,2.48\n"
    "1002,MNQM5,05/29/2026 10:10:00 -0500,05/29/2026 10:12:00 -0500,18520.00,18515.00,1.24,-10.00,2,Short,05/29/2026 00:00:00 -0500,00:02:00.0000000,2.48\n"
)

_USER_ID = "c4765210-0000-0000-0000-000000000000"


class _FakeTable:
    def __init__(self, sink: list):
        self._sink = sink

    def upsert(self, batch, on_conflict=None):
        self._sink.append((list(batch), on_conflict))
        return self

    def execute(self):
        return self


class _FakeClient:
    def __init__(self):
        self.batches: list = []

    def table(self, name):
        assert name == "trades"
        return _FakeTable(self.batches)


class GoldenPathIngestTests(unittest.TestCase):

    def _raw(self) -> pd.DataFrame:
        return pd.read_csv(io.StringIO(_TOPSTEPX_CSV))

    def test_detect_format_topstepx(self):
        self.assertEqual(ingest_core.detect_format(self._raw()), "topstepx")

    def test_full_chain_produces_valid_records(self):
        raw = self._raw()
        df, fmt = ingest_core.parse_csv_to_df(raw)
        self.assertEqual(fmt, "topstepx")
        self.assertEqual(len(df), 2)

        records = ingest_core.records_for_supabase(df, _USER_ID)
        self.assertEqual(len(records), 2)

        for rec in records:
            # user_id injetado em todos (RLS exige) e correto.
            self.assertEqual(rec["user_id"], _USER_ID)
            # Campos não-nulos do schema.
            for key in ("id", "contract_name", "entered_at", "exited_at", "size", "type", "pnl"):
                self.assertIn(key, rec)
                self.assertIsNotNone(rec[key])
            # Timestamps serializados como ISO com offset (string, não Timestamp).
            self.assertIsInstance(rec["entered_at"], str)
            self.assertIn("T", rec["entered_at"])
            # trade_duration truncado p/ microssegundos (Postgres aceita máx 6 casas).
            self.assertNotRegex(str(rec["trade_duration"]), r"\.\d{7,}")

        # IDs preservados, sentido do PnL preservado.
        by_id = {r["id"]: r for r in records}
        self.assertEqual(set(by_id), {1001, 1002})
        self.assertGreater(by_id[1001]["pnl"], 0)
        self.assertLess(by_id[1002]["pnl"], 0)

    def test_upsert_batches_uses_composite_conflict_key(self):
        raw = self._raw()
        df, _ = ingest_core.parse_csv_to_df(raw)
        records = ingest_core.records_for_supabase(df, _USER_ID)

        client = _FakeClient()
        total = ingest_core.upsert_batches(client, records)

        self.assertEqual(total, 2)
        self.assertEqual(len(client.batches), 1)  # 2 registros < BATCH_SIZE → 1 lote
        sent, on_conflict = client.batches[0]
        self.assertEqual(len(sent), 2)
        # Idempotência por dono depende da PK composta (user_id, id).
        self.assertEqual(on_conflict, "user_id,id")


if __name__ == "__main__":
    unittest.main(verbosity=2)
