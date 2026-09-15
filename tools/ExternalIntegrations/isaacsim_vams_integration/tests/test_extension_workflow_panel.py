"""Tests for the workflow panel's run controls and its failure reporting.

The panel offers a run control per selection the workflow can actually accept: a whole-asset run only
when its assetScope admits the asset root, per-file runs only when it takes input files at all, and
per-file runs only for files its inputFileFilters admit. Driving `_show_wf_execute` and
`_refresh_wf_panel` with recording widget stubs asserts which controls are built, and — for a listing
that failed — that the reason reaches the panel and the status line instead of being swallowed.
"""

import contextlib

import omni.ui as ui
import pytest

from vams.connector.isaacsim.extension import VamsConnectorExtension
from vams.connector.isaacsim.vams_cli_service import (
    AssetFile, Asset, Database, VamsCliError, Workflow,
)


class _Recorder:
    """Collects the labels and buttons a panel builds."""

    def __init__(self):
        self.labels = []
        self.buttons = []


class _StatusLabel:
    """Stand-in for the status-bar Label, so `_set_status` writes somewhere observable."""

    def __init__(self):
        self.text = ""


class _StubConnector:
    """Returns queued workflow/execution listings, or raises a queued error."""

    def __init__(self):
        self.workflows_by_db = {}
        self.workflow_error = None
        self.executions = []
        self.execution_error = None
        self.include_unrunnable_calls = []

    def list_workflows(self, database_id=None, include_unrunnable=False):
        self.include_unrunnable_calls.append(include_unrunnable)
        if self.workflow_error is not None:
            raise self.workflow_error
        listed = self.workflows_by_db.get(database_id, [])
        return listed if include_unrunnable else [wf for wf in listed if wf.is_runnable]

    def list_workflow_executions(self, database_id, asset_id, *_args, **_kwargs):
        if self.execution_error is not None:
            raise self.execution_error
        return self.executions


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
    extension._status_label = _StatusLabel()
    extension._connector = _StubConnector()
    extension._selected_db = Database(database_id="db")
    extension._selected_asset = Asset(asset_id="a1", asset_name="Asset One")
    extension._files = [AssetFile(file_name="a.glb", relative_path="/a.glb", is_folder=False)]
    extension.recorder = recorder
    return extension


def _workflow(arity="one", scope=None, filters=None, workflow_id="wf", enabled=True):
    system_config = {"inputFileArity": arity}
    if scope is not None:
        system_config["assetScope"] = scope
    if filters is not None:
        system_config["inputFileFilters"] = filters
    return Workflow(workflow_id=workflow_id, workflow_name="WF", database_id="GLOBAL",
                    system_config=system_config, enabled=enabled)


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


def test_whole_asset_hidden_when_scope_is_undeclared(panel):
    """The workflow-level gate reads `wholeAssetAllowed` with a default of False, so a scope that
    declares neither spelling REJECTS a '/' selection. Offering the control would advertise a run
    that fails with "Workflow does not allow whole-asset ('/') selection.".
    """
    panel._show_wf_execute(_workflow(scope=None))
    assert "  Run on Entire Asset" not in panel.recorder.buttons
    assert "Run" in panel.recorder.buttons
    assert "  Run on a file:" in panel.recorder.labels


def test_arity_none_offers_no_run_control(panel):
    panel._show_wf_execute(_workflow(arity="none", scope={"wholeAssetAllowed": True}))
    assert "  Run on Entire Asset" not in panel.recorder.buttons
    assert "Run" not in panel.recorder.buttons
    assert any("no input files" in label for label in panel.recorder.labels)
    # The back control is always present so the panel is never a dead end.
    assert "  Back to workflow list" in panel.recorder.buttons


