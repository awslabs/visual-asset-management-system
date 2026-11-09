---
description: Resume a parked VAMS release run after IT or a human acted
argument-hint: "RUN=<run-id>"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`).
I confirm the action the run was waiting for is done. Follow
`skill/workflows/W9-permission-loop.md` from step 6 for run $RUN (or SKILL.md
section 3 if the run was parked for another reason).

If the run is waiting for manual work (its message ends "When done, run `vra resume <run-id> --confirm`": a manual migration step or an A/B DNS switch), do not run it: `--confirm` records that a person did the work, so tell me the exact command and I will run `vra resume <run-id> --confirm` myself. Otherwise call the `resume` tool of the vra MCP server for the run.
