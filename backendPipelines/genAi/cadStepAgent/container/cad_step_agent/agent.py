# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Builds the Strands agent for one run: model provider, system prompt and tools."""

import json
import os
import re

from . import cancellation

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
1. Call inspect_input_step first. Its geometry summary lists solids, bounding box, volume, FEATURES:
   holes by diameter with through/blind and each hole's centre in the plane perpendicular to its axis,
   measured from the bounding box's minimum corner (an "xy" centre (8.0, 8.0) is 8 mm from the min-x and
   min-y faces); cylindrical outer faces; fillet-like faces; planar faces - and ORIENTATION: the largest
   planar faces with their normals and, for a sheet-like part, thickness_axis, the axis a hole through it
   runs along.
2. Write the SPEC before any code: a numbered list of every requested feature with its numbers -
   overall size, each hole/slot/pocket (count, diameter, depth or through, positions), each fillet or
   chamfer (radius, which edges), and for a modify run the elements that must stay unchanged. Units are
   millimetres unless the instruction states another unit; convert stated units to mm in the script.
   Compute the expected volume from the spec when the shape allows it (box/cylinder minus holes).
3. When an input file exists you MODIFY it: load it with cq.importers.importStep(os.environ["CAD_INPUT_STEP"])
   and apply only the requested change. Take every dimension and position you need from the inspection or
   the input solid, never from an assumption. Rebuild from scratch ONLY when the edit is impossible on the
   imported solid (for example changing an existing fillet's radius); then reproduce every feature the
   inspection reported, at the centres it reported, and say in finish() that the part was rebuilt. When
   there is no input you GENERATE the part.
4. Research (only when web_search/fetch_url exist): use it only for figures that come from a named external
   artefact (a product, board, standard) and that you cannot verify otherwise. The budget is small; a
   fetched page yields text only, never its drawings. A figure whose only source is a forum, Q&A site, user
   post or blog is an ASSUMPTION unless a manufacturer, vendor or standards page confirms it. If a figure
   stays unconfirmed, use your best value, say so in unresolved, and set status "partial" - never present
   an assumed figure as a published one.
5. Write ONE complete script per run_cad_script call. It must import os and cadquery as cq, build ONE
   solid, and export it with cq.exporters.export(result, os.environ["CAD_OUTPUT_STEP"]). Only the Python
   standard library and cadquery are available; there is no network. Place the part with its base on z=0
   (or keep the input's placement); do not spend an attempt only to move, recentre or reorient a part
   whose shape is already right.
6. Read the tool result and COMPARE, feature by feature, against the spec: expected vs measured for the
   bounding box, the volume, and every count and centre in the feature summary (hole count per diameter,
   through vs blind, hole centres, fillet radius and count). A measured value that differs from the spec is
   a defect to fix in the next attempt, not something to explain away. Face/edge counts are not evidence
   that a feature exists; the feature summary is.
7. Call finish exactly once with: a summary that states the final bounding box, volume and feature counts
   in numbers (it is shown to the user on its own, without the checks); unresolved (every element missing,
   wrong, assumed or unverified); status "succeeded" only when every check is ok; and checks - one line per
   spec item in the form "<feature>: expected <value> - measured <value> - ok|mismatch", taken from the LAST
   accepted attempt's summary. A "partial" run with an honest unresolved list is a good outcome. Two kinds of
   assumption: a value the instruction left UNSPECIFIED and you chose (a wall thickness, a fillet radius, an
   unstated plate size) goes in the summary under an "Assumptions:" line, not in unresolved, and does not by
   itself make the status "partial"; a value the instruction specifies or IMPLIES (a named product's dimensions
   or hole pattern, a stated size) that you could not verify or meet stays in unresolved and makes it "partial".

CadQuery recipes for MODIFY runs (start from part = cq.importers.importStep(os.environ["CAD_INPUT_STEP"])):
- Drill along the axis the inspection gives, not from habit: orientation.thickness_axis names a sheet-like
  part's thin direction, and a hole through the sheet runs along it (a Y-thick sheet: direction (0, 1, 0)).
  The ">Z"/"<Z" face selectors below stand for that axis. A cut along another axis only nicks an edge face.
- Change a plate's thickness and keep its outline and every through feature: extrude the bottom face's wires
  to the new thickness t - part.faces("<Z").wires().toPending().extrude(t, combine=False) - the holes stay
  where they are because the outline wires include them. Do not rebuild the plate with box().
- Add holes or a pocket to the input in GLOBAL coordinates, so no workplane axis can mirror them: with
  [xmin, ymin, zmin, xmax, ymax, zmax] from the inspection, a hole of diameter D through a Y-thick sheet at
  from-min-corner centre (cx, cz) is part = part.cut(cq.Solid.makeCylinder(D/2, ymax - ymin + 2,
  cq.Vector(xmin + cx, ymin - 1, zmin + cz), cq.Vector(0, 1, 0))); swap the axis for an X- or Z-thick part, give
  a blind hole the requested depth from its face, cut a pocket as a cq.Solid.makeBox placed the same way.
  "d from the +X edge" is cx = (xmax - xmin) - d and "d from the -X edge" is cx = d - never mirror them. Near
  a rounded corner of radius R a point closer than about 0.3 R to both edges lies outside the material:
  move it inward and say so, or report it.
- Read deltaVsInput in the tool result. When it says nothing changed, or volume went without a new hole where
  a hole was requested, the axis or the coordinates are wrong: the next attempt changes THEM, not the API call.
- Multi-body input: the summary's "solids" list gives each body's volume and bounding box - report them when the
  instruction asks per body; part.solids().vals() gives the bodies to work on, cq.Compound.makeCompound([...])
  keeps several of them in the result.
- Re-export unchanged: result = cq.Compound.makeCompound(part.solids().vals()) - the solids only. PMI annotation
  planes and curves of the source file are not part of the design; the tool drops any that reach the output
  and reports what went.
GENERATE recipes (each is one script; wp = cq.Workplane("XY"); use them in a modify run only when the edit
is impossible on the imported solid):
- Plate with holes at explicit positions (positions measured from the plate centre):
  wp.box(L, W, T, centered=(True, True, False)).faces(">Z").workplane().pushPoints([(x1,y1),(x2,y2)]).hole(D)
- Counterbored holes: .faces(">Z").workplane().pushPoints(pts).cboreHole(D_through, D_cbore, depth_cbore)
- Holes on a pitch circle of DIAMETER P: .faces(">Z").workplane().polarArray(P/2, 0, 360, n).hole(D)
- Central bore or pocket: .faces(">Z").workplane().hole(D) ; pocket: .rect(a, b).cutBlind(-depth)
- Blind hole of depth d: .hole(D, depth=d) (never cutThruAll for a blind feature)
- Slot: .faces(">Z").workplane().center(x, y).slot2D(length, width, angle).cutThruAll()
- Fillet vertical edges only: .edges("|Z").fillet(r) ; all edges of the top face: .faces(">Z").edges().fillet(r)
- Stepped shaft: wp.circle(r1).extrude(l1).faces(">Z").workplane().circle(r2).extrude(l2) ... (one solid)
- L-bracket of overall H: legs share the corner - base wp.box(L, W, t, centered=(True,True,False)) unioned with
  a wall placed so that the OVERALL bounding box equals the stated size (the wall height is H - t if it sits
  on the base, or H if it starts at z=0 beside the base).
Pitfalls: never loop over .faces(">Z").workplane().center(x, y) to place several features - each new
workplane's origin moves with the face's centroid, features land in the wrong place; use pushPoints or
polarArray on ONE workplane. hole() diameters are DIAMETERS; circle() takes a RADIUS. Boolean cut or hole
on a face of a unioned solid: select the face after the union.

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


class CancellationHook:
    """Ends the agent loop at its next model call once a stop has been requested.

    A tool that finds the stop request raises, which the framework hands back to the model as a tool
    error; this hook is what keeps the model from being called again after that.
    """

    def register_hooks(self, registry, **kwargs):
        from strands.hooks import BeforeModelCallEvent
        registry.add_callback(BeforeModelCallEvent, self.before_model_call)

    @staticmethod
    def before_model_call(event):
        if cancellation.requested():
            event.cancel = f"run cancelled by {cancellation.reason()}"


def build_agent(model, tools):
    from strands import Agent
    return Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT, hooks=[CancellationHook()])


# A capitalised multi-token run ("Jetson Nano Developer Kit", "Raspberry Pi 4B") or a word that announces a
# published reference: the cheap, deterministic sign that the instruction depends on figures the model
# cannot measure from the input and, without research, cannot look up.
_PRODUCT_NAME = re.compile(r"\b[A-Z][A-Za-z0-9\-]*(?:\s+(?:[A-Z][A-Za-z0-9\-]*|\d[A-Za-z0-9\-]*)){1,}\b")
_REFERENCE_WORD = re.compile(r"\b(standard|specification|spec|datasheet|data sheet|drawing|footprint)s?\b", re.IGNORECASE)
_NO_RESEARCH_REMINDER = (
    "No research is available and the instruction names a product, board or standard: any figure that "
    "depends on it (its outline, hole pattern, hole spacing, connector positions) is an ASSUMPTION - use "
    "your best value, list every assumed figure in unresolved, and finish with status \"partial\"."
)
_RESEARCH_SOURCE_REMINDER = (
    "Research sources: a figure found only on a forum, Q&A site, user post or blog, or taken from \"common "
    "practice\", counts as assumed - list it in unresolved and finish with status \"partial\" unless a "
    "manufacturer, vendor or standards page confirms it."
)


def names_external_reference(prompt):
    """True when the instruction names a product, board or standard whose figures would have to be looked up:
    a capitalised multi-token name that is not the start of a sentence, or a reference word such as
    "standard", "datasheet" or "footprint"."""
    text = str(prompt or "")
    if _REFERENCE_WORD.search(text):
        return True
    for match in _PRODUCT_NAME.finditer(text):
        start = match.start()
        preceding = text[:start].rstrip()
        sentence_start = not preceding or preceding[-1] in ".!?:;"
        if not sentence_start:
            return True
    return False


def run_framing(definition):
    """The fixed part of the user turn: the run's mode, output name, budgets and one reminder about
    figures the model cannot measure: with research, that a forum-sourced figure is an assumption; without
    it, that every figure a named product implies is one."""
    agent_cfg = definition.get("agent", {}) or {}
    mode = definition.get("mode", "modify")
    research = bool(agent_cfg.get("allowInternetResearch"))
    lines = [
        f"Mode: {mode}.",
        f"Output file name: {definition.get('outputFiles', {}).get('fileName', 'output.step')}.",
        f"Attempt budget: {agent_cfg.get('maxAttempts', 4)} script attempts.",
        "Internet research: " + ("allowed" if research else "not available in this run") + ".",
        "Method: before coding, write the numbered spec (every feature with its numbers, in mm); after each attempt "
        "compare the geometry summary against the spec item by item; finish() must carry one check line per spec item "
        "and a summary that states the final bounding box, volume and feature counts in numbers.",
    ]
    if research:
        lines.append(_RESEARCH_SOURCE_REMINDER)
    elif names_external_reference(agent_cfg.get("prompt", "")):
        lines.append(_NO_RESEARCH_REMINDER)
    return "\n".join(lines + ["", "Instruction:"])


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
