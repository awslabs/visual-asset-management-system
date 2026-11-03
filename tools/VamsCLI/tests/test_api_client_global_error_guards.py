"""Every APIClient method lets a global error through to the global handler.

A broad ``except Exception`` that wraps errors into a business error would otherwise turn a request
the deployment's web ACL blocked, or a permission denial, into a generic API error, losing its heading
and its request ID. The source checks read every class ``APIClient`` inherits methods from: the core
client and the custom mixin, each in its own file.
"""
import ast
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import click
import pytest

import vamscli.main as main_module
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    AuthenticationError,
    GlobalInfrastructureError,
    PermissionDeniedError,
    RequestBlockedError,
    TokenExpiredError,
)

API_CLIENT = Path(__file__).resolve().parents[1] / "vamscli" / "utils" / "api_client.py"


def _subclass_names(cls):
    return {cls.__name__}.union(*(_subclass_names(sub) for sub in cls.__subclasses__()))


# A handler naming a base of the global errors (or a bare ``except``) catches them with everything else.
BROAD_TYPES = {cls.__name__ for cls in GlobalInfrastructureError.__mro__[1:] if cls is not object}
GLOBAL_TYPES = _subclass_names(GlobalInfrastructureError)


def _names(handler_type):
    if handler_type is None:
        return set()
    nodes = handler_type.elts if isinstance(handler_type, ast.Tuple) else [handler_type]
    return {n.id if isinstance(n, ast.Name) else getattr(n, "attr", "") for n in nodes}


def _raises(handler):
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _is_bare_reraise(handler):
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise) and handler.body[0].exc is None


def _client_classes():
    """Every class APIClient inherits methods from, with the source file that defines it."""
    return [(cls, Path(inspect.getsourcefile(cls))) for cls in APIClient.__mro__ if cls is not object]


def _class_defs():
    for cls, source in _client_classes():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        yield cls.__name__, next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls.__name__)


