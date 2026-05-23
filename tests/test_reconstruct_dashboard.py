"""Tests para ingest_core.reconstruct_dashboard_trades — FIFO multi-leg.

O CSV "TopStep Dashboard legacy" lista cada execucao como uma linha (Buy/Sell
isolados). Esta funcao pareia em trades fechados via FIFO por Product.

Casos cobertos:
- pares simples Buy→Sell e Sell→Buy
- multi-leg open + single close (1→N completed trades)
- single open + multi-leg close (N→N completed trades)
- partial close (sobra do open)
- flip (close maior que open vira novo open no lado oposto)
- proporcao de profit por leg (matched/closing_initial)
- fees por unidade (open + close, somados)
- isolation por Product (MNQ nao confunde com MES)
- empty/no-completion (so' opens, nunca fecha)
- sort por TimeParsed (linhas fora de ordem no CSV)
- strip de sufixo " CT" / " ET" do Time
- Side case-insensitive p/ deduzir Long/Short
"""
from __future__ import annotations

import unittest

import pandas as pd


import ingest_core


def _make_leg(
    time: str,
    side: str,
    size: float,
    price: float,
    product: str = "MNQ",
    profit: float = 0.0,
    fee: float = 0.0,
    leg_id: str | None = None,
    trade_day: str = "2026-05-20",
) -> dict:
    """Helper p/ construir 1 linha de execucao do CSV Dashboard."""
    return {
        "Time": time, "Trade Day": trade_day,
        "ID": leg_id or f"{product}-{time}-{side}",
        "Side": side, "Size": size, "Product": product,
        "Entry Price": price,
        "Total Fees": fee,
        "Profit": profit,
    }


class SimpleRoundtripTests(unittest.TestCase):

    def test_buy_then_sell_creates_one_long_trade(self):
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  2, 18450.00, profit=0.0,
                      fee=0.74, leg_id="L1"),
            _make_leg("2026-05-20 09:45:00", "Sell", 2, 18460.00, profit=40.00,
                      fee=0.74, leg_id="L2"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["Type"], "Long")
        self.assertEqual(row["Size"], 2)
        self.assertEqual(row["EntryPrice"], 18450.00)
        self.assertEqual(row["ExitPrice"], 18460.00)
        self.assertEqual(row["PnL"], 40.00)
        self.assertEqual(row["ContractName"], "MNQ")

    def test_sell_then_buy_creates_one_short_trade(self):
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Sell", 1, 18460.00, fee=0.37),
            _make_leg("2026-05-20 09:50:00", "Buy",  1, 18455.00, profit=20.00,
                      fee=0.37),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        row = out.iloc[0]
        self.assertEqual(row["Type"], "Short")
        self.assertEqual(row["EntryPrice"], 18460.00)
        self.assertEqual(row["ExitPrice"], 18455.00)
        self.assertEqual(row["PnL"], 20.00)


class MultiLegTests(unittest.TestCase):

    def test_two_opens_one_close_creates_two_completed_trades(self):
        # Buy 1 @ 18450, Buy 2 @ 18452, Sell 3 @ 18460 → 2 trades fechados
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, leg_id="A"),
            _make_leg("2026-05-20 09:32:00", "Buy",  2, 18452.00, leg_id="B"),
            _make_leg("2026-05-20 09:45:00", "Sell", 3, 18460.00, profit=78.00,
                      fee=1.11, leg_id="C"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 2)
        # Ambos sao Long (open foi Buy)
        self.assertTrue((out["Type"] == "Long").all())
        # FIFO: 1º completed pega leg A (Buy 1) primeiro
        self.assertEqual(out.iloc[0]["EntryPrice"], 18450.00)
        self.assertEqual(out.iloc[0]["Size"], 1)
        self.assertEqual(out.iloc[1]["EntryPrice"], 18452.00)
        self.assertEqual(out.iloc[1]["Size"], 2)
        # Profit split proporcional: 1/3 + 2/3 = total 78
        self.assertAlmostEqual(out["PnL"].sum(), 78.00, places=4)
        self.assertAlmostEqual(out.iloc[0]["PnL"], 26.00, places=4)  # 1/3
        self.assertAlmostEqual(out.iloc[1]["PnL"], 52.00, places=4)  # 2/3

    def test_one_open_two_closes_creates_two_trades_same_entry(self):
        # Buy 5 @ 18450, Sell 3 @ 18460, Sell 2 @ 18465 → 2 trades, mesmo entry
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  5, 18450.00, leg_id="X"),
            _make_leg("2026-05-20 09:45:00", "Sell", 3, 18460.00, profit=60.00,
                      leg_id="Y"),
            _make_leg("2026-05-20 09:50:00", "Sell", 2, 18465.00, profit=60.00,
                      leg_id="Z"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 2)
        # Ambos com mesmo entry e Long
        self.assertTrue((out["EntryPrice"] == 18450.00).all())
        self.assertTrue((out["Type"] == "Long").all())
        # Sizes 3 e 2
        sizes = sorted(out["Size"].tolist())
        self.assertEqual(sizes, [2, 3])
        # Exits diferentes
        exits = sorted(out["ExitPrice"].tolist())
        self.assertEqual(exits, [18460.00, 18465.00])


