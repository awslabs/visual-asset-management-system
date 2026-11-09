# Conflict resolution

Purpose: resolve the merge conflicts `vra merge` could not resolve by path
policy, keep the fork auditable, and escalate anything semantic.

-   Trigger: `vra merge` (or `vra watch`) exits `20` and the run has
    `<runDir>/conflicts.json`.
-   Gate: semantic conflicts and new core patches need a human **[HUMAN]**.
-   Inputs: `conflicts.json`, the ledger's `path-policy.yaml`, and the
    engine's fork clone (`<work dir>/repo`, default
    `~/.cache/vra/<home id>/repo`) on `sync/<lane>-<ref>` in the merge state the
    engine left. A parked merge belongs to the machine that ran it: its
    conflicts are in that machine's fork clone, so resolve them there.
-   Outputs: resolved files staged, `vra merge --continue --run-id <run-id>` done, affected suites
    run, `core-patches.md` entries proposed to the human, a resolution summary
    for the PR.

## Path categories

Classify every conflicted path. The ledger's `path-policy.yaml` wins over
this table when they differ.

| Category              | Paths                                                                                                                                                                                                                                                                                                                                                                                                                    | Resolution                                                                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Fork lane             | `backend/backend/custom/`, `backend/backend/handlers/custom/`, `backend/backend/models/custom/`, `infra/lib/nestedStacks/custom/`, `infra/common/custom*.ts`, `infra/config/custom/`, `web/src/custom/`, `web/src/visualizerPlugin/**/custom*`, `tools/VamsCLI/vamscli/custom_*`, `tools/VamsCLI/vamscli/commands/custom/`, `tools/VamsCLI/vamscli/utils/custom_api_client.py`, `tools/VamsMCP/vams_mcp/custom_tools.py` | **fork** (keep the customer side)                                                                                                          |
| Ledger-managed config | `infra/config/config.json`, `infra/config/csp/*`, SAML/OIDC config, any file under an env `overlay/`                                                                                                                                                                                                                                                                                                                     | **ledger**: keep upstream in the repo; values come from the ledger overlay at synth. Run `config-upgrade.md`                               |
| Changelog-style       | `CHANGELOG.md`, `documentation/**/revisions.md`                                                                                                                                                                                                                                                                                                                                                                          | **union**, upstream block first                                                                                                            |
| Lockfiles             | `package-lock.json`, `**/package-lock.json`, `poetry.lock`, `uv.lock`                                                                                                                                                                                                                                                                                                                                                    | **regenerate** (engine)                                                                                                                    |
| Core                  | everything else, including upstream add-on files (`**/addon*`, `infra/lib/nestedStacks/addon/`)                                                                                                                                                                                                                                                                                                                          | no rule: `default: agent`, so the conflict comes to you. Take **upstream** unless the fork side carries a `core-patches.md` entry (step 4) |

## Steps

1. Read `conflicts.json`. For each path note: category, the policy strategy
   the engine tried, upstream commit(s) touching it, and whether the fork
   side has a `core-patches.md` entry.
2. Fork-lane conflicts: take the fork side. Then read the upstream change to
   the matching core aggregator (for example `common/apiRoutes.py`,
   `routes.tsx`) — if upstream changed the custom file's contract (renamed an
   export, changed a signature), this is semantic: go to step 6.
3. Core conflicts with no core-patch entry: take upstream, unless the fork
   side holds a real customer change (not just an older upstream text). The
   engine's `merge.core-audit` step stops a merge whose fork edits to core
   files are not registered, so an unregistered fork change here is a gap:
   **[HUMAN]** ask whether to register it as a core patch (step 7) or drop it.
4. Core conflicts WITH a core-patch entry (the fork intentionally changed a
   core file):
   a. Read the patch's stated reason in `core-patches.md`.
   b. If upstream now provides the behaviour (a new extension point, a fix),
   take upstream and mark the entry "retired by <ref>".
   c. If the patch is mechanical to re-apply on the new upstream text (same
   intent, moved lines), re-apply it minimally and update the entry.
   d. Otherwise semantic: step 6.
5. Ledger-managed config: take upstream in the repo and run
   `config-upgrade.md` for each affected environment.
6. Semantic conflict (behaviour, contract or data-shape changes on both
   sides; you are not confident of the merged intent): **[HUMAN]** stop and
   escalate with: path, upstream intent (commit message + diff summary), fork
   intent (core-patch reason or commit), two candidate resolutions with
   their risk, and which suites cover the path. Leave the merge in progress
   or, if the human prefers, run `vra merge --abort --run-id <run-id> --json`.
7. Any edit you make outside the fork lanes that is not a pure "take
   upstream" MUST get a `core-patches.md` entry: path, reason, upstream ref it
   was applied on, owner, and "remove when" condition. **[HUMAN]** A new core
   patch needs the human's agreement: propose the entry text, and the human
   adds it to `ledger/core-patches.md` in the shared home and commits it
   before step 8 (the engine's `merge.core-audit` step reads it).
8. Stage the resolved files (`git add <paths>`), then run
   `vra merge --continue --run-id <run-id> --json`. You MUST NOT `git commit` the merge yourself,
   MUST NOT rebase, and MUST NOT use `-X ours` / `-X theirs` across the tree.
9. Run the affected suites before handing off: `vra build --lane <lane> --ref sync/<lane>-<ref> --json`
   covers lint, unit and synth. A failure here returns you to step 1 for the
   failing paths; a second failure on the same path is escalated (step 6).
10. Write the resolution summary for the PR (W2 step 8): per path, category,
    resolution, core patch added/retired.

## Headless (cron) runs

With no human present you MAY apply steps 2, 3, 5 and 8-9 only. Anything that
needs steps 4c, 6 or 7 MUST stop the run at `NEEDS_HUMAN` with the
escalation from step 6 in your final output.
