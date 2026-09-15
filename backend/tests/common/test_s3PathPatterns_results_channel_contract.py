# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The s3PathPatterns comment entries must agree with the code that uses the constants.

``s3PathPatterns.py`` is the file a pipeline author reads first, and a constant's comment entry is
the only in-repo description of whether that output channel is wired. Two invariants keep the
entries honest:

- an entry that says workflow generation does not use the constant yet may not be contradicted by a
  reference in the ASL generator, the interim step, or the end-state lambda;
- the results channel's entry names the wire keys the prefix travels as
  (``outputResultsPrefixRelative`` for the next pipeline's manifest, ``resultsPathKey`` for the
  process-output step) and states that the end-state lambda reads it on both the normal and the
  results-only terminal path -- so both are asserted against the source rather than trusted.

The assertions read source text because a comment has no runtime behaviour to exercise. Each is
paired with a control showing the scan discriminates, since a scan that matches nothing and a scan
that matches everything both look green.
"""

import ast
import importlib
import os
import re

import pytest

# Fetched through importlib rather than ``import common.s3PathPatterns as ...``: the root conftest
# registers the real module under that name but leaves the ``common`` package itself a MagicMock, and
# the ``as`` form resolves the attribute off the parent, yielding a mock that answers every hasattr.
s3PathPatterns = importlib.import_module("common.s3PathPatterns")

_BACKEND_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
_PATH_PATTERNS_SOURCE = os.path.join(_BACKEND_ROOT, "common", "s3PathPatterns.py")
_ASL_BUILDER_SOURCE = os.path.join(_BACKEND_ROOT, "common", "workflows", "workflowAslBuilder.py")
_INTERIM_SOURCE = os.path.join(
    _BACKEND_ROOT, "handlers", "workflows", "sfn", "interimPipelineTracking.py")
_END_STATE_SOURCE = os.path.join(
    _BACKEND_ROOT, "handlers", "workflows", "sfn", "processWorkflowExecutionOutput.py")

# Phrasings the file uses to mark a constant as defined ahead of the code that would use it.
_UNWIRED_MARKERS = ("not yet used", "future feature", "defined ahead of")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _comment_entries():
    """Map constant name -> its comment entry text.

    An entry starts at ``# NAME: ...`` and continues on the indented ``#   ...`` lines below it,
    which is the file's own comment convention.
    """
    entries = {}
    current = None
    for line in _read(_PATH_PATTERNS_SOURCE).splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            current = None
            continue
        text = stripped.lstrip("#").strip()
        match = re.match(r"^([A-Z][A-Z0-9_]+):\s*(.*)$", text)
        if match:
            current = match.group(1)
            entries[current] = match.group(2)
        elif current:
            entries[current] = (entries[current] + " " + text).strip()
    return entries


def _documented_as_unwired():
    return {name for name, body in _comment_entries().items()
            if any(marker in body.lower() for marker in _UNWIRED_MARKERS)}


def _references(source, name):
    return re.search(r"\b" + re.escape(name) + r"\b", source) is not None


def _functions_referencing(source_path, key):
    """Names of the top-level functions whose body mentions ``key``."""
    source = _read(source_path)
    tree = ast.parse(source)
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node) or ""
            if key in segment:
                found.add(node.name)
    return found


@pytest.mark.unit
class TestUnwiredClaimsMatchTheCode:

    def test_the_results_prefix_entry_does_not_claim_to_be_unwired(self):
        assert "PIPELINE_OUTPUT_RESULTS_PREFIX" not in _documented_as_unwired()

    def test_the_marker_scan_matches_the_one_channel_that_is_unwired(self):
        """Positive control: the file still carries such a claim (PIPELINE_INPUT_PREFIX), so the
        assertion above is not green merely because the scan matched nothing."""
        assert "PIPELINE_INPUT_PREFIX" in _documented_as_unwired()

    def test_no_constant_documented_as_unwired_is_referenced_by_the_workflow_lambdas(self):
        sources = [_read(path) for path in
                   (_ASL_BUILDER_SOURCE, _INTERIM_SOURCE, _END_STATE_SOURCE)]
        contradicted = sorted(name for name in _documented_as_unwired()
                              if any(_references(source, name) for source in sources))
        assert contradicted == []

    def test_every_parsed_entry_names_a_real_constant(self):
        """Control on the parser: a renamed constant, or a comment convention the parser stopped
        understanding, must not silently reduce the scan to an empty set."""
        # A mock would answer every hasattr below, so anchor on a literal value first.
        assert s3PathPatterns.PIPELINE_OUTPUT_RESULTS_PREFIX == "/results/"
        entries = _comment_entries()
        assert entries
        assert [name for name in entries if not hasattr(s3PathPatterns, name)] == []


@pytest.mark.unit
class TestResultsChannelWireNames:

    def test_the_generator_threads_the_results_prefix_under_the_documented_keys(self):
        builder = _read(_ASL_BUILDER_SOURCE)
        assert _references(builder, "PIPELINE_OUTPUT_RESULTS_PREFIX")
        assert "outputResultsPrefixRelative" in builder
        assert "resultsPathKey" in builder

    def test_the_interim_step_carries_the_relative_prefix_to_the_next_manifest(self):
        assert "outputResultsPrefixRelative" in _read(_INTERIM_SOURCE)

    def test_a_wire_key_the_generator_does_not_emit_is_reported_absent(self):
        """Positive control for the two scans above: a fabricated key is not found, so they are not
        passing because the search always matches."""
        builder = _read(_ASL_BUILDER_SOURCE)
        assert "outputResultsPrefixNobodyEmits" not in builder
        assert "outputResultsPrefixNobodyEmits" not in _read(_INTERIM_SOURCE)

    def test_both_end_state_paths_read_the_results_path_key(self):
        readers = _functions_referencing(_END_STATE_SOURCE, "resultsPathKey")
        assert "_process_results_only" in readers
        assert len(readers) >= 2

    def test_the_function_scan_would_notice_a_key_no_function_reads(self):
        """Positive control: the scan returns nothing for a key the lambda does not read, so the
        assertion above is not satisfied by a scan that matches every function."""
        assert _functions_referencing(_END_STATE_SOURCE, "resultsPathKeyNobodyReads") == set()
