"""Tests para src/app_releases.py — catalogo de versoes (M12).

Cobre `_parse_semver`, `compare_versions` (puras) e `get_latest`/`list_releases`
mockando o cliente Supabase via auth.get_client.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


import app_releases


# ---------------------------------------------------------------------------
# _parse_semver
# ---------------------------------------------------------------------------


class ParseSemverTests(unittest.TestCase):

    def test_basic_version(self):
        self.assertEqual(app_releases._parse_semver("1.2.3"), (1, 2, 3))

    def test_strips_v_prefix(self):
        self.assertEqual(app_releases._parse_semver("v0.1.0"), (0, 1, 0))

    def test_strips_whitespace(self):
        self.assertEqual(app_releases._parse_semver("  1.0.0  "), (1, 0, 0))

    def test_returns_none_for_none(self):
        self.assertIsNone(app_releases._parse_semver(None))

    def test_returns_none_for_empty(self):
        self.assertIsNone(app_releases._parse_semver(""))

    def test_returns_none_for_invalid(self):
        # Pre-release tag, dev, sha hash etc. — nao parseiam como semver puro.
        self.assertIsNone(app_releases._parse_semver("1.0.0-rc.1"))
        self.assertIsNone(app_releases._parse_semver("1.0"))
        self.assertIsNone(app_releases._parse_semver("garbage"))
        self.assertIsNone(app_releases._parse_semver("v1"))


# ---------------------------------------------------------------------------
# compare_versions
# ---------------------------------------------------------------------------


class CompareVersionsTests(unittest.TestCase):

    def test_up_to_date_when_equal(self):
        self.assertEqual(
            app_releases.compare_versions("1.0.0", "1.0.0"),
            "up_to_date",
        )

    def test_outdated_when_installed_lower_patch(self):
        self.assertEqual(
            app_releases.compare_versions("1.0.0", "1.0.1"),
            "outdated",
        )

    def test_outdated_when_installed_lower_minor(self):
        self.assertEqual(
            app_releases.compare_versions("1.0.5", "1.1.0"),
            "outdated",
        )

    def test_outdated_when_installed_lower_major(self):
        self.assertEqual(
            app_releases.compare_versions("0.9.9", "1.0.0"),
            "outdated",
        )

    def test_ahead_when_installed_higher(self):
        # Trader rodando build de dev nao publicado
        self.assertEqual(
            app_releases.compare_versions("0.2.0", "0.1.0"),
            "ahead",
        )

    def test_unknown_when_either_invalid(self):
        self.assertEqual(
            app_releases.compare_versions("1.0.0-rc.1", "1.0.0"),
            "unknown",
        )
        self.assertEqual(
            app_releases.compare_versions("1.0.0", "garbage"),
            "unknown",
        )

    def test_unknown_when_either_none(self):
        self.assertEqual(app_releases.compare_versions(None, "1.0.0"), "unknown")
        self.assertEqual(app_releases.compare_versions("1.0.0", None), "unknown")
        self.assertEqual(app_releases.compare_versions(None, None), "unknown")

    def test_v_prefix_ignored_in_comparison(self):
        self.assertEqual(
            app_releases.compare_versions("v1.0.0", "1.0.0"),
            "up_to_date",
        )


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------


def _make_get_latest_chain(row: dict | None) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=[row] if row else [])
    leaf.select.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.limit.return_value = leaf
    return leaf


class GetLatestTests(unittest.TestCase):

    @patch("app_releases.auth")
    def test_returns_row_when_exists(self, mock_auth):
        row = {
            "id": 1, "component": "extension", "version": "0.1.0",
            "is_latest": True, "release_notes": "First version",
            "download_url": "https://example.com/ext.zip",
            "released_at": "2026-05-20T00:00:00+00:00",
        }
        chain = _make_get_latest_chain(row)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = app_releases.get_latest("extension")
        self.assertEqual(result, row)
        # query deve filtrar por component E is_latest=true
        eq_calls = chain.eq.call_args_list
        self.assertEqual(len(eq_calls), 2)
        self.assertEqual(eq_calls[0].args, ("component", "extension"))
        self.assertEqual(eq_calls[1].args, ("is_latest", True))

    @patch("app_releases.auth")
    def test_returns_none_when_no_row(self, mock_auth):
        chain = _make_get_latest_chain(None)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        self.assertIsNone(app_releases.get_latest("extension"))

    @patch("app_releases.auth")
    def test_returns_none_for_invalid_component(self, mock_auth):
        # Componente nao reconhecido — funcao nem tenta roundtrip.
        self.assertIsNone(app_releases.get_latest("bogus"))
        mock_auth.get_client.assert_not_called()

    @patch("app_releases.auth")
    def test_silent_on_exception(self, mock_auth):
        # Tabela ainda nao existir (m12 nao rodada) — devolve None sem propagar.
        client = MagicMock()
        client.table.side_effect = RuntimeError("relation does not exist")
        mock_auth.get_client.return_value = client

        self.assertIsNone(app_releases.get_latest("extension"))

    @patch("app_releases.auth")
    def test_default_component_is_extension(self, mock_auth):
        chain = _make_get_latest_chain(None)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        app_releases.get_latest()  # sem args
        self.assertEqual(chain.eq.call_args_list[0].args, ("component", "extension"))


# ---------------------------------------------------------------------------
# list_releases
# ---------------------------------------------------------------------------


def _make_list_chain(rows: list[dict]) -> MagicMock:
    leaf = MagicMock()
    leaf.execute.return_value = SimpleNamespace(data=rows)
    leaf.select.return_value = leaf
    leaf.eq.return_value = leaf
    leaf.order.return_value = leaf
    leaf.limit.return_value = leaf
    return leaf


class ListReleasesTests(unittest.TestCase):

    @patch("app_releases.auth")
    def test_returns_rows_ordered_desc_by_released_at(self, mock_auth):
        rows = [
            {"id": 2, "component": "extension", "version": "0.1.1"},
            {"id": 1, "component": "extension", "version": "0.1.0"},
        ]
        chain = _make_list_chain(rows)
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        result = app_releases.list_releases("extension")
        self.assertEqual(len(result), 2)
        chain.order.assert_called_once_with("released_at", desc=True)
        chain.limit.assert_called_once_with(10)  # default

    @patch("app_releases.auth")
    def test_custom_limit_passes_through(self, mock_auth):
        chain = _make_list_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        app_releases.list_releases("extension", limit=3)
        chain.limit.assert_called_once_with(3)

    @patch("app_releases.auth")
    def test_returns_empty_for_invalid_component(self, mock_auth):
        result = app_releases.list_releases("bogus")
        self.assertEqual(result, [])
        mock_auth.get_client.assert_not_called()

    @patch("app_releases.auth")
    def test_returns_empty_on_exception(self, mock_auth):
        client = MagicMock()
        client.table.side_effect = RuntimeError("network")
        mock_auth.get_client.return_value = client

        self.assertEqual(app_releases.list_releases("extension"), [])

    @patch("app_releases.auth")
    def test_default_component_is_extension(self, mock_auth):
        chain = _make_list_chain([])
        client = MagicMock()
        client.table.return_value = chain
        mock_auth.get_client.return_value = client

        app_releases.list_releases()  # sem args
        chain.eq.assert_called_once_with("component", "extension")


class ConstantsTests(unittest.TestCase):

    def test_components_match_db_check_constraint(self):
        # public.app_releases tem CHECK constraint nesses 2 valores.
        # Quebrar a lista quebra a UI silenciosamente.
        self.assertEqual(set(app_releases.COMPONENTS), {"extension", "app"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
