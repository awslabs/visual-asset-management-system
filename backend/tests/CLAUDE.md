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

### A mock must be at least as wide as the thing it replaces

`MockSafeLogger` in the root `conftest.py` stands in for the AWS Lambda Powertools `Logger` that
`safeLogger()` returns, so it implements **every** level the real object exposes — `debug`, `info`,
`warning`, `warn`, `error`, `critical`, `exception` — plus `append_keys` / `remove_keys` /
`set_correlation_id`, each accepting `*args, **kwargs`.

That breadth is not cosmetic. When the mock defined only `info`/`warning`/`error`/`exception` with a
single positional argument, two things broke:

-   **22 `logger.debug` and 3 `logger.warn` call sites raised `AttributeError`.** The failure was
    order-dependent and pointed away from its cause: `handlers/authz/__init__.py:111` logs at debug
    level only on the "reuse cached enforcer" branch, and the Casbin enforcer cache is module-level
    global state. Running `tests/handlers/authz/` alone left the cache cold and 199 tests passed; a
    full-suite run warmed it and **27 tests failed in two files nobody had touched**.
-   **`logger.info(msg, extra={...})` was rejected**, because Powertools is routinely called that way
    and the mock took exactly one positional argument.

So when you add a mock, mirror the real interface rather than the subset the first test happened to
need. A mock narrower than its subject converts ordinary code into a test failure at a distance from
its cause — see finding `S17-TEST-003`.

> The enforcer cache itself (`casbin_user_enforcer_map` / `casbin_user_policy_map`) is still global
> with no per-test reset, which is what made the above order-dependent. A fixture clearing it between
> tests would make the suite order-independent; that changes isolation semantics for every authz test,
> so it is a deliberate open item rather than an oversight.

### A `MagicMock` never ends a paging loop

`MagicMock.get(...)` returns a truthy child mock for **every** key, so a handler that pages on
`response.get('LastEvaluatedKey')` loops forever against an under-stubbed reader. `in`, by contrast,
is answered `False`, because `MagicMock.__contains__` defaults to false:

```python
m = MagicMock()
bool(m.get('LastEvaluatedKey'))     # True  -> the loop never terminates
'LastEvaluatedKey' in m             # False -> the loop ends
```

Two consequences, and both are load-bearing:

