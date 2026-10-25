# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin instance item names the image of its session as the workstation record does.

An item carries ``imageName``, ``recipeId``, ``recipeDisplayName``, ``recipeVersion``, ``latestRecipeVersion``
and ``imageLatest``: what the joined row stored at launch, else what the recipe builds that registered the
image name (AVAILABLE and RETIRED alike), with the image name also taken from EDH's own record of the
session (its ``software_stack`` label) when that names the item's image. The recipe table is read once per
request and only for a request that returns items; with AMI builds off it is not read, and a read that
fails is logged and the items keep what their rows stored.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as recipe_store
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    INSTANCES_PATH, STALE_PATH, FakeEdhClient, edh_desktop, install_admin_fakes, instance_path, make_event,
    response_body, session_row,
)
from backend.tests.handlers.addon.edh.edhAmiTestSupport import create_recipe_table
import backend.backend.handlers.addon.edh.edhAdminService as service

IMAGE_FIELDS = ("imageName", "recipeId", "recipeDisplayName", "recipeVersion", "latestRecipeVersion", "imageLatest")
ISAAC_DISPLAY_NAME = "Isaac Sim 6.2"
RETIRED_STACK_NAME = "VAMS Isaac Sim v12"
LATEST_STACK_NAME = "VAMS Isaac Sim v13"
OWNER = "vowner1"


def _image(item):
    return tuple(item[field] for field in IMAGE_FIELDS)


@pytest.fixture
def recipe_table(monkeypatch):
    """Isaac Sim's v12 build RETIRED since its v13 build became AVAILABLE."""
    with mock_aws():
        create_recipe_table(monkeypatch)
        recipe_store.put_recipe({"recipeId": "isaacsim", "displayName": ISAAC_DISPLAY_NAME, "kind": "vdi",
                                 "stackName": "VAMS Isaac Sim"})
        for build in (
            {"recipeId": "isaacsim", "version": 12, "status": "RETIRED", "edhStackId": "30",
             "edhStackName": RETIRED_STACK_NAME, "edhKind": "vdi"},
            {"recipeId": "isaacsim", "version": 13, "status": "AVAILABLE", "edhStackId": "36",
             "edhStackName": LATEST_STACK_NAME, "edhKind": "vdi"},
        ):
            recipe_store.put_build(build)
        yield recipe_store


def _install(monkeypatch, rows, desktops, *, builds_enabled=True):
    fakes = install_admin_fakes(monkeypatch, rows=rows, edh=FakeEdhClient(list_all_desktops=desktops))
    monkeypatch.setattr(service, "recipe_store", recipe_store)
    monkeypatch.setattr(service, "ami_builds_enabled", builds_enabled)
    fakes.scan = MagicMock(wraps=recipe_store.registered_builds_by_stack_id)
    monkeypatch.setattr(recipe_store, "registered_builds_by_stack_id", fakes.scan)
    fakes.logger = MagicMock()
    monkeypatch.setattr(service, "logger", fakes.logger)
    return fakes


def _items(fakes, path=INSTANCES_PATH):
    response = fakes.module.lambda_handler(make_event("GET", path), {})
    assert response["statusCode"] == 200
    return {item["sessionId"]: item for item in response_body(response)["Items"]}


def _world():
    """Four desktops EDH lists: one launched before the launch stored an image, from the now RETIRED build; one
    whose row stored the AVAILABLE build's image; a baseline one whose row stored its name; and one VAMS did not
    launch, which only EDH's record names."""
    rows = [
        session_row(bootstrapId="b-1", sessionId="s-1", edhUid=OWNER, imageId="30"),
        session_row(bootstrapId="b-2", sessionId="s-2", edhUid=OWNER, imageId="36", imageName=LATEST_STACK_NAME,
                    recipeId="isaacsim", recipeDisplayName=ISAAC_DISPLAY_NAME, recipeVersion=Decimal(13)),
        session_row(bootstrapId="b-3", sessionId="s-3", edhUid=OWNER, imageId="22", imageName="Ubuntu 22 GPU"),
    ]
    desktops = [
        edh_desktop("s-1", OWNER, "running", software_stack=f"{RETIRED_STACK_NAME} (id: 30)"),
        edh_desktop("s-2", OWNER, "running", software_stack=f"{LATEST_STACK_NAME} (id: 36)"),
        edh_desktop("s-3", OWNER, "stopped", software_stack="Ubuntu 22 GPU (id: 22)"),
        edh_desktop("s-4", "vother", "running", software_stack="Rocky 9 (id: 23)"),
    ]
    return rows, desktops


