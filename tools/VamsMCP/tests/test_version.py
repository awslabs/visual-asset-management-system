"""The importable version must match the package metadata.

`vams_mcp.__version__` is what a diagnostic reports for the running server, and `pyproject.toml`
is what pip installs. They are two files with no link between them, so a roll that touches one and
not the other leaves the running server reporting a version it is not (root CLAUDE.md Pattern 7
rule 6 rolls them together with the CLI).
"""

import tomllib
from pathlib import Path

import vams_mcp


def test_importable_version_matches_pyproject():
    pyproject = Path(vams_mcp.__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]
    assert vams_mcp.__version__ == declared
