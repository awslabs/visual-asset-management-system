# Three-env live test (CodePipeline tier)

One account and region (tools account = the three envs' account), three envs with three IAM modes, the release
pipeline created by `vra pipeline deploy` from the ledger graph, source = a personal fork's `test/fork-main` branch
through a CodeConnections connection the stack creates. Placeholder values only: replace `111111111111`,
`example-user` and the secret names.

| Env    | Promotes from | Approval | VAMS IAM mode (the IAM lane's ledger example)         |
| ------ | ------------- | -------- | ----------------------------------------------------- |
| `dev`  | (root)        | no       | standard roles                                        |
| `test` | `dev`         | yes      | permission boundary + role name prefix                |
| `prod` | `test`        | final    | IT pre-created roles (IT package of the IAM commands) |

## Files

| File                  | What it is                                                                                                      |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| `vra-automation.yaml` | The `automation` block to merge into the ledger's `vra.yaml` (mode `pipeline`)                                  |
| `pipeline.json`       | What `vra pipeline plan` renders from it (without the machine-local `ledgerDir`); the jest suite synthesizes it |
| `ledger-graph.json`   | `vra --json ledger graph` of the three-env ledger                                                               |
| `topology-digest.txt` | The `TopologyDigest` stack output for that graph; the engine and CDK tests both check it                        |

## Prerequisites

-   The fork (for example `example-user/visual-asset-management-system`, created from the official repository) with
    branches `test/fork-main` (trunk: the release under test plus a small fork-lane change) and
    `test/vendor-vams-main` (stands in for upstream `main`: a new tag on it simulates an upstream release). Actions
    stay disabled on the fork.
-   The ledger with the three envs (deploy roles `vra-deploy-<env>`, each trusting only its CodeBuild tier, see
    `../../README.md` Setup), `lanes.release.source: test/vendor-vams-main`, `trunk: test/fork-main`, approvers of
    `test` and `prod` as assumed-role ARN globs (CodePipeline records approvers as ARNs).
-   A private home repository and Secrets Manager secrets `vra/github-home-token` and `vra/github-token`, the SNS
    topic, the CDK bootstrap of the account, and tools-account credentials for the operator.

## Run

1.  Merge `vra-automation.yaml` into `vra.yaml`; `vra ledger validate` must pass.
2.  `vra pipeline plan`: review the diff, the stages (Deploy-dev, Approve-test, Deploy-test, Approve-prod,
    Deploy-prod), and the IT package for the pipeline's roles in `state/iam/pipeline/<n>/IT-ROLES.md`.
3.  `vra pipeline deploy` (a person, at a terminal: the first deploy is human-only), then
    `vra pipeline push-source --package` so the pipeline's `Vra` source and the watch job have the package zip.
    With S3 sources the stack created, only the watch tier may write them: list the operator's role ARN in
    `automation.pipeline.sourceWriterArns` before the deploy, or this first upload is refused (AccessDenied).
4.  Complete the CodeConnections handshake once in the console (output `ConnectionArn`: the connection is PENDING
    until then), then release a change in the pipeline (or push to `test/fork-main`).
5.  Prove, with `vra pipeline status` after each step:
    -   a new commit on `test/fork-main` flows to dev automatically (Source, Validate, Build, Deploy-dev, Test-dev);
    -   a new tag on `test/vendor-vams-main` is picked up by the daily watch (or start the `vra-watch` CodeBuild
        project by hand): it opens a `sync/release-*` PR into `test/fork-main`;
    -   Approve-test and Approve-prod gate test and prod, and an approver who started the execution is refused;
    -   a failing dev test rolls dev back (Test-dev fails, `vra rollback` ran, nothing promotes);
    -   the IT-package loop: with prod on pre-created roles and one role missing, Promote-prod fails BLOCKED_ON_IT
        with `vra-out/BLOCKED_ON_IT.md` in its artifact; after IT creates the role and the IAM apply step records it,
        **Retry failed actions** resumes the same run.
6.  Clean up: delete the `test/*` branches of the fork on the owner's word.
