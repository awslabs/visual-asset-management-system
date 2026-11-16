# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Registry and extensibility contract of the audit search (test plan section 7).

Adding an audit group must be a descriptor plus the CDK log group. These tests fail when a registered
group lacks a parser, a label, an authorization mapping or a ``ResourceKeys`` entry, when an action
names a group that has no descriptor, when the writer emits an ``auditVersion`` no parser reads, and
when a group added through the add-on or the fork lane is not picked up by the filters, the query
builder, the authorization object and the parsers.
"""

import importlib
import os
import re
import types

import pytest
from auditSearchRecords import (
    GROUPS,
    REGISTRY,
    auditLogging,
    descriptor,
    one_action_message,
)

TYPES = importlib.import_module("common.auditActionTypes")
registryCheck = importlib.import_module("common.auditSearch.registryCheck")
queryBuilder = importlib.import_module("common.auditSearch.queryBuilder")
parsers = importlib.import_module("common.auditSearch.parsers")
legacy = importlib.import_module("common.auditSearch.parsers.legacy")
resourceNames = importlib.import_module("backend.backend.common.resourceNames")

KEYS = auditLogging.ResourceKeys
TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")


def _fake_group(key="auditFake", **overrides):
    values = dict(key=key, resource_key_name="AUDIT_LOG_FAKE", label="Fake", description="A fake group.",
                  prefix="FAKE", has_type_segment=True, legacy_parser="errors")
    values.update(overrides)
    return TYPES.AuditLogGroup(**values)


class _FakeKeys:
    """A ResourceKeys stand-in that has one extra entry, as a fork's resourceNames edit would."""
    AUDIT_LOG_FAKE = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditFake")

    def __getattr__(self, name):  # pragma: no cover - the class attribute lookup is used
        raise AttributeError(name)


@pytest.mark.unit
class TestDescriptorCompleteness:
    def test_the_merged_registry_is_complete(self):
        # The default lanes: core, add-on and custom resource-name keys. A misconfigured lane fails here.
        assert registryCheck.descriptor_problems(GROUPS.AUDIT_LOG_GROUPS) == []
        assert registryCheck.descriptor_problems(GROUPS.AUDIT_LOG_GROUPS, KEYS) == []

    def test_nine_core_groups(self):
        assert [g.key for g in GROUPS.CORE_AUDIT_LOG_GROUPS] == [
            "auditAuthentication", "auditAuthorization", "auditFileUpload", "auditFileDownload",
            "auditFileDownloadStreamed", "auditAuthOther", "auditAuthChanges", "auditActions", "auditErrors"]

    def test_every_group_key_is_a_valid_auditlog_constraint_value(self):
        for group in GROUPS.AUDIT_LOG_GROUPS:
            assert TOKEN.match(group.key), group.key

    def test_every_group_authorizes_as_an_auditlog_object(self):
        for group in GROUPS.AUDIT_LOG_GROUPS:
            assert GROUPS.authorization_object(group.key) == {"object__type": "auditLog",
                                                              "auditLogGroup": group.key}

    def test_group_keys_are_distinct_and_so_are_prefixes(self):
        keys = [g.key for g in GROUPS.AUDIT_LOG_GROUPS]
        prefixes = [g.prefix for g in GROUPS.AUDIT_LOG_GROUPS]
        assert len(set(keys)) == len(keys) and len(set(prefixes)) == len(prefixes)

    def test_pii_tags_are_on_the_auth_groups_that_hold_emails_and_sign_ins(self):
        tagged = {g.key for g in GROUPS.AUDIT_LOG_GROUPS if GROUPS.SENSITIVITY_CONTAINS_PII in g.sensitivity}
        assert tagged == {"auditAuthentication", "auditAuthOther", "auditAuthChanges"}

    def test_a_descriptor_without_a_parser_fails_the_check(self):
        problems = registryCheck.descriptor_problems([_fake_group(legacy_parser="noSuchAdapter")], _FakeKeys)
        assert any("no parser" in problem for problem in problems)

    def test_a_descriptor_without_a_label_fails_the_check(self):
        assert any("no label" in p for p in registryCheck.descriptor_problems([_fake_group(label="")], _FakeKeys))

    def test_a_descriptor_with_an_invalid_constraint_value_fails_the_check(self):
        problems = registryCheck.descriptor_problems([_fake_group(key="audit-fake")], _FakeKeys)
        assert any("auditLog constraint value" in p for p in problems)

    def test_a_descriptor_without_a_resource_key_fails_the_check(self):
        problems = registryCheck.descriptor_problems([_fake_group(resource_key_name="AUDIT_LOG_MISSING")], _FakeKeys)
        assert any("no ResourceKeys" in p for p in problems)

    def test_a_resource_key_naming_another_log_group_fails_the_check(self):
        class Keys:
            AUDIT_LOG_FAKE = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditOther")
        problems = registryCheck.descriptor_problems([_fake_group()], Keys)
        assert any("does not name" in p for p in problems)

    def test_a_complete_fake_group_passes(self):
        assert registryCheck.descriptor_problems([_fake_group()], _FakeKeys) == []

    def test_a_repeated_key_or_prefix_fails_the_check(self):
        problems = registryCheck.descriptor_problems([_fake_group(), _fake_group()], _FakeKeys)
        assert any("twice" in p for p in problems) and any("prefix is used" in p for p in problems)


