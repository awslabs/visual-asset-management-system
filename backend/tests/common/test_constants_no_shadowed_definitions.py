# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Guards that common/constants.py defines each permission-constraint name exactly once.

A second top-level assignment of the same name wins at import, which turns the
documented copy into dead code: an edit to it reads as correct and has no runtime
effect on the ABAC field matrix or the Casbin policy model.
"""

import ast
import os
from collections import defaultdict

import pytest

from backend.backend.common.constants import (
    ALLOWED_CONSTRAINT_OBJECT_TYPES,
    ALLOWED_CONSTRAINT_OPERATORS,
    ALWAYS_ALLOWED_OBJECT_KEYS,
    CONSTRAINT_OBJECT_TYPE_FIELDS,
    CONSTRAINT_OPERATOR_LABELS,
    CONSTRAINT_PERMISSION_LABELS,
    CONSTRAINT_PERMISSION_TYPE_LABELS,
    PERMISSION_CONSTRAINT_FIELDS,
    PERMISSION_CONSTRAINT_POLICY,
    get_constraint_fields_for_object_type,
)

_COMMON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'backend', 'common',
)
_CONSTANTS_PATH = os.path.join(_COMMON_DIR, 'constants.py')


def _top_level_definitions(source):
    """Map every unconditional top-level name to the lines that define it.

    Only module-body assignments and def/class statements count; a name assigned
    inside a try/except or if block is a deliberate fallback, not a shadow.
    """
    definitions = defaultdict(list)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions[target.id].append(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name].append(node.lineno)
    return definitions


def _python_files(directory):
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if name.endswith('.py'):
                yield os.path.join(root, name)


@pytest.mark.unit
class TestNoShadowedTopLevelDefinitions:
    def test_constants_module_defines_each_name_once(self):
        with open(_CONSTANTS_PATH, encoding='utf-8') as handle:
            definitions = _top_level_definitions(handle.read())
        shadowed = {name: lines for name, lines in definitions.items() if len(lines) > 1}
        assert not shadowed, (
            "common/constants.py re-assigns top-level names, so the earlier copy is dead "
            f"code: {shadowed}"
        )

    def test_no_common_module_shadows_a_top_level_name(self):
        shadowed = {}
        for path in _python_files(_COMMON_DIR):
            with open(path, encoding='utf-8') as handle:
                definitions = _top_level_definitions(handle.read())
            duplicates = {name: lines for name, lines in definitions.items() if len(lines) > 1}
            if duplicates:
                shadowed[os.path.relpath(path, _COMMON_DIR)] = duplicates
        assert not shadowed, f"Shadowed top-level definitions in common/: {shadowed}"

    def test_documented_copy_is_the_definition_in_effect(self):
        with open(_CONSTANTS_PATH, encoding='utf-8') as handle:
            source = handle.read()
        definitions = _top_level_definitions(source)
        header_line = source.splitlines().index('# Permission / constraint constants') + 1
        # Every constraint symbol lives in the documented block, i.e. below its header
        # comment and above the unrelated role/file-security constants that follow it.
        boundary = definitions['ALLOWED_ROLE_SOURCES'][0]
        for name in (
            'PERMISSION_CONSTRAINT_FIELDS',
            'PERMISSION_CONSTRAINT_POLICY',
            'CONSTRAINT_OBJECT_TYPE_FIELDS',
            'CONSTRAINT_OPERATOR_LABELS',
            'CONSTRAINT_PERMISSION_LABELS',
            'CONSTRAINT_PERMISSION_TYPE_LABELS',
            'ALWAYS_ALLOWED_OBJECT_KEYS',
            'get_constraint_fields_for_object_type',
        ):
            for line in definitions[name]:
                assert header_line < line < boundary, (
                    f"{name} is defined at line {line}, outside the documented "
                    f"permission-constraint block ({header_line}-{boundary})"
                )


@pytest.mark.unit
class TestConstraintMatrixValuesUnchanged:
    """Positive control: the values every consumer reads are exactly as before."""

    def test_permission_constraint_fields(self):
        assert list(PERMISSION_CONSTRAINT_FIELDS) == [
            'databaseId',
            'assetName',
            'assetType',
            'tags',
            'tagName',
            'tagTypeName',
            'roleName',
            'userId',
            'pipelineId',
            'pipelineExecutionType',
            'workflowId',
            'category',
            'name',
            'metadataSchemaName',
            'metadataSchemaEntityType',
            'object__type',
            'route__path',
        ]

    def test_tags_is_the_only_list_valued_field(self):
        # models/roleConstraints derives its list-valued field set from these samples.
        list_valued = {
            name for name, sample in PERMISSION_CONSTRAINT_FIELDS.items()
            if isinstance(sample, list)
        }
        assert list_valued == {'tags'}

    def test_object_type_matrix(self):
        assert list(CONSTRAINT_OBJECT_TYPE_FIELDS) == list(ALLOWED_CONSTRAINT_OBJECT_TYPES)
        assert get_constraint_fields_for_object_type('asset') == [
            'databaseId', 'assetName', 'assetType', 'tags',
        ]
        assert get_constraint_fields_for_object_type('pipeline') == [
            'databaseId', 'pipelineId', 'pipelineExecutionType', 'category', 'name',
        ]
        assert get_constraint_fields_for_object_type('workflow') == [
            'databaseId', 'workflowId', 'category', 'name',
        ]
        assert get_constraint_fields_for_object_type('nope') == []

    def test_casbin_policy_model(self):
        for section in (
            '[request_definition]',
            '[policy_definition]',
            '[role_definition]',
            '[policy_effect]',
            '[matchers]',
        ):
            assert section in PERMISSION_CONSTRAINT_POLICY
        assert 'r = sub, obj, act' in PERMISSION_CONSTRAINT_POLICY
        assert 'p = sub, obj_rule, act, eft' in PERMISSION_CONSTRAINT_POLICY
        assert (
            'm = g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act'
            in PERMISSION_CONSTRAINT_POLICY
        )

    def test_operator_labels_match_allowed_operators(self):
        assert [o['value'] for o in CONSTRAINT_OPERATOR_LABELS] == list(
            ALLOWED_CONSTRAINT_OPERATORS
        )

    def test_names_defined_only_in_the_documented_block_still_resolve(self):
        # handlers/authz imports ALWAYS_ALLOWED_OBJECT_KEYS; authConstraintsService
        # imports both label lists. None of them existed in the shadowing copy, so
        # removing the wrong block would break those imports.
        assert ALWAYS_ALLOWED_OBJECT_KEYS == {'object__type', 'method'}
        assert [p['value'] for p in CONSTRAINT_PERMISSION_LABELS] == [
            'GET', 'PUT', 'POST', 'DELETE',
        ]
        assert [p['value'] for p in CONSTRAINT_PERMISSION_TYPE_LABELS] == ['allow', 'deny']
