"""The upstream add-on lane for MCP tools (`vams_mcp/addon_tools/`).

`server.py` calls `register_addon_tools(mcp, CLIENT, CONFIG, tool_result)` once, and every module in
`ADDON_TOOL_MODULES` defines its tools inside `register(mcp, client, config, tool_result)`. The
source-layout checks of `test_server_tools.py` read `server.py` only, so the same properties are held
here for every add-on module: where each tool sits against the two gates, its decorator order and
pre-flight, that its read section calls nothing that mutates, that every client method it calls
exists on vamscli's `APIClient`, and that no tool name is taken twice. Each check is a function over a
parsed module, run on the shipped modules and on a synthetic module that breaks it, so a check that
stopped firing fails here too. The hook itself (what it hands each module, and that it runs before
the custom-tools hook) is exercised with probe modules and a server reload.
"""

import ast
import importlib
import logging
import re
import types
from pathlib import Path

import pytest
from vamscli.utils.api_client import APIClient

import vams_mcp.addon_tools as addon_tools
import vams_mcp.custom_tools as custom_tools
from vams_mcp import server as server_module

SERVER_SOURCE = Path(server_module.__file__).read_text(encoding="utf-8")
SERVER_LINES = SERVER_SOURCE.splitlines()
ADDON_DIR = Path(addon_tools.__file__).parent
HOOK_CALL = "register_addon_tools(mcp, CLIENT, CONFIG, tool_result)"
CUSTOM_CALL = "register_custom_tools(mcp, CLIENT, CONFIG, tool_result)"
REGISTER_PARAMS = ["mcp", "client", "config", "tool_result"]
GATES = {"config.enable_writes": "write", "config.enable_destructive": "destructive"}

# The core read-section prefixes of test_server_tools.py plus the compute verbs add-on clients use.
MUTATING_PREFIXES = (
    "create_", "update_", "delete_", "set_", "archive_", "unarchive_", "execute_", "rerun_",
    "abort_", "permanent_", "revert_", "move_", "copy_", "import_", "reset_", "initialize_",
    "complete_", "start_", "stop_", "renew_", "register_", "submit_", "kill_", "deactivate_",
    "retire_", "duplicate_", "upload_", "add_", "remove_", "subscribe", "unsubscribe",
)
# Calls that remove data or terminate compute, which only a destructive-gated tool may make.
DESTRUCTIVE_PREFIXES = ("delete_", "archive_", "permanent_", "kill_", "deactivate_", "retire_")
# APIClient's raw HTTP verbs, which bypass the method-name checks above.
RAW_VERBS = ("post", "put", "patch", "delete")

GOOD_MODULE = '''
def register(mcp, client, config, tool_result):
    @mcp.tool()
    @tool_result
    def probe_read():
        """Read."""
        client.require_feature(FEATURE_PROBE, PROBE_DISABLED_MESSAGE)
        return client.api.get_database("db")

    if config.enable_writes:

        @mcp.tool()
        @tool_result
        def probe_write():
            """Write."""
            client.require_feature(FEATURE_PROBE, PROBE_DISABLED_MESSAGE)
            return client.api.create_database("db", "d", "b")

    if config.enable_destructive:

        @mcp.tool()
        @tool_result
        def probe_delete():
            """Delete."""
            client.require_feature(FEATURE_PROBE, PROBE_DISABLED_MESSAGE)
            return client.api.delete_database("db")
'''


def _is_tool(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        ast.unparse(d) in ("tool_result", "mcp.tool()") for d in node.decorator_list)


def _register_def(tree):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "register":
            return node
    return None


def _placed_tools(register):
    """(tier, def) for each tool directly in `register` or directly under one of its two gates."""
    placed = []
    for statement in register.body:
        if _is_tool(statement):
            placed.append(("read", statement))
        elif isinstance(statement, ast.If) and ast.unparse(statement.test) in GATES and not statement.orelse:
            tier = GATES[ast.unparse(statement.test)]
            placed.extend((tier, inner) for inner in statement.body if _is_tool(inner))
    return placed


