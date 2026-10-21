# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The AMI recipe lifecycle: seeding, versioned builds, queueing, completion, registration, retirement,
pruning, the sweep and deletion — against a moto table and recording Image Builder / EDH fakes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhAmiRecipes as recipes
from backend.backend.handlers.addon.edh.common import edhAmiRecipeStore as store
from backend.backend.handlers.addon.edh.common.edhClient import EdhError, EdhUnavailableError
from backend.backend.models.addon.edh import AmiRecipeRequestModel
from backend.tests.handlers.addon.edh.edhAmiTestSupport import (
    NOT_FOUND, RECIPE_BODY, create_recipe_table, fake_context, fake_edh,
)
from backend.tests.handlers.addon.edh.edhTestSupport import DEFAULT_PROJECT

# A component that names the deployment's session secret prefix, as the Isaac Sim sign-in does.
DEPLOY_COMPONENT = {"name": "060-session-bootstrap", "data": "schemaVersion: 1.0\n# ${DEPLOY_sessionSecretPrefix}\n"}

# A component that fetches the bundled VAMS CLI, as the shipped desktop recipes do.
ASSET_COMPONENT = {"name": "070-vamscli", "data": "schemaVersion: 1.0\n# ${ASSET_vamscli}\n"}
VAMSCLI_ASSETS = [{"id": "vamscli", "repoPath": "tools/VamsCLI"}]

BUILD_TIMED_OUT = recipes.BUILD_TIMEOUT_MINUTES + 5
FORBIDDEN = type("ForbiddenException", (Exception,), {})

PARAMETER_MISSING = ClientError({"Error": {"Code": "ParameterNotFound", "Message": "missing"}}, "GetParameter")
PARENT_UNREADABLE = ("not started: SSM parameter /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 "
                     "could not be read (ParameterNotFound)")

ARN_BEFORE_CLAIMS = "arn:aws:imagebuilder:us-east-1:123456789012:image/x/1.0.0/7"

UNAUTHORIZED = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "not authorized"}}, "DescribeInstances")
THROTTLED = ClientError({"Error": {"Code": "RequestLimitExceeded", "Message": "slow down"}}, "DeregisterImage")

# The architecture refusals: a save's (RecipeInvalid) and a build start's (RecipeConflict).
BUILD_TYPE_MISMATCH = "buildInstanceType does not match the recipe's architecture"
PARENT_IMAGE_MISMATCH = "parentImage does not match the recipe's architecture"
PARENT_MISMATCH = "the parent image's architecture does not match the recipe's architecture"
PARENT_NOT_FOUND = "the parent image could not be found"
# A Graviton type, which runs arm64 images only, and an AMI id a recipe names as its parent.
GRAVITON = "m7g.large"
# An instance type name EC2 does not offer.
NOT_OFFERED = "zz9.nope"
PARENT_AMI = "ami-0123456789abcdef0"
# The AMI the fake SSM parameter resolves to.
PARAMETER_AMI = "ami-0abc0abc0abc0abc0"
AMI_NOT_FOUND = ClientError({"Error": {"Code": "InvalidAMIID.NotFound", "Message": "missing"}}, "DescribeImages")
AMI_MALFORMED = ClientError({"Error": {"Code": "InvalidAMIID.Malformed", "Message": "malformed"}}, "DescribeImages")
INVALID_TYPE = ClientError({"Error": {"Code": "InvalidInstanceType", "Message": "not offered"}}, "DescribeInstanceTypes")
UNREACHABLE = EndpointConnectionError(endpoint_url="https://ec2.example")
TIMED_OUT = ReadTimeoutError(endpoint_url="https://ec2.example")


@pytest.fixture
def world(monkeypatch):
    with mock_aws():
        create_recipe_table(monkeypatch)
        ctx = fake_context()
        edh = fake_edh()
        monkeypatch.setattr(recipes, "_ctx", lambda: ctx)
        monkeypatch.setattr(recipes, "_edh", lambda: edh)
        yield {"ctx": ctx, "edh": edh}


def seeded(build_on_deploy=False, **overrides):
    # If recipeId is overridden, treat as a complete recipe (allows removing keys like baseOs for simulation recipes)
    if "recipeId" in overrides and overrides["recipeId"] != RECIPE_BODY["recipeId"]:
        manifest = overrides
    else:
        manifest = {**RECIPE_BODY, **overrides}
    row, _ = recipes.upsert_config_recipe(manifest, build_on_deploy=build_on_deploy)
    return row


@pytest.fixture
def recipe_log(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(recipes, "logger", logger)
    return logger


def warnings_of(logger):
    return [str(call.args[0]) for call in logger.warning.call_args_list]


def instance_types(architectures):
    """A DescribeInstanceTypes answer listing each type with its supported architectures."""
    return {"InstanceTypes": [{"InstanceType": instance_type, "ProcessorInfo": {"SupportedArchitectures": supported}}
                              for instance_type, supported in architectures.items()]}


def refusing_not_offered(architectures):
    """A DescribeInstanceTypes fake that refuses a call naming NOT_OFFERED, as EC2 does, and otherwise
    lists each named type ``architectures`` holds."""
    def describe(InstanceTypes):
        if NOT_OFFERED in InstanceTypes:
            raise INVALID_TYPE
        return instance_types({t: architectures[t] for t in InstanceTypes if t in architectures})
    return describe


def parent_image(architecture, root_device="/dev/xvda"):
    """A DescribeImages answer for a parent of ``architecture`` (none when it is None)."""
    image = {"RootDeviceName": root_device,
             "BlockDeviceMappings": [{"DeviceName": root_device, "Ebs": {"SnapshotId": "snap-1"}}]}
    if architecture is not None:
        image["Architecture"] = architecture
    return {"Images": [image]}


def admin_recipe(**overrides):
    return recipes.create_admin_recipe(AmiRecipeRequestModel(**{**RECIPE_BODY, **overrides}), "carol")


def separate_lookup_clients(ctx):
    """Give ``ctx`` lookup clients of their own and clear the build clients' calls, so each call shows which
    pair of clients it went through. Returns the context holding the lookup clients."""
    lookups = fake_context()
    ctx.lookup_ec2, ctx.lookup_ssm = lookups.ec2, lookups.ssm
    ctx.ec2.reset_mock()
    ctx.ssm.reset_mock()
    return lookups


def lookup_takes(monkeypatch, ctx, call, seconds):
    """Make the save's ``call`` on the lookup clients take ``seconds`` on the clock the lookup budget reads."""
    now = [1000.0]
    monkeypatch.setattr(recipes, "monotonic", lambda: now[0])
    method = getattr(ctx.lookup_ssm if call == "get_parameter" else ctx.lookup_ec2, call)
    answer = method.return_value

    def slow(**kwargs):
        now[0] += seconds
        return answer
    method.side_effect = slow


@pytest.mark.unit
class TestSeeding:
    def test_a_new_manifest_is_a_changed_config_recipe(self, world):
        row, outcome = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=True)
        assert outcome == "changed" and row["source"] == store.SOURCE_CONFIG and row["buildOnDeploy"] is True
        assert row["contentHash"] and row["keepVersions"] == 2 and row["enabled"] is True

    def test_the_same_manifest_is_unchanged_and_keeps_build_state(self, world):
        seeded()
        store.update_recipe("al2023-desktop", latestVersion=3, imageBuilder={"pipelineArn": "arn:p"})
        row, outcome = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=False)
        assert outcome == "unchanged" and row["latestVersion"] == 3 and row["imageBuilder"]["pipelineArn"] == "arn:p"

    def test_an_admin_recipe_is_never_overwritten_by_config(self, world):
        recipes.create_admin_recipe(AmiRecipeRequestModel(**RECIPE_BODY), "carol")
        row, outcome = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=True)
        assert row is None and outcome == "admin-owned"
        assert store.get_recipe("al2023-desktop")["source"] == store.SOURCE_ADMIN

    def test_a_changed_deployment_value_changes_only_the_recipes_that_name_it(self, world, monkeypatch):
        naming = {**RECIPE_BODY, "recipeId": "names-prefix",
                  "components": [RECIPE_BODY["components"][0], DEPLOY_COMPONENT]}
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "team-a/edh-session/")
        plain, _ = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=False)
        named, _ = recipes.upsert_config_recipe(naming, build_on_deploy=False)
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "team-b/edh-session/")
        plain_again, plain_outcome = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=False)
        named_again, named_outcome = recipes.upsert_config_recipe(naming, build_on_deploy=False)
        assert plain_outcome == "unchanged"
        assert plain_again["contentHash"] == plain["contentHash"] == store.content_hash(plain)
        assert named_outcome == "changed" and named_again["contentHash"] != named["contentHash"]

    def test_missing_config_recipes_are_disabled_not_deleted(self, world):
        seeded()
        assert recipes.disable_missing_config_recipes(present_ids=set()) == ["al2023-desktop"]
        assert store.get_recipe("al2023-desktop")["enabled"] is False


@pytest.mark.unit
class TestRecipeAssets:
    ASSET_MANIFEST = {**RECIPE_BODY, "recipeId": "isaacsim-test",
                      "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}],
                      "parameters": {"isaacSimVersion": "5.1.0"}}

    def test_the_asset_prefix_is_stored_and_hashed(self, world):
        row, outcome = recipes.upsert_config_recipe(
            {**self.ASSET_MANIFEST, "assetPrefix": "addon/edh/amiRecipes/aaaa/isaacsim-test/assets"},
            build_on_deploy=False)
        assert outcome == "changed"
        assert row["assetPrefix"] == "addon/edh/amiRecipes/aaaa/isaacsim-test/assets"
        assert row["assets"] == [{"id": "vamscli", "repoPath": "tools/VamsCLI"}]
        assert row["parameters"] == {"isaacSimVersion": "5.1.0"}
        first_hash = row["contentHash"]
        moved, moved_outcome = recipes.upsert_config_recipe(
            {**self.ASSET_MANIFEST, "assetPrefix": "addon/edh/amiRecipes/bbbb/isaacsim-test/assets"},
            build_on_deploy=False)
        assert moved_outcome == "changed" and moved["contentHash"] != first_hash

    def test_a_manifest_without_an_asset_prefix_stores_none(self, world):
        row, _ = recipes.upsert_config_recipe(RECIPE_BODY, build_on_deploy=False)
        assert "assetPrefix" not in row

    def test_an_admin_recipe_may_not_declare_assets(self, world):
        model = AmiRecipeRequestModel(**self.ASSET_MANIFEST)
        with pytest.raises(recipes.RecipeConflict,
                           match="assets are only supported on shipped recipes"):
            recipes.create_admin_recipe(model, "carol")
        plain = recipes.create_admin_recipe(
            AmiRecipeRequestModel(**{k: v for k, v in self.ASSET_MANIFEST.items()
                                     if k != "assets"}), "carol")
        assert plain["parameters"] == {"isaacSimVersion": "5.1.0"}
        with pytest.raises(recipes.RecipeConflict,
                           match="assets are only supported on shipped recipes"):
            recipes.update_admin_recipe(plain, model, "carol")


@pytest.mark.unit
class TestAdminRecipes:
    def test_create_refuses_a_taken_id(self, world):
        recipes.create_admin_recipe(AmiRecipeRequestModel(**RECIPE_BODY), "carol")
        with pytest.raises(recipes.RecipeConflict):
            recipes.create_admin_recipe(AmiRecipeRequestModel(**RECIPE_BODY), "carol")

    def test_update_refuses_a_config_recipe_and_an_id_change(self, world):
        config_row = seeded()
        with pytest.raises(recipes.RecipeConflict):
            recipes.update_admin_recipe(config_row, AmiRecipeRequestModel(**RECIPE_BODY), "carol")
        mine = {**RECIPE_BODY, "recipeId": "mine", "stackName": "Mine"}
        admin_row = recipes.create_admin_recipe(AmiRecipeRequestModel(**mine), "carol")
        with pytest.raises(recipes.RecipeConflict):
            recipes.update_admin_recipe(admin_row, AmiRecipeRequestModel(**{**mine, "recipeId": "other"}), "carol")
        updated = recipes.update_admin_recipe(admin_row, AmiRecipeRequestModel(**{**mine, "rootSizeGb": 80}), "dave")
        assert updated["rootSizeGb"] == 80 and updated["updatedBy"] == "dave" and updated["createdBy"] == "carol"

    def test_a_document_too_long_once_resolved_is_refused_at_create_and_at_update(self, world):
        token = "${PARAM_p}"
        header = "schemaVersion: 1.0\n# "
        data = header + "#" * (16000 - len(header) - len(token)) + token
        body = {**RECIPE_BODY, "recipeId": "long-doc", "parameters": {"p": "v" * 11},
                "components": [RECIPE_BODY["components"][0], {"name": "big", "data": data}]}
        refusal = "component 2 is 16001 characters once its placeholders are resolved"
        with pytest.raises(recipes.RecipeConflict, match=refusal):
            recipes.create_admin_recipe(AmiRecipeRequestModel(**body), "carol")
        assert store.get_recipe("long-doc") is None
        admin_row = recipes.create_admin_recipe(
            AmiRecipeRequestModel(**{**body, "parameters": {"p": "v" * 10}}), "carol")
        with pytest.raises(recipes.RecipeConflict, match=refusal):
            recipes.update_admin_recipe(admin_row, AmiRecipeRequestModel(**body), "dave")
        assert store.get_recipe("long-doc")["parameters"] == {"p": "v" * 10}

    def test_a_deployment_placeholder_is_resolved_at_save_as_the_build_resolves_it(self, world, monkeypatch):
        token = "${DEPLOY_sessionSecretPrefix}"
        header = "schemaVersion: 1.0\n# "
        data = header + "#" * (16000 - len(header) - len(token)) + token
        body = {**RECIPE_BODY, "recipeId": "names-prefix",
                "components": [RECIPE_BODY["components"][0], {"name": "big", "data": data}]}
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "")
        with pytest.raises(recipes.RecipeConflict, match="unresolved placeholder.*DEPLOY_sessionSecretPrefix"):
            recipes.create_admin_recipe(AmiRecipeRequestModel(**body), "carol")
        assert store.get_recipe("names-prefix") is None
        # A value as long as its token keeps the document at 16000 characters; one more is refused.
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "p" * (len(token) - 1) + "/")
        admin_row = recipes.create_admin_recipe(AmiRecipeRequestModel(**body), "carol")
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "p" * len(token) + "/")
        with pytest.raises(recipes.RecipeConflict, match="component 2 is 16001 characters once its placeholders"):
            recipes.update_admin_recipe(admin_row, AmiRecipeRequestModel(**body), "dave")
        assert store.get_recipe("names-prefix")["contentHash"] == admin_row["contentHash"]

    def test_an_undeclared_placeholder_is_refused_at_create_and_at_update(self, world):
        body = {**RECIPE_BODY, "recipeId": "typo",
                "components": [{"name": "baseline", "data": "schemaVersion: 1.0\n# ${PARAM_missing}\n"}]}
        with pytest.raises(recipes.RecipeConflict, match="PARAM_missing"):
            recipes.create_admin_recipe(AmiRecipeRequestModel(**body), "carol")
        assert store.get_recipe("typo") is None
        admin_row = recipes.create_admin_recipe(
            AmiRecipeRequestModel(**{**body, "parameters": {"missing": "x"}}), "carol")
        with pytest.raises(recipes.RecipeConflict, match="PARAM_missing"):
            recipes.update_admin_recipe(admin_row, AmiRecipeRequestModel(**body), "dave")
        assert store.get_recipe("typo")["parameters"] == {"missing": "x"}


