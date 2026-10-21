# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch worker: its module-load contract, the EDH calls of a launch, the row it leaves behind,
and the audit labels on its records.

Driven through ``lambda_handler`` with the client, the table, the audit hook and the sleep replaced.
Every outcome is asserted on the row: completed with EDH's session id and state; ORPHANED with a
generic code when EDH refused (never EDH's own wording); or left CREATING -- and the failure raised --
when the outcome is unknown, so the retried invocation finds the row unchanged. The attempt counter is
asserted beside those, because it is what turns the last retry's transient failure into an ORPHANED
row rather than a launch nobody settles. One class runs the handler with the logger ``safeLogger``
really builds, because the suite's no-op stand-in accepts ``extra`` keys a LogRecord refuses.
"""

import importlib.util
import io
import json
import os
import sys

import pytest
from unittest.mock import MagicMock, call, patch

from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_DESKTOP_CREATE_FIELDS,
    EDH_ROUTE_DESKTOPS_SCHEDULE,
    EDH_SESSION_ID_FIELD,
    EDH_SESSION_NAME_FIELD,
    EDH_SESSION_OWNER_FIELD,
    EdhApiError,
    EdhAuthError,
    EdhCapacityError,
    EdhNotFoundError,
    EdhOutcomeUnknownError,
    EdhQuotaError,
    EdhUnavailableError,
    edh_session_name,
)
from backend.backend.handlers.addon.edh.common.edhIdentity import display_name_for, edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    EDH_FIELD_INSTANCE_ID,
    LAST_ERROR_CAPACITY_UNAVAILABLE,
    LAST_ERROR_EDH_REFUSED,
    LAST_ERROR_IMAGE_UNAVAILABLE,
    LAST_ERROR_LAUNCH_ERROR,
    LAST_ERROR_QUOTA_EXCEEDED,
    LAST_ERROR_USER_NOT_PROVISIONED,
    EDH_SCHEDULE_FIELD,
    EDH_SESSION_STATE_FIELD,
    never_auto_start_schedule,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    SESSION_OWN,
    USER,
    FakeEdhClient,
    Sequence,
    own_row,
)
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhTestSupport import FIXTURES_DIR, load_fixture
from backend.tests.handlers.addon.edh.edhWorkerTestSupport import (
    creating_row,
    desktop,
    edh_proxy_records,
    install_worker_fakes,
    update_values,
    updates,
    worker_event,
)
import backend.backend.handlers.addon.edh.edhLaunchWorker as worker

_EDH_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "edh",
)
_EDH_PACKAGE = "backend.backend.handlers.addon.edh"
_LOGGER_PATH = os.path.abspath(os.path.join(_EDH_DIR, "..", "..", "..", "customLogging", "logger.py"))
INSTANCE_ID = "i-0123456789abcdef0"
SECRET_NAME = f"vams/edh-session/{BOOTSTRAP_OWN}"
SECRET_ARN = f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{SECRET_NAME}-AbCdEf"

# wrapper name -> the capture of the request that wrapper sends
CAPTURES = {
    "ensure_user": "ldap_user_get_by_uid.json",
    "find_desktop_by_name": "virtual_desktops_list_all.json",
    "create_desktop": "virtual_desktops_create.json",
    "update_desktop_schedule": "virtual_desktops_schedule.json",
}

# (wrapper, the error it raises, the code the row keeps): refusals EDH would repeat.
REFUSALS = [
    ("ensure_user", EdhNotFoundError("User does not seems to exist."), LAST_ERROR_USER_NOT_PROVISIONED),
    ("create_desktop", EdhQuotaError("Max number of Linux or Windows session already reached"),
     LAST_ERROR_QUOTA_EXCEEDED),
    ("create_desktop", EdhNotFoundError("Software Stack not found"), LAST_ERROR_IMAGE_UNAVAILABLE),
    ("create_desktop", EdhCapacityError("Unable to find available capacity in all subnets provided"),
     LAST_ERROR_CAPACITY_UNAVAILABLE),
    ("create_desktop", EdhApiError("Instance type g5.xlarge is not allowed for this stack"), LAST_ERROR_EDH_REFUSED),
]

# (wrapper, the error it raises): failures that say nothing about the launch itself. A not-found answer to
# either read of the cluster listing says nothing of a desktop a create asked for.
TRANSIENT = [
    ("create_desktop", EdhUnavailableError("read timeout")),
    ("ensure_user", EdhAuthError("rejected", status=401)),
    # The client raises a directory that could not be searched as unavailable, never as not-found, so a
    # directory outage retries instead of ending the launch USER_NOT_PROVISIONED.
    ("ensure_user", EdhUnavailableError("The EDH directory could not be searched")),
    ("create_desktop", RuntimeError("unexpected")),
    ("find_desktop_by_name", EdhNotFoundError("EDH has no such route for GET /api/dcv/virtual_desktops/list_all",
                                              status=404)),
]
TRANSIENT_IDS = [type(error).__name__ for _, error in TRANSIENT]

# case -> (the stored row or None, the EDH client): one launch per log statement lambda_handler can
# reach, built fresh per case because the client and the row are consumed.
LOGGED_LAUNCHES = {
    "completed": lambda: (creating_row(), _launch_finding(desktop())),
    "not_listed_yet": lambda: (creating_row(), FakeEdhClient(find_desktop_by_name=None)),
    "refused": lambda: (creating_row(), FakeEdhClient(ensure_user=EdhNotFoundError("User does not seems to exist."))),
    "orphaned_on_last_attempt": lambda: (creating_row(launchAttempts=worker.MAX_LAUNCH_ATTEMPTS - 1),
                                         FakeEdhClient(create_desktop=EdhUnavailableError("read timeout"))),
    "retried_create": lambda: (creating_row(launchAttempts=1), FakeEdhClient(find_desktop_by_name=None)),
    "row_missing": lambda: (None, FakeEdhClient()),
}

# Failures of the schedule call that leave the launch complete: a refusal, and EDH unreachable or silent.
SCHEDULE_FAILURES = [
    EdhApiError("Your administrator has disabled virtual desktops schedule update"),
    EdhUnavailableError("read timeout"),
    EdhOutcomeUnknownError("no answer"),
    EdhNotFoundError("Unable to find this session"),
]


def _load_fresh(unique_suffix):
    """Load an independent copy of edhLaunchWorker by file path (no auth stubs: the worker imports none)."""
    path = os.path.abspath(os.path.join(_EDH_DIR, "edhLaunchWorker.py"))
    spec = importlib.util.spec_from_file_location(
        f"{_EDH_PACKAGE}.edhLaunchWorker_under_test_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


def _real_logger(stream, service):
    """The Powertools logger the deployed worker gets from safeLogger, writing to ``stream``.

    The root conftest replaces ``customLogging.logger`` with a no-op, so the real module is loaded by
    path. Powertools initialises the logger behind a service name once per process, so every caller
    names its own service or inherits another case's stream."""
    spec = importlib.util.spec_from_file_location("customLogging_logger_real", _LOGGER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.safeLogger(service=service, service_name="EdhLaunchWorker", stream=stream)


def _capture(fixture_name):
    with open(os.path.join(FIXTURES_DIR, fixture_name), encoding="utf-8") as handle:
        return json.load(handle)


def _run(fakes, row):
    return fakes.module.lambda_handler(worker_event(row), {})


def _calls(fakes):
    return [name for name, _ in fakes.edh.calls]


def _launch_finding(desktop_record):
    """A client whose first lookup finds nothing and whose lookup after the create finds ``desktop_record``."""
    return FakeEdhClient(find_desktop_by_name=Sequence(None, desktop_record))


def _provisioned_row(**overrides):
    """A CREATING row whose launch minted a session credential."""
    return creating_row(apiKeyId="key-1", secretName=SECRET_NAME, secretArn=SECRET_ARN,
                        keyExpiresAt="2026-09-08T20:00:00Z", **overrides)


def _placed_desktop(**fields):
    """A running desktop EDH lists with the EC2 instance it was placed on."""
    return desktop(**{EDH_FIELD_INSTANCE_ID: INSTANCE_ID, **fields})


def _install_bind(monkeypatch, error=None):
    """Replace the secret binding; records the (row, instance_id) it was asked for."""
    if error is not None:
        bind = MagicMock(side_effect=error)
    else:
        bind = MagicMock(side_effect=lambda row, instance_id: {"secretBoundInstanceId": instance_id})
    monkeypatch.setattr(worker.bootstrap, "bind_session_secret", bind)
    return bind


@pytest.mark.unit
class TestWorkerModuleLoad:
    def test_import_raises_when_the_table_name_is_unresolvable(self):
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh("no_table")
        assert "SSM unreachable" in str(excinfo.value)

    def test_import_raises_when_the_default_project_is_missing(self, monkeypatch):
        monkeypatch.delenv("EDH_VDI_DEFAULT_PROJECT", raising=False)
        with pytest.raises(KeyError) as excinfo:
            _load_fresh("missing_project")
        assert "EDH_VDI_DEFAULT_PROJECT" in str(excinfo.value)

    def test_import_succeeds_and_configuration_is_bound_when_names_resolve(self, monkeypatch):
        """Positive control for the cases above: the same load with a good environment."""
        monkeypatch.setenv("EDH_VDI_DEFAULT_PROJECT", "default")
        module = _load_fresh("ok")

        assert module.edh_session_table is not None
        # The break-glass override the test conftests set is what the name resolves to here.
        assert module.edh_session_table_name == os.environ["EDH_SESSION_STORAGE_TABLE_NAME"]
        assert module.vdi_default_project == "default"
        assert set(module.WORKER_AUDIT_ROUTES) == {"ensure_user", "find_desktop_by_name", "create_desktop",
                                                   "update_desktop_schedule"}


@pytest.mark.unit
class TestLaunch:
    def test_a_launch_provisions_the_user_creates_the_desktop_and_completes_the_row(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        result = _run(fakes, row)

        assert _calls(fakes) == ["ensure_user", "find_desktop_by_name", "create_desktop", "find_desktop_by_name",
                                 "update_desktop_schedule"]
        fakes.client_factory.assert_called_once_with(profile="batch")
        assert fakes.edh.calls_to("ensure_user") == [(edh_uid_for(USER), display_name_for(USER))]
        (edh_uid, form), = fakes.edh.calls_to("create_desktop")
        assert edh_uid == edh_uid_for(USER)
        assert form == {
            worker.EDH_CREATE_FIELD_INSTANCE_TYPE: "g5.xlarge",
            worker.EDH_CREATE_FIELD_SOFTWARE_STACK_ID: "42",
            worker.EDH_CREATE_FIELD_DISK_SIZE: "100",
            worker.EDH_CREATE_FIELD_SESSION_NAME: edh_session_name(BOOTSTRAP_OWN),
            worker.EDH_CREATE_FIELD_PROJECT: worker.vdi_default_project,
            worker.EDH_CREATE_FIELD_SESSION_TYPE: "virtual",
        }
        assert set(form) <= EDH_DESKTOP_CREATE_FIELDS
        assert {SESSION_OWN, "ACTIVE", "running", True} <= set(update_values(fakes))
        assert (result["outcome"], result["sessionId"], result["state"]) == ("launched", SESSION_OWN, "ACTIVE")
        fakes.sleep.assert_not_called()

    def test_a_pending_desktop_completes_the_session_id_and_keeps_the_row_creating(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(
            monkeypatch, row=row, edh=_launch_finding(desktop(state="pending", connection_string="")))

        result = _run(fakes, row)

        values = update_values(fakes)
        assert {SESSION_OWN, "pending"} <= set(values)
        assert "ACTIVE" not in values
        assert (result["state"], result["sessionId"]) == ("CREATING", SESSION_OWN)

    def test_a_desktop_edh_already_lists_is_not_created_again(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=desktop()))

        result = _run(fakes, row)

        assert "create_desktop" not in _calls(fakes)
        assert "find_desktop_by_name" in _calls(fakes)
        assert SESSION_OWN in update_values(fakes)
        assert result["sessionId"] == SESSION_OWN

    @pytest.mark.parametrize("stored, counted", [({}, 1), ({"launchAttempts": 1}, 2)])
    def test_each_start_is_counted_on_the_row_before_edh_is_asked(self, monkeypatch, stored, counted):
        row = creating_row(**stored)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=desktop()))
        edh_calls_at_write = []
        fakes.table.update_item.side_effect = lambda **kw: edh_calls_at_write.append(len(fakes.edh.calls))

        _run(fakes, row)

        assert updates(fakes)[0] == {"launchAttempts": counted}
        assert edh_calls_at_write[0] == 0


@pytest.mark.unit
class TestSecretBinding:
    def test_a_correlated_desktop_with_an_instance_binds_the_session_secret(self, monkeypatch):
        row = _provisioned_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(_placed_desktop()))
        bind = _install_bind(monkeypatch)

        result = _run(fakes, row)

        bound_row, instance_id = bind.call_args.args
        assert (bound_row["bootstrapId"], bound_row["secretName"], instance_id) == (BOOTSTRAP_OWN, SECRET_NAME, INSTANCE_ID)
        completed = updates(fakes)[-1]
        assert (completed["sessionId"], completed["instanceId"], completed["secretBoundInstanceId"]) == (
            SESSION_OWN, INSTANCE_ID, INSTANCE_ID)
        assert (result["outcome"], result["state"]) == ("launched", "ACTIVE")

    def test_a_read_back_of_a_running_pool_desktop_is_active_and_connectable(self, monkeypatch):
        # The cluster listing carries no connection string; a running desktop it lists is connectable.
        row = _provisioned_row()
        listed = {
            EDH_SESSION_ID_FIELD: SESSION_OWN,
            EDH_SESSION_STATE_FIELD: "running",
            EDH_FIELD_INSTANCE_ID: INSTANCE_ID,
            EDH_SESSION_OWNER_FIELD: row["edhUid"],
            EDH_SESSION_NAME_FIELD: edh_session_name(row["bootstrapId"]),
        }
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(listed))
        _install_bind(monkeypatch)

        result = _run(fakes, row)

        completed = updates(fakes)[-1]
        assert (completed["state"], completed["connectionAvailable"]) == ("ACTIVE", True)
        assert (completed["instanceId"], completed["secretBoundInstanceId"]) == (INSTANCE_ID, INSTANCE_ID)
        assert (result["outcome"], result["state"], result["sessionId"]) == ("launched", "ACTIVE", SESSION_OWN)

    def test_a_desktop_without_an_instance_yet_leaves_the_secret_unbound(self, monkeypatch):
        row = _provisioned_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))
        bind = _install_bind(monkeypatch)
        log = MagicMock()
        monkeypatch.setattr(worker, "logger", log)

        result = _run(fakes, row)

        bind.assert_not_called()
        completed = updates(fakes)[-1]
        assert completed["sessionId"] == SESSION_OWN
        assert "secretBoundInstanceId" not in completed and "instanceId" not in completed
        assert result["state"] == "ACTIVE"
        # The deferral is logged once, and the record names nothing of the desktop or the credential.
        assert log.warning.call_count == 1
        (message,) = log.warning.call_args.args
        assert "sweep" in message
        assert not any(value in message for value in (SESSION_OWN, BOOTSTRAP_OWN, SECRET_NAME, USER))

    def test_a_row_without_a_secret_binds_nothing_but_records_the_instance(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(_placed_desktop()))
        bind = _install_bind(monkeypatch)

        _run(fakes, row)

        bind.assert_not_called()
        completed = updates(fakes)[-1]
        assert completed["instanceId"] == INSTANCE_ID
        assert "secretBoundInstanceId" not in completed

    def test_a_failed_binding_still_completes_the_row(self, monkeypatch):
        row = _provisioned_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(_placed_desktop()))
        _install_bind(monkeypatch, error=worker.bootstrap.SessionBootstrapError("denied"))

        result = _run(fakes, row)

        completed = updates(fakes)[-1]
        assert (completed["sessionId"], completed["instanceId"]) == (SESSION_OWN, INSTANCE_ID)
        assert "secretBoundInstanceId" not in completed
        assert (result["outcome"], result["state"], result["sessionId"]) == ("launched", "ACTIVE", SESSION_OWN)

    def test_a_binding_error_that_is_not_the_bootstraps_own_is_raised(self, monkeypatch):
        # Only a failure the bootstrap reports is left to the sweep; anything else fails the invocation,
        # and the row stays CREATING without a session id.
        row = _provisioned_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(_placed_desktop()))
        _install_bind(monkeypatch, error=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            _run(fakes, row)

        assert not any("sessionId" in written for written in updates(fakes))


@pytest.mark.unit
class TestSchedule:
    """Once the worker holds the desktop's session id it sets the desktop's EDH schedule to one that never
    starts it, on the launch's own client and audited like its other calls. The stamp rides the completion
    write; a schedule EDH will not take leaves the launch complete for the sweep to finish."""

    def test_a_first_launch_sets_the_schedule_and_the_completion_write_stamps_it(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        result = _run(fakes, row)

        scheduled = fakes.edh.calls_to("update_desktop_schedule")
        assert scheduled, "the schedule was not set"
        assert len(scheduled) <= 1
        assert all(args == (edh_uid_for(USER), SESSION_OWN, never_auto_start_schedule()) for args in scheduled)
        # The launch's own batch client carries the call, inside the launch's time budget.
        assert {c.kwargs.get("profile") for c in fakes.client_factory.call_args_list} == {"batch"}
        completed = updates(fakes)[-1]
        assert (completed["sessionId"], completed["state"]) == (SESSION_OWN, "ACTIVE")
        assert completed["scheduleAppliedAt"]
        assert (result["outcome"], result["state"]) == ("launched", "ACTIVE")

    def test_the_schedule_call_is_audited_under_the_launch(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        _run(fakes, row)

        scheduled = [record for record in edh_proxy_records(fakes)
                     if record["edhRoute"] == EDH_ROUTE_DESKTOPS_SCHEDULE]
        assert scheduled, "the schedule call was not audited"
        assert all(record == {"operation": "workstationCreate", "edhUid": edh_uid_for(USER), "edhMethod": "PUT",
                              "edhRoute": EDH_ROUTE_DESKTOPS_SCHEDULE, "edhStatus": "ok"} for record in scheduled)

    def test_a_desktop_edh_already_lists_on_that_schedule_is_stamped_without_a_call(self, monkeypatch):
        row = creating_row()
        listed = desktop(**{EDH_SCHEDULE_FIELD: json.dumps(never_auto_start_schedule())})
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(listed))

        _run(fakes, row)

        assert "update_desktop_schedule" not in _calls(fakes)
        assert updates(fakes)[-1]["scheduleAppliedAt"]

    def test_the_default_schedule_edh_lists_a_new_desktop_on_is_replaced(self, monkeypatch):
        row = creating_row()
        default = json.dumps({**never_auto_start_schedule(), "monday": {"start": 480, "stop": 1140}})
        fakes = install_worker_fakes(monkeypatch, row=row,
                                     edh=_launch_finding(desktop(**{EDH_SCHEDULE_FIELD: default})))

        _run(fakes, row)

        assert "update_desktop_schedule" in _calls(fakes)
        assert updates(fakes)[-1]["scheduleAppliedAt"]

    @pytest.mark.parametrize("error", SCHEDULE_FAILURES, ids=[type(error).__name__ for error in SCHEDULE_FAILURES])
    def test_a_schedule_edh_does_not_take_leaves_the_launch_complete_and_unstamped(self, monkeypatch, error):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            find_desktop_by_name=Sequence(None, desktop()), update_desktop_schedule=error))

        result = _run(fakes, row)

        completed = updates(fakes)[-1]
        assert (completed["sessionId"], completed["state"]) == (SESSION_OWN, "ACTIVE")
        assert "scheduleAppliedAt" not in completed
        assert not any(written.get("state") == "ORPHANED" for written in updates(fakes))
        assert (result["outcome"], result["state"], result["sessionId"]) == ("launched", "ACTIVE", SESSION_OWN)
        assert edh_proxy_records(fakes)[-1]["edhStatus"] == type(error).__name__

    def test_a_schedule_call_that_fails_outside_edh_is_raised(self, monkeypatch):
        # Only an EDH failure is left to the sweep; anything else fails the invocation, and no write
        # completes the row or stamps it.
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            find_desktop_by_name=Sequence(None, desktop()), update_desktop_schedule=ValueError("unreadable")))

        with pytest.raises(ValueError):
            _run(fakes, row)

        assert "update_desktop_schedule" in _calls(fakes)
        assert not any("scheduleAppliedAt" in written or "sessionId" in written for written in updates(fakes))

    def test_a_failed_schedule_on_the_last_attempt_does_not_orphan_the_row(self, monkeypatch):
        row = creating_row(launchAttempts=worker.MAX_LAUNCH_ATTEMPTS - 1)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            find_desktop_by_name=desktop(), update_desktop_schedule=EdhUnavailableError("read timeout")))

        result = _run(fakes, row)

        assert (result["state"], result["sessionId"]) == ("ACTIVE", SESSION_OWN)
        assert not any(written.get("lastError") for written in updates(fakes))

    def test_a_row_already_stamped_is_not_scheduled_again(self, monkeypatch):
        row = creating_row(scheduleAppliedAt="2026-09-08T12:00:00+00:00")
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        result = _run(fakes, row)

        assert "update_desktop_schedule" not in _calls(fakes)
        assert result["sessionId"] == SESSION_OWN

    def test_a_desktop_edh_has_not_listed_yet_is_left_for_the_sweep(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=None))

        _run(fakes, row)

        assert "update_desktop_schedule" not in _calls(fakes)
        assert not any("scheduleAppliedAt" in written for written in updates(fakes))

    def test_a_refused_launch_sets_no_schedule(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            create_desktop=EdhQuotaError("Max number of Linux or Windows session already reached")))

        _run(fakes, row)

        assert "update_desktop_schedule" not in _calls(fakes)


