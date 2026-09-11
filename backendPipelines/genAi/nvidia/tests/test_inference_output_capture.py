#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""S32-LIVE-026: a failed GPU child's output must reach the EXECUTION RECORD, not only CloudWatch.

Five NVIDIA container entry points ran their inference/training child through
``subprocess.run(check=True)`` with no capture. The child inherits stdout, so its output did reach
CloudWatch -- nothing was lost from the logs. What was lost is the parent's copy: the ``RuntimeError``
raised on failure could only report ``exit code 1``, and that exception's text is what the workflow
stores as the execution's error. An operator reading the execution record in VAMS was told an exit code
for a cause the child had already printed in full, and finding it meant knowing to go to CloudWatch and
which log stream to open.

Each container now runs the child through a ``_run_streaming`` helper that forwards every line onward
while keeping a bounded tail, and raises with that tail in the message.

**The mechanism the old comments gave for not capturing was wrong, and this file proves it.** They said
``capture_output=True`` fills the 64 KB pipe buffer and deadlocks the child against a parent sitting in
``wait()``. It does not: ``run()`` drains through ``communicate()``, which reads both pipes concurrently.
``test_capture_output_does_not_deadlock`` measures that directly, and
``test_popen_without_a_reader_is_what_deadlocks`` measures the pattern that genuinely does hang -- which
is why the helper reads before it waits. The real reason to stream rather than capture is that
``run(capture_output=True)`` yields nothing until the child exits, so a multi-hour job would log nothing
while running and a hang would be undiagnosable. Recording the correct reason matters because the wrong
one is what justified capturing nothing across five containers.

The helper is duplicated per container because each is a separate Docker build context whose Dockerfile
COPYs an explicit file list, so no shared module is importable at container runtime.
``test_all_four_helpers_are_identical`` compares them structurally: two copies of the same routine
drifting silently is the cost of that duplication.

Four of those five entry points are covered here — the ones a deployment can reach. The fifth,
``cosmos/predict/containerv1/``, is retained as a reference implementation with no configuration key
that deploys it (``NOTICE.md`` and ``docs/additional/notices.md`` record it that way), and carries an
unfixed first-hit output-artifact selection. It is excluded from ``CONTAINERS`` deliberately, and
``test_the_reference_container_is_excluded_and_stays_undeployable`` is what keeps that pair of facts —
excluded from coverage, unreachable from CDK — from drifting apart.

Every behavioural check runs the helper in a CHILD PYTHON PROCESS under a timeout. A deadlock is the
failure being guarded against, and asserting on it in-process would hang the suite instead of failing it.
"""

import ast
import io
import json
import os
import subprocess
import sys
import textwrap

import pytest

REPO_NVIDIA = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The four deployable container entry points whose inference/training child was uncaptured.
CONTAINERS = {
    "cosmos3": "cosmos/3/container/inference.py",
    "predict-v2.5": "cosmos/predict/containerv2.5/inference.py",
    "transfer": "cosmos/transfer/container/inference.py",
    "gr00t": "gr00t/container/inference.py",
}

# Retained as a reference implementation, deliberately outside CONTAINERS.
REFERENCE_CONTAINER = "cosmos/predict/containerv1"

# Comfortably past a 64 KB pipe buffer, and past the 1.2 MB already measured by hand.
CHILD_LINES = 6000
CHILD_LINE = "x" * 200
# A distinctive last line, so "the tail was captured" is asserted on content rather than on length.
CHILD_FINAL_LINE = "RuntimeError: deliberate failure the operator needs to see"
# Generous: the helper returns in well under a second, and only a genuine hang exceeds this.
CHILD_TIMEOUT_SECONDS = 90


def _read(rel):
    with io.open(os.path.join(REPO_NVIDIA, rel), encoding="utf-8") as fh:
        return fh.read()


def _helper_node(source, rel):
    """The `_run_streaming` FunctionDef, or fail naming the file."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_run_streaming":
            return node
    pytest.fail("no module-level _run_streaming in %s" % rel)


