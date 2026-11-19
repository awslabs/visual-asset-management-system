# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""executeWorkflow: the quarantine block applies to the input files of a caller's run.

A run reads its input files' bytes, and its outputs can land in another asset or leave the
deployment, so with ``app.compliance.quarantineBlocksDownload`` on a caller's run is refused when
an input file's asset is quarantined without an exception. The check runs after every asset is
authorized, so a caller who may not read an input never learns its state. A run VAMS launches as
SYSTEM_USER (a file-upload trigger, a compliance pipeline rule, a system pipeline) is not refused:
a compliance pipeline rule must run on a quarantined asset to re-evaluate it. A re-run invokes this
handler as the calling user, so it is covered by the same check. With the block off nothing changes.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows.test_executeWorkflow import (
    MOD, _ASSET, _PIPELINE, _WORKFLOW, _allow_enforcer, _event, ewv2,
)
from backend.tests.quarantineRefusalLog import assert_refusal_logged_once, captured_logs
from backend.tests.workflowDeploymentStub import with_current_deployment

# The guard module the handler bound at import (patched in place, so the handler sees the patch).
guard = sys.modules[ewv2.check_quarantine_block.__module__]

INPUT_MESSAGE = "Asset is quarantined and its files cannot be used as workflow input"
ONE_INPUT = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(asset_id="a1", exception_granted=False):
    row = {"databaseId": "db1", "assetId": asset_id, "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _run(event, table, enforcer=None, block_on=True, get_asset=None):
    """The handler on a happy-path launch (one Lambda pipeline, one input asset) with the given
    asset-state table. Returns (response, sfn mock)."""
    with patch(f"{MOD}._get_workflow", return_value=with_current_deployment(_WORKFLOW, [_PIPELINE])), \
            patch(f"{MOD}._get_pipeline", return_value=dict(_PIPELINE)), \
            patch(f"{MOD}._get_asset", side_effect=get_asset or (lambda db, a: dict(_ASSET, assetId=a))), \
            patch(f"{MOD}._default_run_bucket",
                  return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}), \
            patch(f"{MOD}._asset_bucket_details",
                  return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}), \
            patch(f"{MOD}._input_exists_in_s3", return_value=(True, "v-resolved")), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer or _allow_enforcer()), \
            patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
            patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
            patch(f"{MOD}.dynamodb") as m_dynamo, \
            patch.multiple(guard, quarantine_blocks_download=block_on,
                           compliance_asset_state_table=table):
        m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        m_dynamo.Table.return_value = MagicMock()
        response = ewv2.lambda_handler(event, MagicMock())
    return response, m_sfn


@pytest.mark.unit
class TestACallersRunIsQuarantineGuarded:
    def test_a_quarantined_input_asset_refuses_the_run_before_it_starts(self):
        table = _state_table(_quarantined())
        response, m_sfn = _run(_event(body=ONE_INPUT), table)

        assert response["statusCode"] == 400
        assert json.loads(response["body"]) == {"message": INPUT_MESSAGE}
        table.get_item.assert_called_once_with(Key={"databaseId": "db1", "assetId": "a1"})
        m_sfn.start_execution.assert_not_called()

    def test_the_refusal_is_logged_once_at_info_without_a_traceback(self):
        with captured_logs(ewv2) as loggers:
            _run(_event(body=ONE_INPUT), _state_table(_quarantined()))
        assert_refusal_logged_once(loggers, "db1", "a1", "POST", "/workflows/db1/wf1/execute")

    def test_a_granted_exception_lets_the_run_start(self):
        response, m_sfn = _run(_event(body=ONE_INPUT),
                               _state_table(_quarantined(exception_granted=True)))
        assert response["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()

    def test_an_input_asset_without_a_compliance_record_runs(self):
        response, m_sfn = _run(_event(body=ONE_INPUT), _state_table(None))
        assert response["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()

    def test_a_caller_refused_on_the_input_asset_gets_403_and_no_state_read(self):
        enforcer = _allow_enforcer()
        enforcer.enforce.side_effect = lambda obj, action: obj.get("object__type") != "asset"
        table = _state_table(_quarantined())
        response, m_sfn = _run(_event(body=ONE_INPUT), table, enforcer=enforcer)
        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        m_sfn.start_execution.assert_not_called()

    def test_each_input_asset_is_read_once_however_many_files_it_has(self):
        body = {"inputFiles": [
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
            {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/g.glb"}]}
        table = _state_table(_quarantined())
        _run(_event(body=body), table)
        table.get_item.assert_called_once_with(Key={"databaseId": "db1", "assetId": "a1"})


@pytest.mark.unit
class TestRunsThatAreNotRefused:
    def test_a_run_vams_launches_as_system_user_is_not_refused(self):
        event = dict(_event(body=ONE_INPUT), lambdaCrossCall={"userName": "SYSTEM_USER"})
        table = _state_table(_quarantined())
        response, m_sfn = _run(event, table)
        assert response["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()
        table.get_item.assert_not_called()

    def test_a_delegated_call_for_a_named_user_is_refused(self):
        """[control] A re-run invokes the handler as the calling user, not as SYSTEM_USER."""
        event = dict(_event(body=ONE_INPUT), lambdaCrossCall={"userName": "user1"})
        response, m_sfn = _run(event, _state_table(_quarantined()))
        assert response["statusCode"] == 400
        m_sfn.start_execution.assert_not_called()

    def test_with_the_block_off_the_run_starts_and_no_table_is_read(self):
        table = _state_table(_quarantined())
        response, m_sfn = _run(_event(body=ONE_INPUT), table, block_on=False)
        assert response["statusCode"] == 200
        m_sfn.start_execution.assert_called_once()
        table.get_item.assert_not_called()
