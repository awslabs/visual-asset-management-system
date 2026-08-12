#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Accepting the GLOBAL keyword for one field does not excuse the rest of the request.

`validate()` takes a dict of fields and walks it in insertion order. GLOBAL is a legitimate value for
the database-scope field on the pipeline, workflow, execution, and metadata-schema routes, so those
call sites pass `allowGlobalKeyword: True` for that field and a plain rule for the id beside it —
`{'databaseId': GLOBAL, 'workflowId': <path value>}`. If the keyword branch terminates the walk, the
second field is never checked and the caller-supplied id reaches a DynamoDB key or an S3 prefix
builder unvalidated, while the dispatcher reports the whole request valid.

The empty-optional branches in the same loop carry a comment about exactly this hazard and use
`continue`; these tests pin the keyword branch to the same scope, in both field orders and for every
rule a GLOBAL-bearing call site actually declares.
"""
import pytest

from common.validators import validate


MALFORMED_IDS = (
    ("../../etc/passwd", "path traversal"),
    ("a", "1 char, below the 3-char floor"),
    ("a" * 500, "500 chars, far over the 63-char ceiling"),
    ("ok\n<script>", "an embedded newline plus markup"),
    ("has spaces", "spaces are not in the id character class"),
    ("a/b", "a path separator"),
)


@pytest.mark.unit
class TestAGlobalFieldDoesNotEndTheWalk:
    """The reported bypass: GLOBAL ordered FIRST, a malformed id ordered second."""

    @pytest.mark.parametrize("value,why", MALFORMED_IDS)
    def test_a_field_after_global_is_still_validated(self, value, why):
        valid, message = validate({
            "workflowDatabaseId": {"value": "GLOBAL", "validator": "ID",
                                   "allowGlobalKeyword": True},
            "workflowId": {"value": value, "validator": "ID"},
        })
        assert not valid, f"workflowId {value!r} ({why}) must be rejected even behind a GLOBAL scope"
        assert "workflowId" in message, (
            f"the message must name the field that failed; got {message!r}")

    @pytest.mark.parametrize("value,why", MALFORMED_IDS)
    def test_and_is_still_validated_when_global_is_ordered_last(self, value, why):
        # The same call sites exist in both orders (executionService lists the id first), so neither
        # order may depend on the other for coverage.
        valid, _ = validate({
            "workflowId": {"value": value, "validator": "ID"},
            "workflowDatabaseId": {"value": "GLOBAL", "validator": "ID",
                                   "allowGlobalKeyword": True},
        })
        assert not valid, f"workflowId {value!r} ({why}) must be rejected before a GLOBAL scope too"

    def test_a_third_field_after_a_global_one_is_reached(self):
        valid, message = validate({
            "databaseId": {"value": "GLOBAL", "validator": "ID", "allowGlobalKeyword": True},
            "pipelineId": {"value": "my-pipeline", "validator": "ID"},
            "assetId": {"value": "a/b", "validator": "ASSET_ID"},
        })
        assert not valid, "the walk must continue past a GLOBAL field to every later field"
        assert "assetId" in message


@pytest.mark.unit
class TestTheKeywordItselfStillBehaves:
    """Narrowing the branch to one field must not change whether the keyword is accepted at all."""

    def test_global_alone_is_accepted(self):
        valid, message = validate({
            "databaseId": {"value": "GLOBAL", "validator": "ID", "allowGlobalKeyword": True},
        })
        assert valid, message

    def test_global_beside_a_well_formed_id_is_accepted(self):
        valid, message = validate({
            "workflowDatabaseId": {"value": "GLOBAL", "validator": "ID",
                                   "allowGlobalKeyword": True},
            "workflowId": {"value": "my-workflow", "validator": "ID"},
        })
        assert valid, message

    @pytest.mark.parametrize("rule", ["ID", "OBJECT_NAME", "STRING_256", "REGEX"])
    def test_every_rule_a_global_call_site_declares_still_accepts_the_keyword(self, rule):
        # roleConstraints validates criteria values as STRING_256 and REGEX with the keyword allowed;
        # metadataSchema and the orchestration services use ID; OBJECT_NAME appears on name fields.
        valid, message = validate({
            "field": {"value": "GLOBAL", "validator": rule, "allowGlobalKeyword": True},
        })
        assert valid, f"{rule} must still accept the GLOBAL keyword; got {message!r}"

    @pytest.mark.parametrize("value", ["global", "Global", " global ", "gLoBaL"])
    def test_an_uncapitalized_keyword_is_still_refused_where_it_is_allowed(self, value):
        valid, message = validate({
            "databaseId": {"value": value, "validator": "ID", "allowGlobalKeyword": True},
        })
        assert not valid
        assert "GLOBAL must be capitalized" in message

    @pytest.mark.parametrize("value", ["GLOBAL", "global", "Global"])
    def test_the_keyword_is_still_refused_where_it_is_not_allowed(self, value):
        valid, message = validate({
            "assetId": {"value": value, "validator": "ID"},
        })
        assert not valid
        assert "GLOBAL is not allowed" in message

    def test_a_field_after_a_refused_keyword_is_not_reported_valid(self):
        # An uncapitalized keyword short-circuits with its own error, which is correct: the request
        # already failed. What must never happen is a True on that request.
        valid, _ = validate({
            "databaseId": {"value": "global", "validator": "ID", "allowGlobalKeyword": True},
            "workflowId": {"value": "my-workflow", "validator": "ID"},
        })
        assert not valid
