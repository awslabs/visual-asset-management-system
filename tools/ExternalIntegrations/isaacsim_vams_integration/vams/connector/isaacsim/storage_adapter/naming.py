"""Folder names for VAMS assets in the Omniverse Content window.

Pure Python with no Kit or gRPC import, so the storage adapter process and the tests share it.
"""

# The per-name limit of the file systems a folder is copied to (ext4, XFS, NTFS, APFS), in bytes of UTF-8.
MAX_SEGMENT_BYTES = 255
ELLIPSIS = "..."


def asset_folder(name, asset_id, limit=MAX_SEGMENT_BYTES):
    """Folder name for an asset: ``<asset name> (<asset id>)``, at most ``limit`` bytes of UTF-8.

    The full asset id is always kept. A name that does not fit is cut to the room left and ends in ``...``.
    VAMS names allow letters, digits, ``-``, ``.``, ``_`` and whitespace (``object_name_pattern``), so every
    whitespace run becomes one space. An id that leaves no room for any of the name gives ``(<id>)``, and one
    too long even for that gives the bare id.
    """
    collapsed = " ".join((name or "").split())
    suffix = f" ({asset_id})"
    room = limit - len(suffix.encode())
    if collapsed and room > 0:
        if len(collapsed.encode()) > room:
            cut = collapsed.encode()[:max(room - len(ELLIPSIS), 0)].decode("utf-8", "ignore").rstrip(" .")
            collapsed = cut + ELLIPSIS if cut else ""
        if collapsed:
            return collapsed + suffix
    bare = f"({asset_id})"
    return bare if len(bare.encode()) <= limit else asset_id


def asset_id_of(segment):
    """The asset id in a folder name made by :func:`asset_folder`.

    VAMS names and ids contain no ``(`` or ``)``, except that an id's last character may be one, so the first
    ``(`` that starts the name or follows a space opens the id. The name part is ignored: a path recorded before
    an asset was renamed still resolves to the same asset. A segment without that shape is taken as a bare id.
    """
    if segment.endswith(")"):
        for index, char in enumerate(segment):
            if char == "(" and (index == 0 or segment[index - 1] == " "):
                inner = segment[index + 1:-1]
                if inner:
                    return inner
    return segment
