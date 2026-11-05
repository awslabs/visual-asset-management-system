# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared ``botocore.stub.Stubber`` helpers for the DynamoDB vector operations and Bedrock Runtime.

A hand-written fake (the house ``_boto_client`` MagicMock) answers any keyword argument, so a wrong
parameter NAME in a ``search_vectors`` call passes offline and fails only against the live service. A
``Stubber`` wraps a REAL client: botocore validates every parameter name and type against the service
model before the stub is consulted, the stubbed response is validated against the output shape when it
is registered, and ``expected_params`` pins the values the code under test must send. Use these for
one contract test per operation; keep the behaviour tests on fakes and ``tests/pagingStub.py``.

Requires botocore >= 1.43.64 for the vector operations (``tests/common/test_vector_api_floor.py``
fails first and names the cause; the clients here never skip).

Each entry of ``expected_calls`` is one expected client call, consumed in order::

    {"method": "search_vectors", "expected_params": {...} | None, "response": {...}}
    {"method": "search_vectors", "expected_params": {...} | None,
     "error": {"code": "ValidationException", "message": "...", "http_status_code": 400}}

Leaving the block without having consumed every entry raises ``StubAssertionError``; an exception
raised inside the block propagates unchanged. ``ANY`` (re-exported) stands for a top-level parameter
the test does not pin.
"""

import io
import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Sequence

import boto3
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber  # noqa: F401 -- ANY is part of this module's interface
from botocore.stub import StubAssertionError

__all__ = ["ANY", "invoke_model_response", "stubbed_bedrock_runtime", "stubbed_dynamodb"]

# One attempt per call: a stubbed error is raised once instead of consuming further stubbed responses.
_NO_RETRY = Config(retries={"max_attempts": 1, "mode": "standard"})


def _register(stubber: Stubber, expected_calls: Sequence[Dict[str, Any]]) -> None:
    for call in expected_calls:
        method = call["method"]
        expected_params = call.get("expected_params")
        if "error" in call:
            error = call["error"]
            stubber.add_client_error(
                method,
                service_error_code=error["code"],
                service_message=error.get("message", ""),
                http_status_code=error.get("http_status_code", 400),
                expected_params=expected_params,
            )
        else:
            stubber.add_response(method, call["response"], expected_params)


@contextmanager
def _stubbed(client, expected_calls: Sequence[Dict[str, Any]]) -> Iterator[Any]:
    stubber = Stubber(client)
    _register(stubber, expected_calls)
    stubber.activate()
    try:
        yield client
    except BaseException:
        stubber.deactivate()
        raise
    else:
        stubber.deactivate()
        try:
            stubber.assert_no_pending_responses()
        except AssertionError as e:
            # botocore raises a bare AssertionError here; the helper's contract is StubAssertionError.
            raise StubAssertionError(
                operation_name=client.meta.service_model.service_name, reason=str(e)
            ) from e


@contextmanager
def stubbed_dynamodb(expected_calls: Sequence[Dict[str, Any]], region_name: str = "us-east-1") -> Iterator[Any]:
    """A real low-level DynamoDB client whose calls are served by ``expected_calls``."""
    client = boto3.client("dynamodb", region_name=region_name, config=_NO_RETRY)
    with _stubbed(client, expected_calls) as stubbed:
        yield stubbed


@contextmanager
def stubbed_bedrock_runtime(expected_calls: Sequence[Dict[str, Any]], region_name: str = "us-east-1") -> Iterator[Any]:
    """A real Bedrock Runtime client whose calls are served by ``expected_calls``."""
    client = boto3.client("bedrock-runtime", region_name=region_name, config=_NO_RETRY)
    with _stubbed(client, expected_calls) as stubbed:
        yield stubbed


def invoke_model_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """A valid ``InvokeModel`` response whose ``body`` streams ``json.dumps(payload)``."""
    raw = json.dumps(payload).encode("utf-8")
    return {"body": StreamingBody(io.BytesIO(raw), len(raw)), "contentType": "application/json"}