@pytest.mark.unit
class TestDuplicateRecipes:
    """A duplicate is an admin recipe created from another: it takes the source's bundled files as they
    were uploaded for the source, needs its own id and stack name, and records its source."""
    SOURCE = {**RECIPE_BODY, "components": [RECIPE_BODY["components"][0], ASSET_COMPONENT],
              "assets": VAMSCLI_ASSETS, "assetPrefix": "addon/edh/amiRecipes/aaaa/al2023-desktop/assets"}
    COPY = {**RECIPE_BODY, "components": [RECIPE_BODY["components"][0], ASSET_COMPONENT],
            "recipeId": "al2023-copy", "displayName": "Amazon Linux 2023 desktop (copy)",
            "stackName": "VAMS AL2023 Desktop Copy", "duplicateOf": "al2023-desktop"}
    # The body an update of the duplicate sends.
    EDIT = {key: value for key, value in COPY.items() if key != "duplicateOf"}

    @pytest.fixture
    def source(self, world, monkeypatch):
        monkeypatch.setenv("EDH_AMI_BUILD_ARTEFACTS_BUCKET", "vams-test-artefacts")
        recipes.upsert_config_recipe(self.SOURCE, build_on_deploy=True)
        return store.update_recipe("al2023-desktop", latestVersion=5)

    def test_a_duplicate_takes_the_source_bundled_files_and_records_its_source(self, source):
        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**self.COPY), "carol")

        assert copy["source"] == store.SOURCE_ADMIN and copy["createdBy"] == "carol"
        assert copy["duplicatedFrom"] == "al2023-desktop" and "duplicateOf" not in copy
        assert copy["assets"] == source["assets"] == VAMSCLI_ASSETS
        assert copy["assetPrefix"] == source["assetPrefix"]
        assert copy["buildOnDeploy"] is False and "latestVersion" not in copy
        assert store.list_builds("al2023-copy") == []
        assert store.get_recipe("al2023-copy")["duplicatedFrom"] == "al2023-desktop"
        # The stored hash is over the inherited prefix: the same recipe under another prefix hashes apart.
        assert copy["contentHash"] == store.content_hash(copy)
        assert copy["contentHash"] != store.content_hash(
            {**copy, "assetPrefix": "addon/edh/amiRecipes/bbbb/al2023-desktop/assets"})

    def test_the_body_may_repeat_the_source_assets_exactly(self, source):
        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**{**self.COPY, "assets": VAMSCLI_ASSETS}), "carol")
        assert copy["assets"] == VAMSCLI_ASSETS and copy["assetPrefix"] == source["assetPrefix"]

    @pytest.mark.parametrize("assets", [
        [{"id": "vamscli", "repoPath": "tools/VamsMCP"}],
        VAMSCLI_ASSETS + [{"id": "connector", "repoPath": "tools/ExternalIntegrations"}],
    ])
    def test_a_body_whose_assets_differ_from_the_source_is_refused(self, source, assets):
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.create_admin_recipe(AmiRecipeRequestModel(**{**self.COPY, "assets": assets}), "carol")
        assert str(err.value) == "the assets of a duplicated recipe cannot be changed"
        assert store.get_recipe("al2023-copy") is None

    def test_a_missing_source_is_refused(self, world):
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.create_admin_recipe(AmiRecipeRequestModel(**self.COPY), "carol")
        assert str(err.value) == "the recipe to duplicate does not exist"
        assert store.get_recipe("al2023-copy") is None

    def test_the_source_the_caller_read_is_the_one_copied(self, source):
        read = {**source, "assetPrefix": "addon/edh/amiRecipes/cccc/al2023-desktop/assets"}
        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**self.COPY), "carol", original=read)
        assert copy["assetPrefix"] == "addon/edh/amiRecipes/cccc/al2023-desktop/assets"

    def test_a_duplicate_needs_its_own_stack_name(self, source):
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.create_admin_recipe(
                AmiRecipeRequestModel(**{**self.COPY, "stackName": self.SOURCE["stackName"]}), "carol")
        assert str(err.value) == "stackName is already used by recipe 'al2023-desktop'"
        assert store.get_recipe("al2023-copy") is None

    def test_without_duplicate_of_assets_are_still_refused(self, source):
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.create_admin_recipe(AmiRecipeRequestModel(**{**self.EDIT, "assets": VAMSCLI_ASSETS}), "carol")
        assert str(err.value) == "assets are only supported on shipped recipes"

    def test_an_update_keeps_the_inherited_files_and_source(self, source):
        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**self.COPY), "carol")

        updated = recipes.update_admin_recipe(copy, AmiRecipeRequestModel(**{**self.EDIT, "rootSizeGb": 80}), "dave")

        assert updated["rootSizeGb"] == 80 and updated["duplicatedFrom"] == "al2023-desktop"
        assert updated["assets"] == VAMSCLI_ASSETS and updated["assetPrefix"] == source["assetPrefix"]
        assert updated["contentHash"] == store.content_hash(updated) != copy["contentHash"]
        repeated = recipes.update_admin_recipe(
            updated, AmiRecipeRequestModel(**{**self.EDIT, "rootSizeGb": 80, "assets": VAMSCLI_ASSETS}), "dave")
        assert repeated["assets"] == VAMSCLI_ASSETS and repeated["contentHash"] == updated["contentHash"]

    def test_an_update_may_not_change_the_inherited_files(self, source):
        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**self.COPY), "carol")
        changed = [{"id": "vamscli", "repoPath": "tools/VamsMCP"}]

        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.update_admin_recipe(copy, AmiRecipeRequestModel(**{**self.EDIT, "assets": changed}), "dave")

        assert str(err.value) == "the assets of a duplicated recipe cannot be changed"
        assert store.get_recipe("al2023-copy")["assets"] == VAMSCLI_ASSETS

    def test_a_duplicate_of_a_recipe_without_files_still_refuses_assets(self, world):
        seeded()
        plain = {**RECIPE_BODY, "recipeId": "plain-copy", "stackName": "Plain Copy"}
        with pytest.raises(recipes.RecipeConflict) as refused:
            recipes.create_admin_recipe(
                AmiRecipeRequestModel(**{**plain, "duplicateOf": "al2023-desktop", "assets": VAMSCLI_ASSETS}), "carol")
        assert str(refused.value) == "the assets of a duplicated recipe cannot be changed"
        assert store.get_recipe("plain-copy") is None

        copy = recipes.create_admin_recipe(AmiRecipeRequestModel(**{**plain, "duplicateOf": "al2023-desktop"}), "carol")
        assert copy["duplicatedFrom"] == "al2023-desktop"
        assert "assets" not in copy and "assetPrefix" not in copy

        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.update_admin_recipe(copy, AmiRecipeRequestModel(**{**plain, "assets": VAMSCLI_ASSETS}), "dave")

        assert str(err.value) == "assets are only supported on shipped recipes"
        kept = recipes.update_admin_recipe(copy, AmiRecipeRequestModel(**{**plain, "rootSizeGb": 80}), "dave")
        assert kept["duplicatedFrom"] == "al2023-desktop" and kept["rootSizeGb"] == 80


@pytest.mark.unit
class TestStackNameUniqueness:
    """A build registers, or adopts, the EDH stack named ``<stackName> v<N>``, so two recipes that shared
    a stack name would take each other's stacks."""
    MINE = {**RECIPE_BODY, "recipeId": "mine", "stackName": "Mine"}

    def test_create_refuses_a_stack_name_another_recipe_uses_whatever_its_case(self, world):
        seeded()
        for name in ("VAMS AL2023 Desktop", "vams al2023 DESKTOP", "  VAMS AL2023 Desktop "):
            with pytest.raises(recipes.RecipeConflict) as err:
                recipes.create_admin_recipe(AmiRecipeRequestModel(**{**self.MINE, "stackName": name}), "carol")
            assert str(err.value) == "stackName is already used by recipe 'al2023-desktop'"
        assert store.get_recipe("mine") is None
        created = recipes.create_admin_recipe(
            AmiRecipeRequestModel(**{**self.MINE, "stackName": "VAMS AL2023 Desktop 2"}), "carol")
        assert created["stackName"] == "VAMS AL2023 Desktop 2"

    def test_a_stored_stack_name_is_compared_trimmed(self, world):
        seeded()
        store.update_recipe("al2023-desktop", stackName="  VAMS AL2023 Desktop  ")
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.create_admin_recipe(
                AmiRecipeRequestModel(**{**self.MINE, "stackName": "VAMS AL2023 Desktop"}), "carol")
        assert str(err.value) == "stackName is already used by recipe 'al2023-desktop'"

    def test_update_refuses_another_recipes_stack_name_and_keeps_its_own(self, world):
        seeded()
        row = recipes.create_admin_recipe(AmiRecipeRequestModel(**self.MINE), "carol")

        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.update_admin_recipe(
                row, AmiRecipeRequestModel(**{**self.MINE, "stackName": "vams al2023 desktop"}), "dave")

        assert str(err.value) == "stackName is already used by recipe 'al2023-desktop'"
        assert store.get_recipe("mine")["stackName"] == "Mine"
        updated = recipes.update_admin_recipe(row, AmiRecipeRequestModel(**{**self.MINE, "rootSizeGb": 80}), "dave")
        assert updated["stackName"] == "Mine" and updated["rootSizeGb"] == 80
        renamed = recipes.update_admin_recipe(
            updated, AmiRecipeRequestModel(**{**self.MINE, "stackName": "MINE"}), "dave")
        assert renamed["stackName"] == "MINE"


