# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AmiRecipeRequestModel: the recipe shape shared by recipe.json, the Admin routes and the seeding."""
import pytest
from pydantic import ValidationError

from backend.backend.models.addon.edh import (
    AMI_RECIPE_COMPONENT_DATA_MAX_BYTES,
    AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES,
    ARCHITECTURE_FAMILIES,
    EDH_ARCHITECTURES,
    AmiRecipeAssetModel,
    AmiRecipeComponentModel,
    AmiRecipeRequestModel,
    architecture_family,
    edh_parameter_key_pattern,
)

# The parameters validator names the pattern it applied, so the key cases expect its own message.
KEY_PATTERN_MESSAGE = f"parameters keys must match {edh_parameter_key_pattern}"

VALID = {
    "recipeId": "al2023-desktop",
    "displayName": "Amazon Linux 2023 desktop",
    "kind": "vdi",
    "osFamily": "linux",
    "baseOs": "amazonlinux2023",
    "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "buildInstanceType": "m7i.large",
    "rootSizeGb": 60,
    "stackName": "VAMS AL2023 Desktop",
    "profileId": 1,
    "components": [
        {"arn": "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"},
        {"name": "baseline", "data": "name: x\nschemaVersion: 1.0\nphases: []\n"},
    ],
    "keepVersions": 3,
    "recipeRevision": 1,
    "description": "desc",
}