def _api_calls(node):
    """The X of every `client.api.X(...)` call in the node."""
    return [
        call.func.attr for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and ast.unparse(call.func.value) == "client.api"
    ]


def _calls(node, callee):
    return any(isinstance(call, ast.Call) and ast.unparse(call.func) == callee for call in ast.walk(node))


def _opens_with_pre_flight(node):
    body = node.body[1:] if ast.get_docstring(node) else node.body
    first = body[0] if body else None
    return (
        isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
        and ast.unparse(first.value.func) == "client.require_feature"
        and len(first.value.args) == 2 and not first.value.keywords
    )


def layout_violations(tree, name="module"):
    """Every way the module departs from the lane's layout."""
    register = _register_def(tree)
    if register is None:
        return [f"{name}: no module-level register()"]
    found = []
    params = [arg.arg for arg in register.args.args]
    if params != REGISTER_PARAMS:
        found.append(f"{name}: register({', '.join(params)}) is not register({', '.join(REGISTER_PARAMS)})")
    placed = _placed_tools(register)
    placed_ids = {id(node) for _, node in placed}
    found += [
        f"{name}.{node.name}: not directly in register() or directly under one of its two gates"
        for node in ast.walk(tree) if _is_tool(node) and id(node) not in placed_ids
    ]
    for tier, node in placed:
        tool = f"{name}.{node.name}"
        decorators = [ast.unparse(d) for d in node.decorator_list]
        if decorators != ["mcp.tool()", "tool_result"]:
            found.append(f"{tool}: decorated {decorators}, not @mcp.tool() then @tool_result")
        if not _opens_with_pre_flight(node):
            found.append(f"{tool}: first statement is not client.require_feature(<switch>, <message>)")
        for called in _api_calls(node):
            if tier == "read" and (called.startswith(MUTATING_PREFIXES) or called in RAW_VERBS):
                found.append(f"{tool}: the read section calls client.api.{called}")
            if tier != "destructive" and called.startswith(DESTRUCTIVE_PREFIXES):
                found.append(f"{tool}: calls client.api.{called} outside the destructive gate")
        if tier == "read" and _calls(node, "client.post_json"):
            found.append(f"{tool}: the read section POSTs raw through client.post_json")
        server_globals = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & {"CLIENT", "CONFIG"})
        if server_globals:
            found.append(f"{tool}: reads the server's {server_globals} instead of register()'s arguments")
    return found


def missing_client_methods(tree):
    """The `client.api.X` calls that name no APIClient attribute."""
    return sorted({called for called in _api_calls(tree) if not hasattr(APIClient, called)})


def tool_names(tree):
    return [node.name for node in ast.walk(tree) if _is_tool(node)]


def name_collisions(trees, core_names):
    """Tool names an add-on module repeats: within itself, from a core tool, or from another add-on."""
    found, owners = [], {}
    for module, tree in trees.items():
        names = tool_names(tree)
        found += [f"{module}.{n}: defined twice" for n in sorted({n for n in names if names.count(n) > 1})]
        found += [f"{module}.{n}: a core tool's name" for n in sorted(set(names) & core_names)]
        for n in sorted(set(names)):
            if n in owners:
                found.append(f"{module}.{n}: also defined by {owners[n]}")
            owners.setdefault(n, module)
    return found


def _line_of(lines, prefix):
    for i, line in enumerate(lines, start=1):
        if line.startswith(prefix):
            return i
    return None


