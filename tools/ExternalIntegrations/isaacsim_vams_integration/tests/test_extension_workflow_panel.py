"""Tests for the workflow-execute panel's input controls.

The panel offers a run control per selection the workflow can actually accept: a whole-asset run only
when its assetScope admits the asset root, and per-file runs only when it takes input files at all.
Driving `_show_wf_execute` with recording widget stubs asserts which controls are built.
"""

import contextlib

import omni.ui as ui
import pytest

from vams.connector.isaacsim.extension import VamsConnectorExtension
from vams.connector.isaacsim.vams_cli_service import AssetFile, Asset, Database, Workflow


class _Recorder:
    """Collects the labels and buttons a panel builds."""

    def __init__(self):
        self.labels = []
        self.buttons = []


@pytest.fixture
def panel(monkeypatch):
    """A VamsConnectorExtension whose omni.ui calls are recorded rather than drawn."""
    recorder = _Recorder()

    @contextlib.contextmanager
    def _container(*_args, **_kwargs):
        yield

    class _Stack:
        def clear(self):
            recorder.labels.clear()
            recorder.buttons.clear()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(ui, "Label", lambda text="", **_kw: recorder.labels.append(text))
    monkeypatch.setattr(ui, "Button", lambda text="", **_kw: recorder.buttons.append(text))
    monkeypatch.setattr(ui, "HStack", _container)
    monkeypatch.setattr(ui, "VStack", _container)
    monkeypatch.setattr(ui, "Spacer", lambda **_kw: None)

    extension = VamsConnectorExtension()
    extension._init_state()
    extension._wf_stack = _Stack()
    extension._selected_db = Database(database_id="db")
    extension._selected_asset = Asset(asset_id="a1", asset_name="Asset One")
    extension._files = [AssetFile(file_name="a.glb", relative_path="/a.glb", is_folder=False)]
    extension.recorder = recorder
    return extension


def _workflow(arity="one", scope=None):
    system_config = {"inputFileArity": arity}
    if scope is not None:
        system_config["assetScope"] = scope
    return Workflow(workflow_id="wf", workflow_name="WF", database_id="GLOBAL",
                    system_config=system_config)


def test_whole_asset_offered_when_scope_allows(panel):
    panel._show_wf_execute(_workflow(scope={"wholeAssetAllowed": True}))
    assert "  Run on Entire Asset" in panel.recorder.buttons
    assert "Run" in panel.recorder.buttons


def test_whole_asset_hidden_when_scope_refuses(panel):
    panel._show_wf_execute(_workflow(scope={"wholeAssetAllowed": False}))
    assert "  Run on Entire Asset" not in panel.recorder.buttons
    # A per-file run is still valid, and the file list is relabelled accordingly.
    assert "Run" in panel.recorder.buttons
    assert "  Run on a file:" in panel.recorder.labels
    assert any("single file" in label for label in panel.recorder.labels)


def test_whole_asset_offered_when_scope_is_undeclared(panel):
    panel._show_wf_execute(_workflow(scope=None))
    assert "  Run on Entire Asset" in panel.recorder.buttons


def test_arity_none_offers_no_run_control(panel):
    panel._show_wf_execute(_workflow(arity="none", scope={"wholeAssetAllowed": True}))
    assert "  Run on Entire Asset" not in panel.recorder.buttons
    assert "Run" not in panel.recorder.buttons
    assert any("no input files" in label for label in panel.recorder.labels)
    # The back control is always present so the panel is never a dead end.
    assert "  Back to workflow list" in panel.recorder.buttons
