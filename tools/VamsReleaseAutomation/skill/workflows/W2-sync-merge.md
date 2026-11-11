# W2 — Sync and merge

Purpose: fast-forward the vendor mirrors, merge the new upstream ref into a
`sync/<lane>-<ref>` branch off the customer trunk, resolve conflicts, and get
the result reviewed.

-   Trigger: W1 found something new, or the human asks to merge a VAMS release
    or the development preview.
-   Gate: none for the preview lane (auto). For the release lane the merge into
    the customer trunk follows the customer's PR policy **[HUMAN]**.
-   Inputs: lane (`release` | `preview`), optional ref (tag such as `v2.7.0`, or
    a development sha).
-   Outputs: updated `vendor/vams-main` / `vendor/vams-development`; branch
    `sync/<lane>-<ref>`; `conflicts.json` if unresolved; a merge PR.

## Steps

1. Run `vra sync --json`. Record `newRelease` and `newPreviewCommit`. If the
   human named a ref that `vra sync` did not report, confirm it exists on the
   vendor branch before continuing.
2. Read `branch-strategy-advisor.md` if the vendor branches or trunk do not
   exist yet (`vra sync` exit `20` with a missing-branch reason).
3. Run `vra merge --lane <lane> [--ref <ref>] --json`.
4. Branch on the exit code:
    - `0`: clean merge (path policy resolved everything). Go to step 6.
    - `20` from the `merge.core-audit` step ("fork edit(s) outside the fork
      lanes are not registered"): the fork changed core files that no path
      rule and no `core-patches.md` entry covers. **[HUMAN]** List them for
      the human; each needs a `core-patches.md` entry (path, reason, owner,
      remove-when) or a path-policy rule before the merge continues
      (`vra merge --continue --run-id <run-id> --json`). You MUST NOT add an
      entry the human has not agreed to.
    - `20` with `conflicts.json` (`<runDir>/conflicts.json`): follow
      `conflict-resolution.md`, which ends with
      `vra merge --continue --run-id <run-id> --json` or
      `vra merge --abort --run-id <run-id> --json`.
    - other: SKILL.md section 3.
5. If you edited any file outside the fork lanes while resolving, confirm the
   `core-patches.md` entry exists (conflict-resolution step 7).
6. If the new ref changes `infra/config/config.template.*.json`, run
   `config-upgrade.md` for every environment of the lane.
7. If the version delta crosses a data-migration directory, note it for W4
   (`data-migration.md` step 1) in the PR description.
8. Getting the branch reviewed. On exit `0` the engine has already pushed
   `sync/<lane>-<ref>` (plain push, never forced) in its `merge.publish` step:
    - Release lane: the engine opened (or found) the pull request through the
      `githost` adapter. Record the top-level `prUrl` (same as
      `result.publish.pr.url`). The engine's PR body lists the upstream ref,
      commit, auto-resolved conflicts and lockfiles to regenerate. Add a PR
      comment (or ask the human to) with what the engine cannot know: how each
      agent-resolved conflict was resolved, core patches added, config keys
      added, migrations ahead.
    - `githost.type: local` returns no `prUrl`. Give the human the branch name
      and target branch; they open the review in their tool. You MUST NOT push
      or open PRs yourself.
9. Preview lane: with `autoMerge: true` the engine fast-forwards
   `preview/main` (`result.publish.fastForwarded`) and opens no PR. It is never
   merged into the customer trunk. You MUST NOT open a PR from `preview/main`
   into trunk.
10. Continue with W3. `vra watch` builds the release-lane sync branch and
    deploys it to `dev` before the PR merges; that is expected. Promotion past
    `dev` (W7) is blocked by the engine until the PR is merged into the trunk
    **with a merge commit** (not squash or rebase), because `vra promote`
    requires the build commit to be an ancestor of the trunk. Tell the human
    this when you hand over the PR.

## Stop conditions

-   Semantic conflict escalated (conflict-resolution step 6).
-   The human declines the push / PR.
-   `vra merge --abort` was needed.