def _helper_source(rel):
    source = _read(rel)
    node = _helper_node(source, rel)
    return ast.get_source_segment(source, node)


def _helper_constants(rel):
    """The module-level `_TAIL_*` assignments the helper's default arguments reference.

    Extracted rather than hardcoded here: the helper's signature defaults to `_TAIL_LINES`, so execing
    the function alone raises NameError, and pasting the values into the driver would make the driver
    the source of truth for a bound the containers own.
    """
    source = _read(rel)
    lines = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if getattr(t, "id", "").startswith("_TAIL_"):
                lines.append(ast.get_source_segment(source, node))
    assert lines, "%s: no _TAIL_* constants found" % rel
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------
# Controls. Every assertion below is satisfied by a file that was never found or never read.
# ------------------------------------------------------------------------------------------------


def test_the_covered_set_is_exactly_the_four_deployable_entry_points():
    """Asserted by name, because every other check in this file loops over `CONTAINERS`.

    Each parametrized test takes its ids from this dict and each loop-based control iterates it, so a
    dict that had been emptied or narrowed would leave the whole file reporting green while asserting
    nothing. The set is what fails loudly instead — including if an entry is silently re-added.
    """
    assert set(CONTAINERS) == {"cosmos3", "predict-v2.5", "transfer", "gr00t"}


def test_all_four_container_files_exist_and_were_read():
    for name, rel in CONTAINERS.items():
        path = os.path.join(REPO_NVIDIA, rel)
        assert os.path.isfile(path), "%s: %s not found" % (name, path)
        assert len(_read(rel)) > 1000, "%s: read suspiciously little" % name


def test_the_reference_container_is_excluded_and_stays_undeployable():
    """The reference container is kept, uncovered and unreachable — all three at once.

    It still carries the first-hit output-artifact selection its deployable sibling had fixed
    (`find_output_video`, `glob("*.mp4")`, "Path to first .mp4 file found"), which is safe only while
    nothing deploys it. Its one CDK reference is commented out; uncommenting that line would ship a
    container that publishes an arbitrary artifact under the real output's name and exits 0.
    """
    reference_dir = os.path.join(REPO_NVIDIA, REFERENCE_CONTAINER)
    assert os.path.isdir(reference_dir), "%s was removed" % reference_dir
    covering = [n for n, rel in CONTAINERS.items() if rel.startswith(REFERENCE_CONTAINER + "/")]
    assert covering == [], "%s covers the reference container" % covering

    infra_lib = os.path.join(
        os.path.abspath(os.path.join(REPO_NVIDIA, "..", "..", "..")), "infra", "lib")
    scanned = 0
    live_references = []
    for root, _dirs, files in os.walk(infra_lib):
        for fname in files:
            if not fname.endswith(".ts"):
                continue
            scanned += 1
            path = os.path.join(root, fname)
            with io.open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if "containerv1" in line and not line.strip().startswith("//"):
                        live_references.append("%s:%d" % (path, lineno))
    # Positive control on the walk: a mistyped path would otherwise scan nothing and pass.
    assert scanned > 50, "scanned only %d .ts files under %s" % (scanned, infra_lib)
    assert live_references == [], (
        "the reference container is reachable from CDK at %s — port the output-artifact fix before "
        "enabling it" % live_references
    )


def test_every_container_defines_the_helper_at_module_level():
    # A helper nested inside a function would not be reachable from the call site, and `ast.parse`
    # walking only `tree.body` is what makes this a real check rather than a substring search.
    for name, rel in CONTAINERS.items():
        assert _helper_source(rel), "%s has no module-level _run_streaming" % name


