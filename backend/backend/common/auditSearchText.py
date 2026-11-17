# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The request parameters an audit record carries for a search or a text-filter read.

A search action records what was asked (the ids, options and paging) and never what was returned.
The text the caller typed is neither an id nor an option: it can hold anything a user thinks of, so it
is recorded under one key, ``searchText``, that the log redaction writes as ``<redacted>``
(``CONTENT_KEYS`` in ``customLogging/logger.py``). Generic names such as ``query`` or ``value`` are
not redacted anywhere, so the records of other actions keep them.

Every read handler that records a typed search term builds its ``request_parameters`` with
:func:`search_text_parameters`; ``tests/common/test_audit_search_text.py`` fails when a registered
search or read action records a text field any other way.
"""

from typing import Any, Dict, Optional

# The one key the typed text sits under. customLogging.logger.CONTENT_KEYS names it, so the value is
# written as <redacted>; a test holds the two together.
SEARCH_TEXT_KEY = "searchText"


def _text_length(value: Any) -> int:
    """The number of characters in the strings a text value holds, however it is nested."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_text_length(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_text_length(item) for item in value)
    return 0


def search_text_parameters(parameters: Optional[Dict[str, Any]],
                           text: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The ``request_parameters`` of a search or text-filter read: ``parameters`` (the ids, options and
    paging, which stay as they are) plus the text its caller typed under the ``searchText`` key.

    The record also names the request fields that carried text (``searchTextFields``) and the
    characters they held in all (``searchTextLength``), so it still shows that a text search ran and
    how large it was. ``text`` maps each request field name to the string, or the list or dict of
    strings, it held. Empty values are left out; a ``text`` with nothing in it adds none of the keys.
    """
    recorded = dict(parameters or {})
    typed = {name: value for name, value in (text or {}).items() if value not in (None, "", [], {})}
    if typed:
        recorded[SEARCH_TEXT_KEY] = typed
        recorded["searchTextFields"] = list(typed)
        recorded["searchTextLength"] = _text_length(typed)
    return recorded
