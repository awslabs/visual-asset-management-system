# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shipped core patches keep LF line endings on checkout.

`git apply` of a CRLF patch fails on an LF source, so a CRLF copy of a ledger-template patch stops every build
that lists it with exit 20. The VAMS repository root checks `*.patch` out as CRLF (`.gitattributes`); the
package's own `.gitattributes` overrides that for its files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2]
PATCHES = sorted((PKG / "ledger-template" / "core-patches").glob("*.patch"))


def test_the_package_pins_patch_files_to_lf():
    assert "*.patch text eol=lf" in (PKG / ".gitattributes").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("patch", PATCHES, ids=[p.name for p in PATCHES])
def test_a_shipped_patch_has_lf_line_endings(patch):
    assert PATCHES, "no shipped core patch found"
    assert b"\r\n" not in patch.read_bytes(), f"{patch.name} has CRLF line endings"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_checks_the_patches_out_as_lf_inside_a_repository():
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=PKG, capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("the package is not inside a git work tree")
    out = subprocess.run(["git", "check-attr", "eol", "--", *[str(p) for p in PATCHES]], cwd=PKG,
                         capture_output=True, text=True, check=True).stdout
    assert out.count(": eol: lf") == len(PATCHES), out
