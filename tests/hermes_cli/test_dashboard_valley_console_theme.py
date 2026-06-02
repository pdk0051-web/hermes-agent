"""Static checks for the Valley-inspired dashboard default theme."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_default_theme_uses_valley_console_contract() -> None:
    presets = (ROOT / "web/src/themes/presets.ts").read_text()

    assert 'name: "default"' in presets
    assert 'label: "Valley Console"' in presets
    assert "#fcfcfd" in presets
    assert "#ffffff" in presets
    assert "#58bf67" in presets
    assert "#2f9c46" in presets
    assert "Pretendard Variable" in presets
    assert "componentStyles" in presets
    assert "customCSS" in presets


def test_backend_builtin_theme_metadata_matches_valley_console() -> None:
    server = (ROOT / "hermes_cli/web_server.py").read_text()

    assert '{"name": "default",       "label": "Valley Console"' in server
    assert "Light Valley-inspired operator console" in server