# ------------------------------------------------------------------------------------------------
# The defect: the raised message must carry the child's output.
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_the_raised_error_includes_the_captured_tail(name):
    """The failure path must interpolate the tail, not just the exit code.

    Asserted on the AST of the raise rather than on the file text: a mention of `output_tail` anywhere
    in the file (a comment, the helper's own docstring) would satisfy a substring search while the
    raised message still said only "exit code 1".
    """
    source = _read(CONTAINERS[name])
    tree = ast.parse(source)
    raises_with_tail = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        if not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        if getattr(func, "id", None) != "RuntimeError":
            continue
        rendered = ast.dump(node.exc)
        if "output_tail" in rendered and "returncode" in rendered:
            raises_with_tail.append(node.lineno)
    assert raises_with_tail, (
        "%s: no `raise RuntimeError(...)` interpolates both the returncode and output_tail" % name
    )


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_the_inference_call_no_longer_uses_check_true(name):
    """The child must go through the helper, so the parent holds a copy.

    Scoped to the function that calls `_run_streaming` — the same files also run a short `ffmpeg`
    through `run(check=True, capture_output=True)` for preview generation, which is correct there: the
    output is small and its `stderr` is already in the error message.
    """
    source = _read(CONTAINERS[name])
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("run_inference", "run_training"):
            target = node
            break
    assert target is not None, "%s: no run_inference/run_training" % name

    calls = [n for n in ast.walk(target) if isinstance(n, ast.Call)]
    streamed = [
        c for c in calls if getattr(c.func, "id", None) == "_run_streaming"
    ]
    assert streamed, "%s: %s does not call _run_streaming" % (name, target.name)

    checked = []
    for c in calls:
        attr = getattr(c.func, "attr", None)
        if attr != "run":
            continue
        for kw in c.keywords:
            if kw.arg == "check" and getattr(kw.value, "value", None) is True:
                checked.append(c.lineno)
    assert checked == [], (
        "%s: %s still runs a child with check=True at line(s) %s"
        % (name, target.name, checked)
    )


# ------------------------------------------------------------------------------------------------
# Behaviour, measured in a child process so a hang fails rather than stalls the suite.
# ------------------------------------------------------------------------------------------------


def _exercise_helper(rel, exit_code):
    """Exec this container's helper in a child process against a large-output child. Returns the JSON."""
    helper = _helper_constants(rel) + "\n\n" + _helper_source(rel)
    driver = textwrap.dedent(
        """
        import collections, json, subprocess, sys, io, os

        {helper}

        child = (
            "import sys\\n"
            "for i in range({lines}): print({line!r})\\n"
            "print({final!r})\\n"
            "sys.exit({code})\\n"
        )
        # The helper prints every forwarded line to stdout, which is the behaviour under test (the log
        # must still receive the child's output). Redirected so this driver's own stdout carries only
        # the JSON result.
        real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        rc, tail = _run_streaming([sys.executable, "-c", child])
        forwarded = sys.stdout.getvalue()
        sys.stdout = real_stdout
        json.dump({{
            "returncode": rc,
            "tail": tail,
            "tail_lines": len(tail.splitlines()),
            "forwarded_bytes": len(forwarded),
        }}, real_stdout)
        """
    ).format(
        helper=helper,
        lines=CHILD_LINES,
        line=CHILD_LINE,
        final=CHILD_FINAL_LINE,
        code=exit_code,
    )
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )
    assert proc.returncode == 0, "driver failed: %s" % proc.stderr[-2000:]
    return json.loads(proc.stdout)


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_helper_captures_the_tail_of_a_failing_child_without_hanging(name):
    """The defect, end to end: a child that outdraws the pipe buffer and exits non-zero.

    `subprocess.run(timeout=...)` in `_exercise_helper` is the no-hang assertion — a helper that
    deadlocked would raise TimeoutExpired here rather than returning a wrong value.
    """
    result = _exercise_helper(CONTAINERS[name], exit_code=1)
    assert result["returncode"] == 1
    # The last thing the child said is the thing an operator needs, and it is the line most at risk
    # from a bounded tail that dropped the wrong end.
    assert CHILD_FINAL_LINE in result["tail"], (
        "%s: the final line of the child's output is not in the tail" % name
    )
    # Bounded, not unbounded: 6000 lines went in.
    assert result["tail_lines"] <= 80, "%s: tail is not bounded (%d lines)" % (
        name, result["tail_lines"]
    )
    # And the live log still received everything — the point was to ADD a copy, not reroute the stream.
    assert result["forwarded_bytes"] > CHILD_LINES * len(CHILD_LINE), (
        "%s: the child's output was not forwarded onward (%d bytes)"
        % (name, result["forwarded_bytes"])
    )


