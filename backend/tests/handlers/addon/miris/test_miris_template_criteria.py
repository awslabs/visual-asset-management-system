# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shipped permission templates spell each Miris route grant as the seeded read-only role does: an
``equals`` criterion on the exact path, so no later route under ``/addon/miris/`` is granted with it.
``tests/handlers/auth/test_permission_templates_addon_routes.py`` checks the methods each template
allows through these criteria (``addonRouteGrants.py``)."""

import json
import pathlib

import pytest

from tests.handlers.addon.miris.addonRouteGrants import ROUTE_GRANTS

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[4] / "documentation" / "permissionsTemplates"


def _miris_criteria(template_name):
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    return sorted(
        (criterion["operator"], criterion["value"])
        for constraint in document["constraints"]
        for criterion in constraint.get("criteriaAnd", []) + constraint.get("criteriaOr", [])
        if criterion.get("field") == "route__path"
        and str(criterion.get("value", "")).startswith("/addon/miris/")
    )


@pytest.mark.unit
@pytest.mark.parametrize("template_name", sorted(path.name for path in TEMPLATES_DIR.glob("*.json")))
def test_the_template_spells_each_miris_criterion_as_the_seed_does(template_name):
    granted = sorted(("equals", route.path) for route, grants in ROUTE_GRANTS.items() if template_name in grants)
    assert _miris_criteria(template_name) == granted
