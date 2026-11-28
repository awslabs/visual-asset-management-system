# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workload-profile rules that config_schema.json cannot express.

A template's rendered configuration may carry a `profile` block: prompts that replace the built-in
ones per stage, stage knobs bounded by the built-in caps, and extra artifacts written from the
finalize output. The schema checks the shape; this module checks the rest, and is a byte-identical
copy in the container package and in the constructPipeline Lambda, so the Lambda refuses a bad
profile before any AWS Batch job starts and the container applies the same rules to its definition.
It imports only the standard library for that reason.

Every message is one operator-readable sentence naming the offending value.
"""

import re

# The prompts a profile may configure, keyed like the built-in prompts/<name>.md files.
PROMPT_STAGES = ("system_boundary", "window_extraction", "vision_verification", "finalize")

# The `[[TOKEN]]` inputs each configured prompt may place. They are the values the built-in prompt of
# the same stage receives as `{{TOKEN}}`; a configured prompt uses `[[...]]` because VAMS substitutes
# `{{tag}}` in a template body before the container sees it. A configured system_boundary is appended
# to the fixed built-in boundary and takes no inputs.
PROMPT_INPUTS = {
    "system_boundary": (),
    "window_extraction": (
        "WINDOW_INDEX", "WINDOW_COUNT", "WINDOW_START_HMS", "WINDOW_END_HMS",
        "PART_TYPES", "MATERIAL_TYPES", "MAX_KEY_FRAMES", "TRANSCRIPT",
        "ADDITIONAL_INSTRUCTIONS",
    ),
    "vision_verification": (
        "WINDOW_STEPS", "WINDOW_COMPONENTS", "FRAME_LIST", "ADDITIONAL_INSTRUCTIONS",
    ),
    "finalize": (
        "PRODUCT_NAME", "PART_LEVEL_BASE", "PART_TYPES_WITH_DESCRIPTIONS", "MATERIAL_TYPES",
        "PRIMARY_TECHNIQUES", "MERGED_STEPS", "MERGED_COMPONENTS", "VISION_RESULTS",
        "ADDITIONAL_INSTRUCTIONS",
    ),
}

# The input a configured prompt of that stage must place: without it the model sees no evidence.
REQUIRED_PROMPT_INPUTS = {
    "window_extraction": ("TRANSCRIPT",),
    "finalize": ("MERGED_STEPS",),
}

# Artifact names whose `<name>.json` / `<name>.md` would overwrite a built-in deliverable. The other
# built-in stems carry a hyphen (video-timeline, lab-summary, analysis-report, keyframe-*), which the
# artifact name pattern excludes.
RESERVED_ARTIFACT_NAMES = ("transcript", "sop", "bom", "frames")

ARTIFACT_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
CONFIGURED_TOKEN = re.compile(r"\[\[([A-Za-z0-9_]+)\]\]")
UNRESOLVED_VAMS_TAG = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

MODEL_SETTING = "app.pipelines.useGenAiVideoSopBom.bedrockModelId"


def _normalized(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def model_key_paths(node, path=""):
    """Dotted paths of every key, at any depth, that names a model (modelId, model_id, bedrockModelId...)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if _normalized(key).endswith("modelid"):
                found.append(child)
            found.extend(model_key_paths(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(model_key_paths(value, f"{path}[{index}]"))
    return found


def model_key_errors(config):
    """One sentence per model key. One model per deployment: a template or profile never picks it, and
    the job role's Amazon Bedrock grant is scoped to the configured model at synth."""
    return [
        f"{path} names a model; this pipeline uses one Amazon Bedrock model per deployment, set in "
        f"{MODEL_SETTING}, so a template or profile may not set one."
        for path in model_key_paths(config)
    ]


def _token_list(tokens):
    return ", ".join(f"[[{token}]]" for token in tokens) if tokens else "none"


def _prompt_errors(stage, text):
    errors = []
    allowed = PROMPT_INPUTS[stage]
    used = CONFIGURED_TOKEN.findall(text)
    for token in sorted(set(used) - set(allowed)):
        errors.append(
            f"profile.prompts.{stage} places [[{token}]], which the {stage} stage does not supply; "
            f"it supplies {_token_list(allowed)}.")
    for token in REQUIRED_PROMPT_INPUTS.get(stage, ()):
        if token not in used:
            errors.append(
                f"profile.prompts.{stage} never places [[{token}]]; a configured {stage} prompt must "
                f"carry that input.")
    for tag in sorted(set(UNRESOLVED_VAMS_TAG.findall(text))):
        errors.append(
            f"profile.prompts.{stage} contains {{{{{tag}}}}}, which VAMS left unresolved; a configured "
            f"prompt places a stage input as [[{tag}]] and an operator value through a declared tag.")
    return errors


def _artifact_errors(artifacts):
    errors = []
    seen = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        if not isinstance(name, str):
            continue
        where = f"profile.artifacts[{index}].name"
        if not ARTIFACT_NAME.fullmatch(name):
            errors.append(
                f"{where} is {name!r}; an artifact name is a lowercase letter followed by at most 63 "
                f"lowercase letters, digits or underscores.")
        elif name in RESERVED_ARTIFACT_NAMES:
            errors.append(
                f"{where} is {name!r}, which would overwrite the built-in {name} deliverable; reserved "
                f"names are {', '.join(RESERVED_ARTIFACT_NAMES)}.")
        if name in seen:
            errors.append(f"{where} is {name!r} again; each artifact needs its own name.")
        seen.add(name)
    return errors


def profile_errors(config):
    """Sentences for every profile rule `config` breaks; empty when it has no profile or conforms.
    Run it on a configuration that already passed config_schema.json."""
    if not isinstance(config, dict):
        return []
    profile = config.get("profile")
    if profile is None:
        return []
    if not isinstance(profile, dict):
        return []
    errors = []
    if config.get("mode") != "full":
        errors.append(
            f"profile is set but mode is {config.get('mode')!r}; a profile applies only to the full "
            f"mode's Amazon Bedrock stages.")
    prompts = profile.get("prompts") or {}
    if isinstance(prompts, dict):
        for stage in PROMPT_STAGES:
            text = prompts.get(stage)
            if isinstance(text, str):
                errors.extend(_prompt_errors(stage, text))
    artifacts = profile.get("artifacts") or []
    if isinstance(artifacts, list):
        errors.extend(_artifact_errors(artifacts))
    return errors
