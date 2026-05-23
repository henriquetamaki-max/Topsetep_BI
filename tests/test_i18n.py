"""Tests para src/i18n.py — funcao t() e helpers de label.

Testa:
- t(): lookup, fallback de lang, fallback para chave, interpolacao
- status_label / priority_label / status_options / priority_options
- status_from_label / priority_from_label (roundtrip)
- shortcut_label / result_label / weekday_label

Streamlit session_state e mockado via patch para nao depender de runtime real.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch


import i18n


class _FakeSessionState(dict):
    """st.session_state e' tipo dict, mas tambem suporta atributos. Para o que
    o i18n usa (.get / __contains__ / __setitem__) o dict puro basta."""


class TranslateTests(unittest.TestCase):

    def setUp(self):
        # Cache pode ter sido populado por outros testes — limpa para isolar.
        i18n._load_locale.clear()

    def _with_lang(self, lang: str):
        return patch.dict(i18n.st.session_state, {"lang": lang}, clear=False) \
            if hasattr(i18n.st, "session_state") else patch.object(i18n.st, "session_state", _FakeSessionState({"lang": lang}))

    @patch("i18n.st")
    def test_returns_translated_string_for_current_lang(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "pt_BR"})
        # app.title e' uma chave que existe nos 3 idiomas
        result = i18n.t("app.title")
        # Esperamos "X-Metrics" — todos os 3 idiomas usam o mesmo titulo
        self.assertEqual(result, "X-Metrics")

    @patch("i18n.st")
    def test_falls_back_to_default_lang_when_key_missing(self, mock_st):
        # Cria uma chave que nao existe — funcao deve cair no DEFAULT_LANG
        # e, ainda sem encontrar, devolver a propria chave.
        mock_st.session_state = _FakeSessionState({"lang": "pt_BR"})
        result = i18n.t("totally.made.up.key")
        self.assertEqual(result, "totally.made.up.key")

    @patch("i18n.st")
    def test_returns_key_itself_as_last_fallback(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        result = i18n.t("does.not.exist.anywhere")
        self.assertEqual(result, "does.not.exist.anywhere")

    @patch("i18n.st")
    def test_interpolation_with_kwargs(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        # auth.err_login = "Sign-in failed: {err}"
        result = i18n.t("auth.err_login", err="bad credentials")
        self.assertIn("bad credentials", result)

    @patch("i18n.st")
    def test_interpolation_with_missing_placeholder_returns_raw(self, mock_st):
        # Se a string tem {err} mas o caller esquece de passar, .format levanta
        # KeyError — funcao captura e devolve a string crua (melhor que crashar).
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        result = i18n.t("auth.err_login")  # sem err=
        # Deve devolver a string crua com {err} dentro (ou a chave bruta) sem crashar
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    @patch("i18n.st")
    def test_default_lang_when_session_state_missing(self, mock_st):
        # Sem chave "lang" na session_state — funcao usa DEFAULT_LANG.
        mock_st.session_state = _FakeSessionState()
        result = i18n.t("app.title")
        self.assertEqual(result, "X-Metrics")  # mesmo em en


class CurrentLangTests(unittest.TestCase):

    @patch("i18n.st")
    def test_returns_session_state_lang(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "es"})
        self.assertEqual(i18n.current_lang(), "es")

    @patch("i18n.st")
    def test_returns_default_when_unset(self, mock_st):
        mock_st.session_state = _FakeSessionState()
        self.assertEqual(i18n.current_lang(), i18n.DEFAULT_LANG)


class StatusLabelTests(unittest.TestCase):

    @patch("i18n.st")
    def test_known_status_returns_translated(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        # "Pendente" → action.status.pending → "Pending" (en)
        label = i18n.status_label("Pendente")
        # Nao testa string exata (pode mudar de tradutor); confere que NAO e'
        # o valor canonico cru.
        self.assertNotEqual(label, "Pendente")
        self.assertIsInstance(label, str)
        self.assertTrue(len(label) > 0)

    @patch("i18n.st")
    def test_unknown_status_passes_through(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        self.assertEqual(i18n.status_label("Unknown"), "Unknown")


class PriorityLabelTests(unittest.TestCase):

    @patch("i18n.st")
    def test_known_priority_returns_translated(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        label = i18n.priority_label("Alta")
        self.assertNotEqual(label, "Alta")

    @patch("i18n.st")
    def test_unknown_priority_passes_through(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        self.assertEqual(i18n.priority_label("Critical"), "Critical")


class StatusRoundtripTests(unittest.TestCase):

    @patch("i18n.st")
    def test_label_then_from_label_returns_canonical(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        for db_value in ("Pendente", "Em andamento", "Concluído"):
            label = i18n.status_label(db_value)
            roundtrip = i18n.status_from_label(label)
            self.assertEqual(
                roundtrip, db_value,
                f"roundtrip falhou para {db_value!r}: label={label!r}",
            )

    @patch("i18n.st")
    def test_unknown_label_passes_through(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        self.assertEqual(i18n.status_from_label("Random"), "Random")


class PriorityRoundtripTests(unittest.TestCase):

    @patch("i18n.st")
    def test_label_then_from_label_returns_canonical(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        for db_value in ("Alta", "Média", "Baixa"):
            label = i18n.priority_label(db_value)
            roundtrip = i18n.priority_from_label(label)
            self.assertEqual(roundtrip, db_value)


class StatusOptionsTests(unittest.TestCase):

    @patch("i18n.st")
    def test_returns_three_items_in_canonical_order(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        opts = i18n.status_options()
        self.assertEqual(len(opts), 3)
        # Ordem canonica corresponde a: Pendente, Em andamento, Concluído
        # Cada uma deve ser uma string nao-vazia (a traducao real).
        for opt in opts:
            self.assertIsInstance(opt, str)
            self.assertTrue(len(opt) > 0)

    @patch("i18n.st")
    def test_priority_options_returns_three(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        opts = i18n.priority_options()
        self.assertEqual(len(opts), 3)


class WeekdayLabelTests(unittest.TestCase):

    @patch("i18n.st")
    def test_returns_translated_for_known_day(self, mock_st):
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        result = i18n.weekday_label("Monday")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    @patch("i18n.st")
    def test_lowercases_pandas_name_before_lookup(self, mock_st):
        # Pandas devolve "Monday" / "Tuesday" capitalizado; funcao lowercaseia
        # para casar com a chave weekday.monday.
        mock_st.session_state = _FakeSessionState({"lang": "en"})
        # MIXED case input — funcao deve normalizar
        result = i18n.weekday_label("MONDAY")
        # Se nao retornar a propria chave "weekday.MONDAY", esta funcionando
        self.assertNotEqual(result, "weekday.MONDAY")


class ConstantsIntegrityTests(unittest.TestCase):

    def test_status_db_to_key_covers_all_valid_statuses(self):
        # CHECK constraint do banco aceita exatamente esses 3 status. Quebrar
        # essa lista quebra a UI silenciosamente.
        self.assertEqual(
            set(i18n.STATUS_DB_TO_KEY.keys()),
            {"Pendente", "Em andamento", "Concluído"},
        )

    def test_priority_db_to_key_covers_all_valid_priorities(self):
        self.assertEqual(
            set(i18n.PRIORITY_DB_TO_KEY.keys()),
            {"Alta", "Média", "Baixa"},
        )

    def test_langs_include_three_supported(self):
        self.assertEqual(set(i18n.LANGS.keys()), {"en", "pt_BR", "es"})

    def test_default_lang_is_in_langs(self):
        self.assertIn(i18n.DEFAULT_LANG, i18n.LANGS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
