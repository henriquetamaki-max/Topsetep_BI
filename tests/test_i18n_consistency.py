"""Garante que os 3 idiomas (en, pt_BR, es) tenham o mesmo conjunto de chaves.

Cobre dois universos i18n:
- `locales/<lang>.json` — strings do app Streamlit (acessadas via `i18n.t`).
- `extension/_locales/<lang>/messages.json` — strings do popup da extensao
  Chrome (acessadas via `chrome.i18n.getMessage`).

Falhar este teste = uma chave foi adicionada/renomeada em um idioma e esquecida
nos outros. CLAUDE.md exige os 3 idiomas sincronizados.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_LOCALES_DIR = ROOT / "locales"
EXT_LOCALES_DIR = ROOT / "extension" / "_locales"

LANGS_APP = ("en", "pt_BR", "es")
LANGS_EXT = ("en", "pt_BR", "es")


class TestAppLocalesConsistency(unittest.TestCase):
    """locales/<lang>.json sao dicts flat (chaves literais com pontos, ex.
    'app.title'). Os 3 idiomas devem ter o mesmo conjunto de chaves."""

    def setUp(self) -> None:
        self.bundles = {
            lang: json.loads((APP_LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in LANGS_APP
        }

    def test_all_locales_have_same_keys(self) -> None:
        keys_by_lang = {lang: set(b.keys()) for lang, b in self.bundles.items()}
        baseline = keys_by_lang["en"]
        for lang in ("pt_BR", "es"):
            missing = baseline - keys_by_lang[lang]
            extra = keys_by_lang[lang] - baseline
            self.assertFalse(
                missing,
                f"locales/{lang}.json faltam chaves: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}",
            )
            self.assertFalse(
                extra,
                f"locales/{lang}.json tem chaves extras (nao existem no en.json): {sorted(extra)[:10]}{'...' if len(extra) > 10 else ''}",
            )

    def test_no_empty_translations(self) -> None:
        """Valor vazio indica traducao esquecida."""
        for lang, bundle in self.bundles.items():
            for key, value in bundle.items():
                self.assertTrue(
                    isinstance(value, str) and value.strip(),
                    f"locales/{lang}.json: chave '{key}' tem valor vazio",
                )


class TestExtensionLocalesConsistency(unittest.TestCase):
    """extension/_locales/<lang>/messages.json devem ter o mesmo conjunto de chaves."""

    def setUp(self) -> None:
        self.bundles = {
            lang: json.loads((EXT_LOCALES_DIR / lang / "messages.json").read_text(encoding="utf-8"))
            for lang in LANGS_EXT
        }

    def test_all_locales_have_same_keys(self) -> None:
        keys_by_lang = {lang: set(b.keys()) for lang, b in self.bundles.items()}
        baseline = keys_by_lang["en"]
        for lang in ("pt_BR", "es"):
            missing = baseline - keys_by_lang[lang]
            extra = keys_by_lang[lang] - baseline
            self.assertFalse(
                missing,
                f"extension/_locales/{lang}/messages.json faltam chaves: {sorted(missing)}",
            )
            self.assertFalse(
                extra,
                f"extension/_locales/{lang}/messages.json tem chaves extras: {sorted(extra)}",
            )

    def test_all_messages_have_text(self) -> None:
        for lang, bundle in self.bundles.items():
            for key, entry in bundle.items():
                self.assertIsInstance(
                    entry, dict,
                    f"extension/_locales/{lang}: chave '{key}' nao e dict",
                )
                self.assertIn(
                    "message", entry,
                    f"extension/_locales/{lang}: chave '{key}' sem campo 'message'",
                )
                self.assertTrue(
                    isinstance(entry["message"], str) and entry["message"].strip(),
                    f"extension/_locales/{lang}: chave '{key}' tem message vazio",
                )

    def test_placeholders_aligned_across_langs(self) -> None:
        """Quando uma chave usa $VAR$ ou placeholders, todos os idiomas precisam
        ter o mesmo conjunto de placeholders. Cobre o caso de $EMAIL$ aparecer
        em en mas o tradutor esquecer em pt_BR."""
        baseline = self.bundles["en"]
        for key, entry in baseline.items():
            en_phs = set((entry.get("placeholders") or {}).keys())
            for lang in ("pt_BR", "es"):
                other = self.bundles[lang].get(key, {})
                other_phs = set((other.get("placeholders") or {}).keys())
                self.assertEqual(
                    en_phs, other_phs,
                    f"extension/_locales/{lang}: chave '{key}' placeholders divergentes "
                    f"de en (en={sorted(en_phs)}, {lang}={sorted(other_phs)})",
                )


if __name__ == "__main__":
    unittest.main()
