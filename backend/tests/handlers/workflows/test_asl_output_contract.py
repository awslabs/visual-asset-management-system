# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The generated ASL's outbound contract with the two lambdas that read it.

Guards:

- **S3-CONTRACTS-005** -- ``auxPreviewPipelineSuffix``. The execute handler sources a pipeline's
  configured viewer subfolder from its record for step 1; the interim path hardcoded ``""`` for every
  later step, so the same pipeline wrote its viewer data to a different aux location purely because
  of its position in the workflow. The value travels per EXECUTION, the way ``stepInputFilters`` and
  ``stepInputArity`` already do: pipeline record -> the execute handler's ``stepAuxPreviewSuffixes``
  list on the SFN input -> a static ``$.stepAuxPreviewSuffixes[i]`` index in the ASL -> interim
  payload -> next pipeline's manifest. Asserted here are the ASL's index threading and the interim
  lambda's manifest write; the per-step VALUES on the SFN input are asserted where the rest of the
  per-step arrays are, in ``test_execute_metadata_identity_and_gate.py``.
- **S3-CONTRACTS-039** -- the process-output payload carried ``pipeline`` and ``description``, which
  the end-state lambda never reads. Asserted as a property (every body key is referenced in the
  reader) rather than as two named absences, so a future dead key is caught too. The property is
  asserted twice: a substring scan of the reader source, and an AST scan of the keys the reader
  actually reads off its event dict. The second is the exact form -- a substring scan also accepts a
  key the reader names only in a comment, a log message, a subscript of a different dict, or an
  assignment onto the event of a key it synthesizes for itself.
- **S3-CONTRACTS-040** -- the ``PIPELINE_OUTPUT_RESULTS_PREFIX`` comment declared the prefix unused by
  workflow generation while the generator threads it into every definition. Asserted as the general
  claim: no constant may be documented as unused by workflow generation while the generator
  references it.
- **S11-EXTERNALS3-005 / S2-BACKEND-100** -- run I/O in a default bucket registered under a
  ``baseAssetsPrefix`` was written at the bucket ROOT, outside the area the operator declared as
  VAMS's. The prefix now travels as its own per-execution SFN field, the two full-URI templates
  interpolate it, and the relative path keys stay relative so the end-state lambda's
  ASSET_PATH_PIPELINE check still applies. Asserted by RESOLVING each intrinsic against a real
  execution input, so the assertion is about the key produced rather than the shape of a template.