@pytest.mark.unit
class TestRetriedLaunch:
    """A retried invocation may follow a create that timed out while EDH was still executing it."""

    def test_a_retry_waits_for_the_previous_attempts_desktop_before_creating_another(self, monkeypatch):
        row = creating_row(launchAttempts=1)
        fakes = install_worker_fakes(monkeypatch, row=row,
                                     edh=FakeEdhClient(find_desktop_by_name=Sequence(None, None, desktop())))

        result = _run(fakes, row)

        assert "create_desktop" not in _calls(fakes)
        assert len(fakes.edh.calls_to("find_desktop_by_name")) == 3
        assert fakes.sleep.call_args_list == [call(worker.FIND_DESKTOP_INTERVAL_SECONDS)] * 2
        assert (result["outcome"], result["sessionId"]) == ("launched", SESSION_OWN)

    def test_a_retry_that_finds_no_desktop_creates_one_and_says_so(self, monkeypatch):
        row = creating_row(launchAttempts=1)
        misses = [None] * worker.FIND_DESKTOP_ATTEMPTS
        fakes = install_worker_fakes(monkeypatch, row=row,
                                     edh=FakeEdhClient(find_desktop_by_name=Sequence(*misses, desktop())))
        recorder = MagicMock()
        monkeypatch.setattr(worker, "logger", recorder)

        result = _run(fakes, row)

        calls = _calls(fakes)
        # The whole wait ran before the create; the read-back then found the desktop at once.
        assert calls.count("create_desktop") == 1
        assert calls.index("create_desktop") == 1 + worker.FIND_DESKTOP_ATTEMPTS
        assert calls.count("find_desktop_by_name") == worker.FIND_DESKTOP_ATTEMPTS + 1
        assert any(BOOTSTRAP_OWN in str(logged) for logged in recorder.warning.call_args_list)
        assert result["sessionId"] == SESSION_OWN

    def test_a_first_attempt_probes_once_before_creating(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))
        recorder = MagicMock()
        monkeypatch.setattr(worker, "logger", recorder)

        _run(fakes, row)

        assert _calls(fakes).index("create_desktop") == 2
        fakes.sleep.assert_not_called()
        recorder.warning.assert_not_called()


