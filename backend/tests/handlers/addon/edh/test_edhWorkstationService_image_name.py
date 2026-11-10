# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The image a workstation record names, and whether a newer build of its recipe exists.

A launch stores the EDH image name and, for an image an AVAILABLE AMI recipe build registered, the recipe,
its display name and the build version on the row it creates, once. A row stored without them -- every row
launched before they were -- is resolved when read from the recipe builds that recorded the EDH stack they
registered, AVAILABLE and RETIRED alike, since a superseded build's stack leaves the catalog while its
desktops keep running. ``latestRecipeVersion`` and ``imageLatest`` are derived on every read from the same
scan and never stored. With AMI builds off the recipe table is not read; a read that fails is logged and
the records are served with what their rows stored. Every read of records scans the recipe table at most
once, whatever the number of rows.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as recipe_store
from backend.tests.handlers.addon.edh.edhAmiTestSupport import create_recipe_table
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    CATALOG_STACK,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

# The image fields of a record, in the order the cases below list their values.
IMAGE_FIELDS = ("imageName", "recipeId", "recipeDisplayName", "recipeVersion", "latestRecipeVersion", "imageLatest")
# The fields derived on every read, which no row carries.
DERIVED_FIELDS = ("latestRecipeVersion", "imageLatest")

ISAAC_DISPLAY_NAME = "Isaac Sim 6.2"
RETIRED_STACK_NAME = "VAMS Isaac Sim v12"
LATEST_STACK_NAME = "VAMS Isaac Sim v13"

# The stack the AVAILABLE Isaac Sim build registered, as EDH's catalog lists it.
RECIPE_STACK = {**CATALOG_STACK, service.EDH_FIELD_STACK_ID: 36, service.EDH_FIELD_STACK_NAME: LATEST_STACK_NAME}

# The image fields each row of ROWS reads with, in IMAGE_FIELDS order.
EXPECTED = {
    "legacy-retired": (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False),
    "stored-older": (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False),
    "stored-latest": (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, 13, True),
    "baseline": ("Ubuntu 22 GPU", None, None, None, None, None),
    "recipe-deleted": ("VAMS Old Desktop v4", "old-desktop", "Old desktop", 4, None, None),
    "legacy-without-recipe": (None, None, None, None, None, None),
}


def _image(record):
    return tuple(record[field] for field in IMAGE_FIELDS)


def _snapshot(image_name, recipe_id=None, display_name=None, version=None):
    """The image fields a launch stores on its row; DynamoDB hands a stored number back as a Decimal."""
    stored = {"imageName": image_name}
    if recipe_id is not None:
        stored.update(recipeId=recipe_id, recipeDisplayName=display_name, recipeVersion=Decimal(version))
    return stored


# The rows of the list cases, keyed by the name their expectations use: each with its own session.
ROWS = {
    # Launched before the launch stored an image: its build has been superseded and RETIRED since.
    "legacy-retired": own_row(bootstrapId="11111111-1111-4111-8111-111111111111", sessionId=None, imageId="30",
                              state="STOPPED"),
    # Launched from the build that is now RETIRED, with the image stored at launch.
    "stored-older": own_row(bootstrapId="22222222-2222-4222-8222-222222222222", sessionId=None, imageId="30",
                            state="STOPPED",
                            **_snapshot(RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12)),
    "stored-latest": own_row(bootstrapId="33333333-3333-4333-8333-333333333333", sessionId=None, imageId="36",
                             state="STOPPED",
                             **_snapshot(LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13)),
    "baseline": own_row(bootstrapId="44444444-4444-4444-8444-444444444444", sessionId=None, imageId="22",
                        state="STOPPED", **_snapshot("Ubuntu 22 GPU")),
    # Its recipe has been deleted since the launch, with every build row.
    "recipe-deleted": own_row(bootstrapId="55555555-5555-4555-8555-555555555555", sessionId=None, imageId="60",
                              state="STOPPED", **_snapshot("VAMS Old Desktop v4", "old-desktop", "Old desktop", 4)),
    # Launched before the launch stored an image, from a stack whose build has no recipe row.
    "legacy-without-recipe": own_row(bootstrapId="66666666-6666-4666-8666-666666666666", sessionId=None,
                                     imageId="50", state="STOPPED"),
}


