# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One CAD agent job, from definition document to task-token report.

Both entrypoints (AWS Batch and Amazon Bedrock AgentCore Runtime) call ``run_job``. The outcome is
derived from the tool state, not from the model's prose: a run that produced at least one validated STEP
file succeeds (``succeeded`` or ``partial`` per the agent's own unresolved list); a run that produced
none fails. Every failure route reports the task token.
"""

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid

import boto3
from botocore.config import Config

from . import agent as agent_module
from . import cad_step_naming, report, tools

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md: the container talks to
# Amazon S3 and Step Functions for the length of a run.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = logging.getLogger("cad_step_agent")
TASK_TOKEN_ENV = "TASK_TOKEN"  # nosec B105 - environment variable name, not a secret
DEFAULT_MAX_RUN_SECONDS = 3600
FAILURE_ERROR_CODE = "CadStepAgentRunFailed"
INPUT_FILE_NAME = "input.step"


class RunFailed(RuntimeError):
    """The run produced no valid STEP file (or could not start)."""


def _clients():
    return (boto3.client("s3", config=retry_config),
            boto3.client("stepfunctions", config=retry_config))


def parse_definition(raw):
    """The definition document as a dict, whichever of its transport shapes it arrived in."""
    if isinstance(raw, list):
        raw = raw[0]
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    if isinstance(raw, str):  # a JSON string that itself encodes JSON
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise RunFailed("definition is not a JSON object")
    for key in ("mode", "outputFiles", "agent"):
        if key not in raw:
            raise RunFailed(f"definition is missing '{key}'")
    return raw


def output_key(definition):
    """The S3 key of the STEP file: the output dir + the input's relative subdir + the file name."""
    out = definition["outputFiles"]
    object_dir = out.get("objectDir", "") or ""
    if object_dir and not object_dir.endswith("/"):
        object_dir += "/"
    return f"{object_dir}{out.get('relativeSubdir', '') or ''}{out['fileName']}"


def metadata_key(definition):
    """The S3 key of the file-metadata document, named after the output's asset-relative path."""
    meta = definition.get("outputMetadata") or {}
    object_dir = meta.get("objectDir", "") or ""
    if not meta.get("bucketName") or not object_dir:
        return None
    if not object_dir.endswith("/"):
        object_dir += "/"
    out = definition["outputFiles"]
    return f"{object_dir}{out.get('relativeSubdir', '') or ''}{out['fileName']}.metadata.json"


def derive_outcome(state, definition, model_label, run_id, wall_seconds):
    """The RunOutcome a finished agent run stands for; raises RunFailed when no STEP was produced."""
    if not state.best_output:
        detail = state.attempts[-1].summary if state.attempts else "the agent produced no script attempt"
        raise RunFailed(f"No valid STEP file was produced after {len(state.attempts)} attempt(s): {detail}")
    unresolved = list(state.final_unresolved)
    if not state.finished:
        unresolved.append("The agent ended without recording a final summary; review the output before use.")
    status = report.STATUS_PARTIAL if (unresolved or state.final_status_hint == report.STATUS_PARTIAL) \
        else report.STATUS_SUCCEEDED
    summary = state.final_summary or (state.attempts[-1].summary if state.attempts else "")
    return report.RunOutcome(
        status=status,
        prompt=str(definition["agent"].get("prompt", "")),
        mode=definition["mode"],
        output_file_name=definition["outputFiles"]["fileName"],
        model=model_label,
        run_id=run_id,
        attempts=list(state.attempts),
        summary=summary,
        unresolved=unresolved,
        sources=list(state.sources),
        geometry=state.best_geometry,
        wall_seconds=wall_seconds,
        research_allowed=state.research_allowed,
    )


def upload_outputs(s3, definition, outcome, step_path):
    bucket = definition["outputFiles"]["bucketName"]
    key = output_key(definition)
    s3.upload_file(step_path, bucket, key)
    logger.info("uploaded STEP s3://%s/%s", bucket, key)
    report_key = f"{key[: -len(definition['outputFiles']['fileName'])]}{report.report_file_name(definition['outputFiles']['fileName'])}"
    s3.put_object(Bucket=bucket, Key=report_key, Body=report.markdown_report(outcome).encode("utf-8"),
                  ContentType="text/markdown")
    meta_key = metadata_key(definition)
    if meta_key:
        s3.put_object(Bucket=definition["outputMetadata"]["bucketName"], Key=meta_key,
                      Body=json.dumps(report.metadata_document(outcome)).encode("utf-8"),
                      ContentType="application/json")
    return key


def _download_input(s3, definition, work_root):
    input_file = definition.get("inputFile")
    if not input_file:
        return None
    local = os.path.join(work_root, INPUT_FILE_NAME)
    s3.download_file(input_file["bucketName"], input_file["objectKey"], local)
    return local


class Watchdog:
    """Reports the token as failed when a run outlives its budget, whatever the agent is doing."""

    def __init__(self, seconds, on_expire):
        self._timer = threading.Timer(seconds, on_expire)
        self._timer.daemon = True
        self.fired = False

    def start(self):
        self._timer.start()

    def cancel(self):
        self._timer.cancel()


def run_job(definition_raw, task_token, s3=None, sfn=None, agent_factory=None, search_fn=None,
            fetch_fn=None, now=None):
    """Run one job end to end and report ``task_token``. Returns the success payload, or raises after
    reporting a failure. ``agent_factory(model, tools) -> callable`` is injectable for tests."""
    if s3 is None or sfn is None:
        s3_default, sfn_default = _clients()
        s3 = s3 or s3_default
        sfn = sfn or sfn_default
    started = time.time()
    run_id = uuid.uuid4().hex[:12]
    token_lock = threading.Lock()
    token_state = {"reported": False}

    def report_failure(message):
        with token_lock:
            if token_state["reported"] or not task_token:
                token_state["reported"] = True
                return
            token_state["reported"] = True
        try:
            sfn.send_task_failure(taskToken=task_token, error=FAILURE_ERROR_CODE,
                                  cause=report.failure_cause(message))
        except Exception as exc:  # the failure is already logged; a stale token must not mask it
            logger.error("send_task_failure failed: %s", exc)

    def report_success(payload):
        with token_lock:
            if token_state["reported"]:
                raise RunFailed("the run's time budget expired before its result was reported")
            token_state["reported"] = True
        if task_token:
            sfn.send_task_success(taskToken=task_token, output=json.dumps(payload))

    work_root = tempfile.mkdtemp(prefix="cad-agent-")
    watchdog = None
    try:
        definition = parse_definition(definition_raw)
        agent_cfg = definition["agent"]
        max_run = int(agent_cfg.get("maxRunSeconds") or DEFAULT_MAX_RUN_SECONDS)
        watchdog = Watchdog(max_run, lambda: report_failure(f"Run exceeded its {max_run}s budget"))
        watchdog.start()

        # The output name is passed back through the same naming module the Lambda used, as an
        # override: a name that came out of the rules comes back unchanged, and a definition whose
        # name was hand-edited in transit (a foreign extension, a disallowed character) does not.
        out = definition["outputFiles"]
        input_name = (definition.get("inputFile") or {}).get("objectKey", "").rsplit("/", 1)[-1]
        expected = cad_step_naming.resolve_output_filename(
            definition["mode"], input_name, out.get("fileName", ""), "", "", agent_cfg.get("prompt", ""))
        if out.get("fileName") != expected:
            raise RunFailed("output file name does not follow the mode's naming rules")

        input_step = _download_input(s3, definition, work_root)
        provider, model_id, api_key = agent_module.resolve_model(
            agent_cfg.get("modelProvider"), agent_cfg.get("modelId", ""))
        model_label = f"{provider}:{model_id}"

        state = tools.RunState(
            work_root=work_root,
            input_step=input_step,
            output_name=out["fileName"],
            max_attempts=int(agent_cfg.get("maxAttempts") or 4),
            script_timeout_seconds=int(agent_cfg.get("scriptTimeoutSeconds") or 300),
            deadline_epoch=started + max_run,
            research_allowed=bool(agent_cfg.get("allowInternetResearch", False)),
        )
        bound_tools = tools.build_tools(state, search_fn=search_fn, fetch_fn=fetch_fn)
        factory = agent_factory or (lambda m, t: agent_module.build_agent(m, t))
        model = agent_module.build_model(provider, model_id, api_key) if agent_factory is None else None
        agent = factory(model, bound_tools)
        logger.info("agent run start id=%s mode=%s provider=%s attempts=%s research=%s",
                    run_id, definition["mode"], provider, state.max_attempts, state.research_allowed)
        try:
            agent(agent_module.run_instruction(definition))
        except Exception as exc:
            logger.exception("agent loop raised")
            if not state.best_output:
                raise RunFailed(f"Agent loop failed: {str(exc)[:200]}") from exc
            state.final_unresolved.append(f"The agent stopped early: {str(exc)[:200]}")

        outcome = derive_outcome(state, definition, model_label, run_id, time.time() - started)
        key = upload_outputs(s3, definition, outcome, state.best_output)
        payload = report.success_payload(outcome)
        payload["outputKey"] = key
        report_success(payload)
        logger.info("agent run done id=%s status=%s attempts=%s unresolved=%s",
                    run_id, outcome.status, len(outcome.attempts), len(outcome.unresolved))
        return payload
    except Exception as exc:
        logger.exception("run failed")
        report_failure(str(exc))
        raise
    finally:
        if watchdog:
            watchdog.cancel()
        shutil.rmtree(work_root, ignore_errors=True)
