# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two VAMS object tags on a synced file version (`common.vamsObjectTags`): values S3 accepts,
the merge that keeps customer tags, the 10-tag limit, and a denied tagging call."""

import base64
import io
import json
import re
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from common import vamsObjectTags as tags

BUCKET = "asset-bucket"
KEY = "projects/alpha/model.glb"


def _customer(n):
    return [{"Key": f"team{i}", "Value": f"v{i}"} for i in range(n)]


def _vams(database_id="db1", asset_id="a1"):
    return [{"Key": tags.DATABASE_ID_TAG, "Value": database_id}, {"Key": tags.ASSET_ID_TAG, "Value": asset_id}]


def _client(existing=None, get_error=None, put_error=None):
    client = MagicMock(name="s3_client")
    if get_error is not None:
        client.get_object_tagging.side_effect = get_error
    else:
        client.get_object_tagging.return_value = {"TagSet": list(existing or [])}
    if put_error is not None:
        client.put_object_tagging.side_effect = put_error
    return client


def _denied(operation="GetObjectTagging"):
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"},
                        "ResponseMetadata": {"HTTPStatusCode": 403}}, operation)


def _metric_lines(output):
    return [json.loads(line) for line in output.splitlines() if line.startswith('{"_aws"')]


@pytest.fixture(autouse=True)
def _fresh_warnings():
    tags.reset_access_denied_warnings()
    yield
    tags.reset_access_denied_warnings()


@pytest.mark.unit
class TestTagValues:
    def test_the_allowed_character_set_is_pinned(self):
        assert tags.TAG_VALUE_PATTERN.pattern == r"^[A-Za-z0-9 +\-=._:/@]*$"
        for allowed in ["db-1", "a_1", "a 1", "a+b=c", "x.y:z/w@v"]:
            assert tags.encode_tag_value(allowed) == allowed

    @pytest.mark.parametrize("asset_id", ["it's", "a,b", "caf\u00e9", "b64:looks-encoded"])
    def test_an_id_outside_the_set_is_written_base64url_and_round_trips(self, asset_id):
        value = tags.encode_tag_value(asset_id)
        assert value.startswith("b64:")
        assert re.match(r"^b64:[A-Za-z0-9_\-=]+$", value)
        assert tags.TAG_VALUE_PATTERN.match(value)
        assert tags.decode_tag_value(value) == asset_id
        assert value == "b64:" + base64.urlsafe_b64encode(asset_id.encode()).decode()

    def test_a_value_over_256_characters_is_left_off(self):
        assert tags.encode_tag_value("a" * 256) == "a" * 256
        assert tags.encode_tag_value("a" * 257) is None
        assert tags.encode_tag_value("," * 200) is None

    @pytest.mark.parametrize("registration,expected", [
        ({}, True), (None, True), ({"tagSyncedObjects": True}, True),
        ({"tagSyncedObjects": False}, False), ({"tagSyncedObjects": "false"}, False)])
    def test_the_registration_switch(self, registration, expected):
        assert tags.tagging_enabled(registration) is expected


@pytest.mark.unit
class TestTagWrites:
    def test_missing_tags_are_written_with_the_events_version(self):
        client = _client(existing=_customer(2))
        outcome = tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1")
        assert outcome == tags.TagOutcome("written")
        client.get_object_tagging.assert_called_once_with(Bucket=BUCKET, Key=KEY, VersionId="v7")
        client.put_object_tagging.assert_called_once_with(
            Bucket=BUCKET, Key=KEY, VersionId="v7", Tagging={"TagSet": _customer(2) + _vams()})

    def test_tags_already_present_are_not_written_again(self):
        client = _client(existing=_customer(1) + _vams())
        assert tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1") == tags.TagOutcome("already")
        client.put_object_tagging.assert_not_called()

    def test_a_missing_key_is_added_and_customer_tags_are_kept(self):
        client = _client(existing=_customer(3) + _vams()[:1])
        assert tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1").status == "written"
        written = client.put_object_tagging.call_args.kwargs["Tagging"]["TagSet"]
        assert written == _customer(3) + _vams()

    def test_tags_naming_another_asset_are_rewritten(self):
        """A file copied in S3 from another asset's folder arrives with that asset's tags."""
        client = _client(existing=_customer(1) + _vams("db2", "other"))
        assert tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1").status == "written"
        assert client.put_object_tagging.call_args.kwargs["Tagging"]["TagSet"] == _customer(1) + _vams()

    def test_an_unversioned_object_is_tagged_without_a_version(self):
        client = _client()
        tags.tag_synced_version(client, BUCKET, KEY, "null", "db1", "a1")
        assert "VersionId" not in client.get_object_tagging.call_args.kwargs
        assert "VersionId" not in client.put_object_tagging.call_args.kwargs

    def test_eight_customer_tags_leave_room(self):
        client = _client(existing=_customer(8))
        assert tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1").status == "written"

    @pytest.mark.parametrize("count", [9, 10])
    def test_nine_or_ten_customer_tags_skip_without_removing_any(self, count):
        client = _client(existing=_customer(count))
        out = io.StringIO()
        with redirect_stdout(out):
            outcome = tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1")
        assert outcome == tags.TagOutcome("skipped", "tagLimit")
        client.put_object_tagging.assert_not_called()
        [metric] = _metric_lines(out.getvalue())
        assert (metric["Reason"], metric["VamsTagsSkipped"]) == ("tagLimit", 1)
        assert metric["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "VAMS/BucketSync"

    def test_an_asset_id_with_a_comma_or_quote_is_written_encoded(self):
        client = _client()
        tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "it's,here")
        written = dict((t["Key"], t["Value"]) for t in client.put_object_tagging.call_args.kwargs["Tagging"]["TagSet"])
        assert written[tags.DATABASE_ID_TAG] == "db1"
        assert tags.decode_tag_value(written[tags.ASSET_ID_TAG]) == "it's,here"

    def test_a_too_long_asset_value_is_left_off_and_a_stale_one_removed(self):
        client = _client(existing=_customer(1) + _vams("db2", "other"))
        out = io.StringIO()
        with redirect_stdout(out):
            outcome = tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "," * 200)
        assert outcome == tags.TagOutcome("written", "valueTooLong")
        assert client.put_object_tagging.call_args.kwargs["Tagging"]["TagSet"] == \
            _customer(1) + [{"Key": tags.DATABASE_ID_TAG, "Value": "db1"}]
        assert [m["Reason"] for m in _metric_lines(out.getvalue())] == ["valueTooLong"]


@pytest.mark.unit
class TestDeniedTagging:
    def test_access_denied_skips_and_warns_once_per_bucket_per_hour(self):
        clock = iter([1000.0, 1001.0, 1000.0 + 3601])
        with patch.object(tags.logger, "warning") as warning, \
                patch.object(tags.time, "monotonic", side_effect=lambda: next(clock)):
            outcomes = [tags.tag_synced_version(_client(get_error=_denied()), BUCKET, KEY, "v7", "db1", "a1")
                        for _ in range(3)]
        assert outcomes == [tags.TagOutcome("skipped", "accessDenied")] * 3
        assert warning.call_count == 2

    def test_a_denied_write_is_skipped(self):
        client = _client(put_error=_denied("PutObjectTagging"))
        assert tags.tag_synced_version(client, BUCKET, KEY, "v7", "db1", "a1") == \
            tags.TagOutcome("skipped", "accessDenied")

    def test_any_other_error_never_raises(self):
        throttled = ClientError({"Error": {"Code": "SlowDown", "Message": "slow"}}, "GetObjectTagging")
        assert tags.tag_synced_version(_client(get_error=throttled), BUCKET, KEY, "v7", "db1", "a1") == \
            tags.TagOutcome("skipped", "error")