@pytest.fixture
def recipe_table(monkeypatch):
    """A moto recipe table the handler reads through the real store: Isaac Sim's v12 build RETIRED since its
    v13 build became AVAILABLE, and a build whose recipe row is gone."""
    with mock_aws():
        create_recipe_table(monkeypatch)
        recipe_store.put_recipe({"recipeId": "isaacsim", "displayName": ISAAC_DISPLAY_NAME, "kind": "vdi",
                                 "stackName": "VAMS Isaac Sim"})
        for build in (
            {"recipeId": "isaacsim", "version": 12, "status": "RETIRED", "edhStackId": "30",
             "edhStackName": RETIRED_STACK_NAME, "edhKind": "vdi"},
            {"recipeId": "isaacsim", "version": 13, "status": "AVAILABLE", "edhStackId": "36",
             "edhStackName": LATEST_STACK_NAME, "edhKind": "vdi"},
            {"recipeId": "gone", "version": 2, "status": "AVAILABLE", "edhStackId": "50", "edhKind": "vdi"},
        ):
            recipe_store.put_build(build)
        yield recipe_store


def _with_rows(monkeypatch, rows, *, builds_enabled=True):
    """The handler over ``rows`` and the real store, its one scan recorded."""
    fakes = install_service_fakes(monkeypatch)
    fakes.table.query.return_value = {"Items": [dict(row) for row in rows]}
    monkeypatch.setattr(service, "recipe_store", recipe_store)
    monkeypatch.setattr(service, "ami_builds_enabled", builds_enabled)
    fakes.scan = MagicMock(wraps=recipe_store.registered_builds_by_stack_id)
    monkeypatch.setattr(recipe_store, "registered_builds_by_stack_id", fakes.scan)
    fakes.logger = MagicMock()
    monkeypatch.setattr(service, "logger", fakes.logger)
    return fakes


def _list(fakes):
    response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})
    assert response["statusCode"] == 200
    return {item["bootstrapId"]: item for item in response_body(response)["Items"]}


def _by_case(items):
    return {case: _image(items[row["bootstrapId"]]) for case, row in ROWS.items()}


@pytest.mark.unit
class TestTheListNamesEachImage:
    def test_each_record_names_its_image_and_whether_its_build_is_the_latest(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, ROWS.values())

        assert _by_case(_list(fakes)) == EXPECTED

    def test_a_row_launched_before_the_snapshot_is_resolved_from_its_retired_build(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, [ROWS["legacy-retired"]])

        record = _list(fakes)[ROWS["legacy-retired"]["bootstrapId"]]

        assert (record["imageId"], record["imageName"], record["recipeId"], record["recipeDisplayName"],
                record["recipeVersion"]) == ("30", RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12)
        assert (record["latestRecipeVersion"], record["imageLatest"]) == (13, False)

    def test_a_retired_build_that_recorded_no_stack_name_leaves_the_name_null(self, monkeypatch, recipe_table):
        recipe_store.update_build("isaacsim", 12, remove=("edhStackName",))
        fakes = _with_rows(monkeypatch, [ROWS["legacy-retired"]])

        record = _list(fakes)[ROWS["legacy-retired"]["bootstrapId"]]

        assert _image(record) == (None, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False)
        # EDH is not asked for the name.
        assert fakes.edh.calls == []

    def test_a_newer_available_build_marks_the_record_not_latest(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, [ROWS["stored-older"], ROWS["stored-latest"]])

        items = _list(fakes)

        older, latest = (items[ROWS[case]["bootstrapId"]] for case in ("stored-older", "stored-latest"))
        assert (older["recipeVersion"], older["latestRecipeVersion"], older["imageLatest"]) == (12, 13, False)
        assert (latest["recipeVersion"], latest["latestRecipeVersion"], latest["imageLatest"]) == (13, 13, True)

    def test_the_stored_image_is_kept_when_a_later_build_names_the_same_stack(self, monkeypatch, recipe_table):
        """The snapshot is the launch's and is never refreshed from a later build."""
        recipe_store.put_build({"recipeId": "isaacsim", "version": 14, "status": "AVAILABLE", "edhStackId": "36",
                                "edhStackName": "VAMS Isaac Sim v14", "edhKind": "vdi"})
        fakes = _with_rows(monkeypatch, [ROWS["stored-latest"]])

        record = _list(fakes)[ROWS["stored-latest"]["bootstrapId"]]

        assert _image(record) == (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, 14, False)

    def test_a_build_newer_than_the_recipes_available_one_is_neither_latest_nor_superseded(
            self, monkeypatch, recipe_table):
        """An older build retried into AVAILABLE retires the newer one; the newer one's desktop is not behind."""
        row = own_row(bootstrapId="77777777-7777-4777-8777-777777777777", sessionId=None, imageId="38",
                      state="STOPPED", **_snapshot("VAMS Isaac Sim v15", "isaacsim", ISAAC_DISPLAY_NAME, 15))
        fakes = _with_rows(monkeypatch, [row])

        record = _list(fakes)[row["bootstrapId"]]

        assert (record["recipeVersion"], record["latestRecipeVersion"], record["imageLatest"]) == (15, 13, None)

    def test_the_records_are_resolved_from_one_scan_whatever_their_number(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, ROWS.values())

        _list(fakes)

        assert fakes.scan.called, "the records were not resolved from the recipe table"
        assert fakes.scan.call_count <= 1
        assert fakes.scan.call_args.args == ("vdi",)

    def test_a_list_with_no_record_reads_no_recipe_table(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, [])

        assert _list(fakes) == {}
        fakes.scan.assert_not_called()

    def test_a_read_writes_nothing_back_to_the_rows(self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, ROWS.values())

        _list(fakes)

        fakes.table.update_item.assert_not_called()
        fakes.table.put_item.assert_not_called()


