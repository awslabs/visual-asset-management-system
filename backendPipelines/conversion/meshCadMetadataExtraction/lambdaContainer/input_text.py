# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reads the text of a mesh input as UTF-8 before trimesh loads it.

trimesh reads the text of an OBJ, ASCII STL, XYZ or glTF file, and a GLB's JSON chunk, as UTF-8. When
that fails it guesses another encoding with charset_normalizer, which this image does not install, so
the extraction would end with a raw "No module named 'charset_normalizer'". That text is therefore read
as UTF-8 here first, and a file whose text is not UTF-8 fails with a message naming the file and saying
it could not be read. A text file saved in another encoding fails the same way rather than being
guessed at: with no detector installed there is no reliable way to tell its encoding, and a wrong guess
would change the names it carries (objects, groups, materials) in the extracted metadata while the run
reported success. ASCII is UTF-8, so a file without accented or other non-ASCII characters is
unaffected.
"""

import codecs
import os
import struct


class InputTextError(ValueError):
    """The text a mesh input is read from is not UTF-8, so the loader cannot read it."""


_NOT_UTF8 = "byte 0x{byte:02X} at offset {offset} is not UTF-8"
INPUT_TEXT_ERRORS = {
    '.obj': ("{name} could not be read as OBJ text: " + _NOT_UTF8 + ". The file is binary data or text "
             "saved in another encoding. Save it as UTF-8 OBJ text and run the extraction again."),
    '.xyz': ("{name} could not be read as XYZ text: " + _NOT_UTF8 + ". The file is binary data or text "
             "saved in another encoding. Save it as UTF-8 text and run the extraction again."),
    '.gltf': ("{name} could not be read as glTF JSON: " + _NOT_UTF8 + ". glTF JSON must be UTF-8 text. "
              "Export the model again and run the extraction again."),
    '.stl': ("{name} could not be read as STL: it is not a binary STL (its size does not match the "
             "triangle count in its header), and as ASCII STL text, " + _NOT_UTF8 + ". Export it as "
             "binary STL or as UTF-8 ASCII STL and run the extraction again."),
    '.glb': ("{name} could not be read as GLB: in its JSON chunk, " + _NOT_UTF8 + ". glTF JSON must be "
             "UTF-8 text. Export the model again and run the extraction again."),
}

# Bytes read per step while checking text, so a large input is checked without a second copy of it
# in memory.
_TEXT_CHECK_CHUNK = 1024 ** 2
# A binary STL: an 80-byte header, a little-endian 32-bit triangle count, then 50 bytes per triangle.
_STL_HEADER_BYTES = 84
_STL_TRIANGLE_BYTES = 50
# A GLB starts with a 12-byte header (magic, version, length) and the 8-byte header of its first chunk.
_GLB_HEAD = struct.Struct("<4sIII4s")


def first_non_utf8_byte(handle, length=None, chunk_size=_TEXT_CHECK_CHUNK):
    """``(offset, byte)`` of the first byte that is not valid UTF-8 in the next ``length`` bytes of
    ``handle`` (to its end when ``length`` is None), the offset counted from where the read starts; or
    None when all of them are UTF-8. A character split across two reads is decoded whole."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    consumed = 0
    while True:
        size = chunk_size if length is None else min(chunk_size, length - consumed)
        chunk = handle.read(size) if size > 0 else b""
        final = not chunk
        # The decoder keeps the start of a character that the previous read cut off, and reports an
        # error's position within those bytes plus this read.
        held = len(decoder.getstate()[0])
        try:
            decoder.decode(chunk, final=final)
        except UnicodeDecodeError as e:
            return consumed - held + e.start, e.object[e.start]
        consumed += len(chunk)
        if final:
            return None


def _is_binary_stl(path):
    """True when the file is laid out as a binary STL, the test trimesh applies: its size is the
    header plus 50 bytes for each triangle the header counts. trimesh reads any other STL as ASCII
    text. A binary STL's header is decoded only as metadata, and one that is not UTF-8 is dropped, so
    the header is not checked."""
    size = os.path.getsize(path)
    if size < _STL_HEADER_BYTES:
        return False
    with open(path, "rb") as handle:
        handle.seek(_STL_HEADER_BYTES - 4)
        (triangles,) = struct.unpack("<I", handle.read(4))
    return size - _STL_HEADER_BYTES == triangles * _STL_TRIANGLE_BYTES


def _glb_json_chunk(path):
    """``(offset, length)`` of a GLB's JSON chunk, or None when the file does not start with a glTF 2
    header and a JSON chunk; the loader rejects such a file with its own message."""
    with open(path, "rb") as handle:
        head = handle.read(_GLB_HEAD.size)
    if len(head) < _GLB_HEAD.size:
        return None
    magic, version, _, chunk_length, chunk_type = _GLB_HEAD.unpack(head)
    if magic != b"glTF" or version != 2 or chunk_type != b"JSON":
        return None
    return _GLB_HEAD.size, chunk_length


def check_input_text(local_path, input_extension, input_name):
    """Raise ``InputTextError`` when the text trimesh would decode from this input is not UTF-8: the
    whole of an OBJ, XYZ or glTF file, an STL that is not laid out as binary, and a GLB's JSON chunk.
    Any other input is left to the loader. ``input_name`` is the file name the message gives."""
    input_extension = input_extension.lower()
    start, length = 0, None
    if input_extension == '.stl':
        if _is_binary_stl(local_path):
            return
    elif input_extension == '.glb':
        chunk = _glb_json_chunk(local_path)
        if chunk is None:
            return
        start, length = chunk
    elif input_extension not in INPUT_TEXT_ERRORS:
        return
    with open(local_path, "rb") as handle:
        handle.seek(start)
        found = first_non_utf8_byte(handle, length)
    if found is None:
        return
    offset, byte = found
    raise InputTextError(INPUT_TEXT_ERRORS[input_extension].format(
        name=input_name, byte=byte, offset=start + offset))