"""

import ast
import json
import os
import re

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")

# The real stepfunctions_builder is used, not a stub: these assertions read the payload out of the
# state's Parameters.Payload, which is the shape only the real create_lambda_task_state produces. It
# imports with no AWS or env dependency, so nothing needs standing in for it.
from backend.backend.common.workflows.workflowAslBuilder import generate_workflow_asl
from backend.backend.common.workflows.workflowAsl import to_asl_pipeline_dict
from backend.backend.common.workflows import pipelineRecords as pr
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt

_BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
_READER_SOURCE = os.path.normpath(os.path.join(
    _BACKEND_ROOT, "handlers", "workflows", "sfn", "processWorkflowExecutionOutput.py"))
_PATH_PATTERNS_SOURCE = os.path.normpath(os.path.join(_BACKEND_ROOT, "common", "s3PathPatterns.py"))
_ASL_BUILDER_SOURCE = os.path.normpath(os.path.join(
    _BACKEND_ROOT, "common", "workflows", "workflowAslBuilder.py"))


def _pipeline(name, **extra):
    base = {"name": name, "pipelineId": name, "databaseId": "pdb"}
    base.update(extra)
    return base


def _asl(pipelines):
    definition, job_names = generate_workflow_asl(
        pipelines, "db", "wf",
        process_workflow_output_function="pf",
        interim_tracking_function="itf",
        error_handler_function="ehf")
    return definition, job_names


def _state(definition, prefix):
    return [definition["States"][k] for k in definition["States"] if k.startswith(prefix)]


def _payload_body(state):
    return state["Parameters"]["Payload"]["body"]


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# S3-CONTRACTS-005
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAuxPreviewSuffixReachesLaterSteps:

    def test_each_interim_state_indexes_the_step_it_prepares(self):
        """Hop 1. Each interim state must index the SFN input at the position of the step it is
        preparing. Asserted over a three-step workflow so an off-by-one (indexing the step that just
        finished, or always the first) cannot pass."""
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2"), _pipeline("p3")])
        interims = sorted(
            (k for k in definition["States"] if k.startswith("interim-")),
            key=lambda k: int(k.split("-")[1]))
        threaded = [_payload_body(definition["States"][k])["nextPipelineAuxPreviewSuffix.$"]
                    for k in interims]
        assert threaded == ["$.stepAuxPreviewSuffixes[1]", "$.stepAuxPreviewSuffixes[2]"]

    def test_no_interim_state_bakes_a_literal_suffix(self):
        """A definition carrying the plain key alongside the JSONPath one would resolve to whichever
        Step Functions applied last, so the literal must be gone rather than merely shadowed. Both
        input shapes the builder used to read are declared -- flattened on p2, nested in systemConfig
        on p3 -- so reverting either resolution path shows up as a literal in the definition."""
        definition, _jobs = _asl([
            _pipeline("p1", auxPreviewPipelineSuffix="/One"),
            _pipeline("p2", auxPreviewPipelineSuffix="/Two"),
            _pipeline("p3", systemConfig={"auxPreviewPipelineSuffix": "/Three"}),
        ])
        bodies = [_payload_body(definition["States"][k])
                  for k in definition["States"] if k.startswith("interim-")]
        assert bodies, "no interim state was generated, so this asserts nothing"
        assert [b for b in bodies if "nextPipelineAuxPreviewSuffix" in b] == []
        serialized = json.dumps(definition)
        assert [s for s in ("/One", "/Two", "/Three") if s in serialized] == []

    def test_the_asl_pipeline_adapter_no_longer_carries_the_suffix(self):
        """The flattened key existed only for the ASL builder to bake. Left behind it reads as a live
        second source for a value that now travels per execution — the dead-constant shape."""
        record = {
            "pipelineId": "potree", "databaseId": "pdb",
            "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"}},
            "systemConfig": pr.build_pipeline_system_config(
                aux_preview_pipeline_suffix="/PotreeViewer"),
        }
        assert record["systemConfig"]["auxPreviewPipelineSuffix"] == "/PotreeViewer"
        assert "auxPreviewPipelineSuffix" not in to_asl_pipeline_dict(record, "job-2")

    def test_the_interim_lambda_writes_the_threaded_suffix_into_the_next_manifest(self):
        """Hop 2. The manifest value is what manifestHelper and the {{auxPreviewPipelineSuffix}} tag
        read, so it is asserted on the object actually put to S3. The body key carries the value Step
        Functions resolved from the indexed SFN input."""
        manifest = self._manifest_for(
            {"nextPipelineAuxPreviewSuffix": "/PotreeViewer"})
        assert manifest["auxPreviewPipelineSuffix"] == "/PotreeViewer"

    def test_an_execution_launched_before_the_suffix_was_threaded_still_writes_an_empty_string(self):
        """Negative control + backward compatibility: an in-flight state machine baked without the
        key must not crash the interim step or write a null."""
        manifest = self._manifest_for({})
        assert manifest["auxPreviewPipelineSuffix"] == ""

    @staticmethod
    def _manifest_for(body_overrides):
        manifest_key = "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json"
        body = {
            "workflowExecutionId": "EXEC1", "workflowId": "wf1", "workflowDatabaseId": "wdb1",
            "executingUserName": "user@x",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "outputFilesPrefix": "pipelines/p1/job-1/output/EXEC1/files/",
            "nextPipelineManifestS3Key": manifest_key,
            "nextPipelineConfigS3Key": "",
            "nextPipelineExecutionId": "P2", "nextPipelineId": "potree",
            "nextPipelineDatabaseId": "pdb", "nextPipelineJobName": "job-2",
        }
        body.update(body_overrides)
        captured = {}
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1/scan.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1/"}]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object",
                          MagicMock(side_effect=lambda **kw: captured.update(
                              {kw["Key"]: kw["Body"]}))), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[]):
            ipt.prepare_next_pipeline(body)
        return json.loads(captured[manifest_key].decode("utf-8"))


# ---------------------------------------------------------------------------
# S3-CONTRACTS-039
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestProcessOutputPayloadHasNoDeadKeys:

    @staticmethod
    def _body_keys():
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        body = _payload_body(_state(definition, "process-outputs-")[0])
        return [key[:-2] if key.endswith(".$") else key for key in body]

    def test_every_body_key_is_referenced_by_the_end_state_lambda(self):
        source = _read(_READER_SOURCE)
        unread = [key for key in self._body_keys()
                  if f"'{key}'" not in source and f'"{key}"' not in source]
        assert unread == []

    def test_the_scan_would_notice_a_key_the_reader_ignores(self):
        """Positive control for the scan: a fabricated key is detected as unread, so the assertion
        above is not passing because the search always matches."""
        source = _read(_READER_SOURCE)
        fabricated = "stepLabelNobodyReads"
        assert f"'{fabricated}'" not in source and f'"{fabricated}"' not in source

    def test_the_two_reported_dead_keys_are_gone(self):
        assert "pipeline" not in self._body_keys()
        assert "description" not in self._body_keys()

    def test_the_keys_the_end_state_lambda_needs_are_still_sent(self):
        """Control against over-removal: dropping dead keys must not drop live ones."""
        keys = set(self._body_keys())
        assert {"workflowExecutionId", "workflowDatabaseId", "workflowId",
                "endStatePipelineExecutionId", "priorPipelineExecutionIds",
                "filesPathKey", "metadataPathKey", "previewPathKey", "resultsPathKey",
                "workflowExecutionS3InputOutputBucket", "outputLocationType",
                "outputAssetId", "outputDatabaseId", "outputFileBaseExecutionPathExtension",
                "executingUserName", "executingRequestContext"} <= keys


# Every function in the end-state lambda that receives the process-output body names its parameter
# ``event`` (``lambda_handler``, ``_process_results_only``, ``_validation_failure``). A rename empties
# the scanned set, which the vacuity control below turns into a failure rather than a silent pass.
_READER_EVENT_ALIASES = ("event",)


def _keys_read_off_the_event(source):
    """The string keys the end-state lambda reads off its event/body dict.

    Counts ``event['k']``, ``event.get('k')`` and ``'k' in event`` only, so -- unlike a substring
    scan of the source -- a key that appears solely in a comment, in a log message, or as a
    subscript of some other dict is not mistaken for a read.

    A subscript is counted only in load context. ``event['k'] = v`` is the handler synthesizing a
    key for its own later use, not consuming one the payload sent: a sent key that is only ever
    assigned over is dead on arrival, so counting the assignment would hide exactly the kind of
    dead key this guard exists to find.
    """
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in _READER_EVENT_ALIASES
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in _READER_EVENT_ALIASES
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
        elif (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Name) and comparator.id in _READER_EVENT_ALIASES:
                    keys.add(node.left.value)
    return keys


# One handler-shaped sample carrying each form the scan must accept and each it must reject. Every
# name in it is quoted, so a substring scan of it accepts all five.
_SCANNER_FIXTURE = """
def lambda_handler(event, context):
    event['onlyAssignedOntoTheEvent'] = other['namedOnSomeOtherDict']
    if 'membershipTested' in event:
        return event['subscripted'], event.get('fetched')