@pytest.mark.unit
class TestGroupCoverage:
    def test_every_registered_action_names_a_described_group(self):
        known = set(GROUPS.audit_log_group_keys())
        specs = (list(REGISTRY.AUDIT_ROUTE_ACTIONS.values()) + list(REGISTRY.AUDIT_SECONDARY_ACTIONS)
                 + [system.spec for system in REGISTRY.AUDIT_SYSTEM_ACTIONS])
        assert specs
        for audit_spec in specs:
            assert audit_spec.group in known, f"{audit_spec.action} writes to undescribed group {audit_spec.group}"

    def test_every_audit_resource_key_has_a_descriptor_and_back(self):
        resource_keys = {name: value.param_key for name, value in vars(resourceNames.ResourceKeys).items()
                         if name.startswith("AUDIT_LOG_") and hasattr(value, "param_key")}
        described = {group.resource_key_name for group in GROUPS.AUDIT_LOG_GROUPS}
        assert set(resource_keys) == described

    def test_the_core_descriptors_match_the_cdk_audit_log_groups(self):
        # infra/common/resourceParamKeys.ts publishes the audit log groups the storage stack creates;
        # the core descriptors are the same set (add-on and fork groups are in their own CDK lanes).
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "infra", "common",
                            "resourceParamKeys.ts")
        with open(path, encoding="utf-8") as handle:
            cdk_keys = set(re.findall(r'"cloudwatchLogGroups/(audit\w+)"', handle.read()))
        assert cdk_keys == {group.key for group in GROUPS.CORE_AUDIT_LOG_GROUPS}

    def test_every_action_target_group_is_one_log_action_may_write(self):
        assert GROUPS.action_target_group_keys() == frozenset(REGISTRY.AUDIT_GROUPS)

    def test_the_resource_keys_helper_resolves_each_descriptor_to_its_log_group(self):
        for group, key in zip(GROUPS.AUDIT_LOG_GROUPS, GROUPS.resource_keys()):
            assert key.param_key == getattr(KEYS, group.resource_key_name).param_key
            assert key.param_key == f"cloudwatchLogGroups/{group.key}"


