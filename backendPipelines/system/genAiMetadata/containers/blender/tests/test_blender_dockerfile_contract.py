# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender Lambda image's build definition.

Every property here fails in a way `docker build` reports as success: a floating base tag, a Blender
tarball that is downloaded but never checksummed, a `USER` that is root or that runs before the last
`COPY`, a `COPY . .` that ships the tests, a requirements pin that floats. `infra/test/pipelines/
containerBuildSources.test.ts` guards the Fargate images the same way; this file is the per-image guard
for a Lambda image, whose terminal instruction is `CMD` (the base image supplies the entrypoint).
"""

import io
import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
DOCKERFILE = os.path.join(_CONTAINER, "Dockerfile")
DOCKERIGNORE = os.path.join(_CONTAINER, ".dockerignore")
REQUIREMENTS = os.path.join(_CONTAINER, "requirements.txt")
LICENSE_COPY = os.path.join(_CONTAINER, "GPL3-license.txt")

# The shared libraries Blender dlopens even in --background mode, plus shadow-utils for the `useradd` that
# creates the runtime user (the Lambda base derives from AL2023-minimal, which does not guarantee it). Each
# is asserted separately so a partial regression names the missing one.
REQUIRED_DNF_PACKAGES = (
    "tar", "xz", "libGL", "libGLU", "libX11", "libXext", "libXrender", "libXi", "libXxf86vm",
    "libxkbcommon", "libSM", "libICE", "libXfixes", "libXcursor", "libXrandr", "libXinerama",
    "mesa-libGL", "mesa-dri-drivers", "alsa-lib", "pulseaudio-libs", "shadow-utils",
)

# Every module the handler imports at runtime must be COPY'd by name.
RUNTIME_FILES = ("handler.py", "meshAttributes.py", "formatUnits.py", "containerLogger.py", "executionLogKeys.py",
                 "scene_dependencies.py", "renderScene.py")
LOCK = os.path.join(_CONTAINER, "requirements.lock")
# The 3D renderer image's lock, which pins usd-core for the same referenced-file module.
THUMBNAIL_LOCK = os.path.join(_CONTAINER, *([".."] * 4), "preview", "3dThumbnail", "container", "requirements.lock")

# The importers renderScene.py dispatches to; the build must prove each exists in the installed Blender.
# Blender 5.0 removed collada_import, so probing it fails the build.
PROBED_IMPORTERS = (
    "obj_import", "stl_import", "ply_import", "usd_import", "alembic_import",
    "import_scene.fbx", "import_scene.gltf",
)
REMOVED_IMPORTERS = ("collada_import",)

# Blender's bundled OpenUSD opens the user's USD in the importer and in renderScene.py's dependency check.
# Releases before 25.11 carry OpenUSD parser advisories (GHSA-58p5-r2f6-g2cj, GHSA-grjp-54v3-c442); Blender
# 4.5 bundles 25.02 and 5.0/5.1 bundle 25.08, 5.2 bundles 26.03. pxr.Usd.GetVersion() is (0, major, minor).
OPENUSD_FLOOR_PROBE = "assert Usd.GetVersion() >= (0, 25, 11)"

# The Blender-bundled Python modules renderScene.py imports besides bpy: bmesh (mesh measurements) and the
# USD bindings (stage metersPerUnit). The build proves both import, so a Blender build without them fails here.
PROBED_MODULES = ("import bpy, bmesh", "from pxr import Usd, UsdGeom")

DIGEST_PINNED_FROM = re.compile(
    r"^FROM\s+--platform=linux/amd64\s+public\.ecr\.aws/lambda/python:3\.12@sha256:[0-9a-f]{64}\s*$",
    re.M,
)


def _text():
    return io.open(DOCKERFILE, encoding="utf-8").read()


def _lines():
    return _text().split("\n")


def _instructions(text):
    """Logical Dockerfile instructions with backslash continuations joined, so one RUN is one entry."""
    out = []
    current = None
    for raw in text.split("\n"):
        continues = re.search(r"\\\s*$", raw) is not None
        body = re.sub(r"\\\s*$", "", raw).rstrip()
        current = body if current is None else current + " " + body.strip()
        if continues:
            continue
        out.append(current)
        current = None
    if current is not None:
        out.append(current)
    return out


def _arg_defaults(text):
    return dict(re.findall(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", text, re.M))


@pytest.mark.unit
def test_base_image_is_pinned_by_digest():
    assert DIGEST_PINNED_FROM.search(_text()) is not None, "FROM must pin lambda/python:3.12 by @sha256"


@pytest.mark.unit
def test_digest_detector_rejects_a_floating_tag():
    # Positive control for the regex: a tag-only line must NOT satisfy it.
    assert DIGEST_PINNED_FROM.search("FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12\n") is None


@pytest.mark.unit
def test_blender_is_a_5_2_lts_release_with_a_64_hex_checksum():
    # 5.2 is the first LTS whose bundled OpenUSD (26.03) carries the 25.11 parser fixes.
    args = _arg_defaults(_text())
    assert re.fullmatch(r"5\.2\.\d+", args["BLENDER_VERSION"]), args.get("BLENDER_VERSION")
    assert args["BLENDER_SERIES"] == args["BLENDER_VERSION"].rsplit(".", 1)[0]
    assert re.fullmatch(r"[0-9a-f]{64}", args["BLENDER_SHA256"]), args.get("BLENDER_SHA256")
    assert args["BLENDER_DOWNLOAD_BASE"].startswith("https://")


@pytest.mark.unit
def test_tarball_download_and_checksum_share_one_run_instruction():
    """The checksum is enforced only when it runs in the same layer as the download."""
    downloads = [i for i in _instructions(_text())
                 if i.startswith("RUN") and "blender-${BLENDER_VERSION}-linux-x64.tar.xz" in i]
    assert len(downloads) == 1, downloads
    run = downloads[0]
    assert "curl -fL" in run
    assert 'echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c -' in run
    assert "tar -xJf /tmp/blender.tar.xz" in run
    assert "ln -s /opt/blender/blender /usr/local/bin/blender" in run


@pytest.mark.unit
def test_instruction_joiner_really_joins_continuations():
    # Control for the helper the assertion above depends on.
    joined = _instructions("RUN curl -fL x \\\n    && sha256sum -c -\nRUN echo done")
    assert joined == ["RUN curl -fL x && sha256sum -c -", "RUN echo done"]


@pytest.mark.unit
@pytest.mark.parametrize("package", REQUIRED_DNF_PACKAGES)
def test_required_shared_libraries_are_installed(package):
    dnf = [i for i in _instructions(_text()) if i.startswith("RUN") and "dnf install" in i]
    assert len(dnf) == 1, dnf
    assert re.search(rf"(^|\s){re.escape(package)}(\s|$)", dnf[0]), f"{package} missing from dnf install"


@pytest.mark.unit
def test_build_probes_every_importer_operator_and_bundled_module():
    probes = [i for i in _instructions(_text()) if i.startswith("RUN") and "get_rna_type()" in i]
    assert len(probes) == 1, probes
    for operator in PROBED_IMPORTERS:
        assert operator in probes[0], f"build does not probe {operator}"
    for operator in REMOVED_IMPORTERS:
        assert operator not in probes[0], f"{operator} does not exist in this Blender; probing it fails the build"
    for statement in PROBED_MODULES:
        assert statement in probes[0], f"build does not probe `{statement}`"
    assert "blender --background -noaudio --python-exit-code 1 --python-expr" in probes[0]


@pytest.mark.unit
def test_a_failing_probe_fails_the_build():
    """Blender prints a Python exception raised by --python-expr and still exits 0 (measured on 4.5.13 and
    5.2.3: `bpy.ops.wm.collada_import.get_rna_type()` on 5.2.3 raises KeyError, exit 0). Only a non-zero
    --python-exit-code placed before --python-expr makes the probe's exception the RUN's exit status."""
    probe = next(i for i in _instructions(_text()) if i.startswith("RUN") and "get_rna_type()" in i)
    match = re.search(r"--python-exit-code\s+(\d+)\s+--python-expr", probe)
    assert match, "the probe must set --python-exit-code before --python-expr"
    assert 1 <= int(match.group(1)) <= 255


@pytest.mark.unit
def test_the_build_probe_holds_blenders_openusd_at_the_advisory_floor():
    """The bundled OpenUSD is checked in the image, after the import that loads it, so a Blender release
    bundling an advisory-affected OpenUSD fails the build."""
    probe = next(i for i in _instructions(_text()) if i.startswith("RUN") and "get_rna_type()" in i)
    assert OPENUSD_FLOOR_PROBE in probe
    assert probe.index("from pxr import Usd, UsdGeom") < probe.index(OPENUSD_FLOOR_PROBE)


@pytest.mark.unit
def test_runtime_files_are_copied_by_name_and_nothing_else():
    copies = [i for i in _instructions(_text()) if i.startswith("COPY")]
    joined = " ".join(copies)
    for name in RUNTIME_FILES + ("GPL3-license.txt", "requirements.lock"):
        assert re.search(rf"\s{re.escape(name)}\s", joined), f"{name} is not COPY'd"
    assert not any(re.search(r"^COPY\s+\.\s", c) or re.search(r"^COPY\s+\./\s", c) for c in copies), \
        "a COPY of the whole context ships the tests"


@pytest.mark.unit
def test_drops_root_after_the_last_copy_and_ends_with_the_handler_cmd():
    lines = _lines()
    user_at = next((i for i, line in enumerate(lines) if re.match(r"^USER\s+\S", line)), -1)
    assert user_at > -1, "no USER instruction"
    name = lines[user_at].split()[1].split(":")[0]
    assert name not in ("root", "0")
    last_copy_at = max(i for i, line in enumerate(lines) if re.match(r"^COPY\s", line))
    assert user_at > last_copy_at, "USER must follow the last COPY"
    creation = "\n".join(line for line in lines if re.search(r"\b(useradd|adduser)\b", line))
    assert name in creation, "the runtime user must be created in this (only) stage"
    # shadow-utils installs useradd under /usr/sbin, which the Lambda base image's PATH omits: a bare `useradd`
    # fails the build with exit 127 (measured against the pinned base), so the absolute path is required.
    assert "/usr/sbin/useradd" in creation, "useradd must be called by absolute path (/usr/sbin is not on PATH)"
    assert re.search(r"^ENV\s+HOME=", _text(), re.M), "a writable HOME must be declared"
    cmd_at = next((i for i, line in enumerate(lines) if line.startswith("CMD")), -1)
    assert lines[cmd_at].strip() == 'CMD ["handler.lambda_handler"]'
    assert cmd_at > user_at
    assert not any(line.startswith("ENTRYPOINT") for line in lines), "the Lambda base image supplies the entrypoint"


@pytest.mark.unit
def test_dockerignore_excludes_tests_and_caches():
    text = io.open(DOCKERIGNORE, encoding="utf-8").read().split("\n")
    for pattern in ("tests/", "__pycache__/", ".pytest_cache/", "*.pyc"):
        assert pattern in text, f".dockerignore lacks {pattern}"


@pytest.mark.unit
def test_requirements_are_exact_pins():
    lines = [line.strip() for line in io.open(REQUIREMENTS, encoding="utf-8")
             if line.strip() and not line.startswith("#")]
    names = {}
    for line in lines:
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\d+(?:\.\d+)+)", line)
        assert m, f"not an exact pin: {line!r}"
        names[m.group(1).lower()] = m.group(2)
    assert set(names) == {"boto3", "botocore", "numpy", "trimesh", "aws-lambda-powertools", "usd-core"}
    assert names["boto3"] == names["botocore"] == "1.43.89"
    # The handler reads USD layers with the OpenUSD release the 3D renderer image pins for the same module.
    # Releases before 25.11 carry OpenUSD parser advisories (GHSA-58p5-r2f6-g2cj, GHSA-grjp-54v3-c442).
    usd = tuple(int(part) for part in names["usd-core"].split("."))
    assert usd >= (25, 11), names["usd-core"]
    assert names["usd-core"] == _locked(THUMBNAIL_LOCK)["usd-core"][0]


@pytest.mark.unit
def test_the_lock_pins_the_requirements_usd_core():
    assert _locked(LOCK)["usd-core"][0] == "26.8"
    assert _locked(LOCK)["usd-core"][1] >= 1


def _locked(path):
    """{package: version} of a hash-locked requirements file, with each package's hash count."""
    text = io.open(path, encoding="utf-8").read()
    entries = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", text, re.M)
    return {name.lower(): (version, block.count("--hash=")) for name, version, block in entries}


