# W9 — Permission loop (BLOCKED_ON_IT)

Purpose: in strict-permission environments, turn every permission gap into a
precise IT request, park the run, and continue once IT has acted.

-   Trigger: any `vra` command exits `10`.
-   Gate: the customer's IT / cloud administrators.
-   Inputs: run id.
-   Outputs: `IT-REQUEST-<n>.md` + `.json` handed to the human; resumed run.

The engine classifies `AccessDenied*`, `UnauthorizedOperation`,
`*NotAuthorized*`, CloudFormation "is not authorized to perform" events and
KMS / SCP / permission-boundary hints, decodes encoded messages where it can,
writes the request, and marks the step `blocked`.

## Steps

1. Run `vra status <run-id> --json`. Read `itRequests[]` and the blocked step.
2. Open the newest `<runDir>/it-requests/IT-REQUEST-<n>.md`.
3. Summarize for the human, without editing the file:
    - principal (role ARN), environment, blocked step,
    - actions and resources requested,
    - the likely blocker type the engine identified (identity policy, SCP,
      permission boundary, KMS key policy, resource policy),
    - absolute paths of the `.md` and `.json` files.
4. Say: "Send IT-REQUEST-<n> to your IT / cloud administrators. Tell me when
   it is done and I will resume run <run-id>."
5. **Stop.** You MUST NOT:
    - retry the step, switch roles or profiles, or use broader credentials;
    - propose `*` actions or resources beyond what the engine wrote;
    - edit the policy JSON (if the human wants it narrower or wider, they
      discuss it with IT; the engine re-simulates on resume regardless);
    - continue other steps of the same run.
6. **[HUMAN]** When the human confirms IT acted, run `vra resume <run-id> --json`.
7. Branch:
    - `10`: a new `IT-REQUEST-<n+1>` exists. Diff it against `<n>` and tell
      the human exactly what is still missing (often a second action surfaced
      only after the first was granted). Go to step 3.
    - any other code: SKILL.md section 3.
8. Batching: during W0 preflight of several environments, you MAY collect
   all IT requests and hand them off together.

## Registered asset buckets

VAMS operators can register asset buckets at any time, including
customer-named buckets and buckets in other accounts with their own KMS
keys. Every backup checkpoint, restore, data copy and deploy with data
migrations lists them again first. When an IT request has a "Registered
asset buckets" section (kind `registered-bucket`, or a step named
`*.registered-buckets*`):

1. In step 3, also give the human, per bucket and key: the bucket name, its
   KMS key, whether it is newly registered since the last check, and the
   exact actions. Say that a bucket or key in another account also needs its
   bucket policy or key policy changed by that account's owner, and that the
   role stacks take the ARNs as `AdditionalBucketArns` /
   `AdditionalKmsKeyArns`.
2. Tell the human the run resumes only after the ARNs are also recorded in
   `envs/<env>/env.yaml` `registeredBuckets` (`granted: true`, `bucketArns`,
   `kmsKeyArns`) and the ledger is committed: the simulation cannot see
   another account's bucket or key policy, so the ledger entry is the
   human's statement that access was granted. **[HUMAN]** The human (or IT)
   edits it; you MUST NOT add bucket ARNs or set `granted: true` yourself.
3. `vra resume <run-id>` re-checks the buckets. A further `10` naming a
   different bucket means another bucket was registered in the meantime;
   report it as new.
4. A run that ended `0` but lists an open IT request marked "pending, did not
   block this run" (a deploy that touched no bucket data) still needs steps
   3-5 before the next bucket phase on that env; do not resume that run.

## IT role package (strict-IAM envs)

An env whose `env.yaml` `iam.mode` is `boundary`, `precreated` or
`bootstrap` (or a combination) deploys with roles or a permission boundary
that IT provides. `vra preflight` and `vra build` stop with exit `10` and an
IT request of kind `iam-package` when the env has no package, the package is
stale (new roles after an upstream merge, a boundary change) or the latest
package is not applied yet.

1. Read the request. If it says to run `vra iam plan`, run the `iam_plan`
   tool for the env (or `vra iam plan --env <env> --json`), with `ref` set
   to the ref the request names. It writes `state/iam/<env>/<n>/`.
2. In step 3 above, hand over `state/iam/<env>/<n>/IT-ROLES.md` (and say
   that `it-roles.yaml`, `roles.csv` and `delta.json` are next to it). Say
   how many roles are new or changed since the previous package.
3. **[HUMAN]** IT creates the roles (or the boundary) and returns names or
   ARNs. The human copies `it-response.template.yaml` to `it-response.yaml`,
   fills it in, runs `vra iam apply --env <env> --from it-response.yaml`
   themselves (an approval-gated env also needs an approval record scoped
   `iam-apply`), and commits and pushes the ledger change through review.
   You MUST NOT fill in role names or run the apply command.
4. When the human says it is done, run the `iam_verify` tool (or
   `vra iam verify --env <env> --json`) and report problems and anything
   under `notChecked`. Then resume the run.
5. Exit `20` on a build that says a role carries no permission boundary:
   the fork's VAMS version does not apply the boundary (GitHub issue #431).
   Tell the human the two remedies: the core patch in `core-patches.md`
   (`iam-role-transform-boundary`) or `iam.mode: precreated`. Do not retry.

## Stop conditions

-   Always after step 5 until the human confirms.
