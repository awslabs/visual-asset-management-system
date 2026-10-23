# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The root harness replaces `common` with a mock package, so the REAL `common.compliance` package
is registered here as a package object whose `__path__` points at the source tree (the same way
`tests/handlers/compliance/conftest.py` does), so `import common.compliance.quarantineGuard`
resolves the one module instance the asset handlers bind."""

import os
import sys
import types

_BACKEND_SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

if "common.compliance" not in sys.modules:
    _compliance_pkg = types.ModuleType("common.compliance")
    _compliance_pkg.__path__ = [os.path.join(_BACKEND_SOURCE, "common", "compliance")]
    _compliance_pkg.__package__ = "common.compliance"
    sys.modules["common.compliance"] = _compliance_pkg
if "common" in sys.modules:
    setattr(sys.modules["common"], "compliance", sys.modules["common.compliance"])
