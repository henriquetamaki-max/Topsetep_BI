"""
risk_engine.py — motor puro de risk management pré-trade (release 3.0).

Sem Streamlit, sem Supabase: só numpy/pandas/math. Toda a matemática do Risk
Planner vive aqui, testável isoladamente e determinística (Monte Carlo via seed).
O caller (src/risk_plan.py) injeta point_value_usd (de public.contracts) e os
limites (de public.risk_settings); este módulo nunca toca o banco.

Regras TopStep modeladas (fonte: help.topstep.com 2026):
- MLL (Maximum Loss Limit) = trailing drawdown. Combine traila pelo PICO INTRADAY
  de equity; Express Funded (XFA) traila pelo saldo de FECHAMENTO (EOD) e TRAVA
  no saldo inicial ("$0 net") quando o buffer é alcançado. Buffers por tamanho.
- DLL (Daily Loss Limit): perda máxima por dia; ao bater, o dia é cortado.
- Teto de contratos: 5/10/15 minis (×10 para micros) por tamanho 50/100/150K.
- Consistência 50% (melhor dia ≤ 50% do lucro do ciclo) e dia vencedor ≥ $150:
  checados informativamente.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# --- Constantes TopStep -----------------------------------------------------

# Teto de contratos por tamanho (minis). Micros = 10x.
CONTRACT_CAP_MINIS: dict[str, int] = {"50K": 5, "100K": 10, "150K": 15}
# Buffer do trailing MLL por tamanho (= distância saldo_inicial → MLL inicial).
TRAILING_BUFFER: dict[str, float] = {"50K": 2000.0, "100K": 3000.0, "150K": 4500.0}
# DLL default por tamanho (override possível via input manual).
DLL_DEFAULT: dict[str, float] = {"50K": 1000.0, "100K": 2000.0, "150K": 3000.0}

MIN_WINNING_DAY_USD = 150.0
CONSISTENCY_MAX_PCT = 0.50


def plan_size_key(account_type: str | None) -> str | None:
    """'Express 50K' -> '50K'; 'Custom'/None/desconhecido -> None."""
    if not account_type:
        return None
    # Ordena por comprimento desc: "50K" é substring de "150K", então as chaves
    # maiores precisam ser testadas primeiro para não casar errado.
    for key in sorted(CONTRACT_CAP_MINIS, key=len, reverse=True):
        if key in account_type:
            return key
    return None


# --- Sizing -----------------------------------------------------------------


def risk_dollars_per_trade(balance_usd: float, risk_mode: str, risk_value: float) -> float:
    """risk_mode='pct' -> balance*risk_value/100 ; 'usd' -> risk_value. Nunca < 0."""
    if risk_value is None or risk_value <= 0:
        return 0.0
    if risk_mode == "pct":
        return max(0.0, float(balance_usd) * float(risk_value) / 100.0)
    return max(0.0, float(risk_value))


def contract_cap(
    account_type: str | None, is_micro: bool, max_position_size: int | None
) -> int | None:
    """Teto efetivo de contratos = min(teto TopStep p/ tamanho [×10 se micro],
    max_position_size de risk_settings). Custom sem tamanho TopStep e sem
    max_position_size -> None (sem teto)."""
    key = plan_size_key(account_type)
    caps: list[int] = []
    if key is not None:
        base = CONTRACT_CAP_MINIS[key]
        caps.append(base * 10 if is_micro else base)
    if max_position_size is not None and max_position_size > 0:
        caps.append(int(max_position_size))
    if not caps:
        return None
    return min(caps)


def max_contracts(
    risk_usd: float, stop_points: float, point_value_usd: float, cap: int | None
) -> int:
    """floor(risk_usd / (stop_points × point_value_usd)), clamp em [0, cap].
    Guarda divisão por zero (stop<=0 ou pv<=0 ou risk<=0 -> 0)."""
    denom = (stop_points or 0) * (point_value_usd or 0)
    if denom <= 0 or risk_usd <= 0:
        return 0
    n = math.floor(risk_usd / denom)
    n = max(0, n)
    if cap is not None:
        n = min(n, max(0, int(cap)))
    return n


def max_stop_points(risk_usd: float, n_contracts: int, point_value_usd: float) -> float | None:
    """Stop (pontos) que consome exatamente risk_usd com n contratos.
    None se n<=0 ou pv<=0."""
    if n_contracts <= 0 or (point_value_usd or 0) <= 0 or risk_usd <= 0:
        return None
    return float(risk_usd) / (float(n_contracts) * float(point_value_usd))


def trades_to_dll(daily_loss_limit_usd: float | None, risk_usd_per_trade: float) -> int:
    """Quantos trades perdedores cabem antes de bater o DLL. floor(DLL/risco).
    0 se DLL ausente/<=0 ou risco<=0."""
    if not daily_loss_limit_usd or daily_loss_limit_usd <= 0 or risk_usd_per_trade <= 0:
        return 0
    return int(math.floor(float(daily_loss_limit_usd) / float(risk_usd_per_trade)))


# --- Blowout / trailing MLL -------------------------------------------------


def distance_to_blowout(balance_usd: float, mll_threshold_usd: float) -> float:
    """USD entre o saldo atual e o nível de blowout (MLL). Pode ser negativo
    (conta já abaixo do limite)."""
    return float(balance_usd) - float(mll_threshold_usd)


def days_to_blowout(distance_usd: float, daily_loss_limit_usd: float | None) -> float | None:
    """Quantos dias de PERDA MÁXIMA (= DLL) até o blowout. None se DLL ausente/<=0."""
    if not daily_loss_limit_usd or daily_loss_limit_usd <= 0:
        return None
    return math.floor(max(0.0, distance_usd) / float(daily_loss_limit_usd))


def update_trailing_threshold(
    *,
    mode: str,
    buffer: float | None,
    current_threshold: float,
    initial_balance: float,
    intraday_peak_equity: float,
    eod_equity: float,
) -> tuple[float, bool]:
    """Atualiza o threshold do MLL ao fim de um dia. Devolve (novo, locked).

    Combine: traila pelo PICO INTRADAY -> max(current, intraday_peak - buffer).
    XFA:     traila pelo EOD          -> max(current, eod - buffer), travando em
             initial_balance (uma vez alcançado, congela = "$0 net").
    O threshold nunca desce (garantido pelo max()). buffer=None (Custom) -> sem
    trailing, mantém current."""
    if buffer is None:
        return float(current_threshold), False
    if mode == "xfa":
        cand = min(eod_equity - buffer, initial_balance)
        new = max(current_threshold, cand)
        new = min(new, initial_balance)
        return float(new), bool(new >= initial_balance)
    # combine (default)
    new = max(current_threshold, intraday_peak_equity - buffer)
    return float(new), False


# --- Monte Carlo ------------------------------------------------------------


def _pctiles(arr: np.ndarray) -> dict[str, float]:
    p5, p25, p50, p75, p95 = np.percentile(arr, [5, 25, 50, 75, 95])
    return {
        "p5": float(p5), "p25": float(p25), "p50": float(p50),
        "p75": float(p75), "p95": float(p95),
    }


def monte_carlo(
    *,
    balance_usd: float,
    mll_threshold_usd: float,
    daily_loss_limit_usd: float | None,
    risk_usd_per_trade: float,
    win_rate: float,
    avg_r: float,
    trades_per_day: int,
    horizon_days: int,
    trailing_mode: str = "combine",
    balance_buffer: str | None = None,
    n_sims: int = 10_000,
    seed: int = 42,
    dll_halts_day: bool = True,
) -> dict:
    """Simula caminhos de equity por trade (modelo R-múltiplo) e estima a
    probabilidade de blowout (atingir o MLL), de bater o DLL, e percentis de
    equity final / drawdown.

    Modelo por trade: vitória (prob win_rate) -> +avg_r·risk ; derrota -> -1·risk
    (stop = 1R por definição). Determinístico via numpy default_rng(seed).
    Vetorizado em n_sims; loop só em dias×trades (rápido: <200ms p/ 10k×20×5).
    """
    n_sims = int(n_sims)
    horizon_days = int(horizon_days)
    trades_per_day = int(trades_per_day)
    risk = float(risk_usd_per_trade)
    dll = float(daily_loss_limit_usd) if daily_loss_limit_usd else 0.0
    buffer = TRAILING_BUFFER.get(balance_buffer) if balance_buffer else None

    if n_sims <= 0 or horizon_days <= 0 or trades_per_day <= 0 or risk <= 0:
        return {
            "p_blowout": None, "p_hit_dll_any_day": None, "p_profit": None,
            "equity_pctiles": {}, "max_drawdown_pctiles": {},
            "expected_final_equity": None, "equity_curve_p50": [],
            "n_sims": n_sims, "seed": int(seed), "degenerate": True,
        }

    rng = np.random.default_rng(int(seed))
    wins = rng.random((n_sims, horizon_days, trades_per_day)) < float(win_rate)
    trade_pnl = np.where(wins, float(avg_r) * risk, -risk)

    equity = np.full(n_sims, float(balance_usd), dtype=float)
    threshold = np.full(n_sims, float(mll_threshold_usd), dtype=float)
    initial_balance = float(balance_usd)
    # já abaixo do MLL no início = blown imediato
    blown = equity <= threshold
    hit_dll_any = np.zeros(n_sims, dtype=bool)
    peak_overall = equity.copy()
    max_dd = np.zeros(n_sims, dtype=float)
    equity_curve_eod = np.zeros((n_sims, horizon_days), dtype=float)

    for d in range(horizon_days):
        intraday_peak = equity.copy()
        day_pnl = np.zeros(n_sims, dtype=float)
        day_halted = np.zeros(n_sims, dtype=bool)
        for tr in range(trades_per_day):
            active = (~blown) & (~day_halted)
            pnl = trade_pnl[:, d, tr]
            equity = np.where(active, equity + pnl, equity)
            day_pnl = np.where(active, day_pnl + pnl, day_pnl)
            intraday_peak = np.maximum(intraday_peak, equity)
            peak_overall = np.maximum(peak_overall, equity)
            max_dd = np.maximum(max_dd, peak_overall - equity)
            blown = blown | (active & (equity <= threshold))
            if dll_halts_day and dll > 0:
                newly_halted = active & (day_pnl <= -dll)
                day_halted = day_halted | newly_halted
                hit_dll_any = hit_dll_any | newly_halted
        # fim do dia: atualiza threshold por sim (vetorizado)
        if buffer is not None:
            if trailing_mode == "xfa":
                cand = np.minimum(equity - buffer, initial_balance)
                threshold = np.maximum(threshold, cand)
                threshold = np.minimum(threshold, initial_balance)
            else:
                threshold = np.maximum(threshold, intraday_peak - buffer)
        equity_curve_eod[:, d] = equity

    final_equity = equity
    return {
        "p_blowout": float(blown.mean()),
        "p_hit_dll_any_day": float(hit_dll_any.mean()),
        "p_profit": float((final_equity > balance_usd).mean()),
        "equity_pctiles": _pctiles(final_equity),
        "max_drawdown_pctiles": _pctiles(max_dd),
        "expected_final_equity": float(final_equity.mean()),
        "equity_curve_p50": np.percentile(equity_curve_eod, 50, axis=0).tolist(),
        "n_sims": n_sims, "seed": int(seed), "degenerate": False,
    }


# --- Checagem de regras TopStep --------------------------------------------


def check_rules(
    *,
    account_type: str | None,
    planned_total_contracts: int,
    is_micro: bool = False,
    expected_daily_profit_usd: float | None = None,
    best_day_usd: float | None = None,
    cycle_profit_usd: float | None = None,
) -> list[dict]:
    """Devolve lista de {rule, ok, severity, detail_key, ctx} para as regras
    TopStep verificáveis pré-trade. Custom (sem teto) -> contract_cap ok/info."""
    out: list[dict] = []

    key = plan_size_key(account_type)
    if key is None:
        out.append({
            "rule": "contract_cap", "ok": True, "severity": "info",
            "detail_key": "riskplanner.rule.contract_cap_custom", "ctx": {},
        })
    else:
        cap = CONTRACT_CAP_MINIS[key] * (10 if is_micro else 1)
        ok = planned_total_contracts <= cap
        out.append({
            "rule": "contract_cap", "ok": ok,
            "severity": "info" if ok else "critical",
            "detail_key": "riskplanner.rule.contract_cap",
            "ctx": {"planned": int(planned_total_contracts), "cap": int(cap)},
        })

    if expected_daily_profit_usd is not None:
        ok = expected_daily_profit_usd >= MIN_WINNING_DAY_USD
        out.append({
            "rule": "min_winning_day", "ok": ok,
            "severity": "info" if ok else "warn",
            "detail_key": "riskplanner.rule.min_winning_day",
            "ctx": {"expected": float(expected_daily_profit_usd), "min": MIN_WINNING_DAY_USD},
        })

    if best_day_usd is not None and cycle_profit_usd is not None and cycle_profit_usd > 0:
        ok = best_day_usd <= CONSISTENCY_MAX_PCT * cycle_profit_usd
        out.append({
            "rule": "consistency_50", "ok": ok,
            "severity": "info" if ok else "warn",
            "detail_key": "riskplanner.rule.consistency_50",
            "ctx": {
                "best_day": float(best_day_usd),
                "cap": float(CONSISTENCY_MAX_PCT * cycle_profit_usd),
            },
        })

    return out


# --- Comparativo por ativo --------------------------------------------------


def compare_assets(
    *,
    contracts: list[dict],
    balance_usd: float,
    mll_threshold_usd: float,
    daily_loss_limit_usd: float | None,
    risk_mode: str,
    risk_value: float,
    default_stop_points: float,
    account_type: str | None,
    max_position_size: int | None = None,
    mc_params: dict | None = None,
) -> pd.DataFrame:
    """Uma linha por contrato. Colunas: contract_name, point_value_usd, is_micro,
    risk_usd, max_contracts, max_stop_points, n_trades_to_dll, p_blowout.

    risk_usd é igual para todos (depende do saldo, não do ativo); o sizing difere
    pelo point_value. p_blowout só é preenchido se mc_params for fornecido (caro:
    1 Monte Carlo por linha) — caller deve usar n_sims reduzido nesse caso."""
    risk_usd = risk_dollars_per_trade(balance_usd, risk_mode, risk_value)
    rows: list[dict] = []
    for c in contracts:
        symbol = str(c.get("symbol") or c.get("contract_name") or "").strip().upper()
        if not symbol:
            continue
        pv = float(c.get("point_value_usd") or 0)
        is_micro = bool(c.get("is_micro", False))
        cap = contract_cap(account_type, is_micro, max_position_size)
        n = max_contracts(risk_usd, default_stop_points, pv, cap)
        stop = max_stop_points(risk_usd, n, pv) if n > 0 else default_stop_points
        p_blow = None
        if mc_params is not None and n > 0:
            mc = monte_carlo(
                balance_usd=balance_usd,
                mll_threshold_usd=mll_threshold_usd,
                daily_loss_limit_usd=daily_loss_limit_usd,
                risk_usd_per_trade=risk_usd,
                trailing_mode=mc_params.get("trailing_mode", "combine"),
                balance_buffer=mc_params.get("balance_buffer"),
                win_rate=mc_params["win_rate"],
                avg_r=mc_params["avg_r"],
                trades_per_day=mc_params["trades_per_day"],
                horizon_days=mc_params["horizon_days"],
                n_sims=mc_params.get("n_sims", 2000),
                seed=mc_params.get("seed", 42),
            )
            p_blow = mc["p_blowout"]
        rows.append({
            "contract_name": symbol,
            "point_value_usd": pv,
            "is_micro": is_micro,
            "risk_usd": round(risk_usd, 2),
            "max_contracts": n,
            "max_stop_points": round(stop, 4) if stop is not None else None,
            "n_trades_to_dll": trades_to_dll(daily_loss_limit_usd, risk_usd),
            "p_blowout": p_blow,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("max_contracts", ascending=False).reset_index(drop=True)
