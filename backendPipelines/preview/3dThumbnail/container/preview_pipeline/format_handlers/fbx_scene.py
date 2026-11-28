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
``Objects`` and ``Connections`` in one pass whose work grows linearly with the file (ASCII or binary),
so the loader can refuse a scene over the ``SceneLimit`` bounds before Assimp sees it (ARCC BSC1: size
limits on untrusted input; BSC12: the upload is parsed by a library).

The count must never be lower than what Assimp builds, so the file is read the way Assimp 5.4.2 (the
version Open3D 0.19 bundles) reads it. Where this reading is looser it finds more, never less:

* Connections (``Document::ReadConnections``): every ``C`` record links its child to its parent,
  whatever its type string, except ``"PP"``. The node and geometry walks (``FBXConverter::ConvertNodes``,
  ``Model::ResolveLinks``) then skip a link that names a property, which only ``"OP"`` can.
* Ids (``ParseTokenAsID``): an ASCII id is the token's leading digits, and 0 (the root) once they
  overflow 64 bits. An id given to more than one object counts as each of them, and every ``Objects``
  and ``Connections`` section is read (Assimp reads the first).
* ASCII (``Tokenize``): the text ends at the first NUL byte; ``;`` comments run to the end of the line
  and quoted strings are single tokens, so neither holds structure; array entries are separated by
  commas or line breaks.
* Binary (``TokenizeBinary``): a file is binary when it starts with ``Kaydara FBX Binary``. A nested
  record list runs to its parent's closing null record, and a zero word inside it is skipped.

Vertices are counted as Assimp's FBX converter makes them, one per polygon corner (``PolygonVertexIndex``
entries). Triangles are counted as its triangulation makes them: ``n - 2`` per polygon of ``n`` corners,
none for a one- or two-corner polygon. A geometry holding more than one index array counts its largest.
A cycle in the model graph counts as over the bound.

The parser reads a scope (ASCII ``{ }`` block) or a node record list (binary) by descending into each
level as it opens, so the same pass measures how deeply the file nests and refuses one that nests past
``MAX_FBX_NESTING_DEPTH`` before the file is read, whatever else it holds (ARCC BSC1: bound untrusted
input before parsing; BSC12: validate structure before a native parser reads it).
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
# Bytes read or decompressed at a time while counting a binary index array.
_CHUNK = 16 * 1024 ** 2
# Bytes of an ASCII index array classified at a time.
_TEXT_CHUNK = 4 * 1024 ** 2

# The deepest a file's scopes (ASCII ``{ }`` blocks) or node records (binary nested record lists) may
# nest. The bundled parser reads a scope and a record list by descending into each level as it opens,
# so a file that nests far past any real model is refused by ``scene_size`` before the parser reads it.
# Real FBX exports nest a few tens of levels; this bound sits well above them and far below any depth a
# produced file reaches, leaving a wide margin either way.
MAX_FBX_NESTING_DEPTH = 256