@pytest.mark.parametrize("name", sorted(CONTAINERS))
def test_helper_reports_success_for_a_child_that_exits_zero(name):
    # The positive control for the test above: a helper that always reported failure would satisfy it.
    result = _exercise_helper(CONTAINERS[name], exit_code=0)
    assert result["returncode"] == 0


# ------------------------------------------------------------------------------------------------
# The claim the old comments made, measured.
# ------------------------------------------------------------------------------------------------


def test_capture_output_does_not_deadlock():
    """`subprocess.run(capture_output=True)` drains concurrently, so the 64 KB claim was false.

    Kept as a test rather than a comment because it is the premise the five containers' original
    "don't capture" decision rested on, and a premise that is only asserted in prose gets repeated.
    """
    child = (
        "import sys\n"
        "for i in range(%d): print(%r)\n"
        "sys.exit(1)\n" % (CHILD_LINES, CHILD_LINE)
    )
    result = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )
    assert result.returncode == 1
    assert len(result.stdout) > CHILD_LINES * len(CHILD_LINE)


def test_popen_without_a_reader_is_what_deadlocks():
    """The pattern that genuinely hangs, which is why the helper reads before it waits.

    This is the counterpart control: without it, the test above reads as "pipes never block", and the
    helper's read-then-wait ordering would look like an arbitrary style choice rather than the thing
    that keeps it safe.
    """
    child = (
        "import sys\n"
        "for i in range(%d): print(%r)\n"
        "sys.exit(1)\n" % (CHILD_LINES, CHILD_LINE)
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
    finally:
        proc.kill()
        if proc.stdout:
            proc.stdout.close()
        proc.wait()


# ------------------------------------------------------------------------------------------------
# No drift between the four covered copies.
# ------------------------------------------------------------------------------------------------


def test_all_four_helpers_are_identical():
    """Compared structurally, so formatting and docstrings do not mask a behavioural difference.

    Docstrings are excluded deliberately: the transfer container's carries an extra paragraph about
    torchrun's per-worker logs, which is accurate for that container. The executable body must match.
    """
    shapes = {}
    for name, rel in CONTAINERS.items():
        source = _read(rel)
        node = _helper_node(source, rel)
        body = list(node.body)
        # Drop the docstring statement, which is allowed to differ.
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        shapes[name] = "\n".join(ast.dump(stmt) for stmt in body)

    reference_name = sorted(shapes)[0]
    reference = shapes[reference_name]
    differing = [n for n, s in shapes.items() if s != reference]
    assert differing == [], (
        "the duplicated helper has drifted in %s (compared against %s)"
        % (differing, reference_name)
    )


def test_the_tail_bounds_are_the_same_everywhere():
    # The bound is what keeps a multi-hour job's memory flat; a container that quietly raised it would
    # pass the structural comparison above only if the constant were inlined, so it is checked by value.
    seen = {}
    for name, rel in CONTAINERS.items():
        source = _read(rel)
        tree = ast.parse(source)
        values = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if getattr(t, "id", None) in ("_TAIL_LINES", "_TAIL_LINE_CHARS"):
                    values[t.id] = node.value.value
        seen[name] = values
    assert all(v == {"_TAIL_LINES": 80, "_TAIL_LINE_CHARS": 2000} for v in seen.values()), seen
