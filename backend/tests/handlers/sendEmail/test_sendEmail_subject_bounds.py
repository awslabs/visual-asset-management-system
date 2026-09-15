#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""SNS Subject bounds and response shape in the sendEmail handler (S2-BACKEND-037).

The SNS ``Publish`` Subject must be text with no line breaks or control characters and
fewer than 100 characters. ``assetName`` is bounded only by ``OBJECT_NAME``
(``^[a-zA-Z0-9\\-._\\s]{1,256}$``), whose ``\\s`` class matches ``\\n``/``\\r``/``\\t`` and
whose length allows 256 characters, so the subject line built from it can violate both
constraints. ``Publish`` then raises ``InvalidParameterException`` and every
version-change notification for that asset is dropped, because the inner ``except`` only
logs.

``sanitize_sns_subject`` folds control characters to spaces and trims to
``SNS_SUBJECT_MAX_LENGTH``. The bound is 99, not 100: the documented constraint is "less
than 100 characters long", so a 100-character subject is still rejected.

The handler is invoked asynchronously (``InvocationType='Event'``) by five callers that
discard the result, so the response shape is a diagnostic rather than a contract. It is
pinned anyway: every path returns the response dict, including the asset-with-no-topic
case (``createAsset`` sets ``snsTopic`` on every asset and ``delete_subscription`` REMOVEs
it, so its absence means the asset's subscription was deleted) and the outer failure path.

Each narrowing case is paired with a positive control that publishes an ordinary asset
name verbatim, so a sanitizer that mangles every subject, or a topic guard that no-ops
unconditionally, fails the pair. The long-name case additionally asserts the message BODY
still carries the full name, pinning the trim to the Subject alone.
"""

import json

import pytest

from backend.backend.handlers.sendEmail import sendEmail

ASSET_ID = "test-asset-id"
DATABASE_ID = "test-database-id"
TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:test-topic"
VERSION_ID = "3"

# The documented SNS Subject bound: "less than 100 characters long".
SNS_SUBJECT_HARD_LIMIT = 100


class RecordingSns:
    """Stand-in for the SNS client recording each publish, optionally raising."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MessageId": "test-message-id"}


class RecordingDynamo:
    """Stand-in for the DynamoDB client returning fixed query Items, optionally raising."""

    def __init__(self, items, error=None):
        self.items = items
        self.error = error
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"Items": self.items}


def _asset_row(asset_name, version_id=VERSION_ID, topic_arn=TOPIC_ARN):
    """A projected asset row; topic_arn=None omits snsTopic as DynamoDB does when absent."""
    row = {
        "assetId": {"S": ASSET_ID},
        "assetName": {"S": asset_name},
        "currentVersionId": {"S": version_id},
    }
    if topic_arn is not None:
        row["snsTopic"] = {"S": topic_arn}
    return row


def _event():
    return {"assetId": ASSET_ID, "databaseId": DATABASE_ID}


def _invoke(monkeypatch, items, event=None, sns_error=None, dynamo_error=None):
    """Run lambda_handler against fixed query results and return (response, sns, dynamo)."""
    sns = RecordingSns(error=sns_error)
    dynamo = RecordingDynamo(items, error=dynamo_error)
    monkeypatch.setattr(sendEmail, "sns_client", sns)
    monkeypatch.setattr(sendEmail, "dynamodb_client", dynamo)
    response = sendEmail.lambda_handler(event if event is not None else _event(), None)
    return response, sns, dynamo


@pytest.mark.unit
class TestSubjectRespectsSnsBounds:
    """The published Subject stays inside the SNS length and control-character bounds."""

    def test_long_asset_name_is_trimmed_and_message_is_not(self, monkeypatch):
        asset_name = "A" * 120

        response, sns, _dynamo = _invoke(monkeypatch, [_asset_row(asset_name)])

        assert response["statusCode"] == 200
        assert sns.calls, 'no notification was published'
        assert len(sns.calls) <= 1, 'a second publish would be a duplicate email'
        subject = sns.calls[0]["Subject"]
        assert len(subject) < SNS_SUBJECT_HARD_LIMIT, (
            "SNS rejects a Subject of 100 characters or more")
        assert subject.startswith("[AAAA"), "the trim keeps the leading asset name"
        assert asset_name in sns.calls[0]["Message"], (
            "only the Subject is bounded; the message body keeps the full name")
        # The trim drops the version id from the Subject, so the body is the only place it
        # survives for a long-named asset.
        assert f"Current Version Number: {VERSION_ID}" in sns.calls[0]["Message"]

    def test_max_length_asset_name_is_trimmed(self, monkeypatch):
        # 256 is the longest assetName OBJECT_NAME admits.
        response, sns, _dynamo = _invoke(monkeypatch, [_asset_row("B" * 256)])

        assert response["statusCode"] == 200
        assert len(sns.calls[0]["Subject"]) < SNS_SUBJECT_HARD_LIMIT

    def test_control_characters_in_asset_name_are_folded(self, monkeypatch):
        # OBJECT_NAME's \s class admits these; SNS rejects a Subject containing them.
        response, sns, _dynamo = _invoke(
            monkeypatch, [_asset_row("Bad\nName\rWith\tTabs")])

        assert response["statusCode"] == 200
        subject = sns.calls[0]["Subject"]
        assert not any(character in subject for character in ("\n", "\r", "\t")), (
            "SNS rejects a Subject containing a line break or control character")
        assert subject == "[Bad Name With Tabs] - File or Asset Changed (3)"

    def test_ordinary_asset_name_publishes_verbatim(self, monkeypatch):
        """Positive control: a legitimate name is neither trimmed nor rewritten."""
        response, sns, _dynamo = _invoke(monkeypatch, [_asset_row("Turbine Blade")])

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Email sent successfully"
        assert sns.calls, 'no notification was published'
        assert len(sns.calls) <= 1, 'a second publish would be a duplicate email'
        assert sns.calls[0]["TopicArn"] == TOPIC_ARN
        assert sns.calls[0]["Subject"] == "[Turbine Blade] - File or Asset Changed (3)"
        assert "Turbine Blade" in sns.calls[0]["Message"]