def _handlers(class_defs=None):
    for owner, class_def in (_class_defs() if class_defs is None else class_defs):
        for method in (n for n in class_def.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(method):
                if not isinstance(node, ast.Try):
                    continue
                for index, handler in enumerate(node.handlers):
                    yield f"{owner}.{method.name}", node, index


def _wrapping_handlers(class_defs=None):
    for name, node, index in _handlers(class_defs):
        handler = node.handlers[index]
        if (handler.type is None or _names(handler.type) & BROAD_TYPES) and _raises(handler):
            yield name, node, index


def _global_handlers():
    for name, node, index in _handlers():
        if _names(node.handlers[index].type) & GLOBAL_TYPES:
            yield name, node.handlers[index]


def _missing_reraise(class_defs=None):
    missing = []
    for name, node, index in _wrapping_handlers(class_defs):
        earlier = node.handlers[:index]
        if not any("GlobalInfrastructureError" in _names(h.type) and _is_bare_reraise(h) for h in earlier):
            missing.append(f"{name}:{node.handlers[index].lineno}")
    return missing


def test_every_wrapping_broad_handler_is_preceded_by_a_global_error_reraise():
    assert _missing_reraise() == []


def test_a_handler_that_names_a_global_error_and_raises_only_re_raises_it():
    # Raising anything else from it would hand the global handler another error, or a business error.
    wrapping = [f"{name}:{handler.lineno}" for name, handler in _global_handlers()
                if _raises(handler) and not _is_bare_reraise(handler)]
    assert wrapping == []


def test_the_ratchet_sees_the_client():
    # Positive controls: a parse that matched nothing would pass the tests above vacuously.
    assert {"Exception", "BaseException", "VamsCLIError"} <= BROAD_TYPES
    assert {"RequestBlockedError", "PermissionDeniedError", "TokenExpiredError", "AuthenticationError"} < GLOBAL_TYPES
    assert sum(1 for _ in _wrapping_handlers()) > 150
    assert sum(1 for _ in _global_handlers()) > 150


def test_the_ratchet_reads_every_class_the_client_inherits_from():
    # A parse of api_client.py alone would leave the mixins' handlers out of the checks above.
    assert [name for name, _ in _class_defs()] == [cls.__name__ for cls in APIClient.__mro__ if cls is not object]
    sources = {source for _, source in _client_classes()}
    assert API_CLIENT.resolve() in {source.resolve() for source in sources}
    assert len(sources) > 1


def test_the_check_reports_a_broad_handler_without_the_re_raise():
    # Control: a broad handler that wraps every error is reported, and the same handler behind the
    # re-raise is not.
    def probe(guarded):
        lines = ["class Probe:", "    def call(self):", "        try:", "            pass"]
        if guarded:
            lines += ["        except GlobalInfrastructureError:", "            raise"]
        lines += ["        except Exception as e:", "            raise ValueError(e)"]
        return [("Probe", ast.parse("\n".join(lines)).body[0])]

    assert _missing_reraise(probe(guarded=False)) == ["Probe.call:5"]
    assert _missing_reraise(probe(guarded=True)) == []


# --- Behaviour: the error _make_request raised is the error the caller gets ---------------------

def _client() -> APIClient:
    # A stub profile manager keeps the developer's own configuration out of the test.
    return APIClient("https://api.example.com", profile_manager=Mock())


GLOBAL_ERRORS = [
    pytest.param(lambda: RequestBlockedError("Request blocked by the deployment's AWS WAF web ACL",
                                             request_id="r-1"), id="request-blocked"),
    pytest.param(lambda: PermissionDeniedError("Access forbidden. Not Authorized"), id="permission-denied"),
    pytest.param(lambda: TokenExpiredError("Authentication token has expired"), id="token-expired"),
    pytest.param(lambda: AuthenticationError("Authentication failed: no token"), id="authentication"),
]

# One method per shape: the pipeline helper, an asset write, a comment and a subscription.
METHOD_CALLS = [
    pytest.param(lambda c: c.list_pipelines(), id="list_pipelines"),
    pytest.param(lambda c: c.create_asset({"databaseId": "d-1", "assetName": "a"}), id="create_asset"),
    pytest.param(lambda c: c.add_comment("a-1", "v-1", "c-1", "text"), id="add_comment"),
    pytest.param(lambda c: c.create_subscription("Asset Version Change", "Asset", "a-1",
                                                 ["user@example.com"]), id="create_subscription"),
]


@pytest.mark.parametrize("make_error", GLOBAL_ERRORS)
@pytest.mark.parametrize("call", METHOD_CALLS)
def test_a_global_error_from_the_request_reaches_the_caller_unwrapped(monkeypatch, call, make_error):
    error = make_error()
    client = _client()
    monkeypatch.setattr(client, "_make_request", Mock(side_effect=error))

    with pytest.raises(type(error)) as excinfo:
        call(client)

    assert excinfo.value is error


@pytest.mark.parametrize("make_error", GLOBAL_ERRORS[:3])
def test_a_method_that_re_raised_one_global_error_lets_every_global_error_through(monkeypatch, make_error):
    """`import_constraints_template` already re-raised AuthenticationError beside its business errors."""
    error = make_error()
    client = _client()
    monkeypatch.setattr(client, "_make_request", Mock(side_effect=error))

    with pytest.raises(type(error)) as excinfo:
        client.import_constraints_template({"variableValues": {"ROLE_NAME": "r"}, "constraints": []})

    assert excinfo.value is error


def test_a_block_reaches_the_global_handler_from_a_command(monkeypatch, generic_command_mocks):
    """`pipeline list --json-output` reports the block under its own heading, type and request ID."""
    monkeypatch.setattr(sys, "argv", ["vamscli", "pipeline", "list", "--json-output"])
    blocked = RequestBlockedError("Request blocked by the deployment's AWS WAF web ACL", request_id="r-7")

    def _real_client(url, profile_manager):
        client = APIClient(url, profile_manager)
        monkeypatch.setattr(client, "_make_request", Mock(side_effect=blocked))
        return client

    with generic_command_mocks("pipeline") as mocks:
        mocks["patches"]["cmd_api"].side_effect = _real_client
        with pytest.raises(click.ClickException) as excinfo:
            main_module.main()

    document = json.loads(excinfo.value.message)
    assert excinfo.value.exit_code == 1
    assert document["error"] == f"Request Blocked: {blocked}"
    assert document["error_type"] == "RequestBlockedError"
    assert document["blockedBy"] == "waf"
    assert document["requestId"] == "r-7"