def hook_violations(lines):
    """Where the add-on hook sits in a server source, against the gates, the tools and the custom hook."""
    calls = [i for i, line in enumerate(lines, start=1) if HOOK_CALL in line]
    if len(calls) != 1:
        return [f"expected one call to the add-on hook, found lines {calls}"]
    call = calls[0]
    found = []
    if not lines[call - 1].startswith(HOOK_CALL):
        found.append("the hook is not called at module level")
    tool_lines = [
        i for i, line in enumerate(lines, start=1)
        if re.match(r"^\s*def \w+\(", line) and i >= 2 and "tool_result" in lines[i - 2]
    ]
    destructive = _line_of(lines, "if CONFIG.enable_destructive:")
    custom = _line_of(lines, CUSTOM_CALL)
    main = _line_of(lines, "def main()")
    if destructive is None or call < destructive:
        found.append("the hook runs before the destructive block")
    if tool_lines and call < max(tool_lines):
        found.append("the hook runs before the last core tool is defined")
    if custom is None or call > custom:
        found.append("the hook runs after the custom-tools hook")
    if main is None or call > main:
        found.append("the hook runs after def main()")
    return found


def _addon_module_trees():
    return {
        module.__name__.rsplit(".", 1)[1]: ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for module in addon_tools.ADDON_TOOL_MODULES
    }


def _core_tool_names():
    return set(tool_names(ast.parse(SERVER_SOURCE)))


# --- The hook in server.py --------------------------------------------------------------------


def test_the_hook_is_called_once_at_module_level_after_the_core_tools_and_before_the_custom_hook():
    assert hook_violations(SERVER_LINES) == []
    assert "from .addon_tools import register_addon_tools" in SERVER_SOURCE


def _moved(lines, line, before):
    """`lines` with the line that starts with `line` moved directly in front of the one starting with
    `before`."""
    kept = [entry for entry in lines if not entry.startswith(line)]
    target = _line_of(kept, before) - 1
    return kept[:target] + [line] + kept[target:]


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda lines: _moved(lines, HOOK_CALL, "def main()"), "after the custom-tools hook"),
        (lambda lines: _moved(lines, HOOK_CALL, "if CONFIG.enable_destructive:"), "before the destructive block"),
        (lambda lines: [("    " + entry if entry.startswith(HOOK_CALL) else entry) for entry in lines],
         "not called at module level"),
        (lambda lines: [entry for entry in lines if not entry.startswith(HOOK_CALL)], "expected one call"),
        (lambda lines: lines + [HOOK_CALL], "expected one call"),
    ],
    ids=["after-custom", "before-destructive", "indented", "missing", "twice"],
)
def test_the_hook_placement_check_fires_on_a_misplaced_hook(mutate, expected):
    """Negative controls for the placement check, each on a copy of the shipped server source."""
    violations = hook_violations(mutate(list(SERVER_LINES)))
    assert any(expected in violation for violation in violations), violations


def test_the_placement_check_sees_a_core_tool_defined_after_the_hook():
    lines = list(SERVER_LINES)
    at = lines.index(HOOK_CALL) + 1
    lines[at:at] = ["@mcp.tool()", "@tool_result", "def late_core_tool():", "    return {}"]
    assert "the hook runs before the last core tool is defined" in hook_violations(lines)


# --- The modules in addon_tools/ --------------------------------------------------------------


def test_every_module_in_the_package_is_registered():
    """A module missing from ADDON_TOOL_MODULES is never called, so its tools silently do not exist."""
    on_disk = sorted(path.stem for path in ADDON_DIR.glob("*.py") if path.stem != "__init__")
    listed = sorted(module.__name__.rsplit(".", 1)[1] for module in addon_tools.ADDON_TOOL_MODULES)
    assert on_disk, "no add-on module was found, so this assertion would be vacuous"
    assert listed == on_disk
    assert all(module.__name__.startswith("vams_mcp.addon_tools.") for module in addon_tools.ADDON_TOOL_MODULES)


@pytest.mark.parametrize("module", sorted(_addon_module_trees()))
def test_every_add_on_module_follows_the_lane_layout(module):
    tree = _addon_module_trees()[module]
    assert _placed_tools(_register_def(tree)), f"{module} registers no tool, so this would be vacuous"
    assert layout_violations(tree, module) == []