"""


@pytest.mark.unit
class TestProcessOutputPayloadKeysAreReadOffTheEvent:
    """The same no-dead-keys property as above, stated over what the reader actually reads.

    ``assetId`` / ``databaseId`` are read but not sent -- the handler synthesizes them from
    ``outputAssetId`` / ``outputDatabaseId`` -- so the property is one-directional: sent implies read.
    """

    def test_every_body_key_is_actually_read_off_the_event_dict(self):
        read_keys = _keys_read_off_the_event(_read(_READER_SOURCE))
        unread = [key for key in TestProcessOutputPayloadHasNoDeadKeys._body_keys()
                  if key not in read_keys]
        assert unread == []

    def test_the_check_flags_the_two_keys_that_were_reported(self):
        """Positive control for the defect: the two keys the payload used to carry are reported
        unread when fed through this check, so the assertion above has teeth."""
        read_keys = _keys_read_off_the_event(_read(_READER_SOURCE))
        as_shipped_before = (list(TestProcessOutputPayloadHasNoDeadKeys._body_keys())
                             + ["pipeline", "description"])
        unread = [key for key in as_shipped_before if key not in read_keys]
        assert unread == ["pipeline", "description"]

    def test_the_substring_scan_alone_would_accept_a_key_that_is_never_read(self):
        """Why this class exists alongside the substring scan. Both shapes it lets through are
        quoted in the source, so the substring scan accepts them; neither is a read of the payload,
        and only the AST form says so. Stated over a fixture rather than over the reader, so the
        two scans are compared on their own terms and an unrelated edit to the reader cannot make
        the comparison stop holding."""
        read_keys = _keys_read_off_the_event(_SCANNER_FIXTURE)
        for key in ("namedOnSomeOtherDict", "onlyAssignedOntoTheEvent"):
            assert f"'{key}'" in _SCANNER_FIXTURE          # the substring scan accepts it
            assert key not in read_keys                    # the AST scan does not
        assert read_keys == {"subscripted", "fetched", "membershipTested"}

    def test_the_reader_synthesizes_a_key_it_never_reads_off_the_event(self):
        """The write-only case is real, not hypothetical: the handler assigns ``requestContext``
        onto the event to hand to ``request_to_claims`` and never reads it back, so sending it
        would be dead. Anchors the fixture above to the file the guard actually scans."""
        source = _read(_READER_SOURCE)
        assert "'requestContext'" in source or '"requestContext"' in source
        assert "requestContext" not in _keys_read_off_the_event(source)
        assert "requestContext" not in TestProcessOutputPayloadHasNoDeadKeys._body_keys()

    def test_the_scan_finds_the_keys_the_reader_demonstrably_reads(self):
        """Vacuity control: an alias rename or a broken walk empties the set, which would make the
        subset assertion above pass for every key."""
        read_keys = _keys_read_off_the_event(_read(_READER_SOURCE))
        assert {"filesPathKey", "metadataPathKey", "previewPathKey", "resultsPathKey",
                "outputAssetId", "outputDatabaseId", "executingRequestContext",
                "workflowExecutionS3InputOutputBucket"} <= read_keys


# ---------------------------------------------------------------------------
# S3-CONTRACTS-040
# ---------------------------------------------------------------------------

_UNUSED_CLAIM = "not yet used by workflow generation"


def _documented_as_unused():
    """Constant names whose s3PathPatterns comment entry claims workflow generation does not use them.

    An entry starts at ``# NAME: ...`` and continues on the indented ``#   ...`` lines below it, which
    is the file's own comment convention.
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
            entries[current] += " " + text
    return {name for name, body in entries.items() if _UNUSED_CLAIM in body.lower()}