@pytest.mark.unit
class TestAmiRecipeRequestModel:
    def test_a_valid_recipe_parses_with_defaults(self):
        model = AmiRecipeRequestModel(**{k: v for k, v in VALID.items() if k not in ("keepVersions", "recipeRevision")})
        assert model.keepVersions is None
        assert model.recipeRevision == 1
        assert model.enabled is True
        assert model.components[0].arn.endswith("update-linux/x.x.x")

    @pytest.mark.parametrize("field,value,fragment", [
        ("recipeId", "Bad_Id", "recipeId"),
        ("kind", "container", "kind must be one of"),
        ("architecture", "ppc64le", "architecture must be one of"),
        ("baseOs", "centos7x", "baseOs must be one of"),
        ("parentImage", "latest", "parentImage"),
        ("buildInstanceType", "large", "buildInstanceType"),
        ("rootSizeGb", 5, "rootSizeGb"),
        ("keepVersions", 21, "keepVersions"),
        ("profileId", -1, "profileId"),
    ])
    def test_field_rules(self, field, value, fragment):
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, field: value})
        assert fragment in str(err.value)

    def test_os_family_must_match_base_os(self):
        with pytest.raises(ValidationError, match="osFamily does not match baseOs"):
            AmiRecipeRequestModel(**{**VALID, "osFamily": "windows"})

    def test_a_windows_recipe_is_x86_64_only(self):
        windows = {**VALID, "osFamily": "windows", "baseOs": "windows2022"}
        with pytest.raises(ValidationError, match="Windows recipes support x86_64 only"):
            AmiRecipeRequestModel(**{**windows, "architecture": "arm64"})
        simulation = {**VALID, "kind": "simulation", "recipeId": "sim-node", "osFamily": "windows",
                      "architecture": "arm64"}
        simulation.pop("baseOs")
        with pytest.raises(ValidationError, match="Windows recipes support x86_64 only"):
            AmiRecipeRequestModel(**simulation)
        assert AmiRecipeRequestModel(**windows).architecture == "x86_64"
        assert AmiRecipeRequestModel(**{**VALID, "architecture": "arm64"}).architecture == "arm64"

    def test_a_simulation_recipe_carries_no_base_os(self):
        sim = {**VALID, "kind": "simulation", "recipeId": "sim-node"}
        sim.pop("baseOs")
        assert AmiRecipeRequestModel(**sim).baseOs is None
        with pytest.raises(ValidationError, match="baseOs applies to vdi recipes only"):
            AmiRecipeRequestModel(**{**sim, "baseOs": "amazonlinux2023"})

    def test_components_are_bounded(self):
        with pytest.raises(ValidationError):
            AmiRecipeRequestModel(**{**VALID, "components": []})
        with pytest.raises(ValidationError):
            AmiRecipeRequestModel(**{**VALID, "components": [VALID["components"][0]] * 21})

    def test_a_component_is_an_arn_or_an_inline_document_not_both(self):
        with pytest.raises(ValidationError, match="either"):
            AmiRecipeComponentModel(arn=VALID["components"][0]["arn"], name="x", data="schemaVersion: 1.0")
        with pytest.raises(ValidationError, match="either"):
            AmiRecipeComponentModel(name="x")
        with pytest.raises(ValidationError, match="schemaVersion"):
            AmiRecipeComponentModel(name="x", data="phases: []")

    def test_an_inline_document_is_bounded_at_what_create_component_accepts(self):
        """Image Builder takes the document inline, so the model's bound is CreateComponent's own."""
        assert AMI_RECIPE_COMPONENT_DATA_MAX_BYTES == 16000
        header = "schemaVersion: 1.0\n"
        at_bound = header + "#" * (16000 - len(header))
        assert len(at_bound) == 16000
        assert len(AmiRecipeComponentModel(name="baseline", data=at_bound).data) == 16000
        with pytest.raises(ValidationError) as err:
            AmiRecipeComponentModel(name="baseline", data=at_bound + "#")
        assert ("data",) in [error["loc"] for error in err.value.errors()]

    @pytest.mark.parametrize("value", [
        "m7i.large",
        ["m7i.xlarge"],
        ["m7i.xlarge", "m6i.xlarge", "m5.xlarge"],
        [f"c{n}.large" for n in range(1, 9)],
        ["m7i.large", "m" * 62 + ".x"],
    ])
    def test_the_build_instance_type_is_one_type_or_a_list_kept_as_given(self, value):
        model = AmiRecipeRequestModel(**{**VALID, "buildInstanceType": value})
        assert model.buildInstanceType == value
        assert model.dict()["buildInstanceType"] == value

    @pytest.mark.parametrize("value", [
        [],
        ["m7i.large", "large"],
        ["m7i.large", "m7i.large"],
        [f"c{n}.large" for n in range(1, 10)],
        ["m7i.large", "m" * 63 + ".x"],
        "M7i.large",
    ])
    def test_a_build_instance_type_list_is_bounded_distinct_and_well_formed(self, value):
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, "buildInstanceType": value})
        assert "buildInstanceType" in str(err.value)

    def test_a_file_reference_is_refused_by_the_model(self):
        """`file` entries exist only in recipe.json; the seeding inlines them before validation."""
        with pytest.raises(ValidationError):
            AmiRecipeRequestModel(**{**VALID, "components": [{"file": "components/x.yml"}]})

    def test_an_ssm_parent_image_names_a_public_parameter_path(self):
        """EDH parents resolve through the public AMI parameters; any other path is refused by name."""
        assert AmiRecipeRequestModel(**VALID).parentImage.startswith("ssm:/aws/service/")
        with pytest.raises(ValidationError, match=r"ssm:/aws/service/"):
            AmiRecipeRequestModel(**{**VALID, "parentImage": "ssm:/vams/team/golden-ami-id"})

    @pytest.mark.parametrize("partition", ["aws", "aws-us-gov", "aws-cn", "aws-eusc", "aws-iso-b"])
    def test_a_component_arn_is_accepted_in_every_partition(self, partition):
        arn = f"arn:{partition}:imagebuilder:us-east-1:aws:component/update-linux/1.0.0"
        assert AmiRecipeComponentModel(arn=arn).arn == arn

    def test_assets_and_parameters_are_optional(self):
        model = AmiRecipeRequestModel(**VALID)
        assert model.assets is None and model.parameters is None

    def test_a_display_name_may_carry_parentheses_and_a_stack_name_may_not(self):
        assert AmiRecipeRequestModel(**{**VALID, "displayName": "Isaac Sim (copy)"}).displayName == "Isaac Sim (copy)"
        for value in ("Isaac Sim <copy>", "Isaac; Sim", "Isaac Sim/2", ""):
            with pytest.raises(ValidationError) as err:
                AmiRecipeRequestModel(**{**VALID, "displayName": value})
            assert ("displayName",) in [error["loc"] for error in err.value.errors()]
        with pytest.raises(ValidationError, match="stackName"):
            AmiRecipeRequestModel(**{**VALID, "stackName": "VAMS AL2023 Desktop (copy)"})

    def test_duplicate_of_is_an_optional_recipe_id(self):
        assert AmiRecipeRequestModel(**VALID).duplicateOf is None
        assert AmiRecipeRequestModel(**{**VALID, "duplicateOf": "isaacsim"}).duplicateOf == "isaacsim"
        for value in ("Bad_Id", "x", "-leading", "a" * 41, "isaacsim\n"):
            with pytest.raises(ValidationError) as err:
                AmiRecipeRequestModel(**{**VALID, "duplicateOf": value})
            assert ("duplicateOf",) in [error["loc"] for error in err.value.errors()]

    def test_a_valid_asset_and_parameter_set_parses(self):
        model = AmiRecipeRequestModel(**{
            **VALID,
            "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"},
                       {"id": "connector", "repoPath": "tools/ExternalIntegrations/isaacsim_vams_integration"}],
            "parameters": {"isaacSimVersion": "5.1.0", "isaacSimSha256": ""},
        })
        assert [a.id for a in model.assets] == ["vamscli", "connector"]
        assert model.parameters["isaacSimVersion"] == "5.1.0"
        assert model.parameters["isaacSimSha256"] == ""

    def test_asset_ids_are_unique(self):
        with pytest.raises(ValidationError, match="assets carry duplicate ids"):
            AmiRecipeRequestModel(**{**VALID, "assets": [
                {"id": "vamscli", "repoPath": "tools/VamsCLI"},
                {"id": "vamscli", "repoPath": "tools/permissionsSetup"},
            ]})

    def test_assets_are_capped(self):
        with pytest.raises(ValidationError):
            AmiRecipeRequestModel(**{**VALID, "assets": [
                {"id": f"a{i}", "repoPath": "tools/VamsCLI"} for i in range(9)
            ]})

    @pytest.mark.parametrize("parameters,fragment", [
        ({"bad-key": "x"}, KEY_PATTERN_MESSAGE),
        ({"0start": "x"}, KEY_PATTERN_MESSAGE),
        ({"k" * 65: "x"}, KEY_PATTERN_MESSAGE),
        ({"good": "x" * 1025}, "parameters values are limited to 1024 characters"),
        ({f"k{i}": "v" for i in range(17)}, "parameters accepts at most 16 entries"),
    ])
    def test_parameter_rules(self, parameters, fragment):
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, "parameters": parameters})
        assert fragment in str(err.value)

    def test_the_longest_key_and_asset_id_the_patterns_admit_are_accepted(self):
        """Positive controls for the two length bounds: a 64-character key, a 32-character id."""
        model = AmiRecipeRequestModel(**{
            **VALID,
            "assets": [{"id": "a" + "b" * 31, "repoPath": "tools/VamsCLI"}],
            "parameters": {"k" * 64: "v"},
        })
        assert [asset.id for asset in model.assets] == ["a" + "b" * 31]
        assert list(model.parameters) == ["k" * 64]


