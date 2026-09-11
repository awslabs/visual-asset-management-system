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
