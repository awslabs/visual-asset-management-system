---
description: Show VAMS release automation run status and the next decision (read-only)
argument-hint: "[RUN=<run-id>]"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`).
Read-only: call the `status` tool of the vra MCP server (run $RUN, or all runs) and, for
the run(s) shown, the `report` tool. Do not change anything.
Summarize per skill section 8: outcome, identifiers, the decision needed.
