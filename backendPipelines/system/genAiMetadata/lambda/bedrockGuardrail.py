#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Bedrock guardrail every model call of this pipeline applies: its configuration from the environment
(``BEDROCK_GUARDRAIL_IDENTIFIER`` and ``BEDROCK_GUARDRAIL_VERSION``, both or neither), the ``guardContent``
blocks that carry the untrusted parts of a Converse prompt (text and images) so the guardrail's input filters
evaluate them, the verdict on a Converse response — blocked (an intervention the caught failure records) or
masked (a success whose text carries the sensitive-information filter's mask tokens) — the account of an
intervention, and the standalone ``ApplyGuardrail`` evaluation of the text an embeddings ``InvokeModel`` receives,
which carries no guardrail of its own: the masked output is what is embedded and stored, and a blocked text is not
embedded at all.

The media branch image carries a byte-identical copy of this module (a container build context cannot reach
``lambda/``), so it imports only the standard library.
"""

import json
from dataclasses import dataclass, field
from typing import List, Optional

GUARDRAIL_IDENTIFIER_VAR = "BEDROCK_GUARDRAIL_IDENTIFIER"
GUARDRAIL_VERSION_VAR = "BEDROCK_GUARDRAIL_VERSION"
# The stopReason a Converse response carries when the guardrail acted on the input or the output: a filter
# BLOCKED the call, or the sensitive-information filter ANONYMIZED text. In the second case the message is the
# complete answer with the matched entities replaced by their type tokens ({NAME}, {ADDRESS}, ...). The trace
# tells the two apart; the stop reason alone does not.
GUARDRAIL_STOP_REASON = "guardrail_intervened"
# The actions a filter entry of the trace reports.
FILTER_ACTION_BLOCKED = "BLOCKED"
FILTER_ACTION_ANONYMIZED = "ANONYMIZED"
GUARD_CONTENT_QUALIFIERS = ["guard_content"]
# The one cold-start line a caller logs when no guardrail is configured: the Converse calls of that process run
# without prompt-attack filters, the text it embeds is embedded and stored as composed, and this line is the
# operator's signal of it.
GUARDRAIL_UNCONFIGURED_WARNING = (
    f"No Bedrock guardrail is configured ({GUARDRAIL_IDENTIFIER_VAR} and {GUARDRAIL_VERSION_VAR} are unset): "
    "Converse calls run without prompt-attack filters and embedded text is not masked"
)
# ApplyGuardrail evaluates text on its own, for the text an embeddings model receives: the source is INPUT (file
# content and metadata on their way to a model), and the response's action is NONE or GUARDRAIL_INTERVENED — the
# latter for a filter that blocked and for the sensitive-information filter that only masked, told apart by the
# assessments as on a Converse response.
APPLY_GUARDRAIL_SOURCE = "INPUT"
APPLY_GUARDRAIL_ACTION_INTERVENED = "GUARDRAIL_INTERVENED"
# One ApplyGuardrail call carries several text blocks. A guardrail text unit is 1,000 characters and the service
# quota for ApplyGuardrail is counted in text units per second (25 by default), so a batch stays under 20 text
# units and ten blocks; a single text longer than the character budget travels in a call of its own.
APPLY_GUARDRAIL_BATCH_MAX_CHARS = 20_000
APPLY_GUARDRAIL_BATCH_MAX_BLOCKS = 10
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


def _filter_entries(response: dict) -> List[dict]:
    """Every filter summary of the response's trace, input and output sides together."""
    summary = assessment_summary((response or {}).get("trace"))
    return [entry for side in summary.values() for entry in side]


def intervened(response: dict) -> bool:
    """Whether the guardrail blocked the call. The stop reason is necessary but not sufficient: a response
    whose filters carry a BLOCKED action on either side is an intervention; one whose filters carry ANONYMIZED
    actions and no BLOCKED one is a masked success, and its message is used as any other answer; the stop reason
    with no trace or no filter entries — nothing to say what the guardrail did — is an intervention."""
    if (response or {}).get("stopReason") != GUARDRAIL_STOP_REASON:
        return False
    actions = [entry.get("action") for entry in _filter_entries(response)]
    if not actions or FILTER_ACTION_BLOCKED in actions:
        return True
    return FILTER_ACTION_ANONYMIZED not in actions


def masked_entity_types(response: dict) -> List[str]:
    """The distinct types of the filters that anonymized text on either side, sorted; empty when nothing was
    masked. Types only — the ``match`` text a filter replaced is the sensitive value itself."""
    types = {str(entry.get("type") or "") for entry in _filter_entries(response)
             if entry.get("action") == FILTER_ACTION_ANONYMIZED}
    return sorted(item for item in types if item)


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
        cause += " " + _summary_json(summary)
    return cause


def _summary_json(summary: dict) -> str:
    return json.dumps(summary, default=str, separators=(",", ":"), sort_keys=True)


def assessment_line(response: dict) -> str:
    """The per-side filter summary of a Converse response's trace as one compact JSON line — the INFO line a
    caller logs on a success, so which policies acted on the prompt and on the reply is readable from the log by
    policy, type and action, never by the text. ``{}`` when no filter fired."""
    return _summary_json(assessment_summary((response or {}).get("trace")))


@dataclass
class GuardedText:
    """The ApplyGuardrail verdict on one text. ``text`` is what may be embedded and stored — the text as given when
    no filter acted, the guardrail's masked output when the sensitive-information filter anonymized; ``None`` when
    ``blocked``. ``masked_types`` are the entity types anonymized (types only), ``filters`` the summaries of every
    filter the call reported, and ``cause`` the account of a blocked text for the failure record."""

    text: Optional[str]
    blocked: bool = False
    masked_types: List[str] = field(default_factory=list)
    filters: List[dict] = field(default_factory=list)
    cause: str = ""


def apply_guardrail_batches(texts: List[str]) -> List[List[int]]:
    """The indexes of ``texts`` grouped into the batches one ApplyGuardrail call carries, in order: at most
    APPLY_GUARDRAIL_BATCH_MAX_BLOCKS texts and APPLY_GUARDRAIL_BATCH_MAX_CHARS characters per batch, a text longer
    than the budget forming a batch of its own."""
    batches: List[List[int]] = []
    batch: List[int] = []
    chars = 0
    for index, text in enumerate(texts):
        size = len(text or "")
        if batch and (len(batch) >= APPLY_GUARDRAIL_BATCH_MAX_BLOCKS
                      or chars + size > APPLY_GUARDRAIL_BATCH_MAX_CHARS):
            batches.append(batch)
            batch, chars = [], 0
        batch.append(index)
        chars += size
    if batch:
        batches.append(batch)
    return batches


def apply_guardrail_filters(response: dict) -> List[dict]:
    """One ``{policy, type, action, confidence}`` entry (the fields present) per filter of an ApplyGuardrail
    response's assessments."""
    return [entry for assessment in (response or {}).get("assessments") or [] for entry in _filter_summaries(assessment)]


def apply_guardrail_cause(response: dict) -> str:
    """A short account of an ApplyGuardrail block: the guardrail's output text — its configured blocked message —
    then the policy, type, action and confidence of every filter; never the text a filter matched."""
    text = " ".join(str(output.get("text") or "") for output in (response or {}).get("outputs") or []
                    if isinstance(output, dict)).strip()
    cause = f"guardrail intervened: {text}".strip()
    filters = apply_guardrail_filters(response)
    if filters:
        cause += " " + _summary_json({"input": filters})
    return cause


def guarded_filters(verdicts: List[GuardedText]) -> dict:
    """The distinct filter entries behind ``verdicts`` as an assessment summary keyed by side (``{"input": [...]}``,
    the shape of assessment_summary), or ``{}`` when no filter fired."""
    distinct: List[dict] = []
    for verdict in verdicts:
        for entry in verdict.filters:
            if entry not in distinct:
                distinct.append(entry)
    return {"input": distinct} if distinct else {}


def _apply_guardrail(client, guardrail_config: dict, texts: List[str]) -> dict:
    return client.apply_guardrail(
        guardrailIdentifier=guardrail_config["guardrailIdentifier"],
        guardrailVersion=guardrail_config["guardrailVersion"],
        source=APPLY_GUARDRAIL_SOURCE,
        content=[guard_content_block(text)["guardContent"] for text in texts],
    )


def _verdicts(response: dict, texts: List[str]) -> Optional[List[GuardedText]]:
    """The verdict on each text of one call, or ``None`` when the call's outcome cannot be attributed to the texts
    of a batch — a block, or masked output that does not come one per text — and the batch is re-evaluated one
    text at a time. Blocked when a filter BLOCKED, when the guardrail intervened without a filter saying how, or
    when it reports masking but returns no usable masked text: the text as given is never embedded then."""
    filters = apply_guardrail_filters(response)
    actions = [entry.get("action") for entry in filters]
    intervened = ((response or {}).get("action") == APPLY_GUARDRAIL_ACTION_INTERVENED
                  or FILTER_ACTION_BLOCKED in actions or FILTER_ACTION_ANONYMIZED in actions)
    if not intervened:
        return [GuardedText(text=text, filters=filters) for text in texts]
    masked_types = sorted({str(entry.get("type") or "") for entry in filters
                           if entry.get("action") == FILTER_ACTION_ANONYMIZED} - {""})
    outputs = [str(output.get("text") or "") for output in (response or {}).get("outputs") or []
               if isinstance(output, dict)]
    blocked = not actions or FILTER_ACTION_BLOCKED in actions or FILTER_ACTION_ANONYMIZED not in actions
    if not blocked and len(outputs) == len(texts):
        return [GuardedText(text=output, masked_types=masked_types, filters=filters) for output in outputs]
    if len(texts) > 1:
        return None
    if blocked:
        cause = apply_guardrail_cause(response)
    else:
        cause = "guardrail intervened: masked text unavailable " + _summary_json({"input": filters})
    return [GuardedText(text=None, blocked=True, masked_types=masked_types, filters=filters, cause=cause)]


def apply_guardrail_to_texts(client, guardrail_config: dict, texts: List[str]) -> List[GuardedText]:
    """One GuardedText per text of ``texts``, in order, from ApplyGuardrail calls of apply_guardrail_batches. A
    call whose filters took no action passes its texts as given; one whose filters only anonymized returns the
    masked text of each block, one output per text; one whose outcome cannot be attributed to the texts of its
    batch — a block, or masked output that is not one per text — is re-evaluated one text at a time, so a block
    names the one text it applies to. The client's errors propagate."""
    verdicts: List[Optional[GuardedText]] = [None] * len(texts)
    for batch in apply_guardrail_batches(texts):
        batch_texts = [texts[index] for index in batch]
        results = _verdicts(_apply_guardrail(client, guardrail_config, batch_texts), batch_texts)
        if results is None:
            results = [_verdicts(_apply_guardrail(client, guardrail_config, [text]), [text])[0]
                       for text in batch_texts]
        for index, verdict in zip(batch, results):
            verdicts[index] = verdict
    return verdicts
