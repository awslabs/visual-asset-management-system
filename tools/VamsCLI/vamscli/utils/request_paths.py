"""Request path building for VamsCLI.

This module imports nothing from the package, so the add-on and fork client mixins, which
``api_client`` imports at its top, can import ``route_path`` from it at module level.
"""

from typing import Any

# The characters that end a path segment or the path, and '%', which the API decodes once. A value
# carrying one is sent percent-encoded, so it stays in its own segment and the API reads the value as
# it was given; every other character keeps the encoding requests gives it.
_PATH_SEGMENT_ESCAPES = str.maketrans({"/": "%2F", "?": "%3F", "#": "%23", "%": "%25"})


def _path_segment(value: Any) -> str:
    """A value as one path segment: a '/', '?', '#' or '%' in it is percent-encoded."""
    return str(value).translate(_PATH_SEGMENT_ESCAPES)


def route_path(template: str, **values: Any) -> str:
    """A route constant with each placeholder filled by one path segment, so the request addresses
    the route the constant names whatever a value contains."""
    return template.format(**{name: _path_segment(value) for name, value in values.items()})
