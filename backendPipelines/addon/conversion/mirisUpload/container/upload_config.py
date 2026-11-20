#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Resolution of the per-run Miris upload configuration.

The pipeline receives an ALREADY-RENDERED configuration file: the workflow substitutes the
template's tag values before the container starts, so nothing here renders or re-reads tags.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# Tag prefixes the pipeline writes itself to bind the Miris asset to its VAMS asset and database.
# A configured tag with one of them could bind the Miris asset to another VAMS asset, so it is dropped.
RESERVED_TAG_PREFIXES = ("vams-asset-", "vams-database-")


@dataclass
class UploadConfig:
    asset_name: str = ""
    tags: List[str] = field(default_factory=list)


def _fetch_rendered_config(uri, s3_client):
    """The parsed configuration object, or {} when there is none to read."""
    if not uri:
        return {}
    bucket, _, key = uri.replace("s3://", "").partition("/")
    if not bucket or not key:
        return {}
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body.decode("utf-8")) or {}
    except Exception as e:
        # An unreadable configuration is not fatal: every field it carries is optional and has a
        # fallback, so the upload proceeds with the VAMS-derived defaults.
        logger.warning(f"Could not read rendered configuration {uri}: {e}")
        return {}


def resolve_upload_config(stage, packaged_filename, s3_client):
    """Effective Miris asset name and tags for this run.

    Asset-name precedence: the operator's template value, then the VAMS asset name threaded from
    vamsExecute, then the packaged file's basename.
    """
    rendered = _fetch_rendered_config(stage.get("inputConfigurationS3Location", ""), s3_client)

    asset_name = (
        (rendered.get("mirisAssetName") or "").strip()
        or (stage.get("mirisAssetName") or "").strip()
        or os.path.splitext(os.path.basename(packaged_filename or ""))[0]
    )

    tags = rendered.get("mirisTags") or []
    if not isinstance(tags, list):
        logger.warning(f"Ignoring non-list mirisTags of type {type(tags).__name__}")
        tags = []

    return UploadConfig(asset_name=asset_name, tags=_without_reserved_tags(str(t) for t in tags))


def _without_reserved_tags(tags):
    """The configured tags, less any that use a reserved binding prefix (compared case-insensitively)."""
    kept = []
    for tag in tags:
        if tag.strip().lower().startswith(RESERVED_TAG_PREFIXES):
            logger.warning(f"Dropping configured Miris tag {tag!r}: the vams-asset- and "
                           "vams-database- prefixes are reserved for the pipeline's binding tags")
            continue
        kept.append(tag)
    return kept
