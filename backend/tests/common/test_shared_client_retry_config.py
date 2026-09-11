# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S2-BACKEND-155 -- adaptive retry configuration on the shared modules' AWS clients.

`backend/CLAUDE.md` Rule 6 requires `Config(retries={'max_attempts': 5, 'mode': 'adaptive'})` on
every module-level AWS client. The finding named four shared modules and only `common/resourceNames`
followed it; `customLogging/auditLogging`, `common/s3` and `common/dynamodb` built their clients
bare.

What the missing config costs is not retry itself -- botocore's default `legacy` mode does retry a
`ThrottlingException` -- but the client-side rate limiting `adaptive` adds, which smooths a
sustained burst rather than a spike. These modules sit where that matters: the audit writer issues
`PutLogEvents` inside the request the caller is waiting on, `common/s3`'s client backs the
`head_object` in every extension/content-type validation, and `common/dynamodb`'s clients back
`get_asset_object_from_id`, which is on the Tier-2 authorization path.

Three angles, because each alone is defeatable:

* DECLARED form, by AST. botocore normalises the retries dict it is handed **in place** while
  building the client -- it pops `max_attempts` and writes `total_max_attempts` back into the
  caller's own dict -- so after import there is no `max_attempts` key left to compare against Rule
  6's wording. Only the source has it.
* PASSED to every constructor, by AST. A correct declaration that no `boto3.client(...)` receives
  governs nothing. `common/dynamodb` is the case this exists for: it binds two clients from one
  declaration, so configuring the resource and leaving the low-level client bare would satisfy the
  declaration check while leaving the paginating scans unconfigured.
* RESOLVED on the live object, which is what actually governs a call.

The real module is loaded **by file path**, not by import name. `backend/tests/mocks/common/s3.py`
shadows `common.s3` on the test `sys.path`, so `import common.s3` yields the mock and would assert
nothing about the shipped module. `conftest.py` loads by path for the same reason.

The resolved check covers `common/s3.py` only, and deliberately: loading `common/dynamodb.py` by
path executes its `from models.common import ...`, and `backend/tests/models/` is a package on the
test path, so `models` resolves to the test tree instead of the shipped one. Rather than mutate
`sys.modules` inside a shared session to force it, `common/dynamodb.py` is covered by the two AST
checks — the declaration is Rule 6's dict, and every one of its constructors receives it — and the
`s3c` case demonstrates end to end that botocore resolves that same pairing to
`adaptive`/`total_max_attempts: 6`. What is NOT covered for `dynamodb` is a botocore-side surprise
specific to the `dynamodb` service name, which is not a thing botocore does.

Rule 6 covers every AWS client in `backend/backend`, not only the shared modules, so the last three
classes assert it across the whole tree. Each states a different thing, and none of them is
implementable by importing the modules: 22 of the swept files resolve a resource name at module
level inside a `try:` that re-raises, `handlers/indexing/snsQueuing.py` indexes
`os.environ["SNS_TOPIC_ARN"]` the same way, and `backend/tests/mocks/` shadows five of them plus the
whole `handlers/authz` package. All three are pure AST over the source.

* NO client is built without a `config=` keyword (`TestTheWiderGapIsPinned`, bound at zero).
* Every `config=` value RESOLVES to Rule 6's dict, except a named list of deliberate departures.
  Presence alone governs nothing: passing `handlers/assets/downloadAsset.py`'s `s3_config` to a
  DynamoDB resource satisfies a presence check while shipping S3 path-addressing on DynamoDB.
* The name a `config=` keyword refers to is BOUND, from `botocore.config`, before the line that uses
  it. A `config=retry_config` added without its import is a `NameError` at module import, which
  `python -m compileall` does not catch and which 500s every request from cold start.
