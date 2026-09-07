# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the constraint object-types mapping, endpoint, and matrix enforcement."""

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.common.constants import (
    CONSTRAINT_OBJECT_TYPE_FIELDS,
    CONSTRAINT_OPERATOR_LABELS,
    CONSTRAINT_PERMISSION_LABELS,
    CONSTRAINT_PERMISSION_TYPE_LABELS,
    get_constraint_fields_for_object_type,
    ALLOWED_CONSTRAINT_OBJECT_TYPES,
    ALLOWED_CONSTRAINT_OPERATORS,
    ALLOWED_CONSTRAINT_PERMISSIONS,
    ALLOWED_CONSTRAINT_PERMISSION_TYPES,
    PERMISSION_CONSTRAINT_FIELDS,
)
from backend.backend.handlers.auth.authConstraintsService import lambda_handler


@pytest.mark.unit
class TestConstraintMappingConsistency:
    def test_object_type_keys_match_allowed(self):
        assert set(CONSTRAINT_OBJECT_TYPE_FIELDS.keys()) == set(ALLOWED_CONSTRAINT_OBJECT_TYPES)

    def test_every_field_value_exists_in_permission_fields(self):
        for object_type, entry in CONSTRAINT_OBJECT_TYPE_FIELDS.items():
            for field in entry["fields"]:
                assert field["value"] in PERMISSION_CONSTRAINT_FIELDS, (
                    f"{object_type}.{field['value']} missing from PERMISSION_CONSTRAINT_FIELDS"
                )

    def test_operator_values_match_allowed(self):
        assert [o["value"] for o in CONSTRAINT_OPERATOR_LABELS] == list(ALLOWED_CONSTRAINT_OPERATORS)

    def test_permission_values_match_allowed(self):
        assert [p["value"] for p in CONSTRAINT_PERMISSION_LABELS] == list(ALLOWED_CONSTRAINT_PERMISSIONS)

    def test_permission_type_values_match_allowed(self):
        assert [p["value"] for p in CONSTRAINT_PERMISSION_TYPE_LABELS] == list(
            ALLOWED_CONSTRAINT_PERMISSION_TYPES
        )

    def test_every_entry_has_label_and_fields(self):
        for entry in CONSTRAINT_OBJECT_TYPE_FIELDS.values():
            assert entry["label"]
            assert isinstance(entry["fields"], list)

    def test_lookup_helper_known_and_unknown(self):
        assert get_constraint_fields_for_object_type("asset") == [
            "databaseId", "assetName", "assetType", "tags",
        ]
        assert get_constraint_fields_for_object_type("nope") == []
        assert get_constraint_fields_for_object_type(None) == []

    def test_tag_and_tagtype_support_databaseid_scope(self):
        # Scoped tag/tag-type admin relies on a databaseId constraint surviving the
        # object-type matrix (write-time validation + enforce-time scrubbing).
        assert "databaseId" in get_constraint_fields_for_object_type("tag")
        assert "databaseId" in get_constraint_fields_for_object_type("tagType")


@pytest.mark.unit
class TestPermissionObjectsRouteConstant:
    def test_route_constant_present_and_public(self):
        from backend.backend.common.apiRoutes import (
            API_AUTH_CONSTRAINT_PERMISSION_OBJECTS,
            get_public_api_routes,
        )
        assert API_AUTH_CONSTRAINT_PERMISSION_OBJECTS.path == "/auth/constraints/permissionObjects"
        assert "GET" in API_AUTH_CONSTRAINT_PERMISSION_OBJECTS.methods
        paths = {r.path for r in get_public_api_routes()}
        assert "/auth/constraints/permissionObjects" in paths

    def test_permission_objects_route_does_not_match_constraint_by_id(self):
        from backend.backend.common.apiRoutes import (
            API_AUTH_CONSTRAINT_PERMISSION_OBJECTS,
            API_AUTH_CONSTRAINT_BY_ID,
        )
        # The literal permissionObjects path is also shaped like /auth/constraints/{id};
        # both match it, which is why the handler must check permissionObjects first.
        assert API_AUTH_CONSTRAINT_PERMISSION_OBJECTS.matches("/auth/constraints/permissionObjects")
        assert API_AUTH_CONSTRAINT_BY_ID.matches("/auth/constraints/permissionObjects")


