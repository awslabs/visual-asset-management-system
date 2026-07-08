# CLAUDE.md -- VAMS Backend Test Suite

> Auto-loaded when Claude Code works within `backend/tests/`. Covers pytest configuration, mock module hierarchy, per-handler conftest patterns, and event-shape conventions for backend unit tests. For handler patterns, see `backend/CLAUDE.md`.

---

## Running Tests

```bash
# From backend/ directory
cd backend
pip install -r requirements-dev.txt
pytest                            # Run all tests
pytest -m unit                    # Unit tests only
pytest -m integration             # Integration tests only
pytest -m "not slow"              # Skip slow tests
pytest tests/test_specific.py     # Single file
pytest -v --strict-markers        # Verbose with strict markers
```

---

## Test Configuration (`pytest.ini`)

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
xfail_strict = true
addopts = -v --strict-markers
markers =
    unit: marks tests as unit tests
    integration: marks tests as integration tests
    slow: marks tests as slow (skipped by default)
    aws: marks tests that interact with AWS services
```

---

## Root `conftest.py`

The root `backend/conftest.py` performs critical setup before every test:

1.  **`sys.path` manipulation** -- adds `backend/`, `backend/backend/`, and `tests/mocks/` to Python path so handlers can import their normal module paths.
2.  **Environment variables** -- seeds test values for all required env vars (table names, bucket names, `VAMS_RESOURCE_PARAM_PREFIX`, etc.). These act as break-glass overrides so `get_table_name(ResourceKeys.*)` returns the test names without an SSM call.
3.  **Mock imports** -- replaces real modules in `sys.modules` with mocks from `tests/mocks/` (see below).
4.  **Autouse fixture `setup_mock_imports()`** -- runs before every test to re-establish the mock module hierarchy.

## Mocks Directory

`tests/mocks/` contains stand-in modules that are loaded in place of the real
implementations. Layout mirrors the handler package tree:

```
tests/mocks/
├── common/             # Mock common utilities (validators, dynamodb helpers, etc.)
├── customLogging/      # Mock safeLogger and audit logging
├── customConfigCommon/ # Mock config helpers
└── handlers/           # Mock cross-handler dependencies
```

When adding a new dependency that a handler imports at module load time, add a matching mock module here if the real one requires AWS or Casbin bootstrapping.

---

## Import Pattern in Tests

Tests import from the `backend.backend.*` path (the root `conftest.py` puts both `backend/` and `backend/backend/` on `sys.path`, so both forms resolve to the same modules — pick the explicit `backend.backend.*` form for clarity):

```python
from backend.backend.handlers.assets.assetService import lambda_handler
from backend.backend.models.assetsV3 import CreateAssetRequestModel
```

---

## Writing New Tests

```python
import pytest
from unittest.mock import MagicMock, patch
import json

@pytest.mark.unit
class TestYourHandler:
    """Tests for your handler"""

    def test_get_item_success(self):
        event = {
            'requestContext': {
                'http': {
                    'method': 'GET',
                    'path': '/items/test-item-id'
                }
            },
            'queryStringParameters': {},
            'headers': {
                'authorization': 'Bearer test-token'
            }
        }
        context = MagicMock()

        with patch('backend.backend.handlers.your.handler.your_table') as mock_table:
            mock_table.get_item.return_value = {
                'Item': {'itemId': 'test-item-id', 'name': 'Test'}
            }
            response = lambda_handler(event, context)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['itemId'] == 'test-item-id'
```

### Event-Shape Coverage

Backend handlers run behind API Gateway REST API (v1), which shapes events differently from HTTP API v2 (see `backend/CLAUDE.md` Rule 16). Unit tests that hand-build a v2-shaped event will pass while the deployed handler crashes on the real REST-shaped event. When a handler reads `requestContext['http']`, `pathParameters`, or `queryStringParameters`, cover the REST-shaped event in tests — including `pathParameters: None` and `queryStringParameters: None` for the "no params" case.

---

## Per-Handler `conftest.py`

Create handler-specific `conftest.py` files for environment variables and mocks unique to that handler:

```python
# tests/your_domain/conftest.py
import os

os.environ['YOUR_SPECIFIC_TABLE'] = 'test-table'
os.environ['YOUR_SPECIFIC_BUCKET'] = 'test-bucket'
```

Handler-specific mocks (custom `patch` targets, fixtures for common event shapes) belong here as well, so each domain's tests remain self-contained.
