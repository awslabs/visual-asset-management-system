# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Image Builder materialization: deterministic names and versions, already-exists reuse, parent
resolution, image description and cleanup — against recording fakes of the three AWS clients."""
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhImageBuilder as ib_mod

ALREADY = type("ResourceAlreadyExistsException", (Exception,), {})
NOT_FOUND = type("ResourceNotFoundException", (Exception,), {})

RECIPE = {
    "recipeId": "al2023-desktop", "kind": "vdi", "osFamily": "linux", "architecture": "x86_64",
    "parentImage": "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "buildInstanceType": "m7i.large", "rootSizeGb": 60, "contentHash": "ab" * 32,
    "components": [
        {"arn": "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"},
        {"name": "baseline", "data": "schemaVersion: 1.0"},
    ],
}


ASSET_RECIPE = {
    **RECIPE,
    "recipeId": "isaacsim",
    "assetPrefix": "addon/edh/amiRecipes/abcd1234/isaacsim/assets",
    "assets": [{"id": "vamscli", "repoPath": "tools/VamsCLI"},
               {"id": "connector", "repoPath": "tools/ExternalIntegrations/isaacsim_vams_integration"}],
    "parameters": {"isaacSimVersion": "5.1.0", "isaacSimSha256": ""},
    "components": [
        {"name": "040-vamscli",
         "data": "schemaVersion: 1.0\n# ${ASSET_vamscli} ${ASSET_connector} "
                 "v=${PARAM_isaacSimVersion} s=[${PARAM_isaacSimSha256}]\n"},
    ],
}

# The deployment values a document reaches as ``${DEPLOY_<key>}``.
DEPLOYMENT = {"sessionSecretPrefix": "team-a/edh-session/"}

DEPLOY_RECIPE = {
    **RECIPE,
    "recipeId": "isaacsim",
    "components": [
        RECIPE["components"][0],
        {"name": "060-session-bootstrap",
         "data": 'schemaVersion: 1.0\n'
                 '# SECRET_PREFIX="${VAMS_SESSION_SECRET_PREFIX:-${DEPLOY_sessionSecretPrefix}}"\n'},
    ],
}


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def ctx(**overrides):
    ib = MagicMock()
    ib.exceptions = SimpleNamespace(ResourceAlreadyExistsException=ALREADY, ResourceNotFoundException=NOT_FOUND,
                                    ResourceDependencyException=type("Dep", (Exception,), {}))
    ib.create_component.return_value = {"componentBuildVersionArn": "arn:c/1"}
    ib.create_image_recipe.return_value = {"imageRecipeArn": "arn:r/1"}
    ib.create_infrastructure_configuration.return_value = {"infrastructureConfigurationArn": "arn:i"}
    ib.create_distribution_configuration.return_value = {"distributionConfigurationArn": "arn:d"}
    ib.create_image_pipeline.return_value = {"imagePipelineArn": "arn:p"}
    ib.start_image_pipeline_execution.return_value = {"imageBuildVersionArn": "arn:img/1"}
    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"RootDeviceName": "/dev/xvda", "BlockDeviceMappings": [
        {"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": "snap-1"}}]}]}
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-0abc0abc0abc0abc0"}}
    values = dict(prefix="vams-t-edh", region="us-east-1", partition="aws", account="123456789012",
                  subnet_id="subnet-1", security_group_id="sg-1", instance_profile_name="prof",
                  cluster_id="edh-test", ib=ib, ec2=ec2, ssm=ssm)
    values.update(overrides)
    return ib_mod.ImageBuilderContext(**values)


@pytest.mark.unit
class TestNamesAndVersions:
    def test_semantic_version_is_three_bounded_integers(self):
        assert ib_mod.semantic_version("ffffff" + "ffffff" + "00") == "1.16777215.16777215"

    def test_build_key_changes_with_the_parent_ami(self):
        assert ib_mod.build_key("h", "ami-1") != ib_mod.build_key("h", "ami-2")

    def test_placeholders_are_substituted(self):
        c = ctx(partition="aws-us-gov", region="us-gov-west-1")
        out = ib_mod.substitute_placeholders(RECIPE["components"][0]["arn"], c)
        assert out == "arn:aws-us-gov:imagebuilder:us-gov-west-1:aws:component/update-linux/x.x.x"

    def test_names_carry_the_prefix_and_recipe_id(self):
        c = ctx()
        assert ib_mod.resource_name(c, "al2023-desktop") == "vams-t-edh-al2023-desktop"
        assert ib_mod.component_name(c, "al2023-desktop", 1, "Base Line!") == "vams-t-edh-al2023-desktop-c01-base-line-"


def _document_at_the_limit_ending_in(token):
    header = "schemaVersion: 1.0\n# "
    data = header + "#" * (16000 - len(header) - len(token)) + token
    assert len(data) == 16000
    return data


@pytest.mark.unit
class TestComponentDataSubstitution:
    def test_asset_ids_become_s3_uris_and_parameters_are_inlined(self):
        out = ib_mod.substitute_component_data(
            ASSET_RECIPE["components"][0]["data"], "vams-artefacts", ASSET_RECIPE, DEPLOYMENT)
        assert out.count("s3://vams-artefacts/addon/edh/amiRecipes/abcd1234/isaacsim/assets/vamscli/ ") == 1
        assert out.count("s3://vams-artefacts/addon/edh/amiRecipes/abcd1234/isaacsim/assets/connector/ ") == 1
        assert "v=5.1.0" in out
        assert "s=[]" in out
        assert "${" not in out

    def test_a_recipe_without_assets_or_parameters_is_returned_unchanged(self):
        data = RECIPE["components"][1]["data"]
        assert ib_mod.substitute_component_data(data, "", RECIPE, DEPLOYMENT) == data

    def test_assets_without_a_bucket_or_an_asset_prefix_are_refused(self):
        with pytest.raises(ib_mod.ComponentDocumentError, match="EDH_AMI_BUILD_ARTEFACTS_BUCKET"):
            ib_mod.substitute_component_data(ASSET_RECIPE["components"][0]["data"], "", ASSET_RECIPE, DEPLOYMENT)
        with pytest.raises(ib_mod.ComponentDocumentError, match="EDH_AMI_BUILD_ARTEFACTS_BUCKET"):
            ib_mod.substitute_component_data(
                ASSET_RECIPE["components"][0]["data"], "vams-artefacts", {**ASSET_RECIPE, "assetPrefix": None},
                DEPLOYMENT)

    def test_an_undeclared_placeholder_is_refused(self):
        with pytest.raises(ib_mod.ComponentDocumentError, match=r"unresolved placeholder"):
            ib_mod.substitute_component_data(
                "schemaVersion: 1.0\n# ${ASSET_typo}\n", "vams-artefacts", ASSET_RECIPE, DEPLOYMENT)
        with pytest.raises(ib_mod.ComponentDocumentError, match=r"unresolved placeholder"):
            ib_mod.substitute_component_data(
                "schemaVersion: 1.0\n# ${PARAM_missing}\n", "vams-artefacts", ASSET_RECIPE, DEPLOYMENT)

    def test_parameters_do_not_chain_and_resolution_is_order_independent(self):
        # Both insertion orders yield the same result: ${PARAM_b} is NOT resolved
        for params in [{"a": "${PARAM_b}", "b": "x"}, {"b": "x", "a": "${PARAM_b}"}]:
            recipe = {**ASSET_RECIPE, "parameters": params, "assets": []}
            out = ib_mod.substitute_component_data("v=${PARAM_a}", "", recipe, DEPLOYMENT)
            assert out == "v=${PARAM_b}"

    def test_malformed_asset_and_parameter_tokens_are_refused(self):
        for token in ["${ASSET_}", "${ASSET_vamscli%/}", "${ASSET_a.b}", "${PARAM_key }"]:
            with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(token)):
                ib_mod.substitute_component_data(f"x={token}", "vams-artefacts", ASSET_RECIPE, DEPLOYMENT)
        # ${base} expansions remain untouched
        assert ib_mod.substitute_component_data("x=${base}t64", "", RECIPE, DEPLOYMENT) == "x=${base}t64"

    @pytest.mark.parametrize("value,refused", [("v" * 10, False), ("v" * 11, True)])
    def test_the_length_limit_applies_to_the_document_once_resolved(self, value, refused):
        # 16000 characters as written: at the limit with a ten-character value, one over with eleven.
        data = _document_at_the_limit_ending_in("${PARAM_p}")
        recipe = {**RECIPE, "parameters": {"p": value},
                  "components": [{"name": "first", "data": "schemaVersion: 1.0"}, {"name": "big", "data": data}]}
        c = ctx()
        if refused:
            with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(
                    "component 2 is 16001 characters once its placeholders are resolved; "
                    "Image Builder accepts at most 16000")):
                ib_mod.materialize(c, recipe, "ami-0123456789abcdef0")
            c.ib.create_component.assert_not_called()
        else:
            ib_mod.materialize(c, recipe, "ami-0123456789abcdef0")
            assert len(c.ib.create_component.call_args.kwargs["data"]) == 16000

    def test_an_asset_uri_that_takes_a_document_past_the_limit_is_refused(self):
        uri = "s3://vams-artefacts/addon/edh/amiRecipes/abcd1234/isaacsim/assets/vamscli/"
        data = _document_at_the_limit_ending_in("${ASSET_vamscli}")
        recipe = {**ASSET_RECIPE, "components": [{"arn": RECIPE["components"][0]["arn"]},
                                                 {"name": "040-vamscli", "data": data}]}
        c = ctx(artefacts_bucket="vams-artefacts")
        with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(
                f"component 2 is {16000 - len('${ASSET_vamscli}') + len(uri)} characters")):
            ib_mod.materialize(c, recipe, "ami-0123456789abcdef0")
        c.ib.create_component.assert_not_called()

    def test_component_documents_resolve_without_a_context_and_leave_arn_components_out(self):
        documents = ib_mod.component_documents(ASSET_RECIPE, "vams-artefacts", DEPLOYMENT)
        assert documents == [ib_mod.substitute_component_data(ASSET_RECIPE["components"][0]["data"], "vams-artefacts",
                                                              ASSET_RECIPE, DEPLOYMENT)]
        assert ib_mod.component_documents(RECIPE, "", DEPLOYMENT) == [None, "schemaVersion: 1.0"]
        assert ib_mod.component_documents(DEPLOY_RECIPE, "", DEPLOYMENT)[1].endswith(
            'SECRET_PREFIX="${VAMS_SESSION_SECRET_PREFIX:-team-a/edh-session/}"\n')

    def test_materialize_creates_the_component_from_the_substituted_document(self):
        c = ctx(artefacts_bucket="vams-artefacts")
        ib_mod.materialize(c, ASSET_RECIPE, "ami-0123456789abcdef0")
        data = c.ib.create_component.call_args.kwargs["data"]
        assert data.count("s3://vams-artefacts/addon/edh/amiRecipes/abcd1234/isaacsim/assets/vamscli/ ") == 1
        assert data.count("s3://vams-artefacts/addon/edh/amiRecipes/abcd1234/isaacsim/assets/connector/ ") == 1
        assert "${ASSET_" not in data


@pytest.mark.unit
class TestDeploymentPlaceholders:
    def test_the_session_secret_prefix_is_the_deployments(self):
        data = 'SECRET_PREFIX="${VAMS_SESSION_SECRET_PREFIX:-${DEPLOY_sessionSecretPrefix}}"'
        assert ib_mod.substitute_component_data(data, "", RECIPE, DEPLOYMENT) == (
            'SECRET_PREFIX="${VAMS_SESSION_SECRET_PREFIX:-team-a/edh-session/}"')

    def test_the_deployment_keys_are_a_closed_set(self):
        assert set(ib_mod.DEPLOYMENT_PARAMETERS) == {"sessionSecretPrefix"}

    @pytest.mark.parametrize("token,deployment", [
        ("${DEPLOY_sessionSecretPrefix}", {}),
        ("${DEPLOY_sessionSecretPrefix}", {"sessionSecretPrefix": ""}),
        ("${DEPLOY_other}", {**DEPLOYMENT, "other": "team-a/"}),
        ("${DEPLOY_SessionSecretPrefix}", DEPLOYMENT),
        ("${DEPLOY_}", DEPLOYMENT),
    ])
    def test_a_key_outside_the_set_or_without_a_value_is_unresolved(self, token, deployment):
        with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(
                f"unresolved placeholder(s) in component document: {token}")):
            ib_mod.substitute_component_data(f"schemaVersion: 1.0\n# {token}\n", "", RECIPE, deployment)

    @pytest.mark.parametrize("value", ["team-a/edh-session", 'team-a/"$(id)/', "team a/", "team-a/\n"])
    def test_a_value_not_of_the_keys_form_is_refused(self, value):
        with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(
                "deployment value(s) not in the expected form for: ${DEPLOY_sessionSecretPrefix}")):
            ib_mod.substitute_component_data(
                "schemaVersion: 1.0\n# ${DEPLOY_sessionSecretPrefix}\n", "", RECIPE, {"sessionSecretPrefix": value})

    @pytest.mark.parametrize("extra,refused", [(0, False), (1, True)])
    def test_the_length_limit_counts_the_deployment_value(self, extra, refused):
        # A value as long as its token leaves the document at 16000 characters; one more is over.
        token = "${DEPLOY_sessionSecretPrefix}"
        recipe = {**RECIPE, "components": [RECIPE["components"][0],
                                           {"name": "big", "data": _document_at_the_limit_ending_in(token)}]}
        c = ctx(deployment_parameters={"sessionSecretPrefix": "p" * (len(token) - 1 + extra) + "/"})
        if refused:
            with pytest.raises(ib_mod.ComponentDocumentError, match=re.escape(
                    "component 2 is 16001 characters once its placeholders are resolved")):
                ib_mod.materialize(c, recipe, "ami-0123456789abcdef0")
            c.ib.create_component.assert_not_called()
        else:
            ib_mod.materialize(c, recipe, "ami-0123456789abcdef0")
            assert len(c.ib.create_component.call_args.kwargs["data"]) == 16000


def _set_build_environment(monkeypatch):
    for name, value in {
        "EDH_AMI_BUILD_PREFIX": "vams-t-edh", "EDH_CLUSTER_ID": "edh-test",
        "AWS_REGION": "us-east-1", "EDH_AMI_BUILD_PARTITION": "aws",
        "EDH_AMI_BUILD_ACCOUNT_ID": "123456789012", "EDH_AMI_BUILD_SUBNET_ID": "subnet-1",
        "EDH_AMI_BUILD_SECURITY_GROUP_ID": "sg-1",
        "EDH_AMI_BUILD_INSTANCE_PROFILE_NAME": "prof",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.unit
class TestContextFromEnv:
    def test_the_artefacts_bucket_is_optional_and_read_from_the_environment(self, monkeypatch):
        _set_build_environment(monkeypatch)
        monkeypatch.delenv("EDH_AMI_BUILD_ARTEFACTS_BUCKET", raising=False)
        ctx_empty = ib_mod.context_from_env(ib=MagicMock(), ec2=MagicMock(), ssm=MagicMock())
        assert ctx_empty.artefacts_bucket == ""
        monkeypatch.setenv("EDH_AMI_BUILD_ARTEFACTS_BUCKET", "vams-artefacts")
        ctx_set = ib_mod.context_from_env(ib=MagicMock(), ec2=MagicMock(), ssm=MagicMock())
        assert ctx_set.artefacts_bucket == "vams-artefacts"

    def test_the_deployment_values_are_read_from_the_environment(self, monkeypatch):
        _set_build_environment(monkeypatch)
        monkeypatch.delenv("EDH_SESSION_SECRET_PREFIX", raising=False)
        unset = ib_mod.context_from_env(ib=MagicMock(), ec2=MagicMock(), ssm=MagicMock())
        assert unset.deployment_parameters == {"sessionSecretPrefix": ""}
        monkeypatch.setenv("EDH_SESSION_SECRET_PREFIX", "team-a/edh-session/")
        assert ib_mod.context_from_env(ib=MagicMock(), ec2=MagicMock(), ssm=MagicMock()).deployment_parameters == (
            {"sessionSecretPrefix": "team-a/edh-session/"})
        assert ib_mod.deployment_parameters_from_env() == {"sessionSecretPrefix": "team-a/edh-session/"}

    def test_the_lookup_clients_time_out_quickly_and_try_twice(self, monkeypatch):
        # A recipe save looks the architecture up within the API's deadline, or skips it.
        _set_build_environment(monkeypatch)
        built = []

        def client(name, config=None, **kwargs):
            built.append((name, config))
            return MagicMock(name=name)
        monkeypatch.setattr(ib_mod.boto3, "client", client)
        ec2, ssm = MagicMock(), MagicMock()

        c = ib_mod.context_from_env(ib=MagicMock(), ec2=ec2, ssm=ssm)

        assert [name for name, _ in built] == ["ec2", "ssm"]
        assert c.lookup_ec2 is not ec2 and c.lookup_ssm is not ssm
        for _, config in built:
            assert (config.connect_timeout, config.read_timeout) == (2, 3)
            # Two attempts in all; botocore reads max_attempts as retries after the first.
            assert config.retries == {"total_max_attempts": 2, "mode": "standard"}

    def test_given_lookup_clients_are_used_and_a_bare_context_has_none(self, monkeypatch):
        _set_build_environment(monkeypatch)
        lookup_ec2, lookup_ssm = MagicMock(), MagicMock()
        c = ib_mod.context_from_env(ib=MagicMock(), ec2=MagicMock(), ssm=MagicMock(),
                                    lookup_ec2=lookup_ec2, lookup_ssm=lookup_ssm)
        assert c.lookup_ec2 is lookup_ec2 and c.lookup_ssm is lookup_ssm
        bare = ctx()
        assert bare.lookup_ec2 is None and bare.lookup_ssm is None


@pytest.mark.unit
class TestParentImageDescription:
    def test_the_architecture_and_root_device_are_read_from_one_call(self):
        ec2 = MagicMock()
        ec2.describe_images.return_value = {"Images": [{"Architecture": "arm64", "RootDeviceName": "/dev/sda1"}]}
        assert ib_mod.describe_parent_image(ec2, "ami-0abc0abc0abc0abc0") == {
            "architecture": "arm64", "rootDeviceName": "/dev/sda1"}
        ec2.describe_images.assert_called_once_with(ImageIds=["ami-0abc0abc0abc0abc0"])

    def test_an_image_without_either_field_has_no_architecture_and_the_default_root_device(self):
        ec2 = MagicMock()
        ec2.describe_images.return_value = {"Images": [{}]}
        assert ib_mod.describe_parent_image(ec2, "ami-0abc0abc0abc0abc0") == {
            "architecture": None, "rootDeviceName": "/dev/xvda"}

    @pytest.mark.parametrize("answer", [{"Images": []}, {}])
    def test_no_image_is_a_parent_that_cannot_be_found(self, answer):
        ec2 = MagicMock()
        ec2.describe_images.return_value = answer
        with pytest.raises(ib_mod.ParentImageUnavailable) as err:
            ib_mod.describe_parent_image(ec2, "ami-0abc0abc0abc0abc0")
        assert str(err.value) == "the parent image could not be found"

    @pytest.mark.parametrize("code", ["InvalidAMIID.NotFound", "InvalidAMIID.Malformed"])
    def test_an_ami_ec2_does_not_find_is_a_parent_that_cannot_be_found(self, code):
        ec2 = MagicMock()
        ec2.describe_images.side_effect = _client_error(code)
        with pytest.raises(ib_mod.ParentImageUnavailable) as err:
            ib_mod.describe_parent_image(ec2, "ami-0abc0abc0abc0abc0")
        assert str(err.value) == "the parent image could not be found"

    @pytest.mark.parametrize("code", ["UnauthorizedOperation", "RequestLimitExceeded"])
    def test_any_other_error_is_raised_as_it_is(self, code):
        ec2 = MagicMock()
        ec2.describe_images.side_effect = _client_error(code)
        with pytest.raises(ClientError) as err:
            ib_mod.describe_parent_image(ec2, "ami-0abc0abc0abc0abc0")
        assert err.value.response["Error"]["Code"] == code


@pytest.mark.unit
class TestInstanceTypeArchitectures:
    def test_each_type_maps_to_its_supported_architectures_from_one_call(self):
        ec2 = MagicMock()
        ec2.describe_instance_types.return_value = {"InstanceTypes": [
            {"InstanceType": "m7i.large", "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]}},
            {"InstanceType": "t2.micro", "ProcessorInfo": {"SupportedArchitectures": ["i386", "x86_64"]}},
            {"InstanceType": "m7g.large"},
        ]}
        assert ib_mod.instance_type_architectures(ec2, ["m7i.large", "t2.micro", "m7g.large"]) == {
            "m7i.large": ["x86_64"], "t2.micro": ["i386", "x86_64"], "m7g.large": []}
        ec2.describe_instance_types.assert_called_once_with(InstanceTypes=["m7i.large", "t2.micro", "m7g.large"])

    def test_a_list_naming_a_type_ec2_does_not_offer_is_described_one_type_at_a_time(self):
        ec2 = MagicMock()
        ec2.describe_instance_types.side_effect = [
            _client_error("InvalidInstanceType"),
            {"InstanceTypes": [{"InstanceType": "m7g.large", "ProcessorInfo": {"SupportedArchitectures": ["arm64"]}}]},
            _client_error("InvalidInstanceType"),
        ]
        assert ib_mod.instance_type_architectures(ec2, ["m7g.large", "zz9.nope"]) == {"m7g.large": ["arm64"]}
        assert [call.kwargs for call in ec2.describe_instance_types.call_args_list] == [
            {"InstanceTypes": ["m7g.large", "zz9.nope"]}, {"InstanceTypes": ["m7g.large"]},
            {"InstanceTypes": ["zz9.nope"]}]

    def test_a_single_type_ec2_does_not_offer_is_absent_after_one_call(self):
        ec2 = MagicMock()
        ec2.describe_instance_types.side_effect = _client_error("InvalidInstanceType")
        assert ib_mod.instance_type_architectures(ec2, ["zz9.nope"]) == {}
        ec2.describe_instance_types.assert_called_once_with(InstanceTypes=["zz9.nope"])

    @pytest.mark.parametrize("answers", [
        [_client_error("UnauthorizedOperation")],
        [_client_error("InvalidInstanceType"), _client_error("RequestLimitExceeded")],
    ], ids=["the-list", "one-type"])
    def test_any_other_refusal_is_raised(self, answers):
        ec2 = MagicMock()
        ec2.describe_instance_types.side_effect = answers
        with pytest.raises(ClientError) as err:
            ib_mod.instance_type_architectures(ec2, ["m7i.large", "zz9.nope"])
        assert err.value.response["Error"]["Code"] == answers[-1].response["Error"]["Code"]


@pytest.mark.unit
class TestParentResolution:
    def test_an_ami_id_is_itself(self):
        assert ib_mod.resolve_parent_image("ami-0123456789abcdef0", ctx=ctx(), edh_client=None, kind="vdi") == "ami-0123456789abcdef0"

    def test_an_ssm_parameter_is_read(self):
        c = ctx()
        assert ib_mod.resolve_parent_image("ssm:/aws/service/x", ctx=c, edh_client=None, kind="vdi") == "ami-0abc0abc0abc0abc0"
        c.ssm.get_parameter.assert_called_once_with(Name="/aws/service/x")

    def test_an_edh_stack_is_looked_up_by_name_in_the_kind_catalog(self):
        edh = MagicMock()
        edh.list_software_stacks.return_value = [{"id": 10, "stack_name": "Amazon Linux 2023", "ami_id": "ami-0f6de954b71901fb8"}]
        assert ib_mod.resolve_parent_image("edhStack:Amazon Linux 2023", ctx=ctx(), edh_client=edh, kind="vdi") == "ami-0f6de954b71901fb8"
        edh.list_software_stacks.assert_called_once_with("dcv")
        with pytest.raises(ib_mod.ParentImageUnavailable):
            ib_mod.resolve_parent_image("edhStack:Missing", ctx=ctx(), edh_client=edh, kind="simulation")


@pytest.mark.unit
class TestMaterialize:
    def test_creates_every_resource_once_and_returns_the_arns(self):
        c = ctx()
        out = ib_mod.materialize(c, RECIPE, "ami-0abc0abc0abc0abc0")
        assert out["componentArns"] == ["arn:aws:imagebuilder:us-east-1:aws:component/update-linux/x.x.x", "arn:c/1"]
        assert out["recipeArn"] == "arn:r/1" and out["pipelineArn"] == "arn:p"
        assert out["recipeVersion"] == ib_mod.semantic_version(out["buildKey"])
        c.ib.create_component.assert_called_once()
        kwargs = c.ib.create_component.call_args.kwargs
        assert kwargs["name"] == "vams-t-edh-al2023-desktop-c02-baseline" and kwargs["platform"] == "Linux"
        assert kwargs["semanticVersion"] == ib_mod.semantic_version(RECIPE["contentHash"])
        recipe_kwargs = c.ib.create_image_recipe.call_args.kwargs
        assert recipe_kwargs["parentImage"] == "ami-0abc0abc0abc0abc0"
        assert recipe_kwargs["blockDeviceMappings"][0]["deviceName"] == "/dev/xvda"
        assert recipe_kwargs["blockDeviceMappings"][0]["ebs"]["volumeSize"] == 60
        infra = c.ib.create_infrastructure_configuration.call_args.kwargs
        assert infra["instanceTypes"] == ["m7i.large"] and infra["subnetId"] == "subnet-1" and infra["terminateInstanceOnFailure"] is True
        dist = c.ib.create_distribution_configuration.call_args.kwargs["distributions"][0]
        assert dist["region"] == "us-east-1"
        ami_tags = dist["amiDistributionConfiguration"]["amiTags"]
        assert ami_tags[ib_mod.RECIPE_TAG] == "al2023-desktop"
        assert ami_tags["edh:visibility:edh-test"] == "true"
        assert c.ib.create_image_pipeline.call_args.kwargs["status"] == "ENABLED"
        # The visibility tag belongs on the AMI, not on the Image Builder objects.
        assert "edh:visibility:edh-test" not in c.ib.create_image_recipe.call_args.kwargs["tags"]

    def test_already_existing_resources_are_looked_up_and_updated(self):
        c = ctx()
        c.ib.create_component.side_effect = ALREADY()
        c.ib.list_component_build_versions.return_value = {"componentSummaryList": [{"arn": "arn:c/existing"}]}
        c.ib.create_image_recipe.side_effect = ALREADY()
        c.ib.create_infrastructure_configuration.side_effect = ALREADY()
        c.ib.list_infrastructure_configurations.return_value = {"infrastructureConfigurationSummaryList": [{"arn": "arn:i/old"}]}
        c.ib.create_distribution_configuration.side_effect = ALREADY()
        c.ib.list_distribution_configurations.return_value = {"distributionConfigurationSummaryList": [{"arn": "arn:d/old"}]}
        c.ib.create_image_pipeline.side_effect = ALREADY()
        c.ib.list_image_pipelines.return_value = {"imagePipelineList": [{"arn": "arn:p/old"}]}
        out = ib_mod.materialize(c, RECIPE, "ami-0abc0abc0abc0abc0")
        assert out["componentArns"][1] == "arn:c/existing"
        assert out["recipeArn"].startswith("arn:aws:imagebuilder:us-east-1:123456789012:image-recipe/vams-t-edh-al2023-desktop/")
        c.ib.update_infrastructure_configuration.assert_called_once()
        c.ib.update_distribution_configuration.assert_called_once()
        c.ib.update_image_pipeline.assert_called_once()
        assert c.ib.update_image_pipeline.call_args.kwargs["imageRecipeArn"] == out["recipeArn"]

    def test_the_build_instance_requires_imdsv2_and_an_existing_configuration_takes_the_same_settings(self):
        created = ctx()
        ib_mod.materialize(created, RECIPE, "ami-0abc0abc0abc0abc0")
        create_kwargs = created.ib.create_infrastructure_configuration.call_args.kwargs
        assert create_kwargs["instanceMetadataOptions"] == {"httpTokens": "required", "httpPutResponseHopLimit": 2}
        existing = ctx()
        existing.ib.create_infrastructure_configuration.side_effect = ALREADY()
        existing.ib.list_infrastructure_configurations.return_value = {
            "infrastructureConfigurationSummaryList": [{"arn": "arn:i/old"}]}
        ib_mod.materialize(existing, RECIPE, "ami-0abc0abc0abc0abc0")
        settings = {key: value for key, value in create_kwargs.items() if key not in ("name", "tags")}
        assert existing.ib.update_infrastructure_configuration.call_args.kwargs == {
            "infrastructureConfigurationArn": "arn:i/old", **settings}

    @pytest.mark.parametrize("build_instance_type,instance_types", [
        ("m7i.large", ["m7i.large"]),
        (["m7i.xlarge", "m6i.xlarge", "m5.xlarge"], ["m7i.xlarge", "m6i.xlarge", "m5.xlarge"]),
    ])
    def test_the_build_instance_types_are_the_recipes_in_its_order(self, build_instance_type, instance_types):
        c = ctx()
        ib_mod.materialize(c, {**RECIPE, "buildInstanceType": build_instance_type}, "ami-0abc0abc0abc0abc0")
        assert c.ib.create_infrastructure_configuration.call_args.kwargs["instanceTypes"] == instance_types

    def test_a_given_root_device_names_the_volume_and_the_parent_is_not_described(self):
        c = ctx()
        ib_mod.materialize(c, RECIPE, "ami-0abc0abc0abc0abc0", root_device_name="/dev/sda1")
        assert c.ib.create_image_recipe.call_args.kwargs["blockDeviceMappings"][0]["deviceName"] == "/dev/sda1"
        c.ec2.describe_images.assert_not_called()

    def test_without_a_root_device_the_parent_is_described_once_before_any_component_is_created(self):
        c = ctx()
        calls = []
        c.ec2.describe_images.side_effect = (
            lambda **kwargs: calls.append("describe_images") or {"Images": [{"RootDeviceName": "/dev/sda1"}]})
        c.ib.create_component.side_effect = (
            lambda **kwargs: calls.append("create_component") or {"componentBuildVersionArn": "arn:c/1"})
        ib_mod.materialize(c, RECIPE, "ami-0abc0abc0abc0abc0")
        assert calls == ["describe_images", "create_component"]
        assert c.ib.create_image_recipe.call_args.kwargs["blockDeviceMappings"][0]["deviceName"] == "/dev/sda1"

    def test_a_parent_ec2_does_not_find_creates_nothing(self):
        c = ctx()
        c.ec2.describe_images.side_effect = _client_error("InvalidAMIID.NotFound")
        with pytest.raises(ib_mod.ParentImageUnavailable, match="^the parent image could not be found$"):
            ib_mod.materialize(c, RECIPE, "ami-0abc0abc0abc0abc0")
        c.ib.create_component.assert_not_called()
        c.ib.create_image_recipe.assert_not_called()

    def test_windows_components_use_the_windows_platform(self):
        c = ctx()
        ib_mod.materialize(c, {**RECIPE, "osFamily": "windows"}, "ami-0abc0abc0abc0abc0")
        assert c.ib.create_component.call_args.kwargs["platform"] == "Windows"

    def test_every_document_is_resolved_before_any_component_is_created(self):
        c = ctx(artefacts_bucket="vams-artefacts")
        recipe = {**ASSET_RECIPE, "components": [
            {"name": "first", "data": "schemaVersion: 1.0"},
            {"name": "second", "data": "schemaVersion: 1.0\n# ${PARAM_missing}\n"}]}
        with pytest.raises(ib_mod.ComponentDocumentError, match="PARAM_missing"):
            ib_mod.materialize(c, recipe, "ami-0abc0abc0abc0abc0")
        c.ib.create_component.assert_not_called()

    def test_the_component_version_and_build_key_follow_the_artefacts_bucket_for_a_recipe_with_assets(self):
        c_a = ctx(artefacts_bucket="bucket-a")
        c_b = ctx(artefacts_bucket="bucket-b")
        out_a = ib_mod.materialize(c_a, ASSET_RECIPE, "ami-0abc0abc0abc0abc0")
        out_b = ib_mod.materialize(c_b, ASSET_RECIPE, "ami-0abc0abc0abc0abc0")
        sem_ver_a = c_a.ib.create_component.call_args.kwargs["semanticVersion"]
        sem_ver_b = c_b.ib.create_component.call_args.kwargs["semanticVersion"]
        assert sem_ver_a != sem_ver_b
        assert out_a["buildKey"] != out_b["buildKey"]

    def test_the_component_version_and_build_key_follow_the_deployment_values_a_document_names(self):
        versions, keys = [], []
        for prefix in ["team-a/edh-session/", "team-b/edh-session/", "team-a/edh-session/"]:
            c = ctx(deployment_parameters={"sessionSecretPrefix": prefix})
            keys.append(ib_mod.materialize(c, DEPLOY_RECIPE, "ami-0abc0abc0abc0abc0")["buildKey"])
            versions.append(c.ib.create_component.call_args.kwargs["semanticVersion"])
            assert ":-" + prefix + "}" in c.ib.create_component.call_args.kwargs["data"]
        assert versions[0] != versions[1] and versions[0] == versions[2]
        assert keys[0] != keys[1] and keys[0] == keys[2]

    def test_a_recipe_naming_no_deployment_placeholder_hashes_as_before_whatever_the_values(self):
        # Literal digests: these hashes version the Image Builder objects a recipe has already built.
        for deployment in [{}, DEPLOYMENT, {"sessionSecretPrefix": "team-b/edh-session/"}]:
            c = ctx(artefacts_bucket="vams-artefacts", deployment_parameters=deployment)
            assert ib_mod.materialization_hash(RECIPE, c) == "ab" * 32
            assert ib_mod.materialization_hash(ASSET_RECIPE, c) == (
                "c983ead1b4411e022860e162472c8d16182a0abc0567cda6bd8057e8c01e2d09")

    def test_a_recipe_without_assets_keeps_its_version_whatever_the_bucket(self):
        c_a = ctx(artefacts_bucket="bucket-a")
        c_b = ctx(artefacts_bucket="bucket-b")
        out_a = ib_mod.materialize(c_a, RECIPE, "ami-0abc0abc0abc0abc0")
        out_b = ib_mod.materialize(c_b, RECIPE, "ami-0abc0abc0abc0abc0")
        sem_ver_a = c_a.ib.create_component.call_args.kwargs["semanticVersion"]
        sem_ver_b = c_b.ib.create_component.call_args.kwargs["semanticVersion"]
        assert sem_ver_a == sem_ver_b == ib_mod.semantic_version(RECIPE["contentHash"])
        assert out_a["buildKey"] == out_b["buildKey"]


@pytest.mark.unit
class TestImagesAndCleanup:
    def test_start_pipeline_returns_the_image_build_arn(self):
        assert ib_mod.start_pipeline(ctx(), "arn:p") == "arn:img/1"

    def test_describe_image_reads_the_regional_ami_and_its_snapshots(self):
        c = ctx()
        c.ib.get_image.return_value = {"image": {"state": {"status": "AVAILABLE"}, "outputResources": {"amis": [
            {"region": "eu-west-1", "image": "ami-other"}, {"region": "us-east-1", "image": "ami-0abc0abc0abc0abc0"}]}}}
        out = ib_mod.describe_image(c, "arn:img/1")
        assert out == {"status": "AVAILABLE", "reason": None, "amiId": "ami-0abc0abc0abc0abc0", "snapshotIds": ["snap-1"]}

    def test_describe_image_reports_a_failure_reason(self):
        c = ctx()
        c.ib.get_image.return_value = {"image": {"state": {"status": "FAILED", "reason": "boom"}}}
        assert ib_mod.describe_image(c, "arn:img/1") == {"status": "FAILED", "reason": "boom", "amiId": None, "snapshotIds": []}

    def test_deregister_tolerates_already_gone(self):
        c = ctx()
        c.ec2.deregister_image.side_effect = _client_error("InvalidAMIID.NotFound")
        c.ec2.delete_snapshot.side_effect = _client_error("InvalidSnapshot.NotFound")
        ib_mod.deregister_ami(c, "ami-1", ["snap-1"])

    def test_the_instances_using_an_ami_are_listed_across_pages_sorted_and_once_each(self):
        c = ctx()
        c.ec2.get_paginator.return_value.paginate.return_value = iter([
            {"Reservations": [{"Instances": [{"InstanceId": "i-0c"}, {"InstanceId": "i-0a"}]}]},
            {"Reservations": [{"Instances": [{"InstanceId": "i-0b"}]}, {"Instances": [{"InstanceId": "i-0a"}]}]},
        ])
        assert ib_mod.instances_using_ami(c, "ami-1") == ["i-0a", "i-0b", "i-0c"]
        c.ec2.get_paginator.assert_called_once_with("describe_instances")
        c.ec2.get_paginator.return_value.paginate.assert_called_once_with(Filters=[
            {"Name": "image-id", "Values": ["ami-1"]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ])

    def test_an_ami_no_instance_uses_lists_none_and_a_failed_listing_raises(self):
        c = ctx()
        c.ec2.get_paginator.return_value.paginate.return_value = iter([{"Reservations": []}])
        assert ib_mod.instances_using_ami(c, "ami-1") == []

        def refused():
            raise _client_error("UnauthorizedOperation")
            yield
        c.ec2.get_paginator.return_value.paginate.return_value = refused()
        with pytest.raises(ClientError):
            ib_mod.instances_using_ami(c, "ami-1")

    def test_delete_recipe_resources_walks_pipeline_first_and_collects_warnings(self):
        c = ctx()
        c.ib.delete_component.side_effect = c.ib.exceptions.ResourceDependencyException()
        warnings = ib_mod.delete_recipe_resources(c, {
            "pipelineArn": "arn:p", "recipeArn": "arn:r/1",
            "componentArns": ["arn:aws:imagebuilder:us-east-1:123456789012:component/vams-t-edh-r-c01-x/1.0.0/1"],
            "infrastructureConfigurationArn": "arn:i", "distributionConfigurationArn": "arn:d"})
        assert c.ib.delete_image_pipeline.call_args.kwargs == {"imagePipelineArn": "arn:p"}
        assert any("component/vams-t-edh-r-c01-x" in w for w in warnings)

    def test_delete_recipe_resources_deletes_only_the_components_the_build_created(self):
        # A recipe may reference the account's own component by arn, or a managed one: neither is the
        # build's to delete. Only the inline components, named with the deployment prefix, are.
        c = ctx()
        own = "arn:aws:imagebuilder:us-east-1:123456789012:component/vams-t-edh-r-c02-setup/1.0.0/1"
        referenced = "arn:aws:imagebuilder:us-east-1:123456789012:component/team-hardening/2.0.0/1"
        managed = "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0/1"
        warnings = ib_mod.delete_recipe_resources(c, {"componentArns": [managed, referenced, own]})
        assert [call.kwargs["componentBuildVersionArn"] for call in c.ib.delete_component.call_args_list] == [own]
        assert warnings == []
