# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError

from backend.backend.models import tag as tag_module
from backend.backend.models.tag import (
    CreateTagRequestModel, CreateTagTypeRequestModel, TagResponseModel,
)


def _load_real_validate():
    """The repo conftest replaces common.validators.validate with a stub that
    always returns (True, ""). Load the real validate() by file path so the
    databaseId/GLOBAL validation is actually exercised in these model tests."""
    validators_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "validators.py"
    )
    spec = importlib.util.spec_from_file_location("_real_tag_validators", validators_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


@pytest.fixture(autouse=True)
def _use_real_validate(monkeypatch):
    monkeypatch.setattr(tag_module, "validate", _load_real_validate())


@pytest.mark.unit
class TestTagDatabaseIdField:
    def test_databaseid_defaults_to_none(self):
        m = CreateTagRequestModel(tagName="Status", description="A status tag", tagTypeName="System")
        assert m.databaseId is None

    def test_valid_databaseid_accepted(self):
        m = CreateTagRequestModel(
            tagName="EquipID", description="Equipment ID", tagTypeName="Custom",
            databaseId="factory-db",
        )
        assert m.databaseId == "factory-db"

    def test_global_keyword_accepted(self):
        m = CreateTagRequestModel(
            tagName="Status", description="A status tag", tagTypeName="System",
            databaseId="GLOBAL",
        )
        assert m.databaseId == "GLOBAL"

    def test_lowercase_global_rejected(self):
        with pytest.raises(ValidationError):
            CreateTagRequestModel(
                tagName="Status", description="A status tag", tagTypeName="System",
                databaseId="global",
            )

    def test_tagtype_databaseid_accepted(self):
        m = CreateTagTypeRequestModel(
            tagTypeName="Custom", description="Custom type", databaseId="factory-db",
        )
        assert m.databaseId == "factory-db"

    def test_response_model_carries_databaseid(self):
        r = TagResponseModel(
            tagName="EquipID", description="Equipment ID", tagTypeName="Custom",
            databaseId="factory-db",
        )
        assert r.databaseId == "factory-db"

    def test_response_model_databaseid_optional(self):
        r = TagResponseModel(tagName="Status", description="d", tagTypeName="System")
        assert r.databaseId is None
