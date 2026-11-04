"""The `--json-output` document `output_error` prints for an exception, and the `json_fields` it carries.

The document is `{"error", "error_type"}` plus the exception's `json_fields`, which is merged only when
it is a dict, whether the class or the instance sets it, and never replaces those two keys. Any other
value is left out, so the original error is still reported; the global handler and the MCP server's
`tool_result` read `json_fields` the same way.
"""

import json
import types

import pytest

from vamscli.utils.exceptions import BusinessLogicError, RequestBlockedError
from vamscli.utils.json_output import output_error

MESSAGE = "Operation already in progress (409)."


class _ClassFields(BusinessLogicError):
    """An exception whose class declares its `json_fields`."""
    json_fields = {"inProgress": True}


class _ClearedFields(_ClassFields):
    """A subclass that clears the `json_fields` it inherits."""
    json_fields = None


class _OwnFields(Exception):
    """An exception that declares no `json_fields`; a test sets them on the instance."""


def _document(capsys, error):
    """The document `output_error` prints for `error` in JSON mode, which then exits 1."""
    with pytest.raises(SystemExit) as excinfo:
        output_error(error, True)
    assert excinfo.value.code == 1
    return json.loads(capsys.readouterr().out)


class TestOutputErrorJsonFields:

    def test_an_exception_without_json_fields_gives_error_and_error_type(self, capsys):
        assert _document(capsys, _OwnFields(MESSAGE)) == {"error": MESSAGE, "error_type": "_OwnFields"}

    def test_json_fields_of_none_is_left_out(self, capsys):
        assert _document(capsys, _ClearedFields(MESSAGE)) == {
            "error": MESSAGE,
            "error_type": "_ClearedFields",
        }

    @pytest.mark.parametrize("fields", [["x"], "x", types.MappingProxyType({"x": True})],
                             ids=["list", "str", "mapping-that-is-not-a-dict"])
    def test_json_fields_that_is_not_a_dict_is_left_out(self, capsys, fields):
        error = _OwnFields(MESSAGE)
        error.json_fields = fields

        assert _document(capsys, error) == {"error": MESSAGE, "error_type": "_OwnFields"}

    def test_a_dict_on_the_class_is_merged_beside_the_error(self, capsys):
        assert _document(capsys, _ClassFields(MESSAGE)) == {
            "error": MESSAGE,
            "error_type": "_ClassFields",
            "inProgress": True,
        }

    def test_a_dict_on_the_instance_is_merged_beside_the_error(self, capsys):
        assert _document(capsys, RequestBlockedError(MESSAGE, request_id="r-1")) == {
            "error": MESSAGE,
            "error_type": "RequestBlockedError",
            "blockedBy": "waf",
            "requestId": "r-1",
        }

    def test_a_dict_key_never_replaces_error_or_error_type(self, capsys):
        error = _OwnFields(MESSAGE)
        error.json_fields = {"error": "replaced", "error_type": "Replaced", "extra": 1}

        assert _document(capsys, error) == {"error": MESSAGE, "error_type": "_OwnFields", "extra": 1}
