"""Every --json-output invocation emits ONLY parseable JSON, including on failures.

Click reports a bad or missing option by printing usage text and exiting before any command body
runs, so the flag was previously ignored on that path: a caller parsing stdout got
"Usage: vamscli ..." and a JSONDecodeError. main() now converts UsageError/ClickException to JSON
when the flag is present, which makes the contract total rather than best-effort.
"""

import json
import subprocess
import sys

import pytest

# Argument errors reachable without any deployment or credentials: each omits a required option, so
# Click rejects it during parsing.
USAGE_ERROR_CASES = [
    ["pipeline", "get"],
    ["pipeline", "template", "list"],
    ["pipeline", "tag-schema", "get", "-d", "GLOBAL", "-p", "some-pipeline"],
    ["workflow", "get"],
    ["workflow", "execute"],
    ["workflow", "trigger", "list"],
    ["execution", "details-metadata"],
    ["assets", "get"],
    ["database", "get"],
]


def _run(args):
    proc = subprocess.run([sys.executable, "-m", "vamscli.main", *args],
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.parametrize("args", USAGE_ERROR_CASES, ids=lambda a: " ".join(a))
def test_a_usage_error_is_json_when_json_output_is_requested(args):
    rc, combined = _run([*args, "--json-output"])
    assert rc != 0, "a missing required option must not exit 0"
    payload = json.loads(combined.strip())  # raises if anything non-JSON was emitted
    assert payload["error_type"] == "UsageError"
    assert payload["error"], "the message must name what was wrong"


@pytest.mark.parametrize("args", USAGE_ERROR_CASES[:3], ids=lambda a: " ".join(a))
def test_without_the_flag_the_human_readable_usage_text_is_kept(args):
    # The negative control: the JSON path must be conditional on the flag, not replace usage text.
    rc, combined = _run(args)
    assert rc != 0
    assert combined.lstrip().startswith("Usage:"), combined[:200]


def test_a_successful_command_still_exits_zero():
    # standalone_mode=False changes how Click returns, so pin that a normal run is unaffected.
    rc, combined = _run(["--version"])
    assert rc == 0, combined[:200]
