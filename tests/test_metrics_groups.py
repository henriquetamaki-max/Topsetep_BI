"""Tests para `metrics.compute_groups` — engine de overlap grouping.

Esse e o motor que transforma trades individuais em "operacoes" (grupos)
pela regra: mesma `(contract_name, type)` + `entered_at` <= max(`exited_at`)
ja visto no grupo aberto. Toda a estrutura de KPIs, segmentos e aderencia
ao plano matinal depende desse agrupamento — quebra silenciosa aqui
significa metricas erradas em toda a app.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import metrics  # noqa: E402


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def _make_trades(rows: list[dict]) -> pd.DataFrame:
    """Helper: builda DataFrame com defaults sensatos preenchendo o que
    falta. Campos obrigatorios em `rows`: id, contract_name, type,
    entered_at, exited_at. Tudo o que falta vira 0 / None."""
    defaults = {
        "points": 0.0,
        "pnl": 0.0,
        "pnl_net": 0.0,
        "size": 1,
    }
    out = []
    for r in rows:
        merged = {**defaults, **r}
        merged["entered_at"] = _ts(merged["entered_at"])
        merged["exited_at"] = _ts(merged["exited_at"])
        out.append(merged)
    return pd.DataFrame(out)


class ComputeGroupsEmptyTests(unittest.TestCase):

    def test_empty_dataframe_returns_empty_pair(self):
        df_in = pd.DataFrame()
        df_out, groups = metrics.compute_groups(df_in)
        self.assertTrue(df_out.empty)
        self.assertTrue(groups.empty)

    def test_empty_dataframe_has_group_id_column(self):
        df_out, _ = metrics.compute_groups(pd.DataFrame())
        self.assertIn("group_id", df_out.columns)
        self.assertEqual(df_out["group_id"].dtype, "int64")


class ComputeGroupsSingleTradeTests(unittest.TestCase):

    def test_single_trade_gets_group_id_1(self):
        df = _make_trades([{
            "id": 1, "contract_name": "MNQ", "type": "Long",
            "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10",
            "points": 5, "pnl": 10, "pnl_net": 9, "size": 2,
        }])
        df_out, groups = metrics.compute_groups(df)
        self.assertEqual(list(df_out["group_id"]), [1])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups.loc[0, "trade_count"], 1)
        self.assertEqual(groups.loc[0, "additions_count"], 0)
        self.assertFalse(bool(groups.loc[0, "has_addition"]))

    def test_single_trade_aggregations_match_input(self):
        df = _make_trades([{
            "id": 1, "contract_name": "MNQ", "type": "Long",
            "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10",
            "points": 5.5, "pnl": 11.0, "pnl_net": 9.0, "size": 3,
        }])
        _, groups = metrics.compute_groups(df)
        row = groups.iloc[0]
        self.assertEqual(row["contract_name"], "MNQ")
        self.assertEqual(row["type"], "Long")
        self.assertEqual(row["total_points"], 5.5)
        self.assertEqual(row["total_pnl"], 11.0)
        self.assertEqual(row["total_net_pnl"], 9.0)
        self.assertEqual(row["total_size"], 3)
        self.assertEqual(row["group_start"], _ts("2026-05-20 14:00"))
        self.assertEqual(row["group_end"], _ts("2026-05-20 14:10"))


class ComputeGroupsOverlapTests(unittest.TestCase):

    def test_two_trades_no_overlap_creates_two_groups(self):
        # A (14:00-14:10) e B (14:11-14:20), mesma contract+type. Sem overlap.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10"},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:11", "exited_at": "2026-05-20 14:20"},
        ])
        df_out, groups = metrics.compute_groups(df)
        self.assertEqual(sorted(df_out["group_id"].tolist()), [1, 2])
        self.assertEqual(len(groups), 2)
        # Cada grupo tem trade_count=1, additions_count=0
        self.assertTrue((groups["additions_count"] == 0).all())
        self.assertTrue((groups["has_addition"] == False).all())  # noqa: E712

    def test_two_trades_with_overlap_merge_into_one_group(self):
        # A (14:00-14:15) e B (14:10-14:20). entered_at(B)=14:10 <= cur_end=14:15.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:15",
             "points": 3, "pnl": 6, "pnl_net": 5, "size": 1},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:20",
             "points": 2, "pnl": 4, "pnl_net": 3, "size": 1},
        ])
        df_out, groups = metrics.compute_groups(df)
        self.assertEqual(set(df_out["group_id"]), {1})
        self.assertEqual(len(groups), 1)
        row = groups.iloc[0]
        self.assertEqual(row["trade_count"], 2)
        self.assertEqual(row["additions_count"], 1)
        self.assertTrue(bool(row["has_addition"]))
        self.assertEqual(row["total_points"], 5)
        self.assertEqual(row["total_pnl"], 10)
        self.assertEqual(row["total_net_pnl"], 8)
        self.assertEqual(row["total_size"], 2)

    def test_dynamic_extension_of_cur_end(self):
        # A (14:00-14:10), B (14:09-14:30) — overlap, cur_end vira 14:30.
        # C (14:25-14:40) — overlap com cur_end=14:30 → mesmo grupo.
        # D (14:45-14:50) — sem overlap → novo grupo.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10"},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:09", "exited_at": "2026-05-20 14:30"},
            {"id": 3, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:25", "exited_at": "2026-05-20 14:40"},
            {"id": 4, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:45", "exited_at": "2026-05-20 14:50"},
        ])
        df_out, groups = metrics.compute_groups(df)
        gids = df_out.sort_values("entered_at")["group_id"].tolist()
        self.assertEqual(gids, [1, 1, 1, 2])
        self.assertEqual(len(groups), 2)
        big = groups[groups["group_id"] == 1].iloc[0]
        self.assertEqual(big["trade_count"], 3)
        self.assertEqual(big["additions_count"], 2)
        self.assertEqual(big["group_start"], _ts("2026-05-20 14:00"))
        self.assertEqual(big["group_end"], _ts("2026-05-20 14:40"))

    def test_back_to_back_exact_boundary_is_overlap(self):
        # entered_at(B) == exited_at(A) → entrou exatamente quando A saiu.
        # Regra atual: `entered > cur_end` cria novo grupo; `entered == cur_end`
        # cai no else → fica no mesmo grupo. Documenta esse comportamento.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10"},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:20"},
        ])
        df_out, _ = metrics.compute_groups(df)
        self.assertEqual(set(df_out["group_id"]), {1})


class ComputeGroupsIsolationTests(unittest.TestCase):

    def test_different_contracts_never_merge(self):
        # Mesmo timestamp, contratos diferentes → grupos separados.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:30"},
            {"id": 2, "contract_name": "MES", "type": "Long",
             "entered_at": "2026-05-20 14:05", "exited_at": "2026-05-20 14:25"},
        ])
        df_out, groups = metrics.compute_groups(df)
        self.assertEqual(sorted(df_out["group_id"].tolist()), [1, 2])
        self.assertEqual(set(groups["contract_name"]), {"MNQ", "MES"})

    def test_different_types_never_merge(self):
        # Mesmo contrato, types opostos → hedge, grupos separados.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:30"},
            {"id": 2, "contract_name": "MNQ", "type": "Short",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:20"},
        ])
        df_out, groups = metrics.compute_groups(df)
        self.assertEqual(sorted(df_out["group_id"].tolist()), [1, 2])
        self.assertEqual(set(groups["type"]), {"Long", "Short"})


class ComputeGroupsOrderingTests(unittest.TestCase):

    def test_unordered_input_is_sorted_by_entered_at(self):
        # Input desordenado: B antes de A na linha. Funcao deve ordenar.
        df = _make_trades([
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:20"},
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:05"},
        ])
        df_out, groups = metrics.compute_groups(df)
        # Apos sort, primeiro grupo (group_id=1) deve ser o trade A (entered 14:00).
        self.assertEqual(df_out.iloc[0]["id"], 1)
        self.assertEqual(df_out.iloc[0]["group_id"], 1)
        # 2 trades disjuntos → 2 grupos
        self.assertEqual(len(groups), 2)


class ComputeGroupsStatusTests(unittest.TestCase):

    def test_points_status_winner_loser_flat(self):
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:05",
             "points": 3.0, "pnl": 6.0},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:15",
             "points": -2.0, "pnl": -4.0},
            {"id": 3, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:20", "exited_at": "2026-05-20 14:25",
             "points": 0.0, "pnl": 0.0},
        ])
        _, groups = metrics.compute_groups(df)
        statuses_pts = groups.sort_values("group_start")["points_status"].tolist()
        statuses_pnl = groups.sort_values("group_start")["pnl_status"].tolist()
        self.assertEqual(statuses_pts, ["Winner", "Loser", "Flat"])
        self.assertEqual(statuses_pnl, ["Winner", "Loser", "Flat"])

    def test_status_uses_aggregated_totals(self):
        # Grupo com 2 trades, soma de points negativa apesar de um ser positivo.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:30",
             "points": 1.0, "pnl": 2.0},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:10", "exited_at": "2026-05-20 14:20",
             "points": -5.0, "pnl": -10.0},
        ])
        _, groups = metrics.compute_groups(df)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups.iloc[0]["points_status"], "Loser")
        self.assertEqual(groups.iloc[0]["pnl_status"], "Loser")


class ComputeGroupsDurationTests(unittest.TestCase):

    def test_duration_min_single_trade(self):
        df = _make_trades([{
            "id": 1, "contract_name": "MNQ", "type": "Long",
            "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:07",
        }])
        _, groups = metrics.compute_groups(df)
        self.assertAlmostEqual(float(groups.iloc[0]["duration_min"]), 7.0)

    def test_duration_min_merged_group_uses_outer_span(self):
        # A (14:00-14:10), B (14:05-14:30) → span = 30min do start ao end.
        df = _make_trades([
            {"id": 1, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:00", "exited_at": "2026-05-20 14:10"},
            {"id": 2, "contract_name": "MNQ", "type": "Long",
             "entered_at": "2026-05-20 14:05", "exited_at": "2026-05-20 14:30"},
        ])
        _, groups = metrics.compute_groups(df)
        self.assertEqual(len(groups), 1)
        self.assertAlmostEqual(float(groups.iloc[0]["duration_min"]), 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
