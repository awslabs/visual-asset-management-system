# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input bounds of the EDH Workstations add-on models.

Every malformed-input case is paired with a legitimate-input case so a bound can never be tightened
past what the launch wizard, the admin image form and the CLI legitimately send. Values relayed to
EDH (instance type, AMI id, stack name, job definition) are bounded here because EDH's own
validation is not something VAMS can rely on for a 400 rather than a 500.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError, parse

import models.addon.edh as edh_models

BOOTSTRAP_ID = "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30"


def _create_body(**overrides):
    body = {
        "bootstrapId": BOOTSTRAP_ID,
        "imageId": "42",
        "instanceType": "g5.xlarge",
        "name": "My desktop",
        "rootSizeGb": 100,
        "osFamily": "linux",
    }
    body.update(overrides)
    return body


def _record_body(**overrides):
    body = {
        "bootstrapId": BOOTSTRAP_ID,
        "sessionId": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b",
        "userId": "alice",
        "edhUid": "vfpmansl7byak6gq",
        "state": "ACTIVE",
        "edhState": "running",
        "name": "My desktop",
        "imageId": "42",
        "osFamily": "linux",
        "instanceType": "g5.xlarge",
        "createdAt": "2026-09-08T10:00:00Z",
        "updatedAt": "2026-09-08T10:05:00Z",
        "connectionAvailable": True,
    }
    body.update(overrides)
    return body


def _vdi_body(**overrides):
    body = {
        "amiId": "ami-0123456789abcdef0",
        "stackName": "Ubuntu 22 GPU",
        "baseOs": "ubuntu2204",
        "rootSize": 100,
        "virtualDesktopProfileId": 1,
    }
    body.update(overrides)
    return body


def _sim_body(**overrides):
    body = {
        "amiId": "ami-0123456789abcdef0",
        "stackName": "Sim node",
        "rootSize": 200,
        "targetNodeProfileId": 2,
    }
    body.update(overrides)
    return body


