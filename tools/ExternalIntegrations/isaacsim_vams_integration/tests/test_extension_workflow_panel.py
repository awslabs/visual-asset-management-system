"""Tests for the workflow panel's run controls and its failure reporting.

The panel offers a run control per selection the workflow can actually accept: a whole-asset run only
when its assetScope admits the asset root, per-file runs only when it takes input files at all, and
per-file runs only for files its inputFileFilters and those of its first pipeline admit (a later
pipeline also receives the earlier pipelines' outputs, and the service checks it when the run
reaches it). A pipeline that requires a template and has no default gets a template choice, which
Run passes to the CLI. Driving
`_show_wf_execute` and `_refresh_wf_panel` with recording widget stubs asserts which controls are
built, and — for a listing that failed — that the reason reaches the panel and the status line instead
of being swallowed.
"""

import contextlib
import json
import types

import omni.ui as ui
import pytest

from vams.connector.isaacsim import extension as extension_module
from vams.connector.isaacsim.connector import IsaacVAMSConnector
from vams.connector.isaacsim.extension import (
    _WF_TEXT_CHARS, VamsConnectorExtension, _workflow_button,
)
from vams.connector.isaacsim.vams_cli_service import (
    AssetFile, Asset, Database, Pipeline, PipelineTemplate, VamsCliError, Workflow,
    WorkflowExecution,
)


class _Recorder:
    """Collects the labels, buttons and template choices a panel builds, and each button's click."""

    def __init__(self):
        self.labels = []
        # Each label as (text, keyword arguments).
        self.label_args = []
        self.buttons = []
        self.clicks = []
        self.combos = []

    def click(self, text, index=0):
        return [fn for label, fn in self.clicks if label == text][index]()


class _Combo:
    """Stand-in for ``ui.ComboBox``; ``choose`` fires its value-changed callbacks."""

    def __init__(self, selected, *items):
        self.selected = selected
        self.items = list(items)
        self._changed = []
        value = types.SimpleNamespace(add_value_changed_fn=self._changed.append)
        self.model = types.SimpleNamespace(get_item_value_model=lambda: value)

    def choose(self, index):
        for fn in self._changed:
            fn(types.SimpleNamespace(as_int=index))


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
        # (database ID, pipeline ID) -> a Pipeline / list of templates, or an error to raise.
        self.pipelines = {}
        self.templates = {}
        self.reads = []
        self.executed = []
        self.execute_error = None

    def get_pipeline(self, database_id, pipeline_id):
        self.reads.append(("pipeline", database_id, pipeline_id))
        return self._answer(self.pipelines[(database_id, pipeline_id)])

    def list_pipeline_templates(self, database_id, pipeline_id):
        self.reads.append(("templates", database_id, pipeline_id))
        return self._answer(self.templates.get((database_id, pipeline_id), []))

    def execute_workflow(self, **kwargs):
        self.executed.append(kwargs)
        if self.execute_error is not None:
            raise self.execute_error
        return {"executionId": "e1"}

    @staticmethod
    def _answer(value):
        if isinstance(value, Exception):
            raise value
        return value

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
            recorder.label_args.clear()
            recorder.buttons.clear()
            recorder.clicks.clear()
            recorder.combos.clear()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _button(text="", **kwargs):
        recorder.buttons.append(text)
        recorder.clicks.append((text, kwargs.get("clicked_fn")))

    def _combo(selected, *items, **_kwargs):
        recorder.combos.append(_Combo(selected, *items))
        return recorder.combos[-1]

    def _label(text="", **kwargs):
        recorder.labels.append(text)
        recorder.label_args.append((text, kwargs))

    monkeypatch.setattr(extension_module, "_defer", lambda fn: fn())
    monkeypatch.setattr(ui, "Label", _label)
    monkeypatch.setattr(ui, "Button", _button)
    monkeypatch.setattr(ui, "ComboBox", _combo)
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


STL, USD, JPG = ("/models/robot.stl", "/GingerbreadHouse.usda", "/textures/color.jpg")
NEEDS_TEMPLATE = "  Template for 3D Basic Conversion:"


def _ref(pipeline_id="p1", default_template=""):
    return {"pipelineId": pipeline_id, "pipelineDatabaseId": "GLOBAL", "jobName": pipeline_id,
            "defaultTemplateId": default_template}


