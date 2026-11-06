# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The VAMS user -> EDH principal mapping.

EDH knows a VAMS user only as an opaque uid derived from the normalized claims id: ``"v"``
followed by the first 15 characters of the lowercased base32 SHA-256 digest (charset ``a-z2-7``,
16 characters in all, inside EDH's ``^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,31}$`` username rule). The input is used exactly
as ``request_to_claims`` returns it -- not lowercased or trimmed here -- so two ids that differ only
in case are two EDH users, as they are two VAMS users. The reserved system identity is never an
EDH principal.
"""

import base64
import hashlib

SYSTEM_USER_ID = "SYSTEM_USER"
EDH_UID_PREFIX = "v"
EDH_UID_DIGEST_CHARS = 15


def _require_principal(user_id, what):
    if not isinstance(user_id, str) or user_id == "":
        raise ValueError(f"An EDH {what} requires a non-empty VAMS user id")
    if user_id == SYSTEM_USER_ID:
        raise ValueError("The system identity is not an EDH principal")


def edh_uid_for(user_id: str) -> str:
    """The EDH uid for a normalized VAMS user id."""
    _require_principal(user_id, "uid")
    digest = hashlib.sha256(user_id.encode("utf-8")).digest()
    encoded = base64.b32encode(digest).decode("ascii")
    return EDH_UID_PREFIX + encoded[:EDH_UID_DIGEST_CHARS].lower()


def display_name_for(user_id: str) -> str:
    """The display name kept for VAMS logs and audit records; EDH's directory API stores no display name."""
    _require_principal(user_id, "display name")
    return user_id
