#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Thin Secrets Manager wrapper. Single function: fetch a SecretString by ARN."""
import boto3
from botocore.config import Config

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def get_miris_integration_key(secret_arn: str, region_name: str | None = None) -> str:
    """Return the Miris Integration Key stored as the SecretString of the given ARN.

    Raises if the secret cannot be fetched or has no SecretString. Caller is
    responsible for handling errors (and for NOT logging the returned value).
    """
    kwargs = {}
    if region_name:
        kwargs["region_name"] = region_name
    client = boto3.client("secretsmanager", **kwargs, config=retry_config)
    response = client.get_secret_value(SecretId=secret_arn)
    secret = response.get("SecretString")
    if not secret:
        raise ValueError(
            f"Secret {secret_arn!r} has no SecretString. The Miris Integration "
            "Key must be stored as a plaintext secret (not a binary)."
        )
    return secret
