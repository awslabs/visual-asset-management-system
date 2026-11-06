# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The CLI and the MCP server carry one version, declared in three places.

The MCP server declares its version twice — `tools/VamsMCP/pyproject.toml` (what pip installs) and
`vams_mcp/__init__.py` (what a running server reports) — and the CLI once, in `vamscli/version.py`.
The MCP files are read by path, so the guard runs with the CLI suite and imports nothing from the
MCP package.
"""

import re
from pathlib import Path

from vamscli import version

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_PYPROJECT = REPO_ROOT / "tools/VamsMCP/pyproject.toml"
MCP_PACKAGE_INIT = REPO_ROOT / "tools/VamsMCP/vams_mcp/__init__.py"

PYPROJECT_VERSION = r'^version = "([^"]+)"$'
DUNDER_VERSION = r'^__version__ = "([^"]+)"$'


def _declared(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    assert match, f"no version declaration matching {pattern!r} in {path}"
    return match.group(1)


def test_the_mcp_files_are_present():
    # Without this the reads below would raise FileNotFoundError on a checkout where the MCP moved,
    # which reads as a broken test rather than a missing counterpart.
    assert MCP_PYPROJECT.is_file(), f"MCP pyproject not found at {MCP_PYPROJECT}"
    assert MCP_PACKAGE_INIT.is_file(), f"MCP package __init__ not found at {MCP_PACKAGE_INIT}"


def test_the_two_cli_version_constants_agree():
    assert version.__version__ == version.CLI_VERSION == version.get_version()


def test_the_mcp_distribution_version_matches_the_cli():
    assert _declared(MCP_PYPROJECT, PYPROJECT_VERSION) == version.__version__


def test_the_mcp_package_version_matches_its_distribution():
    assert _declared(MCP_PACKAGE_INIT, DUNDER_VERSION) == _declared(MCP_PYPROJECT, PYPROJECT_VERSION)
