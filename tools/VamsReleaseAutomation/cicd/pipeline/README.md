# cicd/pipeline — optional CodePipeline lane (CDK)

A small CDK app for the TOOLS account. It never deploys VAMS itself: every action is a CodeBuild
project running one `cicd/codebuild/buildspec-*.yml`, and every buildspec calls `vra`. Approval
gates, separation of duties, promotion order and change windows are enforced by `vra`; the
pipeline only adds the human-facing approval buttons and the schedule.

## What it creates

| Resource                                                        | Purpose                                                                                                                                                                                           |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vra-release` pipeline (V2, QUEUED)                             | Source (fork trunk, vra package) → Validate (ledger, topology, preflight) → Build → one stage per depth of the ledger's promotion graph, with a manual approval only before envs that require one |
| `vra-preview` pipeline (V2, SUPERSEDED)                         | Source (fork `preview/main`) → Build (preview lane) → Deploy+Test dev-preview. Never promoted                                                                                                     |
| CodeBuild `vra-validate`, `vra-build`                           | Ledger validate, topology check, preflight of the dev-tier envs; unit suites, synth, bundle                                                                                                       |
| CodeBuild `vra-deploy-<env>`, `vra-test-<env>`                  | One pair per env; deploy projects run one build at a time                                                                                                                                         |
| CodeBuild `vra-watch` + EventBridge Scheduler `vra-watch-daily` | The cron entry point (`vra watch`, or sync + merge when this pipeline owns dev)                                                                                                                   |
| CodeConnections connections, S3 source bucket (optional)        | Created when a source names no `connectionArn` / `bucketName`; outputs `ConnectionArn*`, `SourceBucketName`; output `TopologyDigest` for `vra pipeline status`                                    |
| KMS key `alias/vra-pipeline`                                    | Artifact bucket, CodeBuild output, build logs                                                                                                                                                     |
| Artifact bucket + access-log bucket                             | Versioned, TLS-only, public access blocked, lifecycle; also holds the watch build artifacts                                                                                                       |
| EventBridge rules → SNS                                         | Pipeline FAILED / STOPPED and watch build failures to the vra notifications topic                                                                                                                 |
| `vra-codebuild-build-<region>` role                             | Build tier: may assume only the build-only `vra-synth-<env>` roles (CDK lookups), never a deploy role                                                                                             |
| `vra-codebuild-dev-<region>` role                               | Dev tier (Validate, root envs without approval): may assume only those envs' deploy roles                                                                                                         |
| `vra-codebuild-preview-<region>` role                           | Preview tier (dev-preview deploy and test of upstream `development` code): may assume only that env's deploy role; no source writes                                                               |
| `vra-codebuild-watch-<region>` role                             | Watch tier (scheduled sync / merge, `vra pipeline push-source`): assumes no env role; the only role that may write the S3 sources and start the pipelines                                         |
| `vra-codebuild-deploy-<env>-<region>` role                      | One per other env (gated, or promoted from another env): may assume only `vra-deploy-<env>`; used only by that env's projects                                                                     |
| `vra-codebuild-test-<env>-<region>` role                        | Isolated tests (every approval-gated env unless `release.isolateTests` says otherwise): may assume only `vra-test-<env>`                                                                          |

With a state store configured, each tier gets a managed policy of its own: run state below
`stateStore.prefix`, history records, bundles, IT role packages and responses (`iam/`) and pipeline plans
(`pipeline/`) create-only (`s3:if-none-match`), the IT apply record `iam/<env>/applied.json` replaceable (the
engine trusts it only when the create-only `iam-apply` history record matches), approval claims, no deletes,
and lock items limited to run leases plus the tier's own `env#<env>` locks (`env#watch` for the watch tier).
`release.isolateTests` unset isolates the tests of every approval-gated env; `false` is refused when a
prod-class env runs tests.
Each env's tier gets `kms:Verify` on the env's `approvalSigningKeyArn` (never `kms:Sign`). Every tier reads its own pipeline
execution (the run initiator and the approval binding). The jest suite asserts that the build and watch
tiers cannot assume any gated env's role, and that every topology below synthesizes cdk-nag clean.

## Topology

The stages come from the release lane's promotion graph:

-   `ledgerGraph` (path, relative to the config file, of `vra --json ledger graph` output for the home's
    ledger): envs, accounts, `promotesFrom`, `requiresApproval`, `tier` and `requiredSuites` come from the
    engine. Optional `ledgerDir` (a checkout of `ledger/`) adds what the graph does not carry: role names and
    `approvalSigningKeyArn` from `envs/*/env.yaml`. `release.envs` entries then only override those and `test`.
    `ledgerDir` without `ledgerGraph` is refused. Regenerate the file and redeploy after a topology change.
