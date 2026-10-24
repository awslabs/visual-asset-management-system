"""
The EDH add-on package holds its Lambda entry points at its root and its library modules in common/.

Every module at the package root defines a module-level lambda_handler, which the lambda builder wires
by module name; no module in common/ defines one; and no backend module or test names a library module
at the package root, as a dotted module path or as a file path.
"""
import ast
import re
from pathlib import Path

# The library modules common/ holds; none defines lambda_handler.
LIBRARY_MODULE_NAMES = [
    "edhAmiRecipeStore",
    "edhAmiRecipes",
    "edhCapacityReservations",
    "edhCatalogFields",
    "edhClient",
    "edhIdentity",
    "edhImageBuilder",
    "edhLeasePolicy",
    "edhSessionBootstrap",
    "edhSessionState",
]

# Absolute paths
REPO_ROOT = Path(__file__).resolve().parents[5]
EDH_HANDLERS_DIR = REPO_ROOT / "backend" / "backend" / "handlers" / "addon" / "edh"
EDH_COMMON_DIR = EDH_HANDLERS_DIR / "common"
BACKEND_ROOT = REPO_ROOT / "backend" / "backend"
BACKEND_TESTS = REPO_ROOT / "backend" / "tests"
INFRA_LAMBDA_BUILDER = (
    REPO_ROOT / "infra" / "lib" / "nestedStacks" / "addon" / "edhWorkstations" /
    "lambdaBuilder" / "edhWorkstationsFunctions.ts"
)

# A library module named at the package root, as a dotted module path or as a file path.
_LIBRARY_NAMES = "|".join(LIBRARY_MODULE_NAMES)
PACKAGE_ROOT_PATTERNS = [
    re.compile(rf"handlers\.addon\.edh\.({_LIBRARY_NAMES})\b"),
    re.compile(rf"handlers/addon/edh/({_LIBRARY_NAMES})\.py"),
]


def has_lambda_handler(file_path: Path) -> bool:
    """Check if a Python module defines a module-level lambda_handler function; a module that does not
    parse fails the test."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    return any(isinstance(node, ast.FunctionDef) and node.name == "lambda_handler" for node in tree.body)


def extract_wired_handler_names(builder_file: Path) -> list[str]:
    """
    Extract handler names from the lambda builder TypeScript file.

    Looks for patterns like: name: "edhWorkstationService"
    in function builders.
    """
    names = []
    with open(builder_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Match: name: "xxx" within function definitions
    pattern = r'name:\s*["\']([a-zA-Z0-9_]+)["\']'
    for match in re.finditer(pattern, content):
        name = match.group(1)
        names.append(name)

    return names


def find_package_root_references(root_dir: Path) -> list[tuple[Path, int, str]]:
    """
    Find every line under root_dir, common/ included, that names a library module at the package root.

    Returns: list of (file_path, line_number, line_content)
    """
    violations = []
    for py_file in sorted(root_dir.rglob("*.py")):
        for line_num, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pattern.search(line) for pattern in PACKAGE_ROOT_PATTERNS):
                violations.append((py_file, line_num, line.strip()))
    return violations


def test_edh_entry_points_have_lambda_handler():
    """
    Check 1: Every module directly in handlers/addon/edh/ (except __init__.py)
    defines a module-level lambda_handler.
    """
    assert EDH_HANDLERS_DIR.is_dir(), f"EDH handlers directory not found: {EDH_HANDLERS_DIR}"

    entry_points = [
        f for f in EDH_HANDLERS_DIR.glob("*.py")
        if f.is_file() and f.name != "__init__.py"
    ]

    violations = []
    for entry_point in entry_points:
        if not has_lambda_handler(entry_point):
            violations.append(entry_point.name)

    assert not violations, (
        f"Entry point modules in handlers/addon/edh/ must define lambda_handler. "
        f"Missing lambda_handler in: {violations}"
    )


def test_edh_common_modules_have_no_lambda_handler():
    """
    Check 2: handlers/addon/edh/common/ holds exactly the library modules, and none of them defines
    lambda_handler.
    """
    assert EDH_COMMON_DIR.is_dir(), f"EDH common directory not found: {EDH_COMMON_DIR}"

    common_modules = [
        f for f in EDH_COMMON_DIR.glob("*.py")
        if f.is_file() and f.name != "__init__.py"
    ]
    assert sorted(module.stem for module in common_modules) == sorted(LIBRARY_MODULE_NAMES)

    violations = []
    for module in common_modules:
        if has_lambda_handler(module):
            violations.append(module.name)

    assert not violations, (
        f"Library modules in handlers/addon/edh/common/ must NOT define lambda_handler. "
        f"Found lambda_handler in: {violations}"
    )


def test_wired_handlers_exist_as_entry_points():
    """
    Check 3: Every handlers.addon.edh.<name> the lambda builders wire exists
    as handlers/addon/edh/<name>.py, and there are at least 8 (positive control).
    """
    assert INFRA_LAMBDA_BUILDER.is_file(), f"Lambda builder file not found: {INFRA_LAMBDA_BUILDER}"

    wired_names = extract_wired_handler_names(INFRA_LAMBDA_BUILDER)

    # Positive control: at least 8 wired handlers
    assert len(wired_names) >= 8, (
        f"Positive control: Expected at least 8 wired handler names, found {len(wired_names)}: "
        f"{wired_names}"
    )

    missing = []
    for name in wired_names:
        expected_file = EDH_HANDLERS_DIR / f"{name}.py"
        if not expected_file.exists():
            missing.append(name)

    assert not missing, (
        f"Lambda builder wires handlers that don't exist as entry points: {missing}"
    )


def test_no_backend_module_or_test_names_a_library_module_at_the_package_root():
    """
    Check 4: No backend module or test, common/ included, names a library module at the package root,
    as handlers.addon.edh.<name> or handlers/addon/edh/<name>.py rather than under common.
    """
    violations = find_package_root_references(BACKEND_ROOT) + find_package_root_references(BACKEND_TESTS)

    report = [
        f"{file.relative_to(REPO_ROOT)}:{line_num}: {line_content}"
        for file, line_num, line_content in violations
    ]
    assert not violations, (
        f"Found {len(violations)} reference(s) to a library module at the package root. "
        f"Name it under common/:\n" + "\n".join(report)
    )


def test_the_package_root_patterns_match_both_forms():
    """Control for check 4: each pattern matches its form at the package root and neither matches common/.
    The samples are built from the names, so this file's own text names no module at the package root."""
    name = LIBRARY_MODULE_NAMES[0]
    root = [f"from backend.backend.handlers.addon.edh.{name} import x", f'"handlers/addon/edh/{name}.py"']
    under_common = [f"from backend.backend.handlers.addon.edh.common.{name} import x",
                    f'"handlers/addon/edh/common/{name}.py"']
    assert [[bool(p.search(line)) for p in PACKAGE_ROOT_PATTERNS] for line in root] == [[True, False], [False, True]]
    assert not any(p.search(line) for p in PACKAGE_ROOT_PATTERNS for line in under_common)