@pytest.mark.unit
class TestNoUuidYet:
    def test_a_desktop_edh_has_not_listed_yet_leaves_the_row_creating(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=None))

        result = _run(fakes, row)

        assert len(fakes.edh.calls_to("find_desktop_by_name")) == 1 + worker.FIND_DESKTOP_ATTEMPTS
        assert fakes.sleep.call_args_list == (
            [call(worker.FIND_DESKTOP_INTERVAL_SECONDS)] * (worker.FIND_DESKTOP_ATTEMPTS - 1))
        # The attempt counter is the only thing written: no session id, no state.
        written = updates(fakes)
        assert written, "the launch start was not counted"
        assert all(set(attributes) == {"launchAttempts"} for attributes in written)
        assert (result["outcome"], result["sessionId"], result["state"]) == ("launched", None, "CREATING")

    def test_the_lookup_fits_the_batch_budget(self):
        assert worker.FIND_DESKTOP_ATTEMPTS * worker.FIND_DESKTOP_INTERVAL_SECONDS <= 60


@pytest.mark.unit
class TestRefusals:
    @pytest.mark.parametrize("wrapper, error, code", REFUSALS, ids=[code for _, _, code in REFUSALS])
    def test_a_definite_refusal_orphans_the_row_with_a_generic_code(self, monkeypatch, wrapper, error, code):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(**{wrapper: error}))

        result = _run(fakes, row)

        values = update_values(fakes)
        assert {"ORPHANED", code, False} <= set(values)
        # EDH's wording is logged, never stored: no word of it reaches the row.
        assert str(error).split()[0] not in json.dumps(values)
        assert edh_proxy_records(fakes)[-1]["edhStatus"] == type(error).__name__
        assert (result["outcome"], result["state"], result["sessionId"]) == ("launched", "ORPHANED", None)

    def test_a_user_the_directory_will_not_provision_is_never_given_a_desktop(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(
            monkeypatch, row=row, edh=FakeEdhClient(ensure_user=EdhNotFoundError("User does not seems to exist.")))

        _run(fakes, row)

        assert "create_desktop" not in _calls(fakes)
        assert "find_desktop_by_name" not in _calls(fakes)

    def test_a_desktop_in_error_orphans_with_the_launch_error_code(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(
            monkeypatch, row=row, edh=_launch_finding(desktop(state="error", connection_string="")))

        result = _run(fakes, row)

        assert {"ORPHANED", LAST_ERROR_LAUNCH_ERROR} <= set(update_values(fakes))
        assert result["state"] == "ORPHANED"


@pytest.mark.unit
class TestTransientFailures:
    @pytest.mark.parametrize("wrapper, error", TRANSIENT, ids=TRANSIENT_IDS)
    def test_before_the_last_attempt_the_failure_is_raised_and_the_row_stays_creating(
            self, monkeypatch, wrapper, error):
        row = creating_row(launchAttempts=worker.MAX_LAUNCH_ATTEMPTS - 2)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(**{wrapper: error}))

        with pytest.raises(type(error)):
            _run(fakes, row)

        written = updates(fakes)
        assert written, "the launch start was not counted"
        assert all(set(attributes) == {"launchAttempts"} for attributes in written)
        assert written[0]["launchAttempts"] == worker.MAX_LAUNCH_ATTEMPTS - 1
        assert edh_proxy_records(fakes)[-1]["edhStatus"] == type(error).__name__

    @pytest.mark.parametrize("wrapper, error", TRANSIENT, ids=TRANSIENT_IDS)
    def test_on_the_last_attempt_the_row_is_orphaned_with_launch_error(self, monkeypatch, wrapper, error):
        row = creating_row(launchAttempts=worker.MAX_LAUNCH_ATTEMPTS - 1)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(**{wrapper: error}))

        result = _run(fakes, row)

        assert updates(fakes)[0] == {"launchAttempts": worker.MAX_LAUNCH_ATTEMPTS}
        values = update_values(fakes)
        assert {"ORPHANED", LAST_ERROR_LAUNCH_ERROR, False} <= set(values)
        assert str(error).split()[0] not in json.dumps(values)
        assert (result["outcome"], result["state"]) == ("launched", "ORPHANED")

    def test_a_first_start_that_fails_transiently_is_raised(self, monkeypatch):
        """Control for the counter: a row never started before is nowhere near its last attempt."""
        row = creating_row()
        fakes = install_worker_fakes(
            monkeypatch, row=row, edh=FakeEdhClient(create_desktop=EdhUnavailableError("read timeout")))

        with pytest.raises(EdhUnavailableError):
            _run(fakes, row)

        assert updates(fakes) == [{"launchAttempts": 1}]