@pytest.mark.parametrize("module", sorted(_addon_module_trees()))
def test_every_add_on_client_call_targets_a_real_apiclient_method(module):
    """The tools call APIClient methods directly, so a renamed or removed CLI method fails only when
    the tool is invoked against a live deployment."""
    tree = _addon_module_trees()[module]
    assert _api_calls(tree), f"no client.api.* calls found in {module}, so this would be vacuous"
    assert missing_client_methods(tree) == []


def test_no_add_on_tool_takes_a_name_already_registered():
    """The MCP SDK keeps the first registration of a name and drops the later one with only a log line,
    so an add-on tool named like a core tool, or like another add-on's, would silently not exist."""
    assert _core_tool_names(), "no core tool was read, so this assertion would be vacuous"
    assert name_collisions(_addon_module_trees(), _core_tool_names()) == []


def test_the_synthetic_module_passes_every_check():
    """Positive control for the mutations below: each of them breaks one property of this module."""
    tree = ast.parse(GOOD_MODULE)
    assert layout_violations(tree, "probe") == []
    assert missing_client_methods(tree) == []
    assert name_collisions({"probe": tree}, _core_tool_names()) == []


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ('return client.api.get_database("db")', 'return client.api.create_database("db", "d", "b")',
         "the read section calls client.api.create_database"),
        ('return client.api.get_database("db")', 'return client.api.post("/database", data={})',
         "the read section calls client.api.post"),
        ('return client.api.get_database("db")', 'return client.post_json("/database", {})',
         "POSTs raw through client.post_json"),
        ('return client.api.create_database("db", "d", "b")', 'return client.api.delete_database("db")',
         "calls client.api.delete_database outside the destructive gate"),
        ('    @mcp.tool()\n    @tool_result\n    def probe_read',
         '    @tool_result\n    @mcp.tool()\n    def probe_read', "not @mcp.tool() then @tool_result"),
        ('        """Read."""\n        client.require_feature(FEATURE_PROBE, PROBE_DISABLED_MESSAGE)\n',
         '        """Read."""\n', "probe_read: first statement is not client.require_feature"),
        ("    if config.enable_writes:\n", "    if config.enable_writes or True:\n",
         "probe_write: not directly in register() or directly under one of its two gates"),
        ('            return client.api.create_database("db", "d", "b")\n',
         '            return client.api.create_database("db", "d", "b")\n\n    else:\n\n'
         '        @mcp.tool()\n        @tool_result\n        def probe_else():\n            """Else."""\n'
         '            client.require_feature(FEATURE_PROBE, PROBE_DISABLED_MESSAGE)\n'
         '            return {}\n',
         "not directly in register() or directly under one of its two gates"),
        ('return client.api.get_database("db")', 'return CLIENT.api.get_database("db")',
         "reads the server's ['CLIENT']"),
        ("def register(mcp, client, config, tool_result):", "def register(mcp, client, tool_result):",
         "is not register(mcp, client, config, tool_result)"),
    ],
    ids=["read-mutates", "read-raw-verb", "read-post-json", "delete-under-writes", "decorator-order",
         "no-pre-flight", "other-gate", "else-branch", "server-global", "register-signature"],
)
def test_the_layout_check_fires_on_each_departure(old, new, expected):
    assert GOOD_MODULE.count(old) == 1, f"the mutation {old!r} does not apply"
    violations = layout_violations(ast.parse(GOOD_MODULE.replace(old, new)), "probe")
    assert any(expected in violation for violation in violations), violations


def test_the_client_method_check_fires_on_a_method_apiclient_lacks():
    tree = ast.parse(GOOD_MODULE.replace("client.api.get_database(", "client.api.get_no_such_thing("))
    assert missing_client_methods(tree) == ["get_no_such_thing"]


