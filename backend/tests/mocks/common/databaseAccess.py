# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock of common.databaseAccess for tests that load a search handler by path without exercising the
database pre-filter.

Mirrors the real class's two static methods and their signatures. Both answer "no access", which is
the fail-safe direction and what the handlers' unpatched MagicMock paginator already yields. Tests of
the pre-filter itself load the real module by path (tests/common/test_databaseAccess.py,
tests/handlers/osSemanticSearch/test_database_prefilter_object_type.py).
"""

from typing import Any, Dict, List, Optional, Tuple


class DatabaseAccessManager:
    @staticmethod
    def get_accessible_database(database_id: str, claims_and_roles: Dict[str, Any]) -> Optional[str]:
        return None

    @staticmethod
    def get_accessible_databases(
        claims_and_roles: Dict[str, Any], show_deleted: bool = False, max_databases: int = 10000
    ) -> List[str]:
        return []

    @staticmethod
    def get_accessible_databases_with_count(
        claims_and_roles: Dict[str, Any], show_deleted: bool = False, max_databases: int = 10000
    ) -> Tuple[List[str], int]:
        return [], 0
