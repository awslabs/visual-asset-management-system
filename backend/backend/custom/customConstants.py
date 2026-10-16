# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Backend constants a fork adds to the core lists in ``common/constants.py``. Each list is merged into
its core list at import, as core + add-on (``common/addonConstants.py``) + custom, and a broken rule
raises at import, naming this file and the entry, so every Lambda and the backend tests fail closed:

* ``CUSTOM_PERMISSION_CONSTRAINT_FIELDS`` -- constraint fields a custom object type is authorized
  on, each mapped to its sample value: ``""`` for a single value, ``[]`` for a list of values (a list
  field accepts only the ``is_one_of`` / ``is_not_one_of`` operators, like ``tags``). A name matches
  ``^[A-Za-z][A-Za-z0-9]{1,63}$``, is not a Python keyword, and is not a core or add-on field.
* ``CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS`` -- permission object types for the entities a fork's
  handlers authorize, each mapped to
  ``{"label": "...", "fields": [{"label": "...", "value": "<field>"}]}``. A type name follows the
  same pattern and is not a core or add-on type; each field it lists is a core field (for example
  ``databaseId``) or one declared above. The type then appears in the constraint editor and the
  ``GET /auth/constraints/permissionObjects`` listing. It grants nothing until a constraint allows
  it, the seeded admin role included, so the fork adds its own constraints.
* ``CUSTOM_UNALLOWED_FILE_EXTENSIONS`` / ``CUSTOM_UNALLOWED_MIME_TYPES`` -- entries added to the
  upload deny lists, append-only: a lower-case extension with its leading dot (``".xyz"``), a
  lower-case MIME type (``"application/x-example"``). A core entry cannot be removed or repeated.

See the "Extending VAMS in a fork" developer guide.
"""

from typing import Dict, List, Union

CUSTOM_PERMISSION_CONSTRAINT_FIELDS: Dict[str, Union[str, List[str]]] = {}

CUSTOM_CONSTRAINT_OBJECT_TYPE_FIELDS: Dict[str, dict] = {}

CUSTOM_UNALLOWED_FILE_EXTENSIONS: List[str] = []

CUSTOM_UNALLOWED_MIME_TYPES: List[str] = []
