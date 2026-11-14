# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A compliance pipeline rule may not use a system pipeline or workflow.

The system records are the rows the deployment's import registers with `isSystem: true`: the SYSTEM
GenAI metadata and asset summary pipelines and the 3D preview thumbnail pipeline, with their
workflows (`backendPipelines/**/vamsSchema`). A schema save whose new or changed pipeline rule names
one (its workflow, its own pipeline, or a pipeline its workflow runs) answers 400 with one generic
message, for every caller, after authorization: a caller who may not run the workflow gets the 403 it
got before and learns nothing about the record. An ordinary pipeline is still accepted. A schema
stored before the refusal that names one is evaluated without launching it: the rule's result is an
error with the same message, and no execution starts (so nothing reaches Amazon Bedrock).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import ASSET, DB as EVAL_DB
from backend.tests.handlers.compliance.test_compliancePipelineRuleSaveAuthorization import (
    DB, REFUSED, World, _body, _rule, _stand_in,
)
from handlers.compliance import complianceEvaluationStore as store
from models.compliance import PipelineRule

REPO_ROOT = Path(__file__).resolve().parents[4]
SYSTEM_BUNDLES = [
    REPO_ROOT / "backendPipelines" / "system" / "genAiMetadata" / "vamsSchema",
    REPO_ROOT / "backendPipelines" / "system" / "genAiMetadata" / "vamsSchema" / "assetSummary",
    REPO_ROOT / "backendPipelines" / "preview" / "3dThumbnail" / "vamsSchema",
]
SYSTEM_IDS = {"genAiMetadata": "system-genai-metadata", "assetSummary": "system-genai-asset-summary",
              "3dThumbnail": "preview-3d-thumbnail"}
# The one message every refusal and every error result carries (pinned here, not read from the code).
MESSAGE = ("A pipeline rule cannot use a system pipeline. To require the generated metadata, add a "
           "metadata rule on its genai_* fields.")
REFUSAL = {"message": MESSAGE, "ruleName": "convert"}


def _bundle_id(bundle):
    return SYSTEM_IDS[bundle.name if bundle.name != "vamsSchema" else bundle.parent.name]


def _seed_system(world, bundle, database_id="GLOBAL"):
    """The shipped bundle's pipeline and workflow rows, as the import writes them (isSystem)."""
    pipeline = json.loads((bundle / "pipeline.json").read_text())
    workflow = json.loads((bundle / "workflow.json").read_text())
    assert pipeline["isSystem"] is True and workflow["isSystem"] is True
    record_id = _bundle_id(bundle)
    world.t["pipelineStorageV2"].put_item(Item={
        "databaseId": database_id, "pipelineId": record_id,
        "pipelineName": pipeline.get("pipelineName", ""), "isSystem": True})
    world.t["workflowStorageV2"].put_item(Item={
        "databaseId": database_id, "workflowId": record_id,
        "workflowName": workflow.get("workflowName", ""), "isSystem": True,
        "specifiedPipelines": [{"pipelineId": record_id, "pipelineDatabaseId": database_id}]})
    return record_id


def _system_rule(record_id):
    return _rule(workflow_db="GLOBAL", workflow_id=record_id, pipeline_db="GLOBAL",
                 pipeline_id=record_id)


@pytest.mark.unit
@pytest.mark.parametrize("bundle", SYSTEM_BUNDLES, ids=lambda b: _bundle_id(b))
class TestASaveNamingASystemRecordIsRefused:

    def test_on_register(self, bundle):
        _, factory = _stand_in()
        with World(factory) as world:
            record_id = _seed_system(world, bundle)
            status, body = world.create(_body(convert=_system_rule(record_id)))
            assert (status, body) == (400, REFUSAL)
            assert world.versions() == 0

    def test_on_update(self, bundle):
        _, factory = _stand_in()
        with World(factory) as world:
            record_id = _seed_system(world, bundle)
            assert world.create(_body())[0] == 200
            status, body = world.update(_body(convert=_system_rule(record_id)))
            assert (status, body) == (400, REFUSAL)
            assert world.versions() == 1

    def test_for_the_system_user_too(self, bundle):
        _, factory = _stand_in()
        with World(factory, tokens=("SYSTEM_USER",)) as world:
            record_id = _seed_system(world, bundle)
            status, body = world.create(_body(convert=_system_rule(record_id)))
            assert (status, body) == (400, REFUSAL)

    def test_through_an_ordinary_workflow_that_runs_the_system_pipeline(self, bundle):
        _, factory = _stand_in()
        with World(factory) as world:
            record_id = _seed_system(world, bundle)
            world.t["workflowStorageV2"].put_item(Item={
                "databaseId": DB, "workflowId": "wraps-system", "workflowName": "Wrapper",
                "specifiedPipelines": [{"pipelineId": record_id, "pipelineDatabaseId": "GLOBAL"}]})
            status, body = world.create(_body(convert=_rule(workflow_id="wraps-system")))
            assert (status, body) == (400, REFUSAL)

    def test_a_caller_who_may_not_run_it_gets_the_authorization_refusal(self, bundle):
        _, factory = _stand_in((("workflow", "GET"),))
        with World(factory) as world:
            record_id = _seed_system(world, bundle)
            status, body = world.create(_body(convert=_system_rule(record_id)))
            assert (status, body) == (403, REFUSED)