@pytest.mark.unit
class TestPathPatternCommentsMatchTheGenerator:

    def test_no_constant_is_documented_as_unused_while_the_generator_uses_it(self):
        builder_source = _read(_ASL_BUILDER_SOURCE)
        contradicted = sorted(name for name in _documented_as_unused()
                              if name in builder_source)
        assert contradicted == []

    def test_the_scan_finds_the_claim_at_all(self):
        """Positive control: the file still carries such a claim (PIPELINE_INPUT_PREFIX), so the
        assertion above is not green merely because the scan matched nothing."""
        assert "PIPELINE_INPUT_PREFIX" in _documented_as_unused()

    def test_the_results_prefix_is_genuinely_used_by_the_generator(self):
        """Anchors the pair: the constant the stale comment described IS referenced, so re-adding the
        claim to it fails the first assertion rather than silently agreeing with it."""
        assert "PIPELINE_OUTPUT_RESULTS_PREFIX" in _read(_ASL_BUILDER_SOURCE)
        assert "PIPELINE_OUTPUT_RESULTS_PREFIX" not in _documented_as_unused()


# ---------------------------------------------------------------------------
# S11-EXTERNALS3-005 / S2-BACKEND-100
# ---------------------------------------------------------------------------

_BASE_PREFIX_FIELD = "workflowExecutionS3InputOutputBasePrefix"