@pytest.mark.unit
class TestStartBuild:
    def test_a_disabled_recipe_is_refused(self, world):
        row = seeded()
        row = store.update_recipe(row["recipeId"], enabled=False)
        assert recipes.start_build(row, "carol", only_if_changed=False) == (None, recipes.OUTCOME_REFUSED_DISABLED)

    def test_the_first_build_materializes_and_starts_version_1(self, world):
        row = seeded()
        build, outcome = recipes.start_build(row, recipes.REQUESTED_BY_DEPLOY, only_if_changed=True)
        assert outcome == recipes.OUTCOME_STARTED and build["version"] == 1 and build["status"] == store.STATUS_BUILDING
        assert build["imageBuildVersionArn"].endswith("/1") and build["resolvedParentAmi"] == "ami-0abc0abc0abc0abc0"
        assert store.get_recipe("al2023-desktop")["latestVersion"] == 1
        assert store.get_recipe("al2023-desktop")["imageBuilder"]["pipelineArn"] == "arn:p"
        world["ctx"].ib.start_image_pipeline_execution.assert_called_once()

    def test_deploy_skips_an_unchanged_recipe_but_admin_rebuilds_it(self, world):
        row = seeded()
        recipes.start_build(row, "deploy", only_if_changed=True)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        assert recipes.start_build(row, "deploy", only_if_changed=True)[1] == recipes.OUTCOME_SKIPPED_UNCHANGED
        build, outcome = recipes.start_build(row, "carol", only_if_changed=False)
        assert outcome == recipes.OUTCOME_STARTED and build["version"] == 2

    def test_deploy_rebuilds_a_recipe_whose_deployment_value_changed(self, world, monkeypatch):
        naming = {**RECIPE_BODY, "components": [RECIPE_BODY["components"][0], DEPLOY_COMPONENT]}
        ib = world["ctx"].ib
        for prefix, expected in [("team-a/edh-session/", recipes.OUTCOME_STARTED),
                                 ("team-a/edh-session/", recipes.OUTCOME_SKIPPED_UNCHANGED),
                                 ("team-b/edh-session/", recipes.OUTCOME_STARTED)]:
            monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", prefix)
            world["ctx"].deployment_parameters = {"sessionSecretPrefix": prefix}
            row, _ = recipes.upsert_config_recipe(naming, build_on_deploy=True)
            build, outcome = recipes.start_build(row, recipes.REQUESTED_BY_DEPLOY, only_if_changed=True)
            assert outcome == expected
            if outcome == recipes.OUTCOME_STARTED:
                assert f"# {prefix}" in ib.create_component.call_args.kwargs["data"]
                recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        assert ib.start_image_pipeline_execution.call_count == 2

    def test_a_request_for_changed_content_while_building_is_queued_then_promoted(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        queued, outcome = recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        assert outcome == recipes.OUTCOME_QUEUED and queued["status"] == store.STATUS_QUEUED and queued["version"] == 2
        recipes.handle_image_state(store.get_build("al2023-desktop", 1)["imageBuildVersionArn"], "AVAILABLE", None)
        promoted = store.get_build("al2023-desktop", 2)
        assert promoted["status"] == store.STATUS_BUILDING and promoted["requestedBy"] == "dave"

    def test_an_unresolvable_parent_is_a_conflict(self, world):
        row = seeded(parentImage="edhStack:Nope")
        with pytest.raises(recipes.RecipeConflict, match="Nope"):
            recipes.start_build(row, "carol", only_if_changed=False)

    def test_an_unresolved_component_placeholder_is_a_conflict(self, world):
        row = seeded(components=[{"name": "broken", "data": "schemaVersion: 1.0\n# ${PARAM_missing}\n"}])
        with pytest.raises(recipes.RecipeConflict, match="PARAM_missing"):
            recipes.start_build(row, "carol", only_if_changed=False)
        assert not store.list_builds(row["recipeId"])


@pytest.mark.unit
class TestCompletion:
    def _built(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        return row, store.latest_build("al2023-desktop")

    def test_available_registers_a_versioned_stack_and_tags_the_ami(self, world):
        row, build = self._built(world)
        out = recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        done = store.get_build("al2023-desktop", 1)
        assert out["outcome"] == "available" and done["status"] == store.STATUS_AVAILABLE
        assert done["amiId"] == "ami-0abc0abc0abc0abc0" and done["snapshotIds"] == ["snap-1"]
        assert done["edhStackName"] == "VAMS AL2023 Desktop v1" and done["edhStackId"] == "42"
        fields = world["edh"].register_software_stack.call_args.args[1]
        assert fields["base_os"] == "amazonlinux2023" and fields["root_size"] == "60" and fields["virtual_desktop_profile_id"] == "1"
        assert world["edh"].register_software_stack.call_args.args[0] == "dcv"
        tags = {t["Key"]: t["Value"] for t in world["ctx"].ec2.create_tags.call_args.kwargs["Tags"]}
        assert tags["vams:edhAmiRecipe"] == "al2023-desktop"
        assert tags[f"edh:visibility:{world['ctx'].cluster_id}"] == "true"

    def test_a_simulation_recipe_registers_a_target_node_stack(self, world):
        sim = {**RECIPE_BODY, "recipeId": "sim", "kind": "simulation", "stackName": "Sim"}
        sim.pop("baseOs")
        row = seeded(**sim)
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("sim")["imageBuildVersionArn"], "AVAILABLE", None)
        assert world["edh"].register_software_stack.call_args.args[0] == "target_nodes"
        assert world["edh"].register_software_stack.call_args.args[1]["target_node_profile_id"] == "1"

    def test_the_previous_stack_is_deactivated_and_old_amis_pruned(self, world):
        row, b1 = self._built(world)
        recipes.handle_image_state(b1["imageBuildVersionArn"], "AVAILABLE", None)
        for _ in (2, 3):
            recipes.start_build(row, "carol", only_if_changed=False)
            recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v1, v2, v3 = (store.get_build("al2023-desktop", v) for v in (1, 2, 3))
        assert v3["status"] == store.STATUS_AVAILABLE and v2["status"] == store.STATUS_RETIRED and v1["status"] == store.STATUS_RETIRED
        deactivated = [c.args[1] for c in world["edh"].delete_software_stack.call_args_list]
        assert deactivated == ["42", "43"]
        # keepVersions is 2: v1's AMI is gone, v2's and v3's stay.
        assert v1["amiDeregisteredAt"] and "amiDeregisteredAt" not in v2
        world["ctx"].ec2.deregister_image.assert_called_once_with(ImageId="ami-0abc0abc0abc0abc0")

    def test_a_failed_image_is_recorded_with_its_reason(self, world):
        row, build = self._built(world)
        recipes.handle_image_state(build["imageBuildVersionArn"], "FAILED", "step X failed")
        done = store.get_build("al2023-desktop", 1)
        assert done["status"] == store.STATUS_FAILED and done["error"] == "step X failed"
        world["edh"].register_software_stack.assert_not_called()

    def test_an_unknown_arn_is_reported_not_raised(self, world):
        assert recipes.handle_image_state("arn:aws:imagebuilder:us-east-1:123456789012:image/none/1.0.0/1", "AVAILABLE", None) == {"outcome": "unknown-build"}

    def test_a_registration_failure_is_register_failed_and_the_sweep_retries(self, world):
        row, build = self._built(world)
        world["edh"].register_software_stack.side_effect = EdhError("down")
        recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_REGISTER_FAILED
        world["edh"].register_software_stack.side_effect = None
        world["edh"].register_software_stack.return_value = {"message": "ok"}
        world["edh"].registered.append({"id": 42, "stack_name": "VAMS AL2023 Desktop v1", "ami_id": "ami-0abc0abc0abc0abc0"})
        summary = recipes.sweep()
        assert summary["retried"] == 1 and store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE

    def test_pruned_register_failed_is_retired_and_not_retried(self, world):
        row = seeded(keepVersions=1)
        recipes.start_build(row, "carol", only_if_changed=False)
        recording = world["edh"].register_software_stack.side_effect
        world["edh"].register_software_stack.side_effect = EdhError("down")
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTER_FAILED and v1["amiId"]
        recipes.start_build(row, "carol", only_if_changed=False)
        world["edh"].register_software_stack.side_effect = recording
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_RETIRED and v1["amiDeregisteredAt"]
        world["edh"].register_software_stack.reset_mock()
        recipes.sweep()
        world["edh"].register_software_stack.assert_not_called()
        with pytest.raises(recipes.RecipeConflict, match="deregistered"):
            recipes.retry_registration(row, v1)

    def test_a_catalog_that_cannot_be_read_after_register_is_register_failed(self, world):
        row, build = self._built(world)
        def list_fails(*args, **kwargs):
            raise EdhError("list down")
        world["edh"].list_software_stacks.side_effect = list_fails
        out = recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        done = store.get_build("al2023-desktop", 1)
        assert out["outcome"] == "register_failed" and done["status"] == store.STATUS_REGISTER_FAILED
        assert done["error"] == (
            f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' (the catalog could not be read back)")
        # The name is what the retry adopts the stack by.
        assert done["edhStackName"] == "VAMS AL2023 Desktop v1" and not done.get("edhStackId")
        assert "registeredAt" not in done
        world["edh"].register_software_stack.assert_called_once()
        world["edh"].assign_software_stack_to_project.assert_not_called()

    def test_overlapping_completion_returns_already_handled(self, world):
        row, build = self._built(world)
        store.update_build("al2023-desktop", 1, status=store.STATUS_REGISTERING)
        out = recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        assert out["outcome"] == "already-handled"
        world["edh"].register_software_stack.assert_not_called()

    def test_empty_stack_id_looked_up_and_deactivated_or_warned(self, world):
        row, b1 = self._built(world)
        recipes.handle_image_state(b1["imageBuildVersionArn"], "AVAILABLE", None)
        store.update_build("al2023-desktop", 1, edhStackId="")
        world["edh"].registered[0]["stack_name"] = "VAMS AL2023 Desktop v1"
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_RETIRED and v1["edhStackId"] == "42"
        world["edh"].delete_software_stack.assert_called_with("dcv", "42")
        store.put_build({"recipeId": "al2023-desktop", "version": 3, "status": store.STATUS_AVAILABLE,
                         "edhStackId": "", "edhStackName": "Nope", "amiId": "ami-3"})
        recipes.retire_previous(row, store.get_build("al2023-desktop", 2))
        v3 = store.get_build("al2023-desktop", 3)
        assert v3["status"] == store.STATUS_AVAILABLE and "unknown" in v3.get("retireWarning", "")
        with pytest.raises(recipes.RecipeConflict, match="unknown"):
            recipes.retire_build(row, v3)

    def test_tag_ami_raising_leaves_register_failed(self, world):
        row, build = self._built(world)
        world["ctx"].ec2.create_tags.side_effect = Exception("EC2 API error")
        out = recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        assert out["outcome"] == "register_failed"
        done = store.get_build("al2023-desktop", 1)
        assert done["status"] == store.STATUS_REGISTER_FAILED and "Exception" in done["error"]
        world["edh"].register_software_stack.assert_not_called()
        world["ctx"].ec2.create_tags.side_effect = None
        seeded()
        recipes.start_build(row, "queued", only_if_changed=False)
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_BUILDING

    def test_stale_registering_row_reset_and_retried(self, world):
        row, build = self._built(world)
        recording = world["edh"].register_software_stack.side_effect
        world["edh"].register_software_stack.side_effect = Exception("stuck")
        recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTER_FAILED
        from datetime import datetime, timedelta, timezone
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store._table().update_item(
            Key=store.build_key("al2023-desktop", 1),
            UpdateExpression="SET #status = :s, updatedAt = :u",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":s": store.STATUS_REGISTERING, ":u": stale_ts}
        )
        summary = recipes.sweep()
        assert summary["retried"] == 1
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTER_FAILED and "did not complete" in v1["error"]
        world["edh"].register_software_stack.side_effect = recording
        summary2 = recipes.sweep()
        v1_after = store.get_build("al2023-desktop", 1)
        assert summary2["retried"] == 1 and v1_after["status"] == store.STATUS_AVAILABLE

    def test_retry_registration_with_non_edh_error_stays_register_failed(self, world):
        row, build = self._built(world)
        world["edh"].register_software_stack.side_effect = Exception("unexpected")
        recipes.handle_image_state(build["imageBuildVersionArn"], "AVAILABLE", None)
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_REGISTER_FAILED
        world["edh"].register_software_stack.side_effect = None
        world["edh"].list_software_stacks.side_effect = Exception("list error")
        result = recipes.retry_registration(row, store.get_build("al2023-desktop", 1))
        assert result["status"] == store.STATUS_REGISTER_FAILED and "Exception" in result["error"]


def _unreadable(records):
    raise EdhUnavailableError("EDH returned 504 for GET /api/dcv/virtual_desktops/software_stacks")


def _without_the_newest(records):
    return records[:-1]


@pytest.mark.unit
class TestStackIdReadBack:
    """EDH answers a register with prose, so the new stack's id is read back from the catalog by name.
    A read-back that fails or misses leaves no id to assign to the launch project: the build is a failed
    registration that keeps the stack name, the earlier build keeps serving, and the retry adopts the
    stack by that name, assigns it and only then retires the earlier build."""

    def _v1_available_and_v2_built(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        recipes.start_build(row, "carol", only_if_changed=False)
        return row, store.get_build("al2023-desktop", 2)

    def _read_back_answers(self, world, answer):
        """The first catalog read after the next register is ``answer`` of what EDH holds."""
        edh = world["edh"]
        register, listing = edh.register_software_stack.side_effect, edh.list_software_stacks.side_effect
        armed = []

        def register_and_arm(kind, fields, **kwargs):
            armed.append(True)
            return register(kind, fields, **kwargs)

        def list_stacks(kind, **kwargs):
            if armed:
                armed.clear()
                return answer(listing(kind, **kwargs))
            return listing(kind, **kwargs)
        edh.register_software_stack.side_effect = register_and_arm
        edh.list_software_stacks.side_effect = list_stacks

    @pytest.mark.parametrize("answer, reason", [
        (_unreadable, "the catalog could not be read back"),
        (_without_the_newest, "its EDH id could not be read back"),
    ])
    def test_a_missed_read_back_keeps_the_previous_build_and_the_sweep_completes_it(self, world, answer, reason):
        _row, v2 = self._v1_available_and_v2_built(world)
        self._read_back_answers(world, answer)

        out = recipes.handle_image_state(v2["imageBuildVersionArn"], "AVAILABLE", None)

        v1, v2 = store.get_build("al2023-desktop", 1), store.get_build("al2023-desktop", 2)
        assert out["outcome"] == "register_failed" and v2["status"] == store.STATUS_REGISTER_FAILED
        assert v2["error"] == f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' ({reason})"
        assert v2["edhStackName"] == "VAMS AL2023 Desktop v2" and v2["edhKind"] == "vdi"
        assert not v2.get("edhStackId") and "registeredAt" not in v2
        assert v2["registerAttempts"] == 1
        # The build that still serves launches is neither deactivated nor pruned.
        assert v1["status"] == store.STATUS_AVAILABLE and "retiredAt" not in v1
        world["edh"].delete_software_stack.assert_not_called()
        assert world["edh"].projects.stack_ids("dcv") == ["42"]

        summary = recipes.sweep()

        v1, v2 = store.get_build("al2023-desktop", 1), store.get_build("al2023-desktop", 2)
        assert summary["retried"] == 1
        assert v2["status"] == store.STATUS_AVAILABLE and v2["edhStackId"] == "43" and not v2["error"]
        assert world["edh"].projects.stack_ids("dcv") == ["42", "43"]
        assert v1["status"] == store.STATUS_RETIRED
        world["edh"].delete_software_stack.assert_called_once_with("dcv", "42")
        # The stack EDH already held was adopted, not registered a second time.
        assert [s["stack_name"] for s in world["edh"].registered] == ["VAMS AL2023 Desktop v1", "VAMS AL2023 Desktop v2"]

    def test_the_administrators_retry_adopts_the_stack_as_well(self, world):
        row, v2 = self._v1_available_and_v2_built(world)
        self._read_back_answers(world, _without_the_newest)
        recipes.handle_image_state(v2["imageBuildVersionArn"], "AVAILABLE", None)

        healed = recipes.retry_registration(row, store.get_build("al2023-desktop", 2))

        assert healed["status"] == store.STATUS_AVAILABLE and healed["edhStackId"] == "43"
        assert healed["registerAttempts"] == 0
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_RETIRED
        assert world["edh"].register_software_stack.call_count == 2


@pytest.mark.unit
class TestSweepSettlesLostBuilds:
    """A build Image Builder no longer knows, or one past the build timeout with no result, is settled by
    the sweep so the recipe's queued build starts; a timed-out build that did finish is completed."""

    def _building_with_a_queued_successor(self, world, *, started_minutes_ago=0, queued=True):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        if queued:
            recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        if started_minutes_ago:
            started = (datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago)).isoformat()
            store.update_build("al2023-desktop", 1, startedAt=started)
        return row, store.get_build("al2023-desktop", 1)

    def _image_reports(self, world, status):
        world["ctx"].ib.get_image.return_value = {"image": {"state": {"status": status, "reason": None},
                                                            "outputResources": {"amis": []}}}

    def test_an_image_image_builder_no_longer_has_fails_the_build_and_starts_the_queued_one(self, world):
        _row, _v1 = self._building_with_a_queued_successor(world)
        world["ctx"].ib.get_image.side_effect = NOT_FOUND("image not found")

        summary = recipes.sweep()

        v1, v2 = store.get_build("al2023-desktop", 1), store.get_build("al2023-desktop", 2)
        assert v1["status"] == store.STATUS_FAILED and v1["completedAt"]
        assert v1["error"] == "the Image Builder image no longer exists"
        assert v2["status"] == store.STATUS_BUILDING and v2["requestedBy"] == "dave"
        assert summary["failed"] == 1 and summary["promoted"] == 1 and summary["warnings"] == []
        world["edh"].register_software_stack.assert_not_called()

    def test_a_build_past_the_timeout_is_cancelled_failed_and_the_queued_one_started(self, world):
        _row, v1 = self._building_with_a_queued_successor(world, started_minutes_ago=BUILD_TIMED_OUT)
        self._image_reports(world, "BUILDING")

        summary = recipes.sweep()

        world["ctx"].ib.cancel_image_creation.assert_called_once_with(imageBuildVersionArn=v1["imageBuildVersionArn"])
        v1, v2 = store.get_build("al2023-desktop", 1), store.get_build("al2023-desktop", 2)
        assert v1["status"] == store.STATUS_FAILED and v1["completedAt"]
        assert v1["error"] == f"no result after {recipes.BUILD_TIMEOUT_MINUTES} minutes"
        assert v2["status"] == store.STATUS_BUILDING
        assert summary["failed"] == 1 and summary["promoted"] == 1

    @pytest.mark.parametrize("refusal, named", [
        (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                     "CancelImageCreation"), "AccessDeniedException"),
        (FORBIDDEN("not authorized"), "ForbiddenException"),
        (EndpointConnectionError(endpoint_url="https://imagebuilder.example"), "EndpointConnectionError"),
    ])
    def test_a_refused_cancel_still_fails_the_timed_out_build_and_says_so(self, world, refusal, named):
        # A build whose cancel was refused may still hold a running build instance, so the refusal is
        # named on the row and in the sweep summary as well as logged.
        self._building_with_a_queued_successor(world, started_minutes_ago=BUILD_TIMED_OUT)
        self._image_reports(world, "TESTING")
        world["ctx"].ib.cancel_image_creation.side_effect = refusal

        with patch.object(recipes, "logger") as logger:
            summary = recipes.sweep()

        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_FAILED and v1["completedAt"]
        assert v1["error"] == f"no result after {recipes.BUILD_TIMEOUT_MINUTES} minutes; cancel failed ({named})"
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_BUILDING
        assert summary["failed"] == 1 and summary["promoted"] == 1
        assert summary["warnings"] == [f"al2023-desktop v1: cancel failed ({named})"]
        logger.exception.assert_called_once_with("al2023-desktop v1: image creation not cancelled")

    def test_a_build_past_the_timeout_that_finished_is_completed_not_failed(self, world):
        self._building_with_a_queued_successor(world, started_minutes_ago=BUILD_TIMED_OUT)

        summary = recipes.sweep()

        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE
        assert summary["completed"] == 1 and summary["failed"] == 0
        world["ctx"].ib.cancel_image_creation.assert_not_called()

    def test_a_running_build_inside_the_timeout_is_left_building(self, world):
        self._building_with_a_queued_successor(world, started_minutes_ago=recipes.BUILD_TIMEOUT_MINUTES - 5)
        self._image_reports(world, "BUILDING")

        summary = recipes.sweep()

        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_BUILDING
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_QUEUED
        assert summary["failed"] == 0 and summary["promoted"] == 0
        world["ctx"].ib.cancel_image_creation.assert_not_called()

    def test_a_completion_that_claims_the_build_first_keeps_its_claim(self, world):
        self._building_with_a_queued_successor(world, started_minutes_ago=BUILD_TIMED_OUT)
        running = {"image": {"state": {"status": "BUILDING", "reason": None}, "outputResources": {"amis": []}}}

        def read_while_the_completion_claims(**kwargs):
            store.update_build("al2023-desktop", 1, status=store.STATUS_REGISTERING)
            return running
        world["ctx"].ib.get_image.side_effect = read_while_the_completion_claims

        summary = recipes.sweep()

        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTERING and "error" not in v1
        assert summary["failed"] == 0
        world["ctx"].ib.cancel_image_creation.assert_not_called()

    def test_a_late_completion_event_does_not_revive_a_build_the_sweep_failed(self, world):
        _row, v1 = self._building_with_a_queued_successor(world, started_minutes_ago=BUILD_TIMED_OUT, queued=False)
        self._image_reports(world, "BUILDING")
        recipes.sweep()
        world["ctx"].ib.get_image.return_value = fake_context().ib.get_image.return_value

        out = recipes.handle_image_state(v1["imageBuildVersionArn"], "AVAILABLE", None)

        assert out == {"outcome": "already-handled", "status": store.STATUS_FAILED}
        recipes.sweep()
        failed = store.get_build("al2023-desktop", 1)
        assert failed["status"] == store.STATUS_FAILED and "amiId" not in failed
        world["edh"].register_software_stack.assert_not_called()
        world["ctx"].ec2.create_tags.assert_not_called()


