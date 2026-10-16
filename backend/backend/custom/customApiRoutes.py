# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

API routes a fork adds. ``common/apiRoutes.py`` splices ``CUSTOM_ROUTES`` into ``ALL_API_ROUTES``,
so each route here is served by the ``GET /auth/routes/api`` listing and can be matched by a fork
handler with :meth:`ApiRoute.matches`. Declare each route as an ``ApiRoute`` constant and list it in
``CUSTOM_ROUTES``; register the same method and path with API Gateway from
``infra/lib/nestedStacks/custom/customResources.ts`` through ``attachFunctionToApi``.

A method and path that duplicate a core route fail at synth (``RouteRegistry.register``). Paths
under ``/custom/`` are recommended so a later core route does not collide with one declared here.
"""

from typing import Tuple

from common.apiRouteTypes import ApiRoute

CUSTOM_ROUTES: Tuple[ApiRoute, ...] = ()
