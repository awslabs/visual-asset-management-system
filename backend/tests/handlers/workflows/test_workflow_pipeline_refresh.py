# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A pipeline change reaches the state machines of every workflow that references the pipeline.

A workflow's Step Functions definition encodes its pipelines' execution settings. This file covers the
workflow-service side of keeping that definition current:

  - the per-pipeline FINGERPRINT that says which settings a deployment was built from;
  - JOB NAMES staying stable across a redeploy of an unchanged step list;
  - every deploy PUBLISHING a version (and pruning old ones), so a launch can pin the definition;
  - the REFRESH walk over every referencing workflow, paged to exhaustion, with per-workflow failures
    recorded on the workflow;
  - the INTERNAL ACTIONS being reachable only as a SYSTEM_USER cross-call, never through API Gateway;
  - a workflow UPDATE without specifiedPipelines still redeploying a stale definition.
"""

import json
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest
from boto3.dynamodb.conditions import And, AttributeExists, AttributeNotExists, Equals

from backend.backend.common.workflows import workflowAsl as wa
from backend.backend.common.workflows import workflowAslBuilder as wab
from backend.backend.handlers.workflows import workflowService as ws
from backend.tests.pagingStub import Pager

MOD = "backend.backend.handlers.workflows.workflowService"
ASL = "backend.backend.common.workflows.workflowAsl"

ARN = "arn:aws:states:us-east-1:111122223333:stateMachine:vams-wf"


def _pipeline(pipeline_id="pipe1", database_id="db1", **execution):
    config = {"executionType": "Lambda", "waitForCallback": "Enabled", "taskTimeout": "3600",
              "lambda": {"resourceId": "vams-fn"}}
    config.update(execution)
    return {"databaseId": database_id, "pipelineId": pipeline_id, "pipelineName": "P",
            "enabled": True, "archived": False, "systemConfig": {}, "executionConfig": config}


def _ref(pipeline_id="pipe1", database_id="db1", job_name=""):
    return {"pipelineDatabaseId": database_id, "pipelineId": pipeline_id,
            "pipelineDatabaseId:pipelineId": f"{database_id}:{pipeline_id}",
            "jobName": job_name, "defaultTemplateId": ""}


def _real_to_update_expr(record, op="SET"):
    """The real `common.dynamodb.to_update_expr` (conftest replaces the module with a MagicMock)."""
    keys = list(record.keys())
    names = {f"#u{i}": key for i, key in enumerate(keys)}
    values = {f":u{i}": record[key] for i, key in enumerate(keys)}
    expr = f"{op} " + ", ".join(f"#u{i} = :u{i}" for i in range(len(keys)))
    return names, values, expr


@pytest.fixture(autouse=True)
def bind_real_to_update_expr():
    with patch(f"{MOD}.to_update_expr", _real_to_update_expr):
        yield


def _conditional_check_failed():
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}}, "UpdateItem")


def _holds(condition, row):
    """Evaluate the boto3 condition shapes the handler writes with, against a stored row."""
    if isinstance(condition, And):
        return all(_holds(part, row) for part in condition._values)
    if isinstance(condition, AttributeExists):
        return condition._values[0].name in row
    if isinstance(condition, AttributeNotExists):
        return condition._values[0].name not in row
    if isinstance(condition, Equals):
        attr, value = condition._values
        return attr.name in row and row[attr.name] == value
    raise AssertionError(f"unexpected condition {condition!r}")


class FakeWorkflowTable:
    """Workflow rows keyed by (databaseId, workflowId) that APPLY conditional writes, plus an optional
    Pager serving the by-date GSI walk. With `lagging`, an eventually consistent read returns the rows
    as first stored (a replica that has not caught up) and only a ConsistentRead sees later writes.
    `reads` records the ConsistentRead flag of every get_item."""

    def __init__(self, rows, pager=None, before_write=None, lagging=False):
        self.rows = {(r["databaseId"], r["workflowId"]): r for r in rows}
        self.replica = {key: dict(row) for key, row in self.rows.items()} if lagging else None
        self.pager = pager
        self.before_write = before_write
        self.writes = []
        self.reads = []

    def get_item(self, Key, ConsistentRead=False):
        self.reads.append(ConsistentRead)
        source = self.rows if ConsistentRead or self.replica is None else self.replica
        row = source.get((Key["databaseId"], Key["workflowId"]))
        return {"Item": dict(row)} if row else {}

    def query(self, **kwargs):
        return self.pager(**kwargs)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None):
        key = (Key["databaseId"], Key["workflowId"])
        if self.before_write:
            hook, self.before_write = self.before_write, None
            hook(self.rows.get(key))
        row = self.rows.get(key) or {}
        if ConditionExpression is not None and not _holds(ConditionExpression, row):
            raise _conditional_check_failed()
        written = {}
        for assignment in UpdateExpression.split("SET ", 1)[1].split(", "):
            name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
            written[ExpressionAttributeNames[name_ref]] = ExpressionAttributeValues[value_ref]
        row.update(written)
        self.writes.append((key, written))


class LaggingPipelineTable:
    """Pipeline rows whose eventually consistent reads return `previous` (the row before the latest
    write) and whose ConsistentRead returns `latest`. `reads` records the ConsistentRead flag of
    every get_item."""

    def __init__(self, previous, latest):
        self.previous = {(r["databaseId"], r["pipelineId"]): r for r in previous}
        self.latest = {(r["databaseId"], r["pipelineId"]): r for r in latest}
        self.reads = []

    def get_item(self, Key, ConsistentRead=False):
        self.reads.append(ConsistentRead)
        source = self.latest if ConsistentRead else self.previous
        row = source.get((Key["databaseId"], Key["pipelineId"]))
        return {"Item": dict(row)} if row else {}


def _stored_workflow(workflow_id="wf1", database_id="db1", refs=None, deployed_from=None, **extra):
    """A workflow row, deployed (version + fingerprints) from `deployed_from` when given."""
    row = {"databaseId": database_id, "workflowId": workflow_id, "workflowName": workflow_id,
           "enabled": True, "archived": False, "systemConfig": {},
           "specifiedPipelines": refs if refs is not None else [_ref()],
           "workflow_arn": ARN, "jobNames": ["abcde-pipe1"], "deploymentRevision": 3}
    if deployed_from is not None:
        row["workflowVersionArn"] = ARN + ":3"
        row["pipelineFingerprints"] = wa.workflow_pipeline_fingerprints(deployed_from)
    row.update(extra)
    return row


#######################
# Fingerprint
#######################

@pytest.mark.unit
class TestPipelineFingerprint:
    def test_identical_settings_give_the_same_fingerprint(self):
        assert wa.pipeline_asl_fingerprint(_pipeline()) == wa.pipeline_asl_fingerprint(_pipeline())

    def test_key_order_does_not_matter(self):
        a = _pipeline()
        b = _pipeline()
        b["executionConfig"] = dict(reversed(list(b["executionConfig"].items())))
        assert wa.pipeline_asl_fingerprint(a) == wa.pipeline_asl_fingerprint(b)

    @pytest.mark.parametrize("change", [
        {"taskTimeout": "7200"},
        {"taskHeartbeatTimeout": "600"},
        {"waitForCallback": "Disabled"},
        {"lambda": {"resourceId": "vams-other-fn"}},
        {"executionType": "SQS", "sqs": {"queueUrl": "https://sqs.example/q"}},
    ])
    def test_every_definition_setting_changes_it(self, change):
        assert (wa.pipeline_asl_fingerprint(_pipeline(**change))
                != wa.pipeline_asl_fingerprint(_pipeline()))

    def test_event_bridge_and_deadline_settings_change_it(self):
        eb = _pipeline(executionType="EventBridge",
                       eventBridge={"busArn": "bus", "source": "s", "detailType": "d"})
        eb2 = _pipeline(executionType="EventBridge",
                        eventBridge={"busArn": "bus", "source": "s", "detailType": "other"})
        assert wa.pipeline_asl_fingerprint(eb) != wa.pipeline_asl_fingerprint(eb2)
        dc = _pipeline(executionType="DeadlineCloud",
                       deadlineCloud={"farmId": "f", "queueId": "q", "priority": 50})
        dc2 = _pipeline(executionType="DeadlineCloud",
                        deadlineCloud={"farmId": "f", "queueId": "q", "priority": 60})
        assert wa.pipeline_asl_fingerprint(dc) != wa.pipeline_asl_fingerprint(dc2)

    def test_settings_read_at_launch_do_not_change_it(self):
        # systemConfig, names and flags are read per execution, so editing them needs no redeploy.
        other = _pipeline()
        other.update({"pipelineName": "Renamed", "description": "d", "enabled": False,
                      "systemConfig": {"inputFileArity": "multi", "auxPreviewPipelineSuffix": "x"}})
        other["executionConfig"]["lambda"] = {"resourceId": "vams-fn", "isProvided": False}
        assert wa.pipeline_asl_fingerprint(other) == wa.pipeline_asl_fingerprint(_pipeline())

    def test_workflow_map_is_keyed_by_pipeline_composite(self):
        prints = wa.workflow_pipeline_fingerprints([_pipeline("p1"), _pipeline("p2", "GLOBAL")])
        assert set(prints) == {"db1:p1", "GLOBAL:p2"}

    def test_a_record_without_a_version_never_matches(self):
        workflow = {"pipelineFingerprints": wa.workflow_pipeline_fingerprints([_pipeline()])}
        assert wa.deployment_matches(workflow, [_pipeline()]) is False
        workflow["workflowVersionArn"] = ARN + ":1"
        assert wa.deployment_matches(workflow, [_pipeline()]) is True
        assert wa.deployment_matches(workflow, [_pipeline(taskTimeout="7200")]) is False


#######################
# Stable job names
#######################

@pytest.mark.unit
class TestStableJobNames:
    def _generate(self, pipelines, job_names=None):
        return wab.generate_workflow_asl(
            pipelines, "db1", "wf1", process_workflow_output_function="po",
            interim_tracking_function="it", error_handler_function="eh", job_names=job_names)

    def test_stored_names_are_reused_for_unchanged_steps(self):
        pipelines = [{"name": "convert", "pipelineExecutionType": "Lambda",
                      "userProvidedResource": json.dumps({"resourceId": "fn"})},
                     {"name": "preview", "pipelineExecutionType": "Lambda",
                      "userProvidedResource": json.dumps({"resourceId": "fn"})}]
        _definition, first = self._generate(pipelines)
        definition, second = self._generate(pipelines, job_names=first)
        assert second == first
        # The output-path templates baked into the definition use the reused first-step name.
        assert first[0] in json.dumps(definition)

    def test_new_names_are_generated_when_a_step_changes(self):
        stored = ["abcde-convert"]
        pipelines = [{"name": "preview", "pipelineExecutionType": "Lambda",
                      "userProvidedResource": json.dumps({"resourceId": "fn"})}]
        _definition, names = self._generate(pipelines, job_names=stored)
        assert names != stored and names[0].endswith("-preview")

    def test_a_short_or_malformed_stored_list_is_not_reused(self):
        pipelines = [{"name": "a1b"}, {"name": "c2d"}]
        assert wab.reusable_job_names(["abcde-a1b"], pipelines) is None
        assert wab.reusable_job_names(["a1b", "c2d"], pipelines) is None
        assert wab.reusable_job_names(["abcde-a1b", "fghij-c2d"], pipelines) == [
            "abcde-a1b", "fghij-c2d"]

    def test_a_redeploy_of_unchanged_steps_passes_the_stored_names(self):
        item = _stored_workflow(jobNames=["abcde-pipe1"])
        with patch(f"{MOD}.workflowAsl.deploy_state_machine",
                   return_value=(ARN, ["abcde-pipe1"], ARN + ":4")) as deploy:
            ws._deploy_fields("db1", "wf1", [(_ref(), _pipeline())], item)
        assert deploy.call_args.kwargs["job_names"] == ["abcde-pipe1"]

    def test_a_changed_step_list_does_not_pass_the_stored_names(self):
        item = _stored_workflow(jobNames=["abcde-pipe1"])
        with patch(f"{MOD}.workflowAsl.deploy_state_machine",
                   return_value=(ARN, ["zzzzz-pipe2"], ARN + ":4")) as deploy:
            ws._deploy_fields("db1", "wf1", [(_ref("pipe2"), _pipeline("pipe2"))], item)
        assert deploy.call_args.kwargs["job_names"] is None


#######################
# Deploy publishes a version
#######################

class _DoesNotExist(Exception):
    pass


def _sf_client(versions=None):
    client = MagicMock()
    client.exceptions.StateMachineDoesNotExist = _DoesNotExist
    client.update_state_machine.return_value = {"stateMachineVersionArn": ARN + ":9"}
    client.create_state_machine.return_value = {"stateMachineArn": ARN}
    client.publish_state_machine_version.return_value = {"stateMachineVersionArn": ARN + ":1"}
    client.list_state_machine_versions.return_value = {"stateMachineVersions": [
        {"stateMachineVersionArn": f"{ARN}:{n}"} for n in (versions or [])]}
    return client


_ENV = {"process_workflow_output_function": "po", "interim_tracking_function": "it",
        "error_handler_function": "eh", "role_arn": "arn:aws:iam::1:role/r",
        "log_group_arn": "arn:aws:logs:us-east-1:1:log-group:g", "aws_partition": "aws"}


@pytest.mark.unit
class TestDeployPublishesAVersion:
    def test_an_update_publishes_in_the_same_request(self):
        client = _sf_client()
        with patch(f"{ASL}._deploy_env", return_value=_ENV), \
                patch(f"{ASL}._sf_client", return_value=client):
            arn, job_names, version = wa.deploy_state_machine(
                "db1", "wf1", [(_ref(), _pipeline())], existing_arn=ARN)
        assert arn == ARN and version == ARN + ":9" and len(job_names) == 1
        assert client.update_state_machine.call_args.kwargs["publish"] is True

    def test_a_create_publishes_its_first_revision(self):
        client = _sf_client()
        with patch(f"{ASL}._deploy_env", return_value=_ENV), \
                patch(f"{ASL}._sf_client", return_value=client):
            arn, _job_names, version = wa.deploy_state_machine(
                "db1", "wf1", [(_ref(), _pipeline())], existing_arn="")
        assert arn == ARN and version == ARN + ":1"
        assert {c.kwargs.get("stateMachineArn")
                for c in client.publish_state_machine_version.call_args_list} == {ARN}

    def test_old_versions_beyond_the_retention_are_deleted(self):
        retained = wa.MAX_RETAINED_STATE_MACHINE_VERSIONS
        newest_first = list(range(retained + 3, 0, -1))
        client = _sf_client(versions=newest_first)
        wa.prune_state_machine_versions(client, ARN, keep_version_arns=[f"{ARN}:1"])
        deleted = {c.kwargs["stateMachineVersionArn"]
                   for c in client.delete_state_machine_version.call_args_list}
        # The newest `retained` survive, and so does any version named to keep.
        assert deleted == {f"{ARN}:{n}" for n in newest_first[retained:] if n != 1}

    def test_a_prune_failure_does_not_fail_the_deploy(self):
        client = _sf_client()
        client.list_state_machine_versions.side_effect = Exception("throttled")
        with patch(f"{ASL}._deploy_env", return_value=_ENV), \
                patch(f"{ASL}._sf_client", return_value=client):
            _arn, _job_names, version = wa.deploy_state_machine(
                "db1", "wf1", [(_ref(), _pipeline())], existing_arn=ARN)
        assert version == ARN + ":9"

    def test_an_overlapping_publish_is_retried(self):
        client = _sf_client()
        conflict = botocore.exceptions.ClientError(
            {"Error": {"Code": "ConflictException", "Message": "busy"}}, "UpdateStateMachine")
        client.update_state_machine.side_effect = [conflict, {"stateMachineVersionArn": ARN + ":9"}]
        with patch(f"{ASL}._deploy_env", return_value=_ENV), \
                patch(f"{ASL}._sf_client", return_value=client), patch(f"{ASL}.time.sleep"):
            _arn, _job_names, version = wa.deploy_state_machine(
                "db1", "wf1", [(_ref(), _pipeline())], existing_arn=ARN)
        assert version == ARN + ":9"
        # It was retried, and within the retry budget.
        assert 2 <= client.update_state_machine.call_count <= wa.MAX_PUBLISH_CONFLICT_RETRIES + 1


#######################
# Refresh walk
#######################

def _deployed(version="4"):
    def _deploy(database_id, workflow_id, ref_records, existing_arn="", job_names=None):
        return existing_arn or ARN, job_names or ["fghij-pipe1"], f"{ARN}:{version}"
    return _deploy


@pytest.mark.unit
class TestRefreshPipelineReferences:
    def _table(self, rows, pages):
        pager = Pager(*pages, name="refresh walk", max_reads=50)
        return FakeWorkflowTable(rows, pager=pager), pager

    def test_every_referencing_workflow_on_every_page_is_redeployed(self):
        # More referencing workflows than the 25-label warning cap, spread over three pages.
        stale = [_stored_workflow(f"wf{i}", deployed_from=[_pipeline()]) for i in range(30)]
        unrelated = _stored_workflow("other", refs=[_ref("pipe9")], deployed_from=[_pipeline("pipe9")])
        archived = _stored_workflow("old", archived=True, deployed_from=[_pipeline()])
        rows = stale + [unrelated, archived]
        pages = [
            {"Items": rows[:12], "LastEvaluatedKey": {"k": 1}},
            {"Items": rows[12:24], "LastEvaluatedKey": {"k": 2}},
            {"Items": rows[24:]},
        ]
        table, pager = self._table(rows, pages)
        live = _pipeline(taskTimeout="7200")
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record",
                      side_effect=lambda db, pid, **_kw: live if pid == "pipe1" else _pipeline(pid)), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deployed()) as deploy, \
                patch(f"{MOD}.log_actions"):
            result = ws.refresh_pipeline_references("db1", "pipe1")
        pager.assert_paged_to_exhaustion()
        assert result == {"workflowCount": 30, "outcomes": {"redeployed": 30}}
        redeployed = {kwargs_key for kwargs_key, _w in table.writes}
        assert redeployed == {("db1", f"wf{i}") for i in range(30)}
        assert deploy.call_args_list, "no workflow was redeployed"
        assert {c.args[1] for c in deploy.call_args_list} == {f"wf{i}" for i in range(30)}
        # Each write records the new version and the live pipeline's fingerprint together.
        _key, written = table.writes[0]
        assert written["workflowVersionArn"] == ARN + ":4"
        assert written["pipelineFingerprints"] == wa.workflow_pipeline_fingerprints([live])
        assert written["deploymentRevision"] == 4

    def test_the_walk_projects_only_the_match_attributes(self):
        table, pager = self._table([], [{"Items": []}])
        with patch(f"{MOD}._workflow_table", return_value=table), patch(f"{MOD}.log_actions"):
            ws.refresh_pipeline_references("db1", "pipe1")
        call = pager.calls[0]
        assert call["IndexName"] == "WorkflowsByDateGSI"
        projected = {p.strip() for p in call["ProjectionExpression"].split(",")}
        assert {"databaseId", "workflowId", "specifiedPipelines"} <= projected

    def test_a_current_workflow_is_left_alone(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        table, _pager = self._table([row], [{"Items": [row]}])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=_pipeline()), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine") as deploy, \
                patch(f"{MOD}.log_actions"):
            result = ws.refresh_pipeline_references("db1", "pipe1")
        deploy.assert_not_called()
        assert result["outcomes"] == {"current": 1}

    def test_one_failure_is_recorded_on_its_workflow_and_the_walk_continues(self):
        rows = [_stored_workflow("wf-bad", deployed_from=[_pipeline()]),
                _stored_workflow("wf-good", deployed_from=[_pipeline()])]
        table, _pager = self._table(rows, [{"Items": rows}])

        def _deploy(database_id, workflow_id, *args, **kwargs):
            if workflow_id == "wf-bad":
                raise botocore.exceptions.ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                    "UpdateStateMachine")
            return _deployed()(database_id, workflow_id, *args, **kwargs)

        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=_pipeline(taskTimeout="7200")), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deploy), \
                patch(f"{MOD}.log_actions"):
            result = ws.refresh_pipeline_references("db1", "pipe1")
        assert result["outcomes"] == {"failed": 1, "redeployed": 1}
        bad = table.rows[("db1", "wf-bad")]
        assert bad["deploymentStale"] is True
        assert "AccessDeniedException" in bad["deploymentStaleReason"]
        # The failed workflow keeps its previous deployment record; only the flag is added.
        assert bad["workflowVersionArn"] == ARN + ":3"
        assert table.rows[("db1", "wf-good")]["workflowVersionArn"] == ARN + ":4"

    def test_a_missing_pipeline_is_recorded_as_stale(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        table, _pager = self._table([row], [{"Items": [row]}])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=None), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine") as deploy, \
                patch(f"{MOD}.log_actions"):
            result = ws.refresh_pipeline_references("db1", "pipe1")
        deploy.assert_not_called()
        assert result["outcomes"] == {"failed": 1}
        assert table.rows[("db1", "wf1")]["deploymentStale"] is True


@pytest.mark.unit
class TestRedeployIsConditional:
    def test_a_deployment_recorded_meanwhile_is_not_overwritten(self):
        # Another writer redeploys between this redeploy's read and its write.
        row = _stored_workflow(deployed_from=[_pipeline()])
        live = _pipeline(taskTimeout="7200")

        def _concurrent_redeploy(stored):
            stored.update({"deploymentRevision": 4, "workflowVersionArn": ARN + ":7",
                           "pipelineFingerprints": wa.workflow_pipeline_fingerprints([live])})

        table = FakeWorkflowTable([row], before_write=_concurrent_redeploy)
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=live), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deployed("8")):
            result = ws.redeploy_workflow("db1", "wf1", reason="test")
        # The conditional write failed, the re-read found the other writer's current deployment.
        assert result["outcome"] == "current"
        assert result["workflowVersionArn"] == ARN + ":7"
        assert table.rows[("db1", "wf1")]["workflowVersionArn"] == ARN + ":7"
        assert table.writes == []

    def test_a_first_redeploy_of_a_legacy_record_requires_no_revision(self):
        row = _stored_workflow()
        row.pop("deploymentRevision")
        table = FakeWorkflowTable([row])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=_pipeline()), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deployed()):
            result = ws.redeploy_workflow("db1", "wf1", reason="test")
        assert result["outcome"] == "redeployed"
        assert table.rows[("db1", "wf1")]["deploymentRevision"] == 1


@pytest.mark.unit
class TestRedeployReadsAreStronglyConsistent:
    """A pipeline save writes the pipeline and then invokes the refresh, so the refresh's reads can land
    before the replicas have the write. The redeploy path reads strongly consistent; the ordinary API
    reads keep the default."""

    def test_a_refresh_right_after_a_pipeline_write_sees_the_new_settings(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        live = _pipeline(taskTimeout="7200")
        pipelines = LaggingPipelineTable(previous=[_pipeline()], latest=[live])
        table = FakeWorkflowTable([row])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._pipeline_table", return_value=pipelines), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deployed()):
            result = ws.redeploy_workflow("db1", "wf1", reason="pipeline db1:pipe1 changed")
        assert result["outcome"] == "redeployed", result
        assert table.rows[("db1", "wf1")]["pipelineFingerprints"] == \
            wa.workflow_pipeline_fingerprints([live])
        assert pipelines.reads and all(pipelines.reads), pipelines.reads

    def test_the_re_read_after_a_lost_write_sees_the_other_writers_deployment(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        live = _pipeline(taskTimeout="7200")

        def _concurrent_redeploy(stored):
            stored.update({"deploymentRevision": 4, "workflowVersionArn": ARN + ":7",
                           "pipelineFingerprints": wa.workflow_pipeline_fingerprints([live])})

        table = FakeWorkflowTable([row], before_write=_concurrent_redeploy, lagging=True)
        pipelines = LaggingPipelineTable(previous=[live], latest=[live])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._pipeline_table", return_value=pipelines), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=_deployed("8")):
            result = ws.redeploy_workflow("db1", "wf1", reason="launch-time check")
        assert result["outcome"] == "current", result
        assert result["workflowVersionArn"] == ARN + ":7"
        assert table.writes == []
        assert len(table.reads) == 2 and all(table.reads), table.reads

    def test_an_api_update_keeps_eventually_consistent_reads(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        table = FakeWorkflowTable([row])
        pipelines = LaggingPipelineTable(previous=[_pipeline()], latest=[_pipeline()])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._pipeline_table", return_value=pipelines), \
                patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=_allow()), \
                patch(f"{MOD}.log_actions"), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine") as deploy:
            resp = ws.lambda_handler(_put_event({"description": "edited"}), MagicMock())
        assert resp["statusCode"] == 200
        deploy.assert_not_called()
        assert table.reads and not any(table.reads), table.reads
        assert pipelines.reads and not any(pipelines.reads), pipelines.reads


#######################
# Internal actions
#######################

def _internal_event(action, payload, **extra):
    event = {"vamsInternalAction": action, "lambdaCrossCall": {"userName": "SYSTEM_USER"},
             "payload": payload}
    event.update(extra)
    return event


@pytest.mark.unit
class TestInternalActionsAreNotApiReachable:
    def test_an_api_gateway_shaped_event_is_refused(self):
        # API Gateway always builds a requestContext; such an event is never an internal action.
        event = _internal_event("refreshPipelineReferences",
                                {"pipelineDatabaseId": "db1", "pipelineId": "pipe1"},
                                requestContext={"http": {"method": "PUT", "path": "/x"}})
        with patch(f"{MOD}.refresh_pipeline_references") as refresh:
            resp = ws.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 403
        refresh.assert_not_called()

    def test_a_cross_call_as_another_user_is_refused(self):
        event = _internal_event("redeployWorkflow", {"workflowDatabaseId": "db1", "workflowId": "wf1"})
        event["lambdaCrossCall"] = {"userName": "someone"}
        with patch(f"{MOD}.redeploy_workflow") as redeploy:
            resp = ws.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 403
        redeploy.assert_not_called()

    def test_the_refresh_action_names_only_the_pipeline(self):
        event = _internal_event("refreshPipelineReferences",
                                {"pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe1",
                                 # A caller cannot aim the refresh: extra keys are ignored.
                                 "workflowIds": ["victim"]})
        with patch(f"{MOD}.refresh_pipeline_references",
                   return_value={"workflowCount": 0, "outcomes": {}}) as refresh:
            resp = ws.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert refresh.call_args.args[:2] == ("GLOBAL", "pipe1")

    def test_the_redeploy_action_returns_the_deployment(self):
        event = _internal_event("redeployWorkflow", {"workflowDatabaseId": "db1", "workflowId": "wf1"})
        with patch(f"{MOD}.redeploy_workflow",
                   return_value={"outcome": "redeployed", "workflowVersionArn": ARN + ":4"}), \
                patch(f"{MOD}.log_actions"):
            resp = ws.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["message"]["workflowVersionArn"] == ARN + ":4"

    def test_an_unknown_action_or_bad_id_is_rejected(self):
        assert ws.lambda_handler(_internal_event("dropTables", {}), MagicMock())["statusCode"] == 400
        bad = _internal_event("redeployWorkflow", {"workflowDatabaseId": "db1", "workflowId": "x"})
        assert ws.lambda_handler(bad, MagicMock())["statusCode"] == 400


#######################
# Update without specifiedPipelines
#######################

def _put_event(body):
    return {
        "requestContext": {"http": {"method": "PUT", "path": "/database/db1/workflows/wf1"}},
        "pathParameters": {"databaseId": "db1", "workflowId": "wf1"},
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer t"},
        "body": json.dumps(body),
    }


def _allow():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


@pytest.mark.unit
class TestUpdateWithoutPipelinesRedeploysAStaleDefinition:
    """`vamscli workflow update` without --pipeline sends no specifiedPipelines; the save must still
    bring a stale definition up to date."""

    def _update(self, row, live, body=None, deploy=None):
        table = FakeWorkflowTable([row])
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}._get_pipeline_record", return_value=live), \
                patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=_allow()), \
                patch(f"{MOD}.log_actions"), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine",
                      side_effect=deploy or _deployed()) as m_deploy:
            resp = ws.lambda_handler(_put_event(body or {"description": "edited"}), MagicMock())
        return resp, table, m_deploy

    def test_a_stale_definition_is_redeployed_with_its_stored_job_names(self):
        row = _stored_workflow(deployed_from=[_pipeline()], jobNames=["abcde-pipe1"])
        live = _pipeline(taskTimeout="7200")
        resp, table, m_deploy = self._update(row, live)
        assert resp["statusCode"] == 200
        assert m_deploy.called
        assert m_deploy.call_args.kwargs["job_names"] == ["abcde-pipe1"]
        stored = table.rows[("db1", "wf1")]
        assert stored["description"] == "edited"
        assert stored["workflowVersionArn"] == ARN + ":4"
        assert stored["pipelineFingerprints"] == wa.workflow_pipeline_fingerprints([live])
        assert stored["jobNames"] == ["abcde-pipe1"]

    def test_a_legacy_record_without_fingerprints_is_redeployed(self):
        row = _stored_workflow()
        resp, table, m_deploy = self._update(row, _pipeline())
        assert resp["statusCode"] == 200
        assert m_deploy.called
        assert table.rows[("db1", "wf1")]["workflowVersionArn"] == ARN + ":4"

    def test_a_current_definition_is_not_redeployed(self):
        row = _stored_workflow(deployed_from=[_pipeline()])
        resp, table, m_deploy = self._update(row, _pipeline())
        assert resp["statusCode"] == 200
        m_deploy.assert_not_called()
        assert set(table.writes[0][1]) == {"description", "dateModified", "modifiedBy"}

    def test_a_failed_redeploy_keeps_the_edit_and_flags_the_workflow(self):
        row = _stored_workflow(deployed_from=[_pipeline()])

        def _fail(*args, **kwargs):
            raise ValueError("The workflow's state machine definition is too large.")

        resp, table, _m = self._update(row, _pipeline(taskTimeout="7200"), deploy=_fail)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])["message"]
        assert any("too large" in w for w in body["warnings"]), body["warnings"]
        assert body["deploymentStale"] is True
        stored = table.rows[("db1", "wf1")]
        assert stored["description"] == "edited" and stored["deploymentStale"] is True


@pytest.mark.unit
class TestAFailedRefreshWalkIsRetried:
    def test_the_async_refresh_raises_so_lambda_retries_it(self):
        # Per-workflow failures are caught inside the walk; the walk itself failing (here, its own
        # read) must raise, which is what hands an asynchronous invoke to Lambda's retry.
        event = _internal_event("refreshPipelineReferences",
                                {"pipelineDatabaseId": "db1", "pipelineId": "pipe1"})
        with patch(f"{MOD}._referencing_workflow_keys", side_effect=Exception("throttled")):
            with pytest.raises(Exception, match="throttled"):
                ws.lambda_handler(event, MagicMock())

    def test_a_failed_redeploy_action_answers_its_synchronous_caller(self):
        event = _internal_event("redeployWorkflow", {"workflowDatabaseId": "db1", "workflowId": "wf1"})
        with patch(f"{MOD}.redeploy_workflow", side_effect=Exception("boom")):
            assert ws.lambda_handler(event, MagicMock())["statusCode"] == 500
