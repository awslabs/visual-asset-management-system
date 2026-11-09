---
name: vra-sync
description: Sync vendor mirrors and merge a VAMS release or preview ref into the fork. Usage /vra-sync <release|preview> [ref]
disable-model-invocation: true
---

Read `skill/SKILL.md`, then follow `skill/workflows/W2-sync-merge.md` exactly, and
`skill/workflows/conflict-resolution.md` for conflicts. The lane and optional ref are what I typed after the
command.

`vra merge` pushes the sync branch and opens the pull request itself (plain push, never forced); give me the `prUrl`
it returns. Do not push or open pull requests yourself. Remind me to merge the pull request with a merge commit,
not squash or rebase.
