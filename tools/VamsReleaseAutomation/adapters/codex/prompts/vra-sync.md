---
description: Sync vendor mirrors and merge a VAMS release or preview ref into the fork
argument-hint: "LANE=release|preview [REF=<tag-or-sha>]"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`)
and follow `skill/workflows/W2-sync-merge.md` exactly, using
`skill/workflows/conflict-resolution.md` for any conflicts.

Lane: $LANE
Ref (optional): $REF

`vra merge` pushes the sync branch and opens the PR itself (plain push, never forced); give me the `prUrl` it returns. Do not push or open PRs yourself. Remind me to merge the PR with a merge commit, not squash or rebase.