@pytest.mark.unit
class TestWithoutTheRecipeTable:
    def test_with_ami_builds_off_the_table_is_not_read_and_the_stored_image_is_served(
            self, monkeypatch, recipe_table):
        fakes = _with_rows(monkeypatch, ROWS.values(), builds_enabled=False)

        cases = _by_case(_list(fakes))

        fakes.scan.assert_not_called()
        assert cases["stored-older"] == (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, None, None)
        assert cases["baseline"] == ("Ubuntu 22 GPU", None, None, None, None, None)
        assert cases["legacy-retired"] == (None, None, None, None, None, None)

    @pytest.mark.parametrize("unpublished", [False, True], ids=["table-unreadable", "table-name-unpublished"])
    def test_a_read_that_fails_serves_the_stored_image_and_warns(self, monkeypatch, unpublished):
        with mock_aws():
            # No table: the scan is refused with a ClientError, as it is for a Lambda without a grant.
            monkeypatch.setattr(recipe_store, "dynamodb", boto3.resource("dynamodb", region_name="us-east-1"))
            if unpublished:
                monkeypatch.setattr(recipe_store, "table_name", None)
                monkeypatch.setattr(recipe_store, "get_table_name",
                                    MagicMock(side_effect=KeyError("Resource name parameter not found in SSM")))
            fakes = _with_rows(monkeypatch, ROWS.values())
            cases = _by_case(_list(fakes))

        assert cases["stored-older"] == (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, None, None)
        assert cases["legacy-retired"] == (None, None, None, None, None, None)
        fakes.logger.warning.assert_called_once()
        named = "KeyError" if unpublished else "ResourceNotFoundException"
        assert named in fakes.logger.warning.call_args.args[0]


