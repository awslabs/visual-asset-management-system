# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The :class:`ApiRoute` type and the HTTP method constants every route list uses.

Route lists import these from here rather than from ``common/apiRoutes.py``, which aggregates the
core, add-on (``common/addonApiRoutes.py``) and fork-owned (``custom/customApiRoutes.py``) lists and
so cannot be imported by them. ``apiRoutes.py`` re-exports every name, so callers keep importing
from there.
"""

import re
from functools import lru_cache
from typing import NamedTuple, Tuple

# HTTP methods VAMS routes use.
GET = "GET"
POST = "POST"
PUT = "PUT"
DELETE = "DELETE"
HEAD = "HEAD"


@lru_cache(maxsize=None)
def _route_regex(path_template: str) -> "re.Pattern":
    """Compile an API Gateway route template into an exact-match regex.

    ``{param}`` matches exactly one path segment; ``{param+}`` (greedy proxy)
    matches the remainder of the path. All other characters are literal.
    """
    parts = []
    for segment in path_template.split("/"):
        if segment.startswith("{") and segment.endswith("+}"):
            parts.append(".+")
        elif segment.startswith("{") and segment.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts) + "$")


class ApiRoute(NamedTuple):
    """One API endpoint: route template, allowed methods, logical category.

    ``internal=True`` marks routes that are not registered with API Gateway
    (cross-Lambda invocation only) -- they participate in handler dispatch but
    are excluded from the public route listing.
    ``unauthenticated=True`` marks routes served without the custom Lambda
    authorizer (no Casbin enforcement applies).
    """

    path: str
    methods: Tuple[str, ...]
    category: str
    internal: bool = False
    unauthenticated: bool = False

    def matches(self, path: str) -> bool:
        """Return True if ``path`` (a concrete request path) matches this route."""
        return _route_regex(self.path).fullmatch(path) is not None
