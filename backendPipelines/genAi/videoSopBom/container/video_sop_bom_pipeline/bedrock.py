# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Amazon Bedrock Converse with forced tool use: the three structured calls and the retry budget around them.

The prompts and the tool input schemas are the package's shipped files: a prompt is rendered by substituting every
`{{TOKEN}}` its `PROMPT_TOKENS` entry lists, and a tool schema is the shipped JSON Schema with its
metadata keys dropped and its local `$ref`s inlined, so the model is steered by and validated against
the same document."""

import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass, field

import botocore.exceptions
import jsonschema
from botocore.exceptions import ClientError

from . import PROMPT_TOKENS, load_prompt, load_schema
from .errors import MODEL_OUTPUT_INVALID, PIPELINE_ERROR, PipelineRejection
from .vocab import MATERIAL_TYPES, PART_TYPE_DESCRIPTIONS, PART_TYPES, PRIMARY_TECHNIQUES
from .windows import Window, format_timestamp

logger = logging.getLogger("video_sop_bom_pipeline.bedrock")

BEDROCK_CALL_MARKER = "BEDROCK_CALL stage=%s model=%s in=%d out=%d"
WINDOW_MAX_TOKENS = 16000
FINALIZE_MAX_TOKENS = 32000
VISION_MAX_TOKENS = 32000

MAX_ATTEMPTS_PER_CALL = 6
MAX_WALL_PER_CALL_S = 2700
MAX_CUMULATIVE_RETRY_S = 5400
BACKOFF_BASE_S = 15.0
BACKOFF_CAP_S = 300.0
RETRYABLE_CODES = frozenset({
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelNotReadyException", "ModelTimeoutException", "InternalServerException",
})
FATAL_CODES = frozenset({"ValidationException", "AccessDeniedException", "ResourceNotFoundException", "ModelErrorException"})

# The Converse stopReason enum (API_runtime_Converse.html); there is no `refusal` on Converse.
KNOWN_STOP_REASONS = (
    "end_turn", "tool_use", "max_tokens", "stop_sequence", "guardrail_intervened", "content_filtered",
    "malformed_model_output", "malformed_tool_use", "model_context_window_exceeded",
)
FATAL_STOP_REASONS = ("content_filtered", "guardrail_intervened", "model_context_window_exceeded")

VISION_BATCH_MAX_FRAMES = 12
VISION_BATCH_MAX_BYTES = 15 * 1024 * 1024
VISION_CONTEXT_MARGIN_S = 60.0
COMPACT_DROP_KEYS = ("notes", "failure_modes", "material_notes", "additional_details", "reason", "expected_content")

WINDOW_TOOL = "record_window_extraction"
VISION_TOOL = "record_frame_verification"
FINALIZE_TOOL = "record_final_outputs"
PROMPT_NAMES_BY_TOOL = {WINDOW_TOOL: "window_extraction", VISION_TOOL: "vision_verification", FINALIZE_TOOL: "finalize"}
TOOL_DESCRIPTIONS = {
    WINDOW_TOOL: "Record the steps, component observations and key moments extracted from one transcript window.",
    VISION_TOOL: "Record the visual verification of each key frame in this batch, keyed by momentIndex.",
    FINALIZE_TOOL: "Record the final BOM rows, step-to-BOM references, cross-window dependencies, SOP summary and lab summary.",
}
SCHEMA_METADATA_KEYS = ("$schema", "$id", "title", "description", "$defs")
_TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")

# The system boundary prompt, sent verbatim on every call.
SYSTEM_BOUNDARY = load_prompt("system_boundary").strip()


def _inline_refs(node, defs):
    """Replace every local `{"$ref": "#/$defs/<name>"}` with the definition it names (recursively)."""
    if isinstance(node, dict):
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            merged = dict(target)
            merged.update({key: value for key, value in node.items() if key != "$ref"})
            return _inline_refs(merged, defs)
        return {key: _inline_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(value, defs) for value in node]
    return node


def tool_schema(name):
    """The shipped tool schema as a Converse `inputSchema.json`: top-level metadata dropped, local $refs inlined."""
    shipped = load_schema(name)
    defs = shipped.get("$defs", {})
    body = {key: value for key, value in shipped.items() if key not in SCHEMA_METADATA_KEYS}
    return _inline_refs(body, defs)


WINDOW_TOOL_SCHEMA = tool_schema("window_extraction_schema")
VISION_TOOL_SCHEMA = tool_schema("vision_schema")
FINALIZE_TOOL_SCHEMA = tool_schema("finalize_schema")


def render_prompt(name, values):
    """The shipped prompt with every `{{TOKEN}}` in PROMPT_TOKENS[name] substituted. A slot without a value is
    a programming error (KeyError), never a prompt the model sees."""
    template = load_prompt(name)
    missing = set(_TOKEN.findall(template)) - set(values)
    if missing:
        raise KeyError(f"prompt {name} has no value for {sorted(missing)}")
    text = template
    for token in PROMPT_TOKENS[name]:
        text = text.replace("{{" + token + "}}", str(values[token]))
    return text


class MaxTokensStop(Exception):
    """The model hit maxTokens under forced tool choice; the caller reshapes the request and retries once."""


@dataclass
class BedrockUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retry_wait_s: float = 0.0
    per_stage: dict = field(default_factory=dict)

    def record(self, stage, in_tokens, out_tokens):
        self.calls += 1
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        bucket = self.per_stage.setdefault(stage, {"calls": 0, "inputTokens": 0, "outputTokens": 0})
        bucket["calls"] += 1
        bucket["inputTokens"] += in_tokens
        bucket["outputTokens"] += out_tokens

    def as_dict(self):
        return {"calls": self.calls, "inputTokens": self.input_tokens, "outputTokens": self.output_tokens}


def _request(model_id, system, messages, tool_name, schema, max_tokens):
    description = TOOL_DESCRIPTIONS.get(tool_name, f"Record the structured result for {tool_name}.")
    return {
        "modelId": model_id,
        "system": [{"text": system}],
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens},
        "toolConfig": {
            "tools": [{"toolSpec": {"name": tool_name, "description": description, "inputSchema": {"json": schema}}}],
            "toolChoice": {"tool": {"name": tool_name}},
        },
    }


def _code_and_message(exc):
    error = exc.response.get("Error", {})
    return error.get("Code", "ClientError"), error.get("Message", "")


def _backoff(attempt, rng):
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1))) * rng(0.5, 1.5)


def _wait_or_exhaust(code, attempt, started, stage, usage, sleep, rng, clock, exc):
    delay = _backoff(attempt, rng)
    elapsed = clock() - started
    if (attempt >= MAX_ATTEMPTS_PER_CALL
            or elapsed + delay > MAX_WALL_PER_CALL_S
            or usage.retry_wait_s + delay > MAX_CUMULATIVE_RETRY_S):
        minutes = max(1, int(round(elapsed / 60.0)))
        raise PipelineRejection(PIPELINE_ERROR, f"Amazon Bedrock {code} persisted for {minutes} min at stage {stage}") from exc
    logger.warning("BEDROCK_RETRY stage=%s code=%s attempt=%d wait=%.0fs", stage, code, attempt, delay)
    usage.retry_wait_s += delay
    sleep(delay)


def _converse_with_budget(clients, request, stage, usage, sleep, rng, clock):
    """One Converse response under the bounded retry budget; records usage and logs the marker."""
    attempt = 0
    started = clock()
    while True:
        attempt += 1
        try:
            response = clients.bedrock.converse(**request)
        except ClientError as exc:
            code, message = _code_and_message(exc)
            if code not in RETRYABLE_CODES:
                raise PipelineRejection(PIPELINE_ERROR, f"Amazon Bedrock {code} at stage {stage}: {message}") from exc
            _wait_or_exhaust(code, attempt, started, stage, usage, sleep, rng, clock, exc)
            continue
        except (botocore.exceptions.ConnectionError, botocore.exceptions.HTTPClientError) as exc:
            _wait_or_exhaust(type(exc).__name__, attempt, started, stage, usage, sleep, rng, clock, exc)
            continue
        token_usage = response.get("usage") or {}
        in_tokens = int(token_usage.get("inputTokens") or 0)
        out_tokens = int(token_usage.get("outputTokens") or 0)
        usage.record(stage, in_tokens, out_tokens)
        logger.info(BEDROCK_CALL_MARKER, stage, request["modelId"], in_tokens, out_tokens)
        return response


def _find_tool_use(response, tool_name):
    content = ((response.get("output") or {}).get("message") or {}).get("content") or []
    for block in content:
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == tool_name:
            return tool_use
    return None


def _interpret(response, tool_name, schema, stage):
    """('ok', payload, '') or ('retry', log_label, model_feedback); raises on fatal or unknown stop reasons.
    The log label never carries model output; the feedback (sent back to the model) may."""
    stop = response.get("stopReason")
    if stop == "max_tokens":
        raise MaxTokensStop(f"{tool_name} at stage {stage}")
    if stop in FATAL_STOP_REASONS:
        raise PipelineRejection(
            MODEL_OUTPUT_INVALID,
            f"Amazon Bedrock stopped with stopReason {stop} at stage {stage}; the model produced no usable {tool_name} result.",
        )
    if stop not in KNOWN_STOP_REASONS:
        raise PipelineRejection(MODEL_OUTPUT_INVALID, f"Amazon Bedrock returned an unknown stopReason {stop!r} at stage {stage}.")
    tool_use = _find_tool_use(response, tool_name)
    if stop != "tool_use" or tool_use is None:
        return ("retry", f"stopReason={stop} without a {tool_name} toolUse block",
                f"the response did not call the tool {tool_name} (stopReason {stop})")
    payload = tool_use.get("input")
    if not isinstance(payload, dict):
        return ("retry", "toolUse input is not an object", "the tool input must be a JSON object, not a string or list")
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        return ("retry", f"schema violation at {path}", f"the tool input violated the schema at {path}: {exc.message}")
    return ("ok", payload, "")


def _with_feedback(request, tool_name, feedback):
    """Fold the rejection into the last user message. The assistant's toolUse is never echoed back: a
    toolUse block without a matching toolResult is rejected by the API."""
    messages = [dict(message) for message in request["messages"]]
    last = messages[-1]
    last["content"] = list(last["content"]) + [{
        "text": f"Your previous response was not accepted: {feedback}. Respond again, only through the tool "
                f"{tool_name}, with an input that satisfies its schema."
    }]
    return dict(request, messages=messages)


def converse_tool(clients, model_id, system, messages, tool_name, schema, max_tokens, *, stage="", usage=None,
                  sleep=time.sleep, rng=random.uniform, clock=time.monotonic):
    """The validated tool input, or MaxTokensStop / PipelineRejection. One validation retry, fed back."""
    usage = usage if usage is not None else BedrockUsage()
    request = _request(model_id, system, messages, tool_name, schema, max_tokens)
    response = _converse_with_budget(clients, request, stage, usage, sleep, rng, clock)
    outcome = _interpret(response, tool_name, schema, stage)
    if outcome[0] == "ok":
        return outcome[1]
    logger.warning("BEDROCK_RETRY stage=%s reason=%s", stage, outcome[1])
    response = _converse_with_budget(clients, _with_feedback(request, tool_name, outcome[2]), stage, usage, sleep, rng, clock)
    outcome = _interpret(response, tool_name, schema, stage)
    if outcome[0] == "ok":
        return outcome[1]
    raise PipelineRejection(
        MODEL_OUTPUT_INVALID,
        f"Amazon Bedrock returned no valid {tool_name} result at stage {stage} after one retry ({outcome[1]}).",
    )


def _bullets(values):
    return "\n".join(f"- {value}" for value in values)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=1)


def _operator(config):
    return (config.get("additionalInstructions") or "").strip()


def extract_window(clients, model_id, window, config, product_name, *, max_key_moments, window_count=1, usage=None, **kw):
    """One window through the window_extraction prompt. `product_name` is carried for the finalize call's
    sake (the window template declares no PRODUCT_NAME slot); the window is described 1-based."""
    text = render_prompt(PROMPT_NAMES_BY_TOOL[WINDOW_TOOL], {
        "WINDOW_INDEX": window.index + 1,
        "WINDOW_COUNT": max(1, window_count),
        "WINDOW_START_HMS": format_timestamp(window.start_s),
        "WINDOW_END_HMS": format_timestamp(window.end_s),
        "PART_TYPES": _bullets(PART_TYPES),
        "MATERIAL_TYPES": _bullets(MATERIAL_TYPES),
        "MAX_KEY_FRAMES": max_key_moments,
        "TRANSCRIPT": window.text,
        "ADDITIONAL_INSTRUCTIONS": _operator(config),
    })
    messages = [{"role": "user", "content": [{"text": text}]}]
    return converse_tool(clients, model_id, SYSTEM_BOUNDARY, messages, WINDOW_TOOL, WINDOW_TOOL_SCHEMA,
                         WINDOW_MAX_TOKENS, stage=f"window-{window.index}", usage=usage, **kw)


def _split_window(window):
    half = max(1, len(window.segments) // 2)
    first, second = window.segments[:half], window.segments[half:]
    if not second:
        return None
    cut = second[0].start_s
    return (
        Window(index=window.index, start_s=window.start_s, end_s=cut, segments=first),
        Window(index=window.index, start_s=cut, end_s=window.end_s, segments=second),
    )


def extract_all_windows(clients, model_id, windows, config, product_name, *, max_key_frames, usage=None, **kw):
    """Every window extracted in order; a max_tokens stop splits that window once (each half tried once).
    Returns [(Window, result)] with the windows renumbered consecutively."""
    per_window = max(1, math.ceil(max_key_frames / max(1, len(windows))))
    count = len(windows)
    results = []
    for window in windows:
        try:
            results.append((window, extract_window(clients, model_id, window, config, product_name,
                                                   max_key_moments=per_window, window_count=count, usage=usage, **kw)))
            continue
        except MaxTokensStop:
            halves = _split_window(window)
            if halves is None:
                raise PipelineRejection(MODEL_OUTPUT_INVALID, f"window {window.index} output exceeded 16K tokens even after splitting")
            logger.warning("window %s hit maxTokens; splitting at %.1fs", window.index, halves[1].start_s)
        for half in halves:
            try:
                results.append((half, extract_window(clients, model_id, half, config, product_name,
                                                     max_key_moments=per_window, window_count=count, usage=usage, **kw)))
            except MaxTokensStop:
                raise PipelineRejection(MODEL_OUTPUT_INVALID, f"window {window.index} output exceeded 16K tokens even after splitting")
    for index, (window, _) in enumerate(results):
        window.index = index
    return results


def batch_frames(frames):
    """Time-ordered batches of at most VISION_BATCH_MAX_FRAMES frames and VISION_BATCH_MAX_BYTES bytes."""
    ordered = sorted(frames, key=lambda frame: (frame["timestamp_seconds"], frame["momentIndex"]))
    batches, current, current_bytes = [], [], 0
    for frame in ordered:
        size = os.path.getsize(frame["path"])
        if current and (len(current) >= VISION_BATCH_MAX_FRAMES or current_bytes + size > VISION_BATCH_MAX_BYTES):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(frame)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _context_for(batch, steps, components):
    low = batch[0]["timestamp_seconds"] - VISION_CONTEXT_MARGIN_S
    high = batch[-1]["timestamp_seconds"] + VISION_CONTEXT_MARGIN_S
    window_steps = [step for step in steps if low <= float(step.get("timestamp_seconds", 0)) <= high]
    window_components = [c for c in components if low <= float(c.get("first_seen_timestamp_seconds", 0)) <= high]
    return window_steps, (window_components or components)


def _frame_list(batch):
    # No `=` after momentIndex here: the per-image caption below is the only `momentIndex=<n>` text.
    return "\n".join(
        f"- momentIndex {frame['momentIndex']} (t={frame['timestamp_seconds']:.1f}s): expected {frame.get('expected_content', '')}"
        for frame in batch
    )


def _verify_batch(clients, model_id, batch, steps, components, stage, usage, additional_instructions, kw):
    window_steps, window_components = _context_for(batch, steps, components)
    intro = render_prompt(PROMPT_NAMES_BY_TOOL[VISION_TOOL], {
        "WINDOW_STEPS": _json(window_steps),
        "WINDOW_COMPONENTS": _json(window_components),
        "FRAME_LIST": _frame_list(batch),
        "ADDITIONAL_INSTRUCTIONS": (additional_instructions or "").strip(),
    })
    content = [{"text": intro}]
    for frame in batch:
        content.append({"text": f"Frame momentIndex={frame['momentIndex']} at {frame['timestamp_seconds']:.1f}s — expected: {frame.get('expected_content', '')}"})
        with open(frame["path"], "rb") as handle:
            content.append({"image": {"format": "jpeg", "source": {"bytes": handle.read()}}})
    messages = [{"role": "user", "content": content}]
    result = converse_tool(clients, model_id, SYSTEM_BOUNDARY, messages, VISION_TOOL, VISION_TOOL_SCHEMA,
                           VISION_MAX_TOKENS, stage=stage, usage=usage, **kw)
    return result["frame_analyses"]


def verify_frames(clients, model_id, frames, steps, components, *, additional_instructions="", usage=None, **kw):
    """Vision verification over time-local batches; a max_tokens stop halves that batch once."""
    results = []
    for number, batch in enumerate(batch_frames(frames)):
        stage = f"vision-{number}"
        try:
            results.extend(_verify_batch(clients, model_id, batch, steps, components, stage, usage, additional_instructions, kw))
            continue
        except MaxTokensStop:
            if len(batch) < 2:
                raise PipelineRejection(MODEL_OUTPUT_INVALID, f"vision batch {number} output exceeded 32K tokens for a single frame")
            logger.warning("vision batch %d hit maxTokens; halving %d frames", number, len(batch))
        half = len(batch) // 2
        for part in (batch[:half], batch[half:]):
            try:
                results.extend(_verify_batch(clients, model_id, part, steps, components, stage, usage, additional_instructions, kw))
            except MaxTokensStop:
                raise PipelineRejection(
                    MODEL_OUTPUT_INVALID,
                    f"vision batch {number} output exceeded 32K tokens for {len(batch)} frames even after halving",
                )
    return results


def _compact(items):
    return [{key: value for key, value in item.items() if key not in COMPACT_DROP_KEYS} for item in items]


def finalize(clients, model_id, steps, components, vision_results, config, product_name, *, usage=None, compact=False, **kw):
    """The final BOM rows, bom_refs, dependency edges, SOP summary/safety and lab summary text."""
    if compact:
        steps, components, vision_results = _compact(steps), _compact(components), _compact(vision_results)
    text = render_prompt(PROMPT_NAMES_BY_TOOL[FINALIZE_TOOL], {
        "PRODUCT_NAME": product_name,
        "PART_LEVEL_BASE": config.get("partLevelBase", "0"),
        "PART_TYPES_WITH_DESCRIPTIONS": "\n".join(f"- {name}: {PART_TYPE_DESCRIPTIONS[name]}" for name in PART_TYPES),
        "MATERIAL_TYPES": _bullets(MATERIAL_TYPES),
        "PRIMARY_TECHNIQUES": _bullets(PRIMARY_TECHNIQUES),
        "MERGED_STEPS": _json(steps),
        "MERGED_COMPONENTS": _json(components),
        "VISION_RESULTS": _json(vision_results),
        "ADDITIONAL_INSTRUCTIONS": _operator(config),
    })
    messages = [{"role": "user", "content": [{"text": text}]}]
    try:
        return converse_tool(clients, model_id, SYSTEM_BOUNDARY, messages, FINALIZE_TOOL, FINALIZE_TOOL_SCHEMA,
                             FINALIZE_MAX_TOKENS, stage="finalize", usage=usage, **kw)
    except MaxTokensStop:
        if not compact:
            logger.warning("finalize hit maxTokens; retrying with a compacted draft")
            return finalize(clients, model_id, steps, components, vision_results, config, product_name,
                            usage=usage, compact=True, **kw)
        raise PipelineRejection(
            MODEL_OUTPUT_INVALID,
            f"finalize output exceeded 32K tokens for {len(components)} components / {len(steps)} steps",
        )
