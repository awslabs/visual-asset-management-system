# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance user guide gives the exact constraints Compliance Admin 2.1 and Compliance
Global Admin 1.1 add, as `vamscli role constraint create` commands, and warns that importing a
template again duplicates the role's constraints (D130).

A template import always creates new constraints, so the guide's earlier advice to apply the
templates again left a role built from 2.0 with a second copy of its 10 constraints. Each command
in the guide is read from the page, its placeholders filled, and its body validated with the
constraint create route's own model; together they must add exactly the run-rights constraints the
template holds beyond its earlier version (5 and 3), with the same criteria and permissions.
"""

import json
import pathlib
import re
import shlex

import pytest
from aws_lambda_powertools.utilities.parser import parse

from backend.backend.models.roleConstraints import CreateConstraintRequestModel

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"
GUIDE = REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "user-guide" / "compliance.md"
ROLE, DATABASE = "fin-cadmin", "fin-db1"
# The constraints 2.1 / 1.1 added: the execute route and GET on the scope's workflows and pipelines.
RUN_RIGHTS = re.compile(r"-(workflow-execute-route|workflows-run-[a-z]+|pipelines-run-[a-z]+)$")


def _section(heading):
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(f"#### {heading}\n")
    block = re.search(r"```bash\n(.*?)\n```", text[start:], re.S)
    assert block, f"no command block under '{heading}'"
    return block.group(1)


def _documented(heading):
    """`{identifier: create-route body}` of each command under the heading, placeholders filled."""
    constraints = {}
    for line in _section(heading).splitlines():
        line = line.replace("<ROLE>", ROLE).replace("<DATABASE_ID>", DATABASE)
        argv = shlex.split(line)
        assert argv[:4] == ["vamscli", "role", "constraint", "create"], line
        identifier = argv[argv.index("-c") + 1]
        body = json.loads(argv[argv.index("--json-input") + 1])
        body["identifier"] = identifier
        constraints[identifier] = body
    return constraints


def _template_run_rights(template_name):
    template = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    template = template.replace("{{ROLE_NAME}}", ROLE).replace("{{DATABASE_ID}}", DATABASE)
    return {c["name"]: c for c in json.loads(template)["constraints"]
            if RUN_RIGHTS.search(c["name"])}


@pytest.mark.parametrize("heading, template_name, count", [
    ("Compliance Admin 2.0 to 2.1", "compliance-admin.json", 5),
    ("Compliance Global Admin 1.0 to 1.1", "compliance-global-admin.json", 3),
])
def test_the_guide_adds_exactly_the_templates_new_constraints(heading, template_name, count):
    documented = _documented(heading)
    expected = _template_run_rights(template_name)
    assert len(expected) == count
    assert sorted(documented) == sorted(expected)
    for identifier, body in documented.items():
        # The create route accepts the body as written (per-element value checks included).
        parsed = parse(body, model=CreateConstraintRequestModel)
        assert parsed.identifier == identifier
        template = expected[identifier]
        assert body["name"] == identifier
        assert body["description"] == template["description"]
        for field in ("objectType", "criteriaAnd", "criteriaOr"):
            assert body[field] == template[field], (identifier, field)
        assert [(p["groupId"], p["permission"], p["permissionType"])
                for p in body["groupPermissions"]] == [
            (ROLE, p["action"], p["type"]) for p in template["groupPermissions"]]


def test_the_guide_warns_that_importing_again_duplicates():
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index("### Adding the pipeline-rule run rights to an existing role")
    warning = text[start:text.index("#### Compliance Admin 2.0 to 2.1", start)]
    assert "Do not import the template again" in warning
    assert "second copy of its 10 existing constraints" in warning
    assert "applied to it again" not in text
