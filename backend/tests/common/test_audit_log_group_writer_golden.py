# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Golden test: the audit writer's group constants are derived from the group registry and equal what
they were when they were written out by hand.

``_AUDIT_LOG_GROUP_KEYS`` and ``_GROUP_LABELS`` (``customLogging/auditLogging.py``) and the set of
groups ``log_action`` may write to (``AUDIT_GROUPS``) come from ``common/auditLogGroups.py``. The
literals below are the values before the derivation; a change to one of them is a change to what the
writer does and must be made on purpose.
"""

import importlib
import os

import pytest

for _log_type, _env in (("auditActions", "AUDIT_LOG_ACTIONS"), ("auditAuthorization", "AUDIT_LOG_AUTHORIZATION"),
                        ("auditFileUpload", "AUDIT_LOG_FILEUPLOAD"), ("auditFileDownload", "AUDIT_LOG_FILEDOWNLOAD"),
                        ("auditFileDownloadStreamed", "AUDIT_LOG_FILEDOWNLOAD_STREAMED"),
                        ("auditAuthOther", "AUDIT_LOG_AUTHOTHER"), ("auditAuthChanges", "AUDIT_LOG_AUTHCHANGES"),
                        ("auditErrors", "AUDIT_LOG_ERRORS"), ("auditAuthentication", "AUDIT_LOG_AUTHENTICATION")):
    os.environ.setdefault(_env, f"test-{_log_type}")

from backend.backend.customLogging import auditLogging  # noqa: E402

TYPES = importlib.import_module("common.auditActionTypes")
REGISTRY = importlib.import_module("common.auditActions")

GOLDEN_GROUP_PARAM_KEYS = (
    "cloudwatchLogGroups/auditAuthentication",
    "cloudwatchLogGroups/auditAuthorization",
    "cloudwatchLogGroups/auditFileUpload",
    "cloudwatchLogGroups/auditFileDownload",
    "cloudwatchLogGroups/auditFileDownloadStreamed",
    "cloudwatchLogGroups/auditAuthOther",
    "cloudwatchLogGroups/auditAuthChanges",
    "cloudwatchLogGroups/auditActions",
    "cloudwatchLogGroups/auditErrors",
)

GOLDEN_GROUP_LABELS = {
    "auditActions": "ACTIONS",
    "auditAuthChanges": "AUTHCHANGES",
    "auditAuthOther": "AUTHOTHER",
    "auditAuthorization": "AUTHORIZATION",
    "auditErrors": "ERRORS",
    "auditFileUpload": "FILEUPLOAD",
    "auditFileDownload": "FILEDOWNLOAD",
    "auditFileDownloadStreamed": "FILEDOWNLOAD-STREAMED",
}

GOLDEN_GROUPS_WITHOUT_TYPE_SEGMENT = frozenset({"auditFileUpload", "auditFileDownload", "auditFileDownloadStreamed"})

GOLDEN_AUDIT_GROUPS = frozenset({
    "auditActions", "auditAuthChanges", "auditAuthOther", "auditAuthorization", "auditErrors",
    "auditFileUpload", "auditFileDownload", "auditFileDownloadStreamed",
})


@pytest.mark.unit
class TestWriterGroupConstantsGolden:
    def test_group_keys_and_their_order(self):
        assert tuple(key.param_key for key in auditLogging._AUDIT_LOG_GROUP_KEYS) == GOLDEN_GROUP_PARAM_KEYS

    def test_group_keys_are_the_resource_keys_objects(self):
        expected = (
            auditLogging.ResourceKeys.AUDIT_LOG_AUTHENTICATION, auditLogging.ResourceKeys.AUDIT_LOG_AUTHORIZATION,
            auditLogging.ResourceKeys.AUDIT_LOG_FILEUPLOAD, auditLogging.ResourceKeys.AUDIT_LOG_FILEDOWNLOAD,
            auditLogging.ResourceKeys.AUDIT_LOG_FILEDOWNLOAD_STREAMED, auditLogging.ResourceKeys.AUDIT_LOG_AUTHOTHER,
            auditLogging.ResourceKeys.AUDIT_LOG_AUTHCHANGES, auditLogging.ResourceKeys.AUDIT_LOG_ACTIONS,
            auditLogging.ResourceKeys.AUDIT_LOG_ERRORS,
        )
        assert all(a is b for a, b in zip(auditLogging._AUDIT_LOG_GROUP_KEYS, expected))
        assert len(auditLogging._AUDIT_LOG_GROUP_KEYS) == len(expected)

    def test_group_labels(self):
        assert dict(auditLogging._GROUP_LABELS) == GOLDEN_GROUP_LABELS

    def test_groups_without_a_type_segment(self):
        assert frozenset(auditLogging._GROUPS_WITHOUT_TYPE_SEGMENT) == GOLDEN_GROUPS_WITHOUT_TYPE_SEGMENT

    def test_keys_by_type(self):
        by_type = auditLogging._AUDIT_LOG_KEYS_BY_TYPE
        assert set(by_type) == {key.rsplit("/", 1)[-1] for key in GOLDEN_GROUP_PARAM_KEYS}

    def test_action_groups(self):
        assert frozenset(REGISTRY.AUDIT_GROUPS) == GOLDEN_AUDIT_GROUPS
        assert frozenset(TYPES.AUDIT_GROUPS) == GOLDEN_AUDIT_GROUPS

    @pytest.mark.parametrize("group,label", sorted(GOLDEN_GROUP_LABELS.items()))
    def test_action_prefix_is_unchanged(self, group, label):
        prefix = auditLogging._action_prefix(group, "assetUpdate", "success")
        if group in GOLDEN_GROUPS_WITHOUT_TYPE_SEGMENT:
            assert prefix == f"[{label}]"
        else:
            assert prefix == f"[{label}][type: assetUpdate]"