-   **Page on key PRESENCE, not on the value.** DynamoDB omits `LastEvaluatedKey` once the result set
    is exhausted, and its absence is the only end-of-set signal there is — a key that IS present does
    not promise more matching items (`Query`/`Scan` API reference: "If LastEvaluatedKey is not empty,
    it does not necessarily mean that there is more data in the result set"), because a
    `FilterExpression` is applied after the page has been read and can empty a page that still carries
    a key. So `if 'LastEvaluatedKey' not in response: break` is both the accurate contract and the form
    that stays finite under a stub. `handlers/authz/__init__.py` and
    `handlers/auth/apiKeyService.py` use it for exactly this reason.
-   **Stub every reader a paged handler can call, with a real dict.** A fixture that stubs `.scan` but
    not `.query` leaves the other path returning a bare mock. When a user-scoped listing moved from a
    scan to a GSI query, that fixture gap turned into a hang that ran the whole backend suite past
    600 s against a 167 s baseline — a timeout, not a failure, so it named no test.

#### An existence check fails the other way: an unread stub answers `True`

The same truthiness that hangs a paging loop makes an **existence** helper return a false positive on its
first read, and the two forms fail in opposite directions:

```python
m = MagicMock()
len(m.query().get('Items', []))     # 0     -> "no match"   (fails SAFE)
bool(m.query().get('Items'))        # True  -> "match found" (fails UNSAFE)
```

`MagicMock.__len__` defaults to `0` while `__bool__` defaults to `True`. So a check written as
`len(response.get('Items', [])) > 0` answers `False` against an under-stubbed reader, whereas
`common.dynamodb.query_has_match` — which stops at the first non-empty page via `if response.get('Items')`
— answers `True` after **one** read. A test asserting `assert flags['has_children'] is True` therefore
**passes having read nothing**, which is worse than an inconclusive failure: it retires the assertion.

This matters wherever a truncating existence check is converted to the shared helper, because the
conversion silently inverts what an unpatched table does to the test.

Two defences, and use both:

-   **Assert a read count or `Pager.assert_paged_to_exhaustion()` in-band on your own double.** That
    helper carries a read floor and raises `nothing read this pager`, which is what catches the
    unread-stub case rather than reporting success.
-   **Patch the object the function's `__globals__` actually resolves.** `patch.object(module, 'table')`
    is only effective when `function.__globals__ is module.__dict__`; load the same source file twice and
    the two module objects share a `__file__` while owning separate globals, so patching the second has
    no effect on a function defined in the first. Asserting that identity is a one-line harness check —
    see `tests/handlers/addon/garnetFramework/test_garnetIndexer_query_paging.py::TestTheStubsInThisFileAreActuallyRead`.

When a test must prove that a later page is reached, key the stub on `ExclusiveStartKey` rather than on
call order, and cap the number of reads it will serve. Keying on the cursor keeps the assertion at "the
cursor is threaded" instead of "exactly N reads happened", so an extra or repeated read does not break
the test; the cap turns a loop that never advances into a failure with a message instead of a hang.

#### Use the shared stubs in `tests/pagingStub.py`

Do not copy a private pager out of another test file — the shared helper is the one that carries these
guarantees, and a copy drifts from them silently. Import it as
`from backend.tests.pagingStub import BareMockReader, Pager, RoutedPager`.

| Helper                                     | What it is                                          | What it guarantees                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Pager(*pages, name=..., max_reads=None)`  | A `query`/`scan` side effect serving scripted pages | Pages are keyed on `ExclusiveStartKey`, never on call index, so an extra, retried, or reordered read still resolves to the right page. Rejects the one sequence DynamoDB cannot produce: a page a LATER page continues from with no `LastEvaluatedKey` (the key is omitted only when the result set is exhausted, i.e. when there is no next page — and pages are keyed on that cursor, so such a page leaves the next unreachable). The LAST scripted page MAY carry a key: a filtered page returns zero Items and still pages on, and a loop with a bound of its own stops with a cursor outstanding. Raises if a loop resumes from a cursor it was never handed, and separately if it continues past the last scripted page. |
| `Pager.assert_paged_to_exhaustion()`       | The completeness assertion                          | Every cursor a later page answers was resumed from, stated over the SET of cursors — so it cannot pin a read count or a read order. Fails if nothing read the pager, so a single-page script cannot make it vacuous.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `Pager.resumed_from`                       | Every `ExclusiveStartKey` actually sent             | Lets a test assert "no continuation was needed" without counting calls.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `RoutedPager(on="IndexName", **pagers)`    | Independent page sequences on one table stub        | Two GSIs paged off the same table each get their own sequence, routed by a read kwarg instead of by call order.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `BareMockReader(name=..., max_reads=None)` | A reader whose every page is a bare `MagicMock`     | The shape that HANGS a value-form loop. A presence-form loop reads once and stops; the value form reads to the cap and raises with an explanation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `PagingLoopDidNotTerminate`                | The failure both readers raise                      | Derives from `BaseException` deliberately, because several of these loops sit in best-effort helpers that catch `Exception` and degrade quietly — an `Exception` would be swallowed and the hang would read as an ordinary degraded result.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

`max_reads` defaults to 12, which is BELOW the page caps some loops carry of their own
(`MAX_ID_LOOKUP_PAGES`, `MAX_REFERENCING_WORKFLOW_PAGES`). A test that bounds the read count against
such a cap — "it stopped on the absent key, not by exhausting its pages" — must raise `max_reads`
above that cap, or the stub trips first and the bound can never fail: a vacuous assertion wearing a
bound's clothing.

The stubs' own contract — which sequences they accept, which they reject, and that each rejection can
actually fire — is covered by `tests/test_pagingStub.py`. Change a check in `pagingStub.py` and that
file is the one to run first: a stub that rejects legitimate behaviour makes correct code look broken,
which is how a real paging fix gets reverted.

The loop FORM is additionally guarded across the whole of `backend/backend` by
`tests/common/test_paging_key_presence_form.py`, which walks the tree rather than carrying a module
list. It fails on a new loop whose continuation decision reads the key's VALUE, and on a file that
assigns `ExclusiveStartKey` inside a loop without ever testing for the key's presence. Reading the
value to build a `NextToken` is not the defect and is not flagged; deciding whether to continue from
it is.

Two properties of that guard are load-bearing when you touch it:

-   **An exemption in `_KNOWN_REMAINING` names the FUNCTIONS it covers and how many sites it admits,
    never a bare file.** A file-wide exemption is a hole with no upper bound — a new value-form loop
    appended to the exempted module keeps the guard green while the same loop anywhere else turns it
    red, and the exempted module is the most paging-dense one in the backend. Both parts are upper
    bounds, so converting a site keeps the guard green; adding one anywhere in an exempt file does not.
-   **A read paged by the boto3 paginator is invisible to it by construction.** `paginator.paginate(…)`
    threads the cursor inside botocore, so there is no `LastEvaluatedKey` decision in the tree to
    inspect and neither the value-form check nor its structural converse says anything about such a
    read. Those need their own treatment — a `PaginationConfig` whose `MaxItems` is capped (Rule 15) —
    asserted per module beside that module's tests, as
    `tests/handlers/pipelines/test_pipelineService_paging.py` does for the two paginator-backed
    pipeline list reads.

### `xfail` is the ratchet for a test written before its fix

`xfail_strict = true` is set in `pytest.ini`, so a test marked `@pytest.mark.xfail(reason="FIX-### …")`
stays green while the defect exists and turns **red the moment the fix lands** — an XPASS is a failure
under strict mode, which forces whoever fixed it to delete the marker. That is how a test can be written
against a known defect without leaving the suite red, and without the marker rotting into a test that no
longer asserts anything.

The same setting is now configured for `tools/VamsCLI` (`pyproject.toml`) and the IsaacSim connector
(`tools/ExternalIntegrations/isaacsim_vams_integration/pytest.ini`). The Jest equivalent is
`it.failing`, which is strict by construction.

### `temporary` marks a test that pins one past change, not a durable rule

`@pytest.mark.temporary` (registered in `pytest.ini`) says the test exists to prove a specific change
landed — a deleted file, a removed test seam, a dead branch — rather than to hold a requirement in
place. Mark it when you write it and say what it pins:

```python
@pytest.mark.temporary  # pins the removal of the duplicated tag block in tagService.py
def test_the_duplicated_block_is_gone():
    ...
```

The marker exists because **a temporary test and a durable guardrail are indistinguishable by reading
them later.** Both scan source, both assert an absence, and both carry a docstring explaining why. At
release cleanup they are separated with `python -m pytest -m temporary -q --collect-only`, which only
works if the marker was applied up front.

Do **not** mark — and do not remove — a test whose forbidden construct is still writable, because that
guard can still fire for a good reason: a broader IAM wildcard, a `cdk-nag` suppression, a mutating
call from a read-only path, a validator regex that hardcodes a partition. Nor a positive/negative
**control** whose subject is deliberately a string that does not exist.

The shortcut "the forbidden literal appears nowhere in the source, so the test is spent" is **wrong** —
a forbid-forever guardrail also has zero occurrences, and that absence is the guard working. Full
criterion: root `CLAUDE.md` Rule 13.

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
