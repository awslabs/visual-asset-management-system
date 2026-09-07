# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every boto3 client under `backendPipelines/` carries the retry configuration (owner Q91, option A).

`S2-BACKEND-155` swept `backend/backend` to 59 -> 0 bare clients, and a ratchet holds it there. That
ratchet is scoped to `backend/backend` ONLY, so its green "0" was read as repo-wide while this tree
carried **137 bare clients across 68 files, 0 configured** -- measured, not estimated. A pipeline Lambda
or container runs against Step Functions, Amazon S3, and EventBridge for the length of a job (hours, on
the GPU pipelines), so it arguably needs the client-side rate limiting more than an API handler does.

This file is the widened ratchet. Two properties, because the first alone can be satisfied wrongly:

  * no bare `boto3.client` / `boto3.resource` call, and
  * the retry constant is DEFINED ABOVE its first use in each module. A constant placed after the
    client that references it raises `NameError` at import, which in a Lambda is a cold-start 500 on
    every request -- and it is invisible to a check that only counts `config=` keywords. One module in
    this tree interleaves imports with code and hit exactly that during the sweep.

Deliberately NOT asserting the retry VALUES: `backend/tests/common/test_shared_client_retry_config.py`
owns that check for the shared shape, and duplicating it here would mean two places to update when the
retry policy changes. What this file owns is that no client is left bare.
"""

import ast
import os

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_PIPELINES_DIR = os.path.join(_REPO_ROOT, "backendPipelines")

# Directories that are not shipped pipeline code: test suites, vendored upstream trees, caches.
_SKIP_PARTS = {"__pycache__", "tests", "src", "node_modules", ".venv", ".pytest_cache"}

# A directory carrying this marker is synced from an upstream repository on EVERY cdk synth or list:
# `SplatToolboxConstruct.syncContainerSources` clones the pinned commit and copies every upstream file
# over the local tree. An edit to such a file survives until the next CDK invocation and no further --
# measured, not assumed: `build_models_tar.py` was fixed here, and `npx cdk list` reverted it within
# the hour, leaving this ratchet red with nothing a VAMS change could do about it.
#
# VAMS-owned files in those directories are the ones that do NOT exist upstream, which is precisely why
# they survive the copy: `__main__.py` (the container entry point), `vams_utils/` (its support package),
# `vams_bake_models.py` and `__init__.py` all predate the last sync, while `build_models_tar.py` and the
# marker itself carry its timestamp. That is the measurement the list below records.
#
# Stated as a DENY list rather than an allow list on purpose. Either shape exempts the same file today,
# but they fail in opposite directions: an allow list exempts every file it has not heard of, so a new
# VAMS-owned module dropped into this directory would leave the rule silently. A deny list checks
# anything it has not heard of, so a future upstream bump that adds a Python file turns the rule RED and
# someone classifies it. Loud and wrong beats quiet and wrong for a coverage boundary.
#
# The alternative to exempting at all would be a programmatic injection like the Dockerfile's, rewriting
# the client after each copy. Rejected deliberately: the one bare client is in a workstation utility that
# builds a model archive for local debug runs, not in anything the deployed pipeline executes, so a
# brittle source-rewriting anchor would buy no production behaviour.
_UPSTREAM_SYNC_MARKER = ".synced-commit"
_UPSTREAM_OWNED_FILES = {"build_models_tar.py"}


def _upstream_synced_dirs():
    """Directories under backendPipelines that a CDK invocation overwrites from upstream."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(_PIPELINES_DIR):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
        if _UPSTREAM_SYNC_MARKER in filenames:
            found.add(os.path.abspath(dirpath))
    return found


def _is_upstream_owned(path, synced_dirs):
    """True when this file is upstream's copy rather than a VAMS addition that survives the sync."""
    if os.path.basename(path) not in _UPSTREAM_OWNED_FILES:
        return False
    directory = os.path.abspath(os.path.dirname(path))
    return any(directory == synced or directory.startswith(synced + os.sep)
               for synced in synced_dirs)


