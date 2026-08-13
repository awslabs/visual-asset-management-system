# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Guard against a test module installing a PARTIAL stand-in for a real module into `sys.modules`.

A module placed in `sys.modules` persists for the rest of the pytest session. A stub exposing only
the one or two names its own file needs therefore breaks every LATER module that imports any other
name from it — and because an import error during collection aborts the whole run, one such stub can
take down a directory's entire suite while the offending file passes in isolation.

That is not hypothetical: `test_processOutput_failure_and_paging.py` stubbed `models.assetsV3` with
only `AssetUploadTableModel`, which aborted collection of all 1389 tests under
`tests/handlers/workflows/` whenever it was collected first. Its stated reason — that assetsV3
cannot import under Python 3.13 — was stale; the module imports fine.

`tests/mocks/` is the supported place for a stand-in, because `conftest.py` installs those before
any test module is imported and they mirror the real module's surface.
"""

import importlib
import sys

import pytest

# Real modules that tests import by many different names, so a partial stand-in for any of them is
# almost certainly a session-wide breakage rather than a deliberate local narrowing.
SHARED_REAL_MODULES = [
    "models.assetsV3",
    "models.pipelines",
    "models.workflows",
    "models.executions",
    "common.validators",
    "common.workflows.executionRecords",
    "common.workflows.stepfunctions_builder",
]


@pytest.mark.unit
@pytest.mark.parametrize("module_name", SHARED_REAL_MODULES)
def test_a_shared_module_is_the_real_one_not_a_partial_stub(module_name):
    """Each shared module resolves to a file on disk, not a bare `ModuleType` placeholder.

    `common.validators` legitimately resolves to `tests/mocks/common/validators.py`. That is fine —
    the mock mirrors the real surface and is installed by conftest before any test module loads. What
    this rejects is an ad-hoc `types.ModuleType(...)` built inside a test file, which has no
    `__file__` at all.
    """
    module = sys.modules.get(module_name)
    if module is None:
        pytest.skip(f"{module_name} not loaded in this session")
    assert getattr(module, "__file__", None) is not None, (
        f"{module_name} has been replaced by an ad-hoc stub with no backing file. A partial stub "
        f"persists for the whole session and will break any later test importing a name it omits. "
        f"Put the stand-in in tests/mocks/ instead.")


@pytest.mark.unit
def test_assetsv3_imports_for_real():
    """Pins the specific stale premise that motivated the stub, so it cannot be re-added silently.

    Asserts on names beyond the one the old stub provided: a partial stub would satisfy
    `AssetUploadTableModel` alone and still fail here.
    """
    assetsv3 = importlib.import_module("models.assetsV3")
    for name in ("AssetUploadTableModel", "InitializeUploadRequestModel",
                 "CreateAssetRequestModel"):
        assert hasattr(assetsv3, name), (
            f"models.assetsV3 is missing {name} — it is likely a partial stub installed by another "
            f"test module rather than the real module.")
