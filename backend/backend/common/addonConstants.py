# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Backend constants the optional add-ons (``handlers/addon/``) add to the core lists in
``common/constants.py``. Upstream-owned; each add-on keeps its entries in its own section, and a
list no add-on extends stays empty.

``common/constants.py`` merges each one into its core list at import, as core + add-on + custom
(``custom/customConstants.py``), and raises naming this file and the entry when one breaks a rule:

* ``ADDON_PERMISSION_CONSTRAINT_FIELDS`` -- constraint fields an add-on object type is authorized on,
  each mapped to its sample value: ``""`` for a single value, ``[]`` for a list of values (a list
  field accepts only the ``is_one_of`` / ``is_not_one_of`` operators, like ``tags``). A name matches
  ``^[A-Za-z][A-Za-z0-9]{1,63}$``, is not a Python keyword, and is not a core or custom field in
  any letter case.
* ``ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS`` -- permission object types, each mapped to
  ``{"label": "...", "fields": [{"label": "...", "value": "<field>"}]}``. A type name follows the same
  pattern and is not a core or custom type in any letter case; each field it lists is a core field
  (for example ``databaseId``) or one declared above. A new type grants nothing until a constraint
  allows it, the seeded admin role included.
* ``ADDON_UNALLOWED_FILE_EXTENSIONS`` / ``ADDON_UNALLOWED_MIME_TYPES`` -- entries added to the upload
  deny lists, append-only: a lower-case extension with its leading dot (``".xyz"``), a lower-case
  MIME type (``"application/x-example"``). A core entry cannot be removed or repeated.
* ``ADDON_LOG_SENSITIVE_KEYS`` -- key, header or field names whose values are never logged, added
  to both log redactors (the structured logger and the free-text redactor of returned log text),
  append-only. A key matches ``^[A-Za-z][A-Za-z0-9_-]{1,63}$`` and does not repeat a core or custom
  key in any letter case.

This file is pure data: the backend test harness loads it by path.
"""

from typing import Dict, List, Union

ADDON_PERMISSION_CONSTRAINT_FIELDS: Dict[str, Union[str, List[str]]] = {
    # EDH Workstations (handlers/addon/edh/)
    "edhImageId": "",
    "edhOsFamily": "",
    "edhInstanceType": "",
    "edhCapacityReservationId": "",
    "edhJobDefinition": "",
}

ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS: Dict[str, dict] = {
    # EDH Workstations (handlers/addon/edh/)
    "edhWorkstation": {"label": "EDH Workstation", "fields": [
        {"label": "EDH Image ID", "value": "edhImageId"},
        {"label": "EDH OS Family", "value": "edhOsFamily"},
        {"label": "EDH Instance Type", "value": "edhInstanceType"}]},
    "edhImage": {"label": "EDH Image", "fields": [
        {"label": "EDH Image ID", "value": "edhImageId"},
        {"label": "EDH OS Family", "value": "edhOsFamily"}]},
    # A registered EC2 capacity reservation: GET lists it in the launch catalog, POST launches into it.
    "edhCapacityReservation": {"label": "EDH Capacity Reservation", "fields": [
        {"label": "EDH Capacity Reservation ID", "value": "edhCapacityReservationId"}]},
    "edhContainer": {"label": "EDH Container Job", "fields": [
        {"label": "EDH Job Definition", "value": "edhJobDefinition"}]},
}

ADDON_UNALLOWED_FILE_EXTENSIONS: List[str] = []

ADDON_UNALLOWED_MIME_TYPES: List[str] = []

ADDON_LOG_SENSITIVE_KEYS: List[str] = [
    # EDH Workstations (handlers/addon/edh/): the EDH root key travels as the X-EDH-TOKEN header and
    # as the rootKey field of its secret; a desktop row carries its DCV session_token,
    # authentication_token (returned a second time under broker_session_id) and
    # session_local_admin_password; a connection_string embeds an authToken; the directory user the
    # client creates is sent with a throw-away password.
    "X-EDH-TOKEN",
    "rootKey",
    "connection_string",
    "authToken",
    "session_token",
    "authentication_token",
    "session_local_admin_password",
    "broker_session_id",
    "password",
]
