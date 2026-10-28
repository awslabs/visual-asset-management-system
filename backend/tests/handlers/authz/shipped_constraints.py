# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Constraint sets VAMS ships, in the shape the Casbin policy builder reads.

* ``template_constraints`` -- a ``documentation/permissionsTemplates/`` template run through the
  import's own ``substitute_variables`` and ``build_constraint_data``.
* ``seeded_constraints`` -- the ``adminInitialConstraints`` / ``initialConstraints`` array of a
  ``dynamodb-authdefaults-*`` CDK construct, read from the TypeScript source and converted the way
  that construct's ``convertConstraintToNewFormat`` converts it before the deploy writes it.

The TypeScript reader understands exactly what those two arrays contain -- object literals, ``{ S: ...
}`` attribute values, string and template literals, the role-name constants, line comments and the
add-on spread -- and raises on anything else, so a construct that changes shape fails the tests that
use it rather than seeding them with a partial array.
"""

import json
import pathlib
import re

from backend.backend.handlers.auth import authConstraintsTemplateService as template_service

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"
AUTH_DEFAULTS_DIR = REPO_ROOT / "infra" / "lib" / "nestedStacks" / "auth" / "constructs"

# Construct file -> (array name, the constants its literals reference). The values are the ones the
# constructs assign right above the array.
SEEDED_ROLE_CONSTRUCTS = {
    "admin": ("dynamodb-authdefaults-admin-construct.ts", "adminInitialConstraints",
              {"roleNameAdmin": "admin", "roleNameIDCleanAdmin": "admin"}),
    "basicReadOnly": ("dynamodb-authdefaults-ro-construct.ts", "initialConstraints",
                      {"roleName": "basicReadOnly", "roleNameIDClean": "basicro"}),
}

_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_TEMPLATE_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_$][A-Za-z0-9_$]*)\}")


def template_names():
    return sorted(path.name for path in TEMPLATES_DIR.glob("*.json"))


def template_constraints(template_name, role_name, variable_values):
    """The constraints an import of ``template_name`` for ``role_name`` stores."""
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = [
        {
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": constraint.get("criteriaAnd", []),
            "criteriaOr": constraint.get("criteriaOr", []),
            "groupPermissions": constraint.get("groupPermissions", []),
        }
        for constraint in document["constraints"]
    ]
    values = dict(variable_values, ROLE_NAME=role_name)
    substituted = template_service.substitute_variables(constraints, values)
    return [template_service.build_constraint_data(constraint, role_name, {"tokens": ["SYSTEM_USER"]})
            for constraint in substituted]


def seeded_constraints(role_name):
    """The constraints the construct seeding ``role_name`` writes (add-on criteria excluded)."""
    file_name, array_name, constants = SEEDED_ROLE_CONSTRUCTS[role_name]
    source = (AUTH_DEFAULTS_DIR / file_name).read_text(encoding="utf-8")
    items = json.loads(_ts_array_to_json(source, array_name, constants))
    return [_stored_constraint(item) for item in items]


def _stored_constraint(item):
    """``convertConstraintToNewFormat``: attribute values -> the stored constraint fields."""
    def criteria(key):
        return [{"field": c["M"]["field"]["S"], "id": c["M"]["id"]["S"],
                 "operator": c["M"]["operator"]["S"], "value": c["M"]["value"]["S"]}
                for c in item.get(key, {}).get("L", [])]

    def permissions(key, principal):
        return [{principal: p["M"][principal]["S"], "id": p["M"]["id"]["S"],
                 "permission": p["M"]["permission"]["S"], "permissionType": p["M"]["permissionType"]["S"]}
                for p in item.get(key, {}).get("L", [])]

    return {
        "constraintId": item["constraintId"]["S"],
        "name": item["name"]["S"],
        "objectType": item["objectType"]["S"],
        "criteriaAnd": criteria("criteriaAnd"),
        "criteriaOr": criteria("criteriaOr"),
        "groupPermissions": permissions("groupPermissions", "groupId"),
        "userPermissions": permissions("userPermissions", "userId"),
    }


def _skip_bracketed(source, position):
    """Index just past the bracket that closes the one at ``position``. Comments and string and
    template literals are skipped, so a bracket inside one does not count."""
    depth = 0
    while True:
        char = source[position]
        if char in "\"'`":
            end = position + 1
            while source[end] != char:
                end += 2 if source[end] == "\\" else 1
            position = end + 1
            continue
        if source.startswith("//", position):
            position = source.index("\n", position)
            continue
        if source.startswith("/*", position):
            position = source.index("*/", position) + 2
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1


def _ts_array_to_json(source, array_name, constants):
    """JSON text of the array literal assigned to ``const <array_name>``."""
    marker = f"const {array_name} = ["
    position = source.index(marker) + len(marker) - 1
    out = []
    depth = 0
    length = len(source)
    while True:
        if position >= length:
            raise ValueError(f"{array_name}: unterminated array literal")
        char = source[position]
        if source.startswith("//", position):
            position = source.index("\n", position)
            continue
        if source.startswith("/*", position):
            position = source.index("*/", position) + 2
            continue
        if char in "[{":
            depth += 1
            out.append(char)
            position += 1
        elif char in "]}":
            while out and (out[-1].isspace() or out[-1] == ","):
                out.pop()
            out.append(char)
            depth -= 1
            position += 1
            if depth == 0:
                return "".join(out)
        elif char in ",:" or char.isspace():
            out.append(char)
            position += 1
        elif char == '"':
            end = position + 1
            while source[end] != '"':
                end += 2 if source[end] == "\\" else 1
            out.append(source[position:end + 1])
            position = end + 1
        elif char == "`":
            end = source.index("`", position + 1)
            text = source[position + 1:end]
            if "\\" in text:
                raise ValueError(f"{array_name}: escape in template literal {text!r}")
            out.append(json.dumps(_TEMPLATE_PLACEHOLDER.sub(lambda m: constants[m.group(1)], text)))
            position = end + 1
        elif source.startswith("...", position):
            # An add-on spread contributes elements only when the add-on is enabled: skip it and its
            # comma. Either a call, `...addonReadOnlyApiRouteCriteria(props.config, roleNameIDClean),`,
            # or an inline conditional, `...(props.config.app.addons.<name>.enabled ? [...] : []),`.
            position = _skip_bracketed(source, source.index("(", position))
            while source[position].isspace():
                position += 1
            if source[position] == ",":
                position += 1
        else:
            match = _IDENTIFIER.match(source, position)
            if match is None:
                raise ValueError(f"{array_name}: unexpected {source[position:position + 20]!r}")
            name = match.group(0)
            after = match.end()
            while source[after].isspace():
                after += 1
            if source[after] == ":":
                out.append(json.dumps(name))
            else:
                out.append(json.dumps(constants[name]))
            position = match.end()