@pytest.mark.unit
class TestQueuedBuildStartFailure:
    """A queued build that cannot start carries the reason on its row and stays queued, so the build
    starts once the cause is fixed; starting it clears the reason."""

    def _queued_behind_v1(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        return row, store.get_build("al2023-desktop", 1)

    def test_the_sweep_records_why_the_queued_build_did_not_start_and_starts_it_once_it_can(self, world):
        self._queued_behind_v1(world)
        world["ctx"].ib.get_image.return_value = {"image": {"state": {"status": "FAILED", "reason": "boom"}}}
        world["ctx"].ssm.get_parameter.side_effect = PARAMETER_MISSING

        summary = recipes.sweep()

        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_QUEUED and v2["error"] == PARENT_UNREADABLE
        assert summary["promoted"] == 0 and summary["warnings"] == ["al2023-desktop: RecipeConflict"]

        world["ctx"].ssm.get_parameter.side_effect = None
        summary = recipes.sweep()

        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_BUILDING and "error" not in v2
        assert summary["promoted"] == 1

    def test_a_completion_records_it_on_the_queued_row_and_reports_it_to_its_invoker(self, world):
        _row, v1 = self._queued_behind_v1(world)
        world["ctx"].ssm.get_parameter.side_effect = PARAMETER_MISSING

        with pytest.raises(recipes.RecipeConflict):
            recipes.handle_image_state(v1["imageBuildVersionArn"], "AVAILABLE", None)

        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE
        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_QUEUED and v2["error"] == PARENT_UNREADABLE

    def test_an_unexpected_start_failure_is_named_by_its_type(self, world):
        self._queued_behind_v1(world)
        world["ctx"].ib.get_image.return_value = {"image": {"state": {"status": "CANCELLED", "reason": None}}}
        world["ctx"].ib.start_image_pipeline_execution.side_effect = FORBIDDEN("not authorized")

        recipes.sweep()

        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_QUEUED and v2["error"] == "not started: ForbiddenException"


@pytest.mark.unit
class TestArchitectureAtSave:
    """A create, update or duplicate is refused (RecipeInvalid, which the API answers with 400) only when
    EC2 answers that a build instance type, or the parent image, runs another architecture family than
    the recipe's. Any other answer saves the recipe with a warning naming why it was not checked, and
    the build start checks again. The refusal names neither the types nor the AMI; the warning does."""

    def test_a_build_instance_type_of_another_family_is_refused_before_the_write(self, world, recipe_log):
        ec2 = world["ctx"].ec2
        ec2.describe_instance_types.return_value = instance_types({GRAVITON: ["arm64"]})

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe(buildInstanceType=GRAVITON)

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert store.get_recipe("al2023-desktop") is None
        ec2.describe_instance_types.assert_called_once_with(InstanceTypes=[GRAVITON])
        assert any(GRAVITON in warning for warning in warnings_of(recipe_log))

    def test_one_type_of_another_family_in_a_list_is_refused(self, world):
        world["ctx"].ec2.describe_instance_types.return_value = instance_types(
            {"m7i.large": ["x86_64"], GRAVITON: ["arm64"], "m6i.large": ["x86_64"]})

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe(buildInstanceType=["m7i.large", GRAVITON, "m6i.large"])

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert store.get_recipe("al2023-desktop") is None

    def test_a_type_of_another_family_beside_one_ec2_does_not_offer_is_refused(self, world, recipe_log):
        world["ctx"].ec2.describe_instance_types.side_effect = refusing_not_offered({GRAVITON: ["arm64"]})

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe(buildInstanceType=[GRAVITON, NOT_OFFERED])

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert store.get_recipe("al2023-desktop") is None
        assert any(NOT_OFFERED in warning for warning in warnings_of(recipe_log))

    def test_an_update_to_a_type_of_another_family_beside_one_ec2_does_not_offer_is_refused(self, world):
        row = admin_recipe()
        world["ctx"].ec2.describe_instance_types.side_effect = refusing_not_offered({GRAVITON: ["arm64"]})

        with pytest.raises(recipes.RecipeInvalid) as err:
            recipes.update_admin_recipe(
                row, AmiRecipeRequestModel(**{**RECIPE_BODY, "buildInstanceType": [GRAVITON, NOT_OFFERED]}), "dave")

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert store.get_recipe("al2023-desktop")["buildInstanceType"] == RECIPE_BODY["buildInstanceType"]

    @pytest.mark.parametrize("build_types", [NOT_OFFERED, ["m7i.large", NOT_OFFERED]], ids=["alone", "in-a-list"])
    def test_a_type_ec2_does_not_offer_saves_the_recipe_and_names_it(self, world, recipe_log, build_types):
        world["ctx"].ec2.describe_instance_types.side_effect = refusing_not_offered({"m7i.large": ["x86_64"]})

        assert admin_recipe(buildInstanceType=build_types)["buildInstanceType"] == build_types

        assert any(NOT_OFFERED in warning for warning in warnings_of(recipe_log))

    def test_types_ec2_lists_under_i386_run_an_x86_64_recipe_and_an_arm64_recipe_takes_graviton(self, world):
        world["ctx"].ec2.describe_instance_types.return_value = instance_types(
            {"t2.medium": ["i386", "x86_64"], "c1.medium": ["i386"], "m7i.large": ["x86_64"], GRAVITON: ["arm64"]})
        assert admin_recipe(buildInstanceType=["t2.medium", "c1.medium"])["buildInstanceType"] == ["t2.medium", "c1.medium"]
        arm64 = {"recipeId": "arm-desktop", "stackName": "Arm desktop", "architecture": "arm64"}

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe(**arm64, buildInstanceType="m7i.large")

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert admin_recipe(**arm64, buildInstanceType=GRAVITON)["architecture"] == "arm64"

    @pytest.mark.parametrize("parent", [PARENT_AMI, "ssm:/aws/service/canonical/ubuntu/server/24.04/arm64/ami-id"])
    def test_a_parent_image_of_another_family_is_refused_before_the_write(self, world, recipe_log, parent):
        ctx = world["ctx"]
        ctx.ec2.describe_images.return_value = parent_image("arm64")
        described = parent if parent.startswith("ami-") else PARAMETER_AMI

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe(parentImage=parent)

        assert str(err.value) == PARENT_IMAGE_MISMATCH
        assert store.get_recipe("al2023-desktop") is None
        ctx.ec2.describe_images.assert_called_once_with(ImageIds=[described])
        if parent.startswith("ssm:"):
            ctx.ssm.get_parameter.assert_called_once_with(Name=parent[len("ssm:"):])
        else:
            ctx.ssm.get_parameter.assert_not_called()
        assert any(described in warning for warning in warnings_of(recipe_log))
        ctx.ec2.describe_images.return_value = parent_image("x86_64")
        assert admin_recipe(parentImage=parent)["parentImage"] == parent

    def test_a_duplicate_is_checked_as_a_create(self, world):
        seeded()
        world["ctx"].ec2.describe_instance_types.return_value = instance_types({GRAVITON: ["arm64"]})
        copy = AmiRecipeRequestModel(**{**RECIPE_BODY, "recipeId": "al2023-copy", "stackName": "VAMS AL2023 Copy",
                                        "duplicateOf": "al2023-desktop", "buildInstanceType": GRAVITON})

        with pytest.raises(recipes.RecipeInvalid) as err:
            recipes.create_admin_recipe(copy, "carol")

        assert str(err.value) == BUILD_TYPE_MISMATCH
        assert store.get_recipe("al2023-copy") is None

    def test_an_update_is_refused_and_leaves_the_recipe_as_it_was(self, world):
        row = admin_recipe()
        world["ctx"].ec2.describe_images.return_value = parent_image("arm64")

        with pytest.raises(recipes.RecipeInvalid) as err:
            recipes.update_admin_recipe(row, AmiRecipeRequestModel(**{**RECIPE_BODY, "rootSizeGb": 80}), "dave")

        assert str(err.value) == PARENT_IMAGE_MISMATCH
        assert store.get_recipe("al2023-desktop")["rootSizeGb"] == 60

    def test_an_update_whose_write_is_overtaken_looks_the_architecture_up_once(self, world, monkeypatch):
        row = admin_recipe()
        ec2 = world["ctx"].ec2
        ec2.describe_instance_types.reset_mock()
        ec2.describe_images.reset_mock()
        put = store.put_recipe
        writes = []

        def overtaken_once(item, *, replacing=None):
            writes.append(item["recipeId"])
            return None if len(writes) == 1 else put(item, replacing=replacing)
        monkeypatch.setattr(store, "put_recipe", overtaken_once)

        updated = recipes.update_admin_recipe(row, AmiRecipeRequestModel(**{**RECIPE_BODY, "rootSizeGb": 80}), "dave")

        assert updated["rootSizeGb"] == 80 and len(writes) == 2
        assert ec2.describe_instance_types.call_count == 1 and ec2.describe_images.call_count == 1

    @pytest.mark.parametrize("failure,reason", [
        (UNAUTHORIZED, "UnauthorizedOperation"), (THROTTLED, "RequestLimitExceeded"),
        (UNREACHABLE, "EndpointConnectionError"), (TIMED_OUT, "ReadTimeoutError"),
        (RuntimeError("unexpected"), "RuntimeError"),
    ], ids=["unauthorized", "throttled", "unreachable", "timed-out", "unexpected"])
    def test_a_type_lookup_that_fails_saves_the_recipe_and_names_why(self, world, recipe_log, failure, reason):
        world["ctx"].ec2.describe_instance_types.side_effect = failure

        assert admin_recipe(buildInstanceType=GRAVITON)["buildInstanceType"] == GRAVITON

        assert store.get_recipe("al2023-desktop") is not None
        assert any(reason in warning for warning in warnings_of(recipe_log))

    @pytest.mark.parametrize("instance_type,architectures", [
        (GRAVITON, {GRAVITON: []}), ("mac2.metal", {"mac2.metal": ["arm64_mac"]}), (GRAVITON, {}),
    ], ids=["no-architecture", "mac", "not-listed"])
    def test_a_type_of_no_known_architecture_saves_the_recipe_and_names_it(self, world, recipe_log, instance_type,
                                                                           architectures):
        world["ctx"].ec2.describe_instance_types.return_value = instance_types(architectures)

        assert admin_recipe(buildInstanceType=instance_type)["buildInstanceType"] == instance_type

        assert any(instance_type in warning for warning in warnings_of(recipe_log))

    @pytest.mark.parametrize("failure,reason", [
        (UNAUTHORIZED, "UnauthorizedOperation"), (THROTTLED, "RequestLimitExceeded"),
        (UNREACHABLE, "EndpointConnectionError"), (AMI_NOT_FOUND, "ParentImageUnavailable"),
        (AMI_MALFORMED, "ParentImageUnavailable"), (RuntimeError("unexpected"), "RuntimeError"),
    ], ids=["unauthorized", "throttled", "unreachable", "not-found", "malformed", "unexpected"])
    def test_a_parent_lookup_that_fails_saves_the_recipe_and_names_why(self, world, recipe_log, failure, reason):
        world["ctx"].ec2.describe_images.side_effect = failure

        assert admin_recipe(parentImage=PARENT_AMI)["parentImage"] == PARENT_AMI

        assert any(reason in warning and PARENT_AMI in warning for warning in warnings_of(recipe_log))

    @pytest.mark.parametrize("answer", [{"Images": []}, parent_image(None), parent_image("arm64_mac")],
                             ids=["no-image", "no-architecture", "mac"])
    def test_a_parent_of_no_known_architecture_saves_the_recipe_and_names_it(self, world, recipe_log, answer):
        world["ctx"].ec2.describe_images.return_value = answer

        assert admin_recipe(parentImage=PARENT_AMI)["parentImage"] == PARENT_AMI

        assert any(PARENT_AMI in warning for warning in warnings_of(recipe_log))

    def test_a_parameter_that_cannot_be_read_saves_the_recipe_and_names_why(self, world, recipe_log):
        world["ctx"].ssm.get_parameter.side_effect = PARAMETER_MISSING

        assert admin_recipe()["parentImage"] == RECIPE_BODY["parentImage"]

        world["ctx"].ec2.describe_images.assert_not_called()
        assert any("ParameterNotFound" in warning for warning in warnings_of(recipe_log))

    def test_an_edh_stack_parent_is_left_to_the_build_start(self, world, recipe_log):
        ctx = world["ctx"]
        ctx.ec2.describe_images.return_value = parent_image("arm64")

        assert admin_recipe(parentImage="edhStack:VAMS Base")["parentImage"] == "edhStack:VAMS Base"

        ctx.ec2.describe_images.assert_not_called()
        ctx.ssm.get_parameter.assert_not_called()
        assert any("edhStack" in warning for warning in warnings_of(recipe_log))

    def test_a_context_that_cannot_be_built_saves_the_recipe_and_names_why(self, world, recipe_log, monkeypatch):
        def no_context():
            raise KeyError("EDH_AMI_BUILD_PREFIX")
        monkeypatch.setattr(recipes, "_ctx", no_context)

        assert admin_recipe()["recipeId"] == "al2023-desktop"

        assert any("KeyError" in warning for warning in warnings_of(recipe_log))

    @pytest.mark.parametrize("operation", ["create", "duplicate", "update"])
    def test_a_save_looks_the_architecture_up_through_the_short_timeout_clients_only(self, world, operation):
        ctx = world["ctx"]
        if operation == "duplicate":
            seeded()
            model = AmiRecipeRequestModel(**{**RECIPE_BODY, "recipeId": "al2023-copy", "stackName": "VAMS AL2023 Copy",
                                             "duplicateOf": "al2023-desktop"})
        elif operation == "update":
            row = admin_recipe()
            model = AmiRecipeRequestModel(**{**RECIPE_BODY, "rootSizeGb": 80})
        else:
            model = AmiRecipeRequestModel(**RECIPE_BODY)
        lookups = separate_lookup_clients(ctx)

        if operation == "update":
            recipes.update_admin_recipe(row, model, "dave")
        else:
            recipes.create_admin_recipe(model, "carol")

        lookups.ec2.describe_instance_types.assert_called_once_with(InstanceTypes=[RECIPE_BODY["buildInstanceType"]])
        lookups.ssm.get_parameter.assert_called_once_with(Name=RECIPE_BODY["parentImage"][len("ssm:"):])
        lookups.ec2.describe_images.assert_called_once_with(ImageIds=[PARAMETER_AMI])
        assert ctx.ec2.method_calls == [] and ctx.ssm.method_calls == []

    @pytest.mark.parametrize("slow,parent,skipped", [
        ("describe_instance_types", RECIPE_BODY["parentImage"], ("get_parameter", "describe_images")),
        ("describe_instance_types", PARENT_AMI, ("describe_images",)),
        ("get_parameter", RECIPE_BODY["parentImage"], ("describe_images",)),
    ], ids=["types-then-parameter", "types-then-ami", "parameter-then-ami"])
    def test_no_parent_lookup_starts_once_the_lookups_have_run_their_budget(self, world, recipe_log, monkeypatch,
                                                                            slow, parent, skipped):
        ctx = world["ctx"]
        ctx.ec2.describe_images.return_value = parent_image("arm64")
        lookup_takes(monkeypatch, ctx, slow, recipes.ARCHITECTURE_LOOKUP_BUDGET_SECONDS)

        assert admin_recipe(parentImage=parent)["parentImage"] == parent

        for call in skipped:
            getattr(ctx.lookup_ssm if call == "get_parameter" else ctx.lookup_ec2, call).assert_not_called()
        assert any("lookup budget" in warning for warning in warnings_of(recipe_log))

    @pytest.mark.parametrize("slow", ["describe_instance_types", "get_parameter"])
    def test_a_lookup_that_answers_within_the_budget_lets_the_parent_be_checked(self, world, monkeypatch, slow):
        ctx = world["ctx"]
        ctx.ec2.describe_images.return_value = parent_image("arm64")
        lookup_takes(monkeypatch, ctx, slow, recipes.ARCHITECTURE_LOOKUP_BUDGET_SECONDS - 1)

        with pytest.raises(recipes.RecipeInvalid) as err:
            admin_recipe()

        assert str(err.value) == PARENT_IMAGE_MISMATCH
        ctx.ec2.describe_images.assert_called_once_with(ImageIds=[PARAMETER_AMI])


@pytest.mark.unit
class TestArchitectureAtBuildStart:
    """A build start describes the parent it resolved, whatever the parentImage form, and refuses
    (RecipeConflict, which the API answers with 409) a parent EC2 does not find and a parent or build
    instance type of another architecture family, before any Image Builder object or build row is
    created. A lookup that fails otherwise lets the build start, with a warning naming why."""

    def _refused(self, world, row, message):
        with pytest.raises(recipes.RecipeConflict) as err:
            recipes.start_build(row, "carol", only_if_changed=False)
        assert str(err.value) == message
        ib = world["ctx"].ib
        ib.create_component.assert_not_called()
        ib.create_image_recipe.assert_not_called()
        ib.create_infrastructure_configuration.assert_not_called()
        ib.start_image_pipeline_execution.assert_not_called()
        assert store.list_builds(row["recipeId"]) == []

    @pytest.mark.parametrize("answer", [AMI_NOT_FOUND, AMI_MALFORMED, {"Images": []}],
                             ids=["not-found", "malformed", "no-image"])
    def test_a_parent_ec2_does_not_find_is_refused_and_creates_nothing(self, world, answer):
        describe = world["ctx"].ec2.describe_images
        if isinstance(answer, Exception):
            describe.side_effect = answer
        else:
            describe.return_value = answer
        self._refused(world, seeded(parentImage=PARENT_AMI), PARENT_NOT_FOUND)

    @pytest.mark.parametrize("parent,resolved", [
        (RECIPE_BODY["parentImage"], PARAMETER_AMI), (PARENT_AMI, PARENT_AMI), ("edhStack:VAMS Base", PARENT_AMI),
    ], ids=["ssm", "ami", "edhStack"])
    def test_a_parent_of_another_family_is_refused_whatever_its_form(self, world, parent, resolved):
        world["edh"].list_software_stacks.side_effect = (
            lambda kind, **kwargs: [{"id": 7, "stack_name": "VAMS Base", "ami_id": PARENT_AMI}])
        world["ctx"].ec2.describe_images.return_value = parent_image("arm64")

        self._refused(world, seeded(parentImage=parent), PARENT_MISMATCH)

        world["ctx"].ec2.describe_images.assert_called_once_with(ImageIds=[resolved])

    def test_a_build_instance_type_of_another_family_is_refused_on_a_config_recipe(self, world):
        ec2 = world["ctx"].ec2
        ec2.describe_instance_types.return_value = instance_types({"m7i.large": ["x86_64"], GRAVITON: ["arm64"]})

        self._refused(world, seeded(buildInstanceType=["m7i.large", GRAVITON]), BUILD_TYPE_MISMATCH)

        ec2.describe_instance_types.assert_called_once_with(InstanceTypes=["m7i.large", GRAVITON])

    def test_a_type_of_another_family_beside_one_ec2_does_not_offer_is_refused(self, world):
        world["ctx"].ec2.describe_instance_types.side_effect = refusing_not_offered({GRAVITON: ["arm64"]})

        self._refused(world, seeded(buildInstanceType=[GRAVITON, NOT_OFFERED]), BUILD_TYPE_MISMATCH)

    def test_a_type_ec2_does_not_offer_lets_the_build_start_and_is_named(self, world, recipe_log):
        world["ctx"].ec2.describe_instance_types.side_effect = refusing_not_offered({"m7i.large": ["x86_64"]})

        _build, outcome = recipes.start_build(seeded(buildInstanceType=["m7i.large", NOT_OFFERED]), "carol",
                                              only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED
        assert any(NOT_OFFERED in warning for warning in warnings_of(recipe_log))

    def test_the_parent_is_described_once_before_any_component_and_names_the_root_volume(self, world):
        ctx = world["ctx"]
        calls = []
        ctx.ec2.describe_images.side_effect = (
            lambda **kwargs: calls.append("describe_images") or parent_image("x86_64", root_device="/dev/sda1"))
        ctx.ib.create_component.side_effect = (
            lambda **kwargs: calls.append("create_component") or {"componentBuildVersionArn": "arn:c/1"})

        _build, outcome = recipes.start_build(seeded(), "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED
        assert calls == ["describe_images", "create_component"]
        assert ctx.ib.create_image_recipe.call_args.kwargs["blockDeviceMappings"][0]["deviceName"] == "/dev/sda1"

    @pytest.mark.parametrize("failure,reason", [
        (UNAUTHORIZED, "UnauthorizedOperation"), (THROTTLED, "RequestLimitExceeded"),
        (UNREACHABLE, "EndpointConnectionError"),
    ], ids=["unauthorized", "throttled", "unreachable"])
    def test_a_parent_lookup_that_fails_once_lets_the_build_start(self, world, recipe_log, failure, reason):
        describe = world["ctx"].ec2.describe_images
        describe.side_effect = [failure, parent_image("x86_64", root_device="/dev/sda1")]

        _build, outcome = recipes.start_build(seeded(), "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED and describe.call_count == 2
        kwargs = world["ctx"].ib.create_image_recipe.call_args.kwargs
        assert kwargs["blockDeviceMappings"][0]["deviceName"] == "/dev/sda1"
        assert any(reason in warning for warning in warnings_of(recipe_log))

    def test_a_parent_ec2_does_not_find_after_a_failed_lookup_is_refused_and_creates_nothing(self, world):
        # The first describe fails open; the one before the Image Builder objects then finds no AMI.
        world["ctx"].ec2.describe_images.side_effect = [THROTTLED, AMI_NOT_FOUND]

        self._refused(world, seeded(parentImage=PARENT_AMI), PARENT_NOT_FOUND)

    @pytest.mark.parametrize("failure,reason", [
        (THROTTLED, "RequestLimitExceeded"), (UNREACHABLE, "EndpointConnectionError"),
    ], ids=["throttled", "unreachable"])
    def test_a_type_lookup_that_fails_lets_the_build_start(self, world, recipe_log, failure, reason):
        world["ctx"].ec2.describe_instance_types.side_effect = failure

        _build, outcome = recipes.start_build(seeded(buildInstanceType=GRAVITON), "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED
        assert any(reason in warning for warning in warnings_of(recipe_log))

    def test_a_build_start_looks_up_through_the_build_clients_only(self, world):
        ctx = world["ctx"]
        row = seeded()
        lookups = separate_lookup_clients(ctx)

        _build, outcome = recipes.start_build(row, "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED
        ctx.ec2.describe_images.assert_called_once_with(ImageIds=[PARAMETER_AMI])
        ctx.ec2.describe_instance_types.assert_called_once_with(InstanceTypes=[RECIPE_BODY["buildInstanceType"]])
        assert lookups.ec2.method_calls == [] and lookups.ssm.method_calls == []

    def test_a_queued_build_refused_for_its_architecture_stays_queued_until_the_recipe_is_fixed(self, world):
        recipes.start_build(seeded(), "carol", only_if_changed=False)
        recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        world["ctx"].ib.get_image.return_value = {"image": {"state": {"status": "FAILED", "reason": "boom"}}}
        world["ctx"].ec2.describe_images.return_value = parent_image("arm64")

        for _ in range(2):
            summary = recipes.sweep()
            v2 = store.get_build("al2023-desktop", 2)
            assert v2["status"] == store.STATUS_QUEUED and v2["error"] == f"not started: {PARENT_MISMATCH}"
            assert summary["promoted"] == 0

        world["ctx"].ec2.describe_images.return_value = parent_image("x86_64")
        summary = recipes.sweep()

        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_BUILDING and "error" not in v2
        assert summary["promoted"] == 1


def _minutes_ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.mark.unit
class TestBuildClaims:
    """A request claims its version row before any pipeline starts, so of two concurrent requests one
    starts the build and the other re-reads. A manual request for content already queued or building is
    answered with that build, and a launch that does not start leaves no row behind."""

    def _claimed_first_by(self, monkeypatch, competing_request):
        """Run ``competing_request`` once, inside this request's first claim and before it is written."""
        claim = store.claim_new_build
        competitor = {}

        def claim_after_the_competitor(item):
            if "answer" not in competitor:
                competitor["answer"] = None
                competitor["answer"] = competing_request()
            return claim(item)
        monkeypatch.setattr(store, "claim_new_build", claim_after_the_competitor)
        return competitor

    @pytest.mark.parametrize("status", [store.STATUS_BUILDING, store.STATUS_REGISTERING])
    def test_the_same_content_while_a_build_is_active_is_answered_with_that_build(self, world, status):
        row = seeded()
        first, _ = recipes.start_build(row, "carol", only_if_changed=False)
        store.update_build("al2023-desktop", 1, status=status)

        again, outcome = recipes.start_build(row, "dave", only_if_changed=False)

        assert outcome == recipes.OUTCOME_IN_PROGRESS
        assert again["version"] == 1 and again["status"] == status and again["requestedBy"] == "carol"
        assert {b["version"] for b in store.list_builds("al2023-desktop")} == {1}
        assert first["imageBuildVersionArn"] and world["ctx"].ib.start_image_pipeline_execution.call_count <= 1

    def test_the_same_content_as_a_queued_build_is_answered_with_the_queued_build(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        changed = seeded(recipeRevision=2)
        recipes.start_build(changed, "dave", only_if_changed=False)

        again, outcome = recipes.start_build(changed, "erin", only_if_changed=False)

        assert outcome == recipes.OUTCOME_IN_PROGRESS
        assert again["version"] == 2 and again["status"] == store.STATUS_QUEUED and again["requestedBy"] == "dave"
        assert store.get_build("al2023-desktop", 3) is None

    def test_changed_content_while_a_build_is_active_is_queued(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)

        queued, outcome = recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)

        assert outcome == recipes.OUTCOME_QUEUED and queued["version"] == 2 and queued["status"] == store.STATUS_QUEUED
        assert store.get_recipe("al2023-desktop")["latestVersion"] == 2

    def test_the_same_content_with_nothing_in_progress_starts_a_build(self, world):
        row = seeded()
        first, _ = recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(first["imageBuildVersionArn"], "AVAILABLE", None)

        build, outcome = recipes.start_build(row, "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED and build["version"] == 2
        assert build["contentHash"] == first["contentHash"]
        assert build["imageBuildVersionArn"] != first["imageBuildVersionArn"]

    def test_the_version_row_is_held_before_the_pipeline_starts(self, world):
        row = seeded()
        start = world["ctx"].ib.start_image_pipeline_execution.side_effect
        held = {}

        def start_and_look(**kwargs):
            held["row"] = store.get_build("al2023-desktop", 1)
            return start(**kwargs)
        world["ctx"].ib.start_image_pipeline_execution.side_effect = start_and_look

        build, _ = recipes.start_build(row, "carol", only_if_changed=False)

        assert held["row"]["status"] == store.STATUS_BUILDING and held["row"]["requestedBy"] == "carol"
        assert "imageBuildVersionArn" not in held["row"]
        assert store.get_build("al2023-desktop", 1)["imageBuildVersionArn"] == build["imageBuildVersionArn"]

    def test_a_request_that_loses_the_claim_starts_nothing_and_is_answered_with_the_winner(self, world, monkeypatch):
        row = seeded()
        competitor = self._claimed_first_by(
            monkeypatch, lambda: recipes.start_build(row, "dave", only_if_changed=False))

        build, outcome = recipes.start_build(row, "carol", only_if_changed=False)

        winner, winner_outcome = competitor["answer"]
        assert winner_outcome == recipes.OUTCOME_STARTED and winner["requestedBy"] == "dave"
        assert outcome == recipes.OUTCOME_IN_PROGRESS and build["version"] == winner["version"] == 1
        assert build["imageBuildVersionArn"] == winner["imageBuildVersionArn"]
        assert world["ctx"].ib.start_image_pipeline_execution.call_count <= 1
        assert store.get_build("al2023-desktop", 1)["requestedBy"] == "dave"

    def test_a_queued_request_that_loses_the_claim_queues_behind_the_winner(self, world, monkeypatch):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        changed = seeded(recipeRevision=2)
        edited_again = {**changed, "contentHash": "e" * 64}
        competitor = self._claimed_first_by(
            monkeypatch, lambda: recipes.start_build(changed, "dave", only_if_changed=False))

        build, outcome = recipes.start_build(edited_again, "erin", only_if_changed=False)

        winner, winner_outcome = competitor["answer"]
        assert winner_outcome == recipes.OUTCOME_QUEUED and winner["version"] == 2
        assert outcome == recipes.OUTCOME_QUEUED and build["version"] == 3 and build["requestedBy"] == "erin"
        assert store.get_build("al2023-desktop", 2)["requestedBy"] == "dave"

    def test_a_deploy_build_that_loses_the_claim_to_a_manual_one_does_not_overwrite_it(self, world, monkeypatch):
        row = seeded()
        competitor = self._claimed_first_by(
            monkeypatch, lambda: recipes.start_build(row, "carol", only_if_changed=False))

        build, outcome = recipes.start_build(row, recipes.REQUESTED_BY_DEPLOY, only_if_changed=True)

        manual, _ = competitor["answer"]
        assert outcome == recipes.OUTCOME_SKIPPED_UNCHANGED and build["version"] == manual["version"] == 1
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["requestedBy"] == "carol" and v1["imageBuildVersionArn"] == manual["imageBuildVersionArn"]
        assert world["ctx"].ib.start_image_pipeline_execution.call_count <= 1

    def test_a_launch_refused_after_the_claim_leaves_no_row_and_blocks_no_later_build(self, world):
        row = seeded()
        world["ctx"].ssm.get_parameter.side_effect = PARAMETER_MISSING

        with pytest.raises(recipes.RecipeConflict):
            recipes.start_build(row, "carol", only_if_changed=False)

        assert store.list_builds("al2023-desktop") == []
        world["ctx"].ssm.get_parameter.side_effect = None
        build, outcome = recipes.start_build(row, "carol", only_if_changed=False)
        assert outcome == recipes.OUTCOME_STARTED and build["version"] == 1

    def test_a_pipeline_that_does_not_start_releases_its_claimed_version(self, world):
        row = seeded()
        ib = world["ctx"].ib
        start = ib.start_image_pipeline_execution.side_effect
        ib.start_image_pipeline_execution.side_effect = FORBIDDEN("not authorized")

        with pytest.raises(FORBIDDEN):
            recipes.start_build(row, "carol", only_if_changed=False)

        assert store.list_builds("al2023-desktop") == []
        ib.start_image_pipeline_execution.side_effect = start
        assert recipes.start_build(row, "carol", only_if_changed=False)[1] == recipes.OUTCOME_STARTED

    def test_a_queued_build_two_promoters_reach_at_once_starts_once(self, world, monkeypatch):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        changed = seeded(recipeRevision=2)
        recipes.start_build(changed, "dave", only_if_changed=False)
        # v1 settled on a path that promoted nothing, so the sweep and a manual request both find v2 waiting.
        store.update_build("al2023-desktop", 1, status=store.STATUS_FAILED)
        ib = world["ctx"].ib
        started_before = ib.start_image_pipeline_execution.call_count
        claim = store.claim_build
        manual = {}

        def the_manual_request_claims_first(recipe_id, version, expected_status, **attrs):
            if expected_status == store.STATUS_QUEUED and "answer" not in manual:
                manual["answer"] = None
                manual["answer"] = recipes.start_build(changed, "erin", only_if_changed=False)
            return claim(recipe_id, version, expected_status, **attrs)
        monkeypatch.setattr(store, "claim_build", the_manual_request_claims_first)

        summary = recipes.sweep()

        promoted, outcome = manual["answer"]
        assert outcome == recipes.OUTCOME_STARTED and promoted["version"] == 2 and promoted["requestedBy"] == "dave"
        assert summary["promoted"] == 0
        assert ib.start_image_pipeline_execution.call_count - started_before <= 1
        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_BUILDING and v2["imageBuildVersionArn"] == promoted["imageBuildVersionArn"]
        assert store.get_build("al2023-desktop", 3) is None

    def test_a_request_starts_a_queued_build_nothing_is_ahead_of(self, world):
        row = seeded()
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_QUEUED,
                         "contentHash": row["contentHash"], "requestedBy": "dave"})

        build, outcome = recipes.start_build(row, "carol", only_if_changed=False)

        assert outcome == recipes.OUTCOME_STARTED and build["version"] == 1 and build["requestedBy"] == "dave"
        assert build["imageBuildVersionArn"] and store.get_build("al2023-desktop", 2) is None

    def test_the_sweep_fails_a_claim_whose_launch_never_finished_and_starts_the_queued_build(self, world):
        row = seeded()
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_BUILDING,
                         "contentHash": row["contentHash"], "requestedBy": "carol",
                         "claimedAt": _minutes_ago(recipes.CLAIM_STALE_MINUTES + 5)})
        store.put_build({"recipeId": "al2023-desktop", "version": 2, "status": store.STATUS_QUEUED,
                         "contentHash": row["contentHash"], "requestedBy": "dave"})

        summary = recipes.sweep()

        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_FAILED and v1["completedAt"]
        assert v1["error"] == recipes.LAUNCH_NOT_COMPLETED
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_BUILDING
        assert summary["failed"] == 1 and summary["promoted"] == 1

    def test_a_claim_inside_the_window_is_left_to_its_launch(self, world):
        row = seeded()
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_BUILDING,
                         "contentHash": row["contentHash"], "requestedBy": "carol", "claimedAt": _minutes_ago(1)})
        store.put_build({"recipeId": "al2023-desktop", "version": 2, "status": store.STATUS_QUEUED,
                         "contentHash": row["contentHash"], "requestedBy": "dave"})

        summary = recipes.sweep()

        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_BUILDING and "error" not in v1
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_QUEUED
        assert summary["failed"] == 0 and summary["promoted"] == 0


@pytest.mark.unit
class TestBuildRowsWrittenBeforeClaims:
    """A BUILDING row written before version rows were claimed carries its image ARN and no claim
    marker. The registrar completes it, the sweep follows its image, and a request is answered from it."""

    def _in_flight(self, row, started_minutes_ago=5):
        started = _minutes_ago(started_minutes_ago)
        store.update_recipe("al2023-desktop", latestVersion=7)
        return store.put_build({"recipeId": "al2023-desktop", "version": 7, "status": store.STATUS_BUILDING,
                                "contentHash": row["contentHash"], "requestedBy": "carol", "requestedAt": started,
                                "startedAt": started, "imageBuildVersionArn": ARN_BEFORE_CLAIMS,
                                "resolvedParentAmi": "ami-0abc0abc0abc0abc0", "buildKey": "b7"})

    def test_its_completion_registers_it_and_starts_the_build_queued_behind_it(self, world):
        row = seeded()
        self._in_flight(row)
        queued, outcome = recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        assert outcome == recipes.OUTCOME_QUEUED and queued["version"] == 8

        out = recipes.handle_image_state(ARN_BEFORE_CLAIMS, "AVAILABLE", None)

        assert out == {"outcome": "available", "version": 7}
        v7 = store.get_build("al2023-desktop", 7)
        assert v7["status"] == store.STATUS_AVAILABLE and v7["edhStackName"] == "VAMS AL2023 Desktop v7"
        v8 = store.get_build("al2023-desktop", 8)
        assert v8["status"] == store.STATUS_BUILDING and v8["imageBuildVersionArn"] and v8["requestedBy"] == "dave"

    def test_a_request_for_its_content_is_answered_with_it(self, world):
        row = seeded()
        self._in_flight(row)

        build, outcome = recipes.start_build(row, "dave", only_if_changed=False)

        assert outcome == recipes.OUTCOME_IN_PROGRESS and build["version"] == 7
        assert build["imageBuildVersionArn"] == ARN_BEFORE_CLAIMS
        world["ctx"].ib.start_image_pipeline_execution.assert_not_called()

    def test_the_sweep_leaves_it_building_while_its_image_runs(self, world):
        row = seeded()
        self._in_flight(row, started_minutes_ago=recipes.CLAIM_STALE_MINUTES + 30)
        world["ctx"].ib.get_image.return_value = {"image": {"state": {"status": "BUILDING", "reason": None},
                                                            "outputResources": {"amis": []}}}

        summary = recipes.sweep()

        v7 = store.get_build("al2023-desktop", 7)
        assert v7["status"] == store.STATUS_BUILDING and "error" not in v7
        assert summary["checked"] == 1 and summary["failed"] == 0
        world["ctx"].ib.cancel_image_creation.assert_not_called()


@pytest.mark.unit
class TestSweepAndDelete:
    def test_the_sweep_closes_a_build_whose_event_was_missed(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        summary = recipes.sweep()
        assert summary["checked"] == 1 and summary["completed"] == 1
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE

    def test_retire_build_deactivates_and_deregisters(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        retired = recipes.retire_build(row, store.get_build("al2023-desktop", 1))
        assert retired["status"] == store.STATUS_RETIRED and retired["amiDeregisteredAt"]
        world["edh"].delete_software_stack.assert_called_once_with("dcv", "42")
        with pytest.raises(recipes.RecipeConflict):
            recipes.retire_build(row, store.get_build("al2023-desktop", 1))

    def test_delete_refuses_while_building_and_otherwise_removes_rows_and_resources(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        with pytest.raises(recipes.RecipeConflict):
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "FAILED", "x")
        warnings = recipes.delete_recipe(store.get_recipe("al2023-desktop"))
        assert warnings == [] and store.get_recipe("al2023-desktop") is None
        world["ctx"].ib.delete_image_pipeline.assert_called_once_with(imagePipelineArn="arn:p")


@pytest.mark.unit
class TestOverlappingRecipeWrites:
    """A delete marks the recipe before it reads the builds, and each writer of a new build row reads the
    recipe after writing the row, so a delete and a build that overlap never both go ahead. A recipe is
    written only where the write expects it: a create where none exists, an update over the row it read.
    Retirement and pruning claim the build status they read before they act."""

    ADMIN_BODY = {**RECIPE_BODY, "recipeId": "admin-one"}

    def _v1_failed_and_v2_queued(self):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.start_build(seeded(recipeRevision=2), "dave", only_if_changed=False)
        # v1 settled on a path that promoted nothing, so v2 waits with nothing active.
        store.update_build("al2023-desktop", 1, status=store.STATUS_FAILED)
        return row

    def _v1_available(self):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        return row, store.get_build("al2023-desktop", 1)

    def _ami_rows(self, *statuses):
        for version, status in statuses:
            store.put_build({"recipeId": "al2023-desktop", "version": version, "status": status,
                             "amiId": f"ami-{version}", "snapshotIds": [f"snap-{version}"]})

    def test_a_delete_refuses_while_a_build_is_queued_and_the_queued_build_still_starts(self, world):
        self._v1_failed_and_v2_queued()

        with pytest.raises(recipes.RecipeConflict, match="a build is in progress"):
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert store.DELETE_MARKER_FIELD not in store.get_recipe("al2023-desktop")
        world["ctx"].ib.delete_image_pipeline.assert_not_called()
        assert recipes.sweep()["promoted"] == 1
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_BUILDING

    def test_the_delete_reads_the_builds_it_checks_consistently(self, world, monkeypatch):
        seeded()
        list_builds = store.list_builds
        consistent = []

        def recording(recipe_id, **kwargs):
            consistent.append(kwargs.get("consistent", False))
            return list_builds(recipe_id, **kwargs)
        monkeypatch.setattr(store, "list_builds", recording)

        recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert consistent and all(consistent)

    def test_a_build_requested_while_the_recipe_is_deleted_starts_nothing_and_leaves_no_row(self, world, monkeypatch):
        row = seeded()
        delete_resources = recipes.builder.delete_recipe_resources
        answers = []

        def a_request_arrives_mid_delete(ctx, image_builder):
            with pytest.raises(recipes.RecipeConflict) as refused:
                recipes.start_build(row, "dave", only_if_changed=False)
            answers.append(str(refused.value))
            return delete_resources(ctx, image_builder)
        monkeypatch.setattr(recipes.builder, "delete_recipe_resources", a_request_arrives_mid_delete)

        recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert answers == [recipes.RECIPE_DELETING]
        world["ctx"].ib.start_image_pipeline_execution.assert_not_called()
        assert store._table().scan()["Items"] == []

    def test_a_queued_build_promoted_while_the_recipe_is_deleted_goes_back_to_the_queue(self, world, monkeypatch):
        self._v1_failed_and_v2_queued()
        ib = world["ctx"].ib
        started_before = ib.start_image_pipeline_execution.call_count
        mark = store.mark_recipe_deleting
        sweeps = []

        def the_sweep_runs_once_the_recipe_is_marked(recipe_id, **kwargs):
            marked = mark(recipe_id, **kwargs)
            sweeps.append(recipes.sweep())
            return marked
        monkeypatch.setattr(store, "mark_recipe_deleting", the_sweep_runs_once_the_recipe_is_marked)

        with pytest.raises(recipes.RecipeConflict, match="a build is in progress"):
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert sweeps and sweeps[0]["promoted"] == 0 and sweeps[0]["warnings"]
        assert ib.start_image_pipeline_execution.call_count <= started_before
        v2 = store.get_build("al2023-desktop", 2)
        assert v2["status"] == store.STATUS_QUEUED and recipes.RECIPE_DELETING in v2["error"]
        assert store.DELETE_MARKER_FIELD not in store.get_recipe("al2023-desktop")
        monkeypatch.setattr(store, "mark_recipe_deleting", mark)
        assert recipes.sweep()["promoted"] == 1

    def test_a_build_queued_while_the_recipe_is_deleted_is_withdrawn(self, world, monkeypatch):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        store.update_build("al2023-desktop", 1, status=store.STATUS_FAILED)
        changed = seeded(recipeRevision=2)
        # The request read its listing while v1 was still building.
        building = [{**store.get_build("al2023-desktop", 1), "status": store.STATUS_BUILDING}]
        delete_resources = recipes.builder.delete_recipe_resources
        seen = {}

        def a_queue_request_arrives_mid_delete(ctx, image_builder):
            with monkeypatch.context() as m:
                m.setattr(store, "list_builds", lambda recipe_id, **kwargs: building)
                with pytest.raises(recipes.RecipeConflict):
                    recipes.start_build(changed, "dave", only_if_changed=False)
            seen["v2"] = store.get_build("al2023-desktop", 2)
            return delete_resources(ctx, image_builder)
        monkeypatch.setattr(recipes.builder, "delete_recipe_resources", a_queue_request_arrives_mid_delete)

        recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert "v2" in seen and seen["v2"] is None
        assert store._table().scan()["Items"] == []

    def test_a_launch_whose_recipe_row_is_removed_leaves_no_recipe_row_behind(self, world):
        row = seeded()
        ib = world["ctx"].ib
        start = ib.start_image_pipeline_execution.side_effect

        def start_once_the_rows_are_gone(**kwargs):
            store.delete_recipe_rows("al2023-desktop")
            return start(**kwargs)
        ib.start_image_pipeline_execution.side_effect = start_once_the_rows_are_gone

        recipes.start_build(row, "carol", only_if_changed=False)

        assert "Item" not in store._table().get_item(Key=store.recipe_key("al2023-desktop"))
        assert store.list_recipes() == []
        assert recipes.sweep()["warnings"] == []

    def test_a_queued_build_of_a_recipe_that_is_gone_is_not_started(self, world):
        row = self._v1_failed_and_v2_queued()
        ib = world["ctx"].ib
        started_before = ib.start_image_pipeline_execution.call_count
        store._table().delete_item(Key=store.recipe_key("al2023-desktop"))

        with pytest.raises(recipes.RecipeConflict):
            recipes._promote_queued(row)

        assert ib.start_image_pipeline_execution.call_count <= started_before
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_QUEUED
        assert "Item" not in store._table().get_item(Key=store.recipe_key("al2023-desktop"))

    def test_a_recipe_row_without_a_recipe_id_stops_neither_the_sweep_nor_a_completion(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        orphan_arn = "arn:aws:imagebuilder:us-east-1:123456789012:image/orphan/1.0.0/1"
        table = store._table()
        table.put_item(Item={**store.recipe_key("orphan"), "latestVersion": 1, "imageBuilder": {"pipelineArn": "arn:o"}})
        table.put_item(Item={**store.build_key("orphan", 1), "recipeId": "orphan", "version": 1,
                             "status": store.STATUS_BUILDING, "imageBuildVersionArn": orphan_arn})

        summary = recipes.sweep()

        assert summary["completed"] == 1 and summary["warnings"] == []
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE
        assert recipes.handle_image_state(orphan_arn, "AVAILABLE", None) == {"outcome": "unknown-recipe"}

    def test_a_delete_under_way_holds_off_a_second_delete_and_any_build(self, world):
        seeded()
        store.update_recipe("al2023-desktop", **{store.DELETE_MARKER_FIELD: _minutes_ago(1)})

        with pytest.raises(recipes.RecipeConflict, match=recipes.RECIPE_DELETING):
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))
        with pytest.raises(recipes.RecipeConflict, match=recipes.RECIPE_DELETING):
            recipes.start_build(store.get_recipe("al2023-desktop"), "carol", only_if_changed=False)

        assert store.get_recipe("al2023-desktop") and store.list_builds("al2023-desktop") == []
        world["ctx"].ib.start_image_pipeline_execution.assert_not_called()

    def test_a_marker_older_than_any_delete_can_run_holds_off_nothing(self, world):
        seeded()
        store.update_recipe("al2023-desktop",
                            **{store.DELETE_MARKER_FIELD: _minutes_ago(recipes.DELETE_STALE_MINUTES + 5)})

        build, outcome = recipes.start_build(store.get_recipe("al2023-desktop"), "carol", only_if_changed=False)
        assert outcome == recipes.OUTCOME_STARTED
        recipes.handle_image_state(build["imageBuildVersionArn"], "FAILED", "x")

        recipes.delete_recipe(store.get_recipe("al2023-desktop"))
        assert store._table().scan()["Items"] == []

    def test_a_delete_that_fails_releases_its_marker(self, world, monkeypatch):
        seeded()

        def refused(ctx, image_builder):
            raise FORBIDDEN("not authorized")
        monkeypatch.setattr(recipes.builder, "delete_recipe_resources", refused)

        with pytest.raises(FORBIDDEN):
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert store.DELETE_MARKER_FIELD not in store.get_recipe("al2023-desktop")
        _build, outcome = recipes.start_build(store.get_recipe("al2023-desktop"), "carol", only_if_changed=False)
        assert outcome == recipes.OUTCOME_STARTED

    def test_of_two_creates_of_one_recipe_id_the_later_is_refused(self, world, monkeypatch):
        validate = recipes._require_materializable
        first = {}

        def another_create_lands_first(item):
            if "row" not in first:
                first["row"] = None
                first["row"] = recipes.create_admin_recipe(
                    AmiRecipeRequestModel(**{**self.ADMIN_BODY, "displayName": "first"}), "dave")
            return validate(item)
        monkeypatch.setattr(recipes, "_require_materializable", another_create_lands_first)

        with pytest.raises(recipes.RecipeConflict, match="recipeId is already in use"):
            recipes.create_admin_recipe(AmiRecipeRequestModel(**self.ADMIN_BODY), "carol")

        stored = store.get_recipe("admin-one")
        assert stored["createdBy"] == "dave" and stored["displayName"] == "first"

    def test_an_admin_update_keeps_the_pipeline_and_version_a_build_recorded_since_it_read(self, world):
        recipes.create_admin_recipe(AmiRecipeRequestModel(**self.ADMIN_BODY), "carol")
        read = store.get_recipe("admin-one")
        recipes.start_build(read, "carol", only_if_changed=False)

        updated = recipes.update_admin_recipe(
            read, AmiRecipeRequestModel(**{**self.ADMIN_BODY, "displayName": "renamed"}), "dave")

        stored = store.get_recipe("admin-one")
        assert stored["displayName"] == "renamed" and stored["updatedBy"] == "dave"
        assert stored["latestVersion"] == 1 and stored["imageBuilder"]["pipelineArn"] == "arn:p"
        assert updated["latestVersion"] == stored["latestVersion"]

    def test_a_config_upsert_keeps_the_pipeline_and_version_a_build_recorded_since_it_read(self, world, monkeypatch):
        seeded()
        put = store.put_recipe
        launches = []

        def a_launch_records_first(item, **kwargs):
            if not launches:
                launches.append(store.update_recipe("al2023-desktop", latestVersion=5,
                                                    imageBuilder={"pipelineArn": "arn:p5"}))
            return put(item, **kwargs)
        monkeypatch.setattr(store, "put_recipe", a_launch_records_first)

        row, outcome = recipes.upsert_config_recipe({**RECIPE_BODY, "recipeRevision": 2}, build_on_deploy=False)

        stored = store.get_recipe("al2023-desktop")
        assert outcome == "changed" and row["recipeRevision"] == 2 and stored["recipeRevision"] == 2
        assert stored["latestVersion"] == 5 and stored["imageBuilder"]["pipelineArn"] == "arn:p5"

    def test_an_update_while_a_delete_runs_keeps_its_marker(self, world):
        recipes.create_admin_recipe(AmiRecipeRequestModel(**self.ADMIN_BODY), "carol")
        store.update_recipe("admin-one", **{store.DELETE_MARKER_FIELD: _minutes_ago(1)})

        recipes.update_admin_recipe(store.get_recipe("admin-one"),
                                    AmiRecipeRequestModel(**{**self.ADMIN_BODY, "displayName": "renamed"}), "dave")

        stored = store.get_recipe("admin-one")
        assert stored["displayName"] == "renamed" and stored[store.DELETE_MARKER_FIELD]
        with pytest.raises(recipes.RecipeConflict, match=recipes.RECIPE_DELETING):
            recipes.start_build(stored, "carol", only_if_changed=False)

    def test_an_update_that_follows_a_delete_does_not_bring_the_recipe_back(self, world):
        recipes.create_admin_recipe(AmiRecipeRequestModel(**self.ADMIN_BODY), "carol")
        read = store.get_recipe("admin-one")
        recipes.delete_recipe(read)

        with pytest.raises(recipes.RecipeConflict):
            recipes.update_admin_recipe(
                read, AmiRecipeRequestModel(**{**self.ADMIN_BODY, "displayName": "renamed"}), "dave")

        assert store._table().scan()["Items"] == []

    def test_a_retire_of_a_build_a_registration_retry_holds_changes_nothing(self, world):
        row, _v1 = self._v1_available()
        read = store.update_build("al2023-desktop", 1, status=store.STATUS_REGISTER_FAILED)
        # A registration retry claims the build after the retire read it.
        store.claim_build("al2023-desktop", 1, store.STATUS_REGISTER_FAILED, status=store.STATUS_REGISTERING)

        with pytest.raises(recipes.RecipeConflict, match="the build status changed"):
            recipes.retire_build(row, read)

        world["edh"].delete_software_stack.assert_not_called()
        world["ctx"].ec2.deregister_image.assert_not_called()
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_REGISTERING

    def test_a_retire_whose_deactivation_fails_hands_the_build_back(self, world):
        row, v1 = self._v1_available()
        world["edh"].delete_software_stack.side_effect = EdhError("down")

        with pytest.raises(EdhError):
            recipes.retire_build(row, v1)

        kept = store.get_build("al2023-desktop", 1)
        assert kept["status"] == store.STATUS_AVAILABLE and "retiredAt" not in kept
        world["ctx"].ec2.deregister_image.assert_not_called()
        world["edh"].delete_software_stack.side_effect = None
        assert recipes.retire_build(row, kept)["status"] == store.STATUS_RETIRED

    def test_pruning_skips_a_build_a_registration_retry_holds(self, world, monkeypatch):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_REGISTER_FAILED))
        listed = store.list_builds("al2023-desktop")
        # A registration retry claims v1 after pruning listed it.
        store.claim_build("al2023-desktop", 1, store.STATUS_REGISTER_FAILED, status=store.STATUS_REGISTERING)
        monkeypatch.setattr(store, "list_builds", lambda recipe_id, **kwargs: listed)

        pruned = recipes.prune_amis(row)

        assert 1 not in pruned
        world["ctx"].ec2.deregister_image.assert_not_called()
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTERING and "amiDeregisteredAt" not in v1

    def test_a_prune_that_fails_hands_the_build_back_with_a_warning(self, world):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_REGISTER_FAILED))
        world["ctx"].ec2.deregister_image.side_effect = Exception("EC2 throttled")

        pruned = recipes.prune_amis(row)

        assert 1 not in pruned
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_REGISTER_FAILED and "Exception" in v1["pruneWarning"]
        assert "amiDeregisteredAt" not in v1


