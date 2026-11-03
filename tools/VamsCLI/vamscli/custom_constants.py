"""Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Endpoint paths (``API_*``) and feature-switch names (``FEATURE_CUSTOM_*``) for the routes and
features a fork adds, kept beside ``constants.py`` the same way: no command file or API client
method hardcodes a path (root CLAUDE.md Rule 7). ``tests/test_constants_contract.py`` checks every
path here against the backend route lists (``custom/customApiRoutes.py`` included) and every
``FEATURE_CUSTOM_*`` against ``infra/common/customAppFeatures.ts``.

This module imports nothing from ``vamscli``, so ``utils/custom_api_client.py`` and the custom
commands can import it without an import cycle.
"""
