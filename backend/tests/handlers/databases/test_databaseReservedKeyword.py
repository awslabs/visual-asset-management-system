# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pathlib
import re

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models.databases import CreateDatabaseRequestModel

_VALID = dict(description="A test database", defaultBucketId="123e4567-e89b-12d3-a456-426614174000")


@pytest.mark.unit
class TestGlobalReservedKeyword:
    def test_global_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(databaseId="GLOBAL", **_VALID)

    def test_global_lowercase_rejected(self):
        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(databaseId="global", **_VALID)

    def test_global_mixedcase_rejected(self):
        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(databaseId="GloBal", **_VALID)

    def test_normal_databaseid_still_allowed(self):
        m = CreateDatabaseRequestModel(databaseId="factory-db", **_VALID)
        assert m.databaseId == "factory-db"


@pytest.mark.unit
class TestPreparedDownloadsIsNotReserved:
    """Download as Zip keeps its archives under the top-level auxiliary-bucket prefix
    vams.preparedDownloads/, which a lifecycle rule expires. Every other auxiliary object lives under
    {databaseId}/, and a database id cannot contain a dot, so no database id collides with the prefix
    and none is reserved for it: a database named preparedDownloads is an ordinary database."""

    @pytest.mark.parametrize("name", ["preparedDownloads", "prepareddownloads", "PREPAREDDOWNLOADS"])
    def test_prepared_downloads_accepted(self, name):
        assert CreateDatabaseRequestModel(databaseId=name, **_VALID).databaseId == name

    @pytest.mark.parametrize("name", ["vams.preparedDownloads", "vams.prepareddownloads", "a.b"])
    def test_a_dotted_id_is_refused(self, name):
        # The property the prefix relies on.
        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(databaseId=name, **_VALID)

    def test_its_auxiliary_data_is_outside_the_lifecycle_rule_prefix(self):
        from backend.backend.common import s3PathPatterns
        from backend.backend.common.workflows import executionRecords as er
        from backend.backend.common import zipDownloads as zd

        prefix = s3PathPatterns.AUXILIARY_PREPARED_DOWNLOADS_PREFIX
        assert prefix == "vams.preparedDownloads/"
        # The prefix the CDK lifecycle rules use (PREPARED_DOWNLOADS_PREFIX) is the same literal.
        storage_ts = (pathlib.Path(__file__).resolve().parents[4] / "infra" / "lib" / "nestedStacks"
                      / "storage" / "storageBuilder-nestedStack.ts").read_text()
        assert re.search(r'export const PREPARED_DOWNLOADS_PREFIX = "([^"]*)";', storage_ts).group(1) == prefix
        own = [
            er.aux_preview_file_prefix("preparedDownloads", "xa1/model.e57") + "/PotreeViewer/r.json",
            er.aux_preview_file_prefix("preparedDownloads", "") + "/x.png",
            "preparedDownloads/xa1/model.glb/preview/thumb.png",
            "preparedDownloads/",
        ]
        for key in own:
            # S3 lifecycle filters are case-sensitive plain prefixes.
            assert not key.startswith(prefix), key
        # ...while a zip job of an asset in that database is under the rule.
        assert zd.request_prefix("preparedDownloads", "xa1", "alice", "r1").startswith(prefix)
