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
    "StringField", "SimpleStringModel", "CollapsableFrame", "ScrollingFrame", "Spacer", "ComboBox",
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
def pxr(monkeypatch):
    """A stub ``pxr`` whose exported layer holds the asset paths in ``state.asset_paths``.

    ``UsdUtils.ModifyAssetPaths`` replaces each path with what the callback returns. It, and the
    layer's ``Reload`` and ``Save``, append ``("remap", path)``, ``("reload", path, force)`` and
    ``("save", path)`` to ``state.events`` — point that at a fake CLI's call list to see them in
    order with the upload. ``state.saved_paths`` holds the paths at the last successful save;
    ``open_ok``/``reload_ok``/``save_ok`` make reopening, rereading or saving the export fail.

    ``state.layers`` describes the layers a reference opens, by file name: ``(default prim, root
    prim names, sublayer paths)``, or None for a file that does not open as a layer. A file it does
    not name opens with no default prim and no root prims."""
    state = types.SimpleNamespace(asset_paths=[], saved_paths=None, events=[], layers={},
                                  open_ok=True, reload_ok=True, save_ok=True)

    class _Layer:
        def __init__(self, path):
            self.path = self.identifier = path
            self.defaultPrim, roots, sublayers = state.layers.get(Path(path).name, ("", [], []))
            self.rootPrims = [types.SimpleNamespace(name=name) for name in roots]
            self.subLayerPaths = list(sublayers)

        def Reload(self, force=False):
            state.events.append(("reload", self.path, force))
            return state.reload_ok

        def Save(self):
            state.events.append(("save", self.path))
            if state.save_ok:
                state.saved_paths = list(state.asset_paths)
            return state.save_ok

    def _modify_asset_paths(layer, modify_fn):
        state.events.append(("remap", layer.path))
        state.asset_paths = [modify_fn(path) for path in state.asset_paths]

    def _find_or_open(path):
        if not state.open_ok or state.layers.get(Path(path).name, ()) is None:
            return None
        return _Layer(path)

    module = types.ModuleType("pxr")
    module.Sdf = types.SimpleNamespace(
        Layer=types.SimpleNamespace(FindOrOpen=_find_or_open),
        Path=lambda path: path,
        ComputeAssetPathRelativeToLayer=lambda _anchor, path: path,
    )
    module.UsdUtils = types.SimpleNamespace(ModifyAssetPaths=_modify_asset_paths)
    monkeypatch.setitem(sys.modules, "pxr", module)
    return state


@pytest.fixture
def cli_service(monkeypatch):
    """A VamsCliService with the executable check and the auth gate bypassed. Commands are recorded
    on ``service.commands``, any stdin payload on ``service.stdin_payloads``, and each call returns
    the next queued JSON payload from ``service.responses``.

    ``service.commands`` records the arguments AFTER the ``[vamscli, --profile, X]`` prefix, which is
    assembled inside the real ``_execute_command``. Assertions about that prefix, or about a
    credential reaching stdin, belong in the suites that patch ``subprocess.run`` instead."""
    from vams.connector.isaacsim import vams_cli_service as module

    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    service = module.VamsCliService()
    service.commands = []
    service.responses = []
    service.stdin_payloads = []

    def _execute(args, stdin_payload=None):
        service.commands.append(list(args))
        service.stdin_payloads.append(stdin_payload)
        return service.responses.pop(0) if service.responses else "{}"

    monkeypatch.setattr(service, "_execute_command", _execute)
    monkeypatch.setattr(service, "ensure_authenticated", lambda: None)
    return service
