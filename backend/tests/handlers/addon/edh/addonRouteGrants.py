# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The methods each shipped permission template allows on each EDH route.

``tests/handlers/auth/test_permission_templates_addon_routes.py`` reads ``ROUTE_GRANTS`` from this file
and checks every entry against the real policy generation and Casbin evaluation of every template in
``documentation/permissionsTemplates``. A template absent from a route's entry allows no method on it.
``edh-user.json`` reaches the workstation and catalog routes and ``edh-admin.json`` adds the admin
routes; no shipped template grants the container routes, so a role reaches them through ``api``
constraints of its own.
"""

from backend.backend.common.addonApiRoutes import (
    API_EDH_ADMIN_AMI_RECIPE,
    API_EDH_ADMIN_AMI_RECIPE_BUILD,
    API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER,
    API_EDH_ADMIN_AMI_RECIPE_BUILDS,
    API_EDH_ADMIN_AMI_RECIPES,
    API_EDH_ADMIN_CAPACITY,
    API_EDH_ADMIN_CAPACITY_RESERVATION,
    API_EDH_ADMIN_CAPACITY_RESERVATIONS,
    API_EDH_ADMIN_HEALTH,
    API_EDH_ADMIN_IMAGE_SIMULATION,
    API_EDH_ADMIN_IMAGE_VDI,
    API_EDH_ADMIN_IMAGES,
    API_EDH_ADMIN_IMAGES_SIMULATION,
    API_EDH_ADMIN_IMAGES_VDI,
    API_EDH_ADMIN_INSTANCE_DEACTIVATE,
    API_EDH_ADMIN_INSTANCE_STOP,
    API_EDH_ADMIN_INSTANCES,
    API_EDH_ADMIN_INSTANCES_STALE,
    API_EDH_ADMIN_SETTINGS,
    API_EDH_CATALOG_CAPACITY_RESERVATIONS,
    API_EDH_CATALOG_IMAGES,
    API_EDH_CATALOG_INSTANCE_TYPES,
    API_EDH_CATALOG_LEASE,
    API_EDH_CONTAINERS_CATALOG,
    API_EDH_CONTAINERS_JOB,
    API_EDH_CONTAINERS_JOBS,
    API_EDH_WORKSTATION,
    API_EDH_WORKSTATION_CONNECT,
    API_EDH_WORKSTATION_RENEW,
    API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_STOP,
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN,
    API_EDH_WORKSTATIONS,
)

ROUTE_GRANTS = {
    # Workstation and catalog routes: the EDH user template and the EDH admin template.
    API_EDH_WORKSTATIONS: {"edh-admin.json": ("GET", "POST"), "edh-user.json": ("GET", "POST")},
    API_EDH_WORKSTATION: {"edh-admin.json": ("GET", "DELETE"), "edh-user.json": ("GET", "DELETE")},
    API_EDH_WORKSTATION_STOP: {"edh-admin.json": ("POST",), "edh-user.json": ("POST",)},
    API_EDH_WORKSTATION_START: {"edh-admin.json": ("POST",), "edh-user.json": ("POST",)},
    API_EDH_WORKSTATION_RENEW: {"edh-admin.json": ("POST",), "edh-user.json": ("POST",)},
    API_EDH_WORKSTATION_CONNECT: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    API_EDH_CATALOG_IMAGES: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    API_EDH_CATALOG_INSTANCE_TYPES: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    API_EDH_CATALOG_LEASE: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    # The route answers only the reservations an edhCapacityReservation constraint grants GET on, so the
    # user template reaches the route and, until an administrator grants a reservation, lists none.
    API_EDH_CATALOG_CAPACITY_RESERVATIONS: {"edh-admin.json": ("GET",), "edh-user.json": ("GET",)},
    # Admin routes: the EDH admin template only.
    API_EDH_ADMIN_IMAGES: {"edh-admin.json": ("GET",)},
    API_EDH_ADMIN_IMAGES_VDI: {"edh-admin.json": ("POST",)},
    API_EDH_ADMIN_IMAGES_SIMULATION: {"edh-admin.json": ("POST",)},
    API_EDH_ADMIN_IMAGE_VDI: {"edh-admin.json": ("DELETE",)},
    API_EDH_ADMIN_IMAGE_SIMULATION: {"edh-admin.json": ("DELETE",)},
    API_EDH_ADMIN_INSTANCES: {"edh-admin.json": ("GET",)},
    API_EDH_ADMIN_INSTANCES_STALE: {"edh-admin.json": ("GET",)},
    API_EDH_ADMIN_INSTANCE_STOP: {"edh-admin.json": ("POST",)},
    API_EDH_ADMIN_INSTANCE_DEACTIVATE: {"edh-admin.json": ("POST",)},
    API_EDH_ADMIN_CAPACITY: {"edh-admin.json": ("GET",)},
    API_EDH_ADMIN_HEALTH: {"edh-admin.json": ("GET",)},
    API_EDH_ADMIN_SETTINGS: {"edh-admin.json": ("GET", "PUT")},
    API_EDH_ADMIN_CAPACITY_RESERVATIONS: {"edh-admin.json": ("GET", "POST")},
    API_EDH_ADMIN_CAPACITY_RESERVATION: {"edh-admin.json": ("PUT", "DELETE")},
    API_EDH_ADMIN_AMI_RECIPES: {"edh-admin.json": ("GET", "POST")},
    API_EDH_ADMIN_AMI_RECIPE: {"edh-admin.json": ("GET", "PUT", "DELETE")},
    API_EDH_ADMIN_AMI_RECIPE_BUILDS: {"edh-admin.json": ("GET", "POST")},
    API_EDH_ADMIN_AMI_RECIPE_BUILD: {"edh-admin.json": ("DELETE",)},
    API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER: {"edh-admin.json": ("POST",)},
    # Container routes: no shipped template.
    API_EDH_CONTAINERS_CATALOG: {},
    API_EDH_CONTAINERS_JOBS: {},
    API_EDH_CONTAINERS_JOB: {},
}
