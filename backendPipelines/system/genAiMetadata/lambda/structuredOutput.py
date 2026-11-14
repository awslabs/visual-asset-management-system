#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Structured output for the pipeline's Converse calls: one tool whose input schema is the run's target field
list, so a model that supports tool use answers with typed values. A field that lands in a metadata schema key of
the controlled-list type has the list's values named in its description, as guidance: the model gives one of them
when it fits and its own value when none does, which key reuse then keeps out of the schema key
(``notInControlledList``). An ``enum`` would force a list value onto a subject none of them describes.

A model that does not support tool use answers a request carrying ``toolConfig`` with a ValidationException
that names tool use; the request is then sent again without it (the prompt-JSON form the system prompt already
describes) and the model id is remembered for the rest of the process, so later requests skip the tool. A model
that fails while producing the tool call (a ModelErrorException that names tool use) is asked the same question
once in prompt-JSON for that call only, since the failure depends on the content. A model that answers in text
despite the tool is read as a prompt-JSON answer.

The guardrail of a Converse call does not evaluate the tool input a model returns (it evaluates text and
``guardContent`` blocks only), so a tool answer is screened with ApplyGuardrail as OUTPUT before it is read:
a block fails the analysis as a guardrail intervention, and masked values are what is written. Either way the
answer is the same JSON text ``parse_model_json`` reads, and only the fields of the list survive it.
"""

import json
from typing import Dict, List, Optional, Sequence, Tuple

import bedrockGuardrail

TOOL_NAME = "record_analysis"
TOOL_DESCRIPTION = "Record the description and classification of the subject, one value per field."
# The instruction a request with the tool carries beside the system prompt: the object the system prompt
# describes is the tool's input rather than the reply's text.
TOOL_INSTRUCTION = (f"Call the {TOOL_NAME} tool exactly once; its input is the JSON object described above. "
                    "Leave out a field that does not apply instead of giving it null or a placeholder such as "
                    '"none" or "n/a". '
                    "When a field's description lists values, give one of them when it fits the subject, and "
                    "otherwise the value that does.")
# How a field's description offers a metadata schema's controlled list: guidance, not a constraint.
LISTED_VALUES_GUIDANCE = ("The metadata schema lists these values for {target}: {values}. Give one of them when it "
                          "fits; when none fits, give the value that does.")

KIND_TEXT = "text"
KIND_LIST = "list"

# The model ids that refused a request carrying toolConfig in this process.
_TOOLS_UNSUPPORTED = set()


def _values(values: Optional[Sequence[str]]) -> List[str]:
    return [str(value) for value in values or [] if str(value).strip()]


def field_property(kind: str, description: str, max_chars: Optional[int] = None,
                   allowed: Optional[Sequence[str]] = None, listed: Optional[Sequence[str]] = None) -> dict:
    """One property of the tool's input schema: a string, or an array of strings. ``allowed`` is a closed set of the
    pipeline's own (an ``enum`` on the string, or on each array item); ``listed`` is a metadata schema's controlled
    list, named in the description as guidance (LISTED_VALUES_GUIDANCE) and never an ``enum``."""
    values = _values(allowed)
    suggested = _values(listed)
    if suggested:
        description = description + " " + LISTED_VALUES_GUIDANCE.format(
            target="each item" if kind == KIND_LIST else "this field",
            values=", ".join(json.dumps(value, ensure_ascii=False) for value in suggested))
    if kind == KIND_LIST:
        items = {"type": "string"}
        if values:
            items["enum"] = values
        return {"type": "array", "items": items, "description": description}
    prop = {"type": "string", "description": description}
    if max_chars:
        prop["maxLength"] = int(max_chars)
    if values:
        prop["enum"] = values
    return prop


def tool_config(properties: Dict[str, dict], required: Sequence[str]) -> dict:
    """The Converse ``toolConfig``: the one tool, its input schema the target fields. No ``toolChoice``: forcing a
    named tool is offered by some model families only, and a model that answers in text is read as prompt-JSON."""
    return {"tools": [{"toolSpec": {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "inputSchema": {"json": {"type": "object", "properties": dict(properties),
                                 "required": [name for name in required if name in properties]}},
    }}]}


def supports_tools(model_id: str) -> bool:
    return model_id not in _TOOLS_UNSUPPORTED


def mark_tools_unsupported(model_id: str) -> None:
    _TOOLS_UNSUPPORTED.add(model_id)


def is_tool_refusal(exc) -> bool:
    """Whether a Converse error is the model refusing the tool configuration: a ValidationException whose
    message names tool use."""
    error = (getattr(exc, "response", None) or {}).get("Error") or {}
    if str(error.get("Code") or "") != "ValidationException":
        return False
    message = str(error.get("Message") or "").lower()
    return "tool" in message


def is_tool_use_model_error(exc) -> bool:
    """Whether a Converse error is the model failing while it produced the tool call: a ModelErrorException whose
    message names tool use (Amazon Nova: "Model produced invalid sequence as part of ToolUse")."""
    error = (getattr(exc, "response", None) or {}).get("Error") or {}
    if str(error.get("Code") or "") != "ModelErrorException":
        return False
    return "tool" in str(error.get("Message") or "").lower()


def with_tool(request: dict, config: dict) -> dict:
    """A copy of a Converse request that carries the tool and its instruction."""
    out = dict(request)
    out["toolConfig"] = config
    out["system"] = list(request.get("system") or []) + [{"text": TOOL_INSTRUCTION}]
    return out


def reply(response: dict) -> Tuple[str, bool]:
    """``(text, from_tool)``: the tool input of the first ``toolUse`` block that names the tool, as JSON text,
    else the reply's text blocks joined."""
    message = ((response or {}).get("output") or {}).get("message") or {}
    content = message.get("content") or []
    for block in content:
        tool_use = (block or {}).get("toolUse") if isinstance(block, dict) else None
        if isinstance(tool_use, dict) and tool_use.get("name") == TOOL_NAME:
            payload = tool_use.get("input")
            return json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False), True
    return "".join(block.get("text", "") for block in content if isinstance(block, dict)), False


def screen_tool_reply(client, guardrail_config: Optional[dict], text: str):
    """The guardrail's verdict on a tool answer (``bedrockGuardrail.GuardedText``), screened as OUTPUT, so a block's
    cause lists the filters under ``output``; the text unchanged when no guardrail is configured."""
    if not guardrail_config:
        return bedrockGuardrail.GuardedText(text=text)
    [verdict] = bedrockGuardrail.apply_guardrail_to_texts(
        client, guardrail_config, [text], source=bedrockGuardrail.APPLY_GUARDRAIL_SOURCE_OUTPUT)
    return verdict


def controlled_list_lines(constraints: Dict[str, List[str]]) -> List[str]:
    """The prompt lines naming the values a metadata schema's controlled list holds for each field, in field-name
    order."""
    return [f"{name}: {', '.join(values)}" for name, values in sorted(constraints.items()) if values]
