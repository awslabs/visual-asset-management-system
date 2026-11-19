# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The methods each shipped permission template allows on each Miris Spatial Streaming route.

``tests/handlers/auth/test_permission_templates_addon_routes.py`` reads ``ROUTE_GRANTS`` from this file
and checks every entry against the real policy generation and Casbin evaluation of every template in
``documentation/permissionsTemplates``. A template absent from a route's entry allows no method on it.
A viewer key grants streaming only, so the read-only templates reach the viewer key route; its handler
still checks the asset as a read (Tier 2) before any key is returned.
"""

from backend.backend.common.addonApiRoutes import (
    API_ADDON_MIRIS_ASSET_STATUS,
    API_ADDON_MIRIS_VIEWER_KEY,
)

ROUTE_GRANTS = {
    API_ADDON_MIRIS_ASSET_STATUS: {
        "database-admin.json": ("GET",),
        "database-user.json": ("GET",),
        "database-readonly.json": ("GET",),
        "global-readonly.json": ("GET",),
    },
    API_ADDON_MIRIS_VIEWER_KEY: {
        "database-admin.json": ("POST",),
        "database-user.json": ("POST",),
        "database-readonly.json": ("POST",),
        "global-readonly.json": ("POST",),
    },
}