@pytest.mark.unit
class TestParserCoverage:
    def test_every_group_names_a_legacy_adapter(self):
        for group in GROUPS.AUDIT_LOG_GROUPS:
            assert group.legacy_parser in legacy.LEGACY_PARSERS

    def test_the_writer_version_has_an_adapter(self):
        assert auditLogging.AUDIT_RECORD_VERSION in parsers.CONTRACT_PARSERS

    def test_the_id_keys_of_the_row_model_are_the_writers(self):
        rowModel = importlib.import_module("common.auditSearch.rowModel")
        writer_keys = set(auditLogging.AUDIT_ID_FIELDS) - {"databaseId", "assetId", "filePath", "apiKeyId"}
        assert set(rowModel.OTHER_ID_KEYS) == writer_keys

    def test_the_redacted_text_matches_the_writers(self):
        redaction = importlib.import_module("common.auditSearch.redaction")
        from backend.backend.customLogging import logger as real_logger
        assert redaction.REDACTED == real_logger.REDACTED

    def test_every_registered_action_round_trips_through_the_real_formatter(self, writer):
        # Every action of every lane, written by log_action (database, asset and file ids given),
        # is read back with its group, action, outcome, entity type, user and ids.
        checked = 0
        for name, audit_spec in REGISTRY.AUDIT_ACTIONS.items():
            writer.module.calls.clear()
            message = one_action_message(writer, name, database_id="db1", asset_id="a1",
                                         file_path="dir/model.glb", item_count=3)
            group = descriptor(audit_spec.group)
            row = parsers.parse_record(message, group, "2026-11-16T12:00:00.000Z", len(message))
            assert row.parseStatus == "parsed", name
            assert row.formatVersion == "contract-v1", name
            assert row.groupKey == audit_spec.group and row.action == name, name
            assert row.outcome == "success", name
            assert row.entity.get("type") == audit_spec.entity_type, name
            assert row.user == "alice" and row.databaseId == "db1" and row.assetId == "a1", name
            assert row.filePath == "dir/model.glb", name
            assert row.request["requestId"] == "req-1", name
            assert row.recordId, name
            checked += 1
        assert checked == len(REGISTRY.AUDIT_ACTIONS) > 100

    def test_a_denial_round_trips_in_the_authorization_group(self, writer):
        message = one_action_message(writer, "assetDelete", outcome="denied", reason="forbidden",
                                     database_id="db1", asset_id="a1")
        row = parsers.parse_record(message, descriptor("auditAuthorization"), None, len(message))
        assert (row.groupKey, row.action, row.outcome, row.reason) == ("auditAuthorization", "assetDelete",
                                                                       "denied", "forbidden")


