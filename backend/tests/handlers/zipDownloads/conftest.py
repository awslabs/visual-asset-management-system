# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import types

# The root harness replaces `common` with a mock package, so the REAL `common.compliance` package
# is registered here as a package object whose `__path__` points at the source tree, as
# `tests/handlers/assets/conftest.py` does. The zip download service imports
# `common.compliance.quarantineGuard` at module load (#1125 D35), and the quarantine tests patch
# that one module instance, so the service and the tests must resolve the same object.
_BACKEND_SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

if "common.compliance" not in sys.modules:
    _compliance_pkg = types.ModuleType("common.compliance")
    _compliance_pkg.__path__ = [os.path.join(_BACKEND_SOURCE, "common", "compliance")]
    _compliance_pkg.__package__ = "common.compliance"
    sys.modules["common.compliance"] = _compliance_pkg
if "common" in sys.modules:
    setattr(sys.modules["common"], "compliance", sys.modules["common.compliance"])
