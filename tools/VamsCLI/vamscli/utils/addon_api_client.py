"""Upstream-owned add-on lane of ``APIClient``.

``APIClient`` (``utils/api_client.py``) inherits from ``AddonAPIClientMixin`` ahead of the fork's
``CustomAPIClientMixin``, so the method resolution order is core, then add-on, then custom. Each
add-on keeps its methods in one mixin module under ``utils/addon/`` and is listed in the bases below.
A method defined there is an ordinary ``APIClient`` method: it calls ``self.get`` / ``self.post`` /
``self.put`` / ``self.delete`` with the same authentication, retry/backoff and typed errors as the core
methods, and the MCP server reaches it as ``CLIENT.api.<method>``. Take endpoint paths from
``vamscli.addon_constants``; do not import from ``vamscli.commands`` here, which imports
``APIClient``. ``tests/test_addon_api_client.py`` fails on an add-on method that a core method shadows,
on two add-on mixins that define the same name, and on a custom method named like an add-on method.
"""

from .addon.edh_api_client import EdhAPIClientMixin


class AddonAPIClientMixin(EdhAPIClientMixin):
    """API client methods for the endpoints the optional add-ons add."""
