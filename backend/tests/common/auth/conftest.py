# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Directory-scoped test setup for the relocated common/auth modules.

The root conftest replaces ``sys.modules['common']`` with a mock package that
has no ``auth`` subpackage, so the real ``common.auth.*`` modules these tests
exercise are not importable by their absolute names. Register them here (real
modules, loaded by path) at collection time, scoped to this directory only so
the broader suite's mock wiring is untouched. clientIp must be registered before
authorizerCore (authorizerCore imports ``from common.auth.clientIp import ...``).
"""
import os
import sys
import importlib.util


def _load(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_auth_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "auth"
)
_auth_dir = os.path.abspath(_auth_dir)

_common_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common"
)
_common_dir = os.path.abspath(_common_dir)

# Package marker first, then dependencies (resourceNames), then pure modules, then authorizerCore (depends on clientIp and resourceNames).
if "common.auth" not in sys.modules:
    _load("common.auth", os.path.join(_auth_dir, "__init__.py"))
_load("common.resourceNames", os.path.join(_common_dir, "resourceNames.py"))
_load("common.auth.clientIp", os.path.join(_auth_dir, "clientIp.py"))
_load("common.auth.apiEvent", os.path.join(_auth_dir, "apiEvent.py"))
_load("common.auth.authorizerCore", os.path.join(_auth_dir, "authorizerCore.py"))