@pytest.mark.unit
class TestRegistrationIdempotency:
    """Registration is safe to repeat: an existing stack is adopted, a retirement or pruning failure
    leaves the registered build AVAILABLE, and automatic retries stop at the cap."""

    def _available(self, world, **overrides):
        row = seeded(**overrides)
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build(row["recipeId"])["imageBuildVersionArn"], "AVAILABLE", None)
        return row

    def test_a_prune_failure_leaves_the_build_available_and_the_sweep_adds_no_second_stack(self, world):
        row = self._available(world, keepVersions=1)
        world["ctx"].ec2.deregister_image.side_effect = Exception("EC2 throttled")
        recipes.start_build(row, "carol", only_if_changed=False)
        out = recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        assert out["outcome"] == "available"
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_AVAILABLE
        assert "Exception" in store.get_build("al2023-desktop", 1)["pruneWarning"]
        recipes.sweep()
        assert store.get_build("al2023-desktop", 2)["status"] == store.STATUS_AVAILABLE
        assert [s["stack_name"] for s in world["edh"].registered].count("VAMS AL2023 Desktop v2") == 1

    def test_a_retirement_failure_keeps_the_new_build_available_with_a_warning(self, world):
        row = self._available(world)
        world["edh"].delete_software_stack.side_effect = Exception("connection reset")
        recipes.start_build(row, "carol", only_if_changed=False)
        out = recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v2 = store.get_build("al2023-desktop", 2)
        assert out["outcome"] == "available" and v2["status"] == store.STATUS_AVAILABLE
        assert v2["error"] == "registered; retirement or pruning of earlier versions failed (Exception)"

    def test_a_tag_failure_is_retried_and_the_retry_tags_and_registers_once(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        world["ctx"].ec2.create_tags.side_effect = Exception("EC2 API error")
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        failed = store.get_build("al2023-desktop", 1)
        assert failed["status"] == store.STATUS_REGISTER_FAILED and failed["registerAttempts"] == 1
        world["ctx"].ec2.create_tags.side_effect = None
        world["ctx"].ec2.create_tags.reset_mock()
        registered = recipes.retry_registration(row, failed)
        assert registered["status"] == store.STATUS_AVAILABLE and registered["registerAttempts"] == 0
        world["ctx"].ec2.create_tags.assert_called_once()
        world["edh"].register_software_stack.assert_called_once()
        assert [s["stack_name"] for s in world["edh"].registered] == ["VAMS AL2023 Desktop v1"]

    def test_the_sweep_stops_at_the_retry_cap_and_the_admin_retry_still_runs(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recording = world["edh"].register_software_stack.side_effect
        world["edh"].register_software_stack.side_effect = EdhError("down")
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        assert store.get_build("al2023-desktop", 1)["registerAttempts"] == 1
        store.update_build("al2023-desktop", 1, registerAttempts=recipes.REGISTER_RETRY_LIMIT)
        world["edh"].register_software_stack.side_effect = recording
        world["edh"].register_software_stack.reset_mock()
        reason = store.get_build("al2023-desktop", 1)["error"]
        summary = recipes.sweep()
        capped = store.get_build("al2023-desktop", 1)
        assert summary["retried"] == 0 and capped["status"] == store.STATUS_REGISTER_FAILED
        assert capped["error"].endswith("registration retries exhausted; use Retry registration")
        assert reason and capped["error"].startswith(reason)
        recipes.sweep()
        assert store.get_build("al2023-desktop", 1)["error"] == capped["error"]
        world["edh"].register_software_stack.assert_not_called()
        resumed = recipes.retry_registration(row, capped)
        assert resumed["status"] == store.STATUS_AVAILABLE and resumed["registerAttempts"] == 0
        world["edh"].register_software_stack.assert_called_once()

    def test_a_build_that_already_carries_a_stack_id_is_not_registered_again(self, world):
        row = seeded()
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_REGISTERING,
                         "amiId": "ami-0abc0abc0abc0abc0", "edhStackId": "77",
                         "edhStackName": "VAMS AL2023 Desktop v1"})
        result = recipes.register_build(row, store.get_build("al2023-desktop", 1))
        assert result["status"] == store.STATUS_AVAILABLE and result["edhStackId"] == "77"
        world["edh"].register_software_stack.assert_not_called()

    def test_an_existing_stack_of_the_same_name_is_adopted_instead_of_registered(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        world["edh"].registered.append({"id": 99, "stack_name": "VAMS AL2023 Desktop v1",
                                        "ami_id": "ami-0abc0abc0abc0abc0"})
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        done = store.get_build("al2023-desktop", 1)
        assert done["status"] == store.STATUS_AVAILABLE and done["edhStackId"] == "99"
        world["edh"].register_software_stack.assert_not_called()
        assert len(world["edh"].registered) == 1


@pytest.mark.unit
class TestProjectAssignment:
    """A registered stack is launchable only through the project that carries it, so registration
    attaches it to the launch project; a stack EDH holds that the project does not carry is a failed
    registration the retry repeats."""

    def _available(self, world, **overrides):
        row = seeded(**overrides)
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build(row["recipeId"])["imageBuildVersionArn"], "AVAILABLE", None)
        return row, store.get_build(row["recipeId"], 1)

    def test_a_vdi_stack_is_attached_to_the_default_project(self, world):
        _row, done = self._available(world)
        projects = world["edh"].projects
        assert done["status"] == store.STATUS_AVAILABLE and done["edhStackId"] == "42"
        assert projects.stack_ids("dcv") == ["42"] and projects.stack_ids("target_nodes") == []
        # The update is a full replace: the project's own fields come back with it.
        assert len(projects.puts) == 1
        assert projects.puts[0]["project_id"] == "1"
        assert projects.puts[0]["allowed_users"] == "alice,bob"

    def test_a_simulation_stack_lands_in_the_target_node_membership(self, world):
        sim = {**RECIPE_BODY, "recipeId": "sim", "kind": "simulation", "stackName": "Sim"}
        sim.pop("baseOs")
        _row, done = self._available(world, **sim)
        projects = world["edh"].projects
        assert done["status"] == store.STATUS_AVAILABLE
        assert projects.stack_ids("target_nodes") == ["42"] and projects.stack_ids("dcv") == []

    def test_a_stack_the_project_already_carries_sends_no_update(self, world):
        for project in world["edh"].projects.rows.values():
            project["software_stack_ids"] = [42]
        _row, done = self._available(world)
        assert done["status"] == store.STATUS_AVAILABLE and done["edhStackId"] == "42"
        assert world["edh"].projects.puts == []

    def test_an_adopted_stack_is_attached_as_well(self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        world["edh"].registered.append({"id": 99, "stack_name": "VAMS AL2023 Desktop v1",
                                        "ami_id": "ami-0abc0abc0abc0abc0"})
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        assert store.get_build("al2023-desktop", 1)["status"] == store.STATUS_AVAILABLE
        world["edh"].register_software_stack.assert_not_called()
        assert world["edh"].projects.stack_ids("dcv") == ["99"]

    def test_an_assignment_failure_is_register_failed_and_keeps_no_stack_id(self, world):
        world["edh"].assign_software_stack_to_project.side_effect = EdhError("projects down")
        _row, done = self._available(world)
        assert done["status"] == store.STATUS_REGISTER_FAILED
        assert done["error"].startswith(
            f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' (EdhError")
        # The retry re-runs registration, which only happens while no stack id is recorded.
        assert done["edhStackName"] == "VAMS AL2023 Desktop v1" and not done.get("edhStackId")
        assert "projects down" not in done["error"]
        assert done["registerAttempts"] == 1

    def test_a_missing_project_is_register_failed_and_the_retry_heals_it(self, world):
        for project in world["edh"].projects.rows.values():
            project["project_name"] = "another-project"
        row, done = self._available(world)
        assert done["status"] == store.STATUS_REGISTER_FAILED
        assert done["error"] == (
            f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' (EdhNotFoundError)")
        for project in world["edh"].projects.rows.values():
            project["project_name"] = DEFAULT_PROJECT
        healed = recipes.retry_registration(row, done)
        assert healed["status"] == store.STATUS_AVAILABLE and healed["edhStackId"] == "42"
        assert world["edh"].projects.stack_ids("dcv") == ["42"]
        # The stack EDH already holds was adopted rather than registered a second time.
        world["edh"].register_software_stack.assert_called_once()


@pytest.mark.unit
class TestAmisInUse:
    """An AMI an Amazon EC2 instance uses is never deregistered. Pruning and an administrator's retire keep
    it and name the instances, or the failed check, on the build row; the sweep removes it once no
    instance uses it."""

    def _ami_rows(self, *statuses, recipe_id="al2023-desktop"):
        for version, status in statuses:
            store.put_build({"recipeId": recipe_id, "version": version, "status": status,
                             "amiId": f"ami-{recipe_id}-{version}", "snapshotIds": [f"snap-{version}"],
                             "imageBuildVersionArn": f"{ARN_BEFORE_CLAIMS[:-1]}{recipe_id}-{version}"})

    def _uses(self, world, *instances):
        world["ctx"].ec2.instances = [{"InstanceId": i, "ImageId": ami} for i, ami in instances]

    def _v1_available(self):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        return row, store.get_build("al2023-desktop", 1)

    def _build_writes(self, monkeypatch):
        """Every build row write from here on, as (function, recipeId, version)."""
        writes = []
        for name in ("update_build", "claim_build"):
            def recording(recipe_id, version, *args, _write=getattr(store, name), _name=name, **kwargs):
                writes.append((_name, recipe_id, int(version)))
                return _write(recipe_id, version, *args, **kwargs)
            monkeypatch.setattr(store, name, recording)
        return writes

    @pytest.mark.parametrize("status", [store.STATUS_RETIRED, store.STATUS_REGISTER_FAILED, store.STATUS_FAILED])
    def test_pruning_keeps_an_ami_an_instance_uses_and_names_the_instances(self, world, status):
        row = seeded(keepVersions=1)
        self._ami_rows((3, store.STATUS_AVAILABLE), (2, store.STATUS_RETIRED), (1, status))
        self._uses(world, ("i-0b", "ami-al2023-desktop-1"), ("i-0a", "ami-al2023-desktop-1"),
                   ("i-0c", "ami-elsewhere"))
        newest = store.get_build("al2023-desktop", 3)

        pruned = recipes.prune_amis(row)

        ec2 = world["ctx"].ec2
        assert pruned == [2]
        ec2.deregister_image.assert_called_once_with(ImageId="ami-al2023-desktop-2")
        ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-2")
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == status and v1["amiInUseBy"] == ["i-0a", "i-0b"]
        assert "amiDeregisteredAt" not in v1 and "retiredAt" not in v1 and "pruneWarning" not in v1
        assert store.get_build("al2023-desktop", 2)["amiDeregisteredAt"]
        assert store.get_build("al2023-desktop", 3) == newest
        # Only the AMIs of builds beyond the window are looked up.
        assert [f[0] for f in ec2.instance_filters] == [{"Name": "image-id", "Values": ["ami-al2023-desktop-2"]},
                                                        {"Name": "image-id", "Values": ["ami-al2023-desktop-1"]}]

    def test_at_most_twenty_instances_are_named_in_id_order(self, world):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        self._uses(world, *((f"i-{n:02d}", "ami-al2023-desktop-1") for n in range(25, 0, -1)))

        recipes.prune_amis(row)

        assert store.get_build("al2023-desktop", 1)["amiInUseBy"] == [f"i-{n:02d}" for n in range(1, 21)]

    @pytest.mark.parametrize("error, warning", [
        (UNAUTHORIZED, "in-use check failed (UnauthorizedOperation)"),
        (EndpointConnectionError(endpoint_url="https://ec2.example"), "in-use check failed (EndpointConnectionError)"),
    ])
    def test_an_in_use_check_that_fails_keeps_the_ami_and_says_why(self, world, error, warning):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        world["ctx"].ec2.describe_instances_error = error

        assert recipes.prune_amis(row) == []

        world["ctx"].ec2.deregister_image.assert_not_called()
        v1 = store.get_build("al2023-desktop", 1)
        assert v1["status"] == store.STATUS_RETIRED and v1["pruneWarning"] == warning
        assert "amiDeregisteredAt" not in v1 and "amiInUseBy" not in v1

    @pytest.mark.parametrize("cause", ["in use", "check failed"])
    def test_a_pass_that_finds_the_same_reason_writes_nothing(self, world, monkeypatch, cause):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        if cause == "in use":
            self._uses(world, ("i-1", "ami-al2023-desktop-1"))
        else:
            world["ctx"].ec2.describe_instances_error = UNAUTHORIZED
        recipes.prune_amis(row)
        kept = store.get_build("al2023-desktop", 1)
        writes = self._build_writes(monkeypatch)

        recipes.prune_amis(row)
        summary = recipes.sweep()

        assert writes == [] and summary["pruned"] == 0
        assert store.get_build("al2023-desktop", 1) == kept

    def test_a_changed_reason_is_written_once(self, world):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        world["ctx"].ec2.describe_instances_error = UNAUTHORIZED
        recipes.prune_amis(row)
        world["ctx"].ec2.describe_instances_error = None
        self._uses(world, ("i-1", "ami-al2023-desktop-1"))

        recipes.prune_amis(row)

        v1 = store.get_build("al2023-desktop", 1)
        assert v1["amiInUseBy"] == ["i-1"] and "pruneWarning" not in v1
        self._uses(world, ("i-2", "ami-al2023-desktop-1"), ("i-1", "ami-al2023-desktop-1"))
        recipes.prune_amis(row)
        assert store.get_build("al2023-desktop", 1)["amiInUseBy"] == ["i-1", "i-2"]
        world["ctx"].ec2.deregister_image.assert_not_called()

    def test_the_sweep_removes_a_kept_ami_once_no_instance_uses_it(self, world):
        seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        ec2 = world["ctx"].ec2
        self._uses(world, ("i-1", "ami-al2023-desktop-1"))
        summary = recipes.sweep()
        assert summary["pruned"] == 0 and store.get_build("al2023-desktop", 1)["amiInUseBy"] == ["i-1"]
        ec2.deregister_image.assert_not_called()

        # The desktop is terminated; the first deregistration is refused and the next sweep tries again.
        self._uses(world)
        ec2.deregister_image.side_effect = THROTTLED
        summary = recipes.sweep()
        refused = store.get_build("al2023-desktop", 1)
        assert summary["pruned"] == 0 and summary["warnings"] == []
        assert refused["status"] == store.STATUS_RETIRED and refused["pruneWarning"] == "pruning failed (ClientError)"
        assert "amiDeregisteredAt" not in refused and "amiInUseBy" not in refused

        ec2.deregister_image.side_effect = None
        summary = recipes.sweep()

        done = store.get_build("al2023-desktop", 1)
        assert summary["pruned"] == 1 and summary["warnings"] == []
        assert done["status"] == store.STATUS_RETIRED and done["amiDeregisteredAt"] and done["retiredAt"]
        assert "amiInUseBy" not in done and "pruneWarning" not in done and "amiRemovalPending" not in done
        ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-1")
        world["ctx"].ib.delete_image.assert_called_once_with(imageBuildVersionArn=done["imageBuildVersionArn"])
        assert recipes.sweep()["pruned"] == 0

    def test_a_retire_of_a_build_whose_ami_a_desktop_uses_keeps_the_ami(self, world):
        row, v1 = self._v1_available()
        self._uses(world, ("i-desk", v1["amiId"]))

        retired = recipes.retire_build(row, v1)

        world["edh"].delete_software_stack.assert_called_once_with("dcv", "42")
        world["ctx"].ec2.deregister_image.assert_not_called()
        world["ctx"].ec2.delete_snapshot.assert_not_called()
        world["ctx"].ib.delete_image.assert_not_called()
        assert retired["status"] == store.STATUS_RETIRED and retired["retiredAt"]
        assert retired["amiRemovalPending"] is True and retired["amiInUseBy"] == ["i-desk"]
        assert "amiDeregisteredAt" not in retired
        assert store.get_build("al2023-desktop", 1) == retired

    def test_a_retire_whose_in_use_check_fails_keeps_the_ami(self, world):
        row, v1 = self._v1_available()
        world["ctx"].ec2.describe_instances_error = UNAUTHORIZED

        retired = recipes.retire_build(row, v1)

        world["ctx"].ec2.deregister_image.assert_not_called()
        assert retired["status"] == store.STATUS_RETIRED and retired["retiredAt"]
        assert retired["amiRemovalPending"] is True
        assert retired["pruneWarning"] == "in-use check failed (UnauthorizedOperation)"
        assert "amiDeregisteredAt" not in retired

    def test_the_sweep_removes_a_retired_builds_ami_once_unused_inside_the_window(self, world):
        row, v1 = self._v1_available()
        self._uses(world, ("i-desk", v1["amiId"]))
        retired = recipes.retire_build(row, v1)
        # keepVersions is 2, so v1 is a candidate only through its pending removal.
        assert recipes.sweep()["pruned"] == 0
        self._uses(world)

        summary = recipes.sweep()

        done = store.get_build("al2023-desktop", 1)
        assert summary["pruned"] == 1
        assert done["status"] == store.STATUS_RETIRED and done["amiDeregisteredAt"]
        assert done["retiredAt"] == retired["retiredAt"]
        assert "amiRemovalPending" not in done and "amiInUseBy" not in done
        world["ctx"].ec2.deregister_image.assert_called_once_with(ImageId=v1["amiId"])
        world["ctx"].ib.delete_image.assert_called_once_with(imageBuildVersionArn=v1["imageBuildVersionArn"])
        world["edh"].delete_software_stack.assert_called_once()

    def test_a_retire_of_a_build_no_instance_uses_deregisters_its_ami_at_once(self, world):
        row, v1 = self._v1_available()
        self._uses(world, ("i-other", "ami-elsewhere"))

        retired = recipes.retire_build(row, v1)

        assert retired["status"] == store.STATUS_RETIRED and retired["retiredAt"] and retired["amiDeregisteredAt"]
        assert "amiRemovalPending" not in retired and "amiInUseBy" not in retired
        assert store.get_build("al2023-desktop", 1) == retired
        world["ctx"].ec2.deregister_image.assert_called_once_with(ImageId=v1["amiId"])
        world["ctx"].ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-1")
        world["ctx"].ib.delete_image.assert_called_once_with(imageBuildVersionArn=v1["imageBuildVersionArn"])
        assert world["ctx"].ec2.instance_filters[-1][0] == {"Name": "image-id", "Values": [v1["amiId"]]}

    def test_a_retire_whose_deregistration_fails_raises_and_the_sweep_finishes_it(self, world):
        row, v1 = self._v1_available()
        world["ctx"].ec2.deregister_image.side_effect = THROTTLED

        with pytest.raises(ClientError):
            recipes.retire_build(row, v1)

        pending = store.get_build("al2023-desktop", 1)
        world["edh"].delete_software_stack.assert_called_once_with("dcv", "42")
        assert pending["status"] == store.STATUS_RETIRED and pending["retiredAt"]
        assert pending["amiRemovalPending"] is True and "amiDeregisteredAt" not in pending
        world["ctx"].ec2.deregister_image.side_effect = None

        summary = recipes.sweep()

        done = store.get_build("al2023-desktop", 1)
        assert summary["pruned"] == 1 and done["amiDeregisteredAt"] and "amiRemovalPending" not in done
        assert done["retiredAt"] == pending["retiredAt"]
        world["edh"].delete_software_stack.assert_called_once()

    def test_the_sweep_prunes_no_recipe_being_deleted(self, world):
        seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        store.update_recipe("al2023-desktop", **{store.DELETE_MARKER_FIELD: _minutes_ago(1)})

        summary = recipes.sweep()

        assert summary["pruned"] == 0 and summary["warnings"] == []
        assert world["ctx"].ec2.instance_filters == []
        world["ctx"].ec2.deregister_image.assert_not_called()
        # A marker older than any delete can run belongs to one that did not finish.
        store.update_recipe("al2023-desktop",
                            **{store.DELETE_MARKER_FIELD: _minutes_ago(recipes.DELETE_STALE_MINUTES + 5)})
        assert recipes.sweep()["pruned"] == 1

    def test_a_recipe_whose_pruning_fails_is_a_sweep_warning_and_the_next_recipe_is_pruned(self, world, monkeypatch):
        seeded(**{**RECIPE_BODY, "recipeId": "a-first", "keepVersions": 1})
        seeded(keepVersions=1)
        for recipe_id in ("a-first", "al2023-desktop"):
            self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED), recipe_id=recipe_id)
        claim = store.claim_build

        def throttled_for_the_first(recipe_id, *args, **kwargs):
            if recipe_id == "a-first":
                raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
                                  "UpdateItem")
            return claim(recipe_id, *args, **kwargs)
        monkeypatch.setattr(store, "claim_build", throttled_for_the_first)

        summary = recipes.sweep()

        assert summary["warnings"] == ["a-first: prune ClientError"] and summary["pruned"] == 1
        assert "amiDeregisteredAt" not in store.get_build("a-first", 1)
        assert store.get_build("al2023-desktop", 1)["amiDeregisteredAt"]

    @staticmethod
    def _recipe_deleted():
        """Remove the recipe's rows, as a recipe delete running at the same time does."""
        store.delete_recipe_rows("al2023-desktop")

    @pytest.mark.parametrize("cause", ["in use", "check failed"])
    def test_pruning_does_not_write_back_a_build_row_deleted_during_the_in_use_check(self, world, monkeypatch, cause):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))

        def the_recipe_is_deleted_meanwhile(ctx, ami_id):
            self._recipe_deleted()
            if cause == "check failed":
                raise UNAUTHORIZED
            return ["i-1"]
        monkeypatch.setattr(recipes.builder, "instances_using_ami", the_recipe_is_deleted_meanwhile)

        assert recipes.prune_amis(row) == []

        assert store._table().scan()["Items"] == []
        world["ctx"].ec2.deregister_image.assert_not_called()

    def test_pruning_does_not_write_back_a_build_row_deleted_during_the_deregistration(self, world):
        row = seeded(keepVersions=1)
        self._ami_rows((2, store.STATUS_AVAILABLE), (1, store.STATUS_RETIRED))
        world["ctx"].ec2.deregister_image.side_effect = lambda **kwargs: self._recipe_deleted()

        # The AMI is gone, so it counts as pruned.
        assert recipes.prune_amis(row) == [1]

        assert store._table().scan()["Items"] == []
        world["ctx"].ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-1")

    @pytest.mark.parametrize("moment", ["stack deactivation", "in-use check", "deregistration"])
    def test_a_retire_whose_build_row_a_recipe_delete_removes_answers_as_a_concurrent_delete(
            self, world, monkeypatch, moment):
        row, v1 = self._v1_available()
        if moment == "stack deactivation":
            world["edh"].delete_software_stack.side_effect = lambda *args, **kwargs: self._recipe_deleted()
        elif moment == "in-use check":
            def the_recipe_is_deleted_meanwhile(ctx, ami_id):
                self._recipe_deleted()
                return ["i-desk"]
            monkeypatch.setattr(recipes.builder, "instances_using_ami", the_recipe_is_deleted_meanwhile)
        else:
            world["ctx"].ec2.deregister_image.side_effect = lambda **kwargs: self._recipe_deleted()

        with pytest.raises(recipes.RecipeConflict, match=recipes.RECIPE_DELETING):
            recipes.retire_build(row, v1)

        assert store._table().scan()["Items"] == []
        assert world["ctx"].ec2.deregister_image.call_count == (1 if moment == "deregistration" else 0)

    def test_a_retire_of_a_build_without_an_ami_whose_row_is_removed_answers_as_a_concurrent_delete(self, world):
        row = seeded()
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_AVAILABLE,
                         "edhStackId": "42"})
        world["edh"].delete_software_stack.side_effect = lambda *args, **kwargs: self._recipe_deleted()

        with pytest.raises(recipes.RecipeConflict, match=recipes.RECIPE_DELETING):
            recipes.retire_build(row, store.get_build("al2023-desktop", 1))

        assert store._table().scan()["Items"] == []

    def _stack_named_only(self, world, **attrs):
        """A REGISTER_FAILED build whose project assignment failed: EDH lists its stack name, the row has no id."""
        store.put_build({"recipeId": "al2023-desktop", "version": 1, "status": store.STATUS_REGISTER_FAILED,
                         "amiId": "ami-al2023-desktop-1", "snapshotIds": ["snap-1"],
                         "edhStackName": "VAMS AL2023 Desktop v1", **attrs})
        world["edh"].registered.append({"id": 99, "stack_name": "VAMS AL2023 Desktop v1",
                                        "ami_id": "ami-al2023-desktop-1"})
        return store.get_build("al2023-desktop", 1)

    def test_a_retire_records_the_stack_id_it_looks_up_by_name(self, world):
        row = seeded()
        build = self._stack_named_only(world)

        retired = recipes.retire_build(row, build)

        world["edh"].delete_software_stack.assert_called_once_with("dcv", "99")
        assert retired["status"] == store.STATUS_RETIRED and retired["edhStackId"] == "99"
        assert retired["amiDeregisteredAt"]

    def test_a_retire_does_not_write_back_a_build_row_deleted_during_the_stack_id_lookup(self, world):
        row = seeded()
        build = self._stack_named_only(world)
        listing = world["edh"].list_software_stacks.side_effect

        def the_recipe_is_deleted_meanwhile(kind, **kwargs):
            self._recipe_deleted()
            return listing(kind, **kwargs)
        world["edh"].list_software_stacks.side_effect = the_recipe_is_deleted_meanwhile

        with pytest.raises(recipes.RecipeConflict, match="the build status changed"):
            recipes.retire_build(row, build)

        assert store._table().scan()["Items"] == []
        world["edh"].delete_software_stack.assert_not_called()

    def test_a_retire_whose_deregistration_fails_names_no_instance_the_check_no_longer_found(self, world):
        row = seeded()
        # Pruning kept the AMI for i-1, which has since been terminated.
        build = self._stack_named_only(world, amiInUseBy=["i-1"])
        world["ctx"].ec2.deregister_image.side_effect = THROTTLED

        with pytest.raises(ClientError):
            recipes.retire_build(row, build)

        pending = store.get_build("al2023-desktop", 1)
        assert pending["status"] == store.STATUS_RETIRED and pending["amiRemovalPending"] is True
        assert "amiInUseBy" not in pending and "amiDeregisteredAt" not in pending