"""

import ast
import importlib.util
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

# Shared modules Rule 6 covers, and the module-level attributes each binds a client to.
# `common/resourceNames.py` builds its SSM client lazily inside a function, so it has no
# module-level attribute to resolve -- it is still covered by the two AST checks.
SHARED_MODULES = ["common/resourceNames.py", "common/s3.py", "common/dynamodb.py",
                  "customLogging/auditLogging.py"]
RESOLVABLE_CLIENTS = [("common/s3.py", "s3c")]

RULE_6_RETRIES = {"max_attempts": 5, "mode": "adaptive"}

# Clients that deliberately receive something other than Rule 6's dict, keyed by the file and the
# `config=` expression as written. Each is a bounded or exactly-once client whose reason is recorded
# beside it in the source; a departure absent from here fails the resolution test.
DELIBERATE_DEPARTURES = {
    # The authorizer runs inside every request's latency budget, so its lazy table client takes
    # three attempts rather than five.
    ("common/auth/authorizerCore.py",
     "BotoConfig(retries={'max_attempts': 3, 'mode': 'adaptive'})"),
    # Best-effort EventBridge publishes on a hot ingestion path and on the job callback: bounded
    # connect/read timeouts so an unreachable endpoint fails fast instead of blocking the caller.
    ("handlers/indexing/sqsBucketSync.py",
     "BotoConfig(connect_timeout=3, read_timeout=5, retries={'max_attempts': 2})"),
    ("handlers/workflows/sfn/deadlineCloudJobCallback.py", "events_retry_config"),
    # An advisory trigger-save lookup, bounded so an unreachable table cannot hold the save open.
    ("handlers/workflows/workflowTriggerService.py", "lookup_retry_config"),
    # The executeWorkflowV2 Invoke is not idempotent: a retry would launch a duplicate execution.
    ("handlers/workflows/sfn/workflowTriggerDispatch.py", "invoke_config"),
}


def _parse(relative_path):
    with open(os.path.join(BACKEND, *relative_path.split("/")), encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _declared_retries(relative_path):
    """The `retries={...}` dict as written in the module, or None if there is no declaration."""
    for node in ast.walk(_parse(relative_path)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name != "Config":
            continue
        for keyword in node.keywords:
            if keyword.arg == "retries" and isinstance(keyword.value, ast.Dict):
                return {
                    key.value: value.value
                    for key, value in zip(keyword.value.keys, keyword.value.values)
                    if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)
                }
    return None


def _boto3_calls(tree):
    """Every `boto3.client(...)` / `boto3.resource(...)` call, as (service, keyword names, line)."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("client", "resource")):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "boto3"):
            continue
        service = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        calls.append((service, {keyword.arg for keyword in node.keywords}, node.lineno))
    return calls


def _builds_a_session(node):
    """`boto3.Session()` or `boto3.session.Session()` -- the receiver shape `_boto3_calls` misses."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr != "Session":
        return False
    owner = node.func.value
    if isinstance(owner, ast.Name):
        return owner.id == "boto3"
    return (isinstance(owner, ast.Attribute) and owner.attr == "session"
            and isinstance(owner.value, ast.Name) and owner.value.id == "boto3")


def _load_by_path(module_name, relative_path):
    """Execute the shipped module from disk, bypassing whatever `sys.path` resolves its name to."""
    if BACKEND not in sys.path:
        # The module's own `from common... import` lines still resolve by name.
        sys.path.insert(0, BACKEND)
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(BACKEND, *relative_path.split("/")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_retries(client):
    """The retries dict botocore resolved, for a client or a resource."""
    config = getattr(getattr(client, "meta", None), "config", None)
    if config is not None and hasattr(config, "retries"):
        return config.retries or {}
    return client.meta.client.meta.config.retries or {}


def _source_files():
    """Every parseable module under `backend/backend`, as (relative path, tree)."""
    for directory, _, filenames in os.walk(BACKEND):
        if "__pycache__" in directory:
            continue
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            yield os.path.relpath(path, BACKEND).replace(os.sep, "/"), tree


def _config_symbols(tree):
    """The names this module binds to `botocore.config.Config`, alias included."""
    symbols = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "botocore.config":
            symbols.update(alias.asname or alias.name for alias in node.names
                           if alias.name == "Config")
    return symbols


def _constant_dict(node):
    if not isinstance(node, ast.Dict):
        return None
    return {key.value: value.value for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)}


def _module_config_bindings(tree):
    """Module-level `name = Config(...)` assignments, as name -> (line, retries dict or None)."""
    symbols = _config_symbols(tree)
    bindings = {}
    for statement in tree.body:
        if not (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
                and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)):
            continue
        callee = statement.value.func
        if not (isinstance(callee, ast.Name) and callee.id in symbols):
            continue
        retries = None
        for keyword in statement.value.keywords:
            if keyword.arg == "retries":
                retries = _constant_dict(keyword.value)
        bindings[statement.targets[0].id] = (statement.lineno, retries)
    return bindings


def _sibling_imports(relative_path, tree):
    """`from . import x` bindings, as name -> the sibling module's relative path."""
    package = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
    siblings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
            for alias in node.names:
                name = alias.asname or alias.name
                siblings[name] = f"{package}/{alias.name}.py" if package else f"{alias.name}.py"
    return siblings


