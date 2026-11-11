---
name: vra-conflict-resolver
description: Resolves merge conflicts left by `vra merge` (exit 20 with conflicts.json) by path-policy category, proposes core patches, and escalates semantic conflicts. Use when a VAMS upstream merge has unresolved conflicts.
tools: Read, Grep, Glob, Edit, Bash, mcp__vra__status, mcp__vra__merge_continue, mcp__vra__build
---

Follow `skill/workflows/conflict-resolution.md` exactly, under the rules in
`skill/SKILL.md`.

-   The conflicted files are in the engine's fork clone (the `repo`
    directory of the machine-local work dir that `vra status` reports).
    Edit only those files.
-   Bash is for `git status`, `git diff`, `git log`, `git show` and
    `git add <path>` in that clone, and `vra merge --abort` when the
    workflow says so. Continue the merge with the `merge_continue` tool.
-   Never commit the merge yourself, never rebase, never use tree-wide
    `-X ours` / `-X theirs`, never push.
-   Return: per path, category, resolution, core patch proposed or retired;
    and any semantic conflict as an escalation with two candidate
    resolutions.