def _job_body(**overrides):
    body = {
        "jobDefinition": "arn:aws:batch:us-east-1:123456789012:job-definition/render:3",
        "name": "render job",
        "backend": "batch",
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestCreateWorkstationRequestModel:
    def test_accepts_a_well_formed_body(self):
        model = parse(event=_create_body(), model=edh_models.CreateWorkstationRequestModel)
        assert model.bootstrapId == BOOTSTRAP_ID
        assert model.instanceType == "g5.xlarge"
        assert model.rootSizeGb == 100
        assert model.osFamily == "linux"

    def test_windows_is_an_accepted_os_family(self):
        model = parse(event=_create_body(osFamily="windows"), model=edh_models.CreateWorkstationRequestModel)
        assert model.osFamily == "windows"

    def test_extra_keys_are_ignored(self):
        model = parse(event=_create_body(userId="someone-else"), model=edh_models.CreateWorkstationRequestModel)
        assert "userId" not in model.dict()

    def test_name_is_trimmed(self):
        model = parse(event=_create_body(name="  Padded  "), model=edh_models.CreateWorkstationRequestModel)
        assert model.name == "Padded"

    @pytest.mark.parametrize("field,value", [
        ("bootstrapId", "not-a-uuid"),
        ("bootstrapId", ""),
        ("imageId", ""),
        ("imageId", "x" * 257),
        ("instanceType", "G5.xlarge"),
        ("instanceType", "g5xlarge"),
        ("instanceType", "g5.xlarge; rm -rf"),
        ("name", "bad<name>"),
        ("name", ""),
        ("rootSizeGb", 9),
        ("rootSizeGb", 2049),
        ("osFamily", "macos"),
        ("osFamily", ""),
    ])
    def test_rejects_malformed_values(self, field, value):
        with pytest.raises(ValidationError):
            parse(event=_create_body(**{field: value}), model=edh_models.CreateWorkstationRequestModel)

    @pytest.mark.parametrize("field", ["bootstrapId", "imageId", "instanceType", "name", "rootSizeGb", "osFamily"])
    def test_required_fields(self, field):
        body = _create_body()
        del body[field]
        with pytest.raises(ValidationError):
            parse(event=body, model=edh_models.CreateWorkstationRequestModel)

    def test_root_size_bounds_are_the_exported_constants(self):
        assert (edh_models.ROOT_SIZE_GB_MIN, edh_models.ROOT_SIZE_GB_MAX) == (10, 2048)
        parse(event=_create_body(rootSizeGb=10), model=edh_models.CreateWorkstationRequestModel)
        parse(event=_create_body(rootSizeGb=2048), model=edh_models.CreateWorkstationRequestModel)

    @pytest.mark.parametrize("instance_type", ["g5.xlarge", "g4dn.12xlarge", "u-6tb1.metal", "c7i.metal-24xl"])
    def test_every_size_the_config_and_catalog_accept_is_accepted(self, instance_type):
        model = parse(event=_create_body(instanceType=instance_type), model=edh_models.CreateWorkstationRequestModel)
        assert model.instanceType == instance_type

    def test_the_instance_type_pattern_is_exported_for_other_surfaces(self):
        assert edh_models.edh_instance_type_pattern == r"^[a-z0-9-]+\.[a-z0-9-]+\Z"

    def test_lease_hours_accepts_valid_value(self):
        model = parse(event=_create_body(leaseHours=5), model=edh_models.CreateWorkstationRequestModel)
        assert model.leaseHours == 5

    @pytest.mark.parametrize("hours", [1, 720])
    def test_lease_hours_accepts_both_ends_of_the_range(self, hours):
        # The bounds are inclusive, so a ge/le written as gt/lt fails here rather than in the field.
        model = parse(event=_create_body(leaseHours=hours), model=edh_models.CreateWorkstationRequestModel)
        assert model.leaseHours == hours

    def test_lease_hours_defaults_to_none(self):
        model = parse(event=_create_body(), model=edh_models.CreateWorkstationRequestModel)
        assert model.leaseHours is None

    def test_lease_hours_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            parse(event=_create_body(leaseHours=0), model=edh_models.CreateWorkstationRequestModel)
        with pytest.raises(ValidationError):
            parse(event=_create_body(leaseHours=721), model=edh_models.CreateWorkstationRequestModel)


@pytest.mark.unit
class TestWorkstationRecordModel:
    def test_accepts_a_full_row(self):
        model = parse(event=_record_body(), model=edh_models.WorkstationRecordModel)
        assert model.state == "ACTIVE"
        assert model.connectionAvailable is True

    def test_session_and_edh_state_are_optional(self):
        # A CREATING row exists before EDH has reported a session.
        body = _record_body(state="CREATING", connectionAvailable=False)
        for key in ("sessionId", "edhState"):
            del body[key]
        model = parse(event=body, model=edh_models.WorkstationRecordModel)
        assert model.sessionId is None
        assert model.edhState is None
        assert model.connectionAvailable is False

    def test_connection_available_defaults_to_false(self):
        # A row without the attribute projects as not connectable rather than failing to project.
        body = _record_body()
        del body["connectionAvailable"]
        model = parse(event=body, model=edh_models.WorkstationRecordModel)
        assert model.connectionAvailable is False
        assert model.dict()["connectionAvailable"] is False

    def test_launch_bookkeeping_defaults_to_no_error_and_no_attempts(self):
        # A row written before the launch worker has run carries neither attribute; it still projects.
        model = parse(event=_record_body(), model=edh_models.WorkstationRecordModel)
        assert model.lastError is None
        assert model.launchAttempts == 0
        projected = model.dict()
        assert projected["lastError"] is None
        assert projected["launchAttempts"] == 0

    def test_launch_bookkeeping_is_returned_as_stored(self):
        model = parse(
            event=_record_body(state="ORPHANED", lastError="LAUNCH_ERROR", launchAttempts=3),
            model=edh_models.WorkstationRecordModel,
        )
        assert model.lastError == "LAUNCH_ERROR"
        assert model.launchAttempts == 3

    @pytest.mark.parametrize("state", sorted(edh_models.WORKSTATION_STATES))
    def test_every_declared_state_is_accepted(self, state):
        assert parse(event=_record_body(state=state), model=edh_models.WorkstationRecordModel).state == state

    def test_states_are_the_five_of_the_design(self):
        assert set(edh_models.WORKSTATION_STATES) == {"CREATING", "ACTIVE", "STOPPED", "DELETED", "ORPHANED"}

    @pytest.mark.parametrize("field,value", [("state", "running"), ("osFamily", "macos")])
    def test_rejects_values_outside_the_vocabularies(self, field, value):
        with pytest.raises(ValidationError):
            parse(event=_record_body(**{field: value}), model=edh_models.WorkstationRecordModel)

    def test_the_root_size_is_returned_as_stored(self):
        model = parse(event=_record_body(rootSizeGb=100), model=edh_models.WorkstationRecordModel)
        assert model.rootSizeGb == 100
        assert model.dict()["rootSizeGb"] == 100

    def test_a_row_without_a_root_size_still_projects(self):
        # A row adopted from EDH never recorded the size the desktop was launched with.
        model = parse(event=_record_body(), model=edh_models.WorkstationRecordModel)
        assert model.rootSizeGb is None
        assert "rootSizeGb" in model.dict()

    def test_a_row_value_is_returned_as_stored_not_trimmed(self):
        # Response/record model: a stored name is never rewritten on the way out.
        model = parse(event=_record_body(name=" kept "), model=edh_models.WorkstationRecordModel)
        assert model.name == " kept "

    def test_row_internal_attributes_never_project(self):
        body = _record_body(**{field: f"stored-{field}" for field in edh_models.WORKSTATION_ROW_INTERNAL_FIELDS})
        projected = parse(event=body, model=edh_models.WorkstationRecordModel).dict()
        assert set(projected).isdisjoint(edh_models.WORKSTATION_ROW_INTERNAL_FIELDS)
        # Control: a declared attribute of the same body does project.
        assert projected["bootstrapId"] == BOOTSTRAP_ID

    @pytest.mark.parametrize("stamp", ["scheduleAppliedAt", "credentialClaimedAt", "startRequestedAt",
                                       "edhFailureReadAt"])
    def test_the_rows_own_stamps_never_project(self, stamp):
        # The sweep's schedule stamp, a renew's credential claim, a start's slot and the last read of a
        # failed launch's reason are bookkeeping the row keeps for itself.
        body = _record_body(**{stamp: "2026-09-28T12:00:00.000000+00:00"})
        projected = parse(event=body, model=edh_models.WorkstationRecordModel).dict()
        assert stamp not in projected
        # Control: a declared attribute of the same body does project.
        assert projected["bootstrapId"] == BOOTSTRAP_ID

    def test_the_row_internal_attributes_are_the_credential_placement_and_expiry_ones(self):
        assert set(edh_models.WORKSTATION_ROW_INTERNAL_FIELDS) == {
            "apiKeyId", "secretArn", "secretName", "keyExpiresAt", "instanceId", "secretBoundInstanceId", "expiresAt"}
        assert set(edh_models.WORKSTATION_ROW_INTERNAL_FIELDS).isdisjoint(edh_models.WorkstationRecordModel.__fields__)

    def test_a_row_without_a_launch_failure_reason_projects_null(self):
        projected = parse(event=_record_body(), model=edh_models.WorkstationRecordModel).dict()
        assert "edhFailure" in projected
        assert projected["edhFailure"] is None

    def test_a_stored_launch_failure_reason_is_returned_as_stored(self):
        failure = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                   "operation": "CreateCapacityReservation", "observedAt": "2026-10-22T15:23:00+00:00"}
        body = _record_body(state="ORPHANED", lastError="CAPACITY_UNAVAILABLE", edhFailure=dict(failure))
        assert parse(event=body, model=edh_models.WorkstationRecordModel).dict()["edhFailure"] == failure

    def test_a_reason_without_an_aws_code_keeps_the_absent_keys_absent(self):
        failure = {"event": "session-failed", "checkpoint": "dcv_server_start",
                   "observedAt": "2026-10-22T15:23:00+00:00"}
        projected = parse(event=_record_body(edhFailure=dict(failure)), model=edh_models.WorkstationRecordModel)
        assert projected.dict()["edhFailure"] == failure

    def test_only_the_contract_keys_of_a_stored_reason_project(self):
        stored = {"event": "placement_failed", "observedAt": "2026-10-22T15:23:00+00:00",
                  "subStatus": "EDH text", "connection_string": "https://dcv.example.internal/?authToken=abc"}
        projected = parse(event=_record_body(edhFailure=stored), model=edh_models.WorkstationRecordModel).dict()
        assert projected["edhFailure"] == {"event": "placement_failed", "observedAt": "2026-10-22T15:23:00+00:00"}
        assert set(edh_models.EDH_FAILURE_FIELDS) == {"event", "errorCode", "operation", "checkpoint", "observedAt"}

    @pytest.mark.parametrize("stored", ["placement_failed", ["placement_failed"], 7, {"errorCode": "X"},
                                        {"event": 7}])
    def test_a_stored_reason_that_is_not_a_failure_map_projects_null(self, stored):
        projected = parse(event=_record_body(edhFailure=stored), model=edh_models.WorkstationRecordModel).dict()
        assert projected["edhFailure"] is None

    def test_lease_fields_carry_values(self):
        model = parse(
            event=_record_body(
                leaseExpiresAt="2026-09-16T20:00:00+00:00",
                leaseStoppedAt=None,
                leaseHoursAtLaunch=8,
            ),
            model=edh_models.WorkstationRecordModel,
        )
        dumped = model.dict()
        assert dumped["leaseExpiresAt"] == "2026-09-16T20:00:00+00:00"
        assert dumped["leaseHoursAtLaunch"] == 8

    def test_lease_fields_default_to_none(self):
        # A row written before leases existed must still project.
        model = parse(event=_record_body(), model=edh_models.WorkstationRecordModel)
        assert model.leaseExpiresAt is None
        assert model.leaseStoppedAt is None
        assert model.leaseHoursAtLaunch is None
        dumped = model.dict()
        assert dumped["leaseExpiresAt"] is None
        assert dumped["leaseStoppedAt"] is None
        assert dumped["leaseHoursAtLaunch"] is None


