#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Overlapping content chunks of a document's full text for per-chunk embedding: the window and overlap
sizes, the chunk cap, the segment keys and the labels.

The key builder restates ``backend/backend/common/indexing/documentIds.build_text_chunk_key`` (a pipeline
code asset cannot import the backend package); the test suite pins the two equal. The media branch image
carries a byte-identical copy of this module and applies CONTENT_TEXT_MAX_CHARS when it captures the text,
so the module imports only the standard library.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

# Largest file whose full text is captured for content embedding, in bytes; a larger file is represented by its
# whole-file vector alone. Bounds what the media branch extracts, which the character and chunk caps do not.
CONTENT_EMBED_MAX_FILE_BYTES = 50 * 1024 * 1024
# Longest full text captured for chunking, in characters.
CONTENT_TEXT_MAX_CHARS = 2_000_000
CONTENT_CHUNK_CHARS = 1_600
CONTENT_CHUNK_OVERLAP_CHARS = 200
# Most chunks embedded per file version; text beyond the cap is not chunked and the shortfall is counted.
CONTENT_CHUNK_MAX = 1_000
# The segment kind of a chunk document (documentIds.SEGMENT_KINDS).
SEGMENT_KIND = "textChunk"

_KEY_PREFIX = "c"
_KEY_DIGITS = 6
# A window's end snaps to the last paragraph break, else the last sentence break, inside its final fifth.
_SNAP_FRACTION = 0.2
_PARAGRAPH_BREAK = "\n\n"
_SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]*\s")
_LABEL_SEPARATOR = " \u00b7 "


@dataclass
class Chunk:
    """One chunk: its 1-based ordinal (the key's number), its window in the full text, the chunk text and the
    page number or sheet name the window starts on (``None`` when the text has no page boundaries)."""

    index: int
    start: int
    end: int
    text: str
    page: Optional[Union[int, str]]


def build_text_chunk_key(index: int) -> str:
    """``c`` and the chunk's ordinal zero-padded to six digits (``c000012``), so the keys of one version sort by
    chunk order."""
    value = int(index)
    if value < 0:
        raise ValueError(f"chunk index must not be negative, got {value}")
    if value >= 10 ** _KEY_DIGITS:
        raise ValueError(f"chunk index {value} does not fit {_KEY_DIGITS} digits")
    return f"{_KEY_PREFIX}{value:0{_KEY_DIGITS}d}"


def chunk_label(index: int, count: int, page=None) -> str:
    """``chunk 12/200``, followed by `` · page 7`` for a page number or `` · sheet Q1`` for a sheet name."""
    label = f"chunk {int(index)}/{int(count)}"
    if page is None or isinstance(page, bool) or page == "":
        return label
    if isinstance(page, int):
        return f"{label}{_LABEL_SEPARATOR}page {page}"
    return f"{label}{_LABEL_SEPARATOR}sheet {page}"


def _page_marks(page_offsets) -> List[Tuple[int, Union[int, str]]]:
    """``(start, page-or-sheet)`` pairs sorted by start from a pages.json array; a malformed entry is skipped."""
    marks: List[Tuple[int, Union[int, str]]] = []
    for entry in page_offsets or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = int(entry.get("start"))
        except (TypeError, ValueError):
            continue
        name = entry.get("name")
        page = entry.get("page")
        if isinstance(name, str) and name.strip():
            marks.append((start, name.strip()))
        elif isinstance(page, int) and not isinstance(page, bool):
            marks.append((start, page))
    return sorted(marks, key=lambda mark: mark[0])


def _page_for(marks, position: int):
    page = None
    for start, label in marks:
        if start <= position:
            page = label
        else:
            break
    return page


def _snap(text: str, start: int, end: int) -> int:
    """The window end cut back to the last paragraph break, else the last sentence break, inside the final fifth
    of the window; ``end`` itself when the window carries none."""
    window_start = start + int((end - start) * (1 - _SNAP_FRACTION))
    paragraph = text.rfind(_PARAGRAPH_BREAK, window_start, end)
    if paragraph >= 0:
        return paragraph + len(_PARAGRAPH_BREAK)
    last = None
    for match in _SENTENCE_BREAK.finditer(text, window_start, end):
        last = match
    return last.end() if last else end


def chunk_text(text: str, page_offsets=None) -> Tuple[List[Chunk], int]:
    """``(chunks, dropped)``: windows of CONTENT_CHUNK_CHARS characters advancing by the window minus
    CONTENT_CHUNK_OVERLAP_CHARS, each end snapped to the last paragraph or sentence break inside the final fifth
    of the window, whitespace-only windows skipped, at most CONTENT_CHUNK_MAX chunks; ``dropped`` counts the
    chunks beyond the cap that were not produced."""
    text = text or ""
    marks = _page_marks(page_offsets)
    chunks: List[Chunk] = []
    dropped = 0
    ordinal = 0
    position = 0
    length = len(text)
    while position < length:
        end = min(position + CONTENT_CHUNK_CHARS, length)
        if end < length:
            end = _snap(text, position, end)
        body = text[position:end].strip()
        if body:
            ordinal += 1
            if len(chunks) < CONTENT_CHUNK_MAX:
                chunks.append(Chunk(index=ordinal, start=position, end=end, text=body,
                                    page=_page_for(marks, position)))
            else:
                dropped += 1
        if end >= length:
            break
        position = max(end - CONTENT_CHUNK_OVERLAP_CHARS, position + 1)
    return chunks, dropped
