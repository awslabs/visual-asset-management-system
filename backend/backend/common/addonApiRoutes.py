# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
API routes served by the optional add-ons (``infra/lib/nestedStacks/addon/``).

Spliced into ``ALL_API_ROUTES`` by ``common/apiRoutes.py``, which also re-exports every name here.
An add-on route is declared whether or not its add-on is deployed; the CDK stack that registers it
with API Gateway is the one gated on ``app.addons.*``.
"""

from typing import Tuple

from common.apiRouteTypes import GET, ApiRoute

# ---------------------------------------------------------------------------
# Physna
# ---------------------------------------------------------------------------
API_ADDON_PHYSNA_VIEWER = ApiRoute("/addon/physna/viewer", (GET,), "addons")

ADDON_ROUTES: Tuple[ApiRoute, ...] = (API_ADDON_PHYSNA_VIEWER,)
