# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The ``contains`` free-text filter of the audit search.

A caller gives up to ``max_terms`` short terms. Each is validated against a small ASCII charset, used
twice and never as a pattern:

* as a Logs Insights prefilter, ``like /(?i)<escaped term>/``, where every character that is not a
  letter, a digit or ``_`` is written with a backslash, so the term is a literal;
* as a server-side check on the parsed, redacted and output-encoded row: a case-folded ``str.find``.
  No ``re`` object is built from a term.

What is searched is fixed (``searchable_strings``): the action, the legacy type, the users, the entity
and the ID keys, and the string values and nested keys of ``requestParameters`` and ``details``. A value
shown as ``<redacted>`` is never searched, and ``changes``, ``previous`` and the request echo are not
searched at all, so a term cannot be used to probe a value the viewer hides.

The match is bounded: at most ``MAX_NODES`` nodes, depth ``MAX_DEPTH`` and ``MAX_STRING_CHARS``
characters per string for one record, and a deadline for the whole recheck.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from common.auditSearch.redaction import REDACTED
from common.auditSearch.rowModel import AuditRow

# Limits. The defaults are what the search applies unless configured; the ceilings are what a
# configuration may not exceed.
MIN_TERM_LENGTH = 3
DEFAULT_MAX_TERM_LENGTH = 64
CEILING_MAX_TERM_LENGTH = 128
DEFAULT_MAX_TERMS = 3
CEILING_MAX_TERMS = 5
MIN_ALPHANUMERIC = 3

# Per-record bounds of the server-side match. 500 nodes covers a record's value sections (at most
# 32 KB serialized); depth 6 and 2,048 characters match the redaction bounds.
MAX_NODES = 500
MAX_DEPTH = 6
MAX_STRING_CHARS = 2048

# ASCII only: audit JSON is ASCII-escaped (\uXXXX), so a non-ASCII term could not be prefiltered and
# would match the escape text rather than the character.
TERM_CHARSET = re.compile(r"[A-Za-z0-9 ._:@+/-]+\Z")
_ALPHANUMERIC = re.compile(r"[A-Za-z0-9]")
# A character left unescaped in the Insights literal. Every other character is written with a backslash.
_LITERAL_PLAIN = re.compile(r"[A-Za-z0-9_]\Z")


class ContainsValidationError(ValueError):
    """A ``contains`` request that is refused. The message names no value."""

    def __init__(self, code: str):
        super().__init__("Invalid contains filter")
        self.code = code


