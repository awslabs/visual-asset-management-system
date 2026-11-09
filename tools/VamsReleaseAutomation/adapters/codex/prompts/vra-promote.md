---
description: Promote the same VAMS build from one environment to the next (approval gated)
argument-hint: "FROM=<env> TO=<env> BUILD=<buildId> APPROVAL=<ref>"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`)
and follow `skill/workflows/W7-promote.md` exactly.

From: $FROM
To: $TO
Build: $BUILD
Approval: $APPROVAL

Show me the dry-run diff summary and wait for my go before the real promotion.