@pytest.mark.unit
class TestReadBackNotFound:
    """A create EDH accepted may leave a desktop EDH lists later, so a not-found answer to the read-back after
    it is transient: the row stays CREATING for a retried invocation, which looks for that desktop before it
    creates one, and the last attempt orphans the row as any transient failure does."""

    def test_before_the_last_attempt_the_row_stays_creating_and_the_failure_is_raised(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=Sequence(
            None, EdhNotFoundError("EDH has no such route for GET /api/dcv/virtual_desktops/list_all", status=404))))

        with pytest.raises(EdhNotFoundError):
            _run(fakes, row)

        assert _calls(fakes) == ["ensure_user", "find_desktop_by_name", "create_desktop", "find_desktop_by_name"]
        assert updates(fakes) == [{"launchAttempts": 1}]

    def test_on_the_last_attempt_the_row_is_orphaned_with_launch_error(self, monkeypatch):
        row = creating_row(launchAttempts=worker.MAX_LAUNCH_ATTEMPTS - 1)
        misses = [None] * worker.FIND_DESKTOP_ATTEMPTS
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=Sequence(
            *misses, EdhNotFoundError("EDH has no such route for GET /api/dcv/virtual_desktops/list_all",
                                      status=404))))

        result = _run(fakes, row)

        assert _calls(fakes).count("create_desktop") == 1
        assert _calls(fakes)[-1] == "find_desktop_by_name"
        values = update_values(fakes)
        assert {"ORPHANED", LAST_ERROR_LAUNCH_ERROR, False} <= set(values)
        assert LAST_ERROR_IMAGE_UNAVAILABLE not in values
        assert (result["outcome"], result["state"]) == ("launched", "ORPHANED")

    def test_the_retried_invocation_adopts_the_desktop_the_create_asked_for(self, monkeypatch):
        row = creating_row()
        table = ConditionalRowTable(**row)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(find_desktop_by_name=Sequence(
            None, EdhNotFoundError("EDH has no such route for GET /api/dcv/virtual_desktops/list_all", status=404),
            desktop())))
        fakes.table.get_item.side_effect = table.read
        fakes.table.update_item.side_effect = table

        with pytest.raises(EdhNotFoundError):
            _run(fakes, row)
        retried = _run(fakes, row)

        assert _calls(fakes).count("create_desktop") == 1
        assert (retried["outcome"], retried["state"], retried["sessionId"]) == ("launched", "ACTIVE", SESSION_OWN)
        assert (table.item["state"], table.item["sessionId"], table.item["launchAttempts"]) == (
            "ACTIVE", SESSION_OWN, 2)
        assert table.item.get("lastError") is None


