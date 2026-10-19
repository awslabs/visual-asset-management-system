# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The template import names the template it imported, in the response and in the audit record.

The shipped template files in ``documentation/permissionsTemplates/`` carry their header under
``metadata``. A caller posts such a file unmodified plus ``variableValues``; the response message
and the ``templateName`` audit field must then carry the file's own name rather than 'unknown'.
"""

import json
import pathlib
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

VARIABLE_VALUES = {
    'ROLE_NAME': 'my-role',
    'DATABASE_ID': 'my-db',
    'TAG_VALUE': 'locked',
    'TAG_NAME': 'restricted',
}


def _template_files():
    templates = (pathlib.Path(__file__).resolve().parents[4]
                 / 'documentation' / 'permissionsTemplates')
    return sorted(templates.glob('*.json'))


def _event(body):
    return {
        'requestContext': {'http': {'path': '/auth/constraintsTemplateImport', 'method': 'POST'}},
        'body': json.dumps(body),
    }


@pytest.fixture
def importer(monkeypatch):
    """Drive ``lambda_handler`` with storage, authorization and the audit writer stubbed."""
    monkeypatch.setattr(svc, 'constraints_table', MagicMock())
    monkeypatch.setattr(svc, 'roles_table', MagicMock())
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    monkeypatch.setattr(svc, 'CasbinEnforcer', enforcer)
    monkeypatch.setattr(svc, 'request_to_claims', lambda event: {'tokens': ['tester'], 'roles': []})
    audit = MagicMock()
    monkeypatch.setattr(svc, 'log_auth_changes', audit)
    return audit


def _audited_template_name(audit):
    assert audit.call_count == 1, audit.call_args_list
    _event_arg, event_type, details = audit.call_args.args
    assert event_type == 'constraintsTemplateImport'
    return details['templateName']


@pytest.mark.unit
class TestTheImportNamesTheTemplate:

    @pytest.mark.parametrize('path', _template_files(), ids=lambda path: path.name)
    def test_a_shipped_file_posted_unmodified_is_named(self, path, importer):
        body = json.loads(path.read_text(encoding='utf-8'))
        body['variableValues'] = VARIABLE_VALUES
        expected_name = body['metadata']['name']

        response = svc.lambda_handler(_event(body), None)

        assert response['statusCode'] == 200, response['body']
        message = json.loads(response['body'])['message']
        assert f"template '{expected_name}'" in message, message
        assert 'unknown' not in message, message
        assert _audited_template_name(importer) == expected_name

    def test_the_template_header_key_is_still_named(self, importer):
        body = {
            'template': {'name': 'Legacy Header'},
            'variableValues': {'ROLE_NAME': 'my-role'},
            'constraints': [{
                'name': 'n', 'description': 'd', 'objectType': 'database',
                'criteriaOr': [{'field': 'databaseId', 'operator': 'equals', 'value': 'my-db'}],
                'groupPermissions': [{'action': 'GET', 'type': 'allow'}],
            }],
        }

        response = svc.lambda_handler(_event(body), None)

        assert response['statusCode'] == 200, response['body']
        assert "template 'Legacy Header'" in json.loads(response['body'])['message']
        assert _audited_template_name(importer) == 'Legacy Header'

    def test_a_body_without_a_header_reports_unknown(self, importer):
        """POSITIVE CONTROL: 'unknown' is what a missing header produces, so the assertions above
        are reading the header rather than a constant."""
        body = {
            'variableValues': {'ROLE_NAME': 'my-role'},
            'constraints': [{
                'name': 'n', 'description': 'd', 'objectType': 'database',
                'criteriaOr': [{'field': 'databaseId', 'operator': 'equals', 'value': 'my-db'}],
                'groupPermissions': [{'action': 'GET', 'type': 'allow'}],
            }],
        }

        response = svc.lambda_handler(_event(body), None)

        assert response['statusCode'] == 200, response['body']
        assert "template 'unknown'" in json.loads(response['body'])['message']
        assert _audited_template_name(importer) == 'unknown'