@pytest.mark.unit
class TestDeleteWhileAnAmiRemovalIsPending:
    """A build row is the sweep's only record of an AMI pruning still has to remove, so a recipe delete,
    which removes every build row, is refused while any build is a pruning candidate. Pruning and the
    delete pick the candidates by one rule."""

    # Settled builds, newest first; the deregistered and the AMI-less build hold no place in the
    # keepVersions window.
    SETTLED = (
        (9, store.STATUS_AVAILABLE, {}),
        (8, store.STATUS_RETIRED, {"amiDeregisteredAt": "2026-01-02T00:00:00+00:00"}),
        (6, store.STATUS_FAILED, {"amiId": None}),
        (5, store.STATUS_REGISTER_FAILED, {}),
        (4, store.STATUS_AVAILABLE, {}),
        (3, store.STATUS_FAILED, {}),
        (2, store.STATUS_RETIRED, {}),
    )
    # A retired build whose AMI an instance kept, a candidate inside the window or outside it.
    PENDING = (7, store.STATUS_RETIRED, {"amiRemovalPending": True})

    def _builds(self, *rows):
        for version, status, attrs in rows:
            store.put_build({"recipeId": "al2023-desktop", "version": version, "status": status,
                             "amiId": f"ami-{version}", "snapshotIds": [f"snap-{version}"], **attrs})

    def _delete_refused(self, world):
        builds = store.list_builds("al2023-desktop")

        with pytest.raises(recipes.RecipeConflict) as refused:
            recipes.delete_recipe(store.get_recipe("al2023-desktop"))

        assert str(refused.value) == recipes.AMI_REMOVAL_PENDING
        recipe = store.get_recipe("al2023-desktop")
        assert recipe is not None and store.DELETE_MARKER_FIELD not in recipe
        assert store.list_builds("al2023-desktop") == builds
        world["ctx"].ib.delete_image_pipeline.assert_not_called()

    def test_a_retired_build_whose_ami_removal_is_pending_holds_off_the_delete_until_the_sweep_removes_it(
            self, world):
        row = seeded()
        recipes.start_build(row, "carol", only_if_changed=False)
        recipes.handle_image_state(store.latest_build("al2023-desktop")["imageBuildVersionArn"], "AVAILABLE", None)
        v1 = store.get_build("al2023-desktop", 1)
        world["ctx"].ec2.instances = [{"InstanceId": "i-desk", "ImageId": v1["amiId"]}]
        assert recipes.retire_build(row, v1)["amiRemovalPending"] is True

        self._delete_refused(world)

        # The refusal released its mark, so the sweep prunes the recipe once the desktop is gone.
        world["ctx"].ec2.instances = []
        assert recipes.sweep()["pruned"] == 1
        assert store.get_build("al2023-desktop", 1)["amiDeregisteredAt"]
        assert recipes.delete_recipe(store.get_recipe("al2023-desktop")) == []
        assert store._table().scan()["Items"] == []
        world["ctx"].ib.delete_image_pipeline.assert_called_once()

    @pytest.mark.parametrize("status", [store.STATUS_FAILED, store.STATUS_REGISTER_FAILED, store.STATUS_RETIRED])
    def test_a_build_beyond_keep_versions_whose_ami_is_not_deregistered_refuses_the_delete(self, world, status):
        seeded(keepVersions=1)
        self._builds((2, store.STATUS_AVAILABLE, {}), (1, status, {}))

        self._delete_refused(world)

    @pytest.mark.parametrize("status, attrs", [
        (store.STATUS_RETIRED, {"amiDeregisteredAt": "2026-01-02T00:00:00+00:00"}),
        (store.STATUS_FAILED, {"amiId": None}),
        (store.STATUS_RETIRED, {"amiId": None, "amiRemovalPending": True}),
        (store.STATUS_AVAILABLE, {}),
    ], ids=["retired-ami-deregistered", "failed-without-ami", "retired-pending-without-ami", "available"])
    def test_a_build_pruning_has_no_ami_to_remove_from_does_not_hold_off_the_delete(self, world, status, attrs):
        seeded(keepVersions=1)
        self._builds((2, store.STATUS_AVAILABLE, {}), (1, status, attrs))

        assert recipes.delete_recipe(store.get_recipe("al2023-desktop")) == []

        assert store._table().scan()["Items"] == []

    @pytest.mark.parametrize("keep, candidates", [(1, [7, 5, 3, 2]), (3, [7, 3, 2]), (5, [7, 2]), (20, [7])])
    def test_pruning_removes_the_amis_of_exactly_the_candidates_of_a_mixed_listing(self, world, keep, candidates):
        row = seeded(keepVersions=keep)
        self._builds(*self.SETTLED, self.PENDING, (1, store.STATUS_REGISTERING, {}))
        ec2 = world["ctx"].ec2

        assert recipes.prune_amis(row) == candidates

        amis = [f"ami-{version}" for version in candidates]
        assert [f[0]["Values"] for f in ec2.instance_filters] == [[ami] for ami in amis]
        assert len(ec2.deregister_image.call_args_list) == len(amis)
        assert [c.kwargs["ImageId"] for c in ec2.deregister_image.call_args_list] == amis
        for version in (9, 6, 4, 1):
            assert "amiDeregisteredAt" not in store.get_build("al2023-desktop", version)

    @pytest.mark.parametrize("keep, pending, refused", [(20, False, False), (1, False, True), (20, True, True)],
                             ids=["nothing-to-prune", "beyond-the-window", "pending-inside-the-window"])
    def test_the_delete_is_refused_exactly_when_pruning_has_an_ami_to_remove(self, world, keep, pending, refused):
        row = seeded(keepVersions=keep)
        self._builds(*self.SETTLED, *((self.PENDING,) if pending else ()))
        # Every AMI is in use, so pruning looks each candidate up and removes nothing.
        world["ctx"].ec2.instances = [{"InstanceId": f"i-{v}", "ImageId": f"ami-{v}"} for v in range(1, 10)]
        assert recipes.prune_amis(row) == []
        assert bool(world["ctx"].ec2.instance_filters) is refused

        if refused:
            self._delete_refused(world)
        else:
            assert recipes.delete_recipe(store.get_recipe("al2023-desktop")) == []
            assert store._table().scan()["Items"] == []
