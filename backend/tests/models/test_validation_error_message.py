# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The caller-safe projection of a pydantic ValidationError (backend Rule 11).

`str(ValidationError)` must never reach a client. pydantic's formatting wraps the errors in a header
naming the MODEL CLASS and appends its error taxonomy plus internal constraint values:

    1 validation error for CreatePipelineRequestModel
    pipelineName
      ensure this value has at most 256 characters (type=value_error.any_str.max_length; limit_value=256)

The model class names appear in no published documentation; the taxonomy tokens and limits describe the
implementation rather than the contract. `.errors()` carries the same facts in separate fields with the
model name absent and no taxonomy inside `msg`, so `validation_error_message` assembles a safe message
from it.

Field names ARE kept: they are already published in the OpenAPI specification, and they are what makes
the error actionable. The tests below therefore assert on ABSENCE of the leaking parts rather than only
on the new format — a test that merely matched the new shape would pass trivially if a call site
bypassed the helper.
"""

import sys
import types

import pytest
from unittest.mock import MagicMock

# models.common imports the logging/audit modules at import time.
for _name, _attrs in (
    ("customLogging", {}),
    ("customLogging.logger", {"safeLogger": lambda **kw: MagicMock()}),
    ("customLogging.auditLogging", {"log_errors": lambda *a, **kw: None}),
):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_mod, _k, _v)
        sys.modules[_name] = _mod
sys.modules["customLogging"].logger = sys.modules["customLogging.logger"]

from pydantic import BaseModel, Field, ValidationError, root_validator, validator  # noqa: E402
from typing import List, Optional  # noqa: E402

from backend.backend.models.common import validation_error_message  # noqa: E402

# Everything that must never appear in a response body.
LEAK_MARKERS = ("type=", "limit_value", "__root__", "validation error for")


class SampleRequestModel(BaseModel):
    """Stands in for a real request model; the class NAME is what must not leak."""
    required_field: str
    bounded: Optional[str] = Field(None, max_length=3)
    number: Optional[int] = Field(None, ge=5)
    items: Optional[List[int]] = Field(None, max_items=1)

    @validator("bounded")
    def _reject_sentinel(cls, v):
        if v == "no":
            raise ValueError("bounded must not be the sentinel")
        return v

    @root_validator
    def _model_rule(cls, values):
        if values.get("number") == 99:
            raise ValueError("number 99 is reserved for internal use")
        return values


def _error_for(**kwargs):
    try:
        SampleRequestModel(**kwargs)
    except ValidationError as v:
        return v
    raise AssertionError(f"expected a ValidationError for {kwargs!r}")


@pytest.mark.unit
class TestNoInternalDetailLeaks:
    """The acceptance bar: absence of every leaking element, asserted directly."""

    @pytest.mark.parametrize("kwargs", [
        {},                                              # pydantic: field required
        {"required_field": "x", "bounded": "toolong"},    # pydantic: max_length
        {"required_field": "x", "number": 1},             # pydantic: ge
        {"required_field": "x", "items": [1, 2]},         # pydantic: max_items
        {"required_field": "x", "number": "abc"},         # pydantic: type coercion
        {"required_field": "x", "bounded": "no"},         # ours: field validator
        {"required_field": "x", "number": 99},            # ours: root_validator
    ])
    def test_no_model_name_taxonomy_limit_or_root_token(self, kwargs):
        message = validation_error_message(_error_for(**kwargs))
        assert "SampleRequestModel" not in message
        for marker in LEAK_MARKERS:
            assert marker not in message, f"{marker!r} leaked: {message!r}"

    def test_the_raw_str_would_have_leaked(self):
        # The counterfactual that makes the assertions above meaningful: without the helper, every
        # one of those markers IS present.
        raw = str(_error_for(required_field="x", bounded="toolong"))
        assert "SampleRequestModel" in raw
        assert "type=" in raw and "limit_value" in raw


@pytest.mark.unit
class TestActionableContentSurvives:
    """The trap this fix had to avoid: the message is often the caller's only feedback."""

    def test_field_name_and_reason_are_reported(self):
        message = validation_error_message(_error_for(required_field="x", bounded="toolong"))
        assert message == "bounded: ensure this value has at most 3 characters"

    def test_missing_required_field_is_named(self):
        assert validation_error_message(_error_for()) == "required_field: field required"

    def test_our_field_validator_text_is_preserved_verbatim(self):
        message = validation_error_message(_error_for(required_field="x", bounded="no"))
        assert message == "bounded: bounded must not be the sentinel"

    def test_model_level_message_is_kept_without_the_root_token(self):
        # A root_validator error carries this codebase's own authored text, which already names its
        # subject. It is reported alone rather than prefixed by the internal __root__ token.
        message = validation_error_message(_error_for(required_field="x", number=99))
        assert message == "number 99 is reserved for internal use"

    def test_multiple_errors_are_all_reported(self):
        message = validation_error_message(_error_for(bounded="toolong"))
        assert "required_field: field required" in message
        assert "bounded: ensure this value has at most 3 characters" in message
        assert "; " in message

    def test_nested_location_is_dotted_so_the_field_is_findable(self):
        # Mirrors the search filters shape: filters -> 0 -> query_string. The index identifies WHICH
        # element failed, so it is kept.
        class Inner(BaseModel):
            query_string: str

        class Outer(BaseModel):
            filters: List[Inner]

        try:
            Outer(filters=[{}])
        except ValidationError as v:
            assert validation_error_message(v) == "filters.0.query_string: field required"


