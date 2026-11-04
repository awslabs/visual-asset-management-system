"""`CustomAPIClientMixin` (`vamscli/utils/custom_api_client.py`) is a fork extension point.

`APIClient` inherits from the mixin, so the method resolution order is `APIClient` first: a core
method always wins over a mixin method of the same name. That precedence is intended, but it means a
fork method that shares a name with a core method is never called and nothing reports it. A later
upstream release that adds a core method under a name a fork already uses would silently re-route
the fork's commands and MCP tools to the core method.

The rule for a fork: name each mixin method so that `APIClient` does not define it (a `custom`
prefix is recommended). This test holds that rule for whatever the mixin contains, and fails on the
upgrade that introduces a clash.
"""

from vamscli.utils.api_client import APIClient
from vamscli.utils.custom_api_client import CustomAPIClientMixin


def _shadowed(mixin: type) -> set:
    """Non-dunder names the mixin defines that `APIClient` defines too."""
    names = {name for name in vars(mixin) if not (name.startswith("__") and name.endswith("__"))}
    return names & set(vars(APIClient))


def test_api_client_inherits_the_custom_mixin():
    assert APIClient.__mro__[:2] == (APIClient, CustomAPIClientMixin)


def test_no_custom_api_client_method_is_shadowed_by_a_core_method():
    shadowed = _shadowed(CustomAPIClientMixin)
    assert not shadowed, (
        f"CustomAPIClientMixin defines {sorted(shadowed)}, which APIClient also defines; the core "
        "method wins and the custom one is never called. Rename the custom method."
    )


def test_the_check_detects_a_shadowed_name():
    # Control: a mixin that reuses a core method name is reported, and one that does not is not.
    core_name = next(
        name
        for name in sorted(vars(APIClient))
        if not name.startswith("_") and callable(getattr(APIClient, name))
    )
    clashing = type("ClashingMixin", (), {core_name: lambda self: None})
    distinct = type("DistinctMixin", (), {"custom_probe_method": lambda self: None})
    assert _shadowed(clashing) == {core_name}
    assert _shadowed(distinct) == set()
