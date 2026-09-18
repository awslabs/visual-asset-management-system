# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The three locking concurrencyRestrictions on the execute path, end to end through lambda_handler.

perAsset, perInputFile and perInputFileVersion are all rows on WorkflowExecutionLocksStorageTable, taken
with a conditional put immediately before the run's first side effect. The restriction decides which
identity the key carries — asset, file, or file version — and therefore what a second launch collides
with. Two properties the earlier by-asset inspection guard could not give hold here by construction and
are asserted directly:

  - A launch is never refused because of an asset's HISTORY. The lock table holds only rows of running
    executions, so however many executions an asset has had, a launch with nothing running proceeds with
    no "limit could not be confirmed" caveat. (The guard walked past executions under a budget, and a
    spent budget on a deep-history asset used to be answered with a 400 that no retry could clear — the
    SYSTEM_USER trigger-dispatch path included.)
  - A real conflict is ALWAYS refused, whatever the history depth and whichever identity launched it.

The harness (`_run`, `_LockTable`, the fixtures) is the perInputFileVersion suite's, imported absolutely.
"""

import json

import pytest

from tests.handlers.workflows.test_execute_per_input_file_version_lock import (
    WF_DB, WF_ID, _LockTable, _run, el,
)

ONE_FILE = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
SIBLING_FILE = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/g.glb"}]}
TWO_FILES = {"inputFiles": [
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
    {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/g.glb"},
]}

ASSET_KEY = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "", "", scope=el.LOCK_SCOPE_ASSET)
FILE_F_KEY = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "", scope=el.LOCK_SCOPE_ASSET_FILE)
FILE_G_KEY = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/g.glb", "", scope=el.LOCK_SCOPE_ASSET_FILE)
VERSION_F_KEY = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "v-resolved")


def _locked(table):
    return [p["Item"]["lockKey"] for p in table.puts]


def _message(response):
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestEachRestrictionLocksItsOwnIdentity:
    def test_per_asset_takes_one_lock_for_the_asset_however_many_files_are_selected(self):
        table = _LockTable()
        response, sfn, _log, _stop = _run(table, restriction="perAsset", body=TWO_FILES)
        assert response["statusCode"] == 200, response["body"]
        assert _locked(table) == [ASSET_KEY]
        assert table.puts[0]["Item"]["lockScope"] == el.LOCK_SCOPE_ASSET
        sfn.start_execution.assert_called_once()

    def test_per_input_file_takes_one_lock_per_selected_file(self):
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, restriction="perInputFile", body=TWO_FILES)
        assert response["statusCode"] == 200, response["body"]
        assert _locked(table) == [FILE_F_KEY, FILE_G_KEY]
        assert {p["Item"]["lockScope"] for p in table.puts} == {el.LOCK_SCOPE_ASSET_FILE}

    def test_per_input_file_version_takes_one_lock_per_selected_file_version(self):
        table = _LockTable()
        response, _sfn, _log, _stop = _run(table, restriction="perInputFileVersion", body=ONE_FILE)
        assert response["statusCode"] == 200, response["body"]
        assert _locked(table) == [VERSION_F_KEY]
        assert table.puts[0]["Item"]["lockScope"] == el.LOCK_SCOPE_ASSET_FILE_VERSION

    def test_the_lock_is_taken_before_the_state_machine_starts(self):
        for restriction in ("perAsset", "perInputFile", "perInputFileVersion"):
            table = _LockTable()
            _run(table, restriction=restriction, body=ONE_FILE)
            kinds = [entry[0] for entry in table.journal]
            assert kinds.index("lock") < kinds.index("start"), (restriction, table.journal)


@pytest.mark.unit
class TestAConflictIsRefusedAtTheRestrictionsGranularity:
    def test_per_asset_refuses_a_second_launch_on_a_sibling_file_of_the_same_asset(self):
        # The running execution reads f.glb; perAsset locks the whole asset, so g.glb collides too.
        table = _LockTable(held={ASSET_KEY: "E-running"})
        response, sfn, log, _stop = _run(table, restriction="perAsset", body=SIBLING_FILE)
        assert response["statusCode"] == 400, response["body"]
        assert _message(response) == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET]
        sfn.start_execution.assert_not_called()
        # Key and holder go to the log, never to the caller.
        assert "E-running" not in response["body"]
        assert any("E-running" in str(c) for c in log.warning.call_args_list)

    def test_per_input_file_lets_a_sibling_file_of_the_same_asset_launch(self):
        table = _LockTable(held={FILE_F_KEY: "E-running"})
        response, sfn, _log, _stop = _run(table, restriction="perInputFile", body=SIBLING_FILE)
        assert response["statusCode"] == 200, response["body"]
        assert _locked(table) == [FILE_G_KEY]
        sfn.start_execution.assert_called_once()

    def test_per_input_file_refuses_the_same_file(self):
        table = _LockTable(held={FILE_F_KEY: "E-running"})
        response, sfn, _log, _stop = _run(table, restriction="perInputFile", body=ONE_FILE)
        assert response["statusCode"] == 400, response["body"]
        assert _message(response) == el.LOCK_CONFLICT_MESSAGE_BY_SCOPE[el.LOCK_SCOPE_ASSET_FILE]
        sfn.start_execution.assert_not_called()

    def test_per_input_file_version_lets_the_same_file_launch_when_another_version_is_running(self):
        other_version = el.build_lock_key(WF_DB, WF_ID, "db1", "a1", "/a1/f.glb", "v-older")
        table = _LockTable(held={other_version: "E-running"})
        response, sfn, _log, _stop = _run(table, restriction="perInputFileVersion", body=ONE_FILE)
        assert response["statusCode"] == 200, response["body"]
        sfn.start_execution.assert_called_once()

    def test_a_conflict_on_the_second_key_releases_the_first(self):
        table = _LockTable(held={FILE_G_KEY: "E-running"})
        response, sfn, _log, _stop = _run(table, restriction="perInputFile", body=TWO_FILES)
        assert response["statusCode"] == 400
        assert [d["Key"]["lockKey"] for d in table.deletes] == [FILE_F_KEY]
        sfn.start_execution.assert_not_called()

    def test_every_conflict_body_carries_the_literals_the_cli_maps(self):
        for restriction, held in (("perAsset", ASSET_KEY), ("perInputFile", FILE_F_KEY),
                                  ("perInputFileVersion", VERSION_F_KEY)):
            table = _LockTable(held={held: "E-running"})
            response, _sfn, _log, _stop = _run(table, restriction=restriction, body=ONE_FILE)
            assert response["statusCode"] == 400, restriction
            assert "already running" in _message(response), restriction


@pytest.mark.unit
class TestHistoryNeverRefusesALaunch:
    """The lock table holds rows of RUNNING executions only. Finished executions leave no row (the
    terminal handlers release, and TTL expires a row whose release never ran), so an asset's depth of
    history is invisible to the launch: no budget, no caveat, no unclearable 400."""

    def test_no_running_execution_means_no_conflict_and_no_caveat_under_any_restriction(self):
        for restriction in ("perAsset", "perInputFile", "perInputFileVersion"):
            table = _LockTable()  # nothing held: however many executions have FINISHED
            response, sfn, _log, _stop = _run(table, restriction=restriction, body=ONE_FILE)
            assert response["statusCode"] == 200, (restriction, response["body"])
            warnings = _message(response).get("warnings") or []
            assert not any("could not be" in w or "limit" in w.lower() for w in warnings), (
                restriction, warnings)
            sfn.start_execution.assert_called_once()

    def test_the_launch_touches_the_lock_table_and_then_starts_and_nothing_else_decides(self):
        # The decision is the conditional put alone: the journal shows the lock, then the start, with
        # no read of any execution table in between. A history walk here is how a busy asset used to
        # spend a request's budget before a conflicting run was ever examined.
        table = _LockTable()
        _run(table, restriction="perAsset", body=ONE_FILE)
        assert [entry[0] for entry in table.journal] == ["lock", "start"]

    def test_an_expired_row_of_a_run_whose_release_never_ran_does_not_block(self):
        # _LockTable models a LIVE row as `held`; an expired row is one DynamoDB's condition admits, so
        # from the launch's side it is indistinguishable from no row. Pinned here so the harness's
        # `held` semantics are read as "unexpired" and not "any row".
        table = _LockTable(held={})
        response, _sfn, _log, _stop = _run(table, restriction="perAsset", body=ONE_FILE)
        assert response["statusCode"] == 200
        assert table.puts[0]["ConditionExpression"] == el._PUT_CONDITION
        assert ":now" in table.puts[0]["ExpressionAttributeValues"]


@pytest.mark.unit
class TestTheTriggerDispatchedPathIsGovernedByTheSameLock:
    """workflowTriggerDispatch invokes the handler as a lambdaCrossCall with SYSTEM_USER and reads only
    the status code: a 400 is dropped, a 200 is a launch. The lock decides both the same way it does
    for a user, so automation on a busy asset is neither starved by history nor let through a
    conflict."""

    @staticmethod
    def _cross_call_event():
        # The shape workflowTriggerDispatch sends: no headers, identity in lambdaCrossCall.
        return {
            "requestContext": {"http": {"method": "POST",
                                        "path": f"/workflows/{WF_DB}/{WF_ID}/execute"}},
            "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
            "queryStringParameters": {},
            "body": json.dumps(ONE_FILE),
            "lambdaCrossCall": {"userName": "SYSTEM_USER"},
        }

    def _launch(self, table, restriction):
        return _run(table, restriction=restriction, tokens=("SYSTEM_USER",), event=self._cross_call_event())

    def test_a_system_user_launch_with_nothing_running_proceeds(self):
        table = _LockTable()
        response, sfn, _log, _stop = self._launch(table, "perAsset")
        assert response["statusCode"] == 200
        sfn.start_execution.assert_called_once()

    def test_a_system_user_launch_into_a_running_asset_is_refused_with_a_400(self):
        table = _LockTable(held={ASSET_KEY: "E-running"})
        response, sfn, _log, _stop = self._launch(table, "perAsset")
        assert response["statusCode"] == 400
        sfn.start_execution.assert_not_called()
