# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every module the container imports has to be COPY'd into each model image.

A module that exists on disk and passes the unit tests but is absent from a Dockerfile fails at the
first import inside the container -- after the GPU instance has been provisioned and the image
pulled, which is the most expensive place to discover a missing file. The unit tests cannot catch it
because they import from the working tree, where the file is present.
"""

import os
import re

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dockerfiles():
    """Every model image's Dockerfile, read off disk rather than listed.

    A hardcoded list silently excludes a NEW model's Dockerfile, which is the one most likely to be
    missing a COPY -- so the list that is supposed to catch that omission would have the same omission.
    """
    found = sorted(name for name in os.listdir(CONTAINER_DIR) if name.startswith("Dockerfile."))
    assert len(found) >= 5, f"expected one Dockerfile per model, found {found}"
    return tuple(found)


DOCKERFILES = _dockerfiles()
# Packages copied as directories rather than named files.
PACKAGE_DIRS = ("models", "vams_utils")


def _read(name):
    with open(os.path.join(CONTAINER_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _source_files():
    """Every first-party source file, including the packages copied as directories.

    Scanning only __main__.py misses a module reached through another module -- framing is imported
    by generate, not by __main__ -- and an uncopied module fails identically however it was reached.
    """
    paths = [f for f in os.listdir(CONTAINER_DIR) if f.endswith(".py")]
    for package in PACKAGE_DIRS:
        directory = os.path.join(CONTAINER_DIR, package)
        if os.path.isdir(directory):
            paths += [
                os.path.join(package, f) for f in os.listdir(directory) if f.endswith(".py")
            ]
    return paths


def _first_party_imports():
    """The container's own top-level modules imported anywhere in its own source."""
    on_disk = {
        os.path.splitext(f)[0]
        for f in os.listdir(CONTAINER_DIR)
        if f.endswith(".py") and f != "__main__.py"
    }
    names = set()
    for relative in _source_files():
        source = _read(relative)
        names |= set(re.findall(r"^import (\w+)$", source, re.M))
        names |= set(re.findall(r"^from (\w+) import", source, re.M))
    return sorted(names & on_disk)


def test_first_party_imports_are_discoverable():
    """The scan itself has to find something, or the assertions below are vacuous."""
    found = _first_party_imports()
    assert len(found) >= 5, f"expected the container's own modules, found {found}"
    assert "texturing" in found, "texturing is imported by __main__ and must be in the scan"
    assert "framing" in found, (
        "framing is imported by generate, not __main__, and must still be in the scan -- a scan "
        "that only walks __main__ would let an uncopied transitive module reach a GPU instance")


def test_every_dockerfile_copies_every_imported_module():
    missing = {}
    for dockerfile in DOCKERFILES:
        body = _read(dockerfile)
        absent = [f"{m}.py" for m in _first_party_imports() if f"{m}.py" not in body]
        if absent:
            missing[dockerfile] = absent
    assert not missing, f"modules imported by __main__ but never COPY'd: {missing}"


def test_every_dockerfile_copies_the_package_directories():
    missing = {}
    for dockerfile in DOCKERFILES:
        body = _read(dockerfile)
        absent = [p for p in PACKAGE_DIRS if f"COPY {p} " not in body]
        if absent:
            missing[dockerfile] = absent
    assert not missing, f"package directories never COPY'd: {missing}"


def test_no_instruction_carries_a_literal_escape_sequence():
    """A literal backslash-n anywhere in an instruction is a broken line continuation.

    Docker does not interpret it as a newline: on a COPY it becomes a filename that does not exist,
    and on a RUN it becomes an argument to the shell. Either way the build fails, and the failure
    reads as a missing file or a bad flag rather than as a quoting mistake. Every instruction is
    checked, not just COPY -- the same mistake reaches RUN just as easily.
    """
    literal = chr(92) + "n"
    instructions = ("COPY ", "RUN ", "ENV ", "ARG ", "ENTRYPOINT ", "CMD ", "WORKDIR ")
    offenders = {}
    for dockerfile in DOCKERFILES:
        for i, line in enumerate(_read(dockerfile).splitlines(), start=1):
            if line.startswith(instructions) and literal in line:
                offenders.setdefault(dockerfile, []).append(f"line {i}: {line.strip()[:80]}")
    assert not offenders, f"instructions with a literal escape sequence: {offenders}"


def test_config_json_is_copied_so_the_allow_list_reaches_the_container():
    """resolve_settings reads config.json by an absolute path derived from __file__."""
    for dockerfile in DOCKERFILES:
        assert "config.json" in _read(dockerfile), f"{dockerfile} does not copy config.json"
