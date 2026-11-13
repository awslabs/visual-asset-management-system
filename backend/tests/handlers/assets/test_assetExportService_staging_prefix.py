# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Staged export payloads are keyed under a prefix no database ID can take.

An export response too large to return inline is written to the auxiliary bucket and returned as
a presigned URL. The bucket's lifecycle rules expire everything under the staging prefix, and the
same bucket keys each database's preview and viewer data under `{databaseId}/`. A staging prefix
whose first segment is a valid database ID would therefore let the expiry delete that database's
auxiliary data. The prefix carries a character the database ID pattern rejects, and the payload is
written under it as `<prefix><databaseId>/<assetId>/<32 hex>.json`.

The ID pattern is read from `common/validators.py` source, because the root conftest may install a
mock `common.validators`.
"""

import os
import re
from unittest.mock import patch

from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)

_VALIDATORS_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "validators.py"
)


def _database_id_pattern() -> str:
    with open(os.path.abspath(_VALIDATORS_SOURCE), encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(r"^id_pattern = r'([^']+)'$", source, re.MULTILINE)
    assert match, "id_pattern not found in common/validators.py"
    return match.group(1)


def test_database_id_pattern_is_read():
    # Control: the pattern accepts an ordinary ID and the bare word, so the guard below is real.
    pattern = _database_id_pattern()
    assert re.fullmatch(pattern, "assetExports")
    assert re.fullmatch(pattern, _DB)


def test_staging_prefix_cannot_be_a_database_id():
    module = _load_asset_export_service()
    prefix = module.EXPORT_STAGING_PREFIX
    assert prefix == "vams.assetExports/"
    first_segment = prefix.split("/", 1)[0]
    assert re.fullmatch(_database_id_pattern(), first_segment) is None


def test_payload_is_staged_under_the_prefix():
    module = _load_asset_export_service()
    with patch.object(module, "s3_client") as s3, patch.object(
        module, "asset_auxiliary_bucket_name", "aux-bucket"
    ), patch.object(module, "presigned_url_timeout", "86400"):
        s3.generate_presigned_url.return_value = "https://example.invalid/presigned"
        url = module.stage_export_payload(b"{}", _DB, _ASSET)

    assert url == "https://example.invalid/presigned"
    key = s3.put_object.call_args.kwargs["Key"]
    assert re.fullmatch(rf"vams\.assetExports/{_DB}/{_ASSET}/[0-9a-f]{{32}}\.json", key)
    assert s3.put_object.call_args.kwargs["Bucket"] == "aux-bucket"
    signed = s3.generate_presigned_url.call_args
    assert signed.args[0] == "get_object"
    assert signed.kwargs["Params"] == {"Bucket": "aux-bucket", "Key": key}
    assert signed.kwargs["ExpiresIn"] == 86400
