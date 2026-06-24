# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestDiscoverChildren:
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.asset_links_table")
    def test_finds_direct_children(self, mock_table):
        mock_table.query.return_value = {
            "Items": [
                {
                    "fromAssetDatabaseId": "db1",
                    "fromAssetId": "parent",
                    "toAssetDatabaseId": "db1",
                    "toAssetId": "child1",
                },
                {
                    "fromAssetDatabaseId": "db1",
                    "fromAssetId": "parent",
                    "toAssetDatabaseId": "db2",
                    "toAssetId": "child2",
                },
            ]
        }

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            discover_children,
        )

        result = discover_children("db1", "parent")
        assert len(result) == 2
        assert result[0] == {"databaseId": "db1", "assetId": "child1"}
        assert result[1] == {"databaseId": "db2", "assetId": "child2"}

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.asset_links_table")
    def test_returns_empty_when_no_children(self, mock_table):
        mock_table.query.return_value = {"Items": []}

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            discover_children,
        )

        result = discover_children("db1", "parent")
        assert result == []


@pytest.mark.unit
class TestDiscoverAllDescendants:
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.discover_children")
    def test_bfs_traversal_finds_all_descendants(self, mock_discover):
        mock_discover.side_effect = [
            [{"databaseId": "db1", "assetId": "child1"}],
            [{"databaseId": "db1", "assetId": "grandchild1"}],
            [],
        ]

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            discover_all_descendants,
        )

        result = discover_all_descendants("db1", "root")
        assert len(result) == 2
        keys = {f"{d['databaseId']}:{d['assetId']}" for d in result}
        assert "db1:child1" in keys
        assert "db1:grandchild1" in keys

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.discover_children")
    def test_avoids_revisiting_nodes(self, mock_discover):
        mock_discover.side_effect = [
            [
                {"databaseId": "db1", "assetId": "child1"},
                {"databaseId": "db1", "assetId": "child2"},
            ],
            [{"databaseId": "db1", "assetId": "child2"}],
            [],
        ]

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            discover_all_descendants,
        )

        result = discover_all_descendants("db1", "root")
        assert len(result) == 2


@pytest.mark.unit
class TestTopologicalSort:
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor._get_parent_keys")
    def test_sorts_in_dependency_order(self, mock_parents):
        def parent_lookup(db_id, asset_id):
            if asset_id == "child1":
                return ["db1:root"]
            elif asset_id == "grandchild1":
                return ["db1:child1"]
            return []

        mock_parents.side_effect = parent_lookup

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            topological_sort,
        )

        descendants = [
            {"databaseId": "db1", "assetId": "child1"},
            {"databaseId": "db1", "assetId": "grandchild1"},
        ]

        result = topological_sort("db1", "root", descendants)
        keys = [f"{n['databaseId']}:{n['assetId']}" for n in result]
        assert keys.index("db1:child1") < keys.index("db1:grandchild1")

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor._get_parent_keys")
    def test_excludes_source_from_result(self, mock_parents):
        mock_parents.return_value = ["db1:root"]

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            topological_sort,
        )

        descendants = [{"databaseId": "db1", "assetId": "child1"}]

        result = topological_sort("db1", "root", descendants)
        keys = [f"{n['databaseId']}:{n['assetId']}" for n in result]
        assert "db1:root" not in keys


@pytest.mark.unit
class TestExecuteCascade:
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.cascade_table")
    def test_cascade_not_found(self, mock_table):
        mock_table.get_item.return_value = {}

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            execute_cascade,
        )

        result = execute_cascade("cascade-123")
        assert result == {"error": "Cascade not found"}

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.cascade_table")
    def test_cascade_not_in_executing_state(self, mock_table):
        mock_table.get_item.return_value = {
            "Item": {
                "cascadeId": "cascade-123",
                "state": "pending_approval",
            }
        }

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            execute_cascade,
        )

        result = execute_cascade("cascade-123")
        assert result == {"error": "Cascade not in executing state"}

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.audit_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.cascade_table")
    @patch(
        "backend.backend.handlers.fmm.fmmCascadeExecutor.discover_all_descendants"
    )
    def test_completes_with_no_descendants(
        self, mock_descendants, mock_cascade, mock_audit
    ):
        mock_cascade.get_item.return_value = {
            "Item": {
                "cascadeId": "cascade-123",
                "state": "executing",
                "triggeredByDatabaseId": "db1",
                "triggeredByAssetId": "root",
            }
        }
        mock_descendants.return_value = []

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            execute_cascade,
        )

        result = execute_cascade("cascade-123")
        assert result["status"] == "completed"
        assert result["evaluated"] == 0

    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.audit_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeExecutor.cascade_table")
    @patch(
        "backend.backend.handlers.fmm.fmmCascadeExecutor.discover_all_descendants"
    )
    @patch(
        "backend.backend.handlers.fmm.fmmCascadeExecutor.topological_sort"
    )
    @patch(
        "backend.backend.handlers.fmm.fmmCascadeExecutor._get_asset_schema"
    )
    def test_skips_nodes_without_schema(
        self, mock_schema, mock_topo, mock_descendants, mock_cascade, mock_audit
    ):
        import sys
        import types

        mock_eval_engine = types.ModuleType("handlers.fmm.fmmEvaluationEngine")
        mock_eval_engine.evaluate_asset = MagicMock(return_value={})
        sys.modules["handlers.fmm.fmmEvaluationEngine"] = mock_eval_engine

        mock_cascade.get_item.return_value = {
            "Item": {
                "cascadeId": "cascade-123",
                "state": "executing",
                "triggeredByDatabaseId": "db1",
                "triggeredByAssetId": "root",
            }
        }
        mock_descendants.return_value = [
            {"databaseId": "db1", "assetId": "child1"}
        ]
        mock_topo.return_value = [
            {"databaseId": "db1", "assetId": "child1"}
        ]
        mock_schema.return_value = ""

        from backend.backend.handlers.fmm.fmmCascadeExecutor import (
            execute_cascade,
        )

        result = execute_cascade("cascade-123")
        assert result["status"] == "completed"
        assert result["evaluated"] == 1
        assert result["results"][0]["status"] == "skipped"
