# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-validation coverage for the auth/role/user API request models.

Each malformed-input case is paired with a legitimate-input case so a bound can
never be tightened past what the system legitimately accepts and stores.
"""

import glob
import importlib.util
import json
import os

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


def _real_validators():
    """Load the real common/validators.py by path.

    The root conftest replaces ``common.validators`` in ``sys.modules`` with the
    mock, so the shipped validator functions are otherwise unreachable from tests.
    """
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "backend", "common", "validators.py")
    spec = importlib.util.spec_from_file_location("_real_common_validators", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Model modules under test, each of which binds ``validate`` at import time.
_MODEL_MODULES = (
    "models.roleConstraints",
    "models.apiKeys",
    "models.user",
    "models.authLoginProfile",
)


@pytest.fixture(autouse=True)
def real_validate_dispatcher(monkeypatch):
    """Run these models against the real ``validate()`` dispatcher.

    The root ``tests/conftest.py`` replaces the dispatcher with a permissive stub
    that returns ``(True, "")`` for everything, and each model module binds
    ``validate`` by value at import time. Without this fixture the assertions here
    would pass or fail purely on collection order -- alone they exercise the mock's
    partial rules, and after a sibling suite re-imports a model they exercise the
    always-true stub. Pinning the real dispatcher onto each model module makes the
    validation contract the thing under test.
    """
    real_validate = _real_validators().validate
    for module_name in _MODEL_MODULES:
        module = importlib.import_module(module_name)
        if hasattr(module, "validate"):
            monkeypatch.setattr(module, "validate", real_validate)


CONSTRAINT_BASE = {
    "identifier": "my-constraint",
    "name": "my-constraint",
    "description": "A constraint",
    "objectType": "database",
}


def _constraint(**overrides):
    body = dict(CONSTRAINT_BASE)
    body.setdefault("criteriaOr", [{"field": "databaseId", "operator": "equals", "value": "my-db"}])
    body.update(overrides)
    return body


def _template(**overrides):
    body = {
        "variableValues": {"ROLE_NAME": "my-role"},
        "constraints": [{
            "name": "n",
            "description": "d",
            "objectType": "database",
            "criteriaOr": [{"field": "databaseId", "operator": "equals", "value": "my-db"}],
            "groupPermissions": [{"action": "GET", "type": "allow"}],
        }],
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestRealEmailAndUserIdValidators:
    """The shipped EMAIL/USERID rules must anchor at both ends.

    ``re.match`` leaves '$' matching just before a trailing newline, so a value
    ending in "\\n" would pass and carry a newline into a stored attribute or a
    log line.
    """

    @pytest.mark.parametrize("value", [
        "user@example.com\n", "user@example.com\n\n", "user@example.com\nX\n",
    ])
    def test_email_rejects_trailing_newline(self, value):
        validators = _real_validators()
        (valid, _message) = validators.validate_email("email", value)
        assert valid is False

    @pytest.mark.parametrize("value", ["abc\n", "user.name\n"])
    def test_userid_rejects_trailing_newline(self, value):
        validators = _real_validators()
        (valid, _message) = validators.validate_userid("userId", value)
        assert valid is False

    @pytest.mark.parametrize("value", [
        "user@example.com", "first.last+tag@sub.example.com", "u-1_2@example.org",
    ])
    def test_email_accepts_legitimate_addresses(self, value):
        validators = _real_validators()
        (valid, message) = validators.validate_email("email", value)
        assert valid is True, message

    @pytest.mark.parametrize("value", [
        "abc", "user.name", "user@example.com", "first-last+tag@example.com",
    ])
    def test_userid_accepts_legitimate_ids(self, value):
        validators = _real_validators()
        (valid, message) = validators.validate_userid("userId", value)
        assert valid is True, message


@pytest.mark.unit
class TestConstraintCriteriaValue:
    """A criteria value is interpolated into the Casbin regexMatch() pattern."""

    def test_rejects_uncompilable_regex_value(self):
        from models.roleConstraints import CreateConstraintRequestModel
        with pytest.raises(ValidationError):
            parse(_constraint(criteriaOr=[
                {"field": "databaseId", "operator": "equals", "value": "a(b"}
            ]), model=CreateConstraintRequestModel)

    def test_rejects_uncompilable_regex_inside_list_value(self):
        from models.roleConstraints import CreateConstraintRequestModel
        with pytest.raises(ValidationError):
            parse(_constraint(criteriaOr=[
                {"field": "databaseId", "operator": "is_one_of", "value": ["my-db", "a(b"]}
            ]), model=CreateConstraintRequestModel)

    def test_rejects_oversized_value(self):
        from models.roleConstraints import CreateConstraintRequestModel
        with pytest.raises(ValidationError):
            parse(_constraint(criteriaOr=[
                {"field": "databaseId", "operator": "equals", "value": "a" * 300}
            ]), model=CreateConstraintRequestModel)

    def test_rejects_too_many_list_values(self):
        from models.roleConstraints import CreateConstraintRequestModel, MAX_CRITERIA_VALUES
        with pytest.raises(ValidationError):
            parse(_constraint(criteriaOr=[{
                "field": "databaseId", "operator": "is_one_of",
                "value": ["db"] * (MAX_CRITERIA_VALUES + 1),
            }]), model=CreateConstraintRequestModel)

    @pytest.mark.parametrize("value", ["my-db", ".*", "GLOBAL", "prod-.*-scans", "db_1.2"])
    def test_accepts_legitimate_scalar_values(self, value):
        from models.roleConstraints import CreateConstraintRequestModel
        model = parse(_constraint(criteriaOr=[
            {"field": "databaseId", "operator": "contains", "value": value}
        ]), model=CreateConstraintRequestModel)
        assert model.criteriaOr[0].value == value

    def test_accepts_value_at_the_length_limit(self):
        from models.roleConstraints import CreateConstraintRequestModel
        value = "a" * 256
        model = parse(_constraint(criteriaOr=[
            {"field": "databaseId", "operator": "equals", "value": value}
        ]), model=CreateConstraintRequestModel)
        assert model.criteriaOr[0].value == value


@pytest.mark.unit
class TestConstraintCollectionBounds:
    def test_rejects_too_many_criteria(self):
        from models.roleConstraints import CreateConstraintRequestModel, MAX_CRITERIA_PER_CONSTRAINT
        criteria = [{"field": "databaseId", "operator": "equals", "value": "x"}]
        with pytest.raises(ValidationError):
            parse(_constraint(criteriaOr=criteria * (MAX_CRITERIA_PER_CONSTRAINT + 1)),
                  model=CreateConstraintRequestModel)

    def test_rejects_too_many_group_permissions(self):
        from models.roleConstraints import CreateConstraintRequestModel, MAX_PERMISSIONS_PER_CONSTRAINT
        perms = [{"groupId": f"g{i}", "permission": "GET", "permissionType": "allow"}
                 for i in range(MAX_PERMISSIONS_PER_CONSTRAINT + 1)]
        with pytest.raises(ValidationError):
            parse(_constraint(groupPermissions=perms), model=CreateConstraintRequestModel)

    def test_rejects_too_many_user_permissions(self):
        from models.roleConstraints import CreateConstraintRequestModel, MAX_PERMISSIONS_PER_CONSTRAINT
        perms = [{"userId": f"user{i}@example.com", "permission": "GET", "permissionType": "allow"}
                 for i in range(MAX_PERMISSIONS_PER_CONSTRAINT + 1)]
        with pytest.raises(ValidationError):
            parse(_constraint(userPermissions=perms), model=CreateConstraintRequestModel)

    def test_accepts_a_realistic_constraint(self):
        from models.roleConstraints import CreateConstraintRequestModel
        model = parse(_constraint(
            criteriaOr=[{"field": "databaseId", "operator": "equals", "value": "my-db"},
                        {"field": "databaseId", "operator": "equals", "value": "GLOBAL"}],
            groupPermissions=[{"groupId": "my-role", "permission": "GET", "permissionType": "allow"},
                              {"groupId": "my-role", "permission": "PUT", "permissionType": "allow"}],
            userPermissions=[{"userId": "user@example.com", "permission": "GET",
                              "permissionType": "allow"}],
        ), model=CreateConstraintRequestModel)
        assert len(model.criteriaOr) == 2
        assert len(model.groupPermissions) == 2


@pytest.mark.unit
class TestTemplateImportValidation:
    def test_rejects_uncompilable_regex_in_template_criteria(self):
        from models.roleConstraints import ImportConstraintsTemplateRequestModel
        body = _template()
        body["constraints"][0]["criteriaOr"] = [
            {"field": "databaseId", "operator": "equals", "value": "a(b"}
        ]
        with pytest.raises(ValidationError):
            parse(body, model=ImportConstraintsTemplateRequestModel)

    def test_rejects_oversized_variable_value(self):
        from models.roleConstraints import ImportConstraintsTemplateRequestModel
        with pytest.raises(ValidationError):
            parse(_template(variableValues={"ROLE_NAME": "my-role", "DATABASE_ID": "z" * 300}),
                  model=ImportConstraintsTemplateRequestModel)

    @pytest.mark.parametrize("bad_value", [["a", "b"], {"nested": "dict"}, None])
    def test_rejects_non_scalar_variable_value(self, bad_value):
        from models.roleConstraints import ImportConstraintsTemplateRequestModel
        with pytest.raises(ValidationError):
            parse(_template(variableValues={"ROLE_NAME": "my-role", "DATABASE_ID": bad_value}),
                  model=ImportConstraintsTemplateRequestModel)

    def test_rejects_oversized_variable_name(self):
        from models.roleConstraints import (
            ImportConstraintsTemplateRequestModel, MAX_TEMPLATE_VARIABLE_NAME_LENGTH,
        )
        name = "V" * (MAX_TEMPLATE_VARIABLE_NAME_LENGTH + 1)
        with pytest.raises(ValidationError):
            parse(_template(variableValues={"ROLE_NAME": "my-role", name: "x"}),
                  model=ImportConstraintsTemplateRequestModel)

    def test_rejects_too_many_variables(self):
        from models.roleConstraints import (
            ImportConstraintsTemplateRequestModel, MAX_TEMPLATE_VARIABLES,
        )
        variable_values = {f"VAR_{i}": "x" for i in range(MAX_TEMPLATE_VARIABLES + 1)}
        variable_values["ROLE_NAME"] = "my-role"
        with pytest.raises(ValidationError):
            parse(_template(variableValues=variable_values),
                  model=ImportConstraintsTemplateRequestModel)

    def test_rejects_too_many_constraints(self):
        from models.roleConstraints import (
            ImportConstraintsTemplateRequestModel, MAX_CONSTRAINTS_PER_TEMPLATE,
        )
        body = _template()
        body["constraints"] = body["constraints"] * (MAX_CONSTRAINTS_PER_TEMPLATE + 1)
        with pytest.raises(ValidationError):
            parse(body, model=ImportConstraintsTemplateRequestModel)

    @pytest.mark.parametrize("value", ["my-db", 5, True, 1.5])
    def test_accepts_scalar_variable_values(self, value):
        from models.roleConstraints import ImportConstraintsTemplateRequestModel
        model = parse(_template(variableValues={"ROLE_NAME": "my-role", "DATABASE_ID": value}),
                      model=ImportConstraintsTemplateRequestModel)
        assert model.variableValues["DATABASE_ID"] == value

    def test_accepts_variable_value_at_the_length_limit(self):
        from models.roleConstraints import (
            ImportConstraintsTemplateRequestModel, MAX_TEMPLATE_VARIABLE_VALUE_LENGTH,
        )
        value = "z" * MAX_TEMPLATE_VARIABLE_VALUE_LENGTH
        model = parse(_template(variableValues={"ROLE_NAME": "my-role", "DATABASE_ID": value}),
                      model=ImportConstraintsTemplateRequestModel)
        assert model.variableValues["DATABASE_ID"] == value

    def test_shipped_permission_templates_still_import(self):
        """The templates in documentation/permissionsTemplates/ are the reference input."""
        from models.roleConstraints import ImportConstraintsTemplateRequestModel
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "documentation", "permissionsTemplates")
        paths = sorted(glob.glob(os.path.join(templates_dir, "*.json")))
        assert paths, f"no permission templates found under {templates_dir}"

        variable_values = {
            "ROLE_NAME": "my-role", "DATABASE_ID": "my-db",
            "TAG_VALUE": "locked", "TAG_NAME": "restricted",
        }
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                template = json.load(handle)
            model = parse({
                "template": template.get("metadata"),
                "variables": template.get("variables", []),
                "variableValues": variable_values,
                "constraints": template["constraints"],
            }, model=ImportConstraintsTemplateRequestModel)
            assert model.constraints, os.path.basename(path)


@pytest.mark.unit
class TestUserRolesAndPagination:
    def test_rejects_too_many_roles(self):
        from models.roleConstraints import CreateUserRolesRequestModel, MAX_ROLES_PER_USER_REQUEST
        body = {"userId": "user@example.com",
                "roleName": [f"role-{i}" for i in range(MAX_ROLES_PER_USER_REQUEST + 1)]}
        with pytest.raises(ValidationError):
            parse(body, model=CreateUserRolesRequestModel)

    def test_rejects_empty_role_list(self):
        from models.roleConstraints import CreateUserRolesRequestModel
        with pytest.raises(ValidationError):
            parse({"userId": "user@example.com", "roleName": []},
                  model=CreateUserRolesRequestModel)

    def test_accepts_a_normal_role_assignment(self):
        from models.roleConstraints import CreateUserRolesRequestModel
        model = parse({"userId": "user@example.com", "roleName": ["my-role", "other-role"]},
                      model=CreateUserRolesRequestModel)
        assert model.roleName == ["my-role", "other-role"]

    @pytest.mark.parametrize("model_name", [
        "GetConstraintsRequestModel", "GetRolesRequestModel", "GetUserRolesRequestModel",
    ])
    def test_rejects_unbounded_page_size(self, model_name):
        import models.roleConstraints as role_models
        model_cls = getattr(role_models, model_name)
        with pytest.raises(ValidationError):
            parse({"pageSize": 10 ** 9}, model=model_cls)

    @pytest.mark.parametrize("model_name", [
        "GetConstraintsRequestModel", "GetRolesRequestModel", "GetUserRolesRequestModel",
    ])
    def test_accepts_default_and_normal_pagination(self, model_name):
        import models.roleConstraints as role_models
        model_cls = getattr(role_models, model_name)
        assert parse({}, model=model_cls).pageSize >= 1
        assert parse({"pageSize": 100, "maxItems": 1000}, model=model_cls).pageSize == 100

    @pytest.mark.parametrize("model_name", [
        "GetConstraintsRequestModel", "GetRolesRequestModel", "GetUserRolesRequestModel",
    ])
    def test_rejects_oversized_starting_token(self, model_name):
        import models.roleConstraints as role_models
        model_cls = getattr(role_models, model_name)
        with pytest.raises(ValidationError):
            parse({"startingToken": "t" * 5000}, model=model_cls)


@pytest.mark.unit
class TestWebRouteCheckValidation:
    def test_rejects_unknown_method(self):
        from models.authRoutes import CheckWebRoutesRequestModel
        with pytest.raises(ValidationError):
            parse({"routes": [{"method": "TRACE", "route__path": "/assets"}]},
                  model=CheckWebRoutesRequestModel)

    @pytest.mark.parametrize("path", ["/assets\ninjected", "/assets\rinjected"])
    def test_rejects_newline_in_route_path(self, path):
        from models.authRoutes import CheckWebRoutesRequestModel
        with pytest.raises(ValidationError):
            parse({"routes": [{"method": "GET", "route__path": path}]},
                  model=CheckWebRoutesRequestModel)

    def test_rejects_too_many_routes(self):
        from models.authRoutes import CheckWebRoutesRequestModel, MAX_WEB_ROUTES_PER_REQUEST
        routes = [{"method": "GET", "route__path": "/assets"}] * (MAX_WEB_ROUTES_PER_REQUEST + 1)
        with pytest.raises(ValidationError):
            parse({"routes": routes}, model=CheckWebRoutesRequestModel)

    @pytest.mark.parametrize("method", ["GET", "PUT", "POST", "DELETE"])
    def test_accepts_standard_methods(self, method):
        from models.authRoutes import CheckWebRoutesRequestModel
        model = parse({"routes": [{"method": method, "route__path": "/assets"}]},
                      model=CheckWebRoutesRequestModel)
        assert model.routes[0].method == method

    @pytest.mark.parametrize("path", [
        "/assets", "/databases/my-db/assets", "/assets/my asset/file.glb", "/metadataschema",
    ])
    def test_accepts_legitimate_route_paths(self, path):
        from models.authRoutes import CheckWebRoutesRequestModel
        model = parse({"routes": [{"method": "GET", "route__path": path}]},
                      model=CheckWebRoutesRequestModel)
        assert model.routes[0].route__path == path


@pytest.mark.unit
class TestLoginProfileValidation:
    def test_rejects_oversized_email(self):
        from models.authLoginProfile import UpdateLoginProfileRequestModel
        with pytest.raises(ValidationError):
            parse({"email": "a" * 100000 + "@example.com"},
                  model=UpdateLoginProfileRequestModel)

    def test_rejects_trailing_newline_email(self):
        from models.authLoginProfile import UpdateLoginProfileRequestModel
        with pytest.raises(ValidationError):
            parse({"email": "user@example.com\ninjected"},
                  model=UpdateLoginProfileRequestModel)

    @pytest.mark.parametrize("email", [
        "user@example.com", "first.last+tag@sub.example.com", "u-1_2@example.org",
    ])
    def test_accepts_legitimate_emails(self, email):
        from models.authLoginProfile import UpdateLoginProfileRequestModel
        model = parse({"email": email}, model=UpdateLoginProfileRequestModel)
        assert model.email == email

    def test_accepts_absent_email(self):
        from models.authLoginProfile import UpdateLoginProfileRequestModel
        assert parse({}, model=UpdateLoginProfileRequestModel).email is None


@pytest.mark.unit
class TestApiKeyValidation:
    """An API key name is stored, listed, and echoed in the delete confirmation."""

    @pytest.mark.parametrize("name", [
        "../../etc/passwd", "<script>alert(1)</script>", "$(rm -rf /)",
        "key\nname: injected", "key/with/slashes",
    ])
    def test_rejects_malformed_api_key_name(self, name):
        from models.apiKeys import CreateApiKeyRequestModel
        with pytest.raises(ValidationError):
            parse({"apiKeyName": name, "userId": "user@example.com", "description": "d"},
                  model=CreateApiKeyRequestModel)

    @pytest.mark.parametrize("name", [
        "../../etc/passwd", "<script>alert(1)</script>", "key\nname: injected",
    ])
    def test_rejects_malformed_user_api_key_name(self, name):
        from models.apiKeys import CreateUserApiKeyRequestModel
        with pytest.raises(ValidationError):
            parse({"apiKeyName": name, "description": "d", "expiresAt": "2027-12-31"},
                  model=CreateUserApiKeyRequestModel)

    @pytest.mark.parametrize("name", [
        "my-key", "Prod Key 2", "key_1.2", "CI runner key", "a" * 256,
    ])
    def test_accepts_legitimate_api_key_names(self, name):
        from models.apiKeys import CreateApiKeyRequestModel
        model = parse({"apiKeyName": name, "userId": "user@example.com", "description": "d"},
                      model=CreateApiKeyRequestModel)
        assert model.apiKeyName == name

    @pytest.mark.parametrize("model_name", [
        "UpdateApiKeyRequestModel", "UpdateUserApiKeyRequestModel",
    ])
    @pytest.mark.parametrize("is_active", ["True", "FALSE", "yes", "1", "enabled", "truthy"])
    def test_rejects_non_boolean_is_active(self, model_name, is_active):
        """The authorizer compares isActive != 'true', so only the exact literals work."""
        import models.apiKeys as api_key_models
        model_cls = getattr(api_key_models, model_name)
        with pytest.raises(ValidationError):
            parse({"isActive": is_active}, model=model_cls)

    @pytest.mark.parametrize("model_name", [
        "UpdateApiKeyRequestModel", "UpdateUserApiKeyRequestModel",
    ])
    @pytest.mark.parametrize("is_active", ["true", "false"])
    def test_accepts_boolean_is_active(self, model_name, is_active):
        import models.apiKeys as api_key_models
        model_cls = getattr(api_key_models, model_name)
        assert parse({"isActive": is_active}, model=model_cls).isActive == is_active

    def test_invalid_expiration_message_does_not_echo_the_input(self):
        """Backend Rule 11: the model message reaches the client verbatim."""
        from models.apiKeys import CreateApiKeyRequestModel
        marker = "<img src=x onerror=alert(1)>"
        with pytest.raises(ValidationError) as excinfo:
            parse({"apiKeyName": "my-key", "userId": "user@example.com",
                   "description": "d", "expiresAt": marker},
                  model=CreateApiKeyRequestModel)
        assert marker not in str(excinfo.value)

    @pytest.mark.parametrize("expires_at", ["2027-12-31", "2027-12-31T23:59:59Z"])
    def test_accepts_iso8601_expirations(self, expires_at):
        from models.apiKeys import CreateApiKeyRequestModel
        model = parse({"apiKeyName": "my-key", "userId": "user@example.com",
                       "description": "d", "expiresAt": expires_at},
                      model=CreateApiKeyRequestModel)
        assert model.expiresAt == expires_at


@pytest.mark.unit
class TestCognitoUserValidation:
    def test_rejects_oversized_starting_token(self):
        from models.user import ListCognitoUsersRequestModel
        with pytest.raises(ValidationError):
            parse({"startingToken": "t" * 5000}, model=ListCognitoUsersRequestModel)

    def test_rejects_trailing_newline_email(self):
        from models.user import CreateCognitoUserRequestModel
        with pytest.raises(ValidationError):
            parse({"userId": "user@example.com", "email": "user@example.com\ninjected"},
                  model=CreateCognitoUserRequestModel)

    def test_accepts_a_normal_user(self):
        from models.user import CreateCognitoUserRequestModel
        model = parse({"userId": "user@example.com", "email": "user@example.com",
                       "phone": "+12345678900"}, model=CreateCognitoUserRequestModel)
        assert model.email == "user@example.com"
