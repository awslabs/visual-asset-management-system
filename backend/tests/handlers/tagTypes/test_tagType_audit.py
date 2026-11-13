# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tag type handlers record each action after it succeeded, and nothing when it did not."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.tagTypes import createTagTypes, tagTypeService

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
TAG_TYPE = {"tagTypeName": "status", "description": "Release status", "required": "True"}


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    for module in (createTagTypes, tagTypeService):
        monkeypatch.setattr(module, "log_action", recorder)
        monkeypatch.setattr(module, "claims_and_roles", CLAIMS, raising=False)
    return recorder


def _event(body=None, path_parameters=None, query=None):
    return {"body": json.dumps(body) if body is not None else None, "pathParameters": path_parameters or {},
            "queryStringParameters": query or {}}


@pytest.mark.unit
class TestTagTypeRecords:
    @pytest.mark.parametrize("handler, service, action", [
        ("handle_post_request", "create_tag_type", "tagTypeCreate"),
        ("handle_put_request", "update_tag_type", "tagTypeUpdate")])
    def test_a_write_records_the_written_fields(self, recorder, monkeypatch, handler, service, action):
        monkeypatch.setattr(createTagTypes, service, lambda model, claims: MagicMock(dict=lambda: {}))
        event = _event(TAG_TYPE)
        assert getattr(createTagTypes, handler)(event)["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, action)
        assert recorder.call_args.kwargs["tagTypeId"] == "status"
        assert recorder.call_args.kwargs["changes"]["description"] == "Release status"

    def test_a_refused_write_records_nothing(self, recorder, monkeypatch):
        def refuse(model, claims):
            raise createTagTypes.VAMSGeneralErrorResponse("Not authorized", status_code=403)
        monkeypatch.setattr(createTagTypes, "create_tag_type", refuse)
        createTagTypes.handle_post_request(_event(TAG_TYPE))
        recorder.assert_not_called()

    def test_a_delete_is_recorded(self, recorder, monkeypatch):
        monkeypatch.setattr(tagTypeService, "delete_tag_type",
                            lambda name, claims, database_id: MagicMock(dict=lambda: {}))
        event = _event(path_parameters={"tagTypeId": "status"})
        assert tagTypeService.handle_delete_request(event)["statusCode"] == 200
        recorder.assert_called_once_with(event, "tagTypeDelete", tagTypeId="status", database_id=None)

    def test_a_list_records_the_request_and_the_count(self, recorder, monkeypatch):
        monkeypatch.setattr(tagTypeService, "get_tag_types", lambda params, claims: {"Items": [
            {"tagTypeName": "status", "description": "d", "required": "False", "tags": []}]})
        event = _event(query={"maxItems": "5"})
        assert tagTypeService.handle_get_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "tagTypeList")
        assert recorder.call_args.kwargs["item_count"] == 1
