# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
API routes served by the optional add-ons (``infra/lib/nestedStacks/addon/``).

Spliced into ``ALL_API_ROUTES`` by ``common/apiRoutes.py``, which also re-exports every name here.
An add-on route is declared whether or not its add-on is deployed; the CDK stack that registers it
with API Gateway is the one gated on ``app.addons.*``. Each add-on keeps its routes in its own
section below, and ``ADDON_ROUTES`` lists every one of them.
"""

from typing import Tuple

from common.apiRouteTypes import DELETE, GET, POST, PUT, ApiRoute

# ---------------------------------------------------------------------------
# Physna
# ---------------------------------------------------------------------------
API_ADDON_PHYSNA_VIEWER = ApiRoute("/addon/physna/viewer", (GET,), "addons")

# ---------------------------------------------------------------------------
# EDH Workstations
# ---------------------------------------------------------------------------
API_EDH_WORKSTATIONS = ApiRoute("/addon/edh/workstations", (GET, POST), "addons")
API_EDH_WORKSTATION = ApiRoute("/addon/edh/workstations/{sessionId}", (GET, DELETE), "addons")
API_EDH_WORKSTATION_STOP = ApiRoute("/addon/edh/workstations/{sessionId}/stop", (POST,), "addons")
API_EDH_WORKSTATION_START = ApiRoute("/addon/edh/workstations/{sessionId}/start", (POST,), "addons")
API_EDH_WORKSTATION_RENEW = ApiRoute("/addon/edh/workstations/{sessionId}/renew", (POST,), "addons")
API_EDH_WORKSTATION_CONNECT = ApiRoute("/addon/edh/workstations/{sessionId}/connect", (GET,), "addons")
API_EDH_WORKSTATION_WINDOWS_SIGN_IN = ApiRoute("/addon/edh/workstations/{sessionId}/windows-sign-in", (GET,), "addons")
API_EDH_CATALOG_IMAGES = ApiRoute("/addon/edh/catalog/images", (GET,), "addons")
API_EDH_CATALOG_INSTANCE_TYPES = ApiRoute("/addon/edh/catalog/instance-types", (GET,), "addons")
API_EDH_CATALOG_LEASE = ApiRoute("/addon/edh/catalog/lease", (GET,), "addons")
# The registered capacity reservations the caller may launch into, with their live EC2 view.
API_EDH_CATALOG_CAPACITY_RESERVATIONS = ApiRoute("/addon/edh/catalog/capacity-reservations", (GET,), "addons")

# Admin routes: image catalog administration, cluster-wide instance views and lifecycle, capacity
# and health. The DELETE templates carry the kind segment before the id so they sit one segment
# deeper than the register literals and cannot capture them.
API_EDH_ADMIN_IMAGES = ApiRoute("/addon/edh/admin/images", (GET,), "addons")
API_EDH_ADMIN_IMAGES_VDI = ApiRoute("/addon/edh/admin/images/vdi", (POST,), "addons")
API_EDH_ADMIN_IMAGES_SIMULATION = ApiRoute("/addon/edh/admin/images/simulation", (POST,), "addons")
API_EDH_ADMIN_IMAGE_VDI = ApiRoute("/addon/edh/admin/images/vdi/{imageId}", (DELETE,), "addons")
API_EDH_ADMIN_IMAGE_SIMULATION = ApiRoute("/addon/edh/admin/images/simulation/{imageId}", (DELETE,), "addons")
API_EDH_ADMIN_INSTANCES = ApiRoute("/addon/edh/admin/instances", (GET,), "addons")
API_EDH_ADMIN_INSTANCES_STALE = ApiRoute("/addon/edh/admin/instances/stale", (GET,), "addons")
API_EDH_ADMIN_INSTANCE_STOP = ApiRoute("/addon/edh/admin/instances/{sessionId}/stop", (POST,), "addons")
API_EDH_ADMIN_INSTANCE_DEACTIVATE = ApiRoute("/addon/edh/admin/instances/{sessionId}/deactivate", (POST,), "addons")
API_EDH_ADMIN_CAPACITY = ApiRoute("/addon/edh/admin/capacity", (GET,), "addons")
API_EDH_ADMIN_HEALTH = ApiRoute("/addon/edh/admin/health", (GET,), "addons")
API_EDH_ADMIN_SETTINGS = ApiRoute("/addon/edh/admin/settings", (GET, PUT), "addons")
# The capacity-reservation registry: list and register, then update or unregister one.
API_EDH_ADMIN_CAPACITY_RESERVATIONS = ApiRoute("/addon/edh/admin/capacity-reservations", (GET, POST), "addons")
API_EDH_ADMIN_CAPACITY_RESERVATION = ApiRoute(
    "/addon/edh/admin/capacity-reservations/{capacityReservationId}", (PUT, DELETE), "addons")
# AMI recipe routes: recipes, their builds, and one build's registration retry or retirement.
API_EDH_ADMIN_AMI_RECIPES = ApiRoute("/addon/edh/admin/ami-recipes", (GET, POST), "addons")
API_EDH_ADMIN_AMI_RECIPE = ApiRoute("/addon/edh/admin/ami-recipes/{recipeId}", (GET, PUT, DELETE), "addons")
API_EDH_ADMIN_AMI_RECIPE_BUILDS = ApiRoute("/addon/edh/admin/ami-recipes/{recipeId}/builds", (GET, POST), "addons")
API_EDH_ADMIN_AMI_RECIPE_BUILD = ApiRoute("/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}", (DELETE,), "addons")
API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER = ApiRoute("/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register", (POST,), "addons")
# Container routes: the Batch/EKS catalog, the caller's jobs, and one job by id.
API_EDH_CONTAINERS_CATALOG = ApiRoute("/addon/edh/containers/catalog", (GET,), "addons")
API_EDH_CONTAINERS_JOBS = ApiRoute("/addon/edh/containers/jobs", (GET, POST), "addons")
API_EDH_CONTAINERS_JOB = ApiRoute("/addon/edh/containers/jobs/{jobId}", (DELETE,), "addons")

EDH_ROUTES: Tuple[ApiRoute, ...] = (
    API_EDH_WORKSTATIONS,
    API_EDH_WORKSTATION,
    API_EDH_WORKSTATION_STOP,
    API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_RENEW,
    API_EDH_WORKSTATION_CONNECT,
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN,
    API_EDH_CATALOG_IMAGES,
    API_EDH_CATALOG_INSTANCE_TYPES,
    API_EDH_CATALOG_LEASE,
    API_EDH_CATALOG_CAPACITY_RESERVATIONS,
    API_EDH_ADMIN_IMAGES,
    API_EDH_ADMIN_IMAGES_VDI,
    API_EDH_ADMIN_IMAGES_SIMULATION,
    API_EDH_ADMIN_IMAGE_VDI,
    API_EDH_ADMIN_IMAGE_SIMULATION,
    API_EDH_ADMIN_INSTANCES,
    API_EDH_ADMIN_INSTANCES_STALE,
    API_EDH_ADMIN_INSTANCE_STOP,
    API_EDH_ADMIN_INSTANCE_DEACTIVATE,
    API_EDH_ADMIN_CAPACITY,
    API_EDH_ADMIN_HEALTH,
    API_EDH_ADMIN_SETTINGS,
    API_EDH_ADMIN_CAPACITY_RESERVATIONS,
    API_EDH_ADMIN_CAPACITY_RESERVATION,
    API_EDH_ADMIN_AMI_RECIPES,
    API_EDH_ADMIN_AMI_RECIPE,
    API_EDH_ADMIN_AMI_RECIPE_BUILDS,
    API_EDH_ADMIN_AMI_RECIPE_BUILD,
    API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER,
    API_EDH_CONTAINERS_CATALOG,
    API_EDH_CONTAINERS_JOBS,
    API_EDH_CONTAINERS_JOB,
)

# ---------------------------------------------------------------------------
# Every add-on route
# ---------------------------------------------------------------------------
ADDON_ROUTES: Tuple[ApiRoute, ...] = (API_ADDON_PHYSNA_VIEWER, *EDH_ROUTES)
