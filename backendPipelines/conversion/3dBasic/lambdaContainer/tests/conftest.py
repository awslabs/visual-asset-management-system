#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pytest configuration for the conversion/3dBasic pipeline lambda tests."""


# Several modules in this directory put a MagicMock in sys.modules['trimesh'] at collection when
# trimesh is not imported yet, so their handler tests need no model library. The tests that convert
# real files (test_3dbasic_new_formats.py, test_3dbasic_units_axes.py, test_3dbasic_usd_and_guards.py)
# need the real libraries, so they are imported here, before any module is collected, when they are
# installed. The stubbing modules pass their stub to the code under test explicitly.
for _name in ("trimesh", "pxr"):
    try:
        __import__(_name)
    except ImportError:
        pass


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: standalone unit test (no AWS calls)")
    config.addinivalue_line("markers", "golden: runs against the built image (see tests/golden/run_golden.sh)")
