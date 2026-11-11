---
description: Roll a VAMS environment back to its previous good build, optionally restoring data
argument-hint: "ENV=<env> [BUILD=<buildId>] [CHECKPOINT=<id>]"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`)
and follow `skill/workflows/W6-rollback.md` exactly.

Environment: $ENV
Target build (optional): $BUILD
Checkpoint to restore (optional): $CHECKPOINT

Tell me about forward-only changes before rolling back.
