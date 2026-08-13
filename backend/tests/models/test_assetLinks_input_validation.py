# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Input validation on the asset-link request models.

The asset and database identifiers in a link request become DynamoDB composite
keys ("{databaseId}:{assetId}") and Casbin rule inputs, so each field is checked
against the validator that matches its identifier kind, and the collections are
bounded.
"""

import importlib.util
import os

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


def _real_validate():
    """Load the real validate() dispatcher straight from its source file.

    `tests/conftest.py` replaces the dispatcher with a permissive stub
    (`lambda params: (True, "")`) so that older tests pass regardless of input.
    Under that stub every model root_validator is a no-op, which would make these
    tests assert nothing. Loading the real module by path keeps them honest.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'backend', 'common', 'validators.py',
    )
    spec = importlib.util.spec_from_file_location('_real_validators_assetlinks', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


@pytest.fixture(autouse=True)
def use_real_validators(monkeypatch):
    """Point the asset-link models at the real dispatcher for every test here."""
    import models.assetLinks as assetlinks_models
    monkeypatch.setattr(assetlinks_models, 'validate', _real_validate())


def _tag_cap():
    """Read the cap through a late import.

    A module-level import of models.assetLinks binds a module instance created
    before conftest installs its module mocks, so the model class the tests
    exercise would not be the one the handlers use.
    """
    from models.assetLinks import MAX_TAGS_PER_ASSET_LINK
    return MAX_TAGS_PER_ASSET_LINK


def _link_body(**overrides):
    body = {
        "fromAssetId": "xa31832dc-ca88-42ba-88cd-37fa9bb0cec9",
        "fromAssetDatabaseId": "smoke-db",
        "toAssetId": "xb42943ed-db99-53fb-99de-48e05cbd7fa1",
        "toAssetDatabaseId": "smoke-db",
        "relationshipType": "related",
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestCreateAssetLinkRequestModel:
    def test_accepts_a_normal_link(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        model = parse(_link_body(), model=CreateAssetLinkRequestModel)
        assert model.fromAssetDatabaseId == "smoke-db"
        assert model.tags == []

    def test_accepts_asset_ids_with_dots_and_spaces(self):
        """ASSET_ID (not ID) is the right rule: asset ids legitimately carry dots
        and spaces, which the ID rule would reject."""
        from models.assetLinks import CreateAssetLinkRequestModel
        model = parse(
            _link_body(fromAssetId="building scan v1.2", toAssetId="tower scan v3.4"),
            model=CreateAssetLinkRequestModel,
        )
        assert model.fromAssetId == "building scan v1.2"

    @pytest.mark.parametrize("field", [
        "fromAssetId", "toAssetId",
    ])
    def test_rejects_asset_id_with_a_path_separator(self, field):
        """A '/' in an asset id would split the "{databaseId}:{assetId}" composite
        key target and reach a different S3 prefix."""
        from models.assetLinks import CreateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse(_link_body(**{field: "../../other-asset"}), model=CreateAssetLinkRequestModel)

    @pytest.mark.parametrize("field", [
        "fromAssetDatabaseId", "toAssetDatabaseId",
    ])
    def test_rejects_database_id_that_breaks_the_id_rule(self, field):
        from models.assetLinks import CreateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse(_link_body(**{field: "bad db id!!"}), model=CreateAssetLinkRequestModel)

    @pytest.mark.parametrize("field", [
        "fromAssetDatabaseId", "toAssetDatabaseId",
    ])
    def test_rejects_database_id_with_a_colon(self, field):
        """A ':' would forge an extra segment in the composite key."""
        from models.assetLinks import CreateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse(_link_body(**{field: "smoke:db"}), model=CreateAssetLinkRequestModel)

    def test_rejects_an_oversized_asset_id(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse(_link_body(fromAssetId="a" * 257), model=CreateAssetLinkRequestModel)

    def test_accepts_tags_at_the_cap(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        cap = _tag_cap()
        tags = [f"tag{i}" for i in range(cap)]
        model = parse(_link_body(tags=tags), model=CreateAssetLinkRequestModel)
        assert len(model.tags) == cap

    def test_rejects_more_tags_than_the_cap(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        tags = [f"tag{i}" for i in range(_tag_cap() + 1)]
        with pytest.raises(ValidationError):
            parse(_link_body(tags=tags), model=CreateAssetLinkRequestModel)

    def test_rejects_an_oversized_tag_value(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse(_link_body(tags=["t" * 257]), model=CreateAssetLinkRequestModel)

    def test_unknown_fields_are_dropped(self):
        from models.assetLinks import CreateAssetLinkRequestModel
        model = parse(_link_body(unexpectedField="ignored"), model=CreateAssetLinkRequestModel)
        assert not hasattr(model, "unexpectedField")


@pytest.mark.unit
class TestUpdateAssetLinkRequestModel:
    def test_accepts_normal_tags(self):
        from models.assetLinks import UpdateAssetLinkRequestModel
        model = parse({"tags": ["alpha", "beta"]}, model=UpdateAssetLinkRequestModel)
        assert model.tags == ["alpha", "beta"]

    def test_rejects_more_tags_than_the_cap(self):
        from models.assetLinks import UpdateAssetLinkRequestModel
        tags = [f"tag{i}" for i in range(_tag_cap() + 1)]
        with pytest.raises(ValidationError):
            parse({"tags": tags}, model=UpdateAssetLinkRequestModel)

    def test_rejects_an_oversized_tag_value(self):
        from models.assetLinks import UpdateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse({"tags": ["t" * 257]}, model=UpdateAssetLinkRequestModel)

    def test_rejects_an_oversized_alias(self):
        from models.assetLinks import UpdateAssetLinkRequestModel
        with pytest.raises(ValidationError):
            parse({"tags": [], "assetLinkAliasId": "a" * 129}, model=UpdateAssetLinkRequestModel)


@pytest.mark.unit
class TestGetAssetLinksRequestModel:
    def test_accepts_normal_identifiers(self):
        from models.assetLinks import GetAssetLinksRequestModel
        model = parse(
            {"assetId": "xa31832dc-ca88-42ba-88cd-37fa9bb0cec9",
             "databaseId": "smoke-db", "childTreeView": False},
            model=GetAssetLinksRequestModel,
        )
        assert model.databaseId == "smoke-db"

    def test_rejects_a_traversal_asset_id(self):
        from models.assetLinks import GetAssetLinksRequestModel
        with pytest.raises(ValidationError):
            parse({"assetId": "../../etc/passwd", "databaseId": "smoke-db"},
                  model=GetAssetLinksRequestModel)

    def test_rejects_an_invalid_database_id(self):
        from models.assetLinks import GetAssetLinksRequestModel
        with pytest.raises(ValidationError):
            parse({"assetId": "asset-1", "databaseId": "no"}, model=GetAssetLinksRequestModel)


@pytest.mark.unit
class TestAssetLinkIdRequestModels:
    @pytest.mark.parametrize("model_name", [
        "GetSingleAssetLinkRequestModel", "DeleteAssetLinkRequestModel",
    ])
    def test_accepts_a_generated_link_id(self, model_name):
        import models.assetLinks as m
        model = parse({"assetLinkId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
                      model=getattr(m, model_name))
        assert model.assetLinkId == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @pytest.mark.parametrize("model_name", [
        "GetSingleAssetLinkRequestModel", "DeleteAssetLinkRequestModel",
    ])
    @pytest.mark.parametrize("bad_id", [
        "../../secrets", "has/slash", "has:colon", "ab", "a" * 64,
    ])
    def test_rejects_a_malformed_link_id(self, model_name, bad_id):
        import models.assetLinks as m
        with pytest.raises(ValidationError):
            parse({"assetLinkId": bad_id}, model=getattr(m, model_name))