@pytest.mark.unit
class TestLanes:
    def test_an_addon_group_joins_the_merged_registry(self):
        merged = GROUPS.merge_audit_log_groups(GROUPS.CORE_AUDIT_LOG_GROUPS, (_fake_group(),), ())
        assert merged[-1].key == "auditFake" and len(merged) == len(GROUPS.CORE_AUDIT_LOG_GROUPS) + 1

    def test_a_later_lane_repeating_a_core_key_fails_loudly(self):
        # Mutation of the brief: the key auditActions added to the add-on lane.
        impostor = _fake_group(key="auditActions", prefix="IMPOSTOR")
        with pytest.raises(GROUPS.AuditLogGroupConfigurationError, match="auditActions"):
            GROUPS.merge_audit_log_groups(GROUPS.CORE_AUDIT_LOG_GROUPS, (impostor,), ())

    def test_a_repeated_key_in_the_custom_lane_fails_loudly(self):
        with pytest.raises(GROUPS.AuditLogGroupConfigurationError, match="twice"):
            GROUPS.merge_audit_log_groups(GROUPS.CORE_AUDIT_LOG_GROUPS, (_fake_group(),),
                                          (_fake_group(prefix="OTHER"),))

    def test_a_later_lane_repeating_a_core_prefix_fails_loudly(self):
        with pytest.raises(GROUPS.AuditLogGroupConfigurationError, match="prefix"):
            GROUPS.merge_audit_log_groups(GROUPS.CORE_AUDIT_LOG_GROUPS, (_fake_group(prefix="ACTIONS"),), ())

    def test_the_shipped_lanes_merge_without_error(self):
        assert GROUPS.merge_audit_log_groups(
            GROUPS.CORE_AUDIT_LOG_GROUPS, GROUPS.ADDON_AUDIT_LOG_GROUPS, GROUPS.CUSTOM_AUDIT_LOG_GROUPS
        ) == GROUPS.AUDIT_LOG_GROUPS

    def test_a_fork_file_without_the_custom_lane_still_imports(self, monkeypatch):
        monkeypatch.setattr(GROUPS, "_custom_audit_actions", types.SimpleNamespace())
        assert GROUPS._custom_lane() == ()

    def test_the_shipped_custom_file_defines_no_group_lane(self):
        custom = importlib.import_module("custom.customAuditActions")
        assert not hasattr(custom, "CUSTOM_AUDIT_LOG_GROUPS")
        assert GROUPS.CUSTOM_AUDIT_LOG_GROUPS == ()

    def test_a_fork_group_in_the_custom_lane_is_read_through_getattr(self, monkeypatch):
        monkeypatch.setattr(GROUPS, "_custom_audit_actions",
                            types.SimpleNamespace(CUSTOM_AUDIT_LOG_GROUPS=(_fake_group(),)))
        assert [g.key for g in GROUPS._custom_lane()] == ["auditFake"]

    def test_an_addon_group_keyed_in_the_addon_resource_lane_resolves(self, monkeypatch):
        addon_keys = importlib.import_module("common.addonResourceNames")
        key = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditFake")
        monkeypatch.setattr(addon_keys.AddonResourceKeys, "AUDIT_LOG_FAKE", key, raising=False)
        group = _fake_group()
        assert GROUPS.resource_key(group) is key
        assert registryCheck.descriptor_problems([group]) == []
        # It also lands in the merged registry the writer derives its keys from.
        merged = GROUPS.merge_audit_log_groups(GROUPS.CORE_AUDIT_LOG_GROUPS, (group,), ())
        monkeypatch.setattr(GROUPS, "AUDIT_LOG_GROUPS", merged)
        assert GROUPS.resource_keys()[-1] is key

    def test_a_fork_group_keyed_in_the_custom_resource_lane_resolves(self, monkeypatch):
        custom_keys = importlib.import_module("custom.customResourceNames")
        key = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditFake")
        monkeypatch.setattr(custom_keys.CustomResourceKeys, "AUDIT_LOG_FAKE", key, raising=False)
        group = _fake_group()
        assert GROUPS.resource_key(group) is key
        assert registryCheck.descriptor_problems([group]) == []

    def test_the_lanes_resolve_in_order_core_addon_custom(self, monkeypatch):
        addon_keys = importlib.import_module("common.addonResourceNames")
        custom_keys = importlib.import_module("custom.customResourceNames")
        addon = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditFake")
        custom = types.SimpleNamespace(param_key="cloudwatchLogGroups/auditFake")
        monkeypatch.setattr(addon_keys.AddonResourceKeys, "AUDIT_LOG_FAKE", addon, raising=False)
        monkeypatch.setattr(custom_keys.CustomResourceKeys, "AUDIT_LOG_FAKE", custom, raising=False)
        assert GROUPS.resource_key(_fake_group()) is addon
        # A name on the core lane is never taken from a later lane.
        shadow = types.SimpleNamespace(param_key="cloudwatchLogGroups/shadow")
        monkeypatch.setattr(addon_keys.AddonResourceKeys, "AUDIT_LOG_ACTIONS", shadow, raising=False)
        core = _fake_group(key="auditActions", resource_key_name="AUDIT_LOG_ACTIONS")
        assert GROUPS.resource_key(core).param_key == "cloudwatchLogGroups/auditActions"

    def test_a_group_with_no_resource_key_on_any_lane_fails_closed_and_the_check_reports_it(self):
        group = _fake_group(resource_key_name="AUDIT_LOG_NOWHERE")
        with pytest.raises(GROUPS.AuditLogGroupConfigurationError, match="AUDIT_LOG_NOWHERE"):
            GROUPS.resource_key(group)
        assert any("no ResourceKeys" in p for p in registryCheck.descriptor_problems([group]))

    def test_every_addon_and_custom_audit_resource_key_has_a_descriptor(self):
        addon_keys = importlib.import_module("common.addonResourceNames")
        custom_keys = importlib.import_module("custom.customResourceNames")
        described = {group.resource_key_name for group in GROUPS.AUDIT_LOG_GROUPS}
        for lane in (addon_keys.AddonResourceKeys, custom_keys.CustomResourceKeys):
            for name in vars(lane):
                if name.startswith("AUDIT_LOG_"):
                    assert name in described, f"{lane.__name__}.{name} has no AuditLogGroup descriptor"

    def test_the_addon_lane_ships_empty(self):
        addon = importlib.import_module("common.addonAuditActions")
        assert addon.ADDON_AUDIT_LOG_GROUPS == ()

    def test_a_synthetic_group_is_filterable_queryable_authorizable_and_parsable(self):
        fake = _fake_group()
        registry = {fake.key: fake, **GROUPS.AUDIT_LOG_GROUPS_BY_KEY}
        filters = queryBuilder.validate_filters(
            {"logGroups": ["auditFake"], "startTime": 1763000000, "endTime": 1763003600},
            allowed_group_keys=["auditFake", "auditActions"], groups=registry)
        assert filters.group_keys == ("auditFake",)
        plan = queryBuilder.build_query(filters, groups=registry)
        assert "like /^\\[(FAKE)\\]/" in plan.query_string
        assert GROUPS.authorization_object("auditFake")["auditLogGroup"] == "auditFake"
        record = ('[FAKE][type: x] [user: alice] [roles: []] [mfaEnabled: True] {"error": "e", "assetId": "a-1"}')
        row = parsers.parse_record(record, fake, "2026-11-16T12:00:00.000Z", len(record))
        assert (row.groupKey, row.parseStatus, row.outcome, row.assetId) == ("auditFake", "parsed", "failed", "a-1")

    def test_a_synthetic_group_is_refused_when_not_registered(self):
        with pytest.raises(queryBuilder.AuditSearchValidationError):
            queryBuilder.validate_filters({"logGroups": ["auditFake"], "startTime": 1763000000,
                                           "endTime": 1763003600}, allowed_group_keys=["auditFake"])


