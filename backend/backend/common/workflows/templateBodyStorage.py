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
  - MAX_ITEM_BYTES: DynamoDB's per-item ceiling. `assert_row_within_item_limit` measures the
    assembled row against it, so an over-limit item is a 400 from the handler rather than a
    ValidationException from put_item.
"""

import hashlib
from decimal import Decimal

# Keep bodies inline at or under this combined size, then offload to S3. Set under DynamoDB's 400 KB
# per-item limit (which counts attribute names + values) with room for the OTHER fields that live on
# the same template item: overrides at MAX_CONFIG_BLOCK_BYTES (64 KB), the four bounded free-text
# fields at their declared maxima counted as UTF-8 rather than code points (templateId 64 +
# templateName 256 + description 1024 + inputInstructions 4096 code points, up to 4 bytes each =
# ~21 KB), the two sha256 hex hashes, the composite key, createdBy/modifiedBy, and the attribute
# names. Those sum to ~91 KB, so an at-threshold inline body leaves ~11 KB of margin under 400 KB.
INLINE_THRESHOLD_BYTES = 300 * 1024

# ~5 MB absolute combined ceiling for configBody + webFormJson, enforced at the API and at CDK
# ingestion. Set below the 6 MiB Lambda sync-invoke / API Gateway payload limits, leaving over 1 MiB
# of headroom for the rest of the request (JSON-string escaping of the bodies, the other template
# fields, and the separately-stored tag schema) so an at-cap body is rejected with a 400 rather than
# an opaque payload-too-large error. A body needing more than this becomes a future presigned-upload
# flow.
ABSOLUTE_CAP_BYTES = 5 * 1024 * 1024

# DynamoDB rejects an item larger than 400 KB, counting attribute names plus values. The reserve
# absorbs the difference between the estimate below and the service's own accounting.
MAX_ITEM_BYTES = 400 * 1024
ITEM_SIZE_RESERVE_BYTES = 4 * 1024

BODY_STORAGE_INLINE = "inline"
BODY_STORAGE_S3 = "s3"


class TemplateBodyTooLargeError(Exception):
    """Raised when the combined body exceeds the absolute cap."""


class TemplateRowTooLargeError(Exception):
    """Raised when an assembled template row exceeds DynamoDB's per-item limit."""


def _utf8_len(text) -> int:
    """UTF-8 byte length of a string (None/empty -> 0)."""
    if not text:
        return 0
    return len(text.encode("utf-8"))


def _attribute_bytes(value) -> int:
    """Size of one attribute VALUE under DynamoDB's accounting.

    Strings and binary count their raw length; a number counts its documented 21-byte upper bound; a
    map or list counts 3 bytes plus its entries, each entry costing 1 byte plus the key name and the
    nested value."""
    if isinstance(value, bool):
        return 1
    if isinstance(value, str):
        return _utf8_len(value)
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (int, float, Decimal)):
        return 21
    if value is None:
        return 1
    if isinstance(value, dict):
        return 3 + sum(_utf8_len(str(key)) + _attribute_bytes(item) + 1
                       for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return 3 + sum(_attribute_bytes(item) + 1 for item in value)
    return _utf8_len(str(value))


def item_bytes(item) -> int:
    """Estimated stored size of a DynamoDB item: every attribute name plus its value."""
    return sum(_utf8_len(str(name)) + _attribute_bytes(value) for name, value in item.items())


def assert_row_within_item_limit(
        row, max_bytes: int = MAX_ITEM_BYTES - ITEM_SIZE_RESERVE_BYTES) -> int:
    """Raise TemplateRowTooLargeError when an assembled row exceeds the per-item limit.

    The inline threshold reserves headroom for the fields that share the row rather than measuring
    them, and the update path grows those fields one request at a time, so the assembled row is
    measured before the write. Returns the measured size."""
    size = item_bytes(row)
    if size > max_bytes:
        raise TemplateRowTooLargeError(
            f"The template record is {size} bytes, over the {max_bytes} byte storage limit. Reduce "
            f"the configuration body, the input instructions, or the overrides block."
        )
    return size


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


def rehydrate_template_bodies(s3_client, bucket: str, row: dict, key_fn=None) -> dict:
    """Return {configBody, webFormJson} for a template row, reading from S3 when offloaded.

    Transparent to clients: an inline row returns its inline fields; an s3 row fetches both bodies
    from the default bucket. `row` is the stored template item; `bucket` is the default asset bucket
    name. The row's keys are relative to the area VAMS owns inside that bucket, so `key_fn` maps a
    stored key to the full bucket key (defaultBucket.default_bucket_key bound to the resolved default
    bucket); it is called only for a row that actually carries an offloaded body.
    """
    if row.get("bodyStorage") == BODY_STORAGE_S3:
        resolve_key = key_fn or (lambda key: key)
        config_body = ""
        web_form_json = ""
        if row.get("configBodyS3Key"):
            config_body = read_body_from_s3(s3_client, bucket, resolve_key(row["configBodyS3Key"]))
        if row.get("webFormS3Key"):
            web_form_json = read_body_from_s3(s3_client, bucket, resolve_key(row["webFormS3Key"]))
        return {"configBody": config_body, "webFormJson": web_form_json}
    return {
        "configBody": row.get("configBody", ""),
        "webFormJson": row.get("webFormJson", ""),
    }
