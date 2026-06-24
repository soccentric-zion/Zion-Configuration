"""Config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, load_settings


def test_defaults_are_complete() -> None:
    # An empty config must still produce a fully-populated, valid Settings.
    s = Settings()
    assert s.server.port == 8000
    assert s.auth.max_failed_logins == 5
    assert s.files.allowed_paths


def test_placeholder_secret_is_replaced() -> None:
    s = Settings.model_validate({"auth": {"jwt_secret": "change-me-to-a-long-random-string"}})
    assert s.auth.jwt_secret != "change-me-to-a-long-random-string"
    assert len(s.auth.jwt_secret) > 20


def test_rejects_bad_port() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"server": {"port": 70000}})


def test_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_settings(bad)


def test_loads_from_file(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 9001\n")
    s = load_settings(cfg)
    assert s.server.port == 9001
