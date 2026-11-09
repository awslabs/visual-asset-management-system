---
description: Promote the same VAMS build from one environment to the next (approval gated)
argument-hint: "<from-env> <to-env> <buildId> <approval-ref>"
allowed-tools: mcp__vra, Read
---

Read `skill/SKILL.md`, then follow `skill/workflows/W7-promote.md` exactly.

From, to, build, approval: $ARGUMENTS

Show me the dry-run diff summary and wait for my go before the real promotion.
