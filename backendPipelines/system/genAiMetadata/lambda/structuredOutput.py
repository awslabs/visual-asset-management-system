#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Structured output for the pipeline's Converse calls: one tool whose input schema is the run's target field
list, so a model that supports tool use answers with typed values, and with a value from a metadata schema's
controlled list where a field lands in a key of that type.

A model that does not support tool use answers a request carrying ``toolConfig`` with a ValidationException
that names tool use; the request is then sent again without it (the prompt-JSON form the system prompt already
describes) and the model id is remembered for the rest of the process, so later requests skip the tool. A model
that answers in text despite the tool is read as a prompt-JSON answer.

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
                    "Give a field a value from its listed values when the tool's schema lists them.")

KIND_TEXT = "text"
KIND_LIST = "list"

# The model ids that refused a request carrying toolConfig in this process.
_TOOLS_UNSUPPORTED = set()


def field_property(kind: str, description: str, max_chars: Optional[int] = None,
                   allowed: Optional[Sequence[str]] = None) -> dict:
    """One property of the tool's input schema: a string, or an array of strings; ``allowed`` lists the values a
    schema's controlled list allows (an ``enum`` on the string, or on each array item)."""
    values = [str(value) for value in allowed or [] if str(value).strip()]
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
    """The guardrail's verdict on a tool answer (``bedrockGuardrail.GuardedText``), screened as OUTPUT; the text
    unchanged when no guardrail is configured."""
    if not guardrail_config:
        return bedrockGuardrail.GuardedText(text=text)
    [verdict] = bedrockGuardrail.apply_guardrail_to_texts(
        client, guardrail_config, [text], source=bedrockGuardrail.APPLY_GUARDRAIL_SOURCE_OUTPUT)
    return verdict


def controlled_list_lines(constraints: Dict[str, List[str]]) -> List[str]:
    """The prompt lines naming each constrained field's allowed values, in field-name order."""
    return [f"{name}: one of {', '.join(values)}" for name, values in sorted(constraints.items()) if values]
