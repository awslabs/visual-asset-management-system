---
inclusion: always
---

# Workflow

For every request, pick the workflow from the index in
`#[[file:skill/SKILL.md]]` section 7, read that file in `skill/workflows/`,
and follow its numbered steps. Use the `@vra` MCP tools; branch on exit codes
per SKILL.md section 3.

Delegate to subagents: `vra-conflict-resolver` (exit 20 with conflicts),
`vra-failure-triager` (exit 30 / 1), `vra-reporter` (end of run).
