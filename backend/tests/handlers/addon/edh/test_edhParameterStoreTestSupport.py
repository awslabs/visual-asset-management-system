# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH Parameter Store test support restores every copy of common.ssmParameters it patches.

The conftest resets the state of each copy the loaded EDH modules read through, and a test serves its own fake
behind them. When the test ends, each copy's module state must hold the values it held before, not be deleted:
a copy left without ``_cache`` or ``_ssm_client`` makes every later read in the process fail with a NameError,
and every later EDH test error in the conftest. Each test here runs the helpers under a monkeypatch of its own and
undoes it inside the test.
"""

import pytest

from backend.backend.handlers.addon.edh.common import edhCapacityReservations  # noqa: F401
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.tests.handlers.addon.edh.edhParameterStoreTestSupport import (
    SHARED_LOOKUP_CLOCK_NAMES, SHARED_LOOKUP_STATE, ModuleNamespace, module_globals, patch_globals,
    publish_resource_names, reset_shared_lookup, resolver_names, serve_parameters, set_lookup_state,
    shared_lookups,
)
from backend.tests.ssmParametersStub import FakeClock

LOOKUP_NAMES = tuple(name for name, _ in SHARED_LOOKUP_STATE) + ("_ssm_client",) + SHARED_LOOKUP_CLOCK_NAMES
RESOLVER_NAMES = ("_cache", "_cache_fetched_at", "_cache_complete", "_missing_keys", "_failed_sweeps",
                  "_sweep_retry_after", "_window_logged_for", "_ssm_client")


def _snapshot(views, names):
    return [{name: module_globals(view)[name] for name in names} for view in views]


@pytest.mark.unit
class TestTheLookupsAreRestoredNotDeleted:
    def test_a_reset_and_a_served_fake_leave_every_lookup_as_it_was(self):
        lookups = shared_lookups()
        assert lookups, "control: at least one copy of common.ssmParameters is loaded"
        before = _snapshot(lookups, LOOKUP_NAMES)
        with pytest.MonkeyPatch.context() as inner:
            reset_shared_lookup(inner, FakeClock())
            serve_parameters(inner)
            set_lookup_state(inner, _failed_reads=3, _retry_after=1.0)
            assert all(module_globals(view)["_failed_reads"] == 3 for view in lookups), "control: the state was set"
        assert _snapshot(lookups, LOOKUP_NAMES) == before

    def test_the_resolver_names_are_restored_not_deleted(self):
        resolver = resolver_names(bootstrap)
        before = _snapshot([resolver], RESOLVER_NAMES)
        with pytest.MonkeyPatch.context() as inner:
            publish_resource_names(inner, bootstrap, {"web/deployedUrl": "https://d111.cloudfront.net"},
                                   missing=["location/apiKeyArn"])
            patch_globals(inner, resolver, _failed_sweeps=1)
            assert resolver._cache == {"web/deployedUrl": "https://d111.cloudfront.net"}, "control: names published"
        assert _snapshot([resolver], RESOLVER_NAMES) == before

    def test_a_name_that_did_not_exist_is_removed_again(self):
        lookup = shared_lookups()[0]
        with pytest.MonkeyPatch.context() as inner:
            patch_globals(inner, lookup, _not_a_lookup_name_for_this_test=1)
            assert lookup._not_a_lookup_name_for_this_test == 1
        assert "_not_a_lookup_name_for_this_test" not in module_globals(lookup)

    def test_the_view_refuses_attribute_writes(self):
        view = ModuleNamespace({"_cache": {}})
        with pytest.raises(AttributeError, match="patch_globals"):
            view._cache = {"x": "y"}
        with pytest.raises(AttributeError):
            del view._cache
        assert module_globals(view) == {"_cache": {}}
