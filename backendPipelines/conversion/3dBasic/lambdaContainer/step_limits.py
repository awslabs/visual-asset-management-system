# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Vendored from backendPipelines/preview/3dThumbnail/container/preview_pipeline/format_handlers/cad_handler.py (the
# assembly placement count): a container's build context holds only its own directory, so the copy
# cannot be imported across pipelines. tests/test_3dbasic_step_input.py keeps each definition identical.

"""The component placements a STEP file's assembly expands to, counted from the file's text before
Open CASCADE reads it, so a file whose assembly references one part a great many times is refused
instead of being expanded."""

import mmap
import os
import re

_STRING = rb"'(?:[^']|'')*'"


_NAUO = re.compile(rb"NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*" + _STRING + rb"\s*,\s*" + _STRING
                   + rb"\s*,\s*(?:" + _STRING + rb"|\$)\s*,\s*#(\d+)\s*,\s*#(\d+)", re.IGNORECASE)


def assembly_occurrences(file_path: str, max_occurrences: int) -> int:
    """The component placements a STEP file's assembly expands to: every
    ``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` counted once per placement of its parent, read from the
    file's text in one pass. Counting stops at ``max_occurrences + 1``; a cycle counts as that."""
    children = {}
    related = set()
    if os.path.getsize(file_path):
        with open(file_path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as text:
            for match in _NAUO.finditer(text):
                parent, child = int(match.group(1)), int(match.group(2))
                children.setdefault(parent, []).append(child)
                related.add(child)
    over = max_occurrences + 1
    counted = {}
    total = 0
    for root in children:
        if root not in related:
            total += _occurrences_below(root, children, counted, over)
        if total >= over:
            return over
    if children and not total:
        return over  # every definition is someone's child: the assembly is a cycle
    return total


def _occurrences_below(start, children, counted, over):
    """Placements under the product definition ``start``, at most ``over``, memoised in ``counted``.
    Iterative, so a deep chain cannot exhaust the interpreter's recursion limit; a definition met
    again inside its own subtree is a cycle and counts as ``over``."""
    in_progress = -1
    counted[start] = in_progress
    stack = [[start, 0, 0]]  # definition, next child index, placements so far
    while stack:
        frame = stack[-1]
        definition, index, below = frame
        kids = children.get(definition, ())
        if below >= over or index >= len(kids):
            stack.pop()
            counted[definition] = min(below, over)
            if stack:
                stack[-1][2] += 1 + counted[definition]
            continue
        frame[1] += 1
        child = kids[index]
        known = counted.get(child)
        if known is None:
            counted[child] = in_progress
            stack.append([child, 0, 0])
        elif known == in_progress:
            frame[2] = over
        else:
            frame[2] += 1 + known
    return counted[start]
