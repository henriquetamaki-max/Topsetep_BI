"""Tests para src/app_helpers.py — formatadores puros + parse de evento Plotly.

Helpers extraidos de app.py em 2026-05-23. app.py em si nao e' importavel em
bare-mode (chamadas top-level a auth.current_user) entao testamos so' aqui.
"""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

import app_helpers


# ---------------------------------------------------------------------------
# fmt_money — sinal isolado + separador de milhar + 2 casas
# ---------------------------------------------------------------------------


class FmtMoneyTests(unittest.TestCase):

    def test_positive_small(self):
        self.assertEqual(app_helpers.fmt_money(12.5), "$ 12.50")

    def test_negative_keeps_sign_before_dollar(self):
        # Sinal vem ANTES do "$" para nao ficar "$-12.50" — convencao do dash.
        self.assertEqual(app_helpers.fmt_money(-12.5), "-$ 12.50")

    def test_zero(self):
        self.assertEqual(app_helpers.fmt_money(0), "$ 0.00")

    def test_thousands_separator(self):
        self.assertEqual(app_helpers.fmt_money(12345.67), "$ 12,345.67")

    def test_large_negative(self):
        self.assertEqual(app_helpers.fmt_money(-1_000_000.5), "-$ 1,000,000.50")


# ---------------------------------------------------------------------------
# fmt_pts — sempre com sinal explicito (+/-) + " pts"
# ---------------------------------------------------------------------------


class FmtPtsTests(unittest.TestCase):

    def test_positive_has_plus_sign(self):
        # +0.00 vs 0.00 — leitor diferencia ganho de perda instantaneamente.
        self.assertEqual(app_helpers.fmt_pts(12.5), "+12.50 pts")

    def test_negative_has_minus_sign(self):
        self.assertEqual(app_helpers.fmt_pts(-12.5), "-12.50 pts")

    def test_zero_has_plus_sign(self):
        # 0 e' tecnicamente "positivo" no format spec {+,.2f}.
        self.assertEqual(app_helpers.fmt_pts(0), "+0.00 pts")

    def test_thousands_separator(self):
        self.assertEqual(app_helpers.fmt_pts(1234.5), "+1,234.50 pts")


# ---------------------------------------------------------------------------
# fmt_pct — multiplica por 100, 1 casa
# ---------------------------------------------------------------------------


class FmtPctTests(unittest.TestCase):

    def test_half(self):
        self.assertEqual(app_helpers.fmt_pct(0.5), "50.0%")

    def test_full(self):
        self.assertEqual(app_helpers.fmt_pct(1.0), "100.0%")

    def test_zero(self):
        self.assertEqual(app_helpers.fmt_pct(0), "0.0%")

    def test_small_fraction(self):
        self.assertEqual(app_helpers.fmt_pct(0.0125), "1.2%")

    def test_over_100(self):
        # win_rate > 100% nao acontece mas formatador nao trava.
        self.assertEqual(app_helpers.fmt_pct(1.5), "150.0%")


# ---------------------------------------------------------------------------
# color_class — sinal para CSS class
# ---------------------------------------------------------------------------


class ColorClassTests(unittest.TestCase):

    def test_positive(self):
        self.assertEqual(app_helpers.color_class(10), "pos")

    def test_negative(self):
        self.assertEqual(app_helpers.color_class(-10), "neg")

    def test_zero_counts_as_pos(self):
        # Convencao: flat trade nao e' perda — usa cor de ganho neutralizado.
        self.assertEqual(app_helpers.color_class(0), "pos")


# ---------------------------------------------------------------------------
# fmt_duration — escala automatica s/min/h
# ---------------------------------------------------------------------------


class FmtDurationTests(unittest.TestCase):

    def test_under_60s_uses_seconds(self):
        self.assertEqual(app_helpers.fmt_duration(45), "45s")
        self.assertEqual(app_helpers.fmt_duration(0), "0s")

    def test_boundary_60s_uses_minutes(self):
        # 60s exato → "1.0min", nao "60s". Boundary inclusiva no minute branch.
        self.assertEqual(app_helpers.fmt_duration(60), "1.0min")

    def test_minutes(self):
        self.assertEqual(app_helpers.fmt_duration(90), "1.5min")
        self.assertEqual(app_helpers.fmt_duration(3599), "60.0min")

    def test_boundary_3600s_uses_hours(self):
        self.assertEqual(app_helpers.fmt_duration(3600), "1.0h")

    def test_hours(self):
        self.assertEqual(app_helpers.fmt_duration(7200), "2.0h")
        self.assertEqual(app_helpers.fmt_duration(10800), "3.0h")


# ---------------------------------------------------------------------------
# extract_days_from_event — parsing defensivo de evento Plotly
# ---------------------------------------------------------------------------


class ExtractDaysFromEventTests(unittest.TestCase):

    def test_none_event_returns_empty_set(self):
        self.assertEqual(app_helpers.extract_days_from_event(None), set())

    def test_event_without_selection_returns_empty(self):
        self.assertEqual(app_helpers.extract_days_from_event({}), set())
        self.assertEqual(app_helpers.extract_days_from_event({"other": 1}), set())

    def test_empty_points_returns_empty(self):
        self.assertEqual(
            app_helpers.extract_days_from_event({"selection": {"points": []}}),
            set(),
        )

    def test_none_points_returns_empty(self):
        # selection.points pode vir como None (nem array) em alguns events.
        self.assertEqual(
            app_helpers.extract_days_from_event({"selection": {"points": None}}),
            set(),
        )

    def test_customdata_as_string_iso(self):
        event = {"selection": {"points": [{"customdata": "2026-05-20"}]}}
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20)},
        )

    def test_customdata_as_list_uses_first(self):
        # Heatmap customdata vem como list em Plotly multi-axis.
        event = {"selection": {"points": [{"customdata": ["2026-05-20", "extra"]}]}}
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20)},
        )

    def test_customdata_empty_list_falls_back_to_x(self):
        event = {"selection": {"points": [{"customdata": [], "x": "2026-05-20"}]}}
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20)},
        )

    def test_falls_back_to_x_when_no_customdata(self):
        # Barras: x = trade_day.
        event = {"selection": {"points": [{"x": "2026-05-21"}]}}
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 21)},
        )

    def test_skips_point_with_no_data(self):
        # Defensivo: ponto sem customdata nem x — pula sem quebrar.
        event = {"selection": {"points": [{"unrelated": "x"}]}}
        self.assertEqual(app_helpers.extract_days_from_event(event), set())

    def test_skips_invalid_date_string(self):
        event = {"selection": {"points": [{"x": "nao-e-data"}]}}
        self.assertEqual(app_helpers.extract_days_from_event(event), set())

    def test_dedups_repeated_days(self):
        # Mesmo dia em 2 pontos → set elimina duplicata.
        event = {
            "selection": {
                "points": [
                    {"x": "2026-05-20"},
                    {"x": "2026-05-20"},
                ]
            }
        }
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20)},
        )

    def test_multiple_points_merged_into_set(self):
        event = {
            "selection": {
                "points": [
                    {"x": "2026-05-20"},
                    {"customdata": "2026-05-21"},
                    {"x": "2026-05-22"},
                ]
            }
        }
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20), date(2026, 5, 21), date(2026, 5, 22)},
        )

    def test_pandas_timestamp_in_x(self):
        # Plotly as vezes passa Timestamp ao inves de string.
        ts = pd.Timestamp("2026-05-20")
        event = {"selection": {"points": [{"x": ts}]}}
        self.assertEqual(
            app_helpers.extract_days_from_event(event),
            {date(2026, 5, 20)},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
