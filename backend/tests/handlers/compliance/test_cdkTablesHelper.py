# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The CDK table reader the compliance moto tests build their tables from (`_cdkTables`) reads every
table and index the storage stack declares, including an index whose `indexName` is a file-level
string constant rather than a literal (the workflow execution locks table's holder index). A reader
that only understood literals stopped at that index, and every test built on it failed to start."""

import pytest

from backend.tests.handlers.compliance import _cdkTables
from common.resourceNames import ResourceKeys


@pytest.mark.unit
def test_an_index_named_by_a_constant_is_read_with_its_value():
    locks = _cdkTables.table_for_resource_key(ResourceKeys.WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE)
    assert locks.partition == ("lockKey", "S")
    kind, partition, sort = locks.indexes["WorkflowExecutionLocksByHolderGSI"]
    assert (kind, partition, sort) == ("Global", ("workflowExecutionId", "S"), None)


@pytest.mark.unit
def test_the_compliance_tables_and_their_indexes_are_read():
    audit = _cdkTables.table_for_resource_key(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    assert {"AssetIndex", "EventTypeIndex", "AuditByDateGSI"} <= set(audit.indexes)
    state = _cdkTables.table_for_resource_key(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    assert {"SchemaNameIndex", "ComplianceStateIndex", "DatabaseAssetNameIndex"} <= set(state.indexes)
