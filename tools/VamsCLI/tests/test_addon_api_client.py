"""`AddonAPIClientMixin` (`vamscli/utils/addon_api_client.py`) is the add-on lane of `APIClient`.

`APIClient` inherits from it ahead of the fork's `CustomAPIClientMixin`, so a name resolves to the core
method first, then to an add-on's, then to a fork's. That order is intended, but a method a higher lane
also defines is never called and nothing reports it. Each add-on keeps its methods in one mixin under
`vamscli/utils/addon/`, aggregated by `AddonAPIClientMixin`; these checks hold the order and the names
for whatever the lanes contain.
"""

from pathlib import Path
import inspect

from vamscli.utils.addon_api_client import AddonAPIClientMixin
from vamscli.utils.api_client import APIClient
from vamscli.utils.custom_api_client import CustomAPIClientMixin

ADDON_PACKAGE = Path(inspect.getsourcefile(AddonAPIClientMixin)).parent / "addon"


def _names(cls: type) -> set:
    """Non-dunder names a class defines itself."""
    return {name for name in vars(cls) if not (name.startswith("__") and name.endswith("__"))}


def _addon_mixins() -> tuple:
    """The per-add-on mixins the aggregator lists."""
    return AddonAPIClientMixin.__bases__


def _shared(classes) -> dict:
    """Each name two or more of the classes define, with the classes that define it."""
    owners = {}
    for cls in classes:
        for name in _names(cls):
            owners.setdefault(name, []).append(cls.__name__)
    return {name: found for name, found in owners.items() if len(found) > 1}


def test_the_lanes_resolve_core_then_add_on_then_custom():
    mro = APIClient.__mro__
    assert mro[0] is APIClient
    assert mro.index(AddonAPIClientMixin) < mro.index(CustomAPIClientMixin)
    for mixin in _addon_mixins():
        assert mro.index(AddonAPIClientMixin) < mro.index(mixin) < mro.index(CustomAPIClientMixin)


def test_each_add_on_mixin_lives_in_the_add_on_package():
    for mixin in _addon_mixins():
        if mixin is object:
            continue
        assert Path(inspect.getsourcefile(mixin)).parent == ADDON_PACKAGE, mixin.__name__


def test_the_aggregator_defines_no_method_of_its_own():
    # A method on the aggregator would belong to no add-on; it goes in that add-on's mixin.
    assert _names(AddonAPIClientMixin) == set()


def test_no_add_on_method_is_shadowed_by_a_core_method():
    shadowed = {mixin.__name__: _names(mixin) & set(vars(APIClient)) for mixin in _addon_mixins()}
    assert {name: found for name, found in shadowed.items() if found} == {}, (
        "an add-on mixin defines a name APIClient also defines; the core method wins and the add-on one "
        "is never called. Rename the add-on method."
    )


def test_no_two_add_on_mixins_define_the_same_name():
    assert _shared(_addon_mixins()) == {}


def test_no_custom_method_shares_a_name_with_an_add_on_method():
    clashes = _names(CustomAPIClientMixin) & set().union(*(_names(mixin) for mixin in _addon_mixins()))
    assert clashes == set(), (
        f"CustomAPIClientMixin defines {sorted(clashes)}, which an add-on mixin also defines; the add-on "
        "method wins and the custom one is never called. Rename the custom method."
    )


def test_the_checks_detect_a_clash():
    # Control: two mixins that share a name are reported, and two that do not are not.
    first = type("FirstMixin", (), {"probe_method": lambda self: None, "first_only": lambda self: None})
    second = type("SecondMixin", (), {"probe_method": lambda self: None})
    distinct = type("DistinctMixin", (), {"distinct_method": lambda self: None})
    assert _shared((first, second)) == {"probe_method": ["FirstMixin", "SecondMixin"]}
    assert _shared((first, distinct)) == {}
    core_name = next(name for name in sorted(vars(APIClient)) if not name.startswith("_"))
    clashing = type("ClashingMixin", (), {core_name: lambda self: None})
    assert _names(clashing) & set(vars(APIClient)) == {core_name}
