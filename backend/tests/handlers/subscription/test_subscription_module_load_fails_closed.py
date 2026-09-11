#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""All three subscription handlers fail CLOSED when a required table name cannot be resolved.

The sibling of `tests/handlers/addon/physna/test_physna_module_load_fails_closed.py`, written because the
same fail-OPEN shape S2-BACKEND-067 names lived in three more files that finding does not mention.

What the shape was, and why it was worse than it looked. Each handler resolved its required table names
inside a per-name `try` whose `except` set the name to `None`, then wrote a diagnostic into a module-level
`main_rest_response`:

    if not (subscription_table_name and asset_table_name):
        main_rest_response['body'] = json.dumps({"message": "Failed resolving required table names"})

That object was never returned. Every `lambda_handler` builds its response from a fresh
`copy.deepcopy(STANDARD_JSON_RESPONSE)`, so the diagnostic reached nobody. The module therefore imported
cleanly with a `None` table name and the failure resurfaced per REQUEST as a boto3 error on that name — a
generic 500 naming nothing, for the whole life of the container. A loud cold-start failure had become a
silent per-request one, which is the exact inversion of what Rule 10's module-load contract is for.

None of these tables is optional: `get_subscription_obj`, `get_asset`, and `get_userProfile_Email` each
require theirs, so there is no degraded mode to fall back to.

TWO HARNESS POINTS, both learned the hard way in the Physna sibling:

*   Each module is loaded as a FRESH copy by file path, under a name inside
    `backend.backend.handlers.subscription`, so a relative import inside the package still resolves. A bare
    file-path module name fails on the package-relative import rather than on the resolution under test —
    a failure that looks like the one being asserted.
*   The positive controls are not decoration. With the names resolvable the module must import AND expose
    no `None` table attribute; without them the negative arm could pass because of an unrelated
    constructor error, which would retire the assertion while looking green.