@pytest.mark.unit
class TestConnectResponseModel:
    def test_accepts_the_three_fields(self):
        model = parse(
            event={
                "sessionId": "abc",
                "connectionUrl": "https://dcv.example.internal/?authToken=x#abc",
                "osFamily": "linux",
            },
            model=edh_models.ConnectResponseModel,
        )
        assert model.connectionUrl.startswith("https://")

    def test_connection_url_is_required(self):
        with pytest.raises(ValidationError):
            parse(event={"sessionId": "abc", "osFamily": "linux"}, model=edh_models.ConnectResponseModel)


@pytest.mark.unit
class TestOsSignInResponseModel:
    def test_a_windows_local_account_carries_its_password(self):
        model = parse(
            event={"sessionId": "abc", "osFamily": "windows", "username": "vabc",
                   "credential": "desktopPassword", "password": "winpw-" + "x" * 12},
            model=edh_models.OsSignInResponseModel,
        )
        assert (model.osFamily, model.username, model.credential, model.password) == (
            "windows", "vabc", "desktopPassword", "winpw-" + "x" * 12)

    def test_a_windows_directory_account_carries_no_password(self):
        model = parse(event={"sessionId": "abc", "osFamily": "windows", "username": "CORP\\alice",
                             "credential": "directoryPassword"},
                      model=edh_models.OsSignInResponseModel)
        assert model.password is None
        assert model.dict()["password"] is None

    def test_a_linux_desktop_needs_no_credential(self):
        model = parse(event={"sessionId": "abc", "osFamily": "linux", "username": "vabc", "credential": "none"},
                      model=edh_models.OsSignInResponseModel)
        assert model.dict() == {"sessionId": "abc", "osFamily": "linux", "username": "vabc",
                                "credential": "none", "password": None}

    def test_directory_account_is_not_a_field(self):
        model = parse(event={"sessionId": "abc", "osFamily": "linux", "username": "vabc", "credential": "none",
                             "directoryAccount": True},
                      model=edh_models.OsSignInResponseModel)
        assert "directoryAccount" not in model.dict()

    @pytest.mark.parametrize("missing", ["sessionId", "osFamily", "username", "credential"])
    def test_each_field_but_the_password_is_required(self, missing):
        body = {"sessionId": "abc", "osFamily": "linux", "username": "vabc", "credential": "none"}
        body.pop(missing)
        with pytest.raises(ValidationError):
            parse(event=body, model=edh_models.OsSignInResponseModel)

    @pytest.mark.parametrize("field, value", [("credential", "windowsPassword"), ("osFamily", "macos")])
    def test_an_unknown_value_is_rejected(self, field, value):
        body = {"sessionId": "abc", "osFamily": "linux", "username": "vabc", "credential": "none", field: value}
        with pytest.raises(ValidationError):
            parse(event=body, model=edh_models.OsSignInResponseModel)

    @pytest.mark.parametrize("credential, password", [
        ("desktopPassword", None), ("directoryPassword", "p"), ("none", "p")])
    def test_a_password_comes_with_the_desktop_password_credential_only(self, credential, password):
        body = {"sessionId": "abc", "osFamily": "windows", "username": "vabc", "credential": credential,
                "password": password}
        with pytest.raises(ValidationError):
            parse(event=body, model=edh_models.OsSignInResponseModel)


