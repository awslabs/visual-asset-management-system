# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Minimal 5-field cron matcher used for env change windows.

A change window is either a cron string (window = the matching minute's hour, 60 min) or
``{cron, durationMinutes, timezone}``. A time is inside the window if some cron match ``m``
satisfies ``m <= t < m + duration``.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
          "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
          "oct": 10, "nov": 11, "dec": 12}


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.lower().split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError("step must be >= 1")
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = int(_NAMES.get(x, x)), int(_NAMES.get(y, y))
        else:
            a = b = int(_NAMES.get(part, part))
        if hi == 6 and b == 7:  # 7 == Sunday
            b = 6 if a != 7 else 0
            if a == 7:
                a = 0
        if a < lo or b > hi or a > b:
            raise ValueError(f"value out of range in {spec!r}")
        out.update(range(a, b + 1, step))
    return out


class _Any(set):
    """A field written as ``*`` (POSIX cron treats unrestricted day fields specially)."""


def parse(expr: str) -> list[set[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron needs 5 fields, got {len(parts)}: {expr!r}")
    out = []
    for p, (lo, hi) in zip(parts, _RANGES):
        f = _field(p, lo, hi)
        out.append(_Any(f) if p.strip() == "*" else f)
    return out


def matches(fields: list[set[int]], t: dt.datetime) -> bool:
    """POSIX semantics: when both day-of-month and day-of-week are restricted, a day matches if EITHER does
    (``0 3 1 * 6`` = the 1st of the month and every Saturday); otherwise both must match."""
    dow = (t.weekday() + 1) % 7
    dom_ok, dow_ok = t.day in fields[2], dow in fields[4]
    if not isinstance(fields[2], _Any) and not isinstance(fields[4], _Any):
        day_ok = dom_ok or dow_ok
    else:
        day_ok = dom_ok and dow_ok
    return t.minute in fields[0] and t.hour in fields[1] and t.month in fields[3] and day_ok


def parse_window(window) -> tuple[list[set[int]], int, str]:
    if isinstance(window, str):
        return parse(window), 60, "UTC"
    if isinstance(window, dict):
        dur = int(window.get("durationMinutes", 60))
        if dur < 1 or dur > 7 * 24 * 60:
            raise ValueError("durationMinutes must be 1..10080")
        tz = window.get("timezone", "UTC")
        ZoneInfo(tz)
        return parse(window["cron"]), dur, tz
    raise ValueError("changeWindow must be a cron string or {cron, durationMinutes, timezone}")


def in_window(window, now: dt.datetime | None = None) -> bool:
    if not window:
        return True
    fields, dur, tz = parse_window(window)
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(tz)).replace(second=0, microsecond=0)
    for back in range(dur):
        if matches(fields, now - dt.timedelta(minutes=back)):
            return True
    return False
