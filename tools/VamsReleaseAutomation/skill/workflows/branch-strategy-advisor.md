# Branch strategy advisor

Purpose: explain the fork branch model to the customer and help them set up
their remotes and branches. Read-only by default; any push needs a human.

-   Trigger: W0 setup, `vra sync` / `vra merge` reports a missing branch, or
    the human asks how the fork should be organized.
-   Gate: creating or pushing branches and tags **[HUMAN]**.
-   Inputs: fork URL, trunk name, tag prefix from the ledger.
-   Outputs: an explanation; a checklist of git host settings; optionally the
    initial branches created.

## The model

| Ref                                                  | Contents                                                                                                                      | Moved by                   |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| `upstream/main`, `upstream/development`              | awslabs VAMS                                                                                                                  | read-only remote           |
| `vendor/vams-main`                                   | fast-forward-only mirror of upstream `main`, release tags kept                                                                | `vra sync`                 |
| `vendor/vams-development`                            | fast-forward-only mirror of upstream `development`                                                                            | `vra sync`                 |
| `main` (customer trunk, name configurable)           | `vendor/vams-main` merged + customer fork-lane code                                                                           | merge PRs only             |
| `preview/main`                                       | `vendor/vams-development` merged + customer fork-lane code; feeds `dev-preview` only; never promoted, never merged into trunk | `vra merge --lane preview` |
| `sync/<lane>-<ref>`                                  | short-lived merge branch for one upstream ref                                                                                 | `vra merge`                |
| `feature/*`                                          | customer feature work, PR into trunk                                                                                          | customer developers        |
| tags `<tagPrefix>v<vams>-<n>` (e.g. `cust-v2.7.0-3`) | immutable promotable builds                                                                                                   | `vra build`                |

Why: vendor mirrors keep upstream shas intact so every fork commit can be
traced to an upstream release; merges (never rebases) keep that history; the
preview lane lets the customer see upcoming VAMS changes without risking the
promotion chain; there are no per-environment branches because the same build
bundle is promoted.

## Explaining it

1. Show the table and the "Why" paragraph.
2. Point out the fork lanes (`conflict-resolution.md`) and that customer
   code outside them is tracked in `core-patches.md` and should trend to zero.
3. If the customer currently has environment branches (`dev`, `test`,
   `prod`), explain the migration: pick the branch that matches prod as the
   new trunk, merge the others into it via PR, and from then on promote
   builds, not branches. You MUST NOT delete their old branches.

## Setting it up

1. Read-only checks: `git remote -v`, `git branch -a`, `git tag -l '<tagPrefix>*'`.
2. Report what exists and what is missing.
3. **[HUMAN]** On a yes, add the upstream remote if missing:
   `git remote add upstream https://github.com/awslabs/visual-asset-management-system.git`
4. Run `vra sync --json`; the engine creates the vendor mirrors locally.
5. **[HUMAN]** Ask before publishing branches. On a yes, push each new branch
   with a plain `git push origin <branch>` (never force).
6. Recommend git host settings (the customer's admin applies them):
    - protect trunk: PR required, at least one reviewer, status checks from
      `vra build`, no force pushes, no deletion;
    - protect `vendor/*`: only the automation identity may push; fast-forward
      only; no force pushes;
    - protect tags matching `<tagPrefix>*` from deletion and moves; signed tags
      where the host supports it;
    - `preview/main`: automation identity only.
