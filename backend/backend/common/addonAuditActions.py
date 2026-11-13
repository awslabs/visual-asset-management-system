# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Audit actions of the optional add-ons, merged into the registry by ``common/auditActions.py``.

Every route of ``common/addonApiRoutes.py`` has an entry here or in ``ADDON_AUDIT_EXEMPT``; the
route coverage test fails otherwise. An add-on route is registered whether or not its add-on is
deployed, as its route is.
"""

from typing import Dict, Tuple

from common.addonApiRoutes import (
    API_ADDON_PHYSNA_VIEWER,
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
    API_EDH_WORKSTATION_OS_SIGN_IN,
    API_EDH_WORKSTATION_RENEW,
    API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_STOP,
    API_EDH_WORKSTATIONS,
)
from common.apiRouteTypes import DELETE, GET, POST, PUT
from common.auditActionTypes import (
    GROUP_AUTH_CHANGES,
    AuditSpec,
    RouteMethod,
    SystemAction,
    read,
    route_entries,
    spec,
)

_RECIPE = "edhAmiRecipe"
# The record the EDH proxy routes have always written: one per call the add-on makes to the EDH service.
# Inside a request it is named as the route's action, so it is the route's record and no second record
# is written; a route that makes no EDH call records its action itself.
_PROXY = "edhProxy"

ADDON_AUDIT_ROUTE_ACTIONS: Dict[RouteMethod, AuditSpec] = {
    # Physna
    **route_entries(API_ADDON_PHYSNA_VIEWER, {GET: read("physnaViewerGet", "viewer")}),
    # EDH workstations
    **route_entries(API_EDH_WORKSTATIONS, {
        GET: read("edhWorkstationList", "workstation", legacy_type=_PROXY),
        POST: spec("edhWorkstationCreate", "workstation", legacy_type=_PROXY),
    }),
    **route_entries(API_EDH_WORKSTATION, {
        GET: read("edhWorkstationGet", "workstation", legacy_type=_PROXY),
        DELETE: spec("edhWorkstationDelete", "workstation", legacy_type=_PROXY),
    }),
    **route_entries(API_EDH_WORKSTATION_STOP, {POST: spec("edhWorkstationStop", "workstation", legacy_type=_PROXY)}),
    **route_entries(API_EDH_WORKSTATION_START, {POST: spec("edhWorkstationStart", "workstation", legacy_type=_PROXY)}),
    **route_entries(API_EDH_WORKSTATION_RENEW, {
        POST: spec("edhWorkstationRenew", "workstation", legacy_type="edhWorkstationRenew"),
    }),
    # Connect and OS sign-in hand out a connection credential, so they are recorded as writes.
    **route_entries(API_EDH_WORKSTATION_CONNECT, {GET: spec("edhWorkstationConnect", "workstation", legacy_type=_PROXY)}),
    **route_entries(API_EDH_WORKSTATION_OS_SIGN_IN, {GET: spec("edhWorkstationOsSignIn", "workstation", legacy_type=_PROXY)}),
    **route_entries(API_EDH_CATALOG_IMAGES, {GET: read("edhCatalogImageList", "catalog", legacy_type=_PROXY)}),
    **route_entries(API_EDH_CATALOG_INSTANCE_TYPES, {GET: read("edhCatalogInstanceTypeList", "catalog", legacy_type=_PROXY)}),
    **route_entries(API_EDH_CATALOG_LEASE, {GET: read("edhCatalogLeaseGet", "catalog", legacy_type=_PROXY)}),
    **route_entries(API_EDH_CATALOG_CAPACITY_RESERVATIONS, {
        GET: read("edhCatalogCapacityReservationList", "catalog", legacy_type=_PROXY),
    }),
    # EDH administration
    **route_entries(API_EDH_ADMIN_IMAGES, {GET: read("edhImageList", "image", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_IMAGES_VDI, {POST: spec("edhImageCreate", "image", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_IMAGES_SIMULATION, {POST: spec("edhImageCreate", "image", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_IMAGE_VDI, {DELETE: spec("edhImageDelete", "image", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_IMAGE_SIMULATION, {DELETE: spec("edhImageDelete", "image", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_INSTANCES, {GET: read("edhInstanceList", "instance", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_INSTANCES_STALE, {GET: read("edhInstanceList", "instance", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_INSTANCE_STOP, {POST: spec("edhInstanceStop", "instance", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_INSTANCE_DEACTIVATE, {POST: spec("edhInstanceDeactivate", "instance", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_CAPACITY, {GET: read("edhCapacityGet", "capacityReservation", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_HEALTH, {GET: read("edhHealthGet", "system", legacy_type=_PROXY)}),
    **route_entries(API_EDH_ADMIN_SETTINGS, {
        GET: read("edhSettingsGet", "settings", legacy_type=_PROXY),
        PUT: spec("edhSettingsUpdate", "settings", legacy_type="edhLeasePolicyUpdate"),
    }),
    **route_entries(API_EDH_ADMIN_CAPACITY_RESERVATIONS, {
        GET: read("edhCapacityReservationList", "capacityReservation", legacy_type=_PROXY),
        POST: spec("edhCapacityReservationCreate", "capacityReservation",
                   legacy_type="edhCapacityReservationRegister"),
    }),
    **route_entries(API_EDH_ADMIN_CAPACITY_RESERVATION, {
        PUT: spec("edhCapacityReservationUpdate", "capacityReservation",
                  legacy_type="edhCapacityReservationUpdate"),
        DELETE: spec("edhCapacityReservationDelete", "capacityReservation",
                     legacy_type="edhCapacityReservationDelete"),
    }),
    # EDH AMI recipes: the handler records every change under one legacy type with an operation field.
    **route_entries(API_EDH_ADMIN_AMI_RECIPES, {
        GET: read("edhAmiRecipeList", "amiRecipe", legacy_type=_PROXY),
        POST: spec("edhAmiRecipeCreate", "amiRecipe", legacy_type=_RECIPE),
    }),
    **route_entries(API_EDH_ADMIN_AMI_RECIPE, {
        GET: read("edhAmiRecipeGet", "amiRecipe", legacy_type=_PROXY),
        PUT: spec("edhAmiRecipeUpdate", "amiRecipe", legacy_type=_RECIPE),
        DELETE: spec("edhAmiRecipeDelete", "amiRecipe", legacy_type=_RECIPE),
    }),
    **route_entries(API_EDH_ADMIN_AMI_RECIPE_BUILDS, {
        GET: read("edhAmiBuildList", "amiBuild", legacy_type=_PROXY),
        POST: spec("edhAmiBuildCreate", "amiBuild", legacy_type=_RECIPE),
    }),
    **route_entries(API_EDH_ADMIN_AMI_RECIPE_BUILD, {
        DELETE: spec("edhAmiBuildRetire", "amiBuild", legacy_type=_RECIPE),
    }),
    **route_entries(API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER, {
        POST: spec("edhAmiBuildRegister", "amiBuild", legacy_type=_RECIPE),
    }),
    # EDH containers
    **route_entries(API_EDH_CONTAINERS_CATALOG, {GET: read("edhContainerCatalogList", "catalog", legacy_type=_PROXY)}),
    **route_entries(API_EDH_CONTAINERS_JOBS, {
        GET: read("edhContainerJobList", "containerJob", legacy_type=_PROXY),
        POST: spec("edhContainerJobCreate", "containerJob", legacy_type=_PROXY),
    }),
    **route_entries(API_EDH_CONTAINERS_JOB, {DELETE: spec("edhContainerJobCancel", "containerJob", legacy_type=_PROXY)}),
}

ADDON_AUDIT_EXEMPT: Dict[RouteMethod, str] = {}

# Actions an add-on handler records inside a request in addition to the route's own action.
ADDON_AUDIT_SECONDARY_ACTIONS: Tuple[AuditSpec, ...] = (
    # One record per call the add-on makes to the EDH service, with the call's outcome.
    spec("edhProxy", "system", legacy_type="edhProxy"),
    spec("edhSessionApiKeyCreate", "apiKey", group=GROUP_AUTH_CHANGES, legacy_type="edhSessionApiKeyCreate"),
    spec("edhSessionApiKeyRevoke", "apiKey", group=GROUP_AUTH_CHANGES, legacy_type="edhSessionApiKeyRevoke"),
)

ADDON_AUDIT_SYSTEM_ACTIONS: Tuple[SystemAction, ...] = (
    SystemAction("handlers.addon.physna.physnaAssetSync", spec("physnaAssetSync", "asset")),
    SystemAction("handlers.addon.physna.physnaFileSync", spec("physnaFileSync", "file")),
    SystemAction("handlers.addon.garnetFramework.garnetDataIndexAsset", spec("garnetAssetSync", "asset")),
    SystemAction("handlers.addon.garnetFramework.garnetDataIndexDatabase", spec("garnetDatabaseSync", "database")),
    SystemAction("handlers.addon.garnetFramework.garnetDataIndexFile", spec("garnetFileSync", "file")),
    SystemAction("handlers.addon.edh.edhLaunchWorker", spec("edhWorkstationLaunch", "workstation")),
    SystemAction("handlers.addon.edh.edhSessionReconciler", spec("edhSessionReconcile", "workstation")),
)