@pytest.mark.unit
class TestDegradesSafely:
    """A caller always receives something, and a non-ValidationError never crashes the helper."""

    @pytest.mark.parametrize("value", [None, "text", ValueError("boom"), 0, []])
    def test_non_validation_error_falls_back_to_a_generic_message(self, value):
        assert validation_error_message(value) == "Invalid request."

    def test_error_with_a_blank_message_falls_back(self):
        class Fake:
            def errors(self):
                return [{"loc": ("f",), "msg": "   ", "type": "value_error"}]

        assert validation_error_message(Fake()) == "Invalid request."

    def test_error_list_that_is_empty_falls_back(self):
        class Fake:
            def errors(self):
                return []

        assert validation_error_message(Fake()) == "Invalid request."


@pytest.mark.unit
class TestHandlerCallSiteInvariants:
    """Repo-wide guards on where the helper is used.

    Both invariants were violated by the first sweep of this fix and are pinned so a future edit
    cannot reintroduce either silently.
    """

    @staticmethod
    def _handler_files():
        import glob
        import os
        root = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "handlers")
        return sorted(glob.glob(os.path.join(root, "**", "*.py"), recursive=True))

    @staticmethod
    def _sites(path):
        """(line_number, enclosing except type, whether v is logged just above) per helper call."""
        import re
        lines = open(path, encoding="utf-8").read().split("\n")
        out = []
        for i, line in enumerate(lines):
            if "validation_error_message(v)" not in line:
                continue
            exc = None
            for j in range(i - 1, max(-1, i - 8), -1):
                m = re.search(r"except\s+([A-Za-z_.]+)\s+as\s+v\s*:", lines[j])
                if m:
                    exc = m.group(1)
                    break
            logged = bool(re.search(r"logger\.(exception|error|warning)\([^)]*\{v\}",
                                    "\n".join(lines[max(0, i - 3):i])))
            out.append((i + 1, exc, logged))
        return out

    def test_helper_is_only_used_on_pydantic_validation_errors(self):
        # A plain ValueError / VAMSGeneralErrorResponse carries an authored, caller-facing message and
        # has no .errors(), so the helper would degrade it to "Invalid request." and destroy the only
        # feedback the caller gets. Rule 7 keeps those arms on str(v).
        import os
        wrong = [(os.path.basename(p), ln, exc)
                 for p in self._handler_files()
                 for ln, exc, _ in self._sites(p) if exc != "ValidationError"]
        assert wrong == [], f"helper used outside an `except ValidationError` arm: {wrong}"

    def test_every_call_site_still_logs_the_full_exception(self):
        # The helper drops detail from the RESPONSE; the log is where it has to survive. A site that
        # neither returns nor logs the specifics has traded a disclosure for a blind spot.
        import os
        unlogged = [(os.path.basename(p), ln)
                    for p in self._handler_files()
                    for ln, _, logged in self._sites(p) if not logged]
        assert unlogged == [], f"call sites not logging the exception: {unlogged}"

    def test_no_handler_returns_a_raw_validation_error_string(self):
        import os
        import re
        offenders = []
        for p in self._handler_files():
            lines = open(p, encoding="utf-8").read().split("\n")
            for i, line in enumerate(lines):
                if "validation_error(body={'message': str(v)}" not in line:
                    continue
                for j in range(i - 1, max(-1, i - 8), -1):
                    m = re.search(r"except\s+([A-Za-z_.]+)\s+as\s+v\s*:", lines[j])
                    if m:
                        if m.group(1) == "ValidationError":
                            offenders.append((os.path.basename(p), i + 1))
                        break
        assert offenders == [], f"raw str(ValidationError) reaching a client: {offenders}"


