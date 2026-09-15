# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tag-type list/delete module-load contract and caller-facing error text.

The module-load block must abort the cold start when a resource name cannot be
resolved (backend Rule 10 / the Gold Standard module-load contract). When the
name was swallowed into `None` the module imported cleanly, `tag_type_table`
was `None`, and the first table call raised `AttributeError` inside the
per-operation catch-all -- which then re-raised it as
`VAMSGeneralErrorResponse(f"...: {str(e)}")`, so the internal Python text
reached an authenticated caller (Rule 11).

The module-load tests load a FRESH copy of the handler by file path rather than
reloading the shared module: a failed `importlib.reload` mutates the live module
in place and would leave the other tests in this directory running against a
half-initialized module.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.tagTypes import tagTypeService
from backend.backend.handlers.tagTypes.tagTypeService import delete_tag_type, get_tag_types

# Reference the exception through the module under test so the asserted class is
# the exact object raised (distinct module objects load from the same file).
VAMSGeneralErrorResponse = tagTypeService.VAMSGeneralErrorResponse

CLAIMS = {"tokens": ["u"]}

_HANDLER_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "tagTypes", "tagTypeService.py",
))

# The text a None table produces. Asserted as a substring so the test pins the
# absence of the internal detail rather than only the generic wording.
NONETYPE_TEXT = "'NoneType' object has no attribute 'get_item'"


def _enf(allow=True):
    inst = MagicMock(); inst.enforce.return_value = allow
    return inst


def _stub_handler_packages():
    """The root conftest replaces `handlers.auth` / `handlers.authz` with
    attribute-less stand-ins before every test, and a fresh module copy imports
    names out of them while it executes. The stand-ins are rebuilt per test, so
    the attributes added here do not outlive it."""
    for module_name, attr in (("handlers.auth", "request_to_claims"),
                              ("handlers.authz", "CasbinEnforcer")):
        module = sys.modules[module_name]
        if not hasattr(module, attr):
            setattr(module, attr, MagicMock())


