# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.timestamps.utc_now keeps the stored timestamp strings byte-identical to the deprecated
naive-UTC call it replaces, and that call is gone from the backend."""

import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from common import timestamps
from common.timestamps import utc_now

# Spelled in two parts so this file does not trip its own guard below.
_DEPRECATED_CALL = "utc" + "now("
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_FIXED = datetime(2026, 3, 1, 23, 59, 7, 123456)


class _FixedClock(datetime):
    """Stands in for common.timestamps.datetime so now() returns a known instant."""

    @classmethod
    def now(cls, tz=None):
        assert tz is timezone.utc, "utc_now must read the clock in UTC"
        return _FIXED.replace(tzinfo=timezone.utc)


def _legacy_now():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return getattr(datetime, "utc" + "now")()


@pytest.mark.unit
class TestUtcNow:
    def test_returns_a_naive_datetime(self):
        value = utc_now()
        assert isinstance(value, datetime)
        assert value.tzinfo is None

    def test_isoformat_has_no_offset(self):
        text = utc_now().isoformat()
        assert "+" not in text and not text.endswith("Z")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?", text), text

    def test_reads_the_same_clock_as_the_legacy_call(self):
        before = _legacy_now()
        value = utc_now()
        after = _legacy_now()
        assert type(value) is type(before)
        assert before <= value <= after

    def test_every_stored_format_is_unchanged(self):
        """The formats the handlers write, applied to a fixed instant, match the naive value the
        legacy call produced for that instant."""
        with patch.object(timestamps, "datetime", _FixedClock):
            value = utc_now()
        assert value == _FIXED and value.tzinfo is None
        assert value.isoformat() == "2026-03-01T23:59:07.123456"
        assert value.isoformat() + "Z" == "2026-03-01T23:59:07.123456Z"
        assert value.strftime("%Y-%m-%dT%H:%M:%S.%fZ") == "2026-03-01T23:59:07.123456Z"
        assert value.strftime("%Y-%m-%dT%H:%M:%S") == "2026-03-01T23:59:07"
        assert value.strftime("%B %d %Y - %H:%M:%S") == "March 01 2026 - 23:59:07"
        assert value.strftime("%Y/%m/%d") == "2026/03/01"
        assert value.timestamp() == _FIXED.timestamp()

    def test_comparisons_with_stored_naive_values_still_work(self):
        stored = datetime.strptime("2026-03-01T00:00:00.000000Z", "%Y-%m-%dT%H:%M:%S.%fZ")
        assert (utc_now() - stored).total_seconds() > 0

    def test_raises_no_deprecation_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            utc_now()


@pytest.mark.unit
@pytest.mark.parametrize("subdir", ["backend", "tests"])
def test_no_deprecated_utc_call_remains(subdir):
    offenders = []
    for path in sorted((_BACKEND_DIR / subdir).rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _DEPRECATED_CALL in line:
                offenders.append(f"{path.relative_to(_BACKEND_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, "use common.timestamps.utc_now instead:\n" + "\n".join(offenders)
