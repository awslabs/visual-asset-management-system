# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The catalog registration custom resource: property parsing, natural-key idempotency, per-item
warn-and-continue, the {PhysicalResourceId, Data} response, the id echo rule, and the property names
read here being the ones the nested stack writes on the resource."""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh import edhCatalogRegistration as reg
from backend.backend.handlers.addon.edh.common import edhClient as edh_client
from backend.backend.handlers.addon.edh.common import edhLeasePolicy
from backend.backend.handlers.addon.edh.common.edhClient import EdhApiError, EdhAuthError, EdhError
from backend.tests.handlers.addon.edh.edhTestSupport import DEFAULT_PROJECT, FakeProjects

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_NESTED_STACK = os.path.join(_REPO_ROOT, "infra", "lib", "nestedStacks", "addon", "edhWorkstations",
                             "edhWorkstationsBuilder-nestedStack.ts")

VDI = [
    {"amiId": "ami-0123456789abcdef0", "stackName": "linux-cad", "baseOs": "amazonlinux2023",
     "rootSize": 100, "virtualDesktopProfileId": 1},
    {"amiId": "ami-0fedcba9876543210", "stackName": "win-cad", "baseOs": "windows2022",
     "rootSize": 200, "virtualDesktopProfileId": 2, "launchTenancy": "dedicated"},
]
SIM = [
    {"amiId": "ami-0aaaaaaaaaaaaaaaa", "stackName": "sim-base", "rootSize": 50,
     "targetNodeProfileId": 4},
    {"amiId": "ami-0bbbbbbbbbbbbbbbb", "stackName": "sim-gpu", "rootSize": 80,
     "targetNodeProfileId": 4, "targetNodeUserDataId": 2},
]

BUNDLE = {"recipeId": "al2023-desktop", "prefix": "addon/edh/amiRecipes/abc/al2023-desktop",
          "files": ["recipe.json", "components/010-baseline.yml"], "buildOnDeploy": True}
MANIFEST = {"recipeId": "al2023-desktop", "displayName": "AL2023", "kind": "vdi", "osFamily": "linux",
            "baseOs": "amazonlinux2023", "architecture": "x86_64", "parentImage": "ami-0123456789abcdef0",
            "buildInstanceType": "m7i.large", "rootSizeGb": 60, "stackName": "VAMS AL2023 Desktop", "profileId": 1,
            "components": [{"arn": "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"},
                           {"file": "components/010-baseline.yml"}]}


def _event(request_type, vdi=VDI, sim=SIM, physical_id=None, **extra):
    properties = {
        "predefinedVdiImages": json.dumps(vdi),
        "predefinedSimulationAmis": json.dumps(sim),
        "contentHash": "abc123",
        "registrationRevision": "3",
        "Timestamp": "1760000000000",
        "ServiceToken": "arn:aws:lambda:us-east-1:123456789012:function:provider",
    }
    properties.update(extra)
    event = {"RequestType": request_type, "ResourceProperties": properties,
             "LogicalResourceId": "EdhCatalogRegistration", "RequestId": "r-1"}
    if physical_id:
        event["PhysicalResourceId"] = physical_id
    return event


def _client(existing_dcv=(), existing_target=(), projects=None):
    """An EDH client whose catalog answers what was seeded plus what this run registered, so the id
    read-back the project assignment needs finds the new rows; projects is its project table."""
    client = MagicMock()
    catalog = {
        "dcv": [{"id": i, "stack_name": name} for i, name in enumerate(existing_dcv, start=1)],
        "target_nodes": [{"id": i, "stack_name": name} for i, name in enumerate(existing_target, start=1)],
    }
    next_id = {"value": 100}
    projects = projects if projects is not None else FakeProjects()

    def register(kind, fields, **kwargs):
        next_id["value"] += 1
        catalog[kind].append({"id": next_id["value"], "stack_name": fields["stack_name"],
                              "ami_id": fields["ami_id"]})
        return {"message": "registered successfully in SOCA"}

    client.list_software_stacks.side_effect = lambda kind: list(catalog[kind])
    client.register_software_stack.side_effect = register
    client.assign_software_stack_to_project.side_effect = (
        lambda kind, stack_id, project_name, **kwargs: projects.assign(kind, stack_id, project_name))
    client.catalog = catalog
    client.projects = projects
    return client


def _empty():
    return {"registered": "none", "skipped": "none", "warnings": "none", "queued": "none"}


@pytest.mark.unit
class TestCreateAndUpdate:
    def test_create_registers_every_missing_stack_and_mints_the_physical_id(self):
        client = _client()
        with patch.object(reg, "EdhClient", return_value=client) as client_cls:
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["PhysicalResourceId"] == f"EdhCatalogRegistration:{reg.edh_cluster_id}"
        assert response["Data"] == {
            "registered": "dcv:linux-cad; dcv:win-cad; target_nodes:sim-base; target_nodes:sim-gpu",
            "skipped": "none",
            "warnings": "none",
            "queued": "none",
        }
        # Asserted on the patch handle: outside the block reg.EdhClient is the real class again.
        client_cls.assert_called_once_with(profile="batch")
        kinds = [call.args[0] for call in client.register_software_stack.call_args_list]
        assert kinds == ["dcv", "dcv", "target_nodes", "target_nodes"]
        _kind, fields = client.register_software_stack.call_args_list[1].args
        assert fields == {"ami_id": "ami-0fedcba9876543210", "stack_name": "win-cad",
                          "base_os": "windows2022", "root_size": "200",
                          "virtual_desktop_profile_id": "2", "launch_tenancy": "dedicated"}
        _kind, sim_fields = client.register_software_stack.call_args_list[2].args
        assert sim_fields == {"ami_id": "ami-0aaaaaaaaaaaaaaaa", "stack_name": "sim-base", "root_size": "50",
                              "target_node_profile_id": "4", "target_node_user_data_id": "1"}
        assert client.register_software_stack.call_args_list[3].args[1]["target_node_user_data_id"] == "2"
        # Deploy-time registration has no acting VAMS user: the client's service-actor default stands.
        assert client.register_software_stack.call_args_list, "nothing was registered"
        assert all(call.kwargs == {} for call in client.register_software_stack.call_args_list)

    def test_existing_stack_names_are_skipped_by_natural_key(self):
        client = _client(existing_dcv=("linux-cad",), existing_target=("sim-base",))
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Update", physical_id="keep-me"), MagicMock())
        assert response["PhysicalResourceId"] == "keep-me"
        assert response["Data"]["registered"] == "dcv:win-cad; target_nodes:sim-gpu"
        assert response["Data"]["skipped"] == "dcv:linux-cad; target_nodes:sim-base"
        assert response["Data"]["warnings"] == "none"
        assert client.register_software_stack.call_count == 2

    def test_a_same_named_stack_holding_another_ami_is_skipped_with_a_warning(self):
        # EDH has no update route: the entry is left as EDH holds it, and the divergence is reported so an
        # AMI roll under an unchanged stackName does not pass as a no-op.
        client = _client()
        client.catalog["dcv"] = [{"id": 1, "stack_name": "linux-cad", "ami_id": "ami-0000000000000000f"}]
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Update", physical_id="keep-me"), MagicMock())
        assert response["Data"]["skipped"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] == (
            "dcv:linux-cad: already registered in EDH with a different AMI; not updated "
            "(delete it from the Images tab or register it under a new stackName)")
        assert response["Data"]["registered"] == "dcv:win-cad; target_nodes:sim-base; target_nodes:sim-gpu"
        assert "ami-0000000000000000f" not in json.dumps(response)

    def test_a_same_named_stack_holding_the_same_ami_is_skipped_without_a_warning(self):
        client = _client()
        client.catalog["dcv"] = [{"id": 1, "stack_name": "linux-cad", "ami_id": VDI[0]["amiId"]}]
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["Data"]["skipped"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] == "none"

    def test_a_failed_item_becomes_a_warning_and_the_rest_still_register(self):
        client = _client()
        registering = client.register_software_stack.side_effect

        def refuse_the_first(kind, fields, **kwargs):
            if fields["stack_name"] == VDI[0]["stackName"]:
                raise EdhApiError("dcv said 400: bad ami")
            return registering(kind, fields, **kwargs)

        client.register_software_stack.side_effect = refuse_the_first
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["Data"]["registered"] == "dcv:win-cad; target_nodes:sim-base; target_nodes:sim-gpu"
        assert response["Data"]["warnings"] == "dcv:linux-cad: registration failed (EdhApiError)"
        assert "bad ami" not in json.dumps(response)

    def test_a_listing_failure_warns_for_the_whole_kind_and_continues_with_the_other(self):
        client = _client()

        def listing(kind):
            if kind == "dcv":
                raise EdhAuthError("401")
            return list(client.catalog[kind])

        client.list_software_stacks.side_effect = listing
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["Data"]["warnings"] == \
            "dcv: listing existing software stacks failed; 2 entries not attempted"
        assert response["Data"]["registered"] == "target_nodes:sim-base; target_nodes:sim-gpu"

    def test_a_root_key_secret_that_cannot_be_read_is_one_warning_not_a_failed_resource(self, monkeypatch):
        """The real client against a Secrets Manager that refuses the read: the kind is warned about
        and the resource still succeeds, so the deploy finishes and the next one retries."""
        refusing = MagicMock()
        refusing.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied by policy"}}, "GetSecretValue")
        monkeypatch.setattr(edh_client, "_get_secretsmanager_client", lambda: refusing)
        http = MagicMock()
        monkeypatch.setattr(edh_client, "_http_request", http)
        with patch.object(reg, "EdhClient", return_value=edh_client.EdhClient(profile="batch")):
            response = reg.lambda_handler(_event("Create", sim=[]), MagicMock())
        assert response["Data"]["warnings"] == \
            "dcv: listing existing software stacks failed; 2 entries not attempted"
        assert response["Data"]["registered"] == "none"
        assert "denied by policy" not in json.dumps(response)
        http.assert_not_called()

    def test_an_invalid_config_entry_is_a_warning_not_a_failed_resource(self):
        client = _client()
        bad = [{"amiId": "ami-0123456789abcdef0", "stackName": "no-os", "rootSize": 100,
                "virtualDesktopProfileId": 1}]
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(_event("Create", vdi=bad, sim=[]), MagicMock())
        assert response["Data"]["warnings"] == "dcv[0]: invalid entry"
        assert response["Data"]["registered"] == "none"
        client.register_software_stack.assert_not_called()

    def test_empty_lists_make_no_edh_call_and_still_answer_every_data_key(self):
        with patch.object(reg, "EdhClient") as client_cls:
            response = reg.lambda_handler(_event("Create", vdi=[], sim=[]), MagicMock())
        client_cls.assert_not_called()
        assert response["Data"] == _empty()

    def test_list_properties_may_arrive_already_parsed_or_absent(self):
        client = _client()
        event = _event("Create")
        event["ResourceProperties"]["predefinedVdiImages"] = VDI
        del event["ResourceProperties"]["predefinedSimulationAmis"]
        with patch.object(reg, "EdhClient", return_value=client):
            response = reg.lambda_handler(event, MagicMock())
        assert response["Data"]["registered"] == "dcv:linux-cad; dcv:win-cad"

    def test_a_non_array_list_property_raises_so_the_framework_reports_failed(self):
        event = _event("Create")
        event["ResourceProperties"]["predefinedVdiImages"] = json.dumps({"not": "a list"})
        with patch.object(reg, "EdhClient", return_value=_client()):
            with pytest.raises(ValueError):
                reg.lambda_handler(event, MagicMock())

    def test_unknown_request_type_raises_so_the_framework_reports_failed(self):
        with patch.object(reg, "EdhClient", return_value=_client()):
            with pytest.raises(ValueError):
                reg.lambda_handler(_event("Rotate"), MagicMock())


@pytest.mark.unit
class TestPhysicalId:
    @pytest.mark.parametrize("request_type", ["Create", "Update"])
    def test_an_incoming_id_is_echoed_whatever_the_request_type(self, request_type):
        with patch.object(reg, "EdhClient", return_value=_client()):
            response = reg.lambda_handler(_event(request_type, physical_id="keep-me"), MagicMock())
        assert response["PhysicalResourceId"] == "keep-me"


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _custom_resource_property_keys(source):
    """The keys of the registration custom resource's ``properties`` block, as the nested stack writes them."""
    start = source.find("properties: {")
    assert start >= 0, "the custom resource's properties block is not where the guard reads it"
    depth, index = 0, source.index("{", start)
    for index in range(index, len(source)):
        depth += (source[index] == "{") - (source[index] == "}")
        if depth == 0:
            break
    block = source[start:index]
    return set(re.findall(r"^\s*(\w+):", block, re.M))


