# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The recipe builds a workstation record names its image from: every AVAILABLE or RETIRED build that
recorded the EDH stack it registered, keyed by that stack id under the kind rules the launch catalog uses,
and the newest AVAILABLE version of each recipe, from one scan of the recipe table."""
import pytest
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as store
from backend.tests.handlers.addon.edh.edhAmiTestSupport import create_recipe_table


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        yield create_recipe_table(monkeypatch)


def _recipes():
    store.put_recipe({"recipeId": "isaacsim", "displayName": "Isaac Sim 6.2", "kind": "vdi",
                      "stackName": "VAMS Isaac Sim"})
    store.put_recipe({"recipeId": "al2023-desktop", "displayName": "AL2023", "kind": "vdi",
                      "stackName": "VAMS AL2023 Desktop"})
    store.put_recipe({"recipeId": "sim-node", "displayName": "Sim node", "kind": "simulation",
                      "stackName": "VAMS Sim Node"})


def _put(*builds):
    for build in builds:
        store.put_build(build)


@pytest.mark.unit
class TestRegisteredBuildsByStackId:
    def test_available_and_retired_builds_that_name_their_stack_are_keyed_by_it(self, table):
        _recipes()
        _put(
            {"recipeId": "isaacsim", "version": 12, "status": store.STATUS_RETIRED, "edhStackId": "30",
             "edhStackName": "VAMS Isaac Sim v12", "edhKind": "vdi"},
            {"recipeId": "isaacsim", "version": 13, "status": store.STATUS_AVAILABLE, "edhStackId": "36",
             "edhStackName": "VAMS Isaac Sim v13", "edhKind": "vdi"},
            # Statuses whose row names no stack a workstation was launched from.
            {"recipeId": "isaacsim", "version": 14, "status": store.STATUS_REGISTER_FAILED, "edhStackId": "37"},
            {"recipeId": "isaacsim", "version": 15, "status": store.STATUS_BUILDING},
            {"recipeId": "isaacsim", "version": 16, "status": store.STATUS_FAILED, "edhStackId": "38"},
            # A build without edhKind takes its recipe's kind.
            {"recipeId": "al2023-desktop", "version": 5, "status": store.STATUS_AVAILABLE, "edhStackId": "35"},
        )

        by_stack, latest = store.registered_builds_by_stack_id("vdi")

        assert sorted(by_stack) == ["30", "35", "36"]
        recipe, build = by_stack["30"]
        assert (recipe["recipeId"], recipe["displayName"]) == ("isaacsim", "Isaac Sim 6.2")
        assert (build["status"], build["version"], build["edhStackName"]) == (store.STATUS_RETIRED, 12,
                                                                              "VAMS Isaac Sim v12")
        assert isinstance(build["version"], int)
        assert by_stack["35"][0]["recipeId"] == "al2023-desktop"
        # The newest AVAILABLE version of each recipe: a later build in another status does not count.
        assert latest == {"isaacsim": 13, "al2023-desktop": 5}

    def test_the_kind_rules_are_the_catalogs(self, table):
        """A build counts under the kind it registered as, else its recipe's; of two builds naming one stack
        the later version is kept; a build without its recipe row is left out."""
        _recipes()
        _put(
            {"recipeId": "isaacsim", "version": 3, "status": store.STATUS_RETIRED, "edhStackId": "35"},
            {"recipeId": "al2023-desktop", "version": 5, "status": store.STATUS_AVAILABLE, "edhStackId": "35"},
            # EDH numbers target-node stacks apart from dcv stacks, so a simulation build can name 35 too.
            {"recipeId": "sim-node", "version": 7, "status": store.STATUS_AVAILABLE, "edhStackId": "35",
             "edhKind": "simulation"},
            # A build keeps the kind it registered under, whatever its recipe's kind is now.
            {"recipeId": "isaacsim", "version": 12, "status": store.STATUS_AVAILABLE, "edhStackId": "40",
             "edhKind": "simulation"},
            {"recipeId": "gone", "version": 1, "status": store.STATUS_AVAILABLE, "edhStackId": "99"},
        )

        by_stack, latest = store.registered_builds_by_stack_id("vdi")
        simulation, simulation_latest = store.registered_builds_by_stack_id("simulation")

        assert {stack: (recipe["recipeId"], build["version"]) for stack, (recipe, build) in by_stack.items()} == {
            "35": ("al2023-desktop", 5)}
        assert latest == {"al2023-desktop": 5}
        assert {stack: (recipe["recipeId"], build["version"])
                for stack, (recipe, build) in simulation.items()} == {"35": ("sim-node", 7), "40": ("isaacsim", 12)}
        assert simulation_latest == {"sim-node": 7, "isaacsim": 12}

    def test_a_recipe_whose_only_builds_are_retired_has_no_latest(self, table):
        _recipes()
        _put({"recipeId": "isaacsim", "version": 12, "status": store.STATUS_RETIRED, "edhStackId": "30"})

        by_stack, latest = store.registered_builds_by_stack_id("vdi")

        assert list(by_stack) == ["30"]
        assert latest == {}

    def test_an_empty_table_names_nothing(self, table):
        assert store.registered_builds_by_stack_id("vdi") == ({}, {})


@pytest.mark.unit
class TestLatestAvailableVersions:
    def test_the_newest_available_version_of_each_recipe(self):
        recipe = {"recipeId": "isaacsim"}
        pairs = [
            (recipe, {"version": 12, "status": store.STATUS_AVAILABLE}),
            (recipe, {"version": 14, "status": store.STATUS_RETIRED}),
            (recipe, {"version": 13, "status": store.STATUS_AVAILABLE}),
            ({"recipeId": "al2023-desktop"}, {"version": 2, "status": store.STATUS_AVAILABLE}),
        ]

        assert store.latest_available_versions(pairs) == {"isaacsim": 13, "al2023-desktop": 2}

    def test_no_available_build_is_no_entry(self):
        assert store.latest_available_versions(
            [({"recipeId": "isaacsim"}, {"version": 12, "status": store.STATUS_RETIRED})]) == {}
