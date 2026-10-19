"""Constants for the optional add-ons, kept beside ``constants.py`` the same way the core ones are.

Feature-switch names (``FEATURE_*``) for the switches ``infra/common/addonAppFeatures.ts``
(``ADDON_APP_FEATURES``) declares, which a deployment publishes through ``/secure-config`` when the
add-on is deployed. ``constants.py`` re-exports every name here, so ``from vamscli.constants import
FEATURE_PHYSNA_ADDON`` keeps working; ``tests/test_constants_contract.py`` checks this file against
``addonAppFeatures.ts`` in both directions. Upstream-owned.

This module imports nothing from ``vamscli``, so ``constants.py`` can import it without an import
cycle.
"""

# Physna
FEATURE_PHYSNA_ADDON = "PHYSNA_ADDON"
