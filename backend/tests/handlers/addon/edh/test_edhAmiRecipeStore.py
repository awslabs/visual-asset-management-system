# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The recipe/build table: keys, content hash, version numbering, the image-ARN index and Decimal-free reads."""
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as store
from backend.tests.pagingStub import Pager

TABLE = os.environ["EDH_AMI_RECIPE_STORAGE_TABLE_NAME"]

RECIPE = {
    "recipeId": "al2023-desktop", "displayName": "AL2023", "kind": "vdi", "osFamily": "linux",
    "baseOs": "amazonlinux2023", "architecture": "x86_64", "parentImage": "ami-0123456789abcdef0",
    "buildInstanceType": "m7i.large", "rootSizeGb": 60, "stackName": "VAMS AL2023 Desktop", "profileId": 1,
    "components": [{"name": "b", "data": "schemaVersion: 1.0"}], "keepVersions": 3, "recipeRevision": 1,
    "source": store.SOURCE_CONFIG, "enabled": True,
}


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "imageBuildVersionArn", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": store.IMAGE_BUILD_ARN_INDEX,
                "KeySchema": [{"AttributeName": "imageBuildVersionArn", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(store, "dynamodb", resource)
        yield resource.Table(TABLE)


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.mark.unit
class TestContentHash:
    def test_metadata_does_not_change_the_hash(self):
        a = store.content_hash(RECIPE)
        b = store.content_hash({**RECIPE, "displayName": "other", "stackName": "x", "keepVersions": 5,
                                "buildInstanceType": "m7i.xlarge", "description": "d", "profileId": 9,
                                "launchInstanceTypes": ["g5.2xlarge", "g6e.xlarge"]})
        assert a == b

    @pytest.mark.parametrize("field,value", [
        ("parentImage", "ami-0fedcba9876543210"), ("architecture", "arm64"), ("baseOs", "rhel9"),
        ("rootSizeGb", 61), ("recipeRevision", 2),
        ("components", [{"name": "b", "data": "schemaVersion: 1.0\n# changed"}]),
    ])
    def test_build_inputs_change_the_hash(self, field, value):
        assert store.content_hash(RECIPE) != store.content_hash({**RECIPE, field: value})

    def test_component_order_matters(self):
        two = [{"arn": "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/x.x.x"}, {"name": "b", "data": "schemaVersion: 1.0"}]
        assert store.content_hash({**RECIPE, "components": two}) != store.content_hash({**RECIPE, "components": list(reversed(two))})

    def test_a_recipe_without_assets_hashes_exactly_as_before(self):
        # A recipe that declares neither assets nor parameters hashes only the fields above; the
        # literal digest holds that form fixed, because a change to it rebuilds every shipped AMI
        # on the next deploy.
        canonical = {field: RECIPE.get(field) for field in store.HASHED_FIELDS}
        canonical["components"] = [
            {"arn": c["arn"]} if c.get("arn") else {"name": c.get("name"), "data": c.get("data")}
            for c in RECIPE.get("components", [])
        ]
        expected = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        assert store.content_hash(RECIPE) == expected
        assert store.content_hash({**RECIPE, "assets": None, "parameters": None, "assetPrefix": None}) == expected
        assert store.content_hash({**RECIPE, "assets": [], "parameters": {}}) == expected
        assert expected == "f4687085e8b03a765341afc668c336fc573214ac5a3c4260f9c3dec5f7e56899"

    def test_the_deployment_values_a_recipe_names_change_the_hash_and_none_leaves_it_as_it_was(self):
        base = store.content_hash(RECIPE)
        assert store.content_hash(RECIPE, {}) == base
        assert base == "f4687085e8b03a765341afc668c336fc573214ac5a3c4260f9c3dec5f7e56899"
        a = store.content_hash(RECIPE, {"sessionSecretPrefix": "team-a/edh-session/"})
        b = store.content_hash(RECIPE, {"sessionSecretPrefix": "team-b/edh-session/"})
        assert len({base, a, b}) == 3

    def test_the_asset_prefix_changes_the_hash(self):
        with_assets = {**RECIPE, "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}]}
        a = store.content_hash({**with_assets, "assetPrefix": "addon/edh/amiRecipes/aaaa/r/assets"})
        b = store.content_hash({**with_assets, "assetPrefix": "addon/edh/amiRecipes/bbbb/r/assets"})
        assert a != b

    def test_assets_and_parameters_change_the_hash_but_asset_order_does_not(self):
        base = store.content_hash(RECIPE)
        two = [{"id": "vamscli", "repoPath": "tools/VamsCLI"},
               {"id": "connector", "repoPath": "tools/ExternalIntegrations/isaacsim_vams_integration"}]
        assert store.content_hash({**RECIPE, "assets": two}) != base
        assert store.content_hash({**RECIPE, "assets": two}) == store.content_hash(
            {**RECIPE, "assets": list(reversed(two))})
        assert store.content_hash({**RECIPE, "parameters": {"v": "5.1.0"}}) != base
        assert store.content_hash({**RECIPE, "parameters": {"v": "5.1.0"}}) != store.content_hash(
            {**RECIPE, "parameters": {"v": "5.2.0"}})


@pytest.mark.unit
class TestRecipes:
    def test_put_get_list_and_delete(self, table):
        store.put_recipe(dict(RECIPE))
        got = store.get_recipe("al2023-desktop")
        assert got["recipeId"] == "al2023-desktop" and "pk" not in got and isinstance(got["rootSizeGb"], int)
        assert [r["recipeId"] for r in store.list_recipes()] == ["al2023-desktop"]
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_BUILDING})
        assert store.delete_recipe_rows("al2023-desktop") == 2
        assert store.get_recipe("al2023-desktop") is None
        assert store.list_builds("al2023-desktop") == []

    def test_update_recipe_sets_attributes_and_updated_at(self, table):
        store.put_recipe(dict(RECIPE))
        updated = store.update_recipe("al2023-desktop", enabled=False, latestVersion=4)
        assert updated["enabled"] is False and updated["latestVersion"] == 4 and updated["updatedAt"]

    def test_update_recipe_writes_only_a_recipe_that_exists(self, table):
        assert store.update_recipe("al2023-desktop", latestVersion=2) is None
        assert table.scan()["Items"] == []

    def test_a_new_recipe_is_written_only_where_none_exists(self, table):
        first = store.put_recipe(dict(RECIPE))
        assert store.put_recipe({**RECIPE, "displayName": "second"}) is None
        assert store.get_recipe("al2023-desktop")["displayName"] == first["displayName"]

    def test_a_replacement_lands_only_over_the_row_it_read(self, table):
        read = store.put_recipe(dict(RECIPE))
        store.update_recipe("al2023-desktop", latestVersion=4)
        assert store.put_recipe({**read, "displayName": "edited"}, replacing=read) is None
        current = store.get_recipe("al2023-desktop")
        assert current["latestVersion"] == 4 and current["displayName"] == RECIPE["displayName"]
        replaced = store.put_recipe({**current, "displayName": "edited"}, replacing=current)
        assert replaced["displayName"] == "edited" and replaced["latestVersion"] == 4
        store.delete_recipe_rows("al2023-desktop")
        assert store.put_recipe(replaced, replacing=replaced) is None
        assert table.scan()["Items"] == []

    def test_a_delete_marker_is_held_by_one_delete_until_it_is_released_or_stale(self, table):
        assert store.mark_recipe_deleting("al2023-desktop", stale_before=_ago(30)) is None
        assert table.scan()["Items"] == []
        read = store.put_recipe(dict(RECIPE))
        marked = store.mark_recipe_deleting("al2023-desktop", stale_before=_ago(30))
        assert marked[store.DELETE_MARKER_FIELD] and marked["updatedAt"] != read["updatedAt"]
        assert store.mark_recipe_deleting("al2023-desktop", stale_before=_ago(30)) is None
        assert store.release_recipe_deleting("al2023-desktop", _ago(5)) is False
        held = store.get_recipe("al2023-desktop")
        assert store.release_recipe_deleting("al2023-desktop", marked[store.DELETE_MARKER_FIELD]) is True
        released = store.get_recipe("al2023-desktop")
        assert store.DELETE_MARKER_FIELD not in released and released["updatedAt"] != held["updatedAt"]
        # A marker set before stale_before belongs to a delete that did not finish.
        store.update_recipe("al2023-desktop", **{store.DELETE_MARKER_FIELD: _ago(60)})
        assert store.mark_recipe_deleting("al2023-desktop", stale_before=_ago(30))
        store.delete_recipe_rows("al2023-desktop")
        assert store.release_recipe_deleting("al2023-desktop", marked[store.DELETE_MARKER_FIELD]) is False
        assert table.scan()["Items"] == []

    def test_a_row_without_a_recipe_id_is_left_out_of_the_listing_and_logged(self, table):
        store.put_recipe(dict(RECIPE))
        table.put_item(Item={**store.recipe_key("orphan"), "latestVersion": 2})

        with patch.object(store, "logger") as logger:
            listed = store.list_recipes()

        assert [r["recipeId"] for r in listed] == ["al2023-desktop"]
        assert any("RECIPE#orphan" in str(c) for c in logger.warning.call_args_list)

    def test_a_consistent_listing_and_the_row_delete_read_the_partition_consistently(self, table, monkeypatch):
        store.put_recipe(dict(RECIPE))
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_QUEUED})
        reads = []
        query = table.query

        def recording_query(**kwargs):
            reads.append(kwargs.get("ConsistentRead", False))
            return query(**kwargs)
        monkeypatch.setattr(table, "query", recording_query)
        monkeypatch.setattr(store, "_table", lambda: table)

        assert [b["version"] for b in store.list_builds("al2023-desktop", consistent=True)] == [1]
        assert store.delete_recipe_rows("al2023-desktop") == 2
        assert reads and all(reads)


