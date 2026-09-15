# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.compliance.quarantineGuard: the one quarantine-blocks-download check the four asset
download handlers share.

Two halves. The call-time contract of ``check_quarantine_block`` -- a no-op when the block is off,
a pass for a missing row, any non-quarantined state or a granted exception, and a
``VAMSGeneralErrorResponse`` carrying the fixed message for a quarantined row with no exception.
And the import-time contract: the compliance asset-state table is resolved only when
``COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD`` is ``true`` (through ``get_table_name``, so the
``COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME`` override wins over SSM), and a name that cannot be
resolved while the block is on fails the import -- a misconfigured deployment surfaces at cold
start rather than as an unguarded download.
"""

import importlib.util
import itertools
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard
from models.common import VAMSGeneralErrorResponse

_GUARD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "compliance",
    "quarantineGuard.py")

_DB = "db1"
_ASSET = "asset1"

_fresh_load_counter = itertools.count()


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _row(state="quarantined", **extra):
    row = {"databaseId": _DB, "assetId": _ASSET, "complianceState": state}
    row.update(extra)
    return row


def _enabled(table):
    return patch.multiple(guard, quarantine_blocks_download=True,
                          compliance_asset_state_table=table)


@pytest.mark.unit
class TestCheckQuarantineBlock:

    def test_disabled_block_is_a_no_op_with_no_table_bound(self):
        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=None):
            assert guard.check_quarantine_block(_DB, _ASSET) is None

    def test_disabled_block_reads_nothing_even_with_a_table_bound(self):
        table = _state_table(_row())
        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=table):
            guard.check_quarantine_block(_DB, _ASSET)
        table.get_item.assert_not_called()

    def test_missing_row_passes(self):
        table = _state_table(None)
        with _enabled(table):
            guard.check_quarantine_block(_DB, _ASSET)
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})

    @pytest.mark.parametrize("state", ["compliant", "warning", "pending_pipeline", "unknown"])
    def test_non_quarantined_state_passes(self, state):
        with _enabled(_state_table(_row(state))):
            guard.check_quarantine_block(_DB, _ASSET)

    def test_quarantined_without_exception_raises_the_shared_message(self):
        with _enabled(_state_table(_row())):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                guard.check_quarantine_block(_DB, _ASSET)
        assert str(raised.value).endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        assert raised.value.status_code == 400

    def test_quarantined_with_exception_explicitly_false_raises(self):
        with _enabled(_state_table(_row(exceptionGranted=False))):
            with pytest.raises(VAMSGeneralErrorResponse):
                guard.check_quarantine_block(_DB, _ASSET)

    def test_quarantined_with_granted_exception_passes(self):
        table = _state_table(_row(exceptionGranted=True))
        with _enabled(table):
            guard.check_quarantine_block(_DB, _ASSET)
        table.get_item.assert_called_once()

    def test_the_message_names_no_asset(self):
        """Rule 11: the client message is fixed and carries neither identifier."""
        assert _DB not in guard.QUARANTINE_BLOCK_MESSAGE
        assert _ASSET not in guard.QUARANTINE_BLOCK_MESSAGE
        with _enabled(_state_table(_row())):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                guard.check_quarantine_block(_DB, _ASSET)
        assert _DB not in str(raised.value)
        assert _ASSET not in str(raised.value)

    def test_the_read_is_a_keyed_get_item(self):
        """A single GetItem on the row's key, never a filtered query or scan."""
        table = _state_table(_row())
        with _enabled(table):
            with pytest.raises(VAMSGeneralErrorResponse):
                guard.check_quarantine_block(_DB, _ASSET)
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})
        table.query.assert_not_called()
        table.scan.assert_not_called()


def _load_fresh(env, get_table_name=None):
    """Load the guard source under a fresh module name with ``env`` applied, so the import-time
    branch under test runs rather than the cached module's."""
    resource_names = sys.modules["common.resourceNames"]
    patches = [patch.dict(os.environ, env, clear=False)]
    if get_table_name is not None:
        patches.append(patch.object(resource_names, "get_table_name", get_table_name))
    for p in patches:
        p.start()
    try:
        spec = importlib.util.spec_from_file_location(
            f"quarantineGuard_fresh_{next(_fresh_load_counter)}", os.path.abspath(_GUARD_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.mark.unit
class TestImportTimeResolution:
    """backend Rule 10: the table is resolved once at import, and only when the block is on."""

    @pytest.mark.parametrize("value", ["true", "TRUE", "True"])
    def test_enabled_resolves_the_state_table_through_the_env_override(self, value):
        module = _load_fresh({
            "COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD": value,
            "COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME": "t-state-override",
        })
        assert module.quarantine_blocks_download is True
        assert module.compliance_asset_state_table_name == "t-state-override"
        assert module.compliance_asset_state_table.name == "t-state-override"

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "yes", ""])
    def test_any_value_but_true_leaves_the_block_off_and_resolves_no_name(self, value):
        resolver = MagicMock(side_effect=AssertionError("get_table_name must not be called"))
        module = _load_fresh({"COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD": value},
                             get_table_name=resolver)
        assert module.quarantine_blocks_download is False
        assert module.compliance_asset_state_table is None
        resolver.assert_not_called()

    def test_unset_leaves_the_block_off(self):
        env = dict(os.environ)
        env.pop("COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD", None)
        resolver = MagicMock(side_effect=AssertionError("get_table_name must not be called"))
        with patch.dict(os.environ, env, clear=True):
            module = _load_fresh({}, get_table_name=resolver)
        assert module.quarantine_blocks_download is False
        assert module.compliance_asset_state_table is None
        resolver.assert_not_called()

    def test_enabled_with_an_unresolvable_name_fails_the_import(self):
        """A deployment that turns the block on without a resolvable table name fails at cold
        start, as the handlers do for every required resource -- never as a guard that silently
        lets every download through."""
        resolver = MagicMock(side_effect=KeyError("Resource name parameter not found in SSM"))
        with pytest.raises(KeyError):
            _load_fresh({"COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD": "true"},
                        get_table_name=resolver)
        resolver.assert_called_once()

    def test_the_dynamodb_resource_carries_the_rule_6_retry_config(self):
        assert guard.retry_config.retries == {"max_attempts": 5, "mode": "adaptive"}
