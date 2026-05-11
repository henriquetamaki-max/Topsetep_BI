"""
Coach AI — chama o Claude Code CLI em modo headless (`claude -p`) com um
prompt que sumariza os trades filtrados e pede análise narrativa.

Estratégia:
- Pré-agregamos métricas em Python (rápido, sem token) e passamos o snapshot
  no prompt. Mais barato e mais determinístico que pedir Claude pra consultar
  o Supabase do zero.
- Incluímos um trecho de instruções pro Claude: como interpretar, o que evitar,
  formato de saída em markdown.
- Calculamos um período-baseline (mesma duração imediatamente antes) pra
  comparação de tendência.

`run_claude(prompt)` é blocking — o caller (Streamlit) deve usar `st.spinner`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

import metrics


@dataclass
class FilterContext:
    start: date
    end: date
    contracts: list[str]
    types: list[str]
    weekdays: list[str]
    result_filter: str  # "Todos" | "Só ganhadores" | "Só perdedores"


# ---------------------------------------------------------------------------
# Snapshot / prompt building
# ---------------------------------------------------------------------------


def _summarize(df: pd.DataFrame, groups: pd.DataFrame) -> dict:
    """Resumo numérico do período. Tudo o que vai pro prompt do Claude."""
    if df.empty:
        return {"trades": 0}

    pts_kpis = metrics.compute_kpis(df, groups)
    coach = metrics.compute_coach(df, groups)

    total_pnl = float(df["pnl_net"].sum())
    wins = df[df["pnl_net"] > 0]
    losses = df[df["pnl_net"] <= 0]
    win_rate = len(wins) / len(df) if len(df) else 0.0
    pf = (
        float(wins["pnl_net"].sum() / abs(losses["pnl_net"].sum()))
        if len(losses) and losses["pnl_net"].sum() != 0
        else None
    )

    by_contract = (
        df.groupby("contract_name")["pnl_net"]
        .agg(["sum", "count", "mean"])
        .round(2)
        .to_dict(orient="index")
    )
    by_weekday = (
        df.groupby("weekday")["pnl_net"]
        .agg(["sum", "count"])
        .round(2)
        .to_dict(orient="index")
    )
    by_hour = (
        df.groupby("entry_hour")["pnl_net"]
        .agg(["sum", "count"])
        .round(2)
        .to_dict(orient="index")
    )

    return {
        "trades": int(len(df)),
        "total_pnl_net": round(total_pnl, 2),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "avg_win": round(float(wins["pnl_net"].mean()), 2) if not wins.empty else 0.0,
        "avg_loss": round(float(losses["pnl_net"].mean()), 2) if not losses.empty else 0.0,
        "net_points": round(pts_kpis["total_net_points"], 2),
        "rr_average": round(pts_kpis["rr_average"], 2),
        "rr_aggregate": round(pts_kpis["rr_aggregate"], 2),
        "operations": pts_kpis["total_grouped_operations"],
        "by_contract": by_contract,
        "by_weekday": by_weekday,
        "by_hour": by_hour,
        "behavior": {
            "revenge": coach["revenge"],
            "cut_winners_hold_losers": coach["cut_winners_hold_losers"],
            "overtrading": coach["overtrading"],
            "losing_streak": {
                "length": coach["losing_streak"]["length"],
                "pnl": coach["losing_streak"]["pnl"],
            },
        },
        "leaks": coach["leaks"].to_dict(orient="records") if not coach["leaks"].empty else [],
        "strengths": coach["strengths"].to_dict(orient="records") if not coach["strengths"].empty else [],
        "size_buckets": coach["size_buckets"].to_dict(orient="records") if not coach["size_buckets"].empty else [],
        "checklist_rules": coach["checklist"],
    }


def _baseline_window(df_all: pd.DataFrame, ctx: FilterContext) -> dict | None:
    """Período imediatamente anterior, mesma duração."""
    duration = (ctx.end - ctx.start).days
    base_end = ctx.start - timedelta(days=1)
    base_start = base_end - timedelta(days=duration)
    if base_end < df_all["trade_day"].min():
        return None
    base_start = max(base_start, df_all["trade_day"].min())

    base_df = df_all[
        (df_all["trade_day"] >= base_start)
        & (df_all["trade_day"] <= base_end)
        & (df_all["contract_name"].isin(ctx.contracts))
        & (df_all["type"].isin(ctx.types))
        & (df_all["weekday"].isin(ctx.weekdays))
    ].copy()
    if ctx.result_filter == "Só ganhadores":
        base_df = base_df[base_df["pnl_net"] > 0]
    elif ctx.result_filter == "Só perdedores":
        base_df = base_df[base_df["pnl_net"] <= 0]
    if base_df.empty:
        return None
    _, base_groups = metrics.compute_groups(base_df)
    summary = _summarize(base_df, base_groups)
    summary["range"] = {"start": base_start.isoformat(), "end": base_end.isoformat()}
    return summary


def build_prompt(
    df: pd.DataFrame,
    groups: pd.DataFrame,
    df_all: pd.DataFrame,
    ctx: FilterContext,
) -> str:
    """Monta o prompt completo enviado pro Claude."""
    current = _summarize(df, groups)
    current["range"] = {"start": ctx.start.isoformat(), "end": ctx.end.isoformat()}
    baseline = _baseline_window(df_all, ctx)

    filters_str = (
        f"Período: {ctx.start.isoformat()} a {ctx.end.isoformat()} · "
        f"Contratos: {', '.join(ctx.contracts)} · "
        f"Tipos: {', '.join(ctx.types)} · "
        f"Dias: {', '.join(ctx.weekdays)} · "
        f"Resultado: {ctx.result_filter}"
    )

    payload = {
        "current_period": current,
        "baseline_period": baseline,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    prompt = f"""Você é um coach de trading especializado em day trading de futuros (mini índices, mini SP, etc).

