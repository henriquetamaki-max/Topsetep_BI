"""Smoke universal: "config aponta para arquivo que existe".

Pega a classe de bug da "string mentirosa" — um path declarado em config que
não corresponde a nenhum arquivo no disco (passa nos testes de lib, quebra em
runtime). Princípio #3 dos quality gates (~/.claude/rules/quality-gates.md).

Cobre os dois mapeamentos config→arquivo do projeto:
1. `i18n.LANGS` → `locales/<code>.json` (cada idioma declarado tem arquivo).
2. `extension/manifest.json` → arquivos referenciados (popup, service worker,
   content scripts, ícones) existem sob `extension/`.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parent.parent


class LocaleConfigPathsTests(unittest.TestCase):
    """Cada idioma em i18n.LANGS precisa de um locales/<code>.json válido."""

    def test_every_declared_lang_has_existing_json(self):
        missing = []
        for code in i18n.LANGS:
            p = ROOT / "locales" / f"{code}.json"
            if not p.is_file():
                missing.append(str(p.relative_to(ROOT)))
        self.assertEqual(missing, [], f"locales declarados sem arquivo: {missing}")

    def test_every_locale_file_parses(self):
        bad = []
        for code in i18n.LANGS:
            p = ROOT / "locales" / f"{code}.json"
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                bad.append(f"{code}: {e}")
        self.assertEqual(bad, [], f"locales com JSON inválido: {bad}")

    def test_default_lang_is_declared(self):
        self.assertIn(i18n.DEFAULT_LANG, i18n.LANGS)


class ExtensionManifestPathsTests(unittest.TestCase):
    """Arquivos referenciados no manifest.json da extensão existem no disco."""

    EXT = ROOT / "extension"

    def _manifest(self) -> dict:
        return json.loads((self.EXT / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_exists_and_parses(self):
        self.assertTrue((self.EXT / "manifest.json").is_file())
        self._manifest()  # não levanta

    def test_referenced_files_exist(self):
        m = self._manifest()
        refs: list[str] = []

        sw = (m.get("background") or {}).get("service_worker")
        if sw:
            refs.append(sw)

        popup = (m.get("action") or {}).get("default_popup")
        if popup:
            refs.append(popup)

        for cs in m.get("content_scripts", []) or []:
            refs.extend(cs.get("js", []) or [])
            refs.extend(cs.get("css", []) or [])

        icons = m.get("icons") or {}
        refs.extend(icons.values())
        action_icons = (m.get("action") or {}).get("default_icon") or {}
        if isinstance(action_icons, dict):
            refs.extend(action_icons.values())
        elif isinstance(action_icons, str):
            refs.append(action_icons)

        missing = [r for r in refs if not (self.EXT / r).is_file()]
        self.assertEqual(missing, [], f"manifest referencia arquivos inexistentes: {missing}")
        self.assertGreater(len(refs), 0, "manifest sem nenhum arquivo referenciado — suspeito")


if __name__ == "__main__":
    unittest.main(verbosity=2)