-   Otherwise `release.envs`: `[{name, account, promotesFrom?, requiresApproval?, test?, deployRoleName?, ...}]`.

Envs at the same depth share a stage and run in parallel, each promoting from its own `promotesFrom`
(fan-out never chains siblings). A chain of gated envs becomes one stage pair per wave. An env without
`requiresApproval` gets no approval action and no approval record, whether it is a root or promoted. A gated
root (for example a tenant prod deployed straight from the build) gets an approval and deploys with an
approval record scoped `deploy`. A level's approvals form one stage before its deploys, so siblings wait for
each other's approval; use separate pipelines for prods that must not wait on each other. Test actions run
for envs that another env promotes from (override with `test`), with `release.testSuites` plus every suite the
ledger requires on the env (`requiredSuites`). The Validate stage compares the ledger with
the topology the pipeline was synthesized from (`VRA_PIPELINE_TOPOLOGY`) and fails after a topology change
until the stack is redeployed.

## Home folder and state

Every project clones the home repository (`home.repoUrl`, `home.ref`; `codecommit::<region>://<repo>` uses IAM
auth, an HTTPS host needs `home.tokenSecretName`) with `cicd/codebuild/scripts/vra-home.sh`. Without
`stateStore` the jobs share state through it and push what they write (CodeCommit: `GitPush` on the home
repository for every tier). With `stateStore` (deploy `iam/cloudformation/vra-state-store.yaml` first and copy
its outputs; `vra.yaml` `stateStore.type: s3`) locks, history and run state live in S3 and DynamoDB, and the
jobs only read the home repository.

## Setup

```bash
cd cicd/pipeline
npm ci
cp config/pipeline.example.json config/pipeline.json   # edit; keep the real file in your home repository
npm run typecheck && npm test                          # offline: synth in memory + cdk-nag
npx cdk bootstrap aws://<tools-account>/<region>       # tools account, once
npx cdk deploy                                         # tools account credentials
```

Then, in each env account, deploy `iam/cloudformation/vra-deploy-role.yaml` with ONE trusted principal,
the tier role of that env:

| Env                                               | `TrustedPrincipalArns`                                                          |
| ------------------------------------------------- | ------------------------------------------------------------------------------- |
| root envs without approval, dev-preview           | `arn:<partition>:iam::<tools-account>:role/vra-codebuild-dev-<region>`          |
| every other env (gated, or promoted from another) | `arn:<partition>:iam::<tools-account>:role/vra-codebuild-deploy-<env>-<region>` |

and `CreateSynthRole=true`, `SynthTrustedPrincipalArns=arn:<partition>:iam::<tools-account>:role/vra-codebuild-build-<region>`;
set the resulting `vra-synth-<env>` ARN as `synthRoleArn` in each env.yaml. With `release.isolateTests`, also
`CreateTestRole=true`, `TestTrustedPrincipalArns=arn:<partition>:iam::<tools-account>:role/vra-codebuild-test-<env>-<region>`,
and `testRoleArn` in the env.yaml. Never add the build or dev tier to a gated env's deploy role.

Approvers need `codepipeline:PutApprovalResult` on the `Approve-*` actions and must be listed in
`envs/<env>/env.yaml` `approvers` as the IAM identity CodePipeline records: the full assumed-role
ARN or an ARN glob, e.g. `arn:<partition>:sts::111111111111:assumed-role/ReleaseApprover*/alice@example.com`
(a bare session name never matches an ARN, because session names are chosen by the caller).
Approval comment format: `ticket: CHG-1234; notes: UAT signed off` (optionally `scope: emergency`).
`cicd/codebuild/scripts/record-approval.sh` turns the approval of THIS pipeline execution
(`ListActionExecutions` filtered by `#{codepipeline.PipelineExecutionId}`) into a vra approval record
bound to the execution (`source: codepipeline:<pipeline>/<executionId>`), scoped to `promote` (`deploy` for a
gated root), valid 24 h. vra takes the run initiator from the execution's trigger (`GetPipelineExecution`) and
refuses an approver who is the initiator.

## Dev ownership: pipeline or watch

