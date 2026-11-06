# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1, dispatch, the container catalog, the caller's job list, submit, kill and backend
resolution of edhContainerService.

Denials are asserted together with what was NOT consulted (no Casbin construction on an empty
token list, no EDH client on a denied request), because a MagicMock's ``enforce`` answers truthy and
a verdict-only assertion can pass for the wrong reason. EDH routes are read from the client's
constants, never spelled here. The catalog is a projection: a job definition or queue item carries
its curated fields and nothing else of the Batch record, and a definition the caller may not submit
is absent from it.
"""

import json

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_BATCH_SUBMIT_FIELDS,
    EDH_CONTAINER_JOB_ROUTES,
    EDH_CONTAINER_JOBS_ROUTES,
    EDH_EKS_JOB_UID_FIELD,
    EDH_EKS_SUBMIT_FIELDS,
    EDH_ROUTE_BATCH_JOB_DEFINITIONS,
    EDH_ROUTE_BATCH_JOB_QUEUES,
    EDH_ROUTE_EKS_CLUSTERS,
    EdhContractError,
    EdhNotFoundError,
    EdhOutcomeUnknownError,
    EdhUnavailableError,
)
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhContainerTestSupport import (
    BATCH_JOB_ID,
    CATALOG_PATH,
    EKS_CLUSTER,
    EKS_IMAGE,
    JOB_DEFINITION_ARN,
    JOBS_PATH,
    NO_IDENTITY_CLAIMS,
    SYSTEM_CLAIMS,
    USER,
    FakeEdhClient,
    batch_job,
    edh_proxy_records,
    eks_job,
    fixture_document,
    fixture_records,
    install_container_fakes,
    job_path,
    make_event,
    response_body,
)
import backend.backend.handlers.addon.edh.edhContainerService as service

UID = edh_uid_for(USER)
SUBMIT_BODY = {"jobDefinition": "sim-train", "jobQueue": "gpu-queue", "name": "render-1", "backend": "batch",
               "command": ["python", "main.py"], "environment": {"MODE": "headless"},
               "vcpus": 4, "memory": 16384, "gpu": 1}
EXPECTED_BATCH_FORM = {
    "job_definition": "sim-train", "job_queue": "gpu-queue", "job_name": "render-1",
    "overrideCommands": '["python", "main.py"]',
    "envVars": '[{"name": "MODE", "value": "headless"}]',
    "vcpus": "4", "memory": "16.0",
    "consumableResources": '[{"type": "GPU", "value": "1"}]',
}
EKS_QUERY = {"backend": "eks", "cluster": EKS_CLUSTER}
CONTAINER_OBJECT = {"object__type": "edhContainer", "edhJobDefinition": "sim-train"}
# The curated job item of each backend: nothing of the EDH record beyond these travels to a caller.
BATCH_ITEM_KEYS = {"backend", "jobId", "jobName", "status", "statusReason", "edhJobDefinition", "jobHandle",
                   "jobQueueName", "createdAt", "startedAt", "stoppedAt"}
EKS_ITEM_KEYS = {"backend", "jobId", "jobName", "status", "edhJobDefinition", "jobHandle", "cluster", "namespace",
                 "creationTimestamp"}


def _catalog_client(**results):
    defaults = {
        "list_batch_job_definitions": [{"jobDefinitionName": "sim-train", "jobDefinitionArn": "arn:a"}],
        "list_batch_queues": [{"jobQueueName": "gpu-queue", "jobQueueArn": "arn:q"}],
        "list_eks_clusters": [EKS_CLUSTER],
    }
    defaults.update(results)
    return FakeEdhClient(**defaults)


def _definition_item(**fields):
    """A curated job-definition item: every curated key present, None where the record carried nothing."""
    item = {"kind": "jobDefinition", "jobDefinitionName": None, "jobDefinitionArn": None, "revision": None,
            "status": None, "image": None, "platformCapabilities": None, "vcpus": None, "memory": None, "gpu": None}
    item.update(fields)
    return item


def _queue_item(**fields):
    item = {"kind": "jobQueue", "jobQueueName": None, "jobQueueArn": None, "state": None, "status": None}
    item.update(fields)
    return item


def _catalog_label(item):
    return item.get("jobDefinitionName") or item.get("jobQueueName") or item.get("name")


@pytest.mark.unit
class TestTierOne:
    def test_a_tier1_denial_is_403_and_reaches_no_edh_client(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)
        fakes.enforcer.enforceAPI.return_value = False

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer.enforceAPI.assert_called_once()
        fakes.client_factory.assert_not_called()

    def test_an_empty_token_list_is_denied_before_casbin_is_consulted(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, claims=NO_IDENTITY_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer_factory.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_the_system_identity_is_never_relayed_to_edh(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, claims=SYSTEM_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert response["statusCode"] == 403
        fakes.client_factory.assert_not_called()

    def test_a_permitted_call_is_not_denied_by_tier1(self, monkeypatch):
        """Control: with Tier-1 allowing, the request reaches dispatch (an unknown path answers 400)."""
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("GET", "/addon/edh/containers/elsewhere"), {})

        assert response["statusCode"] == 400
        fakes.enforcer.enforceAPI.assert_called_once()


@pytest.mark.unit
class TestDispatch:
    def test_an_unsupported_method_is_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("PUT", CATALOG_PATH), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Method not allowed"

    @pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
    def test_an_unknown_path_is_400_for_every_method(self, monkeypatch, method):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event(method, "/addon/edh/containers/elsewhere"), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Unsupported route"
        fakes.client_factory.assert_not_called()

    def test_a_rest_shaped_event_is_normalized_before_the_method_is_read(self, monkeypatch):
        """Top-level httpMethod/path and null params must not become a 500 on requestContext['http']."""
        fakes = install_container_fakes(monkeypatch, edh=_catalog_client())

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH, rest=True), {})

        assert response["statusCode"] == 200


@pytest.mark.unit
class TestCatalog:
    def test_items_are_tagged_by_kind_and_eks_is_left_out_while_disabled(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=_catalog_client(), eks_enabled=False)

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["eksEnabled"] is False
        assert body["Items"] == [
            _definition_item(jobDefinitionName="sim-train", jobDefinitionArn="arn:a"),
            _queue_item(jobQueueName="gpu-queue", jobQueueArn="arn:q"),
        ]
        assert fakes.edh.calls_to("list_eks_clusters") == []
        fakes.client_factory.assert_called_once_with(profile="api")

    def test_job_definitions_are_projected_to_curated_fields(self, monkeypatch):
        records = fixture_records("batch_job_definition_list.json")
        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_batch_job_definitions=records), eks_enabled=False)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        definitions = [item for item in body["Items"] if item["kind"] == "jobDefinition"]
        assert len(definitions) == len(records)
        record, item = records[0], definitions[0]
        properties = record["containerProperties"]
        requested = {entry["type"]: entry["value"] for entry in properties["resourceRequirements"]}
        assert item == _definition_item(
            jobDefinitionName=record["jobDefinitionName"], jobDefinitionArn=record["jobDefinitionArn"],
            revision=record["revision"], status=record["status"], image=properties["image"],
            platformCapabilities=record["platformCapabilities"],
            vcpus=int(requested["VCPU"]), memory=int(requested["MEMORY"]), gpu=None)
        # Nothing else of the record leaves: not the roles, not the tags, not the properties block.
        text = json.dumps(item)
        assert "containerProperties" not in text and "tags" not in text
        assert properties["jobRoleArn"] not in text and properties["executionRoleArn"] not in text

    def test_a_gpu_requirement_and_the_legacy_resource_keys_are_read(self, monkeypatch):
        records = [
            {"jobDefinitionName": "gpu-def", "containerProperties": {
                "image": "img:gpu", "resourceRequirements": [
                    {"type": "GPU", "value": "2"}, {"type": "VCPU", "value": "8"}, {"type": "MEMORY", "value": "32768"}]}},
            {"jobDefinitionName": "legacy-def", "containerProperties": {"image": "img:cpu", "vcpus": 2, "memory": 4096}},
            {"jobDefinitionName": "fargate-def", "containerProperties": {
                "image": "img:far", "resourceRequirements": [
                    {"type": "VCPU", "value": "0.25"}, {"type": "MEMORY", "value": "512"}]}},
        ]
        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_batch_job_definitions=records, list_batch_queues=[]), eks_enabled=False)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        # Fargate accepts a fractional vCPU, which reads as the fraction it is.
        assert [(item["jobDefinitionName"], item["image"], item["vcpus"], item["memory"], item["gpu"])
                for item in body["Items"]] == [("gpu-def", "img:gpu", 8, 32768, 2),
                                               ("legacy-def", "img:cpu", 2, 4096, None),
                                               ("fargate-def", "img:far", 0.25, 512, None)]

    def test_job_queues_are_projected(self, monkeypatch):
        records = fixture_records("batch_job_queue_list.json")
        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_batch_job_definitions=[], list_batch_queues=records), eks_enabled=False)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        record = records[0]
        assert body["Items"] == [_queue_item(
            jobQueueName=record["jobQueueName"], jobQueueArn=record["jobQueueArn"],
            state=record["state"], status=record["status"])]
        assert "computeEnvironmentOrder" not in json.dumps(body["Items"])

    def test_a_definition_the_caller_may_not_submit_is_absent_from_the_catalog(self, monkeypatch):
        definitions = [{"jobDefinitionName": "sim-train", "jobDefinitionArn": "arn:a"},
                       {"jobDefinitionName": "sim-infer", "jobDefinitionArn": "arn:b"}]
        seen = []

        def enforce(obj, act):
            seen.append((obj, act))
            return obj["edhJobDefinition"] == "sim-train"

        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_batch_job_definitions=definitions), enforce=enforce, eks_enabled=True)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        # Positive control: the allowed definition, the queue and the cluster survive; only the
        # denied definition is gone, and queues and clusters are never put to the enforcer.
        assert [(item["kind"], _catalog_label(item)) for item in body["Items"]] == [
            ("jobDefinition", "sim-train"), ("jobQueue", "gpu-queue"), ("eksCluster", EKS_CLUSTER)]
        assert seen == [
            (CONTAINER_OBJECT, "GET"),
            ({"object__type": "edhContainer", "edhJobDefinition": "sim-infer"}, "GET"),
        ]

    def test_eks_clusters_join_the_catalog_as_named_items_when_enabled(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=_catalog_client(), eks_enabled=True)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        assert body["eksEnabled"] is True
        assert {"kind": "eksCluster", "name": EKS_CLUSTER} in body["Items"]
        assert fakes.edh.calls_to("list_eks_clusters") == [()]

    def test_a_record_that_is_not_an_object_is_dropped(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_batch_queues=["garbage", None]), eks_enabled=False)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        assert [item["kind"] for item in body["Items"]] == ["jobDefinition"]

    def test_a_cluster_entry_that_is_not_a_name_is_dropped(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=_catalog_client(list_eks_clusters=["", None, {"name": "x"}, "edh-eks-b"]),
            eks_enabled=True)

        body = response_body(fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {}))

        assert [item for item in body["Items"] if item["kind"] == "eksCluster"] == [
            {"kind": "eksCluster", "name": "edh-eks-b"}]

    def test_every_listing_is_audited_with_its_route(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=_catalog_client(), eks_enabled=True)

        fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhUid"], r["edhStatus"])
                for r in edh_proxy_records(fakes)] == [
            ("containerCatalog", "GET", EDH_ROUTE_BATCH_JOB_DEFINITIONS, UID, "ok"),
            ("containerCatalog", "GET", EDH_ROUTE_BATCH_JOB_QUEUES, UID, "ok"),
            ("containerCatalog", "GET", EDH_ROUTE_EKS_CLUSTERS, UID, "ok"),
        ]

    def test_an_edh_failure_maps_through_the_typed_error_response(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch,
            edh=_catalog_client(list_batch_job_definitions=EdhUnavailableError("upstream said something")))

        response = fakes.module.lambda_handler(make_event("GET", CATALOG_PATH), {})

        assert response["statusCode"] == 502
        assert "upstream said something" not in response["body"]
        assert [r["edhStatus"] for r in edh_proxy_records(fakes)] == ["EdhUnavailableError"]


@pytest.mark.unit
class TestBackendResolution:
    def test_an_omitted_backend_is_batch(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("GET", JOBS_PATH), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("list_jobs") == [(UID, "batch")]

    def test_an_unknown_backend_is_400_before_any_edh_call(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("GET", JOBS_PATH, query={"backend": "slurm"}), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "VAMS General Error: backend must be one of: batch, eks"
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()

    def test_eks_is_400_while_the_deployment_has_not_enabled_it(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, eks_enabled=False)

        response = fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query=EKS_QUERY), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == \
            "VAMS General Error: The eks backend is not enabled in this deployment"
        assert fakes.edh.calls == []

    def test_eks_reaches_the_eks_listing_with_its_cluster_and_namespace_when_enabled(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, eks_enabled=True)

        fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query=EKS_QUERY), {})

        assert fakes.edh.calls_to("list_jobs") == [(UID, "eks", EKS_CLUSTER, "default")]
        assert [r["edhRoute"] for r in edh_proxy_records(fakes)] == [EDH_CONTAINER_JOBS_ROUTES["eks"]]

    def test_eks_without_a_cluster_is_400_before_any_edh_call(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, eks_enabled=True)

        response = fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query={"backend": "eks"}), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "VAMS General Error: cluster is required for the eks backend"
        assert fakes.edh.calls == []


@pytest.mark.unit
class TestJobsList:
    def test_batch_items_carry_the_id_name_status_definition_and_handle_and_are_filtered_per_item(self, monkeypatch):
        other_arn = "arn:aws:batch:us-east-1:123456789012:job-definition/sim-infer:1"
        jobs = [batch_job(), batch_job(jobId="j-2", jobName="render-2", jobDefinition=other_arn)]
        seen = []

        def enforce(obj, act):
            seen.append((obj, act))
            return obj["edhJobDefinition"] == "sim-train"

        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=jobs), enforce=enforce)

        body = response_body(fakes.module.lambda_handler(make_event("GET", JOBS_PATH), {}))

        assert len(body["Items"]) == 1
        item = body["Items"][0]
        assert (item["jobId"], item["jobName"], item["status"]) == (BATCH_JOB_ID, "render-1", "RUNNING")
        assert (item["backend"], item["edhJobDefinition"], item["jobHandle"]) == \
            ("batch", "sim-train", f"batch:{BATCH_JOB_ID}")
        # The curated shape: the queue and timing fields travel; the raw definition ARN, the tags and
        # the rest of the describe record do not.
        assert (item["jobQueueName"], item["createdAt"], item["startedAt"], item["statusReason"]) == \
            ("gpu-queue", 1789041600000, None, None)
        assert set(item) == BATCH_ITEM_KEYS
        assert JOB_DEFINITION_ARN not in json.dumps(item)
        assert seen == [
            (CONTAINER_OBJECT, "GET"),
            ({"object__type": "edhContainer", "edhJobDefinition": "sim-infer"}, "GET"),
        ]

    def test_a_batch_records_container_section_and_its_environment_are_not_relayed(self, monkeypatch):
        # The describe record echoes the environment the job was submitted with; a listing returns none of it.
        job = batch_job(container={"image": "img", "environment": [{"name": "DB_PASSWORD", "value": "s3cr3t-value"}]},
                        attempts=[{"container": {"logStreamName": "stream-1"}}])
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[job]))

        response = fakes.module.lambda_handler(make_event("GET", JOBS_PATH), {})

        assert set(response_body(response)["Items"][0]) == BATCH_ITEM_KEYS
        assert "s3cr3t-value" not in response["body"] and "stream-1" not in response["body"]

    def test_an_eks_records_spec_is_not_relayed_and_its_creation_timestamp_is(self, monkeypatch):
        job = eks_job("render-eks", spec={"template": {"spec": {"containers": [
            {"env": [{"name": "DB_PASSWORD", "value": "s3cr3t-value"}]}]}}})
        job["metadata"]["creation_timestamp"] = "2026-09-10 12:00:00+00:00"
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[job]), eks_enabled=True)

        response = fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query=EKS_QUERY), {})

        item = response_body(response)["Items"][0]
        assert set(item) == EKS_ITEM_KEYS
        assert item["creationTimestamp"] == "2026-09-10 12:00:00+00:00"
        assert "s3cr3t-value" not in response["body"] and "pod_ips" not in response["body"]

    def test_the_listing_is_user_scoped_and_audited(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()]))

        fakes.module.lambda_handler(make_event("GET", JOBS_PATH), {})

        assert fakes.edh.calls_to("list_jobs") == [(UID, "batch")]
        assert edh_proxy_records(fakes) == [{
            "operation": "containerJobsList", "edhUid": UID, "edhMethod": "GET",
            "edhRoute": EDH_CONTAINER_JOBS_ROUTES["batch"], "edhStatus": "ok"}]

    def test_records_that_are_not_objects_are_dropped(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=["garbage", batch_job()]))

        body = response_body(fakes.module.lambda_handler(make_event("GET", JOBS_PATH), {}))

        assert len(body["Items"]) == 1

    def test_eks_items_are_named_by_the_job_and_limited_to_the_requested_cluster_and_namespace(self, monkeypatch):
        jobs = [eks_job("render-eks"), eks_job("elsewhere", cluster="edh-eks-b"), eks_job("team-job", namespace="team")]
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=jobs), eks_enabled=True)

        body = response_body(fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query=EKS_QUERY), {}))

        assert [item["jobId"] for item in body["Items"]] == ["render-eks"]
        item = body["Items"][0]
        assert (item["jobName"], item["status"], item["backend"]) == ("render-eks", "RUNNING", "eks")
        assert (item["edhJobDefinition"], item["cluster"], item["namespace"]) == (EKS_IMAGE, EKS_CLUSTER, "default")
        assert item["jobHandle"] == f"eks:{EKS_CLUSTER}/default/render-eks"

    @pytest.mark.parametrize("status,expected", [
        ({"active": 1}, "RUNNING"),
        ({"succeeded": 1}, "SUCCEEDED"),
        ({"failed": 2}, "FAILED"),
        ({}, "PENDING"),
    ])
    def test_an_eks_job_status_word_follows_the_kubernetes_counters(self, monkeypatch, status, expected):
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(list_jobs=[eks_job(status=status)]), eks_enabled=True)

        body = response_body(fakes.module.lambda_handler(make_event("GET", JOBS_PATH, query=EKS_QUERY), {}))

        assert body["Items"][0]["status"] == expected


@pytest.mark.unit
class TestSubmit:
    def test_a_valid_batch_body_submits_the_edh_form_and_answers_202(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 202
        body = response_body(response)
        assert (body["jobId"], body["jobHandle"]) == (BATCH_JOB_ID, f"batch:{BATCH_JOB_ID}")
        assert (body["backend"], body["jobDefinition"], body["jobQueue"], body["name"]) == \
            ("batch", "sim-train", "gpu-queue", "render-1")
        assert "submitted successfully" in body["message"]
        assert "cluster" not in body
        assert fakes.edh.calls == [("submit_job", (UID, "batch", EXPECTED_BATCH_FORM))]

    def test_the_eks_body_maps_the_image_the_cluster_the_cpu_and_the_namespace(self, monkeypatch):
        submitted = {"eks_cluster": EKS_CLUSTER, "namespace": "default", "job_name": "render-eks",
                     "job_handle": f"eks:{EKS_CLUSTER}/default/render-eks",
                     "message": "Job render-eks submitted successfully."}
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(submit_job=submitted), eks_enabled=True)
        body = {"jobDefinition": EKS_IMAGE, "jobQueue": EKS_CLUSTER, "name": "render-eks", "backend": "eks",
                "vcpus": 2, "memory": 4096, "gpu": 1, "environment": {"MODE": "headless"}}

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=body), {})

        assert response["statusCode"] == 202
        assert fakes.edh.calls_to("submit_job") == [(UID, "eks", {
            "image_uri": EKS_IMAGE, "eks_cluster": EKS_CLUSTER, "job_name": "render-eks",
            "envVars": '[{"name": "MODE", "value": "headless"}]',
            "cpu": "2", "memory": "4.0", "gpu": "1", "namespace": "default"})]
        answer = response_body(response)
        assert (answer["jobId"], answer["cluster"], answer["namespace"]) == ("render-eks", EKS_CLUSTER, "default")

    def test_tier2_is_enforced_from_the_body_before_any_edh_call(self, monkeypatch):
        seen = []

        def enforce(obj, act):
            seen.append((obj, act))
            return False

        fakes = install_container_fakes(monkeypatch, enforce=enforce)

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 403
        assert seen == [(CONTAINER_OBJECT, "POST")]
        assert fakes.edh.calls == []

    def test_an_arn_job_definition_is_authorized_on_its_name_and_sent_as_given(self, monkeypatch):
        seen = []

        def enforce(obj, act):
            seen.append((obj, act))
            return True

        fakes = install_container_fakes(monkeypatch, enforce=enforce)

        fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "jobDefinition": JOB_DEFINITION_ARN}), {})

        assert seen == [(CONTAINER_OBJECT, "POST")]
        assert fakes.edh.calls_to("submit_job")[0][2]["job_definition"] == JOB_DEFINITION_ARN

    def test_an_invalid_body_is_400_without_reaching_edh(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={"name": "render-1", "backend": "batch"}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_a_non_json_body_is_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body="not json"), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "VAMS General Error: Request body must be a JSON object"

    def test_an_unknown_backend_in_the_body_is_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "backend": "slurm"}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_eks_in_the_body_is_400_while_disabled(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, eks_enabled=False)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "backend": "eks"}), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == \
            "VAMS General Error: The eks backend is not enabled in this deployment"
        assert fakes.edh.calls == []

    def test_a_missing_job_queue_is_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)
        body = {key: value for key, value in SUBMIT_BODY.items() if key != "jobQueue"}

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=body), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == \
            "VAMS General Error: jobQueue is required: a Batch job queue, or the EKS cluster for the eks backend"
        assert fakes.edh.calls == []

    @pytest.mark.parametrize("backend,name,rule", [
        ("batch", "render.1", service.JOB_NAME_RULES["batch"]),
        ("batch", "-render", service.JOB_NAME_RULES["batch"]),
        ("batch", "_render", service.JOB_NAME_RULES["batch"]),
        ("batch", "r" * 129, service.JOB_NAME_RULES["batch"]),
        ("eks", "Render-EKS", service.JOB_NAME_RULES["eks"]),
    ])
    def test_a_name_the_backend_would_refuse_is_400_here(self, monkeypatch, backend, name, rule):
        fakes = install_container_fakes(monkeypatch, eks_enabled=True)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "backend": backend, "name": name}), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == f"VAMS General Error: {rule}"
        assert fakes.edh.calls == []

    def test_a_batch_name_is_accepted_up_to_the_rules_length(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)
        name = "R0_-" + "r" * 124

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "name": name}), {})

        assert response["statusCode"] == 202
        assert fakes.edh.calls == [("submit_job", (UID, "batch", {**EXPECTED_BATCH_FORM, "job_name": name}))]

    def test_an_environment_name_batch_reserves_is_400_here(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "environment": {"AWS_BATCH_CE_NAME": "x"}}), {})

        assert response["statusCode"] == 400
        assert "environment names cannot start with AWS_BATCH for the batch backend" in \
            response_body(response)["message"]
        assert fakes.edh.calls == []

    def test_memory_below_the_edh_minimum_is_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("POST", JOBS_PATH, body={**SUBMIT_BODY, "memory": 256}), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "VAMS General Error: memory must be at least 512 MiB"
        assert fakes.edh.calls == []

    def test_the_submit_is_audited_and_an_edh_failure_maps(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(submit_job=EdhUnavailableError("queue full")))

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 502
        assert "queue full" not in response["body"]
        assert "outcomeUnknown" not in response_body(response)
        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhStatus"])
                for r in edh_proxy_records(fakes)] == [
            ("containerJobSubmit", "POST", EDH_CONTAINER_JOB_ROUTES["batch"], "EdhUnavailableError"),
        ]

    def test_a_submit_edh_did_not_answer_is_a_502_flagged_as_an_unknown_outcome(self, monkeypatch):
        # The job may have been queued, so the caller is told to refresh rather than that the submit failed.
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(submit_job=EdhOutcomeUnknownError("EDH did not answer POST")))

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 502
        assert response_body(response) == {
            "message": ("EDH did not confirm the request in time; it may still be in progress. "
                        "Refresh before trying again."),
            "outcomeUnknown": True,
        }

    def test_an_accepted_job_whose_answer_cannot_be_read_is_a_502_not_a_rejection(self, monkeypatch):
        # EDH submitted the job before composing the prose the client could not read; a 400 would
        # invite a duplicate submission, so the caller is told to check the job list first.
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(submit_job=EdhContractError("EDH accepted the job without returning its id",
                                                                        status=200, body_text="Job ID: unknown",
                                                                        job_submitted=True)))

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 502
        # The job may be running, so the answer is flagged as an outcome EDH did not confirm.
        assert response_body(response) == {
            "message": ("EDH accepted the request but returned an unreadable answer. "
                        "Check the job list before retrying."),
            "outcomeUnknown": True,
        }
        assert "Job ID" not in response["body"]

    def test_an_unexpected_failure_is_audited_and_is_a_500(self, monkeypatch):
        """A wrapper failure that is not a typed EDH error still leaves an edhProxy record before the 500."""
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(submit_job=ValueError("boom")))

        response = fakes.module.lambda_handler(make_event("POST", JOBS_PATH, body=SUBMIT_BODY), {})

        assert response["statusCode"] == 500
        assert "boom" not in response["body"]
        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhStatus"])
                for r in edh_proxy_records(fakes)] == [
            ("containerJobSubmit", "POST", EDH_CONTAINER_JOB_ROUTES["batch"], "ValueError"),
        ]


@pytest.mark.unit
class TestKill:
    def test_a_listed_batch_job_is_killed_by_its_handle_and_answers_200(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()]))

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path(BATCH_JOB_ID), path_params={"jobId": BATCH_JOB_ID},
                       query={"backend": "batch"}), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["jobId"], body["backend"]) == (BATCH_JOB_ID, "batch")
        assert "terminated successfully" in body["message"]
        assert fakes.edh.calls_to("kill_job") == [(UID, "batch", f"batch:{BATCH_JOB_ID}")]

    def test_a_listed_eks_job_is_killed_by_its_cluster_namespace_and_name(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(list_jobs=[eks_job("render-eks")],
                                           kill_job={"message": "Job render-eks deleted successfully."}),
            eks_enabled=True)

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path("render-eks"), path_params={"jobId": "render-eks"}, query=EKS_QUERY), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["jobId"], body["backend"], body["cluster"], body["namespace"]) == \
            ("render-eks", "eks", EKS_CLUSTER, "default")
        assert fakes.edh.calls_to("kill_job") == [(UID, "eks", f"eks:{EKS_CLUSTER}/default/render-eks")]

    def test_an_id_absent_from_the_callers_listing_is_404_without_killing(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()]))

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path("j-other"), path_params={"jobId": "j-other"}), {})

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Job not found"
        assert fakes.edh.calls_to("kill_job") == []

    def test_a_tier2_denial_is_403_without_killing(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()]), enforce=lambda obj, act: False)

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path(BATCH_JOB_ID), path_params={"jobId": BATCH_JOB_ID}), {})

        assert response["statusCode"] == 403
        fakes.enforcer.enforce.assert_called_once_with(CONTAINER_OBJECT, "DELETE")
        assert fakes.edh.calls_to("kill_job") == []

    def test_an_invalid_path_id_is_400_before_any_edh_call(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path("bad id"), path_params={"jobId": "bad id"}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_null_path_parameters_on_a_rest_event_are_400(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path("j-1"), path_params=None, rest=True), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_an_eks_kill_without_a_cluster_is_400_before_any_edh_call(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, eks_enabled=True)

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path("render-eks"), path_params={"jobId": "render-eks"},
                       query={"backend": "eks"}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_the_kill_is_audited_after_the_listing(self, monkeypatch):
        fakes = install_container_fakes(monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()]))

        fakes.module.lambda_handler(
            make_event("DELETE", job_path(BATCH_JOB_ID), path_params={"jobId": BATCH_JOB_ID}), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"]) for r in edh_proxy_records(fakes)] == [
            ("containerJobKill", "GET", EDH_CONTAINER_JOBS_ROUTES["batch"]),
            ("containerJobKill", "DELETE", EDH_CONTAINER_JOB_ROUTES["batch"]),
        ]

    def test_edh_not_found_on_kill_maps_to_the_typed_response(self, monkeypatch):
        fakes = install_container_fakes(
            monkeypatch, edh=FakeEdhClient(list_jobs=[batch_job()], kill_job=EdhNotFoundError("gone")))

        response = fakes.module.lambda_handler(
            make_event("DELETE", job_path(BATCH_JOB_ID), path_params={"jobId": BATCH_JOB_ID}), {})

        assert response["statusCode"] == 404
        assert "gone" not in response["body"]


@pytest.mark.unit
class TestFixtureShapes:
    """The record and form keys this module reads or writes, checked against the sanitized EDH
    captures so a renamed EDH field fails here rather than as an empty catalog in production. The
    captures are transcriptions: named keys are asserted, never whole bodies. Skipped, not failed,
    when the spike cluster had the feature off."""

    def test_the_job_definition_capture_names_each_record(self):
        records = fixture_records("batch_job_definition_list.json")
        assert records, "the job-definition capture holds no records"
        assert all(record.get(service.EDH_FIELD_JOB_DEFINITION_NAME) for record in records)

    def test_the_job_queue_capture_names_each_record(self):
        records = fixture_records("batch_job_queue_list.json")
        assert records, "the job-queue capture holds no records"
        assert all(record.get(service.EDH_FIELD_JOB_QUEUE_NAME) for record in records)

    def test_the_job_definition_capture_carries_the_role_arns_the_projection_drops(self):
        """The projection is load-bearing: the records EDH lists do carry IAM role ARNs."""
        records = fixture_records("batch_job_definition_list.json")
        assert records, "the job-definition capture holds no records"
        for record in records:
            properties = record[service.EDH_FIELD_CONTAINER_PROPERTIES]
            assert properties["jobRoleArn"].startswith("arn:")
            assert properties[service.EDH_FIELD_CONTAINER_IMAGE]

    def test_the_jobs_capture_carries_the_id_and_a_definition_that_reduces_to_a_name(self):
        records = fixture_records("batch_jobs_list.json")
        assert records, "the jobs capture holds no records"
        first = records[0]
        assert first.get(service.EDH_FIELD_BATCH_JOB_ID)
        assert service.EDH_FIELD_BATCH_JOB_DEFINITION in first
        name = service._job_definition_name(first[service.EDH_FIELD_BATCH_JOB_DEFINITION])
        assert name and ":" not in name and "/" not in name

    def test_the_batch_submit_form_this_module_writes_is_what_the_client_and_the_capture_accept(self):
        sent = set(fixture_document("batch_job_submit.json")["request"]["body"])
        assert {"job_name", "job_definition", "job_queue"} <= sent
        assert set(service.EDH_SUBMIT_FIELDS_BY_BACKEND["batch"].values()) <= EDH_BATCH_SUBMIT_FIELDS

    def test_the_cluster_capture_is_a_list_of_names(self):
        records = fixture_records("eks_clusters_list.json")
        assert records, "the cluster capture holds no records"
        assert all(isinstance(record, str) and record for record in records)

    def test_the_eks_submit_form_this_module_writes_is_what_the_client_and_the_capture_accept(self):
        sent = set(fixture_document("eks_job_submit.json")["request"]["body"])
        assert {"image_uri", "eks_cluster", "job_name"} <= sent
        assert set(service.EDH_SUBMIT_FIELDS_BY_BACKEND["eks"].values()) | {"namespace"} <= EDH_EKS_SUBMIT_FIELDS

    def test_the_eks_jobs_capture_yields_items_named_by_the_job_and_its_image(self):
        records = fixture_records("eks_jobs_list.json", key_field=EDH_EKS_JOB_UID_FIELD)
        assert records, "the EKS jobs capture holds no records"
        record = records[0]
        item = service._job_item(record, "eks", {"cluster": record["eks_cluster"], "namespace": "default"})
        assert item["jobId"] == record["metadata"]["name"]
        assert item["edhJobDefinition"] == record["images"][0]
        assert item["cluster"] == record["eks_cluster"]
        assert item["creationTimestamp"] == record["metadata"][service.EDH_FIELD_EKS_CREATION_TIMESTAMP]

    def test_the_jobs_capture_carries_the_queue_and_timing_fields_the_item_names(self):
        first = fixture_records("batch_jobs_list.json")[0]
        item = service._job_item(first, "batch", None)
        assert item["jobQueueName"] == first[service.EDH_FIELD_BATCH_JOB_QUEUE_NAME]
        assert item["createdAt"] == first[service.EDH_FIELD_BATCH_JOB_CREATED_AT]
        assert item["startedAt"] == first[service.EDH_FIELD_BATCH_JOB_STARTED_AT]
        # The capture's record carries a container section; the item does not.
        assert "container" in first and "container" not in item