@pytest.mark.unit
class TestOrdinaryPipelinesAreStillAccepted:

    def test_an_ordinary_pipeline_rule_saves(self):
        _, factory = _stand_in()
        with World(factory) as world:
            status, _ = world.create(_body(convert=_rule()))
            assert status == 200
            assert world.versions() == 1

    def test_an_unchanged_stored_rule_is_not_rechecked_on_another_edit(self):
        """A schema stored before the refusal keeps saving while its system rule is left as it was;
        the rule itself is recorded as an error when it is evaluated."""
        _, factory = _stand_in()
        with World(factory) as world:
            record_id = _seed_system(world, SYSTEM_BUNDLES[0])
            body = _body(convert=_system_rule(record_id))
            world.t["complianceSchemaStorage"].put_item(Item={
                "schemaName": "pipeline-schema", "internalVersion": 1, "databaseId": DB,
                "description": "", "schemaBody": json.dumps(body), "isSystem": False})
            body["rules"]["owner-present"]["enforcement"] = "inform"
            assert world.update(body)[0] == 200


SYSTEM_WORKFLOW = {"databaseId": "GLOBAL", "workflowId": "system-genai-metadata", "isSystem": True,
                   "systemConfig": {"inputFileArity": "one"},
                   "specifiedPipelines": [{"pipelineId": "system-genai-metadata",
                                           "pipelineDatabaseId": "GLOBAL"}]}
SYSTEM_PIPELINE = {"databaseId": "GLOBAL", "pipelineId": "system-genai-metadata", "isSystem": True,
                   "systemConfig": {"inputFileArity": "one"}}
STORED_RULE = PipelineRule(**{
    "ruleType": "pipeline", "enforcement": "quarantine",
    "pipelineRef": {"databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
                    "pipelineDatabaseId": "GLOBAL", "pipelineId": "system-genai-metadata"},
    "checks": [{"name": "must_succeed", "outputField": "execution_success",
                "tolerance": {"operator": "gte", "value": 1.0}}]})


@pytest.mark.unit
class TestAStoredRuleNamingASystemRecordIsNotLaunched:

    @pytest.mark.parametrize("workflow,pipeline", [
        (SYSTEM_WORKFLOW, SYSTEM_PIPELINE),
        (dict(SYSTEM_WORKFLOW, isSystem=False), SYSTEM_PIPELINE),
        (SYSTEM_WORKFLOW, dict(SYSTEM_PIPELINE, isSystem=False)),
    ], ids=["both", "system-pipeline", "system-workflow"])
    def test_the_rule_is_an_error_with_the_reason_and_nothing_starts(self, workflow, pipeline):
        launch = MagicMock(name="launch_workflow_execution", return_value=("exec-1", False))
        table = MagicMock(name="evaluation_table")
        with patch.object(store, "get_workflow_item", return_value=workflow), \
                patch.object(store, "get_pipeline_item", return_value=pipeline), \
                patch.object(store, "get_template_item", return_value=None), \
                patch.object(store, "resolve_asset_s3_location", return_value=("bucket", "a/")), \
                patch.object(store, "list_file_keys_under", return_value=["/pump.glb"]), \
                patch.object(store, "launch_workflow_execution", launch), \
                patch.object(store, "evaluation_table", table):
            started, errored = store.start_pipeline_rule_executions(
                {"genai": STORED_RULE}, "eval-1", EVAL_DB, ASSET, "2026-03-01T12:00:00+00:00")
        assert started == []
        (result,) = errored
        assert result.ruleName == "genai"
        assert result.status == "error"
        assert result.message == MESSAGE
        launch.assert_not_called()
        table.put_item.assert_not_called()

    def test_an_ordinary_rule_still_launches(self):
        launch = MagicMock(name="launch_workflow_execution", return_value=("exec-1", False))
        ordinary_workflow = dict(SYSTEM_WORKFLOW, isSystem=False, specifiedPipelines=[])
        with patch.object(store, "get_workflow_item", return_value=ordinary_workflow), \
                patch.object(store, "get_pipeline_item", return_value=dict(SYSTEM_PIPELINE, isSystem=False)), \
                patch.object(store, "get_template_item", return_value=None), \
                patch.object(store, "resolve_asset_s3_location", return_value=("bucket", "a/")), \
                patch.object(store, "list_file_keys_under", return_value=["/pump.glb"]), \
                patch.object(store, "asset_file_change_source", return_value=""), \
                patch.object(store, "launch_workflow_execution", launch), \
                patch.object(store, "evaluation_table", MagicMock()):
            started, errored = store.start_pipeline_rule_executions(
                {"genai": STORED_RULE}, "eval-1", EVAL_DB, ASSET, "2026-03-01T12:00:00+00:00")
        assert errored == []
        assert [entry["executionId"] for entry in started] == ["exec-1"]
        launch.assert_called_once()

    def test_a_waiting_rule_is_closed_instead_of_relaunched(self):
        row = {"evaluationId": "eval-1#genai", "pipelineRuleName": "genai",
               "workflowDatabaseId": "GLOBAL", "workflowId": "system-genai-metadata",
               "launchRequest": json.dumps({"inputFiles": []})}
        launch = MagicMock(name="launch_workflow_execution")
        close = MagicMock(name="_close_waiting_rule", return_value=True)
        with patch.object(store, "_update_tracking_row"), \
                patch.object(store, "get_workflow_item", return_value=SYSTEM_WORKFLOW), \
                patch.object(store, "get_pipeline_item", return_value=SYSTEM_PIPELINE), \
                patch.object(store, "launch_workflow_execution", launch), \
                patch.object(store, "_close_waiting_rule", close):
            outcome = store._launch_waiting_rule(row, {"evaluationId": "eval-1"})
        assert outcome == "failed"
        launch.assert_not_called()
        assert close.call_args.args[2] == MESSAGE


@pytest.mark.unit
def test_the_code_carries_the_pinned_message():
    from common.workflows import systemRecords
    assert systemRecords.SYSTEM_RECORD_PIPELINE_RULE_MESSAGE == MESSAGE