Pick one owner for dev deploys. With this pipeline, `watch.command` is `sync-merge` (the default when vra renders
the config; `sync` only mirrors upstream): the schedule detects new upstream releases and development commits and
opens sync PRs, and merged trunk commits trigger the release pipeline. Without the pipeline, keep `watch` and the
schedule runs the full cron workflow (`vra ledger validate` refuses `watch` with automation.mode `pipeline`). The env
lock prevents concurrent deploys either way, and each deploy project runs one build at a time.

## Rendered by vra (automation tiers)

With `automation.mode` `hybrid`, `pipeline` or `pipeline+actions` in `vra.yaml`, you do not write
`config/pipeline.json` by hand: `automation.pipeline` holds this config without `ledgerGraph`, `ledgerDir` and
`automationMode`, and `vra pipeline plan` renders the rest from `vra ledger graph`.

| Command                    | What it does                                                                                                                                                                                                                                     |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vra pipeline plan`        | Renders the config, synthesizes this app offline (cdk-nag applies), diffs it against the deployed stack, and writes the IT package for the stack's own roles (`state/iam/pipeline/<n>/`). The plan record pins the template digest               |
| `vra pipeline deploy`      | Re-synthesizes, refuses a template or ledger that moved since the plan, then `cdk deploy` into the tools account. Human-only for the first deploy, IAM or source-bucket-policy changes and removed approvals; an agent may deploy any other plan |
| `vra pipeline status`      | Stack, stage states, pending approvals, recent executions, and whether the deployed topology (output `TopologyDigest`) matches the ledger                                                                                                        |
| `vra pipeline sync-ledger` | Plans again when the ledger's topology moved away from the deployed pipeline; exit 20 until it is deployed (`--deploy` deploys under the same gates)                                                                                             |
| `vra pipeline push-source` | For S3 sources the stack created: uploads the fork branch (or `--package`, this package) when its commit changed and starts the pipeline                                                                                                         |

`hybrid` renders `scope: dev`: only the envs reachable from a root without any approval get stages, and people or
agents run the gated envs with `vra promote`. The scheduled job then runs `sync-merge` (`scripts/pipeline-watch.sh`):
`vra sync` detects new upstream release tags and development commits, `vra merge` opens the release lane's sync PR
(its merge into the trunk starts the release pipeline) or merges the preview lane (starts the preview pipeline), and
`vra pipeline push-source` feeds S3 sources. It never deploys. The stack-created source bucket accepts writes only
from the watch tier plus the exact role ARNs in `sourceWriterArns` (the operator who uploads the first
`--package` zip the watch job needs; never a tier that runs fork code). For a source the stack created (no `bucketName`),
`--bucket` and `--pipeline` default to the stack outputs `SourceBucketName` and `ReleasePipelineName` (`--lane preview`:
`PreviewPipelineName`); that source does not poll, so the start is what runs the pipeline. The fork zip carries
the commit in `.vra-revision`, which the build stage uses as its ref (an S3 source resolves to the object's version id). `examples/three-env/` is the three-env live test.

## Source options and partitions

| Source type                                                                                        | Commercial | GovCloud / EU Sovereign                        | Notes                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codeconnections` (`GitHub`, `GitHubEnterpriseServer`, `GitLab`, `GitLabSelfManaged`, `Bitbucket`) | yes        | check regional availability before choosing it | Without `connectionArn` the stack creates the connection (PENDING: complete the handshake once in the console; retained when the source changes, so delete an unused one yourself). Server providers need `hostArn`. Full clone output |
| `codestar`                                                                                         | yes        | as above                                       | 0.4 spelling of `codeconnections` with an existing `connectionArn`                                                                                                                                                                     |
| `codecommit`                                                                                       | yes        | yes where CodeCommit is offered                | Mirror the fork from your git host (push mirror)                                                                                                                                                                                       |
| `s3`                                                                                               | yes        | yes                                            | Without `bucketName` the stack creates the bucket and `vra pipeline push-source` feeds it; with `bucketName`, your git host CI uploads and the pipeline polls. No git metadata: `vra build` clones the fork itself                     |

IAM matches the action prefix to the connection ARN's prefix: the stack grants `codeconnections:UseConnection` and
`codestar-connections:UseConnection` on every connection it uses, to the source action roles and to the CodeBuild
tiers (full clone).

## Failed tests, parked runs

-   **Auto-rollback**: the Test action of an env without approval runs `vra rollback --env <env>` when its tests fail
    and the env's `rollbackPolicy` is `auto`; the action still fails, so nothing promotes from it. Turn it off with
    `release.autoRollback: false`. With `release.isolateTests` the Test action cannot deploy; the deploy's own smoke
    rolls back instead.