def _pipeline(pipeline_id="p1", allow=None, require_template=False, name="3D Basic Conversion",
              arity="one"):
    system_config = {"inputFileArity": arity, "requireTemplate": require_template}
    if allow is not None:
        system_config["inputFileFilters"] = {"allow": allow, "exclude": []}
    return Pipeline(pipeline_id=pipeline_id, database_id="GLOBAL", pipeline_name=name,
                    system_config=system_config)


def _template(template_id, name, default=False, allow=None):
    overrides = {"inputFileFilters": {"allow": allow}} if allow is not None else {}
    return PipelineTemplate(template_id=template_id, template_name=name, is_default=default,
                            overrides=overrides)


CONVERT = [_template("convert-to-gltf", "Convert to glTF"), _template("convert-to-glb", "Convert to GLB")]


def _chain(*refs, whole_asset=False):
    system_config = {"inputFileArity": "one", "inputFileFilters": {"allow": [], "exclude": []}}
    if whole_asset:
        system_config["assetScope"] = {"wholeAssetAllowed": True}
    return Workflow(workflow_id="conversion-3d-basic", workflow_name="3D Basic Conversion",
                    database_id="GLOBAL", specified_pipelines=list(refs),
                    system_config=system_config)


PIPELINE_FILTERS = "the input-file filters of this workflow and its first pipeline"
OBJ = "/models/robot.obj"


@pytest.fixture
def chain_panel(panel):
    panel._files = [AssetFile(relative_path=key) for key in (STL, USD, JPG)]
    return panel


def _listed(panel):
    return [label.strip() for label in panel.recorder.labels
            if label.strip() in {key.strip("/") for key in (STL, USD, JPG)}]


