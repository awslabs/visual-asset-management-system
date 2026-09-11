# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Forward compatibility of the pipeline/workflow systemConfig and the vamsSchema bundle.

Two independent audiences store a PARTIAL systemConfig, so a field added later must be inert for
everything already written:

  - **Records already in DynamoDB.** A row written before a field existed cannot carry it. Both
    records store systemConfig WHOLESALE (create/update replace the field, never merge), so nothing
    backfills them.
  - **API clients.** The create/update handlers persist exactly the block the caller sent, so a client
    that names only the keys it cares about — a CLI script, an external integration, the web form —
    stores a partial map by design, not by accident.

The vamsSchema importer additionally fills a bundle's omissions at registration
(``_fill_system_config_defaults``, covered by TestSystemConfigDefaultsFill in
test_wb6_vamsSchemaImport.py), so a bundle is complete by the time it lands. These tests cover what
that fill does NOT reach: the API path, and every reader's treatment of an absent key.

The compatibility floor — the fields that genuinely cannot default — is asserted here too, so adding a
required field to a bundle fails this test rather than silently breaking a side project that registers
against an older shape.
"""

import json
import os

import pytest

from backend.backend.common.workflows import executionRecords as er
from backend.backend.common.workflows import executionValidation as ev
from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows.pipelineRecords import build_pipeline_system_config
from backend.backend.common.workflows.workflowRecords import build_workflow_system_config

METADATA_KEYS = ("assetMetadata", "fileMetadata", "fileAttributes", "databaseMetadata")


@pytest.mark.unit
class TestMetadataInputDefaultsAreSingleSourced:
    """One table defines what an omitted metadataInputs key means. The builders that WRITE a map and
    the readers that INTERPRET one both resolve through it, so they cannot drift apart — the failure
    mode that hid here was a reader table listing one default-on key while the builders defaulted all
    four on, making a partial map report three types off that a run actually collected."""

    def test_both_builders_emit_exactly_the_shared_defaults(self):
        assert build_pipeline_system_config()["metadataInputs"] == er.METADATA_INPUT_DEFAULTS
        assert build_workflow_system_config()["metadataInputs"] == er.METADATA_INPUT_DEFAULTS

    def test_the_table_covers_every_key_the_validators_read(self):
        assert set(er.METADATA_INPUT_DEFAULTS) == set(METADATA_KEYS)
        assert set(ev._METADATA_KEYS) == set(METADATA_KEYS)

    def test_a_built_map_is_not_the_shared_dict(self):
        # A builder handing back the module-level table would let one caller's edit rewrite the
        # defaults for the whole Lambda container.
        built = build_pipeline_system_config()["metadataInputs"]
        built["assetMetadata"] = False
        assert er.METADATA_INPUT_DEFAULTS["assetMetadata"] is True

    def test_the_validators_read_through_the_shared_helper(self):
        # Not a copy that happens to agree today: the validators' reader IS the executionRecords one,
        # so a change to the rule cannot update one and miss the other. Asserted on provenance rather
        # than object identity because the conftest puts both `backend/` and `backend/backend/` on
        # sys.path, so the module object a test imports is a second load of the same file.
        assert ev._metadata_enabled.__qualname__ == "metadata_input_enabled"
        assert ev._metadata_enabled.__module__.endswith("common.workflows.executionRecords")

    def test_every_key_defaults_on(self):
        # The permissive direction is the correct one here: a config that says nothing about a metadata
        # type gets it, so adding a key does not retroactively strip metadata from existing runs.
        assert all(er.METADATA_INPUT_DEFAULTS.values())


@pytest.mark.unit
class TestAnOmittedMetadataKeyReadsAsItsDefault:
    """Every reader of a metadataInputs map resolves an absent key to its builder default. Plain
    truthiness would read a partial map as opting OUT of everything it does not mention."""

    @pytest.mark.parametrize("key", METADATA_KEYS)
    def test_an_empty_map_enables_every_key(self, key):
        assert er.metadata_input_enabled({}, key) is True
        assert er.metadata_input_enabled(None, key) is True

    @pytest.mark.parametrize("key", METADATA_KEYS)
    def test_only_an_explicit_false_disables_a_key(self, key):
        assert er.metadata_input_enabled({key: False}, key) is False
        # Naming one key does not disable the others.
        for other in METADATA_KEYS:
            if other != key:
                assert er.metadata_input_enabled({key: False}, other) is True

    def test_the_aggregate_reports_what_a_run_collects(self):
        # The workflow response's aggregate is what a caller reads to see which metadata a run gathers,
        # so it must agree with the execute path on a partial map rather than under-reporting.
        agg = ev.aggregate_metadata_inputs({"metadataInputs": {}}, [{"metadataInputs": {}}])
        for key in METADATA_KEYS:
            assert agg[key] is True, key
        assert agg["gatedOffByWorkflow"] == []

    def test_a_future_key_is_read_as_on(self):
        # A key the table does not know yet — an older deployment reading a record written by a newer
        # one — still reads permissively rather than as an opt-out.
        assert er.metadata_input_enabled({}, "someFutureMetadataType") is True


@pytest.mark.unit
class TestApiStoredPartialSystemConfig:
    """The API stores systemConfig wholesale, so a partial block from a client persists as sent. These
    assert what the readers then make of it — the case the vamsSchema fill does not cover."""

    def test_a_partial_map_disables_only_the_key_it_names(self):
        stored = {"inputFileArity": "one", "metadataInputs": {"fileMetadata": False}}
        gate = stored["metadataInputs"]
        assert er.metadata_input_enabled(gate, "fileMetadata") is False
        assert er.metadata_input_enabled(gate, "assetMetadata") is True
        assert er.metadata_input_enabled(gate, "fileAttributes") is True
        assert er.metadata_input_enabled(gate, "databaseMetadata") is True

    def test_a_systemConfig_missing_whole_blocks_is_readable(self):
        # Not a crash and not a silent widening: each absent block resolves to its documented default.
        sparse = {"inputFileArity": "multi"}
        assert ev._arity(sparse) == "multi"
        assert er.metadata_input_enabled(sparse.get("metadataInputs"), "assetMetadata") is True
        assert ev.normalize_asset_scope(sparse.get("assetScope")) == {}

    def test_an_omitted_assetScope_key_fails_CLOSED(self):
        # assetScope is the opposite direction from metadataInputs and deliberately so: an omitted
        # permission is not a grant. A scope that does not say cross-asset is allowed rejects a
        # multi-asset selection.
        two_assets = [{"assetId": "a1", "relativeFileKey": "/f1.glb"},
                      {"assetId": "a2", "relativeFileKey": "/f2.glb"}]
        assert ev._scope_errors({}, two_assets, "Workflow")
        assert ev._scope_errors({"folderAllowed": True}, two_assets, "Workflow")
        # Granting it explicitly admits the selection.
        assert ev._scope_errors({"crossAssetAllowed": True}, two_assets, "Workflow") == []

    def test_an_omitted_input_file_filter_list_means_no_restriction(self):
        # An absent allow list is allow-all, so a config written before a filter field existed does not
        # start rejecting files.
        assert ev.is_open_allow_list(None)
        assert ev.is_open_allow_list([])


@pytest.mark.unit
class TestVamsSchemaCompatibilityFloor:
    """The minimum a vamsSchema bundle must declare. A side project registers against a shape it
    pinned at some earlier version, so anything NOT asserted required here must stay optional — adding
    a required field breaks every bundle already written, and this test is where that surfaces."""

    MINIMAL_BUNDLE = {"pipeline": {"pipelineId": "min-pipe", "pipelineName": "Minimal"}}

    def test_the_minimal_bundle_registers(self):
        requests = vsi.build_import_requests(self.MINIMAL_BUNDLE)
        assert [r["target"] for r in requests] == ["pipelineService"]

    def test_the_minimal_bundle_lands_a_complete_systemConfig(self):
        # It declares no systemConfig at all, so every field is the documented default rather than
        # absent — which is what keeps a later field addition inert for it.
        body = vsi.build_import_requests(self.MINIMAL_BUNDLE)[0]["createBody"]
        assert body["systemConfig"] == build_pipeline_system_config()

    def test_only_pipeline_id_and_name_are_required(self):
        # Drop each in turn: exactly these two are the floor. Anything else that becomes mandatory
        # fails this test.
        for missing in ("pipelineId", "pipelineName"):
            bundle = {"pipeline": {k: v for k, v in self.MINIMAL_BUNDLE["pipeline"].items()
                                   if k != missing}}
            with pytest.raises(vsi.VamsSchemaError):
                vsi.build_import_requests(bundle)

    def test_a_pipeline_id_may_come_from_an_override_instead(self):
        # The CDK supplies ids for built-ins, so a bundle that omits pipelineId still registers when an
        # override provides one.
        requests = vsi.build_import_requests(
            {"pipeline": {"pipelineName": "Minimal"}}, id_overrides={"pipelineId": "from-cdk"})
        assert requests[0]["createBody"]["pipelineId"] == "from-cdk"

    def test_workflow_and_templates_stay_optional(self):
        assert len(vsi.build_import_requests(self.MINIMAL_BUNDLE)) == 1

    def test_a_workflow_needs_only_an_id_and_a_name(self):
        bundle = dict(self.MINIMAL_BUNDLE,
                      workflow={"workflowId": "min-wf", "workflowName": "Minimal WF"})
        assert [r["target"] for r in vsi.build_import_requests(bundle)] == [
            "pipelineService", "workflowService"]

    def test_a_template_needs_only_an_id_and_a_name(self):
        bundle = dict(self.MINIMAL_BUNDLE,
                      templates=[{"templateId": "min-t", "templateName": "Minimal T"}])
        assert [r["target"] for r in vsi.build_import_requests(bundle)] == [
            "pipelineService", "templateService"]

    def test_a_trigger_needs_only_its_type(self):
        bundle = dict(self.MINIMAL_BUNDLE,
                      workflow={"workflowId": "min-wf", "workflowName": "Minimal WF",
                                "triggers": [{"triggerType": "fileUpload"}]})
        assert [r["target"] for r in vsi.build_import_requests(bundle)] == [
            "pipelineService", "workflowService", "triggerService"]


@pytest.mark.unit
class TestVamsSchemaToleratesUnknownFields:
    """A bundle written by a NEWER VAMS must not break an older one reading it, so unknown fields are
    ignored rather than rejected. This is the direction the metadata envelope's equality-gated
    schemaVersion got wrong (a version bump there drops every row), and it is checked here so the
    bundle path does not repeat it."""

    def test_an_unknown_top_level_bundle_section_is_ignored(self):
        bundle = {"pipeline": {"pipelineId": "p", "pipelineName": "P"},
                  "someFutureSection": {"anything": [1, 2, 3]}}
        assert [r["target"] for r in vsi.build_import_requests(bundle)] == ["pipelineService"]

    def test_an_unknown_pipeline_field_is_ignored(self):
        bundle = {"pipeline": {"pipelineId": "p", "pipelineName": "P", "someFutureKnob": True}}
        body = vsi.build_import_requests(bundle)[0]["createBody"]
        assert "someFutureKnob" not in body

    def test_an_unknown_systemConfig_field_survives_the_defaults_fill(self):
        # The fill adds the known defaults around it rather than dropping it, so a field a newer VAMS
        # understands reaches the record intact.
        bundle = {"pipeline": {"pipelineId": "p", "pipelineName": "P",
                               "systemConfig": {"someFutureKnob": "x"}}}
        stored = vsi.build_import_requests(bundle)[0]["createBody"]["systemConfig"]
        assert stored["someFutureKnob"] == "x"
        assert set(build_pipeline_system_config()).issubset(stored)

    def test_a_bundle_declaring_a_newer_schema_version_still_registers(self):
        # Record schemaVersion is stamped on write and read as .get(..., 1) everywhere — no equality
        # gate — so a bundle carrying a higher number is not rejected.
        bundle = {"pipeline": {"pipelineId": "p", "pipelineName": "P", "schemaVersion": 99}}
        assert [r["target"] for r in vsi.build_import_requests(bundle)] == ["pipelineService"]


@pytest.mark.unit
class TestShippedBundlesRelyOnTheDefaults:
    """The shipped bundles are themselves partial, so the fill is load-bearing rather than theoretical.
    If this finds none, the discovery glob broke — not that every bundle became complete."""

    def _bundle_files(self):
        import glob
        root = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "..", "backendPipelines"))
        found = set()
        for pattern in ("/**/vamsSchema/**/pipeline.json", "/**/vamsSchema/pipeline.json",
                        "/**/vamsSchema/**/workflow.json", "/**/vamsSchema/workflow.json"):
            found.update(os.path.normpath(f) for f in glob.glob(root + pattern, recursive=True))
        return sorted(found)

    def test_shipped_bundles_omit_metadata_keys_and_still_read_them_on(self):
        partial = []
        for path in self._bundle_files():
            with open(path, encoding="utf-8") as handle:
                declared = (json.load(handle).get("systemConfig") or {}).get("metadataInputs")
            if isinstance(declared, dict) and any(k not in declared for k in METADATA_KEYS):
                partial.append((path, declared))
        assert partial, "no shipped bundle declares a partial metadataInputs; check the glob"
        for path, declared in partial:
            for key in METADATA_KEYS:
                if key not in declared:
                    assert er.metadata_input_enabled(declared, key) is True, f"{path}:{key}"