@pytest.mark.unit
def test_the_image_installs_the_hash_locked_requirements():
    """The pip install reads the lock with --require-hashes, so a rebuild of one commit installs the same
    wheels; the lock's header names the command that regenerates it."""
    installs = [i for i in _instructions(_text()) if i.startswith("RUN") and "pip install" in i]
    assert len(installs) == 1, installs
    assert "--require-hashes" in installs[0] and "requirements.lock" in installs[0]
    assert "requirements.txt" not in installs[0].split("pip install", 1)[1]
    assert 'python -c "from pxr import Sdf, UsdUtils' in installs[0], "the build proves usd-core imports"
    header = io.open(LOCK, encoding="utf-8").read(400)
    assert "uv pip compile requirements.txt --generate-hashes --python-version 3.12" in header


@pytest.mark.unit
def test_the_lock_pins_every_direct_dependency_at_its_requirements_version():
    locked = _locked(LOCK)
    assert locked, "no entries parsed from requirements.lock"
    for line in io.open(REQUIREMENTS, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        assert locked[name.lower()][0] == version, f"{name}: lock {locked.get(name.lower())} vs {version}"
    assert all(hashes >= 1 for _, hashes in locked.values())


@pytest.mark.unit
def test_gpl_license_text_travels_with_the_blender_script():
    assert os.path.isfile(LICENSE_COPY)
    head = io.open(LICENSE_COPY, encoding="utf-8").read(400)
    assert "GNU GENERAL PUBLIC LICENSE" in head
    assert "Version 3" in head