@pytest.mark.unit
class TestSingleRecordRoutes:
    def test_get_names_the_image(self, monkeypatch, recipe_table):
        row = {**ROWS["legacy-retired"], "sessionId": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"}
        fakes = _with_rows(monkeypatch, [row])

        response = fakes.module.lambda_handler(
            make_event("GET", workstation_path(row["sessionId"]), path_params={"sessionId": row["sessionId"]}), {})

        assert response["statusCode"] == 200
        assert _image(response_body(response)) == EXPECTED["legacy-retired"]
        assert fakes.scan.called and fakes.scan.call_count <= 1


def _launch_body(image_id):
    return {"bootstrapId": BOOTSTRAP_OWN, "imageId": image_id, "instanceType": "g5.xlarge", "name": "Design desk",
            "rootSizeGb": 100, "osFamily": "linux"}


def _available(stack_id="36", version=13):
    """The store's answer naming the AVAILABLE Isaac Sim build that registered ``stack_id``."""
    recipe = {"recipeId": "isaacsim", "displayName": ISAAC_DISPLAY_NAME, "kind": "vdi"}
    build = {"recipeId": "isaacsim", "version": version, "status": "AVAILABLE", "edhStackId": stack_id,
             "edhStackName": LATEST_STACK_NAME, "edhKind": "vdi"}
    return {stack_id: (recipe, build)}


def _launch(monkeypatch, stack, *, available=None, raises=None, builds_enabled=True):
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=[stack]))
    monkeypatch.setattr(service, "allowed_instance_types", ("g5.xlarge",))
    monkeypatch.setattr(service, "ami_builds_enabled", builds_enabled)
    fakes.logger = MagicMock()
    monkeypatch.setattr(service, "logger", fakes.logger)
    lookup = fakes.recipe_store.available_builds_by_stack_id
    if raises is not None:
        lookup.side_effect = raises
    else:
        lookup.return_value = {} if available is None else available
    response = fakes.module.lambda_handler(
        make_event("POST", WORKSTATIONS_PATH, body=_launch_body(str(stack[service.EDH_FIELD_STACK_ID]))), {})
    assert response["statusCode"] == 202
    fakes.item = fakes.table.put_item.call_args[1]["Item"]
    fakes.body = response_body(response)
    return fakes


@pytest.mark.unit
class TestTheLaunchStoresTheImage:
    def test_a_recipe_image_stores_its_name_recipe_and_build_version(self, monkeypatch):
        fakes = _launch(monkeypatch, RECIPE_STACK, available=_available())

        assert {field: fakes.item.get(field) for field in IMAGE_FIELDS[:4]} == {
            "imageName": LATEST_STACK_NAME, "recipeId": "isaacsim", "recipeDisplayName": ISAAC_DISPLAY_NAME,
            "recipeVersion": 13}
        assert not set(DERIVED_FIELDS) & set(fakes.item)
        assert _image(fakes.body) == (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, 13, True)

    def test_a_baseline_image_stores_its_name_alone(self, monkeypatch):
        fakes = _launch(monkeypatch, dict(CATALOG_STACK))

        assert fakes.item["imageName"] == "Ubuntu 22 GPU"
        assert not {"recipeId", "recipeDisplayName", "recipeVersion", *DERIVED_FIELDS} & set(fakes.item)
        assert _image(fakes.body) == ("Ubuntu 22 GPU", None, None, None, None, None)

    def test_the_launch_reads_the_recipe_table_once(self, monkeypatch):
        """The launch check's read of the AVAILABLE builds also names the record's image."""
        fakes = _launch(monkeypatch, RECIPE_STACK, available=_available())

        fakes.recipe_store.available_builds_by_stack_id.assert_called_once_with("vdi")
        fakes.recipe_store.registered_builds_by_stack_id.assert_not_called()

    def test_with_ami_builds_off_the_launch_stores_the_name_alone(self, monkeypatch):
        fakes = _launch(monkeypatch, RECIPE_STACK, available=_available(), builds_enabled=False)

        fakes.recipe_store.available_builds_by_stack_id.assert_not_called()
        assert fakes.item["imageName"] == LATEST_STACK_NAME
        assert "recipeId" not in fakes.item
        assert _image(fakes.body) == (LATEST_STACK_NAME, None, None, None, None, None)

    def test_a_recipe_read_that_fails_stores_the_name_alone_and_launches(self, monkeypatch):
        refused = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, "Scan")
        fakes = _launch(monkeypatch, RECIPE_STACK, raises=refused)

        assert fakes.item["imageName"] == LATEST_STACK_NAME and "recipeId" not in fakes.item
        assert fakes.body["imageLatest"] is None
        fakes.lambda_client.invoke.assert_called_once()

    @pytest.mark.parametrize("name", [None, "", "   "], ids=["absent", "empty", "blank"])
    def test_a_stack_without_a_name_stores_none(self, monkeypatch, name):
        stack = {**CATALOG_STACK, service.EDH_FIELD_STACK_NAME: name}
        fakes = _launch(monkeypatch, stack)

        assert "imageName" not in fakes.item
        assert fakes.body["imageName"] is None