-   **BLOCKED_ON_IT**: the action fails (exit 10) with the IT request and the IT packages in its output artifact
    (`vra-out/BLOCKED_ON_IT.md` lists them). Runs are bound to the execution and the action (`cp-<execution>-<action>`),
    so **Retry failed actions** after IT granted the access continues the parked run with `vra resume` instead of
    starting over. A run that ended FAILED or TESTS_FAILED gets a new attempt id.

## Strict IAM

`vra pipeline plan` synthesizes once more with `-c vraIamReport=true` (CDK `Role.customizeRoles`, the mechanism VAMS
uses for `useCustomVamsStackRoles`) and turns `cdk.out/iam-policy-report.json` into the IT package: one row per role
(construct path, proposed name, trust, policy), `it-roles.yaml` IT may deploy, a CSV, and the delta to the previous
package. References to resources with fixed names are resolved; the generated bucket and key ARNs are listed for IT
to generalize. Then set `iamRoles.mode: precreated` with `iamRoles.precreatedRoles` (construct path -> role name IT
returned): the stack creates no role, and a role missing from the map stops `vra pipeline plan` with exit 10.
`iamRoles.roleNamePrefix` prefixes every role name the stack creates; `permissionsBoundaryArn` applies a boundary.

Other partition notes:

-   All ARNs are built with `${AWS::Partition}`; the test fails if a hardcoded `arn:aws:` appears.
-   The default image `aws/codebuild/amazonlinux-x86_64-standard:5.0` provides Node 22 and Python
    3.12. If the image is not yet offered in your region, set `codebuild.image`.
-   Without internet egress, set `codebuild.environment` to point npm / pip / uv at a CodeArtifact
    or internal mirror (`NPM_CONFIG_REGISTRY`, `PIP_INDEX_URL`, `UV_INDEX_URL`) and run the projects
    in a VPC (`codebuild.vpc`) with the needed VPC endpoints.
-   EventBridge Scheduler and CodePipeline V2 are used; confirm both are available in the target
    region before deploying.
-   AWS GovCloud (US) and the AWS European Sovereign Cloud (partition `aws-eusc`): service availability differs
    from the commercial regions and changes over time. Check CodeConnections (and the provider you need, especially
    GitHub Enterprise Server / GitLab self-managed hosts), CodeCommit, EventBridge Scheduler, CodePipeline V2 and the
    CodeBuild image in the AWS documentation for the partition before choosing a source. The `s3` source fed by
    `vra pipeline push-source` needs only S3, CodePipeline and CodeBuild. Keep the tools account in the same
    partition as the envs: role ARNs, `${AWS::Partition}` and service principals do not cross partitions.

## cdk-nag

`bin/app.ts` applies the AwsSolutions pack; `npm test` fails on any unsuppressed error or warning.
Suppressions, all with reasons and `appliesTo` scopes, in `lib/vra-pipeline-stack.ts`:

| Rule                | Where                                                       | Why                                                                                                                                                                                                                                                                                   |
| ------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CB3 privileged mode | build, deploy, watch projects; test projects that roll back | CDK bundles Lambda code and publishes docker image assets (a rollback redeploys the previous build)                                                                                                                                                                                   |
| IAM5                | CodeBuild tier roles                                        | CDK grant action families on the stack's artifact bucket and the state bucket and keys, objects in those buckets, `vra/*` secrets, report groups / log streams of these projects, VPC ENIs; `*` only for `sts:GetCallerIdentity` and namespace-conditioned `cloudwatch:PutMetricData` |
| IAM5                | state-store managed policies                                | Objects below `stateStore.prefix` whose keys carry run, env and build ids; history and bundles create-only, deletes denied                                                                                                                                                            |
| IAM5                | CodeBuild VPC policy                                        | ENI actions have no resource-level permissions                                                                                                                                                                                                                                        |
| IAM5                | pipeline + source action roles                              | CDK-generated access to this stack's own artifact bucket and key                                                                                                                                                                                                                      |

## Session length

VAMS deploys can run longer than one hour. A CodeBuild role assuming `vra-deploy-<env>` is role
chaining, which AWS caps at one hour per session. The engine must use a refreshable credential
source (an AWS config profile with `role_arn` + `credential_source = EcsContainer`, or web
identity in GitHub / GitLab) rather than exporting static keys, so the SDK and the CDK CLI
re-assume the role as needed.
