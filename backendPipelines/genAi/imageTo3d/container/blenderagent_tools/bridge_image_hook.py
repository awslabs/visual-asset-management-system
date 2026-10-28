# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Move an image out of a tool result and into the user turn beside it, before the bridge sees it.

Codex's ``view_image`` tool returns the image it looked at as the tool call's output. The bridge translates
that into a Bedrock Converse ``toolResult`` block carrying an image, and the OpenAI models on Bedrock reject
exactly that shape::

    ValidationException: This model doesn't support the image field for user messages. Remove image and
    try again.

Measured against ``us.openai.gpt-5.6-terra`` with every shape a mid-conversation image can take: an image
in the first user message passes; an image in a later user message passes; an image in the SAME user
message as a tool result passes; an image INSIDE the tool result's content fails. So the fix is to keep the
tool result textual and append the image as a user message immediately after it. Converse requires roles
to alternate, so the bridge merges that user message with the tool result into one user turn -- the shape
that passes.

Registered with the LiteLLM proxy as a pre-call hook, so it runs on the request before any provider
translation. It rewrites both request shapes it may be handed: the Responses API input list Codex sends,
and the Chat Completions message list the bridge produces from it.

The same hook also reads the response stream, to refuse a completion the model never ran. Bedrock answers
a small share of ConverseStream calls -- six of one session's 662 -- with HTTP 200 and a usage of zero
input and zero output tokens; the bridge translates that into a ``response.completed`` event, and Codex
reads a completed response as the end of its turn. The hook refuses a completion whose output holds no
text and no function call and whose usage bills no output tokens. The input count is not read: the bridge
fills a zero prompt count from its own token counter, so on the fault it can be non-zero, while the output
count stays zero for a completion the model never produced and every real completion carries output.
Raising on the empty one ends the stream in an error frame instead, and Codex re-sends the request with
its in-memory history, up to its ``stream_max_retries``.
"""

import json
import os
import re

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:  # pragma: no cover - the unit tests exercise the rewrite without LiteLLM installed
    class CustomLogger:  # type: ignore[no-redef]
        pass


IMAGE_PART_TYPES = ("input_image", "image_url")


def _is_image_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") in IMAGE_PART_TYPES


def lift_images_from_responses_input(items):
    """Responses API ``input``: images inside a ``function_call_output`` move to a user message after it."""
    if not isinstance(items, list):
        return items, 0
    rewritten, moved = [], 0
    for item in items:
        if not (isinstance(item, dict) and item.get("type") == "function_call_output"
                and isinstance(item.get("output"), list)):
            rewritten.append(item)
            continue
        images = [p for p in item["output"] if _is_image_part(p)]
        if not images:
            rewritten.append(item)
            continue
        text_parts = [p for p in item["output"] if not _is_image_part(p)]
        kept = dict(item)
        # A tool output that was only an image still needs an output, and the text names what moved.
        kept["output"] = text_parts or [{"type": "input_text",
                                         "text": f"(returned {len(images)} image(s); shown below)"}]
        rewritten.append(kept)
        rewritten.append({
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text",
                         "text": f"Image(s) returned by tool call {item.get('call_id', '')}:"}]
            + [{"type": "input_image", **{k: v for k, v in p.items() if k != "type"}} for p in images],
        })
        moved += len(images)
    return rewritten, moved


def lift_images_from_chat_messages(messages):
    """Chat Completions ``messages``: images inside a ``tool`` message move to a user message after it."""
    if not isinstance(messages, list):
        return messages, 0
    rewritten, moved = [], 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not (isinstance(message, dict) and message.get("role") == "tool" and isinstance(content, list)):
            rewritten.append(message)
            continue
        images = [p for p in content if _is_image_part(p)]
        if not images:
            rewritten.append(message)
            continue
        text_parts = [p for p in content if not _is_image_part(p)]
        kept = dict(message)
        kept["content"] = text_parts or [{"type": "text",
                                          "text": f"(returned {len(images)} image(s); shown below)"}]
        rewritten.append(kept)
        rewritten.append({
            "role": "user",
            "content": [{"type": "text",
                         "text": f"Image(s) returned by tool call {message.get('tool_call_id', '')}:"}]
            + [{"type": "image_url", **{k: v for k, v in p.items() if k != "type"}} for p in images],
        })
        moved += len(images)
    return rewritten, moved


def rewrite_request(data):
    """Apply whichever rewrite the request shape calls for. Returns the data and how many images moved."""
    if not isinstance(data, dict):
        return data, 0
    moved = 0
    if isinstance(data.get("input"), list):
        data["input"], n = lift_images_from_responses_input(data["input"])
        moved += n
    if isinstance(data.get("messages"), list):
        data["messages"], n = lift_images_from_chat_messages(data["messages"])
        moved += n
    return data, moved


class EmptyCompletionRefused(RuntimeError):
    """A completed response with no output and no output tokens billed: the provider ended the stream before
    the model ran."""


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _carries_content(item):
    """A function call, or text: something the model produced."""
    if _field(item, "type") == "function_call":
        return True
    content = _field(item, "content")
    if isinstance(content, str):
        return bool(content)
    parts = content if isinstance(content, (list, tuple)) else [item]
    return any(_field(part, "text") for part in parts)


def empty_completion(response):
    """True when a completed response holds no function call and no text, and its usage bills no output
    tokens or is absent altogether. The input count is not read; the bridge fills it from its own token
    counter."""
    usage = _field(response, "usage")
    if usage is not None and (_field(usage, "output_tokens") or 0):
        return False
    output = _field(response, "output")
    return not any(_carries_content(item) for item in (output if isinstance(output, (list, tuple)) else []))


class ToolImageLiftHook(CustomLogger):
    """LiteLLM proxy hook: rewrites every request before provider translation, and refuses a completion
    that produced nothing on the way back."""

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        data, moved = rewrite_request(data)
        if moved:
            print(json.dumps({"bridge_image_hook": "lifted images out of tool results", "moved": moved,
                              "call_type": str(call_type)}), flush=True)
        return data

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        async for chunk in response:
            if _field(chunk, "type") == "response.completed" and empty_completion(_field(chunk, "response")):
                # The proxy turns the raise into an error frame and closes the stream; Codex retries.
                print(json.dumps({"bridge_image_hook": "refused an empty completion that billed no output tokens",
                                  "model": str(_field(request_data, "model") or "")}), flush=True)
                raise EmptyCompletionRefused(
                    "the response completed with no output and 0 output tokens billed: the model never ran")
            yield chunk


proxy_handler_instance = ToolImageLiftHook()


# ---------------------------------------------------------------------------------------------------------
# A model LiteLLM has never heard of gets no tools.
#
# LiteLLM decides which request fields a Bedrock Converse model accepts by looking the id up in its model
# map, and with ``drop_params`` on it silently removes anything it decides is unsupported. An id that is not
# in the map -- a model released after this LiteLLM, which is the whole point of making the model a setting
# -- comes back as "no tools, no reasoning effort", so the agent's shell and image tools never reach the
# model. Measured: GPT-6 Astra answered the opening prompt in 13 seconds with "this session provides no
# filesystem, terminal, or Blender tools" and ran nothing, while GPT-5.6 through the same bridge, whose id
# IS in the map, ran the whole job. The fix is to register the configured id with the capabilities of its
# nearest known sibling before the first request.
# ---------------------------------------------------------------------------------------------------------

KNOWN_SIBLINGS = ("global.openai.gpt-5.6-terra", "us.openai.gpt-5.6-terra", "global.openai.gpt-5.6-sol")


def ensure_model_known(model_id: str) -> str:
    """Register ``model_id`` in LiteLLM's model map from a known sibling if it is absent. Returns what happened."""
    import litellm

    if not model_id:
        return "no model id"
    if model_id in litellm.model_cost:
        return "known"
    template_key = next((key for key in KNOWN_SIBLINGS if key in litellm.model_cost), None)
    if template_key is None:
        return "no known sibling to copy capabilities from"
    litellm.register_model({model_id: dict(litellm.model_cost[template_key])})
    return f"registered from {template_key}"