def validate_terms(raw: Any, max_terms: int = DEFAULT_MAX_TERMS,
                   max_term_length: int = DEFAULT_MAX_TERM_LENGTH) -> Tuple[str, ...]:
    """
    The validated, case-folded terms of a ``contains`` value (a string is one term, a list is several).

    Raises :class:`ContainsValidationError` for a non-text term, a term outside the charset, a term
    shorter than 3 or longer than ``max_term_length`` characters, a term with fewer than 3 letters or
    digits, a leading or trailing space, or more than ``max_terms`` terms. A repeated term counts once.
    """
    max_terms = min(max(1, int(max_terms)), CEILING_MAX_TERMS)
    max_term_length = min(max(MIN_TERM_LENGTH, int(max_term_length)), CEILING_MAX_TERM_LENGTH)
    if raw is None:
        return ()
    items: Sequence[Any] = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, (list, tuple)):
        raise ContainsValidationError("notText")
    terms: List[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ContainsValidationError("notText")
        if not (MIN_TERM_LENGTH <= len(item) <= max_term_length):
            raise ContainsValidationError("length")
        if not item.isascii() or not TERM_CHARSET.match(item):
            raise ContainsValidationError("charset")
        if item != item.strip(" "):
            raise ContainsValidationError("space")
        if len(_ALPHANUMERIC.findall(item)) < MIN_ALPHANUMERIC:
            raise ContainsValidationError("tooFewCharacters")
        folded = item.casefold()
        if folded not in terms:
            terms.append(folded)
    if len(terms) > max_terms:
        raise ContainsValidationError("count")
    return tuple(terms)


def escape_regex_literal(text: str) -> str:
    """
    The text as a regular expression literal: every character that is not a letter, a digit or ``_`` is
    written with a backslash. ``_`` stays plain because RE2 refuses ``\\_``.

    Only ASCII text passes: a character outside ASCII raises ``ValueError``, so no multi-byte or
    control character can reach a query.
    """
    if not text.isascii() or any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise ValueError("literal is not printable ASCII")
    return "".join(char if _LITERAL_PLAIN.match(char) else "\\" + char for char in text)


def prefilter_term(term: str) -> str:
    """The Insights regex literal of a validated term: case-insensitive and fully escaped."""
    return "(?i)" + escape_regex_literal(term)


@dataclass
class MatchBudget:
    """The time left for the server-side match of one search, shared across its rows."""

    deadline: Optional[float] = None   # time.monotonic() value; None is unbounded
    exceeded: bool = False

    def check(self) -> bool:
        """False once the deadline has passed; the first failure is remembered."""
        if self.exceeded:
            return False
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.exceeded = True
            return False
        return True


@dataclass
class _Walk:
    nodes: int = 0
    strings: List[str] = field(default_factory=list)


def _walk_value(value: Any, depth: int, walk: _Walk) -> None:
    """Collect the strings of a value: its string leaves and its keys, within the node and depth bounds."""
    if walk.nodes >= MAX_NODES or depth > MAX_DEPTH:
        return
    walk.nodes += 1
    if isinstance(value, str):
        if value != REDACTED:
            walk.strings.append(value[:MAX_STRING_CHARS])
    elif isinstance(value, dict):
        for key, item in value.items():
            if walk.nodes >= MAX_NODES:
                return
            if isinstance(key, str):
                walk.strings.append(key[:MAX_STRING_CHARS])
            _walk_value(item, depth + 1, walk)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if walk.nodes >= MAX_NODES:
                return
            _walk_value(item, depth + 1, walk)


def searchable_strings(row: AuditRow) -> List[str]:
    """
    The strings of a row a ``contains`` term is matched against, in the bounds above.

    Identity facts first (action, legacy type, users, API key, entity, the ID keys), then the nested
    keys and string values of ``requestParameters`` and ``details``. ``changes``, ``previous`` and the
    request echo are not searched, and a value shown as ``<redacted>`` is skipped.
    """
    strings: List[str] = []
    for value in (row.action, row.legacyType, row.user, row.prefixUser, row.onBehalfOf, row.apiKeyId,
                  row.databaseId, row.assetId, row.filePath):
        if isinstance(value, str) and value != REDACTED:
            strings.append(value[:MAX_STRING_CHARS])
    for value in (row.entity.get("type"), row.entity.get("id")):
        if isinstance(value, str) and value != REDACTED:
            strings.append(value[:MAX_STRING_CHARS])
    for key, value in row.ids.items():
        strings.append(key)
        if isinstance(value, str) and value != REDACTED:
            strings.append(value[:MAX_STRING_CHARS])
    walk = _Walk()
    for section in (row.requestParameters, row.details):
        if section is not None:
            _walk_value(section, 0, walk)
    strings.extend(walk.strings)
    return strings


def row_matches_terms(row: AuditRow, terms: Iterable[str], budget: Optional[MatchBudget] = None) -> bool:
    """
    True when every term is found, case-folded, in at least one searchable string of the row.

    A row with no terms to match always matches. When the budget's deadline has passed the row does
    not match, and ``budget.exceeded`` is set so the caller can report the partial result.
    """
    terms = tuple(terms)
    if not terms:
        return True
    if budget is not None and not budget.check():
        return False
    folded: List[str] = [text.casefold() for text in searchable_strings(row)]
    return all(any(term in text for text in folded) for term in terms)
