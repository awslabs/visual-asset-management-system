# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Read-side redaction and output encoding of an audit record's values.

The writer redacts by key name before a record is stored. The viewer applies a second pass on read,
because records written before a redaction rule existed still hold the value, and because a value can
look like a credential whatever its key. Per value, in order:

1. ``mask_sensitive_data`` (the writer's key-name redaction) on the section.
2. Value-shape redaction to ``<redacted>``: a JWT, ``Bearer <token>``, an AWS access key id, and the
   signature parameters of a presigned URL.
3. The search text of a search record (``redact_search_text``).
4. Bounds: a string is cut at 2,048 characters, a nested value at depth 6 and 2,000 nodes, and the
   sections of a record together at 16 KB serialized.
5. Output encoding: C0 and C1 control characters, bidirectional and zero-width characters, line and
   paragraph separators, lone surrogates and tag characters are replaced by a visible ``\\uXXXX``.
   JSON decoding turns ``\\u202e`` back into the character, so this step is required on every string
   the viewer returns, keys included.

Nothing here raises on hostile input: a value that cannot be processed is replaced by ``<redacted>``.
"""

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from customLogging.logger import mask_sensitive_data

# The value a redacted field is written as; customLogging/logger.py writes the same text (a test holds
# the two together).
REDACTED = "<redacted>"

# Bounds. A string longer than the cap is cut and the section is flagged truncated.
MAX_STRING_CHARS = 2048
MAX_DEPTH = 6
MAX_NODES = 2000
MAX_KEY_CHARS = 256
# The sections of one record (changes, previous, requestParameters, details), serialized.
MAX_SECTIONS_BYTES = 16384
# A string is searched for credential shapes only in its first characters: the rest is cut anyway.
_SHAPE_SCAN_CHARS = 16384

# Characters shown as a visible escape. The rule is by Unicode category, not a hand-kept list:
# every control (Cc), format (Cf: bidi marks and isolates, zero-width characters, the byte order mark,
# soft hyphen, interlinear annotation, Arabic and Syriac number signs, tag characters), surrogate (Cs),
# private use (Co), line and paragraph separator (Zl, Zp) and every space other than U+0020 (Zs:
# no-break, ideographic and the typographic spaces), plus the characters that draw nothing but are
# not in those categories: the combining grapheme joiner, Hangul and Braille fillers, the Khmer
# inherent vowels, the Mongolian and general variation selectors, the non-characters, and the
# unassigned default-ignorable code points (U+2065, U+FFF0-FFF8 and the unassigned parts of the tag and
# variation selector supplement block, so the whole U+E0000-E0FFF block is covered).
# ``tests/common/auditSearch/test_audit_search_redaction_export_matcher.py`` checks that the class
# covers every code point of those categories in the running Unicode database, so a character a newer
# Unicode release adds to a category fails the test instead of passing through.
_UNSAFE_CHARS = re.compile(
    r"[\u0000-\u001f\u007f-\u00a0\u00ad\u034f\u0600-\u0605\u061c\u06dd\u070f\u0890-\u0891\u08e2\u115f-\u1160\u1680\u17b4-\u17b5\u180b-\u180f\u2000-\u200f\u2028-\u202f\u205f-\u206f\u2800\u3000\u3164\ud800-\uf8ff\ufdd0-\ufdef\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufffb\ufffe-\uffff\U000110bd\U000110cd\U00013430-\U0001343f\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U0001fffe-\U0001ffff\U0002fffe-\U0002ffff\U0003fffe-\U0003ffff\U0004fffe-\U0004ffff\U0005fffe-\U0005ffff\U0006fffe-\U0006ffff\U0007fffe-\U0007ffff\U0008fffe-\U0008ffff\U0009fffe-\U0009ffff\U000afffe-\U000affff\U000bfffe-\U000bffff\U000cfffe-\U000cffff\U000dfffe-\U000dffff\U000e0000-\U000e0fff\U000efffe-\U0010ffff]"
)
UNSAFE_CHARS = _UNSAFE_CHARS

_SHAPE_PATTERNS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), REDACTED),
    (re.compile(r"(?i)\bBearer[ \t]+[^\s\"']+"), "Bearer " + REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    # GitHub personal, OAuth, user, server and refresh tokens and fine-grained tokens; Slack tokens;
    # Google API keys; Stripe secret keys; npm tokens.
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{20,255})(?![A-Za-z0-9_])"), REDACTED),
    (re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,200}|\bxapp-[A-Za-z0-9-]{10,200}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"), REDACTED),
    (re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,200}"), REDACTED),
    (re.compile(r"\bnpm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"), REDACTED),
    # HTTP Basic credentials: the base64 of ``user:password``. The word "Basic" alone is common, so a
    # value must be 12 or more base64 characters with a digit, ``+``, ``/`` or ``=`` in it.
    (re.compile(r"(?i)\bBasic[ \t]+(?=[A-Za-z0-9+/_-]*[0-9+/=])[A-Za-z0-9+/_-]{12,512}={0,2}"), "Basic " + REDACTED),
    # A password inside a URL: ``scheme://user:password@host``.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://[^\s/:@\"']{1,256}):[^\s/@\"']{1,256}@"), r"\1:" + REDACTED + "@"),
    # A PEM private key block, to its end line or the end of the scanned text.
    (re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----.*?(?:-----END [A-Z ]{0,30}PRIVATE KEY-----|\Z)",
                re.DOTALL), REDACTED),
    (re.compile(r"(?i)\b(X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|AWSAccessKeyId|Signature)=[^&\s\"']+"),
     r"\1=" + REDACTED),
)

# The request fields a search record holds the typed text in. Records written before the text moved
# under ``searchText`` (which the writer's redaction hides) hold it under these names. Matched on the
# lower-cased key, anywhere in the section.
SEARCH_TEXT_KEYS = frozenset({
    "query", "metadataquery", "metadatakey", "metadatavalue", "searchterm", "searchtext",
})
# Further free-text request fields of the search routes and of the text filters of other reads.
READ_TEXT_KEYS = frozenset({
    "text", "filterpattern", "assetname", "filekey", "tags",
})
# A list under one of these keys holds items whose named field is typed text.
_ITEM_TEXT_FIELDS = {"tokens": "value", "filters": "query_string"}

SEARCH_ACTION = "search"


def encode_text(text: str) -> str:
    """Replace every character that changes how text reads without being visible by ``\\uXXXX``."""
    def _escape(match: "re.Match[str]") -> str:
        code = ord(match.group(0))
        return "\\u%04x" % code if code <= 0xFFFF else "\\u{%x}" % code
    return _UNSAFE_CHARS.sub(_escape, text)


def redact_string_shapes(text: str) -> str:
    """Replace credential-shaped substrings of a string by ``<redacted>``."""
    scanned = text[:_SHAPE_SCAN_CHARS]
    for pattern, replacement in _SHAPE_PATTERNS:
        scanned = pattern.sub(replacement, scanned)
    return scanned if len(text) <= _SHAPE_SCAN_CHARS else scanned + text[_SHAPE_SCAN_CHARS:]


def safe_text(value: Any, limit: int = MAX_STRING_CHARS) -> str:
    """A value as display text: shapes redacted, cut at ``limit`` characters, characters encoded."""
    text = value if isinstance(value, str) else str(value)
    text = redact_string_shapes(text)
    if len(text) > limit:
        text = text[:limit]
    return encode_text(text)


def redact_search_text(value: Any, keys: Iterable[str] = SEARCH_TEXT_KEYS | READ_TEXT_KEYS,
                       _depth: int = 0) -> Any:
    """
    The section with every typed-text field written as ``<redacted>``.

    A key in ``keys`` (compared lower-cased) loses its value whatever it holds; ``tokens[].value`` and
    ``filters[].query_string`` lose the named field of each item. Used on the sections of a search
    record, and, with ``READ_TEXT_KEYS``, on the request parameters of a read.
    """
    key_set = frozenset(keys)
    if _depth > 12:
        return REDACTED
    if isinstance(value, dict):
        result: Dict[Any, Any] = {}
        for key, item in value.items():
            lowered = key.lower() if isinstance(key, str) else key
            if lowered in key_set:
                result[key] = REDACTED
            elif lowered in _ITEM_TEXT_FIELDS and isinstance(item, list):
                field = _ITEM_TEXT_FIELDS[lowered]
                result[key] = [_redact_item_field(entry, field, key_set, _depth) for entry in item]
            else:
                result[key] = redact_search_text(item, key_set, _depth + 1)
        return result
    if isinstance(value, list):
        return [redact_search_text(item, key_set, _depth + 1) for item in value]
    return value


def _redact_item_field(entry: Any, field: str, keys: "frozenset[str]", depth: int) -> Any:
    if isinstance(entry, dict):
        redacted = redact_search_text({k: v for k, v in entry.items() if k != field}, keys, depth + 1)
        if field in entry:
            redacted[field] = REDACTED
        return redacted
    return REDACTED if entry not in (None, "") else entry


class _Budget:
    """The node count and truncation state of one bounded walk."""

    def __init__(self) -> None:
        self.nodes = 0
        self.truncated = False


def _bound(value: Any, depth: int, budget: _Budget) -> Any:
    """Apply shape redaction, the string, depth and node bounds and output encoding to a value."""
    budget.nodes += 1
    if budget.nodes > MAX_NODES:
        budget.truncated = True
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, str):
        if value == REDACTED:
            return value
        if len(value) > MAX_STRING_CHARS:
            budget.truncated = True
        return safe_text(value)
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            budget.truncated = True
            return {"truncated": True}
        bounded: Dict[str, Any] = {}
        for key, item in value.items():
            text_key = key if isinstance(key, str) else str(key)
            if len(text_key) > MAX_KEY_CHARS:
                budget.truncated = True
            bounded_key = encode_text(redact_string_shapes(text_key[:MAX_KEY_CHARS]))
            if budget.nodes >= MAX_NODES:
                budget.truncated = True
                break
            bounded[bounded_key] = _bound(item, depth + 1, budget)
        return bounded
    if isinstance(value, (list, tuple)):
        if depth >= MAX_DEPTH:
            budget.truncated = True
            return [{"truncated": True}]
        items: List[Any] = []
        for item in value:
            if budget.nodes >= MAX_NODES:
                budget.truncated = True
                break
            items.append(_bound(item, depth + 1, budget))
        return items
    return safe_text(value)


def redact_value(value: Any, *, search_text: str = "none") -> Tuple[Any, bool]:
    """
    Redact, bound and encode one value (a section of a record).

    ``search_text`` is ``"search"`` for a search record (every typed-text field is hidden), ``"read"``
    for a read (the free-text request fields of the text filters are hidden) or ``"none"``.
    Returns ``(value, truncated)``; a value that cannot be processed becomes ``<redacted>``.
    """
    try:
        masked = mask_sensitive_data(value)
        if search_text == "search":
            masked = redact_search_text(masked)
        elif search_text == "read":
            masked = redact_search_text(masked, READ_TEXT_KEYS | {"searchtext"})
        budget = _Budget()
        bounded = _bound(masked, 0, budget)
        return bounded, budget.truncated
    except Exception:
        return REDACTED, True


def _serialized_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=True, default=str))
    except Exception:
        return MAX_SECTIONS_BYTES + 1


def redact_sections(sections: Dict[str, Any], *, search_text: str = "none",
                    search_text_sections: Iterable[str] = ("requestParameters", "details")
                    ) -> Tuple[Dict[str, Any], bool]:
    """
    Redact the value sections of a record and bound them together at 16 KB serialized.

    ``search_text`` applies to the sections named in ``search_text_sections`` only: a typed-text field
    name is meaningful in the request, not in ``changes``. When the sections together are over the
    bound, the largest is replaced by ``{"truncated": true, "bytes": <size>}`` until they fit.
    Returns ``(sections, truncated)``; a section that is absent is left out.
    """
    redacted: Dict[str, Any] = {}
    truncated = False
    for name, value in sections.items():
        if value is None:
            continue
        mode = search_text if name in search_text_sections else "none"
        redacted[name], section_truncated = redact_value(value, search_text=mode)
        truncated = truncated or section_truncated
    sizes = {name: _serialized_size(value) for name, value in redacted.items()}
    while sum(sizes.values()) > MAX_SECTIONS_BYTES and sizes:
        largest = max(sizes, key=lambda name: sizes[name])
        if redacted[largest] == {"truncated": True, "bytes": sizes[largest]}:
            break
        redacted[largest] = {"truncated": True, "bytes": sizes[largest]}
        sizes[largest] = _serialized_size(redacted[largest])
        truncated = True
    return redacted, truncated


def encode_scalar(value: Optional[Any]) -> Optional[Any]:
    """A scalar of a row: a string is redacted, bounded and encoded; other scalars pass through."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    return safe_text(value, 1024)