_BRIDGE_MODEL_ID = os.environ.get("BRIDGE_MODEL_ID", "")
if _BRIDGE_MODEL_ID:
    print(f"bridge model {_BRIDGE_MODEL_ID}: {ensure_model_known(_BRIDGE_MODEL_ID)}", flush=True)


# ---------------------------------------------------------------------------------------------------------
# LiteLLM recognises an OpenAI model on Bedrock by the substring "openai.gpt-5" in its id.
#
# Three places in its Converse transformation key on that substring: which request fields the model
# supports, how ``reasoning_effort`` is sent (OpenAI's ``reasoning.effort`` against Anthropic's ``thinking``),
# and whether a ``thinking`` field is dropped. So ``openai.gpt-6-astra`` was handled as an Anthropic model,
# was sent ``thinking``, and Bedrock answered ``Unknown parameter: 'thinking'`` before the agent's first
# turn. The check is widened here to any ``openai.gpt-<digit>`` id, GPT-OSS excepted, which is what the
# id means. Pinned LiteLLM 1.100.0; revisit if the pin moves.
# ---------------------------------------------------------------------------------------------------------

OPENAI_ON_BEDROCK = re.compile(r"openai\.gpt-\d")


def is_openai_on_bedrock(model: str) -> bool:
    return bool(OPENAI_ON_BEDROCK.search(model or "")) and "gpt-oss" not in (model or "")


def widen_openai_model_detection() -> str:
    """Patch LiteLLM's Converse config so every OpenAI GPT model id on Bedrock takes the OpenAI code path."""
    try:
        from litellm.llms.bedrock.chat.converse_transformation import AmazonConverseConfig
    except Exception as e:  # the bridge cannot work without LiteLLM anyway; say so rather than crash the import
        return f"not patched: {e}"
    if getattr(AmazonConverseConfig, "_vams_openai_widened", False):
        return "already patched"

    original_handle = AmazonConverseConfig._handle_reasoning_effort_parameter
    original_supported = AmazonConverseConfig.get_supported_openai_params

    def handle_reasoning_effort(self, model, reasoning_effort, optional_params):
        if is_openai_on_bedrock(model):
            optional_params["reasoning"] = {"effort": reasoning_effort}
            optional_params.pop("thinking", None)
            return None
        return original_handle(self, model, reasoning_effort, optional_params)

    def supported_params(self, model):
        params = original_supported(self, model)
        if is_openai_on_bedrock(model):
            for name in ("reasoning_effort", "tools", "tool_choice"):
                if name not in params:
                    params.append(name)
        return params

    AmazonConverseConfig._handle_reasoning_effort_parameter = handle_reasoning_effort
    AmazonConverseConfig.get_supported_openai_params = supported_params
    AmazonConverseConfig._vams_openai_widened = True
    return "patched"


print(f"bridge: OpenAI-on-Bedrock detection {widen_openai_model_detection()}", flush=True)