"""

import importlib.util
import os
import sys

import pytest

# Imported at module scope so the root conftest's autouse fixture does not replace
# `backend.backend.handlers` with a non-package stub before the by-path loads below.
from backend.backend.handlers.subscription import subscriptionService  # noqa: F401

PACKAGE = "backend.backend.handlers.subscription"
HANDLER_DIR = os.path.join(
    os.path.dirname(os.path.abspath(subscriptionService.__file__)))

# The module, and every legacy env override its required names accept. Resolution order puts the env var
# first, so clearing these is what forces the SSM path and therefore the failure.
CASES = {
    "unsubscribe": (
        "unsubscribeService.py",
        ["SUBSCRIPTIONS_STORAGE_TABLE_NAME", "ASSET_STORAGE_TABLE_NAME"],
    ),
    "subscription": (
        "subscriptionService.py",
        ["SUBSCRIPTIONS_STORAGE_TABLE_NAME", "ASSET_STORAGE_TABLE_NAME",
         "USER_STORAGE_TABLE_NAME"],
    ),
    "check": (
        "checkSubscriptionService.py",
        ["SUBSCRIPTIONS_STORAGE_TABLE_NAME"],
    ),
}


# The dependency modules the handlers import by their TOP-LEVEL names, snapshotted here at test-module
# import time — which is before the root conftest's autouse fixture runs.
#
# This snapshot is what makes the negative arm honest. The autouse fixture reinstalls mock modules into
# `sys.modules` before EVERY test, and the mock for `handlers.auth` carries no `request_to_claims`. A probe
# run without the snapshot therefore raised `ImportError: cannot import name 'request_to_claims'` — which
# satisfies `pytest.raises(Exception)` just as well as the resolution failure under test. The negative arm
# passed while asserting nothing, and the positive control is what exposed it.
_REAL_DEPENDENCIES = {
    name: module
    for name, module in list(sys.modules.items())
    if module is not None
    and (name == "handlers" or name.startswith(("handlers.", "common.", "models.",
                                                "customLogging.")))
}


def _load_fresh(file_name, suffix):
    """Load a fresh copy of the handler under a package-relative module name.

    The real dependency modules are put back for the duration of the load, so the only thing that can
    fail is the resource-name resolution the test is about.
    """
    module_name = f"{PACKAGE}._probe_{suffix}"
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(HANDLER_DIR, file_name))
    module = importlib.util.module_from_spec(spec)

    replaced = {}
    for name, real in _REAL_DEPENDENCIES.items():
        replaced[name] = sys.modules.get(name)
        sys.modules[name] = real
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)
        for name, previous in replaced.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.mark.unit
class TestSubscriptionModuleLoadFailsClosed:
    @pytest.mark.parametrize("case", sorted(CASES))
    def test_import_raises_when_a_required_name_is_unresolvable(self, case, monkeypatch):
        """The defect, stated directly: an unresolvable REQUIRED name must fail the import.

        Degrading to None let the module import and turned every subsequent request into a generic 500.
        """
        file_name, env_names = CASES[case]
        for name in env_names:
            monkeypatch.delenv(name, raising=False)
        # Force the SSM path to fail rather than reach a real endpoint.
        monkeypatch.delenv("VAMS_RESOURCE_PARAM_PREFIX", raising=False)

        with pytest.raises(Exception) as caught:
            _load_fresh(file_name, f"{case}_closed")

        # It must fail for the RESOLUTION, not for anything else. `pytest.raises(Exception)` alone was
        # satisfied by an ImportError from a mocked dependency, so the arm passed while asserting
        # nothing. The prefix env var is what the resolver reads when no override is present, so a
        # KeyError naming it is the resolution failure itself.
        assert not isinstance(caught.value, ImportError), (
            f"the import failed on a dependency rather than on resource-name resolution: "
            f"{caught.value}")
        chain, error = [], caught.value
        while error is not None and error not in chain:
            chain.append(error)
            error = error.__cause__ or error.__context__
        assert any("VAMS_RESOURCE_PARAM_PREFIX" in str(link) or "resource name" in str(link).lower()
                   for link in chain), (
            f"the raised error does not name a resource-name resolution failure: "
            f"{[type(link).__name__ + ': ' + str(link)[:80] for link in chain]}")

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_import_succeeds_and_no_table_name_is_none(self, case):
        """Positive control, and it carries two jobs.

        It proves the negative arm above fails for the reason claimed rather than because of an unrelated
        constructor error, AND it proves the module no longer holds a `None` table name — the state that
        made the per-request failure possible in the first place.
        """
        file_name, _env_names = CASES[case]

        module = _load_fresh(file_name, f"{case}_open")

        none_names = sorted(
            name for name in dir(module)
            if name.endswith("_table_name") and getattr(module, name) is None)
        assert none_names == [], (
            f"{file_name} imported with an unresolved table name: {none_names}")

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_the_dead_diagnostic_response_is_gone(self, case):
        """`main_rest_response` was written to and never returned.

        Its presence is what made the fail-open look handled, so its absence is asserted rather than left
        implied — a future edit that reintroduces it would reintroduce the same false reassurance.
        """
        file_name, _env_names = CASES[case]
        with open(os.path.join(HANDLER_DIR, file_name), encoding="utf-8") as handle:
            source = handle.read()

        assert "main_rest_response" not in source, (
            f"{file_name} carries a module-level response object that no handler returns; every "
            f"lambda_handler builds its own from copy.deepcopy(STANDARD_JSON_RESPONSE)")

    @pytest.mark.parametrize("case", sorted(CASES))
    def test_no_required_name_degrades_to_none(self, case):
        """The mechanism, not just the outcome.

        An import that happens to raise for some other reason would satisfy the first test; this asserts
        the SHAPE — no `<name>_table_name = None` fallback survives in the source.
        """
        file_name, _env_names = CASES[case]
        with open(os.path.join(HANDLER_DIR, file_name), encoding="utf-8") as handle:
            source = handle.read()

        offenders = [
            line.strip() for line in source.splitlines()
            if line.strip().endswith("_table_name = None")
        ]
        assert offenders == [], (
            f"{file_name} still degrades a table name to None on a resolution failure: {offenders}")

    def test_the_probe_actually_loads_a_module(self):
        """Corpus control. Every assertion above passes if `_load_fresh` silently returned nothing."""
        module = _load_fresh("checkSubscriptionService.py", "corpus_control")

        assert hasattr(module, "lambda_handler"), "the probe did not load a real handler module"
        assert module.subscription_table_name, "the loaded module resolved no table name"