@pytest.mark.unit
class TestCustomResourceContract:
    """The property names this handler reads are the ones the nested stack writes on the resource."""

    def test_the_list_properties_are_the_ones_the_nested_stack_writes(self):
        keys = _custom_resource_property_keys(_read(_NESTED_STACK))
        assert {reg.PROPERTY_VDI, reg.PROPERTY_SIMULATION, reg.PROPERTY_JOB_DEFINITIONS, reg.PROPERTY_LEASE_POLICY_DEFAULTS} <= keys

    def test_the_guard_reads_the_landed_stack(self):
        # Control: a moved file must fail here, not pass by reading nothing.
        assert os.path.exists(_NESTED_STACK)

    def test_the_natural_key_is_edhs_stack_name_column(self):
        assert reg.NATURAL_KEY == "stack_name"


@pytest.mark.unit
class TestDelete:
    def test_delete_echoes_the_id_and_calls_edh_nowhere(self):
        with patch.object(reg, "EdhClient") as client_cls:
            response = reg.lambda_handler(_event("Delete", physical_id="keep-me"), MagicMock())
        client_cls.assert_not_called()
        assert response == {"PhysicalResourceId": "keep-me", "Data": _empty()}

    def test_delete_without_an_id_still_answers_a_stable_one(self):
        with patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Delete"), MagicMock())
        assert response["PhysicalResourceId"] == f"EdhCatalogRegistration:{reg.edh_cluster_id}"


