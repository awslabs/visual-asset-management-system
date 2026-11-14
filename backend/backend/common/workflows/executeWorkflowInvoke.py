# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The synchronous executeWorkflowV2 Invoke of the system launchers (the file-upload trigger dispatcher
and the reindex launcher), repeated only when its connection failed before the call was delivered.

The Invoke is NOT idempotent: each delivered request can start an execution. The callers' Lambda clients
therefore make one attempt (`retries={"total_max_attempts": 1}`), because botocore's retries would also
repeat a read timeout, which follows a delivered call. `invoke_execute_workflow` repeats right away, at
most CONNECTION_RETRIES times, only the failures that come before executeWorkflow has the call:

- EndpointConnectionError and ConnectTimeoutError: no connection was made, so nothing was sent.
- ConnectionClosedError (urllib3's ProtocolError): the connection closed before any response, typically
  a pooled connection the network dropped while the container was idle. urllib3 discards it, so the next
  attempt opens a new one.

In the rare case a closed connection did deliver the request, the repeat is a second launch of the same
input. Under a concurrency restriction the workflow's execution lock refuses it with a 400 (the reindex
launcher drops it; the dispatcher logs it as skipped, and executeWorkflow records it as a SKIPPED
execution); a workflow without a restriction runs it a second time.

Never repeated: a ReadTimeoutError, a broken response stream, a Lambda service error (ClientError), a
FunctionError, or any status executeWorkflow returned. The last connection failure is raised to the
caller, which handles it as it handles any other invoke failure.
"""

from typing import Any, Dict

from botocore.exceptions import ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError

# Repeats after the first attempt, so at most CONNECTION_RETRIES + 1 Invokes per launch.
CONNECTION_RETRIES = 2
CONNECTION_ERRORS = (ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError)


def invoke_execute_workflow(lambda_client, function_name: str, payload: bytes, logger) -> Dict[str, Any]:
    """One RequestResponse Invoke of `function_name`, repeated right away after a CONNECTION_ERRORS
    failure (at most CONNECTION_RETRIES repeats). Returns the Invoke response; raises the last
    connection failure, and every other failure at once."""
    attempts = CONNECTION_RETRIES + 1
    attempt = 1
    while True:
        try:
            return lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=payload)
        except CONNECTION_ERRORS as error:
            if attempt == attempts:
                raise
            logger.warning(f"executeWorkflow invoke attempt {attempt} of {attempts} failed before delivery "
                           f"({type(error).__name__}), sending it again")
            attempt += 1