class TestPerFileRunHonorsInputFileFilters:
    """inputFileFilters is a hard execute-time error, so a rejected file gets no Run button."""

    def test_only_admitted_files_are_listed(self, panel):
        panel._files = [
            AssetFile(file_name="a.glb", relative_path="/a.glb", is_folder=False),
            AssetFile(file_name="notes.txt", relative_path="/notes.txt", is_folder=False),
        ]
        panel._show_wf_execute(_workflow(
            scope={"wholeAssetAllowed": False}, filters={"allow": ["*.glb"], "exclude": []}))
        # The panel strips the leading slash for display; the filter is evaluated against the
        # leading-slash form the execute request sends.
        assert "    a.glb" in panel.recorder.labels
        assert "    notes.txt" not in panel.recorder.labels
        assert any("1 file(s) excluded" in label for label in panel.recorder.labels)

    def test_every_file_listed_when_filters_are_open(self, panel):
        """Control: without this, "notes.txt is absent" would also hold for a panel that lists
        nothing at all, or one whose file loop broke."""
        panel._files = [
            AssetFile(file_name="a.glb", relative_path="/a.glb", is_folder=False),
            AssetFile(file_name="notes.txt", relative_path="/notes.txt", is_folder=False),
        ]
        panel._show_wf_execute(_workflow(scope={"wholeAssetAllowed": False}))
        assert "    a.glb" in panel.recorder.labels
        assert "    notes.txt" in panel.recorder.labels
        assert not any("excluded by" in label for label in panel.recorder.labels)

    def test_no_admitted_file_says_so(self, panel):
        panel._files = [AssetFile(file_name="notes.txt", relative_path="/notes.txt",
                                  is_folder=False)]
        panel._show_wf_execute(_workflow(
            scope={"wholeAssetAllowed": False}, filters={"allow": ["*.glb"]}))
        assert "Run" not in panel.recorder.buttons
        assert any("input-file filters" in label for label in panel.recorder.labels)
        assert "  Back to workflow list" in panel.recorder.buttons


class TestWorkflowListFailuresAreSurfaced:
    """A denied or expired listing must not render as "No workflows found." — the user has no way to
    tell that re-authenticating or a permission change would help."""

    def test_a_listing_error_reaches_the_panel_and_the_status_line(self, panel):
        panel._connector.workflow_error = VamsCliError("401 Unauthorized")
        panel._refresh_wf_panel()
        assert "401" in panel._status_label.text
        assert "  No workflows found." not in panel.recorder.labels
        assert any("401" in label for label in panel.recorder.labels)
        assert any("GLOBAL" in label and "Error" in label for label in panel.recorder.labels)

    def test_an_execution_listing_error_is_surfaced_too(self, panel):
        panel._connector.workflows_by_db = {"GLOBAL": [_workflow()], "db": []}
        panel._connector.execution_error = VamsCliError("403 Forbidden")
        panel._refresh_wf_panel()
        assert "403" in panel._status_label.text
        assert "  No executions." not in panel.recorder.labels
        assert any("403" in label for label in panel.recorder.labels)

    def test_all_disabled_is_distinguished_from_empty(self, panel):
        panel._connector.workflows_by_db = {
            "GLOBAL": [_workflow(workflow_id="off", enabled=False)], "db": []}
        panel._refresh_wf_panel()
        assert panel._workflows == []
        assert "  No workflows found." not in panel.recorder.labels
        assert any("all disabled" in label for label in panel.recorder.labels)
        assert "disabled" in panel._status_label.text

    def test_a_genuinely_empty_database_still_says_no_workflows(self, panel):
        """Control: the three messages above must not have replaced the ordinary empty case."""
        panel._connector.workflows_by_db = {"GLOBAL": [], "db": []}
        panel._refresh_wf_panel()
        assert "  No workflows found." in panel.recorder.labels
        assert "  No executions." in panel.recorder.labels
        assert "error" not in panel._status_label.text

    def test_the_listing_asks_for_disabled_workflows(self, panel):
        """"all disabled" is only distinguishable if the listing kept the disabled rows."""
        panel._connector.workflows_by_db = {"GLOBAL": [_workflow()], "db": []}
        panel._refresh_wf_panel()
        assert panel._connector.include_unrunnable_calls == [True, True]
        assert [wf.workflow_id for wf in panel._workflows] == ["wf"]
