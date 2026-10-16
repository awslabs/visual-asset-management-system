"""Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

``APIClient`` (``utils/api_client.py``) inherits from ``CustomAPIClientMixin``, so a method defined
here is an ordinary ``APIClient`` method: it calls ``self.get`` / ``self.post`` / ``self.put`` /
``self.delete`` with the same authentication, retry/backoff and typed errors as the core methods,
and the MCP server reaches it as ``CLIENT.api.<method>``. Take endpoint paths from
``vamscli.custom_constants``; do not import from ``vamscli.commands`` here, which imports this
module's subclass. A method whose name ``APIClient`` also defines is never called, because the core
method comes first in the MRO; ``tests/test_custom_api_client.py`` fails on such a name.
"""


class CustomAPIClientMixin:
    """API client methods for the endpoints a fork adds."""
