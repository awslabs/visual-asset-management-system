"""Suite-wide isolation for the VAMS MCP server tests.

`vams_mcp.server` reads its configuration ONCE, at module level: `CONFIG = Config.from_env()` runs on
import, and the write/destructive tool blocks are `if CONFIG.enable_writes:` / `if
CONFIG.enable_destructive:` guards evaluated at that same moment. No fixture can reach that read —
by the time the first fixture runs, the gates have already been decided.

So the two tests that verify the gates actually withhold tools
(`test_write_tools_gated_off_by_default`, `test_destructive_tools_gated_off_by_default`) were
describing the shell rather than the code: a developer who exports `VAMS_ENABLE_WRITES=true` for the
MCP server and then runs `pytest` in the same shell sees both fail with
`assert 'create_asset' not in names`, for a reason unrelated to any code change. The likely reaction
is to weaken the two assertions that are the security core of this component. Conversely on CI they
passed while proving only that the runner happens not to export the variables. This is the same class
as the `sys.argv --verbose` defect in the CLI suite: a suite whose outcome varies with its invocation
environment is not a guard.

Clearing the variables here, at conftest import time, is what makes those assertions about the code.
pytest imports every conftest before any test module, so this runs before `vams_mcp.server` is first
imported — and `test_gate_env_is_cleared_for_the_suite` in `test_config.py` asserts it took effect,
so a future reordering that broke the guarantee fails loudly instead of going quiet.

Verify with: `VAMS_ENABLE_WRITES=true VAMS_ENABLE_DESTRUCTIVE=true python -m pytest tests -q`
— the suite must still be green.
"""

import os

import pytest

# Every variable `Config.from_env()` reads. `VAMS_PROFILE` and the pagination pair are included even
# though no gate depends on them: a stray VAMS_PAGE_SIZE changes what `paginate()` sends, and
# VAMS_PROFILE decides which vamscli profile a reloaded module tries to resolve.
GATE_AND_TUNING_ENV_VARS = (
    "VAMS_ENABLE_WRITES",
    "VAMS_ENABLE_DESTRUCTIVE",
    "VAMS_PROFILE",
    "VAMS_MAX_PAGES",
    "VAMS_PAGE_SIZE",
)

for _name in GATE_AND_TUNING_ENV_VARS:
    os.environ.pop(_name, None)

# The vamscli profile lives under $HOME (vamscli.constants.get_config_dir). Point HOME at an empty
# directory BEFORE vams_mcp.server is first imported, so the suite never reads the developer's own
# profile: CI runs with no profile, and a suite that passes only with a signed-in profile on the
# host is not a guard. Every test then
# starts from the same state, nobody signed in, and the `signed_in_by_default` fixture below supplies
# a mocked client to every test that does not ask for the signed-out state.
import tempfile  # noqa: E402

ISOLATED_HOME = tempfile.mkdtemp(prefix="vams-mcp-tests-home-")
os.environ["HOME"] = ISOLATED_HOME
os.environ.pop("APPDATA", None)

# Tests that exercise the signed-out server ask for one of these fixtures; every other test gets a
# mocked client in place of the missing one.
SIGNED_OUT_FIXTURES = {"signed_out", "no_client"}


@pytest.fixture(autouse=True)
def signed_in_by_default(request, monkeypatch):
    """Install a mocked client when the server has none, unless the test targets the signed-out path.

    With the isolated HOME the server starts degraded (`CLIENT is None`), and `@tool_result` then
    answers NotAuthenticated before a tool body runs. Tests of a tool's behaviour mock
    `server.CLIENT` themselves, or register an add-on module with their own mocked client, and expect
    the body to run, so a stand-in client is set here. Tests of the degraded path request
    `signed_out` (or `no_client`), which keeps `CLIENT` None.
    """
    if SIGNED_OUT_FIXTURES & set(request.fixturenames):
        yield
        return
    from unittest.mock import MagicMock

    from vams_mcp import server

    if server.CLIENT is None:
        monkeypatch.setattr(server, "CLIENT", MagicMock(name="conftest-default-client"))
    yield


@pytest.fixture
def no_client(monkeypatch):
    """The server as it starts with nobody signed in (the isolated HOME holds no profile)."""
    from vams_mcp import server

    monkeypatch.setattr(server, "CLIENT", None)
    return server


@pytest.fixture(autouse=True)
def gate_env_stays_cleared():
    """Restore the cleared state after any test that sets one of these.

    `test_gated_tools.py` sets the gate variables deliberately and reloads the module. Its own
    fixture restores them, but this is the backstop: a leaked `VAMS_ENABLE_WRITES` would make the
    default-gate tests in `test_server_tools.py` fail depending on test ORDER, which is the same
    invocation-dependence this file exists to remove.
    """
    before = {name: os.environ.get(name) for name in GATE_AND_TUNING_ENV_VARS}
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture
def server_with_switches():
    """Reload `vams_mcp.server` over a profile whose feature-switch snapshot holds the given switches.

    The add-on tools are registered from the snapshot of the profile the server signs in with
    (`addon_tools.register_addon_tools`), so a test of which add-on tools a server lists must not
    depend on the profile of the machine running the suite. The returned context manager builds the
    server's client as a mock whose `has_feature` answers from `switches` (the real `require_feature`
    and `unwrap_message` on top of it), applies the gate variables, reloads, yields the module, and
    reloads it again on exit with the client restored and the gates cleared.
    """
    import contextlib
    import importlib
    import types
    from unittest.mock import MagicMock

    from vams_mcp import client as client_module
    from vams_mcp import server as server_module

    real_client_class = client_module.VamsClient

    @contextlib.contextmanager
    def _reloaded(switches, enable_writes=False, enable_destructive=False):
        def _client(config):
            mock = MagicMock()
            mock.config = config
            mock.has_feature.side_effect = lambda name: name in set(switches)
            mock.require_feature = types.MethodType(real_client_class.require_feature, mock)
            mock.unwrap_message = real_client_class.unwrap_message
            return mock

        gates = {"VAMS_ENABLE_WRITES": enable_writes, "VAMS_ENABLE_DESTRUCTIVE": enable_destructive}
        for name, enabled in gates.items():
            if enabled:
                os.environ[name] = "true"
            else:
                os.environ.pop(name, None)
        client_module.VamsClient = _client
        try:
            importlib.reload(server_module)
            yield server_module
        finally:
            client_module.VamsClient = real_client_class
            for name in gates:
                os.environ.pop(name, None)
            importlib.reload(server_module)

    return _reloaded
