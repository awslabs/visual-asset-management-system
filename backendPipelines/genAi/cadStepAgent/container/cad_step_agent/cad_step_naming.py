# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Output file-name resolution for the GenAI CAD STEP agent pipeline.

Pure functions only: the constructPipeline Lambda calls ``resolve_output_filename`` to reject a bad
name BEFORE any compute starts, and the container calls it again to place the file. The two copies of
this module (``lambda/`` and ``container/cad_step_agent/``) are byte-identical and asserted so.

Rules (spec section 7):

1. An override is sanitized: directory separators, control characters and leading dots are removed;
   an override that sanitizes to nothing when it was non-empty is an error.
2. Extension: a modify run keeps the INPUT file's extension; a generate run uses ``.step`` unless the
   override itself carries ``.stp`` or ``.step``. Any other extension on an override is replaced.
3. Base name: the override without its extension when set; else the input base name (modify); else a
   slug of the design name, or of the first six words of the prompt, or ``cad-agent``, followed by a
   ``-YYYYMMDD-HHMMSS`` timestamp (generate).
4. The prefix is prepended AFTER step 3, so it stacks on an override.
5. The result must be a plain file name VAMS accepts.
"""

import datetime
import posixpath
import re

MODE_MODIFY = "modify"
MODE_GENERATE = "generate"
MODES = (MODE_MODIFY, MODE_GENERATE)

STEP_EXTENSIONS = (".stp", ".step")
DEFAULT_GENERATE_EXTENSION = ".step"
DEFAULT_BASE_NAME = "cad-agent"

# VAMS's file-name validator (common/validators.py filename_pattern): no <>:"/\|?*, no trailing dot
# or whitespace, word characters, whitespace, dots, commas, apostrophes and hyphens only.
_FILE_NAME_PATTERN = re.compile(r"^(?!.*[<>:\"\/\\|?*])(?!.*[.\s]$)[\w\s.,'-]{1,254}[^.\s]$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
MAX_FILE_NAME_LENGTH = 255
MAX_SLUG_LENGTH = 48
PROMPT_SLUG_WORDS = 6


class OutputNameError(ValueError):
    """A caller-supplied name or prefix cannot be turned into an acceptable file name."""


def sanitize_component(value):
    """A file-name component with directory separators, control characters and leading dots removed.

    Empty input yields an empty string; the caller decides whether that is an error.
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    text = _CONTROL_CHARS.sub("", text).strip()
    text = text.lstrip(".")
    return text


def slugify(value, max_length=MAX_SLUG_LENGTH):
    """A lower-case, hyphen-separated slug of the text, bounded in length; empty when nothing survives."""
    slug = _SLUG_STRIP.sub("-", str(value or "").lower()).strip("-")
    return slug[:max_length].rstrip("-")


def prompt_slug(prompt, words=PROMPT_SLUG_WORDS):
    """A slug of the first few words of a prompt, for a generated file's default name."""
    tokens = [t for t in re.split(r"\s+", str(prompt or "").strip()) if t]
    return slugify(" ".join(tokens[:words]))


def split_extension(file_name):
    """(base, extension) where extension is lower-cased and includes the dot, or '' when absent."""
    base, ext = posixpath.splitext(file_name)
    return base, ext.lower()


def resolve_output_filename(mode, input_file_name="", output_filename="", output_filename_prefix="",
                            design_name="", prompt="", now=None):
    """The file name the run writes, per the rules in the module docstring.

    Raises ``OutputNameError`` when the mode is unknown, a modify run names no input file, an
    override sanitizes to nothing, or the resolved name is not a plain acceptable file name.
    """
    if mode not in MODES:
        raise OutputNameError(f"Unknown mode '{mode}'; expected one of {', '.join(MODES)}")

    raw_override = "" if output_filename is None else str(output_filename)
    override = sanitize_component(raw_override)
    if raw_override.strip() and not override:
        raise OutputNameError("outputFilename contains no usable file-name characters")

    raw_prefix = "" if output_filename_prefix is None else str(output_filename_prefix)
    prefix = sanitize_component(raw_prefix)
    if raw_prefix.strip() and not prefix:
        raise OutputNameError("outputFilenamePrefix contains no usable file-name characters")

    input_name = sanitize_component(input_file_name)
    if mode == MODE_MODIFY and not input_name:
        raise OutputNameError("A modify run requires an input file name")

    # Extension
    override_base, override_ext = split_extension(override) if override else ("", "")
    if mode == MODE_MODIFY:
        _, input_ext = split_extension(input_name)
        extension = input_ext if input_ext in STEP_EXTENSIONS else DEFAULT_GENERATE_EXTENSION
    else:
        extension = override_ext if override_ext in STEP_EXTENSIONS else DEFAULT_GENERATE_EXTENSION

    # Base name
    if override:
        base = override_base if override_ext else override
    elif mode == MODE_MODIFY:
        base, _ = split_extension(input_name)
    else:
        stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%d-%H%M%S")
        slug = slugify(design_name) or prompt_slug(prompt) or DEFAULT_BASE_NAME
        base = f"{slug}-{stamp}"

    resolved = f"{prefix}{base}{extension}"
    if len(resolved) > MAX_FILE_NAME_LENGTH or not _FILE_NAME_PATTERN.match(resolved):
        raise OutputNameError("Resolved output file name is not an acceptable file name")
    return resolved


def relative_subdir_of(input_key, asset_id):
    """The asset-relative directory ('sub/dir/' or '') of an input S3 key, sliced at the threaded
    assetId segment. '' when the asset id is not a segment of the key."""
    parts = [p for p in str(input_key or "").split("/") if p]
    if asset_id and asset_id in parts:
        inner = parts[parts.index(asset_id) + 1:-1]
        return "/".join(inner) + "/" if inner else ""
    return ""
