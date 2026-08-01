# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hybrid inline/S3 storage for template config bodies.

Template config bodies (`configBody` + `webFormJson`) and template tag schemas are stored inline on
their DynamoDB rows when small, and offloaded to the default asset bucket under the `pipelines/`
prefix when large. Clients (web/CLI) NEVER deal with S3: create/update handlers send the full body
inline, the offload happens here, and reads rehydrate the inline body transparently.

This module contains the pure sizing/decision/key/hash logic (no AWS SDK import) plus thin
offload/rehydrate helpers that take an injected S3 client so they stay unit-testable. The absolute
combined cap is enforced by callers via `assert_within_cap` at BOTH the API and CDK-ingestion paths.

Size budget (UTF-8 bytes):
  - INLINE_THRESHOLD_BYTES: at or below, keep inline on the row.
  - ABSOLUTE_CAP_BYTES: hard ceiling on the combined body; beyond this the request is rejected.
    The gap between the cap and the 6 MiB sync-invoke payload limit reserves headroom for the other
    row fields (ids, timestamps) and the separately-stored tag schema.
"""

import hashlib

# Keep bodies inline at or under this combined size, then offload to S3. Set well under DynamoDB's
# 400 KB per-item limit (which counts attribute names + values), reserving ~64 KB of headroom for
# the OTHER fields that live on the same template item — inputInstructions, overrides, names,
# hashes, and the long composite-key attribute names — so an at-threshold inline body plus those
# co-resident fields still fits comfortably under 400 KB.
INLINE_THRESHOLD_BYTES = 320 * 1024

# ~5 MB absolute combined ceiling for configBody + webFormJson, enforced at the API and at CDK
# ingestion. Set below the 6 MiB Lambda sync-invoke / API Gateway payload limits, leaving over 1 MiB
# of headroom for the rest of the request (JSON-string escaping of the bodies, the other template
# fields, and the separately-stored tag schema) so an at-cap body is rejected with a 400 rather than
# an opaque payload-too-large error. A body needing more than this becomes a future presigned-upload
# flow.
ABSOLUTE_CAP_BYTES = 5 * 1024 * 1024

BODY_STORAGE_INLINE = "inline"
BODY_STORAGE_S3 = "s3"


class TemplateBodyTooLargeError(Exception):
    """Raised when the combined body exceeds the absolute cap."""


def _utf8_len(text) -> int:
    """UTF-8 byte length of a string (None/empty -> 0)."""
    if not text:
        return 0
    return len(text.encode("utf-8"))


def combined_body_bytes(config_body, web_form_json) -> int:
    """Combined UTF-8 byte size of the two body fields."""
    return _utf8_len(config_body) + _utf8_len(web_form_json)


def assert_within_cap(config_body, web_form_json, cap: int = ABSOLUTE_CAP_BYTES) -> int:
    """Raise TemplateBodyTooLargeError when the combined body exceeds the cap. Returns the size."""
    size = combined_body_bytes(config_body, web_form_json)
    if size > cap:
        raise TemplateBodyTooLargeError(
            f"Combined configBody + webFormJson size {size} bytes exceeds the maximum allowed "
            f"{cap} bytes."
        )
    return size


def should_offload(config_body, web_form_json, threshold: int = INLINE_THRESHOLD_BYTES) -> bool:
    """True when the combined body exceeds the inline threshold and must go to S3."""
    return combined_body_bytes(config_body, web_form_json) > threshold


def content_hash(text) -> str:
    """Stable SHA-256 hex digest of a body (empty string for None/empty)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def config_body_s3_key(pipeline_database_id: str, pipeline_id: str, template_id: str) -> str:
    """Deterministic default-bucket key for an offloaded config body."""
    return (
        f"pipelines/templates/{pipeline_database_id}/{pipeline_id}/{template_id}/configBody"
    )


def web_form_s3_key(pipeline_database_id: str, pipeline_id: str, template_id: str) -> str:
    """Deterministic default-bucket key for an offloaded web-form JSON body."""
    return (
        f"pipelines/templates/{pipeline_database_id}/{pipeline_id}/{template_id}/webForm.json"
    )


def tag_schema_s3_key(pipeline_database_id: str, pipeline_id: str, template_id: str) -> str:
    """Deterministic default-bucket key for an offloaded tag-schema fields JSON."""
    return (
        f"pipelines/templates/{pipeline_database_id}/{pipeline_id}/{template_id}/tagSchema.json"
    )


def plan_body_storage(config_body, web_form_json):
    """Decide inline vs S3 for a template body (after the cap has been asserted separately).

    Returns a dict describing the decision:
      {bodyStorage, configBodyHash, webFormHash, offload: bool}
    Callers use `offload` to know whether to write the bodies to S3 (with the *_s3_key helpers) and
    which fields to persist on the row. Hashes are always computed so a later update can detect an
    unchanged body and skip re-uploading.
    """
    offload = should_offload(config_body, web_form_json)
    return {
        "bodyStorage": BODY_STORAGE_S3 if offload else BODY_STORAGE_INLINE,
        "configBodyHash": content_hash(config_body),
        "webFormHash": content_hash(web_form_json),
        "offload": offload,
    }


def write_body_to_s3(s3_client, bucket: str, key: str, body: str) -> None:
    """Write a UTF-8 body string to S3 (injected client keeps this unit-testable)."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=(body or "").encode("utf-8"))


def read_body_from_s3(s3_client, bucket: str, key: str) -> str:
    """Read a UTF-8 body string back from S3."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read().decode("utf-8")


def rehydrate_template_bodies(s3_client, bucket: str, row: dict) -> dict:
    """Return {configBody, webFormJson} for a template row, reading from S3 when offloaded.

    Transparent to clients: an inline row returns its inline fields; an s3 row fetches both bodies
    from the default bucket. `row` is the stored template item; `bucket` is the default asset bucket
    name.
    """
    if row.get("bodyStorage") == BODY_STORAGE_S3:
        config_body = ""
        web_form_json = ""
        if row.get("configBodyS3Key"):
            config_body = read_body_from_s3(s3_client, bucket, row["configBodyS3Key"])
        if row.get("webFormS3Key"):
            web_form_json = read_body_from_s3(s3_client, bucket, row["webFormS3Key"])
        return {"configBody": config_body, "webFormJson": web_form_json}
    return {
        "configBody": row.get("configBody", ""),
        "webFormJson": row.get("webFormJson", ""),
    }