@pytest.mark.unit
class TestPermissionObjectsResponseModels:
    def test_response_model_serializes(self):
        from backend.backend.models.roleConstraints import (
            GetConstraintPermissionObjectsResponseModel,
        )
        model = GetConstraintPermissionObjectsResponseModel(
            objectTypes=[{
                "label": "Asset", "value": "asset",
                "fields": [{"label": "Database ID", "value": "databaseId"}],
            }],
            operators=[{"label": "Equals", "value": "equals"}],
            permissions=[{"label": "View/GET", "value": "GET"}],
            permissionTypes=[{"label": "Allow", "value": "allow"}],
        )
        data = model.dict()
        assert data["objectTypes"][0]["value"] == "asset"
        assert data["objectTypes"][0]["fields"][0]["value"] == "databaseId"
        assert data["operators"][0]["value"] == "equals"
        assert data["permissions"][0]["value"] == "GET"
        assert data["permissionTypes"][0]["value"] == "allow"


def _make_event(method='GET', path='/auth/constraints/permissionObjects'):
    return {
        'requestContext': {'http': {'method': method, 'path': path}},
        'pathParameters': {},
        'queryStringParameters': {},
        'headers': {'authorization': 'Bearer test-token'},
    }


_CLAIMS = {"tokens": ["test-user-id"], "roles": ["admin"], "mfaEnabled": False}


@pytest.mark.unit
class TestGetConstraintObjectTypesEndpoint:

    @patch('backend.backend.handlers.auth.authConstraintsService.request_to_claims')
    @patch('backend.backend.handlers.auth.authConstraintsService.CasbinEnforcer')
    def test_returns_object_types_and_operators(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = True
        mock_casbin.return_value = mock_enforcer

        response = lambda_handler(_make_event(), {})

        if response['statusCode'] != 200:
            body = json.loads(response['body'])
            pytest.fail(f"Expected 200, got {response['statusCode']}. Body: {body}")

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        payload = body['message'] if 'message' in body else body
        values = {o['value'] for o in payload['objectTypes']}
        assert 'asset' in values and 'web' in values
        asset = next(o for o in payload['objectTypes'] if o['value'] == 'asset')
        assert {'databaseId', 'assetName', 'assetType', 'tags'} == {f['value'] for f in asset['fields']}
        assert {o['value'] for o in payload['operators']} >= {'equals', 'contains'}
        assert {p['value'] for p in payload['permissions']} == {'GET', 'PUT', 'POST', 'DELETE'}
        assert {p['value'] for p in payload['permissionTypes']} == {'allow', 'deny'}

    @patch('backend.backend.handlers.auth.authConstraintsService.request_to_claims')
    @patch('backend.backend.handlers.auth.authConstraintsService.CasbinEnforcer')
    def test_api_authorization_denied_returns_403(self, mock_casbin, mock_claims):
        mock_claims.return_value = dict(_CLAIMS)
        mock_enforcer = MagicMock()
        mock_enforcer.enforceAPI.return_value = False
        mock_casbin.return_value = mock_enforcer

        response = lambda_handler(_make_event(), {})
        assert response['statusCode'] == 403

    @patch('backend.backend.handlers.auth.authConstraintsService.request_to_claims')
    def test_no_tokens_returns_403(self, mock_claims):
        mock_claims.return_value = {"tokens": [], "roles": [], "mfaEnabled": False}
        response = lambda_handler(_make_event(), {})
        assert response['statusCode'] == 403


@pytest.mark.unit
class TestWriteTimeMatrixValidation:
    def _base_constraint(self, object_type, field):
        return {
            "identifier": "test-constraint",
            "name": "test-constraint",
            "description": "a valid description",
            "objectType": object_type,
            "criteriaAnd": [{"field": field, "operator": "equals", "value": "x"}],
            "groupPermissions": [
                {"groupId": "admin", "permission": "GET", "permissionType": "allow"}
            ],
        }

    def test_create_rejects_out_of_matrix_field(self):
        from aws_lambda_powertools.utilities.parser import parse, ValidationError
        from backend.backend.models.roleConstraints import CreateConstraintRequestModel
        with pytest.raises(ValidationError):
            parse(self._base_constraint("asset", "workflowId"), model=CreateConstraintRequestModel)

    def test_create_accepts_valid_field(self):
        from aws_lambda_powertools.utilities.parser import parse
        from backend.backend.models.roleConstraints import CreateConstraintRequestModel
        model = parse(self._base_constraint("asset", "assetName"), model=CreateConstraintRequestModel)
        assert model.objectType == "asset"

    def test_template_import_rejects_out_of_matrix_field(self):
        from aws_lambda_powertools.utilities.parser import parse, ValidationError
        from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel
        body = {
            "variableValues": {"ROLE_NAME": "admin"},
            "constraints": [{
                "name": "c1", "description": "d", "objectType": "asset",
                "criteriaAnd": [{"field": "workflowId", "operator": "equals", "value": "x"}],
                "groupPermissions": [{"action": "GET", "type": "allow"}],
            }],
        }
        with pytest.raises(ValidationError):
            parse(body, model=ImportConstraintsTemplateRequestModel)