@pytest.mark.unit
class TestBuilds:
    def test_versions_are_numbered_and_listed_newest_first(self, table):
        assert store.next_version("r") == 1
        store.put_build({"recipeId": "r", "version": 1, "status": store.STATUS_AVAILABLE})
        store.put_build({"recipeId": "r", "version": 2, "status": store.STATUS_BUILDING,
                         "imageBuildVersionArn": "arn:aws:imagebuilder:us-east-1:123456789012:image/x/1.0.1/1"})
        assert store.next_version("r") == 3
        assert [b["version"] for b in store.list_builds("r")] == [2, 1]
        assert store.latest_build("r")["version"] == 2

    def test_build_by_image_arn_uses_the_index(self, table):
        arn = "arn:aws:imagebuilder:us-east-1:123456789012:image/x/1.0.1/1"
        store.put_build({"recipeId": "r", "version": 7, "status": store.STATUS_BUILDING, "imageBuildVersionArn": arn})
        assert store.build_by_image_arn(arn)["version"] == 7
        assert store.build_by_image_arn("arn:aws:imagebuilder:us-east-1:123456789012:image/none/1.0.0/1") is None

    def test_update_build_returns_the_row(self, table):
        store.put_build({"recipeId": "r", "version": 1, "status": store.STATUS_BUILDING})
        row = store.update_build("r", 1, status=store.STATUS_FAILED, error="boom")
        assert row["status"] == store.STATUS_FAILED and row["error"] == "boom" and row["version"] == 1
        assert store.get_build("r", 1)["status"] == store.STATUS_FAILED

    def test_update_build_removes_the_named_attributes_and_sets_the_others(self, table):
        store.put_build({"recipeId": "r", "version": 1, "status": store.STATUS_RETIRED,
                         "amiInUseBy": ["i-1"], "pruneWarning": "w"})
        row = store.update_build("r", 1, remove=("amiInUseBy", "pruneWarning", "absent"), amiDeregisteredAt="t")
        assert row["amiDeregisteredAt"] == "t" and row["status"] == store.STATUS_RETIRED
        assert "amiInUseBy" not in row and "pruneWarning" not in row
        assert store.get_build("r", 1) == row
        counted = store.update_build_counting("r", 1, "registerAttempts", error="e")
        assert counted["registerAttempts"] == 1 and counted["error"] == "e"

    def test_claim_build_conditionally_updates_and_returns_none_on_refusal(self, table):
        store.put_build({"recipeId": "r", "version": 1, "status": store.STATUS_BUILDING})
        claimed = store.claim_build("r", 1, store.STATUS_BUILDING, status=store.STATUS_REGISTERING, amiId="ami-1")
        assert claimed["status"] == store.STATUS_REGISTERING and claimed["amiId"] == "ami-1"
        assert store.get_build("r", 1)["status"] == store.STATUS_REGISTERING
        refused = store.claim_build("r", 1, store.STATUS_BUILDING, status=store.STATUS_AVAILABLE)
        assert refused is None
        assert store.get_build("r", 1)["status"] == store.STATUS_REGISTERING

    def test_claim_build_removes_the_named_attributes_only_when_it_claims(self, table):
        store.put_build({"recipeId": "r", "version": 1, "status": store.STATUS_RETIRED,
                         "amiInUseBy": ["i-1"], "pruneWarning": "w"})
        before = store.get_build("r", 1)
        assert store.claim_build("r", 1, store.STATUS_AVAILABLE, remove=("amiInUseBy",), pruneWarning="x") is None
        assert store.get_build("r", 1) == before

        claimed = store.claim_build("r", 1, store.STATUS_RETIRED, remove=("amiInUseBy", "absent"), pruneWarning="x")

        assert claimed["status"] == store.STATUS_RETIRED and claimed["pruneWarning"] == "x"
        assert "amiInUseBy" not in claimed and "remove" not in claimed
        assert store.get_build("r", 1) == claimed
        # A row that is gone is not written again.
        assert store.claim_build("r", 2, store.STATUS_RETIRED, remove=("amiInUseBy",), pruneWarning="x") is None
        assert store.get_build("r", 2) is None

    def test_claim_new_build_writes_a_free_version_and_refuses_a_taken_one(self, table):
        claimed = store.claim_new_build({"recipeId": "r", "version": 1, "status": store.STATUS_BUILDING,
                                         "requestedBy": "carol"})
        assert claimed["requestedBy"] == "carol" and claimed["requestedAt"] and "pk" not in claimed
        taken = store.claim_new_build({"recipeId": "r", "version": 1, "status": store.STATUS_QUEUED,
                                       "requestedBy": "dave"})
        assert taken is None
        held = store.get_build("r", 1)
        assert held["requestedBy"] == "carol" and held["status"] == store.STATUS_BUILDING

    def test_only_a_claim_whose_pipeline_never_started_is_settled_or_released(self, table):
        arn = "arn:aws:imagebuilder:us-east-1:123456789012:image/x/1.0.1/1"
        store.claim_new_build({"recipeId": "r", "version": 1, "status": store.STATUS_BUILDING})
        store.put_build({"recipeId": "r", "version": 2, "status": store.STATUS_BUILDING, "imageBuildVersionArn": arn})
        store.claim_new_build({"recipeId": "r", "version": 3, "status": store.STATUS_BUILDING})

        assert store.settle_unlaunched("r", 2, status=store.STATUS_FAILED) is None
        assert store.release_claim("r", 2) is False
        assert store.get_build("r", 2)["status"] == store.STATUS_BUILDING

        settled = store.settle_unlaunched("r", 1, status=store.STATUS_QUEUED, error="not started: x")
        assert settled["status"] == store.STATUS_QUEUED and settled["error"] == "not started: x"
        assert store.release_claim("r", 1) is False and store.get_build("r", 1)

        assert store.release_claim("r", 3) is True and store.get_build("r", 3) is None
        assert store.release_claim("r", 3) is False


