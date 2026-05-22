"""Tests para `metrics.compute_coach` — analise comportamental.

`compute_coach` orquestra 8 sub-analises (revenge, cut/hold, overtrading,
losing_streak, leaks, strengths, size_buckets, points_distribution) +
headline e checklist textuais. Testamos via interface publica para `empty`
e schema, e via helpers privados para cada sub-analise — permite fixtures
menores e mais legiveis.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


EXPECTED_COACH_KEYS = {
    "headline", "revenge", "cut_winners_hold_losers", "overtrading",
    "losing_streak", "leaks", "strengths", "size_buckets",
    "points_distribution", "checklist",
}


def _trades_for_coach(rows: list[dict]) -> pd.DataFrame:
    """Builda DataFrame com colunas que `compute_coach` espera/usa.

    `compute_coach` deriva `duration_sec` internamente — passamos
    entered_at/exited_at via `duracao_sec` (helper field).
    """
    defaults = {
        "id": 1,
        "contract_name": "MNQ",
        "type": "Long",
        "size": 1,
        "weekday": "Wednesday",
        "entry_hour": 10,
        "points": 0.0,
    }
    base = pd.Timestamp("2026-05-20 10:00:00")
    out = []
    for i, r in enumerate(rows):
        merged = {**defaults, **r}
        merged["id"] = merged.get("id", i + 1)
        if "entered_at" not in merged:
            offset_min = merged.pop("offset_min", i * 10)
            duracao_sec = merged.pop("duracao_sec", 60)
            merged["entered_at"] = base + pd.Timedelta(minutes=offset_min)
            merged["exited_at"] = merged["entered_at"] + pd.Timedelta(seconds=duracao_sec)
        out.append(merged)
    df = pd.DataFrame(out)
    # _day_col prefere trade_day_et — usa essa coluna.
    if "trade_day" in df.columns:
        df = df.rename(columns={"trade_day": "trade_day_et"})
    elif "trade_day_et" not in df.columns:
        # Sem coluna de dia, _coach_overtrading falha. Derivamos do entered_at.
        df["trade_day_et"] = df["entered_at"].dt.date.astype(str)
    return df


def _prep_for_private(df: pd.DataFrame) -> pd.DataFrame:
    """`_coach_*` privados esperam `duration_sec` ja' computada (feito por
    compute_coach antes de chamar os helpers). Replica esse passo aqui."""
    d = df.copy()
    d["duration_sec"] = (d["exited_at"] - d["entered_at"]).dt.total_seconds()
    return d


# ---------------------------------------------------------------------------
# Top-level compute_coach
# ---------------------------------------------------------------------------


class ComputeCoachEmptyTests(unittest.TestCase):

    def test_empty_df_returns_full_schema(self):
        out = metrics.compute_coach(pd.DataFrame(), pd.DataFrame())
        self.assertEqual(set(out.keys()), EXPECTED_COACH_KEYS)
        self.assertEqual(out["headline"], [])
        self.assertEqual(out["checklist"], [])
        self.assertEqual(out["revenge"]["count"], 0)
        self.assertEqual(out["losing_streak"]["length"], 0)
        self.assertTrue(out["leaks"].empty)
        self.assertTrue(out["strengths"].empty)
        self.assertTrue(out["size_buckets"].empty)


class ComputeCoachSmokeTests(unittest.TestCase):

    def test_nonempty_df_runs_without_error(self):
        # Smoke test do orquestrador completo — apenas garante que cada
        # sub-analise roda sem explodir num dataset minusculo mas valido.
        df = _trades_for_coach([
            {"pnl_net": 5.0, "points": 1.0, "duracao_sec": 60},
            {"pnl_net": -3.0, "points": -0.5, "duracao_sec": 180},
            {"pnl_net": 2.0, "points": 0.5, "duracao_sec": 120},
        ])
        groups = pd.DataFrame({
            "additions_count": [0, 1, 0],
            "points_status": ["Winner", "Loser", "Winner"],
        })
        out = metrics.compute_coach(df, groups)
        self.assertEqual(set(out.keys()), EXPECTED_COACH_KEYS)
        self.assertIsInstance(out["headline"], list)
        self.assertIsInstance(out["checklist"], list)


# ---------------------------------------------------------------------------
# _coach_revenge
# ---------------------------------------------------------------------------


class CoachRevengeTests(unittest.TestCase):

    def test_too_few_trades_returns_zeroed(self):
        df = _prep_for_private(_trades_for_coach([{"pnl_net": -5.0}]))
        out = metrics._coach_revenge(df)
        self.assertEqual(out["count"], 0)
        self.assertEqual(out["pnl"], 0.0)

    def test_no_losses_returns_baseline_only(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 5.0, "offset_min": 0},
            {"pnl_net": 3.0, "offset_min": 10},
        ]))
        out = metrics._coach_revenge(df)
        self.assertEqual(out["count"], 0)
        self.assertAlmostEqual(out["baseline_avg_pnl"], 4.0)

    def test_revenge_window_detects_quick_post_loss_trade(self):
        # Loss grande no trade 1 (-10), trade 2 imediatamente depois (<5min).
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -10.0, "offset_min": 0, "duracao_sec": 60},
            {"pnl_net": -5.0, "offset_min": 2, "duracao_sec": 60},
        ]))
        out = metrics._coach_revenge(df)
        # Trade 2 entrou 2min depois (1min depois do exit do 1) — dentro da janela
        self.assertGreaterEqual(out["count"], 1)

    def test_revenge_window_excludes_far_apart_trades(self):
        # Loss grande, mas proximo trade so' 10min depois → fora da janela 5min.
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -10.0, "offset_min": 0, "duracao_sec": 60},
            {"pnl_net": -5.0, "offset_min": 15, "duracao_sec": 60},
        ]))
        out = metrics._coach_revenge(df)
        self.assertEqual(out["count"], 0)


# ---------------------------------------------------------------------------
# _coach_cut_hold
# ---------------------------------------------------------------------------


class CoachCutHoldTests(unittest.TestCase):

    def test_ratio_zero_when_no_wins(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -1.0, "duracao_sec": 300},
        ]))
        out = metrics._coach_cut_hold(df)
        self.assertEqual(out["ratio"], 0.0)
        self.assertFalse(out["flag"])

    def test_ratio_zero_when_no_losses(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 1.0, "duracao_sec": 60},
        ]))
        out = metrics._coach_cut_hold(df)
        self.assertAlmostEqual(out["avg_win_sec"], 60.0)
        self.assertEqual(out["avg_loss_sec"], 0.0)
        self.assertEqual(out["ratio"], 0.0)
        self.assertFalse(out["flag"])

    def test_flag_when_loss_duration_2x_or_more_than_win(self):
        # avg loss = 200s, avg win = 100s → ratio 2.0 → flag=True
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 1.0, "duracao_sec": 100, "offset_min": 0},
            {"pnl_net": -1.0, "duracao_sec": 200, "offset_min": 10},
        ]))
        out = metrics._coach_cut_hold(df)
        self.assertAlmostEqual(out["ratio"], 2.0)
        self.assertTrue(out["flag"])

    def test_no_flag_when_ratio_below_2(self):
        # avg loss = 100s, avg win = 100s → ratio 1.0
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 1.0, "duracao_sec": 100, "offset_min": 0},
            {"pnl_net": -1.0, "duracao_sec": 100, "offset_min": 10},
        ]))
        out = metrics._coach_cut_hold(df)
        self.assertAlmostEqual(out["ratio"], 1.0)
        self.assertFalse(out["flag"])


# ---------------------------------------------------------------------------
# _coach_overtrading
# ---------------------------------------------------------------------------


class CoachOvertradingTests(unittest.TestCase):

    def test_empty_df_returns_zeroed(self):
        # `_coach_overtrading` espera as colunas mas DF vazio devolve early.
        df = pd.DataFrame(columns=["id", "pnl_net", "trade_day_et"])
        out = metrics._coach_overtrading(df)
        self.assertEqual(out["threshold"], 0)
        self.assertEqual(out["tilt_days"], 0)

    def test_threshold_is_p75_of_trades_per_day(self):
        # 4 dias: 1, 2, 3, 10 trades. p75 = 5.25
        rows = []
        days_counts = [("2026-05-20", 1), ("2026-05-21", 2),
                       ("2026-05-22", 3), ("2026-05-23", 10)]
        for day, n in days_counts:
            for i in range(n):
                rows.append({
                    "id": len(rows) + 1, "pnl_net": -1.0,
                    "trade_day": day, "offset_min": i,
                })
        df = _trades_for_coach(rows)
        out = metrics._coach_overtrading(df)
        self.assertGreater(out["threshold"], 0)
        # Tilt days: dias com trades > threshold (>5.25) → 1 dia
        self.assertEqual(out["tilt_days"], 1)


# ---------------------------------------------------------------------------
# _coach_losing_streak
# ---------------------------------------------------------------------------


class CoachLosingStreakTests(unittest.TestCase):

    def test_empty_df_returns_zeroed(self):
        out = metrics._coach_losing_streak(pd.DataFrame())
        self.assertEqual(out["length"], 0)
        self.assertEqual(out["pnl"], 0.0)
        self.assertIsNone(out["start"])

    def test_no_losses_returns_zero_length(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 1.0, "offset_min": 0},
            {"pnl_net": 2.0, "offset_min": 10},
        ]))
        out = metrics._coach_losing_streak(df)
        self.assertEqual(out["length"], 0)

    def test_single_loss_returns_streak_of_1(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -5.0, "offset_min": 0},
        ]))
        out = metrics._coach_losing_streak(df)
        self.assertEqual(out["length"], 1)
        self.assertAlmostEqual(out["pnl"], -5.0)
        self.assertIsNotNone(out["start"])

    def test_consecutive_losses_form_streak(self):
        # 3 losses seguidas, depois 1 win quebra. Streak = 3, pnl = -10.
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -3.0, "offset_min": 0},
            {"pnl_net": -4.0, "offset_min": 10},
            {"pnl_net": -3.0, "offset_min": 20},
            {"pnl_net": 1.0, "offset_min": 30},
        ]))
        out = metrics._coach_losing_streak(df)
        self.assertEqual(out["length"], 3)
        self.assertAlmostEqual(out["pnl"], -10.0)

    def test_two_streaks_returns_longest(self):
        # Streak 1: 2 losses; win; Streak 2: 4 losses (mais longo).
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -1.0, "offset_min": 0},
            {"pnl_net": -1.0, "offset_min": 10},
            {"pnl_net": 5.0, "offset_min": 20},
            {"pnl_net": -1.0, "offset_min": 30},
            {"pnl_net": -2.0, "offset_min": 40},
            {"pnl_net": -3.0, "offset_min": 50},
            {"pnl_net": -4.0, "offset_min": 60},
        ]))
        out = metrics._coach_losing_streak(df)
        self.assertEqual(out["length"], 4)
        self.assertAlmostEqual(out["pnl"], -10.0)


# ---------------------------------------------------------------------------
# _coach_combo (leaks / strengths)
# ---------------------------------------------------------------------------


class CoachComboTests(unittest.TestCase):

    def test_combo_requires_min_trades_threshold(self):
        # Cada combinacao tem 1-2 trades — abaixo do LEAK_MIN_TRADES (3).
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -5.0, "contract_name": "MNQ",
             "weekday": "Monday", "entry_hour": 10},
            {"pnl_net": -3.0, "contract_name": "MES",
             "weekday": "Tuesday", "entry_hour": 11, "offset_min": 60},
        ]))
        out_leaks = metrics._coach_combo(df, kind="leak")
        self.assertTrue(out_leaks.empty)

    def test_combo_returns_leaks_when_3plus_trades(self):
        # MNQ Wednesday 10h × 3 trades, todos perdedores → 1 leak.
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": -5.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 0},
            {"pnl_net": -3.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 60},
            {"pnl_net": -2.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 120},
        ]))
        out_leaks = metrics._coach_combo(df, kind="leak")
        self.assertEqual(len(out_leaks), 1)
        self.assertEqual(out_leaks.iloc[0]["contract_name"], "MNQ")
        self.assertAlmostEqual(out_leaks.iloc[0]["pnl"], -10.0)

    def test_combo_returns_strengths_only_positive_pnl(self):
        # 1 combo positivo, 1 combo negativo → kind=strength devolve so' o +.
        df = _prep_for_private(_trades_for_coach([
            # Combo positivo: MNQ Wed 10h × 3 wins
            {"pnl_net": 5.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 0},
            {"pnl_net": 3.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 60},
            {"pnl_net": 2.0, "contract_name": "MNQ",
             "weekday": "Wednesday", "entry_hour": 10, "offset_min": 120},
            # Combo negativo: MES Thu 11h × 3 losses
            {"pnl_net": -2.0, "contract_name": "MES",
             "weekday": "Thursday", "entry_hour": 11, "offset_min": 1440},
            {"pnl_net": -3.0, "contract_name": "MES",
             "weekday": "Thursday", "entry_hour": 11, "offset_min": 1500},
            {"pnl_net": -1.0, "contract_name": "MES",
             "weekday": "Thursday", "entry_hour": 11, "offset_min": 1560},
        ]))
        out_strengths = metrics._coach_combo(df, kind="strength")
        self.assertEqual(len(out_strengths), 1)
        self.assertEqual(out_strengths.iloc[0]["contract_name"], "MNQ")
        out_leaks = metrics._coach_combo(df, kind="leak")
        self.assertEqual(len(out_leaks), 1)
        self.assertEqual(out_leaks.iloc[0]["contract_name"], "MES")


# ---------------------------------------------------------------------------
# _coach_size_buckets
# ---------------------------------------------------------------------------


class CoachSizeBucketsTests(unittest.TestCase):

    def test_no_size_column_returns_empty(self):
        df = pd.DataFrame({"pnl_net": [1.0, -1.0], "id": [1, 2]})
        out = metrics._coach_size_buckets(df)
        self.assertTrue(out.empty)

    def test_groups_by_size_with_aggregations(self):
        df = _prep_for_private(_trades_for_coach([
            {"pnl_net": 5.0, "size": 1, "offset_min": 0},
            {"pnl_net": -3.0, "size": 1, "offset_min": 10},
            {"pnl_net": 8.0, "size": 2, "offset_min": 20},
            {"pnl_net": 2.0, "size": 2, "offset_min": 30},
        ]))
        out = metrics._coach_size_buckets(df)
        self.assertEqual(len(out), 2)
        out = out.set_index("size")
        self.assertEqual(out.loc[1, "trades"], 2)
        self.assertAlmostEqual(out.loc[1, "total_pnl"], 2.0)
        self.assertAlmostEqual(out.loc[1, "avg_pnl"], 1.0)
        self.assertAlmostEqual(out.loc[1, "win_rate"], 0.5)
        self.assertEqual(out.loc[2, "trades"], 2)
        self.assertAlmostEqual(out.loc[2, "total_pnl"], 10.0)


# ---------------------------------------------------------------------------
# _coach_points_dist
# ---------------------------------------------------------------------------


class CoachPointsDistTests(unittest.TestCase):

    def test_empty_points_returns_zeroed(self):
        df = pd.DataFrame({"points": []})
        out = metrics._coach_points_dist(df)
        self.assertEqual(out["values"], [])
        self.assertEqual(out["mean"], 0.0)
        self.assertEqual(out["median"], 0.0)

    def test_dist_mean_and_median(self):
        df = pd.DataFrame({"points": [1.0, 2.0, 3.0, 4.0, 5.0]})
        out = metrics._coach_points_dist(df)
        self.assertAlmostEqual(out["mean"], 3.0)
        self.assertAlmostEqual(out["median"], 3.0)
        self.assertEqual(len(out["values"]), 5)

    def test_dist_drops_nan(self):
        df = pd.DataFrame({"points": [1.0, float("nan"), 3.0]})
        out = metrics._coach_points_dist(df)
        self.assertEqual(len(out["values"]), 2)
        self.assertAlmostEqual(out["mean"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
