# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the v2.6 -> v2.7 migration's command line: the step choices, the release flags, the
dry-run default, the account-id confirmation gate, and the exit-code folding of main().

Run: python -m pytest test_v2_6_to_v2_7_cli.py -q
"""

import importlib.util
import io
import json
import logging
import os
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))

ACCOUNT = "123456789012"
OTHER_ACCOUNT = "210987654321"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v26_to_v27_migration", os.path.join(_HERE, "v2.6_to_v2.7_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _write_config(tmp_path, **extra):
    values = {"resource_names_ssm_param_prefix": "/vams-x/resourceNames"}
    values.update(extra)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(values))
    return str(path)


#######################
# Fakes for the AWS surface main() and confirm_migration_target() touch
#######################


class _FakeSts:
    def __init__(self, account=ACCOUNT, error=None):
        self._account = account
        self._error = error

    def get_caller_identity(self):
        if self._error is not None:
            raise self._error
        return {"Account": self._account, "Arn": "caller-arn"}


class _RecordingDynamoDB:
    """Answers the reads every step makes and records every write it is asked for.

    The rows seeded are one retired built-in's trigger (would be deleted), one live workflow's trigger
    (kept), the live workflow row, one RUNNING execution of a retired workflow, and the two retired
    definitions as archived rows - enough for every step to have work to report on."""

    LIVE_WORKFLOW = ("db1", "wf-live")

    def __init__(self):
        self.reads = []
        self.mutations = []

    def query(self, **kwargs):
        self.reads.append(("query", kwargs))
        if kwargs.get("IndexName") == mig.TRIGGERS_BY_BASE_TYPE_GSI:
            retired = mig.RETIRED_WORKFLOW_IDS[0]
            return {
                "Items": [
                    {
                        "workflowDatabaseId:workflowId": {"S": f"GLOBAL:{retired}"},
                        "triggerType": {"S": "fileUpload"},
                        "workflowDatabaseId": {"S": "GLOBAL"},
                        "workflowId": {"S": retired},
                    },
                    {
                        "workflowDatabaseId:workflowId": {"S": "db1:wf-live"},
                        "triggerType": {"S": "fileUpload"},
                        "workflowDatabaseId": {"S": "db1"},
                        "workflowId": {"S": "wf-live"},
                    },
                ]
            }
        if kwargs.get("IndexName") == mig.EXECUTIONS_BY_WORKFLOW_GSI:
            return {
                "Items": [
                    {
                        "workflowExecutionId": {"S": "exec-1"},
                        "executionStatus": {"S": "RUNNING"},
                        "executionStartDate": {"S": "2026-01-01T00:00:00Z"},
                    }
                ]
            }
        return {"Items": []}

    def scan(self, **kwargs):
        self.reads.append(("scan", kwargs))
        return {
            "Items": [
                {
                    "databaseId": {"S": "db1"},
                    "workflowId": {"S": "wf-live"},
                    "archived": {"BOOL": False},
                    "specifiedPipelines": {
                        "L": [{"M": {"pipelineDatabaseId:pipelineId": {"S": "db1:p1"}}}]
                    },
                }
            ]
        }

    def get_item(self, **kwargs):
        self.reads.append(("get_item", kwargs))
        key = kwargs["Key"]
        database_id = key["databaseId"]["S"]
        record_id = (key.get("workflowId") or key.get("pipelineId"))["S"]
        if (database_id, record_id) == self.LIVE_WORKFLOW:
            return {"Item": {"databaseId": {"S": database_id}, "workflowId": {"S": record_id},
                             "archived": {"BOOL": False}}}
        if database_id == "GLOBAL" and record_id in mig.RETIRED_PIPELINE_IDS:
            return {"Item": {"databaseId": {"S": database_id}, "archived": {"BOOL": True},
                             "enabled": {"BOOL": False}}}
        return {}

    # Every write the client offers is recorded, never performed.
    def delete_item(self, **kwargs):
        self.mutations.append(("delete_item", kwargs))
        return {}

    def put_item(self, **kwargs):
        self.mutations.append(("put_item", kwargs))
        return {}

    def update_item(self, **kwargs):
        self.mutations.append(("update_item", kwargs))
        return {}

    def batch_write_item(self, **kwargs):
        self.mutations.append(("batch_write_item", kwargs))
        return {}


class _RecordingLambda:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["Payload"])
        body = {"operation": payload["operation"], "reindexRunId": "r-dry", "phase": "done",
                "dryRun": payload["dryRun"], "deleted": 0, "enqueued": 0, "chunks": 0,
                "continued": False, "invocations": 1}
        wire = json.dumps({"statusCode": 200, "body": json.dumps(body)}).encode()
        return {"StatusCode": 200, "Payload": io.BytesIO(wire)}


class _FakeSession:
    region_name = "us-west-2"

    def __init__(self, sts, dynamodb=None, lambda_client=None):
        self._clients = {"sts": sts, "dynamodb": dynamodb, "lambda": lambda_client}

    def client(self, name, **kwargs):
        client = self._clients.get(name)
        assert client is not None, f"unexpected client {name}"
        return client


class _FakeLookup:
    """SsmResourceLookup stand-in that resolves every parameter key to a name derived from it."""

    def __init__(self, base_param_prefix, profile=None, region=None):
        self.base_param_prefix = base_param_prefix

    def resolve(self, param_key):
        return "name-for-" + param_key.replace("/", "-")


def _install_fake_boto3(monkeypatch, sts, dynamodb=None, lambda_client=None):
    session = _FakeSession(sts, dynamodb, lambda_client)
    monkeypatch.setattr(mig, "boto3", SimpleNamespace(Session=lambda **kwargs: session))
    return session


def _no_prompt(monkeypatch):
    def refuse(prompt=""):
        raise AssertionError("input() must not be called")

    monkeypatch.setattr("builtins.input", refuse)


def _tty(monkeypatch, is_tty):
    monkeypatch.setattr(mig.sys, "stdin", SimpleNamespace(isatty=lambda: is_tty))


#######################
# Parser
#######################


def test_parser_steps_choices_are_the_release_steps():
    parser = mig.build_parser()
    steps = next(action for action in parser._actions if action.dest == "steps")
    assert tuple(steps.choices) == (
        "orphanedTriggers",
        "vectorBackfill",
        "systemPipelineRetirement",
        "all",
    )
    assert steps.default == "all"
    assert mig.STEP_CHOICES == tuple(steps.choices)


def test_parser_accepts_the_release_flags():
    namespace = mig.build_parser().parse_args(
        [
            "--config", "c.json", "--steps", "vectorBackfill", "--clear-vectors", "--async",
            "--execute", "--limit", "5", "--profile", "p", "--region", "us-west-2",
            "--log-level", "DEBUG", "--confirm-account", ACCOUNT,
        ]
    )
    assert namespace.config == "c.json"
    assert namespace.steps == "vectorBackfill"
    assert namespace.clear_vectors is True
    assert namespace.async_invoke is True
    assert namespace.dry_run is False
    assert namespace.limit == 5
    assert namespace.profile == "p"
    assert namespace.region == "us-west-2"
    assert namespace.log_level == "DEBUG"
    assert namespace.confirm_account == ACCOUNT


def test_parser_defaults_to_a_dry_run_with_every_other_switch_off():
    namespace = mig.build_parser().parse_args(["--config", "c.json"])
    assert namespace.steps == "all"
    assert namespace.clear_vectors is False
    assert namespace.async_invoke is False
    assert namespace.dry_run is True
    assert namespace.limit is None
    assert namespace.log_level == "INFO"
    assert namespace.confirm_account is None


@pytest.mark.parametrize("flag", ["--clear-indexes", "--operation"])
def test_parser_rejects_the_v26_only_flags(flag):
    with pytest.raises(SystemExit):
        mig.build_parser().parse_args(["--config", "c.json", flag, "both"])


@pytest.mark.parametrize("flag", ["--dry-run", "--yes"])
def test_parser_rejects_the_flags_the_dry_run_default_replaced(flag):
    # --dry-run is the default and needs no flag; --yes would skip the account confirmation.
    with pytest.raises(SystemExit):
        mig.build_parser().parse_args(["--config", "c.json", flag])


@pytest.mark.parametrize("value", ["12345", "abcdefghijkl", "1234567890123", ""])
def test_parser_confirm_account_requires_a_12_digit_id(value):
    with pytest.raises(SystemExit):
        mig.build_parser().parse_args(["--config", "c.json", "--confirm-account", value])


#######################
# main(): dry-run default and the gate's inputs
#######################


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--steps", "orphanedTriggers"], True),
        (["--steps", "systemPipelineRetirement"], False),
        (["--steps", "vectorBackfill"], True),
        (["--steps", "vectorBackfill", "--clear-vectors"], True),
        (["--steps", "all"], True),
    ],
)
def test_main_gates_every_run_that_deletes_or_bills(monkeypatch, tmp_path, argv, expected):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)

    rc = mig.main(["--config", _write_config(tmp_path), "--execute"] + argv)

    # The refused confirmation stops the run before any step; that is the only thing that ran.
    assert rc == 1
    assert seen["gated"] is expected
    assert seen["base_param_prefix"] == "/vams-x/resourceNames"
    assert seen["dry_run"] is False


def test_main_is_a_dry_run_without_execute(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)

    mig.main(["--config", _write_config(tmp_path)])

    assert seen["dry_run"] is True
    assert seen["gated"] is True  # the steps would delete and bill; dry_run is what keeps them from it


def test_main_gated_summary_names_what_a_real_run_deletes_and_bills(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)

    mig.main(["--config", _write_config(tmp_path), "--execute", "--clear-vectors"])

    assert "orphanedTriggers" in seen["gated_summary"]
    assert "--clear-vectors" in seen["gated_summary"]
    assert "Bedrock" in seen["gated_summary"]
    assert mig._gated_summary(False, False, False) == "nothing"
    # A plain backfill bills even though it deletes nothing.
    assert mig._gated_summary(False, True, False) == (
        "launch one Amazon Bedrock-billed execution per file (vectorBackfill)"
    )


def test_main_treats_a_placeholder_prefix_as_unset(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)
    config = _write_config(
        tmp_path, resource_names_ssm_param_prefix="<RESOURCE_NAMES_SSM_PARAM_PREFIX>"
    )

    mig.main(["--config", config])

    assert seen["base_param_prefix"] is None


def test_main_config_dry_run_true_overrides_execute(monkeypatch, tmp_path, caplog):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)
    config = _write_config(tmp_path, dry_run=True, clear_vectors=True)

    with caplog.at_level(logging.WARNING):
        mig.main(["--config", config, "--steps", "vectorBackfill", "--execute"])

    # The config latch wins over --execute, and says so.
    assert seen["dry_run"] is True
    assert "remains a DRY RUN" in caplog.text
    # clear_vectors from the config reaches the summary.
    assert "--clear-vectors" in seen["gated_summary"]


def test_main_config_dry_run_false_alone_does_not_make_a_real_run(monkeypatch, tmp_path):
    seen = {}

    def fake_confirm(**kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr(mig, "confirm_migration_target", fake_confirm)
    config = _write_config(tmp_path, dry_run=False)

    mig.main(["--config", config])

    assert seen["dry_run"] is True


def test_main_bare_invocation_with_the_shipped_config_writes_nothing(monkeypatch, tmp_path):
    """The shipped config plus no flags must run every step against the fakes without a single
    write: no DynamoDB mutation, and the only Lambda invoke carries dryRun: true."""
    shipped_path = os.path.join(_HERE, "v2.6_to_v2.7_migration_config.json")
    with open(shipped_path) as f:
        shipped = json.load(f)
    assert shipped["dry_run"] is True, "the shipped config must pin dry_run: true"
    # The operator's only required edit: the SSM prefix.
    shipped["resource_names_ssm_param_prefix"] = "/vams-x/resourceNames"
    config = tmp_path / "shipped.json"
    config.write_text(json.dumps(shipped))

    dynamodb = _RecordingDynamoDB()
    lambda_client = _RecordingLambda()
    _install_fake_boto3(monkeypatch, _FakeSts(), dynamodb, lambda_client)
    monkeypatch.setattr(mig, "SsmResourceLookup", _FakeLookup)
    _no_prompt(monkeypatch)
    _tty(monkeypatch, False)

    rc = mig.main(["--config", str(config)])

    assert rc == 0
    assert dynamodb.mutations == []
    # Every step ran: the trigger enumeration, the retirement report's reads, and the backfill invoke.
    read_kinds = {kind for kind, _ in dynamodb.reads}
    assert read_kinds == {"query", "get_item", "scan"}
    assert len(lambda_client.calls) == 1
    assert json.loads(lambda_client.calls[0]["Payload"]) == {"operation": "enqueue", "dryRun": True}


def test_main_runs_the_steps_in_order_and_folds_exit_codes(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(
        mig, "run_orphaned_triggers_step", lambda *a, **k: order.append("orphanedTriggers") or 0
    )

    def vector(*args, **kwargs):
        order.append("vectorBackfill")
        return 1

    monkeypatch.setattr(mig, "run_vector_backfill_step", vector)
    monkeypatch.setattr(
        mig,
        "run_system_pipeline_retirement_step",
        lambda *a, **k: order.append("systemPipelineRetirement") or 0,
    )

    rc = mig.main(["--config", _write_config(tmp_path)])

    assert rc == 1
    assert order == ["orphanedTriggers", "vectorBackfill", "systemPipelineRetirement"]


def test_main_runs_only_the_named_step(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(
        mig, "run_orphaned_triggers_step", lambda *a, **k: order.append("orphanedTriggers") or 0
    )
    monkeypatch.setattr(
        mig, "run_vector_backfill_step", lambda *a, **k: order.append("vectorBackfill") or 0
    )
    monkeypatch.setattr(
        mig,
        "run_system_pipeline_retirement_step",
        lambda *a, **k: order.append("systemPipelineRetirement") or 0,
    )

    rc = mig.main(["--config", _write_config(tmp_path), "--steps", "systemPipelineRetirement"])

    assert rc == 0
    assert order == ["systemPipelineRetirement"]


def test_main_passes_explicit_only_when_vector_backfill_is_named(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(mig, "confirm_migration_target", lambda **kwargs: True)
    monkeypatch.setattr(mig, "run_orphaned_triggers_step", lambda *a, **k: 0)
    monkeypatch.setattr(mig, "run_system_pipeline_retirement_step", lambda *a, **k: 0)
    monkeypatch.setattr(mig, "run_vector_backfill_step", lambda *a, **k: seen.append(k) or 0)
    config = _write_config(tmp_path)

    mig.main(["--config", config, "--steps", "vectorBackfill", "--clear-vectors"])
    mig.main(["--config", config])

    assert seen == [
        {"clear_vectors": True, "explicit": True},
        {"clear_vectors": False, "explicit": False},
    ]


#######################
# confirm_migration_target(): the gate itself
#######################

_GATED_SUMMARY = "delete orphaned workflow-trigger rows (orphanedTriggers)"


def _confirm(**overrides):
    values = {
        "profile": None,
        "region": None,
        "base_param_prefix": "/vams-x/resourceNames",
        "dry_run": False,
        "gated": True,
        "gated_summary": _GATED_SUMMARY,
        "confirm_account": None,
    }
    values.update(overrides)
    return mig.confirm_migration_target(**values)


def test_confirm_refuses_a_gated_run_when_confirm_account_mismatches(monkeypatch, caplog):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, True)

    with caplog.at_level(logging.ERROR):
        assert _confirm(confirm_account=OTHER_ACCOUNT) is False

    assert "does not match the resolved account" in caplog.text


def test_confirm_checks_confirm_account_on_a_dry_run_too(monkeypatch):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _no_prompt(monkeypatch)

    assert _confirm(dry_run=True, confirm_account=OTHER_ACCOUNT) is False
    assert _confirm(dry_run=True, confirm_account=ACCOUNT) is True


def test_confirm_refuses_confirm_account_when_the_identity_is_unresolved(monkeypatch, caplog):
    _install_fake_boto3(monkeypatch, _FakeSts(error=RuntimeError("AccessDenied")))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, True)

    with caplog.at_level(logging.ERROR):
        assert _confirm(confirm_account=ACCOUNT) is False

    assert "could not be resolved" in caplog.text


def test_confirm_refuses_a_gated_run_when_the_identity_is_unresolved(monkeypatch, caplog):
    # A terminal is available, but there is no account to type: the run is refused, not prompted.
    _install_fake_boto3(monkeypatch, _FakeSts(error=RuntimeError("AccessDenied")))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, True)

    with caplog.at_level(logging.ERROR):
        assert _confirm() is False

    assert "Refusing to run" in caplog.text
    assert "could not be resolved" in caplog.text


def test_confirm_refuses_a_gated_run_without_a_terminal(monkeypatch, caplog):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, False)

    with caplog.at_level(logging.ERROR):
        assert _confirm() is False

    assert "stdin is not a terminal" in caplog.text
    assert "--confirm-account" in caplog.text


@pytest.mark.parametrize("typed", [OTHER_ACCOUNT, "yes", "y", "", ACCOUNT[:-1]])
def test_confirm_refuses_anything_but_the_resolved_account_id_at_the_prompt(monkeypatch, typed):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _tty(monkeypatch, True)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)

    assert _confirm() is False


def test_confirm_accepts_the_resolved_account_id_typed_at_the_prompt(monkeypatch):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _tty(monkeypatch, True)
    prompts = []

    def answer(prompt=""):
        prompts.append(prompt)
        return f"  {ACCOUNT}\n"

    monkeypatch.setattr("builtins.input", answer)

    assert _confirm() is True
    assert len(prompts) == 1
    assert _GATED_SUMMARY in prompts[0]
    assert ACCOUNT in prompts[0]


def test_confirm_accepts_a_matching_confirm_account_without_a_terminal(monkeypatch):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, False)

    assert _confirm(confirm_account=ACCOUNT) is True


@pytest.mark.parametrize("overrides", [{"dry_run": True}, {"gated": False}])
def test_confirm_passes_an_ungated_run_without_a_prompt(monkeypatch, overrides):
    # Nothing is deleted or billed, so neither a terminal nor a resolved identity is required.
    _install_fake_boto3(monkeypatch, _FakeSts(error=RuntimeError("AccessDenied")))
    _no_prompt(monkeypatch)
    _tty(monkeypatch, False)

    assert _confirm(**overrides) is True


def test_confirm_echo_names_the_target_and_what_the_run_will_do(monkeypatch, caplog):
    _install_fake_boto3(monkeypatch, _FakeSts(ACCOUNT))
    _no_prompt(monkeypatch)

    with caplog.at_level(logging.INFO):
        _confirm(dry_run=True)
        _confirm(confirm_account=ACCOUNT)

    assert f"Account:      {ACCOUNT}" in caplog.text
    assert "Dry run:      True   (pass --execute for a real run)" in caplog.text
    assert "This run will: delete nothing and bill nothing" in caplog.text
    assert f"This run will: {_GATED_SUMMARY}" in caplog.text