def _resolve_intrinsic(expr, execution_input, execution_name):
    """Evaluate a ``States.Format('fmt', arg, ...)`` intrinsic the way Step Functions would.

    The assertions below are about the KEY a state machine produces, not about the text of a
    template, so each intrinsic is resolved against a real execution input. A ``$.field`` the input
    does not carry raises -- which is what Step Functions does (States.Runtime) -- rather than
    resolving to empty and letting a missing field read as a bucket-root key.
    """
    match = re.match(r"^States\.Format\('(.*)',\s*(.*)\)$", expr, re.S)
    assert match, f"not a States.Format intrinsic: {expr!r}"
    fmt, raw_args = match.group(1), [arg.strip() for arg in match.group(2).split(",")]
    values = []
    for arg in raw_args:
        if arg == "$$.Execution.Name":
            values.append(execution_name)
        else:
            assert arg.startswith("$."), f"unhandled intrinsic argument {arg!r}"
            field = arg[2:]
            assert field in execution_input, (
                f"the intrinsic reads $.{field}, which the execution input does not carry; "
                "Step Functions would fail the state with States.Runtime")
            values.append(execution_input[field])
    out, rest = [], fmt
    for value in values:
        head, sep, rest = rest.partition("{}")
        assert sep, f"more arguments than {{}} placeholders in {fmt!r}"
        out.extend([head, str(value)])
    out.append(rest)
    return "".join(out)


def _execution_input(base_prefix):
    return {"workflowExecutionS3InputOutputBucket": "run-bkt",
            _BASE_PREFIX_FIELD: base_prefix}


def _step_input_uris(base_prefix, step_prefix="step1-"):
    definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
    body = _payload_body(_state(definition, step_prefix)[0])
    resolved = _execution_input(base_prefix)
    return (_resolve_intrinsic(body["inputManifestS3Location.$"], resolved, "EXEC1"),
            _resolve_intrinsic(body["inputConfigurationS3Location.$"], resolved, "EXEC1"))


