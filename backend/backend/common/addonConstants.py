# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Backend constants the optional add-ons (``handlers/addon/``) add to the core lists in
``common/constants.py``. Upstream-owned; every list ships empty today.

``common/constants.py`` merges each one into its core list at import, as core + add-on + custom
(``custom/customConstants.py``), and raises naming this file and the entry when one breaks a rule:

* ``ADDON_PERMISSION_CONSTRAINT_FIELDS`` -- constraint fields an add-on object type is authorized on,
  each mapped to its sample value: ``""`` for a single value, ``[]`` for a list of values (a list
  field accepts only the ``is_one_of`` / ``is_not_one_of`` operators, like ``tags``). A name matches
  ``^[A-Za-z][A-Za-z0-9]{1,63}$``, is not a Python keyword, and is not a core or custom field.
* ``ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS`` -- permission object types, each mapped to
  ``{"label": "...", "fields": [{"label": "...", "value": "<field>"}]}``. A type name follows the same
  pattern and is not a core or custom type; each field it lists is a core field (for example
  ``databaseId``) or one declared above. A new type grants nothing until a constraint allows it, the
  seeded admin role included.
* ``ADDON_UNALLOWED_FILE_EXTENSIONS`` / ``ADDON_UNALLOWED_MIME_TYPES`` -- entries added to the upload
  deny lists, append-only: a lower-case extension with its leading dot (``".xyz"``), a lower-case
  MIME type (``"application/x-example"``). A core entry cannot be removed or repeated.
"""

from typing import Dict, List, Union

ADDON_PERMISSION_CONSTRAINT_FIELDS: Dict[str, Union[str, List[str]]] = {}

ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS: Dict[str, dict] = {}

ADDON_UNALLOWED_FILE_EXTENSIONS: List[str] = []

ADDON_UNALLOWED_MIME_TYPES: List[str] = []