@pytest.mark.unit
class TestNestedValidationErrorIsSanitized:
    """A validator that embeds a nested ValidationError's str() into its own message.

    models/pipelines.py's tagSchema validator constructs TemplateTagFieldModel per entry and, on
    failure, raises its own ValueError naming which entry failed. Interpolating the nested error
    directly would bake pydantic's wrapper — the nested MODEL CLASS NAME and its taxonomy — into a
    single `msg` on the outer model, where validation_error_message cannot distinguish it from
    authored prose. The leak has to be closed at that validator, so it is pinned here.
    """

    @staticmethod
    def _template_request(tag_schema):
        from backend.backend.models import pipelines as mp
        return mp.CreateTemplateRequestModel(
            templateId="tpl1", templateName="T", configFormat="json", configBody="{}",
            tagSchema=tag_schema)

    def test_valid_tag_schema_is_accepted(self):
        # POSITIVE CONTROL. Every model here declares extra='ignore', so a wrong-named kwarg is
        # silently dropped and a "no leak" result below would be meaningless. This proves the
        # tagSchema kwarg reaches the validator at all.
        assert self._template_request([{"tagKey": "k1", "type": "string"}]) is not None

    def test_nested_error_does_not_leak_the_inner_model_name_or_taxonomy(self):
        with pytest.raises(ValidationError) as caught:
            self._template_request([{"tagKey": "k1", "type": "bogus"}])
        message = validation_error_message(caught.value)
        assert "TemplateTagFieldModel" not in message
        for marker in LEAK_MARKERS:
            assert marker not in message, f"{marker!r} leaked: {message!r}"

    def test_the_actionable_parts_survive(self):
        with pytest.raises(ValidationError) as caught:
            self._template_request([{"tagKey": "k1", "type": "bogus"}])
        message = validation_error_message(caught.value)
        # Which entry failed, which field, and what the permitted values are.
        assert "tagSchema[0]" in message
        assert "type" in message
        assert "string" in message and "enum" in message


@pytest.mark.unit
class TestValidatorsDoNotEchoCallerValues:
    """Validator messages must describe the rule, never repeat the submitted value (Rule 11).

    `validation_error_message` promotes each error's `msg` to the response, and an echoed value inside
    `msg` is indistinguishable from authored prose — so the echo has to be absent at the validator.
    Each test below drives a REAL request model and asserts the marker never appears.

    Every case carries a POSITIVE CONTROL asserting a valid payload is accepted. Every model here
    declares extra='ignore', so a wrong-named or wrong-typed kwarg is silently dropped and a
    "no marker" result would otherwise be meaningless.
    """

    MARKER = "SECRET-abc123"

    @staticmethod
    def _message_for(ctor, **kwargs):
        try:
            ctor(**kwargs)
        except ValidationError as exc:
            return validation_error_message(exc)
        raise AssertionError(f"expected {ctor.__name__} to reject {kwargs!r}")

    # ---- databases.py: restrictFileUploadsToExtensions ----

    @staticmethod
    def _database_kwargs(**overrides):
        import uuid
        base = {"databaseId": "db01", "description": "a valid description",
                "defaultBucketId": str(uuid.uuid4())}
        base.update(overrides)
        return base

    def test_database_control_accepts_a_valid_extension(self):
        from backend.backend.models import databases as md
        assert md.CreateDatabaseRequestModel(
            **self._database_kwargs(restrictFileUploadsToExtensions=".pdf")) is not None

    @pytest.mark.parametrize("supplied", ["SECRET-abc123", ".SECRET!!", ".a,SECRET-abc123"])
    def test_database_extension_errors_do_not_echo_the_value(self, supplied):
        from backend.backend.models import databases as md
        message = self._message_for(
            md.CreateDatabaseRequestModel,
            **self._database_kwargs(restrictFileUploadsToExtensions=supplied))
        assert self.MARKER not in message
        assert "SECRET" not in message

    # ---- metadata.py: metadataValueType ----

    def test_metadata_control_accepts_a_valid_value_type(self):
        from backend.backend.models import metadata as mm
        assert mm.MetadataItemModel(
            metadataKey="k", metadataValue="v", metadataValueType="string") is not None

    def test_metadata_value_type_error_does_not_echo_the_value(self):
        from backend.backend.models import metadata as mm
        message = self._message_for(
            mm.MetadataItemModel,
            metadataKey="k", metadataValue="v", metadataValueType=self.MARKER)
        assert self.MARKER not in message
        # The supported-type vocabulary is ours, so it stays — that is what makes it actionable.
        assert "string" in message

    # ---- metadataSchema.py: fileKeyTypeRestriction ----

    @staticmethod
    def _schema_kwargs(**overrides):
        base = {"databaseId": "db01", "metadataSchemaEntityType": "fileMetadata",
                "schemaName": "s1",
                "fields": {"fields": [{"metadataFieldKeyName": "k",
                                       "metadataFieldValueType": "string"}]}}
        base.update(overrides)
        return base

    def test_schema_control_accepts_a_valid_extension(self):
        from backend.backend.models import metadataSchema as ms
        assert ms.CreateMetadataSchemaRequestModel(
            **self._schema_kwargs(fileKeyTypeRestriction=".pdf")) is not None

    def test_schema_extension_error_does_not_echo_the_value(self):
        from backend.backend.models import metadataSchema as ms
        message = self._message_for(
            ms.CreateMetadataSchemaRequestModel,
            **self._schema_kwargs(fileKeyTypeRestriction=self.MARKER))
        assert self.MARKER not in message

    # ---- roleConstraints.py: the closed-set fields ----

    @staticmethod
    def _import_template_kwargs(**constraint_overrides):
        constraint = {"identifier": "i1", "name": "nm1", "description": "d",
                      "objectType": "asset", "groupPermissions": [],
                      "criteriaAnd": [{"field": "assetName", "operator": "contains",
                                       "value": "x"}],
                      "permissions": [{"action": "GET", "type": "allow"}]}
        constraint.update(constraint_overrides)
        return {"templateId": "tmpl-1", "variableValues": {"ROLE_NAME": "role1"},
                "constraints": [constraint]}

    def test_constraint_control_accepts_a_valid_object_type(self):
        from backend.backend.models import roleConstraints as rc
        assert rc.ImportConstraintsTemplateRequestModel(
            **self._import_template_kwargs()) is not None

    def test_invalid_object_type_does_not_echo_the_value(self):
        from backend.backend.models import roleConstraints as rc
        message = self._message_for(
            rc.ImportConstraintsTemplateRequestModel,
            **self._import_template_kwargs(objectType=self.MARKER))
        assert self.MARKER not in message
        # The allowed vocabulary survives, matching the value-free form already used elsewhere in
        # roleConstraints.py for the same check.
        assert "asset" in message


