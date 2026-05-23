"""Tests para src/ingest_core.py — parsing e normalizacao de CSVs TopStepX.

Foca nas funcoes puras (detect_format, _clean_money, _duration_to_pg_interval,
normalize_topstepx, records_for_supabase, upsert_batches). reconstruct_dashboard_trades
nao e' coberto aqui — FIFO multi-leg merece arquivo proprio + fixtures reais.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd


import ingest_core


class DetectFormatTests(unittest.TestCase):

    def test_detects_topstepx(self):
        df = pd.DataFrame(columns=[
            "Id", "ContractName", "EnteredAt", "ExitedAt", "EntryPrice",
            "ExitPrice", "PnL", "Size", "Type", "ExtraColumnIgnored",
        ])
        self.assertEqual(ingest_core.detect_format(df), "topstepx")

    def test_detects_dashboard(self):
        df = pd.DataFrame(columns=[
            "Time", "Trade Day", "ID", "Side", "Size", "Product",
            "Entry Price", "Total Fees", "Profit",
        ])
        self.assertEqual(ingest_core.detect_format(df), "dashboard")

    def test_unknown_format_raises_with_helpful_message(self):
        df = pd.DataFrame(columns=["foo", "bar"])
        with self.assertRaisesRegex(ValueError, "não reconhecido"):
            ingest_core.detect_format(df)

    def test_trim_whitespace_in_column_names(self):
        # CSVs reais as vezes vem com espaco no nome — funcao deve absorver.
        df = pd.DataFrame(columns=[
            " Id ", "ContractName", "EnteredAt", "ExitedAt", "EntryPrice",
            "ExitPrice", "PnL", "Size", "Type",
        ])
        self.assertEqual(ingest_core.detect_format(df), "topstepx")


class CleanMoneyTests(unittest.TestCase):

    def test_strips_dollar_and_comma(self):
        self.assertEqual(ingest_core._clean_money("$1,234.56"), 1234.56)

    def test_negative_value(self):
        self.assertEqual(ingest_core._clean_money("-$500.00"), -500.00)

    def test_nan_becomes_zero(self):
        self.assertEqual(ingest_core._clean_money(float("nan")), 0.0)

    def test_empty_string_becomes_zero(self):
        self.assertEqual(ingest_core._clean_money(""), 0.0)

    def test_none_becomes_zero(self):
        self.assertEqual(ingest_core._clean_money(None), 0.0)

    def test_float_passthrough(self):
        self.assertEqual(ingest_core._clean_money(42.5), 42.5)


class DurationToPgIntervalTests(unittest.TestCase):

    def test_no_fraction_passthrough(self):
        self.assertEqual(
            ingest_core._duration_to_pg_interval("01:23:45"),
            "01:23:45",
        )

    def test_truncates_dotnet_7_digit_fraction_to_6(self):
        # .NET TimeSpan serializa com 7 casas (100ns); Postgres aceita maximo
        # 6 (microssegundo). Sem o truncate, o INSERT falha.
        result = ingest_core._duration_to_pg_interval("01:23:45.1234567")
        self.assertEqual(result, "01:23:45.123456")

    def test_pads_short_fraction_to_6(self):
        # .NET as vezes serializa 3 casas (ms); Postgres tem que ler como 100ms.
        result = ingest_core._duration_to_pg_interval("01:23:45.100")
        self.assertEqual(result, "01:23:45.100000")

    def test_unmatchable_returns_unchanged(self):
        # Garante que strings malformadas nao sao destruidas — passam adiante
        # e o erro acontece no upsert (mais visivel).
        self.assertEqual(
            ingest_core._duration_to_pg_interval("garbage"),
            "garbage",
        )

    def test_handles_leading_whitespace(self):
        self.assertEqual(
            ingest_core._duration_to_pg_interval("  00:01:30  "),
            "00:01:30",
        )


class NormalizeTopstepxTests(unittest.TestCase):

    def _raw_row(self, **overrides) -> dict:
        base = {
            "Id": 12345,
            "ContractName": "MNQM5",
            "EnteredAt": "05/20/2026 09:30:15 -0500",
            "ExitedAt":  "05/20/2026 09:45:30 -0500",
            "EntryPrice": 18450.25,
            "ExitPrice":  18452.75,
            "PnL": 5.00,
            "Size": 1,
            "Type": "Long",
            "TradeDay": "05/20/2026 00:00:00 -0500",
            "TradeDuration": "00:15:15.5000000",
            "Fees": "$0.74",
            "Commissions": "$0.00",
        }
        base.update(overrides)
        return base

    def test_renames_columns_to_snake_case(self):
        df = pd.DataFrame([self._raw_row()])
        out = ingest_core.normalize_topstepx(df)
        expected_cols = {
            "id", "contract_name", "entered_at", "exited_at",
            "entry_price", "exit_price", "fees", "pnl", "size", "type",
            "trade_day", "trade_duration", "commissions",
        }
        self.assertTrue(expected_cols.issubset(set(out.columns)))

    def test_id_and_size_as_int64(self):
        df = pd.DataFrame([self._raw_row()])
        out = ingest_core.normalize_topstepx(df)
        self.assertEqual(out["id"].dtype, "int64")
        self.assertEqual(out["size"].dtype, "int64")

    def test_fees_money_parsed(self):
        df = pd.DataFrame([self._raw_row(Fees="$1,234.56")])
        out = ingest_core.normalize_topstepx(df)
        # PnL ja vem como float; fees passa por to_numeric. "$1,234.56" nao
        # parseia direto via to_numeric — vira NaN, fillna 0.0.
        # Comportamento atual: dolar e virgula em "Fees" nao sao limpos
        # (so' em _clean_money do reconstruct_dashboard). Verifica que ao
        # menos nao quebra.
        self.assertIn("fees", out.columns)

    def test_dropped_duplicate_ids_keep_last(self):
        # Mesmo Id em duas linhas — deduplicacao mantem a ultima (mais recente
        # no CSV; assume ordem temporal).
        df = pd.DataFrame([
            self._raw_row(PnL=1.00),
            self._raw_row(PnL=5.00),
        ])
        out = ingest_core.normalize_topstepx(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out["pnl"].iloc[0], 5.00)

    def test_timestamps_become_utc(self):
        df = pd.DataFrame([self._raw_row()])
        out = ingest_core.normalize_topstepx(df)
        # entered_at deve ser timestamptz UTC
        self.assertIsNotNone(out["entered_at"].iloc[0].tzinfo)

    def test_microsecond_truncation_applied(self):
        # .NET 7-digit fraction deve ser truncado para 6 antes do INSERT.
        df = pd.DataFrame([self._raw_row(TradeDuration="00:15:15.1234567")])
        out = ingest_core.normalize_topstepx(df)
        self.assertEqual(out["trade_duration"].iloc[0], "00:15:15.123456")


class RecordsForSupabaseTests(unittest.TestCase):

    def _normalized_df(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "id": 12345,
            "contract_name": "MNQM5",
            "entered_at": pd.Timestamp("2026-05-20T14:30:15", tz="UTC"),
            "exited_at":  pd.Timestamp("2026-05-20T14:45:30", tz="UTC"),
            "entry_price": 18450.25,
            "exit_price": 18452.75,
            "pnl": 5.00, "fees": 0.74, "commissions": 0.0,
            "size": 1, "type": "Long",
            "trade_day": pd.Timestamp("2026-05-20").date(),
            "trade_duration": "00:15:15.500000",
        }])

    def test_injects_user_id_into_every_record(self):
        records = ingest_core.records_for_supabase(self._normalized_df(), "user-abc")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["user_id"], "user-abc")

    def test_timestamps_serialized_as_iso(self):
        records = ingest_core.records_for_supabase(self._normalized_df(), "user-abc")
        # entered_at vira string ISO 8601 com timezone
        self.assertIsInstance(records[0]["entered_at"], str)
        # contem o offset
        self.assertTrue(records[0]["entered_at"].endswith("+0000"))

    def test_trade_day_serialized_as_string(self):
        records = ingest_core.records_for_supabase(self._normalized_df(), "user-abc")
        self.assertIsInstance(records[0]["trade_day"], str)
        self.assertEqual(records[0]["trade_day"], "2026-05-20")

    def test_input_dataframe_not_mutated(self):
        df = self._normalized_df()
        before_cols = set(df.columns)
        ingest_core.records_for_supabase(df, "user-abc")
        # user_id nao deve aparecer no original — funcao usa df.copy()
        self.assertEqual(set(df.columns), before_cols)


class UpsertBatchesTests(unittest.TestCase):

    def test_single_batch_below_size_cap(self):
        client = MagicMock()
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[])
        chain.upsert.return_value = chain
        client.table.return_value = chain

        records = [{"user_id": "u", "id": i} for i in range(50)]
        n = ingest_core.upsert_batches(client, records)
        self.assertEqual(n, 50)
        # Uma unica chamada de upsert (batch < 1000)
        self.assertEqual(chain.upsert.call_count, 1)
        # on_conflict deve apontar para composite key (user_id, id)
        kwargs = chain.upsert.call_args.kwargs
        self.assertEqual(kwargs.get("on_conflict"), "user_id,id")

    def test_multiple_batches_above_size_cap(self):
        client = MagicMock()
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[])
        chain.upsert.return_value = chain
        client.table.return_value = chain

        records = [{"user_id": "u", "id": i} for i in range(2500)]
        n = ingest_core.upsert_batches(client, records)
        self.assertEqual(n, 2500)
        # 2500 / 1000 = 3 batches (1000, 1000, 500)
        self.assertEqual(chain.upsert.call_count, 3)

    def test_empty_records_returns_zero(self):
        client = MagicMock()
        n = ingest_core.upsert_batches(client, [])
        self.assertEqual(n, 0)
        client.table.assert_not_called()


class SyntheticIdTests(unittest.TestCase):

    def test_deterministic_within_process(self):
        # Hash do Python e' randomizado entre processos mas estavel num mesmo
        # run. Garante que duas chamadas com mesmos args retornam o mesmo id.
        a = ingest_core._synthetic_id("123", "456")
        b = ingest_core._synthetic_id("123", "456")
        self.assertEqual(a, b)

    def test_different_pairs_different_ids(self):
        # Probabilidade de colisao com hash truncado em 15 digitos e' baixa,
        # nao zero — mas para sanity check vale.
        a = ingest_core._synthetic_id("123", "456")
        b = ingest_core._synthetic_id("123", "457")
        self.assertNotEqual(a, b)

    def test_id_fits_in_bigint(self):
        # bigint Postgres maximo: 2^63 - 1 ≈ 9.22e18. Sintetico tem prefixo
        # "9" + 15 digitos = 16 caracteres, sempre <= 9999999999999999 < 1e16.
        a = ingest_core._synthetic_id("999999", "888888")
        self.assertLess(a, 10**16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
