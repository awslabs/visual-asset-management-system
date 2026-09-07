"""Every log call that receives a whole request or response payload routes through the redactor.

`tests/test_log_redaction.py::TestSinkIntegration` covers three sinks by name — `output_result`,
`log_api_request`, `log_api_response`. That is a per-site check, so it stays green while a fourth site
writes an unredacted payload, which is what happened: `@requires_setup_and_auth` (on essentially every
command) and `@handle_global_exceptions()` both did `result_str = str(result)` and wrote it at DEBUG.
The rotating log file is opened at DEBUG regardless of `--verbose`, so both lines always landed there,
and ~45 command callbacks return the API response body verbatim — `assets download
--shareable-links-only` returns a result built entirely out of presigned Amazon S3 URLs, each a bearer
credential in its query string.

Two kinds of assertion, and neither substitutes for the other:

* **Behavioural** — drive each wrapper and assert the credential is absent from what reached
  `log_debug`, with a positive control asserting a non-secret identifier from the same payload is
  present. Without the control a wrapper that logged nothing at all would pass.
* **Inventory** — an AST pass over the whole package that fails on *any* log call interpolating a
  variable assigned from a bare `str(<payload>)`. This is the durable half: it is what makes a
  sixth sink fail here rather than at an audit. It carries its own positive control, because a
  detector that matches nothing reports zero offenders exactly like a clean tree.
"""

import ast
from pathlib import Path

import pytest

from vamscli.utils.logging import REDACTED

VAMSCLI_ROOT = Path(__file__).resolve().parents[1] / "vamscli"

# A presigned URL's signature parameter and a VAMS API key: the two credential shapes the value-level
# backstop in `utils/logging.py` recognizes, so a redacted sink masks them even inside rendered text.
PRESIGNED_URL = (
    "https://bucket.s3.amazonaws.com/a/b.glb?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Signature=deadbeefsignaturevalue0123456789&X-Amz-Security-Token=tok-abcdefghijklmnop"
)
API_KEY = "vams_SUPERSECRETKEYVALUE0123456789"

PAYLOAD = {
    "assetId": "asset-visible-id",
    "downloadUrl": PRESIGNED_URL,
    "apiKey": API_KEY,
}


def _assert_payload_redacted(logged: str):
    """The credential is gone and the identifier survived — both halves, on one captured string."""
    assert "deadbeefsignaturevalue0123456789" not in logged
    assert "tok-abcdefghijklmnop" not in logged
    assert API_KEY not in logged
    # Positive control: a sink that logged nothing, or logged a placeholder, also has no credential.
    assert "asset-visible-id" in logged
    assert REDACTED in logged


class TestRequiresSetupAndAuthSink:
    """`@requires_setup_and_auth` logs the command's return value."""

    def test_command_result_is_redacted(self, monkeypatch):
        from vamscli.utils import decorators
        from vamscli.utils import logging as vlog

        captured = []
        monkeypatch.setattr(vlog, "log_debug", lambda msg: captured.append(msg))
        monkeypatch.setattr(vlog, "log_command_start", lambda *a, **k: None)
        monkeypatch.setattr(vlog, "log_command_end", lambda *a, **k: None)

        class _ProfileManager:
            profile_name = "unit"

            def has_config(self):
                return True

            def load_config(self):
                return {"api_gateway_url": "https://example.invalid/api"}

        monkeypatch.setattr(
            decorators, "get_profile_manager_from_context", lambda ctx=None: _ProfileManager()
        )

        @decorators.requires_setup_and_auth
        def some_command():
            return PAYLOAD

        assert some_command() is PAYLOAD
        _assert_payload_redacted(" ".join(captured))


class TestGlobalExceptionHandlerSink:
    """`@handle_global_exceptions()` logs the wrapped callable's return value."""

    def test_command_result_is_redacted(self, monkeypatch):
        from vamscli.utils import global_exceptions
        from vamscli.utils import logging as vlog

        captured = []
        monkeypatch.setattr(vlog, "log_debug", lambda msg: captured.append(msg))
        monkeypatch.setattr(vlog, "log_command_start", lambda *a, **k: None)
        monkeypatch.setattr(vlog, "log_command_end", lambda *a, **k: None)

        @global_exceptions.handle_global_exceptions()
        def some_command():
            return PAYLOAD

        assert some_command() is PAYLOAD
        _assert_payload_redacted(" ".join(captured))


# ---------------------------------------------------------------------------------------------
# Inventory pass
# ---------------------------------------------------------------------------------------------

