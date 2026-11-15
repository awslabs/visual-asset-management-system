# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Signature parity between the scripted EdhClient fakes and the client they stand in for.

Every handler calls a client wrapper positionally through ``getattr(client, wrapper)(*args)``, so a
wrapper renamed, reordered or made keyword-only in ``edhClient.py`` would leave every handler suite
green against its fake while the deployed handler fails. Three guards hold that closed. Every public
method a fake defines, its recording helper aside, must exist on the client, so a wrapper the client
no longer defines is reported rather than silently dropped from the comparison. Every wrapper name a
handler resolves by string must be a function on the client. And for each method a fake and the
client share, the fake's parameters must be a compatible prefix of the client's: the same names in
the same order with the same kinds (defaults may differ), and the client may carry extra trailing
defaulted parameters. The fakes are imported as their suites import them.
"""

import inspect

import pytest

import backend.backend.handlers.addon.edh.edhAdminService as admin
import backend.backend.handlers.addon.edh.edhContainerService as containers
import backend.backend.handlers.addon.edh.edhLaunchWorker as worker
import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common.edhClient import EdhClient
from backend.tests.handlers.addon.edh import edhAdminTestSupport, edhContainerTestSupport, edhServiceTestSupport

FAKES = (
    edhServiceTestSupport.FakeEdhClient,
    edhAdminTestSupport.FakeEdhClient,
    edhContainerTestSupport.FakeEdhClient,
)
FAKE_IDS = [fake.__module__.rsplit(".", 1)[-1] for fake in FAKES]

# The one public helper each fake carries for its suite; every other public method must be a client wrapper.
FAKE_ONLY = {"calls_to"}

# Every wrapper name a handler resolves by string through getattr(client, wrapper).
DISPATCHED_WRAPPERS = {
    "edhWorkstationService": set(service.EDH_AUDIT_ROUTES),
    "edhLaunchWorker": set(worker.WORKER_AUDIT_ROUTES),
    "edhAdminService": set(admin.EDH_CALLS) | set(admin.EDH_KIND_CALLS),
    "edhContainerService": set(containers.EDH_CALLS) | set(containers.EDH_BACKEND_CALLS),
}
DISPATCH_CASES = sorted((handler, name) for handler, names in DISPATCHED_WRAPPERS.items() for name in names)


def _public_methods(cls):
    return {name for name, member in inspect.getmembers(cls, inspect.isfunction) if not name.startswith("_")}


def _shared_methods(fake):
    """The public methods ``fake`` defines that EdhClient defines too."""
    return sorted(_public_methods(fake) & _public_methods(EdhClient))


CASES = [(fake, name) for fake in FAKES for name in _shared_methods(fake)]
IDS = [f"{fake.__module__.rsplit('.', 1)[-1]}.{name}" for fake, name in CASES]


def _parameters(function):
    return list(inspect.signature(function).parameters.values())


@pytest.mark.unit
class TestFakeClientParity:
    def test_every_fake_shares_methods_with_the_client(self):
        """Control: an empty case list would let the parametrised guard pass by asserting nothing."""
        for fake in FAKES:
            assert _shared_methods(fake), fake.__module__

    def test_every_handler_dispatches_at_least_one_wrapper_by_string(self):
        """Control for the dispatch guard: an empty map would make it pass by asserting nothing."""
        for handler, names in DISPATCHED_WRAPPERS.items():
            assert names, handler

    @pytest.mark.parametrize("fake", FAKES, ids=FAKE_IDS)
    def test_every_fake_method_the_handlers_can_reach_exists_on_the_client(self, fake):
        extra = _public_methods(fake) - FAKE_ONLY - _public_methods(EdhClient)
        assert not extra, f"{fake.__module__}: the fake answers wrappers the client no longer defines: {sorted(extra)}"

    @pytest.mark.parametrize("handler, name", DISPATCH_CASES, ids=[f"{h}.{n}" for h, n in DISPATCH_CASES])
    def test_every_wrapper_a_handler_dispatches_by_string_is_a_client_method(self, handler, name):
        assert inspect.isfunction(getattr(EdhClient, name, None)), \
            f"{handler} dispatches {name!r}, which EdhClient does not define"

    @pytest.mark.parametrize("fake, name", CASES, ids=IDS)
    def test_the_fakes_parameters_are_a_compatible_prefix_of_the_clients(self, fake, name):
        ours = _parameters(getattr(fake, name))
        real = _parameters(getattr(EdhClient, name))
        assert len(ours) <= len(real), f"{name}: the fake takes parameters the client does not"
        for fake_param, real_param in zip(ours, real):
            assert fake_param.name == real_param.name, f"{name}: {fake_param.name} is {real_param.name} on the client"
            assert fake_param.kind == real_param.kind, f"{name}: {fake_param.name} is passed differently on the client"
        for extra in real[len(ours):]:
            assert extra.default is not inspect.Parameter.empty or extra.kind in (
                inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD), \
                f"{name}: the client requires {extra.name}, which the fake does not accept"