Estou te enviando um snapshot agregado das minhas operações em um período filtrado, junto com um período-baseline imediatamente anterior do mesmo tamanho pra você comparar tendência. Os dados já foram pré-processados — você NÃO precisa consultar nenhum banco. Trabalhe SÓ com o JSON abaixo.

FILTROS APLICADOS: {filters_str}

DADOS (JSON):
```json
{payload_json}
```

Glossário rápido:
- pnl_net: lucro líquido em USD (já descontadas fees + commissions).
- net_points: pontos líquidos do contrato (independente do tamanho).
- rr_average / rr_aggregate: razão risco/retorno em pontos.
- revenge: trades abertos <5min depois de uma perda acima da média.
- cut_winners_hold_losers.ratio: razão entre duração média de losses e wins. >2 é assimétrico.
- overtrading.threshold: p75 de trades/dia; dias acima são "tilt days".
- leaks: combinações (contrato × dia × hora) com PnL negativo acumulado.
- strengths: análogo positivo.
- baseline_period: mesmo conjunto de filtros aplicado ao período anterior de mesma duração (pode ser null se não houver dados anteriores).

ENTREGUE em markdown, em português, com estas 4 seções obrigatórias:

## 1. Resumo executivo
3 a 5 bullets curtos: o que está funcionando, o que está sangrando. Cite números concretos.

## 2. Padrões comportamentais
Comente revenge, cortar/segurar, overtrading e maior losing streak. Para cada um: diga se é problema relevante OU se está sob controle. Se a amostra for pequena demais pra conclusão, diga.

## 3. Comparação com período anterior
Se baseline_period existe: PnL evoluiu? Win rate? Profit factor? Os padrões comportamentais melhoraram ou pioraram? Se não existe, diga "Sem baseline comparável" e siga.

## 4. Checklist acionável para a próxima sessão
3 a 6 regras concretas pra eu aplicar amanhã. Específicas: contrato, horário, dia, condição. Cada regra tem que ser executável ("Não opere MNQ depois das 15h às quartas" — não "tome mais cuidado").

REGRAS:
- Seja direto. Não enrole.
- Não invente padrões que os dados não suportam. Se a amostra é < 30 trades em algum corte, diga.
- Não cite o JSON literalmente; traduza pra linguagem de trader.
- Se total_pnl_net for positivo, reconheça — mas mantenha visão crítica.
"""
    return prompt


# ---------------------------------------------------------------------------
# Claude CLI execution
# ---------------------------------------------------------------------------


def claude_available() -> bool:
    return shutil.which("claude") is not None


def run_claude(prompt: str, timeout_sec: int = 180) -> dict:
    """Executa `claude -p <prompt>` e devolve dict com stdout/stderr/erro.

    O retorno tem sempre as chaves: ok (bool), text (str), error (str|None).
    """
    if not claude_available():
        return {
            "ok": False,
            "text": "",
            "error": "CLI `claude` não está no PATH. Instale Claude Code (https://claude.com/claude-code) e faça login.",
        }

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "text": "",
            "error": f"Timeout após {timeout_sec}s. Tente novamente ou reduza o período filtrado.",
        }
    except Exception as e:
        return {"ok": False, "text": "", "error": f"Falha ao executar claude: {e}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "text": proc.stdout or "",
            "error": (proc.stderr or "").strip() or f"exit code {proc.returncode}",
        }
    return {"ok": True, "text": proc.stdout.strip(), "error": None}
