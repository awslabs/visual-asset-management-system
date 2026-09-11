# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-110: the asset unarchive confirmation must be enforced on the request path.

`UnarchiveAssetRequestModel.confirmUnarchive` is the ONLY place the unarchive confirmation is
enforced -- `unarchive_asset` reads `unarchiveFiles` and never reads `confirmUnarchive`, unlike
`delete_asset_permanent`, which checks `confirmPermanentDelete` itself. That makes the interlock
entirely dependent on two things holding at once:

1.  the validator running for an omitted value (`always=True`), which
    `test_confirmation_interlocks_live.py` asserts on the parsed `ModelField`; and
2.  the unarchive route parsing the caller's body through **that** model.

This file covers (2), which no other test states. A model-level assertion stays green if the
route stops parsing with the model, or if a handler mints the model itself with the confirmation
pre-set -- the shape the DELETE fallback legitimately uses for `ArchiveAssetRequestModel`, whose
confirmation is advisory. Either change silently removes the interlock, since there is no second
guard behind it.

The route assertions read `assetService.py` as text rather than importing it: the module wires a
dozen DynamoDB tables at import time, and the property under test is which model the branch names,
which the source states directly. `test_the_named_model_is_the_one_that_rejects_an_empty_body`
then resolves that name against `models.assetsV3` and exercises it, so the textual link and the
behaviour are asserted against the same spelling rather than against a hardcoded class.
"""

import pathlib
import re

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

import models.assetsV3

_ASSET_SERVICE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "backend" / "handlers" / "assets" / "assetService.py"
)

#: How far past a route match to look for the branch's `parse(...)` call. Wide enough for a
#: comment and a blank line between them, narrow enough that the next branch cannot answer.
_BRANCH_WINDOW = 400

_PARSE_CALL = re.compile(r"parse\(\s*body\s*,\s*model\s*=\s*(\w+)\s*\)")

# Route constant -> the confirmation field the branch's request model must enforce.
CONFIRMING_ROUTES = {
    "API_UNARCHIVE_ASSET": "confirmUnarchive",
    "API_DELETE_ASSET": "confirmPermanentDelete",
}


def _source():
    return _ASSET_SERVICE.read_text(encoding="utf-8")


def _model_parsed_by(route_constant, source):
    """The model named by the `parse(body, model=...)` call inside `route_constant`'s branch."""
    match = re.search(re.escape(route_constant) + r"\.matches\(", source)
    if match is None:
        return None
    window = source[match.end():match.end() + _BRANCH_WINDOW]
    parsed = _PARSE_CALL.search(window)
    return parsed.group(1) if parsed else None


@pytest.mark.unit
class TestTheConfirmationIsEnforcedOnTheRoute:
    def test_the_handler_source_was_really_read(self):
        """Positive control: a wrong path would make every assertion below vacuous."""
        assert _ASSET_SERVICE.is_file(), f"{_ASSET_SERVICE} does not exist"
        source = _source()
        for route_constant in CONFIRMING_ROUTES:
            assert route_constant in source, (
                f"{route_constant} is absent from {_ASSET_SERVICE.name}, so this file is not "
                f"reading the handler it claims to")

    @pytest.mark.parametrize("route_constant, field_name", sorted(CONFIRMING_ROUTES.items()))
    def test_the_route_parses_the_confirming_request_model(self, route_constant, field_name):
        """The caller's body must go through the model, which is where the guard lives."""
        source = _source()
        model_name = _model_parsed_by(route_constant, source)

        assert model_name is not None, (
            f"the {route_constant} branch does not parse the request body with "
            f"parse(body, model=...), so nothing validates {field_name} for that route")

        model = getattr(models.assetsV3, model_name, None)
        assert model is not None, (
            f"the {route_constant} branch parses with '{model_name}', which models.assetsV3 does "
            f"not define")
        assert field_name in model.__fields__, (
            f"the {route_constant} branch parses with {model_name}, which declares no "
            f"{field_name} field -- the route no longer carries its confirmation")

    @pytest.mark.parametrize("route_constant, field_name", sorted(CONFIRMING_ROUTES.items()))
    def test_the_named_model_is_the_one_that_rejects_an_empty_body(self, route_constant, field_name):
        """Closes the loop: the model the route names refuses a body that confirms nothing."""
        model = getattr(models.assetsV3, _model_parsed_by(route_constant, _source()))
        with pytest.raises(ValidationError):
            parse({}, model=model)

    @pytest.mark.parametrize("route_constant, field_name", sorted(CONFIRMING_ROUTES.items()))
    def test_a_confirmed_body_still_reaches_the_handler(self, route_constant, field_name):
        """Positive control: the route's model must still accept a properly confirmed request."""
        model = getattr(models.assetsV3, _model_parsed_by(route_constant, _source()))
        parsed = parse({field_name: True, "reason": "restoring for active use"}, model=model)
        assert getattr(parsed, field_name) is True
        assert parsed.reason == "restoring for active use"

    @pytest.mark.parametrize("model_name", sorted(
        {"UnarchiveAssetRequestModel", "DeleteAssetRequestModel"}))
    def test_the_handler_does_not_mint_the_confirmation_itself(self, model_name):
        """A handler-constructed model supplies the confirmation the caller was meant to.

        This is the one shape neither guard catches. `delete_asset_permanent`'s own
        `confirmPermanentDelete` check reads the same field the construction just set, so a
        handler check is no defence against it either -- the caller's intent is simply never
        consulted. A construction that becomes genuinely necessary belongs on an internal
        function taking the options directly, not on the request model.
        """
        source = _source()
        assert f"{model_name}(" not in source, (
            f"{_ASSET_SERVICE.name} constructs {model_name} directly, which sets the confirmation "
            f"in code instead of requiring it from the caller")

    def test_the_direct_construction_check_can_actually_fire(self):
        """Positive control for the check above, on the one construction that is legitimate.

        `ArchiveAssetRequestModel(confirmArchive=True)` is the DELETE fallback: archiving is
        reversible and `confirmArchive` is an advisory intent signal with no validator (it is
        listed in `test_confirmation_interlocks_live.EXEMPT_CONFIRMATIONS` for that reason). Its
        presence proves the `Model(` pattern matches a real construction in this very file, so a
        clean result above is a finding rather than a pattern that never matches anything.
        """
        assert "ArchiveAssetRequestModel(" in _source(), (
            f"{_ASSET_SERVICE.name} no longer constructs any request model directly, so the "
            f"`Model(` pattern above matches nothing and its clean result proves nothing; "
            f"re-point this control at another construction in the file, or assert the pattern "
            f"against a fixture")