@pytest.mark.unit
class TestRegisterVdiImageRequestModel:
    def test_accepts_a_well_formed_body(self):
        model = parse(event=_vdi_body(), model=edh_models.RegisterVdiImageRequestModel)
        assert model.amiId == "ami-0123456789abcdef0"
        assert model.launchTenancy is None
        assert model.description is None

    def test_short_ami_ids_are_accepted(self):
        model = parse(event=_vdi_body(amiId="ami-01234567"), model=edh_models.RegisterVdiImageRequestModel)
        assert model.amiId == "ami-01234567"

    def test_stack_name_and_description_are_trimmed(self):
        model = parse(
            event=_vdi_body(stackName="  Ubuntu  ", description="  GPU image  "),
            model=edh_models.RegisterVdiImageRequestModel,
        )
        assert model.stackName == "Ubuntu"
        assert model.description == "GPU image"

    @pytest.mark.parametrize("field,value", [
        ("amiId", "ami-xyz"),
        ("amiId", "AMI-0123456789abcdef0"),
        ("amiId", "ami-0123456789abcdef01"),
        ("stackName", "bad<stack>"),
        ("baseOs", ""),
        ("baseOs", "x" * 65),
        ("baseOs", "ubuntu-22.04"),
        ("baseOs", "Ubuntu 22.04"),
        ("baseOs", "windows_2022"),
        ("baseOs", "2022windows"),
        ("rootSize", 0),
        ("rootSize", 2049),
        ("virtualDesktopProfileId", -1),
        ("launchTenancy", "x" * 31),
        ("description", "x" * 257),
    ])
    def test_rejects_malformed_values(self, field, value):
        with pytest.raises(ValidationError):
            parse(event=_vdi_body(**{field: value}), model=edh_models.RegisterVdiImageRequestModel)

    def test_the_ami_pattern_is_exported(self):
        assert edh_models.edh_ami_id_pattern == r"^ami-[0-9a-f]{8,17}\Z"

    @pytest.mark.parametrize("base_os", ["ubuntu2204", "amazonlinux2023", "windows2022", "rhel9"])
    def test_every_base_os_key_edh_configures_is_accepted(self, base_os):
        model = parse(event=_vdi_body(baseOs=base_os), model=edh_models.RegisterVdiImageRequestModel)
        assert model.baseOs == base_os

    def test_a_malformed_base_os_is_refused_naming_the_field(self):
        with pytest.raises(ValidationError) as excinfo:
            parse(event=_vdi_body(baseOs="ubuntu-22.04"), model=edh_models.RegisterVdiImageRequestModel)
        assert ("baseOs",) in [error["loc"] for error in excinfo.value.errors()]

    def test_the_base_os_pattern_is_exported(self):
        assert edh_models.edh_base_os_pattern == r"^[a-z][a-z0-9]{1,63}\Z"