@pytest.mark.unit
class TestSanitizeSnsSubject:
    """sanitize_sns_subject trims at the documented bound and leaves shorter text alone."""

    def test_length_boundary(self):
        assert sendEmail.SNS_SUBJECT_MAX_LENGTH < SNS_SUBJECT_HARD_LIMIT

        at_bound = "x" * sendEmail.SNS_SUBJECT_MAX_LENGTH
        assert sendEmail.sanitize_sns_subject(at_bound) == at_bound

        over_bound = "x" * SNS_SUBJECT_HARD_LIMIT
        trimmed = sendEmail.sanitize_sns_subject(over_bound)
        assert len(trimmed) == sendEmail.SNS_SUBJECT_MAX_LENGTH
        assert len(trimmed) < SNS_SUBJECT_HARD_LIMIT

    def test_short_subject_passes_through(self):
        """Positive control: text already inside the bounds is returned byte-for-byte."""
        subject = "[Turbine Blade] - File or Asset Changed (12)"
        assert sendEmail.sanitize_sns_subject(subject) == subject

    def test_non_ascii_separators_are_folded(self):
        # U+2028 LINE SEPARATOR (category Zl) and U+200B ZERO WIDTH SPACE (category
        # Cf) sit outside the ASCII control range and are still not Subject-legal.
        folded = sendEmail.sanitize_sns_subject(
            "a" + chr(0x2028) + "b" + chr(0x200B) + "c")
        assert folded == "a b c"


@pytest.mark.unit
class TestResponseShapeOnEveryPath:
    """Every path returns the response dict rather than falling off the end as None."""

    def test_asset_without_sns_topic_is_a_no_op(self, monkeypatch):
        response, sns, _dynamo = _invoke(
            monkeypatch, [_asset_row("Turbine Blade", topic_arn=None)])

        assert isinstance(response, dict), "the missing-attribute path must not return None"
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "No subscribers to notify"
        assert sns.calls == [], "there is no topic to publish to"

    def test_asset_with_sns_topic_still_publishes(self, monkeypatch):
        """Positive control: the topic guard must not no-op an asset that has a topic."""
        response, sns, _dynamo = _invoke(monkeypatch, [_asset_row("Turbine Blade")])

        assert response["statusCode"] == 200
        assert sns.calls, 'no notification was published'
        assert len(sns.calls) <= 1, 'a second publish would be a duplicate email'

    def test_event_missing_asset_id_returns_a_response(self, monkeypatch):
        response, sns, dynamo = _invoke(
            monkeypatch, [_asset_row("Turbine Blade")], event={"databaseId": DATABASE_ID})

        assert isinstance(response, dict), "a malformed invocation must not raise out"
        assert response["statusCode"] == 500
        assert dynamo.calls == []
        assert sns.calls == []

    def test_dynamodb_failure_returns_a_response(self, monkeypatch):
        response, sns, _dynamo = _invoke(
            monkeypatch, [], dynamo_error=Exception("DynamoDB error"))

        assert isinstance(response, dict), "the outer failure path must not return None"
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
        assert sns.calls == []

    def test_asset_not_found_returns_400(self, monkeypatch):
        """Positive control: the not-found path is unchanged."""
        response, sns, _dynamo = _invoke(monkeypatch, [])

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == "Asset doesn't exist."
        assert sns.calls == []

    def test_publish_failure_returns_500(self, monkeypatch):
        """Positive control: a genuine SNS rejection is still reported, not swallowed."""
        response, sns, _dynamo = _invoke(
            monkeypatch, [_asset_row("Turbine Blade")],
            sns_error=Exception("InvalidParameterException"))

        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
        assert sns.calls, 'no publish was attempted'
        assert len(sns.calls) <= 1, 'the failing path published more than once'