def _load_fresh(unique_suffix):
    """Load an independent copy of tagTypeService by file path."""
    _stub_handler_packages()
    spec = importlib.util.spec_from_file_location(
        f"tagTypeService_under_test_{unique_suffix}", _HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.resource", return_value=MagicMock()), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestModuleLoadFailsClosed:
    """An unresolvable resource name must fail the cold start, not the request."""

    def test_import_raises_when_resource_name_unresolvable(self):
        boom = RuntimeError("SSM unreachable")
        with patch("common.resourceNames.get_table_name", side_effect=boom):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh("fail")
        assert "SSM unreachable" in str(excinfo.value)

    def test_import_builds_every_table_when_names_resolve(self):
        """Positive control for the test above: with names resolvable the module
        imports and every table is a real object, not None. Without this a
        constructor error unrelated to resolution would make the negative test
        pass for the wrong reason. Also proves the patched resolver is the one the
        fresh copy binds, so the negative test's `side_effect` really reaches it."""
        resolver = MagicMock(side_effect=lambda key: key.param_key)
        with patch("common.resourceNames.get_table_name", resolver):
            module = _load_fresh("ok")

        assert resolver.call_count == 2, "the fresh copy did not resolve through the patch"
        table_attrs = [name for name in vars(module) if name.endswith("_table")]
        assert sorted(table_attrs) == ["tag_table", "tag_type_table"]
        for name in table_attrs:
            assert getattr(module, name) is not None, f"{name} resolved to None"

    def test_only_the_second_name_unresolvable_still_aborts_the_cold_start(self):
        """Both names share one block, so a failure on either one aborts. Guards
        the shape as well as the outcome: a per-table fallback would swallow this
        one and leave `tag_type_table` None."""
        boom = KeyError("dynamoTables/tagTypeStorage")

        def resolve(key):
            if key.param_key == "dynamoTables/tagTypeStorage":
                raise boom
            return "tagsTable"

        with patch("common.resourceNames.get_table_name", side_effect=resolve):
            with pytest.raises(KeyError):
                _load_fresh("fail_second")


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_type_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.CasbinEnforcer')
class TestErrorTextCarriesNoInternalDetail:
    """Rule 11: an unexpected failure reports generic text; specifics are logged."""

    def test_list_error_message_omits_the_exception_text(self, casbin, tag_type_table,
                                                        tag_table):
        casbin.return_value = _enf(True)
        tag_type_table.query.side_effect = AttributeError(NONETYPE_TEXT)

        with pytest.raises(VAMSGeneralErrorResponse) as exc:
            get_tag_types({'maxItems': 100, 'pageSize': 100, 'startingToken': None,
                           'databaseId': 'factory-db', 'scope': None}, CLAIMS)

        assert "NoneType" not in str(exc.value)
        # endswith, not equality: the exception class prefixes "VAMS General Error: ".
        assert str(exc.value).endswith("Error retrieving tag types")

    def test_delete_error_message_omits_the_exception_text(self, casbin, tag_type_table,
                                                          tag_table):
        casbin.return_value = _enf(True)
        tag_type_table.get_item.side_effect = AttributeError(NONETYPE_TEXT)

        with pytest.raises(VAMSGeneralErrorResponse) as exc:
            delete_tag_type("Custom", CLAIMS, "factory-db")

        assert "NoneType" not in str(exc.value)
        assert str(exc.value).endswith("Error deleting tag type")

    def test_get_response_body_carries_no_internal_detail(self, casbin, tag_type_table,
                                                         tag_table):
        """End to end: the message reaches the caller through general_error, so the
        assertion is made on the serialized response body the client receives."""
        casbin.return_value = _enf(True)
        tag_type_table.query.side_effect = AttributeError(NONETYPE_TEXT)
        event = {"requestContext": {"http": {"method": "GET"}},
                 "queryStringParameters": {"databaseId": "factory-db"}}

        with patch.object(tagTypeService, 'claims_and_roles', CLAIMS):
            response = tagTypeService.handle_get_request(event)

        assert response['statusCode'] == 400
        assert "NoneType" not in response['body']
        assert json.loads(response['body'])['message'].endswith("Error retrieving tag types")

    def test_delete_response_body_carries_no_internal_detail(self, casbin, tag_type_table,
                                                            tag_table):
        casbin.return_value = _enf(True)
        tag_type_table.get_item.side_effect = AttributeError(NONETYPE_TEXT)
        event = {"requestContext": {"http": {"method": "DELETE"}},
                 "pathParameters": {"tagTypeId": "Custom"},
                 "queryStringParameters": {"databaseId": "factory-db"}}

        with patch.object(tagTypeService, 'claims_and_roles', CLAIMS):
            response = tagTypeService.handle_delete_request(event)

        assert response['statusCode'] == 400
        assert "NoneType" not in response['body']
        assert json.loads(response['body'])['message'].endswith("Error deleting tag type")

    def test_delete_of_a_missing_tag_type_still_reports_not_found(self, casbin,
                                                                 tag_type_table, tag_table):
        """Positive control: the intentional, actionable message survives. Only the
        catch-all's final fallback is generic. This is also the shape a scoped
        delete sent without its databaseId takes -- it resolves in the GLOBAL
        partition, finds nothing, and must say so."""
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {}

        with pytest.raises(VAMSGeneralErrorResponse) as exc:
            delete_tag_type("Custom", CLAIMS, None)

        assert str(exc.value).endswith("Tag type not found")
        assert exc.value.status_code == 404

    def test_delete_of_a_referenced_tag_type_still_reports_in_use(self, casbin,
                                                                 tag_type_table, tag_table):
        """Positive control for the in-use branch of the same catch-all's function."""
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {
            'Item': {'databaseId': 'factory-db', 'tagTypeName': 'Custom'}}
        tag_table.scan.return_value = {
            'Items': [{'databaseId': 'factory-db', 'tagName': 't', 'tagTypeName': 'Custom'}]}

        with pytest.raises(VAMSGeneralErrorResponse) as exc:
            delete_tag_type("Custom", CLAIMS, "factory-db")

        assert str(exc.value).endswith("currently in use by a tag")
        assert exc.value.status_code == 400

    def test_conditional_check_failure_still_reports_not_found(self, casbin, tag_type_table,
                                                              tag_table):
        """Positive control for the delete catch-all's actionable branch: it still
        reports the specific cause even though the fallback below it is generic."""
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {
            'Item': {'databaseId': 'factory-db', 'tagTypeName': 'Custom'}}
        tag_table.scan.return_value = {'Items': []}
        tag_type_table.delete_item.side_effect = ClientError(
            {'Error': {'Code': 'ConditionalCheckFailedException',
                       'Message': 'The conditional request failed'}}, 'DeleteItem')

        with pytest.raises(VAMSGeneralErrorResponse) as exc:
            delete_tag_type("Custom", CLAIMS, "factory-db")

        assert str(exc.value).endswith("Tag type not found")
        assert exc.value.status_code == 404

    def test_successful_delete_is_unaffected(self, casbin, tag_type_table, tag_table):
        """Positive control: the happy path still removes the row and reports success."""
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {
            'Item': {'databaseId': 'factory-db', 'tagTypeName': 'Custom'}}
        tag_table.scan.return_value = {'Items': []}

        response = delete_tag_type("Custom", CLAIMS, "factory-db")

        assert response.success is True
        assert tag_type_table.delete_item.call_args.kwargs['Key'] == {
            'databaseId': 'factory-db', 'tagTypeName': 'Custom'}
