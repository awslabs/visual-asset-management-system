---
description: Deploy an existing build to one VAMS environment, then test it
argument-hint: "<env> <buildId> [approval-ref]"
allowed-tools: mcp__vra, Read
---

Read `skill/SKILL.md`, then follow `skill/workflows/W4-deploy.md` and
`skill/workflows/W5-test.md`. Delegate test-failure classification to the
`vra-failure-triager` subagent.

Env, build, approval: $ARGUMENTS

This message is my request for this deploy. It is not an approval record; the
engine checks that separately.