_BINARY_MAGIC = b"Kaydara FBX Binary"
_FIXED_SIZES = {b"Y": 2, b"C": 1, b"I": 4, b"F": 4, b"D": 8, b"L": 8}
_ARRAY_STRIDES = {b"f": 4, b"d": 8, b"l": 8, b"i": 4, b"c": 1}
_ZEROS = re.compile(rb"\x00*")
_U64 = (1 << 64) - 1


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
    ``max_models + 1``; a cycle counts as that. A file whose scopes (ASCII) or node records (binary)
    nest deeper than ``MAX_FBX_NESTING_DEPTH`` is refused with a ``ValueError`` before it is counted
    further, since the parser reads them by descending into each level (ARCC BSC1: bound untrusted
    input before parsing; BSC12: validate structure before a native parser reads it)."""
    models, geometries, connections = set(), {}, []
    if os.path.getsize(file_path):
        with open(file_path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data[:len(_BINARY_MAGIC)] == _BINARY_MAGIC:
                try:
                    _read_binary(data, models, geometries, connections)
                except (struct.error, zlib.error) as e:
                    raise ValueError(f"FBX binary file is truncated or corrupt: {e}") from e
            else:
                _AsciiScan(data, models, geometries, connections, MAX_FBX_NESTING_DEPTH).run()
    children = {}
    for child, parent in connections:
        if parent != ROOT_ID and parent not in models:
            continue
        if child in models:
            children.setdefault(parent, []).append((MODEL, child))
        if child in geometries and parent in models:
            children.setdefault(parent, []).append((GEOMETRY, child))
    models, vertices, triangles = _placed_below(ROOT_ID, children, geometries, max_models + 1)
    stored = geometries.values()
    return SceneSize(models, vertices, triangles,
                     min(sum(v for v, _ in stored), _CAP), min(sum(t for _, t in stored), _CAP))


def _is_link(kind, prop):
    """Whether Assimp's node and geometry walks follow a connection of type ``kind`` that names the
    property ``prop`` (empty or None when it names none)."""
    return kind != b"PP" and not (kind == b"OP" and prop)


def _placed_below(start, children, geometries, over):
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
        kind, child = kids[index]
        if kind == GEOMETRY:
            _add(frame, 0, (0,) + geometries[child])
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


def _larger(counts, other):
    return max(counts[0], other[0]), max(counts[1], other[1])


class _Polygons:
    """Corners and triangles of a ``PolygonVertexIndex`` array read in pieces. A polygon ends at a
    negative index and triangulates into ``n - 2`` triangles; corners after the last end are
    vertices in no polygon (Assimp's ``MeshGeometry`` keeps them as vertices and makes no face)."""
    __slots__ = ("corners", "triangles", "open")

    def __init__(self):
        self.corners = self.triangles = self.open = 0

    def add(self, ends):
        """``ends``: one flag per index, in order, True where the index ends a polygon."""
        count = len(ends)
        if not count:
            return
        self.corners += count
        closes = np.flatnonzero(ends)
        if not len(closes):
            self.open += count
            return
        sizes = np.diff(closes, prepend=-1 - self.open)
        self.triangles += int(np.maximum(sizes - 2, 0).sum())
        self.open = count - 1 - int(closes[-1])

    def add_open(self, count):
        """``count`` indices that end no polygon."""
        self.corners += count
        self.open += count

    def counts(self):
        """(vertices, triangles)."""
        return self.corners, self.triangles


# --------------------------------------------------------------------------------------------------
# ASCII
# --------------------------------------------------------------------------------------------------

_QUOTE, _SEMICOLON, _OPEN, _CLOSE, _COLON, _MINUS = b'";{}:-'
_SEPARATORS = b" \t\r\n\x0c,"
_SEPARATOR_BYTES = [bytes([c]) for c in _SEPARATORS]
# One token of Assimp's ASCII tokenizer, or a run of data tokens ending at a separator. A bare token
# followed by ':' is a key; whitespace is space, tab, CR, LF and form feed (not vertical tab).
_TOKEN = re.compile(
    rb'"[^"]*+"?'
    rb'|;[^\r\n\x0c]*+'
    rb'|[{}:]'
    rb'|[^"{};:]*[ \t\r\n\x0c,]'
    rb'|[^"{};: \t\r\n\x0c,]++')
# The contents of a scope the count does not read, up to its next bracket.
_SKIP = re.compile(rb'(?:[^"{};]++|"[^"]*+"?|;[^\r\n\x0c]*+)*+')
# Brackets in a row.
_BRACKETS = re.compile(rb'[{}]++')
# The rest of an element the count does not read: whole data tokens, strings and comments, stopping
# before a bracket or a token that may be the next key.
_REST = rb'(?:[^"{};:]*[ \t\r\n\x0c,]|"[^"]*+"?|;[^\r\n\x0c]*+)*+'
_ELEMENT_REST = re.compile(_REST)
# A connection element written plainly, ``C: "type",child,parent`` and an optional property string,
# that ends at the next key or the closing bracket.
_PLAIN_CONNECTION = re.compile(
    rb'[ \t\r\n\x0c,]*+C:[ \t\r\n\x0c,]*+("[^"]*+")'
    rb'[ \t\r\n\x0c,]++([^"{};: \t\r\n\x0c,]++)'
    rb'[ \t\r\n\x0c,]++([^"{};: \t\r\n\x0c,]++)'
    rb'(?:[ \t\r\n\x0c,]++("[^"]*+"))?'
    rb'(?=[ \t\r\n\x0c,]*+(?:[^"{};: \t\r\n\x0c,]++:|\}))')
_DATA = re.compile(rb'[^ \t\r\n\x0c,]++')
# Quoted strings in a row, the separators between them included.
_STRINGS = re.compile(rb'(?:"[^"]*+"[ \t\r\n\x0c,]*+)++')
_SEPARATOR = re.compile(rb'[ \t\r\n\x0c,]')
_DIGITS = re.compile(rb'[0-9]++')

_SEPARATOR_BYTE = np.zeros(256, dtype=bool)
_SEPARATOR_BYTE[list(_SEPARATORS)] = True
_DIGIT_BYTE = np.zeros(256, dtype=np.uint8)
_DIGIT_BYTE[list(b"0123456789")] = 1
_NONZERO_BYTE = np.zeros(256, dtype=np.uint8)
_NONZERO_BYTE[list(b"123456789")] = 1

# Roles of the scopes the count reads; every other scope is skipped to its closing bracket.
_ROOT, _OBJECTS, _CONNECTIONS, _GEOMETRY, _INDEX = range(5)
# Keys whose element the count reads, or whose scope it enters, in each role.
_READ_KEYS = {_ROOT: (b"Objects", b"Connections"), _OBJECTS: (MODEL, GEOMETRY), _CONNECTIONS: (b"C",),
              _GEOMETRY: (b"PolygonVertexIndex",), _INDEX: (b"a",)}
# The rest of an unread element and the unread elements after it in the same scope, up to a bracket
# or a read key.
_UNREAD_ELEMENTS = {
    role: re.compile(_REST + rb'(?:[ \t\r\n\x0c,]*+(?!(?:' + b"|".join(map(re.escape, keys))
                     + rb'):)[^"{};: \t\r\n\x0c,]++:' + _REST + rb')*+')
    for role, keys in _READ_KEYS.items()}


class _AsciiScan:
    """One pass over an ASCII FBX that keeps the ``Model``/``Geometry`` ids in ``Objects``, the index
    arrays (``PolygonVertexIndex`` ``a``) of each geometry, and the links in ``Connections``."""

    def __init__(self, data, models, geometries, connections, max_depth):
        self.data = data
        end = data.find(b"\x00")  # Assimp's tokenizer stops at the first NUL byte
        self.end = len(data) if end < 0 else end
        self.models, self.geometries, self.connections = models, geometries, connections
        self.scopes = [(_ROOT, None)]  # (role, geometry id) of each open scope
        self.key = None  # key of the element being read in the innermost scope
        self.tokens = None  # (start, end) of the tokens kept for an object header or a connection
        self.wanted = 0
        self.polygons = None  # set while the element is an index array
        self.max_depth = max_depth  # deepest scope nesting allowed before the file is refused

    def run(self):
        data, end = self.data, self.end
        position = 0
        while position < end:
            start, position = _TOKEN.match(data, position, end).span()
            first = data[start]
            if first == _QUOTE:
                position = self._string(start, position)
            elif first == _OPEN:
                position = self._open(position)
            elif first == _CLOSE:
                self._finish()
                if len(self.scopes) > 1:
                    self.scopes.pop()
            elif first in (_SEMICOLON, _COLON):
                continue
            elif data[position - 1] not in _SEPARATORS and position < end and data[position] == _COLON:
                position = self._key(start, position)
            else:
                self._data(start, position)
        self._finish()

    def _key(self, start, stop):
        """Start the element keyed ``data[start:stop]``; return where to read on (past its data when
        the count does not read it)."""
        self._finish()
        self.key = key = self.data[start:stop] if stop - start <= 32 else b""
        role = self.scopes[-1][0]
        if role == _OBJECTS and key in (MODEL, GEOMETRY):
            self.tokens, self.wanted = [], 1
        elif role == _CONNECTIONS and key == b"C":
            position = self._plain_connections(start)
            if position > start:
                self.key = None
                return position
            self.tokens, self.wanted = [], 4
        elif role == _INDEX and key == b"a":
            self.polygons = _Polygons()
        elif key in _READ_KEYS[role]:
            return _ELEMENT_REST.match(self.data, stop + 1, self.end).end()
        else:
            self.key = b""
            return _UNREAD_ELEMENTS[role].match(self.data, stop + 1, self.end).end()
        return stop + 1

    def _data(self, start, stop):
        if self.polygons is not None:
            _count_ascii_entries(self.data, start, stop, self.polygons)
        elif self.tokens is not None and len(self.tokens) < self.wanted:
            for token in _DATA.finditer(self.data, start, stop):
                self.tokens.append(token.span())
                if len(self.tokens) == self.wanted:
                    break

    def _string(self, start, stop):
        """Take the string token at ``start``; in an index array, every string in a row with it (each
        an entry that ends no polygon). Return where to read on."""
        if self.polygons is not None:
            strings = _STRINGS.match(self.data, start, self.end)
            if strings is None:  # not closed before the end of the text
                self.polygons.add_open(1)
                return stop
            stop = strings.end()
            quotes = sum(self.data[offset:min(offset + _TEXT_CHUNK, stop)].count(b'"')
                         for offset in range(start, stop, _TEXT_CHUNK))
            self.polygons.add_open(quotes // 2)
        elif self.tokens is not None and len(self.tokens) < self.wanted:
            self.tokens.append((start, stop))
        return stop

    def _open(self, position):
        """Enter the scope the current element opens; skip it unless the count reads it."""
        role, owner = self.scopes[-1]
        key, scope = self.key, None
        if role == _ROOT and key == b"Objects":
            scope = (_OBJECTS, None)
        elif role == _ROOT and key == b"Connections":
            scope = (_CONNECTIONS, None)
        elif role == _OBJECTS and key == GEOMETRY and self.tokens:
            geometry = self._id(*self.tokens[0])
            scope = None if geometry is None else (_GEOMETRY, geometry)
        elif role == _GEOMETRY and key == b"PolygonVertexIndex":
            scope = (_INDEX, owner)
        self._finish()
        if scope is not None:
            self.scopes.append(scope)
            self._check_depth(len(self.scopes) - 1)
            return position
        data, end, depth = self.data, self.end, 1
        peak = 1
        while position < end:
            position = _SKIP.match(data, position, end).end()
            if position >= end:
                break
            stop = _BRACKETS.match(data, position, end).end()
            position, depth, run_peak = _close_brackets(data, position, stop, depth)
            if run_peak > peak:
                peak = run_peak
            if not depth:
                break
        # The skipped scope sits one level below the enclosing read scopes (``len(self.scopes) - 1``
        # brackets deep); ``peak`` is the deepest nesting reached inside it, the unread scope itself
        # counting as 1.
        self._check_depth(len(self.scopes) - 1 + peak)
        return position

    def _check_depth(self, depth):
        """Refuse a file that nests past the bound before the recursive parser reads it."""
        if depth > self.max_depth:
            raise ValueError(f"FBX file nests deeper than {self.max_depth} levels")

    def _finish(self):
        """Record the element being read, which ends at a key, a bracket or the end of the text."""
        key, tokens, polygons = self.key, self.tokens, self.polygons
        self.key = self.tokens = self.polygons = None
        if polygons is not None:
            geometry = self.scopes[-1][1]
            self.geometries[geometry] = _larger(self.geometries.get(geometry, (0, 0)), polygons.counts())
        elif tokens and key == b"C":
            self._connect(tokens)
        elif tokens:
            object_id = self._id(*tokens[0])
            if object_id is None:
                return
            if key == MODEL:
                self.models.add(object_id)
            else:
                self.geometries.setdefault(object_id, (0, 0))

    def _connect(self, tokens):
        """Record a ``C`` element of (start, end) tokens: type, child, parent and maybe a property."""
        if len(tokens) < 3:
            return
        child, parent = self._id(*tokens[1]), self._id(*tokens[2])
        prop = self._quoted(*tokens[3]) if len(tokens) > 3 else None
        if child is not None and parent is not None and _is_link(self._quoted(*tokens[0]), prop):
            self.connections.append((child, parent))

    def _plain_connections(self, start):
        """Read the plainly written ``C`` elements in a row from ``start`` with one match each; return
        where the first other element begins (``start`` when the first is not plain)."""
        position = start
        while True:
            plain = _PLAIN_CONNECTION.match(self.data, position, self.end)
            if plain is None:
                return position
            self._connect([plain.span(group) for group in (1, 2, 3, 4) if plain.start(group) >= 0])
            position = plain.end()

    def _id(self, start, stop):
        """The id Assimp's ``strtoul10_64`` reads from a token: its leading digits, 0 once a step
        overflows 64 bits; None when it does not start with a digit (Assimp then refuses the file)."""
        digits = _DIGITS.match(self.data, start, stop)
        if digits is None:
            return None
        digits = digits.group().lstrip(b"0")
        if len(digits) <= 19:  # below 10^19 < 2^64
            return int(digits or b"0")
        value = 0
        for digit in digits:
            stepped = (value * 10 + digit - 48) & _U64
            if stepped < value:
                return 0
            value = stepped
        return value

    def _quoted(self, start, stop):
        """The contents of a quoted-string token (only its first bytes when long), or None."""
        data = self.data
        if stop - start < 2 or data[start] != _QUOTE or data[stop - 1] != _QUOTE:
            return None
        return data[start + 1:min(stop - 1, start + 9)]


def _close_brackets(data, start, stop, depth):
    """Read the brackets in ``[start, stop)`` from nesting ``depth``; return (position, depth, peak),
    with position just past the bracket that brings the depth to 0 if one does, and peak the deepest
    nesting reached up to that point (so a scope closed mid-run does not count its siblings)."""
    position = start
    peak = depth
    while position < stop:
        chunk = data[position:min(position + _TEXT_CHUNK, stop)]
        if len(chunk) < 16:
            for bracket in chunk:
                depth += 1 if bracket == _OPEN else -1
                if depth > peak:
                    peak = depth
                position += 1
                if not depth:
                    return position, 0, peak
            continue
        running = depth + np.cumsum(np.where(np.frombuffer(chunk, dtype=np.uint8) == _OPEN, 1, -1))
        closed = np.flatnonzero(running == 0)
        if len(closed):
            cut = int(closed[0])
            run_peak = int(running[:cut + 1].max())
            return position + cut + 1, 0, run_peak if run_peak > peak else peak
        run_peak = int(running.max())
        if run_peak > peak:
            peak = run_peak
        depth = int(running[-1])
        position += len(chunk)
    return position, depth, peak


def _count_ascii_entries(data, start, stop, polygons):
    """Add the index entries in ``[start, stop)`` (whole tokens) to ``polygons``, a chunk at a time."""
    position = start
    while position < stop:
        chunk = data[position:min(position + _TEXT_CHUNK, stop)]
        if position + len(chunk) < stop:
            cut = max(chunk.rfind(separator) for separator in _SEPARATOR_BYTES)
            if cut < 0:
                # One entry longer than the chunk, which is not a polygon end.
                polygons.add_open(1)
                found = _SEPARATOR.search(data, position, stop)
                position = stop if found is None else found.start()
                continue
            chunk = chunk[:cut + 1]
        polygons.add(_ascii_ends(chunk))
        position += len(chunk)


def _ascii_ends(chunk):
    """One flag per index entry in ``chunk``, True where Assimp's ``strtol10`` reads a negative index.
    An entry is flagged when it is ``-`` and 1 to 9 digits, not all zero. Other spellings Assimp also
    reads as negative (more digits, values wrapping past 2^31) are not flagged; that merges polygons,
    which counts more triangles, never fewer."""
    codes = np.frombuffer(chunk, dtype=np.uint8)
    inside = ~_SEPARATOR_BYTE[codes]
    starts = np.flatnonzero(inside & ~np.concatenate(([False], inside[:-1])))
    if not len(starts):
        return np.zeros(0, dtype=bool)
    length = np.add.reduceat(inside, starts, dtype=np.int64)
    digits = np.add.reduceat(_DIGIT_BYTE[codes], starts, dtype=np.int64)
    nonzero = np.add.reduceat(_NONZERO_BYTE[codes], starts, dtype=np.int64)
    return (codes[starts] == _MINUS) & (digits == length - 1) & (length <= 10) & (nonzero > 0)


# --------------------------------------------------------------------------------------------------
# Binary
# --------------------------------------------------------------------------------------------------

def _read_binary(data, models, geometries, connections):
    if len(data) < 27:
        raise ValueError("FBX binary header is truncated")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    _check_binary_depth(data, wide)
    for name, _, _, body, end in _records(data, 27, len(data), wide, nested=False):
        if name == b"Objects":
            for kind, props, count, kbody, kend in _records(data, body, end, wide, nested=True):
                if kind not in (MODEL, GEOMETRY) or not count:
                    continue
                object_id = _properties(data, props, kbody, 1)[0]
                if not isinstance(object_id, int):
                    continue
                if kind == MODEL:
                    models.add(object_id)
                    continue
                counts = geometries.get(object_id, (0, 0))
                for child, cprops, ccount, cbody, _ in _records(data, kbody, kend, wide, nested=True):
                    if child == b"PolygonVertexIndex" and ccount:
                        counts = _larger(counts, _binary_index_counts(
                            data, _properties(data, cprops, cbody, 1)[0]))
                geometries[object_id] = counts
        elif name == b"Connections":
            for kind, props, count, cbody, _ in _records(data, body, end, wide, nested=True):
                if kind != b"C" or count < 3:
                    continue
                values = _properties(data, props, cbody, min(count, 4))
                child, parent = values[1], values[2]
                prop = values[3] if len(values) > 3 else None
                if isinstance(child, int) and isinstance(parent, int) and _is_link(values[0], prop):
                    connections.append((child, parent))


def _check_binary_depth(data, wide):
    """Refuse a binary FBX whose node records nest past the bound, before the recursive reader reads
    it. Iterative: a stack of record iterators, one per open nesting level, so the walk itself cannot
    exhaust the interpreter's recursion limit. Only record headers are read (each record jumps to the
    next by its stored offset), so the walk is cheap whatever the file holds and stops as soon as the
    bound is passed."""
    stack = [_records(data, 27, len(data), wide, nested=False)]
    while stack:
        if len(stack) > MAX_FBX_NESTING_DEPTH:
            raise ValueError(f"FBX file nests deeper than {MAX_FBX_NESTING_DEPTH} levels")
        try:
            _, _, _, body, nested_end = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if nested_end > body:  # the record holds a nested record list
            stack.append(_records(data, body, nested_end, wide, nested=True))


def _records(data, start, end, wide, nested):
    """(name, properties start, property count, nested records start, nested records end) of each
    node record in ``[start, end)``, as Assimp's ``ReadScope`` reads them. The top-level list ends
    at its first null record; a nested list runs to ``end`` (its parent's closing null record) and
    skips a zero word inside it."""
    word = 8 if wide else 4
    header = 3 * word + 1
    layout = "<QQQB" if wide else "<IIIB"
    position = start
    while position < end:
        if nested:
            zeros = _ZEROS.match(data, position, end).end() - position
            position += zeros - zeros % word
            if position >= end:
                return
        if position + word > end:
            raise ValueError("FBX binary node record is truncated")
        if not any(data[position:position + word]):
            return
        if position + header > end:
            raise ValueError("FBX binary node record is truncated")
        record_end, count, length, name_length = struct.unpack_from(layout, data, position)
        props = position + header + name_length
        body = props + length
        if not position < body <= record_end <= end:
            raise ValueError("FBX binary node record is out of range")
        nested_end = body
        if body < record_end:
            if record_end - body < header:
                raise ValueError("FBX binary node record has no closing null record")
            nested_end = record_end - header
        yield data[position + header:props], props, count, body, nested_end
        position = record_end


def _properties(data, position, end, count):
    """The first ``count`` properties at ``position``: numbers, bytes for strings and raw data, and
    (type, length, encoding, data start, data length) for arrays. A ``b`` property takes the rest of
    the list, as Assimp's ``ReadData`` reads it."""
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
        elif code == b"b":
            values.append(None)
            position = end
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
    polygons = _Polygons()
    if encoding == 0:
        if size != expected:
            raise ValueError("FBX binary index array length does not match its data")
        for offset in range(start, start + size, _CHUNK):
            chunk = data[offset:min(offset + _CHUNK, start + size)]
            polygons.add(np.frombuffer(chunk, dtype="<i4") < 0)
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
                polygons.add(np.frombuffer(out[:whole], dtype="<i4") < 0)
                pending = out[whole:]
        if produced != expected:
            raise ValueError("FBX binary index array inflates to less than its declared length")
    else:
        raise ValueError(f"FBX binary array encoding {encoding} is not known")
    return polygons.counts()
