"""Tests para src/coach_ai.py — prompt builder do Coach.

Cobre as funcoes puras (e' a maioria do modulo):
- _lookup: lang-aware i18n sem session_state
- _extract_checklist: parsing de bullets em resposta markdown
- _format_history_block: bloco de avisos anteriores
- save_analysis / fetch_history: CRUD mockando supabase client

_summarize e _baseline_window dependem de DataFrames com schema completo de
trades — cobertos indiretamente via testes do metrics e do app.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import coach_ai  # noqa: E402


# ---------------------------------------------------------------------------
# _lookup
# ---------------------------------------------------------------------------


class LookupTests(unittest.TestCase):

    def setUp(self):
        # Garante cache limpo (outros testes podem ter populado).
        import i18n
        i18n._load_locale.clear()

    def test_returns_key_for_missing_string(self):
        # Chave inexistente em qualquer idioma — devolve a propria.
        self.assertEqual(
            coach_ai._lookup("en", "totally.fake.key"),
            "totally.fake.key",
        )

    def test_returns_string_for_known_key_in_en(self):
        # app.title existe em todos os idiomas — devolve string nao-vazia.
        result = coach_ai._lookup("en", "app.title")
        self.assertIsInstance(result, str)
        self.assertNotEqual(result, "app.title")

    def test_falls_back_to_en_when_lang_missing_key(self):
        # Se algum dia houver chave so em en, pt_BR cai no en. Como hoje a
        # consistencia esta travada por test, simulamos via cache patch.
        import i18n
        i18n._load_locale.clear()
        # Forca cache em ambos os idiomas
        en_bundle = {"new.key": "EN value"}
        pt_bundle = {}  # sem new.key
        with patch.object(coach_ai.i18n, "_load_locale") as mock_load:
            mock_load.side_effect = lambda code: en_bundle if code == "en" else pt_bundle
            result = coach_ai._lookup("pt_BR", "new.key")
            self.assertEqual(result, "EN value")


# ---------------------------------------------------------------------------
# _extract_checklist
# ---------------------------------------------------------------------------


class ExtractChecklistTests(unittest.TestCase):

    def test_captures_section_4_checklist(self):
        text = """\
## 1. Resumo
texto resumo

## 4. Checklist
- Esperar pullback em VWAP
- Stop max 2 pts
- Limite de 3 trades/dia
"""
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(len(bullets), 3)
        self.assertIn("Esperar pullback em VWAP", bullets)
        self.assertIn("Stop max 2 pts", bullets)
        self.assertIn("Limite de 3 trades/dia", bullets)

    def test_captures_section_2_padroes_when_no_checklist(self):
        text = """\
## 1. Resumo
texto

## 2. Padrões
- Overtrading nas terças
- Stop estreito demais
"""
        bullets = coach_ai._extract_checklist(text)
        # Sem secao 4 → cai pra secao 2 "Padroes"
        self.assertEqual(len(bullets), 2)
        self.assertIn("Overtrading nas terças", bullets)

    def test_checklist_takes_priority_over_padroes(self):
        # Se ambas as secoes existem, checklist (prioridade 1) ganha.
        text = """\
## 2. Padrões
- Padrao A
- Padrao B

## 4. Checklist
- Regra 1
- Regra 2
"""
        bullets = coach_ai._extract_checklist(text)
        self.assertIn("Regra 1", bullets)
        self.assertIn("Regra 2", bullets)
        self.assertNotIn("Padrao A", bullets)

    def test_supports_asterisk_and_bullet_chars(self):
        text = """\
## 4. Checklist
* Regra estrela
• Regra bullet
- Regra hifen
"""
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(len(bullets), 3)

    def test_supports_numbered_list(self):
        text = """\
## 4. Checklist
1. Primeira regra
2. Segunda regra
3) Terceira regra
"""
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(len(bullets), 3)
        self.assertIn("Primeira regra", bullets)
        self.assertIn("Terceira regra", bullets)

    def test_fallback_to_any_bullet_when_no_section(self):
        # Resposta sem secao 4 nem 2 — fallback pega qualquer bullet.
        text = """\