class PartialCloseAndFlipTests(unittest.TestCase):

    def test_partial_close_leaves_remainder_open(self):
        # Buy 3, Sell 2 → 1 trade fechado (size 2). Outro Buy 1 ainda aberto
        # nunca fecha → so' 1 completed.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  3, 18450.00, leg_id="A"),
            _make_leg("2026-05-20 09:45:00", "Sell", 2, 18460.00, profit=40.00,
                      leg_id="B"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["Size"], 2)
        self.assertEqual(out.iloc[0]["PnL"], 40.00)
        # O size restante de A (1 unidade) fica em open_execs — nao aparece em out.

    def test_flip_creates_completed_then_new_open(self):
        # Buy 2 @ 18450, Sell 5 → fecha 2 (long), abre 3 (short)
        # Sell 3 sozinho — close para os 3 short → fecha 3 (long)? Nao, Buy fecha o short.
        # Vamos testar: Buy 2, Sell 5, Buy 3 → 1 long fechado(2) + 1 short fechado(3)
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  2, 18450.00, leg_id="A"),
            _make_leg("2026-05-20 09:45:00", "Sell", 5, 18460.00, profit=20.00,
                      leg_id="B"),
            # Sell 5 fecha os 2 long (matched=2, sobra 3 que abre Short)
            _make_leg("2026-05-20 10:00:00", "Buy",  3, 18455.00, profit=15.00,
                      leg_id="C"),
            # Buy 3 fecha o Short de 3
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 2)
        # 1º fechado: Long (entry 18450, exit 18460, size 2)
        first = out.iloc[0]
        self.assertEqual(first["Type"], "Long")
        self.assertEqual(first["Size"], 2)
        self.assertEqual(first["EntryPrice"], 18450.00)
        self.assertEqual(first["ExitPrice"], 18460.00)
        # 2º fechado: Short (entry 18460, exit 18455, size 3)
        second = out.iloc[1]
        self.assertEqual(second["Type"], "Short")
        self.assertEqual(second["Size"], 3)
        self.assertEqual(second["EntryPrice"], 18460.00)
        self.assertEqual(second["ExitPrice"], 18455.00)

    def test_close_exact_size_no_remainder(self):
        # Buy 4, Sell 4 — exato. Sem sobra, sem novo open.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  4, 18450.00),
            _make_leg("2026-05-20 09:45:00", "Sell", 4, 18460.00, profit=80.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["Size"], 4)
        self.assertEqual(out.iloc[0]["PnL"], 80.00)


class FeesTests(unittest.TestCase):

    def test_fees_combine_open_and_close_per_matched_unit(self):
        # Open: 2 @ fee=$1.48 → $0.74/unit. Close: 2 @ fee=$1.48 → $0.74/unit.
        # Matched 2 unidades → 2*0.74 (entry) + 2*0.74 (exit) = 2.96
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  2, 18450.00, fee=1.48),
            _make_leg("2026-05-20 09:45:00", "Sell", 2, 18460.00, profit=40.00,
                      fee=1.48),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertAlmostEqual(out.iloc[0]["Fees"], 2.96, places=4)

    def test_negative_total_fees_is_absolute(self):
        # CSV pode trazer fee com sinal negativo ("-$1.48"). Funcao usa abs().
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, fee=-0.74),
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 18460.00, profit=20.00,
                      fee=-0.74),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        # 1*0.74 + 1*0.74 = 1.48 (não -1.48)
        self.assertAlmostEqual(out.iloc[0]["Fees"], 1.48, places=4)

    def test_commissions_field_always_zero(self):
        # Reconstrucao a partir do Dashboard nao distingue fee/commission —
        # joga tudo em Fees. Commissions e' 0.0 para nao quebrar schema do
        # banco que tem ambos os campos.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, fee=0.74),
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 18460.00, profit=10.00,
                      fee=0.74),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(out.iloc[0]["Commissions"], 0.0)


class MultiProductIsolationTests(unittest.TestCase):

    def test_mnq_and_mes_dont_cross_match(self):
        # Buy MNQ, Sell MES → nada deveria fechar (lados opostos mas Products
        # diferentes). open_execs e' por product.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, product="MNQ"),
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 5500.00, product="MES"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        # Cada Product so' tem uma execucao isolada — nada fecha.
        self.assertTrue(out.empty)

    def test_two_products_independent_roundtrips(self):
        # MNQ tem Buy/Sell, MES tem Sell/Buy. Devem produzir 2 trades.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, product="MNQ"),
            _make_leg("2026-05-20 09:31:00", "Sell", 1, 5500.00, product="MES"),
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 18460.00, product="MNQ",
                      profit=20.00),
            _make_leg("2026-05-20 09:46:00", "Buy",  1, 5495.00, product="MES",
                      profit=25.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 2)
        products = set(out["ContractName"])
        self.assertEqual(products, {"MNQ", "MES"})
        # MNQ deve ser Long, MES deve ser Short
        mnq_row = out[out["ContractName"] == "MNQ"].iloc[0]
        mes_row = out[out["ContractName"] == "MES"].iloc[0]
        self.assertEqual(mnq_row["Type"], "Long")
        self.assertEqual(mes_row["Type"], "Short")


