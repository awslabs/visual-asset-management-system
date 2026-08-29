# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-176 (LOW) and S2-BACKEND-177 (LOW): metadata model standards.

**-176** -- backend/CLAUDE.md Rule 2 requires ``BaseModel`` to come from
``aws_lambda_powertools.utilities.parser``; ``from pydantic import BaseModel`` is the named
anti-pattern. There is no runtime difference today because the parser re-exports pydantic
v1's ``BaseModel``, which is exactly why nothing catches a drift: the direct import is the
path by which a v2 API creeps in unnoticed. The sweep is AST-based so it also catches the
multi-name form (``from pydantic import BaseModel, Field, validator``), which a grep for the
single-name statement misses.

**-177** -- Rule 11 forbids echoing request input into a message returned to the client.
``_validate_lon_lat`` interpolated the caller's submitted coordinate, and
``models/common.py:validation_error_message`` emits a validator's ``msg`` verbatim, so the
value reached the 400 body. The value is type-guaranteed numeric, so nothing injectable was
reflected -- it is a standards fix, and the coordinate is logged instead.

## Why the imports below are at module scope

The repo-wide ``conftest.py`` installs a NON-package placeholder at
``sys.modules['backend.backend']`` from an autouse fixture, guarded by ``if ... not in
sys.modules``. A lazy ``from backend.backend.models.metadata import ...`` inside a test body
therefore raises ``ModuleNotFoundError: ... 'backend.backend' is not a package`` on any run
where no earlier-collected module happened to claim that name -- i.e. on every selection
narrower than the whole suite. Importing at module scope claims it during collection, before
the fixture looks. This file used to fail 5 of its 9 tests when run on its own path; see
``docs/review/findings/S27-test-selection-dependence.json`` and the guard test at the bottom
of ``TestBaseModelComesFromPowertools``.
"""

import ast
import json
import os
import sys

import pytest

from aws_lambda_powertools.utilities.parser import (  # noqa: E402
    BaseModel as PowertoolsBaseModel,
    ValidationError,
)
from backend.backend.models import metadata as metadata_module  # noqa: E402
from backend.backend.models.metadata import (  # noqa: E402
    MetadataItemModel,
    _validate_lon_lat,
)


_MODELS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend", "models")
)


def _model_files():
    return sorted(
        os.path.join(_MODELS_DIR, name)
        for name in os.listdir(_MODELS_DIR)
        if name.endswith(".py")
    )


def _pydantic_basemodel_importers(paths):
    """``file:line`` for every ``from pydantic import ... BaseModel ...`` statement."""
    offenders = []
    for path in paths:
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pydantic":
                if any(alias.name == "BaseModel" for alias in node.names):
                    offenders.append(f"{os.path.basename(path)}:{node.lineno}")
    return offenders


@pytest.mark.unit
class TestBaseModelComesFromPowertools:
    def test_the_metadata_model_file_does_not_import_basemodel_from_pydantic(self):
        offenders = _pydantic_basemodel_importers(
            [os.path.join(_MODELS_DIR, "metadata.py")]
        )
        assert offenders == [], (
            "backend Rule 2: BaseModel must come from aws_lambda_powertools.utilities.parser"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "S2-BACKEND-176 second site: models/metadataSchema.py imports BaseModel from "
            "pydantic. That file is outside this shard's boundary; the marker turns this "
            "test red the moment it is fixed, which is when it must be deleted."
        ),
    )
    def test_no_model_file_imports_basemodel_from_pydantic(self):
        assert _pydantic_basemodel_importers(_model_files()) == []

    def test_the_sweep_actually_reads_the_model_files(self):
        """Positive control: the negative assertion above is not scanning an empty set."""
        files = _model_files()
        assert len(files) > 10
        assert any(name.endswith("metadata.py") for name in files)

    def test_the_sweep_detects_the_import_it_forbids(self):
        """Positive control on the detector itself, against a synthetic source."""
        tree = ast.parse("from pydantic import BaseModel, Field\n")
        found = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "pydantic"
            and any(alias.name == "BaseModel" for alias in node.names)
        ]
        assert len(found) == 1

    def test_the_metadata_models_use_the_powertools_basemodel(self):
        assert issubclass(MetadataItemModel, PowertoolsBaseModel)

    def test_this_file_can_reach_the_real_backend_package(self):
        """Guards the import PLACEMENT, which is invisible in a full-suite run.

        The imports this file needs are at module scope so they run during collection, before
        the root conftest's autouse fixture installs its non-package placeholder at
        ``sys.modules['backend.backend']``. Move them back into the test bodies and every test
        that touches ``models/metadata`` fails with ``'backend.backend' is not a package`` --
        but ONLY when the selection is narrow enough that no other module claimed the name
        first, so a full-suite run stays green and reports nothing.

        That is how a future reader notices: this assertion goes red on the same narrow run
        that the real tests would, and it names the placeholder rather than the handler package
        the traceback would blame.
        """
        module = sys.modules["backend.backend"]
        assert hasattr(module, "__path__"), (
            "sys.modules['backend.backend'] is the conftest placeholder rather than the real "
            "package, so this file's imports are happening after the autouse fixture and it "
            "cannot pass on its own path"
        )


@pytest.mark.unit
class TestGeoCoordinateErrorsDoNotEchoTheValue:
    @staticmethod
    def _geopoint(lon, lat):
        """A well-formed GEOPOINT value: ``{"type": "Point", "coordinates": [lon, lat]}``.

        Anything else is refused by ``models/metadata.py`` BEFORE the coordinate validator runs.
        The earlier version of the end-to-end test below submitted ``{"lon": ..., "lat": ...}``,
        which fails on "GEOPOINT type must be 'Point'" and never reaches ``_validate_lon_lat``
        at all -- so its "the value is not echoed" assertion held for a reason unrelated to the
        validator it names.
        """
        return json.dumps({"type": "Point", "coordinates": [lon, lat]})

    @staticmethod
    def _message(lon, lat):
        with pytest.raises(ValueError) as raised:
            _validate_lon_lat([lon, lat], "coordinates")
        return str(raised.value)

    def test_an_out_of_range_longitude_is_not_echoed(self):
        message = self._message(999.5, 0)
        assert "999" not in message
        assert "longitude must be between -180 and 180" in message

    def test_an_out_of_range_latitude_is_not_echoed(self):
        message = self._message(0, -91.25)
        assert "91.25" not in message
        assert "latitude must be between -90 and 90" in message

    def test_an_in_range_coordinate_raises_nothing(self):
        """Positive control: the validator still accepts a valid coordinate."""
        assert _validate_lon_lat([12.5, -45.25], "coordinates") is None

    def test_an_in_range_geopoint_is_accepted_by_the_model(self):
        """Positive control for the payload SHAPE used by the two tests below.

        Without this, a payload the model rejects for an unrelated reason -- a missing
        ``"type": "Point"``, say -- makes "the submitted value is absent from the message" hold
        vacuously, because the message comes from a check that runs earlier and never sees the
        coordinate. This asserts the shape is one the model accepts, so a failure below can only
        be the range check.
        """
        item = MetadataItemModel(
            metadataKey="location",
            metadataValue=self._geopoint(12.5, -45.25),
            metadataValueType="geopoint",
        )
        assert item.metadataKey == "location"

    def test_the_client_facing_message_carries_no_submitted_value(self):
        """End to end through the model, which is what reaches validation_error_message."""
        with pytest.raises(ValidationError) as raised:
            MetadataItemModel(
                metadataKey="location",
                metadataValue=self._geopoint(999.5, 0),
                metadataValueType="geopoint",
            )
        message = str(raised.value)
        assert "999" not in message, (
            f"the coordinate the caller submitted reached the client-facing message: {message}"
        )
        # The message must come from _validate_lon_lat, not from a shape check that runs first.
        assert "longitude must be between -180 and 180" in message, (
            f"the model refused this payload before the coordinate validator ran, so this test "
            f"is not measuring _validate_lon_lat: {message}"
        )

    def test_the_end_to_end_assertion_would_catch_an_echoing_validator(self, monkeypatch):
        """Positive control on the assertion itself, by mutating the code it names.

        The end-to-end check is only as good as its payload, and its payload was wrong once
        already. Here ``_validate_lon_lat`` is replaced with a version that interpolates the
        coordinate -- exactly the Rule 11 violation -177 fixed -- and the same request is
        re-submitted. The submitted value MUST now appear in the message; if it does not, the
        payload never reaches that validator and the assertions above prove nothing about it.
        """
        def echoing(coord, label):
            lon, lat = coord[0], coord[1]
            if lon < -180 or lon > 180:
                raise ValueError(f"{label} longitude {lon} must be between -180 and 180")
            if lat < -90 or lat > 90:
                raise ValueError(f"{label} latitude {lat} must be between -90 and 90")

        monkeypatch.setattr(metadata_module, "_validate_lon_lat", echoing)

        with pytest.raises(ValidationError) as raised:
            MetadataItemModel(
                metadataKey="location",
                metadataValue=self._geopoint(999.5, 0),
                metadataValueType="geopoint",
            )
        assert "999.5" in str(raised.value), (
            f"an echoing validator did not reach the client-facing message, so this payload "
            f"does not exercise _validate_lon_lat: {raised.value}"
        )

    def test_the_payload_shape_that_made_this_test_vacuous_is_refused_earlier(self):
        """Pinned so the vacuous form cannot come back looking equivalent.

        ``{"lon": ..., "lat": ...}`` is not a GEOPOINT: ``models/metadata.py`` requires
        ``"type": "Point"`` and refuses this shape before any coordinate is examined. Submitting
        it and asserting the coordinate is absent from the message passes for that reason alone.
        """
        with pytest.raises(ValidationError) as raised:
            MetadataItemModel(
                metadataKey="location",
                metadataValue='{"lon": 999.5, "lat": 0}',
                metadataValueType="geopoint",
            )
        message = str(raised.value)
        assert "type must be 'Point'" in message, message
        assert "longitude must be between" not in message, (
            f"this payload now reaches the coordinate validator, so the note above is stale: "
            f"{message}"
        )

    def test_the_latitude_half_reaches_the_validator_too(self):
        """Both branches of `_validate_lon_lat`, reached through the model."""
        with pytest.raises(ValidationError) as raised:
            MetadataItemModel(
                metadataKey="location",
                metadataValue=self._geopoint(0, -91.25),
                metadataValueType="geopoint",
            )
        message = str(raised.value)
        assert "91.25" not in message, message
        assert "latitude must be between -90 and 90" in message, (
            f"the model refused this payload before the coordinate validator ran: {message}"
        )
