# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The size an FBX scene expands to, read from the file before Assimp reads it.

An FBX scene is a graph of ``Model`` objects joined by connections: a model is placed once under every
parent it is connected to, and a ``Geometry`` connected to several models is placed once per model.
Assimp (through Open3D's ``read_triangle_model``) builds a node for every path from the root and,
pre-transforming the vertices, a copy of the geometry for every placement, all before any Python
runs. A file of a few kilobytes whose models each sit under both models of the level above describes
2^depth placements. ``scene_size`` counts the placed models, vertices and triangles from the file's
``Objects`` and ``Connections`` in one pass (ASCII or binary), so the loader can refuse a scene over
the ``SceneLimit`` bounds before Assimp sees it (ARCC BSC1: size limits on untrusted input).

Vertices are counted as Assimp's FBX converter makes them, one per polygon corner
(``PolygonVertexIndex`` entries); triangles as its triangulation does, ``n - 2`` per polygon of ``n``
corners. A cycle in the model graph counts as over the bound.
"""

import mmap
import os
import re
import struct
import zlib
from typing import NamedTuple

import numpy as np

MODEL = b"Model"
GEOMETRY = b"Geometry"
ROOT_ID = 0

# Counts above this are reported as this, so a deep doubling never builds huge integers.
_CAP = 1 << 62
# Bytes read or decompressed at a time while counting an index array.
_CHUNK = 16 * 1024 ** 2

_BINARY_MAGIC = b"Kaydara FBX Binary  \x00"
_FIXED_SIZES = {b"Y": 2, b"C": 1, b"I": 4, b"F": 4, b"D": 8, b"L": 8}
_ARRAY_STRIDES = {b"f": 4, b"d": 8, b"l": 8, b"i": 4, b"b": 1}

# ASCII 7.x: an object header ``Model: 123, "Model::name", "Mesh" {``, an index array
# ``PolygonVertexIndex: *24 {`` and a connection ``C: "OO",child,parent``. Assimp's tokenizer treats any
# whitespace alike and needs no line breaks, so neither does the scan; a match inside a ';' comment
# only adds to the counts.
_ASCII_TOKEN = re.compile(
    rb'(?<![A-Za-z0-9_])(?:(Model|Geometry):\s*(\d+)\s*,'
    rb'|(PolygonVertexIndex):\s*\*\s*\d+\s*\{'
    rb'|C:\s*"O[OP]"\s*,\s*(\d+)\s*,\s*(\d+))')
# Where Assimp's tokenizer ends a ';' comment.
_LINE_END = re.compile(rb"[\r\n\x00\x0c]")


class SceneSize(NamedTuple):
    """What an FBX scene places (``models``, ``vertices``, ``triangles``) and what its geometry
    objects hold counted once each (``stored_vertices``, ``stored_triangles``)."""
    models: int
    vertices: int
    triangles: int
    stored_vertices: int
    stored_triangles: int


def scene_size(file_path: str, max_models: int) -> SceneSize:
    """The models, vertices and triangles the file's scene places. Model counting stops at
    ``max_models + 1``; a cycle counts as that."""
    objects, connections = {}, []
    if os.path.getsize(file_path):
        with open(file_path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data[:len(_BINARY_MAGIC)] == _BINARY_MAGIC:
                try:
                    _read_binary(data, objects, connections)
                except (struct.error, zlib.error) as e:
                    raise ValueError(f"FBX binary file is truncated or corrupt: {e}") from e
            else:
                _read_ascii(data, objects, connections)
    children = {}
    for child, parent in connections:
        kind = objects.get(child, (None,))[0]
        parent_kind = objects.get(parent, (None,))[0]
        if (kind == MODEL and (parent == ROOT_ID or parent_kind == MODEL)) or \
                (kind == GEOMETRY and parent_kind == MODEL):
            children.setdefault(parent, []).append(child)
    models, vertices, triangles = _placed_below(ROOT_ID, children, objects, max_models + 1)
    stored = [value for value in objects.values() if value[0] == GEOMETRY]
    return SceneSize(models, vertices, triangles,
                     min(sum(v for _, v, _ in stored), _CAP), min(sum(t for _, _, t in stored), _CAP))


def _placed_below(start, children, objects, over):
    """(models, vertices, triangles) placed under ``start``, models at most ``over``, memoised.
    Iterative, so a deep chain cannot exhaust the interpreter's recursion limit; a model met again
    inside its own subtree is a cycle and counts as ``over`` models."""
    in_progress = object()
    counted = {start: in_progress}
    stack = [[start, 0, 0, 0, 0]]  # node, next child index, models, vertices, triangles so far
    while stack:
        frame = stack[-1]
        node, index = frame[0], frame[1]
        kids = children.get(node, ())
        if frame[2] >= over or index >= len(kids):
            stack.pop()
            counted[node] = (min(frame[2], over), min(frame[3], _CAP), min(frame[4], _CAP))
            if stack:
                _add(stack[-1], 1, counted[node])
            continue
        frame[1] += 1
        child = kids[index]
        kind, vertices, triangles = objects[child]
        if kind == GEOMETRY:
            _add(frame, 0, (0, vertices, triangles))
            continue
        known = counted.get(child)
        if known is None:
            counted[child] = in_progress
            stack.append([child, 0, 0, 0, 0])
        elif known is in_progress:
            frame[2] = over
        else:
            _add(frame, 1, known)
    return counted[start]


def _add(frame, models, below):
    frame[2] = min(frame[2] + models + below[0], _CAP)
    frame[3] = min(frame[3] + below[1], _CAP)
    frame[4] = min(frame[4] + below[2], _CAP)


def _counts(corners: int, polygons: int):
    """(vertices, triangles) of an index array of ``corners`` entries closing ``polygons`` polygons."""
    return corners, max(corners - 2 * polygons, 0)


# --------------------------------------------------------------------------------------------------
# ASCII
# --------------------------------------------------------------------------------------------------

def _read_ascii(data, objects, connections):
    current = None  # the Geometry whose block the scan is in
    for match in _ASCII_TOKEN.finditer(data):
        kind, object_id, index_array, child, parent = match.groups()
        if kind is not None:
            # A repeated id replaces the earlier object, as Assimp's object table does.
            current = int(object_id) if kind == GEOMETRY else None
            objects[int(object_id)] = (kind, 0, 0)
        elif index_array is not None:
            if current is not None:
                # More than one index array in a block: count the largest, whichever Assimp reads.
                objects[current] = (GEOMETRY,) + _larger(objects[current][1:],
                                                         _ascii_index_counts(data, match.end()))
        else:
            connections.append((int(child), int(parent)))


def _larger(counts, other):
    return max(counts[0], other[0]), max(counts[1], other[1])


def _ascii_index_counts(data, start):
    """(vertices, triangles) of the ``a: i,j,k,-l,...`` array from ``start`` to its closing brace,
    skipping ';' comments as Assimp's tokenizer does. The entries are counted, not taken from the
    ``*N`` header, which Assimp does not check against them."""
    commas = negatives = 0
    entries = False
    position = start
    while position < len(data):
        close = data.find(b"}", position)
        close = len(data) if close < 0 else close
        comment = data.find(b";", position, close)
        stop = close if comment < 0 else comment
        for offset in range(position, stop, _CHUNK):
            chunk = data[offset:min(offset + _CHUNK, stop)]
            commas += chunk.count(b",")
            negatives += chunk.count(b"-")
            entries = entries or any(digit in chunk for digit in b"0123456789")
        if comment < 0:
            break
        line_end = _LINE_END.search(data, comment)
        position = len(data) if line_end is None else line_end.end()
    return _counts(commas + 1 if entries else 0, negatives)


# --------------------------------------------------------------------------------------------------
# Binary
# --------------------------------------------------------------------------------------------------

def _read_binary(data, objects, connections):
    if len(data) < 27:
        raise ValueError("FBX binary header is truncated")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    for name, props, count, body, end in _records(data, 27, len(data), wide):
        if name == b"Objects":
            for kind, kprops, kcount, kbody, kend in _records(data, body, end, wide):
                if kind not in (MODEL, GEOMETRY) or not kcount:
                    continue
                object_id = _properties(data, kprops, kbody, 1)[0]
                if not isinstance(object_id, int):
                    continue
                counts = (0, 0)
                if kind == GEOMETRY:
                    for child, cprops, ccount, cbody, _ in _records(data, kbody, kend, wide):
                        if child == b"PolygonVertexIndex" and ccount:
                            counts = _larger(counts, _binary_index_counts(
                                data, _properties(data, cprops, cbody, 1)[0]))
                objects[object_id] = (kind,) + counts
        elif name == b"Connections":
            for kind, cprops, ccount, cbody, _ in _records(data, body, end, wide):
                if kind != b"C" or ccount < 3:
                    continue
                mode, child, parent = _properties(data, cprops, cbody, 3)
                if mode in (b"OO", b"OP") and isinstance(child, int) and isinstance(parent, int):
                    connections.append((child, parent))


def _records(data, start, end, wide):
    """(name, properties start, property count, nested records start, end) of each node record in
    ``[start, end)``, up to the null record that closes the list."""
    header = 25 if wide else 13
    layout = "<QQQB" if wide else "<IIIB"
    position = start
    while position + header <= end:
        record_end, count, length, name_length = struct.unpack_from(layout, data, position)
        if record_end == 0:
            return
        props = position + header + name_length
        body = props + length
        if not position < body <= record_end <= end:
            raise ValueError("FBX binary node record is out of range")
        yield data[position + header:props], props, count, body, record_end
        position = record_end


def _properties(data, position, end, count):
    """The first ``count`` properties at ``position``: numbers, bytes for strings and raw data, and
    (type, length, encoding, data start, data length) for arrays."""
    values = []
    for _ in range(count):
        if position >= end:
            raise ValueError("FBX binary property list is truncated")
        code = data[position:position + 1]
        position += 1
        if code in _FIXED_SIZES:
            size = _FIXED_SIZES[code]
            if code == b"L":
                values.append(struct.unpack_from("<q", data, position)[0])
            else:
                values.append(None)
            position += size
        elif code in (b"S", b"R"):
            size = struct.unpack_from("<I", data, position)[0]
            values.append(data[position + 4:position + 4 + size])
            position += 4 + size
        elif code in _ARRAY_STRIDES:
            length, encoding, size = struct.unpack_from("<III", data, position)
            values.append((code, length, encoding, position + 12, size))
            position += 12 + size
        else:
            raise ValueError(f"FBX binary property type {code!r} is not known")
        if position > end:
            raise ValueError("FBX binary property list is truncated")
    return values


def _binary_index_counts(data, array):
    """(vertices, triangles) of a binary ``PolygonVertexIndex`` int32 array, raw or zlib-encoded,
    read in chunks so neither a large nor a highly compressed array is held in memory at once."""
    if not isinstance(array, tuple) or array[0] != b"i":
        return 0, 0
    _, length, encoding, start, size = array
    expected = 4 * length
    negatives = 0
    if encoding == 0:
        if size != expected:
            raise ValueError("FBX binary index array length does not match its data")
        for offset in range(start, start + size, _CHUNK):
            chunk = data[offset:min(offset + _CHUNK, start + size)]
            negatives += int(np.count_nonzero(np.frombuffer(chunk, dtype="<i4") < 0))
    elif encoding == 1:
        inflater = zlib.decompressobj()
        produced, pending = 0, b""
        for offset in range(start, start + size, _CHUNK):
            chunk = data[offset:min(offset + _CHUNK, start + size)]
            while True:
                # At most _CHUNK bytes out per call; the input it could not use stays in the tail.
                out = inflater.decompress(chunk, _CHUNK)
                chunk = inflater.unconsumed_tail
                if not out and not chunk:
                    break
                produced += len(out)
                if produced > expected:
                    raise ValueError("FBX binary index array inflates past its declared length")
                out = pending + out
                whole = len(out) - len(out) % 4
                negatives += int(np.count_nonzero(np.frombuffer(out[:whole], dtype="<i4") < 0))
                pending = out[whole:]
        if produced != expected:
            raise ValueError("FBX binary index array inflates to less than its declared length")
    else:
        raise ValueError(f"FBX binary array encoding {encoding} is not known")
    return _counts(length, negatives)