Bla bla bla introducao.

- Bullet 1
- Bullet 2
"""
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(len(bullets), 2)

    def test_returns_empty_for_text_without_bullets(self):
        text = "Apenas paragrafo de texto sem nenhuma lista."
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(bullets, [])

    def test_caps_at_10_bullets(self):
        # Mais que 10 bullets — funcao deve cortar.
        text = "## 4. Checklist\n" + "\n".join(f"- regra {i}" for i in range(20))
        bullets = coach_ai._extract_checklist(text)
        self.assertEqual(len(bullets), 10)


# ---------------------------------------------------------------------------
# _format_history_block
# ---------------------------------------------------------------------------


class FormatHistoryBlockTests(unittest.TestCase):

    def test_empty_history_returns_empty_string(self):
        self.assertEqual(coach_ai._format_history_block([]), "")
        self.assertEqual(coach_ai._format_history_block(None), "")

    @patch("coach_ai.i18n")
    def test_includes_header_and_entries(self, mock_i18n):
        mock_i18n.current_lang.return_value = "en"
        # Patcheia o _lookup para retornar templates previsiveis
        mock_i18n._load_locale.return_value = {
            "coach.prompt.history_header": "PREV ANALYSES:",
            "coach.prompt.history_entry": "Entry from {when} ({period}):",
            "coach.prompt.history_no_checklist": "Raw: {text}",
        }
        history = [
            {
                "created_at": "2026-05-15T10:00:00Z",
                "period_start": "2026-05-01",
                "period_end": "2026-05-14",
                "response_text": "## 4. Checklist\n- Wait for VWAP\n- Stop max 2pts",
            },
        ]
        block = coach_ai._format_history_block(history, lang="en")
        self.assertIn("PREV ANALYSES:", block)
        self.assertIn("Entry from 2026-05-15", block)
        # Bullets foram extraidos
        self.assertIn("Wait for VWAP", block)
        self.assertIn("Stop max 2pts", block)

    @patch("coach_ai.i18n")
    def test_falls_back_to_raw_text_when_no_bullets(self, mock_i18n):
        mock_i18n.current_lang.return_value = "en"
        mock_i18n._load_locale.return_value = {
            "coach.prompt.history_header": "PREV:",
            "coach.prompt.history_entry": "Entry {when} ({period}):",
            "coach.prompt.history_no_checklist": "RAW: {text}",
        }
        history = [
            {
                "created_at": "2026-05-15T10:00:00Z",
                "period_start": "2026-05-01",
                "period_end": "2026-05-14",
                "response_text": "Pure prose without any bullets at all.",
            },
        ]
        block = coach_ai._format_history_block(history, lang="en")
        self.assertIn("RAW:", block)
        # Texto bruto truncado em 300 chars (no nosso caso ja' e' curto)
        self.assertIn("Pure prose", block)


# ---------------------------------------------------------------------------
# save_analysis / fetch_history
# ---------------------------------------------------------------------------


class SaveAnalysisTests(unittest.TestCase):

    @patch("coach_ai.auth")
    def test_blocks_empty_text(self, mock_auth):
        ctx = coach_ai.FilterContext(
            start=date(2026, 5, 1), end=date(2026, 5, 20),
            contracts=["MNQ"], types=["Long"], weekdays=["Monday"],
            result_filter="all",
        )
        result = coach_ai.save_analysis(ctx, "")
        self.assertFalse(result["ok"])
        self.assertIn("vazio", result["error"].lower())

    @patch("coach_ai.auth")
    def test_blocks_when_no_user(self, mock_auth):
        mock_auth.current_user_id.return_value = None
        mock_auth.get_client.return_value = MagicMock()
        ctx = coach_ai.FilterContext(
            start=date(2026, 5, 1), end=date(2026, 5, 20),
            contracts=["MNQ"], types=["Long"], weekdays=["Monday"],
            result_filter="all",
        )
        result = coach_ai.save_analysis(ctx, "valid response")
        self.assertFalse(result["ok"])
        self.assertIn("autenticado", result["error"])

    @patch("coach_ai.auth")
    def test_inserts_with_full_payload(self, mock_auth):
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[{"id": 1}])
        chain.insert.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        ctx = coach_ai.FilterContext(
            start=date(2026, 5, 1), end=date(2026, 5, 20),
            contracts=["MNQ", "MES"], types=["Long", "Short"],
            weekdays=["Monday", "Tuesday"], result_filter="winners",
        )
        result = coach_ai.save_analysis(ctx, "  ## 1. Resumo  ")
        self.assertTrue(result["ok"])

        chain.insert.assert_called_once()
        payload = chain.insert.call_args.args[0]
        self.assertEqual(payload["user_id"], "user-abc")
        self.assertEqual(payload["period_start"], "2026-05-01")
        self.assertEqual(payload["period_end"], "2026-05-20")
        self.assertEqual(payload["contracts"], ["MNQ", "MES"])
        self.assertEqual(payload["result_filter"], "winners")
        # Texto e' trimmed
        self.assertEqual(payload["response_text"], "## 1. Resumo")

    @patch("coach_ai.auth")
    def test_returns_error_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")
        mock_auth.get_client.return_value = client
        mock_auth.current_user_id.return_value = "user-abc"

        ctx = coach_ai.FilterContext(
            start=date(2026, 5, 1), end=date(2026, 5, 20),
            contracts=["MNQ"], types=["Long"], weekdays=["Monday"],
            result_filter="all",
        )
        result = coach_ai.save_analysis(ctx, "some response")
        self.assertFalse(result["ok"])
        self.assertIn("db down", result["error"])


class FetchHistoryTests(unittest.TestCase):

    @patch("coach_ai.auth")
    def test_empty_contracts_returns_empty(self, mock_auth):
        # Otimizacao: sem contratos selecionados, evita roundtrip ao banco.
        result = coach_ai.fetch_history([])
        self.assertEqual(result, [])
        mock_auth.get_client.assert_not_called()

    @patch("coach_ai.auth")
    def test_uses_overlaps_filter_on_contracts(self, mock_auth):
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[
            {"created_at": "2026-05-15T10:00:00Z",
             "period_start": "2026-05-01", "period_end": "2026-05-14",
             "contracts": ["MNQ"], "response_text": "..."},
        ])
        chain.select.return_value = chain
        chain.overlaps.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = coach_ai.fetch_history(["MNQ", "MES"], limit=5)
        self.assertEqual(len(result), 1)
        chain.overlaps.assert_called_once_with("contracts", ["MNQ", "MES"])
        chain.limit.assert_called_once_with(5)

    @patch("coach_ai.auth")
    def test_default_limit_is_20(self, mock_auth):
        chain = MagicMock()
        chain.execute.return_value = SimpleNamespace(data=[])
        chain.select.return_value = chain
        chain.overlaps.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        coach_ai.fetch_history(["MNQ"])
        chain.limit.assert_called_once_with(20)

    @patch("coach_ai.auth")
    def test_returns_empty_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("network")
        mock_auth.get_client.return_value = client

        # Sem ruido para o usuario — devolve lista vazia silenciosamente.
        result = coach_ai.fetch_history(["MNQ"])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Legacy aliases (backward compat com modulos pre-refactor)
# ---------------------------------------------------------------------------


class LegacyAliasesTests(unittest.TestCase):

    @patch("coach_ai.auth")
    def test_supabase_alias_delegates(self, mock_auth):
        sentinel = object()
        mock_auth.get_client.return_value = sentinel
        self.assertIs(coach_ai._supabase(), sentinel)

    @patch("coach_ai.auth")
    def test_current_user_id_alias_delegates(self, mock_auth):
        mock_auth.current_user_id.return_value = "user-xyz"
        self.assertEqual(coach_ai._current_user_id(), "user-xyz")


if __name__ == "__main__":
    unittest.main(verbosity=2)
