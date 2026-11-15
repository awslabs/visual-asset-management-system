# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Amazon Bedrock guardrail every run applies to what the model is shown.

Two inputs reach the model from outside the deployment: the caller's instruction and the text of the
pages the research tool fetches. Both are screened here through ``ApplyGuardrail`` (source INPUT), so
the PROMPT_ATTACK filter sees them whichever model provider the run uses; on the Bedrock provider the
same guardrail is additionally attached to every model invocation, with the instruction tagged as
guard content. A run with no guardrail configured does not start: the deployment always supplies one.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.config import Config

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

logger = logging.getLogger("cad_step_agent.guardrail")

GUARDRAIL_ID_ENV = "BEDROCK_GUARDRAIL_ID"
GUARDRAIL_VERSION_ENV = "BEDROCK_GUARDRAIL_VERSION"
# ApplyGuardrail accepts at most 25 text units (1000 characters each) per call; a page the tool hands
# to the model is already cut to 12 000 characters and an instruction to 20 000.
SCREEN_MAX_CHARS = 25_000
ACTION_INTERVENED = "GUARDRAIL_INTERVENED"


class GuardrailNotConfigured(RuntimeError):
    """The container was started without a guardrail id and version."""


class GuardrailBlocked(RuntimeError):
    """The guardrail intervened on the screened text."""


@dataclass
class Guardrail:
    guardrail_id: str
    version: str
    client: Optional[object] = None

    @classmethod
    def from_env(cls, env=None, client=None):
        env = os.environ if env is None else env
        guardrail_id = (env.get(GUARDRAIL_ID_ENV, "") or "").strip()
        version = (env.get(GUARDRAIL_VERSION_ENV, "") or "").strip()
        if not guardrail_id or not version:
            raise GuardrailNotConfigured(
                f"{GUARDRAIL_ID_ENV} and {GUARDRAIL_VERSION_ENV} must both be set; the deployment supplies them")
        return cls(guardrail_id=guardrail_id, version=version, client=client)

    def _client(self):
        if self.client is None:
            self.client = boto3.client("bedrock-runtime", config=retry_config)
        return self.client

    def screen(self, text, what="input"):
        """Raise GuardrailBlocked when the guardrail intervenes on ``text``; otherwise return it."""
        text = (text or "")[:SCREEN_MAX_CHARS]
        if not text.strip():
            return text
        response = self._client().apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.version,
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
        if response.get("action") == ACTION_INTERVENED:
            logger.warning("guardrail intervened on %s", what)
            raise GuardrailBlocked(f"The {what} was blocked by the guardrail's input filter")
        return text
