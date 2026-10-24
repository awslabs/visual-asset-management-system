# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The job-definition branch of the catalog registration custom resource: the tagged Batch request,
natural-key idempotency against the cluster's tagged ACTIVE revisions, supersession of a changed
image, per-item warn-and-continue, and the cases that must touch neither Batch nor EDH."""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh import edhCatalogRegistration as reg
import backend.backend.handlers.addon.edh.common.edhClient as edh_client

IMAGE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/vams-t1-edh-sim-worker:0123456789abcdef0123456789abcdef"
OLD_IMAGE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/vams-t1-edh-sim-worker:fedcba9876543210fedcba9876543210"
JOB_DEFINITIONS = [
    {"name": "sim-train", "imageUri": IMAGE_URI, "vcpus": 4, "memory": 16384},
    {"name": "sim-infer", "imageUri": IMAGE_URI, "vcpus": 2, "memory": 8192, "gpu": 1, "platform": "EC2"},
]
VDI = [
    {"amiId": "ami-0123456789abcdef0", "stackName": "linux-cad", "baseOs": "amazonlinux2023",
     "rootSize": 100, "virtualDesktopProfileId": 1},
]
TAG = f"edh:visibility:{os.environ['EDH_CLUSTER_ID']}"
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_IMAGE_BUILD_CONSTRUCT = os.path.join(_REPO_ROOT, "infra", "lib", "nestedStacks", "addon", "edhWorkstations",
                                      "constructs", "edhImageBuild-construct.ts")


def _event(request_type, job_definitions=JOB_DEFINITIONS, vdi=(), sim=(), physical_id=None):
    properties = {
        "predefinedVdiImages": json.dumps(list(vdi)),
        "predefinedSimulationAmis": json.dumps(list(sim)),
        "jobDefinitions": json.dumps(list(job_definitions)),
        "contentHash": "abc123",
        "registrationRevision": "3",
        "Timestamp": "1760000000000",
        "ServiceToken": "arn:aws:lambda:us-east-1:123456789012:function:provider",
    }
    event = {"RequestType": request_type, "ResourceProperties": properties,
             "LogicalResourceId": "EdhCatalogRegistration", "RequestId": "r-1"}
    if physical_id:
        event["PhysicalResourceId"] = physical_id
    return event


def _record(name, revision=1, image=IMAGE_URI, vcpus=4, memory=16384, gpu=None, tagged=True):
    requirements = [{"type": "VCPU", "value": str(vcpus)}, {"type": "MEMORY", "value": str(memory)}]
    if gpu:
        requirements.append({"type": "GPU", "value": str(gpu)})
    return {
        "jobDefinitionName": name,
        "jobDefinitionArn": f"arn:aws:batch:us-east-1:123456789012:job-definition/{name}:{revision}",
        "revision": revision, "status": "ACTIVE", "type": "container",
        "containerProperties": {"image": image, "resourceRequirements": requirements},
        "platformCapabilities": ["EC2"],
        "tags": {TAG: "true"} if tagged else {},
    }


def _batch(existing=()):
    """A scripted Batch client: one describe page holding ``existing``; register/deregister succeed."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{"jobDefinitions": list(existing)}]
    client.register_job_definition.return_value = {
        "jobDefinitionName": "new", "revision": 1,
        "jobDefinitionArn": "arn:aws:batch:us-east-1:123456789012:job-definition/new:1"}
    client.deregister_job_definition.return_value = {}
    return client


def _client_error(operation):
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied by policy"}}, operation)


def _edh_client():
    """An EDH client whose catalog answers what this run registered, so the id read-back the project
    assignment needs finds the new rows."""
    client = MagicMock()
    catalog = {"dcv": [], "target_nodes": []}
    next_id = {"value": 100}

    def register(kind, fields, **kwargs):
        next_id["value"] += 1
        catalog[kind].append({"id": next_id["value"], "stack_name": fields["stack_name"]})
        return {"message": "ok"}

    client.list_software_stacks.side_effect = lambda kind: list(catalog[kind])
    client.register_software_stack.side_effect = register
    return client


