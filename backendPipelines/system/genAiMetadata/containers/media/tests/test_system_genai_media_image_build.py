#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Build-source properties of the media Lambda image that a green `docker build` cannot show: the base image
is digest-pinned (a tag is a moving reference), every requirement is an exact pin including the transitive
closure, every third-party import in the runtime code has a pinned distribution, every runtime file the
handler imports is COPYed, the image drops root after the last COPY, the bundled ffmpeg is named through
IMAGEIO_FFMPEG_EXE, and the vendored logger is byte-identical to the pipeline copy it was taken from.

Each rule stays writable, so this is a durable guard (root CLAUDE.md Rule 13), not a temporary test."""

import ast
import hashlib
import os
import re
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(_CONTAINER_DIR, *([".."] * 5)))
_DOCKERFILE = os.path.join(_CONTAINER_DIR, "Dockerfile")
_DOCKERIGNORE = os.path.join(_CONTAINER_DIR, ".dockerignore")
_REQUIREMENTS = os.path.join(_CONTAINER_DIR, "requirements.txt")
_LOGGER_SOURCE = os.path.join(_REPO_ROOT, "backendPipelines", "preview", "3dThumbnail", "lambda", "customLogging", "logger.py")
_LOGGER_COPY = os.path.join(_CONTAINER_DIR, "customLogging", "logger.py")

# Files in the build context that are not runtime code and therefore need no COPY.
_NOT_RUNTIME = {"Dockerfile", ".dockerignore", "requirements.txt", "tests", "__pycache__", ".pytest_cache"}
# Import name -> distribution name for every third-party package the runtime code may import.
_IMPORT_TO_DISTRIBUTION = {
    "PIL": "pillow", "imageio_ffmpeg": "imageio-ffmpeg", "pypdfium2": "pypdfium2", "tinytag": "tinytag",
    "charset_normalizer": "charset-normalizer", "defusedxml": "defusedxml", "boto3": "boto3",
    "botocore": "botocore", "aws_lambda_powertools": "aws-lambda-powertools",
    "docx": "python-docx", "pptx": "python-pptx", "openpyxl": "openpyxl",
}
_LOCAL_PACKAGES = {"media_extractors", "customLogging", "handler", "videoSegments", "contentChunks",
                   "bedrockGuardrail", "vectorsearch", "segment_handler"}
_LAMBDA_DIR = os.path.join(_REPO_ROOT, "backendPipelines", "system", "genAiMetadata", "lambda")
# Modules copied into the image from lambda/ because the build context cannot reach it; each is pinned
# byte-identical to its source.
_VENDORED_FROM_LAMBDA = ("videoSegments.py", "contentChunks.py", "bedrockGuardrail.py")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._!+-]*)$")
_PINNED_BASE = re.compile(
    r"^FROM\s+--platform=linux/amd64\s+public\.ecr\.aws/lambda/python:3\.12@sha256:[0-9a-f]{64}\s*$")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dockerfile_lines():
    with open(_DOCKERFILE, encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle]


def _instruction_lines():
    """Non-comment, non-empty lines with `\\` continuation lines joined onto their instruction."""
    joined = []
    current = []
    for line in _dockerfile_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        current.append(stripped[:-1].strip() if continued else stripped)
        if continued:
            continue
        joined.append(" ".join(current))
        current = []
    if current:
        joined.append(" ".join(current))
    return joined


def _requirements():
    pins = {}
    with open(_REQUIREMENTS, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            match = _PIN.match(line)
            assert match, f"requirement is not an exact pin: {raw.strip()!r}"
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


def _runtime_python_files():
    for root, dirs, files in os.walk(_CONTAINER_DIR):
        dirs[:] = [d for d in dirs if d not in _NOT_RUNTIME]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _top_level_imports(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


@pytest.mark.unit
class TestBaseImage:
    def test_single_from_pinned_by_digest_for_amd64(self):
        froms = [line for line in _instruction_lines() if line.upper().startswith("FROM ")]
        assert len(froms) == 1
        assert _PINNED_BASE.match(froms[0]), froms[0]

    def test_the_pin_detector_rejects_a_floating_tag(self):
        # Positive control for the regex: a tag-only reference must be reported.
        assert not _PINNED_BASE.match("FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12")


@pytest.mark.unit
class TestRequirements:
    def test_every_line_is_an_exact_pin_and_the_spec_packages_are_present(self):
        pins = _requirements()
        for distribution in ("pillow", "imageio-ffmpeg", "pypdfium2", "tinytag", "charset-normalizer", "defusedxml",
                             "boto3", "botocore", "aws-lambda-powertools", "python-docx", "python-pptx", "openpyxl"):
            assert distribution in pins, distribution
        assert pins["boto3"] == pins["botocore"] == "1.43.89"

    def test_office_libraries_carry_their_transitive_closure(self):
        # python-pptx needs lxml and XlsxWriter, python-docx needs lxml, openpyxl needs et_xmlfile: the closure is
        # pinned, or a rebuild resolves them afresh.
        pins = _requirements()
        assert (pins["python-docx"], pins["python-pptx"], pins["openpyxl"]) == ("1.2.0", "1.0.2", "3.1.5")
        for distribution in ("lxml", "xlsxwriter", "et-xmlfile"):
            assert distribution in pins, distribution

    def test_every_third_party_import_in_runtime_code_is_pinned(self):
        pins = _requirements()
        stdlib = set(sys.stdlib_module_names)
        missing = set()
        for path in _runtime_python_files():
            for name in _top_level_imports(path):
                if name in stdlib or name in _LOCAL_PACKAGES:
                    continue
                distribution = _IMPORT_TO_DISTRIBUTION.get(name)
                if distribution is None or _normalise(distribution) not in pins:
                    missing.add(name)
        assert missing == set()

    def test_the_import_scan_sees_the_runtime_code(self):
        # Control: the scan above is vacuous if it walks nothing.
        files = list(_runtime_python_files())
        assert any(os.path.basename(f) == "handler.py" for f in files)
        assert sum(1 for f in files if os.sep + "media_extractors" + os.sep in f) >= 12


@pytest.mark.unit
class TestRuntimeStage:
    def test_every_runtime_entry_is_copied(self):
        copies = [line for line in _instruction_lines() if line.upper().startswith("COPY ")]
        sources = {line.split()[1] for line in copies}
        runtime_entries = {name for name in os.listdir(_CONTAINER_DIR) if name not in _NOT_RUNTIME}
        assert runtime_entries <= sources, runtime_entries - sources
        assert "requirements.txt" in sources

    def test_drops_root_after_the_last_copy_and_before_the_entrypoint(self):
        lines = _instruction_lines()
        user_at = [i for i, line in enumerate(lines) if line.upper().startswith("USER ")]
        assert len(user_at) == 1
        user = lines[user_at[0]].split()[1]
        assert user not in ("root", "0") and not user.startswith("root:")
        last_copy = max(i for i, line in enumerate(lines) if line.upper().startswith("COPY "))
        entrypoint_at = [i for i, line in enumerate(lines) if line.upper().startswith("ENTRYPOINT ")]
        assert len(entrypoint_at) == 1
        assert last_copy < user_at[0] < entrypoint_at[0]
        creation = "\n".join(line for line in lines if re.search(r"\buseradd\b", line))
        assert user in creation
        assert any(line.startswith("ENV HOME=") for line in lines)

    def test_lambda_entrypoint_and_handler(self):
        lines = _instruction_lines()
        assert 'ENTRYPOINT ["/lambda-entrypoint.sh"]' in lines
        assert 'CMD ["handler.lambda_handler"]' in lines

    def test_ffmpeg_is_named_through_the_stable_symlink(self):
        lines = _instruction_lines()
        assert "ENV IMAGEIO_FFMPEG_EXE=/usr/local/bin/ffmpeg" in lines
        assert any("ln -s" in line and "/usr/local/bin/ffmpeg" in line and "chmod a+rx" in line for line in lines)

    def test_task_root_is_world_readable(self):
        assert any("chmod -R a+rX ${LAMBDA_TASK_ROOT}" in line for line in _instruction_lines())

    def test_the_build_asserts_https_in_ffmpeg_protocols(self):
        # The Linux binary is only reachable at build time; the Dockerfile fails the build when it lacks https.
        assert any("ffmpeg -hide_banner -protocols" in line and "grep -qw https" in line
                   for line in _instruction_lines())

    def test_the_local_ffmpeg_lists_https(self):
        """The dev binary imageio-ffmpeg resolves lists https among its input protocols, so the presigned-URL
        read the segment child relies on is exercised end to end wherever the tests run."""
        import subprocess

        import imageio_ffmpeg

        output = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-protocols"],
                                capture_output=True, text=True, check=True).stdout
        inputs = output.split("Input:", 1)[1].split("Output:", 1)[0]
        assert "https" in inputs.split()


@pytest.mark.unit
class TestContextHygiene:
    def test_dockerignore_excludes_tests_and_caches(self):
        with open(_DOCKERIGNORE, encoding="utf-8") as handle:
            entries = {line.strip() for line in handle if line.strip() and not line.startswith("#")}
        assert {"tests", "__pycache__", ".pytest_cache"} <= entries

    def test_vendored_logger_is_byte_identical_to_its_source(self):
        def digest(path):
            with open(path, "rb") as handle:
                return hashlib.md5(handle.read()).hexdigest()

        assert digest(_LOGGER_COPY) == digest(_LOGGER_SOURCE)

    def test_vendored_lambda_modules_are_byte_identical_to_the_lambda_copies(self):
        # The image cannot COPY from lambda/, so the window-plan, chunking and guardrail modules are copied in;
        # a copy that drifts would plan windows, cap text or guard prompts differently from the Lambdas.
        for name in _VENDORED_FROM_LAMBDA:
            assert _digest(os.path.join(_CONTAINER_DIR, name)) == _digest(os.path.join(_LAMBDA_DIR, name)), name

    def test_vendored_embeddings_is_byte_identical_to_the_canonical_module(self):
        canonical = os.path.join(_REPO_ROOT, "backend", "backend", "common", "vectorsearch", "embeddings.py")
        assert os.path.isfile(canonical), f"{canonical} is missing: the canonical embedding adapter has not landed"
        assert _digest(os.path.join(_CONTAINER_DIR, "vectorsearch", "embeddings.py")) == _digest(canonical), (
            "vectorsearch/embeddings.py drifted from the canonical module; edit the canonical file and cp it here")
