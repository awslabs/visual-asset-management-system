"""No CLI surface names a retired pipeline identifier.

The identifiers below were removed from VAMS and must never reappear in the CLI or its documentation.
A copy-paste from an old example, a stale generated help page or a merge across the removal can
reintroduce one of them, and nothing else would notice: the CLI does not validate pipeline ids
against a deployment, so a reader would simply be pointed at a pipeline that no longer exists.
This is a durable guard, not a pin on a one-time change (root CLAUDE.md Rule 13): the forbidden
spellings remain writable in every file it scans.

Each identifier is checked in every spelling the repository used (camelCase config keys, the
CDK/stack names and the hyphenated pipeline ids), across the command docstrings and the Docusaurus
CLI pages. Job names such as `label-converted-files` are user-chosen and are not identifiers, so
they are not in the set.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_SOURCES = sorted((REPO_ROOT / "tools/VamsCLI/vamscli").rglob("*.py"))
CLI_DOCS = sorted((REPO_ROOT / "documentation/docusaurus-site/docs/cli").rglob("*.md"))
RETIRED = re.compile(
    r"metadata3dLabeling|Metadata3dLabeling|useGenAiMetadata3dLabeling"
    r"|genai-metadata-3d-labeling|metadata-3d-labeling"
    r"|meshCadMetadata|MeshCadMetadata|useConversionCadMeshMetadataExtraction"
    r"|metadata-extraction-cad-mesh"
)


def test_the_scan_is_not_vacuous():
    assert len(CLI_SOURCES) > 20 and len(CLI_DOCS) > 15


def test_no_cli_source_or_doc_names_a_retired_pipeline_id():
    offenders = {
        str(path.relative_to(REPO_ROOT)): [m.group(0) for m in RETIRED.finditer(path.read_text(encoding="utf-8"))]
        for path in CLI_SOURCES + CLI_DOCS
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"retired pipeline identifiers still referenced: {offenders}"
