# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Mock of common.indexing.geoLocation for testing.

Only build_geo_location is imported by the indexing handlers; the mock returns
None (no geo location) which is a valid result for any metadata input.
"""

from typing import Any, Dict, Optional


def build_geo_location(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return None
