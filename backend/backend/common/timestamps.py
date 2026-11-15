# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Current-time helper shared by the backend handlers."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    The value carries no tzinfo, so ``isoformat()`` has no ``+00:00`` offset and ``strftime``,
    arithmetic and comparisons behave exactly as they do for the timestamps already stored by
    VAMS (naive UTC strings, some written with a literal ``Z`` suffix by the caller).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