def _optional_records(file_name):
    path = os.path.join(FIXTURES_DIR, file_name)
    if not os.path.exists(path):
        pytest.skip(f"the spike did not capture {file_name}")
    with open(path, "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    return edh_client._as_list(edh_client._payload(fixture["response"]["json"]))


@pytest.mark.unit
class TestRegistration:
    def test_create_registers_every_missing_definition_with_the_tagged_batch_request(self):
        batch = _batch()
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient") as client_cls:
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["Data"]["registered"] == "jobDefinition:sim-train; jobDefinition:sim-infer"
        assert response["Data"]["skipped"] == "none"
        assert response["Data"]["warnings"] == "none"
        client_cls.assert_not_called()
        first, second = [call.kwargs for call in batch.register_job_definition.call_args_list]
        assert first == {
            "jobDefinitionName": "sim-train", "type": "container",
            "containerProperties": {"image": IMAGE_URI, "resourceRequirements": [
                {"type": "VCPU", "value": "4"}, {"type": "MEMORY", "value": "16384"}]},
            "platformCapabilities": ["EC2"], "tags": {TAG: "true"}}
        assert second["jobDefinitionName"] == "sim-infer"
        assert {"type": "GPU", "value": "1"} in second["containerProperties"]["resourceRequirements"]
        assert second["tags"] == {TAG: "true"}
        batch.deregister_job_definition.assert_not_called()
        # The existing revisions come from the ACTIVE listing alone: an INACTIVE revision must neither
        # suppress a registration nor be fed to the deregistration.
        batch.get_paginator.assert_called_once_with("describe_job_definitions")
        batch.get_paginator.return_value.paginate.assert_called_once_with(status="ACTIVE")

    def test_an_identical_tagged_revision_is_skipped_by_name(self):
        batch = _batch(existing=[_record("sim-train")])
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Update", physical_id="keep-me"), MagicMock())
        assert response["PhysicalResourceId"] == "keep-me"
        assert response["Data"]["registered"] == "jobDefinition:sim-infer"
        assert response["Data"]["skipped"] == "jobDefinition:sim-train"
        assert batch.register_job_definition.call_count == 1
        batch.deregister_job_definition.assert_not_called()

    def test_a_changed_image_registers_a_new_revision_and_retires_the_superseded_one(self):
        batch = _batch(existing=[_record("sim-train", revision=2, image=OLD_IMAGE_URI)])
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create", job_definitions=JOB_DEFINITIONS[:1]), MagicMock())
        assert response["Data"]["registered"] == "jobDefinition:sim-train"
        assert response["Data"]["skipped"] == "none"
        batch.deregister_job_definition.assert_called_once_with(
            jobDefinition="arn:aws:batch:us-east-1:123456789012:job-definition/sim-train:2")

    def test_a_same_named_definition_without_the_edh_tag_is_not_a_match(self):
        batch = _batch(existing=[_record("sim-train", tagged=False)])
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create", job_definitions=JOB_DEFINITIONS[:1]), MagicMock())
        assert response["Data"]["registered"] == "jobDefinition:sim-train"
        # An untagged revision is not VAMS's to retire.
        batch.deregister_job_definition.assert_not_called()

    def test_a_failed_registration_becomes_a_warning_and_the_rest_still_register(self):
        batch = _batch()
        batch.register_job_definition.side_effect = [_client_error("RegisterJobDefinition"), {"revision": 1}]
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create"), MagicMock())
        assert response["Data"]["registered"] == "jobDefinition:sim-infer"
        assert response["Data"]["warnings"] == "jobDefinition:sim-train: registration failed (ClientError)"
        assert "denied by policy" not in json.dumps(response)

    def test_a_failed_deregistration_is_a_warning_and_the_registration_still_counts(self):
        batch = _batch(existing=[_record("sim-train", revision=2, image=OLD_IMAGE_URI)])
        batch.deregister_job_definition.side_effect = _client_error("DeregisterJobDefinition")
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create", job_definitions=JOB_DEFINITIONS[:1]), MagicMock())
        assert response["Data"]["registered"] == "jobDefinition:sim-train"
        assert response["Data"]["warnings"] == \
            "jobDefinition:sim-train: superseded revision not deregistered (ClientError)"

    def test_a_listing_failure_warns_for_the_kind_and_the_images_still_register(self):
        batch = _batch()
        batch.get_paginator.return_value.paginate.side_effect = _client_error("DescribeJobDefinitions")
        client = _edh_client()
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient", return_value=client) as client_cls:
            response = reg.lambda_handler(_event("Create", vdi=VDI), MagicMock())
        assert response["Data"]["warnings"] == \
            "jobDefinition: listing existing job definitions failed; 2 entries not attempted"
        assert response["Data"]["registered"] == "dcv:linux-cad"
        client_cls.assert_called_once_with(profile="batch")
        batch.register_job_definition.assert_not_called()

    def test_an_invalid_entry_is_a_warning_not_a_failed_resource(self):
        batch = _batch()
        bad = [{"name": "no-image", "vcpus": 1, "memory": 512}]
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create", job_definitions=bad), MagicMock())
        assert response["Data"]["warnings"] == "jobDefinition[0]: invalid entry"
        assert response["Data"]["registered"] == "none"
        batch.register_job_definition.assert_not_called()

    def test_a_fargate_entry_is_a_warning_and_is_not_registered(self):
        batch = _batch()
        fargate = [{"name": "sim-fargate", "imageUri": IMAGE_URI, "vcpus": 1, "memory": 2048, "platform": "FARGATE"}]
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient"):
            response = reg.lambda_handler(_event("Create", job_definitions=fargate), MagicMock())
        assert response["Data"]["warnings"] == "jobDefinition:sim-fargate: only EC2 job definitions are registered here"
        batch.register_job_definition.assert_not_called()

    def test_an_images_only_resource_never_lists_job_definitions(self):
        batch = _batch()
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient", return_value=_edh_client()):
            response = reg.lambda_handler(_event("Create", job_definitions=[], vdi=VDI), MagicMock())
        assert response["Data"]["registered"] == "dcv:linux-cad"
        batch.get_paginator.assert_not_called()

    def test_three_empty_lists_make_no_client_no_batch_call_and_still_answer_every_data_key(self):
        batch = _batch()
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient") as client_cls:
            response = reg.lambda_handler(_event("Create", job_definitions=[]), MagicMock())
        client_cls.assert_not_called()
        batch.get_paginator.assert_not_called()
        assert response["Data"] == {"registered": "none", "skipped": "none", "warnings": "none", "queued": "none"}

    def test_the_property_may_arrive_already_parsed_or_absent(self):
        batch = _batch()
        parsed = _event("Create")
        parsed["ResourceProperties"]["jobDefinitions"] = JOB_DEFINITIONS
        absent = _event("Create", vdi=VDI)
        del absent["ResourceProperties"]["jobDefinitions"]
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient", return_value=_edh_client()):
            first = reg.lambda_handler(parsed, MagicMock())
            second = reg.lambda_handler(absent, MagicMock())
        assert first["Data"]["registered"] == "jobDefinition:sim-train; jobDefinition:sim-infer"
        assert second["Data"]["registered"] == "dcv:linux-cad"
        assert batch.get_paginator.call_count == 1

    def test_delete_calls_neither_edh_nor_batch(self):
        batch = _batch()
        with patch.object(reg, "batch", batch), patch.object(reg, "EdhClient") as client_cls:
            response = reg.lambda_handler(_event("Delete", physical_id="keep-me"), MagicMock())
        client_cls.assert_not_called()
        batch.get_paginator.assert_not_called()
        assert response["PhysicalResourceId"] == "keep-me"


@pytest.mark.unit
class TestJobDefinitionRequest:
    def test_maps_an_entry_to_the_tagged_ec2_request_and_omits_an_unset_gpu(self):
        request = reg.job_definition_request(JOB_DEFINITIONS[0])
        assert request["jobDefinitionName"] == "sim-train"
        assert request["type"] == "container"
        assert request["containerProperties"] == {"image": IMAGE_URI, "resourceRequirements": [
            {"type": "VCPU", "value": "4"}, {"type": "MEMORY", "value": "16384"}]}
        assert request["platformCapabilities"] == ["EC2"]
        assert request["tags"] == {reg.EDH_VISIBILITY_TAG: reg.EDH_VISIBILITY_TAG_VALUE}

    def test_a_gpu_count_becomes_a_gpu_resource_requirement(self):
        request = reg.job_definition_request(JOB_DEFINITIONS[1])
        assert request["containerProperties"]["resourceRequirements"][-1] == {"type": "GPU", "value": "1"}

    @pytest.mark.parametrize("entry", [
        "not-an-object",
        {"imageUri": IMAGE_URI, "vcpus": 1, "memory": 512},
        {"name": "x", "imageUri": IMAGE_URI, "vcpus": 0, "memory": 512},
        {"name": "x", "imageUri": IMAGE_URI, "vcpus": 1, "memory": "big"},
        {"name": "x", "imageUri": IMAGE_URI, "vcpus": 1, "memory": 512, "gpu": -1},
    ])
    def test_a_malformed_entry_raises_value_error(self, entry):
        with pytest.raises(ValueError):
            reg.job_definition_request(entry)


@pytest.mark.unit
class TestVisibilityTagContract:
    """The tag the registration writes on a job definition is the one EDH lists by and the one the image
    repository construct writes on the ECR repository."""

    def test_the_tag_names_the_cluster_and_is_true(self):
        assert reg.EDH_VISIBILITY_TAG == TAG and reg.EDH_VISIBILITY_TAG_VALUE == "true"
        assert reg.JOB_DEFINITION_NATURAL_KEY == "name"
        assert reg.BATCH_JOB_DEFINITION_NAME_FIELD == "jobDefinitionName"

    def test_the_tag_is_the_one_the_image_repository_is_tagged_with(self):
        with open(_IMAGE_BUILD_CONSTRUCT, "r", encoding="utf-8") as handle:
            source = handle.read()
        tagging = re.search(r"add\(`edh:visibility:\$\{props\.edhClusterId\}`,\s*\"true\"\)", source)
        assert tagging, "the repository tag is not written where the guard reads it"
        assert reg.EDH_VISIBILITY_TAG == f"edh:visibility:{reg.edh_cluster_id}"


@pytest.mark.unit
class TestFixtureShapes:
    """Skipped, not failed, when the spike cluster had the containers feature off (no capture). The
    capture is a transcription: named keys are asserted, never whole bodies."""

    def test_the_job_definition_listing_capture_carries_the_fields_the_registration_reads(self):
        records = _optional_records("batch_job_definition_list.json")
        assert records, "the job-definition capture holds no records"
        for record in records:
            assert record.get(reg.BATCH_JOB_DEFINITION_NAME_FIELD)
            assert record.get(reg.BATCH_JOB_DEFINITION_ARN_FIELD, "").startswith("arn:")
            assert any(key.startswith("edh:visibility:") for key in record.get("tags", {}))
            assert record.get("containerProperties", {}).get("image")
