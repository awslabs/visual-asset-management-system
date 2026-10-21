"""Constants for the optional add-ons, kept beside ``constants.py`` the same way the core ones are.

Endpoint paths (``API_*``), feature-switch names (``FEATURE_*``) and the messages an add-on's
commands and MCP tools share. The feature switches mirror those ``infra/common/addonAppFeatures.ts``
(``ADDON_APP_FEATURES``) declares, which a deployment publishes through ``/secure-config`` when the
add-on is deployed. ``constants.py`` re-exports every name here, so ``from vamscli.constants import
FEATURE_PHYSNA_ADDON`` keeps working; ``tests/test_constants_contract.py`` checks the switches
against ``addonAppFeatures.ts`` in both directions and every endpoint path against the backend route
registry. Upstream-owned.

This module imports nothing from ``vamscli``, so ``constants.py`` can import it without an import
cycle.
"""

# Physna
FEATURE_PHYSNA_ADDON = "PHYSNA_ADDON"

# EDH Workstations
# Endpoint paths, one constant per ApiRoute in the backend's EDH_ROUTES group; tests/test_constants_contract.py
# compares each string, path-parameter names included, against that registry.
API_EDH_WORKSTATIONS = "/addon/edh/workstations"
API_EDH_WORKSTATION = "/addon/edh/workstations/{sessionId}"
API_EDH_WORKSTATION_STOP = "/addon/edh/workstations/{sessionId}/stop"
API_EDH_WORKSTATION_START = "/addon/edh/workstations/{sessionId}/start"
API_EDH_WORKSTATION_CONNECT = "/addon/edh/workstations/{sessionId}/connect"
API_EDH_WORKSTATION_WINDOWS_SIGN_IN = "/addon/edh/workstations/{sessionId}/windows-sign-in"
API_EDH_WORKSTATION_RENEW = "/addon/edh/workstations/{sessionId}/renew"
API_EDH_CATALOG_IMAGES = "/addon/edh/catalog/images"
API_EDH_CATALOG_INSTANCE_TYPES = "/addon/edh/catalog/instance-types"
API_EDH_CATALOG_LEASE = "/addon/edh/catalog/lease"
API_EDH_ADMIN_IMAGES = "/addon/edh/admin/images"
API_EDH_ADMIN_IMAGES_VDI = "/addon/edh/admin/images/vdi"
API_EDH_ADMIN_IMAGES_SIMULATION = "/addon/edh/admin/images/simulation"
API_EDH_ADMIN_IMAGE_VDI = "/addon/edh/admin/images/vdi/{imageId}"
API_EDH_ADMIN_IMAGE_SIMULATION = "/addon/edh/admin/images/simulation/{imageId}"
API_EDH_ADMIN_INSTANCES = "/addon/edh/admin/instances"
API_EDH_ADMIN_INSTANCES_STALE = "/addon/edh/admin/instances/stale"
API_EDH_ADMIN_INSTANCE_STOP = "/addon/edh/admin/instances/{sessionId}/stop"
API_EDH_ADMIN_INSTANCE_DEACTIVATE = "/addon/edh/admin/instances/{sessionId}/deactivate"
API_EDH_ADMIN_CAPACITY = "/addon/edh/admin/capacity"
API_EDH_ADMIN_HEALTH = "/addon/edh/admin/health"
API_EDH_ADMIN_SETTINGS = "/addon/edh/admin/settings"
API_EDH_ADMIN_AMI_RECIPES = "/addon/edh/admin/ami-recipes"
API_EDH_ADMIN_AMI_RECIPE = "/addon/edh/admin/ami-recipes/{recipeId}"
API_EDH_ADMIN_AMI_RECIPE_BUILDS = "/addon/edh/admin/ami-recipes/{recipeId}/builds"
API_EDH_ADMIN_AMI_RECIPE_BUILD = "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}"
API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER = "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register"
API_EDH_CONTAINERS_CATALOG = "/addon/edh/containers/catalog"
API_EDH_CONTAINERS_JOBS = "/addon/edh/containers/jobs"
API_EDH_CONTAINERS_JOB = "/addon/edh/containers/jobs/{jobId}"

FEATURE_EDH_WORKSTATIONS = "EDH_WORKSTATIONS"
FEATURE_EDH_CONTAINERS = "EDH_CONTAINERS"
FEATURE_EDH_AMI_RECIPES = "EDH_AMI_RECIPES"

# The refusal a feature-gated EDH command or MCP tool answers when its switch is absent from the
# profile's login-time snapshot. Shared so both surfaces say the same thing.
EDH_WORKSTATIONS_DISABLED_MESSAGE = (
    "The EDH Workstations add-on is not enabled for this deployment. If it was enabled after you "
    "logged in, run 'vamscli auth login' again to refresh the feature switches."
)
EDH_CONTAINERS_DISABLED_MESSAGE = (
    "EDH container jobs are not enabled for this deployment. If they were enabled after you logged "
    "in, run 'vamscli auth login' again to refresh the feature switches."
)
EDH_AMI_RECIPES_DISABLED_MESSAGE = (
    "EDH AMI recipes are not enabled for this deployment. If they were enabled after you logged "
    "in, run 'vamscli auth login' again to refresh the feature switches."
)