@pytest.mark.unit
class TestRegisterSimulationImageRequestModel:
    def test_accepts_a_well_formed_body(self):
        model = parse(event=_sim_body(), model=edh_models.RegisterSimulationImageRequestModel)
        assert model.targetNodeProfileId == 2
        assert model.userDataVariables is None

    def test_user_data_variables_accept_the_declared_maximum(self):
        model = parse(
            event=_sim_body(userDataVariables="x" * 16384),
            model=edh_models.RegisterSimulationImageRequestModel,
        )
        assert len(model.userDataVariables) == 16384

    def test_stack_name_and_description_are_trimmed(self):
        model = parse(
            event=_sim_body(stackName="  Sim  ", description="  Target node image  "),
            model=edh_models.RegisterSimulationImageRequestModel,
        )
        assert model.stackName == "Sim"
        assert model.description == "Target node image"

    @pytest.mark.parametrize("field,value", [
        ("amiId", "ami-xyz"),
        ("stackName", ""),
        ("rootSize", 0),
        ("targetNodeProfileId", -1),
        ("targetNodeUserDataId", 0),
        ("targetNodeUserDataId", "one"),
        ("userDataVariables", "x" * 16385),
        ("description", "x" * 257),
    ])
    def test_rejects_malformed_values(self, field, value):
        with pytest.raises(ValidationError):
            parse(event=_sim_body(**{field: value}), model=edh_models.RegisterSimulationImageRequestModel)

    def test_the_user_data_template_defaults_to_edh_s_first_template(self):
        assert parse(event=_sim_body(), model=edh_models.RegisterSimulationImageRequestModel).targetNodeUserDataId == 1
        model = parse(event=_sim_body(targetNodeUserDataId=3), model=edh_models.RegisterSimulationImageRequestModel)
        assert model.targetNodeUserDataId == 3