@pytest.mark.unit
class TestValueErrorArmsCannotSwallowAValidationError:
    """`pydantic.ValidationError` SUBCLASSES `ValueError`.

    So a handler whose `except ValueError` arm echoes `str(...)` catches a model-validation failure
    and returns pydantic's full wrapper — model class name, taxonomy, `__root__` — even though the
    handler never mentions ValidationError. #102's sweep could not see this: it enumerated arms that
    NAME ValidationError, and the correct enumeration is arms that can RECEIVE one.

    The check is on ORDER, not presence: a ValidationError arm placed AFTER the ValueError arm is
    dead code, so `presence` alone would pass while the leak shipped.
    """

    @staticmethod
    def _exposed_arms():
        import ast
        import glob
        import os
        found = []
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'backend', 'handlers')
        for path in glob.glob(os.path.join(root, '**', '*.py'), recursive=True):
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                types = [ast.unparse(h.type) if h.type else 'bare' for h in node.handlers]
                if 'ValueError' not in types:
                    continue
                i_ve = types.index('ValueError')
                arm = node.handlers[i_ve]
                body = ast.unparse(arm)
                # Only arms that put the exception text into a RESPONSE can leak.
                if 'str(' not in body or not any(
                        fn in body for fn in ('validation_error', 'general_error')):
                    continue
                i_vd = next((i for i, t in enumerate(types) if 'ValidationError' in t), None)
                if i_vd is None or i_vd > i_ve:
                    found.append(f"{os.path.relpath(path, root)}:{arm.lineno} order={types}")
        return sorted(found)

    def test_no_value_error_arm_can_receive_an_unsanitized_validation_error(self):
        exposed = self._exposed_arms()
        assert exposed == [], (
            "A pydantic ValidationError would be caught by an `except ValueError` arm that "
            "echoes str(...) into the response, leaking the model class name and taxonomy. "
            "Add an `except ValidationError` arm using validation_error_message() ABOVE it: "
            + "; ".join(exposed))

    def test_the_scan_itself_finds_arms_to_inspect(self):
        """POSITIVE CONTROL: the scan must actually be walking handler code.

        Without this, a scan that silently matched nothing (wrong root path, parse failures) would
        make the assertion above pass vacuously.
        """
        import ast
        import glob
        import os
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'backend', 'handlers')
        arms = 0
        for path in glob.glob(os.path.join(root, '**', '*.py'), recursive=True):
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Try):
                    arms += sum(
                        1 for h in node.handlers
                        if h.type and 'ValueError' == ast.unparse(h.type))
        assert arms >= 9, f"expected the scan to see many ValueError arms, saw {arms}"