@pytest.mark.unit
class TestAvailableBuildsByStackId:
    def test_each_available_build_that_names_its_stack_is_keyed_by_it_with_its_recipe(self, table):
        store.put_recipe(dict(RECIPE))
        store.put_recipe({**RECIPE, "recipeId": "isaacsim", "displayName": "Isaac Sim", "stackName": "VAMS Isaac Sim"})
        store.put_recipe({**RECIPE, "recipeId": "sim-node", "displayName": "Sim node", "kind": "simulation",
                          "stackName": "VAMS Sim Node"})
        # A build without edhKind takes its recipe's kind.
        for build in (
            {"recipeId": "al2023-desktop", "version": 3, "status": store.STATUS_AVAILABLE, "edhStackId": "35"},
            {"recipeId": "al2023-desktop", "version": 4, "status": store.STATUS_RETIRED, "edhStackId": "34"},
            {"recipeId": "al2023-desktop", "version": 5, "status": store.STATUS_AVAILABLE, "edhStackId": "35"},
            {"recipeId": "isaacsim", "version": 8, "status": store.STATUS_AVAILABLE, "edhStackId": ""},
            {"recipeId": "isaacsim", "version": 9, "status": store.STATUS_REGISTER_FAILED,
             "edhStackName": "VAMS Isaac Sim v9"},
            {"recipeId": "isaacsim", "version": 10, "status": store.STATUS_AVAILABLE, "edhStackId": "36"},
            {"recipeId": "isaacsim", "version": 11, "status": store.STATUS_BUILDING},
            # A build keeps the kind it registered under, whatever its recipe's kind is now.
            {"recipeId": "isaacsim", "version": 12, "status": store.STATUS_AVAILABLE, "edhStackId": "40",
             "edhKind": "simulation"},
            # EDH numbers target-node stacks apart from dcv stacks, so a simulation build can name 35 too.
            {"recipeId": "sim-node", "version": 7, "status": store.STATUS_AVAILABLE, "edhStackId": "35",
             "edhKind": "simulation"},
            {"recipeId": "sim-node", "version": 2, "status": store.STATUS_AVAILABLE, "edhStackId": "22",
             "edhKind": "simulation"},
            # A build whose recipe row is gone names no recipe.
            {"recipeId": "gone", "version": 1, "status": store.STATUS_AVAILABLE, "edhStackId": "99"},
        ):
            store.put_build(build)

        by_stack = store.available_builds_by_stack_id("vdi")

        assert sorted(by_stack) == ["35", "36"]
        recipe, build = by_stack["35"]
        assert recipe["recipeId"] == "al2023-desktop" and recipe["displayName"] == "AL2023"
        # Of two builds naming one stack, the later version.
        assert build["version"] == 5 and isinstance(build["version"], int)
        assert by_stack["36"][0]["recipeId"] == "isaacsim" and by_stack["36"][1]["version"] == 10
        simulation = store.available_builds_by_stack_id("simulation")
        assert {stack: (recipe["recipeId"], build["version"]) for stack, (recipe, build) in simulation.items()} == {
            "22": ("sim-node", 2), "35": ("sim-node", 7), "40": ("isaacsim", 12)}

    def test_the_scan_pages_to_exhaustion_before_builds_are_joined_to_recipes(self, monkeypatch):
        build_row = {**store.build_key("al2023-desktop", 5), "recipeId": "al2023-desktop", "version": Decimal(5),
                     "status": store.STATUS_AVAILABLE, "edhStackId": "35", "edhKind": "vdi"}
        recipe_row = {**store.recipe_key("al2023-desktop"), "recipeId": "al2023-desktop", "displayName": "AL2023"}
        pager = Pager({"Items": [build_row], "LastEvaluatedKey": {"pk": "p1", "sk": "s1"}},
                      {"Items": [], "LastEvaluatedKey": {"pk": "p2", "sk": "s2"}},
                      {"Items": [recipe_row]}, name="recipe builds by stack")
        fake = MagicMock()
        fake.scan.side_effect = pager
        monkeypatch.setattr(store, "_table", lambda: fake)

        by_stack = store.available_builds_by_stack_id("vdi")

        pager.assert_paged_to_exhaustion()
        assert list(by_stack) == ["35"] and by_stack["35"][0]["displayName"] == "AL2023"


