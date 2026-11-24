# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parameter Store for the EDH tests, faked behind ``common.ssmParameters``, the bounded lookup the add-on reads
EDH's, AWS's and its own parameters through.

The backend conftest registers a fresh copy of ``common.ssmParameters`` (and of ``common.resourceNames`` and
``common.deployTimeValues``) for every test, while an EDH module binds ``read_parameter_value`` and the lookup's
error classes from whichever copy was registered when it was imported, usually at collection. So a fake goes
behind the copy each loaded EDH module actually reads through, not only the registered one: the lookups here
are every such copy (the registered one included), found from the functions the EDH modules bound. A module
that reads a deploy-time value binds ``resolve_deploy_time_value`` (``common.deployTimeValues.resolve``), whose
legacy reads go through the ``common.ssmParameters`` copy that module bound.

The EDH conftest resets the state of every lookup for each test through ``monkeypatch`` (cache, missing-parameter
record, back-off window), gives it a client holding no parameter and drives its clock and sleeps from one
``FakeClock``, so a throttled read runs its whole budget without real time passing.
"""

import sys
import time
import types
from unittest.mock import MagicMock

from backend.tests.ssmParametersStub import FakeClock, ParameterStoreStub, install_clock

EDH_PACKAGE = "backend.backend.handlers.addon.edh"
SHARED_LOOKUP_MODULE = "common.ssmParameters"
SHARED_LOOKUP_SUFFIX = "/backend/common/ssmParameters.py"
SHARED_LOOKUP_STATE = (("_cache", dict), ("_missing", dict), ("_failed_reads", lambda: 0),
                       ("_retry_after", lambda: 0.0), ("_window_logged_for", lambda: None))


class ModuleNamespace:
    """Attribute access over a module's globals, for a module copy no longer in ``sys.modules``, so
    ``monkeypatch.setattr`` and ``install_clock`` can patch the names its functions read (and restore them)."""

    def __init__(self, namespace):
        object.__setattr__(self, "_namespace", namespace)

    def __getattr__(self, name):
        try:
            return self._namespace[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self._namespace[name] = value

    def __delattr__(self, name):
        del self._namespace[name]


def _is_lookup_function(function, name) -> bool:
    file_name = str(getattr(function, "__globals__", {}).get("__file__", "")).replace("\\", "/")
    return (callable(function) and getattr(function, "__module__", None) == SHARED_LOOKUP_MODULE
            and getattr(function, "__name__", None) == name and file_name.endswith(SHARED_LOOKUP_SUFFIX))


def reads_through_the_shared_lookup(module) -> bool:
    """Whether ``module`` binds ``read_parameter_value`` from the shipped ``common.ssmParameters``."""
    return _is_lookup_function(vars(module).get("read_parameter_value"), "read_parameter_value")


def _edh_modules():
    return [module for name, module in list(sys.modules.items())
            if name.startswith(EDH_PACKAGE) and isinstance(module, types.ModuleType)]


def _bound_namespaces():
    namespaces = {}
    registered = sys.modules.get(SHARED_LOOKUP_MODULE)
    if isinstance(registered, types.ModuleType):
        namespaces[id(vars(registered))] = vars(registered)
    for module in _edh_modules():
        names = vars(module)
        reader = names.get("read_parameter_value")
        if _is_lookup_function(reader, "read_parameter_value"):
            namespaces.setdefault(id(reader.__globals__), reader.__globals__)
        resolver = names.get("resolve_deploy_time_value")
        legacy_reader = getattr(resolver, "__globals__", {}).get("get_parameter_value")
        if _is_lookup_function(legacy_reader, "get_parameter_value"):
            namespaces.setdefault(id(legacy_reader.__globals__), legacy_reader.__globals__)
    return list(namespaces.values())


def shared_lookups():
    """Every ``common.ssmParameters`` copy a loaded EDH module reads through, the registered one first."""
    return [ModuleNamespace(namespace) for namespace in _bound_namespaces()]


def shared_lookup():
    """The lookup ``read_parameter_value`` reads through in the module under test, for reading its constants and
    setting its state; set state on every lookup with ``set_lookup_state`` when several modules are involved."""
    return shared_lookups()[0]


def set_lookup_state(monkeypatch, **state):
    """Set module state (``_failed_reads``, ``_retry_after``, ...) on every lookup, restored after the test."""
    for lookup in shared_lookups():
        for name, value in state.items():
            monkeypatch.setattr(lookup, name, value)


def reset_shared_lookup(monkeypatch, clock=None):
    """Empty every lookup's cache, missing-parameter record and back-off window, give it a client holding no
    parameter, and drive its time from ``clock`` (a new FakeClock by default); all restored after the test.
    Returns the clock."""
    clock = clock or FakeClock()
    for lookup in shared_lookups():
        for name, initial in SHARED_LOOKUP_STATE:
            monkeypatch.setattr(lookup, name, initial())
        monkeypatch.setattr(lookup, "_ssm_client", ParameterStoreStub())
        install_clock(lookup, clock, monkeypatch)
    return clock


def serve_parameters(monkeypatch, client=None, **stub):
    """Answer every lookup's reads from ``client``, or from a ParameterStoreStub built with ``stub`` (values,
    errors, fault, clock, call_seconds). Returns the client."""
    client = client if client is not None else ParameterStoreStub(**stub)
    for lookup in shared_lookups():
        monkeypatch.setattr(lookup, "_ssm_client", client)
    return client


def lookup_clock():
    """The FakeClock driving the lookups in this test (the conftest's, unless a test installed another)."""
    return shared_lookup()._monotonic.__self__


def resolver_names(module):
    """The ``common.resourceNames`` copy that ``module``'s ``resolve_deploy_time_value`` looks keys up in."""
    resolver = vars(module)["resolve_deploy_time_value"]
    return ModuleNamespace(resolver.__globals__["get_resource_name"].__globals__)


def publish_resource_names(monkeypatch, module, names, *, missing=(), client=None):
    """Make ``module``'s deploy-time lookups find ``names`` (key -> value) among the resource names the container
    loaded, as a cold start leaves them after the chunk read, with ``missing`` keys recorded as not published; the
    resolver's own reads go to ``client`` (a MagicMock recording every call by default). Returns that client."""
    resolver = resolver_names(module)
    client = client if client is not None else MagicMock()
    now = time.time()
    for name, value in (("_cache", dict(names)), ("_cache_fetched_at", now), ("_cache_complete", False),
                        ("_missing_keys", {key: now for key in missing}), ("_failed_sweeps", 0),
                        ("_sweep_retry_after", 0.0), ("_window_logged_for", None), ("_ssm_client", client)):
        monkeypatch.setattr(resolver, name, value)
    return client
