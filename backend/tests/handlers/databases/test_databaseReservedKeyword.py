# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

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