def _client_sites(tree):
    """Every `boto3.client(...)` / `boto3.resource(...)`, as (line, service, config value node)."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not (isinstance(callee, ast.Attribute) and callee.attr in ("client", "resource")):
            continue
        if not (isinstance(callee.value, ast.Name) and callee.value.id == "boto3"):
            continue
        service = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        config = next((keyword.value for keyword in node.keywords if keyword.arg == "config"), None)
        sites.append((node.lineno, service, config))
    return sites


_SIBLING_BINDINGS = {}


def _site_retries(relative_path, tree, config):
    """The retries dict the `config=` value carries, or None when it cannot be resolved.

    Resolves the three forms the codebase uses: an inline `Config(...)`, a module-level name, and a
    one-hop `sibling._retry_config` reached through a `from . import sibling`.
    """
    if isinstance(config, ast.Call):
        callee = config.func
        if isinstance(callee, ast.Name) and callee.id in _config_symbols(tree):
            for keyword in config.keywords:
                if keyword.arg == "retries":
                    return _constant_dict(keyword.value)
        return None
    if isinstance(config, ast.Name):
        binding = _module_config_bindings(tree).get(config.id)
        return binding[1] if binding else None
    if isinstance(config, ast.Attribute) and isinstance(config.value, ast.Name):
        sibling = _sibling_imports(relative_path, tree).get(config.value.id)
        if sibling is None:
            return None
        if sibling not in _SIBLING_BINDINGS:
            path = os.path.join(BACKEND, *sibling.split("/"))
            if not os.path.exists(path):
                _SIBLING_BINDINGS[sibling] = {}
            else:
                with open(path, encoding="utf-8") as handle:
                    _SIBLING_BINDINGS[sibling] = _module_config_bindings(ast.parse(handle.read()))
        binding = _SIBLING_BINDINGS[sibling].get(config.attr)
        return binding[1] if binding else None
    return None


class TestDeclaredForm:
    @pytest.mark.parametrize("relative_path", SHARED_MODULES)
    def test_the_module_declares_the_rule_6_retry_config(self, relative_path):
        declared = _declared_retries(relative_path)
        assert declared is not None, (
            f"{relative_path} declares no `Config(retries={{...}})`; Rule 6 requires one for its "
            f"AWS client")
        assert declared == {"max_attempts": 5, "mode": "adaptive"}, (
            f"{relative_path} declares retries={declared}, not the "
            f"{{'max_attempts': 5, 'mode': 'adaptive'}} Rule 6 requires")

    @pytest.mark.parametrize("relative_path", SHARED_MODULES)
    def test_every_client_the_module_builds_is_handed_a_config(self, relative_path):
        bare = [(service, line) for service, keywords, line in _boto3_calls(_parse(relative_path))
                if "config" not in keywords]
        assert bare == [], (
            f"{relative_path} builds client(s) {bare} (service, line) without `config=`, so the "
            f"module's retry_config does not apply to them")


class TestResolvedOnTheLiveClient:
    """What botocore resolved, which is what governs a call."""

    @pytest.mark.parametrize("relative_path,attribute", RESOLVABLE_CLIENTS)
    def test_the_client_resolves_adaptive_mode_and_six_attempts(self, relative_path, attribute):
        module = _load_by_path(
            "_shipped_" + relative_path.replace("/", "_")[:-3], relative_path)
        retries = _resolved_retries(getattr(module, attribute))
        assert retries.get("mode") == "adaptive", (
            f"{relative_path}:{attribute} resolves retry mode {retries.get('mode')!r}; Rule 6 "
            f"requires 'adaptive', which is what adds client-side rate limiting under a sustained "
            f"burst")
        assert retries.get("total_max_attempts") == 6, (
            f"{relative_path}:{attribute} allows {retries.get('total_max_attempts')} total "
            f"attempts, not the 6 that max_attempts=5 resolves to")

    def test_the_shipped_module_is_loaded_not_its_mock(self):
        """Positive control for the loader.

        `tests/mocks/common/s3.py` shadows `common.s3` on the test sys.path. If this suite ever
        started asserting against the mock, every assertion above would be about a test double.
        """
        module = _load_by_path("_shipped_control_s3", "common/s3.py")
        assert module.__file__.replace("\\", "/").endswith("backend/backend/common/s3.py"), (
            f"loaded {module.__file__} rather than the shipped common/s3.py")
        assert hasattr(module, "S3_VERSIONS_PAGE_SIZE"), (
            "the loaded module lacks S3_VERSIONS_PAGE_SIZE, which only the shipped module defines")


class TestEveryConfigResolvesToRule6:
    """What a `config=` keyword actually carries, across the whole of `backend/backend`.

    The ratchet below is presence-only, so it cannot tell a Rule 6 config from any other one. This
    class resolves the value: an inline `Config(...)`, a module-level name, or a one-hop
    `sibling._retry_config`. The departures live in `DELIBERATE_DEPARTURES`, keyed by the expression
    as written, so a new one has to be added there rather than tolerated by omission.
    """

    def test_no_client_receives_a_config_that_is_not_rule_6(self):
        surveyed, unexplained = set(), set()
        for relative_path, tree in _source_files():
            for line, service, config in _client_sites(tree):
                if config is None:
                    continue                        # the ratchet below owns the bare case
                surveyed.add((relative_path, line, service))
                if _site_retries(relative_path, tree, config) == RULE_6_RETRIES:
                    continue
                if (relative_path, ast.unparse(config)) in DELIBERATE_DEPARTURES:
                    continue
                unexplained.add((relative_path, line, service, ast.unparse(config)))
        assert surveyed, (
            "the walker found no configured AWS client anywhere in backend/backend, so the "
            "assertion below would pass having resolved nothing")
        assert unexplained == set(), (
            f"{len(unexplained)} client(s) receive a config that is not Rule 6's "
            f"{RULE_6_RETRIES} and are not in DELIBERATE_DEPARTURES: {sorted(unexplained)}")

    def test_every_deliberate_departure_still_names_a_real_client(self):
        """Anti-rot for the list above, without pinning the departures in place.

        Asserting the departures are still non-Rule-6 would turn TIGHTENING one into a failure. This
        asserts only that each entry still describes a `config=` expression that exists, so a
        removed or renamed client turns the stale entry red.
        """
        written = set()
        for relative_path, tree in _source_files():
            for _, _, config in _client_sites(tree):
                if config is not None:
                    written.add((relative_path, ast.unparse(config)))
        assert written, "no `config=` expression was collected, so the check below is vacuous"
        assert DELIBERATE_DEPARTURES <= written, (
            f"DELIBERATE_DEPARTURES names client config(s) that no longer exist: "
            f"{sorted(DELIBERATE_DEPARTURES - written)}")


class TestTheConfigNameIsBoundBeforeItIsUsed:
    """The cold-start `NameError` class, which no import-based check in this suite can reach.

    `config=retry_config` written without `from botocore.config import Config`, or with the
    declaration placed BELOW the client it configures, raises at module import: every request to
    that Lambda returns 500 from the first invocation, and `python -m compileall` is silent about
    it. Both are decidable from the source.
    """

    def test_every_config_name_has_a_module_level_config_binding(self):
        surveyed, unbound = set(), set()
        for relative_path, tree in _source_files():
            bindings = _module_config_bindings(tree)
            for line, service, config in _client_sites(tree):
                if not isinstance(config, ast.Name):
                    continue
                surveyed.add((relative_path, line, service))
                if config.id not in bindings:
                    unbound.add((relative_path, line, config.id))
        assert surveyed, "no client passes `config=<name>`, so nothing was resolved"
        assert unbound == set(), (
            f"a `config=` name is not bound to a module-level `Config(...)` built from a symbol "
            f"imported from botocore.config, so the module raises NameError on import: "
            f"{sorted(unbound)}")

    def test_no_config_declaration_sits_below_the_client_it_configures(self):
        surveyed, late = set(), set()
        for relative_path, tree in _source_files():
            bindings = _module_config_bindings(tree)
            for line, service, config in _client_sites(tree):
                if not (isinstance(config, ast.Name) and config.id in bindings):
                    continue
                surveyed.add((relative_path, line, service))
                declared_at = bindings[config.id][0]
                if declared_at > line:
                    late.add((relative_path, config.id, declared_at, line))
        assert surveyed, "no resolvable `config=<name>` was found, so no ordering was checked"
        assert late == set(), (
            f"a Config declaration follows the client it is passed to, which is a NameError at "
            f"module import: {sorted(late)}")


class TestTheWiderGapIsPinned:
    """No AWS client in `backend/backend` is built without a `config=` keyword."""

    # Measured across backend/backend: 176 `boto3.client(...)`/`boto3.resource(...)` call sites, all
    # of them handed a `config=`. The bound is zero rather than a tolerance because Rule 6 admits no
    # exception for the keyword itself -- what the config CONTAINS is where the deliberate
    # departures live, and TestEveryConfigResolvesToRule6 owns those. A new bare client turns this
    # red naming its file.
    #
    # The bound covers `backend/backend` only, so a green run here is not "no bare client anywhere".
    # `backendPipelines/` is held to the same rule by its own ratchet,
    # `tests/common/workflows/test_pipeline_boto_clients_configured.py` -- which also covers the
    # receiver shapes this file's walker does not see (below).
    MAX_UNCONFIGURED = 0

    def test_no_shared_module_is_among_the_unconfigured(self):
        bare = self._unconfigured()
        shared = [path for path in bare if path.startswith(("common/", "customLogging/"))]
        assert shared == [], f"a shared module regressed to a bare AWS client: {shared}"

    def test_the_handler_gap_has_not_grown(self):
        bare = self._unconfigured()
        assert len(bare) <= self.MAX_UNCONFIGURED, (
            f"{len(bare)} module-level clients omit `config=`, above the pinned "
            f"{self.MAX_UNCONFIGURED}. A new AWS client needs `config=retry_config` per "
            f"backend/CLAUDE.md Rule 6. Full list: {bare}")

    def test_the_walker_can_still_see_a_bare_client(self):
        """Negative control for a bound of zero, which otherwise passes if `_boto3_calls` stops
        matching. Feeds the walker a module that IS bare and asserts it is reported."""
        tree = ast.parse("import boto3\nsns = boto3.client('sns')\n")
        bare = [service for statement in tree.body
                for service, keywords, _ in _boto3_calls(ast.Module(body=[statement],
                                                                    type_ignores=[]))
                if "config" not in keywords]
        assert bare == ["sns"], (
            f"the walker reported {bare} for a module with one bare `boto3.client('sns')`, so a "
            f"count of zero over the real tree proves nothing")

    def test_no_client_is_built_off_a_session_where_the_walker_cannot_see_it(self):
        """The walker matches a literal `boto3.` receiver, so `session.client(...)` is invisible to it.

        `backend/backend` builds no client that way today -- the three `boto3.Session()` calls in the
        indexers and search take credentials only -- so the bound above is honest. This states the
        precondition rather than leaving it as an accident: a client built off a session would be
        silently exempt from every assertion in this class, which is worse than an uncovered file
        because the count would still read zero.

        `backendPipelines` had exactly that escape (`kubernetes_utils.py`, a bare
        `session.client('sts')`), which is why its ratchet resolves session receivers.
        """
        offenders = []
        examined = 0
        for relative_path, tree in _source_files():
            examined += 1
            sessions = {
                target.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name) and _builds_a_session(node.value)
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in ("client", "resource"):
                    continue
                owner = node.func.value
                reached = (isinstance(owner, ast.Name) and owner.id in sessions) \
                    or _builds_a_session(owner)
                if reached:
                    offenders.append(f"{relative_path}:{node.lineno}")

        assert examined >= 100, (
            f"only {examined} source files walked; an empty offender list would prove nothing")
        assert offenders == [], (
            "these clients are built off a boto3 Session, which this file's walker does not match -- "
            "so they are exempt from the `config=` bound without appearing in it. Either pass "
            "`config=retry_config` and widen `_boto3_calls`/`_client_sites` to resolve session "
            "receivers (see tests/common/workflows/test_pipeline_boto_clients_configured.py), or "
            "build the client from the module directly:\n  " + "\n  ".join(offenders))

    def test_the_session_detector_fires_on_both_session_spellings(self):
        """Control for the empty set above. `backend/backend` has no session-built client, so the
        assertion is satisfied equally by "none exist" and by a detector that matches nothing."""
        sample = ast.parse(
            "import boto3\n"
            "s1 = boto3.Session()\n"
            "s2 = boto3.session.Session()\n"
            "creds = boto3.Session().get_credentials()\n"   # NOT a client -- must not be flagged
        )
        assigns = [node.value for node in sample.body if isinstance(node, ast.Assign)]
        assert _builds_a_session(assigns[0]) is True, "boto3.Session() not recognised"
        assert _builds_a_session(assigns[1]) is True, "boto3.session.Session() not recognised"
        # The credentials call is a Session construction too; what keeps it out of the offender list
        # is that `.get_credentials` is not in ("client", "resource"), which the rule above checks.
        creds_call = assigns[2]
        assert isinstance(creds_call.func, ast.Attribute)
        assert creds_call.func.attr == "get_credentials"
        assert _builds_a_session(creds_call) is False

    @staticmethod
    def _unconfigured():
        bare = []
        for directory, _, filenames in os.walk(BACKEND):
            if "__pycache__" in directory:
                continue
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                relative = os.path.relpath(path, BACKEND).replace(os.sep, "/")
                try:
                    with open(path, encoding="utf-8") as handle:
                        tree = ast.parse(handle.read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                for statement in tree.body:
                    for service, keywords, _ in _boto3_calls(ast.Module(body=[statement],
                                                                        type_ignores=[])):
                        if "config" not in keywords:
                            bare.append(relative)
        return sorted(bare)
