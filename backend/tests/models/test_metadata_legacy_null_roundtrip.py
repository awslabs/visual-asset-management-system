# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Saving the record a metadata GET just returned must not be a validation error.

A stored metadata record can carry no ``metadataValue`` and no ``metadataValueType``. Every
metadata GET reports each of those as ``null`` (``metadataService.metadata_response_models``)
rather than dropping the row or inventing a type, so the record stays visible and the operator
has the key needed to repair it. The clients that read that response feed it straight back into
the write body -- the web metadata editor, ``vamscli metadata <entity> update --json-input``, the
VamsMCP metadata tools that call the same ``APIClient`` methods, and the two external connectors
that shell out to the CLI. The write model refused an explicit ``null`` on either field, so the
repair path the tolerant read exists to enable ended in a 400 on a field the operator never
touched, and there was no request body they could construct to fix it.

## The shape chosen: an explicit null on the WRITE reads as the field being absent

``metadataValue: null`` parses as the empty string; ``metadataValueType: null`` parses as the
field's declared default. Both are coerced by ``pre=True`` field validators, so the field
annotations stay non-optional and the PARSED model still carries a concrete ``str`` and a
concrete ``MetadataValueType`` -- the roughly twenty ``metadata_item.metadataValueType.value``
call sites in ``metadataService`` are untouched, and only the accepted INPUT widens.
``TestTheParsedFieldContractIsUnchanged`` pins that distinction, because making the fields
``Optional`` would satisfy "the write is accepted" and break every one of those call sites.

Nothing new passes. Both coerced results are already reachable: a client that sends
``metadataValue: ""`` gets the same empty value (``validate_metadata_value_common`` returns early
on it by design, so optional fields can be blank), and a client that omits ``metadataValueType``
gets the same default. ``TestExplicitNullIsEquivalentToAbsent`` states both as parity with those
existing inputs rather than as pinned literals.

## Why not the two alternatives

* **Have the GET omit the key instead of emitting null.** It changes the response shape for every
  reader, and it does not actually fix the round-trip: ``metadataValue`` is required with no
  default, so a body with the key omitted is refused for "field required" instead. Reaching it
  would need ``.dict(exclude_none=True)``, which is per-model and not per-field in Pydantic v1 --
  it would also drop the eight ``metadataSchema*`` enrichment keys that the web editor and the
  CLI display read. It also makes the malformed record less visible, not more: an absent key
  looks like an ordinary record, where an explicit null is a signal.
* **Accept null only where the stored record also lacks the field.** A request model cannot see
  the stored record, so this would be a read-before-validate in the handler plus a second error
  path -- for no gain. Under the chosen shape a null on a record that DOES carry a type resolves
  to the same default that omitting the key already resolves to, so the distinction protects
  nothing and the extra 400 would name a field the caller cannot act on.

## What must still be refused

FIX-061 (S2-BACKEND-119) records the owner's ruling that retroactive enforcement of a newly
required schema field is INTENDED: existing records are not grandfathered and
``defaultMetadataFieldValue`` is not required. A tolerant READ must not become a tolerant
VALIDATION. The coerced empty value is what keeps that true -- ``is_empty_value("")`` is True, so
``validate_metadata_against_schema`` still refuses a schema-required field that carries no value.
That is asserted against the real handler in
``tests/handlers/metadata/test_metadataService_legacy_row_repair_roundtrip.py``, which is also
where the end-to-end round-trip (real GET output fed into the real write) lives. This file covers
the model contract only.

## Why the imports below are at module scope