@pytest.mark.unit
class TestLaunchInstanceTypes:
    """The instance types a desktop of the recipe's image may launch on: absent, null or empty is no
    restriction of the recipe's own, and a list is 1-64 distinct types of the build types' own form."""

    def test_the_bound_is_the_documented_one(self):
        assert AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES == 64

    @pytest.mark.parametrize("value", [None, []], ids=["null", "empty"])
    def test_absent_null_or_empty_is_no_restriction(self, value):
        assert AmiRecipeRequestModel(**VALID).launchInstanceTypes is None
        model = AmiRecipeRequestModel(**{**VALID, "launchInstanceTypes": value})
        assert model.launchInstanceTypes is None
        assert "launchInstanceTypes" not in model.dict(exclude_none=True)

    @pytest.mark.parametrize("value", [
        ["g5.2xlarge"],
        ["g6e.xlarge", "g5.2xlarge", "gr6.4xlarge"],
        [f"g{n}.xlarge" for n in range(64)],
        ["g5.2xlarge", "m" * 62 + ".x"],
    ], ids=["one", "kept-in-order", "at-the-bound", "longest-type"])
    def test_a_list_is_kept_as_given(self, value):
        model = AmiRecipeRequestModel(**{**VALID, "launchInstanceTypes": value})
        assert model.launchInstanceTypes == value
        assert model.dict(exclude_none=True)["launchInstanceTypes"] == value

    @pytest.mark.parametrize("value", [
        [f"g{n}.xlarge" for n in range(65)],
        ["g5.2xlarge", "g5.2xlarge"],
        ["g5.2xlarge", "large"],
        ["G5.2xlarge"],
        ["g5.2xlarge", "m" * 63 + ".x"],
        ["g5.2xlarge\n"],
        "g5.2xlarge",
        [17],
    ], ids=["over-the-bound", "duplicate", "no-size", "upper-case", "too-long", "trailing-newline", "a-string",
            "not-text"])
    def test_a_list_is_bounded_distinct_and_well_formed(self, value):
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, "launchInstanceTypes": value})
        assert ("launchInstanceTypes",) == err.value.errors()[0]["loc"][:1]