@pytest.mark.unit
class TestIdempotence:
    def test_a_row_that_already_has_a_session_is_left_alone(self, monkeypatch):
        row = own_row()
        fakes = install_worker_fakes(monkeypatch, row=row)

        result = _run(fakes, row)

        assert (result["outcome"], result["state"]) == ("settled", "ACTIVE")
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_an_orphaned_row_is_not_launched_again(self, monkeypatch):
        row = creating_row(state="ORPHANED", lastError=LAST_ERROR_QUOTA_EXCEEDED)
        fakes = install_worker_fakes(monkeypatch, row=row)

        result = _run(fakes, row)

        assert result["outcome"] == "settled"
        fakes.client_factory.assert_not_called()
        assert fakes.edh.calls == []
        fakes.table.update_item.assert_not_called()

    @pytest.mark.parametrize("since_read", [
        {"launchAttempts": 1},
        {"sessionId": SESSION_OWN},
        {"state": "ORPHANED", "lastError": LAST_ERROR_LAUNCH_ERROR},
    ], ids=["another invocation counted the attempt", "a read bound the session id", "the sweep orphaned it"])
    def test_an_invocation_whose_row_moved_on_since_its_read_settles_without_asking_edh(
            self, monkeypatch, since_read):
        # Lambda delivers an asynchronous invocation at least once: a duplicate reads the row alongside the
        # first, and whichever counts the attempt second leaves the launch to the other.
        row = creating_row()
        table = ConditionalRowTable(**{**row, **since_read})
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))
        fakes.table.update_item.side_effect = table

        result = _run(fakes, row)

        assert result["outcome"] == "settled"
        fakes.client_factory.assert_not_called()
        assert fakes.edh.calls == []
        assert table.item == {**row, **since_read}

    def test_of_two_invocations_that_read_the_row_together_only_one_creates_a_desktop(self, monkeypatch):
        row = creating_row()
        read = dict(row)
        table = ConditionalRowTable(**row)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=FakeEdhClient(
            find_desktop_by_name=Sequence(None, desktop(), None, desktop())))
        fakes.table.get_item.side_effect = lambda **kwargs: {"Item": dict(read)}
        fakes.table.update_item.side_effect = table

        first, second = _run(fakes, row), _run(fakes, row)

        assert (first["outcome"], second["outcome"]) == ("launched", "settled")
        assert _calls(fakes).count("create_desktop") <= 1
        assert (table.item["sessionId"], table.item["launchAttempts"]) == (SESSION_OWN, 1)

    @pytest.mark.parametrize("stored", [{}, {"launchAttempts": 1}], ids=["first attempt", "retried attempt"])
    def test_an_invocation_counting_the_attempt_it_read_launches(self, monkeypatch, stored):
        # Control for the two above: a retry after a failed invocation reads the count that invocation left.
        row = creating_row(**stored)
        table = ConditionalRowTable(**row)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))
        fakes.table.update_item.side_effect = table

        result = _run(fakes, row)

        assert (result["outcome"], result["sessionId"]) == ("launched", SESSION_OWN)
        assert table.item["launchAttempts"] == int(stored.get("launchAttempts", 0)) + 1
        assert table.item["sessionId"] == SESSION_OWN

    def test_a_row_that_no_longer_exists_is_reported_and_not_launched(self, monkeypatch):
        fakes = install_worker_fakes(monkeypatch)

        result = _run(fakes, creating_row())

        assert result["outcome"] == "missing"
        fakes.client_factory.assert_not_called()
        assert fakes.edh.calls == []

    def test_the_row_is_read_consistently_so_the_just_written_row_is_seen(self, monkeypatch):
        # The service puts the row and invokes the worker in the same request; an eventually consistent
        # read may miss the put and report the launch missing, which nothing retries.
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        _run(fakes, row)

        assert fakes.table.get_item.call_args.kwargs == {"Key": {"bootstrapId": BOOTSTRAP_OWN}, "ConsistentRead": True}

    def test_a_payload_without_a_bootstrap_id_is_refused(self, monkeypatch):
        fakes = install_worker_fakes(monkeypatch)

        with pytest.raises(ValueError):
            fakes.module.lambda_handler({}, {})

        fakes.table.get_item.assert_not_called()


