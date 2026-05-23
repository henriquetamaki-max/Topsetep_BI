"""Tests para helpers internos de src/metrics.py.

Hoje so' cobre `_day_col`, que decide entre `trade_day_et` (NY/ET derivado
pos-fusao) e `trade_day` (legado, CT do CSV TopStepX). Critico porque toda
agregacao por dia no Dashboard depende dele para nao misturar fusos.
"""
from __future__ import annotations

import unittest

import pandas as pd


import metrics


class DayColTests(unittest.TestCase):

    def test_prefers_trade_day_et_when_present(self):
        df = pd.DataFrame({
            "trade_day": ["2026-05-20"],
            "trade_day_et": ["2026-05-20"],
        })
        self.assertEqual(metrics._day_col(df), "trade_day_et")

    def test_falls_back_to_trade_day_when_et_missing(self):
        df = pd.DataFrame({"trade_day": ["2026-05-20"]})
        self.assertEqual(metrics._day_col(df), "trade_day")

    def test_empty_dataframe_with_et_column(self):
        df = pd.DataFrame({"trade_day_et": pd.Series(dtype="object")})
        self.assertEqual(metrics._day_col(df), "trade_day_et")

    def test_empty_dataframe_no_columns_falls_back(self):
        # DataFrame totalmente vazio — funcao escolhe o legado por seguranca
        # (sem checar se trade_day existe; downstream falha rapido com
        # KeyError, que e' o comportamento esperado vs. retornar None).
        df = pd.DataFrame()
        self.assertEqual(metrics._day_col(df), "trade_day")

    def test_et_takes_precedence_even_if_trade_day_first(self):
        # Mesma coluna ordem nao deveria afetar — pandas DataFrames sao dict-like.
        df = pd.DataFrame({
            "trade_day_et": pd.Series(dtype="object"),
            "trade_day": pd.Series(dtype="object"),
        })
        self.assertEqual(metrics._day_col(df), "trade_day_et")


if __name__ == "__main__":
    unittest.main(verbosity=2)
