# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reader for the Point Cloud Library's PCD format with no native dependency: the header is parsed by
hand, the body with numpy (``ascii`` and ``binary``), and ``binary_compressed`` bodies are decoded from
their LZF stream in Python. The compressed body is field-major (all x, then all y, ...), the others are
point-major."""

import io
import struct

import numpy as np

# (TYPE letter, SIZE bytes) -> little-endian numpy dtype string.
_DTYPES = {
    ("F", 4): "<f4", ("F", 8): "<f8",
    ("U", 1): "<u1", ("U", 2): "<u2", ("U", 4): "<u4", ("U", 8): "<u8",
    ("I", 1): "<i1", ("I", 2): "<i2", ("I", 4): "<i4", ("I", 8): "<i8",
}


class PcdError(ValueError):
    """The file is not a PCD the reader can decode."""


def lzf_decompress(data: bytes, expected_length: int) -> bytes:
    """Decode an LZF stream: a control byte below 32 introduces a literal run of ``ctrl + 1`` bytes;
    otherwise its top three bits carry a copy length (``7`` adds the next byte) and its low five bits
    plus the following byte a back-reference offset."""
    out = bytearray()
    position = 0
    end = len(data)
    while position < end and len(out) < expected_length:
        ctrl = data[position]
        position += 1
        if ctrl < 32:
            out += data[position:position + ctrl + 1]
            position += ctrl + 1
            continue
        length = ctrl >> 5
        if length == 7:
            length += data[position]
            position += 1
        reference = len(out) - ((ctrl & 0x1F) << 8) - data[position] - 1
        position += 1
        if reference < 0:
            raise PcdError("LZF back-reference points before the start of the output")
        count = length + 2
        if reference + count <= len(out):
            # The run lies entirely inside the output already produced: one slice copy.
            out += out[reference:reference + count]
        else:
            # The run overlaps the bytes it produces (a repeat of the last few bytes), so each byte
            # has to read one written earlier in this same run.
            for _ in range(count):
                out.append(out[reference])
                reference += 1
    return bytes(out)


def read_pcd_header(path: str) -> dict:
    fields = {}
    header_bytes = 0
    with open(path, "rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                raise PcdError("PCD header has no DATA line")
            header_bytes += len(line)
            text = line.decode("ascii", "replace").strip()
            if not text or text.startswith("#"):
                continue
            key, _sep, value = text.partition(" ")
            key = key.upper()
            if key in ("FIELDS", "SIZE", "TYPE", "COUNT"):
                fields[key] = value.split()
            elif key in ("WIDTH", "HEIGHT", "POINTS"):
                fields[key] = int(value)
            elif key in ("VERSION", "VIEWPOINT"):
                fields[key] = value.strip()
            elif key == "DATA":
                fields[key] = value.strip().lower()
                break
    for required in ("FIELDS", "SIZE", "TYPE"):
        if required not in fields:
            raise PcdError(f"PCD header lacks {required}")
    names = fields["FIELDS"]
    counts = [int(c) for c in fields.get("COUNT", ["1"] * len(names))]
    width = int(fields.get("WIDTH", 0))
    height = int(fields.get("HEIGHT", 1))
    points = int(fields.get("POINTS", width * height))
    return {
        "version": fields.get("VERSION", ""), "fields": names, "sizes": [int(s) for s in fields["SIZE"]],
        "types": fields["TYPE"], "counts": counts, "width": width, "height": height, "points": points,
        "data": fields["DATA"], "headerBytes": header_bytes,
    }


def _field_dtypes(header):
    dtypes = []
    for name, size, kind, count in zip(header["fields"], header["sizes"], header["types"], header["counts"]):
        code = _DTYPES.get((kind.upper(), size))
        if code is None:
            raise PcdError(f"unsupported PCD field type {kind}{size} for {name}")
        dtypes.append((name, code, count))
    return dtypes


def _columns(header, body: bytes) -> dict:
    """Per-field float64 column arrays (N, count) from the body in the header's encoding."""
    dtypes = _field_dtypes(header)
    points = header["points"]
    encoding = header["data"]
    if encoding == "ascii":
        table = np.loadtxt(io.BytesIO(body), ndmin=2, dtype=np.float64)
        if table.size == 0:
            table = table.reshape(0, sum(d[2] for d in dtypes))
        columns, offset = {}, 0
        for name, _code, count in dtypes:
            columns[name] = table[:, offset:offset + count]
            offset += count
        return columns
    if encoding == "binary":
        structured = np.frombuffer(body, dtype=np.dtype([(n, c, (k,)) for n, c, k in dtypes]), count=points)
        return {name: structured[name].reshape(points, count).astype(np.float64) for name, _code, count in dtypes}
    if encoding == "binary_compressed":
        compressed_size, raw_size = struct.unpack("<II", body[:8])
        raw = lzf_decompress(body[8:8 + compressed_size], raw_size)
        columns, offset = {}, 0
        for name, code, count in dtypes:
            span = points * count * np.dtype(code).itemsize
            columns[name] = np.frombuffer(raw[offset:offset + span], dtype=code).reshape(points, count).astype(np.float64)
            offset += span
        return columns
    raise PcdError(f"unsupported PCD DATA encoding {encoding}")


def _colors(header, columns, body_types) -> "np.ndarray | None":
    if all(n in columns for n in ("r", "g", "b")):
        rgb = np.column_stack([columns["r"][:, 0], columns["g"][:, 0], columns["b"][:, 0]])
        return np.clip(rgb, 0, 255).astype(np.uint8)
    for name in ("rgb", "rgba"):
        if name in columns:
            raw = columns[name][:, 0]
            index = header["fields"].index(name)
            if body_types[index].upper() == "F":
                packed = np.asarray(raw, dtype=np.float32).view(np.uint32)
            else:
                packed = np.asarray(raw, dtype=np.uint32)
            return np.column_stack([(packed >> 16) & 255, (packed >> 8) & 255, packed & 255]).astype(np.uint8)
    return None


def read_pcd(path: str):
    """``(points float64 (N, 3), colors uint8 (N, 3) or None, header)``. Rows with a non-finite
    coordinate are dropped, and the colour rows with them."""
    header = read_pcd_header(path)
    if not all(n in header["fields"] for n in ("x", "y", "z")):
        raise PcdError("PCD cloud has no x, y, z fields")
    with open(path, "rb") as handle:
        handle.seek(header["headerBytes"])
        body = handle.read()
    columns = _columns(header, body)
    points = np.column_stack([columns["x"][:, 0], columns["y"][:, 0], columns["z"][:, 0]]).astype(np.float64)
    colors = _colors(header, columns, header["types"])
    finite = np.all(np.isfinite(points), axis=1)
    if not finite.all():
        points = points[finite]
        if colors is not None:
            colors = colors[finite]
    return points, colors, header
