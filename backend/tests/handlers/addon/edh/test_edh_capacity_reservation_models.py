# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input bounds of the capacity-reservation models, and the reservation fields of the workstation record.

A reservation id is ``cr-`` and 17 lowercase hex digits, as EC2 issues it; a label is 1-64 characters of the
display-name charset, trimmed. The record returns the reservation a launch was sent with, its label (joined
at read time by the handler, never stored) and whether the launch fell back to on-demand; the id it asked
for on a fallback stays on the row.
"""

import pytest
from aws_lambda_powertools.utilities.parser import ValidationError, parse

import models.addon.edh as edh_models

CR_ID = "cr-0123456789abcdef0"
BOOTSTRAP_ID = "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30"
CREATE = {"bootstrapId": BOOTSTRAP_ID, "imageId": "42", "instanceType": "g5.xlarge", "name": "My desktop",
          "rootSizeGb": 100, "osFamily": "linux"}
RECORD = {"bootstrapId": BOOTSTRAP_ID, "userId": "alice", "edhUid": "vfpmansl7byak6gq", "state": "ACTIVE",
          "name": "My desktop", "imageId": "42", "osFamily": "linux", "instanceType": "g5.xlarge",
          "createdAt": "2026-09-08T10:00:00Z", "updatedAt": "2026-09-08T10:05:00Z"}
MALFORMED_IDS = ["cr-0123456789abcdef", "cr-0123456789abcdef00", "cr-0123456789ABCDEF0", "CR-0123456789abcdef0",
                 "cr-0123456789abcdefg", " cr-0123456789abcdef0", "ami-0123456789abcdef0", ""]


@pytest.mark.unit
class TestCreateWorkstationReservationField:
    def test_the_field_is_optional(self):
        assert parse(event=CREATE, model=edh_models.CreateWorkstationRequestModel).capacityReservationId is None

    def test_a_well_formed_id_is_accepted(self):
        request = parse(event={**CREATE, "capacityReservationId": CR_ID},
                        model=edh_models.CreateWorkstationRequestModel)
        assert request.capacityReservationId == CR_ID

    @pytest.mark.parametrize("value", MALFORMED_IDS)
    def test_a_malformed_id_is_refused(self, value):
        with pytest.raises(ValidationError):
            parse(event={**CREATE, "capacityReservationId": value}, model=edh_models.CreateWorkstationRequestModel)

    def test_the_pattern_is_exported(self):
        assert edh_models.edh_capacity_reservation_id_pattern == r'^cr-[0-9a-f]{17}\Z'


@pytest.mark.unit
class TestRegisterCapacityReservationRequestModel:
    def test_defaults(self):
        request = parse(event={"capacityReservationId": CR_ID, "label": "  Render farm "},
                        model=edh_models.RegisterCapacityReservationRequestModel)
        assert request.label == "Render farm"
        assert request.fallbackToOnDemand is None and request.enabled is None

    def test_flags(self):
        request = parse(event={"capacityReservationId": CR_ID, "label": "Strict", "fallbackToOnDemand": False,
                               "enabled": True}, model=edh_models.RegisterCapacityReservationRequestModel)
        assert (request.fallbackToOnDemand, request.enabled) == (False, True)

    @pytest.mark.parametrize("label", ["x", "x" * 64, "GPU farm 1 - us-east-1a", "farm_2.b"])
    def test_labels_accepted(self, label):
        parse(event={"capacityReservationId": CR_ID, "label": label},
              model=edh_models.RegisterCapacityReservationRequestModel)

    @pytest.mark.parametrize("label", ["", "   ", "x" * 65, "semi;colon", "<b>", "a/b"])
    def test_labels_refused(self, label):
        with pytest.raises(ValidationError):
            parse(event={"capacityReservationId": CR_ID, "label": label},
                  model=edh_models.RegisterCapacityReservationRequestModel)

    @pytest.mark.parametrize("value", MALFORMED_IDS)
    def test_ids_refused(self, value):
        with pytest.raises(ValidationError):
            parse(event={"capacityReservationId": value, "label": "Farm"},
                  model=edh_models.RegisterCapacityReservationRequestModel)

    @pytest.mark.parametrize("field", ["fallbackToOnDemand", "enabled"])
    @pytest.mark.parametrize("value", ["yes", 1, "true"])
    def test_flags_are_strict_booleans(self, field, value):
        with pytest.raises(ValidationError):
            parse(event={"capacityReservationId": CR_ID, "label": "Farm", field: value},
                  model=edh_models.RegisterCapacityReservationRequestModel)


@pytest.mark.unit
class TestUpdateCapacityReservationRequestModel:
    def test_any_one_field_is_enough(self):
        for body in ({"label": "New"}, {"fallbackToOnDemand": False}, {"enabled": False}):
            parse(event=body, model=edh_models.UpdateCapacityReservationRequestModel)

    @pytest.mark.parametrize("body", [{}, {"label": None}, {"label": ""}, {"enabled": "no"}, {"label": "x" * 65}])
    def test_refused(self, body):
        with pytest.raises(ValidationError):
            parse(event=body, model=edh_models.UpdateCapacityReservationRequestModel)


@pytest.mark.unit
class TestRecordReservationFields:
    def test_a_row_without_a_reservation_projects_nulls_and_no_fallback(self):
        record = edh_models.WorkstationRecordModel(**RECORD).dict()
        assert record["capacityReservationId"] is None
        assert record["capacityReservationLabel"] is None
        assert record["capacityReservationFallback"] is False

    def test_a_reservation_row_projects_its_id_and_label(self):
        record = edh_models.WorkstationRecordModel(**RECORD, capacityReservationId=CR_ID,
                                                   capacityReservationLabel="Farm").dict()
        assert (record["capacityReservationId"], record["capacityReservationLabel"]) == (CR_ID, "Farm")

    def test_a_fallback_row_projects_the_fallback_and_never_the_requested_id(self):
        record = edh_models.WorkstationRecordModel(**RECORD, capacityReservationFallback=True,
                                                   capacityReservationRequested=CR_ID).dict()
        assert record["capacityReservationFallback"] is True
        assert record["capacityReservationId"] is None
        assert "capacityReservationRequested" not in record


@pytest.mark.unit
class TestCapacityReservationViewModel:
    def test_the_view_fields(self):
        assert list(edh_models.CapacityReservationViewModel.__fields__) == [
            "capacityReservationId", "label", "fallbackToOnDemand", "enabled", "instanceType", "availabilityZone",
            "instancePlatform", "state", "totalInstanceCount", "availableInstanceCount", "instanceMatchCriteria",
            "endDate", "reservationType",
        ]
