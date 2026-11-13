# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tag handlers record each action after it succeeded, and nothing when it did not."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.tags import createTag, tagService


CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    monkeypatch.setattr(createTag, "log_action", recorder)
    monkeypatch.setattr(tagService, "log_action", recorder)
    monkeypatch.setattr(createTag, "claims_and_roles", CLAIMS, raising=False)
    monkeypatch.setattr(tagService, "claims_and_roles", CLAIMS, raising=False)
    return recorder


def _event(body=None, path_parameters=None, query=None):
    return {"body": json.dumps(body) if body is not None else None, "pathParameters": path_parameters or {},
            "queryStringParameters": query or {}}


TAG = {"tagName": "approved", "description": "Approved for release", "tagTypeName": "status"}


@pytest.mark.unit
class TestTagRecords:
    def test_a_create_records_the_written_tag(self, recorder, monkeypatch):
        monkeypatch.setattr(createTag, "create_tag", lambda data, claims: MagicMock(dict=lambda: {}))
        event = _event(TAG)
        assert createTag.handle_post_request(event)["statusCode"] == 200
        recorder.assert_called_once_with(event, "tagCreate", tagId="approved", database_id=None, changes=TAG)

    def test_an_update_records_the_written_tag(self, recorder, monkeypatch):
        monkeypatch.setattr(createTag, "update_tag", lambda data, claims: MagicMock(dict=lambda: {}))
        event = _event(TAG)
        assert createTag.handle_put_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "tagUpdate")
        assert recorder.call_args.kwargs["changes"]["description"] == "Approved for release"

    @pytest.mark.parametrize("handler, service", [("handle_post_request", "create_tag"),
                                                  ("handle_put_request", "update_tag")])
    def test_a_write_that_failed_records_nothing(self, recorder, monkeypatch, handler, service):
        def refuse(data, claims):
            raise createTag.VAMSGeneralErrorResponse("Not authorized")
        monkeypatch.setattr(createTag, service, refuse)
        assert getattr(createTag, handler)(_event(TAG))["statusCode"] == 400
        recorder.assert_not_called()

    def test_a_delete_is_recorded(self, recorder, monkeypatch):
        monkeypatch.setattr(tagService, "delete_tag", lambda name, claims, database_id: MagicMock(dict=lambda: {}))
        event = _event(path_parameters={"tagId": "approved"}, query={"databaseId": "db1"})
        assert tagService.handle_delete_request(event)["statusCode"] == 200
        recorder.assert_called_once_with(event, "tagDelete", tagId="approved", database_id="db1")

    def test_a_refused_delete_records_nothing(self, recorder, monkeypatch):
        def refuse(*args):
            raise tagService.VAMSGeneralErrorResponse("Not authorized")
        monkeypatch.setattr(tagService, "delete_tag", refuse)
        tagService.handle_delete_request(_event(path_parameters={"tagId": "approved"}))
        recorder.assert_not_called()

    def test_a_list_records_the_request_and_the_count(self, recorder, monkeypatch):
        monkeypatch.setattr(tagService, "get_tags", lambda params: {"Items": [
            {"tagName": "a", "description": "secret-description", "tagTypeName": "t"},
            {"tagName": "b", "description": "secret-description", "tagTypeName": "t"}]})
        event = _event(query={"maxItems": "10"})
        assert tagService.handle_get_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "tagList")
        assert recorder.call_args.kwargs["item_count"] == 2
        assert "secret-description" not in json.dumps(recorder.call_args.kwargs, default=str)