@pytest.mark.unit
class TestMisconfiguredLaneFailsAtImport:
    """A misconfigured lane stops the import of the registry (and so of the writer) with a clear error."""

    _BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

    def _run(self, groups_source, then):
        import subprocess
        import sys
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import common.addonAuditActions as addon\n"
            "from common.auditActionTypes import AuditLogGroup\n"
            "addon.ADDON_AUDIT_LOG_GROUPS = %s\n"
            "import common.auditLogGroups as g\n%s\n" % (self._BACKEND, groups_source, then)
        )
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def test_a_duplicate_key_in_the_addon_lane_fails_the_import(self):
        code, output = self._run(
            "(AuditLogGroup('auditActions', 'AUDIT_LOG_ACTIONS', 'x', 'x', 'DUP', True, 'errors'),)", "")
        assert code != 0 and "AuditLogGroupConfigurationError" in output and "auditActions" in output

    def test_an_unknown_resource_key_fails_the_derivation_the_writer_runs_at_import(self):
        group = "(AuditLogGroup('auditFake', 'AUDIT_LOG_NOWHERE', 'x', 'x', 'FAKE', True, 'errors'),)"
        code, output = self._run(group, "g.resource_keys()")
        assert code != 0 and "AUDIT_LOG_NOWHERE" in output

    def test_a_valid_addon_group_with_a_resource_key_imports(self):
        code, output = self._run(
            "(AuditLogGroup('auditFake', 'EDH_SESSION_STORAGE_TABLE', 'x', 'x', 'FAKE', True, 'errors'),)",
            "assert g.audit_log_group_keys()[-1] == 'auditFake'; assert g.resource_keys()[-1].param_key")
        assert code == 0, output
