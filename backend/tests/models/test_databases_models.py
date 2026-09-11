# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


@pytest.mark.unit
class TestCreateDatabaseRequestModel:
    def _valid_body(self, database_id):
        return {
            "databaseId": database_id,
            "description": "A test database",
            "defaultBucketId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        }

    def test_accepts_normal_id(self):
        from models.databases import CreateDatabaseRequestModel
        model = parse(self._valid_body("building-scans"), model=CreateDatabaseRequestModel)
        assert model.databaseId == "building-scans"

    @pytest.mark.parametrize("reserved_id", [
        "pipeline", "pipelines", "preview", "previews",
        "temp-upload", "temp-uploads", "workspace", "workspaces",
    ])
    def test_rejects_reserved_keyword(self, reserved_id):
        from models.databases import CreateDatabaseRequestModel
        with pytest.raises(ValidationError):
            parse(self._valid_body(reserved_id), model=CreateDatabaseRequestModel)

    @pytest.mark.parametrize("reserved_id", ["Pipelines", "PREVIEW", "Workspaces"])
    def test_rejects_reserved_keyword_case_insensitive(self, reserved_id):
        from models.databases import CreateDatabaseRequestModel
        with pytest.raises(ValidationError):
            parse(self._valid_body(reserved_id), model=CreateDatabaseRequestModel)