def test_the_name_check_fires_on_a_repeated_core_or_add_on_name():
    core_named = ast.parse(GOOD_MODULE.replace("def probe_read(", "def list_databases("))
    assert name_collisions({"probe": core_named}, _core_tool_names()) == ["probe.list_databases: a core tool's name"]
    twice = ast.parse(GOOD_MODULE.replace("def probe_write(", "def probe_read("))
    assert name_collisions({"probe": twice}, set()) == ["probe.probe_read: defined twice"]
    other = ast.parse(GOOD_MODULE)
    assert name_collisions({"a": other, "b": other}, set()) == [
        "b.probe_delete: also defined by a", "b.probe_read: also defined by a", "b.probe_write: also defined by a"]


# --- The hook at run time ---------------------------------------------------------------------


class _Records(logging.Handler):
    """Collects log records. Attached to the `mcp` logger, which the server's root-logger reset
    (`logging.basicConfig(force=True)` at import) does not touch."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def probed_server(monkeypatch):
    """Reload the server with one probe add-on module in place of the shipped ones, and a custom hook
    that registers a tool under the probe's name; restore both afterwards."""
    seen = {}
    records = _Records()
    mcp_logger = logging.getLogger("mcp")
    mcp_logger.addHandler(records)

    def register(mcp, client, config, tool_result):
        seen.update(mcp=mcp, client=client, config=config, tool_result=tool_result)

        @mcp.tool()
        @tool_result
        def addon_probe_shared() -> dict:
            """Add-on probe tool."""
            return {"from": "addon"}

        if config.enable_writes:

            @mcp.tool()
            @tool_result
            def addon_probe_write() -> dict:
                """Add-on probe write tool."""
                return {"from": "addon"}

    def custom(mcp, client, config, tool_result):
        @mcp.tool()
        @tool_result
        def addon_probe_shared() -> dict:
            """Custom probe tool."""
            return {"from": "custom"}

    probe = types.ModuleType("vams_mcp.addon_tools.probe")
    probe.register = register
    monkeypatch.setattr(addon_tools, "ADDON_TOOL_MODULES", (probe,))
    monkeypatch.setattr(custom_tools, "register_custom_tools", custom)
    importlib.reload(server_module)
    try:
        yield server_module, seen, records
    finally:
        mcp_logger.removeHandler(records)
        monkeypatch.undo()
        importlib.reload(server_module)


@pytest.mark.asyncio
async def test_each_add_on_module_is_handed_the_servers_objects(probed_server):
    server, seen, _ = probed_server
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert "addon_probe_shared" in names
    assert seen["mcp"] is server.mcp
    assert seen["client"] is server.CLIENT
    assert seen["config"] is server.CONFIG
    assert seen["tool_result"] is server.tool_result


@pytest.mark.asyncio
async def test_an_add_on_write_tool_follows_the_servers_write_gate(probed_server):
    server, _, _ = probed_server
    assert server.CONFIG.enable_writes is False
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert "addon_probe_write" not in names


@pytest.mark.asyncio
async def test_the_add_on_hook_runs_before_the_custom_hook(probed_server):
    """The SDK keeps the first registration, so a fork's tool named like an add-on tool is the one
    dropped: the add-on lane is registered first."""
    server, _, records = probed_server
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert tools["addon_probe_shared"].description == "Add-on probe tool."
    assert "Tool already exists: addon_probe_shared" in records.messages


@pytest.mark.asyncio
async def test_the_reload_restores_the_shipped_add_on_modules():
    # Control for the fixture's teardown: the default server carries no probe tool, and the shipped
    # add-on tools are registered again.
    names = {tool.name for tool in await server_module.mcp.list_tools()}
    assert "addon_probe_shared" not in names
    shipped = {
        node.name for tree in _addon_module_trees().values()
        for tier, node in _placed_tools(_register_def(tree)) if tier == "read"
    }
    assert shipped and shipped <= names
    assert server_module.register_addon_tools is addon_tools.register_addon_tools
