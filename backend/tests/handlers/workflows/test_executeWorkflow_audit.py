# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An execute forwards its metadata reads marked as internal calls, so they are recorded as cross-calls
on behalf of the caller rather than as the caller's own reads."""

import os
import sys
import types
from types import SimpleNamespace

import pytest

# The production audit module, for its real marker and request context variable (the handler under test
# imports the test tree's no-op stand-in). Imported before the handler stubs below.
from backend.backend.customLogging import auditLogging as real_audit  # noqa: E402

# executeWorkflowV2 loads these at import.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

import backend.backend.handlers.workflows.executeWorkflow as execute_svc  # noqa: E402


@pytest.mark.unit
class TestForwardedMetadataReads:
    def test_an_execute_marks_its_metadata_reads_as_internal_calls(self, monkeypatch):
        monkeypatch.setattr(execute_svc, "internal_call_marker",
                            lambda event: {"requestId": "req-1", "onBehalfOf": "alice"})
        event = {"requestContext": {"authorizer": {"lambda": {"vams:tokens": '["alice"]'}}}}
        payload = execute_svc._metadata_service_payload("/database/db1/metadata", {"databaseId": "db1"}, event)
        assert payload[execute_svc.AUDIT_INTERNAL_CALL_KEY] == {"requestId": "req-1", "onBehalfOf": "alice"}
        assert payload["requestContext"]["authorizer"] == event["requestContext"]["authorizer"]

    def test_a_cross_call_forwards_its_own_identity_unmarked(self):
        event = {"lambdaCrossCall": {"userName": "alice"}}
        payload = execute_svc._metadata_service_payload("/database/db1/metadata", {}, event)
        assert payload["lambdaCrossCall"] == {"userName": "alice"}
        assert execute_svc.AUDIT_INTERNAL_CALL_KEY not in payload


@pytest.mark.unit
class TestPooledMetadataReads:
    """The metadata reads run in _run_bounded's worker pool; each must still carry the request's marker.

    The handler module imports the test-tree auditLogging stand-in, so the real marker and the real
    request context variable are wired in from the production module; the pool itself is not patched."""

    def test_every_pooled_read_carries_the_callers_request_id(self, monkeypatch):
        monkeypatch.setattr(execute_svc, "internal_call_marker", real_audit.internal_call_marker)
        event = {"requestContext": {"authorizer": {"lambda": {"vams:tokens": '["alice"]'}}}}
        ctx = SimpleNamespace(event=event, request={"requestId": "req-1"}, actor={"userId": "alice"})
        jobs = [lambda d=database_id: execute_svc._metadata_service_payload(
            f"/database/{d}/metadata", {"databaseId": d}, event) for database_id in ("db1", "db2", "db3")]
        token = real_audit._audit_context.set(ctx)
        try:
            payloads = execute_svc._run_bounded(jobs)
        finally:
            real_audit._audit_context.reset(token)
        assert len(payloads) == 3
        first, second, third = payloads
        expected = {"requestId": "req-1", "onBehalfOf": "alice"}
        assert first[execute_svc.AUDIT_INTERNAL_CALL_KEY] == expected
        assert second[execute_svc.AUDIT_INTERNAL_CALL_KEY] == expected
        assert third[execute_svc.AUDIT_INTERNAL_CALL_KEY] == expected
        assert first["pathParameters"] == {"databaseId": "db1"}
        assert third["pathParameters"] == {"databaseId": "db3"}
