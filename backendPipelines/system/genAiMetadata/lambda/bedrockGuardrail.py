#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Bedrock guardrail every Converse call of this pipeline applies: its configuration from the environment
(``BEDROCK_GUARDRAIL_IDENTIFIER`` and ``BEDROCK_GUARDRAIL_VERSION``, both or neither), the ``guardContent``
blocks that carry the untrusted parts of a prompt (text and images) so the guardrail's input filters evaluate
them, and the account of an intervention that the caught failure records.

The media branch image carries a byte-identical copy of this module (a container build context cannot reach
``lambda/``), so it imports only the standard library.
"""

import json
from typing import List, Optional

GUARDRAIL_IDENTIFIER_VAR = "BEDROCK_GUARDRAIL_IDENTIFIER"
GUARDRAIL_VERSION_VAR = "BEDROCK_GUARDRAIL_VERSION"
# The stopReason a Converse response carries when the guardrail blocked the input or the output.
GUARDRAIL_STOP_REASON = "guardrail_intervened"
GUARD_CONTENT_QUALIFIERS = ["guard_content"]
# The one cold-start line a caller logs when no guardrail is configured: the Converse calls of that process run
# without prompt-attack filters, and this line is the operator's signal of it.
GUARDRAIL_UNCONFIGURED_WARNING = (
    f"No Bedrock guardrail is configured ({GUARDRAIL_IDENTIFIER_VAR} and {GUARDRAIL_VERSION_VAR} are unset): "
    "Converse calls run without prompt-attack filters"
)
# The policy sections of a guardrail assessment and the filter list each carries.
_ASSESSMENT_FILTER_LISTS = (
    ("topicPolicy", "topics"),
    ("contentPolicy", "filters"),
    ("wordPolicy", "customWords"),
    ("wordPolicy", "managedWordLists"),
    ("sensitiveInformationPolicy", "piiEntities"),
    ("sensitiveInformationPolicy", "regexes"),
    ("contextualGroundingPolicy", "filters"),
)
# The fields of a filter entry an intervention's cause records. A word, PII or regex filter also carries
# ``match`` (the text of the prompt it matched, so file content) and a regex filter its ``regex``; neither is
# recorded, because the cause reaches the execution record and the logs.
_FILTER_FIELDS = ("type", "action", "confidence")


def guardrail_config_from_env(environ) -> Optional[dict]:
    """The ``guardrailConfig`` of a Converse request from the environment, or ``None`` when no guardrail is
    configured. Both values or neither: an identifier without a version (or the reverse) cannot be applied and
    is a deployment error rather than a silent no-guardrail run."""
    identifier = (environ.get(GUARDRAIL_IDENTIFIER_VAR) or "").strip()
    version = (environ.get(GUARDRAIL_VERSION_VAR) or "").strip()
    if bool(identifier) != bool(version):
        raise ValueError(f"{GUARDRAIL_IDENTIFIER_VAR} and {GUARDRAIL_VERSION_VAR} must be set together")
    if not identifier:
        return None
    return {"guardrailIdentifier": identifier, "guardrailVersion": version, "trace": "enabled"}


def guard_content_block(text: str) -> dict:
    """A ``guardContent`` text block: the prompt parts that come from the file and its metadata, tagged so the
    guardrail's input filters evaluate them."""
    return {"guardContent": {"text": {"text": text, "qualifiers": list(GUARD_CONTENT_QUALIFIERS)}}}


def guard_image_block(image: dict) -> dict:
    """A ``guardContent`` image block from a Converse ``image`` block: a rendered view, page or frame of the
    file, tagged so the guardrail's input filters evaluate it. Once any block of a message is tagged the
    guardrail evaluates the tagged blocks only, so an untagged image would bypass the input assessment."""
    body = image["image"]
    return {"guardContent": {"image": {"format": body["format"], "source": dict(body["source"])}}}


def user_content_blocks(plain: str, guarded: str, guardrail_config: Optional[dict]) -> List[dict]:
    """The user message's text blocks. Without a guardrail, one text block carrying both parts in order (the
    plain instruction first). With one, the plain block, then a ``guardContent`` block for the untrusted part;
    an empty untrusted part adds no block."""
    if not guardrail_config:
        joined = "\n".join(part for part in (plain, guarded) if part)
        return [{"text": joined}]
    blocks = [{"text": plain}]
    if guarded:
        blocks.append(guard_content_block(guarded))
    return blocks


def user_image_blocks(images: List[dict], guardrail_config: Optional[dict]) -> List[dict]:
    """The user message's image blocks: the Converse ``image`` blocks as given without a guardrail, each one
    wrapped in a ``guardContent`` block with one."""
    if not guardrail_config:
        return list(images)
    return [guard_image_block(image) for image in images]


def intervened(response: dict) -> bool:
    return (response or {}).get("stopReason") == GUARDRAIL_STOP_REASON


def _filter_summaries(assessment: dict) -> List[dict]:
    """One ``{policy, type, action, confidence}`` entry (the fields present) per filter of one assessment."""
    summaries = []
    for policy, list_name in _ASSESSMENT_FILTER_LISTS:
        for entry in ((assessment or {}).get(policy) or {}).get(list_name) or []:
            if not isinstance(entry, dict):
                continue
            summary = {"policy": policy}
            summary.update({field: entry[field] for field in _FILTER_FIELDS if field in entry})
            summaries.append(summary)
    return summaries


def assessment_summary(trace: dict) -> dict:
    """The filters of a guardrail trace by side: ``input`` from ``inputAssessment`` and ``output`` from
    ``outputAssessments``, each a list of filter summaries; a side without filters is absent. Nothing else of the
    trace (its ``match`` texts, the model output it echoes) is carried."""
    guardrail = ((trace or {}).get("guardrail") or {})
    summary = {}
    input_filters = [item for assessment in (guardrail.get("inputAssessment") or {}).values()
                     for item in _filter_summaries(assessment)]
    if input_filters:
        summary["input"] = input_filters
    output_filters = [item for assessments in (guardrail.get("outputAssessments") or {}).values()
                      for assessment in (assessments if isinstance(assessments, list) else [assessments])
                      for item in _filter_summaries(assessment)]
    if output_filters:
        summary["output"] = output_filters
    return summary


def guardrail_cause(response: dict) -> str:
    """A short account of a guardrail intervention: the guardrail's reply text, then the type, action and
    confidence of every filter its trace reports. The trace itself is not recorded: with a word, PII or regex
    policy its ``match`` fields are the file text the policy matched, and the cause reaches the execution record
    and the logs."""
    message = (((response or {}).get("output") or {}).get("message") or {})
    text = "".join(block.get("text", "") for block in (message.get("content") or []))
    cause = f"guardrail intervened: {text}".strip()
    summary = assessment_summary((response or {}).get("trace"))
    if summary:
        cause += " " + json.dumps(summary, default=str, separators=(",", ":"))
    return cause
