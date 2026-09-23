# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Builds the Strands agent for one run: model provider, system prompt and tools."""

import json
import os

PROVIDER_BEDROCK = "bedrock"
PROVIDER_OPENAI = "openai"
PROVIDERS = (PROVIDER_BEDROCK, PROVIDER_OPENAI)

BEDROCK_MODEL_ID_ENV = "BEDROCK_MODEL_ID"
OPENAI_MODEL_ID_ENV = "OPENAI_MODEL_ID"
OPENAI_API_KEY_SECRET_ARN_ENV = "OPENAI_API_KEY_SECRET_ARN"  # nosec B105 - environment variable name, not a secret
# Keys a JSON-shaped secret may store the API key under; a plain-string secret is used as-is.
OPENAI_SECRET_JSON_KEYS = ("apiKey", "OPENAI_API_KEY", "api_key", "key")

MAX_TOKENS = 8192
TEMPERATURE = 0.2

SYSTEM_PROMPT = """You are a mechanical CAD engineer who produces STEP files by writing CadQuery (Python) scripts.

How a run works:
1. Call inspect_input_step first. When an input file exists you MODIFY it: load it with
   cq.importers.importStep(os.environ["CAD_INPUT_STEP"]) and apply only the requested change, keeping
   everything else as it is. When there is no input you GENERATE the requested part from scratch.
2. When research tools are available and the instruction refers to a real product, standard or
   published design, use web_search and fetch_url to find reference dimensions before modelling.
   Record what you could not find; never invent a source.
3. Write ONE complete script per run_cad_script call. It must import os and cadquery as cq, build a
   solid, and export it with cq.exporters.export(result, os.environ["CAD_OUTPUT_STEP"]). Work in
   millimetres. Only the Python standard library and cadquery are available; there is no network.
4. Read the tool result: fix errors, and check the geometry summary (solid count, bounding box,
   volume) against what was asked before accepting an attempt.
5. Call finish exactly once with a factual summary, the list of requested elements you could NOT
   complete (including references you could not find online), and status "succeeded" only when every
   requested element is present. Be honest: "partial" with a clear unresolved list is a good outcome.

Constraints: stay within the attempt budget the tool reports; do not write files anywhere except
CAD_OUTPUT_STEP; do not attempt network access from a script.
"""


class ModelConfigurationError(RuntimeError):
    """The requested provider cannot be built from this deployment's configuration."""


def read_openai_api_key(secret_arn, secrets_client=None):
    """The OpenAI API key held in the configured Secrets Manager secret (plain or JSON-shaped)."""
    if not secret_arn:
        raise ModelConfigurationError("The openai provider is not configured (no API key secret)")
    if secrets_client is None:
        import boto3
        from botocore.config import Config
        secrets_client = boto3.client(
            "secretsmanager", config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
    value = secrets_client.get_secret_value(SecretId=secret_arn).get("SecretString", "")
    if not value:
        raise ModelConfigurationError("The OpenAI API key secret holds no string value")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value.strip()
    if isinstance(parsed, dict):
        for key in OPENAI_SECRET_JSON_KEYS:
            if parsed.get(key):
                return str(parsed[key]).strip()
        raise ModelConfigurationError("The OpenAI API key secret JSON names no recognised key")
    return value.strip()


def resolve_model(provider, model_id_override="", env=None, secrets_client=None):
    """(provider, model_id, api_key) for a run; the key is None for Bedrock."""
    env = os.environ if env is None else env
    provider = (provider or PROVIDER_BEDROCK).strip().lower()
    if provider not in PROVIDERS:
        raise ModelConfigurationError(f"Unknown model provider '{provider}'")
    if provider == PROVIDER_BEDROCK:
        model_id = (model_id_override or env.get(BEDROCK_MODEL_ID_ENV, "")).strip()
        if not model_id:
            raise ModelConfigurationError("No Bedrock model id is configured")
        return provider, model_id, None
    model_id = (model_id_override or env.get(OPENAI_MODEL_ID_ENV, "")).strip()
    if not model_id:
        raise ModelConfigurationError("No OpenAI model id is configured")
    api_key = read_openai_api_key(env.get(OPENAI_API_KEY_SECRET_ARN_ENV, ""), secrets_client)
    return provider, model_id, api_key


def build_model(provider, model_id, api_key=None, region=None, guardrail=None):
    """The Strands model object for the provider (lazy imports: the SDK lives in the container).

    On Bedrock the deployment's guardrail rides on every invocation; the OpenAI provider has no
    per-invocation guardrail, so its inputs are screened through ApplyGuardrail by the run instead.
    """
    if provider == PROVIDER_BEDROCK:
        from strands.models import BedrockModel
        if guardrail is None:
            raise ModelConfigurationError("The Bedrock provider requires a guardrail")
        return BedrockModel(model_id=model_id, region_name=region or os.environ.get("AWS_REGION"),
                            temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                            guardrail_id=guardrail.guardrail_id, guardrail_version=guardrail.version,
                            guardrail_trace="enabled")
    from strands.models.openai import OpenAIModel
    return OpenAIModel(client_args={"api_key": api_key}, model_id=model_id,
                       params={"max_tokens": MAX_TOKENS, "temperature": TEMPERATURE})


def build_agent(model, tools):
    from strands import Agent
    return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)


def run_framing(definition):
    """The fixed part of the user turn: the run's mode, output name and budgets."""
    agent_cfg = definition.get("agent", {}) or {}
    mode = definition.get("mode", "modify")
    return "\n".join([
        f"Mode: {mode}.",
        f"Output file name: {definition.get('outputFiles', {}).get('fileName', 'output.step')}.",
        f"Attempt budget: {agent_cfg.get('maxAttempts', 4)} script attempts.",
        "Internet research: " + ("allowed" if agent_cfg.get("allowInternetResearch") else "not available in this run") + ".",
        "",
        "Instruction:",
    ])


def run_instruction(definition, tag_prompt=False):
    """The user turn handed to the agent for one run.

    With ``tag_prompt`` the caller's instruction is a ``guardContent`` block, so the Bedrock guardrail
    attached to the model inspects exactly the caller-supplied text; without it the turn is one string
    (the OpenAI provider does not carry guard content).
    """
    prompt = str((definition.get("agent", {}) or {}).get("prompt", "")).strip()
    framing = run_framing(definition)
    if not tag_prompt:
        return framing + "\n" + prompt
    return [
        {"text": framing},
        {"guardContent": {"text": {"text": prompt, "qualifiers": ["guard_content"]}}},
    ]
