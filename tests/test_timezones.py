"""
Tests para src/timezones.py — dual-tz (M7 da fusao).

Cobre to_primary, to_user, fmt_dual, _tz_short, e o comportamento de
fallback de user_tz() sem st.session_state populado.
"""
from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd


# st.session_state e' usado dentro de timezones — precisamos de um stub.
# Streamlit em "bare mode" emite warnings mas funciona; melhor mockar.
import streamlit as st

import timezones


def _set_user_tz(tz: str | None) -> None:
    """Helper: garante st.session_state['user_tz'] = tz (ou apaga)."""
    if tz is None:
        st.session_state.pop("user_tz", None)
    else:
        st.session_state["user_tz"] = tz


class TimezoneHelpersTests(unittest.TestCase):

    def setUp(self):
        # Limpa session_state de testes anteriores.
        _set_user_tz(None)

    def test_primary_tz_is_ny(self):
        self.assertEqual(timezones.PRIMARY_TZ, "America/New_York")
        self.assertEqual(timezones.PRIMARY_TZ_SHORT, "ET")

    def test_tz_short_known(self):
        self.assertEqual(timezones._tz_short("America/New_York"), "ET")
        self.assertEqual(timezones._tz_short("America/Sao_Paulo"), "BRT")
        self.assertEqual(timezones._tz_short("America/Chicago"), "CT")
        self.assertEqual(timezones._tz_short("Europe/London"), "BST/GMT")
        self.assertEqual(timezones._tz_short("UTC"), "UTC")

    def test_tz_short_unknown_falls_back_to_last_segment(self):
        self.assertEqual(timezones._tz_short("Asia/Tokyo"), "Tokyo")
        self.assertEqual(timezones._tz_short("Pacific/Auckland"), "Auckland")

    def test_user_tz_uses_session_state(self):
        _set_user_tz("Europe/London")
        self.assertEqual(timezones.user_tz(), "Europe/London")

    def test_user_tz_falls_back_to_brt_when_unset(self):
        _set_user_tz(None)
        # auth.current_user() retorna None em bare mode — cai no fallback.
        with mock.patch.object(timezones, "_ensure_tz", side_effect=lambda x: x):
            pass  # apenas garantir que o import nao quebrou
        self.assertEqual(timezones.user_tz(), "America/Sao_Paulo")

    def test_to_primary_converts_utc_to_et(self):
        _set_user_tz("America/Sao_Paulo")
        ts = pd.Timestamp("2026-05-20 14:32:00", tz="UTC")
        et = timezones.to_primary(ts)
        # 14:32 UTC = 10:32 EDT (DST ativo em maio)
        self.assertEqual(et.strftime("%H:%M"), "10:32")
        self.assertIn("New_York", str(et.tz))

    def test_to_user_uses_session_tz(self):
        _set_user_tz("Europe/Madrid")
        ts = pd.Timestamp("2026-05-20 14:32:00", tz="UTC")
        u = timezones.to_user(ts)
        # 14:32 UTC = 16:32 CEST (DST em maio)
        self.assertEqual(u.strftime("%H:%M"), "16:32")
        self.assertIn("Madrid", str(u.tz))

    def test_fmt_dual_brt(self):
        _set_user_tz("America/Sao_Paulo")
        ts = pd.Timestamp("2026-05-20 14:32:00", tz="UTC")
        s = timezones.fmt_dual(ts)
        # 14:32 UTC = 10:32 ET / 11:32 BRT (BRT = UTC-3 sem DST)
        self.assertEqual(s, "20/05 10:32 ET / 20/05 11:32 BRT")

    def test_fmt_dual_collapses_when_same_tz(self):
        _set_user_tz("America/New_York")
        ts = pd.Timestamp("2026-05-20 14:32:00", tz="UTC")
        s = timezones.fmt_dual(ts)
        # User tz = primary tz: nao duplica
        self.assertEqual(s, "20/05 10:32 ET")
        self.assertNotIn("/", s.split("ET")[1] if "ET" in s else "")

    def test_fmt_dual_series_matches_per_row(self):
        # Vetorizado deve casar linha-a-linha com fmt_dual em tz != primario.
        _set_user_tz("America/Sao_Paulo")
        ts = pd.Series(pd.to_datetime([
            "2026-05-20 14:32:00", "2026-01-10 19:05:00",
        ], utc=True))
        out = timezones.fmt_dual_series(ts)
        expected = [timezones.fmt_dual(t) for t in ts]
        self.assertEqual(list(out), expected)

    def test_fmt_dual_series_collapses_when_same_tz(self):
        _set_user_tz("America/New_York")
        ts = pd.Series(pd.to_datetime(["2026-05-20 14:32:00"], utc=True))
        out = timezones.fmt_dual_series(ts)
        self.assertEqual(list(out), ["20/05 10:32 ET"])

    def test_fmt_dual_series_nat_to_empty(self):
        _set_user_tz("America/Sao_Paulo")
        ts = pd.Series(pd.to_datetime(["2026-05-20 14:32:00", None], utc=True))
        out = timezones.fmt_dual_series(ts)
        self.assertEqual(out.iloc[0], "20/05 10:32 ET / 20/05 11:32 BRT")
        self.assertEqual(out.iloc[1], "")

    def test_fmt_dual_series_localizes_naive(self):
        # Série naive deve assumir UTC, como fmt_dual.
        _set_user_tz("America/Sao_Paulo")
        ts = pd.Series(pd.to_datetime(["2026-05-20 14:32:00"]))
        out = timezones.fmt_dual_series(ts)
        self.assertEqual(out.iloc[0], "20/05 10:32 ET / 20/05 11:32 BRT")

    def test_to_primary_localizes_naive(self):
        _set_user_tz("America/Sao_Paulo")
        # timestamp naive — funcao deve assumir UTC
        ts = pd.Timestamp("2026-05-20 14:32:00")
        et = timezones.to_primary(ts)
        self.assertEqual(et.strftime("%H:%M"), "10:32")


if __name__ == "__main__":
    unittest.main(verbosity=2)
