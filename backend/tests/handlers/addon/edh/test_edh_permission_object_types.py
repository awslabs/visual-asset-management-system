# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH permission object types and constraint fields come from the add-on constants lane.

``common/addonConstants.py`` declares ``edhWorkstation``, ``edhImage``, ``edhCapacityReservation`` and
``edhContainer`` and the five fields they are authorized on; ``common/constants.py`` merges them after the core
lists, so the constraint
editor, the ``GET /auth/constraints/permissionObjects`` listing and the enforcer's field scrub see them
like core types. A constraint needs a criterion and every criterion field must sit in its type's matrix,
so a type with no field could never be granted to any role.
"""

import pytest

from backend.backend.common.addonConstants import (
    ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS,
    ADDON_PERMISSION_CONSTRAINT_FIELDS,
)
from backend.backend.common.constants import (
    ALLOWED_CONSTRAINT_OBJECT_TYPES,
    CONSTRAINT_OBJECT_TYPE_FIELDS,
    CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES,
    CORE_CONSTRAINT_OBJECT_TYPE_FIELDS,
    CORE_PERMISSION_CONSTRAINT_FIELDS,
    PERMISSION_CONSTRAINT_FIELDS,
    get_constraint_fields_for_object_type,
)

EDH_TYPES = ["edhWorkstation", "edhImage", "edhCapacityReservation", "edhContainer"]
EDH_FIELDS = ["edhImageId", "edhOsFamily", "edhInstanceType", "edhCapacityReservationId", "edhJobDefinition"]
# Each EDH type: its editor label and the fields it is authorized on, in editor order.
EDH_TYPE_MATRIX = {
    "edhWorkstation": ("EDH Workstation", ["edhImageId", "edhOsFamily", "edhInstanceType"]),
    "edhImage": ("EDH Image", ["edhImageId", "edhOsFamily"]),
    "edhCapacityReservation": ("EDH Capacity Reservation", ["edhCapacityReservationId"]),
    "edhContainer": ("EDH Container Job", ["edhJobDefinition"]),
}


@pytest.mark.unit
class TestEdhObjectTypesComeFromTheAddOnLane:
    def test_the_add_on_lane_declares_the_four_types_and_five_fields(self):
        assert list(ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS) == EDH_TYPES
        assert list(ADDON_PERMISSION_CONSTRAINT_FIELDS) == EDH_FIELDS

    def test_no_edh_name_is_a_core_name(self):
        assert not set(EDH_TYPES) & set(CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES)
        assert not set(EDH_TYPES) & set(CORE_CONSTRAINT_OBJECT_TYPE_FIELDS)
        assert not set(EDH_FIELDS) & set(CORE_PERMISSION_CONSTRAINT_FIELDS)
        # Control: the core lists are the real ones.
        assert "database" in CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES and "databaseId" in CORE_PERMISSION_CONSTRAINT_FIELDS

    def test_the_merged_lists_carry_the_edh_entries_after_the_core_ones(self):
        core_count = len(CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES)
        assert ALLOWED_CONSTRAINT_OBJECT_TYPES[:core_count] == CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES
        assert ALLOWED_CONSTRAINT_OBJECT_TYPES[core_count:core_count + len(EDH_TYPES)] == EDH_TYPES
        field_names = list(PERMISSION_CONSTRAINT_FIELDS)
        core_fields = len(CORE_PERMISSION_CONSTRAINT_FIELDS)
        assert field_names[core_fields:core_fields + len(EDH_FIELDS)] == EDH_FIELDS

    @pytest.mark.parametrize("object_type", sorted(EDH_TYPE_MATRIX))
    def test_each_type_has_its_label_and_field_matrix(self, object_type):
        label, fields = EDH_TYPE_MATRIX[object_type]
        assert CONSTRAINT_OBJECT_TYPE_FIELDS[object_type]["label"] == label
        assert get_constraint_fields_for_object_type(object_type) == fields

    @pytest.mark.parametrize("object_type", EDH_TYPES)
    def test_each_type_carries_string_fields(self, object_type):
        fields = get_constraint_fields_for_object_type(object_type)
        assert fields, f"{object_type} has no constraint field"
        for field in fields:
            assert isinstance(PERMISSION_CONSTRAINT_FIELDS[field], str), (
                f"{object_type}.{field} must be a string sample; list fields refuse regex operators"
            )