@pytest.mark.unit
class TestTheInstanceListNamesEachImage:
    def test_each_item_names_its_image_and_whether_its_build_is_the_latest(self, monkeypatch, recipe_table):
        fakes = _install(monkeypatch, *_world())

        items = _items(fakes)

        assert {session_id: _image(item) for session_id, item in items.items()} == {
            "s-1": (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False),
            "s-2": (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, 13, True),
            "s-3": ("Ubuntu 22 GPU", None, None, None, None, None),
            "s-4": ("Rocky 9", None, None, None, None, None),
        }

    def test_the_name_comes_from_edhs_record_when_the_retired_build_recorded_none(self, monkeypatch, recipe_table):
        recipe_store.update_build("isaacsim", 12, remove=("edhStackName",))
        rows, desktops = _world()
        fakes = _install(monkeypatch, rows, desktops)

        assert _image(_items(fakes)["s-1"]) == (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False)

    def test_a_label_that_names_another_image_names_nothing(self, monkeypatch, recipe_table):
        rows = [session_row(bootstrapId="b-5", sessionId="s-5", edhUid=OWNER, imageId="42")]
        desktops = [edh_desktop("s-5", OWNER, "running", software_stack="vams-spike-stack (id: 1)")]
        fakes = _install(monkeypatch, rows, desktops)

        assert _image(_items(fakes)["s-5"]) == (None, None, None, None, None, None)

    def test_the_items_are_resolved_from_one_scan(self, monkeypatch, recipe_table):
        fakes = _install(monkeypatch, *_world())

        _items(fakes)

        assert fakes.scan.called, "the items were not resolved from the recipe table"
        assert fakes.scan.call_count <= 1
        assert fakes.scan.call_args.args == ("vdi",)

    def test_a_list_without_items_reads_no_recipe_table(self, monkeypatch, recipe_table):
        fakes = _install(monkeypatch, [], [])

        assert _items(fakes) == {}
        fakes.scan.assert_not_called()

    def test_a_stale_item_names_its_image(self, monkeypatch, recipe_table):
        rows, desktops = _world()
        # A row EDH no longer lists is stale whatever its age.
        fakes = _install(monkeypatch, rows, [desktop for desktop in desktops if desktop["session_uuid"] != "s-1"])

        items = _items(fakes, STALE_PATH)

        assert _image(items["s-1"]) == (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False)
        assert fakes.scan.called and fakes.scan.call_count <= 1

    def test_the_answer_of_an_admin_stop_names_the_image(self, monkeypatch, recipe_table):
        fakes = _install(monkeypatch, *_world())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert _image(response_body(response)) == (RETIRED_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 12, 13, False)


@pytest.mark.unit
class TestWithoutTheRecipeTable:
    def test_with_ami_builds_off_the_table_is_not_read(self, monkeypatch, recipe_table):
        fakes = _install(monkeypatch, *_world(), builds_enabled=False)

        items = _items(fakes)

        fakes.scan.assert_not_called()
        assert _image(items["s-2"]) == (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, None, None)
        # EDH's own record still names the image of the row that stored none.
        assert _image(items["s-1"]) == (RETIRED_STACK_NAME, None, None, None, None, None)

    def test_a_read_that_fails_serves_the_stored_image_and_warns(self, monkeypatch):
        with mock_aws():
            # No table: the scan is refused with a ClientError, as it is for a Lambda without a grant.
            monkeypatch.setattr(recipe_store, "dynamodb", boto3.resource("dynamodb", region_name="us-east-1"))
            fakes = _install(monkeypatch, *_world())
            items = _items(fakes)

        assert _image(items["s-2"]) == (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, None, None)
        assert _image(items["s-1"]) == (RETIRED_STACK_NAME, None, None, None, None, None)
        fakes.logger.warning.assert_called_once()
        assert "ResourceNotFoundException" in fakes.logger.warning.call_args.args[0]


@pytest.mark.unit
class TestTheItemShape:
    def test_an_item_built_without_the_recipe_builds_carries_every_image_field(self):
        row = session_row(imageId="36", imageName=LATEST_STACK_NAME, recipeId="isaacsim",
                          recipeDisplayName=ISAAC_DISPLAY_NAME, recipeVersion=Decimal(13),
                          latestRecipeVersion=99, imageLatest=True)

        item = service._instance_item(edh_desktop("s-1", OWNER, "running"), row)

        # A stored latest or verdict is never returned: both are derived when the recipe builds are read.
        assert _image(item) == (LATEST_STACK_NAME, "isaacsim", ISAAC_DISPLAY_NAME, 13, None, None)