def _pipeline_modules():
    """Every shipped, VAMS-owned pipeline module, as (relative path, AST)."""
    out = []
    synced_dirs = _upstream_synced_dirs()
    for dirpath, dirnames, filenames in os.walk(_PIPELINES_DIR):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            if _is_upstream_owned(path, synced_dirs):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read())
            except (SyntaxError, OSError):
                continue
            out.append((os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/"), tree))
    return out


def _boto3_aliases(tree):
    """The names `boto3` is bound to in this module (`import boto3 as _boto3` counts)."""
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "boto3":
                    aliases.add(alias.asname or "boto3")
    return aliases or {"boto3"}


def _is_session_construction(node, aliases):
    """`boto3.Session()` or `boto3.session.Session()`, under any alias."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr != "Session":
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        return owner.id in aliases
    # boto3.session.Session()
    return (isinstance(owner, ast.Attribute) and owner.attr == "session"
            and isinstance(owner.value, ast.Name) and owner.value.id in aliases)


def _session_names(tree, aliases):
    """Local names bound to a boto3 Session, so clients built off them are seen too."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_session_construction(node.value, aliases):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _scopes(tree):
    """Yield (scope name, statements) for the module and for each function/class, without mixing them.

    A plain `ast.walk` descends THROUGH a function boundary, so a constant defined inside one function
    would appear to satisfy a use in another -- or at module level, where it would still raise
    `NameError`. Splitting by scope is what keeps the define-before-use rule meaningful: the module
    scope sees only its own statements (descending into `if`/`try`/`with`, which do not create a
    scope), and each function is checked against its own body.
    """
    def nodes_of(body, name):
        own, nested = [], []
        stack = list(body)
        while stack:
            node = stack.pop(0)
            if isinstance(node, _SCOPE_NODES):
                nested.append(node)
                continue
            own.append(node)
            # Descend one level at a time, pruning at every scope boundary, so a nested `def` inside
            # an `if` or `try` is handed to its own scope rather than folded into this one.
            for child in ast.iter_child_nodes(node):
                if isinstance(child, _SCOPE_NODES):
                    nested.append(child)
                else:
                    stack.append(child)
        yield name, own
        for node in nested:
            child_name = node.name if name == "<module>" else "%s.%s" % (name, node.name)
            yield from nodes_of(node.body, child_name)

    yield from nodes_of(tree.body, "<module>")


def _boto_constructions(tree):
    """Every boto3 client/resource construction in the module, as (node, has_config).

    Three receiver shapes, because a check that only matches a literal `boto3.` prefix is blind to
    two of them and reports a clean sweep while a bare client sits in the tree:

      * `boto3.client(...)` / `boto3.resource(...)` -- including under an import alias
      * `session.client(...)`, where `session` was assigned a `boto3[.session].Session()`
      * `boto3.Session().client(...)` -- constructed and used in one expression
    """
    aliases = _boto3_aliases(tree)
    sessions = _session_names(tree, aliases)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("client", "resource")):
            continue
        owner = func.value
        if isinstance(owner, ast.Name):
            if owner.id not in aliases and owner.id not in sessions:
                continue
        elif not _is_session_construction(owner, aliases):
            continue
        found.append((node, any(kw.arg == "config" for kw in node.keywords)))
    return found


@pytest.mark.unit
class TestTheDetectorItself:
    """The rules below are all assertions that a set is EMPTY, which a broken detector satisfies.

    Two things are proved here rather than assumed: that `has_config` discriminates (a detector that
    hardcoded `True` would make `test_no_pipeline_client_is_left_bare` pass over every bare client in
    the tree), and that each receiver shape is actually reached.
    """

    _SAMPLE = (
        "import boto3\n"
        "import boto3 as _boto3\n"
        "from botocore.config import Config\n"
        "retry_config = Config()\n"
        "a = boto3.client('s3', config=retry_config)\n"          # configured, module
        "b = boto3.client('s3')\n"                               # BARE, module
        "c = boto3.resource('dynamodb', config=retry_config)\n"  # configured, resource
        "d = _boto3.client('sts')\n"                             # BARE, under an alias
        "session = boto3.session.Session()\n"
        "e = session.client('sts', config=retry_config)\n"       # configured, off a session name
        "f = session.client('eks')\n"                            # BARE, off a session name
        "g = boto3.Session().client('sqs')\n"                    # BARE, chained construction
        "h = some_other_object.client('nope')\n"                 # not boto3 -- must NOT be counted
    )

    def test_has_config_reports_both_answers(self):
        found = _boto_constructions(ast.parse(self._SAMPLE))
        flags = [has_config for _, has_config in found]
        assert True in flags, "has_config never reports True; the keyword check is broken"
        assert False in flags, (
            "has_config never reports False, so the bare-client rule cannot fail no matter what the "
            "tree contains"
        )

    def test_every_receiver_shape_is_reached(self):
        found = _boto_constructions(ast.parse(self._SAMPLE))
        bare_lines = sorted(node.lineno for node, has_config in found if not has_config)
        configured = sorted(node.lineno for node, has_config in found if has_config)
        # 5=configured module, 7=configured resource, 10=configured off a session name
        assert configured == [5, 7, 10], f"configured shapes missed: {configured}"
        # 6=bare module, 8=bare alias, 11=bare session name, 12=bare chained Session()
        assert bare_lines == [6, 8, 11, 12], f"bare shapes missed: {bare_lines}"

    def test_the_scope_split_still_catches_a_module_level_use_before_definition(self):
        """The defect this ordering rule exists for, plus the two shapes the scope split must not
        confuse with it.

        Making the rule scope-aware could have excused the very thing it was written to catch: a
        module-level client built above the module-level constant, which is a `NameError` at import.
        These three arms fix that, so the widening is bounded.
        """
        def first_offence(src):
            tree = ast.parse(src)
            for scope_name, nodes in _scopes(tree):
                assigned = first_use = None
                for sub in nodes:
                    if (isinstance(sub, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "retry_config"
                                    for t in sub.targets)):
                        if assigned is None or sub.lineno < assigned:
                            assigned = sub.lineno
                    if (isinstance(sub, ast.Name) and sub.id == "retry_config"
                            and isinstance(sub.ctx, ast.Load)):
                        if first_use is None or sub.lineno < first_use:
                            first_use = sub.lineno
                if first_use is None:
                    continue
                if assigned is None and scope_name == "<module>":
                    return "undefined-at-module"
                if assigned is not None and first_use < assigned:
                    return "used-before-defined:" + scope_name
            return None

        # The real defect: constant below the client that uses it, both at module level.
        assert first_offence(
            "import boto3\n"
            "s3 = boto3.client('s3', config=retry_config)\n"
            "retry_config = 1\n"
        ) == "used-before-defined:<module>"

        # A module-level use with the ONLY definition inside a function is still a NameError, and the
        # scope split must not let the function's definition satisfy it.
        assert first_offence(
            "import boto3\n"
            "s3 = boto3.client('s3', config=retry_config)\n"
            "def f():\n"
            "    retry_config = 1\n"
            "    return retry_config\n"
        ) == "undefined-at-module"

        # Legitimate and must NOT be flagged: defined and used inside the same function, which is the
        # deferred-import shape in splatToolbox/container/build_models_tar.py.
        assert first_offence(
            "def upload():\n"
            "    import boto3\n"
            "    from botocore.config import Config\n"
            "    retry_config = Config()\n"
            "    return boto3.client('s3', config=retry_config)\n"
        ) is None

        # Also legitimate: module-level constant read inside a function.
        assert first_offence(
            "import boto3\n"
            "retry_config = 1\n"
            "def upload():\n"
            "    return boto3.client('s3', config=retry_config)\n"
        ) is None

    def test_an_unrelated_client_call_is_not_counted(self):
        """`some_other_object.client(...)` is not a boto3 construction; counting it would produce a
        false offender that cannot be fixed, and the rule would be permanently red."""
        found = _boto_constructions(ast.parse(self._SAMPLE))
        assert 13 not in [node.lineno for node, _ in found]


@pytest.mark.unit
class TestPipelineBotoClientsAreConfigured:
    def test_the_walk_finds_the_pipeline_modules(self):
        """Control. An empty set, or one that missed the client-bearing modules, would make the rules
        below pass without examining anything -- and a skip list is an easy way to lose coverage."""
        modules = _pipeline_modules()
        assert len(modules) >= 100, f"only {len(modules)} pipeline modules found"
        with_clients = [rel for rel, tree in modules if _boto_constructions(tree)]
        assert len(with_clients) >= 60, (
            f"only {len(with_clients)} modules construct a boto3 client; the walk or the detector is "
            f"broken, since 68 did before the sweep"
        )

    def test_the_upstream_exemption_is_narrow_and_still_examines_vams_code(self):
        """The upstream-sync skip is the one place this ratchet can lose coverage silently.

        Four arms, because a skip keyed on a marker file is easy to widen by accident:
          * the marker directory is actually found (otherwise the skip is inert and the rule would be
            red, which is at least loud);
          * every OTHER Python file in that directory is still examined -- `__main__.py` (the container
            entry point), `vams_bake_models.py` and `__init__.py` are VAMS-owned and survive the sync;
          * the exemption names one file, so a future upstream bump that adds Python files turns the
            rule red rather than absorbing them;
          * a file with an exempt basename OUTSIDE a marked directory is NOT exempted.
        """
        synced = _upstream_synced_dirs()
        assert synced, (
            "no .synced-commit marker found under backendPipelines. Either the splat container has "
            "never been synced in this checkout, or the marker was renamed -- in which case this "
            "ratchet is now asserting against files a cdk synth will overwrite.")

        examined = {rel for rel, _ in _pipeline_modules()}

        # Every VAMS-owned file in a synced directory stays in scope, not just the entry point.
        for owned in ("__main__.py", "vams_bake_models.py"):
            assert any(r.endswith("splatToolbox/container/" + owned) for r in examined), (
                f"splatToolbox/container/{owned} is VAMS-owned (it predates the last sync) and must "
                f"still be checked, but the walk skipped it. Examined {len(examined)} modules.")
        assert any("splatToolbox/container/vams_utils/" in r for r in examined), \
            "the VAMS support package vams_utils/ must still be examined"

        # The exemption names exactly the files measured to be upstream's.
        exempted, present = [], []
        for directory in synced:
            for name in sorted(os.listdir(directory)):
                full = os.path.join(directory, name)
                if os.path.isfile(full) and name.endswith(".py"):
                    present.append(name)
                    if _is_upstream_owned(full, synced):
                        exempted.append(os.path.relpath(full, _REPO_ROOT).replace(os.sep, "/"))
        assert len(exempted) <= 2, (
            f"{len(exempted)} upstream-owned Python files are exempt, more than measured. An upstream "
            f"bump may have added files or moved VAMS code into the synced set:\n  "
            + "\n  ".join(exempted))
        assert len(present) - len(exempted) >= 3, (
            f"only {len(present) - len(exempted)} of {len(present)} Python files in the synced "
            f"directory are examined; the exemption has widened past its measurement")

        # An exempt BASENAME outside a synced directory must not be exempted. Guards the shape of the
        # rule: keying on the filename alone would exempt any same-named file anywhere in the tree.
        elsewhere = os.path.join(_PIPELINES_DIR, "conversion", "build_models_tar.py")
        assert _is_upstream_owned(elsewhere, synced) is False

    def test_no_pipeline_client_is_left_bare(self):
        offenders = []
        for rel, tree in _pipeline_modules():
            for node, has_config in _boto_constructions(tree):
                if not has_config:
                    offenders.append(f"{rel}:{node.lineno}")

        assert offenders == [], (
            "these boto3 clients are constructed without a retry configuration, so they sit on "
            "botocore's default retry mode with no client-side rate limiting -- a sustained burst "
            "surfaces as a throttling error on the caller instead of being smoothed. Pass "
            "`config=retry_config` (see backendPipelines/CLAUDE.md). If a call must NOT retry, say so "
            "in a comment so it is distinguishable from an oversight:\n  " + "\n  ".join(offenders)
        )

    def test_the_retry_constant_is_defined_above_its_first_use(self):
        """A constant below the client that references it is a NameError at import.

        Counting `config=` keywords cannot see this: the call looks configured. In a Lambda the module
        fails to import, so every request 500s from cold start -- and CDK synth, lint and the unit
        suites all still pass.
        """
        offenders = []
        for rel, tree in _pipeline_modules():
            for scope_name, scope_nodes in _scopes(tree):
                assigned, first_use = None, None
                for sub in scope_nodes:
                    if (isinstance(sub, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == "retry_config"
                                    for t in sub.targets)):
                        if assigned is None or sub.lineno < assigned:
                            assigned = sub.lineno
                    if (isinstance(sub, ast.Name) and sub.id == "retry_config"
                            and isinstance(sub.ctx, ast.Load)):
                        if first_use is None or sub.lineno < first_use:
                            first_use = sub.lineno
                if first_use is None:
                    continue
                if assigned is None:
                    # Only an offence at module level. A function may legitimately read a constant
                    # defined at module scope, which is the ordinary case.
                    if scope_name == "<module>":
                        offenders.append(
                            f"{rel}: uses retry_config at line {first_use} but never defines it")
                elif first_use < assigned:
                    offenders.append(
                        f"{rel}: in {scope_name}, defines retry_config at line {assigned} but uses "
                        f"it at line {first_use}")

        assert offenders == [], (
            "retry_config is used before it is defined. Declare it above the FIRST client, not merely "
            "after the imports -- some modules here interleave imports with executable code:\n  "
            + "\n  ".join(offenders)
        )
