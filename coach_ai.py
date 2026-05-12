"""
Coach AI — gera um prompt pronto para copiar e colar em qualquer UI de LLM
(Gemini, Perplexity, ChatGPT, Claude.ai). Não executa LLM diretamente.

Estratégia:
- Pré-agregamos métricas em Python (rápido, sem token) e montamos um snapshot
  JSON. O usuário cola o prompt resultante na LLM de preferência.
- Cabeçalho com período (DD/MM/YYYY) e hora local (America/Sao_Paulo) do
  momento de geração, para rastreabilidade.
- Calculamos um período-baseline (mesma duração imediatamente antes) pra
  comparação de tendência.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from supabase import Client

import auth
import metrics

_TZ_SP = ZoneInfo("America/Sao_Paulo")


def _supabase() -> Client:
    """Cliente Supabase com a sessão do usuário logado (RLS ativa)."""
    return auth.get_client()


def _current_user_id() -> str | None:
    user = auth.current_user()
    return user["id"] if user else None


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
    history: list[dict] | None = None,
) -> str:
    """Monta o prompt completo para ser copiado e colado em uma UI de LLM.

    Se `history` for fornecido (lista de dicts com `created_at`, `period_start`,
    `period_end` e `response_text`), inclui uma seção de avisos anteriores
    pedindo que a LLM cobre o trader em caso de reincidência.
    """
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

    now_sp = datetime.now(_TZ_SP)
    header = (
        f"Trades de: {ctx.start.strftime('%d/%m/%Y')} a {ctx.end.strftime('%d/%m/%Y')}\n"
        f"Hora: {now_sp.strftime('%H:%M')}\n\n"
    )

    history_block = _format_history_block(history) if history else ""

    prompt = header + f"""Você é um coach de trading especializado em day trading de futuros (mini índices, mini SP, etc).

Estou te enviando um snapshot agregado das minhas operações em um período filtrado, junto com um período-baseline imediatamente anterior do mesmo tamanho pra você comparar tendência. Os dados já foram pré-processados — você NÃO precisa consultar nenhum banco. Trabalhe SÓ com o JSON abaixo.

FILTROS APLICADOS: {filters_str}
{history_block}

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
- Se a seção AVISOS ANTERIORES existir e o trader estiver repetindo um padrão já apontado, seja MAIS DURO: cite explicitamente "isso você já foi avisado em [data]", cobre por que não foi corrigido, e exija ação no checklist da próxima sessão.
"""
    return prompt


# ---------------------------------------------------------------------------
# Histórico de análises (Supabase)
# ---------------------------------------------------------------------------


def _extract_checklist(response_text: str) -> list[str]:
    """Tenta extrair bullets das seções 4 (checklist) e 2 (padrões).

    Estratégia: procura headers `## 4` ou `## 2`, lê linhas até o próximo
    `##` ou fim, e captura linhas começando com `-` ou `*` ou `1.`.
    Fallback: bullets em qualquer lugar do texto.
    """
    bullets: list[str] = []
    lines = response_text.splitlines()
    capture_priority: list[tuple[int, int, int]] = []  # (prioridade, idx_ini, idx_fim)

    section_start = None
    section_priority = 99
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("## "):
            if section_start is not None:
                capture_priority.append((section_priority, section_start, i))
            lower = stripped.lower()
            if "checklist" in lower or stripped.startswith("## 4"):
                section_priority = 1
                section_start = i + 1
            elif "padr" in lower or stripped.startswith("## 2"):
                section_priority = 2
                section_start = i + 1
            else:
                section_start = None
    if section_start is not None:
        capture_priority.append((section_priority, section_start, len(lines)))

    capture_priority.sort(key=lambda t: t[0])
    for _, ini, fim in capture_priority:
        for raw in lines[ini:fim]:
            s = raw.strip()
            if s.startswith(("-", "*", "•")):
                bullets.append(s.lstrip("-*• ").strip())
            elif len(s) > 2 and s[0].isdigit() and s[1] in (".", ")"):
                bullets.append(s[2:].strip())
        if bullets:
            break

    if not bullets:
        for raw in lines:
            s = raw.strip()
            if s.startswith(("-", "*", "•")):
                bullets.append(s.lstrip("-*• ").strip())

    return [b for b in bullets if b][:10]


def _format_history_block(history: list[dict]) -> str:
    """Monta o bloco 'AVISOS ANTERIORES' a ser injetado no prompt."""
    if not history:
        return ""
    lines = [
        "",
        "AVISOS ANTERIORES (análises passadas sobre estes mesmos contratos):",
    ]
    for h in history:
        when = h.get("created_at", "")
        if isinstance(when, str) and len(when) >= 10:
            when_fmt = when[:10]
        else:
            when_fmt = str(when)
        period = f"{h.get('period_start', '?')} a {h.get('period_end', '?')}"
        bullets = _extract_checklist(h.get("response_text", "") or "")
        lines.append(f"\n[{when_fmt} · período analisado: {period}]")
        if bullets:
            for b in bullets:
                lines.append(f"- {b}")
        else:
            text = (h.get("response_text") or "").strip().replace("\n", " ")
            lines.append(f"(sem checklist extraível) {text[:300]}")
    return "\n".join(lines) + "\n"


def fetch_history(contracts: list[str], limit: int = 20) -> list[dict]:
    """Busca análises anteriores cujos contratos se sobrepõem aos atuais.

    Retorna lista (mais recente primeiro) de dicts com:
    created_at, period_start, period_end, contracts, response_text.
    Em caso de erro de conexão, retorna [].
    """
    if not contracts:
        return []
    try:
        client = _supabase()
        r = (
            client.table("coach_analyses")
            .select("created_at,period_start,period_end,contracts,response_text")
            .overlaps("contracts", contracts)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


def save_analysis(ctx: FilterContext, response_text: str) -> dict:
    """Insere uma análise da LLM no Supabase. Retorna {ok, error}."""
    text = (response_text or "").strip()
    if not text:
        return {"ok": False, "error": "Texto vazio."}
    user_id = _current_user_id()
    if not user_id:
        return {"ok": False, "error": "Usuário não autenticado."}
    try:
        client = _supabase()
        client.table("coach_analyses").insert(
            {
                "user_id": user_id,
                "period_start": ctx.start.isoformat(),
                "period_end": ctx.end.isoformat(),
                "contracts": ctx.contracts,
                "types": ctx.types,
                "weekdays": ctx.weekdays,
                "result_filter": ctx.result_filter,
                "response_text": text,
            }
        ).execute()
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}
