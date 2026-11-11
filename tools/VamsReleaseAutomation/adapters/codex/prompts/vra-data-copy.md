---
description: Replace a lower VAMS environment's data with a full copy of a higher one (approval gated)
argument-hint: "FROM=<env> TO=<env> APPROVAL=<ref> [DATABASES=<ids>]"
---

Load the vams-release-automation skill named in AGENTS.md (`skill/SKILL.md`)
and follow `skill/workflows/W8-data-copy.md` exactly.

Source: $FROM
Target: $TO
Approval: $APPROVAL
Databases (optional): $DATABASES

State the destructive impact and wait for my explicit confirmation.

The approval reference is the record id or ledger path of an approval whose `build` field is `data-copy:<from-env>`, filed under `approvals/<to-env>/` (default `approvals/<to-env>/data-copy_<from-env>.yaml`). The engine refuses prod-class targets; never edit `dataCopy.allowAsTarget` to get around it.
