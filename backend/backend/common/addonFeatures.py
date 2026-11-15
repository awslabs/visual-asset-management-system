# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The add-on feature switches, and which of them this deployment enabled.

Every add-on API route (``common/addonApiRoutes.py``, ``ApiRoute.addon_feature``) and every add-on
permission object type (``common/addonConstants.py``, ``ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES``)
names the switch its add-on publishes when it is deployed. The values mirror
``infra/common/addonAppFeatures.ts`` (``ADDON_APP_FEATURES``); ``tests/common/test_addon_features.py``
compares the two lists.

``AddonBuilderNestedStack`` collects the switches of the add-ons a deployment builds, and the core
stack passes them, comma-separated, in the ``ADDON_FEATURES_ENABLED`` environment variable to the
Lambdas that list routes and object types for the constraint editor and the allowed-route checks
(``handlers/auth/routes.py``, ``authConstraintsService.py``, ``authConstraintsTemplateService.py``).
Those listings then leave out the entries of an add-on that is not deployed, so a deployment without
the add-on offers none of its object types or routes and probes none of its routes.

The listings only decide what is offered. Authorization does not read the switches: Casbin evaluates
every stored constraint as before, and a request to a route that is not deployed never reaches a
handler.

When the variable is absent (the local development server, a unit test that does not set it) every
entry is listed, as before the variable existed. A deployed Lambda that lists entries always has it,
empty when no add-on is deployed (``infra/test/addon/addonFeatureListings.test.ts``).
"""

import os
from typing import FrozenSet, Optional

# The add-on switches, one per ADDON_APP_FEATURES member (infra/common/addonAppFeatures.ts).
ADDON_FEATURE_PHYSNA = "PHYSNA_ADDON"
ADDON_FEATURE_EDH_WORKSTATIONS = "EDH_WORKSTATIONS"
ADDON_FEATURE_EDH_CONTAINERS = "EDH_CONTAINERS"
ADDON_FEATURE_EDH_AMI_RECIPES = "EDH_AMI_RECIPES"

ADDON_FEATURES = (
    ADDON_FEATURE_PHYSNA,
    ADDON_FEATURE_EDH_WORKSTATIONS,
    ADDON_FEATURE_EDH_CONTAINERS,
    ADDON_FEATURE_EDH_AMI_RECIPES,
)

# The environment variable the core stack sets on the listing Lambdas.
ADDON_FEATURES_ENABLED_ENV = "ADDON_FEATURES_ENABLED"


def deployed_addon_features() -> Optional[FrozenSet[str]]:
    """The add-on switches this deployment enabled, or None when the Lambda was not told.

    Read on every call, so a test can set the variable after import.
    """
    raw = os.environ.get(ADDON_FEATURES_ENABLED_ENV)
    if raw is None:
        return None
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def addon_feature_deployed(feature: Optional[str]) -> bool:
    """Whether an entry tagged with ``feature`` belongs on this deployment's listings.

    An entry with no tag (a core or fork entry) always does, and so does every entry when the
    deployment's switches are unknown.
    """
    if not feature:
        return True
    deployed = deployed_addon_features()
    return deployed is None or feature in deployed


def constraint_object_type_addon_feature(object_type: str) -> Optional[str]:
    """The add-on switch a permission object type is offered under, or None for a core or fork type."""
    from common.addonConstants import ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES

    return ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES.get(object_type)


def constraint_object_type_deployed(object_type: str) -> bool:
    """Whether this deployment offers ``object_type``: a core or fork type, or one of a deployed add-on."""
    return addon_feature_deployed(constraint_object_type_addon_feature(object_type))
