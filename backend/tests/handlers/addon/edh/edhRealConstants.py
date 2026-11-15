# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pins the real ``common.constants`` module for the EDH tests, whose modules bind constants at import.

The repo-wide tests/conftest.py installs a bare mock ``common.constants`` at collection time and swaps the
real module in only once the per-test fixture runs. ``constants.py`` is pure data with no AWS dependencies,
so the EDH conftest calls ``install_real_constants()`` at load -- directory conftests load after the
parent, so the pin wins for the test modules collected beneath it."""

import importlib.util
import os
import sys
import types

_BACKEND_PKG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "backend"))


def install_real_constants():
    common_pkg = sys.modules.get("common")
    if not isinstance(common_pkg, types.ModuleType) or not hasattr(common_pkg, "__path__"):
        common_pkg = types.ModuleType("common")
        common_pkg.__path__ = [os.path.join(_BACKEND_PKG_DIR, "common")]
        sys.modules["common"] = common_pkg
    spec = importlib.util.spec_from_file_location(
        "common.constants", os.path.join(_BACKEND_PKG_DIR, "common", "constants.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["common.constants"] = module
    spec.loader.exec_module(module)