@pytest.mark.unit
class TestSubmitContainerJobRequestModel:
    def test_accepts_a_minimal_body(self):
        model = parse(event=_job_body(), model=edh_models.SubmitContainerJobRequestModel)
        assert model.backend == "batch"
        assert model.command is None
        assert model.environment is None
        assert model.gpu is None

    def test_accepts_a_full_body(self):
        model = parse(
            event=_job_body(
                backend="eks",
                jobQueue="arn:aws:batch:us-east-1:123456789012:job-queue/gpu",
                command=["python", "train.py", "--epochs", "3"],
                environment={"MODE": "train"},
                vcpus=4,
                memory=16384,
                gpu=1,
            ),
            model=edh_models.SubmitContainerJobRequestModel,
        )
        assert model.backend == "eks"
        assert model.command == ["python", "train.py", "--epochs", "3"]
        assert model.gpu == 1

    def test_gpu_zero_is_accepted(self):
        assert parse(event=_job_body(gpu=0), model=edh_models.SubmitContainerJobRequestModel).gpu == 0

    def test_backends_are_the_two_of_the_design(self):
        assert set(edh_models.CONTAINER_BACKENDS) == {"batch", "eks"}

    @pytest.mark.parametrize("field,value", [
        ("backend", "k8s"),
        ("backend", ""),
        ("jobDefinition", ""),
        ("jobDefinition", "x" * 257),
        ("jobQueue", "x" * 257),
        ("name", "bad<job>"),
        ("command", ["x"] * 65),
        ("command", ["x" * 1025]),
        ("environment", {f"K{i}": "v" for i in range(65)}),
        ("environment", {"K": "x" * 1025}),
        ("environment", {"k" * 257: "v"}),
        ("vcpus", 0),
        ("memory", 0),
        ("gpu", -1),
    ])
    def test_rejects_malformed_values(self, field, value):
        with pytest.raises(ValidationError):
            parse(event=_job_body(**{field: value}), model=edh_models.SubmitContainerJobRequestModel)

    def test_a_command_of_sixty_four_entries_is_accepted(self):
        model = parse(event=_job_body(command=["x"] * 64), model=edh_models.SubmitContainerJobRequestModel)
        assert len(model.command) == 64

    def test_an_environment_name_batch_reserves_is_refused_for_the_batch_backend(self):
        with pytest.raises(ValidationError, match="environment names cannot start with AWS_BATCH for the batch backend"):
            parse(event=_job_body(environment={"MODE": "train", "AWS_BATCH_JOB_ID": "x"}),
                  model=edh_models.SubmitContainerJobRequestModel)

    @pytest.mark.parametrize("backend,environment", [
        ("eks", {"AWS_BATCH_JOB_ID": "x"}),
        ("batch", {"MY_AWS_BATCH": "x"}),
    ])
    def test_an_environment_name_batch_does_not_reserve_is_accepted(self, backend, environment):
        model = parse(event=_job_body(backend=backend, environment=environment),
                      model=edh_models.SubmitContainerJobRequestModel)
        assert model.environment == environment