@pytest.mark.unit
class TestRealLogger:
    """A ``logging.LogRecord`` refuses any ``extra`` key it already owns (``name`` among them), and the
    suite's no-op logger cannot show that. Each case runs the handler with the logger safeLogger really
    builds and reads the records it wrote."""

    @pytest.mark.parametrize("case", sorted(LOGGED_LAUNCHES))
    def test_every_log_statement_is_accepted_and_the_invocation_names_the_launch(self, monkeypatch, case):
        stored, edh = LOGGED_LAUNCHES[case]()
        fakes = install_worker_fakes(monkeypatch, row=stored, edh=edh)
        stream = io.StringIO()
        # safeLogger installs its own uncaught-exception hook; monkeypatch puts the test runner's back.
        monkeypatch.setattr(sys, "excepthook", sys.excepthook)
        monkeypatch.setattr(worker, "logger", _real_logger(stream, f"EdhLaunchWorker.{case}"))

        result = _run(fakes, creating_row())

        assert result["outcome"] in {"launched", "missing"}
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert records, "nothing was logged"
        assert any(record.get("launch", {}).get("bootstrapId") == BOOTSTRAP_OWN for record in records)
        # Nothing of the payload is spread over the record's own attributes.
        assert all("bootstrapId" not in record for record in records)


