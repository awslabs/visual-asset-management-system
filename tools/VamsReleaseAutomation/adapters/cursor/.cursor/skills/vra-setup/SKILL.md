---
name: vra-setup
description: First-run setup of the VAMS release automation shared home, or join an existing one. Usage /vra-setup [home-folder | --join <path-or-git-url>]
disable-model-invocation: true
---

Read `skill/SKILL.md`, then follow `skill/workflows/W0-setup.md` exactly. The home folder or join target is what I
typed after the command (optional).

Ask the setup questions a few at a time, never ask for secret values, write the answers file outside the shared
home, and confirm it with me before running `vra setup`.
