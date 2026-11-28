"""MCP tool annotations, one per gate tier (Mandatory Rule 12 in ``tools/VamsMCP/CLAUDE.md``).

Every tool passes exactly one of these to ``@mcp.tool(title=..., annotations=...)``, chosen by the
gate block it is registered in — never by what the tool seems to do:

* read block (always registered)               -> ``READ`` (and ``STOP_OWN_SEARCH``, for
  ``vams_audit_stop`` only)
* ``if CONFIG.enable_writes:`` block            -> ``WRITE``, or ``COMPUTE`` for a tool that starts,
  resumes, extends or stops billed compute
* ``if CONFIG.enable_destructive:`` block       -> ``DESTRUCTIVE``, or ``DESTRUCTIVE_COMPUTE`` for one
  that terminates compute

``openWorldHint`` is stated on every constant because the MCP specification defaults it to true: a
tool that only talks to this deployment's VAMS API sets it false, and the compute tools set it true.
The hints are advisory for the client (auto-approval, confirmation prompts); the gates and the
deployment's authorization stay the controls. ``tests/test_agent_surface.py`` checks every core
and add-on tool against its block.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
COMPUTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
# Stops the caller's own audit search: not read-only, removes no data, and repeating it changes nothing
# further. Registered in the read block (the audit set is the only gate); only `vams_audit_stop` uses it.
STOP_OWN_SEARCH = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE_COMPUTE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

# The constant names allowed in each gate tier, for the layout tests.
TIER_ANNOTATIONS = {
    "read": ("READ",),
    "write": ("WRITE", "COMPUTE"),
    "destructive": ("DESTRUCTIVE", "DESTRUCTIVE_COMPUTE"),
}
