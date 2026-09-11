"""The three dependency manifests must agree.

`requirements.txt`, `pyproject.toml [project.dependencies]` and `setup.py`'s `install_requires` all
declare the same runtime dependency set, and `requirements.txt` says in its own header that it
matches the others. Nothing enforced it: `requirements.txt` carried `click>=8.0.0` while both
authoritative manifests required `>=8.3.1`, and `botocore` (a real direct import, in
`vamscli/auth/cognito.py`) appeared in `setup.py` alone. A developer or CI job bootstrapping with
`pip install -r requirements.txt` — the documented dev path — could therefore resolve a Click three
minor versions older than `pip install -e .` requires, and any behaviour that differs between them
reproduces in one environment and not the other with nothing pointing at the cause.

`setup.py` is parsed with `ast` rather than executed: importing it runs setuptools machinery, and a
`literal_eval` of the list it assigns is both cheaper and unable to have side effects.

Ordering is deliberately NOT compared — the specifier per package name is the contract.
"""

import ast
import re
from pathlib import Path

import pytest

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - the project requires >=3.12
    tomllib = None

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _PACKAGE_ROOT / "requirements.txt"
_PYPROJECT = _PACKAGE_ROOT / "pyproject.toml"
_SETUP_PY = _PACKAGE_ROOT / "setup.py"

# `name>=1.2.3`, `name[extra]>=1.2.3`, `name` — enough for this project's declarations. A form this
# does not recognize raises rather than being silently dropped, so the check cannot go quiet.
_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?P<extras>\[[^\]]*\])?(?P<spec>.*)$")


def _normalize(requirements):
    """{canonical name: specifier} for a list of requirement strings."""
    parsed = {}
    for raw in requirements:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        match = _REQUIREMENT.match(text)
        assert match, f"unrecognized requirement line: {raw!r}"
        # PEP 503 canonicalization, so `Foo_Bar` and `foo-bar` compare equal.
        name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        parsed[name] = match.group("spec").replace(" ", "")
    return parsed


def _requirements_txt():
    return _normalize(_REQUIREMENTS.read_text(encoding="utf-8").splitlines())


def _pyproject_dependencies():
    assert tomllib is not None, "tomllib is required to parse pyproject.toml"
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return _normalize(data["project"]["dependencies"])


def _setup_py_install_requires():
    """`install_requires=[...]` from setup.py, read statically."""
    tree = ast.parse(_SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "install_requires":
                return _normalize(ast.literal_eval(keyword.value))
    raise AssertionError("no install_requires= keyword found in setup.py")


def test_every_manifest_was_actually_parsed():
    """Control: a parser that silently resolves nothing would report every comparison clean."""
    for name, parsed in (
        ("requirements.txt", _requirements_txt()),
        ("pyproject.toml", _pyproject_dependencies()),
        ("setup.py", _setup_py_install_requires()),
    ):
        assert len(parsed) >= 10, f"{name} yielded only {len(parsed)} requirements: {parsed}"
        # A package every manifest is known to declare, so a regex that matched nothing useful fails.
        assert "click" in parsed, f"{name} yielded no click requirement: {parsed}"


def test_requirements_txt_matches_pyproject():
    assert _requirements_txt() == _pyproject_dependencies()


def test_setup_py_matches_pyproject():
    assert _setup_py_install_requires() == _pyproject_dependencies()


def test_botocore_is_declared_because_it_is_imported_directly():
    """`vamscli/auth/cognito.py` imports `botocore.exceptions`, so it is a direct dependency.

    It reached only `setup.py`, which meant `pip install -r requirements.txt` never guaranteed it —
    it happened to be present as a boto3 transitive.
    """
    source = (_PACKAGE_ROOT / "vamscli" / "auth" / "cognito.py").read_text(encoding="utf-8")
    assert "from botocore" in source or "import botocore" in source, (
        "botocore is no longer imported directly; drop it from the manifests instead of pinning it")
    for parsed in (_requirements_txt(), _pyproject_dependencies(), _setup_py_install_requires()):
        assert "botocore" in parsed


@pytest.mark.parametrize("manifest", ["requirements.txt", "pyproject.toml", "setup.py"])
def test_click_floor_is_the_same_everywhere(manifest):
    """The specific drift that was live: `requirements.txt` allowed Click 8.0.x."""
    parsed = {
        "requirements.txt": _requirements_txt,
        "pyproject.toml": _pyproject_dependencies,
        "setup.py": _setup_py_install_requires,
    }[manifest]()
    assert parsed["click"] == ">=8.3.1"