@pytest.mark.unit
class TestWorkerAuditRoutes:
    def test_every_audited_wrapper_has_a_capture(self):
        """Control: the map and the capture table name the same wrappers, so no entry goes unchecked."""
        assert set(worker.WORKER_AUDIT_ROUTES) == set(CAPTURES)

    @pytest.mark.parametrize("wrapper", sorted(CAPTURES))
    def test_the_audit_labels_match_the_captured_request(self, wrapper):
        verb, route = worker.WORKER_AUDIT_ROUTES[wrapper]
        request = _capture(CAPTURES[wrapper])["request"]
        assert verb == request["method"]
        assert route in (request["path"], request.get("absolutePath"))

    def test_the_create_form_names_only_fields_the_captured_create_sent(self):
        """The form field constants are EDH-side assumptions, checked against the source-derived create."""
        capture = _capture(CAPTURES["create_desktop"])
        form = worker._create_form(creating_row())

        assert set(form) <= set(capture["request"]["body"])
        assert set(form) <= EDH_DESKTOP_CREATE_FIELDS
        # The captured create named its session after a bootstrap id, as the form does.
        assert len(form[worker.EDH_CREATE_FIELD_SESSION_NAME]) == len(capture["expect"]["sessionName"])


@pytest.mark.unit
class TestAuditIdentity:
    def test_every_record_is_attributed_to_the_launching_user_as_a_cross_call(self, monkeypatch):
        row = creating_row()
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        _run(fakes, row)

        assert fakes.log_actions.call_args_list, "nothing was audited"
        assert all(audit_call.args[0]["lambdaCrossCall"] == {"userName": USER, "mfaEnabled": False}
                   for audit_call in fakes.log_actions.call_args_list)
        records = edh_proxy_records(fakes)
        assert records
        assert all(record["edhUid"] == edh_uid_for(USER) for record in records)
        assert {record["operation"] for record in records} == {"workstationCreate"}


