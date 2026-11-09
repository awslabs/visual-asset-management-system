# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The string length limit accepts exactly N characters, and its message says so: "at most N"."""

import pytest

from backend.backend.common.validators import validate


def _check(validator, value):
    return validate({"field": {"value": value, "validator": validator}})


@pytest.mark.unit
class TestStringMaxLengthMessage:
    def test_the_limit_itself_is_accepted(self):
        assert _check("STRING_256", "a" * 256) == (True, "")

    def test_one_over_the_limit_says_at_most_the_limit(self):
        valid, message = _check("STRING_256", "a" * 257)

        assert valid is False
        assert message == "field must be at most 256 characters"

    def test_the_array_form_uses_the_same_message(self):
        valid, message = _check("STRING_256_ARRAY", ["ok", "a" * 257])

        assert valid is False
        assert message == "field must be at most 256 characters"
