---
description: Deploy an existing build to one VAMS environment, then test it
argument-hint: "ENV=<env> BUILD=<buildId> [APPROVAL=<ref>]"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`)
and follow `skill/workflows/W4-deploy.md`, then `skill/workflows/W5-test.md`.

Environment: $ENV
Build: $BUILD
Approval (gated envs): $APPROVAL

This message is my request for this deploy. It is not an approval record; the
engine checks that separately.
