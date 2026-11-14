# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The failed-record warning names the records it redrives, since the event itself is no longer logged."""

from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.indexing.test_indexer_batch_failure_reporting import _load_indexer


@pytest.mark.unit
class TestFailedRecordIdentifiers:
    @pytest.mark.parametrize("module_name", ["assetIndexer", "fileIndexer"])
    def test_failed_records_are_named_by_identifier(self, module_name):
        m = _load_indexer(module_name)
        mock_logger = MagicMock()
        with patch.object(m, "logger", mock_logger):
            m.with_batch_item_failures({}, {"Records": [{}]}, [{"itemIdentifier": "msg-7"}, {"itemIdentifier": "msg-8"}])
        text = "\n".join(repr(c) for c in mock_logger.mock_calls)
        assert "2 failed record(s)" in text and "msg-7" in text and "msg-8" in text