@pytest.mark.unit
class TestAmiRecipeAssetModel:
    def test_a_relative_tools_directory_is_accepted_and_normalized(self):
        assert AmiRecipeAssetModel(id="vamscli", repoPath=r"tools\VamsCLI").repoPath == "tools/VamsCLI"

    @pytest.mark.parametrize("repo_path,fragment", [
        ("/tools/VamsCLI", "relative repository path"),
        ("C:/tools/VamsCLI", "relative repository path"),
        ("tools/../backend", "relative repository path"),
        ("tools//VamsCLI", "relative repository path"),
        ("backend/handlers", "must be under tools/"),
        ("toolsX/VamsCLI", "must be under tools/"),
    ])
    def test_repo_path_rules(self, repo_path, fragment):
        with pytest.raises(ValidationError) as err:
            AmiRecipeAssetModel(id="vamscli", repoPath=repo_path)
        assert fragment in str(err.value)

    @pytest.mark.parametrize("asset_id", ["VamsCli", "1vamscli", "-vamscli", "vams_cli", "v" * 33])
    def test_id_pattern(self, asset_id):
        with pytest.raises(ValidationError):
            AmiRecipeAssetModel(id=asset_id, repoPath="tools/VamsCLI")


@pytest.mark.unit
class TestATrailingNewlineIsRefused:
    """Pydantic v1 matches with ``re.match``, where a terminal ``$`` also matches immediately before a
    trailing newline. The recipe patterns anchor with ``\\Z``, so a value whose only flaw is that
    newline is refused rather than accepted and carried into a component document or an Image Builder
    call. Each case pairs the refusal with the same value without the newline, which is accepted.
    """

    def test_a_recipe_id_that_ends_in_a_newline(self):
        assert AmiRecipeRequestModel(**VALID).recipeId == "al2023-desktop"
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, "recipeId": "al2023-desktop\n"})
        assert ("recipeId",) in [error["loc"] for error in err.value.errors()]

    def test_an_asset_id_that_ends_in_a_newline(self):
        assert AmiRecipeAssetModel(id="vamscli", repoPath="tools/VamsCLI").id == "vamscli"
        with pytest.raises(ValidationError) as err:
            AmiRecipeAssetModel(id="vamscli\n", repoPath="tools/VamsCLI")
        assert ("id",) in [error["loc"] for error in err.value.errors()]

    def test_a_parameter_key_that_ends_in_a_newline(self):
        accepted = AmiRecipeRequestModel(**{**VALID, "parameters": {"isaacSimVersion": "5.1.0"}})
        assert list(accepted.parameters) == ["isaacSimVersion"]
        with pytest.raises(ValidationError) as err:
            AmiRecipeRequestModel(**{**VALID, "parameters": {"isaacSimVersion\n": "5.1.0"}})
        assert KEY_PATTERN_MESSAGE in str(err.value)

    def test_a_component_name_that_ends_in_a_newline(self):
        document = "name: x\nschemaVersion: 1.0\nphases: []\n"
        assert AmiRecipeComponentModel(name="baseline", data=document).name == "baseline"
        with pytest.raises(ValidationError) as err:
            AmiRecipeComponentModel(name="baseline\n", data=document)
        assert ("name",) in [error["loc"] for error in err.value.errors()]


@pytest.mark.unit
class TestArchitectureFamily:
    """An instance type runs an image of its own family. EC2 and EDH list some x86 types under i386,
    and any other name, a mac architecture among them, is of no known family."""

    @pytest.mark.parametrize("architecture,family", [("x86_64", "x86"), ("i386", "x86"), ("arm64", "arm")])
    def test_each_known_architecture_maps_to_its_family(self, architecture, family):
        assert architecture_family(architecture) == family

    def test_the_families_are_exactly_the_known_architectures(self):
        assert ARCHITECTURE_FAMILIES == {"x86_64": "x86", "i386": "x86", "arm64": "arm"}

    @pytest.mark.parametrize("value", ["x86_64_mac", "arm64_mac", "", " x86_64", "X86_64", "x86", None, 64,
                                       ["x86_64"]])
    def test_any_other_value_is_of_no_known_family(self, value):
        assert architecture_family(value) is None

    def test_every_recipe_architecture_has_a_family(self):
        assert {architecture_family(architecture) for architecture in EDH_ARCHITECTURES} == {"x86", "arm"}
