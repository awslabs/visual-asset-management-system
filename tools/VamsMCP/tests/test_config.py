"""Tests for vams_mcp.config."""

import pytest

from vams_mcp.config import Config, ConfigError


def _clear_env(monkeypatch):
    for var in (
        "VAMS_PROFILE",
        "VAMS_ENABLE_WRITES",
        "VAMS_ENABLE_DESTRUCTIVE",
        "VAMS_MAX_PAGES",
        "VAMS_PAGE_SIZE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults(monkeypatch):
    _clear_env(monkeypatch)
    cfg = Config.from_env()
    assert cfg.profile is None
    assert cfg.enable_writes is False
    assert cfg.enable_destructive is False
    assert cfg.max_pages == 20
    assert cfg.page_size == 100


def test_writes_enabled(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VAMS_ENABLE_WRITES", "true")
    cfg = Config.from_env()
    assert cfg.enable_writes is True
    # destructive still off unless explicitly enabled
    assert cfg.enable_destructive is False


def test_destructive_requires_writes(monkeypatch):
    _clear_env(monkeypatch)
    # destructive on but writes off -> destructive stays off
    monkeypatch.setenv("VAMS_ENABLE_DESTRUCTIVE", "1")
    assert Config.from_env().enable_destructive is False

    # both on -> destructive enabled
    monkeypatch.setenv("VAMS_ENABLE_WRITES", "1")
    assert Config.from_env().enable_destructive is True


def test_profile_passthrough(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VAMS_PROFILE", "staging")
    assert Config.from_env().profile == "staging"


def test_page_size_clamped(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VAMS_PAGE_SIZE", "999999")
    assert Config.from_env().page_size == 2000


def test_bad_integers_raise(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VAMS_MAX_PAGES", "not-a-number")
    with pytest.raises(ConfigError):
        Config.from_env()