# Names that hold a whole request or response payload rather than a derived value. `str(count)` or
# `str(duration)` is not a payload render and must not be flagged.
PAYLOAD_NAMES = frozenset({
    "result",
    "response",
    "response_data",
    "responseData",
    "body",
    "payload",
    "data",
})

# The log entry points in this package. `logger.debug(...)` / `logger.info(...)` reach the same
# rotating file handler as the module-level helpers.
LOG_FUNCTION_NAMES = frozenset({"log_debug", "log_info", "log_warning", "log_error"})
LOG_METHOD_NAMES = frozenset({"debug", "info", "warning", "error", "exception"})

REDACTORS = frozenset({"redact_to_text", "redact_sensitive", "scrub_text", "redact_mapping_for_log"})


def _rendered_payload_vars(tree: ast.AST) -> dict:
    """Map variable name -> line, for each `<var> = str(<payload>)` assignment in the tree."""
    rendered = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        if not (isinstance(value.func, ast.Name) and value.func.id == "str"):
            continue
        if len(value.args) != 1 or not isinstance(value.args[0], ast.Name):
            continue
        if value.args[0].id in PAYLOAD_NAMES:
            rendered[target.id] = node.lineno
    return rendered


def _redacted_vars(tree: ast.AST) -> set:
    """Names assigned from a redactor call — these are safe to interpolate into a log line."""
    safe = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in REDACTORS:
            safe.add(target.id)
    return safe


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in LOG_FUNCTION_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in LOG_METHOD_NAMES
    return False


def _names_interpolated(call: ast.Call) -> set:
    """Every bare name read anywhere inside a log call's arguments."""
    names = set()
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for node in ast.walk(arg):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def _scan_source(source: str, label: str) -> list:
    """Return `(label, line, var)` for each log call interpolating an unredacted payload render."""
    tree = ast.parse(source)
    rendered = _rendered_payload_vars(tree)
    if not rendered:
        return []
    safe = _redacted_vars(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_log_call(node):
            continue
        for name in _names_interpolated(node) & set(rendered):
            if name not in safe:
                offenders.append((label, node.lineno, name))
    return offenders


UNREDACTED_SNIPPET = """
def handler(result):
    result_str = str(result)
    log_debug(f"returned result: {result_str}")
"""

REDACTED_SNIPPET = """
def handler(result):
    result_str = redact_to_text(result)
    log_debug(f"returned result: {result_str}")
"""


class TestPayloadSinkInventory:
    def test_detector_flags_an_unredacted_sink(self):
        """Positive control: the shape this pass forbids must actually be detected."""
        assert _scan_source(UNREDACTED_SNIPPET, "<snippet>") == [("<snippet>", 4, "result_str")]

    def test_detector_accepts_a_redacted_sink(self):
        """Negative control: routing through the redactor must clear the finding."""
        assert _scan_source(REDACTED_SNIPPET, "<snippet>") == []

    def test_the_corpus_is_not_empty(self):
        """Guards against a path that stopped matching any file, which reports zero offenders."""
        files = list(VAMSCLI_ROOT.rglob("*.py"))
        assert len(files) > 20, f"expected the vamscli package under {VAMSCLI_ROOT}"

    def test_no_log_call_interpolates_an_unredacted_payload(self):
        offenders = []
        for path in sorted(VAMSCLI_ROOT.rglob("*.py")):
            offenders.extend(
                _scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(VAMSCLI_ROOT)))
            )
        assert offenders == [], (
            "these log calls write a whole payload to the rotating log file without redacting it; "
            "route the value through redact_to_text() (tools/VamsCLI/CLAUDE.md Rule 10): "
            + "; ".join(f"{f}:{line} ({var})" for f, line, var in offenders)
        )


class TestKnownSinksStayWired:
    """The five payload sinks are named so a removal of the redactor call fails here too.

    Not a substitute for the inventory pass above: this catches a redactor call being deleted from a
    known site, while the inventory catches a NEW site being added without one.
    """

    @pytest.mark.parametrize(
        "relative_path,sink",
        [
            ("utils/json_output.py", "output_result"),
            ("utils/logging.py", "log_api_request"),
            ("utils/logging.py", "log_api_response"),
            ("utils/decorators.py", "requires_setup_and_auth"),
            ("utils/global_exceptions.py", "handle_global_exceptions"),
        ],
    )
    def test_sink_calls_the_redactor(self, relative_path, sink):
        source = (VAMSCLI_ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == sink:
                target = node
                break
        assert target is not None, f"{sink} not found in {relative_path}"
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
        }
        assert calls & REDACTORS, f"{relative_path}:{sink} does not call a redactor"