@pytest.mark.unit
class TestTableNameResolution:
    def test_the_name_is_resolved_at_import_where_it_is_published(self):
        # Positive control for the case below: the module as the suite imported it.
        assert store.table_name == TABLE

    def test_a_module_whose_table_name_is_not_published_imports_and_resolves_it_on_use(self, monkeypatch):
        """The workstation service imports the store for its launch catalog whether or not the table
        is published to it, so an unresolved name is retried when a read first needs the table."""
        spec = importlib.util.spec_from_file_location(f"{store.__package__}.edhAmiRecipeStore_under_test",
                                                      store.__file__)
        module = importlib.util.module_from_spec(spec)
        missing = KeyError("Resource name parameter not found in SSM: addon/dynamoTables/edhAmiRecipeStorage")
        with patch("common.resourceNames.get_table_name", side_effect=missing), \
                patch("boto3.resource", return_value=MagicMock()):
            spec.loader.exec_module(module)
        assert module.table_name is None

        resolver = MagicMock(side_effect=[missing, "edh-ami-recipes"])
        monkeypatch.setattr(module, "get_table_name", resolver)
        with pytest.raises(KeyError):
            module._table()
        module._table()
        module._table()

        module.dynamodb.Table.assert_called_with("edh-ami-recipes")
        assert resolver.call_count <= 2 and module.table_name == "edh-ami-recipes"
