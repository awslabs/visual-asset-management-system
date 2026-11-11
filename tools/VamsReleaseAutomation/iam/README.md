# iam/ — roles, policies and action manifests

Everything an IT / cloud-security team needs to review before `vra` can run in a strict
environment. All templates are partition-generic (`${AWS::Partition}`) and work in commercial,
GovCloud (`aws-us-gov`) and EU Sovereign partitions. Nothing here contains real account ids.

## Layout

| Path                                                     | What it is                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Where it is deployed                                                                                                                                                                               |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `actions/<workflow>.yaml`                                | Human-reviewed action manifests, one per workflow. `vra preflight` simulates them; the permission loop compares denied actions against them                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Read by the engine. The engine ships byte-identical copies in `engine/vra/data/iam-actions/` (`scripts/check.sh` enforces it); `VRA_IAM_ACTIONS_DIR` points it at another reviewed directory first |
| `cloudformation/vra-state-store.yaml`                    | KMS key, versioned SSE-KMS S3 state bucket (TLS-only policy; S3 conditional writes enforced for every principal: history records and bundles `If-None-Match`, run state `If-Match` / `If-None-Match`; deletes of history and approval claims denied to all but `HistoryAdminPrincipalArns`), DynamoDB lock table with PITR and a resource policy (`ProtectLocks`: only `env#*` / `run#*` items with the engine's attributes, no UpdateItem / batch / PartiQL writes), SNS notifications topic                                                                                                                                                                                                                                                                                                                                                                                                | Tools account (only when `stateStore.type: s3`)                                                                                                                                                    |
| `cloudformation/vra-deploy-role.yaml`                    | `vra-deploy-<env>`: trusted by tools / CI principals (the env's own CI tier only), optional ExternalId, permissions boundary, `sts:SourceIdentity` required by default for `EnvTier` test and prod (`RequireSourceIdentity=auto`; dev optional), session tags limited to `vra:*`, policies for deploy, backup, migrate, test-live, preflight, optional restore (with the maintenance-mode switch), optional change-set gate (`EnableChangeSetGate`) and workflow-execution abort (`EnableWorkflowAbort`) and the read-only `vra iam verify` of IT-created roles (`EnableIamVerify`, `IamVerifyRolePrefix`); optional build-only `vra-synth-<env>` (`CreateSynthRole`, CDK lookups only) and live-test-only `vra-test-<env>` (`CreateTestRole`: stack outputs, resource-name parameters, the env's test secret); `ExternalTableKmsKeyArn` for tables encrypted with a key VAMS did not create | Each env account                                                                                                                                                                                   |
| `cloudformation/vra-data-copy-roles.yaml`                | `vra-data-copy-source-<env>` + staging bucket and CMK (source), `vra-data-copy-target-<env>` (target), S3 Batch Operations roles. Each side reads only its own VAMS data; assets move through staging. `TrustPeerLoaderRole=true` (both sides) allows the chained source assume of `dataCopy.chainSourceRole`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Source and target env accounts (target first)                                                                                                                                                      |
| `cloudformation/vra-cdk-execution-policy.yaml`           | Scoped CloudFormation execution policy used instead of AdministratorAccess, with a mandatory permissions boundary for created roles (created here by default); role changes and `iam:PassRole` only on VAMS's own role names (`ConfigName`, `RoleNamePrefix`); further closed escalation paths in `cdk-bootstrap.md`. Its service list and PassRole targets equal `vams-services.json`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Each env account, before `cdk bootstrap`                                                                                                                                                           |
| `cdk-bootstrap.md`                                       | Bootstrap commands, trust models, trade-offs, source identity                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | —                                                                                                                                                                                                  |
| `vams-services.json`, `scripts/extract_vams_services.py` | The services the VAMS CDK app creates resources in and passes roles to, extracted from the VAMS source; `tests/` fails when the execution policy and the extraction disagree                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | —                                                                                                                                                                                                  |
| `scripts/patch-bootstrap-template.py`                    | Adds `sts:SetSourceIdentity` to the CDK bootstrap roles' trust (needed when env sessions carry a source identity)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Run before `cdk bootstrap --template` in env accounts of gated environments                                                                                                                        |
| `it-roles/`                                              | Inputs of the IT role package `vra iam plan` writes for strict-IAM environments (`iam.mode` boundary, precreated, bootstrap): the CDK bootstrap roles of the pinned CDK CLI (`cdk-bootstrap-roles.json`, engine copy byte-identical) and how the package is generated (`it-roles/README.md`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Read by the engine; the package's own `it-roles.yaml` is deployed by IT in the env account, optionally                                                                                             |
| `scripts/extract_cdk_bootstrap_roles.py`                 | Refreshes `it-roles/cdk-bootstrap-roles.json` and its engine copy from a CDK CLI's bootstrap template                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Run when the VAMS release pins a new aws-cdk CLI                                                                                                                                                   |

## State store statements

Every manifest ends with the same `ToolsStateStore*` block (the orchestrator identity, `stateStore.type: s3`):
run state (`runs/*`), history read and create-only writes of history records and bundles (`s3:if-none-match`),
approval claims, signatures / checkpoints / sync state, IT role packages, IT responses and pipeline plans
(`iam/*`, `pipeline/*`: read and create-only writes), the IT apply record `iam/<env>/applied.json` (replaced by
`vra iam apply`; the gates trust it only when the create-only `iam-apply` history record matches),
`s3:ListBucket` (not prefix-narrowed: without it a missing key reads as 403), lock items `env#*` / `run#*` with GetItem, PutItem, DeleteItem (no UpdateItem), and
the `vra-state` key. No statement grants a delete. Workflows that check approval records add the optional
`ToolsVerifyApprovalSignature` (`kms:Verify` on env.yaml `approvalSigningKeyArn`). `iam/tests` checks the block
against the bucket and lock-table policies and against the requests the engine sends.

## Manifest schema

Validated by `engine/vra/data/iam-actions.schema.json` (unknown keys are rejected). The engine
loads `iam/actions/<workflow>.yaml` before its built-in defaults, so these reviewed copies win.

```yaml
workflow: deploy
schemaVersion: 1
role: deploy # env role preflight simulates: deploy -> deployRoleArn, dataCopy -> dataCopyRoleArn
description: ...
statements:
    - sid: VamsStacksRead
      actions: [cloudformation:DescribeStacks, ...]
      resources:
          - arn:${Partition}:cloudformation:*:${Account}:stack/${ConfigName}-*-${BaseStackName}/*
      condition: {} # optional IAM condition block (for reviewers; simulation uses actions + resources)
      note: one sentence for the reviewer
      optional: false # true = a gap warns instead of blocking
      feature:
          changeSetGate # optional: simulated only when the env enables it (changeSetGate: diffGate
          #   changeset; maintenanceMode: writeFreeze backend maintenance-mode, optional for auto)
```

Placeholders (rendered by `engine/vra/permissions.py`): `${Partition}` (env.yaml `partition` or the
STS caller ARN), `${Account}`, `${Region}`, `${ConfigName}` (VAMS config `name`, default `vams`),
`${BaseStackName}` (region-suffixed, `<app.baseStackName>-<region>`, as VAMS `config.ts` sets it),
`${CoreStack}` (`<configName>-core-<baseStackName>-<region>`) and `${SsmPrefix}`
(`/<configName>-<baseStackName>-<region>/resourceNames`), `${LiveTestSecret}` (the env's
`liveTest.credentialsSecret`) and `${ExternalTableKmsKey}` (overlay `app.useKmsCmkEncryption.optionalExternalCmkArn`,
statements with `feature: externalTableKey`). Statement ids starting with `Tools` apply to the orchestrator
identity in the tools account; all others apply to the env role.

Registered asset buckets: a statement with `forEach: registeredBucket` uses `${RegisteredBucket}` and is
expanded at run time to every bucket of the env (SSM `s3Buckets/*` plus each asset bucket registered in
VAMS's `s3AssetBucketsStorage`); `forEach: registeredBucketKmsKey` uses `${RegisteredBucketKmsKey}` and is
expanded to each bucket's default-encryption KMS key. Without a bucket list (dry run, before the first deploy)
`${RegisteredBucket}` becomes `${CoreStack}*` and key statements are skipped. Buckets outside the core stack's
names go into `AdditionalBucketArns` / `AdditionalKmsKeyArns` of `vra-deploy-role.yaml` and
`vra-data-copy-roles.yaml` (plus the bucket or key policy when it is in another account) and into
`envs/<env>/env.yaml` `registeredBuckets`; see
[Permissions and IT requests](../../../documentation/docusaurus-site/docs/deployment/release-automation/permissions-and-it-requests.md#registered-asset-buckets).

| Manifest                                                                               | Principal                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| sync, build, report                                                                    | tools / CI identity (no `role`)                                                                                                                                                                                                                     |
| deploy (also promote, watch), rollback, backup, restore, migrate, test-live, preflight | `vra-deploy-<env>` (`deployRoleArn`)                                                                                                                                                                                                                |
| data-copy-source                                                                       | `vra-data-copy-source-<env>` in the source account (source env's `dataCopyRoleArn`)                                                                                                                                                                 |
| data-copy-target                                                                       | `vra-data-copy-target-<env>` in the target account (target env's `dataCopyRoleArn`). The target's pre-copy checkpoint runs as `vra-deploy-<target>` and is preflighted with `backup` (CMK tables need `kms:Decrypt` through DynamoDB on both roles) |

The CloudFormation templates take the RAW `BaseStackName` (as written in `config.json`) and append
the region themselves, mirroring VAMS.

## How resources are scoped

-   VAMS stacks: `<ConfigName>-<kind>-<BaseStackName>-<region>` (core, waf, ...) and their nested stacks.
-   VAMS tables, buckets and Lambdas are CloudFormation auto-named under the core stack name, so
    `<ConfigName>-core-<BaseStackName>-<region>*` covers them. The templates accept only lowercase
    `ConfigName` / `BaseStackName`: generated bucket names are lowercase and IAM ARN matching is case-sensitive.
-   Per-asset SNS topics are created at runtime as `AssetTopic<databaseId>-<assetId>`. The roles that use them
    get `...:sns:<region>:<account>:AssetTopic*`, as VAMS grants it (`engine/vra/iam.py` `RUNTIME_PATTERNS`).
-   External asset buckets (`app.assetBuckets.externalAssetBuckets`) go into `AdditionalBucketArns` /
    `AdditionalKmsKeyArns`; an external table key (`app.useKmsCmkEncryption.optionalExternalCmkArn`) into
    `ExternalTableKmsKeyArn` (DynamoDB use only).
-   Live-test secrets: `vra/<BaseStackName>/*` (put `liveTest.credentialsSecret` there, e.g. `vra/prod/smoke-user`).
-   Several VAMS stacks in one account share the CDK bootstrap roles, so one account is one trust boundary
    (`cdk-bootstrap.md` section 6).
-   KMS statements use `kms:ViaService` with a wildcard host (`dynamodb.*`, `s3.*`) so they hold in
    every partition's endpoint domain. `vra preflight` simulates a manifest statement's `condition:` with
    concrete context keys (`dynamodb.<region>.<dns suffix>`, the account), so conditioned grants simulate as
    the service would call them.
-   Actions without resource-level permissions (`cloudformation:ListStacks`,
    `cloudformation:DescribeStackDriftDetectionStatus`, `dynamodb:ListBackups`, `iam:GetAccountSummary`) sit
    in `*` statements in the manifests and in the templates. `iam:GetAccountSummary` (`IamQuotaRead`) feeds
    the IAM quota check before `cdk deploy` and returns counts only. The CloudFront quota check reads
    `cloudfront:ListCachePolicies`, `ListOriginRequestPolicies`, `ListResponseHeadersPolicies` and
    `ListDistributions` (`CloudFrontQuotaRead`) and `servicequotas:GetServiceQuota` (`CloudFrontQuotaLimitsRead`),
    all on `*`, read-only; preflight simulates them only for CloudFront envs (feature `cloudFrontQuota`).
-   The Cognito user/group export statement (`CognitoExportOptional`) is simulated only when
    `envs/<env>/env.yaml` sets `cognitoExport: true` (template: `EnableCognitoExport=true`).

## Order of operations (per env account)

1. `vra-cdk-execution-policy.yaml`
2. `cdk bootstrap` (see `cdk-bootstrap.md`; gated envs: with the source-identity patched template)
3. `vra-deploy-role.yaml` (record `DeployRoleArn` in `envs/<env>/env.yaml`)
4. Optional: `vra-data-copy-roles.yaml` with `Side=target` in the target account, then `Side=source`
   in the source account.

Tools account: `vra-state-store.yaml`, then the optional `cicd/pipeline` app.

## Validation

```bash
uvx --from cfn-lint cfn-lint --regions us-east-1 us-gov-west-1 eu-west-1 -- iam/cloudformation/*.yaml cicd/oidc/*.yaml
python3 -m pytest iam/tests cicd/tests                      # service coverage, OIDC scoping, CI lanes
VAMS_REPO=<vams clone> python3 -m pytest iam/tests          # also re-extracts the services from VAMS source
```

Before production use, have the cloud-security team run IAM Access Analyzer policy validation
(`aws accessanalyzer validate-policy`) on the rendered policies, and do one full deploy in a
sandbox account with the scoped execution policy.

## Strict-IAM modes and the deploy role

-   `iam.mode bootstrap`: pass the deploy, lookup and publishing role ARNs IT returned as `BootstrapRoleArns` of
    `vra-deploy-role.yaml` (empty: the `cdk bootstrap` names). With `iam.cliCredentialsSynthesizer`, set
    `CliCredentialsDeploy=true`, `CdkExecutionPolicyArn`, `StagingBucketName` and `StagingRepositoryName`: there
    are no bootstrap roles, so the deploy role runs AWS CloudFormation with its own credentials. The package's
    `vra-deploy-<env>` statements name the same ARNs.
-   `EnableIamVerify` lets `vra iam verify` read the IT-created roles' trust, attached and inline policies, so it
    reports roles broader than the package (extra principals or actions, AWS managed policies as warnings).
