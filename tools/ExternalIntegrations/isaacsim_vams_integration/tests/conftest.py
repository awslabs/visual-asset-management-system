"""Test fixtures for the Isaac Sim VAMS connector.

The connector package imports the Omniverse Kit modules (``carb``, ``omni.ext``, ``omni.kit.app``,
``omni.ui``), which exist only inside Isaac Sim. Stubs for them are installed before the package is
imported so the pure-Python logic — CLI argument construction, JSON parsing, and the workflow input
gates — is testable in a plain interpreter.
"""

import sys
import types
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

_UI_WIDGETS = (
    "Window", "VStack", "HStack", "ZStack", "Label", "Button", "Rectangle",
    "StringField", "SimpleStringModel", "CollapsableFrame", "ScrollingFrame", "Spacer",
)


def _install_kit_stubs():
    carb = types.ModuleType("carb")
    carb.settings = types.SimpleNamespace(get_settings=lambda: None)

    omni = types.ModuleType("omni")
    omni.__path__ = []
    omni_ext = types.ModuleType("omni.ext")
    omni_ext.IExt = type("IExt", (), {})
    omni_kit = types.ModuleType("omni.kit")
    omni_kit.__path__ = []
    omni_kit_app = types.ModuleType("omni.kit.app")
    omni_kit_app.get_app = lambda: None

    omni_ui = types.ModuleType("omni.ui")
    for widget in _UI_WIDGETS:
        setattr(omni_ui, widget, type(widget, (), {}))
    omni_ui.ScrollBarPolicy = types.SimpleNamespace(
        SCROLLBAR_AS_NEEDED=0, SCROLLBAR_ALWAYS_ON=1)
    omni_ui.DockPreference = types.SimpleNamespace(LEFT_BOTTOM=0)

    omni.ext = omni_ext
    omni.kit = omni_kit
    omni_kit.app = omni_kit_app
    omni.ui = omni_ui

    sys.modules.setdefault("carb", carb)
    for name, module in (
        ("omni", omni), ("omni.ext", omni_ext), ("omni.kit", omni_kit),
        ("omni.kit.app", omni_kit_app), ("omni.ui", omni_ui),
    ):
        sys.modules.setdefault(name, module)


_install_kit_stubs()


@pytest.fixture
def cli_service(monkeypatch):
    """A VamsCliService with the executable check and the auth gate bypassed. Commands are recorded
    on ``service.commands`` and each call returns the next queued JSON payload from
    ``service.responses``."""
    from vams.connector.isaacsim import vams_cli_service as module

    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    service = module.VamsCliService()
    service.commands = []
    service.responses = []

    def _execute(args):
        service.commands.append(list(args))
        return service.responses.pop(0) if service.responses else "{}"

    monkeypatch.setattr(service, "_execute_command", _execute)
    monkeypatch.setattr(service, "ensure_authenticated", lambda: None)
    return service