@pytest.mark.unit
class TestRunIoHonoursTheDefaultBucketBasePrefix:
    """The state machine's own I/O locations resolve INSIDE the default bucket's declared area."""

    def test_a_prefixed_bucket_puts_step_one_input_inside_the_declared_area(self):
        manifest_uri, config_uri = _step_input_uris("vams-assets/")
        assert manifest_uri == ("s3://run-bkt/vams-assets/pipelines/workflowExecutionInputs/"
                               "EXEC1/pipeline1/manifest.json")
        assert config_uri == ("s3://run-bkt/vams-assets/pipelines/workflowExecutionInputs/"
                             "EXEC1/pipeline1/config.json")

    def test_the_wrong_key_the_defect_produced_is_no_longer_produced(self):
        """Stated as the named key rather than as 'starts with the prefix': the defect wrote
        s3://bucket/pipelines/... on a prefixed bucket, and that exact string must be gone."""
        manifest_uri, _config_uri = _step_input_uris("vams-assets/")
        assert manifest_uri != ("s3://run-bkt/pipelines/workflowExecutionInputs/"
                               "EXEC1/pipeline1/manifest.json")

    def test_an_empty_prefix_still_resolves_to_the_bucket_root(self):
        """The owner's carve-out, and the must-still-work arm for every deployment whose default
        bucket is the VAMS-created one."""
        manifest_uri, config_uri = _step_input_uris("")
        assert manifest_uri == ("s3://run-bkt/pipelines/workflowExecutionInputs/"
                               "EXEC1/pipeline1/manifest.json")
        assert config_uri == ("s3://run-bkt/pipelines/workflowExecutionInputs/"
                             "EXEC1/pipeline1/config.json")

    def test_no_resolved_uri_carries_a_double_slash(self):
        """A prefix joined unconditionally would mint s3://bucket//pipelines/..., i.e. an object under
        an empty first path segment. Checked for both the empty and the prefixed area."""
        for base_prefix in ("", "vams-assets/"):
            for uri in _step_input_uris(base_prefix):
                assert "//" not in uri[len("s3://"):], (base_prefix, uri)

    def test_every_step_gets_its_own_folder_inside_the_area(self):
        """Off-by-one control: the prefix must not collapse the per-step folders together."""
        step1, _ = _step_input_uris("vams-assets/", "step1-")
        step2, _ = _step_input_uris("vams-assets/", "step2-")
        assert step1.endswith("/pipeline1/manifest.json")
        assert step2.endswith("/pipeline2/manifest.json")
        assert step2.startswith("s3://run-bkt/vams-assets/pipelines/workflowExecutionInputs/")

    def test_both_downstream_payloads_carry_the_base_prefix(self):
        """The interim and end-state lambdas join it themselves, so a payload that omits it silently
        sends them to the bucket root while the pipelines write under the prefix."""
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        interim_body = _payload_body(_state(definition, "interim-")[0])
        process_body = _payload_body(_state(definition, "process-outputs-")[0])
        assert interim_body[f"{_BASE_PREFIX_FIELD}.$"] == f"$.{_BASE_PREFIX_FIELD}"
        assert process_body[f"{_BASE_PREFIX_FIELD}.$"] == f"$.{_BASE_PREFIX_FIELD}"

    def test_the_end_state_path_keys_stay_in_the_shape_the_validator_accepts(self):
        """THE TRAP. processWorkflowExecutionOutput validates filesPathKey/metadataPathKey/
        previewPathKey against ASSET_PATH_PIPELINE, which is anchored at 'pipelines/'. A definition
        that baked the prefix into these would fail validation on every prefixed run and ingest
        nothing -- worse than the defect. They must resolve WITHOUT the area prefix."""
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        body = _payload_body(_state(definition, "process-outputs-")[0])
        resolved = _execution_input("vams-assets/")
        for key_name in ("filesPathKey", "metadataPathKey", "previewPathKey", "resultsPathKey"):
            value = _resolve_intrinsic(body[f"{key_name}.$"], resolved, "EXEC1")
            assert value.startswith("pipelines/"), (key_name, value)
            assert "vams-assets/" not in value, (key_name, value)

    def test_the_aux_temp_prefix_is_not_joined_to_the_area(self):
        """The auxiliary bucket is VAMS-created and has no baseAssetsPrefix, so prefixing its working
        folder would be a second, silent misplacement."""
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        body = _payload_body(_state(definition, "interim-")[0])
        value = _resolve_intrinsic(
            body["nextPipelineAuxTempPrefix.$"], _execution_input("vams-assets/"), "EXEC1")
        assert value == "pipelines/p2/EXEC1/"

    def test_the_resolver_rejects_an_intrinsic_reading_a_field_the_input_lacks(self):
        """Vacuity control for _resolve_intrinsic: an execution input missing the base-prefix field
        must RAISE rather than resolve to a bucket-root key, or every assertion above could pass on a
        definition whose field name does not match what executeWorkflow sends."""
        definition, _jobs = _asl([_pipeline("p1"), _pipeline("p2")])
        body = _payload_body(_state(definition, "step1-")[0])
        with pytest.raises(AssertionError, match="States.Runtime"):
            _resolve_intrinsic(body["inputManifestS3Location.$"],
                              {"workflowExecutionS3InputOutputBucket": "run-bkt"}, "EXEC1")