@pytest.mark.unit
class TestSessionType:
    """The DCV session type a create names: EDH refuses a virtual session for Windows, and EDH's own
    default resolves to a console session on images that have no token verifier for one."""

    def test_a_windows_row_creates_a_console_session(self):
        form = worker._create_form(creating_row(osFamily="windows"))
        assert form[worker.EDH_CREATE_FIELD_SESSION_TYPE] == worker.EDH_SESSION_TYPE_CONSOLE == "console"

    def test_a_linux_row_creates_a_virtual_session(self):
        form = worker._create_form(creating_row(osFamily="linux"))
        assert form[worker.EDH_CREATE_FIELD_SESSION_TYPE] == worker.EDH_SESSION_TYPE_VIRTUAL == "virtual"

    @pytest.mark.parametrize("os_family", [None, "", "macos"])
    def test_any_other_family_creates_a_virtual_session(self, os_family):
        row = creating_row(osFamily=os_family)
        if os_family is None:
            row.pop("osFamily")
        assert worker._create_form(row)[worker.EDH_CREATE_FIELD_SESSION_TYPE] == "virtual"

    @pytest.mark.parametrize("os_family,capture", [
        ("linux", "desktop_create"),
        ("windows", "desktop_create_windows"),
    ])
    def test_the_session_type_is_the_one_the_captured_create_of_the_family_sent(self, os_family, capture):
        sent = load_fixture(capture)["request"]["body"][worker.EDH_CREATE_FIELD_SESSION_TYPE]
        form = worker._create_form(creating_row(osFamily=os_family))
        assert form[worker.EDH_CREATE_FIELD_SESSION_TYPE] == sent

    def test_the_field_is_one_the_client_sends(self):
        assert worker.EDH_CREATE_FIELD_SESSION_TYPE in EDH_DESKTOP_CREATE_FIELDS

    def test_a_windows_launch_sends_a_console_session_to_edh(self, monkeypatch):
        row = creating_row(osFamily="windows")
        fakes = install_worker_fakes(monkeypatch, row=row, edh=_launch_finding(desktop()))

        _run(fakes, row)

        (_edh_uid, form), = fakes.edh.calls_to("create_desktop")
        assert form[worker.EDH_CREATE_FIELD_SESSION_TYPE] == "console"