class TimeHandlingTests(unittest.TestCase):

    def test_strips_ct_and_et_suffix(self):
        # CSV TopStep as vezes traz " CT" ou " ET" no Time. Funcao remove
        # antes do parse pra pd.to_datetime nao errar.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00 CT", "Buy",  1, 18450.00),
            _make_leg("2026-05-20 09:45:00 ET", "Sell", 1, 18460.00, profit=10.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        # EnteredAt deve estar SEM o sufixo (foi removido antes)
        entered = str(out.iloc[0]["EnteredAt"])
        self.assertNotIn(" CT", entered)
        self.assertNotIn(" ET", entered)

    def test_out_of_order_input_gets_sorted_before_matching(self):
        # Linha Sell vem ANTES da Buy no CSV (raro mas pode acontecer com
        # export por timestamp local).
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 18460.00, profit=10.00,
                      leg_id="LATE"),
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, leg_id="EARLY"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        # Apos sort, deve casar Buy(09:30) com Sell(09:45) em ordem cronologica.
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["Type"], "Long")
        self.assertEqual(out.iloc[0]["EntryPrice"], 18450.00)


class EdgeCasesTests(unittest.TestCase):

    def test_only_opens_no_closes_returns_empty(self):
        # 3 Buys seguidos — nada fecha. Reconstruir devolve DataFrame vazio.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy", 1, 18450.00),
            _make_leg("2026-05-20 09:31:00", "Buy", 1, 18451.00),
            _make_leg("2026-05-20 09:32:00", "Buy", 1, 18452.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertTrue(out.empty)

    def test_side_case_insensitive_for_long_short(self):
        # CSV pode trazer "buy" minusculo. Funcao usa .lower() para deduzir
        # Long/Short.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "buy",  1, 18450.00),
            _make_leg("2026-05-20 09:45:00", "sell", 1, 18460.00, profit=10.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["Type"], "Long")

    def test_trade_day_propagates_from_close_leg(self):
        # Trade Day vem do CSV em cada leg; o trade fechado pega o do close.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00,
                      trade_day="2026-05-20"),
            _make_leg("2026-05-20 23:45:00", "Sell", 1, 18460.00, profit=10.00,
                      trade_day="2026-05-21"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(out.iloc[0]["TradeDay"], "2026-05-21")

    def test_synthetic_ids_are_unique_per_pairing(self):
        # Cada (open_leg_id, close_leg_id) gera 1 Id sintetico distinto.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00, leg_id="A"),
            _make_leg("2026-05-20 09:31:00", "Buy",  1, 18451.00, leg_id="B"),
            _make_leg("2026-05-20 09:45:00", "Sell", 2, 18460.00, profit=20.00,
                      leg_id="C"),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        self.assertEqual(len(out), 2)
        self.assertNotEqual(out.iloc[0]["Id"], out.iloc[1]["Id"])

    def test_duration_is_computed_correctly(self):
        # Buy 09:30 → Sell 09:45 → trade_duration = 00:15:00
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00", "Buy",  1, 18450.00),
            _make_leg("2026-05-20 09:45:00", "Sell", 1, 18460.00, profit=10.00),
        ])
        out = ingest_core.reconstruct_dashboard_trades(df)
        # Format "HH:MM:SS.ffffff" — começa com "00:15:00"
        dur = out.iloc[0]["TradeDuration"]
        self.assertTrue(dur.startswith("00:15:00"), f"duration was {dur!r}")


class IntegrationWithNormalizeTests(unittest.TestCase):

    def test_full_pipeline_via_normalize_dashboard(self):
        # End-to-end: CSV cru → normalize_dashboard → DataFrame com colunas
        # snake_case prontas para upsert.
        df = pd.DataFrame([
            _make_leg("2026-05-20 09:30:00 CT", "Buy",  2, 18450.00),
            _make_leg("2026-05-20 09:45:00 CT", "Sell", 2, 18460.00,
                      profit=40.00, fee=1.48),
        ])
        out = ingest_core.normalize_dashboard(df)
        self.assertEqual(len(out), 1)
        # Apos normalize, colunas estao em snake_case (renomeadas)
        self.assertIn("id", out.columns)
        self.assertIn("contract_name", out.columns)
        self.assertIn("entered_at", out.columns)
        self.assertEqual(out["type"].iloc[0], "Long")
        self.assertEqual(out["size"].dtype, "int64")


if __name__ == "__main__":
    unittest.main(verbosity=2)