class TestThePipelinesFiltersGateRun:
    """The service judges the FIRST pipeline against the workflow-admitted selection, with that
    pipeline's own effective inputFileFilters, and refuses a run that leaves it with none, so an
    open workflow filter does not make a file runnable. A later pipeline receives the selection plus
    every file the earlier pipelines wrote and is checked when the run reaches it, so its filters do
    not gate Run: a pipeline that reads only the type the first one writes is a valid chain."""

    def test_only_files_the_pipeline_accepts_are_runnable(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl", "*.obj"])
        chain_panel._show_wf_execute(_chain(_ref()))
        assert _listed(chain_panel) == ["models/robot.stl"]
        assert chain_panel.recorder.buttons.count("Run") == 1
        assert f"    2 file(s) excluded by {PIPELINE_FILTERS}." in chain_panel.recorder.labels

    def test_no_file_the_pipeline_accepts_says_so(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.glb"])
        chain_panel._show_wf_execute(_chain(_ref()))
        assert "Run" not in chain_panel.recorder.buttons
        assert f"  No file in this asset passes {PIPELINE_FILTERS}." in chain_panel.recorder.labels

    def test_the_filters_of_the_template_in_use_apply(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(
            allow=["*.stl"], require_template=True)
        chain_panel._connector.templates[("GLOBAL", "p1")] = [
            _template("usd-in", "USD in", allow=["*.usda"])]
        chain_panel._show_wf_execute(_chain(_ref(default_template="usd-in")))
        assert _listed(chain_panel) == ["GingerbreadHouse.usda"]

    def test_a_chosen_templates_filters_apply_once_it_is_chosen(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(
            allow=["*.stl"], require_template=True)
        chain_panel._connector.templates[("GLOBAL", "p1")] = [
            _template("usd-in", "USD in", allow=["*.usda"])]
        chain_panel._show_wf_execute(_chain(_ref()))
        assert _listed(chain_panel) == ["models/robot.stl"]
        chain_panel.recorder.combos[0].choose(1)
        assert _listed(chain_panel) == ["GingerbreadHouse.usda"]

    def test_a_later_pipelines_filters_do_not_gate(self, chain_panel):
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl", "*.usda"])
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.stl"], name="Thumbnail")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda"]

    def test_a_later_pipeline_reading_only_the_first_ones_output_type_offers_run(self,
                                                                                 chain_panel):
        """The OBJ-to-GLB chain: the second pipeline reads only the GLB the first one writes, so
        the asset's OBJ is runnable although no file of the asset passes the second pipeline's
        filters."""
        connector = chain_panel._connector
        chain_panel._files = [AssetFile(relative_path=key) for key in (OBJ, JPG)]
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.obj"], name="OBJ to GLB")
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.glb"], name="Thumbnail")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert "    models/robot.obj" in chain_panel.recorder.labels
        assert chain_panel.recorder.buttons.count("Run") == 1
        assert f"    1 file(s) excluded by {PIPELINE_FILTERS}." in chain_panel.recorder.labels
        chain_panel.recorder.click("Run")
        [run] = connector.executed
        assert run["file_key"] == "models/robot.obj"

    def test_the_first_pipelines_filters_still_gate_a_chain(self, chain_panel):
        """Control: the same two pipelines in the other order refuse the OBJ, as the service
        does."""
        connector = chain_panel._connector
        chain_panel._files = [AssetFile(relative_path=key) for key in (OBJ, JPG)]
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.glb"], name="Thumbnail")
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.obj"], name="OBJ to GLB")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert "Run" not in chain_panel.recorder.buttons
        assert f"  No file in this asset passes {PIPELINE_FILTERS}." in chain_panel.recorder.labels

    def test_a_first_pipeline_that_takes_no_file_leaves_the_workflows_filters(self, chain_panel):
        """An arity-'none' first pipeline is still the first: it writes files a later one reads, so
        the later pipeline's filters do not gate Run either."""
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(arity="none", name="Generate")
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.glb"], name="Thumbnail")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda",
                                        "textures/color.jpg"]

    def test_a_later_pipeline_that_takes_no_file_does_not_gate(self, chain_panel):
        """Control: a pipeline of arity 'none' is given no files, so its filters refuse none."""
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl", "*.usda"])
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.glb"], arity="none",
                                                         name="Report")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda"]

    def test_a_later_pipelines_template_in_use_does_not_gate_either(self, chain_panel):
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl", "*.usda"])
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.stl"], name="Thumbnail")
        connector.templates[("GLOBAL", "p2")] = [_template("usd-in", "USD in", allow=["*.usda"])]
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2", default_template="usd-in")))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda"]

    def test_a_later_pipeline_of_a_chain_that_cannot_be_read_says_so(self, chain_panel):
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl"])
        connector.pipelines[("GLOBAL", "p2")] = VamsCliError("403 Forbidden")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert _listed(chain_panel) == ["models/robot.stl"]
        assert chain_panel._status_label.text == (
            "  Could not read pipeline GLOBAL:p2, so a template it requires cannot be chosen here: "
            "403 Forbidden")

    def test_a_first_pipeline_of_a_chain_that_cannot_be_read_does_not_gate_and_says_so(
            self, chain_panel):
        connector = chain_panel._connector
        connector.pipelines[("GLOBAL", "p1")] = VamsCliError("403 Forbidden")
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", allow=["*.stl"], name="Thumbnail")
        chain_panel._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda",
                                        "textures/color.jpg"]
        assert chain_panel._status_label.text == (
            "  Could not read pipeline GLOBAL:p1, so its filters do not gate Run and a template it "
            "requires cannot be chosen here: 403 Forbidden")

    def test_an_unreadable_pipeline_leaves_the_workflows_filters_and_says_so(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = VamsCliError("403 Forbidden")
        chain_panel._show_wf_execute(_chain(_ref()))
        assert _listed(chain_panel) == ["models/robot.stl", "GingerbreadHouse.usda",
                                        "textures/color.jpg"]
        assert chain_panel._status_label.text == (
            "  Could not read pipeline GLOBAL:p1, so Run follows the workflow's filters only: "
            "403 Forbidden")
        assert any("403 Forbidden" in label for label in chain_panel.recorder.labels)

    def test_the_pipeline_is_read_once_for_the_view(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(
            allow=["*.stl"], require_template=True)
        chain_panel._connector.templates[("GLOBAL", "p1")] = CONVERT
        chain_panel._show_wf_execute(_chain(_ref()))
        reads = list(chain_panel._connector.reads)
        chain_panel.recorder.combos[0].choose(2)
        assert chain_panel._connector.reads == reads == [
            ("pipeline", "GLOBAL", "p1"), ("templates", "GLOBAL", "p1")]


class TestWholeAssetRunWhenNoFilePasses:
    """The service refuses a whole-asset run whose files the filters leave a pipeline none of."""

    def test_a_whole_asset_run_no_file_passes_is_refused_here(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.glb"])
        chain_panel._show_wf_execute(_chain(_ref(), whole_asset=True))
        chain_panel.recorder.click("  Run on Entire Asset")
        assert chain_panel._connector.executed == []
        assert chain_panel._status_label.text == (
            f"  No file in this asset passes {PIPELINE_FILTERS}.")

    def test_a_whole_asset_run_a_file_passes_goes_to_the_service(self, chain_panel):
        """Control: the refusal is for an asset none of whose files passes."""
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl"])
        chain_panel._show_wf_execute(_chain(_ref(), whole_asset=True))
        chain_panel.recorder.click("  Run on Entire Asset")
        [run] = chain_panel._connector.executed
        assert run["file_key"] is None
        assert chain_panel._status_label.text == "  Workflow started: e1"


class TestTemplateChoice:
    """A pipeline that requires a template, with none named by the workflow or marked default,
    is refused unless the run names one."""

    @pytest.fixture
    def needs_template(self, chain_panel):
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(
            allow=["*.stl"], require_template=True)
        chain_panel._connector.templates[("GLOBAL", "p1")] = CONVERT
        return chain_panel

    def test_a_required_template_without_a_fallback_is_chosen_by_name(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        assert NEEDS_TEMPLATE in needs_template.recorder.labels
        [combo] = needs_template.recorder.combos
        assert (combo.selected, combo.items) == (
            0, ["Choose a template", "Convert to GLB", "Convert to glTF"])

    def test_run_before_choosing_asks_for_a_template(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template.recorder.click("Run")
        assert needs_template._connector.executed == []
        assert needs_template._status_label.text == (
            "  Choose a template for 3D Basic Conversion before running")

    def test_run_passes_the_chosen_template(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template.recorder.combos[0].choose(1)
        assert needs_template.recorder.combos[0].selected == 1
        needs_template.recorder.click("Run")
        [run] = needs_template._connector.executed
        assert (run["file_key"], run["pipeline_parameters"]) == (
            "models/robot.stl", {"p1": {"templateId": "convert-to-glb"}})
        assert needs_template._status_label.text == "  Workflow started: e1"

    def test_a_workflow_default_template_needs_no_choice(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref(default_template="convert-to-glb")))
        assert needs_template.recorder.combos == []
        needs_template.recorder.click("Run")
        assert needs_template._connector.executed[0]["pipeline_parameters"] is None

    def test_a_default_template_needs_no_choice(self, needs_template):
        needs_template._connector.templates[("GLOBAL", "p1")] = [
            CONVERT[0], _template("convert-to-glb", "Convert to GLB", default=True)]
        needs_template._show_wf_execute(_chain(_ref()))
        assert needs_template.recorder.combos == []
        needs_template.recorder.click("Run")
        assert needs_template._connector.executed[0]["pipeline_parameters"] is None

    def test_a_pipeline_that_does_not_require_a_template_gets_no_choice(self, chain_panel):
        """Control: a template-less run is what the service expects of it."""
        chain_panel._connector.pipelines[("GLOBAL", "p1")] = _pipeline(allow=["*.stl"])
        chain_panel._connector.templates[("GLOBAL", "p1")] = CONVERT
        chain_panel._show_wf_execute(_chain(_ref()))
        assert chain_panel.recorder.combos == []
        chain_panel.recorder.click("Run")
        assert chain_panel._connector.executed[0]["pipeline_parameters"] is None

    def test_each_pipeline_that_needs_one_gets_a_choice(self, needs_template):
        connector = needs_template._connector
        connector.pipelines[("GLOBAL", "p2")] = _pipeline("p2", require_template=True,
                                                         name="Thumbnail")
        connector.templates[("GLOBAL", "p2")] = [_template("small", "Small")]
        needs_template._show_wf_execute(_chain(_ref(), _ref("p2")))
        assert "  Template for Thumbnail:" in needs_template.recorder.labels
        needs_template.recorder.combos[0].choose(2)
        needs_template.recorder.combos[1].choose(1)
        needs_template.recorder.click("Run")
        assert connector.executed[0]["pipeline_parameters"] == {
            "p1": {"templateId": "convert-to-gltf"}, "p2": {"templateId": "small"}}

    def test_choosing_a_template_answers_the_request_for_one(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template.recorder.click("Run")
        needs_template.recorder.combos[0].choose(1)
        assert needs_template._status_label.text == (
            "  Template for 3D Basic Conversion: Convert to GLB")

    def test_choosing_another_template_names_that_one(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template.recorder.click("Run")
        needs_template.recorder.combos[0].choose(1)
        needs_template.recorder.combos[0].choose(2)
        assert needs_template._status_label.text == (
            "  Template for 3D Basic Conversion: Convert to glTF")

    def test_choosing_the_placeholder_again_sets_nothing_new(self, needs_template):
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template.recorder.click("Run")
        needs_template.recorder.combos[0].choose(1)
        needs_template.recorder.combos[0].choose(0)
        assert needs_template._status_label.text == (
            "  Template for 3D Basic Conversion: Convert to GLB")

    def test_a_choice_leaves_any_other_status(self, needs_template):
        """Control: only the request to choose a template is answered by a choice."""
        needs_template._show_wf_execute(_chain(_ref()))
        needs_template._set_status("Workflow started: e0")
        needs_template.recorder.combos[0].choose(1)
        assert needs_template._status_label.text == "  Workflow started: e0"

    def test_a_pipeline_with_no_template_to_choose_says_so(self, needs_template):
        needs_template._connector.templates[("GLOBAL", "p1")] = []
        needs_template._show_wf_execute(_chain(_ref()))
        assert needs_template.recorder.combos == []
        assert any("has no templates" in label for label in needs_template.recorder.labels)

    def test_a_refused_template_tag_points_at_the_web_ui(self, needs_template):
        refusal = "Workflow execution failed: pipeline 'GLOBAL:p1': tag 'scale' is required"
        needs_template._connector.execute_error = VamsCliError(refusal)
        needs_template._show_wf_execute(_chain(_ref(default_template="convert-to-glb")))
        needs_template.recorder.click("Run")
        assert needs_template._status_label.text == (
            f"  Workflow error: {refusal} - run it from the VAMS web UI to set its template tags")

    def test_any_other_refusal_carries_no_hint(self, needs_template):
        """Control: the hint is for a template tag the connector cannot supply."""
        refusal = "pipeline 'GLOBAL:p1' requires exactly one input file but none were provided."
        needs_template._connector.execute_error = VamsCliError(refusal)
        needs_template._show_wf_execute(_chain(_ref(default_template="convert-to-glb")))
        needs_template.recorder.click("Run")
        assert needs_template._status_label.text == f"  Workflow error: {refusal}"


def test_the_chosen_template_reaches_the_cli_as_pipeline_parameters(chain_panel, cli_service):
    """End to end from the Execute view to the `workflow execute` argv."""
    cli_service.responses = [
        json.dumps({"pipelineId": "conversion-3d-basic", "databaseId": "GLOBAL",
                    "pipelineName": "3D Basic Conversion",
                    "systemConfig": {"requireTemplate": True, "inputFileArity": "one",
                                     "inputFileFilters": {"allow": ["*.stl"], "exclude": []}}}),
        json.dumps({"Items": [{"templateId": "convert-to-glb", "templateName": "Convert to GLB",
                               "isDefault": False, "overrides": {}}]}),
        json.dumps({"executionId": "e9"}),
    ]
    connector = IsaacVAMSConnector.__new__(IsaacVAMSConnector)
    connector._cli, connector._authenticated, connector._download_roots = cli_service, True, {}
    chain_panel._connector = connector

    chain_panel._show_wf_execute(_chain(_ref("conversion-3d-basic")))
    chain_panel.recorder.combos[0].choose(1)
    chain_panel.recorder.click("Run")

    argv = cli_service.commands[-1]
    assert argv[:2] == ["workflow", "execute"]
    assert argv[argv.index("--input-file") + 1] == "db:a1:/models/robot.stl"
    assert json.loads(argv[argv.index("--pipeline-parameters") + 1]) == {
        "conversion-3d-basic": {"templateId": "convert-to-glb"}}
    assert chain_panel._status_label.text == "  Workflow started: e9"


class TestExecutionHistoryRows:
    """An execution's inputAssetFileKey is ``/<assetId>/<path>``. A row shows the path within the
    asset, and its labels are bounded, so a long key or date cannot widen the window."""

    START = "2026-09-28T06:31:12.482913+00:00"

    def _row(self, panel, key):
        panel._connector.executions = [WorkflowExecution(
            execution_id="e1", workflow_id="conversion-3d-basic", execution_status="SUCCEEDED",
            start_date=self.START, input_file_key=key)]
        panel._refresh_wf_panel()
        return dict(panel.recorder.label_args)

    def test_the_file_is_shown_as_a_path_within_the_asset(self, panel):
        labels = self._row(panel, "/a1/models/robot.stl")
        assert "/a1/models/robot.stl" not in labels
        shown = labels["/models/robot.stl"]
        assert (shown.get("elided_text"), shown.get("tooltip")) == (True, "/a1/models/robot.stl")

    @pytest.mark.parametrize("key", ["/a10/x.stl", "/other/a1/x.stl"])
    def test_a_key_outside_the_asset_is_shown_as_it_is(self, panel, key):
        labels = self._row(panel, key)
        assert labels[key].get("elided_text") is True

    @pytest.mark.parametrize("key", ["", "/", "/a1", "/a1/"])
    def test_a_whole_asset_execution_shows_a_dash(self, panel, key):
        assert "-" in self._row(panel, key)

    def test_the_workflow_and_start_labels_are_bounded(self, panel):
        labels = self._row(panel, "/a1/models/robot.stl")
        workflow = labels["conversion-3d-basic"]
        assert (workflow.get("elided_text"), workflow.get("tooltip")) == (True, "conversion-3d-basic")
        assert labels[self.START].get("elided_text") is True


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

    def test_a_long_description_is_listed_bounded(self, panel):
        wf = Workflow(workflow_id="wf", workflow_name="WF", database_id="GLOBAL",
                      description="x" * 200)
        panel._connector.workflows_by_db = {"GLOBAL": [wf], "db": []}
        panel._refresh_wf_panel()
        assert [b for b in panel.recorder.buttons if b.startswith("  WF")] == [
            _workflow_button(wf)[0]]

    def test_the_listing_asks_for_disabled_workflows(self, panel):
        """"all disabled" is only distinguishable if the listing kept the disabled rows."""
        panel._connector.workflows_by_db = {"GLOBAL": [_workflow()], "db": []}
        panel._refresh_wf_panel()
        assert panel._connector.include_unrunnable_calls == [True, True]
        assert [wf.workflow_id for wf in panel._workflows] == ["wf"]


class TestWorkflowTextIsBounded:
    """The workflow panel scrolls sideways to fit its longest row, so one long description moved
    every row's text off-screen."""

    _LONG = "Automated workflow for 3D mesh format conversion using the CAD and mesh pipeline"

    def test_a_long_description_is_cut_and_carried_whole_in_the_tooltip(self):
        wf = Workflow(workflow_id="basic-conversion", workflow_name="3D Basic Conversion",
                      database_id="GLOBAL", description=self._LONG)
        text, tooltip = _workflow_button(wf)
        assert len(text) == len("  ") + _WF_TEXT_CHARS
        assert text.startswith("  3D Basic Conversion - Automated workflow")
        assert text.endswith("...")
        assert tooltip == ("ID: basic-conversion  |  DB: GLOBAL\n"
                           f"3D Basic Conversion - {self._LONG}")

    def test_a_short_description_is_shown_whole(self):
        wf = Workflow(workflow_id="wf", workflow_name="WF", database_id="GLOBAL",
                      description="Converts meshes")
        assert _workflow_button(wf) == ("  WF - Converts meshes", "ID: wf  |  DB: GLOBAL")

    def test_a_workflow_without_a_description_or_a_name_shows_its_id(self):
        named = Workflow(workflow_id="wf", workflow_name="WF", database_id="db")
        assert _workflow_button(named) == ("  WF", "ID: wf  |  DB: db")
        assert _workflow_button(Workflow(workflow_id="wf", database_id="db")) == (
            "  wf", "ID: wf  |  DB: db")

    def test_a_long_name_is_bounded_too(self):
        wf = Workflow(workflow_id="wf", workflow_name="N" * 120, database_id="GLOBAL")
        text, tooltip = _workflow_button(wf)
        assert len(text) == len("  ") + _WF_TEXT_CHARS
        assert tooltip == "ID: wf  |  DB: GLOBAL\n" + "N" * 120

    @pytest.fixture
    def label_tooltips(self, panel, monkeypatch):
        tooltips = {}
        monkeypatch.setattr(ui, "Label", lambda text="", **kwargs: tooltips.__setitem__(
            text, kwargs.get("tooltip", "")))
        return tooltips

    def test_a_long_execute_header_is_cut_and_carried_whole_in_its_tooltip(self, panel,
                                                                          label_tooltips):
        """A workflow without a name is headed by its description."""
        panel._show_wf_execute(Workflow(workflow_id="wf", database_id="GLOBAL",
                                        description=self._LONG,
                                        system_config={"inputFileArity": "none"}))
        header = f"  Execute: {self._LONG[:_WF_TEXT_CHARS - 3]}..."
        assert label_tooltips[header] == self._LONG

    def test_a_short_execute_header_is_shown_whole(self, panel, label_tooltips):
        panel._show_wf_execute(_workflow())
        assert label_tooltips["  Execute: WF"] == ""
