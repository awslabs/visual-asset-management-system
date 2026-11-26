# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A refused create-version request answers with the one reason it was refused.

`CreateAssetVersionRequestModel`'s root validator checks the request as a whole (a file list or `useLatestFiles`,
not both, no file named twice). Pydantic v1 runs a plain root validator even after a field failed, without the
failed field in its values, so a request whose `files` entry was refused (a path made only of slashes, a `..`
segment) also answered "Either useLatestFiles must be true or a list of files must be provided", which is wrong
for that request. The root validator now runs only when every field passed.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError


def _model():
    import models.assetsV3 as m
    return m.CreateAssetVersionRequestModel


def _messages(**body):
    with pytest.raises(ValidationError) as refused:
        _model()(**body)
    return [error["msg"] for error in refused.value.errors()]


@pytest.mark.unit
class TestAFieldRefusalIsTheOneAnswer:
    @pytest.mark.parametrize("relative_key", ["/", "//"])
    def test_a_path_made_only_of_slashes(self, relative_key):
        assert _messages(comment="c", files=[{"relativeKey": relative_key, "versionId": "v-1"}]) == [
            "relativeKey must name a file"]

    def test_a_path_with_a_traversal_segment(self):
        [message] = _messages(comment="c", files=[{"relativeKey": "/a/../b.txt", "versionId": "v-1"}])
        assert "'..'" in message

    def test_a_missing_comment(self):
        assert _messages(files=[{"relativeKey": "/a.txt", "versionId": "v-1"}]) == ["field required"]

    def test_a_missing_comment_is_reported_before_the_request_rules(self):
        """The request rules are checked once the fields pass, so the file-list reason follows on a retry."""
        assert _messages() == ["field required"]


@pytest.mark.unit
class TestControlsTheRequestRulesStillApply:
    def test_no_file_list_and_no_latest_files(self):
        assert _messages(comment="c") == ["Either useLatestFiles must be true or a list of files must be provided"]

    def test_a_file_list_with_latest_files(self):
        assert _messages(comment="c", useLatestFiles=True, files=[{"relativeKey": "a.txt", "versionId": "v-1"}]) == [
            "When useLatestFiles is true, files list should not be provided"]

    def test_one_file_named_twice(self):
        assert _messages(comment="c", files=[{"relativeKey": "/a.txt", "versionId": "v-1"},
                                             {"relativeKey": "a.txt", "versionId": "v-2"}]) == [
            "Duplicate relative keys are not allowed"]

    def test_a_valid_request_parses(self):
        model = _model()(comment="c", files=[{"relativeKey": "/a.txt", "versionId": "v-1"}])
        assert [f.relativeKey for f in model.files] == ["/a.txt"]
