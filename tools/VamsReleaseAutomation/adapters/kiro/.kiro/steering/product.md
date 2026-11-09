---
inclusion: always
---

# Product: VAMS release automation

This workspace runs the release lifecycle of a customer fork of the Visual
Asset Management System (VAMS) with the `vra` engine. The canonical agent
instructions are `#[[file:skill/SKILL.md]]`; workflow SOPs are in
`skill/workflows/`. This steering file only orients you; the skill wins on
any difference.

-   Upstream: awslabs/visual-asset-management-system (`main` = releases,
    `development` = preview).
-   The engine's settings and state live in the shared home folder
    (`vra-home.yaml`, `ledger/`, `state/`), outside this workspace. Read it
    through the `vra` MCP tools, never edit it.
-   Customer code lives in the VAMS fork lanes (`**/custom/**`, `custom_*`).
