---
description: First-run setup of the VAMS release automation shared home, or join an existing one
argument-hint: "[home-folder | --join <path-or-git-url>]"
allowed-tools: Bash(vra setup:*), Bash(vra --json setup:*), mcp__vra, Read, Write
---

Read `skill/SKILL.md`, then follow `skill/workflows/W0-setup.md` exactly.

Home folder or join target (optional): $ARGUMENTS

Ask the setup questions a few at a time, never ask for secret values, write
the answers file outside the shared home, and confirm it with me before
running `vra setup`.
