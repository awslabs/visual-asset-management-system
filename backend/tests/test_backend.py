#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

from backend import __version__


def test_version():
    assert __version__ == '0.1.0'