def _s3(objects):
    client = MagicMock()

    def get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = objects[Key].encode("utf-8")
        return {"Body": body}
    client.get_object.side_effect = get_object
    return client


@pytest.mark.unit
class TestAmiRecipeSeeding:
    def _install(self, monkeypatch, *, upsert=None, start=None):
        monkeypatch.setattr(reg, "s3", _s3({
            "addon/edh/amiRecipes/abc/al2023-desktop/recipe.json": json.dumps(MANIFEST),
            "addon/edh/amiRecipes/abc/al2023-desktop/components/010-baseline.yml": "name: b\nschemaVersion: 1.0\n",
        }))
        monkeypatch.setattr(reg, "get_bucket_name", lambda key: "artefacts")
        lifecycle = MagicMock()
        lifecycle.RecipeConflict = type("RecipeConflict", (Exception,), {})
        lifecycle.REQUESTED_BY_DEPLOY = "deploy"
        lifecycle.OUTCOME_STARTED, lifecycle.OUTCOME_QUEUED, lifecycle.OUTCOME_SKIPPED_UNCHANGED = "started", "queued", "skipped-unchanged"
        lifecycle.upsert_config_recipe.side_effect = upsert or (lambda manifest, build_on_deploy: ({**manifest, "buildOnDeploy": build_on_deploy}, "changed"))
        lifecycle.start_build.side_effect = start or (lambda recipe, by, only_if_changed: ({"version": 1}, "started"))
        lifecycle.disable_missing_config_recipes.return_value = []
        monkeypatch.setattr(reg, "recipes", lifecycle)
        return lifecycle

    def test_file_components_are_inlined_from_the_bucket_before_the_upsert(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        result = reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                                       "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        manifest = lifecycle.upsert_config_recipe.call_args.args[0]
        assert manifest["components"][1] == {"name": "010-baseline", "data": "name: b\nschemaVersion: 1.0\n"}
        assert lifecycle.upsert_config_recipe.call_args.kwargs == {"build_on_deploy": True}
        assert result["registered"] == ["amiRecipe:al2023-desktop"]
        assert result["queued"] == ["amiRecipe:al2023-desktop v1 (started)"]
        assert lifecycle.start_build.call_args.kwargs == {"only_if_changed": True}
        lifecycle.disable_missing_config_recipes.assert_called_once_with(["al2023-desktop"])

    def test_disabled_builds_read_nothing(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                              "amiBuildsEnabled": "false", "amiRecipeBundles": json.dumps([BUNDLE])})
        lifecycle.upsert_config_recipe.assert_not_called()
        reg.s3.get_object.assert_not_called()

    def test_an_unchanged_recipe_is_skipped_and_an_admin_owned_one_reported(self, monkeypatch):
        lifecycle = self._install(monkeypatch, upsert=lambda m, build_on_deploy: (None, "admin-owned"))
        result = reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                                       "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        assert result["skipped"] == ["amiRecipe:al2023-desktop (admin-owned)"] and result["queued"] == []
        lifecycle.start_build.assert_not_called()

    def test_a_refused_build_and_a_missing_object_are_warnings(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        lifecycle.start_build.side_effect = lifecycle.RecipeConflict("EDH lists no vdi stack named 'x'")
        result = reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                                       "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        assert result["warnings"] == ["amiRecipe:al2023-desktop: build not started (EDH lists no vdi stack named 'x')"]
        reg.s3.get_object.side_effect = KeyError("missing")
        result = reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                                       "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        assert result["warnings"] == ["amiRecipe:al2023-desktop: KeyError",
                                      "AMI recipe seeding: 1 bundle(s) could not be read; disable pass skipped"]

    def test_an_unread_bundle_leaves_every_recipe_enabled(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        reg.s3.get_object.side_effect = KeyError("missing")
        result = reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                                       "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        assert result["warnings"][-1] == "AMI recipe seeding: 1 bundle(s) could not be read; disable pass skipped"
        lifecycle.disable_missing_config_recipes.assert_not_called()

    def test_the_response_data_carries_queued(self):
        assert reg._response_data({"registered": [], "skipped": [], "warnings": [], "queued": ["x"]})["queued"] == "x"

    def test_an_asset_bundle_sets_the_asset_prefix_on_the_manifest(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        bundle = {**BUNDLE, "assets": [{"id": "vamscli", "prefix": f"{BUNDLE['prefix']}/assets/vamscli"}]}
        monkeypatch.setattr(reg, "s3", _s3({
            f"{BUNDLE['prefix']}/recipe.json": json.dumps(
                {**MANIFEST, "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"}]}),
            f"{BUNDLE['prefix']}/components/010-baseline.yml": "name: b\nschemaVersion: 1.0\n",
        }))
        reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                              "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([bundle])})
        manifest = lifecycle.upsert_config_recipe.call_args.args[0]
        assert manifest["assetPrefix"] == f"{BUNDLE['prefix']}/assets"

    def test_the_manifest_not_the_bundle_property_decides_the_asset_prefix(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        bundle = {**BUNDLE, "assets": [{"id": "vamscli", "prefix": f"{BUNDLE['prefix']}/assets/vamscli"}]}
        reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                              "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([bundle])})
        assert "assetPrefix" not in lifecycle.upsert_config_recipe.call_args.args[0]

    def test_an_asset_prefix_declared_by_the_manifest_is_discarded(self, monkeypatch):
        lifecycle = self._install(monkeypatch)
        monkeypatch.setattr(reg, "s3", _s3({
            f"{BUNDLE['prefix']}/recipe.json": json.dumps(
                {**MANIFEST, "assetPrefix": "elsewhere/assets"}),
            f"{BUNDLE['prefix']}/components/010-baseline.yml": "name: b\nschemaVersion: 1.0\n",
        }))
        reg.register_catalog({**_event("Create", vdi=[], sim=[])["ResourceProperties"],
                              "amiBuildsEnabled": "true", "amiRecipeBundles": json.dumps([BUNDLE])})
        assert "assetPrefix" not in lifecycle.upsert_config_recipe.call_args.args[0]


def _not_assigned(detail):
    """The warning of a stack EDH registered that the launch project does not carry."""
    return f"registered in EDH but not assigned to project '{DEFAULT_PROJECT}' ({detail})"


@pytest.mark.unit
class TestProjectAssignment:
    """EDH resolves a user's launchable stacks through the projects a stack is attached to, so the
    predefined images are attached to the launch project. A stack the project already carries sends no
    update, and a refusal is a warning the resource still succeeds with, so the next deploy retries it."""

    def _run(self, client, **entries):
        with patch.object(reg, "EdhClient", return_value=client):
            return reg.lambda_handler(_event("Create", **entries), MagicMock())

    def test_every_registered_stack_is_attached_to_the_default_project(self):
        client = _client()
        response = self._run(client)
        assert response["Data"]["warnings"] == "none"
        assert client.projects.stack_ids("dcv") == ["101", "102"]
        assert client.projects.stack_ids("target_nodes") == ["103", "104"]
        # The update is a full replace: every field the project carries goes back with it.
        first = client.projects.puts[0]
        assert first["project_id"] == "1" and first["software_stack_ids"] == "101"
        assert first["allowed_users"] == "alice,bob" and first["allowed_groups"] == "designers"

    def test_a_stack_the_project_already_carries_sends_no_update(self):
        client = _client(projects=FakeProjects(software_stack_ids=(101, 102)))
        response = self._run(client, sim=[])
        assert response["Data"]["registered"] == "dcv:linux-cad; dcv:win-cad"
        assert client.projects.puts == []

    def test_an_entry_already_in_the_catalog_is_attached_as_well(self):
        """A stack an earlier deploy registered is skipped, and still checked against the project, so a
        redeploy attaches one EDH holds outside every project."""
        client = _client(existing_dcv=("linux-cad",))
        response = self._run(client, sim=[])
        assert response["Data"]["skipped"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] == "none"
        assert client.projects.stack_ids("dcv") == ["1", "101"]

    def test_a_refused_assignment_is_one_warning_per_entry(self):
        client = _client()
        client.assign_software_stack_to_project.side_effect = EdhAuthError("401")
        response = self._run(client, sim=[])
        assert response["Data"]["registered"] == "dcv:linux-cad; dcv:win-cad"
        assert response["Data"]["warnings"] == "; ".join(
            f"dcv:{name}: {_not_assigned('EdhAuthError')}" for name in ("linux-cad", "win-cad"))
        assert "401" not in json.dumps(response)

    def test_a_project_edh_does_not_have_is_a_warning_naming_it(self):
        client = _client(projects=FakeProjects(name="another-project"))
        response = self._run(client, vdi=VDI[:1], sim=[])
        assert response["Data"]["registered"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] == f"dcv:linux-cad: {_not_assigned('EdhNotFoundError')}"

    def test_a_stack_whose_id_the_catalog_does_not_show_is_warned_about(self):
        client = _client()
        client.list_software_stacks.side_effect = lambda kind: []
        response = self._run(client, vdi=VDI[:1], sim=[])
        assert response["Data"]["registered"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] ==             f"dcv:linux-cad: {_not_assigned('its EDH id could not be read back')}"
        assert client.projects.puts == []

    def test_a_catalog_that_stops_answering_warns_once_for_the_kind(self):
        client = _client()
        listing = client.list_software_stacks.side_effect
        reads = {"count": 0}

        def fails_after_the_first(kind):
            reads["count"] += 1
            if reads["count"] > 1:
                raise EdhAuthError("401")
            return listing(kind)

        client.list_software_stacks.side_effect = fails_after_the_first
        response = self._run(client, vdi=VDI[:1], sim=[])
        assert response["Data"]["registered"] == "dcv:linux-cad"
        assert response["Data"]["warnings"] == f"dcv: {_not_assigned('the catalog could not be read back')}"


@pytest.mark.unit
class TestLeaseSeeding:
    def test_seed_lease_policy_inserts_on_create(self, edh_session_table):
        props = {
            "leasePolicyDefaults": json.dumps(
                {"enabled": True, "leaseHours": 5, "forced": False, "graceHours": 2}
            )
        }
        result = {"registered": [], "skipped": [], "warnings": []}
        reg._seed_lease_policy(props, result)
        assert edhLeasePolicy.read_lease_policy()["leaseHours"] == 5
        assert "leasePolicy" in result["registered"]

    def test_seed_lease_policy_noop_when_present(self, edh_session_table):
        edhLeasePolicy.write_lease_policy({"leaseHours": 12}, "admin")
        props = {
            "leasePolicyDefaults": json.dumps(
                {"enabled": True, "leaseHours": 5, "forced": False, "graceHours": 2}
            )
        }
        result = {"registered": [], "skipped": [], "warnings": []}
        reg._seed_lease_policy(props, result)
        assert edhLeasePolicy.read_lease_policy()["leaseHours"] == 12
        assert "leasePolicy (admin-owned)" in result["skipped"]

    def test_seed_lease_policy_absent_property_is_noop(self, edh_session_table):
        result = {"registered": [], "skipped": [], "warnings": []}
        reg._seed_lease_policy({}, result)
        assert result == {"registered": [], "skipped": [], "warnings": []}

    def test_seed_lease_policy_failure_is_a_warning(self, edh_session_table, monkeypatch):
        monkeypatch.setattr(edhLeasePolicy, "seed_lease_policy", lambda defaults: 1 / 0)
        props = {
            "leasePolicyDefaults": json.dumps(
                {"enabled": True, "leaseHours": 5, "forced": False, "graceHours": 2}
            )
        }
        result = {"registered": [], "skipped": [], "warnings": []}
        reg._seed_lease_policy(props, result)
        assert "leasePolicy" not in result["registered"]
        assert any("leasePolicy" in w and "ZeroDivisionError" in w for w in result["warnings"])

    def test_register_catalog_invokes_seed_and_registers_the_row(self, edh_session_table):
        result = reg.register_catalog(
            {
                **_event("Create", vdi=[], sim=[])["ResourceProperties"],
                "leasePolicyDefaults": json.dumps(
                    {"enabled": True, "leaseHours": 7, "forced": True, "graceHours": 3}
                ),
            }
        )
        assert "leasePolicy" in result["registered"]
        policy = edhLeasePolicy.read_lease_policy()
        assert policy["leaseHours"] == 7
        assert policy["forced"] is True
