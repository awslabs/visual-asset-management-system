---
name: vra-data-copy
description: Replace a lower VAMS environment's data with a full copy of a higher one (approval gated). Usage /vra-data-copy <from-env> <to-env> <approval-ref> [database-ids]
disable-model-invocation: true
---

Read `skill/SKILL.md`, then follow `skill/workflows/W8-data-copy.md` exactly. Source, target, approval and optional
databases are what I typed after the command.

State the destructive impact and wait for my explicit confirmation.

The approval reference is the record id or ledger path of an approval whose `build` field is `data-copy:<from-env>`,
filed under `approvals/<to-env>/` (default `approvals/<to-env>/data-copy_<from-env>.yaml`). The engine refuses
prod-class targets; never edit `dataCopy.allowAsTarget` to get around it.