The repo-wide ``conftest.py`` installs a NON-package placeholder at
``sys.modules['backend.backend']`` from an autouse fixture, guarded by ``if ... not in
sys.modules``. A lazy ``from backend.backend.models.metadata import ...`` inside a test body
therefore raises ``ModuleNotFoundError: ... 'backend.backend' is not a package`` on any run where
no earlier-collected module happened to claim that name -- i.e. on every selection narrower than
the whole suite. Importing at module scope claims it during collection, before the fixture looks.
See the same note in ``test_metadata_model_standards.py``.
"""

import pytest

from aws_lambda_powertools.utilities.parser import ValidationError  # noqa: E402
from backend.backend.models.metadata import (  # noqa: E402
    MAX_METADATA_VALUE_LENGTH,
    CreateAssetLinkMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    CreateFileMetadataRequestModel,
    MetadataItemModel,
    MetadataValueType,
    AssetLinkMetadataResponseModel,
    AssetMetadataResponseModel,
    DatabaseMetadataResponseModel,
    FileMetadataResponseModel,
    UpdateAssetLinkMetadataRequestModel,
    UpdateAssetMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
    UpdateFileMetadataRequestModel,
)

TOLERATED_FIELDS = ("metadataValue", "metadataValueType")


def _get_shaped_item(response_model_cls, absent=TOLERATED_FIELDS, **identity):
    """One metadata GET response item for a record that carries none of ``absent``.

    Built through the response model and ``.copy(update=...)`` -- the same construction
    ``metadataService.metadata_response_models`` uses to report an absent stored attribute as
    null through a model that declares it non-optional. So the dict below carries every key a
    real GET item carries, including the eight ``metadataSchema*`` enrichment keys and the entity
    identity fields, all of which the write model has to ignore.
    """
    complete = response_model_cls(
        metadataKey="legacyKey",
        metadataValue="v",
        metadataValueType="string",
        **identity,
    )
    return complete.copy(update={name: None for name in absent}).dict()


@pytest.mark.unit
class TestExplicitNullIsEquivalentToAbsent:
    """The write reads a null on either field as the field not being supplied at all."""

    def test_a_null_value_type_resolves_to_the_declared_default(self):
        parsed = MetadataItemModel(metadataKey="k", metadataValue="v", metadataValueType=None)
        declared_default = MetadataItemModel.__fields__["metadataValueType"].default
        assert parsed.metadataValueType == declared_default, (
            "a null value type did not resolve to the field's own declared default: "
            f"{parsed.metadataValueType!r}"
        )

    def test_a_null_value_type_parses_the_same_as_omitting_the_key(self):
        """Parity, not a pinned literal: whatever the default becomes, the two agree."""
        with_null = MetadataItemModel(
            metadataKey="k", metadataValue="v", metadataValueType=None).dict()
        omitted = MetadataItemModel(metadataKey="k", metadataValue="v").dict()
        assert with_null == omitted, (
            f"null and omitted disagree: {with_null} vs {omitted}")

    def test_a_null_value_parses_the_same_as_an_empty_string(self):
        """An empty value is already an accepted input -- validate_metadata_value_common
        returns early on it so that optional fields may be blank."""
        with_null = MetadataItemModel(
            metadataKey="k", metadataValue=None, metadataValueType="string").dict()
        empty = MetadataItemModel(
            metadataKey="k", metadataValue="", metadataValueType="string").dict()
        assert with_null == empty, f"null and empty disagree: {with_null} vs {empty}"

    def test_both_fields_null_at_once_is_accepted(self):
        """The shape of a stored record that predates both attributes."""
        parsed = MetadataItemModel(
            metadataKey="k", metadataValue=None, metadataValueType=None)
        assert parsed.metadataValue == ""
        assert parsed.metadataValueType == \
            MetadataItemModel.__fields__["metadataValueType"].default

    def test_a_null_value_does_not_skip_the_type_check_for_the_resolved_type(self):
        """Control on the coercion order: the value validation still runs on the result.

        Without it, "null is accepted" could be satisfied by a pre-validator that returns
        early and leaves the whole root validator unreached.
        """
        parsed = MetadataItemModel(
            metadataKey="k", metadataValue=None, metadataValueType="number")
        assert parsed.metadataValue == "", (
            "the coerced empty value did not survive the number-type validation")
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey="k", metadataValue="abc", metadataValueType="number")


@pytest.mark.unit
class TestTheParsedFieldContractIsUnchanged:
    """Only the accepted input widened. The parsed model still carries concrete values.

    ``metadataService`` reads ``metadata_item.metadataValueType.value`` at roughly twenty write
    sites and ``metadata_item.metadataValue`` as a DynamoDB ``S`` attribute at as many. Making
    either field ``Optional`` would also make "the write is accepted" true, and would break all
    of them -- which is why these assert on the PARSED field rather than on the declaration text.
    """

    @pytest.mark.parametrize("field_name", TOLERATED_FIELDS)
    def test_the_field_does_not_admit_none_after_parsing(self, field_name):
        assert MetadataItemModel.__fields__[field_name].allow_none is False, (
            f"{field_name} was made nullable; the parsed model can now carry None and every "
            f"metadataService call site that reads it unconditionally breaks")

    def test_the_value_type_field_still_holds_the_enum_type_and_a_member_default(self):
        field = MetadataItemModel.__fields__["metadataValueType"]
        assert field.type_ is MetadataValueType
        assert isinstance(field.default, MetadataValueType), (
            f"the declared default is not a MetadataValueType member: {field.default!r}")

    @pytest.mark.parametrize("field_name", ("metadataKey",) + TOLERATED_FIELDS)
    def test_no_field_kwarg_was_swallowed_into_field_info_extra(self, field_name):
        """Pydantic v1 collects an unrecognised Field kwarg into ``extra`` and validates
        nothing with it, so a v2 spelling reads like a live constraint."""
        assert not MetadataItemModel.__fields__[field_name].field_info.extra, (
            f"{field_name} carries swallowed Field kwargs: "
            f"{MetadataItemModel.__fields__[field_name].field_info.extra}")

    def test_metadata_value_is_still_required(self):
        """Widened to accept null, NOT to accept the key being missing."""
        assert MetadataItemModel.__fields__["metadataValue"].required is True
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey="k")


@pytest.mark.unit
class TestNothingElseWasWidened:
    """The counter-tests. "Accepts null" must not have become "accepts anything"."""

    def test_an_unknown_value_type_is_still_rejected(self):
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey="k", metadataValue="v", metadataValueType="notAType")

    def test_a_null_metadata_key_is_still_rejected(self):
        """The coercion is scoped to the two tolerated fields; the key is neither."""
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey=None, metadataValue="v")

    def test_an_empty_metadata_key_is_still_rejected(self):
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey="", metadataValue="v")

    def test_an_over_long_value_is_still_rejected(self):
        with pytest.raises(ValidationError):
            MetadataItemModel(
                metadataKey="k", metadataValue="x" * (MAX_METADATA_VALUE_LENGTH + 1))

    def test_a_malformed_typed_value_is_still_rejected(self):
        with pytest.raises(ValidationError):
            MetadataItemModel(
                metadataKey="k", metadataValue="{not json}", metadataValueType="json")


# (id, request model, extra constructor kwargs) for every wrapper a repair write goes through.
# Both file modes are here: the ATTRIBUTE mode adds a check that reads item.metadataValueType,
# which a None would either reject or crash on.
WRAPPERS = [
    ("create-assetMetadata", CreateAssetMetadataRequestModel, {}),
    ("update-assetMetadata", UpdateAssetMetadataRequestModel, {"updateType": "update"}),
    ("create-databaseMetadata", CreateDatabaseMetadataRequestModel, {}),
    ("update-databaseMetadata", UpdateDatabaseMetadataRequestModel, {"updateType": "update"}),
    ("create-assetLinkMetadata", CreateAssetLinkMetadataRequestModel, {}),
    ("update-assetLinkMetadata", UpdateAssetLinkMetadataRequestModel, {"updateType": "update"}),
    ("create-fileMetadata", CreateFileMetadataRequestModel,
     {"filePath": "/folder/file.txt", "type": "metadata"}),
    ("update-fileMetadata", UpdateFileMetadataRequestModel,
     {"filePath": "/folder/file.txt", "type": "metadata", "updateType": "update"}),
    ("create-fileAttribute", CreateFileMetadataRequestModel,
     {"filePath": "/folder/file.txt", "type": "attribute"}),
    ("update-fileAttribute", UpdateFileMetadataRequestModel,
     {"filePath": "/folder/file.txt", "type": "attribute", "updateType": "update"}),
]

_WRAPPER_IDS = [name for name, _, _ in WRAPPERS]

# One GET response item per entity type, in the shape that entity's GET returns it.
GET_SHAPED_ITEMS = [
    ("assetMetadata", AssetMetadataResponseModel, {"databaseId": "db1", "assetId": "asset1"}),
    ("databaseMetadata", DatabaseMetadataResponseModel, {"databaseId": "db1"}),
    ("assetLinkMetadata", AssetLinkMetadataResponseModel, {"assetLinkId": "link1"}),
    ("fileMetadata", FileMetadataResponseModel,
     {"databaseId": "db1", "assetId": "asset1", "filePath": "/folder/file.txt"}),
]

_ITEM_IDS = [name for name, _, _ in GET_SHAPED_ITEMS]


@pytest.mark.unit
@pytest.mark.parametrize("wrapper_name,wrapper_cls,wrapper_kwargs", WRAPPERS, ids=_WRAPPER_IDS)
class TestEveryWriteWrapperAcceptsAGetShapedItem:
    """The item comes back inside a request wrapper, so the wrapper has to accept it too."""

    @pytest.mark.parametrize(
        "item_name,response_cls,identity", GET_SHAPED_ITEMS, ids=_ITEM_IDS)
    def test_a_get_item_with_both_fields_null_round_trips(
            self, wrapper_name, wrapper_cls, wrapper_kwargs, item_name, response_cls, identity):
        item = _get_shaped_item(response_cls, **identity)
        assert all(item[name] is None for name in TOLERATED_FIELDS), (
            f"the {item_name} GET-shaped item under test is not actually null: {item}")

        request = wrapper_cls(metadata=[item], **wrapper_kwargs)

        assert len(request.metadata) == 1
        parsed = request.metadata[0]
        assert parsed.metadataKey == "legacyKey"
        assert parsed.metadataValue == ""
        assert parsed.metadataValueType == \
            MetadataItemModel.__fields__["metadataValueType"].default

    @pytest.mark.parametrize(
        "item_name,response_cls,identity", GET_SHAPED_ITEMS, ids=_ITEM_IDS)
    def test_a_complete_get_item_still_round_trips_unchanged(
            self, wrapper_name, wrapper_cls, wrapper_kwargs, item_name, response_cls, identity):
        """Positive control: the wrapper accepts a well-formed GET item, and does not
        rewrite its value or type on the way through."""
        item = _get_shaped_item(response_cls, absent=(), **identity)
        assert item["metadataValue"] == "v" and item["metadataValueType"] is not None

        request = wrapper_cls(metadata=[item], **wrapper_kwargs)

        parsed = request.metadata[0]
        assert parsed.metadataValue == "v"
        assert parsed.metadataValueType == MetadataValueType.STRING
