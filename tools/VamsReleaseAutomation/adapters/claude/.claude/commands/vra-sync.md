---
description: Sync vendor mirrors and merge a VAMS release or preview ref into the fork
argument-hint: "<release|preview> [ref]"
---

Read `skill/SKILL.md`, then follow `skill/workflows/W2-sync-merge.md` exactly.
Delegate conflicts to the `vra-conflict-resolver` subagent
(`skill/workflows/conflict-resolution.md`).

Lane and optional ref: $ARGUMENTS

`vra merge` pushes the sync branch and opens the PR itself (plain push, never forced); give me the `prUrl` it returns. Do not push or open PRs yourself. Remind me to merge the PR with a merge commit, not squash or rebase.
